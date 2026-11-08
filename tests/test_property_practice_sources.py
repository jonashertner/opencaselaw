"""Property-law practice sources (2026-11-08): BWO Mitteilungen zum
Mietrecht (cantonal tenancy-law decision extracts) and EGBA (federal land
register + Lex Koller + BGBB + ship register guidance). Offline: golden
HTML fixtures captured from the live pages on 2026-11-08 (bj.admin.ch
fixtures have their large __NUXT_DATA__ hydration payload stripped — not
needed by sdweb.download_items, which parses the rendered anchor markup);
the BWO fixtures are a minimal synthetic Nuxt payload (same shape as the
real one) and a short, hand-trimmed excerpt of the real Band 64 PDF text,
not a captured PDF."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scrapers.practice import bwo_mietrecht as bwo  # noqa: E402
from scrapers.practice import egba  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "practice"


def _read(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


# ── BWO: discovery (Nuxt payload) ──────────────────────────────────────

def test_bwo_file_teaser_items_and_volume_fields():
    items = bwo.file_teaser_items(_read("bwo_mitteilungen_de.html"))
    assert len(items) == 3
    titles = {i["title"] for i in items}
    assert titles == {"Mitteilungen zum Mietrecht 64", "Mitteilungen zum Mietrecht 63",
                       "Mitteilungen zum Mietrecht 21"}
    newest = next(i for i in items if i["title"].endswith("64"))
    assert bwo.band_number(newest) == "64"
    assert bwo.band_year(newest) == "2025"
    assert newest["filePreviewUrl"].startswith("https://backend.bwo.admin.ch/fileservice/")
    oldest = next(i for i in items if i["title"].endswith("21"))
    assert bwo.band_number(oldest) == "21" and bwo.band_year(oldest) == "1988"


def test_bwo_file_teaser_items_raises_on_unparsable_page():
    # discover_documents() catches this and logs + returns; the bare
    # function surfaces the failure rather than silently returning []
    import pytest
    with pytest.raises(ValueError):
        bwo.file_teaser_items("<html><body>no nuxt data here</body></html>")


# ── BWO: the extract splitter (pure function, short text fixture) ──────

def test_bwo_split_extracts_finds_both_extracts():
    text = _read("bwo_sample_extracts.txt")
    extracts = bwo.split_extracts(text)
    assert len(extracts) == 2


def test_bwo_split_extracts_german_extract_fields():
    text = _read("bwo_sample_extracts.txt")
    ex = bwo.split_extracts(text)[0]
    assert ex["language"] == "de"
    assert ex["court"] == "Obergerichts des Kantons Zürich"
    assert ex["date"] == "2024-11-13"
    assert ex["docket"] == "NG240007-O/U"
    assert ex["articles"] == ["Art. 260 OR"]
    # body runs from the start of the segment through the closing citation
    assert ex["body_text"].startswith("Schweizerische Eidgenossenschaft")
    assert ex["body_text"].endswith("(NG240007-O/U)")
    # page-header / bare-page-number noise is stripped
    assert "BWO | Mitteilungen zum Mietrecht" not in ex["body_text"]


def test_bwo_split_extracts_french_extract_fields():
    text = _read("bwo_sample_extracts.txt")
    ex = bwo.split_extracts(text)[1]
    assert ex["language"] == "fr"
    assert ex["court"] == "Cour de Justice de Genève"
    assert ex["date"] == "2024-06-25"
    assert ex["docket"] == "ACJC/831/2024"
    assert ex["articles"] == ["Art. 148 CPC", "Art. 206 CPC", "Art. 16 CC"]
    assert "Exposé des faits" in ex["body_text"]
    assert ex["body_text"].endswith("(ACJC/831/2024)")


def test_bwo_split_extracts_empty_without_citations():
    assert bwo.split_extracts("Just some prose with no court citation at all.") == []


def test_bwo_find_citations_recognises_old_bracketed_form():
    # the pre-2000 "(Urteil ... vom DATE in Sachen X gegen Y.)" form, with
    # no docket, that older Bände use instead of a trailing "(docket)"
    text = ("Irrelevant preceding prose.\n"
            "(Urteil des Bezirksgerichtes Frauenfeld vom 4. September 1987 "
            "in Sachen M. gegen K.)\n")
    cits = bwo.find_citations(text)
    assert len(cits) == 1
    assert cits[0]["language"] == "de"
    assert cits[0]["court"] == "Bezirksgerichtes Frauenfeld"
    assert bwo._verb_lang("Urteil") == "de" and bwo._verb_lang("Decisione") == "it"
    assert bwo._verb_lang("Arrêt") == "fr"


def test_bwo_clean_body_drops_noise_lines():
    seg = "Real sentence one.\n27\nBWO | Mitteilungen zum Mietrecht , Band 64\nReal sentence two."
    cleaned = bwo.clean_body(seg)
    assert "27" not in cleaned.split("\n")
    assert "BWO | Mitteilungen zum Mietrecht" not in cleaned
    assert "Real sentence one." in cleaned and "Real sentence two." in cleaned


# ── EGBA: classification / language detection helpers ──────────────────

def test_egba_classify_doc_type():
    assert egba.classify_doc_type("Weisung 1/2016 über die Langzeitsicherung von Grundbuchdaten") == "weisung"
    assert egba.classify_doc_type("Erläuterungen zur Änderung der Grundbuchverordnung") == "erlaeuterung"
    assert egba.classify_doc_type("Erwerb von Grundstücken durch Personen im Ausland. "
                                   "Wegleitung für die Grundbuchämter") == "wegleitung"
    assert egba.classify_doc_type("Informationsschreiben vom 4. April 2019 über das BewG und Brexit") == "mitteilung"
    assert egba.classify_doc_type("Update vom 18. Dezember 2020 zum Informationsschreiben") == "mitteilung"
    assert egba.classify_doc_type("Bestätigung betreffend die wirtschaftlich Berechtigten") == "formular"
    assert egba.classify_doc_type("Anmeldung zur Aufnahme eines Schiffes") == "formular"
    assert egba.classify_doc_type("Bericht Querschnittsinspektion 2024") == "bericht"
    assert egba.classify_doc_type("Verzeichnis der anerkannten Körperschaften (Art. 79 BGBB)") == "bericht"


def test_egba_detect_lang_from_title_text_not_page():
    # the whole point of detect_lang: a French-only report relisted
    # unchanged on the German page must still come back "fr"
    assert egba.detect_lang("Rapport de l'inspection de l'office du registre "
                             "foncier de la Gruyère, Bulle, Canton de Fribourg", hint="de") == "fr"
    assert egba.detect_lang("Inspektionsbericht Amt für das Grundbuch, Kanton Uri", hint="fr") == "de"
    assert egba.detect_lang("Direttiva 1/2016 sulla salvaguardia a lungo termine", hint="de") == "it"
    assert egba.detect_lang("Weisung 1/2016 über die Langzeitsicherung", hint="de") == "de"


def test_egba_stand_am_beats_a_cited_statutes_own_date():
    # live dry-run finding: the Art. 79 BGBB register's cover line cites
    # the STATUTE's enactment date (1991) before its own "Stand am" date
    # (2010); first_date_iso's "first date wins" rule would misdate the
    # whole row 1991, so _normalize checks stand_am_iso first
    text = ("Verzeichnis der ... nach Artikel 79 des Bundesgesetzes vom 4. "
            "Oktober 1991 über das bäuerliche Bodenrecht (BGBB) anerkannten "
            "Körperschaften\n(Stand am 20. Oktober 2010)\n")
    assert egba.stand_am_iso(text) == "2010-10-20"
    assert egba.first_date_iso(text) == "1991-10-04"  # the trap, left as-is


def test_egba_dam_key_and_same_doc():
    url_de = "https://www.bj.admin.ch/dam/de/sd-web/vhl3HbWikJyP/wegleitung.pdf"
    url_fr = "https://www.bj.admin.ch/dam/fr/sd-web/vhl3HbWikJyP/wegleitung.pdf"
    assert egba._dam_key(url_de) == egba._dam_key(url_fr) == "vhl3HbWikJyP/wegleitung.pdf"
    assert egba._is_same_doc("Übersicht Inspektionen EGBA",
                              "Übersicht Inspektionen EGBA / Aide-mémoire relatif aux inspections de l'OFRF")
    assert not egba._is_same_doc("Weisung 1/2016 über die Langzeitsicherung von Grundbuchdaten",
                                  "Directive 1/2016 relative à la sauvegarde à long terme des données")


# ── EGBA: grouping across the live-captured pages ───────────────────────

def test_egba_weisungen_trilingual_grouping_and_language_fix():
    pages = {"weisungen": {
        "de": _read("egba_weisungen_de.html"),
        "fr": _read("egba_weisungen_fr.html"),
        "it": _read("egba_weisungen_it.html"),
    }}
    stubs = egba.group_documents(pages)
    by_number: dict[str, list[dict]] = {}
    for s in stubs:
        by_number.setdefault(s["doc_number"], []).append(s)

    # Weisung 1/2016 is genuinely trilingual: 3 distinct titles, 3 rows,
    # one shared doc_number taken from the German title, doc_type weisung
    langzeit = next(v for k, v in by_number.items() if "Langzeitsicherung" in k)
    assert {s["language"] for s in langzeit} == {"de", "fr", "it"}
    assert all(s["doc_type"] == "weisung" for s in langzeit)
    assert len({s["doc_number"] for s in langzeit}) == 1

    # the Fribourg/Gruyère inspection report is French-only and relisted
    # unchanged on the DE and IT pages too: one row, correctly tagged fr
    fribourg = [s for s in stubs if "Gruyère" in s["title"]]
    assert len(fribourg) == 1
    assert fribourg[0]["language"] == "fr"
    assert fribourg[0]["doc_type"] == "bericht"

    # the Uri report is German-only, same pattern, tagged de
    uri = [s for s in stubs if "Uri" in s["title"]]
    assert len(uri) == 1 and uri[0]["language"] == "de"

    # the cross-cutting aide-mémoire: German original + French translation,
    # NOT a third row for the Italian page's "<de> / <fr>" fallback title
    overview = [s for s in stubs if "Inspektionen EGBA" in s["title"]
                or "Aide-mémoire" in s["title"]]
    assert len(overview) == 2
    assert {s["language"] for s in overview} == {"de", "fr"}
    assert len({s["doc_number"] for s in overview}) == 1


def test_egba_bgbb_french_only_annexe_and_combined_it_title():
    pages = {"bgbb": {
        "de": _read("egba_bgbb_de.html"),
        "fr": _read("egba_bgbb_fr.html"),
        "it": _read("egba_bgbb_it.html"),
    }}
    stubs = egba.group_documents(pages)
    titles = [s["title"] for s in stubs]

    # the French-only Annexe II is not listed on the German page at all —
    # only found by also scanning FR/IT, and kept as its own fr row
    annexe = [s for s in stubs if "Annexe II" in s["title"]]
    assert len(annexe) == 1 and annexe[0]["language"] == "fr"

    # the Bundesrat report: DE original + FR translation, and the IT
    # page's combined "<de title> / <fr title>" listing folds into one of
    # the two rather than becoming a spurious third row
    rapport = [s for s in stubs if "administrative" in s["title"].lower()
               or "Vereinfachungen" in s["title"]]
    assert len(rapport) == 2
    assert {s["language"] for s in rapport} == {"de", "fr"}
    assert len({s["doc_number"] for s in rapport}) == 1

    # the Art. 79 register: three genuinely distinct titles -> 3 rows
    register = [s for s in stubs if "organismes" in s["title"] or "Körperschaften" in s["title"]
                or "organismi" in s["title"]]
    assert {s["language"] for s in register} == {"de", "fr", "it"}
    assert len({s["doc_number"] for s in register}) == 1
    assert all(s["topics"][1:] == ["BGBB", "Bäuerliches Bodenrecht"] for s in register)


def test_egba_schiffsregister_untranslated_forms_collapse_to_one_row_each():
    pages = {"schiffsregister": {
        "de": _read("egba_schiffsregister_de.html"),
        "fr": _read("egba_schiffsregister_fr.html"),
    }}
    stubs = egba.group_documents(pages)
    # both forms are German-only text reused unchanged on the FR page —
    # each dam-hash group must collapse to exactly one row
    assert len(stubs) == 2
    assert all(s["language"] == "de" for s in stubs)
    assert all(s["doc_type"] == "formular" for s in stubs)


def test_egba_single_page_group_minimal():
    pages = {"behoerden": {"de": _read("egba_behoerden_de.html")}}
    stubs = egba.group_documents(pages)
    assert len(stubs) == 3
    assert {s["doc_type"] for s in stubs} == {"wegleitung", "mitteilung"}
    assert all(s["topics"][1:] == ["Lex Koller", "BewG", "Grundbuch"] for s in stubs)


def test_citation_with_court_wrapped_over_two_lines_is_a_boundary():
    """Geneva's Chambre des baux et loyers wraps after "baux"; without the
    wrap the boundary was missed and four decisions shared one row."""
    text = ("… que de besoin.\nArrêt de la Cour de justice du canton de Genève, Chambre des baux\n"
            "et loyers du 16 avril 2018 (ACJC/458/2018)\n2. Résiliation\n…\n"
            "(Urteil des Bezirksgerichtspräsidiums Oberlandquart\nvom 6. Februar 1995 i.S. K. c/ P.-Stiftung)\n")
    cites = bwo.find_citations(text)
    assert [c["docket"] for c in cites][:1] == ["ACJC/458/2018"]
    assert cites[0]["date_raw"] == "16 avril 2018"
    assert len(cites) == 2 and "Oberlandquart" in cites[1]["court"]


def test_citation_never_spans_a_blank_line():
    text = "Urteil des Obergerichts\n\nZürich vom 3. März 2020 (LB200001)"
    assert bwo.find_citations(text) == []


def test_decision_cited_inside_the_reasoning_is_not_a_boundary():
    text = ("Fällen angenommen, in denen die Mieter während \nzehn Monaten (Urteil des Bun­\n"
            "desgerichts 4A_664/2018 vom 12. März 2019 E. 3) nichts bezahlten.\n"
            "Das Urteil ist rechtskräftig, \nbestätigt mit Entscheid des Obergerichts Zug, vom \n"
            "20. Dezember 2018 (Z1 2018 18), und wurde publiziert.\n"
            "Urteil des Kantonsgerichts Zug vom 5. Juni 2018 (EV 2018 32)\n")
    cites = bwo.find_citations(text)
    assert [c["docket"] for c in cites] == ["EV 2018 32"]
