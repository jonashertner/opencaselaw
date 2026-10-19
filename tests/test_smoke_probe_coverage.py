"""The production smoke probe must watch search, and must not cry wolf.

Both properties come from the 2026-08-22 incident. Search returned 500 for
~70 minutes (458 tracebacks, 12:41–13:24 UTC) and every probe stayed green,
because the set covered health, one decision page, one export and publish
freshness — nothing that ran a query. Meanwhile `publish_freshness` was
*already* red, as it was for ~13 hours of every day while the nightly ran
against a 28 h threshold. So even a probe that had caught the outage would
have landed in a channel that was already alarming.

A monitor that misses the main verb and shouts during normal operation is
worse than none: it costs attention and buys nothing.

Offline — no network. Probe composition is inspected structurally; the
freshness logic runs against temp files.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from quality import smoke  # noqa: E402


def _dummy(name: str) -> smoke.ProbeResult:
    return smoke.ProbeResult(name=name, url="", status=200, elapsed_ms=0.0,
                             content_type="", bytes_read=0, passed=True)


def _collect_probes(monkeypatch) -> list[tuple]:
    """Probe definitions, without issuing a single request."""
    captured: list[tuple] = []

    def fake(name, url, **kw):
        captured.append((name, url, kw))
        return _dummy(name)

    monkeypatch.setattr(smoke, "_probe", fake)
    monkeypatch.setattr(smoke, "_probe_publish_freshness",
                        lambda: _dummy("publish_freshness"))
    monkeypatch.setattr(smoke, "_probe_served_data_age",
                        lambda url: captured.append(("served_data_age", url, {})) or _dummy("served_data_age"))
    monkeypatch.setattr(smoke, "_probe_serving_pressure",
                        lambda: captured.append(("serving_io_pressure", "", {})) or _dummy("serving_io_pressure"))
    smoke.run_smoke("https://example.invalid")
    return captured


# ── the gap that let a 70-minute search outage pass unnoticed ────────────────

def test_a_search_probe_exists(monkeypatch):
    names = [n for n, _, _ in _collect_probes(monkeypatch)]
    assert "search_query" in names, (
        f"no search probe among {names} — a total search outage would not be "
        "detected, which is exactly what happened on 2026-08-22"
    )


def test_the_search_probe_actually_runs_a_query(monkeypatch):
    urls = {n: u for n, u, _ in _collect_probes(monkeypatch)}
    assert "/api/decisions" in urls["search_query"]
    assert "query=" in urls["search_query"]


def test_the_search_probe_requires_a_row_not_just_a_200(monkeypatch):
    """`"total"` appears on an empty result page too, so asserting the envelope
    would stay green while search silently returned nothing."""
    kw = {n: k for n, _, k in _collect_probes(monkeypatch)}["search_query"]
    assert kw.get("must_contain") == b'"decision_id"'


def test_the_search_probe_tolerates_real_search_latency(monkeypatch):
    """Measured against production on 2026-08-22: 2.1–3.7 s warm, 6.8 s cold.
    At the 10 s module default a cold query under load would false-alert."""
    kw = {n: k for n, _, k in _collect_probes(monkeypatch)}["search_query"]
    assert kw.get("timeout", smoke.TIMEOUT) >= 15.0


def test_only_search_gets_the_relaxed_timeout(monkeypatch):
    """A slow static read is a real fault and must keep the strict default."""
    for name, _, kw in _collect_probes(monkeypatch):
        if name != "search_query":
            assert kw.get("timeout") is None, f"{name} should use the default"


# ── freshness must tell "slow" from "dead" ───────────────────────────────────

def _marker(tmp_path: Path, age_h: float) -> Path:
    p = tmp_path / "last_publish_success.json"
    p.write_text(json.dumps({"ts": int(time.time() - age_h * 3600)}))
    return p


def test_a_running_build_is_not_a_failure(monkeypatch, tmp_path):
    """39.7 h stale with a build 10 h in — the literal state at 11:30 UTC on
    2026-08-22, which had the probe red while nothing was wrong."""
    monkeypatch.setattr(smoke, "_publish_lock_age_h", lambda path=None: 10.0)
    r = smoke._probe_publish_freshness(marker_path=_marker(tmp_path, 39.7))
    assert r.passed
    assert any("in progress" in n for n in r.notes), r.notes


def test_a_stuck_build_still_alerts(monkeypatch, tmp_path):
    """A held lock must not silence the probe forever — a hung publish is the
    failure this probe exists to catch."""
    monkeypatch.setattr(smoke, "_publish_lock_age_h", lambda path=None: 26.0)
    r = smoke._probe_publish_freshness(marker_path=_marker(tmp_path, 39.7))
    assert not r.passed
    assert any("stuck" in n for n in r.notes), r.notes


def test_stale_publish_with_no_build_running_still_alerts(monkeypatch, tmp_path):
    """The original behaviour, preserved."""
    monkeypatch.setattr(smoke, "_publish_lock_age_h", lambda path=None: None)
    r = smoke._probe_publish_freshness(marker_path=_marker(tmp_path, 39.7))
    assert not r.passed
    assert any("nightly likely failing" in n for n in r.notes), r.notes


def test_a_recent_publish_passes_regardless_of_the_lock(monkeypatch, tmp_path):
    monkeypatch.setattr(smoke, "_publish_lock_age_h", lambda path=None: 3.0)
    r = smoke._probe_publish_freshness(marker_path=_marker(tmp_path, 2.0))
    assert r.passed and not r.notes


def test_an_unseeded_marker_arms_rather_than_alerts(tmp_path):
    r = smoke._probe_publish_freshness(marker_path=tmp_path / "absent.json")
    assert r.passed


# ── the lock helper ──────────────────────────────────────────────────────────

def test_lock_age_is_none_when_no_build_is_running(tmp_path):
    assert smoke._publish_lock_age_h(tmp_path / "absent.lock") is None


def test_lock_age_is_measured_from_mtime(tmp_path):
    lock = tmp_path / "held.lock"
    lock.write_text("")
    os.utime(lock, (time.time() - 7200, time.time() - 7200))
    age = smoke._publish_lock_age_h(lock)
    assert age is not None and 1.9 < age < 2.1



# ── 2026-10-10: three days of stale data, and a 16 GB process starving the cache ──

class _Resp:
    def __init__(self, body: bytes):
        self._b = body

    def read(self, n=-1):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _health(monkeypatch, body: dict):
    monkeypatch.setattr(smoke.urllib.request, "urlopen",
                        lambda req, timeout=None: _Resp(json.dumps(body).encode()))


def test_both_new_probes_run(monkeypatch):
    names = [n for n, _, _ in _collect_probes(monkeypatch)]
    assert "served_data_age" in names and "serving_io_pressure" in names


def test_a_three_day_old_served_database_fails(monkeypatch):
    now = 1_791_700_000
    _health(monkeypatch, {"status": "ok", "db_generation": now - 60 * 3600})
    r = smoke._probe_served_data_age("https://x/health", max_age_h=36, now=now)
    assert not r.passed and "60.0h ago" in r.notes[0]


def test_a_day_old_served_database_passes(monkeypatch):
    now = 1_791_700_000
    _health(monkeypatch, {"status": "ok", "db_generation": now - 24 * 3600})
    assert smoke._probe_served_data_age("https://x/health", max_age_h=36, now=now).passed


def test_health_without_a_generation_is_not_an_alarm(monkeypatch):
    _health(monkeypatch, {"status": "ok"})
    assert smoke._probe_served_data_age("https://x/health").passed


def test_io_pressure_of_the_outage_fails_and_names_the_memory_users(tmp_path, monkeypatch):
    p = tmp_path / "io"
    p.write_text("some avg10=90.35 avg60=91.29 avg300=90.44 total=1\n"
                 "full avg10=69.07 avg60=70.22 avg300=72.97 total=1\n")
    monkeypatch.setattr(smoke, "_top_memory_users", lambda: ["ocl-model 15.9 GB", "mcp 22.9 GB"])
    r = smoke._probe_serving_pressure(p, max_full_pct=45)
    assert not r.passed and "72.97" not in r.notes[0] and "73.0%" in r.notes[0]
    assert "ocl-model 15.9 GB" in r.notes[0]


def test_io_pressure_of_a_normal_build_night_passes(tmp_path):
    p = tmp_path / "io"
    p.write_text("some avg10=17.03 avg60=17.09 avg300=15.34 total=1\n"
                 "full avg10=17.01 avg60=17.05 avg300=15.26 total=1\n")
    assert smoke._probe_serving_pressure(p, max_full_pct=45).passed


def test_no_pressure_file_is_not_an_alarm(tmp_path):
    assert smoke._probe_serving_pressure(tmp_path / "missing").passed
