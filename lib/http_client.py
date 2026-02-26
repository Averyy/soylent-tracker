"""HTTP client backed by wafer.

Thin wrapper around wafer.SyncSession. wafer handles TLS fingerprinting,
challenge detection/solving, retry, backoff, and rate limiting internally.
"""

import logging

import wafer

log = logging.getLogger(__name__)


class HttpClient:
    """Thin wrapper around wafer.SyncSession."""

    def __init__(
        self,
        rate_limit: float = 0.0,
        rate_jitter: float = 0.0,
    ):
        self._session = wafer.SyncSession(
            timeout=20,
            max_retries=1,
            max_rotations=3,
            max_failures=None,
            rate_limit=rate_limit,
            rate_jitter=rate_jitter,
            cache_dir=None,
        )

    def fetch(
        self,
        url: str,
        timeout: float = 15.0,
        headers: dict | None = None,
    ) -> wafer.WaferResponse:
        """Fetch a URL. Returns WaferResponse with .text, .json(), .ok, etc."""
        return self._session.get(url, timeout=timeout, headers=headers)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self._session.__exit__(None, None, None)
