"""coverage.db lock hygiene in run_scraper (2026-09-15, extended 2026-09-16).

coverage.db is shared by ~12 concurrent scrapers. The event writer used to commit only
every 200 events, so a slow fetch loop held the write lock for minutes and 10-20 courts a
night logged "Coverage snapshot update failed: database is locked". A transaction is now
committed by count OR after MAX_TXN_AGE_S, and the snapshot writer retries while the
database is locked. On the first night with those bounded transactions 5 courts still gave
up, because a rollback journal has one writer at a time and no fairness, so the database is
opened in WAL mode and the retry schedule has one more step.
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


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def isolated_coverage_db(tmp_path, monkeypatch):
    db = tmp_path / "coverage.db"
    monkeypatch.setenv("SWISS_CASELAW_COVERAGE_DB", str(db))
    return db


def _rows(db: Path) -> int:
    conn = sqlite3.connect(str(db))
    try:
        return conn.execute("SELECT COUNT(*) FROM source_discoveries").fetchone()[0]
    finally:
        conn.close()


def test_event_transaction_is_committed_by_age(isolated_coverage_db, tmp_path, monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(run_scraper.time, "monotonic", clock)
    w = run_scraper._RunEventWriter(output_dir=tmp_path, source_key="t_court", run_id="r1")
    assert w._enabled

    w.log_discovery({"decision_id": "t_court_1", "docket_number": "1"})
    assert w._pending == 1                      # under the count threshold: still open
    assert _rows(isolated_coverage_db) == 0     # a second connection cannot see it yet

    clock.now += run_scraper._RunEventWriter.MAX_TXN_AGE_S + 0.1
    w.log_discovery({"decision_id": "t_court_2", "docket_number": "2"})
    assert w._pending == 0                      # age threshold committed the batch
    assert _rows(isolated_coverage_db) == 2
    w.close()


def test_event_transaction_is_still_committed_by_count(isolated_coverage_db, tmp_path, monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(run_scraper.time, "monotonic", clock)  # frozen clock: only the count rule
    w = run_scraper._RunEventWriter(output_dir=tmp_path, source_key="t_court", run_id="r1")
    for i in range(199):
        w.log_discovery({"decision_id": f"t_court_{i}", "docket_number": str(i)})
    assert w._pending == 199
    w.log_discovery({"decision_id": "t_court_199", "docket_number": "199"})
    assert w._pending == 0
    assert _rows(isolated_coverage_db) == 200
    w.close()


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
