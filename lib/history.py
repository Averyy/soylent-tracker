"""Product change history — append-only JSON log.

Records stock availability changes to history.json for trend display.
Each entry is a timestamped event with product key, availability, and metadata.

Schema (list of events, newest last):
    [
        {
            "timestamp": "2026-02-18T15:30:00+00:00",
            "product_key": "shopify-ca:12345",
            "available": true,
            "title": "Soylent Mocha",
            "source": "soylent.ca",
            "inventory_qty": 50,
            ...
        }
    ]
"""

import logging
from datetime import datetime, timezone

from .config import HISTORY_FILE, MAX_HISTORY_ENTRIES, SOURCE_LABELS
from .file_lock import locked_json, read_json

log = logging.getLogger(__name__)


def record_change(product_key: str, available: bool, title: str, **extra) -> None:
    """Append a stock change event to history.json."""
    record_changes([{"product_key": product_key, "available": available, "title": title, **extra}])


def record_changes(changes: list[dict]) -> None:
    """Append multiple stock change events to history.json in a single write.

    Each dict must have product_key, available, title. Extra keys with non-None
    values are included as-is.
    """
    if not changes:
        return
    now = datetime.now(timezone.utc).isoformat()
    events = []
    for c in changes:
        source_prefix = c["product_key"].split(":")[0]
        events.append({
            "timestamp": now,
            "product_key": c["product_key"],
            "available": c["available"],
            "title": c["title"],
            "source": SOURCE_LABELS.get(source_prefix, source_prefix),
            **{k: v for k, v in c.items() if k not in ("product_key", "available", "title") and v is not None},
        })

    with locked_json(HISTORY_FILE, default_factory=list) as history:
        history.extend(events)
        if len(history) > MAX_HISTORY_ENTRIES:
            del history[:-MAX_HISTORY_ENTRIES]

    for e in events:
        log.info(f"Recorded history: {e['title']} -> {'available' if e['available'] else 'unavailable'}")


def load_history(product_key: str | None = None, limit: int = 200) -> list[dict]:
    """Load history events, newest first.

    Args:
        product_key: Filter to a specific product (or None for all).
        limit: Max events to return.
    """
    history = read_json(HISTORY_FILE, default_factory=list)

    if product_key:
        history = [e for e in history if e.get("product_key") == product_key]

    # Newest first, capped
    return list(reversed(history[-limit:]))
