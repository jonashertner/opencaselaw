"""Prosecution-guidance practice sources (2026-11-08): ZH Oberstaatsanwaltschaft
(WOSTA + Strafmassempfehlungen), SSK-CPS, VS Ministère public, AG
Staatsanwaltschaft (source 'strafverfolgung'), and BJ international mutual
legal assistance in criminal matters (source 'bj_rechtshilfe'). Offline:
golden HTML fixtures captured from the live pages on 2026-11-08, plus a
trimmed text excerpt of the real WOSTA PDF for the chapter splitter."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scrapers.practice import strafverfolgung as sv  # noqa: E402
from scrapers.practice import bj_rechtshilfe as bjr  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "practice"


def _read(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


# ── WOSTA chapter splitter ──────────────────────────────────────────────

def test_wosta_splitter_on_real_excerpt():
    text = _read("strafverfolgung_wosta_excerpt.txt")
    chapters = sv.split_wosta(text)
    assert [c["number"] for c in chapters] == ["1", "2", "3"]
    assert chapters[0]["title"] == "Grundsätze"
    assert chapters[1]["title"] == "Ausstand und Aufsichtsbeschwerden"
    assert chapters[2]["title"] == "Verfahrenskosten, Entschädigung, Genugtuung"
    # each chapter's body starts with its own real heading, not the next one's
    assert chapters[0]["body"].startswith("1. Grundsätze")
    assert chapters[1]["body"].startswith("7. Ausstand")
    # the last chapter runs to the end of the text (no next-chapter boundary)
    assert chapters[2]["body"].startswith("17. Verfahrenskosten")
    assert chapters[2]["body"].endswith(text.strip()[-20:])
    # page-footer markers are stripped out of the body, not left as noise
    assert "Seite" not in chapters[0]["body"] and "von 22" not in chapters[0]["body"]


def test_wosta_splitter_bails_out_on_unrecognised_structure():
    assert sv.split_wosta("no Inhaltsübersicht or Inhaltsverzeichnis here") == []
    assert sv.split_wosta("Inhaltsübersicht but no verzeichnis marker") == []


def test_wosta_stand_date_from_filename():
    url = ("https://www.zh.ch/content/dam/zhweb/bilder-dokumente/themen/"
           "sicherheit-justiz/strafverfahren/WOSTA_Stand_09.09.2026_barrierefrei_V02.pdf")
    assert sv.wosta_stand_date(url) == "2026-09-09"
    assert sv.wosta_stand_date("https://example.com/no-date-here.pdf") == ""


def test_latest_version_date_prefers_highest_version_over_reading_order():
    # a real ZH Merkblatt revision table: Version 1 is EARLIEST in reading
    # order but Version 3 is the one in force
    text = "Version 1 \n09.01.2019 \nVersion 2 \n27.05.2022 \nVersion 3 \n28.01.2025 \n"
    assert sv.latest_version_date(text) == "2025-01-28"
    assert sv.latest_version_date("no version table here") == ""


# ── ZH Oberstaatsanwaltschaft page ──────────────────────────────────────

def test_zh_page_finds_wosta_and_strafmassempfehlungen():
    items = sv.parse_zh_page(_read("strafverfolgung_zh.html"))
    assert len(items) == 3
    wosta = next(i for i in items if i["section"] == "WOSTA")
    assert wosta["title"] == "Weisungen der Oberstaatsanwaltschaft für das Vorverfahren"
    assert "WOSTA_Stand_09.09.2026" in wosta["pdf_url"]
    titles = {i["title"] for i in items}
    assert "Strafmassempfehlungen" in titles
    assert "Strafmassempfehlung Umweltkriminalität" in titles


# ── SSK-CPS ──────────────────────────────────────────────────────────────

def test_ssk_de_and_fr_counts_and_dates():
    de = sv.parse_ssk(_read("strafverfolgung_ssk_de.html"), "de",
                       "https://www.ssk-cmp.ch/de/dienstleistungen/empfehlungen")
    fr = sv.parse_ssk(_read("strafverfolgung_ssk_fr.html"), "fr",
                       "https://www.ssk-cmp.ch/fr/dienstleistungen/empfehlungen")
    assert len(de) == 19 and len(fr) == 19
    bank = next(i for i in de if i["title"] == "Empfehlungen Bankeneditionen")
    assert bank["date"] == "2025-11-20"
    assert bank["section"] == "Edition von Bankunterlagen"
    assert all(i["language"] == "de" for i in de)
    assert all(i["language"] == "fr" for i in fr)


# ── VS Ministère public ──────────────────────────────────────────────────

def test_vs_de_and_fr_counts_and_doc_types():
    de = sv.parse_vs(_read("strafverfolgung_vs_de.html"), "de",
                      "https://www.vs.ch/de/web/mp/directives-et-reglements")
    fr = sv.parse_vs(_read("strafverfolgung_vs_fr.html"), "fr",
                      "https://www.vs.ch/fr/web/mp/directives-et-reglements")
    assert len(de) == 21 and len(fr) == 21
    reglemente = [i for i in de if i["doc_type"] == "erlass"]
    weisungen = [i for i in de if i["doc_type"] == "weisung"]
    assert len(reglemente) == 2 and len(weisungen) == 19
    assert any("Gerichtsstand" in i["title"] for i in weisungen)
    # titles are cleaned of the "pdf " prefix and ".pdf" suffix the anchor text carries
    assert not any(t["title"].lower().startswith("pdf") for t in de)
    assert not any(t["title"].lower().endswith(".pdf") for t in de)


# ── AG Staatsanwaltschaft ────────────────────────────────────────────────

def test_ag_page_items_and_dates():
    items = sv.parse_ag(_read("strafverfolgung_ag.html"),
                         "https://www.ag.ch/de/themen/recht-justiz/strafverfolgung/strafverfahren")
    assert len(items) == 4
    titles = {i["title"] for i in items}
    assert "Weisung zur Führung von Strafverfahren" in titles
    fuehrung = next(i for i in items if i["title"] == "Weisung zur Führung von Strafverfahren")
    assert fuehrung["date"] == "2026-01-01"
    empf = next(i for i in items if "Strafbefehlsempfehlungen" in i["title"])
    assert sv._doc_type(empf["title"], "") == "empfehlung"
    assert sv._doc_type(fuehrung["title"], "") == "weisung"


# ── BJ Rechtshilfe: annual reports + statistics ──────────────────────────

def test_bj_main_page_reports_and_statistics():
    items = bjr._dam_items(_read("bj_rechtshilfe_main_de.html"),
                            "https://www.bj.admin.ch/de/internationale-rechtshilfe-in-strafsachen")
    assert len(items) == 11
    years = sorted(m.group(1) for it in items
                    for m in [__import__("re").search(r"jb-irh-(\d{4})", it["pdf_url"])] if m)
    assert years == [str(y) for y in range(2015, 2025)]
    stats = next(i for i in items if "rhf-statistik" in i["pdf_url"])
    assert stats["title"] == "Internationale Rechtshilfe. Statistik 2025"
    assert stats["file_date"] == "2026-03-03"
    tb2024 = next(i for i in items if "jb-irh-2024" in i["pdf_url"])
    assert tb2024["file_date"] == "2025-05-01"


# ── BJ Rechtshilfe: Auslieferung ─────────────────────────────────────────

def test_auslieferung_page_families_and_language_suffixes():
    items = bjr._dam_items(_read("bj_rechtshilfe_auslieferung_de.html"),
                            "https://www.bj.admin.ch/de/auslieferung")
    assert len(items) == 11
    split = [bjr.split_lang_suffix(bjr._filename_stem(i["pdf_url"])) for i in items]
    bases = {b for b, _ in split}
    assert bases == {"auslieferung", "darlegung-auslieferung", "checkliste-personenfahndung"}
    langs = sorted(l for _, l in split if l)
    assert langs == sorted(["es", "es", "pt", "ro", "ru", "sq", "sr", "hr"])
    # the institutional docs (Fact Sheet, Darlegung, Checkliste) carry no
    # filename suffix - their language comes from the page, not the file
    no_suffix = [b for b, l in split if l is None]
    assert len(no_suffix) == 3


def test_split_lang_suffix_longest_match_and_no_match():
    assert bjr.split_lang_suffix("darlegung-auslieferung-es") == ("darlegung-auslieferung", "es")
    assert bjr.split_lang_suffix("wegleitung-strafsachen-e") == ("wegleitung-strafsachen", "en")
    assert bjr.split_lang_suffix("auslieferung") == ("auslieferung", None)


# ── BJ Rechtshilfe: Wegleitungen + Muster-Ersuchen ───────────────────────

def test_wegleitungen_page_has_the_strafsachen_and_italien_editions():
    items = bjr._dam_items(_read("bj_rechtshilfe_wegleitungen_de.html"),
                            "https://www.rhf.admin.ch/de/wegleitungen-checklisten-und-rundschreiben")
    stems = {bjr._filename_stem(i["pdf_url"]): i for i in items}
    assert "wegleitung-strafsachen" in stems and "wegleitung-strafsachen-e" in stems
    assert "wegleitung-rechtshilfe-italien" in stems
    de = stems["wegleitung-strafsachen"]
    assert de["title"] == "Wegleitung Strafsachen (Ausgabe 2025)"
    assert de["file_date"] == "2025-10-15"
    en = stems["wegleitung-strafsachen-e"]
    assert "edition 2009" in en["title"]
    # the description paragraph (present on this page, absent on the main
    # BJ page) must not leak into `meta`/file_date
    assert de["meta"].startswith("PDF")
    assert "Rechtshilfe in Strafsachen" not in de["meta"]


def test_muster_ersuchen_strafrecht_found_and_civil_law_models_excluded():
    items = bjr._dam_items(_read("bj_rechtshilfe_muster_de.html"),
                            "https://www.rhf.admin.ch/de/muster-und-formulare")
    strafrecht = [i for i in items if "muster-ersuchen-strafrecht" in i["pdf_url"]]
    assert len(strafrecht) == 1
    assert "Rechtshilfeersuchens in Strafsachen" in strafrecht[0]["title"]
    # HBewUe70/HUE54 are civil-law (Hague evidence/service conventions);
    # the module only picks muster-ersuchen-strafrecht.pdf out of this page
    assert not any("hbewue70" in i["pdf_url"].lower() for i in strafrecht)


def test_dam_hash_shared_across_language_prefixes():
    url_de = "https://www.bj.admin.ch/dam/de/sd-web/cQpyVBCv-ewr/jb-irh-2024.pdf"
    url_fr = "https://www.bj.admin.ch/dam/fr/sd-web/cQpyVBCv-ewr/jb-irh-2024.pdf"
    assert bjr._dam_hash(url_de) == bjr._dam_hash(url_fr) == "cQpyVBCv-ewr"


def test_wegleitung_date_is_the_printed_edition_not_the_migration_stamp():
    """rhf.admin.ch re-stamped old files 2020-07-13; the cover's edition wins."""
    s = bjr.BjRechtshilfeScraper.__new__(bjr.BjRechtshilfeScraper)
    stub = {"title": "Guidelines", "doc_number": "BJ Wegleitung Internationale Rechtshilfe Strafsachen",
            "date": "2020-07-13", "language": "en", "doc_type": "wegleitung", "bj_id": "x"}
    doc = bjr.BjRechtshilfeScraper._normalize(s, stub, "FOJ | 9th edition 2009 (Case law as of May 2010)")
    assert doc["date"] == "2009"
    stub_de = {**stub, "date": "2025-10-15", "language": "de"}
    doc = bjr.BjRechtshilfeScraper._normalize(s, stub_de, "10. Auflage 2025 (Rechtsprechung Stand Dezember 2022)")
    assert doc["date"] == "2025-10-15"
