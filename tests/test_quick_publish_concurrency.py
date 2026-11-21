"""Concurrency safety for scripts/quick_publish.py (the fix for the ECtHR daily
failure: two quick_publish runs sharing the single decisions.db.quick working
copy corrupted it -> 'database disk image is malformed').

Offline: a throwaway DB in tmp_path; the live 70 GB DB is never touched. Proves
the dedicated flock mutex serializes concurrent runs, skips on a held lock, and
releases so the next run proceeds.
"""
from __future__ import annotations

import sqlite3
import sys
import threading
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import db_schema  # noqa: E402
import scripts.quick_publish as qp  # noqa: E402


def _make_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript(db_schema.SCHEMA_SQL)
    conn.execute(
        "INSERT INTO decisions(decision_id, court, canton, docket_number, "
        "language, title, regeste, full_text) VALUES "
        "('seed_1','ecthr','CH','1/20','de','t','','body')"
    )
    conn.commit()
    conn.close()


@pytest.fixture
def wired(tmp_path, monkeypatch):
    """Point quick_publish at a throwaway DB + JSONL dir + private lock paths."""
    db = tmp_path / "decisions.db"
    jsonl = tmp_path / "decisions"
    jsonl.mkdir()
    _make_db(db)
    monkeypatch.setattr(qp, "DB_PATH", db)
    monkeypatch.setattr(qp, "JSONL_DIR", jsonl)
    monkeypatch.setattr(qp, "TMP_PATH", Path(str(db) + ".quick"))
    monkeypatch.setattr(qp, "QUICK_PUBLISH_LOCK_PATH", str(tmp_path / "quick.lock"))
    monkeypatch.setattr(qp, "PUBLISH_LOCK_PATH", str(tmp_path / "publish.lock"))
    # A swap restarts the workers (tests/test_quick_publish_recycle.py); never here.
    monkeypatch.setattr(qp, "_recycle_workers_after_swap", lambda: None)
    return tmp_path, db, jsonl


def test_concurrent_runs_serialize_and_never_corrupt(wired, monkeypatch):
    tmp_path, db, jsonl = wired
    # A JSONL row that already exists -> each run copies + reads, finds nothing
    # new, cleans up. This exercises the exact corruption point (concurrent copy
    # to the shared .quick + SELECT on the copy) without needing the insert path.
    (jsonl / "ecthr.jsonl").write_text(
        '{"decision_id":"seed_1","court":"ecthr"}\n', encoding="utf-8")

    in_copy = 0
    max_in_copy = 0
    guard = threading.Lock()
    real_copy2 = qp.shutil.copy2

    def slow_copy2(src, dst, *a, **k):
        nonlocal in_copy, max_in_copy
        with guard:
            in_copy += 1
            max_in_copy = max(max_in_copy, in_copy)
        try:
            time.sleep(0.4)          # widen the window so any overlap would show
            return real_copy2(src, dst, *a, **k)
        finally:
            with guard:
                in_copy -= 1

    monkeypatch.setattr(qp.shutil, "copy2", slow_copy2)

    errors: list[Exception] = []

    def run():
        try:
            qp.quick_publish(courts=["ecthr"], dry_run=False)
        except Exception as e:  # a malformed copy would raise here
            errors.append(e)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, f"quick_publish raised under concurrency: {errors}"
    # The mutex must have serialized the critical section: never 2 copies at once.
    assert max_in_copy == 1, f"copies overlapped (max concurrent = {max_in_copy})"
    # No orphaned working copy left behind.
    assert not Path(str(db) + ".quick").exists()
    # Source DB still intact.
    c = sqlite3.connect(str(db))
    assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    c.close()


def test_skips_when_mutex_already_held(wired, monkeypatch):
    tmp_path, db, jsonl = wired
    (jsonl / "ecthr.jsonl").write_text(
        '{"decision_id":"seed_1","court":"ecthr"}\n', encoding="utf-8")
    monkeypatch.setattr(qp, "QUICK_PUBLISH_LOCK_TIMEOUT_S", 1)  # fail fast

    # Hold the mutex from "another process".
    holder = open(qp.QUICK_PUBLISH_LOCK_PATH, "a+")
    qp.fcntl.flock(holder.fileno(), qp.fcntl.LOCK_EX)
    try:
        result = qp.quick_publish(courts=["ecthr"], dry_run=False)
    finally:
        qp.fcntl.flock(holder.fileno(), qp.fcntl.LOCK_UN)
        holder.close()

    assert result is None  # deliberately skipped, not crashed


def test_lock_released_after_run(wired):
    tmp_path, db, jsonl = wired
    (jsonl / "ecthr.jsonl").write_text(
        '{"decision_id":"seed_1","court":"ecthr"}\n', encoding="utf-8")
    # Two sequential runs must both complete (0 inserted); the second only works
    # if the first released the mutex.
    assert qp.quick_publish(courts=["ecthr"], dry_run=False) == 0
    assert qp.quick_publish(courts=["ecthr"], dry_run=False) == 0
    assert not Path(str(db) + ".quick").exists()


def test_dry_run_does_not_delete_a_concurrent_runs_quick_file(wired):
    tmp_path, db, jsonl = wired
    (jsonl / "ecthr.jsonl").write_text(
        '{"decision_id":"seed_1","court":"ecthr"}\n', encoding="utf-8")
    # Simulate a REAL quick_publish mid-copy: its working .quick exists.
    other = Path(str(db) + ".quick")
    other.write_bytes(b"in-flight working copy of a concurrent real run")
    # A dry-run (no mutex, read-only) must NOT touch it.
    assert qp.quick_publish(courts=["ecthr"], dry_run=True) == 0
    assert other.exists(), "dry-run deleted a concurrent run's .quick working file"
    assert other.read_bytes() == b"in-flight working copy of a concurrent real run"


def test_full_publish_probe_still_skips(wired, monkeypatch):
    tmp_path, db, jsonl = wired
    (jsonl / "ecthr.jsonl").write_text(
        '{"decision_id":"seed_1","court":"ecthr"}\n', encoding="utf-8")
    # Simulate publish.py holding its exclusive lock -> quick_publish must skip
    # even after acquiring its own mutex.
    monkeypatch.setattr(qp, "_publish_in_progress", lambda: True)
    assert qp.quick_publish(courts=["ecthr"], dry_run=False) is None
    assert not Path(str(db) + ".quick").exists()
