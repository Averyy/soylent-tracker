"""Registry tests — classification, display names, visibility."""

from lib.registry import classify, display_name, is_hidden, _get_registry


def test_classify_uses_product_type():
    # A key not in registry should fall back to product_type mapping
    result = classify("shopify-ca:unknown-key", "Drink")
    assert result == "drinks"
    result = classify("shopify-ca:unknown-key", "Powder")
    assert result == "powder"


def test_classify_defaults_to_drinks():
    result = classify("shopify-ca:unknown-key", "SomeRandomType")
    assert result == "drinks"


def test_classify_respects_registry_override():
    # If there's a registry entry with category, it should override product_type
    registry = _get_registry()
    for key, entry in registry.items():
        if "category" in entry:
            result = classify(key, "Drink")  # pass Drink, but registry should win
            assert result == entry["category"]
            break


def test_display_name_returns_auto_title_when_no_override():
    name, from_registry = display_name("shopify-ca:unknown-key", "Auto Title")
    assert name == "Auto Title"
    assert from_registry is False


def test_display_name_uses_registry_override():
    registry = _get_registry()
    for key, entry in registry.items():
        if "name" in entry:
            name, from_registry = display_name(key, "Should Not Use This")
            assert name == entry["name"]
            assert from_registry is True
            break


def test_is_hidden_false_for_unknown_key():
    assert is_hidden("shopify-ca:unknown-key", True) is False
    assert is_hidden("shopify-ca:unknown-key", False) is False


def test_is_hidden_when_oos():
    registry = _get_registry()
    for key, entry in registry.items():
        if entry.get("hidden") == "when_oos":
            assert is_hidden(key, False) is True
            assert is_hidden(key, True) is False
            break


def test_is_hidden_always():
    registry = _get_registry()
    for key, entry in registry.items():
        if entry.get("hidden") is True:
            assert is_hidden(key, True) is True
            assert is_hidden(key, False) is True
            break
