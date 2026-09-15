"""UBI (ubi.admin.ch): nightly early stop and full rescan (offline).

2026-09-15: the nightly walk stops at the first all-known page after page 1, so a
decision listed behind known ones is never reached. A full listing walk found 668
decisions, 667 held; the missing one (b.980 of 16.05.2024) sat behind known pages.
OCL_SCRAPER_RESCAN_ALL=1 walks every page. Pages, rows and the next link are faked.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scrapers.ubi import UBIScraper  # noqa: E402


def _entry(n: int) -> dict:
    return {"docket_number": f"b.{n}", "decision_date": "16.05.2024",
            "pdf_url": f"https://www.ubi.admin.ch/inhalte/entscheide/b_{n}.pdf"}


def _scraper(tmp_path, monkeypatch, pages, held=()):
    s = UBIScraper(state_dir=tmp_path)
    for n in held:
        s.state.mark_scraped(f"ubi_b.{n}")
    urls = [f"https://www.ubi.admin.ch/de/entscheide?page={i}" for i in range(len(pages))]
    monkeypatch.setattr(s, "_fetch_page", lambda url: url)
    monkeypatch.setattr(s, "_parse_decision_rows", lambda soup: [dict(e) for e in pages[urls.index(soup)]])
    monkeypatch.setattr(s, "_get_next_page_url",
                        lambda soup: urls[urls.index(soup) + 1] if urls.index(soup) + 1 < len(urls) else None)
    monkeypatch.setattr("scrapers.ubi.LISTING_URL", urls[0])
    return s


PAGES = [
    [_entry(1100), _entry(1099)],   # page 1: new
    [_entry(1000), _entry(999)],    # page 2: all known
    [_entry(981), _entry(980)],     # page 3: b.980 not held
]
HELD = (1000, 999, 981)


def test_nightly_walk_stops_at_the_first_all_known_page(tmp_path, monkeypatch):
    monkeypatch.delenv("OCL_SCRAPER_RESCAN_ALL", raising=False)
    s = _scraper(tmp_path, monkeypatch, PAGES, HELD)
    assert [x["docket_number"] for x in s.discover_new()] == ["b.1100", "b.1099"]


def test_rescan_all_reaches_a_decision_behind_known_pages(tmp_path, monkeypatch):
    monkeypatch.setenv("OCL_SCRAPER_RESCAN_ALL", "1")
    s = _scraper(tmp_path, monkeypatch, PAGES, HELD)
    assert [x["docket_number"] for x in s.discover_new()] == ["b.1100", "b.1099", "b.980"]


def test_duplicate_dockets_in_one_walk_are_yielded_once(tmp_path, monkeypatch):
    monkeypatch.setenv("OCL_SCRAPER_RESCAN_ALL", "1")
    s = _scraper(tmp_path, monkeypatch, [[_entry(998), _entry(998)], [_entry(998), _entry(997)]])
    assert [x["docket_number"] for x in s.discover_new()] == ["b.998", "b.997"]


def test_page_cap_stops_a_runaway_next_chain(tmp_path, monkeypatch):
    monkeypatch.setenv("OCL_SCRAPER_RESCAN_ALL", "1")
    s = _scraper(tmp_path, monkeypatch, [[_entry(2000 + i)] for i in range(12)])
    s.MAX_PAGES = 5
    assert len(list(s.discover_new())) == 5
