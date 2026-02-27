"""Build product display dicts from state data."""

import re

from . import config
from .helpers import relative_time, product_url
from .registry import classify, display_name, is_hidden

_SIZE_ORDER = {"XS": 0, "S": 1, "M": 2, "L": 3, "XL": 4, "XXL": 5, "XXXL": 6, "2XL": 5, "3XL": 6}


def build_products(state: dict, subscriptions: set) -> dict[str, list]:
    """Build product dicts grouped by type: drinks, powder, prepaid, accessories."""
    groups: dict[str, list] = {"drinks": [], "powder": [], "prepaid": [], "accessories": []}
    for key, info in sorted(state.items()):
        source = key.split(":")[0]
        available = info.get("available", False)
        product_type = info.get("product_type", "")

        if is_hidden(key, available):
            continue

        inventory = info.get("inventory_qty")
        status_text_raw = info.get("status_text", "")

        # Status label for the badge
        if not available and status_text_raw:
            status_label = "Unavailable"
        elif available:
            status_label = "In Stock"
        else:
            status_label = "Out of Stock"

        detail = ""
        low_stock = False
        if inventory is not None and inventory > 0:
            detail = f"{inventory:,} unit{'s' if inventory != 1 else ''}"
            if inventory < 5:
                low_stock = True
        elif not available and (inventory is not None and inventory == 0):
            detail = "0 units"

        raw_price = info.get("price")
        if raw_price:
            try:
                p_val = float(raw_price)
                price = f"CA${p_val:.0f}" if p_val == int(p_val) else f"CA${p_val:.2f}"
            except (ValueError, TypeError):
                price = None
        else:
            price = None

        category = classify(key, product_type)
        raw_title, from_registry = display_name(key, info.get("title", key.split(":", 1)[1]))
        if "prepaid" in raw_title.lower():
            category = "prepaid"
        if from_registry:
            title = raw_title  # registry names are already correctly formatted
        else:
            # Title case, but don't capitalize after digits (e.g. "20oz" not "20Oz")
            title = re.sub(r'(\d)([A-Z])', lambda m: m.group(1) + m.group(2).lower(), raw_title.title())
            # Restore all-caps size abbreviations (e.g. "Xl" → "XL", "Xs" → "XS")
            title = re.sub(r'\b(Xs|Xl|Xxl|Xxxl|2Xl|3Xl)\b', lambda m: m.group(1).upper(), title)
        product = {
            "key": key,
            "title": title,
            "source": source,
            "source_label": config.SOURCE_LABELS.get(source, source),
            "available": available,
            "status_label": status_label,
            "detail": detail,
            "low_stock": low_stock,
            "price": price,
            "relative_time": relative_time(info.get("last_checked", "")),
            "subscribed": key in subscriptions,
            "url": product_url(key, info.get("handle")),
            "is_gift_card": product_type == "Gift Card",
            "no_subscribe": category == "prepaid",
        }
        groups.setdefault(category, []).append(product)

    # Sort each category group
    for products in groups.values():
        _sort_product_list(products)

    return groups


def _sort_product_list(products: list[dict]) -> None:
    """Sort products: in-stock first, gift cards last, Amazon after Shopify,
    bundles after singles, then alphabetically. Size variants (S/M/L/XL)
    sort together as a group, with the group ranked by its best availability."""
    # Pre-compute group availability: if any size is in-stock, the whole group sorts as in-stock
    group_avail: dict[str, bool] = {}
    for p in products:
        title = p["title"]
        if " - " in title:
            base, _, suffix = title.rpartition(" - ")
            if suffix.upper() in _SIZE_ORDER:
                group_avail[base] = group_avail.get(base, False) or p["available"]

    def _sort_key(p, _ga=group_avail):
        title = p["title"]
        base = title
        size_rank = 0

        # Size variant: group by base name, sort by size within group
        if " - " in title:
            base, _, suffix = title.rpartition(" - ")
            rank = _SIZE_ORDER.get(suffix.upper(), 99)
            if rank != 99:
                size_rank = rank
                available = _ga.get(base, p["available"])
            else:
                base = title
                available = p["available"]
        else:
            available = p["available"]

        return (
            not available,                                  # in-stock first
            p.get("is_gift_card", False),                   # gift cards last
            p["source"] == "amazon-ca",                     # Shopify before Amazon
            "bundle" in title.lower() or "builder" in title.lower(),  # bundles after singles
            base.lower(),                                   # alphabetical by name
            size_rank,                                      # size order within group
        )

    products.sort(key=_sort_key)
