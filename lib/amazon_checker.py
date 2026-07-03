"""Amazon.ca stock checker.

Fetches product pages for tracked ASINs, parses div#availability for stock
status, and notifies subscribers on changes. Uses wafer's built-in rate
limiting and challenge handling.

Run on a schedule via Docker or cron.
"""

import logging
import random
import re

import wafer
from wreq import Emulation

from .config import SOURCE_AMAZON_CA
from .history import record_changes
from .http_client import HttpClient
from .notifications import notify_changes
from .registry import get_amazon_asins
from .users import load_users
from .state import locked_state, update_product

log = logging.getLogger(__name__)

AMAZON_CA_URL = "https://www.amazon.ca/dp/{asin}"


def parse_availability(html: str) -> tuple[bool, str | None, int | None]:
    """Parse Amazon product page for availability info.

    Checks multiple HTML locations since Amazon's layout varies:
    - div#outOfStock for out-of-stock products
    - div#availability for in-stock products
    - span.a-color-price for "Currently unavailable"
    - span.a-color-success for "In Stock"

    Returns:
        (available, status_text, count)
        - available: True if in stock
        - status_text: Raw availability text
        - count: Numeric count if "Only X left" is shown
    """
    # Check div#outOfStock first (clearest signal for unavailable)
    out_of_stock_match = re.search(
        r'<div\s+id="outOfStock"[^>]*>([\s\S]*?)</div>\s*</div>',
        html,
    )
    if out_of_stock_match:
        text = re.sub(r'<[^>]+>', ' ', out_of_stock_match.group(1))
        text = re.sub(r'\s+', ' ', text).strip()
        if "currently unavailable" in text.lower():
            return False, "Currently unavailable.", None
        if text:
            return False, text, None

    # Check for outOfStockBuyBox_feature_div
    if re.search(r'id="outOfStockBuyBox_feature_div"', html):
        return False, "Currently unavailable.", None

    # Look for "Only X left in stock" anywhere in the page
    count_match = re.search(r'Only (\d+) left in stock', html, re.IGNORECASE)
    if count_match:
        count = int(count_match.group(1))
        return True, f"Only {count} left in stock.", count

    # Look for "In Stock" in a success-colored span
    in_stock_match = re.search(
        r'class="[^"]*a-color-success[^"]*"[^>]*>\s*In Stock',
        html,
        re.IGNORECASE,
    )
    if in_stock_match:
        return True, "In Stock.", None

    # Look for "In stock on [date]" (temporarily out)
    restock_match = re.search(r'In stock on ([^<]+)', html, re.IGNORECASE)
    if restock_match:
        return False, f"In stock on {restock_match.group(1).strip()}", None

    # Broad search: "Currently unavailable" anywhere prominent
    if re.search(r'class="[^"]*a-color-price[^"]*"[^>]*>\s*Currently unavailable', html):
        return False, "Currently unavailable.", None

    # Final fallback: search for common patterns in the page text
    if "currently unavailable" in html.lower():
        return False, "Currently unavailable.", None

    log.warning("Could not determine availability from HTML")
    return False, None, None


def check_all_asins() -> list[dict]:
    """Check all ASINs with wafer rate limiting. Returns list of changes."""
    changes = []
    asin_list = list(get_amazon_asins().items())

    # Randomize order to avoid predictable patterns
    random.shuffle(asin_list)

    # Fetch all results first (slow, network I/O — don't hold lock here).
    # Pin the TLS identity: Amazon's WAF keys reputation on (IP, fingerprint),
    # and wafer's default advances with each version (0.3.0 -> Chrome147), which
    # got the VPS's datacenter IP challenged. Start on Chrome145 (the identity
    # the pre-0.3.0 deploy used and Amazon accepted here) and keep a pool to
    # rotate across on any challenge. max_rotations=6 reaches the full ladder.
    results = []
    with HttpClient(
        rate_limit=5.0,
        rate_jitter=7.0,
        emulation=Emulation.Chrome145,
        fingerprint_pool=[
            Emulation.Chrome145, Emulation.Chrome147,
            Emulation.Firefox149, Emulation.Edge147,
        ],
        max_rotations=6,
    ) as client:
        for asin, title in asin_list:
            url = AMAZON_CA_URL.format(asin=asin)
            log.info(f"Checking {asin} ({title})...")

            try:
                # No explicit timeout: the session's 60s total budget must
                # absorb the rate-limit wait (up to 12s) plus bounded attempts.
                result = client.fetch(url)
            except wafer.ChallengeDetected as e:
                log.warning(f"{asin} hit unsolvable {e.challenge_type} challenge")
                continue
            except wafer.EmptyResponse:
                log.warning(f"{asin} returned empty response")
                continue
            except wafer.WaferError as e:
                log.error(f"Failed to fetch {asin}: {e}")
                continue
            except Exception as e:
                log.error(f"Failed to fetch {asin}: {type(e).__name__}: {e}")
                continue

            if result.status_code != 200:
                log.warning(f"{asin} returned HTTP {result.status_code}")
                continue

            html = result.text

            available, status_text, count = parse_availability(html)
            log.info(f"  {asin}: available={available}, status={status_text}, count={count}")
            results.append((asin, title, available, status_text, count))

    # Lock state only for the quick read-modify-write
    if results:
        with locked_state() as state:
            for asin, title, available, status_text, count in results:
                key = f"{SOURCE_AMAZON_CA}:{asin}"
                change = update_product(
                    state, key, available,
                    title=title,
                    status_text=status_text,
                    inventory_qty=count,
                )
                if change:
                    change["title"] = title
                    change["status_text"] = status_text
                    change["inventory_qty"] = count
                    changes.append(change)

        # Record history outside the state lock (single write for all changes)
        record_changes([
            {"product_key": c["key"], "available": c["available"], "title": c["title"],
             "status_text": c.get("status_text"), "inventory_qty": c.get("inventory_qty")}
            for c in changes
        ])

    return changes


def main():
    log.info("Starting Amazon.ca stock check...")
    changes = check_all_asins()
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
