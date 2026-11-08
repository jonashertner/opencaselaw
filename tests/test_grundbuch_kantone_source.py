"""Cantonal land-register practice source (2026-11-08): ZH Notariate,
BS Grundbuch-Newsletter, BE Handbuch chapter split, AG Weisung/Kreisschreiben.
Offline: golden HTML fixtures captured from the live pages on 2026-11-08."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scrapers.practice import grundbuch_kantone as gk  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "practice"


def _read(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


# ── ZH: table of contents -> page list ──────────────────────────────────

def test_zh_navtree_paths_drops_tool_pages_and_keeps_order():
    paths = gk.zh_navtree_paths(_read("grundbuch_zh_inhaltsuebersicht.html"))
    assert "/de/grundbuch/grundpfandrechte/schuldbrief" in paths
    assert "/de/grundbuch/allgemein/stockwerkeigentum" in paths
    # service/tool pages and the TOC page itself are not practice guidance
    assert "/de/grundbuch/auszugsbestellung" not in paths
    assert "/de/grundbuch/elektronische-eigentumsabfrage" not in paths
    assert "/de/grundbuch/inhaltsuebersicht" not in paths
    assert len(paths) == len(set(paths))          # first occurrence wins, no dupes
    assert len(paths) > 90                          # "~100 pages" per the brief


# ── ZH: doc_number disambiguation ───────────────────────────────────────

def test_zh_breadcrumb_disambiguates_pages_sharing_an_h1():
    # "Gläubigerwechsel" is the H1 on two different handbook pages
    # (under Schuldbrief and under Grundpfand-Verschreibung); doc_number
    # must not collapse them (search_practice collapses same doc_number).
    a = gk.zh_breadcrumb("/de/grundbuch/grundpfandrechte/schuldbrief/glaeubigerwechsel")
    b = gk.zh_breadcrumb("/de/grundbuch/grundpfandrechte/grundpfand-verschreibung/glaeubigerwechsel")
    assert a != b
    # "Gebühren" repeats on three pages; a short, generic trailing segment
    # alone would not disambiguate them, the fuller path does
    c = gk.zh_breadcrumb("/de/grundbuch/grundpfandrechte/gebuehren")
    d = gk.zh_breadcrumb("/de/grundbuch/grundpfandrechte/gesetzliche-pfandrechte/"
                         "bauhandwerker-pfandrecht/gebuehren")
    assert c != d
    assert gk.zh_breadcrumb("/de/grundbuch") == "Grundbuch"


# ── ZH: page body extraction ────────────────────────────────────────────

def test_parse_zh_page_accordion_excludes_sidebar_and_related_links():
    parsed = gk.parse_zh_page(_read("grundbuch_zh_schuldbrief.html"))
    assert parsed["title"] == "Schuldbrief"
    body = parsed["body_text"]
    assert "Register-Schuldbrief" in body and "Papier-Schuldbrief" in body
    # the "Weitere Informationen zum Schuldbrief unter:" related-links list
    # and the sidebar (glossary box, download-area) are not body content
    assert "Weitere Informationen zum Schuldbrief" not in body
    assert "Downloads zum Thema" not in body
    assert "Pfandvertrag Errichtung" not in body


def test_parse_zh_page_without_accordion_still_finds_the_single_section():
    parsed = gk.parse_zh_page(_read("grundbuch_zh_grundeigentum.html"))
    assert parsed["title"] == "Grundeigentum"
    assert len(parsed["body_text"]) > 100


# ── ZH: downloads listings ──────────────────────────────────────────────

def test_zh_grundbuch_downloads_twelve_pdfs_forms_flagged():
    rows = gk.zh_download_rows(_read("grundbuch_zh_downloads_grundbuch.html"))
    assert len(rows) == 12
    titles = {r["title"] for r in rows}
    assert "Pfandvertrag Errichtung" in titles
    assert "Praxis bei Gesamtüberbauungen" in titles
    types = {r["title"]: gk.zh_grundbuch_doc_type(r["title"]) for r in rows}
    assert types["Vollmacht"] == "formular" and types["Generalvollmacht"] == "formular"
    assert types["Pfandvertrag Errichtung"] == "merkblatt"


def test_zh_notariat_downloads_filtered_to_non_form_guidance():
    rows = [r for r in gk.zh_download_rows(_read("grundbuch_zh_downloads_notariat.html"))
            if gk._ZH_NOTARIAT_DOWNLOADS_INCLUDE.search(r["title"])]
    titles = {r["title"] for r in rows}
    assert titles == {
        "Dienstanweisung zur Notariatsgebührenverordnung",
        "Merkblatt für die Hinterbliebenen",
        "Gesetzliche Erbfolgeregelung gültig bis Ende 2022",
        "Gesetzliche Erbfolgeregelung ab Januar 2023",
        "Partnerschaftsgesetz: Das Wichtigste in Kürze",
    }
    # the ~100 blank AG/GmbH/Genossenschaft deed templates on the same page
    # (and Vollmacht/Generalvollmacht, forms) are not in scope
    assert "Vollmacht" not in titles and "Gründung: bar in CHF" not in titles


# ── BS: newsletter listing ───────────────────────────────────────────────

def test_bs_newsletter_rows_seventeen_issues_compilation_and_form_dropped():
    rows = gk.bs_newsletter_rows(_read("grundbuch_bs_newsletter.html"))
    numbers = {r["number"] for r in rows}
    assert "17" in numbers and "1" in numbers
    assert "1-17" not in numbers          # the bound compilation repeats every issue
    assert len(rows) == 17
    assert all("AHV" not in r["title"] for r in rows)
    nr17 = next(r for r in rows if r["number"] == "17")
    assert gk.month_year_year(nr17["title"]) == "2026"


# ── AG: Weisung/Kreisschreiben + Praxishilfen ───────────────────────────

def test_ag_weisung_und_kreisschreiben_four_pdfs():
    rows = gk.ag_pdf_rows(_read("grundbuch_ag_weisung_kreisschreiben.html"), gk._AG_WEISUNG_URL)
    assert len(rows) == 4
    types = {gk.ag_doc_type(r["title"]) for r in rows}
    assert types == {"weisung", "kreisschreiben"}
    weisung = next(r for r in rows if gk.ag_doc_type(r["title"]) == "weisung")
    assert "Grundbuchführung" in weisung["title"]


def test_ag_praxishilfen_drops_forms_and_event_handouts():
    rows = gk.ag_pdf_rows(_read("grundbuch_ag_praxishilfen.html"), gk._AG_PRAXISHILFEN_URL)
    kept = [r for r in rows if not gk._AG_PRAXISHILFEN_EXCLUDE.search(r["title"])]
    titles = {r["title"] for r in kept}
    assert "Merkblatt Grundbuchauszüge und -auskünfte" in titles
    assert "Anmerkungstatbestände in der Gesetzgebung des Kantons Aargau; Liste gemäss " \
           "Art. 129 Abs. 4 Grundbuchverordnung (GBV)" in titles
    assert not any("Grundbuchanmeldung" == t for t in titles)
    assert not any("Veranstaltung der ANG" in t for t in titles)
    assert not any(t.startswith("Austausch zwischen Urkundspersonen") for t in titles)


# ── BE: Handbuch chapter splitter (short text excerpts, no PDF fixture) ──

_BE_EXCERPT_DE = (
    "Inhaltsverzeichnis\n"
    "1. \nGeschäftsverkehr mit dem Grundbuchamt ...........................14 \n"
    "2. \nFormelle Vorschriften [26] ..................................17 \n"
    "\n\n\nHandbuch \nfür den Verkehr mit den Grundbuchämtern und die \n"
    "Grundbuchführung \nGeschäftsverkehr mit dem Grundbuchamt \n \n \n14/231\n"
    "1. \nGeschäftsverkehr mit dem Grundbuchamt \n1.1 \nDie Grundbuchämter im Internet \n"
    "Text des ersten Kapitels.\n\n\n"
    "Handbuch \nfür den Verkehr mit den Grundbuchämtern und die \n"
    "Grundbuchführung \nFormelle Vorschriften \n \n \n17/231\n"
    "2. \nFormelle Vorschriften [26] \n2.1 \nGrundstückbeschreibung in Urschriften \n"
    "Text des zweiten Kapitels.\n"
)

_BE_COVER_DE = (
    "Grundbuchämter \nHandbuch \nfür den Verkehr mit den Grundbuchämtern und die \n"
    "Grundbuchführung \n \n \n \n \nBearbeitungsdatum \n17. August 2026 \nVersion \n37 \n"
    "Autor/-in \nHabuko \n"
)


def test_split_be_chapters_on_short_excerpt():
    chapters = gk.split_be_chapters(_BE_EXCERPT_DE)
    assert [c["number"] for c in chapters] == ["1", "2"]
    assert chapters[0]["title"] == "Geschäftsverkehr mit dem Grundbuchamt"
    assert "Text des ersten Kapitels" in chapters[0]["body_text"]
    assert "Text des zweiten Kapitels" not in chapters[0]["body_text"]
    assert "Text des zweiten Kapitels" in chapters[1]["body_text"]
    # the TOC's dotted-leader occurrences never match (no "n/total" marker before them)
    assert not any("..." in c["title"] for c in chapters)


def test_split_be_chapters_empty_text_yields_nothing():
    assert gk.split_be_chapters("no chapters here") == []


def test_be_handbuch_version_from_cover_page():
    date, version = gk.be_handbuch_version(_BE_COVER_DE)
    assert date == "2026-08-17" and version == "37"


def test_be_handbuch_version_missing_returns_empty():
    assert gk.be_handbuch_version("nothing to see here") == ("", "")


# ── scraper identity ─────────────────────────────────────────────────

def test_scraper_class_identity():
    # Not registered in scrapers/practice/runner.py: this task does not
    # touch that file (see module docstring / task instructions). The
    # class is otherwise fully wired to the PracticeScraper framework.
    assert gk.GrundbuchKantoneScraper.SOURCE_KEY == "grundbuch_kantone"
    assert gk.GrundbuchKantoneScraper.REVISION_FIELD == "revision"
