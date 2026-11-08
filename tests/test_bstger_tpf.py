"""Bundesstrafgericht official collection (TPF) in scrapers/bstger.py.

Fixtures captured 2026-11-08 from bstger.weblaw.ch: one listing page of the
rulingType "Entscheide der Amtlichen Sammlung" (TPF 2024 194, TPF 2004 16,
TPF 2004 48; trimmed to the fields the scraper reads) and the PyMuPDF text of
the PDFs of TPF 2024 194, TPF 2004 16 and TPF 2024 205. No network.
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

FIX = REPO / "tests" / "fixtures" / "bstger_tpf"

from scrapers import bstger as b  # noqa: E402
import mcp_server as m  # noqa: E402


def _text(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


@pytest.fixture
def scraper(tmp_path, monkeypatch):
    def refuse(self, url, **_):
        raise AssertionError(f"network access in a test: {url}")
    monkeypatch.setattr(b.BStGerScraper, "get", refuse)
    monkeypatch.setattr(b.BStGerScraper, "post", refuse)
    return b.BStGerScraper(state_dir=tmp_path)


def _listing():
    return json.loads((FIX / "listing_page.json").read_text(encoding="utf-8"))


# ── reference and id ─────────────────────────────────────────────────────

def test_reference_normalises_the_portal_title():
    assert b.tpf_reference("TPF 2024  205") == "TPF 2024 205"
    assert b.tpf_reference("TPF 2004   6") == "TPF 2004 6"
    assert b.tpf_reference("BB.2021.123") is None
    assert b.tpf_reference("TPF201126") is None


def test_id_matches_the_rows_already_served():
    # The 583 held rows (2004-2021) are served as bstger_TPF_<year>_<page>;
    # the direct row must take the same id to replace them, not sit beside them.
    assert b.tpf_decision_id("TPF 2004 12") == "bstger_TPF_2004_12"


# ── entry cut, date, underlying docket ───────────────────────────────────

def test_entry_cut_drops_previous_and_next_entry():
    raw = _text("TPF-2024-194.txt")
    e = b.tpf_entry(raw, "TPF 2024 194", ["BG.2024.60"])
    assert e["heading_found"]
    assert e["decision_date"] == "2024-12-11"
    assert e["docket"] == "BG.2024.60"
    assert e["text"].startswith("29. Auszug aus dem Beschluss der Beschwerdekammer")
    # The extract opens with the end of TPF 2024 187 and ends with the start of
    # TPF 2024 205 (30. Auszug ... CA.2024.34); neither belongs to this entry.
    assert "30. Auszug" not in e["text"]
    assert "CA.2024.34" not in e["text"]
    assert "Anders als A. AG und B. AG geltend machen" not in e["text"]
    assert not e["text"].rstrip().endswith("TPF 2024 205")
    assert len(e["text"]) < len(raw)


def test_entry_heading_split_one_word_per_line_italian():
    # The 2004 PDFs put every heading word on its own line.
    e = b.tpf_entry(_text("TPF-2004-16.txt"), "TPF 2004 16", [])
    assert e["decision_date"] == "2004-05-27"
    assert e["docket"] == "BK_B 023/04"
    assert e["text"].startswith("4. Estratto della Sentenza")
    assert "5. Auszug" not in e["text"]
    assert "TK 045/04" not in e["text"]


def test_entry_last_in_volume_runs_to_the_end():
    e = b.tpf_entry(_text("TPF-2024-205.txt"), "TPF 2024 205", ["CA.2024.34"])
    assert e["decision_date"] == "2024-12-16"
    assert e["docket"] == "CA.2024.34"
    assert e["text"].rstrip().endswith("CA.2024.34 in \nRechtskraft erwachsen ist.")


def test_entry_dossier_picks_the_heading_when_not_first():
    # With the previous entry's heading also on the first page, the portal's
    # dossier decides which heading is this entry's.
    raw = _text("TPF-2024-194.txt")
    e = b.tpf_entry(raw, "TPF 2024 205", ["CA.2024.34"])
    assert e["docket"] == "CA.2024.34"
    assert e["decision_date"] == "2024-12-16"
    assert e["text"].startswith("30. Auszug aus dem Urteil der Berufungskammer")


@pytest.mark.parametrize("heading, expected", [
    # The ordinal suffix once ate the "o" of "octobre" (TPF 2006 313).
    ("84. Extrait de l’arrêt de la Cour des plaintes dans la cause A. contre Minis-\n"
     "tère public de la Confédération, Office des juges d’instruction fédéraux du\n"
     "25 octobre 2006 (BB.2006.61)\nDétention préventive",
     ("2006-10-25", "BB.2006.61")),
    # Joined rulings, two dates (TPF 2005 123): the first, like the first docket.
    ("33. Auszug aus den Entscheiden der Strafkammer in Sachen Bundesanwalt-\n"
     "schaft gegen A., B. und C. vom 2. und 14. Juni 2005 (SK.2004.14 und\nSK.2004.15)\n",
     ("2005-06-02", "SK.2004.14 und SK.2004.15")),
    # Month hyphenated at the line end (TPF 2006 240).
    ("63. Auszug aus dem Entscheid der Beschwerdekammer in Sachen A. gegen\n"
     "Bundesanwaltschaft, Eidgenössisches Untersuchungsrichteramt vom 8. Feb-\n"
     "ruar 2006 (BB.2005.132)\nAkteneinsicht",
     ("2006-02-08", "BB.2005.132")),
    ("5. Arrêt du 1er mars 2022 (RR.2021.245) Loi du 20 mars 1981", ("2022-03-01", "RR.2021.245")),
    ("12. Estratto della sentenza dell'8 giugno 2010 (RR.2010.1)", ("2010-06-08", "RR.2010.1")),
    # A party parenthesis is not the docket.
    ("7. Auszug aus dem Beschluss in Sachen A. AG (vormals B.) vom 3. Mai 2012 (BB.2012.3)",
     ("2012-05-03", "BB.2012.3")),
])
def test_heading_forms(heading, expected):
    assert b._parse_tpf_heading(heading) == expected


def test_entry_reference_line_beats_first_heading():
    # A one-page previous entry starts and ends on this entry's first page.
    raw = ("TPF 2010 5\n5\nend of entry 2.\n"
           "TPF 2010 5\n3. Auszug aus dem Entscheid der Beschwerdekammer vom 1. März 2010 (BB.2010.1)\n"
           "Short entry.\n"
           "TPF 2010 6\n4. Auszug aus dem Entscheid der Beschwerdekammer vom 2. April 2010 (BB.2010.2)\n"
           "Our entry.\n"
           "TPF 2010 9\n5. Auszug aus dem Entscheid der Beschwerdekammer vom 3. Mai 2010 (BB.2010.3)\n")
    e = b.tpf_entry(raw, "TPF 2010 6", [])
    assert e["docket"] == "BB.2010.2" and e["decision_date"] == "2010-04-02"
    assert e["text"] == ("4. Auszug aus dem Entscheid der Beschwerdekammer vom 2. April 2010 (BB.2010.2)\n"
                         "Our entry.")


def test_entry_without_heading_keeps_text_and_no_date():
    e = b.tpf_entry("Some page text without a heading.", "TPF 2010 1", [])
    assert e == {"text": "Some page text without a heading.", "decision_date": None,
                 "docket": None, "heading_found": False}


def test_entry_date_outside_volume_is_dropped():
    raw = "TPF 2010 5\n5. Auszug aus dem Entscheid der Beschwerdekammer vom 3. Mai 1999 (BB.1999.1)\nText."
    e = b.tpf_entry(raw, "TPF 2010 5", [])
    assert e["heading_found"] and e["decision_date"] is None


def test_regeste_date_is_not_the_decision_date():
    raw = ("TPF 2012 7\n7. Auszug aus dem Beschluss der Beschwerdekammer in Sachen A. "
           "(BB.2012.3)\nVerordnung vom 3. März 2011 über ...\n")
    e = b.tpf_entry(raw, "TPF 2012 7", [])
    assert e["decision_date"] is None


# ── listing and decision ─────────────────────────────────────────────────

def test_listing_parses_stubs(scraper, monkeypatch):
    calls = []

    def fake_post(self, url, **kw):
        calls.append(kw["json"])
        return SimpleNamespace(json=_listing)
    monkeypatch.setattr(b.BStGerScraper, "post", fake_post)
    stubs, total = scraper.list_tpf_year(2024)
    assert total == 3 and len(stubs) == 3
    body = calls[0]
    assert body["metadataKeywordsMap"] == {"rulingType": b.TPF_RULING_TYPE, "year": "2024"}
    s = {x["docket_number"]: x for x in stubs}
    assert s["TPF 2024 194"]["dossiers"] == ["BG.2024.60"]
    assert s["TPF 2024 194"]["decision_id"] == "bstger_TPF_2024_194"
    assert s["TPF 2024 194"]["language"] == "de"
    assert s["TPF 2004 16"]["dossiers"] == []
    assert s["TPF 2004 16"]["language"] == "it"
    assert s["TPF 2004 48"]["dossiers"] == ["BK_B 153/04"]
    assert s["TPF 2024 194"]["pdf_url"].endswith("/api/getDocumentContent/b1b26691-8398-3df0-94bb-aee17d08c8b0")


def test_listing_paginates(scraper, monkeypatch):
    page = _listing()
    docs = page["data"]["documents"]
    pages = [
        {"status": "success", "data": {"totalNumberOfDocuments": 3, "hasMoreResults": True, "documents": docs[:2]}},
        {"status": "success", "data": {"totalNumberOfDocuments": 3, "hasMoreResults": False, "documents": docs[2:]}},
    ]
    offsets = []

    def fake_post(self, url, **kw):
        offsets.append(kw["json"].get("from", 0))
        p = pages[len(offsets) - 1]
        return SimpleNamespace(json=lambda: p)
    monkeypatch.setattr(b.BStGerScraper, "post", fake_post)
    stubs, total = scraper.list_tpf_year(2004)
    assert offsets == [0, 2] and len(stubs) == 3


def test_listing_error_is_loud(scraper, monkeypatch):
    bad = {"status": "success", "data": {"status": 400, "detail": "Invalid request content."}}
    monkeypatch.setattr(b.BStGerScraper, "post", lambda self, url, **kw: SimpleNamespace(json=lambda: bad))
    with pytest.raises(RuntimeError):
        scraper.list_tpf_year(2024)


def test_discover_daily_lists_recent_volumes_only(scraper, monkeypatch):
    years = []
    monkeypatch.setattr(b.BStGerScraper, "_discover_windowed", lambda self, since: iter(()))
    monkeypatch.setattr(b.BStGerScraper, "list_tpf_year",
                        lambda self, y: (years.append(y), ([], 1))[1])
    list(scraper.discover_new())
    this = date.today().year
    assert years == list(range(this - b.TPF_DAILY_YEARS + 1, this + 1))
    years.clear()
    list(scraper.discover_new(date(2004, 1, 1)))
    assert years[0] == 2004 and years[-1] == this


def test_tpf_only_skips_windowed_pass(scraper, monkeypatch):
    monkeypatch.setenv(b.TPF_ONLY_ENV, "1")

    def boom(self, since):
        raise AssertionError("windowed pass ran")
    monkeypatch.setattr(b.BStGerScraper, "_discover_windowed", boom)
    monkeypatch.setattr(b.BStGerScraper, "list_tpf_year", lambda self, y: ([], 1))
    list(scraper.discover_new(date(2022, 1, 1)))


def test_known_entries_are_not_rediscovered(scraper, monkeypatch):
    monkeypatch.setattr(b.BStGerScraper, "_discover_windowed", lambda self, since: iter(()))
    monkeypatch.setattr(b.BStGerScraper, "post", lambda self, url, **kw: SimpleNamespace(json=_listing))
    scraper.state.mark_scraped("bstger_TPF_2024_194")
    found = [s["decision_id"] for s in scraper._discover_tpf(2024, 2024)]
    assert found == ["bstger_TPF_2004_16", "bstger_TPF_2004_48"]


def test_fetch_builds_the_decision(scraper, monkeypatch):
    monkeypatch.setattr(b.BStGerScraper, "post", lambda self, url, **kw: SimpleNamespace(json=_listing))
    stub = next(s for s in scraper.list_tpf_year(2024)[0] if s["docket_number"] == "TPF 2024 194")
    monkeypatch.setattr(b.BStGerScraper, "get",
                        lambda self, url, **kw: SimpleNamespace(content=b"%PDF-1.7 fixture", status_code=200))
    monkeypatch.setattr(b, "_extract_pdf_text", lambda data: _text("TPF-2024-194.txt"))
    d = scraper.fetch_decision(stub)
    assert d.decision_id == "bstger_TPF_2024_194"
    assert d.court == "bstger" and d.canton == "CH"
    assert d.docket_number == "TPF 2024 194"
    assert d.docket_number_2 == "BG.2024.60"
    assert d.decision_date == date(2024, 12, 11)
    assert d.language == "de"
    assert d.regeste.startswith("Gerichtsstandskonflikt; implizite oder informelle Erledigung")
    assert "Conflit de fors" in d.regeste
    assert d.regeste.count("Gerichtsstandskonflikt; implizite") == 1  # 4th (en) copy folded
    assert d.full_text.startswith("29. Auszug aus dem Beschluss")
    assert d.pdf_url == d.source_url == f"{b.CONTENT_URL}b1b26691-8398-3df0-94bb-aee17d08c8b0"
    assert d.publication_date is None  # the portal gives none for TPF entries


def test_fetch_non_pdf_returns_none(scraper, monkeypatch):
    monkeypatch.setattr(b.BStGerScraper, "post", lambda self, url, **kw: SimpleNamespace(json=_listing))
    stub = scraper.list_tpf_year(2024)[0][0]
    monkeypatch.setattr(b.BStGerScraper, "get",
                        lambda self, url, **kw: SimpleNamespace(content=b"<html>", status_code=200))
    assert scraper.fetch_decision(stub) is None


def test_fetch_falls_back_to_portal_dossier(scraper, monkeypatch):
    stub = {"kind": "tpf", "decision_id": "bstger_TPF_2015_3", "docket_number": "TPF 2015 3",
            "doc_id": "x", "language": "fr", "headnote": "a;;b", "dossiers": ["RR.2014.1"],
            "pdf_url": f"{b.CONTENT_URL}x"}
    monkeypatch.setattr(b.BStGerScraper, "get",
                        lambda self, url, **kw: SimpleNamespace(content=b"%PDF-", status_code=200))
    monkeypatch.setattr(b, "_extract_pdf_text", lambda data: "page text, no heading")
    d = scraper.fetch_decision(stub)
    assert d.docket_number_2 == "RR.2014.1"
    assert d.decision_date is None
    assert d.regeste == "a\n\nb"


# ── citation string ──────────────────────────────────────────────────────

def test_cite_tpf_is_the_collection_reference():
    row = {"court": "bstger", "decision_id": "bstger_TPF_2004_12",
           "docket_number": "TPF 2004 12", "decision_date": "2004-05-26"}
    c = m._build_citation_strings(row)
    assert c["citation_string_de"] == "TPF 2004 12"
    assert c["citation_string_fr"] == "TPF 2004 12"
    assert c["citation_string_it"] == "TPF 2004 12"
    assert c["canonical_url"].endswith("/entscheid/bstger_TPF_2004_12")
    p = m._build_citation_strings(row, pinpoint="2.1")
    assert p["citation_string_de"] == "TPF 2004 12, E. 2.1"
    assert p["citation_string_fr"] == "TPF 2004 12, consid. 2.1"


def test_cite_ordinary_bstger_ruling_unchanged():
    row = {"court": "bstger", "decision_id": "bstger_BG.2024.60",
           "docket_number": "BG.2024.60", "decision_date": "2024-12-11"}
    c = m._build_citation_strings(row)
    assert c["citation_string_de"] == "BStGer BG.2024.60 vom 11. Dezember 2024"
    assert c["citation_string_fr"] == "TPF BG.2024.60 du 11 décembre 2024"


def test_entry_number_on_its_own_line():
    # TPF 2009 3: "2.\nAuszug\naus\ndem ..." (number and words on separate lines).
    raw = ("3\nTPF 2009 3\n\n2.\nAuszug\naus\ndem\nEntscheid\nder\nStrafkammer\nin\nSachen\n"
           "Bundesanwaltschaft gegen A. vom 16. September 2008 (SK.2007.18)\n\nAnspruch auf Übersetzung.\n")
    e = b.tpf_entry(raw, "TPF 2009 3", ["SK.2007.18"])
    assert e["decision_date"] == "2008-09-16" and e["docket"] == "SK.2007.18"
    assert e["text"].startswith("2.\nAuszug")


@pytest.mark.parametrize("heading, expected", [
    # TPF 2012 70: two dates with their own months, one year.
    ("14. Auszug aus dem Urteil der Strafkammer in Sachen Bundesanwaltschaft\nund EMS CHEMIE AG "
     "gegen A. vom 6. Juni und 23. Juli 2012\n(SK.2012.15)\n", ("2012-06-06", "SK.2012.15")),
    # TPF 2012 97: the volume misprints "juillet".
    ("18. Extrait de la décision de la Cour des plaintes dans la cause A. contre\nMinistère public "
     "de la Confédération, B. et C. du 25 juilet 2012\n(BB.2011.140)\n", ("2012-07-25", "BB.2011.140")),
])
def test_heading_forms_2012(heading, expected):
    assert b._parse_tpf_heading(heading) == expected


def test_month_table_is_complete():
    assert sorted(set(b._TPF_MONTHS.values())) == list(range(1, 13))
    assert b._TPF_MONTHS["août"] == 8 and b._TPF_MONTHS["dicembre"] == 12


def test_case_name_is_not_part_of_the_docket():
    h = ("1. Auszug aus dem Urteil der Strafkammer in Sachen Bundesanwaltschaft gegen A. "
         "vom 21. März 2012 (SK.2011.5; «Montecristo»)\n")
    assert b._parse_tpf_heading(h) == ("2012-03-21", "SK.2011.5")


def test_marked_heading_with_unlisted_ruling_type():
    # TPF 2008 180: "Präsidialentscheid" is no listed ruling type; the reference
    # line above it marks it. The next entry's heading must not be taken.
    raw = ("TPF 2008 180 \n \n180 \nend of the previous entry.\n"
           "TPF 2008 180 \n  \n44. Präsidialentscheid der Strafkammer in Sachen Bundesanwaltschaft ge-\n"
           "gen A. vom 9. Dezember 2008 (SN.2008.49) \n  \nBeschlagnahme.\n"
           "TPF 2008 180 \n \n182 \n1. Mit Einreichung der Anklageschrift ...\n"
           "TPF 2008 183 \n \n45. Auszug aus dem Entscheid der I. Beschwerdekammer vom\n"
           "11. Dezember 2008 (BG.2008.24)\n")
    e = b.tpf_entry(raw, "TPF 2008 180", ["SN.2008.49"])
    assert (e["decision_date"], e["docket"]) == ("2008-12-09", "SN.2008.49")
    assert e["text"].startswith("44. Präsidialentscheid")
    assert "45. Auszug" not in e["text"] and "1. Mit Einreichung" in e["text"]
    # Without its own heading the entry stays undated, never the neighbour's date.
    e2 = b.tpf_entry(raw.replace("44. Präsidialentscheid", "Präsidialentscheid"), "TPF 2008 180", [])
    assert e2["decision_date"] is None and e2["docket"] is None


# ── retiring the entscheidsuche copies ───────────────────────────────────

import scripts.bstger_retire_es_tpf as retire  # noqa: E402


def _row(court, docket, did=None, source=None):
    r = {"decision_id": did or f"{court}_{docket.replace(' ', '_')}", "court": court, "docket_number": docket}
    if source:
        r["source"] = source
    return json.dumps(r) + "\n"


def test_retire_drops_only_covered_tpf_rows(tmp_path):
    (tmp_path / "bstger.jsonl").write_text(
        _row("bstger", "TPF 2004 12") + _row("bstger", "TPF 2011 26") + _row("bstger", "BB.2021.1"))
    es = (_row("bstger", "TPF 2004 12", source="entscheidsuche")
          + _row("bstger", "TPF201126", did="bstger_TPF201126", source="entscheidsuche")
          + _row("bstger", "TPF 2011 26", source="entscheidsuche")
          + _row("bstger", "BK_B 023/04", source="entscheidsuche"))
    (tmp_path / "es_bstger.jsonl").write_text(es)
    assert retire.main(["--dir", str(tmp_path)]) == 0
    assert (tmp_path / "es_bstger.jsonl").read_text() == es  # dry run writes nothing
    assert retire.main(["--dir", str(tmp_path), "--apply"]) == 0
    left = [json.loads(l)["docket_number"] for l in (tmp_path / "es_bstger.jsonl").read_text().splitlines()]
    assert left == ["BK_B 023/04"]
    assert (tmp_path / ("es_bstger.jsonl" + retire.BACKUP_SUFFIX)).read_text() == es


def test_retire_refuses_when_an_entry_would_be_lost(tmp_path):
    (tmp_path / "bstger.jsonl").write_text(_row("bstger", "TPF 2004 12"))
    es = _row("bstger", "TPF 2004 12") + _row("bstger", "TPF 2005 61")
    (tmp_path / "es_bstger.jsonl").write_text(es)
    assert retire.main(["--dir", str(tmp_path), "--apply"]) == 1
    assert (tmp_path / "es_bstger.jsonl").read_text() == es


def test_retire_key():
    assert retire.tpf_key("TPF 2004 12") == "TPF 2004 12"
    assert retire.tpf_key("TPF201126") == "TPF 2011 26"
    assert retire.tpf_key("BB.2021.12") is None
