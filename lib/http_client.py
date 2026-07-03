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
        # timeout is the TOTAL budget per call in wafer -- it covers rate-limit
        # waits (up to 12s with the Amazon client's rate_limit=5 + jitter=7),
        # retries, and rotations. attempt_timeout caps each individual try so
        # a single hanging attempt can't eat the budget and rotations fire.
        self._session = wafer.SyncSession(
            timeout=60,
            attempt_timeout=10,
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
        timeout: float | None = None,
        attempt_timeout: float | None = None,
        headers: dict | None = None,
    ) -> wafer.WaferResponse:
        """Fetch a URL. Returns WaferResponse with .text, .json(), .ok, etc.

        timeout/attempt_timeout default to the session values (60s total,
        10s per attempt); pass explicitly to override for a single call.
        """
        return self._session.get(
            url, timeout=timeout, attempt_timeout=attempt_timeout, headers=headers
        )

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self._session.__exit__(None, None, None)
