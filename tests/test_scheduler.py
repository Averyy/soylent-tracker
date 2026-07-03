"""Tests for the background checker scheduler."""

import asyncio
import threading
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from lib.scheduler import (
    _run_checker_loop, start_checkers, stop_checkers, _shutdown, checker_health,
)


@pytest.fixture(autouse=True)
def reset_shutdown():
    """Ensure clean shutdown state for each test."""
    _shutdown.clear()
    yield
    _shutdown.set()


def test_shutdown_stops_loop_promptly():
    """Shutdown event should stop the checker loop within ~1 second."""
    call_count = 0

    def fake_check():
        nonlocal call_count
        call_count += 1

    t = threading.Thread(target=_run_checker_loop, args=("test", fake_check, 3600))
    t.start()

    # Let it run one check
    time.sleep(0.1)
    _shutdown.set()
    t.join(timeout=3)
    assert not t.is_alive(), "Loop didn't stop within 3 seconds"
    assert call_count >= 1


@patch("lib.scheduler.MIN_INTERVAL", 1)
def test_errors_dont_crash_loop():
    """Checker errors should be logged, not crash the loop."""
    call_count = 0
    ran = threading.Event()

    def failing_check():
        nonlocal call_count
        call_count += 1
        if call_count >= 3:
            ran.set()
        if call_count <= 2:
            raise RuntimeError("simulated failure")

    t = threading.Thread(target=_run_checker_loop, args=("test", failing_check, 1))
    t.start()

    # Wait for at least 3 calls
    ran.wait(timeout=10)
    _shutdown.set()
    t.join(timeout=3)
    assert call_count >= 3, f"Expected at least 3 calls, got {call_count}"


def test_interval_zero_disables_checker():
    """Setting interval to 0 should prevent the checker from starting."""
    with patch("lib.scheduler.SOYLENT_CHECK_INTERVAL", 0), \
         patch("lib.scheduler.AMAZON_CHECK_INTERVAL", 0):
        tasks = asyncio.run(start_checkers())
        assert tasks == []


def _iso(seconds_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat()


def test_checker_health_fresh_is_healthy():
    """Recent last_checked timestamps → healthy, not stale."""
    state = {
        "shopify-ca:1": {"available": True, "last_checked": _iso(5)},
        "amazon-ca:X": {"available": False, "last_checked": _iso(30)},
    }
    with patch("lib.scheduler.load_state", return_value=state), \
         patch("lib.scheduler.SOYLENT_CHECK_INTERVAL", 60), \
         patch("lib.scheduler.AMAZON_CHECK_INTERVAL", 1200):
        h = checker_health()
    assert h["healthy"] is True
    assert h["checkers"]["soylent"]["stale"] is False
    assert h["checkers"]["amazon"]["stale"] is False


def test_checker_health_stale_flags_unhealthy():
    """A last_checked older than 3x the interval → stale + unhealthy."""
    state = {
        "shopify-ca:1": {"available": True, "last_checked": _iso(5)},
        # amazon last check is 1 hour old vs 1200s interval (threshold 3600s) → stale
        "amazon-ca:X": {"available": False, "last_checked": _iso(4000)},
    }
    with patch("lib.scheduler.load_state", return_value=state), \
         patch("lib.scheduler.SOYLENT_CHECK_INTERVAL", 60), \
         patch("lib.scheduler.AMAZON_CHECK_INTERVAL", 1200):
        h = checker_health()
    assert h["healthy"] is False
    assert h["checkers"]["soylent"]["stale"] is False
    assert h["checkers"]["amazon"]["stale"] is True


def test_checker_health_disabled_not_stale():
    """A disabled checker (interval=0) is reported but never counts as stale."""
    state = {"shopify-ca:1": {"available": True, "last_checked": _iso(5)}}
    with patch("lib.scheduler.load_state", return_value=state), \
         patch("lib.scheduler.SOYLENT_CHECK_INTERVAL", 60), \
         patch("lib.scheduler.AMAZON_CHECK_INTERVAL", 0):
        h = checker_health()
    assert h["healthy"] is True
    assert h["checkers"]["amazon"]["enabled"] is False
    assert h["checkers"]["amazon"]["stale"] is False


def test_checker_health_no_data_is_stale():
    """An enabled checker with no product timestamps yet → stale (never ran)."""
    with patch("lib.scheduler.load_state", return_value={}), \
         patch("lib.scheduler.SOYLENT_CHECK_INTERVAL", 60), \
         patch("lib.scheduler.AMAZON_CHECK_INTERVAL", 1200):
        h = checker_health()
    assert h["healthy"] is False
    assert h["checkers"]["soylent"]["stale"] is True
    assert h["checkers"]["soylent"]["last_checked"] is None


def test_min_interval_clamped():
    """Intervals below MIN_INTERVAL should be clamped, not cause tight loops."""
    call_count = 0

    def fake_check():
        nonlocal call_count
        call_count += 1

    # interval=1 should be clamped to MIN_INTERVAL (10)
    t = threading.Thread(target=_run_checker_loop, args=("test", fake_check, 1))
    t.start()

    # With MIN_INTERVAL=10, should only run once in 2 seconds
    time.sleep(2)
    _shutdown.set()
    t.join(timeout=3)
    assert call_count == 1, f"Expected 1 call (clamped interval), got {call_count}"
