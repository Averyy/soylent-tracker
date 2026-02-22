"""Shopify stock checker for soylent.ca.

Fetches products.json, compares availability against state.json,
and notifies subscribers on changes. Supports ETag-based conditional
requests to save bandwidth.

Run on a schedule via Docker or cron.
"""

import json
import logging
import os
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

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

    result = client.fetch(PRODUCTS_URL, json_mode=True, headers=extra_headers)

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
        return json.loads(result.content)
    except json.JSONDecodeError:
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


def fetch_page_qty(client: HttpClient, handle: str, variant_id: str | None = None) -> int | None:
    """Scrape inventory quantity from a product page.

    For multi-variant products, pass variant_id to select the specific variant
    (Shopify's gsf_conversion_data reflects the ?variant= query param).

    Returns inventory count (can be negative for oversold), or None if not found.
    """
    url = f"https://soylent.ca/products/{handle}"
    if variant_id:
        url += f"?variant={variant_id}"
    try:
        time.sleep(random.uniform(0.5, 3.0))
        result = client.fetch(url)
        if result.status_code != 200:
            return None
        html = result.content.decode("utf-8", errors="replace")
        return _parse_page_qty(html)
    except Exception as e:
        log.warning(f"Failed to fetch inventory for {handle}: {type(e).__name__}: {e}")
    return None


_PAGE_QTY_WORKERS = 4


def _fetch_qty_task(args: tuple) -> tuple[int, int | None]:
    """Thread pool worker: fetch one product page's inventory quantity.

    Uses its own HttpClient since curl_cffi Session is not thread-safe.
    Returns (index, quantity).
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
                return idx, None
            html = result.content.decode("utf-8", errors="replace")
            return idx, _parse_page_qty(html)
    except Exception as e:
        log.warning(f"Failed to fetch inventory for {handle}: {type(e).__name__}: {e}")
        return idx, None


def _batch_fetch_quantities(tasks: list[tuple[int, str, str | None]]) -> dict[int, int | None]:
    """Fetch page quantities in parallel. Returns {index: quantity}."""
    if not tasks:
        return {}
    results = {}
    with ThreadPoolExecutor(max_workers=_PAGE_QTY_WORKERS) as pool:
        for idx, qty in pool.map(_fetch_qty_task, tasks):
            results[idx] = qty
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
        results = []  # (key, available, title, handle, product_type, inventory_qty, price)
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
                    results.append([key, available, variant_title, handle, product_type, None, price])
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
            results.append([parent_key, available, title, handle, product_type, None, price])
            if available and is_physical:
                qty_tasks.append((idx, handle, None))

        # Fetch page quantities in parallel (slow network I/O)
        if qty_tasks:
            log.info(f"Fetching {len(qty_tasks)} page quantities ({_PAGE_QTY_WORKERS} workers)...")
            qty_map = _batch_fetch_quantities(qty_tasks)
            for idx, inventory_qty in qty_map.items():
                results[idx][5] = inventory_qty
                if inventory_qty is not None and inventory_qty <= 0:
                    title = results[idx][2]
                    log.info(f"Overriding {title}: API says available but "
                             f"page quantity is {inventory_qty}")
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

            for key, available, title, handle, product_type, inventory_qty, price in results:
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
