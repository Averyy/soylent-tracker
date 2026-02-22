"""Centralized configuration with env var overrides.

All hardcoded values live here. Override any via environment variables.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent

load_dotenv(PROJECT_ROOT / ".env")

# ── Paths ──
STATE_FILE = Path(os.environ.get("STATE_FILE", str(PROJECT_ROOT / "state.json")))
USERS_FILE = Path(os.environ.get("USERS_FILE", str(PROJECT_ROOT / "users.json")))
HISTORY_FILE = Path(os.environ.get("HISTORY_FILE", str(PROJECT_ROOT / "history.json")))
LOG_FILE = Path(os.environ.get("LOG_FILE", str(PROJECT_ROOT / "soylent-tracker.log")))
SESSION_SECRET_FILE = Path(os.environ.get("SESSION_SECRET_FILE", str(PROJECT_ROOT / ".session_secret")))
SHOPIFY_ETAG_FILE = Path(os.environ.get("SHOPIFY_ETAG_FILE", str(PROJECT_ROOT / ".shopify_etag")))

# ── Session ──
SESSION_COOKIE = "soylent_session"
SESSION_MAX_AGE = int(os.environ.get("SESSION_MAX_AGE", 30 * 24 * 60 * 60))  # 30 days

# ── OTP ──
OTP_EXPIRY = int(os.environ.get("OTP_EXPIRY", 300))  # 5 minutes
OTP_MAX_ATTEMPTS = int(os.environ.get("OTP_MAX_ATTEMPTS", 5))
OTP_SEND_COOLDOWN = int(os.environ.get("OTP_SEND_COOLDOWN", 60))  # seconds

# ── Admin ──
_raw_admin_phone = os.environ.get("ADMIN_PHONE", "").strip()
ADMIN_PHONE = f"+{_raw_admin_phone}" if _raw_admin_phone and not _raw_admin_phone.startswith("+") else _raw_admin_phone
ADMIN_COOKIE = "soylent_admin"

# ── History ──
MAX_HISTORY_ENTRIES = int(os.environ.get("MAX_HISTORY_ENTRIES", 1000))

# ── Product key prefixes ──
SOURCE_SHOPIFY_CA = "shopify-ca"
SOURCE_AMAZON_CA = "amazon-ca"

# ── Source display labels ──
SOURCE_LABELS = {
    SOURCE_SHOPIFY_CA: "soylent.ca",
    SOURCE_AMAZON_CA: "Amazon.ca",
}

# ── SMS Stats ──
SMS_STATS_FILE = Path(os.environ.get("SMS_STATS_FILE", str(PROJECT_ROOT / "sms_stats.json")))

# ── Twilio ──
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_API_KEY     = os.environ.get("TWILIO_API_KEY", "")      # SK... restricted key
TWILIO_API_SECRET  = os.environ.get("TWILIO_API_SECRET", "")
TWILIO_FROM        = os.environ.get("TWILIO_FROM", "")         # E.164 e.g. +15555555555
DAILY_SMS_CAP      = int(os.environ.get("DAILY_SMS_CAP", 200))

# Warn at import time if Twilio is partially configured
_twilio_vars = {"TWILIO_ACCOUNT_SID": TWILIO_ACCOUNT_SID, "TWILIO_API_KEY": TWILIO_API_KEY,
                "TWILIO_API_SECRET": TWILIO_API_SECRET, "TWILIO_FROM": TWILIO_FROM}
_twilio_set = {k for k, v in _twilio_vars.items() if v}
if _twilio_set and _twilio_set != set(_twilio_vars):
    import logging as _logging
    _logging.getLogger(__name__).warning(
        f"Twilio partially configured — missing: {', '.join(sorted(set(_twilio_vars) - _twilio_set))}. SMS will not work."
    )
