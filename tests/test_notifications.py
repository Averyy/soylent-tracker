"""Tests for notification formatting and subscriber matching."""

import json
from unittest.mock import patch

from lib.notifications import format_notification, notify_changes


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


def test_multiple_same_type_grouped():
    changes = [
        {"key": "shopify-ca:1", "title": "Soylent drink (chocolate)", "available": True},
        {"key": "shopify-ca:2", "title": "Soylent drink (strawberry)", "available": True},
    ]
    msg = format_notification(changes)
    assert "chocolate" in msg
    assert "strawberry" in msg
    assert "is back in stock" in msg  # single group → singular verb


def test_multiple_different_types():
    changes = [
        {"key": "shopify-ca:1", "title": "Soylent drink (chocolate)", "available": True},
        {"key": "shopify-ca:2", "title": "Soylent powder (original)", "available": True},
    ]
    msg = format_notification(changes)
    assert "are back in stock" in msg  # multiple groups → plural verb
    assert " and " in msg


def test_three_plus_types_oxford_comma():
    changes = [
        {"key": "shopify-ca:1", "title": "Soylent drink (chocolate)", "available": True},
        {"key": "shopify-ca:2", "title": "Soylent powder (original)", "available": True},
        {"key": "shopify-ca:3", "title": "Blender Bottle", "available": True},
    ]
    msg = format_notification(changes)
    assert ", and " in msg  # Oxford comma


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
    changes = [{"key": "shopify-ca:1", "title": "Test", "available": True}]

    with patch("lib.notifications.send_sms", return_value=True) as mock_sms:
        notify_changes(changes, _TEST_USERS)

    # Only the subscribed user with notifications enabled should get SMS
    assert mock_sms.call_count == 1
    assert mock_sms.call_args[0][0] == "+16665551111"


def test_notify_changes_skips_notifications_disabled():
    """Users with notifications_enabled=False are skipped."""
    changes = [{"key": "shopify-ca:1", "title": "Test", "available": True}]

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
