"""Offline tests: new rows of the scrapers get the date their source states
(comparison report 2026-11-08, item #5). No network: every fetch is stubbed."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from models import Decision
from scrapers.bge import BGELeitentscheideScraper
from scrapers.bge_egmr import BGEEGMRScraper
from scrapers.cantonal import ar_gerichte, be_zivilstraf, gr_gerichte, nw_gerichte, ur_gerichte
from scrapers.cantonal.base_tribuna import TribunaBaseScraper


class _Resp:
    def __init__(self, text="<html></html>", status_code=200, content=b""):
        self.text, self.status_code, self.content = text, status_code, content


def test_bge_egmr_scraper_dates_from_the_docket(tmp_path, monkeypatch):
    s = BGEEGMRScraper(state_dir=tmp_path)
    monkeypatch.setattr(s, "_safe_get", lambda url: _Resp())
    monkeypatch.setattr(s, "_parse_egmr_document", lambda html: {"text": "Décision du 23 août 2016."})
    d = s.fetch_decision({"docket_number": "20160823_53377_11", "url": "https://x/egmr",
                          "decision_date": "23.08.16", "case_name": "Zuisens SA c. Suisse"})
    assert d.decision_date == date(2016, 8, 23)


def test_bge_scraper_egmr_path_never_writes_1_january(tmp_path, monkeypatch):
    s = BGELeitentscheideScraper(state_dir=tmp_path, include_egmr=True)
    monkeypatch.setattr(s, "_fetch_trilingual_regesten", lambda url: {})
    monkeypatch.setattr(s, "_safe_get", lambda url: _Resp())
    monkeypatch.setattr(s, "_parse_egmr_document", lambda html: {"text": "Urteil vom 27. März 2025.", "chamber": "EGMR"})
    stub = {"docket_number": "20250327_2633_23", "url": "/ext/x", "decision_date": "27.03.25",
            "case_name": "J.G. c. Suisse", "is_egmr": True}
    d = s.fetch_decision(stub)
    assert d.court == "bge_egmr" and d.decision_date == date(2025, 3, 27)
    d2 = s.fetch_decision(dict(stub, docket_number="12345/67", decision_date="n/a"))
    assert d2.decision_date is None


def test_ur_rechenschaftsbericht_excerpt_dated_by_its_citation_line(tmp_path, monkeypatch):
    s = ur_gerichte.URGerichteScraper(state_dir=tmp_path)
    excerpt = ("2020_OG V 19 63. Kantonales Verfahrensrecht. … Entscheid vom 6. Dezember 2011 "
               "der Vorinstanz … " * 3 + "\nObergericht, 11. Februar 2020, OG V 19 63\n" + "Text " * 50)
    monkeypatch.setattr(s, "get", lambda url, timeout=30: _Resp(content=b"%PDF" + b"x" * 2000))
    monkeypatch.setattr(ur_gerichte.URGerichteScraper, "_extract_pdf_text", staticmethod(lambda b: excerpt))
    d = s.fetch_decision({"decision_id": "ur_gerichte_2020_OG V 19 63", "docket_number": "2020_OG V 19 63",
                          "decision_date": None, "pdf_url": "https://www.ur.ch/_docn/1/x.pdf",
                          "url": "https://www.ur.ch/_rte/publikation/1"})
    assert d.decision_date == date(2020, 2, 11)


def test_nw_head_fallback_when_listing_has_no_date(tmp_path, monkeypatch):
    s = nw_gerichte.NWGerichteScraper(state_dir=tmp_path)
    text = ("GERICHTE VERWALTUNGSGERICHT Bahnhofplatz 3, Postfach 1241, 6371 Stans\n"
            "VA 25 26\nEntscheid vom 23. Februar 2026\nVerwaltungsabteilung\n"
            "Besetzung Präsidentin Livia Zimmermann\n" + "Sachverhalt " * 30)
    monkeypatch.setattr(s, "_extract_pdf_text", lambda url: text)
    d = s.fetch_decision({"decision_id": "nw_gerichte_VA 25 26", "docket_number": "VA 25 26",
                          "decision_date": None, "pdf_url": "https://www.nw.ch/_rte/publikation/42163"})
    assert d.decision_date == date(2026, 2, 23)
    d = s.fetch_decision({"decision_id": "nw_gerichte_VA 25 26", "docket_number": "VA 25 26",
                          "decision_date": date(2026, 2, 22), "pdf_url": "https://www.nw.ch/x"})
    assert d.decision_date == date(2026, 2, 22)   # the listing wins when it has one


def _tribuna_decision(court, docket, d, text):
    return Decision(decision_id=f"{court}_{docket}", court=court, canton="GR", docket_number=docket,
                    decision_date=d, language="de", full_text=text, source_url="https://x")


def test_gr_yearbook_entry_dated_by_its_trailer(tmp_path, monkeypatch):
    s = gr_gerichte.GRGerichteScraper(state_dir=tmp_path)
    text = ("PKG 1999 25\n…\nSB 99 71\nUrteil vom 14. Dezember 1999\n25 – …\n" + "y" * 3000
            + "\nSB 98 74\nUrteil vom 20. Januar 1999\n")
    monkeypatch.setattr(TribunaBaseScraper, "fetch_decision",
                        lambda self, stub: _tribuna_decision("gr_gerichte", stub["docket_number"],
                                                             date(2026, 2, 14), text))
    assert s.fetch_decision({"docket_number": "PKG 1999 25"}).decision_date == date(1999, 1, 20)
    # No trailer: undated, not the register's upload date.
    monkeypatch.setattr(TribunaBaseScraper, "fetch_decision",
                        lambda self, stub: _tribuna_decision("gr_gerichte", stub["docket_number"],
                                                             date(2026, 2, 14), "PKG 1999 26 …"))
    assert s.fetch_decision({"docket_number": "PKG 1999 26"}).decision_date is None
    # Ordinary rulings keep the register's date.
    monkeypatch.setattr(TribunaBaseScraper, "fetch_decision",
                        lambda self, stub: _tribuna_decision("gr_gerichte", stub["docket_number"],
                                                             date(2026, 10, 22), "Urteil vom 22. Oktober 2026"))
    assert s.fetch_decision({"docket_number": "ZR1 2026 134"}).decision_date == date(2026, 10, 22)


def test_be_letterhead_only_when_register_has_no_date(tmp_path, monkeypatch):
    s = be_zivilstraf.BEZivilStrafScraper(state_dir=tmp_path)
    text = "Beschluss\nBK 25 533\n3001 Bern\nBern, 5. Februar 2026\nBesetzung\nOberrichter X"
    monkeypatch.setattr(TribunaBaseScraper, "fetch_decision",
                        lambda self, stub: _tribuna_decision("be_zivilstraf", "BK 25 533", stub.get("d"), text))
    assert s.fetch_decision({"d": None}).decision_date == date(2026, 2, 5)
    assert s.fetch_decision({"d": date(2026, 2, 6)}).decision_date == date(2026, 2, 6)


def test_ar_gvp_entry_dated_by_its_file_name(tmp_path):
    s = ar_gerichte.ARGerichteScraper(state_dir=tmp_path)
    doc = {"leid": "x", "metadataKeywordTextMap": {
        "title": ["OG ARGVP 2002 3410"],
        "originalUrl": ["/download/AR GVP/Gerichtsentscheide/2002/OG-20021114-20021114-ARGVP-2002-3410.pdf"]},
        "metadataDateMap": {"decisionDate": "1995-04-30T00:00:00Z", "publicationDate": "2002-11-14T00:00:00Z"}}
    assert s._parse_doc(doc)["decision_date"] == date(2002, 11, 14)
    doc["metadataKeywordTextMap"]["originalUrl"] = ["/download/Kantonsgericht/2013/KG-20130117-K1S-12-2-20190618.pdf"]
    doc["metadataKeywordTextMap"]["title"] = ["KG K1S-12-2"]
    doc["metadataDateMap"]["decisionDate"] = "2013-01-17T00:00:00Z"
    assert s._parse_doc(doc)["decision_date"] == date(2013, 1, 17)
