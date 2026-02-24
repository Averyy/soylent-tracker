"""HTTP client backed by wafer.

Thin wrapper around wafer.SyncSession preserving the HttpClient/FetchResult
interface used by checkers. wafer handles TLS fingerprinting, challenge
detection/solving, retry, backoff, and rate limiting internally.
"""

import logging
from dataclasses import dataclass

import wafer

log = logging.getLogger(__name__)


@dataclass
class FetchResult:
    """Result from fetching a URL."""
    content: bytes
    status_code: int
    headers: dict[str, str]
    url: str


class HttpClient:
    """Thin wrapper around wafer.SyncSession.

    Preserves the existing interface so checkers need minimal changes.
    """

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
    ) -> FetchResult:
        """Fetch a URL. wafer handles retries, fingerprint rotation, and challenges."""
        resp = self._session.get(url, timeout=timeout, headers=headers)
        return FetchResult(
            content=resp.content,
            status_code=resp.status_code,
            headers=resp.headers,
            url=resp.url,
        )

    def close(self):
        self._session.__exit__(None, None, None)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
