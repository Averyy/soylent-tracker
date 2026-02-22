"""Basic smoke tests for core logic. Keep it simple."""

import json
import os
import tempfile
from pathlib import Path

from lib.amazon_checker import parse_availability
from lib.file_lock import locked_json, read_json
from lib.state import load_state, locked_state, update_product


# ── Amazon availability parsing ──

def test_parse_out_of_stock():
    html = '<div id="outOfStock" class="a-section">Currently unavailable.</div></div>'
    available, status, count = parse_availability(html)
    assert not available
    assert "unavailable" in status.lower()
    assert count is None


def test_parse_in_stock():
    html = '<span class="a-color-success"> In Stock</span>'
    available, status, count = parse_availability(html)
    assert available
    assert "In Stock" in status


def test_parse_only_x_left():
    html = 'Only 3 left in stock - order soon.'
    available, status, count = parse_availability(html)
    assert available
    assert count == 3


# ── State management ──

def test_state_roundtrip():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name

    # Patch STATE_FILE temporarily
    import lib.state as state_mod
    original = state_mod.STATE_FILE
    state_mod.STATE_FILE = Path(path)
    try:
        with locked_state() as state:
            change = update_product(state, "shopify-ca:test", True, title="Test")
            assert change is not None
            assert change["available"] is True

        loaded = load_state()
        assert "shopify-ca:test" in loaded
        assert loaded["shopify-ca:test"]["available"] is True

        # No change on same status
        change2 = update_product(loaded, "shopify-ca:test", True, title="Test")
        assert change2 is None
    finally:
        state_mod.STATE_FILE = original
        os.unlink(path)


# ── File locking (locked_json) ──

def test_locked_json_read_modify_write(tmp_path):
    path = tmp_path / "test.json"
    path.write_text('{"a": 1}')

    with locked_json(path) as data:
        data["b"] = 2

    result = json.loads(path.read_text())
    assert result == {"a": 1, "b": 2}


def test_locked_json_creates_file_if_missing(tmp_path):
    path = tmp_path / "new.json"
    assert not path.exists()

    with locked_json(path) as data:
        data["created"] = True

    result = json.loads(path.read_text())
    assert result == {"created": True}


def test_locked_json_skips_write_on_exception(tmp_path):
    path = tmp_path / "test.json"
    path.write_text('{"original": true}')

    try:
        with locked_json(path) as data:
            data["mutated"] = True
            raise ValueError("intentional error")
    except ValueError:
        pass

    result = json.loads(path.read_text())
    assert result == {"original": True}
    assert "mutated" not in result


def test_locked_json_handles_corrupt_json(tmp_path):
    path = tmp_path / "corrupt.json"
    path.write_text("not json at all {{{")

    with locked_json(path) as data:
        data["recovered"] = True

    result = json.loads(path.read_text())
    assert result == {"recovered": True}


def test_read_json_returns_default_for_missing(tmp_path):
    path = tmp_path / "nope.json"
    result = read_json(path)
    assert result == {}


def test_read_json_caches_by_mtime(tmp_path):
    path = tmp_path / "cached.json"
    path.write_text('{"val": 1}')

    r1 = read_json(path)
    r2 = read_json(path)
    assert r1 == r2 == {"val": 1}

    # Mutating r1 should NOT affect r2 (deep copy)
    r1["val"] = 99
    r3 = read_json(path)
    assert r3 == {"val": 1}
