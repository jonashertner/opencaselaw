"""PostCom (postcom.admin.ch) after the 2026 admin.ch relaunch (offline).

Golden excerpts of /de/verfuegungen and /de/strafbescheide fetched 2026-09-15. The old
listing URL /de/dokumentation/verfuegungen became a hub without decisions; the <p> parser
found 0 entries and the nightly run reported "+0 new" (the 2026 Verfügungen after 2/2026
and every Strafbescheid were missed). All PDF URLs moved to the admin.ch DAM and held rows
carry ids from older parser rules, so tiles are resolved against the corpus shard before
an id is minted.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import scrapers.postcom as pc  # noqa: E402
from scrapers.postcom import STRAFBESCHEIDE_URL, VERFUEGUNGEN_URL, PostComScraper  # noqa: E402

FIX = REPO / "tests" / "fixtures"
VERF = (FIX / "postcom_verfuegungen_20260915_excerpt.html").read_text(encoding="utf-8")
STRAF = (FIX / "postcom_strafbescheide_20260915_excerpt.html").read_text(encoding="utf-8")
HUB = "<html><body><a href='/de/verfuegungen'>Mehr über «Verfügungen»</a></body></html>"

STRAF_DOCKETS = {
    "Strafbescheid 2025-05-15 de", "Strafbescheid 2025-05-15 fr", "PostCom-413-6/2",
    "PostCom-413-6/5", "Strafbescheid 2020-12-10 fr 2", "Strafbescheid 2021-12-13 it",
}


class _R:
    def __init__(self, text="", content=b""):
        self.text = text
        self.content = content


def _scraper(tmp_path, monkeypatch, shard_rows=None, pages=None):
    shard = tmp_path / "postcom.jsonl"
    if shard_rows is not None:
        shard.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in shard_rows), encoding="utf-8")
    monkeypatch.setenv("POSTCOM_SHARD", str(shard))
    s = PostComScraper(state_dir=tmp_path / "state")
    for r in shard_rows or []:
        s.state.mark_scraped(r["decision_id"])
    pages = pages if pages is not None else {VERFUEGUNGEN_URL: VERF, STRAFBESCHEIDE_URL: STRAF}
    fetched = []

    def fake_get(url, **kw):
        fetched.append(url)
        return _R(pages.get(url, HUB))

    monkeypatch.setattr(s, "get", fake_get)
    return s, fetched


def test_reads_both_pages_and_keys_every_tile(tmp_path, monkeypatch):
    s, fetched = _scraper(tmp_path, monkeypatch)
    stubs = list(s.discover_new())
    assert fetched == [VERFUEGUNGEN_URL, STRAFBESCHEIDE_URL]
    by = {x["docket_number"]: x for x in stubs}
    assert len(stubs) == len(by) == 15
    for docket in ("VFG-9-2026", "VFG-6-2026", "VFG-2-2026", "VFG-2-2026-liste", "VFG-9-2023", "VFG-10-2014", "VFG-1-2013"):
        assert by[docket]["decision_type"] == "Verfügung", docket
    assert by["VFG-6-2026"]["decision_date"] == "13.5.2026"            # D.M.YYYY title date
    slugs = [d for d in by if not d.startswith(("VFG-", "Strafbescheid ", "PostCom-"))]
    assert sorted(d[-10:] for d in slugs) == ["2013-02-07", "2022-03-18"]
    assert STRAF_DOCKETS <= set(by)
    assert by["Strafbescheid 2025-05-15 de"]["decision_date"] == "2025-05-15"   # title date, not the 2026 upload date
    assert all(by[d]["decision_type"] == "Strafbescheid" for d in STRAF_DOCKETS)


def test_tiles_of_held_decisions_are_skipped_whatever_id_they_were_stored_under(tmp_path, monkeypatch):
    """2026-09-15: production holds 6/2026 under a slug id, 9/2023 as VFG-09-2023, the 2026
    Liste as VFG-2-2026-beilage, 10/2014 under a French slug, 1/2013 as VFG-1-2013-beilage."""
    dam = "https://www.postcom.admin.ch/inhalte/PDF/Verfuegungen/"
    held = [
        {"decision_id": "postcom_1352026-verfuegung-6-2026-betreffend-standort-des-ha", "decision_date": None,
         "title": "13.5.2026 Verfügung 6 2026 betreffend Standort des Hausbriefkastens anonymisiert",
         "full_text": "Eidgenössische Postkommission PostCom ... Verfügung Nr. 6/2026 vom 13. Mai 2026",
         "pdf_url": dam + "13.5.2026_Verfügung_6_2026_Standort_des_Hausbriefkastens_anonymisiert_20260513.pdf"},
        {"decision_id": "postcom_VFG-09-2023", "decision_date": "2023-06-15",
         "title": "Verfügung 09/2023 betreffend Hausbriefkasten rechtskräftig",
         "full_text": "Eidgenössische Postkommission PostCom ... Verfügung Nr.  9/2023 vom 15. Juni 2023",
         "pdf_url": dam + "VFG_09_2023_PostCom_Hausbriefkasten_20230615.pdf"},
        {"decision_id": "postcom_VFG-2-2026-beilage", "decision_date": "2026-01-30",
         "title": "Liste Dienstleistungen der Grundversorgung 2026, Beilage zur Verfügung 2/2026",
         "full_text": "Anbietende Gesellschaft Segment Produktgruppe Kategorie ...",
         "pdf_url": dam + "VFG_2_2026_PostCom_Beilage_Liste_DL_Grundversorgung2025_20260130.pdf"},
        {"decision_id": "postcom_VFG-2-2026", "decision_date": "2026-01-30",
         "title": "Verfügung 2 2026 betreffend Zuweisung der Dienstleistungen zur Grundversorgung 2026",
         "full_text": "Eidgenössische Postkommission PostCom ...",
         "pdf_url": dam + "VFG_2_2026_PostCom_DL_Grundversorgung2026_20260130.pdf"},
        {"decision_id": "postcom_decision-no102014-concernant-lemplacement-des-boites", "decision_date": "2014-08-28",
         "title": "Décision no.10/2014 concernant l’emplacement des boîtes aux lettres",
         "full_text": "Commission fédérale de la poste PostCom ...",
         "pdf_url": dam + "VFG_10_2014_PostCom_vom_2014-08-28_anonymisiert.pdf"},
        {"decision_id": "postcom_VFG-1-2013-beilage", "decision_date": "2013-02-07",
         "title": "Verfügung 1/2013 betr. Nettokosten Grundversorgung",
         "full_text": "Commission fédérale de la poste PostCom Le Président ...",
         "pdf_url": dam + "VFG_01_2013_PostCom_20130207.pdf"},
    ]
    s, _ = _scraper(tmp_path, monkeypatch, shard_rows=held)
    dockets = {x["docket_number"] for x in s.discover_new()}
    new_verf = {d for d in dockets if d not in STRAF_DOCKETS}
    assert "VFG-9-2026" in new_verf
    assert sorted(d[-10:] for d in new_verf if d != "VFG-9-2026") == ["2013-02-07", "2022-03-18"]
    assert STRAF_DOCKETS <= dockets


def test_no_tiles_anywhere_is_an_error(tmp_path, monkeypatch):
    s, _ = _scraper(tmp_path, monkeypatch, pages={})
    with pytest.raises(RuntimeError, match="no download-item tiles"):
        list(s.discover_new())


def test_unreadable_shard_while_state_holds_ids_refuses_to_mint(tmp_path, monkeypatch):
    monkeypatch.setenv("POSTCOM_SHARD", str(tmp_path / "missing.jsonl"))
    s = PostComScraper(state_dir=tmp_path / "state")
    s.state.mark_scraped("postcom_VFG-1-2013")
    monkeypatch.setattr(s, "get", lambda url, **k: _R(VERF))
    with pytest.raises(RuntimeError, match="refusing to mint"):
        list(s.discover_new())


def test_fetch_keeps_the_decision_type_of_the_page(tmp_path, monkeypatch):
    s, _ = _scraper(tmp_path, monkeypatch)
    monkeypatch.setattr(s, "get", lambda url, **k: _R(content=b"%PDF-1.4 " + b"x" * 500))
    monkeypatch.setattr(pc, "_extract_pdf_text", lambda data: "Eidgenössische Postkommission PostCom Strafbescheid " * 20)
    d = s.fetch_decision({"docket_number": "PostCom-413-6/2", "decision_date": "2022-05-09", "title": "t",
                          "pdf_url": "https://www.postcom.admin.ch/dam/de/sd-web/x/a.pdf", "decision_type": "Strafbescheid"})
    assert d is not None and d.decision_type == "Strafbescheid"


def test_number_patterns_cover_the_title_forms():
    assert pc.NUMBER_IN_TITLE.search("Verfügung 1 / 2013 betr. Nettokosten").groups() == ("1", "2013")
    assert pc.NUMBER_IN_TITLE.search("Décision no.10/2014 concernant").groups() == ("10", "2014")
    assert pc.NUMBER_IN_TITLE.search("13.5.2026 Verfügung 6 2026 betreffend").groups() == ("6", "2026")
    assert pc.NUMBER_IN_TITLE.search("Verfügung Nr. 7/2014 betreffend").groups() == ("7", "2014")
    assert pc.NUMBER_IN_TITLE.search("Verfügung vom 13. Mai 2026") is None
    assert pc.NUMBER_WITHOUT_YEAR.search("Verfügung 16 betreffend Harmonisierung LZM").group(1) == "16"
    assert pc._kind("30.01.2026 - Liste Dienstleistungen der Grundversorgung 2026") == "liste"
    assert pc._kind("Verfügung Nr. 7/2014 betreffend", "Dienstleistungen der Grundversorgung 2014 ...") == "liste"
    assert pc._kind("21.03.2013 - Beilage zur Verfügung Nr. 2 / 2013") == "beilage"
    # held 26/2023 annex: extracted text starts "Anbietende\nGesellschaft" (line break, not a space)
    assert pc._kind("Verfügung 26 2023 betreffend Grundversorgung beilage", "Anbietende\nGesellschaft Segment") == "liste"


def test_beilage_tile_matches_the_liste_stored_for_the_same_decision(tmp_path, monkeypatch):
    """Live check 2026-09-15: the tile "Beilage zur Verfügung 05/2025" is the document
    production stores as the Liste of 5/2025 (id VFG-05-2025-beilage, Liste text)."""
    tile = ('<h2>2025</h2><a class="download-item" href="https://www.postcom.admin.ch/dam/de/sd-web/x/Beilage.pdf">'
            '<h4 class="download-item__title">31.01.2025 - Beilage zur Verfügung 05/2025 betreffend Grundversorgung</h4></a>')
    held = [{"decision_id": "postcom_VFG-05-2025-beilage", "decision_date": "2025-01-30",
             "title": "Verfügung 05 2025 betreffend Grungversorgung Beilage",
             "full_text": "Dienstleistungen der Grundversorgung 2025 Anbietende Gesellschaft ...",
             "pdf_url": "https://www.postcom.admin.ch/inhalte/PDF/Verfuegungen/"
                        "VFG_5_2025_PostCom_Beilage_Liste_DL_Grundversorgung2025_20250131.pdf"}]
    s, _ = _scraper(tmp_path, monkeypatch, shard_rows=held,
                    pages={VERFUEGUNGEN_URL: f"<html><body>{tile}</body></html>", STRAFBESCHEIDE_URL: STRAF})
    dockets = {x["docket_number"] for x in s.discover_new()}
    assert not any(d.startswith("VFG-5-2025") for d in dockets)
    assert STRAF_DOCKETS <= dockets
