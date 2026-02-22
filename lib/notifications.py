"""SMS notifications via Twilio Programmable Messaging.

Provides send_sms() for OTP delivery and stock alert notifications.
Uses a singleton Client for connection pooling.
SMS stats are persisted to sms_stats.json for the admin dashboard.
"""

import logging
import os
import re
import threading
import time

from twilio.base.exceptions import TwilioRestException
from twilio.rest import Client

from .config import (
    DAILY_SMS_CAP,
    SMS_STATS_FILE,
    SOURCE_LABELS,
    TWILIO_ACCOUNT_SID,
    TWILIO_API_KEY,
    TWILIO_API_SECRET,
    TWILIO_FROM,
)
from .file_lock import locked_json, read_json
from .helpers import mask_phone, product_url
from .registry import sms_name

log = logging.getLogger(__name__)

# Hard-blocked numbers — never receive any SMS under any circumstances
_BLOCKED = {"+15555555555"}

# Singleton Twilio client — reuse for connection pooling (SDK uses requests.Session)
_client: Client | None = None
_client_lock = threading.Lock()


def _record_sms(phone: str, message: str) -> int:
    """Record a successful SMS send in persistent stats. Returns today's send count."""
    today = time.strftime("%Y-%m-%d", time.gmtime())
    # Redact OTP codes (e.g. "1234 is your Soylent Tracker code" → "is your Soylent Tracker code")
    display_msg = re.sub(r'^\d{4,6}\s+', '', message)
    with locked_json(SMS_STATS_FILE) as stats:
        stats["total"] = stats.get("total", 0) + 1
        daily = stats.setdefault("daily", {})
        daily[today] = daily.get(today, 0) + 1
        by_phone = stats.setdefault("by_phone", {})
        by_phone[phone] = by_phone.get(phone, 0) + 1
        last_msg = stats.setdefault("last_message", {})
        last_msg[phone] = {"text": display_msg, "at": time.time()}
        return daily[today]


def _check_daily_cap() -> bool:
    """Check if we're under the daily SMS cap. Returns True if allowed."""
    today = time.strftime("%Y-%m-%d", time.gmtime())
    stats = read_json(SMS_STATS_FILE)
    sent_today = stats.get("daily", {}).get(today, 0)
    return sent_today < DAILY_SMS_CAP


def get_sms_stats() -> dict:
    """Return SMS stats for the admin dashboard.

    Returns: {"today": int, "total": int, "cap": int, "by_phone": {phone: count},
             "last_message": {phone: {"text": str, "at": float}}}
    """
    today = time.strftime("%Y-%m-%d", time.gmtime())
    stats = read_json(SMS_STATS_FILE)
    return {
        "today": stats.get("daily", {}).get(today, 0),
        "total": stats.get("total", 0),
        "cap": DAILY_SMS_CAP,
        "by_phone": stats.get("by_phone", {}),
        "last_message": stats.get("last_message", {}),
    }


def _get_client() -> Client | None:
    """Get or create the singleton Twilio client (thread-safe)."""
    global _client
    if _client is not None:
        return _client
    with _client_lock:
        if _client is not None:
            return _client
        if not all([TWILIO_ACCOUNT_SID, TWILIO_API_KEY, TWILIO_API_SECRET]):
            return None
        _client = Client(TWILIO_API_KEY, TWILIO_API_SECRET, TWILIO_ACCOUNT_SID)
        return _client


def send_sms(phone: str, message: str) -> bool:
    """Send SMS via Twilio. Returns True on success (or blocked), False on error."""
    if phone in _BLOCKED:
        log.debug(f"[BLOCKED] SMS to {mask_phone(phone)} — test number")
        return True

    if not _check_daily_cap():
        log.error(f"Daily SMS cap ({DAILY_SMS_CAP}) reached — SMS to {mask_phone(phone)} blocked")
        return False

    client = _get_client()
    if not client or not TWILIO_FROM:
        log.warning("Twilio not configured — SMS skipped")
        return False

    try:
        client.messages.create(to=phone, from_=TWILIO_FROM, body=message)
        sent_today = _record_sms(phone, message)
        log.info(f"SMS sent to {mask_phone(phone)} ({sent_today}/{DAILY_SMS_CAP} today)")
        return True
    except TwilioRestException as e:
        if e.code == 21211:
            log.error(f"Invalid phone number: {mask_phone(phone)}")
        elif e.code == 21614:
            log.error(f"Phone cannot receive SMS: {mask_phone(phone)}")
        elif e.code == 20429:
            log.warning("Twilio rate limited — SMS deferred")
        else:
            log.error(f"Twilio error {e.code} (HTTP {e.status}): {e.msg}")
        return False


TRACKER_URL = os.environ.get("TRACKER_URL", "https://soylent.dev/buy")


def format_notification(restocked: list[dict]) -> str:
    """Format a back-in-stock SMS.

    Single product: direct product link.
    Multiple: groups flavours, links to tracker.
    """

    # Single product — direct link
    if len(restocked) == 1:
        c = restocked[0]
        name = sms_name(c["key"], c.get("title", c["key"]))
        url = product_url(c["key"], c.get("handle"))
        return f"{name} is back in stock:\n{url}"

    # Multiple products — group by prefix, link to tracker
    groups: dict[str, list[str]] = {}  # prefix -> [flavour, ...]
    for c in restocked:
        name = sms_name(c["key"], c.get("title", c["key"]))
        m = re.match(r'^(.+?)\s*\((.+)\)$', name)
        if m:
            groups.setdefault(m.group(1), []).append(m.group(2))
        else:
            groups.setdefault(name, [])

    type_parts = []
    for prefix, flavours in groups.items():
        if flavours:
            type_parts.append(f"{prefix} ({', '.join(flavours)})")
        else:
            type_parts.append(prefix)

    if len(type_parts) == 1:
        label = type_parts[0]
    elif len(type_parts) == 2:
        label = " and ".join(type_parts)
    else:
        label = ", ".join(type_parts[:-1]) + ", and " + type_parts[-1]
    verb = "is" if len(type_parts) == 1 else "are"
    return f"{label} {verb} back in stock:\n{TRACKER_URL}"


def notify_changes(changes: list[dict], users: list[dict]) -> None:
    """Send one bundled SMS per subscriber for all back-in-stock changes."""
    restocked = [c for c in changes if c["available"]]
    if not restocked:
        return

    for user in users:
        if not user.get("notifications_enabled", True):
            continue

        subs = set(user.get("subscriptions", []))
        user_changes = [c for c in restocked if c["key"] in subs]
        if not user_changes:
            continue

        message = format_notification(user_changes)
        sent = send_sms(user["phone"], message)
        if not sent:
            log.warning(f"Failed to notify {mask_phone(user['phone'])}")
