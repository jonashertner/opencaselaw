"""Polite, single-connection downloader for the Bundesarchiv portal.

The historical Botschaften (1850-1999) exist only as OCR'd PDF scans on
``www.amtsdruckschriften.bar.admin.ch`` (``viewOrigDoc.do?id=<id>&action=open``,
the URL Fedlex publishes as the manifestation's ``jolux:isExemplifiedBy``).
That host's robots.txt disallows ``/viewOrigDoc.do``. The decision to fetch
anyway was taken by Jonas on 2026-09-09 for a recognised non-profit effort,
on the condition that the fetcher is exemplary. This module is that
condition:

* it identifies itself, with a contact address, in the User-Agent;
* one connection, one request at a time — never parallel;
* at least ``min_interval`` seconds (default 2.5 s) between the end of one
  request and the start of the next;
* on 429 / 403 / 5xx or a transport error it backs off exponentially
  (``Retry-After`` honoured when larger) and stops hard after
  ``max_consecutive_failures`` such answers in a row (default 3) — the
  run ends, nothing is retried until the next run;
* a per-run cap (``--max-per-run`` in the CLI) keeps a run inside one night.

The fetcher does not know about the corpus; the caller decides what to do
with the bytes and keeps the resume state (``botschaft_fetch_state`` in
materialien.db, keyed by ELI URI). Nothing here retries a document within a
run: a document that fails is recorded and comes up again next run.

The transport is injectable so the backoff and stop logic is tested offline
with a scripted fake (``tests/test_materialien_historical.py``).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable

log = logging.getLogger("bar_fetcher")

USER_AGENT = (
    "OpenCaseLaw/1.0 (non-profit legal corpus; "
    "contact: jonashertner@protonmail.ch; +https://opencaselaw.ch)"
)
BAR_HOST = "www.amtsdruckschriften.bar.admin.ch"

DEFAULT_MIN_INTERVAL = 2.5
DEFAULT_MAX_CONSECUTIVE_FAILURES = 3
DEFAULT_BACKOFF_BASE = 15.0     # seconds after the first failure
DEFAULT_BACKOFF_MAX = 600.0
DEFAULT_TIMEOUT = 120.0

# A transport answers (status_code, headers, body). It raises on transport
# errors (DNS, connect, read timeout); the fetcher turns those into failures.
Transport = Callable[[str, dict, float], tuple[int, dict, bytes]]


class FetchStopped(RuntimeError):
    """Raised once the burst limit is reached; the run must end."""


@dataclass
class FetchResult:
    url: str
    status: int | None          # HTTP status, None on transport error
    content: bytes | None
    error: str | None = None
    elapsed: float = 0.0

    @property
    def ok(self) -> bool:
        return self.status == 200 and bool(self.content)


def _requests_transport(session=None) -> Transport:
    """One ``requests.Session`` with a pool of exactly one connection."""
    import requests
    from requests.adapters import HTTPAdapter

    s = session or requests.Session()
    adapter = HTTPAdapter(pool_connections=1, pool_maxsize=1, max_retries=0)
    s.mount("https://", adapter)
    s.mount("http://", adapter)

    def _do(url: str, headers: dict, timeout: float) -> tuple[int, dict, bytes]:
        r = s.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        return r.status_code, {k.lower(): v for k, v in r.headers.items()}, r.content

    return _do


class PoliteFetcher:
    """Serial, rate-limited GET with exponential backoff and a hard stop."""

    def __init__(
        self,
        *,
        min_interval: float = DEFAULT_MIN_INTERVAL,
        max_consecutive_failures: int = DEFAULT_MAX_CONSECUTIVE_FAILURES,
        backoff_base: float = DEFAULT_BACKOFF_BASE,
        backoff_max: float = DEFAULT_BACKOFF_MAX,
        timeout: float = DEFAULT_TIMEOUT,
        user_agent: str = USER_AGENT,
        transport: Transport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if min_interval < 2.0:
            # The brief's floor. Faster than this is not "exemplary".
            raise ValueError("min_interval must be at least 2.0 seconds")
        self.min_interval = float(min_interval)
        self.max_consecutive_failures = int(max_consecutive_failures)
        self.backoff_base = float(backoff_base)
        self.backoff_max = float(backoff_max)
        self.timeout = float(timeout)
        self.headers = {
            "User-Agent": user_agent,
            "Accept": "application/pdf,*/*;q=0.5",
            "Accept-Language": "de-CH,de;q=0.9,fr;q=0.8,it;q=0.7",
        }
        self._transport = transport or _requests_transport()
        self._sleep = sleep
        self._clock = clock
        self._last_end: float | None = None
        self.consecutive_failures = 0
        self.stopped = False
        self.stop_reason: str | None = None
        self.requests_made = 0
        self.bytes_received = 0
        self.log: list[tuple[str, int | None]] = []

    # ── pacing ──────────────────────────────────────────────────────

    def _wait_for_slot(self) -> None:
        if self._last_end is None:
            return
        due = self._last_end + self.min_interval
        now = self._clock()
        if now < due:
            self._sleep(due - now)

    # ── main entry ──────────────────────────────────────────────────

    def fetch(self, url: str) -> FetchResult:
        """GET ``url``. Returns a FetchResult; raises FetchStopped when the
        consecutive-failure limit is reached (after recording that
        failure)."""
        if self.stopped:
            raise FetchStopped(self.stop_reason or "fetcher stopped")
        self._wait_for_slot()
        t0 = self._clock()
        self.requests_made += 1
        try:
            status, headers, body = self._transport(url, dict(self.headers), self.timeout)
        except Exception as e:  # transport-level failure
            self._last_end = self._clock()
            result = FetchResult(url=url, status=None, content=None,
                                 error=f"{type(e).__name__}: {e}",
                                 elapsed=self._last_end - t0)
            self.log.append((url, None))
            self._on_failure(result, retry_after=None)
            return result
        self._last_end = self._clock()
        self.log.append((url, status))
        elapsed = self._last_end - t0
        if status == 200:
            self.consecutive_failures = 0
            self.bytes_received += len(body or b"")
            return FetchResult(url=url, status=status, content=body, elapsed=elapsed)
        result = FetchResult(url=url, status=status, content=None,
                             error=f"HTTP {status}", elapsed=elapsed)
        if status in (429, 403) or status >= 500:
            self._on_failure(result, retry_after=_retry_after_seconds(headers))
        else:
            # 404 / 410 / 3xx-without-body: a per-document answer, not a
            # sign the host wants us gone. Does not count toward the burst.
            self.consecutive_failures = 0
        return result

    def _on_failure(self, result: FetchResult, retry_after: float | None) -> None:
        self.consecutive_failures += 1
        n = self.consecutive_failures
        log.warning("  %s -> %s (consecutive failure %d/%d)",
                    result.url, result.error, n, self.max_consecutive_failures)
        if n >= self.max_consecutive_failures:
            self.stopped = True
            self.stop_reason = (
                f"{n} consecutive failures on {BAR_HOST}; last: {result.error}"
            )
            raise FetchStopped(self.stop_reason)
        delay = min(self.backoff_max, self.backoff_base * (2 ** (n - 1)))
        if retry_after is not None:
            delay = max(delay, min(self.backoff_max, retry_after))
        log.warning("  backing off %.0f s before the next request", delay)
        self._sleep(delay)


def _retry_after_seconds(headers: dict | None) -> float | None:
    if not headers:
        return None
    raw = headers.get("retry-after") or headers.get("Retry-After")
    if not raw:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None
