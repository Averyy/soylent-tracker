"""Integration tests for Shopify and Amazon checkers.

Mock HttpClient.fetch to verify the full check flow: product detection,
state transitions, history recording, and notification dispatch.
"""

import json
from unittest.mock import patch

import pytest

from lib.http_client import FetchResult


@pytest.fixture(autouse=True)
def _redirect_files(monkeypatch, tmp_path):
    """Redirect state/history files to temp dir."""
    import lib.state as state_mod
    import lib.config as config_mod
    import lib.history as history_mod
    import lib.notifications as notif_mod
    import lib.soylent_checker as shopify_mod
    import lib.users as users_mod

    state_file = tmp_path / "state.json"
    history_file = tmp_path / "history.json"
    users_file = tmp_path / "users.json"
    etag_file = tmp_path / ".shopify_etag"
    sms_stats_file = tmp_path / "sms_stats.json"

    monkeypatch.setattr(state_mod, "STATE_FILE", state_file)
    monkeypatch.setattr(config_mod, "STATE_FILE", state_file)
    monkeypatch.setattr(config_mod, "HISTORY_FILE", history_file)
    monkeypatch.setattr(history_mod, "HISTORY_FILE", history_file)
    monkeypatch.setattr(config_mod, "USERS_FILE", users_file)
    monkeypatch.setattr(users_mod, "USERS_FILE", users_file)
    monkeypatch.setattr(config_mod, "SMS_STATS_FILE", sms_stats_file)
    monkeypatch.setattr(notif_mod, "SMS_STATS_FILE", sms_stats_file)
    monkeypatch.setattr(shopify_mod, "ETAG_FILE", str(etag_file))

    users_file.write_text("[]")


def _shopify_json(products: list[dict]) -> bytes:
    return json.dumps({"products": products}).encode()


def _make_product(pid=1, title="Test Product", handle="test-product",
                  product_type="Drink", available=True):
    """Build a minimal Shopify product JSON object."""
    return {
        "id": pid,
        "title": title,
        "handle": handle,
        "product_type": product_type,
        "variants": [{
            "id": pid * 100,
            "title": "Default Title",
            "available": available,
            "price": "9.99",
            "requires_shipping": True,
        }],
    }


def _page_html(qty: int) -> bytes:
    return f'<html>gsf_conversion_data = {{ quantity: "{qty}" }}</html>'.encode()


# ── Shopify checker tests ──

@patch("lib.soylent_checker.HttpClient")
@patch("lib.soylent_checker.notify_changes")
def test_shopify_detects_new_available_product(mock_notify, MockClient, tmp_path):
    """New available product is detected and recorded."""
    from lib.soylent_checker import check_products
    from lib.state import load_state
    from lib.history import load_history

    client = MockClient.return_value.__enter__.return_value
    client.fetch.side_effect = [
        # products.json
        FetchResult(_shopify_json([_make_product()]), 200, {}, ""),
        # page qty fetch (from _batch_fetch_quantities worker creates its own client)
    ]

    # Mock the batch fetch to return a quantity
    with patch("lib.soylent_checker._batch_fetch_quantities", return_value={0: 50}):
        changes = check_products()

    assert len(changes) == 1
    assert changes[0]["available"] is True
    assert changes[0]["key"] == "shopify-ca:1"

    state = load_state()
    assert "shopify-ca:1" in state
    assert state["shopify-ca:1"]["available"] is True
    assert state["shopify-ca:1"]["inventory_qty"] == 50


@patch("lib.soylent_checker.HttpClient")
@patch("lib.soylent_checker.notify_changes")
def test_shopify_detects_out_of_stock_transition(mock_notify, MockClient, tmp_path):
    """Product going unavailable is detected as a change."""
    from lib.soylent_checker import check_products
    from lib.state import locked_state

    # Seed state with an available product
    with locked_state() as state:
        state["shopify-ca:1"] = {"available": True, "title": "Test"}

    client = MockClient.return_value.__enter__.return_value
    client.fetch.return_value = FetchResult(
        _shopify_json([_make_product(available=False)]), 200, {}, ""
    )

    with patch("lib.soylent_checker._batch_fetch_quantities", return_value={}):
        changes = check_products()

    assert len(changes) == 1
    assert changes[0]["available"] is False


@patch("lib.soylent_checker.HttpClient")
@patch("lib.soylent_checker.notify_changes")
def test_shopify_304_updates_last_checked(mock_notify, MockClient, tmp_path):
    """304 Not Modified still updates last_checked timestamps."""
    from lib.soylent_checker import check_products
    from lib.state import locked_state, load_state

    with locked_state() as state:
        state["shopify-ca:1"] = {"available": True, "title": "Test", "last_checked": "old"}

    client = MockClient.return_value.__enter__.return_value
    client.fetch.return_value = FetchResult(b"", 304, {}, "")

    changes = check_products()
    assert changes == []

    state = load_state()
    assert state["shopify-ca:1"]["last_checked"] != "old"


@patch("lib.soylent_checker.HttpClient")
@patch("lib.soylent_checker.notify_changes")
def test_shopify_no_change_when_status_same(mock_notify, MockClient, tmp_path):
    """No change emitted when availability stays the same."""
    from lib.soylent_checker import check_products
    from lib.state import locked_state

    with locked_state() as state:
        state["shopify-ca:1"] = {"available": False, "title": "Test"}

    client = MockClient.return_value.__enter__.return_value
    client.fetch.return_value = FetchResult(
        _shopify_json([_make_product(available=False)]), 200, {}, ""
    )

    with patch("lib.soylent_checker._batch_fetch_quantities", return_value={}):
        changes = check_products()

    assert changes == []


@patch("lib.soylent_checker.HttpClient")
@patch("lib.soylent_checker.notify_changes")
def test_shopify_page_qty_override(mock_notify, MockClient, tmp_path):
    """Product marked available by API but page qty <=0 is overridden to unavailable."""
    from lib.soylent_checker import check_products
    from lib.state import load_state

    client = MockClient.return_value.__enter__.return_value
    client.fetch.return_value = FetchResult(
        _shopify_json([_make_product(available=True)]), 200, {}, ""
    )

    # Page says qty is 0
    with patch("lib.soylent_checker._batch_fetch_quantities", return_value={0: 0}):
        changes = check_products()

    state = load_state()
    # Should be marked unavailable despite API saying available
    assert state["shopify-ca:1"]["available"] is False


@patch("lib.soylent_checker.HttpClient")
@patch("lib.soylent_checker.notify_changes")
def test_shopify_stale_variants_cleaned(mock_notify, MockClient, tmp_path):
    """Stale variant entries are removed when product is no longer multi-variant."""
    from lib.soylent_checker import check_products
    from lib.state import locked_state, load_state

    # Seed state with variant entries
    with locked_state() as state:
        state["shopify-ca:1:100"] = {"available": True, "title": "Old variant"}
        state["shopify-ca:1:200"] = {"available": True, "title": "Old variant 2"}

    # Product now single-variant
    client = MockClient.return_value.__enter__.return_value
    client.fetch.return_value = FetchResult(
        _shopify_json([_make_product()]), 200, {}, ""
    )

    with patch("lib.soylent_checker._batch_fetch_quantities", return_value={0: 50}):
        check_products()

    state = load_state()
    # Old variants should be gone, parent key should exist
    assert "shopify-ca:1:100" not in state
    assert "shopify-ca:1:200" not in state
    assert "shopify-ca:1" in state


@patch("lib.soylent_checker.HttpClient")
@patch("lib.soylent_checker.notify_changes")
def test_shopify_history_recorded(mock_notify, MockClient, tmp_path):
    """Changes are recorded in history.json."""
    from lib.soylent_checker import check_products
    from lib.history import load_history

    client = MockClient.return_value.__enter__.return_value
    client.fetch.return_value = FetchResult(
        _shopify_json([_make_product()]), 200, {}, ""
    )

    with patch("lib.soylent_checker._batch_fetch_quantities", return_value={0: 50}):
        check_products()

    history = load_history()
    assert len(history) == 1
    assert history[0]["product_key"] == "shopify-ca:1"
    assert history[0]["available"] is True
    assert history[0]["source"] == "soylent.ca"


# ── Amazon checker tests ──

@patch("lib.amazon_checker.HttpClient")
@patch("lib.amazon_checker.notify_changes")
@patch("lib.amazon_checker.get_amazon_asins", return_value={"B08TEST": "Test ASIN Product"})
def test_amazon_detects_available_product(mock_asins, mock_notify, MockClient, tmp_path):
    """Amazon checker detects an in-stock product."""
    from lib.amazon_checker import check_all_asins
    from lib.state import load_state

    client = MockClient.return_value.__enter__.return_value
    html = '<html>' + 'x' * 60_000 + '<span class="a-color-success">In Stock</span></html>'
    client.fetch.return_value = FetchResult(html.encode(), 200, {}, "")

    changes = check_all_asins()

    assert len(changes) == 1
    assert changes[0]["available"] is True
    assert changes[0]["key"] == "amazon-ca:B08TEST"


@patch("lib.amazon_checker.HttpClient")
@patch("lib.amazon_checker.notify_changes")
def test_amazon_detects_unavailable_product(mock_notify, MockClient, tmp_path):
    """Amazon checker detects an out-of-stock product."""
    from lib.amazon_checker import check_all_asins

    with patch("lib.amazon_checker.get_amazon_asins", return_value={"B08TEST": "Test"}):
        client = MockClient.return_value.__enter__.return_value
        html = '<html>' + 'x' * 60_000 + '<div id="outOfStock"><span>Currently unavailable.</span></div></div></html>'
        client.fetch.return_value = FetchResult(html.encode(), 200, {}, "")

        changes = check_all_asins()

    # First run: was_available is None -> False is a change
    assert len(changes) == 1
    assert changes[0]["available"] is False


@patch("lib.amazon_checker.HttpClient")
@patch("lib.amazon_checker.notify_changes")
def test_amazon_skips_challenge(mock_notify, MockClient, tmp_path):
    """Amazon checker skips products when wafer raises ChallengeDetected."""
    import wafer
    from lib.amazon_checker import check_all_asins

    with patch("lib.amazon_checker.get_amazon_asins", return_value={"B08TEST": "Test"}):
        client = MockClient.return_value.__enter__.return_value
        client.fetch.side_effect = wafer.ChallengeDetected("amazon", "https://amazon.ca/dp/B08TEST", 503)

        changes = check_all_asins()

    assert changes == []


@patch("lib.amazon_checker.HttpClient")
@patch("lib.amazon_checker.notify_changes")
def test_amazon_history_recorded(mock_notify, MockClient, tmp_path):
    """Amazon changes are recorded in history."""
    from lib.amazon_checker import check_all_asins
    from lib.history import load_history

    with patch("lib.amazon_checker.get_amazon_asins", return_value={"B08TEST": "Test"}):
        client = MockClient.return_value.__enter__.return_value
        html = '<html>' + 'x' * 60_000 + '<span class="a-color-success">In Stock</span></html>'
        client.fetch.return_value = FetchResult(html.encode(), 200, {}, "")

        check_all_asins()

    history = load_history()
    assert len(history) == 1
    assert history[0]["product_key"] == "amazon-ca:B08TEST"
