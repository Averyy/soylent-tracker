"""Tests for the background checker scheduler."""

import asyncio
import threading
import time
from unittest.mock import patch

import pytest

from lib.scheduler import _run_checker_loop, start_checkers, stop_checkers, _shutdown


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
