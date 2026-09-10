"""scripts/warm_structure_sidecar.py: index-only, budgeted, never fails its caller."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

import warm_structure_sidecar as ws  # noqa: E402


def _sidecar(tmp_path, n=300):
    db = tmp_path / "decision_structure.db"
    c = sqlite3.connect(db)
    c.executescript("""
        CREATE TABLE structure (decision_id TEXT PRIMARY KEY, court TEXT, language TEXT,
            sachverhalt TEXT, erwaegungen TEXT, dispositiv TEXT);
        CREATE INDEX idx_court ON structure(court);
        CREATE TABLE erwaegungen_paragraph (decision_id TEXT, e_number TEXT, depth INTEGER,
            parent TEXT, text TEXT, PRIMARY KEY (decision_id, e_number));
        CREATE INDEX idx_erw_decision ON erwaegungen_paragraph(decision_id);
    """)
    c.executemany("INSERT INTO structure VALUES (?, 'bger', 'de', 'S', ?, 'D')",
                  [(f"bger_{i}", "E " * 500) for i in range(n)])
    c.executemany("INSERT INTO erwaegungen_paragraph VALUES (?, ?, 1, NULL, 'text')",
                  [(f"bger_{i}", str(k)) for i in range(n) for k in range(3)])
    c.commit(); c.close()
    return db


def test_warm_runs_all_three_index_only(tmp_path):
    db = _sidecar(tmp_path)
    res = ws.warm(db, budget_s=60)
    assert [r[0] for r in res["ran"]] == [q[0] for q in ws.WARM_QUERIES]
    assert res["ran"][0][1] == 300 and res["ran"][1][1] == 300 and res["ran"][2][1] == 300
    assert res["skipped"] == []


def test_symlink_is_resolved_and_missing_is_a_skip(tmp_path):
    db = _sidecar(tmp_path)
    link = tmp_path / "link.db"
    link.symlink_to(db)
    res = ws.warm(link, budget_s=60)
    assert res["path"] == str(db.resolve()) and len(res["ran"]) == 3
    res = ws.warm(tmp_path / "absent.db", budget_s=60)
    assert res["ran"] == [] and res["skipped"] == [("all", "sidecar missing")]


def test_budget_zero_and_exhaustion(tmp_path, monkeypatch):
    db = _sidecar(tmp_path)
    assert ws.warm(db, budget_s=0)["skipped"] == [("all", "budget 0")]
    # deadline already passed when the first query starts → hard stop, then exhausted
    monkeypatch.setattr(ws, "_PROGRESS_EVERY_OPCODES", 1)
    base = ws.time.monotonic()
    ticks = iter([base, base, base + 10, base + 10, base + 10, base + 10, base + 10, base + 10])
    monkeypatch.setattr(ws.time, "monotonic", lambda: next(ticks, base + 10))
    res = ws.warm(db, budget_s=5)
    assert res["ran"] == []
    reasons = {r[1] for r in res["skipped"]}
    assert reasons <= {"hard stop at budget", "budget exhausted"} and res["skipped"]


def test_non_index_plan_is_skipped(tmp_path):
    db = _sidecar(tmp_path)
    scan = (("full scan", "SELECT count(*) FROM structure WHERE erwaegungen LIKE '%x%'", "INDEX"),)
    res = ws.warm(db, budget_s=60, queries=scan)
    assert res["ran"] == [] and res["skipped"][0][0] == "full scan"
    assert "not index-only" in res["skipped"][0][1]


def test_main_never_fails_caller_unless_strict(tmp_path, capsys):
    assert ws.main(["--structure-db", str(tmp_path / "absent.db")]) == 0
    assert ws.main(["--structure-db", str(tmp_path / "absent.db"), "--strict"]) == 1
    db = _sidecar(tmp_path)
    assert ws.main(["--structure-db", str(db), "--budget-s", "60", "--strict"]) == 0
