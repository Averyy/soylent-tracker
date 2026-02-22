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

from .config import SOYLENT_CHECK_INTERVAL, AMAZON_CHECK_INTERVAL
from .soylent_checker import main as soylent_main
from .amazon_checker import main as amazon_main

log = logging.getLogger(__name__)

_shutdown = threading.Event()
_checker_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="checker")

MIN_INTERVAL = 10  # floor to prevent accidental self-DoS


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

    if SOYLENT_CHECK_INTERVAL > 0:
        tasks.append(loop.run_in_executor(
            _checker_pool, _run_checker_loop, "soylent", soylent_main, SOYLENT_CHECK_INTERVAL
        ))
        log.info(f"Scheduler: soylent checker every {SOYLENT_CHECK_INTERVAL}s")
    else:
        log.info("Scheduler: soylent checker disabled (interval=0)")

    if AMAZON_CHECK_INTERVAL > 0:
        tasks.append(loop.run_in_executor(
            _checker_pool, _run_checker_loop, "amazon", amazon_main, AMAZON_CHECK_INTERVAL
        ))
        log.info(f"Scheduler: amazon checker every {AMAZON_CHECK_INTERVAL}s")
    else:
        log.info("Scheduler: amazon checker disabled (interval=0)")

    return tasks


def stop_checkers():
    """Signal all checker loops to stop."""
    log.info("Scheduler: stopping checkers...")
    _shutdown.set()
