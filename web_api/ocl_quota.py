"""Per-IP daily quota for expensive LLM-backed REST endpoints.

Defends against Scenario B/C/D in the cost-risk audit (commercial heavy
use, AI-startup inner-loop integration, malicious scraping). Public REST
endpoints that fan out to Claude (attest, verify-claim, mock-decision,
exam-question, trends, doctrine) check this quota before issuing the
LLM call. Returns 429 when exceeded.

Fail-open: if the quota DB is unavailable, requests are allowed through
and the failure is logged. Better to over-bill than to over-deny on a
flaky sidecar.

API-key override: a commercial caller can mint a key (manually for now,
via scripts/mint_quota_key.py) and pass it as the X-OCL-Key header. Keys
have their own per-endpoint quota multipliers — set to 10× for known
adopters, 100× for paid commercial. This is the scaffold for the
full Stripe-integrated commercial tier (deferred).

Storage:
    output/quota.db — writable sidecar, WAL mode.
      quota_calls(ip, day, endpoint, calls)         INTEGER PK on (ip,day,endpoint)
      api_keys(key, label, multiplier, created_at)
      quota_alerts(day, endpoint, ip, calls, alerted_at)  — audit trail

Cleanup: rows older than 30 days are removed by a once-a-day vacuum
called from the worker startup path (cheap, sqlite handles it fast).
"""
from __future__ import annotations

import logging
import os
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger("ocl_quota")

QUOTA_DB_PATH = Path(
    os.environ.get(
        "OCL_QUOTA_DB",
        str(Path(__file__).resolve().parents[1] / "output" / "quota.db"),
    )
)

# Per-endpoint daily limits (free / unauthenticated tier).
# Calibrated against ordinary institutional-adopter traffic, while high-volume
# AI-startup inner-loop integrations (~10k calls/day) are
# capped at a level that costs ≤$50/day in worst case.
DEFAULT_QUOTAS: dict[str, int] = {
    # Sonnet-heavy multi-rail audits — ~$0.05-0.30 per call
    "attest": 200,
    "verify_claim": 200,
    # Single-call Sonnet — ~$0.05-0.50 per call
    "mock_decision": 50,
    "exam_question": 50,
    "trends": 100,
    "doctrine_llm": 200,
    # Strengthen / Reflect / Find-Support are inside /billing/* and have
    # their own Stripe-tied quota; we shadow them here as a defense in
    # depth in case the open paths are used to bypass.
    "strengthen": 100,
    "reflect": 100,
    "find_support": 100,
    # Search-with-Haiku-expansion — light per-call cost ($0.001-0.003).
    # Permissive: only a real bot rotating thousands of unique queries
    # would hit this floor. Optional — disabled by default.
    # "search_haiku": 5000,
}

# IPs / UAs that get unlimited access (set via env var, comma-separated).
# Example: OCL_QUOTA_ALLOWLIST="193.247.119.164,127.0.0.1"
ALLOWLIST = set(
    ip.strip()
    for ip in os.environ.get("OCL_QUOTA_ALLOWLIST", "127.0.0.1,::1").split(",")
    if ip.strip()
)

# ── Operator alerting ─────────────────────────────────────────
# quota_alerts rows are an audit trail nobody watches in real time; this
# push is the delivery channel. Found missing 2026-08-23: a client burned
# through the verify_claim quota at 09:43 UTC and the first human notice
# was an evening ledger review. Pushed WITHOUT the offending IP — ntfy.sh
# topics are public-by-name, so an IP there would leak personal data to
# anyone who guesses the topic. The IP lives in the quota_alerts row.
NTFY_URL = os.environ.get("NTFY_URL", "https://ntfy.sh")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "opencaselaw-prod")
QUOTA_NTFY_ENABLED = os.environ.get(
    "OCL_QUOTA_NTFY", "1"
).lower() not in {"0", "false", "no"}


def _notify_quota_alert(endpoint: str, calls: int, limit: int) -> None:
    """Fire-and-forget ntfy push on a FRESH quota breach. Never raises;
    a failed push must not affect the request being throttled."""
    if not QUOTA_NTFY_ENABLED:
        return
    try:
        import urllib.request
        req = urllib.request.Request(
            f"{NTFY_URL}/{NTFY_TOPIC}",
            data=(
                f"quota exceeded: {endpoint} at {calls} calls "
                f"(limit {limit}). Offending IP: see quota_alerts "
                f"(output/quota.db) or the per-IP ledger."
            ).encode("utf-8"),
            headers={
                "Title": "OpenCaseLaw quota alert",
                "Tags": "no_entry",
                "Priority": "high",
            },
            method="POST",
        )
        urllib.request.urlopen(req, timeout=3).close()
    except Exception as e:  # noqa: BLE001
        log.warning("quota ntfy push failed: %s", e)


# ── Schema ─────────────────────────────────────────────────────────


_SCHEMA = """
CREATE TABLE IF NOT EXISTS quota_calls (
    ip TEXT NOT NULL,
    day TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    calls INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (ip, day, endpoint)
);
CREATE INDEX IF NOT EXISTS idx_quota_day ON quota_calls(day);

CREATE TABLE IF NOT EXISTS api_keys (
    key TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    multiplier REAL NOT NULL DEFAULT 10.0,
    created_at TEXT NOT NULL,
    note TEXT
);

CREATE TABLE IF NOT EXISTS quota_alerts (
    day TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    ip TEXT NOT NULL,
    calls INTEGER NOT NULL,
    alerted_at TEXT NOT NULL,
    PRIMARY KEY (day, endpoint, ip)
);
"""


def _init_db() -> None:
    """Create schema + WAL mode. Idempotent."""
    QUOTA_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(QUOTA_DB_PATH))
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        conn.close()


@contextmanager
def _connect(read_only: bool = False):
    if read_only:
        conn = sqlite3.connect(
            f"file:{QUOTA_DB_PATH}?mode=ro", uri=True, timeout=2.0
        )
    else:
        conn = sqlite3.connect(str(QUOTA_DB_PATH), timeout=2.0)
        conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


# ── Public API ─────────────────────────────────────────────────────


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _seconds_to_midnight_utc() -> int:
    now = datetime.now(timezone.utc)
    midnight = (now + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return int((midnight - now).total_seconds())


def _resolve_multiplier(api_key: Optional[str]) -> tuple[float, Optional[str]]:
    """Look up an API key; return (multiplier, label) or (1.0, None)."""
    if not api_key:
        return 1.0, None
    try:
        with _connect(read_only=True) as conn:
            row = conn.execute(
                "SELECT multiplier, label FROM api_keys WHERE key = ?",
                (api_key.strip(),),
            ).fetchone()
            if row:
                return float(row["multiplier"]), row["label"]
    except Exception as e:
        log.warning("api_key lookup failed (fail-open): %s", e)
    return 1.0, None


class QuotaResult:
    __slots__ = ("allowed", "calls", "limit", "endpoint", "label", "remaining")

    def __init__(self, allowed, calls, limit, endpoint, label, remaining):
        self.allowed = allowed
        self.calls = calls
        self.limit = limit
        self.endpoint = endpoint
        self.label = label
        self.remaining = remaining


def check_and_increment(
    ip: str,
    endpoint: str,
    api_key: Optional[str] = None,
) -> QuotaResult:
    """Atomically increment the IP's counter for today's bucket on this
    endpoint. Returns a QuotaResult with .allowed indicating whether the
    request should proceed.

    Fail-open: any failure to read or write the quota DB returns
    allowed=True so a flaky sidecar doesn't bring down the API.
    """
    base = DEFAULT_QUOTAS.get(endpoint)
    if base is None:
        # Unknown endpoint — not gated, allow.
        return QuotaResult(True, 0, 0, endpoint, None, 0)

    if ip in ALLOWLIST:
        return QuotaResult(True, 0, -1, endpoint, "allowlist", -1)

    multiplier, label = _resolve_multiplier(api_key)
    effective_limit = int(base * multiplier)

    day = _today_utc()
    try:
        with _connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """INSERT INTO quota_calls (ip, day, endpoint, calls)
                   VALUES (?, ?, ?, 1)
                   ON CONFLICT(ip, day, endpoint)
                   DO UPDATE SET calls = calls + 1""",
                (ip, day, endpoint),
            )
            row = conn.execute(
                "SELECT calls FROM quota_calls "
                "WHERE ip = ? AND day = ? AND endpoint = ?",
                (ip, day, endpoint),
            ).fetchone()
            n = int(row["calls"]) if row else 1
            conn.commit()
    except Exception as e:
        log.error("quota check failed (fail-open): %s", e)
        return QuotaResult(True, 0, effective_limit, endpoint, label, effective_limit)

    allowed = n <= effective_limit
    if not allowed:
        # Emit one alert per (day, endpoint, ip) so logs don't flood.
        try:
            with _connect() as conn:
                cur = conn.execute(
                    """INSERT OR IGNORE INTO quota_alerts
                       (day, endpoint, ip, calls, alerted_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (day, endpoint, ip, n, datetime.now(timezone.utc).isoformat()),
                )
                conn.commit()
            # rowcount 1 ⇔ the OR IGNORE actually inserted ⇔ first breach
            # of this (day, endpoint, ip) — push exactly once, not per call.
            if cur.rowcount == 1:
                _notify_quota_alert(endpoint, n, effective_limit)
            log.warning(
                "quota.exceeded ip=%s endpoint=%s calls=%d limit=%d label=%s",
                ip, endpoint, n, effective_limit, label,
            )
        except Exception:
            pass
    return QuotaResult(
        allowed=allowed,
        calls=n,
        limit=effective_limit,
        endpoint=endpoint,
        label=label,
        remaining=max(0, effective_limit - n),
    )


def usage_summary(days: int = 7) -> dict:
    """Read-only summary for monitoring (#7). Returns aggregate stats."""
    try:
        with _connect(read_only=True) as conn:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
                "%Y-%m-%d"
            )
            by_endpoint = [
                dict(r) for r in conn.execute(
                    """SELECT endpoint, SUM(calls) AS calls,
                              COUNT(DISTINCT ip) AS distinct_ips
                       FROM quota_calls
                       WHERE day >= ?
                       GROUP BY endpoint ORDER BY calls DESC""",
                    (cutoff,),
                ).fetchall()
            ]
            top_ips = [
                dict(r) for r in conn.execute(
                    """SELECT ip, endpoint, SUM(calls) AS calls
                       FROM quota_calls
                       WHERE day >= ?
                       GROUP BY ip, endpoint
                       ORDER BY calls DESC LIMIT 25""",
                    (cutoff,),
                ).fetchall()
            ]
            alerts = [
                dict(r) for r in conn.execute(
                    """SELECT day, endpoint, ip, calls, alerted_at
                       FROM quota_alerts
                       WHERE day >= ?
                       ORDER BY alerted_at DESC LIMIT 50""",
                    (cutoff,),
                ).fetchall()
            ]
            return {
                "days": days,
                "by_endpoint": by_endpoint,
                "top_ips": top_ips,
                "recent_alerts": alerts,
                "default_quotas": DEFAULT_QUOTAS,
                "allowlist": sorted(ALLOWLIST),
            }
    except Exception as e:
        return {"error": str(e)}


def cleanup_old_rows(days_to_keep: int = 30) -> int:
    """Drop quota_calls older than days_to_keep. Returns row count deleted."""
    try:
        with _connect() as conn:
            cutoff = (
                datetime.now(timezone.utc) - timedelta(days=days_to_keep)
            ).strftime("%Y-%m-%d")
            cur = conn.execute(
                "DELETE FROM quota_calls WHERE day < ?", (cutoff,)
            )
            n = cur.rowcount
            conn.execute(
                "DELETE FROM quota_alerts WHERE day < ?", (cutoff,)
            )
            conn.commit()
            return n
    except Exception as e:
        log.error("quota cleanup failed: %s", e)
        return 0


def reset_for_ip(ip: str, endpoint: Optional[str] = None) -> int:
    """Admin helper: wipe today's counter for an IP. Useful for
    unblocking a legitimate adopter who hit the quota."""
    day = _today_utc()
    try:
        with _connect() as conn:
            if endpoint:
                cur = conn.execute(
                    "DELETE FROM quota_calls "
                    "WHERE ip = ? AND day = ? AND endpoint = ?",
                    (ip, day, endpoint),
                )
            else:
                cur = conn.execute(
                    "DELETE FROM quota_calls WHERE ip = ? AND day = ?",
                    (ip, day),
                )
            conn.commit()
            return cur.rowcount
    except Exception as e:
        log.error("quota reset failed: %s", e)
        return 0


# Initialize at import time so workers come up with schema in place.
try:
    _init_db()
except Exception as e:
    log.error("quota DB init failed: %s", e)
