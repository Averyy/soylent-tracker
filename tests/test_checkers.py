"""Tests for checker parsing logic."""

from lib.amazon_checker import is_captcha
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


# ── is_captcha tests ──

def test_captcha_detected_on_small_page():
    body = "<html><body>Please continue shopping on Amazon</body></html>"
    assert is_captcha(body) is True


def test_captcha_detected_with_amzn():
    body = "<html><body>amzn please continue shopping here</body></html>"
    assert is_captcha(body) is True


def test_captcha_not_detected_on_large_page():
    # Real product pages are >50KB
    body = "x" * 60_000 + " continue shopping amazon"
    assert is_captcha(body) is False


def test_captcha_not_detected_without_keywords():
    body = "<html><body>some small page without the trigger phrase</body></html>"
    assert is_captcha(body) is False


def test_captcha_not_detected_without_amazon():
    body = "<html><body>continue shopping on some other site</body></html>"
    assert is_captcha(body) is False
