"""run_with_persistence honours CACHE_NONE_AS_GAP (2026-09-14).

BaseScraper.run cached None returns as gaps for opted-in scrapers, but production runs
through run_scraper.run_with_persistence, which never did — bge_historical re-probed the
same 161 dead ids every night, hudoc_ch 71, elcom 8. A None from an opted-in scraper is
now a gap for GAP_TTL_DAYS; scrapers without the flag keep retrying nightly.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import run_scraper  # noqa: E402
from base_scraper import ScraperState  # noqa: E402


def _run(monkeypatch, tmp_path, key, cls):
    monkeypatch.setitem(run_scraper.SCRAPERS, key, ("tests._fake_gap_scrapers", cls))
    out, state = tmp_path / "out", tmp_path / "state"
    rc = run_scraper.run_with_persistence(key, output_dir=out, state_dir=state,
                                          auto_coverage_snapshot=False)
    assert rc == 0
    rows = (out / "decisions" / f"{key}.jsonl").read_text().strip().splitlines()
    return rows, ScraperState(state / f"{key}.jsonl")


def test_opted_in_scraper_caches_none_as_gap(monkeypatch, tmp_path):
    rows, st = _run(monkeypatch, tmp_path, "fakegap", "GapCachingScraper")
    assert len(rows) == 1                                   # A-1 written
    assert st.is_known("fakegap_A-1")                       # scraped
    assert st.is_known("fakegap_A-2")                       # gap-cached, not re-probed
    assert "fakegap_A-2" in (tmp_path / "state" / "fakegap.gaps.jsonl").read_text()


def test_plain_scraper_keeps_retrying(monkeypatch, tmp_path):
    rows, st = _run(monkeypatch, tmp_path, "fakeplain", "PlainScraper")
    assert len(rows) == 1
    assert st.is_known("fakeplain_A-1")
    assert not st.is_known("fakeplain_A-2")
    assert not (tmp_path / "state" / "fakeplain.gaps.jsonl").exists()
