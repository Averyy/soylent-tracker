"""Tests for checker parsing logic."""

from lib.soylent_checker import _parse_page_qty


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
