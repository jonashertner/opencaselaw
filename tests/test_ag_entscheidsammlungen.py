"""scrapers/cantonal/ag_entscheidsammlungen.py (offline).

Fixtures read 2026-11-08: a sample of the ag.ch doctable answer for the BVU
Entscheidsammlung (the mislinked Stützmauer pair, the entry without a
document, the three 1997-03-04 "Planbeschwerde" excerpts' neighbours, one of
the nine 1991-93 rulings entscheidsuche never held) and the Grundbuch
decisions table. PDF texts are short excerpts of the real extractions, so no
PDF backend is involved (CI and prod extract with different libraries).
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scrapers.cantonal import ag_entscheidsammlungen as ag  # noqa: E402

FIX = Path(__file__).parent / "fixtures"
DOCTABLE = json.loads((FIX / "ag_bvu_doctable_20261108.json").read_text(encoding="utf-8"))
GB_HTML = (FIX / "ag_grundbuch_entscheide_20261108.html").read_text(encoding="utf-8")

# Older form layout: the field lines come last; the body quotes a statute's date.
FORM_TEXT = (
    "Bereits ein Verstoss gegen das Vorsorgeprinzip löst die Sanierungspflicht nach Art. 16\n"
    "USG aus; als lex specialis geht diese Bestimmung der allgemeinen Widerrufsregelung vor.\n"
    "kein\n\"3.\nc) (...)\naa) Gemäss Art. 16 Abs. 1 des Bundesgesetzes über den Umweltschutz (USG) "
    "vom 7. Oktober 1983 müssen Anlagen,\ndie den Vorschriften dieses Gesetzes nicht genügen, saniert\n"
    "werden. (vgl.\nUrteil des Verwaltungsgerichtes vom 25. September 1978, ZBl 1979 S. 164; RRB Nr. 109 "
    "vom 18. Januar 1988 i.S. C.A.).\n"
    "Aus den Erwägungen\nSachverhalt\nEntscheid des Verwaltungsgerichts (III/7) vom 21.02.1994 \n"
    "Umweltschutzrecht\n")
# Newer layout: header before the Erwägungen, month split across lines.
HEAD_TEXT = (
    "Gebäudelänge; Ausnützungsziffer\n– Ein Anbau, dessen Dach als Terrasse genutzt wird, wird in die "
    "Berechnung\nder Gebäudelänge einbezogen.\n"
    "Entscheid des Departements Bau, Verkehr und Umwelt (EBVU) vom 13. De-\nzember 2011 (BVURA.11.722)\n"
    "Aus den Erwägungen\n6.\na)\nGemäss § 11 ABauV vom 23. Februar 2011 wird die Gebäudelänge ...\n"
    "Stichwörter: Gebäudelänge, Ausnützungsziffer, Anbaute")
HEAD_VOM_NEXT_LINE = (
    "Fassadenhöhe (gemäss IVHB)\nDie Fassadenhöhe eines begehbaren Flachdachs wird oberkant der Brüs-\n"
    "tung gemessen.\nAus dem Entscheid des Departements Bau, Verkehr und Umwelt (EBVU)\n"
    "vom 6. Oktober 2014 (BVURA.14.453).\nAus den Erwägungen\n4.4.2\nDie zulässige Fassadenhöhe ...")
FORM_WITH_TRAILER = ("...gesprochen werden kann.\nEntscheid des Regierungsrats (Art. Nr. 445) vom "
                     "06.03.1996 S. 7")


def test_form_line_wins_over_body_dates():
    assert ag.ruling_header(FORM_TEXT)[:2] == ("1994-02-21", "form")


def test_citation_line_with_two_dates_is_not_the_form_field():
    text = ("(vgl.\nUrteil des Verwaltungsgerichtes vom 25. September 1978, ZBl 1979 S. 164; RRB Nr. 109 "
            "vom 18. Januar 1988 i.S. C.A.).\nEntscheid des Regierungsrats (Art. Nr. 837) vom 20.04.1994")
    assert ag.ruling_header(text)[0] == "1994-04-20"


def test_header_with_hyphenated_month_and_case_number():
    d, how, line = ag.ruling_header(HEAD_TEXT)
    assert (d, how) == ("2011-12-13", "head")
    assert ag.case_number(line, HEAD_TEXT) == "BVURA.11.722"
    assert ag.keywords(HEAD_TEXT) == "Gebäudelänge, Ausnützungsziffer, Anbaute"


def test_header_with_vom_on_the_next_line():
    assert ag.ruling_header(HEAD_VOM_NEXT_LINE)[0] == "2014-10-06"


def test_form_field_with_trailing_page_reference():
    assert ag.ruling_header(FORM_WITH_TRAILER)[0] == "1996-03-06"


def test_no_header():
    assert ag.ruling_header("Ein Text ohne Kopfzeile vom 3. Mai 1990, nur Erwägungen.") == (None, None, None)


# ── BVU listing ──────────────────────────────────────────────────────────

def test_doctable_query_shape():
    q = ag.doctable_query(2)
    assert q["pagination"] == {"page": 2, "size": 100}
    assert q["filter"]["field"] == "categories" and len(q["filter"]["values"]) == 5


def test_grouping_by_pdf_and_entry_without_document():
    stubs, warnings = ag.group_bvu_entries(DOCTABLE["files"])
    by = {s["docket_number"]: s for s in stubs}
    assert "1993-12-03-missbraeuchliche-prozessfuehrung" in by
    assert len(by["2012-07-26-stuetzmauer-ebvu"]["entries"]) == 2
    assert any("without a document" in w and "Parteientschädigung" in w for w in warnings)
    assert all(s["pdf_url"].startswith(ag.AG_HOST + ag.BVU_MEDIA_PREFIX) for s in stubs)


def test_mislinked_entry_is_not_attached(tmp_path, caplog):
    stubs, _ = ag.group_bvu_entries(DOCTABLE["files"])
    st = next(s for s in stubs if s["docket_number"] == "2012-07-26-stuetzmauer-ebvu")
    text = ("Stützmauer; Schadenersatz\nStützmauern müssen bezüglich Höhe und Abstand als Ganzes beurteilt "
            "werden. Die zulässige Höhe ...\nEntscheid des Departements Bau, Verkehr und Umwelt vom 26. Juli 2012\n"
            "(BVURA.12.102)\nAus den Erwägungen\n...")
    s = ag.AGBaugesetzgebungScraper(state_dir=tmp_path)
    with caplog.at_level(logging.WARNING):
        d = s.build_decision(st, text)
    assert d.title == "Stützmauer; Schadenersatz"
    assert "Submission" not in (d.regeste or "") and "Qualitätsmanagement" not in (d.regeste or "")
    assert d.decision_date.isoformat() == "2012-07-26"
    assert d.chamber == "Departement Bau, Verkehr und Umwelt"
    assert d.docket_number_2 == "BVURA.12.102"
    assert "not attached" in caplog.text


def test_build_decision_for_a_ruling_es_never_held(tmp_path):
    stubs, _ = ag.group_bvu_entries(DOCTABLE["files"])
    st = next(s for s in stubs if s["docket_number"] == "1993-12-03-missbraeuchliche-prozessfuehrung")
    text = ("Missbräuchliche Prozessführung\n... Erwägungen ...\nAus den Erwägungen\nSachverhalt\n"
            "Entscheid des Verwaltungsgerichts (III/80) vom 03.12.1993 in Sachen X.\n")
    d = ag.AGBaugesetzgebungScraper(state_dir=tmp_path).build_decision(st, text)
    assert d.decision_id == "ag_baugesetzgebung_1993-12-03-missbraeuchliche-prozessfuehrung"
    assert d.docket_number == "1993-12-03-missbraeuchliche-prozessfuehrung"
    assert d.decision_date.isoformat() == "1993-12-03"
    assert d.chamber == "Verwaltungsgericht" and d.canton == "AG" and d.language == "de"
    assert d.regeste and d.collection.startswith("Entscheidsammlung Baugesetzgebung")
    assert d.source_url == d.pdf_url == st["pdf_url"]


def test_baudepartement_named_as_printed(tmp_path):
    st = {"docket_number": "1994-01-18-x", "pdf_url": ag.AG_HOST + ag.BVU_MEDIA_PREFIX + "1994-01-18-x.pdf",
          "portal_date": "1994-01-18",
          "entries": [{"id": "1", "title": "X", "teaser": None, "date": "1994-01-18",
                       "chamber": "Departement Bau, Verkehr und Umwelt"}]}
    d = ag.AGBaugesetzgebungScraper(state_dir=tmp_path).build_decision(
        st, "Text\nEntscheid des Baudepartements vom 18.01.1994\n")
    assert d.chamber == "Baudepartement"


def test_portal_date_when_the_pdf_prints_none(tmp_path):
    st = {"docket_number": "2000-11-08-x", "pdf_url": "https://www.ag.ch/x.pdf", "portal_date": "2000-11-08",
          "entries": [{"id": "1", "title": "X", "teaser": None, "date": "2000-11-08", "chamber": "Regierungsrat"}]}
    d = ag.AGBaugesetzgebungScraper(state_dir=tmp_path).build_decision(st, "Erwägungen ohne Kopf. " * 20)
    assert d.decision_date.isoformat() == "2000-11-08"


class _Resp:
    def __init__(self, data=None, text="", content=b""):
        self._data, self.text, self.content = data, text, content

    def json(self):
        return self._data


def test_list_entries_pages_through_the_api(tmp_path):
    files = [{"id": str(i), "title": f"T{i}", "documentDatetime": "2001-01-01T00:00:00Z",
              "categories": [next(iter(ag.BVU_CATEGORIES))],
              "targetUrls": [{"url": f"{ag.BVU_MEDIA_PREFIX}2001-01-01-t{i}.pdf"}]} for i in range(150)]
    pages = {0: files[:100], 1: files[100:]}
    seen = []

    def post(url, json=None, **kw):
        seen.append(json["pagination"]["page"])
        p = json["pagination"]["page"]
        return _Resp({"files": pages[p], "control": {"totalPages": 2, "totalElements": 150}})

    s = ag.AGBaugesetzgebungScraper(state_dir=tmp_path)
    s.post = post
    stubs = list(s.discover_new())
    assert seen == [0, 1] and len(stubs) == 150 and s.portal_count == 150


def test_listing_failures_raise(tmp_path):
    s = ag.AGBaugesetzgebungScraper(state_dir=tmp_path)
    s.post = lambda url, **kw: _Resp({"files": [], "control": {"totalPages": 0, "totalElements": 0}})
    with pytest.raises(RuntimeError):
        list(s.discover_new())
    s.post = lambda url, **kw: _Resp({"title": "Internal Server Error", "status": 500})
    with pytest.raises(RuntimeError):
        list(s.discover_new())
    # fewer entries than the API's own total
    s.post = lambda url, **kw: _Resp({"files": DOCTABLE["files"][:2],
                                      "control": {"totalPages": 1, "totalElements": 9}})
    with pytest.raises(RuntimeError):
        list(s.discover_new())


def test_known_ids_are_skipped(tmp_path):
    s = ag.AGBaugesetzgebungScraper(state_dir=tmp_path)
    s.state.mark_scraped("ag_baugesetzgebung_1993-12-03-missbraeuchliche-prozessfuehrung")
    s.post = lambda url, **kw: _Resp(DOCTABLE)
    assert "1993-12-03-missbraeuchliche-prozessfuehrung" not in {st["docket_number"] for st in s.discover_new()}


# ── Grundbuch (ag_weitere) ───────────────────────────────────────────────

def test_grundbuch_table():
    rows = ag.parse_grundbuch_page(GB_HTML)
    assert len(rows) == 10
    by = {r["docket_number"]: r for r in rows}
    r = by["20090219-grundbuchabgaben-kauf-werkpreis-2"]
    assert r["portal_date"] == "2009-02-19"
    assert r["pdf_url"] == ag.AG_HOST + ag.GRUNDBUCH_MEDIA_PREFIX + "20090219-grundbuchabgaben-kauf-werkpreis-2.pdf"
    assert "(PDF," not in r["title"]
    assert "20090219-grundbuchabgaben-kauf-werkpreis-1" in by    # same date, another judgment


def test_grundbuch_chamber():
    assert ag.grundbuch_chamber("Urteil vom 11. Dezember 2002 des Verwaltungsgerichts des Kantons Aargau gegen "
                                "den Entscheid des Departements des Innern") == "Verwaltungsgericht"
    assert ag.grundbuch_chamber("Verfügung vom 23. Februar 2004 des Departements des Innern") == \
        "Departement des Innern"
    assert ag.grundbuch_chamber("Grundbuchbeschwerde", "…; Verfügung vom 12. September 2002 des "
                                "Departementes des Innern; Gutheissung") == "Departement des Innern"
    assert ag.grundbuch_chamber("Aufhebung der Wohnsitzpflicht; Regierungsratsbeschluss vom 05.12.2001") == \
        "Regierungsrat"


def test_grundbuch_discovery_and_decision(tmp_path):
    s = ag.AGWeitereScraper(state_dir=tmp_path)
    s.get = lambda url, **kw: _Resp(text=GB_HTML)
    stubs = list(s.discover_new())
    assert len(stubs) == 10 and s.portal_count == 10
    st = next(x for x in stubs if x["docket_number"] == "20151029-grundbuchabgaben-wert-gesamteigentumsanteile")
    d = s.build_decision(st, "Urteil des aargauischen Verwaltungsgerichts vom 29. Oktober 2015 ... " * 5)
    assert d.decision_id == "ag_weitere_20151029-grundbuchabgaben-wert-gesamteigentumsanteile"
    assert d.decision_date.isoformat() == "2015-10-29" and d.chamber == "Verwaltungsgericht"


def test_grundbuch_empty_page_raises(tmp_path):
    s = ag.AGWeitereScraper(state_dir=tmp_path)
    s.get = lambda url, **kw: _Resp(text="<html><body></body></html>")
    with pytest.raises(RuntimeError):
        list(s.discover_new())


# ── wiring ───────────────────────────────────────────────────────────────

def test_registered_and_es_spiders_retired():
    from run_scraper import SCRAPERS
    import scripts.check_scraper_freshness as f
    from scrapers.entscheidsuche_ingest import SPIDER_MAP
    assert SCRAPERS["ag_baugesetzgebung"] == ("scrapers.cantonal.ag_entscheidsammlungen",
                                              "AGBaugesetzgebungScraper")
    assert SCRAPERS["ag_weitere"] == ("scrapers.cantonal.ag_entscheidsammlungen", "AGWeitereScraper")
    assert not ({"ag_baugesetzgebung", "ag_weitere"} & f.ENTSCHEIDSUCHE_ONLY)
    assert "AG_Baugesetzgebung" not in SPIDER_MAP and "AG_Weitere" not in SPIDER_MAP
    assert not ({"ag_baugesetzgebung", "ag_weitere"} & {v[0] for v in SPIDER_MAP.values()})
