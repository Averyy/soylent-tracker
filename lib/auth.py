"""Authentication, session management, OTP verification, rate limiting, CSRF."""

import base64
import hashlib
import hmac
import logging
import os
import re
import secrets
import threading
import time

from fastapi import Request

from . import config

log = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# Session secret — persisted to .session_secret
# ──────────────────────────────────────────────

def _load_or_create_session_secret() -> str:
    """Load session secret from file, or generate and persist one."""
    env_secret = os.environ.get("SESSION_SECRET")
    if env_secret:
        return env_secret
    if config.SESSION_SECRET_FILE.exists():
        return config.SESSION_SECRET_FILE.read_text().strip()
    secret = secrets.token_hex(32)
    config.SESSION_SECRET_FILE.write_text(secret)
    config.SESSION_SECRET_FILE.chmod(0o600)
    log.info("Generated new session secret")
    return secret


SESSION_SECRET = _load_or_create_session_secret()


# ──────────────────────────────────────────────
# In-memory state
# ──────────────────────────────────────────────

# Pending verification codes: {phone: {"code": "1234", "expires": timestamp, "attempts": 0}}
pending_codes: dict[str, dict] = {}
PENDING_CODES_CAP = 1_000

# Rate limiter: per-IP and per-phone, with global SMS throttle
rate_limiter: dict[str, dict] = {}
RATE_LIMIT_WINDOW = 15 * 60       # 15 minutes
RATE_LIMIT_MAX_IP = 10            # max attempts per IP per window
RATE_LIMIT_MAX_PHONE = 5          # max attempts per phone per window
RATE_LIMITER_CAP = 10_000         # hard cap on tracked keys

# Global SMS throttle — max 1 SMS every N seconds across all users
GLOBAL_SMS_MIN_INTERVAL = 2.0
_last_sms_time: float = 0.0

# Lock for in-memory state (OTP codes, rate limiter, SMS throttle).
_mem_lock = threading.RLock()

_purge_counter = 0

# Error codes for URL-safe redirects (avoid free-text in query strings)
ERROR_MESSAGES = {
    "invalid_phone": "Please enter a valid 10-digit phone number",
    "send_failed": "Unable to send code. Please try again later.",
    "sms_failed": "Failed to send verification code. Please try again.",
    "no_pending": "No pending code. Please try again.",
    "expired": "Code expired. Please try again.",
    "busy": "Server is busy. Please try again in a minute.",
}


# ──────────────────────────────────────────────
# Purging
# ──────────────────────────────────────────────

def purge_expired() -> None:
    """Purge expired entries from pending_codes and rate_limiter."""
    with _mem_lock:
        now = time.time()
        expired_codes = [k for k, v in pending_codes.items() if now > v["expires"]]
        for k in expired_codes:
            del pending_codes[k]
        expired_rl = [k for k, v in rate_limiter.items() if now - v["first_attempt"] > RATE_LIMIT_WINDOW]
        for k in expired_rl:
            del rate_limiter[k]


def maybe_purge() -> None:
    """Increment counter and purge every 100 calls. Used by middleware."""
    global _purge_counter
    with _mem_lock:
        _purge_counter += 1
        if _purge_counter >= 100:
            _purge_counter = 0
            do_purge = True
        else:
            do_purge = False
    if do_purge:
        purge_expired()


# ──────────────────────────────────────────────
# Rate limiting
# ──────────────────────────────────────────────

def _is_trusted_proxy(ip: str) -> bool:
    """Check if IP is a trusted reverse proxy (localhost or Docker bridge network)."""
    if ip in ("127.0.0.1", "::1"):
        return True
    # Docker bridge networks use 172.16.0.0/12
    if ip.startswith("172.") and 16 <= int(ip.split(".")[1]) <= 31:
        return True
    return False


def get_client_ip(request: Request) -> str:
    """Get the real client IP. Trusts X-Forwarded-For from local and Docker proxies."""
    direct_ip = request.client.host if request.client else "unknown"
    if _is_trusted_proxy(direct_ip):
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return direct_ip


def check_rate_limit(request: Request, phone: str | None = None) -> bool:
    """Check if request is rate limited. Returns True if allowed, False if blocked."""
    with _mem_lock:
        now = time.time()

        expired = [k for k, v in rate_limiter.items() if now - v["first_attempt"] > RATE_LIMIT_WINDOW]
        for k in expired:
            del rate_limiter[k]

        if len(rate_limiter) >= RATE_LIMITER_CAP:
            return False

        ip = get_client_ip(request)
        ip_key = f"ip:{ip}"
        ip_entry = rate_limiter.get(ip_key)
        if not ip_entry or now - ip_entry["first_attempt"] > RATE_LIMIT_WINDOW:
            rate_limiter[ip_key] = {"attempts": 1, "first_attempt": now}
        else:
            ip_entry["attempts"] += 1
            if ip_entry["attempts"] > RATE_LIMIT_MAX_IP:
                return False

        if phone:
            phone_key = f"phone:{phone}"
            phone_entry = rate_limiter.get(phone_key)
            if not phone_entry or now - phone_entry["first_attempt"] > RATE_LIMIT_WINDOW:
                rate_limiter[phone_key] = {"attempts": 1, "first_attempt": now}
            else:
                phone_entry["attempts"] += 1
                if phone_entry["attempts"] > RATE_LIMIT_MAX_PHONE:
                    return False

        return True


def check_global_sms_throttle() -> bool:
    """Global SMS send throttle. Returns True if allowed, False if too soon."""
    global _last_sms_time
    with _mem_lock:
        now = time.time()
        if now - _last_sms_time < GLOBAL_SMS_MIN_INTERVAL:
            return False
        _last_sms_time = now
        return True


def reset_sms_throttle():
    """Reset the SMS throttle after a failed send so the slot isn't wasted."""
    global _last_sms_time
    with _mem_lock:
        _last_sms_time = 0


# ──────────────────────────────────────────────
# Session signing / verification
# ──────────────────────────────────────────────

def _encrypt_phone(phone: str) -> str:
    """Encrypt phone using HMAC-derived one-time pad with random nonce."""
    nonce = secrets.token_bytes(16)
    pad = hmac.new(SESSION_SECRET.encode(), nonce, hashlib.sha256).digest()
    phone_bytes = phone.encode()
    encrypted = bytes(a ^ b for a, b in zip(phone_bytes, pad[:len(phone_bytes)]))
    return base64.urlsafe_b64encode(nonce + encrypted).decode().rstrip("=")


def _decrypt_phone(blob: str) -> str | None:
    """Decrypt phone from HMAC-derived one-time pad."""
    # Re-add base64 padding
    padded = blob + "=" * (-len(blob) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded)
    except Exception:
        return None
    if len(raw) < 17:  # 16 nonce + at least 1 byte
        return None
    nonce, encrypted = raw[:16], raw[16:]
    pad = hmac.new(SESSION_SECRET.encode(), nonce, hashlib.sha256).digest()
    phone_bytes = bytes(a ^ b for a, b in zip(encrypted, pad[:len(encrypted)]))
    try:
        return phone_bytes.decode()
    except UnicodeDecodeError:
        return None


def sign_session(phone: str) -> str:
    """Create a signed session token with encrypted phone."""
    encrypted = _encrypt_phone(phone)
    payload = f"{encrypted}:{int(time.time())}"
    sig = hmac.new(SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{payload}:{sig}"


_E164_RE = re.compile(r"^\+1\d{10}$")


def verify_session(token: str) -> str | None:
    """Verify a session token. Returns phone if valid, None otherwise."""
    parts = token.split(":")
    if len(parts) != 3:
        return None
    encrypted, ts_str, sig = parts
    try:
        ts = int(ts_str)
    except ValueError:
        return None
    if time.time() - ts > config.SESSION_MAX_AGE:
        return None
    payload = f"{encrypted}:{ts_str}"
    expected_sig = hmac.new(SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(sig, expected_sig):
        return None
    phone = _decrypt_phone(encrypted)
    if phone and not _E164_RE.match(phone):
        return None
    return phone


def get_session_phone(request: Request) -> str | None:
    token = request.cookies.get(config.SESSION_COOKIE)
    if not token:
        return None
    return verify_session(token)


# ──────────────────────────────────────────────
# CSRF
# ──────────────────────────────────────────────

def get_csrf_token(request: Request, cookie_name: str | None = None) -> str:
    """Get or generate a per-session CSRF token from a session cookie."""
    if cookie_name is None:
        cookie_name = config.SESSION_COOKIE
    token = request.cookies.get(cookie_name, "")
    if not token:
        return ""
    return hmac.new(SESSION_SECRET.encode(), f"csrf:{token}".encode(), hashlib.sha256).hexdigest()[:16]


def check_csrf(request: Request, token: str, cookie_name: str | None = None) -> bool:
    """Validate a CSRF token against a session cookie."""
    if cookie_name is None:
        cookie_name = config.SESSION_COOKIE
    expected = get_csrf_token(request, cookie_name)
    if not expected:
        return False
    return hmac.compare_digest(token, expected)


# ──────────────────────────────────────────────
# OTP verification
# ──────────────────────────────────────────────

def verify_otp(phone_key: str, code: str) -> str:
    """Shared OTP verification logic.

    Returns: "success", "no_pending", "expired", "max_attempts", or "wrong_code".
    """
    with _mem_lock:
        pending = pending_codes.get(phone_key)
        if not pending:
            return "no_pending"
        if time.time() > pending["expires"]:
            del pending_codes[phone_key]
            return "expired"
        if pending["attempts"] >= config.OTP_MAX_ATTEMPTS:
            del pending_codes[phone_key]
            return "max_attempts"
        if not hmac.compare_digest(code, pending["code"]):
            pending["attempts"] += 1
            if pending["attempts"] >= config.OTP_MAX_ATTEMPTS:
                del pending_codes[phone_key]
                return "max_attempts"
            return "wrong_code"
        del pending_codes[phone_key]
        return "success"


# ──────────────────────────────────────────────
# Admin session
# ──────────────────────────────────────────────

def sign_admin_session() -> str:
    """Create a signed admin session token with encrypted phone."""
    encrypted = _encrypt_phone(config.ADMIN_PHONE)
    payload = f"admin:{encrypted}:{int(time.time())}"
    sig = hmac.new(SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{payload}:{sig}"


def verify_admin_session(token: str) -> bool:
    """Verify an admin session token. Invalidates if ADMIN_PHONE changes."""
    parts = token.split(":")
    if len(parts) != 4 or parts[0] != "admin":
        return False
    _, encrypted, ts_str, sig = parts
    try:
        ts = int(ts_str)
    except ValueError:
        return False
    if time.time() - ts > config.SESSION_MAX_AGE:
        return False
    payload = f"admin:{encrypted}:{ts_str}"
    expected_sig = hmac.new(SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(sig, expected_sig):
        return False
    phone = _decrypt_phone(encrypted)
    return phone == config.ADMIN_PHONE


def is_admin(request: Request) -> bool:
    """Check if the request has a valid admin session."""
    token = request.cookies.get(config.ADMIN_COOKIE)
    if not token:
        return False
    return verify_admin_session(token)


def get_admin_csrf(request: Request) -> str:
    return get_csrf_token(request, config.ADMIN_COOKIE)


def check_admin_csrf(request: Request, token: str) -> bool:
    return check_csrf(request, token, config.ADMIN_COOKIE)
