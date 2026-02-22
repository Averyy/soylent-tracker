"""State management for stock tracking.

Reads/writes state.json with file locking for safe concurrent access
from checkers and the web app.
"""

import logging
from datetime import datetime, timezone

from .config import STATE_FILE
from .file_lock import locked_json, read_json, read_json_snapshot

log = logging.getLogger(__name__)


def load_state() -> dict:
    """Load current state snapshot (read-only). Do not mutate the returned dict."""
    return read_json_snapshot(STATE_FILE)


def locked_state():
    """Hold exclusive lock across read-modify-write cycle.

    Usage:
        with locked_state() as state:
            update_product(state, key, available, title=title)
        # lock released, file saved automatically on exit
    """
    return locked_json(STATE_FILE)


def update_product(state: dict, key: str, available: bool, **extra) -> dict | None:
    """Update a product's availability in state. Returns change dict if changed, None otherwise.

    Args:
        state: Current state dict (modified in place)
        key: Product key like "shopify-ca:handle" or "amazon-ca:ASIN"
        available: Whether the product is currently available
        **extra: Additional fields (inventory_qty, status_text, title, etc.)

    Returns:
        Dict with change details if availability changed, None if unchanged.
    """
    now = datetime.now(timezone.utc).isoformat()
    prev = state.get(key, {})
    was_available = prev.get("available")

    entry = {
        "available": available,
        "last_checked": now,
    }

    # Apply extra fields: None means "clear this field", non-None means "set it"
    for k, v in extra.items():
        if v is not None:
            entry[k] = v
        # None values intentionally omitted (clears from entry)

    # Preserve existing fields not mentioned in this update
    for k, v in prev.items():
        if k not in entry and k not in extra:
            entry[k] = v

    if was_available != available:
        state[key] = entry
        return {
            "key": key,
            "available": available,
            "was_available": was_available,
            **{k: v for k, v in extra.items() if v is not None},
        }

    state[key] = entry
    return None
