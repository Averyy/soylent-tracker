"""History module tests."""

import json
import os
import tempfile

import pytest

from lib.history import record_change, load_history


@pytest.fixture(autouse=True)
def _patch_history_file(monkeypatch, tmp_path):
    """Redirect history file to temp dir."""
    import lib.history as history_mod
    history_file = tmp_path / "history.json"
    monkeypatch.setattr(history_mod, "HISTORY_FILE", history_file)


def test_record_and_load():
    record_change("shopify-ca:123", True, "Test Product")
    history = load_history()
    assert len(history) == 1
    event = history[0]
    assert event["product_key"] == "shopify-ca:123"
    assert event["available"] is True
    assert event["title"] == "Test Product"
    assert "timestamp" in event
    assert "source" in event


def test_multiple_records_newest_first():
    record_change("shopify-ca:1", True, "First")
    record_change("shopify-ca:2", False, "Second")
    history = load_history()
    assert len(history) == 2
    # Newest first
    assert history[0]["title"] == "Second"
    assert history[1]["title"] == "First"


def test_filter_by_product_key():
    record_change("shopify-ca:1", True, "Product A")
    record_change("amazon-ca:X", False, "Product B")
    record_change("shopify-ca:1", False, "Product A")

    history = load_history(product_key="shopify-ca:1")
    assert len(history) == 2
    assert all(e["product_key"] == "shopify-ca:1" for e in history)


def test_limit():
    for i in range(10):
        record_change(f"shopify-ca:{i}", True, f"Product {i}")
    history = load_history(limit=3)
    assert len(history) == 3


def test_load_empty_history():
    history = load_history()
    assert history == []


def test_extra_fields_stored():
    record_change("shopify-ca:1", True, "Test", inventory_qty=50, status_text="In Stock")
    history = load_history()
    assert history[0]["inventory_qty"] == 50
    assert history[0]["status_text"] == "In Stock"


def test_none_extras_excluded():
    record_change("shopify-ca:1", True, "Test", inventory_qty=None)
    history = load_history()
    assert "inventory_qty" not in history[0]


def test_source_label_resolved():
    record_change("shopify-ca:1", True, "Test")
    record_change("amazon-ca:X", False, "Test2")
    history = load_history()
    sources = {e["source"] for e in history}
    assert "soylent.ca" in sources
    assert "Amazon.ca" in sources
