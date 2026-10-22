"""Tests for the health metrics + synthetic alerts modules.

Both modules are pure-read / pure-logic — these tests run against
tempdirs with synthetic inputs. No production data needed.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

import pytest

REPO_DIR = Path(__file__).resolve().parents[1]
if str(REPO_DIR) not in sys.path:
    sys.path.insert(0, str(REPO_DIR))

import health_alerts  # noqa: E402
import health_metrics  # noqa: E402


# ──────────────────────────── health_metrics ──────────────────────────────


def _make_decisions_db(path: Path, rows: list[tuple[str, str]]) -> None:
    """rows = [(court, scraped_at_iso), ...]"""
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE decisions ("
        "decision_id TEXT PRIMARY KEY, "
        "court TEXT, scraped_at TEXT)"
    )
    for i, (court, ts) in enumerate(rows):
        conn.execute(
            "INSERT INTO decisions VALUES (?, ?, ?)",
            (f"id_{i}", court, ts),
        )
    conn.commit()
    conn.close()


def test_freshness_returns_empty_when_db_missing(tmp_path):
    health_metrics._freshness_cache_clear()
    out = health_metrics.freshness_seconds_by_court(
        tmp_path / "nope.db"
    )
    assert out == {}


def test_freshness_per_court(tmp_path, monkeypatch):
    health_metrics._freshness_cache_clear()
    # 2026-05-17 12:00:00 UTC
    monkeypatch.setattr(health_metrics, "_now", lambda: 1_779_019_200)
    p = tmp_path / "decisions.db"
    _make_decisions_db(p, [
        # BGer most recent: 2026-05-17T08:00:00Z = 1779004800 → 14400s old
        ("BGer", "2026-05-17T00:00:00Z"),
        ("BGer", "2026-05-17T08:00:00Z"),
        # BVGer: 2026-05-16T12:00:00Z = 1778932800 → 86400s old
        ("BVGer", "2026-05-16T12:00:00Z"),
        ("ZH", None),  # filtered out (NULL scraped_at)
    ])
    out = health_metrics.freshness_seconds_by_court(p)
    assert out == {"BGer": 14_400, "BVGer": 86_400}


def test_freshness_cached_between_calls(tmp_path, monkeypatch):
    """Two successive calls within TTL hit the cache (no re-query).

    Verified by: change the DB after the first call; the second call
    should still return the first-call result.
    """
    monkeypatch.setattr(health_metrics, "_now", lambda: 1_779_019_200)
    health_metrics._freshness_cache_clear()
    p = tmp_path / "decisions.db"
    _make_decisions_db(p, [("BGer", "2026-05-17T08:00:00Z")])
    first = health_metrics.freshness_seconds_by_court(p)
    assert "BGer" in first

    # Wipe rows; cached result should still be returned
    conn = sqlite3.connect(str(p))
    conn.execute("DELETE FROM decisions")
    conn.commit()
    conn.close()

    second = health_metrics.freshness_seconds_by_court(p)
    assert second == first  # cache hit, even though DB now empty


def test_freshness_cache_expires(tmp_path, monkeypatch):
    """After TTL elapses, cache is bypassed and DB re-read."""
    health_metrics._freshness_cache_clear()
    monkeypatch.setattr(health_metrics, "_now", lambda: 1_779_019_200)
    p = tmp_path / "decisions.db"
    _make_decisions_db(p, [("BGer", "2026-05-17T08:00:00Z")])
    first = health_metrics.freshness_seconds_by_court(p)
    assert "BGer" in first

    # Wipe rows
    conn = sqlite3.connect(str(p))
    conn.execute("DELETE FROM decisions")
    conn.commit()
    conn.close()

    # Advance the clock past TTL
    monkeypatch.setattr(
        health_metrics, "_now",
        lambda: 1_779_019_200 + health_metrics._FRESHNESS_CACHE_TTL + 10,
    )
    second = health_metrics.freshness_seconds_by_court(p)
    assert second == {}  # cache expired, fresh read sees empty


def test_freshness_skips_unparseable_timestamps(tmp_path, monkeypatch):
    health_metrics._freshness_cache_clear()
    monkeypatch.setattr(health_metrics, "_now", lambda: 1_779_019_200)
    p = tmp_path / "decisions.db"
    _make_decisions_db(p, [
        ("BGer", "garbage"),
        ("BVGer", "2026-05-17T00:00:00Z"),
    ])
    out = health_metrics.freshness_seconds_by_court(p)
    assert "BGer" not in out
    assert "BVGer" in out


def test_pipeline_last_success_uses_mtime(tmp_path):
    p = tmp_path / "decisions.db"
    p.write_bytes(b"x")
    expected = int(p.stat().st_mtime)
    assert health_metrics.pipeline_last_success_ts(p) == expected


def test_pipeline_last_success_missing(tmp_path):
    assert health_metrics.pipeline_last_success_ts(
        tmp_path / "nope.db"
    ) is None


def test_quick_publish_last_run_uses_log_mtime(tmp_path):
    p = tmp_path / "bger_poller.log"
    p.write_text("log line\n")
    expected = int(p.stat().st_mtime)
    assert health_metrics.quick_publish_last_run_ts(p) == expected


def test_quick_publish_last_run_missing(tmp_path):
    assert health_metrics.quick_publish_last_run_ts(
        tmp_path / "nope.log"
    ) is None


def test_daily_cost_sums_within_window(tmp_path):
    p = tmp_path / "llm_usage.jsonl"
    # now = Sun 2026-05-17 12:00 UTC
    now = 1_779_019_200
    # Append order, as the workers write it (oldest first).
    p.write_text(
        # 36h ago — outside 24h window
        json.dumps({"ts": "2026-05-16T00:00:00Z", "cost_usd": 1.00}) + "\n"
        # 12h ago — in window
        + json.dumps({"ts": "2026-05-17T00:00:00Z", "cost_usd": 0.05}) + "\n"
        # 1h ago — in window
        + json.dumps({"ts": "2026-05-17T11:00:00Z", "cost_usd": 0.10}) + "\n"
    )
    total = health_metrics.daily_cost_usd(24, p, now_ts=now)
    assert round(total, 2) == 0.15


def test_daily_cost_handles_malformed_rows(tmp_path):
    p = tmp_path / "llm_usage.jsonl"
    now = 1_779_019_200
    p.write_text(
        "not json\n"
        + json.dumps({"ts": "garbage", "cost_usd": 99}) + "\n"
        + json.dumps({"ts": "2026-05-17T11:00:00Z", "cost_usd": "not a number"}) + "\n"
        + json.dumps({"ts": "2026-05-17T11:00:00Z", "cost_usd": 0.25}) + "\n"
    )
    assert health_metrics.daily_cost_usd(24, p, now_ts=now) == 0.25


def _iso(ts: int) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def test_daily_cost_reverse_scan_matches_full_sum_across_chunks(tmp_path):
    """Rows straddle many tiny read chunks; the sum must not lose or
    double-count a line cut at a chunk boundary."""
    p = tmp_path / "llm_usage.jsonl"
    now = 1_779_019_200
    rows = [
        {"ts": _iso(now - 48 * 3600 + i * 600), "cost_usd": 0.001 * (i + 1),
         "feature": "query_parse" * (i % 3)}
        for i in range(48 * 6)
    ]
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))
    expected = sum(r["cost_usd"] for i, r in enumerate(rows)
                   if now - 48 * 3600 + i * 600 >= now - 24 * 3600)
    for chunk in (7, 64, 1 << 20):
        got = health_metrics.daily_cost_usd(24, p, now_ts=now, chunk_size=chunk)
        assert round(got, 6) == round(expected, 6)


def test_daily_cost_tolerates_misordered_rows_near_cutoff(tmp_path):
    """Concurrent writers interleave: a row just before the window must
    not end the scan early."""
    p = tmp_path / "llm_usage.jsonl"
    now = 1_779_019_200
    cutoff = now - 24 * 3600
    p.write_text(
        json.dumps({"ts": _iso(cutoff + 60), "cost_usd": 0.20}) + "\n"
        + json.dumps({"ts": _iso(cutoff - 1800), "cost_usd": 1.00}) + "\n"
        + json.dumps({"ts": _iso(now - 60), "cost_usd": 0.10})  # no trailing \n
    )
    assert round(health_metrics.daily_cost_usd(24, p, now_ts=now), 2) == 0.30


def test_daily_cost_stops_at_row_well_before_window(tmp_path):
    """The scan ends at the first row older than the window plus slack,
    so the file head is never read."""
    p = tmp_path / "llm_usage.jsonl"
    now = 1_779_019_200
    cutoff = now - 24 * 3600
    p.write_text(
        json.dumps({"ts": _iso(now - 60), "cost_usd": 5.00}) + "\n"  # unreachable
        + json.dumps({"ts": _iso(cutoff - 2 * 3600), "cost_usd": 1.00}) + "\n"
        + json.dumps({"ts": _iso(now - 60), "cost_usd": 0.10}) + "\n"
    )
    assert round(health_metrics.daily_cost_usd(24, p, now_ts=now), 2) == 0.10


def test_daily_cost_zero_when_missing(tmp_path):
    assert health_metrics.daily_cost_usd(24, tmp_path / "nope.jsonl") == 0.0


def test_collect_health_structure(tmp_path, monkeypatch):
    """collect_health() returns the expected keys even when inputs missing."""
    health_metrics._freshness_cache_clear()
    monkeypatch.setattr(
        health_metrics, "DEFAULT_DB_PATH", tmp_path / "no_db",
    )
    monkeypatch.setattr(
        health_metrics, "DEFAULT_BGER_POLLER_LOG", tmp_path / "no_log",
    )
    monkeypatch.setattr(
        health_metrics, "DEFAULT_LLM_USAGE_LOG", tmp_path / "no_usage",
    )
    monkeypatch.setattr(health_metrics, "_now", lambda: 1_779_019_200)
    out = health_metrics.collect_health()
    assert set(out.keys()) == {
        "ts",
        "pipeline_last_success_ts",
        "quick_publish_last_run_ts",
        "bger_poller_last_run_ts",
        "freshness_seconds_by_court",
        "daily_cost_usd_24h",
    }
    assert out["ts"] == 1_779_019_200
    assert out["pipeline_last_success_ts"] is None
    assert out["freshness_seconds_by_court"] == {}
    assert out["daily_cost_usd_24h"] == 0.0


# ───────────────────────────── health_alerts ──────────────────────────────


def _monday_2026_05_18_12_00_utc() -> int:
    # Mon 2026-05-18 12:00:00 UTC
    return 1_779_105_600


def _sunday_2026_05_17_12_00_utc() -> int:
    # Sun 2026-05-17 12:00:00 UTC
    return 1_779_019_200


def test_pipeline_stale_fires_above_threshold():
    now = _monday_2026_05_18_12_00_utc()
    health = {"pipeline_last_success_ts": now - 27 * 3600}  # 27h ago
    result = health_alerts.check_pipeline_stale(health, now=now)
    assert result is not None
    assert result["key"] == "pipeline_stale"
    assert result["level"] == "critical"
    assert result["age_hours"] == 27.0


def test_pipeline_stale_clear_under_threshold():
    now = _monday_2026_05_18_12_00_utc()
    health = {"pipeline_last_success_ts": now - 10 * 3600}
    assert health_alerts.check_pipeline_stale(health, now=now) is None


def test_pipeline_stale_unknown_when_missing():
    result = health_alerts.check_pipeline_stale({"pipeline_last_success_ts": None})
    assert result is not None
    assert result["key"] == "pipeline_unknown"


def test_quick_publish_stale_exempts_weekends():
    now = _sunday_2026_05_17_12_00_utc()
    health = {"quick_publish_last_run_ts": now - 48 * 3600}  # 48h ago
    assert health_alerts.check_quick_publish_stale(health, now=now) is None


def test_quick_publish_stale_fires_on_weekday():
    now = _monday_2026_05_18_12_00_utc()
    health = {"quick_publish_last_run_ts": now - 3 * 3600}  # 3h ago
    result = health_alerts.check_quick_publish_stale(health, now=now)
    assert result is not None
    assert result["key"] == "quick_publish_stale"
    assert result["age_hours"] == 3.0


def test_quick_publish_stale_clear_on_weekday():
    now = _monday_2026_05_18_12_00_utc()
    health = {"quick_publish_last_run_ts": now - 60 * 60}  # 1h ago
    assert health_alerts.check_quick_publish_stale(health, now=now) is None


def test_quick_publish_stale_quiet_in_the_evening():
    """Poller's last fire is 16:00 UTC; by 20:00 the age is legitimately
    ~4h. Pre-fix this false-fired every weekday evening."""
    now = _monday_2026_05_18_12_00_utc() + 8 * 3600  # Monday 20:00 UTC
    health = {"quick_publish_last_run_ts": now - 4 * 3600}
    assert health_alerts.check_quick_publish_stale(health, now=now) is None


def test_quick_publish_stale_quiet_pre_dawn():
    """Before the 05:00 UTC first fire the overnight gap is expected."""
    now = _monday_2026_05_18_12_00_utc() - 9 * 3600  # Monday 03:00 UTC
    health = {"quick_publish_last_run_ts": now - 11 * 3600}
    assert health_alerts.check_quick_publish_stale(health, now=now) is None


def test_quick_publish_stale_fires_monday_morning_after_missed_polls():
    """Monday 08:00 UTC with the last run still Friday afternoon means
    the 05/06/07:00 polls all failed — that must fire."""
    now = _monday_2026_05_18_12_00_utc() - 4 * 3600  # Monday 08:00 UTC
    health = {"quick_publish_last_run_ts": now - 64 * 3600}  # Friday ~16:00
    result = health_alerts.check_quick_publish_stale(health, now=now)
    assert result is not None
    assert result["key"] == "quick_publish_stale"


def test_mcp_error_rate_silent_below_samples():
    metrics = {
        "tool_calls": {"search": 30, "get_decision": 20},  # 50 < 100
        "tool_errors": {"search": 5},
    }
    assert health_alerts.check_mcp_error_rate(metrics) is None


def test_mcp_error_rate_silent_below_threshold():
    metrics = {
        "tool_calls": {"search": 1000},
        "tool_errors": {"search": 5},  # 0.5%
    }
    assert health_alerts.check_mcp_error_rate(metrics) is None


def test_mcp_error_rate_fires_above_threshold():
    metrics = {
        "tool_calls": {"search": 500, "get_decision": 500},
        "tool_errors": {"search": 30},  # 3%
    }
    result = health_alerts.check_mcp_error_rate(metrics)
    assert result is not None
    assert result["key"] == "mcp_error_rate_high"
    assert result["rate"] == 0.03


def test_check_all_empty_when_clear():
    now = _monday_2026_05_18_12_00_utc()
    health = {
        "pipeline_last_success_ts": now - 60 * 60,
        "quick_publish_last_run_ts": now - 10 * 60,
    }
    assert health_alerts.check_all(health, metrics=None, now=now) == []


def test_check_all_aggregates_all_fired():
    now = _monday_2026_05_18_12_00_utc()
    health = {
        "pipeline_last_success_ts": now - 30 * 3600,  # fires
        "quick_publish_last_run_ts": now - 5 * 3600,  # fires
    }
    metrics = {
        "tool_calls": {"search": 1000},
        "tool_errors": {"search": 50},  # fires
    }
    out = health_alerts.check_all(health, metrics=metrics, now=now)
    keys = {a["key"] for a in out}
    assert keys == {"pipeline_stale", "quick_publish_stale", "mcp_error_rate_high"}


def test_check_all_one_rule_failing_does_not_suppress_others(monkeypatch):
    """If a rule raises, the engine emits a synthetic error alert and
    continues — other rules still fire.

    The error key is built from ``rule.__name__``, so the replacement
    function gets its ``__name__`` set to match what we registered as,
    keeping the operator-visible key stable across monkey-patches.
    """
    def boom(health, now=None):
        raise RuntimeError("rule crashed")
    boom.__name__ = "check_pipeline_stale"  # preserve operator-visible key

    monkeypatch.setattr(health_alerts, "check_pipeline_stale", boom)

    now = _monday_2026_05_18_12_00_utc()
    health = {"quick_publish_last_run_ts": now - 5 * 3600}
    out = health_alerts.check_all(health, now=now)
    keys = {a["key"] for a in out}
    assert "check_pipeline_stale_error" in keys
    assert "quick_publish_stale" in keys
