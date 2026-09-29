"""BGE lists only the recent volumes daily and walks every volume once a week.

Every run used to list all ~356 volumes (1954-today), three times a day, for
new rulings that only ever appear in the current volumes; search.bger.ch began
refusing the tunnel address in the busiest window (2026-09-28/29). The late
10:20 run now also skips bge when the 09:00 federal run of it succeeded.
"""
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import scrapers.bge as bge_mod
from scrapers.bge import BGELeitentscheideScraper
from run_all_scrapers import _clean_federal_run_today

TODAY = date(2026, 9, 29)


class _FixedDate(date):
    @classmethod
    def today(cls):
        return cls(TODAY.year, TODAY.month, TODAY.day)


class _Resp:
    def __init__(self, text):
        self.text = text


def _scraper(tmp_path, monkeypatch, fail_times=None):
    monkeypatch.setattr(bge_mod, "date", _FixedDate)
    monkeypatch.delenv("OCL_SCRAPER_RESCAN_ALL", raising=False)
    s = BGELeitentscheideScraper(state_dir=tmp_path, include_egmr=False)
    s.LISTING_RETRY_PAUSE = 0
    monkeypatch.setattr(s, "_establish_session", lambda: None)
    remaining = dict(fail_times or {})
    years = []

    def fake_parse(text, year, volume):
        years.append(year)
        if remaining.get((year, volume), 0) > 0:
            remaining[(year, volume)] -= 1
            raise requests.exceptions.ConnectionError("dropped")
        return []

    monkeypatch.setattr(s, "_safe_get", lambda url: _Resp(url))
    monkeypatch.setattr(s, "_parse_volume_listing", fake_parse)
    return s, years


def _marker(tmp_path):
    return tmp_path / "bge.fullwalk"


def test_first_run_walks_everything_and_marks(tmp_path, monkeypatch):
    s, years = _scraper(tmp_path, monkeypatch)
    list(s.discover_new())
    assert min(years) == 1954 and max(years) == 2026
    assert _marker(tmp_path).read_text().strip() == "2026-09-29"


def test_daily_run_lists_only_recent_volumes(tmp_path, monkeypatch):
    _marker(tmp_path).write_text("2026-09-27\n")
    s, years = _scraper(tmp_path, monkeypatch)
    list(s.discover_new())
    assert sorted(set(years)) == [2025, 2026]
    assert len(years) == 10                      # 2 years x volumes I-V
    assert _marker(tmp_path).read_text().strip() == "2026-09-27"


def test_full_walk_due_after_seven_days(tmp_path, monkeypatch):
    _marker(tmp_path).write_text("2026-09-22\n")
    s, years = _scraper(tmp_path, monkeypatch)
    list(s.discover_new())
    assert min(years) == 1954
    assert _marker(tmp_path).read_text().strip() == "2026-09-29"


def test_unreadable_marker_means_full_walk(tmp_path, monkeypatch):
    _marker(tmp_path).write_text("garbage\n")
    s, years = _scraper(tmp_path, monkeypatch)
    list(s.discover_new())
    assert min(years) == 1954


def test_rescan_flag_forces_full_walk(tmp_path, monkeypatch):
    _marker(tmp_path).write_text("2026-09-28\n")
    s, years = _scraper(tmp_path, monkeypatch)
    monkeypatch.setenv("OCL_SCRAPER_RESCAN_ALL", "1")
    list(s.discover_new())
    assert min(years) == 1954


def test_walk_with_a_lost_listing_does_not_count(tmp_path, monkeypatch):
    s, _ = _scraper(tmp_path, monkeypatch, {(1980, "II"): 2})
    list(s.discover_new())
    assert not _marker(tmp_path).exists()        # next run walks fully again


def test_walk_with_a_recovered_listing_counts(tmp_path, monkeypatch):
    s, _ = _scraper(tmp_path, monkeypatch, {(1980, "II"): 1})
    list(s.discover_new())
    assert _marker(tmp_path).read_text().strip() == "2026-09-29"


def test_since_date_is_explicit_and_leaves_the_marker(tmp_path, monkeypatch):
    s, years = _scraper(tmp_path, monkeypatch)
    list(s.discover_new(since_date="2000-01-01"))
    assert min(years) == 2000
    assert not _marker(tmp_path).exists()


def _federal(tmp_path, run_at, entry):
    (tmp_path / "scraper_health_federal.json").write_text(json.dumps(
        {"run_at": run_at, "scrapers": {"bge": entry}}))


def test_late_run_skips_after_clean_federal_run(tmp_path):
    _federal(tmp_path, "2026-09-29T09:18:53+00:00", {"success": True, "timed_out": False})
    assert _clean_federal_run_today("bge", tmp_path, "2026-09-29") == "09:18"


def test_late_run_retries_after_failed_federal_run(tmp_path):
    _federal(tmp_path, "2026-09-29T09:37:17+00:00", {"success": False, "error": "403"})
    assert _clean_federal_run_today("bge", tmp_path, "2026-09-29") is None


def test_late_run_ignores_yesterdays_federal_run(tmp_path):
    _federal(tmp_path, "2026-09-28T09:18:53+00:00", {"success": True})
    assert _clean_federal_run_today("bge", tmp_path, "2026-09-29") is None


def test_late_run_retries_after_timeout_or_missing_file(tmp_path):
    _federal(tmp_path, "2026-09-29T09:18:53+00:00", {"success": True, "timed_out": True})
    assert _clean_federal_run_today("bge", tmp_path, "2026-09-29") is None
    assert _clean_federal_run_today("bge", tmp_path / "nowhere", "2026-09-29") is None
