"""Offline test for the RAB/ASR scraper (scrapers/rab.py).

Golden HTML fixture of the Drupal rab-download-tile (invariant #8). Asserts docket from
the tile <p>, decision date decoded from the filename, German PDF preferred, and that
pagination stops on an empty page.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scrapers.rab import RABScraper  # noqa: E402

TILE = """<div class="rab-download-tile">
  <div class="rab-download-tile__info"><div class="uk-flex"><span class="rab-icon"></span>
    <p>RAB-Verfügung 2020-01</p></div></div>
  <ul class="rab-download-tile__items">
    <li class="rab-download-tile__item"><a
       href="/sites/default/files/2025-10/Verf%C3%BCgung_der_RAB_vom_28__September_2020.pdf"
       download aria-label="Download Deutsch document"
       class="rab-download-tile__link rab-link--download">de</a></li>
    <li class="rab-download-tile__item rab-download-tile__item--disabled">fr</li>
  </ul></div>"""
EMPTY = "<html><body>no tiles</body></html>"


def _scraper(monkeypatch, tmp_path):
    s = RABScraper(state_dir=tmp_path)

    def fake_get(url, **k):
        class R:
            text = TILE if "page=0" in url else EMPTY
        return R()

    monkeypatch.setattr(s, "get", fake_get)
    return s


def test_rab_discover(monkeypatch, tmp_path):
    stubs = list(_scraper(monkeypatch, tmp_path).discover_new())
    assert len(stubs) == 1                              # pagination stopped on the empty page-1
    st = stubs[0]
    assert st["docket_number"] == "2020-01"
    assert st["decision_date"] == "28. September 2020"
    assert st["pdf_url"].endswith("28__September_2020.pdf")
    assert st["title"] == "RAB-Verfügung 2020-01"


def test_rab_since_filter(monkeypatch, tmp_path):
    stubs = list(_scraper(monkeypatch, tmp_path).discover_new(since_date=date(2021, 1, 1)))
    assert stubs == []                                  # the 2020 decision is filtered out


def test_rab_stops_when_pager_repeats_the_tile_block(monkeypatch, tmp_path):
    # rab-asr.ch renders the same 5 "Leading Cases" tiles on every ?page=N (the pager
    # belongs to the enforcement digests). Before 2026-09-14 the loop fetched 30 pages.
    s = RABScraper(state_dir=tmp_path)
    fetched = []

    def fake_get(url, **k):
        fetched.append(url)

        class R:
            text = TILE
        return R()

    monkeypatch.setattr(s, "get", fake_get)
    stubs = list(s.discover_new())
    assert len(stubs) == 1
    assert len(fetched) == 2          # page 0 (new tiles) + page 1 (same tiles → stop)
