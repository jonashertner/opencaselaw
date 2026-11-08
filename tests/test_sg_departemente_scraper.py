"""Offline tests for the SG departments scraper (scrapers/cantonal/sg_departemente.py).

Golden fixtures from publikationen.sg.ch/rechtsprechung-departemente,
2026-11-08 (invariant #8): result items, the result-count element and a detail
page whose ruling is a PDF attachment.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scrapers.cantonal import sg_departemente as sgd  # noqa: E402
from scrapers.cantonal.sg_departemente import (  # noqa: E402
    SGDepartementeScraper,
    assign_ids,
    attachment_urls,
    inline_text,
    parse_listing_page,
    parse_total,
)
from scrapers.entscheidsuche_ingest import _normalize_id_part  # noqa: E402

FIX = REPO / "tests" / "fixtures"
RESULTS = (FIX / "sg_departemente_results_20261108.html").read_text(encoding="utf-8")
TOTAL = (FIX / "sg_departemente_total_20261108.html").read_text(encoding="utf-8")
DETAIL = (FIX / "sg_departemente_detail_1146_20261108.html").read_text(encoding="utf-8")


class _Resp:
    def __init__(self, text="", content=b""):
        self.text = text
        self.content = content
        self.status_code = 200


def test_listing_item_fields():
    items = parse_listing_page(RESULTS)
    assert [i["uid"] for i in items] == ["1146", "1144", "1140", "518", "273", "152"]
    it = items[0]
    assert it["docket_number"] == "25-6483"
    assert it["department"] == "Bau- und Umweltdepartement"
    assert it["publisher"] == "Generalsekretariat Bau- und Umweltdepartement"
    assert it["title"] == "BUDE 2026 Nr. 062"
    assert it["decision_date"] == date(2026, 9, 8)
    assert it["publication_date"] == date(2026, 9, 22)
    assert it["summary"].startswith("Baurecht, Art. 92. Abs. 1")
    assert it["detail_url"] == "https://publikationen.sg.ch/rechtsprechung-departemente-detail/1146/"
    assert "DownloadPdf" in it["export_pdf_url"] and "&amp;" not in it["export_pdf_url"]
    old = items[-1]
    assert old["docket_number"] == "VD/G-13.12"
    assert old["department"] == "Volkswirtschaftsdepartement"
    assert old["decision_date"] == date(2013, 9, 23)


def test_total():
    assert parse_total(TOTAL) == 779
    assert parse_total("<html></html>") is None


def test_ids_match_the_entscheidsuche_rows_and_split_shared_dockets():
    items = parse_listing_page(RESULTS)
    assign_ids(items)
    by = {i["uid"]: i["decision_id"] for i in items}
    assert by["1146"] == "sg_publikationen_25-6483"
    assert by["152"] == "sg_publikationen_VD_G-13.12"           # as held from entscheidsuche
    assert by["152"] == "sg_publikationen_" + _normalize_id_part("VD/G-13.12")
    # 21-9043: Rekursentscheid 2021-11-08 (uid 273) and 2023-08-31 (uid 518).
    assert by["273"] == "sg_publikationen_21-9043"
    assert by["518"] == "sg_publikationen_21-9043_d20230831"
    # Order on the page does not matter: the earliest ruling keeps the bare id.
    rev = list(reversed(parse_listing_page(RESULTS)))
    assign_ids(rev)
    assert {i["uid"]: i["decision_id"] for i in rev} == by


def test_detail_page_points_at_the_attachment():
    assert attachment_urls(DETAIL) == [
        "https://publikationen.sg.ch/fileadmin/ekab/judical_sg/2026/09/25-6483/attachments/BUDE_2026_Nr._62.pdf"]
    assert inline_text(DETAIL) == ""          # "… finden Sie im angehängten PDF-Dokument."


def _scraper(tmp_path, pages, held=()):
    (tmp_path / "sg_departemente.jsonl").write_text("".join(f"{h}\n" for h in held))
    s = SGDepartementeScraper(state_dir=tmp_path)

    def fake_get(url, params=None, **k):
        assert url == sgd.LIST_URL
        if params and "page" in params:
            return _Resp(text=pages.get(int(params["page"]), ""))
        return _Resp(text=TOTAL)

    s.get = fake_get
    return s


def test_discovery_skips_known_and_reports_portal_total(tmp_path):
    s = _scraper(tmp_path, {1: RESULTS}, held=["sg_publikationen_21-9043"])
    stubs = list(s.discover_new())
    assert s.portal_count == 779
    assert [x["uid"] for x in stubs] == ["1146", "1144", "1140", "518", "152"]
    assert stubs[0]["url"] == stubs[0]["detail_url"]


def test_since_filter(tmp_path):
    s = _scraper(tmp_path, {1: RESULTS})
    assert {x["uid"] for x in s.discover_new(since_date="2026-01-01")} == {"1146", "1144", "1140"}


def test_restructured_listing_fails_the_run(tmp_path):
    s = _scraper(tmp_path, {1: "<html><body><p>Keine Treffer</p></body></html>"})
    with pytest.raises(RuntimeError, match="0 publications parsed"):
        list(s.discover_new())


def test_fetch_builds_the_row_from_the_attachment(tmp_path, monkeypatch):
    s = _scraper(tmp_path, {1: RESULTS})
    stub = next(iter(s.discover_new()))
    ruling = "Bau- und Umweltdepartement\nRekursentscheid vom 8. September 2026\n" + "Erwägungen " * 60
    calls = []

    def fake_get(url, **k):
        calls.append(url)
        if url.endswith("/1146/"):
            return _Resp(text=DETAIL)
        assert url.endswith("BUDE_2026_Nr._62.pdf")
        return _Resp(content=b"%PDF-1.6 ...")

    s.get = fake_get
    monkeypatch.setattr(SGDepartementeScraper, "_extract_pdf_text", staticmethod(lambda b: ruling))
    d = s.fetch_decision(stub)
    assert d.decision_id == "sg_publikationen_25-6483"
    assert d.court == "sg_publikationen" and d.canton == "SG"
    assert d.chamber == "Bau- und Umweltdepartement"
    assert d.docket_number == "25-6483"
    assert d.decision_date == date(2026, 9, 8)
    assert d.publication_date == date(2026, 9, 22)
    assert d.title == "BUDE 2026 Nr. 062"
    assert d.regeste.startswith("Baurecht, Art. 92.")
    assert d.full_text == ruling.strip()
    assert d.language == "de"
    assert d.source_url == "https://publikationen.sg.ch/rechtsprechung-departemente-detail/1146/"
    assert d.pdf_url.endswith("/attachments/BUDE_2026_Nr._62.pdf")
    assert d.external_id == "sg_dep:1146"


def test_textless_attachment_is_a_gap_not_a_row(tmp_path, monkeypatch):
    s = _scraper(tmp_path, {1: RESULTS})
    stub = next(iter(s.discover_new()))
    s.get = lambda url, **k: _Resp(text=DETAIL) if url.endswith("/1146/") else _Resp(content=b"%PDF-1.6")
    monkeypatch.setattr(SGDepartementeScraper, "_extract_pdf_text", staticmethod(lambda b: ""))
    assert s.fetch_decision(stub) is None
    assert s.state.is_known("sg_publikationen_25-6483")      # cached as gap, re-probed after TTL
