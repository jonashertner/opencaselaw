"""The weekly erwaegungen_paragraphs.parquet export runs after the build,
bounded by a deadline instead of the probe's projection (2026-11-08: the
in-build probe projected 252 min and skipped; the real run took 48 min)."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

pytest.importorskip("pyarrow.parquet")
import export_parquet as ep  # noqa: E402
from export_parquet import export_decision_structure  # noqa: E402
from scripts import export_paragraphs_weekly as w  # noqa: E402

UTC = timezone.utc
BUILD = (3, 30)
SUN_2240 = datetime(2026, 11, 8, 22, 40, tzinfo=UTC)     # a Sunday


@pytest.fixture(autouse=True)
def _clean_budget_env(monkeypatch):
    for var in ("OCL_STRUCTURE_EXPORT_BUDGET_S", "OCL_STRUCTURE_PARAGRAPHS_BUDGET_S",
                "OCL_EXPORT_WALLCLOCK_BUDGET_S"):
        monkeypatch.delenv(var, raising=False)


def _sidecar(tmp_path, n_rows=40):
    db = tmp_path / "decision_structure.db"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE structure (decision_id TEXT PRIMARY KEY, court TEXT, language TEXT,
            sachverhalt TEXT, sachverhalt_method TEXT, erwaegungen TEXT,
            erwaegungen_method TEXT, erwaegungen_paragraph_count INTEGER,
            dispositiv TEXT, dispositiv_method TEXT);
        CREATE TABLE erwaegungen_paragraph (decision_id TEXT, e_number TEXT,
            depth INTEGER, parent TEXT, text TEXT);
    """)
    conn.executemany(
        "INSERT INTO structure VALUES (?, 'bger', 'de', 'A.', 'anchor', 'E.', 'anchor', 1, "
        "'D.', 'anchor')", [(f"bger_{i}",) for i in range(n_rows)])
    conn.executemany(
        "INSERT INTO erwaegungen_paragraph VALUES (?, '1', 1, NULL, 'Die Beschwerde...')",
        [(f"bger_{i}",) for i in range(n_rows)])
    conn.commit(); conn.close()
    return db


# --- export_parquet: probe as log only, status merge ----------------------

def test_probe_gates_skips_but_log_only_mode_writes(tmp_path, monkeypatch):
    db = _sidecar(tmp_path)
    monkeypatch.setattr(ep, "_projected_seconds", lambda *a, **k: 10_000.0)
    out = tmp_path / "o"
    gated = export_decision_structure(db, out, include_paragraphs=True, include_meta=False,
                                      paragraphs_budget_s=60)
    assert gated["erwaegungen_paragraphs_skipped"].startswith("projected 166.7 min > budget")
    assert not (out / "structure" / "erwaegungen_paragraphs.parquet").exists()

    free = export_decision_structure(db, out, include_paragraphs=True, include_meta=False,
                                     paragraphs_budget_s=60, paragraphs_probe_gates=False)
    assert free == {"erwaegungen_paragraphs": 40}
    assert (out / "structure" / "erwaegungen_paragraphs.parquet").exists()
    assert not (out / "structure" / "structure.parquet").exists()   # meta not run


def test_log_only_mode_ignores_an_inconclusive_probe(tmp_path, monkeypatch):
    db = _sidecar(tmp_path)
    monkeypatch.setattr(ep, "_projected_seconds", lambda *a, **k: float("inf"))
    counts = export_decision_structure(db, tmp_path / "o", include_paragraphs=True,
                                       include_meta=False, paragraphs_probe_gates=False)
    assert counts == {"erwaegungen_paragraphs": 40}


def test_log_only_mode_keeps_the_hard_stop(tmp_path, monkeypatch):
    db = _sidecar(tmp_path)
    monkeypatch.setattr(ep, "_PROGRESS_EVERY_OPCODES", 1)
    monkeypatch.setattr(ep, "_HARD_STOP_FACTOR", -1.0)   # deadline already past
    counts = export_decision_structure(db, tmp_path / "o", include_paragraphs=True,
                                       include_meta=False, paragraphs_probe_gates=False)
    assert counts["erwaegungen_paragraphs_skipped"].startswith("hard stop after")
    assert not list((tmp_path / "o" / "structure").glob("*.tmp"))


def test_status_keeps_entries_of_exports_not_run(tmp_path, monkeypatch):
    db = _sidecar(tmp_path)
    out = tmp_path / "o"
    status_path = out / "structure" / "export_status.json"
    # nightly step 3: meta only
    assert export_decision_structure(db, out) == {"structure": 40}
    # weekly job: paragraphs only; the nightly entry survives
    export_decision_structure(db, out, include_paragraphs=True, include_meta=False,
                              paragraphs_probe_gates=False)
    st = json.loads(status_path.read_text())
    assert st["counts"] == {"structure": 40, "erwaegungen_paragraphs": 40}
    assert set(st["ran_at"]) == {"structure", "erwaegungen_paragraphs"}
    # next night's meta-only run skips: its old count goes, the weekly entry stays
    monkeypatch.setattr(ep, "_HARD_STOP_FACTOR", -1.0)
    monkeypatch.setattr(ep, "_PROGRESS_EVERY_OPCODES", 1)
    export_decision_structure(db, out)
    st = json.loads(status_path.read_text())
    assert "structure" not in st["counts"]
    assert st["counts"]["structure_skipped"].startswith("hard stop")
    assert st["counts"]["erwaegungen_paragraphs"] == 40


# --- the weekly job: when it is due, its deadline, its exit codes ---------

def test_deadline_is_margin_before_next_build():
    m = timedelta(minutes=30)
    assert w.deadline_for(SUN_2240, BUILD, m) == datetime(2026, 11, 9, 3, 0, tzinfo=UTC)
    # just after midnight: still the same night's deadline
    assert w.deadline_for(datetime(2026, 11, 9, 0, 30, tzinfo=UTC), BUILD, m) == \
        datetime(2026, 11, 9, 3, 0, tzinfo=UTC)
    # between the deadline and the build start: already past
    late = datetime(2026, 11, 9, 3, 10, tzinfo=UTC)
    assert w.deadline_for(late, BUILD, m) < late


def test_due_on_the_sunday_build_even_after_midnight():
    week = timedelta(days=7)
    six = timedelta(days=6, hours=23)
    assert w.due_reason(SUN_2240, BUILD, six, week + timedelta(hours=12)) == "Sunday build"
    # the Sunday build that exits at 00:40 Monday is still the Sunday build
    mon_0040 = datetime(2026, 11, 9, 0, 40, tzinfo=UTC)
    assert w.due_reason(mon_0040, BUILD, six, week) == "Sunday build"


def test_not_due_midweek_but_catches_up_a_missed_sunday():
    max_age = timedelta(days=7.5)
    sat_2000 = datetime(2026, 11, 14, 20, 0, tzinfo=UTC)
    # Saturday: last Sunday's file is 6.9 days old -> not due
    assert w.due_reason(sat_2000, BUILD, timedelta(days=6.9), max_age) is None
    # Monday after a failed Sunday: 7.9 days -> catch-up
    mon_2000 = datetime(2026, 11, 16, 20, 0, tzinfo=UTC)
    assert "catch-up" in w.due_reason(mon_2000, BUILD, timedelta(days=7.9), max_age)
    assert w.due_reason(sat_2000, BUILD, None, max_age) == "no paragraphs file yet"


def _args(tmp_path, *extra):
    return w.parse_args(["--dataset-dir", str(tmp_path / "ds"),
                         "--structure-db", str(tmp_path / "decision_structure.db"), *extra])


def test_refuses_while_publish_is_running(tmp_path):
    called = []
    rc = w.run(_args(tmp_path, "--force"), now=SUN_2240, state_fn=lambda: "activating",
               export_fn=lambda *a, **k: called.append(1) or {})
    assert rc == w.EXIT_REFUSED and not called


def test_not_due_exits_zero_without_exporting(tmp_path):
    target = tmp_path / "ds" / "structure" / w.PARAGRAPHS_FILE
    target.parent.mkdir(parents=True)
    target.write_bytes(b"x")
    wed = datetime(2026, 11, 11, 20, 0, tzinfo=UTC)
    os.utime(target, (wed.timestamp() - 3 * 86400,) * 2)
    called = []
    rc = w.run(_args(tmp_path), now=wed, state_fn=lambda: "inactive",
               export_fn=lambda *a, **k: called.append(1) or {})
    assert rc == w.EXIT_OK and not called


def test_too_little_time_left_fails_loudly(tmp_path):
    called = []
    rc = w.run(_args(tmp_path), now=datetime(2026, 11, 9, 2, 50, tzinfo=UTC),
               state_fn=lambda: "inactive", export_fn=lambda *a, **k: called.append(1) or {})
    assert rc == w.EXIT_NOT_WRITTEN and not called


def test_passes_the_deadline_as_budget_and_hard_stop(tmp_path):
    seen = {}

    def fake(db, ds, **kw):
        seen.update(kw)
        return {"erwaegungen_paragraphs": 7}

    rc = w.run(_args(tmp_path), now=SUN_2240, state_fn=lambda: "inactive", export_fn=fake)
    assert rc == w.EXIT_OK
    assert seen["include_meta"] is False and seen["include_paragraphs"] is True
    assert seen["paragraphs_probe_gates"] is False
    assert seen["paragraphs_budget_s"] == pytest.approx(260 * 60)   # 22:40 -> 03:00
    assert seen["deadline_monotonic"] > 0


def test_skip_or_stop_fails_the_unit(tmp_path):
    rc = w.run(_args(tmp_path), now=SUN_2240, state_fn=lambda: "inactive",
               export_fn=lambda *a, **k: {"erwaegungen_paragraphs_skipped": "hard stop after 260 min"})
    assert rc == w.EXIT_NOT_WRITTEN
    # no sidecar at all: export returns {} -> also a failure
    assert w.run(_args(tmp_path), now=SUN_2240, state_fn=lambda: None,
                 export_fn=lambda *a, **k: {}) == w.EXIT_NOT_WRITTEN


def test_end_to_end_writes_the_file(tmp_path):
    _sidecar(tmp_path)
    rc = w.run(_args(tmp_path), now=SUN_2240, state_fn=lambda: "inactive")
    assert rc == w.EXIT_OK
    pq = pytest.importorskip("pyarrow.parquet")
    t = pq.read_table(tmp_path / "ds" / "structure" / w.PARAGRAPHS_FILE)
    assert t.num_rows == 40
    assert not (tmp_path / "ds" / "structure" / "structure.parquet").exists()


# --- publish step 3 no longer passes --structure-paragraphs by default ----

@pytest.mark.parametrize("env, expect", [(None, False), ("1", True)])
def test_step3_leaves_paragraphs_to_the_weekly_unit(monkeypatch, env, expect):
    import publish

    class _Sunday(datetime):
        @classmethod
        def now(cls, tz=None):
            return SUN_2240

    seen = {}
    monkeypatch.setattr(publish, "datetime", _Sunday)
    monkeypatch.setattr(publish, "run_cmd",
                        lambda cmd, *a, **k: seen.setdefault("cmd", cmd) and True)
    if env is None:
        monkeypatch.delenv("OCL_PARAGRAPHS_IN_BUILD", raising=False)
    else:
        monkeypatch.setenv("OCL_PARAGRAPHS_IN_BUILD", env)
    assert publish.step_3_export_parquet()
    assert ("--structure-paragraphs" in seen["cmd"]) is expect
