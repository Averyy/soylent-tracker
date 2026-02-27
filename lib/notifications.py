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
    UNSUB_STOCK_THRESHOLD,
)
from .file_lock import locked_json, read_json
from .helpers import mask_phone, product_url
from .registry import classify, sms_name

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
    display_msg = re.sub(r'^\d{4,6}\s+', '[code] ', message)
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


def _stock_suffix(qty: int | None) -> str:
    """Return ' (N available)' if qty is known, else empty string."""
    if qty is not None and qty > 0:
        return f" ({qty} available)"
    return ""


def _footer(restocked: list[dict], unsub_keys: set[str]) -> str:
    """Build the footer line based on which products are being unsubscribed."""
    if len(restocked) == 1:
        c = restocked[0]
        qty = c.get("inventory_qty")
        if c["key"] in unsub_keys:
            return "You've been unsubscribed from this product."
        elif qty is not None and qty > 0:
            return "Limited stock, we'll keep watching."
        else:
            return "Unknown stock, we'll keep watching."

    # Multiple products
    all_unsub = all(c["key"] in unsub_keys for c in restocked)
    none_unsub = not any(c["key"] in unsub_keys for c in restocked)
    if all_unsub:
        return "You've been unsubscribed from these products."
    elif none_unsub:
        return "We'll keep watching the low inventory products."
    else:
        return "You've been unsubscribed from high-stock items."


def format_notification(restocked: list[dict], unsub_keys: set[str] | None = None) -> str:
    """Format a back-in-stock SMS.

    Single product: direct product link with stock qty.
    Multiple: one line per product with qty prefix, links to tracker.
    unsub_keys: product keys being auto-unsubscribed (determines footer text).
    """
    if unsub_keys is None:
        unsub_keys = set()

    # Single product — direct link
    if len(restocked) == 1:
        c = restocked[0]
        name = sms_name(c["key"], c.get("title", c["key"]))
        url = product_url(c["key"], c.get("handle"))
        qty = c.get("inventory_qty")
        suffix = _stock_suffix(qty)
        footer = _footer(restocked, unsub_keys)
        return f"{name} is back in stock{suffix}:\n{url}\n\n{footer}"

    # Multiple products — one line per product
    lines = []
    for c in restocked:
        name = sms_name(c["key"], c.get("title", c["key"]))
        qty = c.get("inventory_qty")
        if qty is not None and qty > 0:
            lines.append(f"{qty}x {name}")
        else:
            lines.append(name)

    footer = _footer(restocked, unsub_keys)
    product_list = "\n".join(lines)
    return f"Back in stock:\n\n{product_list}\n\n{TRACKER_URL}\n\n{footer}"


def notify_changes(changes: list[dict], users: list[dict]) -> None:
    """Send one bundled SMS per subscriber for all back-in-stock changes.

    After notification, products with stock > UNSUB_STOCK_THRESHOLD are
    removed from the user's subscriptions. Low-stock and unknown-stock
    products stay subscribed so the user gets re-notified if the product
    flickers (goes OOS and comes back).
    """
    restocked = [
        c for c in changes
        if c["available"]
        and classify(c["key"], c.get("product_type", "")) != "prepaid"
        and "prepaid" not in c.get("title", "").lower()
    ]
    if not restocked:
        return

    # Track which users were notified for which keys
    notified: list[tuple[str, set[str]]] = []  # (phone, {keys...})

    for user in users:
        if not user.get("notifications_enabled", True):
            continue

        subs = set(user.get("subscriptions", []))
        user_changes = [c for c in restocked if c["key"] in subs]
        if not user_changes:
            continue

        unsub_keys = {
            c["key"] for c in user_changes
            if (c.get("inventory_qty") or 0) > UNSUB_STOCK_THRESHOLD
        }
        message = format_notification(user_changes, unsub_keys)
        sent = send_sms(user["phone"], message)
        if not sent:
            log.warning(f"Failed to notify {mask_phone(user['phone'])}")
            continue
        if unsub_keys:
            notified.append((user["phone"], unsub_keys))

    # Unsubscribe notified users from the products they were alerted about
    if notified:
        from .users import locked_users
        with locked_users() as all_users:
            for phone, keys in notified:
                for u in all_users:
                    if u["phone"] == phone:
                        before = len(u.get("subscriptions", []))
                        u["subscriptions"] = [
                            s for s in u.get("subscriptions", []) if s not in keys
                        ]
                        after = len(u["subscriptions"])
                        if before != after:
                            log.info(f"Auto-unsubscribed {mask_phone(phone)} from {before - after} product(s)")
                        break
