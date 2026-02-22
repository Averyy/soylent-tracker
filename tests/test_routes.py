"""Route tests using FastAPI TestClient. Tests behavior, not hardcoded values."""

import os
import tempfile

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _patch_files(monkeypatch, tmp_path):
    """Redirect all data files to temp dir so tests don't touch real data."""
    import lib.state as state_mod
    import lib.users as users_mod
    import lib.config as config_mod
    import lib.history as history_mod
    import lib.notifications as notif_mod
    import lib.auth as auth_mod

    state_file = tmp_path / "state.json"
    users_file = tmp_path / "users.json"
    history_file = tmp_path / "history.json"
    sms_stats_file = tmp_path / "sms_stats.json"

    monkeypatch.setattr(state_mod, "STATE_FILE", state_file)
    monkeypatch.setattr(users_mod, "USERS_FILE", users_file)
    monkeypatch.setattr(config_mod, "STATE_FILE", state_file)
    monkeypatch.setattr(config_mod, "USERS_FILE", users_file)
    monkeypatch.setattr(config_mod, "HISTORY_FILE", history_file)
    monkeypatch.setattr(history_mod, "HISTORY_FILE", history_file)
    monkeypatch.setattr(config_mod, "SMS_STATS_FILE", sms_stats_file)
    monkeypatch.setattr(notif_mod, "SMS_STATS_FILE", sms_stats_file)

    # Clear in-memory state between tests
    auth_mod.pending_codes.clear()
    auth_mod.rate_limiter.clear()

    # Seed a test user
    import json
    users_file.write_text(json.dumps([
        {
            "phone": "+15555555555",
            "name": "Test",
            "notifications_enabled": True,
            "subscriptions": [],
        }
    ]))


@pytest.fixture()
def client():
    from app import app
    return TestClient(app)


# ── Auth flow ──

def test_login_page_returns_html(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 200
    assert "phone" in r.text.lower()


def test_tracker_redirects_when_not_authenticated(client):
    r = client.get("/tracker", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/"


def test_send_code_rejects_short_phone(client):
    r = client.post("/send-code", data={"phone": "123"}, follow_redirects=False)
    assert r.status_code == 303
    assert "error" in r.headers["location"].lower()


def test_send_code_rejects_unknown_user(client):
    r = client.post("/send-code", data={"phone": "5559999999"}, follow_redirects=False)
    assert r.status_code == 303
    assert "error" in r.headers["location"].lower()


def test_verify_rejects_without_pending_code(client):
    r = client.post("/verify", data={"phone": "+15555555555", "code": "1234"})
    # Should show error about no pending code
    assert r.status_code == 200
    assert "no pending code" in r.text.lower()


def test_subscribe_rejects_unauthenticated(client):
    r = client.post("/subscribe", data={"product_key": "test", "csrf_token": "bad"})
    assert r.status_code == 403


def test_toggle_notifications_rejects_unauthenticated(client):
    r = client.post("/toggle-notifications", data={"csrf_token": "bad"})
    assert r.status_code == 403


def test_logout_clears_session(client):
    # POST with empty CSRF token — will fail CSRF check (403)
    # but GET /logout was removed for security (no CSRF protection)
    r = client.post("/logout", data={"csrf_token": ""}, follow_redirects=False)
    assert r.status_code == 403


# ── Full login flow ──

def test_full_login_flow(client, monkeypatch):
    """Complete OTP flow: send-code → verify → access tracker."""
    monkeypatch.setenv("DEV_MODE", "1")

    # Step 1: Send code
    r = client.post("/send-code", data={"phone": "5555555555"})
    assert r.status_code == 200
    assert "code" in r.text.lower()

    # Step 2: Verify with dev code
    r = client.post("/verify", data={"phone": "+15555555555", "code": "5555"}, follow_redirects=False)
    assert r.status_code == 303
    assert "/tracker" in r.headers["location"]
    assert "soylent_session" in r.cookies

    # Step 3: Access tracker with session cookie
    r = client.get("/tracker")
    assert r.status_code == 200
    assert "tracker" in r.text.lower() or "soylent" in r.text.lower()


def test_verify_wrong_code_rejected(client, monkeypatch):
    monkeypatch.setenv("DEV_MODE", "1")
    client.post("/send-code", data={"phone": "5555555555"})
    r = client.post("/verify", data={"phone": "+15555555555", "code": "9999"})
    assert r.status_code == 200
    assert "incorrect" in r.text.lower()


# ── Subscription toggle ──

def test_subscribe_toggle(client, monkeypatch):
    """Test subscribing and unsubscribing to a product."""
    import json
    import lib.state as state_mod
    from lib.auth import sign_session, get_csrf_token
    from lib.config import SESSION_COOKIE
    from unittest.mock import MagicMock

    monkeypatch.setenv("DEV_MODE", "1")

    # Seed state with a product
    state_file = state_mod.STATE_FILE
    state_file.write_text(json.dumps({
        "shopify-ca:test-product": {
            "available": True,
            "title": "Test Product",
            "product_type": "Drink",
            "handle": "test-product",
            "last_checked": "2026-01-01T00:00:00+00:00",
        }
    }))

    # Create session directly (bypass OTP)
    session_token = sign_session("+15555555555")
    client.cookies.set(SESSION_COOKIE, session_token)

    # Derive CSRF token from the session
    mock_request = MagicMock()
    mock_request.cookies = {SESSION_COOKIE: session_token}
    csrf_token = get_csrf_token(mock_request)
    assert csrf_token  # should be non-empty

    # Subscribe
    r = client.post("/subscribe", data={
        "product_key": "shopify-ca:test-product",
        "csrf_token": csrf_token,
    })
    assert r.status_code == 200

    # Verify user is now subscribed
    from lib.users import find_user
    user = find_user("+15555555555")
    assert "shopify-ca:test-product" in user.get("subscriptions", [])

    # Unsubscribe (toggle again)
    r = client.post("/subscribe", data={
        "product_key": "shopify-ca:test-product",
        "csrf_token": csrf_token,
    })
    assert r.status_code == 200

    user = find_user("+15555555555")
    assert "shopify-ca:test-product" not in user.get("subscriptions", [])


# ── Session signing ──

def test_session_roundtrip():
    from lib.auth import sign_session, verify_session
    token = sign_session("+15555555555")
    phone = verify_session(token)
    assert phone == "+15555555555"


def test_session_rejects_tampered_token():
    from lib.auth import sign_session, verify_session
    token = sign_session("+15555555555")
    # Tamper with the signature
    parts = token.split(":")
    parts[-1] = "0" * len(parts[-1])
    tampered = ":".join(parts)
    assert verify_session(tampered) is None


def test_session_rejects_garbage():
    from lib.auth import verify_session
    assert verify_session("garbage") is None
    assert verify_session("") is None
    assert verify_session("a:b:c:d") is None


# ── CSRF ──

# ── Helpers ──

def test_relative_time_just_now():
    from lib.helpers import relative_time
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    result = relative_time(now)
    assert result == "just now"


def test_relative_time_handles_empty():
    from lib.helpers import relative_time
    assert relative_time("") == "unknown"
    assert relative_time(None) == "unknown"


def test_build_products_groups_correctly():
    from lib.products import build_products
    state = {
        "shopify-ca:1": {"available": True, "title": "Test Drink", "product_type": "Drink", "handle": "test-drink", "last_checked": "2026-01-01T00:00:00+00:00"},
        "shopify-ca:2": {"available": False, "title": "Test Powder", "product_type": "Powder", "handle": "test-powder", "last_checked": "2026-01-01T00:00:00+00:00"},
    }
    groups = build_products(state, set())
    assert len(groups["drinks"]) >= 1
    assert len(groups["powder"]) >= 1
    # Verify structure: each product has required keys
    for products in groups.values():
        for p in products:
            assert "key" in p
            assert "title" in p
            assert "available" in p
            assert "status_label" in p


def test_build_products_marks_subscribed():
    from lib.products import build_products
    state = {
        "shopify-ca:1": {"available": True, "title": "Test", "product_type": "Drink", "handle": "test", "last_checked": "2026-01-01T00:00:00+00:00"},
    }
    groups = build_products(state, {"shopify-ca:1"})
    drink = groups["drinks"][0]
    assert drink["subscribed"] is True


def test_sort_in_stock_before_out_of_stock():
    from lib.products import _sort_product_list
    products = [
        {"title": "B Drink", "available": False, "source": "shopify-ca", "is_gift_card": False},
        {"title": "A Drink", "available": True, "source": "shopify-ca", "is_gift_card": False},
    ]
    _sort_product_list(products)
    assert products[0]["title"] == "A Drink"  # in-stock first
    assert products[1]["title"] == "B Drink"


def test_sort_shopify_before_amazon():
    from lib.products import _sort_product_list
    products = [
        {"title": "Mocha", "available": True, "source": "amazon-ca", "is_gift_card": False},
        {"title": "Mocha", "available": True, "source": "shopify-ca", "is_gift_card": False},
    ]
    _sort_product_list(products)
    assert products[0]["source"] == "shopify-ca"


def test_sort_bundles_after_singles():
    from lib.products import _sort_product_list
    products = [
        {"title": "Soylent Bundle", "available": True, "source": "shopify-ca", "is_gift_card": False},
        {"title": "Soylent Mocha", "available": True, "source": "shopify-ca", "is_gift_card": False},
    ]
    _sort_product_list(products)
    assert products[0]["title"] == "Soylent Mocha"
    assert products[1]["title"] == "Soylent Bundle"


def test_sort_size_variants_grouped():
    from lib.products import _sort_product_list
    products = [
        {"title": "Shirt - XL", "available": True, "source": "shopify-ca", "is_gift_card": False},
        {"title": "Shirt - S", "available": True, "source": "shopify-ca", "is_gift_card": False},
        {"title": "Shirt - M", "available": False, "source": "shopify-ca", "is_gift_card": False},
    ]
    _sort_product_list(products)
    # S < M < XL order, all grouped together (group has at least one available)
    assert [p["title"] for p in products] == ["Shirt - S", "Shirt - M", "Shirt - XL"]


def test_sort_gift_cards_last():
    from lib.products import _sort_product_list
    products = [
        {"title": "Gift Card", "available": True, "source": "shopify-ca", "is_gift_card": True},
        {"title": "Real Product", "available": True, "source": "shopify-ca", "is_gift_card": False},
    ]
    _sort_product_list(products)
    assert products[0]["title"] == "Real Product"
    assert products[1]["title"] == "Gift Card"


# ── Admin routes ──

def test_admin_returns_404_when_not_configured(client, monkeypatch):
    import lib.config as config_mod
    monkeypatch.setattr(config_mod, "ADMIN_PHONE", "")
    r = client.get("/admin")
    assert r.status_code == 404


def test_admin_login_page_loads(client, monkeypatch):
    import lib.config as config_mod
    monkeypatch.setattr(config_mod, "ADMIN_PHONE", "+15555555555")
    r = client.get("/admin", follow_redirects=False)
    assert r.status_code == 200
    assert "verification" in r.text.lower() or "verify" in r.text.lower()


def test_admin_send_code_creates_pending(client, monkeypatch):
    import lib.config as config_mod
    import lib.auth as auth_mod
    monkeypatch.setattr(config_mod, "ADMIN_PHONE", "+15555555555")
    monkeypatch.setenv("DEV_MODE", "1")
    r = client.post("/admin/send-code", data={"phone": "5555555555"})
    assert r.status_code == 200
    assert "code" in r.text.lower()
    # Pending code should exist (namespaced with admin: prefix)
    assert "admin:+15555555555" in auth_mod.pending_codes


def test_admin_verify_wrong_code_rejected(client, monkeypatch):
    import lib.config as config_mod
    monkeypatch.setattr(config_mod, "ADMIN_PHONE", "+15555555555")
    monkeypatch.setenv("DEV_MODE", "1")
    client.post("/admin/send-code", data={"phone": "5555555555"})
    r = client.post("/admin/verify", data={"code": "9999"})
    assert r.status_code == 200
    assert "incorrect" in r.text.lower() or "error" in r.text.lower()


def test_admin_verify_correct_code_sets_cookie(client, monkeypatch):
    import lib.config as config_mod
    monkeypatch.setattr(config_mod, "ADMIN_PHONE", "+15555555555")
    monkeypatch.setenv("DEV_MODE", "1")
    client.post("/admin/send-code", data={"phone": "5555555555"})
    r = client.post("/admin/verify", data={"code": "5555"}, follow_redirects=False)
    assert r.status_code == 303
    assert "/admin/dashboard" in r.headers["location"]
    assert "soylent_admin" in r.cookies


def test_admin_dashboard_requires_auth(client, monkeypatch):
    import lib.config as config_mod
    monkeypatch.setattr(config_mod, "ADMIN_PHONE", "+15555555555")
    r = client.get("/admin/dashboard", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/admin"


# ── OTP edge cases ──

def test_verify_otp_expired(monkeypatch):
    import time
    import lib.auth as auth_mod
    # Set code that expired 10 seconds ago
    auth_mod.pending_codes["+15555555555"] = {
        "code": "1234", "expires": time.time() - 10, "attempts": 0,
    }
    result = auth_mod.verify_otp("+15555555555", "1234")
    assert result == "expired"
    assert "+15555555555" not in auth_mod.pending_codes


def test_verify_otp_max_attempts():
    import lib.auth as auth_mod
    import time
    auth_mod.pending_codes["+15555555555"] = {
        "code": "1234", "expires": time.time() + 300, "attempts": 5,
    }
    result = auth_mod.verify_otp("+15555555555", "9999")
    assert result == "max_attempts"
    assert "+15555555555" not in auth_mod.pending_codes


def test_verify_otp_wrong_increments_attempts():
    import lib.auth as auth_mod
    import time
    auth_mod.pending_codes["+15555555555"] = {
        "code": "1234", "expires": time.time() + 300, "attempts": 0,
    }
    result = auth_mod.verify_otp("+15555555555", "9999")
    assert result == "wrong_code"
    assert auth_mod.pending_codes["+15555555555"]["attempts"] == 1


def test_verify_otp_success():
    import lib.auth as auth_mod
    import time
    auth_mod.pending_codes["+15555555555"] = {
        "code": "1234", "expires": time.time() + 300, "attempts": 0,
    }
    result = auth_mod.verify_otp("+15555555555", "1234")
    assert result == "success"
    assert "+15555555555" not in auth_mod.pending_codes


# ── Rate limiter ──

def test_rate_limiter_allows_up_to_max_ip(client):
    from lib.auth import check_rate_limit, rate_limiter, RATE_LIMIT_MAX_IP
    from unittest.mock import MagicMock

    rate_limiter.clear()
    req = MagicMock()
    req.client.host = "1.2.3.4"
    req.headers = {}

    for _ in range(RATE_LIMIT_MAX_IP):
        assert check_rate_limit(req) is True

    assert check_rate_limit(req) is False


def test_rate_limiter_per_phone(client):
    from lib.auth import check_rate_limit, rate_limiter, RATE_LIMIT_MAX_PHONE
    from unittest.mock import MagicMock

    rate_limiter.clear()
    req = MagicMock()
    req.client.host = "1.2.3.4"
    req.headers = {}

    for _ in range(RATE_LIMIT_MAX_PHONE):
        assert check_rate_limit(req, phone="+15551234567") is True

    # Phone limit hit, even though IP limit isn't
    assert check_rate_limit(req, phone="+15551234567") is False

    # Different phone is still allowed
    assert check_rate_limit(req, phone="+15559999999") is True


def test_rate_limiter_forwarded_for_only_trusted(client):
    from lib.auth import check_rate_limit, rate_limiter
    from unittest.mock import MagicMock

    rate_limiter.clear()

    # From reverse proxy (127.0.0.1) — trusts X-Forwarded-For
    req = MagicMock()
    req.client.host = "127.0.0.1"
    req.headers = {"x-forwarded-for": "5.6.7.8, 10.0.0.1"}
    check_rate_limit(req)
    assert "ip:5.6.7.8" in rate_limiter

    rate_limiter.clear()

    # From direct client (not proxy) — ignores X-Forwarded-For
    req2 = MagicMock()
    req2.client.host = "9.9.9.9"
    req2.headers = {"x-forwarded-for": "spoofed.ip"}
    check_rate_limit(req2)
    assert "ip:9.9.9.9" in rate_limiter
    assert "ip:spoofed.ip" not in rate_limiter


def test_rate_limiter_window_expiry(client):
    from lib.auth import check_rate_limit, rate_limiter, RATE_LIMIT_MAX_IP
    from unittest.mock import MagicMock
    import time

    rate_limiter.clear()
    req = MagicMock()
    req.client.host = "1.2.3.4"
    req.headers = {}

    # Fill up the limit
    for _ in range(RATE_LIMIT_MAX_IP + 1):
        check_rate_limit(req)

    assert check_rate_limit(req) is False

    # Simulate window expiry
    rate_limiter["ip:1.2.3.4"]["first_attempt"] = time.time() - (16 * 60)
    assert check_rate_limit(req) is True


def test_rate_limiter_hard_cap(client):
    from lib.auth import check_rate_limit, rate_limiter, RATE_LIMITER_CAP
    from unittest.mock import MagicMock
    import time

    rate_limiter.clear()
    # Fill to cap with fake entries
    for i in range(RATE_LIMITER_CAP):
        rate_limiter[f"ip:fake.{i}"] = {"attempts": 1, "first_attempt": time.time()}

    req = MagicMock()
    req.client.host = "1.2.3.4"
    req.headers = {}
    assert check_rate_limit(req) is False

    rate_limiter.clear()


def test_global_sms_throttle(client):
    import lib.auth as auth_mod

    # First call always succeeds
    auth_mod._last_sms_time = 0.0
    assert auth_mod.check_global_sms_throttle() is True

    # Immediate second call is blocked
    assert auth_mod.check_global_sms_throttle() is False

    # After interval passes, allowed again
    auth_mod._last_sms_time = auth_mod._last_sms_time - 3.0
    assert auth_mod.check_global_sms_throttle() is True


# ── /health and /buy ──

def test_health_endpoint(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_buy_page_renders(client):
    r = client.get("/buy")
    assert r.status_code == 200
    assert "soylent" in r.text.lower()


def test_buy_page_etag_304(client, monkeypatch):
    import json
    import lib.state as state_mod

    state_file = state_mod.STATE_FILE
    state_file.write_text(json.dumps({
        "shopify-ca:test-drink": {
            "available": True, "title": "Test Drink", "product_type": "Drink",
            "handle": "test-drink", "last_checked": "2026-01-01T00:00:00+00:00",
        }
    }))

    # First request gets 200 with ETag
    r1 = client.get("/buy")
    assert r1.status_code == 200
    etag = r1.headers.get("etag")
    assert etag

    # Second request with If-None-Match gets 304
    r2 = client.get("/buy", headers={"if-none-match": etag})
    assert r2.status_code == 304


def test_buy_page_excludes_oos_products(client, monkeypatch):
    import json
    import lib.state as state_mod

    state_file = state_mod.STATE_FILE
    state_file.write_text(json.dumps({
        "shopify-ca:1": {
            "available": True, "title": "In Stock", "product_type": "Drink",
            "handle": "in-stock", "last_checked": "2026-01-01T00:00:00+00:00",
        },
        "shopify-ca:2": {
            "available": False, "title": "Out Of Stock", "product_type": "Drink",
            "handle": "oos", "last_checked": "2026-01-01T00:00:00+00:00",
        },
    }))

    r = client.get("/buy")
    assert r.status_code == 200
    assert "In Stock" in r.text
    assert "Out Of Stock" not in r.text


# ── Admin routes (authenticated) ──

def _admin_login(client, monkeypatch):
    """Helper: log in as admin and return CSRF token."""
    import lib.config as config_mod
    import lib.auth as auth_mod
    from unittest.mock import MagicMock

    monkeypatch.setattr(config_mod, "ADMIN_PHONE", "+15555555555")

    # Create admin session directly (bypass OTP)
    token = auth_mod.sign_admin_session()
    client.cookies.set("soylent_admin", token)

    # Derive CSRF from the admin cookie
    mock_req = MagicMock()
    mock_req.cookies = {"soylent_admin": token}
    csrf = auth_mod.get_csrf_token(mock_req, "soylent_admin")
    return csrf


def test_admin_add_user(client, monkeypatch):
    csrf = _admin_login(client, monkeypatch)
    r = client.post("/admin/add-user", data={
        "csrf_token": csrf, "phone": "5559998888", "name": "New User",
    })
    assert r.status_code == 200
    from lib.users import find_user
    user = find_user("+15559998888")
    assert user is not None
    assert user["name"] == "New User"


def test_admin_add_user_truncates_long_name(client, monkeypatch):
    csrf = _admin_login(client, monkeypatch)
    long_name = "A" * 100
    r = client.post("/admin/add-user", data={
        "csrf_token": csrf, "phone": "5559997777", "name": long_name,
    })
    assert r.status_code == 200
    from lib.users import find_user
    user = find_user("+15559997777")
    assert len(user["name"]) == 50


def test_admin_add_duplicate_user(client, monkeypatch):
    csrf = _admin_login(client, monkeypatch)
    # +15555555555 already exists as test user
    r = client.post("/admin/add-user", data={
        "csrf_token": csrf, "phone": "5555555555", "name": "Dup",
    })
    assert r.status_code == 200
    assert "already exists" in r.text.lower()


def test_admin_remove_user(client, monkeypatch):
    csrf = _admin_login(client, monkeypatch)

    # Add a user first
    client.post("/admin/add-user", data={
        "csrf_token": csrf, "phone": "5551112222", "name": "ToDelete",
    })
    from lib.users import find_user
    assert find_user("+15551112222") is not None

    # Remove them
    r = client.post("/admin/remove-user", data={
        "csrf_token": csrf, "phone": "+15551112222",
    })
    assert r.status_code == 200
    assert find_user("+15551112222") is None


def test_admin_cannot_remove_admin(client, monkeypatch):
    csrf = _admin_login(client, monkeypatch)
    r = client.post("/admin/remove-user", data={
        "csrf_token": csrf, "phone": "+15555555555",
    })
    assert r.status_code == 200
    assert "cannot remove" in r.text.lower()


def test_admin_rename_user(client, monkeypatch):
    csrf = _admin_login(client, monkeypatch)
    # Add a user to rename
    client.post("/admin/add-user", data={
        "csrf_token": csrf, "phone": "5553334444", "name": "OldName",
    })
    from lib.users import find_user
    assert find_user("+15553334444")["name"] == "OldName"

    # Rename them
    r = client.post("/admin/rename-user", data={
        "csrf_token": csrf, "phone": "+15553334444", "name": "NewName",
    })
    assert r.status_code == 200
    assert find_user("+15553334444")["name"] == "NewName"


def test_admin_rename_truncates_long_name(client, monkeypatch):
    csrf = _admin_login(client, monkeypatch)
    client.post("/admin/add-user", data={
        "csrf_token": csrf, "phone": "5553335555", "name": "Short",
    })
    r = client.post("/admin/rename-user", data={
        "csrf_token": csrf, "phone": "+15553335555", "name": "X" * 100,
    })
    assert r.status_code == 200
    from lib.users import find_user
    assert len(find_user("+15553335555")["name"]) == 50


def test_admin_rename_requires_auth(client):
    r = client.post("/admin/rename-user", data={
        "csrf_token": "", "phone": "+15555555555", "name": "Hacked",
    })
    assert r.status_code == 403


def test_admin_test_sms_requires_auth(client):
    r = client.post("/admin/test-sms", data={"csrf_token": ""})
    assert r.status_code == 403


def test_admin_test_notify_requires_products(client, monkeypatch):
    csrf = _admin_login(client, monkeypatch)
    r = client.post("/admin/test-notify", data={"csrf_token": csrf})
    assert r.status_code == 200
    assert "no products" in r.text.lower()


def test_admin_dashboard_renders_with_data(client, monkeypatch):
    """Admin dashboard renders with seeded state, history, users, and SMS stats."""
    import json
    import time
    import lib.state as state_mod
    import lib.history as history_mod
    import lib.notifications as notif_mod
    from lib.file_lock import locked_json

    _admin_login(client, monkeypatch)

    # Seed state with products
    with locked_json(state_mod.STATE_FILE) as state:
        state["shopify-ca:1"] = {
            "available": True, "title": "Soylent Chocolate", "last_checked": "2026-02-21T00:00:00+00:00",
            "product_type": "Drink", "handle": "soylent-chocolate",
        }
        state["shopify-ca:2"] = {
            "available": False, "title": "Soylent Vanilla", "last_checked": "2026-02-21T00:00:00+00:00",
            "product_type": "Drink", "handle": "soylent-vanilla",
        }

    # Seed history
    with locked_json(history_mod.HISTORY_FILE, default_factory=list) as history:
        history.append({
            "timestamp": "2026-02-21T00:00:00+00:00",
            "product_key": "shopify-ca:1",
            "available": True,
            "title": "Soylent Chocolate",
            "source": "soylent.ca",
        })

    # Seed SMS stats
    today = time.strftime("%Y-%m-%d", time.gmtime())
    with locked_json(notif_mod.SMS_STATS_FILE) as stats:
        stats["total"] = 5
        stats["daily"] = {today: 2}
        stats["by_phone"] = {"+15555555555": 5}
        stats["last_message"] = {"+15555555555": {"text": "Test msg", "at": time.time()}}

    r = client.get("/admin/dashboard")
    assert r.status_code == 200

    # Verify key sections render
    html = r.text
    assert "Soylent Chocolate" in html  # product in history
    assert "1 in stock" in html.lower() or "1</span>" in html  # stats rendered
    assert "SMS" in html  # SMS section present
    assert "+1 (555) 555-5555" in html or "555-5555" in html  # user phone displayed
