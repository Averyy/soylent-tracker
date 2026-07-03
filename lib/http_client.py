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
        emulation=None,
        fingerprint_pool=None,
        max_rotations: int = 3,
    ):
        # timeout is the TOTAL budget per call in wafer -- it covers rate-limit
        # waits (up to 12s with the Amazon client's rate_limit=5 + jitter=7),
        # retries, and rotations. attempt_timeout caps each individual try so
        # a single hanging attempt can't eat the budget and rotations fire.
        #
        # emulation/fingerprint_pool pin the TLS identity. Default (None) rides
        # wafer's newest default (Chrome147). Amazon's WAF keys reputation on
        # (IP, fingerprint), so the Amazon checker pins an accepted identity --
        # a bare wafer version bump silently changes the default fingerprint and
        # can get the VPS's datacenter IP challenged on every request.
        kwargs = dict(
            timeout=60,
            attempt_timeout=10,
            max_retries=1,
            max_rotations=max_rotations,
            max_failures=None,
            rate_limit=rate_limit,
            rate_jitter=rate_jitter,
            cache_dir=None,
        )
        if emulation is not None:
            kwargs["emulation"] = emulation
        if fingerprint_pool is not None:
            kwargs["fingerprint_pool"] = fingerprint_pool
        self._session = wafer.SyncSession(**kwargs)

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
