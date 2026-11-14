"""quick_publish restarts the workers after a swap (Step B prerequisite).

Pooled worker connections keep the swapped-out decisions.db (~73 GB) allocated
until the process restarts. publish.py recycles the workers after its Step 2
swap; quick_publish must do the same once the incremental build swaps every
weekday night, or the structure rebuild that follows finds too little free
space. Offline: a throwaway DB; subprocess.run is stubbed, so no systemctl or
bash runs.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import db_schema  # noqa: E402
import scripts.quick_publish as qp  # noqa: E402


@pytest.fixture
def wired(tmp_path, monkeypatch):
    db = tmp_path / "decisions.db"
    jsonl = tmp_path / "decisions"
    jsonl.mkdir()
    conn = sqlite3.connect(str(db))
    conn.executescript(db_schema.SCHEMA_SQL)
    conn.execute(
        "INSERT INTO decisions(decision_id, court, canton, docket_number, "
        "language, title, regeste, full_text) VALUES "
        "('seed_1','ecthr','CH','1/20','de','t','','body')"
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(qp, "DB_PATH", db)
    monkeypatch.setattr(qp, "JSONL_DIR", jsonl)
    monkeypatch.setattr(qp, "TMP_PATH", Path(str(db) + ".quick"))
    monkeypatch.setattr(qp, "QUICK_PUBLISH_LOCK_PATH", str(tmp_path / "quick.lock"))
    monkeypatch.setattr(qp, "PUBLISH_LOCK_PATH", str(tmp_path / "publish.lock"))
    monkeypatch.delenv("OCL_QUICK_PUBLISH_RECYCLE", raising=False)
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="restarting mcp-server@8770.service ...\n", stderr="")

    monkeypatch.setattr(qp.subprocess, "run", fake_run)
    return db, jsonl, calls


def _new_row(did: str) -> str:
    return json.dumps({
        "decision_id": did, "court": "ecthr", "canton": "CH", "docket_number": did,
        "language": "en", "title": "t", "decision_date": "2020-01-01",
        "full_text": "The Court holds unanimously. " * 20,
    }) + "\n"


def test_a_swap_restarts_the_workers_once(wired):
    db, jsonl, calls = wired
    (jsonl / "ecthr.jsonl").write_text(_new_row("ecthr_new_1"), encoding="utf-8")
    assert qp.quick_publish(courts=["ecthr"], dry_run=False) == 1
    assert calls == [["bash", str(qp.ROLLING_RESTART_SCRIPT)]]


def test_nothing_new_means_no_swap_and_no_restart(wired):
    db, jsonl, calls = wired
    (jsonl / "ecthr.jsonl").write_text('{"decision_id":"seed_1","court":"ecthr"}\n', encoding="utf-8")
    assert qp.quick_publish(courts=["ecthr"], dry_run=False) == 0
    assert calls == []


def test_dry_run_never_restarts(wired):
    db, jsonl, calls = wired
    (jsonl / "ecthr.jsonl").write_text(_new_row("ecthr_new_2"), encoding="utf-8")
    assert qp.quick_publish(courts=["ecthr"], dry_run=True) == 1
    assert calls == []


def test_the_switch_turns_it_off(wired, monkeypatch):
    db, jsonl, calls = wired
    monkeypatch.setenv("OCL_QUICK_PUBLISH_RECYCLE", "0")
    (jsonl / "ecthr.jsonl").write_text(_new_row("ecthr_new_3"), encoding="utf-8")
    assert qp.quick_publish(courts=["ecthr"], dry_run=False) == 1
    assert calls == []


def test_a_failed_restart_leaves_the_swap_in_place(wired, monkeypatch):
    db, jsonl, calls = wired

    def boom(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, qp.RECYCLE_TIMEOUT_S)

    monkeypatch.setattr(qp.subprocess, "run", boom)
    (jsonl / "ecthr.jsonl").write_text(_new_row("ecthr_new_4"), encoding="utf-8")
    assert qp.quick_publish(courts=["ecthr"], dry_run=False) == 1
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        assert conn.execute(
            "SELECT count(*) FROM decisions WHERE decision_id = 'ecthr_new_4'").fetchone()[0] == 1
    finally:
        conn.close()
    assert not Path(str(db) + ".quick").exists()


def test_the_restart_script_is_the_health_gated_one():
    text = qp.ROLLING_RESTART_SCRIPT.read_text(encoding="utf-8")
    assert "systemctl restart" in text and "/health" in text
