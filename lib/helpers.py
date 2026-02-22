"""Shared utility functions used across the web app and notifications."""

import re
from datetime import datetime, timezone


def normalize_phone(raw: str) -> str | None:
    """Normalize raw phone input to E.164 format (+1XXXXXXXXXX).

    Returns None if the input isn't a valid 10-digit North American number.
    """
    digits = re.sub(r'\D', '', raw.strip())
    if digits.startswith("1") and len(digits) == 11:
        digits = digits[1:]
    if len(digits) != 10:
        return None
    return "+1" + digits


def product_url(key: str, handle: str | None = None) -> str:
    """Build external product page URL from key and optional Shopify handle."""
    source, identifier = key.split(":", 1)
    if source == "amazon-ca":
        return f"https://www.amazon.ca/dp/{identifier}"
    slug = handle or identifier
    url = f"https://soylent.ca/products/{slug}"
    # Multi-variant: key is shopify-ca:PRODUCT_ID:VARIANT_ID
    parts = identifier.split(":")
    if len(parts) == 2:
        url += f"?variant={parts[1]}"
    return url


def mask_phone(phone: str) -> str:
    """Mask phone number for logging: +1555***4567."""
    if len(phone) >= 8:
        return phone[:4] + "***" + phone[-4:]
    return "***"


def format_phone(phone: str) -> str:
    """Format +15551234567 as +1 (555) 123-4567."""
    digits = re.sub(r'\D', '', phone)
    if len(digits) == 11 and digits.startswith("1"):
        return f"+1 ({digits[1:4]}) {digits[4:7]}-{digits[7:]}"
    return phone


def relative_time(iso_str: str | None) -> str:
    """Convert ISO timestamp to human-readable relative time."""
    if not iso_str:
        return "unknown"
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        diff = now - dt
        seconds = int(diff.total_seconds())
        if seconds < 0:
            return "just now"
        if seconds < 60:
            return "just now"
        minutes = seconds // 60
        if minutes < 60:
            return f"{minutes}m ago"
        hours = minutes // 60
        if hours < 24:
            return f"{hours}h ago"
        days = hours // 24
        if days < 30:
            return f"{days}d ago"
        return dt.strftime("%b %d")
    except Exception:
        return iso_str[:16]
