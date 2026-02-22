"""FastAPI web app for the Soylent Stock Tracker.

Routes:
  GET /           -- login page (enter phone number)
  POST /send-code -- send verification code via SMS
  POST /verify    -- submit verification code
  GET /tracker  -- stock status + subscription toggles (authenticated)
  GET /buy        -- public buy page (in-stock products only, no auth)
  POST /subscribe -- toggle product subscription (HTMX partial)
  POST /toggle-notifications -- toggle notifications (HTMX partial)
  POST /logout    -- clear session

NOTE: This app uses in-memory state for OTP codes and rate limiting.
It must run as a single worker process (no multi-worker deployment).
"""

import asyncio
import hashlib
import logging
import os
import random
import re
import secrets
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request, Response, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import lib.config as config
from lib.auth import (
    ERROR_MESSAGES,
    PENDING_CODES_CAP,
    _mem_lock,
    check_admin_csrf,
    check_csrf,
    check_global_sms_throttle,
    check_rate_limit,
    get_admin_csrf,
    get_csrf_token,
    get_session_phone,
    is_admin,
    maybe_purge,
    pending_codes,
    purge_expired,
    reset_sms_throttle,
    sign_admin_session,
    sign_session,
    verify_otp,
)
from lib.helpers import format_phone, mask_phone, normalize_phone, product_url, relative_time
from lib.history import load_history
from lib.notifications import send_sms, format_notification, get_sms_stats
from lib.products import build_products
from lib.registry import display_name, is_hidden
from lib.state import load_state
from lib.scheduler import start_checkers, stop_checkers
from lib.users import find_user, load_users, locked_users

# ──────────────────────────────────────────────
# Logging — stdout + file
# ──────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger(__name__)


def _check_single_worker():
    """Warn if multi-worker deployment detected. In-memory OTP/rate-limit state
    requires a single worker process — multiple workers silently break auth."""
    workers = os.environ.get("WEB_CONCURRENCY")
    if workers and workers.isdigit() and int(workers) > 1:
        log.error(
            "WEB_CONCURRENCY=%s detected — this app MUST run with a single worker. "
            "In-memory OTP codes and rate limits are not shared across workers.", workers
        )
        raise SystemExit(1)


@asynccontextmanager
async def lifespan(app):
    _check_single_worker()
    checker_tasks = await start_checkers()
    yield
    stop_checkers()
    if checker_tasks:
        try:
            results = await asyncio.wait_for(
                asyncio.gather(*checker_tasks, return_exceptions=True),
                timeout=30,
            )
            for r in results:
                if isinstance(r, Exception):
                    log.error("Checker thread error during shutdown", exc_info=r)
        except TimeoutError:
            log.warning("Checker threads did not stop within 30s")


app = FastAPI(
    title="Soylent Stock Tracker",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    maybe_purge()
    response = await call_next(request)
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "font-src 'self'; "
        "connect-src 'self'; "
        "frame-ancestors 'none'"
    )
    if os.environ.get("SECURE_COOKIES", "1") != "0":
        response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
    return response


@app.get("/health")
def health():
    return {"status": "ok"}


app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

_static_dir = Path(__file__).parent / "static"
_asset_hashes: dict[str, str] = {}
_asset_mtimes: dict[str, float] = {}
_is_dev = os.environ.get("DEV_MODE") == "1"

def _asset_hash(path: str) -> str:
    """Return short content hash for cache-busting static assets.

    In production, hashes are cached permanently (files don't change between deploys).
    In dev mode, uses mtime to detect changes.
    """
    if path in _asset_hashes and not _is_dev:
        return _asset_hashes[path]
    fp = _static_dir / path.lstrip("/")
    try:
        mtime = fp.stat().st_mtime
    except FileNotFoundError:
        return "0"
    if _asset_mtimes.get(path) == mtime and path in _asset_hashes:
        return _asset_hashes[path]
    _asset_hashes[path] = hashlib.md5(fp.read_bytes()).hexdigest()[:8]
    _asset_mtimes[path] = mtime
    return _asset_hashes[path]

templates.env.globals["asset_hash"] = _asset_hash


# ──────────────────────────────────────────────
# Shared helpers
# ──────────────────────────────────────────────

def _send_otp_code(phone: str, code_key: str) -> str | None:
    """Store a new OTP code and send it via SMS.

    Returns None on success, or an error code: "cooldown", "busy", "sms_failed".
    """
    is_dev_bypass = os.environ.get("DEV_MODE") == "1" and phone == "+15555555555"
    code_to_send = None

    with _mem_lock:
        if len(pending_codes) >= PENDING_CODES_CAP:
            return "busy"
        existing = pending_codes.get(code_key)
        if existing and existing["expires"] - config.OTP_EXPIRY + config.OTP_SEND_COOLDOWN > time.time():
            return "cooldown"
        if is_dev_bypass:
            pending_codes[code_key] = {"code": "5555", "expires": time.time() + config.OTP_EXPIRY, "attempts": 0}
        else:
            if not check_global_sms_throttle():
                return "busy"
            code_to_send = f"{secrets.randbelow(10000):04d}"
            pending_codes[code_key] = {"code": code_to_send, "expires": time.time() + config.OTP_EXPIRY, "attempts": 0}

    if code_to_send is not None:
        message = f"{code_to_send} is your Soylent Tracker code"
        sent = send_sms(phone, message)
        if not sent:
            reset_sms_throttle()
            with _mem_lock:
                pending_codes.pop(code_key, None)
            log.warning(f"Failed to send SMS to {mask_phone(phone)}")
            return "sms_failed"

    return None


def _render_admin_users(request: Request, error: str = ""):
    """Render the admin users partial (shared by add/remove/rename)."""
    users = load_users()
    for u in users:
        u["phone_display"] = format_phone(u["phone"])
    return templates.TemplateResponse(request, "partials/admin_users.html", {
        "users": users,
        "csrf_token": get_admin_csrf(request),
        "admin_phone": config.ADMIN_PHONE,
        "error": error,
    })


# ──────────────────────────────────────────────
# Routes — plain def (not async) since all do blocking I/O
# ──────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def login_page(request: Request):
    phone = get_session_phone(request)
    if phone:
        return RedirectResponse("/tracker", status_code=303)
    error_code = request.query_params.get("error", "")
    error = ERROR_MESSAGES.get(error_code, "")
    return templates.TemplateResponse(request, "login.html", {"error": error})


@app.post("/send-code", response_class=HTMLResponse)
def send_code(request: Request, phone: str = Form(...)):
    purge_expired()

    phone = normalize_phone(phone)
    if not phone:
        return RedirectResponse("/?error=invalid_phone", status_code=303)

    # Rate limit by IP + phone (check before user lookup to avoid timing leak)
    if not check_rate_limit(request, phone=phone):
        return RedirectResponse("/?error=send_failed", status_code=303)

    user = find_user(phone)

    if not user:
        # Sleep to mask timing difference vs SMS-sending path (match typical Twilio latency)
        time.sleep(random.uniform(0.5, 2.0))
        return RedirectResponse("/?error=send_failed", status_code=303)

    err = _send_otp_code(phone, phone)
    if err == "cooldown":
        # Code was sent recently — show verify page without resending
        pass
    elif err:
        return RedirectResponse(f"/?error={err}", status_code=303)

    return templates.TemplateResponse(request, "verify.html", {
        "phone": phone, "phone_display": format_phone(phone), "error": "",
    })


@app.post("/verify")
def verify_code(request: Request, phone: str = Form(...), code: str = Form(...)):
    purge_expired()
    phone = phone.strip()
    code = code.strip()

    # Validate hidden phone field (user-controllable)
    if not re.fullmatch(r'\+1\d{10}', phone):
        return RedirectResponse("/", status_code=303)

    def _code_error(msg: str, form_disabled: bool = False):
        return templates.TemplateResponse(request, "verify.html", {
            "phone": phone, "phone_display": format_phone(phone),
            "error": msg, "form_disabled": form_disabled,
        })

    if not check_rate_limit(request, phone=phone):
        return _code_error("Too many attempts. Please try again later.", form_disabled=True)

    result = verify_otp(phone, code)
    if result == "no_pending":
        return _code_error("No pending code. Please go back and request a new one.", form_disabled=True)
    if result == "expired":
        return _code_error("Code expired. Please go back and request a new one.", form_disabled=True)
    if result == "max_attempts":
        return _code_error("Too many attempts. Please go back and request a new code.", form_disabled=True)
    if result == "wrong_code":
        return _code_error("Incorrect code. Please try again.")

    with locked_users() as users:
        for user in users:
            if user["phone"] == phone:
                user["last_login"] = datetime.now(timezone.utc).isoformat()
                break
    token = sign_session(phone)
    response = RedirectResponse("/tracker", status_code=303)
    response.set_cookie(
        config.SESSION_COOKIE, token,
        max_age=config.SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=os.environ.get("SECURE_COOKIES", "1") != "0",
    )
    return response


@app.get("/tracker", response_class=HTMLResponse)
def dashboard(request: Request):
    phone = get_session_phone(request)
    if not phone:
        return RedirectResponse("/", status_code=303)
    user = find_user(phone)
    if not user:
        return RedirectResponse("/", status_code=303)

    state = load_state()
    subscriptions = set(user.get("subscriptions", []))
    notif_enabled = user.get("notifications_enabled", True)
    product_groups = build_products(state, subscriptions)

    sub_count = len(subscriptions)
    sub_note = f"{sub_count} subscription{'s' if sub_count != 1 else ''}"

    return templates.TemplateResponse(request, "dashboard.html", {
        "phone_display": format_phone(phone),
        "display_name": user.get("name") or format_phone(phone),
        "sub_note": sub_note,
        "notif_enabled": notif_enabled,
        "drinks": product_groups["drinks"],
        "powder": product_groups["powder"],
        "prepaid": product_groups["prepaid"],
        "accessories": product_groups["accessories"],
        "csrf_token": get_csrf_token(request),
    })


@app.get("/buy", response_class=HTMLResponse)
def buy_page(request: Request):
    state = load_state()
    product_groups = build_products(state, set())

    # In-stock only; exclude gift cards, powder scoop, blender bottle
    _EXCLUDED_BUY = {"powder scoop", "blender bottle"}

    def _buy_ok(p: dict) -> bool:
        if not p["available"] or p.get("is_gift_card"):
            return False
        return not any(exc in p["title"].lower() for exc in _EXCLUDED_BUY)

    drinks = [p for p in product_groups["drinks"] if _buy_ok(p)]
    powder = [p for p in product_groups["powder"] if _buy_ok(p)]
    prepaid = [p for p in product_groups["prepaid"] if _buy_ok(p)]
    accessories = [p for p in product_groups["accessories"] if _buy_ok(p)]

    all_products = drinks + powder + prepaid + accessories

    # Find the actual most recent last_checked ISO timestamp from state
    latest_ts = ""
    for p in all_products:
        ts = state.get(p["key"], {}).get("last_checked", "")
        if ts > latest_ts:
            latest_ts = ts
    last_updated = relative_time(latest_ts) if latest_ts else "unknown"

    # ETag caching — includes titles, prices, inventory, and last_updated
    cache_parts = sorted(f"{p['key']}|{p['title']}|{p.get('price') or ''}|{p.get('detail') or ''}" for p in all_products)
    cache_parts.append(f"updated:{latest_ts}")
    etag = hashlib.md5(":".join(cache_parts).encode()).hexdigest()[:16]

    # 304 if client already has this version
    if_none_match = request.headers.get("if-none-match", "")
    client_etags = [t.strip().removeprefix("W/").strip('"') for t in if_none_match.split(",") if t.strip()]
    if etag in client_etags:
        return Response(status_code=304, headers={"ETag": f'"{etag}"', "Cache-Control": "no-cache"})

    logged_in = get_session_phone(request) is not None

    response = templates.TemplateResponse(request, "buy.html", {
        "drinks": drinks,
        "powder": powder,
        "prepaid": prepaid,
        "accessories": accessories,
        "logged_in": logged_in,
        "last_updated": last_updated,
    })
    response.headers["ETag"] = f'"{etag}"'
    response.headers["Cache-Control"] = "no-cache"
    return response


@app.post("/subscribe", response_class=HTMLResponse)
def toggle_subscription(
    request: Request,
    product_key: str = Form(...),
    csrf_token: str = Form(""),
):
    phone = get_session_phone(request)
    if not phone:
        return Response(status_code=403, headers={"HX-Redirect": "/"})

    if not check_csrf(request, csrf_token):
        return Response("Invalid request", status_code=403)

    # Validate product_key against known products
    state = load_state()
    if product_key not in state:
        return Response("Unknown product", status_code=400)

    subscribed = False
    sub_count = 0
    found = False
    with locked_users() as users:
        for user in users:
            if user["phone"] == phone:
                found = True
                subs = user.get("subscriptions", [])
                if product_key in subs:
                    subs.remove(product_key)
                else:
                    subs.append(product_key)
                    subscribed = True
                user["subscriptions"] = subs
                sub_count = len(subs)
                break

    if not found:
        return Response(status_code=403, headers={"HX-Redirect": "/"})

    sub_note = f"{sub_count} subscription{'s' if sub_count != 1 else ''}"

    # HTMX partial: toggle button + OOB sub_note update
    return templates.TemplateResponse(request, "partials/toggle_btn.html", {
        "subscribed": subscribed,
        "product_key": product_key,
        "csrf_token": get_csrf_token(request),
        "oob": True,
        "sub_note": sub_note,
    })


@app.post("/toggle-notifications", response_class=HTMLResponse)
def toggle_notifications(request: Request, csrf_token: str = Form("")):
    phone = get_session_phone(request)
    if not phone:
        return Response(status_code=403, headers={"HX-Redirect": "/"})

    if not check_csrf(request, csrf_token):
        return Response("Invalid request", status_code=403)

    notif_enabled = True
    found = False
    with locked_users() as users:
        for user in users:
            if user["phone"] == phone:
                found = True
                notif_enabled = not user.get("notifications_enabled", True)
                user["notifications_enabled"] = notif_enabled
                break

    if not found:
        return Response(status_code=403, headers={"HX-Redirect": "/"})

    # HTMX partial: return just the notification badge
    response = templates.TemplateResponse(request, "partials/notif_toggle.html", {
        "notif_enabled": notif_enabled,
        "csrf_token": get_csrf_token(request),
    })
    response.headers["HX-Trigger"] = "notifOn" if notif_enabled else "notifOff"
    return response


@app.post("/logout")
def logout(request: Request, csrf_token: str = Form("")):
    if not check_csrf(request, csrf_token):
        return Response("Invalid request", status_code=403)
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie(config.SESSION_COOKIE)
    return response


# ──────────────────────────────────────────────
# Admin routes — OTP-protected dashboard
# ──────────────────────────────────────────────

@app.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request):
    if not config.ADMIN_PHONE:
        return Response("Admin access not configured", status_code=404)
    if is_admin(request):
        return RedirectResponse("/admin/dashboard", status_code=303)
    error_code = request.query_params.get("error", "")
    error = ERROR_MESSAGES.get(error_code, "")
    return templates.TemplateResponse(request, "admin_login.html", {"error": error})


@app.post("/admin/send-code")
def admin_send_code(request: Request, phone: str = Form(...)):
    if not config.ADMIN_PHONE:
        return Response("Admin access not configured", status_code=404)

    # Normalize input and verify it matches the admin phone
    phone = normalize_phone(phone)
    if not phone or phone != config.ADMIN_PHONE:
        return RedirectResponse("/admin?error=send_failed", status_code=303)

    # Rate limit by IP + phone
    if not check_rate_limit(request, phone=config.ADMIN_PHONE):
        return RedirectResponse("/admin?error=send_failed", status_code=303)

    purge_expired()

    admin_key = f"admin:{config.ADMIN_PHONE}"
    err = _send_otp_code(config.ADMIN_PHONE, admin_key)
    if err == "cooldown":
        pass  # Code was sent recently — show verify page without resending
    elif err:
        return RedirectResponse(f"/admin?error={err}", status_code=303)

    return templates.TemplateResponse(request, "admin_verify.html", {"error": ""})


@app.post("/admin/verify")
def admin_verify(request: Request, code: str = Form(...)):
    if not config.ADMIN_PHONE:
        return Response("Admin access not configured", status_code=404)

    purge_expired()
    code = code.strip()
    admin_key = f"admin:{config.ADMIN_PHONE}"

    def _admin_error(msg: str, form_disabled: bool = False):
        return templates.TemplateResponse(request, "admin_verify.html", {
            "error": msg, "form_disabled": form_disabled,
        })

    if not check_rate_limit(request, phone=config.ADMIN_PHONE):
        return _admin_error("Too many attempts. Please try again later.", form_disabled=True)

    result = verify_otp(admin_key, code)
    if result == "no_pending":
        return RedirectResponse("/admin?error=no_pending", status_code=303)
    if result == "expired":
        return RedirectResponse("/admin?error=expired", status_code=303)
    if result == "max_attempts":
        return _admin_error("Too many attempts. Go back and request a new code.", form_disabled=True)
    if result == "wrong_code":
        return _admin_error("Incorrect code. Please try again.")

    token = sign_admin_session()
    response = RedirectResponse("/admin/dashboard", status_code=303)
    response.set_cookie(
        config.ADMIN_COOKIE, token,
        max_age=config.SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=os.environ.get("SECURE_COOKIES", "1") != "0",
    )
    return response


@app.get("/admin/dashboard", response_class=HTMLResponse)
def admin_dashboard(request: Request):
    if not is_admin(request):
        return RedirectResponse("/admin", status_code=303)

    users = load_users()
    state = load_state()
    history = load_history(limit=50)

    # Filter history: exclude always-hidden products and gift cards
    filtered_history = []
    for event in history:
        pk = event.get("product_key", "")
        if is_hidden(pk, True):
            continue
        info = state.get(pk, {})
        if info.get("product_type") == "Gift Card":
            continue
        event["relative_time"] = relative_time(event.get("timestamp", ""))
        filtered_history.append(event)

    # Summary stats (trackable products only)
    trackable = {k: v for k, v in state.items()
                 if not is_hidden(k, True) and v.get("product_type") != "Gift Card"}
    total_products = len(trackable)
    in_stock = sum(1 for v in trackable.values() if v.get("available"))

    # Product list for test tools — filtered, sorted by display name
    products = []
    for key, info in trackable.items():
        name, _ = display_name(key, info.get("title", key.split(":", 1)[1]))
        products.append({"key": key, "name": name})
    products.sort(key=lambda p: p["name"].lower())

    # Enrich users with formatted phone
    for u in users:
        u["phone_display"] = format_phone(u["phone"])

    sms_stats = get_sms_stats()

    # Enrich SMS per-phone stats with names, formatted numbers, last message
    phone_lookup = {u["phone"]: u.get("name", "") for u in users}
    last_messages = sms_stats.get("last_message", {})
    sms_by_user = []
    for phone, count in sms_stats["by_phone"].items():
        entry = last_messages.get(phone, {})
        # Handle legacy format (plain string) and new format (dict with text/at)
        if isinstance(entry, str):
            last_msg, last_at = entry, None
        else:
            last_msg = entry.get("text", "")
            last_at = entry.get("at")
        last_time = ""
        if last_at:
            dt = datetime.fromtimestamp(last_at, tz=timezone.utc).astimezone()
            last_time = dt.strftime("%b %-d, %-I:%M%p").replace("AM", "am").replace("PM", "pm")
        sms_by_user.append({
            "phone": phone,
            "phone_display": format_phone(phone),
            "name": phone_lookup.get(phone, ""),
            "count": count,
            "last_msg": last_msg,
            "last_time": last_time,
            "last_at": last_at or 0,
        })
    sms_by_user.sort(key=lambda x: x["last_at"], reverse=True)
    sms_stats["by_user"] = sms_by_user

    return templates.TemplateResponse(request, "admin_dashboard.html", {
        "users": users,
        "history": filtered_history,
        "total_products": total_products,
        "in_stock": in_stock,
        "out_of_stock": total_products - in_stock,
        "products": products,
        "csrf_token": get_admin_csrf(request),
        "admin_phone": config.ADMIN_PHONE,
        "error": "",
        "sms_stats": sms_stats,
    })


@app.post("/admin/test-sms", response_class=HTMLResponse)
def admin_test_sms(request: Request, csrf_token: str = Form("")):
    if not is_admin(request):
        return Response(status_code=403)
    if not check_admin_csrf(request, csrf_token):
        return Response("Invalid request", status_code=403)

    message = "Soylent Tracker test \u2014 SMS is working"
    sent = send_sms(config.ADMIN_PHONE, message)

    return templates.TemplateResponse(request, "partials/admin_tool_result.html", {
        "success": sent,
        "message_preview": message,
        "error": "Failed to send SMS. Check Twilio configuration." if not sent else "",
    })


@app.post("/admin/test-notify", response_class=HTMLResponse)
def admin_test_notify(request: Request, csrf_token: str = Form(""), product_keys: list[str] = Form([])):
    if not is_admin(request):
        return Response(status_code=403)
    if not check_admin_csrf(request, csrf_token):
        return Response("Invalid request", status_code=403)

    if not product_keys:
        return templates.TemplateResponse(request, "partials/admin_tool_result.html", {
            "success": False,
            "message_preview": "",
            "error": "No products selected.",
        })

    state = load_state()
    changes = []
    for key in product_keys:
        info = state.get(key)
        if not info:
            continue
        changes.append({
            "key": key,
            "available": True,
            "title": info.get("title", key.split(":", 1)[1]),
            "handle": info.get("handle"),
        })

    if not changes:
        return templates.TemplateResponse(request, "partials/admin_tool_result.html", {
            "success": False,
            "message_preview": "",
            "error": "No valid products found.",
        })

    # Format the notification message and send to admin only — never to subscribers
    message = format_notification(changes)
    sent = send_sms(config.ADMIN_PHONE, message)

    return templates.TemplateResponse(request, "partials/admin_tool_result.html", {
        "success": sent,
        "message_preview": message,
        "error": "Failed to send SMS. Check Twilio configuration." if not sent else "",
    })


@app.post("/admin/add-user", response_class=HTMLResponse)
def admin_add_user(request: Request, csrf_token: str = Form(""), phone: str = Form(...), name: str = Form("")):
    if not is_admin(request):
        return Response(status_code=403)
    if not check_admin_csrf(request, csrf_token):
        return Response("Invalid request", status_code=403)

    normalized = normalize_phone(phone)
    error = ""
    if not normalized:
        error = "Invalid phone number."
    else:
        if find_user(normalized):
            error = "User already exists."
        else:
            with locked_users() as users:
                # Re-check under lock
                if any(u["phone"] == normalized for u in users):
                    error = "User already exists."
                else:
                    users.append({
                        "phone": normalized,
                        "name": name.strip()[:50],
                        "notifications_enabled": True,
                        "subscriptions": [],
                    })
                    log.info(f"Admin added user {mask_phone(normalized)}")

    return _render_admin_users(request, error)


@app.post("/admin/remove-user", response_class=HTMLResponse)
def admin_remove_user(request: Request, csrf_token: str = Form(""), phone: str = Form(...)):
    if not is_admin(request):
        return Response(status_code=403)
    if not check_admin_csrf(request, csrf_token):
        return Response("Invalid request", status_code=403)

    error = ""
    if phone == config.ADMIN_PHONE:
        error = "Cannot remove the admin user."
    else:
        with locked_users() as users:
            before = len(users)
            users[:] = [u for u in users if u["phone"] != phone]
            if len(users) == before:
                error = "User not found."
            else:
                log.info(f"Admin removed user {mask_phone(phone)}")

    return _render_admin_users(request, error)


@app.post("/admin/rename-user", response_class=HTMLResponse)
def admin_rename_user(request: Request, csrf_token: str = Form(""), phone: str = Form(...), name: str = Form("")):
    if not is_admin(request):
        return Response(status_code=403)
    if not check_admin_csrf(request, csrf_token):
        return Response("Invalid request", status_code=403)

    error = ""
    new_name = name.strip()[:50]
    with locked_users() as users:
        found = False
        for u in users:
            if u["phone"] == phone:
                u["name"] = new_name
                found = True
                break
        if not found:
            error = "User not found."
        else:
            log.info(f"Admin renamed user {mask_phone(phone)} to {new_name!r}")

    return _render_admin_users(request, error)


@app.post("/admin/logout")
def admin_logout(request: Request, csrf_token: str = Form("")):
    if not check_admin_csrf(request, csrf_token):
        return Response("Invalid request", status_code=403)
    response = RedirectResponse("/admin", status_code=303)
    response.delete_cookie(config.ADMIN_COOKIE)
    return response
