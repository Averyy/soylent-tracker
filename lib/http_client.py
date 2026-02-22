"""Shared HTTP client with curl_cffi TLS fingerprint impersonation.

Modeled after fetchaller-mcp's fetcher.py. Provides browser-realistic
requests with fingerprint rotation on 403s. Includes inline Amazon
captcha solving (no browser needed).
"""

import html as html_mod
import logging
import os
import random
import re
import ssl
import time
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

from curl_cffi.requests import Session, Response

log = logging.getLogger(__name__)

# Browser fingerprints for TLS impersonation rotation
BROWSER_FINGERPRINTS = ["chrome131", "chrome133a", "chrome136", "chrome124"]

# Sec-Ch-Ua headers must match the TLS fingerprint
FINGERPRINT_SEC_CH_UA = {
    "chrome124": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "chrome131": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    "chrome133a": '"Google Chrome";v="133", "Chromium";v="133", "Not(A:Brand";v="24"',
    "chrome136": '"Google Chrome";v="136", "Chromium";v="136", "Not)A;Brand";v="99"',
}

# Browser navigation headers (Sec-Ch-Ua set dynamically per fingerprint)
HTML_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "max-age=0",
    "DNT": "1",
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"macOS"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

# JSON API headers
JSON_HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"macOS"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}


def _find_ca_bundle() -> str | None:
    """Find system CA certificate bundle for SSL verification."""
    env_path = os.environ.get("CURL_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE")
    if env_path and os.path.isfile(env_path):
        return env_path
    system_ca = ssl.get_default_verify_paths().cafile
    if system_ca and os.path.isfile(system_ca):
        return system_ca
    return None


@dataclass
class FetchResult:
    """Result from fetching a URL."""
    content: bytes
    status_code: int
    headers: dict[str, str]
    url: str


class HttpClient:
    """HTTP client with TLS fingerprint impersonation and retry logic.

    Synchronous (not async) since our checkers run as simple scripts.
    """

    def __init__(self, max_retries: int = 1, retry_delay: float = 0.5):
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self._fingerprint = random.choice(BROWSER_FINGERPRINTS)
        self._pinned = False
        ca_bundle = _find_ca_bundle()
        self._session = Session(verify=ca_bundle) if ca_bundle else Session()

    def pin_fingerprint(self, fingerprint: str | None = None) -> None:
        """Lock the TLS fingerprint to prevent rotation.

        Must be called after solving an Amazon captcha — cookies are bound
        to the TLS fingerprint, and rotating would cause rejection.
        """
        if fingerprint:
            self._fingerprint = fingerprint
        self._pinned = True

    def _rotate_fingerprint(self) -> None:
        """Switch to a different fingerprint (call after 403).

        No-op when fingerprint is pinned (e.g. after Amazon captcha solve).
        """
        if self._pinned:
            return
        old = self._fingerprint
        available = [f for f in BROWSER_FINGERPRINTS if f != old]
        self._fingerprint = random.choice(available) if available else old

    def _build_headers(self, json_mode: bool = False, extra: dict | None = None, url: str | None = None) -> dict:
        """Build request headers with Sec-Ch-Ua matching the active fingerprint."""
        base = JSON_HEADERS.copy() if json_mode else HTML_HEADERS.copy()
        sec_ch_ua = FINGERPRINT_SEC_CH_UA.get(self._fingerprint)
        if sec_ch_ua:
            base["Sec-Ch-Ua"] = sec_ch_ua
        # Auto-set Referer (real browsers always send this)
        if url and not (extra and "Referer" in extra):
            try:
                parsed = urlparse(url)
                base["Referer"] = f"{parsed.scheme}://{parsed.hostname}/"
            except Exception:
                pass
        if extra:
            base.update(extra)
        return base

    def fetch(
        self,
        url: str,
        timeout: float = 15.0,
        json_mode: bool = False,
        headers: dict | None = None,
    ) -> FetchResult:
        """Fetch a URL with retry and fingerprint rotation on 403.

        Args:
            url: URL to fetch
            timeout: Request timeout in seconds
            json_mode: Use JSON API headers instead of HTML navigation headers
            headers: Extra headers to merge in
        """
        request_headers = self._build_headers(json_mode=json_mode, extra=headers, url=url)
        delay = self.retry_delay

        for attempt in range(self.max_retries + 1):
            response: Response = self._session.get(
                url,
                headers=request_headers,
                timeout=timeout,
                impersonate=self._fingerprint,
                allow_redirects=True,
            )

            # 403 = possibly blocked, rotate fingerprint and retry
            if response.status_code == 403 and attempt < self.max_retries:
                self._rotate_fingerprint()
                request_headers = self._build_headers(json_mode=json_mode, extra=headers, url=url)
                time.sleep(delay)
                delay *= 2
                continue

            # 429 = rate limited, wait and retry
            if response.status_code == 429 and attempt < self.max_retries:
                retry_after = response.headers.get("Retry-After")
                wait = delay
                if retry_after:
                    try:
                        wait = min(float(retry_after), 30.0)
                    except ValueError:
                        pass
                time.sleep(wait)
                delay *= 2
                continue

            # 5xx = server error, retry with backoff
            if response.status_code >= 500 and attempt < self.max_retries:
                time.sleep(delay * random.uniform(0.9, 1.1))
                delay *= 2
                continue

            return FetchResult(
                content=response.content,
                status_code=response.status_code,
                headers=dict(response.headers),
                url=str(response.url),
            )

        # Should not reach here, but just in case
        return FetchResult(
            content=response.content,
            status_code=response.status_code,
            headers=dict(response.headers),
            url=str(response.url),
        )

    def solve_amazon_captcha(self, captcha_html: str, page_url: str) -> bool:
        """Solve Amazon's rate-limit captcha inline via form submission.

        Parses the captcha page's <form>, submits it as GET with the same
        session and TLS fingerprint, and lets cookies accumulate in the
        session jar. Pins the fingerprint after solving so subsequent
        requests use the same TLS identity (Amazon binds cookies to it).

        Returns True if the form was found and submitted successfully.
        """
        # Parse the form action and hidden fields with regex
        # (simple structure: 1 form, 3 hidden inputs, always GET)
        form_match = re.search(
            r'<form[^>]*action="([^"]*)"[^>]*>',
            captcha_html, re.IGNORECASE,
        )
        if not form_match:
            log.warning("Amazon captcha: no <form> found in page")
            return False

        raw_action = html_mod.unescape(form_match.group(1))
        target_url = urljoin(page_url, raw_action)

        # Collect hidden input name/value pairs
        params = {}
        for inp_match in re.finditer(
            r'<input[^>]*type=["\']?hidden["\']?[^>]*>',
            captcha_html, re.IGNORECASE,
        ):
            inp = inp_match.group(0)
            name_m = re.search(r'name="([^"]*)"', inp)
            value_m = re.search(r'value="([^"]*)"', inp)
            if name_m:
                params[name_m.group(1)] = html_mod.unescape(
                    value_m.group(1) if value_m else ""
                )

        if not params:
            log.warning("Amazon captcha: no hidden fields found")
            return False

        log.info(
            f"Amazon captcha: submitting form to {target_url} "
            f"with {len(params)} params"
        )

        try:
            headers = self._build_headers(url=target_url)
            headers["Referer"] = page_url
            self._session.get(
                target_url,
                params=params,
                headers=headers,
                impersonate=self._fingerprint,
                allow_redirects=True,
                timeout=10,
            )
            # Pin fingerprint — cookies are bound to TLS identity
            self.pin_fingerprint()
            log.info("Amazon captcha solved, fingerprint pinned")
            return True
        except Exception:
            log.exception("Amazon captcha form submission failed")
            return False

    def close(self):
        """Close the session."""
        self._session.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
