"""Tests for checker parsing logic."""

from lib.soylent_checker import _parse_page_qty, _parse_waitlist


def test_parse_page_qty_gsf_conversion():
    html = 'gsf_conversion_data = { quantity: "42" }'
    assert _parse_page_qty(html) == 42


def test_parse_page_qty_inventory():
    html = '"inventoryQty":150'
    assert _parse_page_qty(html) == 150


def test_parse_page_qty_none_on_missing():
    html = "<html><body>no quantity info</body></html>"
    assert _parse_page_qty(html) is None


def test_parse_page_qty_negative():
    html = 'gsf_conversion_data = { quantity: "-5" }'
    assert _parse_page_qty(html) == -5


def test_parse_page_qty_gsf_preferred_over_inventory():
    """gsf_conversion_data quantity should be checked first."""
    html = 'gsf_conversion_data = { quantity: "10" } "inventoryQty":99'
    assert _parse_page_qty(html) == 10


def test_parse_waitlist_theme_class():
    """Real markup from soylent.ca subscriber-only waitlist (2026-07)."""
    html = ('<div class="alternative-products">'
            '<p class="alt-p-title">due to high demand, we’re prioritizing '
            "current inventory for our subscribers. drop your email below to be "
            "notified when we're back in stock.</p></div>")
    assert _parse_waitlist(html) is True


def test_parse_waitlist_text_only():
    """Message text alone triggers detection (theme class may change)."""
    html = "<p>we're prioritizing current inventory for our subscribers</p>"
    assert _parse_waitlist(html) is True


def test_parse_waitlist_absent_on_buyable_page():
    html = '<button class="add-to-cart-pdp">Add to Cart</button> subscribe & save'
    assert _parse_waitlist(html) is False
