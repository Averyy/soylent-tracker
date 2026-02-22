"""Product registry for manual overrides and auto-classification.

Loads lib/products.json at import time. Read-only, no locking needed.
"""

import json
import time
from pathlib import Path

_REGISTRY_FILE = Path(__file__).parent / "products.json"

# Auto-classification from Shopify product_type
_TYPE_MAP = {
    "Drink": "drinks",
    "Powder": "powder",
    "Gift Card": "accessories",
    "Accessories": "accessories",
}


_cache: tuple[float, dict] | None = None  # (mtime, data)
_last_stat_time: float = 0.0
_STAT_INTERVAL = 1.0  # seconds — avoid repeated stat() calls within a single request


def _get_registry() -> dict:
    """Load products.json, reloading automatically when the file changes.

    Throttles stat() calls to once per second to avoid 75+ syscalls per page load.
    """
    global _cache, _last_stat_time
    now = time.monotonic()
    if _cache is not None and now - _last_stat_time < _STAT_INTERVAL:
        return _cache[1]
    _last_stat_time = now
    try:
        mtime = _REGISTRY_FILE.stat().st_mtime
    except FileNotFoundError:
        return {}
    if _cache is None or _cache[0] != mtime:
        try:
            with open(_REGISTRY_FILE) as f:
                _cache = (mtime, json.load(f))
        except (json.JSONDecodeError, OSError):
            return _cache[1] if _cache else {}
    return _cache[1]


def classify(key: str, product_type: str | None = None) -> str:
    """Classify a product into: drinks, powder, or accessories.

    Priority: registry override → auto from product_type → default "drinks"
    """
    entry = _get_registry().get(key)
    if entry and "category" in entry:
        return entry["category"]
    if product_type and product_type in _TYPE_MAP:
        return _TYPE_MAP[product_type]
    return "drinks"


def display_name(key: str, auto_title: str) -> tuple[str, bool]:
    """Get display name for a product.

    Returns (name, from_registry) — from_registry=True means the name is
    an explicit override and should NOT be title-cased by the caller.
    Priority: registry override → auto_title from checker
    """
    entry = _get_registry().get(key)
    if entry and "name" in entry:
        return entry["name"], True
    return auto_title, False


def sms_name(key: str, fallback_title: str) -> str:
    """Get short SMS notification name for a product.

    Returns sms_name from registry if set, otherwise falls back to the
    regular display title.
    """
    entry = _get_registry().get(key)
    if entry and "sms_name" in entry:
        return entry["sms_name"]
    return fallback_title


def no_expand(key: str) -> bool:
    """Return True if this product should NOT be expanded into per-variant items."""
    entry = _get_registry().get(key)
    return bool(entry and entry.get("no_expand"))


def get_amazon_asins() -> dict[str, str]:
    """Return {ASIN: title} for all amazon-ca entries in the registry."""
    registry = _get_registry()
    result = {}
    for key, entry in registry.items():
        if key.startswith("amazon-ca:"):
            asin = key.split(":", 1)[1]
            title = entry.get("name", asin)
            result[asin] = title
    return result


def is_hidden(key: str, available: bool) -> bool:
    """Check if a product should be hidden.

    Registry hidden field: true = always, "when_oos" = hide when unavailable
    """
    entry = _get_registry().get(key)
    if not entry:
        return False
    hidden = entry.get("hidden")
    if hidden is True:
        return True
    if hidden == "when_oos" and not available:
        return True
    return False
