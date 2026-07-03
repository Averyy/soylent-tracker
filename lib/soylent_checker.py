"""Shopify stock checker for soylent.ca.

Fetches products.json, compares availability against state.json,
and notifies subscribers on changes. Supports ETag-based conditional
requests to save bandwidth.

Run on a schedule via Docker or cron.
"""

import logging
import os
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import wafer

from .config import SHOPIFY_ETAG_FILE, SOURCE_SHOPIFY_CA
from .history import record_changes
from .http_client import HttpClient
from .notifications import notify_changes
from .registry import no_expand
from .users import load_users
from .state import locked_state, update_product

log = logging.getLogger(__name__)

PRODUCTS_URL = "https://soylent.ca/products.json?limit=250"
ETAG_FILE = str(SHOPIFY_ETAG_FILE)


def load_etag() -> str | None:
    """Load saved ETag from previous request."""
    if os.path.exists(ETAG_FILE):
        with open(ETAG_FILE, "r") as f:
            return f.read().strip() or None
    return None


def save_etag(etag: str) -> None:
    """Save ETag for next request (atomic write)."""
    tmp = ETAG_FILE + ".tmp"
    with open(tmp, "w") as f:
        f.write(etag)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, ETAG_FILE)


_NOT_MODIFIED = "NOT_MODIFIED"


def fetch_products(client: HttpClient) -> dict | str | None:
    """Fetch products.json with ETag-based conditional request.

    Returns:
        dict: parsed JSON on 200
        _NOT_MODIFIED: server confirmed data is current (304)
        None: error (bad status, parse failure)
    """
    extra_headers = {}
    etag = load_etag()
    if etag:
        extra_headers["If-None-Match"] = etag

    try:
        result = client.fetch(PRODUCTS_URL, headers={"Accept": "application/json", **extra_headers})
    except wafer.ChallengeDetected as e:
        log.warning(f"Shopify products fetch hit {e.challenge_type} challenge")
        return None
    except wafer.EmptyResponse:
        log.warning("Shopify products fetch returned empty response")
        return None
    except wafer.WaferError as e:
        log.error(f"Failed to fetch Shopify products: {type(e).__name__}: {e}")
        return None
    except Exception as e:
        log.error(f"Failed to fetch Shopify products: {type(e).__name__}: {e}")
        return None

    if result.status_code == 304:
        log.info("304 Not Modified - no changes")
        return _NOT_MODIFIED

    if result.status_code != 200:
        log.error(f"Shopify returned {result.status_code}")
        return None

    # Save ETag for next request
    new_etag = result.headers.get("etag")
    if new_etag:
        save_etag(new_etag)

    try:
        return result.json()
    except ValueError:
        log.error("Failed to parse Shopify JSON response")
        return None


def _parse_page_qty(html: str) -> int | None:
    """Extract inventory quantity from product page HTML.

    Checks two sources (in order):
    1. gsf_conversion_data quantity — present on all product pages
    2. inventoryQty — present on some products with Shopify inventory tracking
    """
    match = re.search(r'gsf_conversion_data\b.*?quantity\s*:\s*"(-?\d+)"', html)
    if match:
        return int(match.group(1))
    match = re.search(r'"inventoryQty":\s*(\d+)', html)
    if match:
        return int(match.group(1))
    return None


# Subscriber-only waitlist: the API reports stock, but the page replaces the
# buy button with an email signup ("due to high demand, we're prioritizing
# current inventory for our subscribers"). The block is rendered server-side
# only when the product is in this state — check the theme class and the
# message text in case either changes.
_WAITLIST_MARKERS = ('class="alternative-products"', "prioritizing current inventory")


def _parse_waitlist(html: str) -> bool:
    """Detect the subscriber-only waitlist block on a product page."""
    return any(marker in html for marker in _WAITLIST_MARKERS)


_PAGE_QTY_WORKERS = 4


def _fetch_qty_task(args: tuple) -> tuple[int, int | None, bool, bool]:
    """Thread pool worker: fetch one product page's inventory quantity.

    Uses its own HttpClient since wafer SyncSession is not thread-safe.
    Returns (index, quantity, waitlisted, fetched). `fetched` is False when the
    page could not be read (non-200, challenge, timeout) — the caller must NOT
    treat that as "confirmed not waitlisted"/"confirmed no stock", or a
    transient failure would clear a real override and fire a spurious change.
    """
    idx, handle, variant_id = args
    time.sleep(random.uniform(0.3, 1.5))
    try:
        with HttpClient() as client:
            url = f"https://soylent.ca/products/{handle}"
            if variant_id:
                url += f"?variant={variant_id}"
            result = client.fetch(url)
            if result.status_code != 200:
                log.warning(f"Page fetch for {handle} returned HTTP {result.status_code}")
                return idx, None, False, False
            html = result.text
            return idx, _parse_page_qty(html), _parse_waitlist(html), True
    except wafer.ChallengeDetected as e:
        log.warning(f"Page fetch for {handle} hit {e.challenge_type} challenge")
        return idx, None, False, False
    except wafer.WaferError as e:
        log.warning(f"Page fetch for {handle} failed: {type(e).__name__}: {e}")
        return idx, None, False, False
    except Exception as e:
        log.warning(f"Failed to fetch inventory for {handle}: {type(e).__name__}: {e}")
        return idx, None, False, False


def _batch_fetch_quantities(tasks: list[tuple[int, str, str | None]]) -> dict[int, tuple[int | None, bool, bool]]:
    """Fetch page quantities in parallel. Returns {index: (quantity, waitlisted, fetched)}."""
    if not tasks:
        return {}
    results = {}
    with ThreadPoolExecutor(max_workers=_PAGE_QTY_WORKERS) as pool:
        for idx, qty, waitlisted, fetched in pool.map(_fetch_qty_task, tasks):
            results[idx] = (qty, waitlisted, fetched)
    return results


def check_products() -> list[dict]:
    """Main check loop. Returns list of changes detected."""
    changes = []

    with HttpClient() as client:
        data = fetch_products(client)
        if data is _NOT_MODIFIED:
            # Server confirmed our cached data is current — a successful check.
            now = datetime.now(timezone.utc).isoformat()
            with locked_state() as state:
                for key, entry in state.items():
                    if key.startswith(SOURCE_SHOPIFY_CA + ":"):
                        entry["last_checked"] = now
            return changes
        if data is None:
            return changes

        products = data.get("products", [])
        log.info(f"Fetched {len(products)} products from soylent.ca")

        # Build product entries, collecting page-qty fetch tasks for parallel execution
        results = []  # (key, available, title, handle, product_type, inventory_qty, price, waitlisted)
        qty_tasks = []  # (result_index, handle, variant_id)
        multi_variant_parent_keys = []  # parent keys to remove when expanding into variants
        for product in products:
            handle = product.get("handle", "")
            title = product.get("title", "")
            product_type = product.get("product_type", "")
            variants = product.get("variants", [])
            parent_key = f"{SOURCE_SHOPIFY_CA}:{product['id']}"

            # Multi-variant: create one entry per variant (e.g. T-shirt sizes)
            # Skip expansion for gift cards or products marked no_expand in registry
            is_multi = (
                len(variants) > 1
                and any(v.get("title", "Default Title") != "Default Title" for v in variants)
                and product_type != "Gift Card"
                and not no_expand(parent_key)
            )
            if is_multi:
                multi_variant_parent_keys.append(parent_key)
                for v in variants:
                    key = f"{SOURCE_SHOPIFY_CA}:{product['id']}:{v['id']}"
                    variant_title = f"{title} - {v.get('title', '')}"
                    available = v.get("available", False)
                    price = v.get("price")
                    idx = len(results)
                    results.append([key, available, variant_title, handle, product_type, None, price, False])
                    if available:
                        qty_tasks.append((idx, handle, str(v["id"])))
                continue

            if not variants:
                continue

            available = any(v.get("available", False) for v in variants)
            price = variants[0].get("price")

            # Cross-check: products.json can lie about availability when
            # inventory_management is null (e.g. ReCharge bundles). Scrape
            # the product page for the real quantity if the API says available.
            # Skip digital products (gift cards) which don't require shipping.
            is_physical = any(v.get("requires_shipping", True) for v in variants)
            idx = len(results)
            results.append([parent_key, available, title, handle, product_type, None, price, False])
            if available and is_physical:
                qty_tasks.append((idx, handle, None))

        # Fetch page quantities in parallel (slow network I/O)
        # Keys whose page fetch was attempted but failed: their availability and
        # waitlist state are UNKNOWN this cycle, so we must fall back to prior
        # state rather than the raw API (which doesn't know about qty=0 or the
        # subscriber-only theme gate). Applied under the state lock below.
        failed_fetch_keys: set[str] = set()
        if qty_tasks:
            log.info(f"Fetching {len(qty_tasks)} page quantities ({_PAGE_QTY_WORKERS} workers)...")
            qty_map = _batch_fetch_quantities(qty_tasks)
            for idx, (inventory_qty, waitlisted, fetched) in qty_map.items():
                title = results[idx][2]
                if not fetched:
                    # Don't trust the raw API over prior page-derived state on a
                    # transient failure. Leave the row's API values untouched here;
                    # prior state is restored in the state-lock loop.
                    failed_fetch_keys.add(results[idx][0])
                    continue
                results[idx][5] = inventory_qty
                results[idx][7] = waitlisted
                if inventory_qty is not None and inventory_qty <= 0:
                    log.info(f"Overriding {title}: API says available but "
                             f"page quantity is {inventory_qty}")
                    results[idx][1] = False
                if waitlisted and results[idx][1]:
                    log.info(f"Overriding {title}: API says available but page "
                             f"shows subscriber-only waitlist")
                    results[idx][1] = False

        # Lock state only for the quick read-modify-write
        with locked_state() as state:
            # Remove stale parent entries for products now expanded into variants
            for parent_key in multi_variant_parent_keys:
                state.pop(parent_key, None)

            # Remove stale variant entries for Shopify products no longer expanded
            all_result_keys = {r[0] for r in results}
            stale_variant_keys = [
                k for k in list(state.keys())
                if k.startswith(SOURCE_SHOPIFY_CA + ":") and k.count(":") == 2 and k not in all_result_keys
            ]
            for k in stale_variant_keys:
                state.pop(k, None)

            for key, available, title, handle, product_type, inventory_qty, price, waitlisted in results:
                # Page fetch failed this cycle: we have NO reliable new signal for
                # this product (the raw API "available" is exactly what the page
                # check exists to verify — it stays True for waitlisted and qty<=0
                # products). Keep the last determination rather than letting the
                # API flip an unavailable product back to available and fire a
                # spurious "back in stock" notification. Real restocks that
                # coincide with a fetch failure are delayed one cycle, not lost.
                if key in failed_fetch_keys:
                    prev = state.get(key)
                    if prev is not None:
                        available = prev.get("available", available)
                        waitlisted = bool(prev.get("waitlisted"))
                        if inventory_qty is None:
                            inventory_qty = prev.get("inventory_qty")

                change = update_product(
                    state, key, available,
                    title=title, handle=handle, product_type=product_type,
                    price=price,
                )

                # Update inventory_qty: set when known, clear when unknown or unavailable
                if key in state:
                    if inventory_qty is not None and inventory_qty > 0:
                        state[key]["inventory_qty"] = inventory_qty
                    else:
                        state[key].pop("inventory_qty", None)
                    # Waitlisted: stock exists but is reserved for subscribers
                    if waitlisted:
                        state[key]["waitlisted"] = True
                    else:
                        state[key].pop("waitlisted", None)

                if change:
                    if inventory_qty is not None and inventory_qty > 0:
                        change["inventory_qty"] = inventory_qty

                    change["title"] = title
                    changes.append(change)
                    log.info(f"CHANGE: {title} -> {'AVAILABLE' if available else 'UNAVAILABLE'}"
                             + (f" ({inventory_qty:,} units)" if inventory_qty else ""))

        # Record history outside the state lock (single write for all changes)
        record_changes([
            {"product_key": c["key"], "available": c["available"], "title": c["title"],
             "inventory_qty": c.get("inventory_qty")}
            for c in changes
        ])

    return changes


def main():
    log.info("Starting Shopify stock check...")
    changes = check_products()
    if changes:
        log.info(f"Detected {len(changes)} change(s)")
        notify_changes(changes, load_users())
    else:
        log.info("No changes detected")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    main()
