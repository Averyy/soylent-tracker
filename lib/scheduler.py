"""Background scheduler for stock checkers.

Runs soylent and amazon checkers as background threads inside the web process,
replacing the separate Docker containers. Each checker runs in a loop with
configurable intervals, logging errors without crashing.

Thread model:
  - 2 long-lived threads (soylent + amazon) on a dedicated executor
  - Soylent checker internally spawns 4 short-lived threads for page-qty fetching
  - These are isolated from FastAPI's default executor for sync route handlers
"""

import asyncio
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from .config import (
    SOYLENT_CHECK_INTERVAL, AMAZON_CHECK_INTERVAL,
    SOURCE_SHOPIFY_CA, SOURCE_AMAZON_CA,
)
from .soylent_checker import main as soylent_main
from .amazon_checker import main as amazon_main
from .state import load_state

log = logging.getLogger(__name__)

MIN_INTERVAL = 10  # floor to prevent accidental self-DoS

# A checker is "stale" if its newest product last_checked is older than this many
# intervals — a signal that the background thread has silently stopped.
_STALENESS_FACTOR = 3

# Registry of background checkers. Each entry runs as one long-lived thread in
# an infinite loop, so the pool MUST have at least one worker per checker or the
# extra ones silently never start (loops never yield the worker back). Sizing
# the pool from this list keeps that invariant when a checker is added. The
# interval is a callable so it's read fresh at start (respects env/test patching)
# rather than frozen at import; the source prefix keys the health check into
# state.json's per-product last_checked timestamps.
_CHECKERS = [
    ("soylent", soylent_main, lambda: SOYLENT_CHECK_INTERVAL, SOURCE_SHOPIFY_CA),
    ("amazon", amazon_main, lambda: AMAZON_CHECK_INTERVAL, SOURCE_AMAZON_CA),
]

_shutdown = threading.Event()
_checker_pool = ThreadPoolExecutor(
    max_workers=len(_CHECKERS), thread_name_prefix="checker"
)


def _run_checker_loop(name: str, check_fn, interval: int):
    """Run a checker in a loop until shutdown is signalled.

    Sleeps in 1-second increments so shutdown is responsive.
    """
    interval = max(interval, MIN_INTERVAL)
    log.info(f"Scheduler: {name} started (interval={interval}s)")
    while not _shutdown.is_set():
        try:
            check_fn()
        except Exception:
            log.exception(f"Scheduler: {name} error")
        # Sleep in 1s increments for responsive shutdown
        for _ in range(interval):
            if _shutdown.wait(1):
                break
    log.info(f"Scheduler: {name} stopped")


async def start_checkers() -> list[asyncio.Future]:
    """Launch checker loops as background tasks. Returns future handles."""
    _shutdown.clear()
    loop = asyncio.get_running_loop()
    tasks = []

    for name, check_fn, interval_fn, _source in _CHECKERS:
        interval = interval_fn()
        if interval > 0:
            tasks.append(loop.run_in_executor(
                _checker_pool, _run_checker_loop, name, check_fn, interval
            ))
            log.info(f"Scheduler: {name} checker every {interval}s")
        else:
            log.info(f"Scheduler: {name} checker disabled (interval=0)")

    return tasks


def stop_checkers():
    """Signal all checker loops to stop."""
    log.info("Scheduler: stopping checkers...")
    _shutdown.set()


def checker_health() -> dict:
    """Report per-checker freshness for external monitoring.

    A checker is "stale" if the newest last_checked among its products (keyed by
    source prefix in state.json) is older than _STALENESS_FACTOR intervals, which
    means its background thread has silently stopped making progress. Disabled
    checkers (interval=0) are reported but never count as stale. This is a
    readiness/monitoring signal only — it is intentionally NOT wired to the
    Docker healthcheck, so an upstream (soylent.ca/Amazon) outage can't trigger
    container restarts of the web process.
    """
    state = load_state()
    now = datetime.now(timezone.utc)
    checkers = {}
    all_healthy = True

    for name, _check_fn, interval_fn, source in _CHECKERS:
        interval = interval_fn()
        if interval <= 0:
            checkers[name] = {
                "enabled": False, "stale": False,
                "last_checked": None, "age_seconds": None, "threshold_seconds": None,
            }
            continue

        newest = None
        for key, entry in state.items():
            if not key.startswith(source + ":"):
                continue
            ts = entry.get("last_checked")
            if not ts:
                continue
            try:
                dt = datetime.fromisoformat(ts)
            except ValueError:
                continue
            if newest is None or dt > newest:
                newest = dt

        threshold = interval * _STALENESS_FACTOR
        if newest is None:
            age, stale = None, True  # enabled but no data yet / never ran
        else:
            age = (now - newest).total_seconds()
            stale = age > threshold
        if stale:
            all_healthy = False
        checkers[name] = {
            "enabled": True,
            "stale": stale,
            "last_checked": newest.isoformat() if newest else None,
            "age_seconds": round(age, 1) if age is not None else None,
            "threshold_seconds": threshold,
        }

    return {"healthy": all_healthy, "checkers": checkers}
