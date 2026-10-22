"""Pure-read health metric collectors for the OpenCaseLaw observability layer.

Every function in this module:

- Returns ``None`` or an empty data structure on missing/inaccessible input.
- Performs no writes (no logs, no DB mutations, no file creation).
- Is unit-testable against synthetic inputs (paths are parameters).

Consumed by ``mcp_server.py``'s ``/metrics/health`` endpoint and the
``/dev/health`` dashboard. Adding a new freshness or pipeline signal:
write a reader here, add it to ``collect_health()``, render it in the
dashboard, optionally add an alert rule in ``health_alerts``.

The defaults assume the production layout (``/opt/caselaw/repo``) but
every function accepts an explicit path argument so tests can run
against tempdirs.
"""
from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

REPO_DIR = Path(__file__).resolve().parent
DEFAULT_DB_PATH = REPO_DIR / "output" / "decisions.db"
DEFAULT_BGER_POLLER_LOG = REPO_DIR / "logs" / "bger_poller.log"
DEFAULT_LLM_USAGE_LOG = REPO_DIR / "logs" / "llm_usage.jsonl"

# Module-level cache for freshness_seconds_by_court (keyed by str(db_path),
# value is (computed_at_ts, result_dict)). The query is GROUP BY court with
# MAX(scraped_at) — without an index on scraped_at it can take 30+s on the
# 61 GB production DB, which would wedge MCP workers if /metrics/health is
# polled at 30s cadence. Index will land in the Saturday A6 deploy; until
# then we cap the query time and cache the result.
_FRESHNESS_CACHE_TTL = 300  # 5 minutes
_FRESHNESS_MAX_QUERY_MS = 500
_freshness_cache: dict = {}


def _freshness_cache_clear() -> None:
    """Test-only helper to reset module state between runs."""
    global _freshness_cache
    _freshness_cache = {}


def _now() -> int:
    """Wall clock, separated so tests can monkeypatch."""
    return int(time.time())


def _parse_iso(s: str) -> Optional[int]:
    """Parse ISO-8601 (with 'Z' or +00:00) to unix epoch seconds.

    Returns None for malformed input — never raises.
    """
    if not s or not isinstance(s, str):
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except (ValueError, TypeError):
        return None


def freshness_seconds_by_court(
    db_path: Optional[Path] = None,
) -> dict[str, int]:
    """Per-court seconds-since-most-recent ``scraped_at``.

    Uses ``scraped_at`` because no ``ingest_ts`` column exists yet
    (planned for the A6 schema deploy). ``scraped_at`` is when the
    scraper grabbed the row, not when it landed in the published DB —
    this is documented in ``docs/observability.md``.

    Cached for 5 minutes per DB path. Hard-bounded to ~500 ms per
    query via a SQLite progress handler — without an index on
    ``scraped_at`` the GROUP BY would otherwise scan the full 61 GB
    table and wedge the calling worker. If the query is aborted, the
    last good cached value is returned (empty dict on first abort).

    Returns ``{}`` on any DB error so the consumer never crashes on
    partial outages.
    """
    if db_path is None:
        db_path = DEFAULT_DB_PATH
    key = str(db_path)
    now = _now()
    cached = _freshness_cache.get(key)
    if cached is not None and now - cached[0] < _FRESHNESS_CACHE_TTL:
        return cached[1]
    if not db_path.exists():
        return {}

    out: dict[str, int] = {}
    try:
        conn = sqlite3.connect(
            f"file:{db_path}?immutable=1", uri=True, timeout=1.0,
        )
        # Abort the query if it takes too long. progress_handler fires
        # every N opcodes; returning non-zero raises OperationalError
        # ("interrupted"). Keeps us off the critical path on a 61 GB DB
        # without scraped_at index. The 10_000 opcode interval is a
        # rough balance between responsiveness and overhead.
        start = time.monotonic()
        def _abort_if_slow() -> int:
            return 1 if (time.monotonic() - start) * 1000 > _FRESHNESS_MAX_QUERY_MS else 0
        try:
            conn.set_progress_handler(_abort_if_slow, 10_000)
        except AttributeError:
            # Older sqlite3 without set_progress_handler — proceed
            # without the cap (only a concern in dev / non-prod).
            pass

        rows = conn.execute(
            "SELECT court, MAX(scraped_at) FROM decisions "
            "WHERE scraped_at IS NOT NULL AND court IS NOT NULL "
            "GROUP BY court"
        ).fetchall()
        conn.close()
        for court, ts_str in rows:
            ts = _parse_iso(ts_str)
            if ts is not None:
                out[court] = now - ts
    except sqlite3.Error:
        # Most likely: progress-handler abort (sqlite3.OperationalError
        # "interrupted"), or the DB is being heavily written. Cache the
        # abort outcome too so we don't re-hit the slow query on every
        # subsequent /metrics/health poll; will retry after TTL.
        fallback = cached[1] if cached else {}
        _freshness_cache[key] = (now, fallback)
        return fallback

    _freshness_cache[key] = (now, out)
    return out


def pipeline_last_success_ts(
    db_path: Optional[Path] = None,
) -> Optional[int]:
    """Unix ts when ``decisions.db`` was last successfully swapped.

    Uses the file's ``mtime`` — atomic ``os.replace`` updates it on
    every full rebuild and every ``quick_publish`` swap. None if the
    file doesn't exist (DB never built / symlink broken).
    """
    if db_path is None:
        db_path = DEFAULT_DB_PATH
    try:
        return int(db_path.stat().st_mtime)
    except OSError:
        return None


def quick_publish_last_run_ts(
    log_path: Optional[Path] = None,
) -> Optional[int]:
    """Unix ts when ``bger_poller`` (which invokes ``quick_publish``) last ran.

    v1 uses the log file's ``mtime`` — when A1 lands a dedicated
    ``quick_publish_metrics.jsonl`` writer, this function should switch
    to reading the last entry's timestamp instead.
    """
    if log_path is None:
        log_path = DEFAULT_BGER_POLLER_LOG
    try:
        return int(log_path.stat().st_mtime)
    except OSError:
        return None


def bger_poller_last_run_ts(
    log_path: Optional[Path] = None,
) -> Optional[int]:
    """Alias of ``quick_publish_last_run_ts`` — same log today.

    Will diverge after A1 when ``quick_publish`` gets its own metrics
    file independent of the invoking poller.
    """
    return quick_publish_last_run_ts(log_path)


def _iter_lines_reversed(f, chunk_size: int = 1 << 20):
    """Yield the lines of binary file ``f`` last-to-first, without newlines."""
    f.seek(0, 2)
    pos = f.tell()
    tail = b""
    while pos > 0:
        step = min(chunk_size, pos)
        pos -= step
        f.seek(pos)
        lines = (f.read(step) + tail).split(b"\n")
        tail = lines.pop(0)  # may be cut mid-line; completed by the next chunk
        yield from reversed(lines)
    yield tail


# Eight workers append to llm_usage.jsonl, so rows are only roughly in time
# order. The reverse scan stops at the first row this far before the window.
_COST_SCAN_SLACK_S = 3600


def daily_cost_usd(
    hours: int = 24,
    log_path: Optional[Path] = None,
    now_ts: Optional[int] = None,
    chunk_size: int = 1 << 20,
) -> float:
    """Sum of ``cost_usd`` from ``llm_usage.jsonl`` within the last ``hours``.

    Returns 0.0 if the file doesn't exist or has no in-window rows.
    Reads the file backwards from the end and stops at the first row
    older than the window by more than ``_COST_SCAN_SLACK_S``. The log is
    append-only and never rotated (427 MB / 2M rows on 2026-10-22); the
    old full read took 6 s and pushed /metrics/health past the
    dispatcher's timeout from 2026-10-19.

    ``now_ts`` is injectable for tests; production passes None and
    uses wall clock.
    """
    if log_path is None:
        log_path = DEFAULT_LLM_USAGE_LOG
    if not log_path.exists():
        return 0.0
    now = now_ts if now_ts is not None else _now()
    cutoff = now - hours * 3600
    total = 0.0
    try:
        with open(log_path, "rb") as f:
            for line in _iter_lines_reversed(f, chunk_size):
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(row, dict):
                    continue
                ts = _parse_iso(row.get("ts", ""))
                if ts is None:
                    continue
                if ts < cutoff:
                    if ts < cutoff - _COST_SCAN_SLACK_S:
                        break
                    continue
                try:
                    total += float(row.get("cost_usd", 0))
                except (TypeError, ValueError):
                    continue
    except OSError:
        return 0.0
    return total


def collect_health() -> dict:
    """One-shot collector — returns a fresh health dict.

    Never raises; each reader is independently defensive. Caller (the
    ``/metrics/health`` endpoint) augments with ``db_generation`` and
    alert dry-run results.
    """
    return {
        "ts": _now(),
        "pipeline_last_success_ts": pipeline_last_success_ts(),
        "quick_publish_last_run_ts": quick_publish_last_run_ts(),
        "bger_poller_last_run_ts": bger_poller_last_run_ts(),
        "freshness_seconds_by_court": freshness_seconds_by_court(),
        "daily_cost_usd_24h": round(daily_cost_usd(24), 4),
    }
