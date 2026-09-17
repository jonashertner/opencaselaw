"""coverage.db lock hygiene in run_scraper (2026-09-15, extended 2026-09-16 and 2026-09-17).

coverage.db is shared by ~12 concurrent scrapers. The event writer used to commit only
every 200 events, so a slow fetch loop held the write lock for minutes and 10-20 courts a
night logged "Coverage snapshot update failed: database is locked". Bounding a transaction
by age (2 s) and retrying the snapshot write left 5 failures a night, WAL mode left 2: the
age was only evaluated when the *next* event arrived, so a scraper that logged one event
and then sat in a slow fetch kept its transaction, and with it the single WAL writer lock,
open for minutes. Every event is now committed on its own, which synchronous=NORMAL makes
an append without an fsync.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import run_scraper  # noqa: E402


@pytest.fixture
def isolated_coverage_db(tmp_path, monkeypatch):
    db = tmp_path / "coverage.db"
    monkeypatch.setenv("SWISS_CASELAW_COVERAGE_DB", str(db))
    return db


def _rows(db: Path, table: str = "source_discoveries") -> int:
    conn = sqlite3.connect(str(db))
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


def _writer(tmp_path):
    w = run_scraper._RunEventWriter(output_dir=tmp_path, source_key="t_court", run_id="r1")
    assert w._enabled
    return w


def test_every_discovery_is_committed_on_its_own(isolated_coverage_db, tmp_path):
    w = _writer(tmp_path)
    for i in range(1, 4):
        w.log_discovery({"decision_id": f"t_court_{i}", "docket_number": str(i)})
        assert _rows(isolated_coverage_db) == i      # a second connection sees it at once
        assert not w._conn.in_transaction            # and nothing is left open behind it
    w.close()


def test_every_fetch_attempt_is_committed_on_its_own(isolated_coverage_db, tmp_path):
    w = _writer(tmp_path)
    stub = {"decision_id": "t_court_2026_1", "docket_number": "1", "decision_date": "2026-01-05"}
    w.log_fetch_attempt(stub=stub, status="error", error_type="HTTPError", error_message="500")
    assert _rows(isolated_coverage_db, "source_fetch_attempts") == 1
    assert not w._conn.in_transaction
    w.log_fetch_attempt(stub=stub, status="success")
    assert _rows(isolated_coverage_db, "source_fetch_attempts") == 2
    assert not w._conn.in_transaction
    w.close()


def test_writer_holds_no_lock_between_events(isolated_coverage_db, tmp_path):
    """The 2026-09-17 failure mode: a scraper sitting between two events (a slow fetch, a
    long discovery request) must not keep other scrapers from writing their snapshot."""
    w = _writer(tmp_path)
    w.log_discovery({"decision_id": "t_court_1", "docket_number": "1"})
    other = sqlite3.connect(str(isolated_coverage_db), timeout=0.2)
    try:
        other.execute("BEGIN IMMEDIATE")             # "database is locked" if w still held it
        other.rollback()
    finally:
        other.close()
    w.close()


def test_close_after_committed_events_is_quiet(isolated_coverage_db, tmp_path):
    w = _writer(tmp_path)
    w.log_discovery({"decision_id": "t_court_1", "docket_number": "1"})
    w.close()
    w.close()                                        # idempotent
    assert _rows(isolated_coverage_db) == 1


def test_coverage_db_is_opened_in_wal_mode(isolated_coverage_db, tmp_path):
    """A rollback journal lets one writer block every reader; WAL is what keeps ~12 parallel
    scrapers from losing their snapshot (5 still failed on 2026-09-16 with retries alone)."""
    w = run_scraper._RunEventWriter(output_dir=tmp_path, source_key="t_court", run_id="r1")
    w.log_discovery({"decision_id": "t_court_1", "docket_number": "1"})
    w.close()
    conn = sqlite3.connect(str(isolated_coverage_db))
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    finally:
        conn.close()


def test_coverage_db_commits_without_an_fsync(tmp_path):
    """synchronous=NORMAL (1) is what makes a commit per event affordable in WAL mode."""
    conn = run_scraper._open_coverage_db(tmp_path / "coverage.db")
    try:
        assert conn.execute("PRAGMA synchronous").fetchone()[0] == 1
    finally:
        conn.close()


def test_snapshot_retry_schedule_has_four_attempts():
    assert run_scraper._SNAPSHOT_LOCK_RETRIES == (20, 40, 80)


def test_snapshot_write_retries_while_locked(monkeypatch, tmp_path):
    calls = []
    sleeps = []

    def _once(**kw):
        calls.append(kw["scraper_key"])
        if len(calls) <= len(run_scraper._SNAPSHOT_LOCK_RETRIES):
            raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(run_scraper, "_record_coverage_snapshots_once", _once)
    monkeypatch.setattr(run_scraper.time, "sleep", sleeps.append)
    run_scraper._record_coverage_snapshots(
        scraper_key="t_court", output_dir=tmp_path, ids_by_year={2026: {"a"}}, changed_years={2026}
    )
    assert calls == ["t_court"] * (len(run_scraper._SNAPSHOT_LOCK_RETRIES) + 1)
    assert sleeps == list(run_scraper._SNAPSHOT_LOCK_RETRIES)


def test_snapshot_write_gives_up_after_the_last_retry(monkeypatch, tmp_path):
    def _once(**kw):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(run_scraper, "_record_coverage_snapshots_once", _once)
    monkeypatch.setattr(run_scraper.time, "sleep", lambda s: None)
    with pytest.raises(sqlite3.OperationalError):
        run_scraper._record_coverage_snapshots(
            scraper_key="t_court", output_dir=tmp_path, ids_by_year={2026: {"a"}}, changed_years={2026}
        )


def test_other_operational_errors_are_not_retried(monkeypatch, tmp_path):
    calls = []

    def _once(**kw):
        calls.append(1)
        raise sqlite3.OperationalError("no such table: source_snapshots")

    monkeypatch.setattr(run_scraper, "_record_coverage_snapshots_once", _once)
    with pytest.raises(sqlite3.OperationalError):
        run_scraper._record_coverage_snapshots(
            scraper_key="t_court", output_dir=tmp_path, ids_by_year={2026: {"a"}}, changed_years={2026}
        )
    assert calls == [1]
