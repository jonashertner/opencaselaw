"""Offline tests for the Basel-Stadt Gerichte scraper (scrapers/cantonal/bs_gerichte.py).

Golden excerpt of a live result page (invariant #8). Covers the 2026-09-17 fix
for the 11,005-vs-10,628 gap:

  * decision_id is minted from the court's decision number (the bracketed
    "(SVG.2018.352)"), not the case number, so the several decisions the
    portal lists under one case number all survive;
  * the case number stays in docket_number (the cited form), the decision
    number in docket_number_2;
  * the Zivilgericht is a source; the page size exceeds every year on the
    portal; an over-full year is split by Geschäftsart and a split that still
    comes up short is an ERROR the health check sees, not a warning;
  * a state file still on the case-number scheme stops the run.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scrapers.cantonal import bs_gerichte as B  # noqa: E402

FIXTURE = (REPO / "tests" / "fixtures" / "bs_gerichte_listing_excerpt.html").read_text(encoding="utf-8")
SVG = next(s for s in B.SOURCES if s["key"] == "SVG")


class _Resp:
    def __init__(self, text):
        self.text = text


def _scraper(tmp_path, monkeypatch, responder):
    s = B.BSGerichteScraper(state_dir=tmp_path)
    monkeypatch.setattr(s, "post", lambda url, data=None, **k: _Resp(responder(data or {})))
    return s


# ── listing parse ───────────────────────────────────────────────────────


def test_hit_count_and_rows_parse():
    s = B.BSGerichteScraper.__new__(B.BSGerichteScraper)
    assert B.BSGerichteScraper._parse_hit_count(s, FIXTURE) == 4
    stubs = list(B.BSGerichteScraper._parse_result_page(s, FIXTURE, SVG))
    assert len(stubs) == 4


def test_ids_come_from_the_decision_number_and_are_distinct():
    s = B.BSGerichteScraper.__new__(B.BSGerichteScraper)
    stubs = list(B.BSGerichteScraper._parse_result_page(s, FIXTURE, SVG))
    by_id = {x["decision_id"]: x for x in stubs}
    assert len(by_id) == 4, "two decisions under one case number must not collapse"
    assert set(by_id) == {
        "bs_sozialversicherungsgericht_SVG.2020.291",
        "bs_sozialversicherungsgericht_SVG.2018.352",
        "bs_sozialversicherungsgericht_SVG.2019.152",
        "bs_sozialversicherungsgericht_SVG.2018.65",
    }
    row = by_id["bs_sozialversicherungsgericht_SVG.2020.291"]
    assert row["docket_number"] == "IV.2017.108"      # the cited case number
    assert row["docket_number_2"] == "SVG.2020.291"   # the court's decision number
    assert row["court_code"] == "bs_sozialversicherungsgericht"
    assert row["title"] == "Nichteintreten"
    assert str(row["decision_date"]) == "2020-11-25"       # Entscheiddatum is in the listing
    assert str(row["publication_date"]) == "2021-02-02"
    assert row["url"].startswith("https://rechtsprechung.gerichte.bs.ch/cgi-bin/")
    # both siblings of IV.2017.108 are present
    assert {x["docket_number"] for x in stubs} == {"IV.2017.108", "UV.2017.30"}


# ── sources / constants ─────────────────────────────────────────────────


def test_three_instances_including_zivilgericht():
    assert [s["key"] for s in B.SOURCES] == ["AG", "SVG", "ZG"]
    assert {s["court_code"] for s in B.SOURCES} == {
        "bs_appellationsgericht", "bs_sozialversicherungsgericht", "bs_zivilgericht",
    }


def test_page_size_covers_the_largest_year():
    # 793 rows (AG 2019) was the largest year on 2026-09-17; the portal
    # honours 2000 (verified live). Anything below 1000 would truncate again.
    assert B.RESULTS_PER_PAGE >= 1000
    assert B.START_YEAR <= 2008          # the portal's own earliest year


# ── discovery: page cap, Geschäftsart split, health-visible errors ────────


def _listing(n_hits, rows_html=FIXTURE):
    """The fixture page with its hit count rewritten to ``n_hits``."""
    return rows_html.replace("von 4 gefundenen", f"von {n_hits} gefundenen")


def test_discovery_yields_new_rows_only(tmp_path, monkeypatch):
    def responder(data):
        return _listing(4) if data.get("cGeschaeftsjahr") == "2017" else "<html>keine Treffer</html>"

    s = _scraper(tmp_path, monkeypatch, responder)
    s.state.mark_scraped("bs_sozialversicherungsgericht_SVG.2018.65")
    stubs = list(s._discover_source(SVG, None))
    assert {x["decision_id"] for x in stubs} == {
        "bs_sozialversicherungsgericht_SVG.2020.291",
        "bs_sozialversicherungsgericht_SVG.2018.352",
        "bs_sozialversicherungsgericht_SVG.2019.152",
    }


def test_overfull_year_is_split_by_geschaeftsart(tmp_path, monkeypatch, caplog):
    calls = []

    def responder(data):
        calls.append((data.get("cGeschaeftsjahr"), data.get("cGeschaeftsart")))
        if data.get("cGeschaeftsjahr") != "2017":
            return "<html>keine Treffer</html>"
        art = data.get("cGeschaeftsart") or ""
        if art == "":
            return _listing(B.RESULTS_PER_PAGE + 1)      # the year overflows the page
        if art == "IV":
            return _listing(2)                           # both IV.2017.108 rows
        if art == "UV":
            return _listing(2)
        return "<html>keine Treffer</html>"

    s = _scraper(tmp_path, monkeypatch, responder)
    with caplog.at_level(logging.ERROR):
        stubs = list(s._discover_source(SVG, None))
    # one request per Geschäftsart after the overflow, merged on decision_id
    assert ("2017", "IV") in calls and ("2017", "UV") in calls
    assert len({x["decision_id"] for x in stubs}) == 4
    # the merged split lists fewer rows than the year's hit count → ERROR the
    # health check counts ("search failed"), never a silent gap
    assert any("search failed" in r.getMessage() and "after splitting" in r.getMessage()
               for r in caplog.records)


def test_short_or_failed_search_is_a_discovery_error(tmp_path, monkeypatch, caplog):
    def responder(data):
        raise ConnectionError("portal down")

    s = _scraper(tmp_path, monkeypatch, responder)
    with caplog.at_level(logging.ERROR):
        assert list(s._discover_source(SVG, None)) == []
    msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert msgs and all("search failed" in m for m in msgs)


# ── startup guard ───────────────────────────────────────────────────────


def test_unmigrated_state_stops_the_run(tmp_path, monkeypatch):
    s = _scraper(tmp_path, monkeypatch, lambda data: "<html>keine Treffer</html>")
    s.state.mark_scraped("bs_appellationsgericht_SB.2013.5")   # case-number scheme only
    with pytest.raises(RuntimeError, match="migrate_bs_gerichte_ids"):
        list(s.discover_new())


def test_migrated_or_empty_state_runs(tmp_path, monkeypatch):
    s = _scraper(tmp_path, monkeypatch, lambda data: "<html>keine Treffer</html>")
    assert list(s.discover_new()) == []                         # empty state: fine
    s.state.mark_scraped("bs_appellationsgericht_SB.2013.5")
    s.state.mark_scraped("bs_appellationsgericht_AG.2014.40")  # one new-scheme id suffices
    assert list(s.discover_new()) == []


# ── document text: every paragraph style, not only MsoNormal ─────────────


def test_document_text_takes_every_paragraph_style():
    from bs4 import BeautifulSoup
    html = """<html><body><div class="WordSection1">
      <p class="MsoNormal"><b>Zivilgericht</b> des Kantons Basel-Stadt</p>
      <p class="MsoNormal">K3.2025.24 ENTSCHEID vom<span> 18.</span> August 2026</p>
      <p class="NummerierungTatsachen">1. Die Klägerin verlangt ...</p>
      <p class="Entscheidtext">Die Beklagte bestreitet die Forderung.</p>
      <p class="NummerierungErwgungen">2.1 Zuständigkeit</p>
      <h2>Im Auftrag der Beschwerdegegnerin hat die Begutachtung stattgefunden.</h2>
      <table><tr><td><p class="aaDispositiv">Die Klage wird abgewiesen.</p></td></tr></table>
      <p class="MsoBodyText">Das Gericht ist zuständig.</p>
      <p class="MsoNormal"></p>
    </div></body></html>"""
    soup = BeautifulSoup(html, "html.parser")
    text = B._extract_document_text(soup)
    assert text.split("\n\n") == [
        "Zivilgericht des Kantons Basel-Stadt",
        "K3.2025.24 ENTSCHEID vom 18. August 2026",     # inline runs joined with a space
        "1. Die Klägerin verlangt ...",
        "Die Beklagte bestreitet die Forderung.",
        "2.1 Zuständigkeit",
        "Im Auftrag der Beschwerdegegnerin hat die Begutachtung stattgefunden.",   # body text in <h2>
        "Die Klage wird abgewiesen.",                                              # paragraph inside a table
        "Das Gericht ist zuständig.",
    ]
    assert str(B._extract_decision_date_from_doc(soup)) == "2026-08-18"


# ── health line: portal count ───────────────────────────────────────────


def test_portal_count_is_the_sum_of_every_year_and_instance(tmp_path, monkeypatch):
    def responder(data):
        if data.get("cGeschaeftsjahr") != "2017":
            return "<html>keine Treffer</html>"
        # every instance answers the same four rows for 2017
        return _listing(4)

    s = _scraper(tmp_path, monkeypatch, responder)
    list(s.discover_new())
    # AG + SVG + ZG, one year each with 4 hits → run_scraper prints our/12
    assert s.portal_count == 12
