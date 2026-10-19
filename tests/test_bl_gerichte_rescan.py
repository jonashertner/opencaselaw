"""BL (bl.swisslex.ch): whole-corpus rescan and format-independent docket identity (offline).

2026-09-15: the daily run searches a 730-day window and stops after 300 consecutive known
hits, so decisions the portal lists with an older date are never reached. A read-only
whole-corpus walk found 12 unknown ids: 9 missing from the served database (2011-2024)
and 3 held decisions listed under another docket form ("470 24 21" vs "470 2024 21").
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scrapers.cantonal.bl_gerichte import BLGerichteScraper, _docket_key  # noqa: E402


def _hit(docket: str, n: int) -> dict:
    return {"targetID": f"t{n}", "caseLawNumbers": docket, "date": "2024-04-02T00:00:00",
            "courtDescription": "Kantonsgericht", "title": "", "description": ""}


def _scraper(tmp_path, monkeypatch, pages, held=()):
    s = BLGerichteScraper(state_dir=tmp_path)
    for did in held:
        s.state.mark_scraped(did)
    calls = []

    def fake_search(page, date_from=None):
        calls.append((page, date_from))
        hits = pages[page - 1] if page <= len(pages) else []
        return {"numberOfDocuments": sum(len(p) for p in pages), "transactionId": "tx", "hits": hits}

    monkeypatch.setattr(s, "_search_page", fake_search)
    return s, calls


def _pages_with_a_late_unknown():
    known = [f"470 2020 {i}" for i in range(1, 301)]
    pages = [[_hit(d, i) for i, d in enumerate(known[k:k + 100], start=k)] for k in (0, 100, 200)]
    pages.append([_hit("350 2012 269", 999)])
    return pages, [f"bl_gerichte_{d}" for d in known]


def test_daily_run_stops_early_and_never_reaches_the_late_unknown(tmp_path, monkeypatch):
    monkeypatch.delenv("OCL_SCRAPER_RESCAN_ALL", raising=False)
    pages, held = _pages_with_a_late_unknown()
    s, calls = _scraper(tmp_path, monkeypatch, pages, held)
    assert list(s.discover_new()) == []
    assert calls[0][1] is not None                        # dated 730-day window
    assert [c[0] for c in calls] == [1, 2, 3]             # page 4 never requested


def test_rescan_all_walks_the_whole_corpus_without_early_stop(tmp_path, monkeypatch):
    monkeypatch.setenv("OCL_SCRAPER_RESCAN_ALL", "1")
    pages, held = _pages_with_a_late_unknown()
    s, calls = _scraper(tmp_path, monkeypatch, pages, held)
    stubs = list(s.discover_new())
    assert [x["docket_number"] for x in stubs] == ["350 2012 269"]
    assert all(c[1] is None for c in calls)               # no dateFrom filter
    assert [c[0] for c in calls] == [1, 2, 3, 4]


def test_held_decision_under_another_docket_form_is_not_ingested_again(tmp_path, monkeypatch):
    monkeypatch.setenv("OCL_SCRAPER_RESCAN_ALL", "1")
    pages = [[_hit("470 24 21", 1), _hit("810 2012 179 _ 180", 2), _hit("460 2023 19", 3), _hit("470 24 22", 4)]]
    held = ["bl_gerichte_470 2024 21", "bl_gerichte_810 2012 179 _180", "bl_gerichte_460 23 19 a"]
    s, _ = _scraper(tmp_path, monkeypatch, pages, held)
    assert [x["docket_number"] for x in s.discover_new()] == ["470 24 22"]


def test_docket_key_normalises_year_and_separators():
    assert _docket_key("470 24 21") == _docket_key("470 2024 21") == ("470", "2024", (21,))
    assert _docket_key("720 12 156 295") == _docket_key("720 2012 156 _ 295")
    assert _docket_key("470 11 46") == ("470", "2011", (46,))
    assert _docket_key("810") is None
