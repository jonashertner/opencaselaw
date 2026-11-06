"""Banking/financial-intelligence practice sources (2026-11-06): MROS, ESTV
AIA/FATCA/CbCR guidance, SIF. Offline: golden HTML fixtures captured from the
live pages on 2026-11-06 (script/style/svg stripped).

These three scrapers are deliberately NOT wired into
scrapers/practice/runner.py or mcp_server.py by this change — see the task
report for the exact lines to add; someone else owns those files."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scrapers.practice import estv_aia  # noqa: E402
from scrapers.practice import mros  # noqa: E402
from scrapers.practice import sif  # noqa: E402
from scrapers.practice.base import dateline_iso, first_date_iso, month_year_year  # noqa: E402
from scrapers.practice.sdweb import download_items  # noqa: E402
from scrapers.practice.stiftungsaufsicht import parse_faq  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "practice"


def _read(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


# ── date helpers (base.py) ──────────────────────────────────────────────
# Regression coverage for the bug found while building these three sources:
# a plain scan of the first few hundred characters of a PDF can land on an
# in-document revision-history table instead of the cover date.

def test_dateline_iso_beats_a_revision_history_table():
    # The ESTV CbCR Technical Wegleitung: cover says "Bern, September 2026"
    # (no day), but the very next page is a changelog starting with an
    # earlier full date. dateline_iso must report the cover's year, not
    # the changelog's full (and wrong-for-this-row) date.
    text = ("Bern, September 2026 \nTechnische Wegleitung\n\n2/53\n"
            "Dokumentengeschichte\nDatum\nÄnderungen\n19.09.2017 \nInitiale Version")
    assert dateline_iso(text[:500]) == "2026"           # no day in the dateline itself -> year only
    assert first_date_iso(text[:500]) == "2017-09-19"   # what a naive scan would wrongly pick instead


def test_dateline_iso_handles_de_fr_it_and_numeric_form():
    assert dateline_iso("Bern, 15. Januar 2026 \nWegleitung") == "2026-01-15"
    assert dateline_iso("Berne, septembre 2026 \nDirective technique") == "2026"
    assert dateline_iso("Berna, settembre 2026 \nDirettiva tecnica") == "2026"
    assert dateline_iso("Bern, 03.11.2023 \nALERTS AND TYPOLOGIES") == "2023-11-03"
    assert dateline_iso("no dateline here at all") == ""


def test_month_year_year_understands_english_too():
    # English-only MROS/SIF documents ("January 2024", no day) need this —
    # _MONTHS_ALL originally covered only DE/FR/IT.
    assert month_year_year("January 2024 is when this NRA was issued") == "2024"
    assert month_year_year("Reference 2024 nothing-month") == ""
    assert month_year_year("") == ""


# ── MROS ─────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def mros_items_de():
    return list(download_items(_read("mros_publikationen_de.html"), mros.BASE))


@pytest.fixture(scope="module")
def mros_items_en():
    return list(download_items(_read("mros_publikationen_en.html"), mros.BASE))


def test_mros_jahresberichte_discovered_2015_through_2025(mros_items_de):
    years = {m.group(1) for i in mros_items_de
             if (m := mros._JB_YEAR.search(i["title"])) and mros._JB_TITLE.search(i["title"])}
    assert years == {str(y) for y in range(2015, 2026)}


def test_mros_known_hashes_cover_every_other_publication(mros_items_de, mros_items_en):
    for items in (mros_items_de, mros_items_en):
        for item in items:
            if mros._JB_TITLE.search(item["title"]):
                continue
            h = mros._hash_from_url(item["pdf_url"])
            assert h in mros._KNOWN or h in mros._EN_ONLY, (item["title"], item["pdf_url"])


def test_mros_en_only_documents_are_english_only_on_every_page(mros_items_de, mros_items_en):
    # The Typology Report and both Alerts are served identically under
    # every language path; their DAM hash must be in _EN_ONLY, not _KNOWN
    # (which would wrongly tag them with the page's own language).
    typ = next(i for i in mros_items_de if i["title"] == "Typology Report")
    assert mros._hash_from_url(typ["pdf_url"]) in mros._EN_ONLY
    alert = next(i for i in mros_items_de if i["title"].startswith("Alerts and typologies"))
    assert mros._hash_from_url(alert["pdf_url"]) in mros._EN_ONLY


def test_mros_jahresbericht_hash_shared_across_languages_but_filename_differs(mros_items_de, mros_items_en):
    de_2025 = next(i for i in mros_items_de if i["title"] == "Jahresbericht MROS 2025")
    en_2025 = next(i for i in mros_items_en if i["title"] == "Annual Report MROS 2025")
    assert mros._hash_from_url(de_2025["pdf_url"]) == mros._hash_from_url(en_2025["pdf_url"])
    assert de_2025["pdf_url"] != en_2025["pdf_url"]        # different file name per language


def test_mros_weitere_berichte_section_not_in_known_hashes():
    # The excluded Bundesrat/Postulat reports (Schliessfächer, virtuelle
    # Währungen, Regulierungsfolgenabschätzung) live further down the same
    # page, hosted on sif.admin.ch/efd.admin.ch — confirm none of their
    # hashes were accidentally added to _KNOWN.
    assert sif._DUPLICATE_OF_MROS.isdisjoint(mros._KNOWN)
    assert sif._DUPLICATE_OF_MROS.isdisjoint(mros._EN_ONLY)


class _FakeMros(mros.MrosScraper):
    TEXTS: dict[str, str] = {}

    def fetch_pdf_text(self, pdf_url):  # offline
        return self.TEXTS.get(pdf_url, "")


def _mros_stub(url, lang, number, doc_type="bericht"):
    return {"pdf_url": url, "url": "x", "title": "t", "doc_number": number, "date": "",
            "language": lang, "doc_type": doc_type, "topics": [number], "file_date": "",
            "mros_id": f"{number}_{lang}".replace(" ", "_")}


def test_mros_dedup_keeps_the_first_language_for_a_real_translation(tmp_path, monkeypatch):
    monkeypatch.setattr(_FakeMros, "OUTPUT_DIR", tmp_path)
    _FakeMros.TEXTS = {"de.pdf": "Negativtypologien deutscher Text",
                       "fr.pdf": "Typologies négatives texte français"}
    s = _FakeMros()
    de = s.with_body(_mros_stub("de.pdf", "de", "MROS-Negativtypologien 2025"), "MROS-Negativtypologien 2025")
    fr = s.with_body(_mros_stub("fr.pdf", "fr", "MROS-Negativtypologien 2025"), "MROS-Negativtypologien 2025")
    assert de["body_text"] and fr["body_text"] and de["body_text"] != fr["body_text"]


def test_mros_dedup_drops_a_byte_identical_fallback_copy(tmp_path, monkeypatch):
    monkeypatch.setattr(_FakeMros, "OUTPUT_DIR", tmp_path)
    _FakeMros.TEXTS = {"de.pdf": "GUIDE Financial Intelligence", "it.pdf": "GUIDE Financial Intelligence"}
    s = _FakeMros()
    de = s.with_body(_mros_stub("de.pdf", "de", "MROS-FIAHT-Guide", "wegleitung"), "MROS-FIAHT-Guide")
    it = s.with_body(_mros_stub("it.pdf", "it", "MROS-FIAHT-Guide", "wegleitung"), "MROS-FIAHT-Guide")
    assert de["body_text"] and it is None


# ── ESTV AIA / FATCA / CbCR ──────────────────────────────────────────────

@pytest.fixture(scope="module")
def aia_items_de():
    return list(download_items(_read("estv_aia_publikationen_de.html"), estv_aia.BASE))


@pytest.fixture(scope="module")
def fatca_items_de():
    return list(download_items(_read("estv_aia_fatca_de.html"), estv_aia.BASE))


def test_aia_wegleitung_and_technical_wegleitung_known(aia_items_de):
    assert len(aia_items_de) == 2
    for item in aia_items_de:
        h = estv_aia._hash_from_url(item["pdf_url"])
        assert h in estv_aia._KNOWN
    numbers = {estv_aia._KNOWN[estv_aia._hash_from_url(i["pdf_url"])][0] for i in aia_items_de}
    assert numbers == {"ESTV-AIA-Wegleitung", "ESTV-AIA-Technische-Wegleitung"}


def test_fatca_page_separates_guidance_from_technical_specs_and_forms(fatca_items_de):
    hashes = {estv_aia._hash_from_url(i["pdf_url"]): i["title"] for i in fatca_items_de}
    # the technical rule tables and the form are explicitly excluded
    assert estv_aia._SKIP == {"L2uOxgEr-d93", "mE7rJIQOHBKm", "SYpGz0JIklio"}
    assert estv_aia._SKIP <= hashes.keys()
    # the English-only user guide and the three info letters are guidance
    assert "1obWmL9z5Fud" in estv_aia._EN_ONLY
    guidance_hashes = {h for h in hashes if h not in estv_aia._SKIP}
    assert all(h in estv_aia._KNOWN or h in estv_aia._EN_ONLY for h in guidance_hashes)


def test_aia_faq_parsed_with_the_shared_accordion_parser():
    de = parse_faq(_read("estv_aia_faq_de.html"))
    fr = parse_faq(_read("estv_aia_faq_fr.html"))
    assert [q["question"] for q in de] == [
        "Behandlung von Darlehensrückzahlungen im Zusammenhang mit Trusts",
        "Rücktauschbarkeit von Stablecoins",
    ]
    assert len(fr) == len(de)
    assert de[0]["answer"].startswith("Frage") or "Trust" in de[0]["answer"]


class _FakeAia(estv_aia.EstvAiaScraper):
    TEXTS: dict[str, str] = {}

    def fetch_pdf_text(self, pdf_url):  # offline
        return self.TEXTS.get(pdf_url, "")


def test_aia_fatca_user_guide_tagged_english_regardless_of_discovery_page():
    number, doc_type = estv_aia._EN_ONLY["1obWmL9z5Fud"]
    assert number == "ESTV-FATCA-User-Guide" and doc_type == "wegleitung"


# ── SIF ────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def sif_items_de():
    return list(download_items(_read("sif_integritaet_finanzplatzes_de.html"), sif.BASE))


def test_sif_integrity_page_classifies_every_item(sif_items_de):
    for item in sif_items_de:
        h = sif._hash_from_url(item["pdf_url"])
        assert h in sif._KNOWN or h in sif._EXCLUDE or h in sif._DUPLICATE_OF_MROS, \
            (item["title"], item["pdf_url"])


def test_sif_excludes_mandat_gutachten_rfa_and_bundesrat_postulate_reports(sif_items_de):
    excluded_titles = {i["title"] for i in sif_items_de
                        if sif._hash_from_url(i["pdf_url"]) in sif._EXCLUDE}
    assert any("Mandat" in t for t in excluded_titles)
    assert any("Rechtsgutachten" in t for t in excluded_titles)
    assert any("Regulierungsfolge" in t for t in excluded_titles)
    assert any("virtuellen Währungen" in t for t in excluded_titles)


def test_sif_nra_reports_mirrored_on_fedpol_are_flagged_as_duplicates(sif_items_de):
    dup_titles = {i["title"] for i in sif_items_de
                  if sif._hash_from_url(i["pdf_url"]) in sif._DUPLICATE_OF_MROS}
    assert any("Proliferationsfinanzierung" in t for t in dup_titles)
    assert any("Krypto Assets" in t or "Krypto-Assets" in t for t in dup_titles)
    # and those hashes must never collide with mros.py's own _KNOWN/_EN_ONLY
    assert sif._DUPLICATE_OF_MROS.isdisjoint(mros._KNOWN)


def test_sif_known_doc_types_are_in_the_existing_enum(sif_items_de):
    # doc_type values used here (bericht, wegleitung, erlaeuterung) already
    # exist in mcp_server.py's search_practice doc_type enum — no new value
    # needed for this source.
    used = {dt for _, dt in sif._KNOWN.values()}
    assert used <= {"bericht", "wegleitung", "erlaeuterung"}
