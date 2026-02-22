"""Shared file-locking utilities for JSON data files.

Provides read_json() for shared-lock reads (with mtime-based caching) and
locked_json() context manager for exclusive read-modify-write cycles. Uses
fcntl.flock for process-safe locking and atomic writes (tmp + os.replace).
"""

import copy
import fcntl
import json
import logging
import os
import threading
from contextlib import contextmanager
from pathlib import Path

log = logging.getLogger(__name__)

# mtime-based read cache: {path_str: (mtime_ns, data)}
# Uses st_mtime_ns for nanosecond resolution to avoid stale reads
# when two writes happen within the same second.
# Expected entries: ~6 (state.json, users.json, history.json, sms_stats.json, products.json, etc.)
_READ_CACHE_MAX = 20
_read_cache: dict[str, tuple[int, object]] = {}
_cache_lock = threading.Lock()


def _read_json_cached(path: Path, default_factory=dict):
    """Internal: read JSON with mtime caching, return cached reference (no copy)."""
    try:
        mtime = path.stat().st_mtime_ns
    except FileNotFoundError:
        return default_factory()

    key = str(path)
    with _cache_lock:
        cached = _read_cache.get(key)
        if cached and cached[0] == mtime:
            return cached[1]

    try:
        with open(path, "r") as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            try:
                data = json.load(f)
            except json.JSONDecodeError:
                log.warning(f"{path.name} is corrupt or empty — returning default")
                return default_factory()
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
    except FileNotFoundError:
        return default_factory()

    with _cache_lock:
        if len(_read_cache) >= _READ_CACHE_MAX:
            _read_cache.clear()
        _read_cache[key] = (mtime, data)
    return data


def read_json(path: Path, default_factory=dict):
    """Read a JSON file with mtime-based caching.

    Returns a deep copy to prevent mutation of cached data.
    For read-only access, use read_json_snapshot() instead.
    """
    return copy.deepcopy(_read_json_cached(path, default_factory))


def read_json_snapshot(path: Path, default_factory=dict):
    """Read a JSON file, returning the cached reference directly.

    DO NOT mutate the returned data — it is the live cache entry.
    Use this for read-only lookups (e.g., find_user, dashboard display).
    """
    return _read_json_cached(path, default_factory)


@contextmanager
def locked_json(path: Path, default_factory=dict):
    """Hold exclusive lock across read-modify-write cycle.

    Reads JSON from path, yields the data for in-place modification, then
    writes back atomically on context exit. Invalidates read cache on write.

    Usage:
        with locked_json(STATE_FILE) as data:
            data["key"] = "value"
        # lock released, file saved automatically on exit
    """
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            with open(path, "r") as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            data = default_factory()

        try:
            yield data
        except BaseException:
            raise  # Don't write back on error
        else:
            # Write back atomically, still holding lock
            tmp = str(path) + ".tmp"
            with open(tmp, "w") as f:
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, str(path))

            # Invalidate read cache so next read_json picks up the new data
            with _cache_lock:
                _read_cache.pop(str(path), None)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
