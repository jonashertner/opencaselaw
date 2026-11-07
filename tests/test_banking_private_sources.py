"""Private-body banking practice sources, added 2026-11-06: the Swiss
Banking Ombudsman (bankingombudsman.py) and the Swiss Bankers Association /
SBVg self-regulation (sbvg.py). Both are PRIVATE bodies — permission to
index and serve this material was requested by email on 2026-11-06 and had
not been granted as of that date (see each module's docstring). Offline:
golden HTML fixtures captured from the live sites on 2026-11-06."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scrapers.practice import bankingombudsman as bo  # noqa: E402
from scrapers.practice import sbvg  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "practice"


def _read(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


# ── bankingombudsman: Jahresberichte ────────────────────────────────────

def test_jahresberichte_de_every_year_full_and_summary():
    tiles = bo.parse_file_tiles(_read("bankingombudsman_jahresberichte_de.html"))
    assert len(tiles) == 22  # 11 years (2015-2025) x {full, summary}
    kept = [(t["title"], bo.classify_report(t["title"], "de")) for t in tiles]
    assert all(info is not None for _, info in kept)
    years = {info["year"] for _, info in kept}
    assert years == {str(y) for y in range(2015, 2026)}
    full = next(info for t, info in kept if t == "Jahresbericht 2025")
    assert full == {"year": "2025", "kind": "full"}
    summary = next(info for t, info in kept if t == "2025 in Kürze")
    assert summary == {"year": "2025", "kind": "summary"}


def test_jahresberichte_de_decomposed_umlaut_still_matches():
    # The site mixes precomposed "ü" (U+00FC) with a bare "u" + combining
    # diaeresis (U+0075 U+0308) across otherwise-identical titles — only
    # the newest year used the precomposed form on 2026-11-06. Without
    # NFC normalisation every older "... in Kürze" title fails to classify.
    decomposed = "2016 in Kürze"
    assert bo.classify_report(decomposed, "de") == {"year": "2016", "kind": "summary"}


def test_jahresberichte_it_en_relist_de_fr_as_cross_references():
    for lang, fn in (("it", "bankingombudsman_jahresberichte_it.html"),
                      ("en", "bankingombudsman_jahresberichte_en.html")):
        tiles = bo.parse_file_tiles(_read(fn))
        assert len(tiles) == 33  # 11 x {DE cross-ref, FR cross-ref, own summary}
        classified = [bo.classify_report(t["title"], lang) for t in tiles]
        kept = [c for c in classified if c is not None]
        assert len(kept) == 11  # only the page's own-language summary survives
        assert all(c["kind"] == "summary" for c in kept)


def test_jahresberichte_fr_full_and_summary_every_year():
    tiles = bo.parse_file_tiles(_read("bankingombudsman_jahresberichte_fr.html"))
    kept = [bo.classify_report(t["title"], "fr") for t in tiles]
    kept = [c for c in kept if c]
    assert {c["year"] for c in kept if c["kind"] == "full"} == {str(y) for y in range(2015, 2026)}


# ── bankingombudsman: Stellungnahmen und Publikationen ──────────────────

def test_publications_de_language_tags_stripped():
    tiles = bo.parse_file_tiles(_read("bankingombudsman_publications_de.html"))
    assert len(tiles) == 17
    tagged = [bo.classify_publication(t["title"], "de") for t in tiles]
    negativ = next(c for c in tagged if "Negativzinsen" in c["title"])
    assert negativ == {"title": "Negativzinsen bei Vorfälligkeitsentschädigungen", "language": "de"}
    faktenblatt = next(c for c in tagged if "Factsheet" in c["title"])
    assert faktenblatt["language"] == "de"  # the page's own translation, no tag needed
    assert bo.publication_doc_type(faktenblatt["title"]) == "merkblatt"
    assert bo.publication_doc_type(negativ["title"]) == "bericht"


def test_publications_fr_it_en_relist_de_items_with_tags():
    for lang, fn in (("fr", "bankingombudsman_publications_fr.html"),
                      ("it", "bankingombudsman_publications_it.html"),
                      ("en", "bankingombudsman_publications_en.html")):
        tiles = bo.parse_file_tiles(_read(fn))
        # The evaluation study's 4 language PDFs (same on every UI-language
        # page — "_DE"/"_FR"/"_EN"/"_IT" in the URL): each tagged with its
        # true language regardless of which page's titles are translated.
        eval_tiles = [t for t in tiles if "Evaluation_presserohstoff" in t["url"]]
        eval_langs = {bo.classify_publication(t["title"], lang)["language"] for t in eval_tiles}
        assert eval_langs == {"de", "fr", "en", "it"}
        # The Faktenblatt is always this page's own translation (every other
        # item keeps its true original-publication language even when its
        # anchor text is itself translated into the page's UI language).
        faktenblatt = next(t for t in tiles if "Faktenblatt-Betrug" in t["url"])
        assert bo.classify_publication(faktenblatt["title"], lang)["language"] == lang


def test_publications_same_url_across_pages_is_one_document():
    # The evaluation study PDF is linked identically (same URL, same DE
    # text) from all four language pages; a scraper that didn't dedupe by
    # URL across pages would quadruple-count it.
    urls = set()
    for fn in ("bankingombudsman_publications_de.html", "bankingombudsman_publications_it.html"):
        tiles = bo.parse_file_tiles(_read(fn))
        urls.update(t["url"] for t in tiles if "Evaluation_presserohstoff" in t["url"] and "_DE" in t["url"])
    assert len(urls) == 1


# ── bankingombudsman: hreflang-based translation grouping ───────────────

def test_hreflang_links_from_a_case_page():
    links = bo.hreflang_links(_read("bankingombudsman_case_hreflang.html"))
    assert links == {
        "de": "https://bankingombudsman.ch/nicht-umgesetzte-sonderkonditionen-fuer-mandatsgebuehren/",
        "fr": "https://bankingombudsman.ch/fr/conditions-particulieres-non-mises-en-oeuvre-pour-les-frais-de-mandat/",
        "it": "https://bankingombudsman.ch/it/mancata-applicazione-di-condizioni-preferenziali-convenute-per-le-commissioni-di-gestione/",
        "en": "https://bankingombudsman.ch/en/unimplemented-special-conditions-for-mandate-fees/",
    }
    assert "x-default" not in links  # excluded on purpose, it only duplicates "de"


def test_collect_descendant_category_ids_excludes_the_exclude_category():
    categories = [
        {"id": 44, "slug": "fallsammlung", "parent": 0, "count": 0},
        {"id": 45, "slug": "anlageberatung", "parent": 44, "count": 43},
        {"id": 224, "slug": "exclude", "parent": 43, "count": 0},
        {"id": 43, "slug": "mitteilungen", "parent": 0, "count": 1},
        {"id": 41, "slug": "aktuelles", "parent": 43, "count": 19},
    ]
    assert bo.collect_descendant_category_ids(categories, "fallsammlung") == {45}
    assert bo.collect_descendant_category_ids(categories, "mitteilungen") == {41}
    assert bo.collect_descendant_category_ids(categories, "no-such-root") == set()


def test_post_stub_strips_html_and_normalises_unicode():
    scraper = bo.BankingOmbudsmanScraper.__new__(bo.BankingOmbudsmanScraper)
    post = {
        "link": "https://bankingombudsman.ch/x/",
        "title": {"rendered": "Ein <em>Für</em> Beispiel"},
        "content": {"rendered": "<p>Zeile 1</p>\n<p>Zeile 2</p>"},
        "date": "2026-04-16T20:03:07",
    }
    stub = bo.BankingOmbudsmanScraper._post_stub(
        scraper, post, "de", "SBO Fall 1", "rechtsprechung", "case_de_1")
    assert stub["title"] == "Ein Für Beispiel"
    assert stub["body_text"] == "Zeile 1\nZeile 2"
    assert stub["date"] == "2026-04-16"
    assert stub["doc_number"] == "SBO Fall 1"
    assert stub["bo_id"] == "case_de_1"


def test_sources_registered():
    # Published on Jonas's decision of 2026-11-07 while permission is pending
    # (see the module docstrings for how to withdraw a source).
    from scrapers.practice.runner import ENABLED_SCRAPERS
    assert "bankingombudsman" in ENABLED_SCRAPERS
    assert "sbvg" in ENABLED_SCRAPERS


# ── SBVg: pdf link extraction ────────────────────────────────────────────

def test_sbvg_parse_pdf_links_prefers_title_attribute():
    tiles = sbvg.parse_pdf_links(_read("sbvg_selbstregulierung_de.html"))
    titles = {t["title"] for t in tiles}
    assert "Sorgfaltspflichtvereinbarung zur Bekämpfung der Geldwäscherei (VSB)" in titles
    assert "Übersicht über die Richtlinien der SBVg (01.09.2023)" in titles


def test_sbvg_parse_pdf_links_drops_short_untitled_mentions():
    tiles = sbvg.parse_pdf_links(_read("sbvg_geldwaesche_de.html"))
    titles = {t["title"] for t in tiles}
    assert "VSB" not in titles  # bare in-text mention, same href as the real VSB tile
    assert "Stellungnahme" not in titles  # out-of-scope consultation response


def test_sbvg_parse_pdf_links_excludes_the_tjpg_stellungnahme_everywhere():
    # On the Italian page the SAME out-of-scope link's anchor text is long
    # enough ("presa di posizione", 18 chars) to pass the short-text filter
    # on its own — only the explicit URL denylist keeps it out.
    tiles = sbvg.parse_pdf_links(_read("sbvg_geldwaesche_it.html"))
    assert not any("TJPG" in t["url"] for t in tiles)
    assert "presa di posizione" not in {t["title"] for t in tiles}


# ── SBVg: classification ─────────────────────────────────────────────────

def test_sbvg_half_year_regex_matches_the_period_form():
    # Regression: a trailing \b after "1\." / "2\." can never match (a
    # period is always followed by whitespace, never a word character), so
    # EVERY "1. Halbjahr"/"2. Halbjahr" title silently failed classification
    # until the \b was dropped from that side of the pattern.
    assert sbvg._HALF1_RE.search("1. Halbjahr 2020")
    assert sbvg._HALF2_RE.search("2. Halbjahr 2020")


def test_sbvg_classify_korrekturmodus_and_compilation_are_dropped():
    assert sbvg.classify_sbvg_pdf(
        "Vereinbarung über Standesregeln zur Sorgfaltspflicht der Banken (VSB 27) - im Korrekturmodus"
    ) is None
    assert sbvg.classify_sbvg_pdf("Leading Cases Aufsichtskommission VSB 2017 – 2026") is None


def test_sbvg_classify_vsb_editions_share_doc_number_with_status():
    de27 = sbvg.classify_sbvg_pdf("Vereinbarung über die Standesregeln zur Sorgfaltspflicht der Banken (VSB 27)")
    de20 = sbvg.classify_sbvg_pdf("Vereinbarung über die Standesregeln zur Sorgfaltspflicht der Banken (VSB 20) (2020)")
    fr27 = sbvg.classify_sbvg_pdf("Convention relative à l’obligation de diligence des banques (CDB 27)")
    assert de27["doc_number"] == de20["doc_number"] == fr27["doc_number"] == "SBVg VSB"
    assert de27["doc_type"] == "richtlinie"
    # Whichever edition is in force as of today (VSB 20 until 2027-01-01,
    # then VSB 27 — see sbvg._VSB_CURRENT_EDITION) gets "in Kraft"; the
    # other edition gets "aufgehoben", exactly one of the two.
    current, other = (de20, de27) if sbvg._VSB_CURRENT_EDITION == "20" else (de27, de20)
    assert "in Kraft" in current["topics"] and "aufgehoben" not in current["topics"]
    assert "aufgehoben" in other["topics"] and "in Kraft" not in other["topics"]


def test_sbvg_classify_kommentar_shares_its_own_doc_number():
    k27 = sbvg.classify_sbvg_pdf("Kommentar zur Vereinbarung über die Standesregeln zur Sorgfaltspflicht der Banken (VSB 27)")
    k20_it = sbvg.classify_sbvg_pdf("Commento alla convenzione relativa all'obbligo di diligenza delle banche CDB 20")
    assert k27["doc_number"] == k20_it["doc_number"] == "SBVg Kommentar zur VSB"
    assert k27["doc_type"] == "bericht"


def test_sbvg_classify_leading_cases_period_is_language_invariant():
    de = sbvg.classify_sbvg_pdf(
        "Leading Cases der Aufsichtskommission VSB 1. Halbjahr 2026 (1. Januar bis 30. Juni 2026)")
    fr = sbvg.classify_sbvg_pdf(
        "Leading Cases (cas d’école) de la Commission de surveillance 1er semestre 2026 "
        "(1er janvier au 30 juin 2026)")
    assert de["doc_number"] == fr["doc_number"] == "SBVg Leading Cases H1 2026"
    assert de["doc_type"] == "rechtsprechung"


def test_sbvg_classify_taetigkeitsbericht_year_range_is_language_invariant():
    de = sbvg.classify_sbvg_pdf("Tätigkeitsbericht der Aufsichtskommission zur VSB 1977 - 1980")
    fr = sbvg.classify_sbvg_pdf("Rapport d’activités de la Commission de surveillance de la CDB 1977 - 1980")
    assert de["doc_number"] == fr["doc_number"] == "SBVg Tätigkeitsbericht Aufsichtskommission 1977-1980"
    assert de["doc_type"] == "bericht"


def test_sbvg_classify_esg_richtlinie_editions_kept_separate_by_design():
    old = sbvg.classify_sbvg_pdf(
        "Richtlinien für die Finanzdienstleister zum Einbezug von ESG-Präferenzen und ESG-Risiken "
        "bei der Anlageberatung und Vermögensverwaltung (2023)")
    new_en = sbvg.classify_sbvg_pdf(
        "Guidelines for the financial service providers on the integration of ESG-preferences and "
        "ESG-risks and the prevention of greenwashing in investment advice and portfolio management (2025)")
    assert old["doc_type"] == new_en["doc_type"] == "richtlinie"
    assert old["doc_number"] != new_en["doc_number"]  # the SBVg renamed the guideline between editions
    assert "2023" in old["doc_number"] and "2025" in new_en["doc_number"]


def test_sbvg_classify_energy_and_mortgage_richtlinien_are_language_invariant():
    de = sbvg.classify_sbvg_pdf("Richtlinien für Anbieter von Hypotheken zur Förderung der Energieeffizienz (2022)")
    fr = sbvg.classify_sbvg_pdf(
        "Directives pour les fournisseurs d’hypothèques relatives à l’amélioration "
        "de l’efficacité énergétique des bâtiments (2022)")
    it = sbvg.classify_sbvg_pdf(
        "Direttive per gli offerenti di ipoteche per la promozione dell’efficienza energetica (2022)")
    assert de["doc_number"] == fr["doc_number"] == it["doc_number"] == "SBVg Richtlinien Energieeffizienz Hypotheken"

    de_min = sbvg.classify_sbvg_pdf("Richtlinien betreffend Mindestanforderungen bei Hypothekarfinanzierungen (2023)")
    fr_min = sbvg.classify_sbvg_pdf(
        "Directives relatives aux exigences minimales pour les financements hypothécaires (2023)")
    assert de_min["doc_number"] == fr_min["doc_number"] == "SBVg Richtlinien Mindestanforderungen Hypothekarfinanzierungen"

    de_pledge = sbvg.classify_sbvg_pdf(
        "Richtlinien für die Prüfung, Bewertung und Abwicklung grundpfandgesicherter Kredite (2023)")
    assert de_pledge["doc_number"] == "SBVg Richtlinien Grundpfandgesicherte Kredite"


def test_sbvg_classify_faq_and_study_and_onepager_are_language_invariant():
    faq_de = sbvg.classify_sbvg_pdf(
        "Richtlinien für die Finanzdienstleister zum Einbezug von ESG-Präferenzen und ESG-Risiken und "
        "zur Prävention von Greenwashing bei der Anlageberatung und Vermögensverwaltung – FAQ (2026)")
    faq_it = sbvg.classify_sbvg_pdf(
        "Direttive per i fornitori di servizi finanziari in materia di inclusion delle preferenze dei "
        "rischi nonché di prevenzione delle pratiche di greenwashing nelle attività di consulenza in "
        "investimenti e di gestione patrimoniale FAQ")  # IT title omits "ESG" itself
    assert faq_de["doc_type"] == faq_it["doc_type"] == "faq"
    assert faq_de["doc_number"] == faq_it["doc_number"] == "SBVg Richtlinien ESG-Präferenzen FAQ"

    study_de = sbvg.classify_sbvg_pdf("Wie Banken in der Schweiz die ESG-Richtlinien in der Vermögensverwaltung umsetzen")
    study_en = sbvg.classify_sbvg_pdf("How banks in Switzerland implement ESG guidelines in portfolio management")
    assert study_de["doc_type"] == study_en["doc_type"] == "bericht"
    assert study_de["doc_number"] == study_en["doc_number"] == "SBVg Studie ESG-Richtlinien Umsetzung"
    # not misclassified as a Richtlinie just because its own title mentions "guidelines"/"ESG-Richtlinien"
    assert study_en["doc_number"] != "SBVg guidelines"

    one_de = sbvg.classify_sbvg_pdf("Onepager: Selbstregulierungen im Bereich «Sustainable Finance» (2025)")
    one_fr = sbvg.classify_sbvg_pdf("Onepager: Autorégulations en matière de finance durable (2025)")
    assert one_de["doc_number"] == one_fr["doc_number"] == "SBVg Onepager Sustainable Finance"
    assert one_de["doc_type"] == "merkblatt"


def test_sbvg_classify_changelog_is_not_the_main_index_table():
    index = sbvg.classify_sbvg_pdf("Übersicht über die Richtlinien der SBVg (01.09.2023)")
    changelog = sbvg.classify_sbvg_pdf(
        "Übersicht Änderungen Richtlinien zum Einbezug von ESG-Präferenzen und ESG-Risiken und "
        "Greenwashing-Prävention (2025)")
    assert index["doc_number"] == "SBVg Übersicht Richtlinien"
    assert changelog["doc_number"] == "SBVg Übersicht Änderungen ESG-Richtlinien"
    assert index["doc_number"] != changelog["doc_number"]
    assert index["doc_type"] == changelog["doc_type"] == "bericht"


def test_sbvg_classify_blockchain_leitfaden():
    de = sbvg.classify_sbvg_pdf("Leitfaden der SBVg zur Eröffnung von Firmenkonti für DLT-Unternehmen (2019) (PDF)")
    fr = sbvg.classify_sbvg_pdf("Guide pratique de l’ASB concernant l’ouverture de comptes d’entreprise pour des sociétés TRD (PDF)")
    assert de["doc_number"] == fr["doc_number"] == "SBVg Leitfaden Firmenkonti DLT-Unternehmen"
    assert de["doc_type"] == "merkblatt"


def test_sbvg_doc_type_enum_subset():
    # Every doc_type this module emits must already be registered in
    # mcp_server.py's search_practice doc_type enum (checked by hand against
    # that list — this test pins the set so a future edit here notices).
    seen = set()
    for fn in sorted(p.name for p in FIX.glob("sbvg_*.html")):
        for t in sbvg.parse_pdf_links(_read(fn)):
            info = sbvg.classify_sbvg_pdf(t["title"])
            if info:
                seen.add(info["doc_type"])
    assert seen <= {"richtlinie", "bericht", "rechtsprechung", "faq", "merkblatt"}


def test_sbvg_make_doc_id_uses_sb_id():
    scraper = sbvg.SbvgScraper.__new__(sbvg.SbvgScraper)
    doc_id = sbvg.SbvgScraper._make_doc_id(scraper, {"sb_id": "geldwaesche_vsb27_de"})
    assert doc_id == "sbvg_geldwaesche_vsb27_de"


def test_sbvg_registered_in_runner():
    from scrapers.practice.runner import ENABLED_SCRAPERS
    assert "sbvg" in ENABLED_SCRAPERS
