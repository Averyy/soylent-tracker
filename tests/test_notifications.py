"""Tests for notification formatting and subscriber matching."""

import json
from unittest.mock import patch

from lib.notifications import format_notification, notify_changes


# ── format_notification() tests ──

def test_single_product_notification():
    changes = [{"key": "shopify-ca:123", "title": "Test Product", "available": True}]
    msg = format_notification(changes)
    assert "is back in stock" in msg
    assert "soylent.ca/products/123" in msg


def test_single_product_with_handle():
    changes = [{"key": "shopify-ca:123", "title": "Test", "handle": "test-drink", "available": True}]
    msg = format_notification(changes)
    assert "soylent.ca/products/test-drink" in msg


def test_single_amazon_product():
    changes = [{"key": "amazon-ca:B08V6JFZSR", "title": "Mocha", "available": True}]
    msg = format_notification(changes)
    assert "amazon.ca/dp/B08V6JFZSR" in msg


def test_single_product_stock_shown():
    changes = [{"key": "shopify-ca:1", "title": "Test", "available": True, "inventory_qty": 12}]
    msg = format_notification(changes, unsub_keys=set())
    assert "(12 available)" in msg
    assert "is back in stock (12 available):" in msg


def test_single_product_no_stock():
    changes = [{"key": "amazon-ca:B123", "title": "Test", "available": True}]
    msg = format_notification(changes, unsub_keys=set())
    assert "available)" not in msg
    assert "Unknown stock" in msg


def test_multiple_products_listed():
    changes = [
        {"key": "shopify-ca:1", "title": "Soylent drink (chocolate)", "available": True, "inventory_qty": 12},
        {"key": "shopify-ca:2", "title": "Soylent drink (strawberry)", "available": True, "inventory_qty": 5},
    ]
    msg = format_notification(changes, unsub_keys=set())
    assert "Back in stock:" in msg
    assert "12x Soylent drink (chocolate)" in msg
    assert "5x Soylent drink (strawberry)" in msg


def test_multiple_products_stock_per_line():
    """Each product line shows its own stock prefix; no-stock products have no prefix."""
    changes = [
        {"key": "shopify-ca:1", "title": "Soylent drink (chocolate)", "available": True, "inventory_qty": 12},
        {"key": "amazon-ca:B123", "title": "Soylent powder (original)", "available": True},
    ]
    msg = format_notification(changes, unsub_keys=set())
    assert "12x Soylent drink (chocolate)" in msg
    assert "Soylent powder (original)" in msg
    # No "x " prefix for the product without stock
    assert "x Soylent powder" not in msg


def test_multiple_different_types():
    changes = [
        {"key": "shopify-ca:1", "title": "Soylent drink (chocolate)", "available": True, "inventory_qty": 10},
        {"key": "shopify-ca:2", "title": "Soylent powder (original)", "available": True, "inventory_qty": 20},
    ]
    msg = format_notification(changes, unsub_keys=set())
    assert "Back in stock:" in msg
    assert "10x Soylent drink (chocolate)" in msg
    assert "20x Soylent powder (original)" in msg


def test_unsub_message_when_high_stock():
    """Single product with stock > threshold shows unsubscribe message."""
    changes = [{"key": "shopify-ca:1", "title": "Test", "available": True, "inventory_qty": 150}]
    msg = format_notification(changes, unsub_keys={"shopify-ca:1"})
    assert "You've been unsubscribed from this product." in msg


def test_watching_message_when_low_stock():
    """Single product with stock <= threshold shows watching message."""
    changes = [{"key": "shopify-ca:1", "title": "Test", "available": True, "inventory_qty": 50}]
    msg = format_notification(changes, unsub_keys=set())
    assert "Limited stock, we'll keep watching." in msg


def test_unknown_stock_watching_message():
    """Single product with no stock info shows unknown watching message."""
    changes = [{"key": "amazon-ca:B123", "title": "Test", "available": True}]
    msg = format_notification(changes, unsub_keys=set())
    assert "Unknown stock, we'll keep watching." in msg


def test_mixed_unsub_message():
    """Multiple products with mixed high/low stock shows mixed footer."""
    changes = [
        {"key": "shopify-ca:1", "title": "High Stock", "available": True, "inventory_qty": 200},
        {"key": "shopify-ca:2", "title": "Low Stock", "available": True, "inventory_qty": 10},
    ]
    msg = format_notification(changes, unsub_keys={"shopify-ca:1"})
    assert "You've been unsubscribed from high-stock items." in msg


def test_all_unsub_multiple():
    """Multiple products all above threshold shows plural unsubscribe."""
    changes = [
        {"key": "shopify-ca:1", "title": "A", "available": True, "inventory_qty": 200},
        {"key": "shopify-ca:2", "title": "B", "available": True, "inventory_qty": 300},
    ]
    msg = format_notification(changes, unsub_keys={"shopify-ca:1", "shopify-ca:2"})
    assert "You've been unsubscribed from these products." in msg


def test_none_unsub_multiple():
    """Multiple products all below threshold shows watching message."""
    changes = [
        {"key": "shopify-ca:1", "title": "A", "available": True, "inventory_qty": 10},
        {"key": "shopify-ca:2", "title": "B", "available": True, "inventory_qty": 20},
    ]
    msg = format_notification(changes, unsub_keys=set())
    assert "We'll keep watching the low inventory products." in msg


def test_sms_name_override_used():
    """Registry sms_name overrides title when set."""
    changes = [{"key": "shopify-ca:4376580980820", "title": "Original Title", "available": True}]
    msg = format_notification(changes)
    # Registry has sms_name "Soylent drink (chocolate)" for this key
    assert "Soylent drink (chocolate)" in msg


# ── notify_changes() tests ──

_TEST_USERS = [
    {"phone": "+16665551111", "name": "Sub User", "notifications_enabled": True,
     "subscriptions": ["shopify-ca:1"]},
    {"phone": "+16665552222", "name": "No Notif", "notifications_enabled": False,
     "subscriptions": ["shopify-ca:1"]},
    {"phone": "+16665553333", "name": "No Sub", "notifications_enabled": True,
     "subscriptions": []},
]


def test_notify_changes_sends_to_subscribed_users():
    """notify_changes sends SMS only to users subscribed to the restocked product."""
    changes = [{"key": "shopify-ca:1", "title": "Test", "available": True, "inventory_qty": 200}]

    with patch("lib.notifications.send_sms", return_value=True) as mock_sms:
        notify_changes(changes, _TEST_USERS)

    # Only the subscribed user with notifications enabled should get SMS
    assert mock_sms.call_count == 1
    assert mock_sms.call_args[0][0] == "+16665551111"


def test_notify_changes_skips_notifications_disabled():
    """Users with notifications_enabled=False are skipped."""
    changes = [{"key": "shopify-ca:1", "title": "Test", "available": True, "inventory_qty": 200}]

    with patch("lib.notifications.send_sms", return_value=True) as mock_sms:
        notify_changes(changes, _TEST_USERS)

    phones_called = [call[0][0] for call in mock_sms.call_args_list]
    assert "+16665552222" not in phones_called


def test_notify_changes_skips_out_of_stock():
    """Only restocked (available=True) changes trigger notifications."""
    changes = [{"key": "shopify-ca:1", "title": "Test", "available": False}]

    with patch("lib.notifications.send_sms", return_value=True) as mock_sms:
        notify_changes(changes, _TEST_USERS)

    mock_sms.assert_not_called()


def test_notify_changes_no_changes():
    """Empty changes list is a no-op."""
    with patch("lib.notifications.send_sms") as mock_sms:
        notify_changes([], [])

    mock_sms.assert_not_called()


def test_notify_changes_only_unsubs_high_stock():
    """Only products with stock > threshold are removed from subscriptions."""
    users = [
        {"phone": "+16665551111", "name": "User", "notifications_enabled": True,
         "subscriptions": ["shopify-ca:1", "shopify-ca:2"]},
    ]
    changes = [
        {"key": "shopify-ca:1", "title": "High", "available": True, "inventory_qty": 200},
        {"key": "shopify-ca:2", "title": "Low", "available": True, "inventory_qty": 10},
    ]

    with patch("lib.notifications.send_sms", return_value=True), \
         patch("lib.users.locked_users") as mock_locked:
        # Set up the context manager to capture the unsubscribe logic
        saved_users = [
            {"phone": "+16665551111", "subscriptions": ["shopify-ca:1", "shopify-ca:2"]},
        ]
        mock_locked.return_value.__enter__ = lambda s: saved_users
        mock_locked.return_value.__exit__ = lambda s, *a: None
        notify_changes(changes, users)

    # Only high-stock key removed, low-stock key preserved
    assert "shopify-ca:1" not in saved_users[0]["subscriptions"]
    assert "shopify-ca:2" in saved_users[0]["subscriptions"]


def test_notify_changes_no_unsub_when_low_stock():
    """Subscriptions preserved when all products have stock <= threshold."""
    users = [
        {"phone": "+16665551111", "name": "User", "notifications_enabled": True,
         "subscriptions": ["shopify-ca:1"]},
    ]
    changes = [{"key": "shopify-ca:1", "title": "Low", "available": True, "inventory_qty": 50}]

    with patch("lib.notifications.send_sms", return_value=True), \
         patch("lib.users.locked_users") as mock_locked:
        notify_changes(changes, users)

    # locked_users should never be called — no unsubscribes needed
    mock_locked.assert_not_called()
