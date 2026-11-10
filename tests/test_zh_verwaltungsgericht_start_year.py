"""scrapers/cantonal/zh_verwaltungsgericht.py: the year-by-year discovery loop
must cover filing year 1995, not just decision year >= 1996.

Regression for the parity audit of 2026-11-09: SR.1995.00044 was decided
1996-03-27 (inside the "published since ~1996" policy window) but filed in
1995, and cGeschaeftsjahr (the search form field the scraper loops over) is
the filing year, not the decision year. A loop starting at 1996 can never
query cGeschaeftsjahr=1995 and the case is unreachable no matter its decision
date. Offline only (invariant #8): no network, the court portal is never hit.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import scrapers.cantonal.zh_verwaltungsgericht as zh_vg  # noqa: E402
from scrapers.cantonal.zh_verwaltungsgericht import ZHVerwaltungsgerichtScraper  # noqa: E402


def test_start_year_covers_the_1995_filing_year():
    assert zh_vg.START_YEAR <= 1995


def test_discovery_queries_1995_by_default(tmp_path, monkeypatch):
    queried_years = []

    def fake_search_year(self, year=None):
        queried_years.append(year)
        return iter(())

    monkeypatch.setattr(ZHVerwaltungsgerichtScraper, "_search_year", fake_search_year)

    scraper = ZHVerwaltungsgerichtScraper(state_dir=tmp_path)
    list(scraper.discover_new(since_date=None))

    assert 1995 in queried_years
    assert 1996 in queried_years
    assert min(queried_years) == 1995
