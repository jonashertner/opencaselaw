"""be_bvd and be_steuerrekurs: court filters, registration, health (offline).

Both portals were written off as dead. Neither was: the scrapers sent the wrong
court filter and the server answers a wrong filter with //OK[0] and no error.
Captured from each portal's own search in the browser on 2026-11-08:

  bvd-entscheide.apps.be.ch   filter "TRI"   //OK[2255]  ("BVD", the DLAConfig name: 0)
  strk-entscheide.apps.be.ch  filter "StRK"  //OK[367]   ("STRK": 0; case-sensitive)

The credential does not matter (all five BVD config strings give 2255).
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def test_be_bvd_registered():
    from run_scraper import SCRAPERS
    assert SCRAPERS["be_bvd"] == ("scrapers.cantonal.be_bvd", "BEBvdScraper")


def test_be_steuerrekurs_runs_nightly_and_reports_to_health():
    import run_all_scrapers
    from run_scraper import SCRAPERS
    from scripts import check_scraper_freshness as f
    assert "be_steuerrekurs" in SCRAPERS
    assert "be_steuerrekurs" not in run_all_scrapers.SKIP_BY_DEFAULT
    # A registered scraper missing from a full health run is flagged unless it is
    # KNOWN_DEAD or ENTSCHEIDSUCHE_ONLY; both BE courts must be checked again.
    for court in ("be_steuerrekurs", "be_bvd"):
        assert court not in f.KNOWN_DEAD_SOURCES
        assert court not in f.ENTSCHEIDSUCHE_ONLY
        assert court not in run_all_scrapers.SKIP_BY_DEFAULT


def test_be_bvd_has_backfill_budget():
    import run_all_scrapers
    assert run_all_scrapers.SLOW_SCRAPERS["be_bvd"] >= 4 * 3600


def test_be_bvd_imports_and_subclasses_tribuna():
    from scrapers.cantonal.be_bvd import BEBvdScraper
    from scrapers.cantonal.base_tribuna import TribunaBaseScraper
    assert issubclass(BEBvdScraper, TribunaBaseScraper)


def test_be_bvd_config():
    from scrapers.cantonal.be_bvd import BEBvdScraper as s
    assert s.CANTON == "BE"
    assert s.COURT_CODE_STR == "be_bvd"
    assert s.BASE_URL == "https://www.bvd-entscheide.apps.be.ch"
    assert s.TRIBUNA_PATH == "tribunavtplus"
    assert s.COURT_FILTER == "TRI"
    assert s.SEARCH_FIELD_COUNT == 20          # nf=21 answers //EX
    assert s.TITLE_FROM_RECORD is True
    assert s.LOCALE == "de"
    # VERIFY_SSL stays gone (removed 2026-08-26; the bundle verifies cleanly).
    assert getattr(s, "VERIFY_SSL", True) is not False
    assert f"{s.BASE_URL}/{s.TRIBUNA_PATH}" == "https://www.bvd-entscheide.apps.be.ch/tribunavtplus"


def test_be_steuerrekurs_config():
    from scrapers.cantonal.be_steuerrekurs import BESteuerrekursScraper as s
    assert s.COURT_FILTER == "StRK"
    assert s.SEARCH_FIELD_COUNT == 21
    assert s.TITLE_FROM_RECORD is True
    assert s.BASE_URL == "https://www.strk-entscheide.apps.be.ch/tribunapublikation"


def test_search_body_carries_the_filter(tmp_path):
    from scrapers.cantonal.be_bvd import BEBvdScraper
    from scrapers.cantonal.be_steuerrekurs import BESteuerrekursScraper
    bvd = BEBvdScraper(state_dir=tmp_path / "a")._build_search_body("c" * 160, 0, None)
    strk = BESteuerrekursScraper(state_dir=tmp_path / "b")._build_search_body("c" * 128, 0, None)
    assert "|0|TRI|0;false|" in bvd and "|46|" in bvd           # 46-param search
    assert "|0|StRK|0;false|" in strk and "|47|" in strk         # 47-param search


def test_direct_ids_are_the_live_es_ids(tmp_path):
    """The frozen es shards key rows as <court>_<docket with underscores>, and
    those ids are live. make_decision_id would keep the spaces, so the direct
    row would never meet its es copy on import and the content dedup would keep
    the es copy (longer by page furniture) with its date."""
    from scrapers.cantonal.be_bvd import BEBvdScraper
    from scrapers.cantonal.be_steuerrekurs import BESteuerrekursScraper
    from scrapers.cantonal.be_verwaltungsgericht import BEVerwaltungsgerichtScraper
    assert BEBvdScraper(state_dir=tmp_path / "a")._decision_id("110 2002 108") == "be_bvd_110_2002_108"
    assert BEBvdScraper(state_dir=tmp_path / "a")._decision_id("120 2026 13") == "be_bvd_120_2026_13"
    assert (BESteuerrekursScraper(state_dir=tmp_path / "b")._decision_id("100 2025 392")
            == "be_steuerrekurs_100_2025_392")
    # other Tribuna courts keep their (space) ids
    assert (BEVerwaltungsgerichtScraper(state_dir=tmp_path / "c")._decision_id("100 2023 240")
            == "be_verwaltungsgericht_100 2023 240")


def test_discovery_skips_known_live_ids(tmp_path, monkeypatch):
    from scrapers.cantonal.be_steuerrekurs import BESteuerrekursScraper
    s = BESteuerrekursScraper(state_dir=tmp_path)
    page = (REPO / "tests" / "fixtures" / "tribuna_be_strk_page8_20261108.txt").read_text()
    s.state.mark_scraped("be_steuerrekurs_100_2022_20")
    monkeypatch.setattr(s, "post", lambda *a, **k: type("R", (), {"text": page})())
    stubs = list(s._search_court("cred", "StRK"))
    ids = {x["decision_id"] for x in stubs}
    assert "be_steuerrekurs_100_2022_20" not in ids
    assert "be_steuerrekurs_100_2021_349" in ids
    assert len(ids) == 19                     # 20 rows on the page, one already known
