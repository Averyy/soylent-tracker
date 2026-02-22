"""User management for the stock tracker.

Loads/saves users.json. Each user has a phone number, optional name,
notification preferences, and product subscriptions.

Schema:
    {
        "phone": "+15551234567",
        "name": "Avery",
        "notifications_enabled": true,
        "subscriptions": ["shopify-ca:some-handle"],
        "invited_by": "+15559876543"
    }
"""

import logging

from .config import USERS_FILE
from .file_lock import locked_json, read_json, read_json_snapshot

log = logging.getLogger(__name__)


def load_users() -> list[dict]:
    """Load users from users.json."""
    return read_json(USERS_FILE, default_factory=list)


def locked_users():
    """Hold exclusive lock across read-modify-write cycle.

    Usage:
        with locked_users() as users:
            for u in users:
                if u["phone"] == phone:
                    u["subscriptions"].append(key)
                    break
        # lock released, file saved automatically on exit
    """
    return locked_json(USERS_FILE, default_factory=list)


def find_user(phone: str) -> dict | None:
    """Find a user by phone number. Returns a snapshot — do not mutate."""
    users = read_json_snapshot(USERS_FILE, default_factory=list)
    for user in users:
        if user["phone"] == phone:
            return user
    return None
