"""Tier 2 practice sources (2026-11-08): the NWI/OSK Erlasssammlung
(strafvollzug), the rhf.admin.ch checklists, circulars and Merkblätter in
DE/FR/IT (bj_rechtshilfe), the BE/VD/FR/GE prosecutors' directives and the
BA / AB-BA reports (strafverfolgung). Offline: golden fixtures captured from
the live sites on 2026-11-08 (HTML with scripts and styles stripped; the
konkordate tree as the raw /ajax response bytes)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scrapers.practice import bj_rechtshilfe as bjr  # noqa: E402
from scrapers.practice import strafverfolgung as sv  # noqa: E402
from scrapers.practice import strafvollzug as svz  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "practice"


def _read(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


# ── NWI/OSK Erlasssammlung (erlasse.konkordate.ch) ───────────────────────

def _konk_erlasse():
    tree = svz.konk_tree_from_ajax((FIX / "strafvollzug_konkordate_tree.bin").read_bytes())
    assert tree is not None
    return svz.konk_erlasse(tree)


def test_konk_request_body_matches_the_captured_wire_format():
    body = svz.konk_tree_request("t" * 262)
    # header, call name "home", flags, then the token length as 0x81 0x06
    assert body[:7] == bytes([135, 255, 3, 0, 11, 0, 4])
    assert body[7:11] == b"home"
    assert body[11:16] == bytes([1, 1, 0, 0x81, 0x06])
    assert body[16:278] == b"t" * 262
    assert body[278:] == bytes([0, 1, 0, 0, 1, 1, 0, 1, 1, 1])
    assert svz.konk_tree_request("t" * 100)[14] == 100      # short lengths: one byte


def test_konk_tree_holds_all_six_categories():
    erlasse = _konk_erlasse()
    assert len(erlasse) == 74
    cats = {e["category"] for e in erlasse}
    assert cats == {"I. Grundlagen", "II. Reglemente", "III. Tarife, Gebühren",
                    "IV. Richtlinien", "V. Merkblätter", "VI. Standards"}
    assert sum(e["type"] == "Richtlinie" for e in erlasse) == 26
    assert sum(1 for e in erlasse if e["pdf_path"]) == 72


def test_konk_richtlinie_fields_and_stub():
    e = next(x for x in _konk_erlasse() if x["number"] == "IV.16")
    assert e["title"] == "Richtlinie betreffend die Ausgangs- und Urlaubsgewährung"
    assert e["prefix"] == "NWI & OSK"
    assert e["lex_path"] == "lex-IV_16"
    assert e["pdf_path"].startswith("/blb-") and e["pdf_path"].endswith(".downloadpayload")
    stub = svz.konk_stub(e)
    assert stub["doc_number"] == "NWI-OSK Erlass IV.16"
    assert stub["doc_type"] == "richtlinie"
    assert stub["issuing_authority"] == "NWI-OSK"
    assert stub["url"] == "https://erlasse.konkordate.ch/lex-IV_16"
    assert stub["pdf_url"].startswith("https://erlasse.konkordate.ch/blb-")
    # each in-force version is its own row
    assert stub["sv_id"] == f"konk_iv_16_{e['effective']}"
    assert stub["date"] == e["beschluss"]


def test_konk_doc_types_and_dates():
    by_no = {e["number"]: e for e in _konk_erlasse()}
    i03 = svz.konk_stub(by_no["I.03"])
    assert i03["doc_type"] == "konkordat"
    assert i03["date"] == "2023-10-27"           # Beschluss 27. Oktober 2023
    assert by_no["I.03"]["inkraft"] == "2024-01-01"
    assert svz.konk_stub(by_no["V.21"])["doc_type"] == "merkblatt"
    assert svz.konk_stub(by_no["VI.01"])["doc_type"] == "richtlinie"   # Standards
    reglemente = [svz.konk_stub(e) for e in by_no.values() if e["type"] == "Reglement"]
    assert reglemente and all(s is None or s["doc_type"] == "erlass" for s in reglemente)


def test_konk_without_pdf_is_skipped():
    no_pdf = [e for e in _konk_erlasse() if not e["pdf_path"]]
    assert len(no_pdf) == 2
    assert all(svz.konk_stub(e) is None for e in no_pdf)


def test_konk_garbage_answer_is_none():
    assert svz.konk_tree_from_ajax(b"\x87\xff\x34\x00") is None
    assert svz.konk_tree_from_ajax(b"") is None


# ── rhf.admin.ch: Wegleitungen, Rundschreiben, Checklisten (DE/FR/IT) ───

def _rhf_rows():
    rows, taken = [], set()
    for lang, url in bjr.WEGLEITUNGEN_PAGES:
        name = "bj_rechtshilfe_wegleitungen_%s.html" % lang
        for it in bjr._dam_items(_read(name), url):
            c = bjr.classify_rhf_item(it["pdf_url"], lang)
            if c is None:
                continue
            key = f"{c['key']}_{c['language']}"
            if key not in taken:
                taken.add(key)
                rows.append((key, c, it))
    return rows


def test_rhf_pages_have_every_family_in_each_language():
    rows = _rhf_rows()
    keys = {k for k, _, _ in rows}
    assert len(rows) == 82
    for lang in ("de", "fr", "it"):
        for n in range(1, 8):
            assert f"rhf_rs_{n}_{lang}" in keys
        assert f"wegleitung_strafsachen_{lang}" in keys
    # the ids the first build gave the Wegleitungen are kept
    assert {"wegleitung_strafsachen_de", "wegleitung_strafsachen_en",
            "wegleitung_italien_de"} <= keys


def test_rhf_suffixed_files_taken_once_with_their_own_language():
    rows = _rhf_rows()
    ar = [k for k, _, _ in rows if k.startswith("rhf_asset_recovery_")]
    assert sorted(ar) == ["rhf_asset_recovery_ar", "rhf_asset_recovery_de", "rhf_asset_recovery_en",
                          "rhf_asset_recovery_es", "rhf_asset_recovery_fr", "rhf_asset_recovery_it",
                          "rhf_asset_recovery_uk"]
    en = next(it for k, _, it in rows if k == "wegleitung_strafsachen_en")
    assert "/dam/de/" in en["pdf_url"]          # from the first (DE) page


def test_rhf_personenfahndung_left_to_the_auslieferung_family():
    for lang, url in bjr.WEGLEITUNGEN_PAGES:
        for it in bjr._dam_items(_read("bj_rechtshilfe_wegleitungen_%s.html" % lang), url):
            if "checkliste-personenfahndung" in it["pdf_url"]:
                assert bjr.classify_rhf_item(it["pdf_url"], lang) is None


def test_rhf_unknown_file_is_not_given_a_made_up_number():
    assert bjr.classify_rhf_item("https://www.rhf.admin.ch/dam/de/sd-web/x/neues-dokument.pdf", "de") is None


def test_rhf_rundschreiben_doc_number_and_type():
    c = bjr.classify_rhf_item("https://www.rhf.admin.ch/dam/fr/sd-web/x/rundschreiben-sharing.pdf", "fr")
    assert c["doc_number"] == "BJ Rechtshilfe Rundschreiben Nr. 7"
    assert c["doc_type"] == "rundschreiben" and c["language"] == "fr"


def test_rhf_migration_stamp_falls_back_to_the_letterhead_date():
    s = bjr.BjRechtshilfeScraper.__new__(bjr.BjRechtshilfeScraper)
    stub = {"title": "Nr. 3", "doc_number": "BJ Rechtshilfe Rundschreiben Nr. 3", "date": "",
            "date_from_text": True, "language": "de", "doc_type": "rundschreiben", "bj_id": "rhf_rs_3_de"}
    doc = bjr.BjRechtshilfeScraper._normalize(s, stub, "Bundesamt für Justiz\nBern, 24. Oktober 2013\n"
                                                     "Gemäss Übereinkommen vom 20. April 1959 ...")
    assert doc["date"] == "2013-10-24"


def test_edition_year_reads_italian_ordinal_words():
    assert bjr.edition_year("Ufficio federale di giustizia UFG\nPrima edizione 2003\nAssistenza") == "2003"
    assert bjr.edition_year("1re édition 2003") == "2003"


# ── BE Generalstaatsanwaltschaft ─────────────────────────────────────────

def test_be_weisungen_de_and_fr_share_file_numbers():
    de = sv.parse_be(_read("strafverfolgung_be_de.html"), "de", sv._BE_PAGES[0][1])
    fr = sv.parse_be(_read("strafverfolgung_be_fr.html"), "fr", sv._BE_PAGES[1][1])
    assert len(de) == len(fr) == 26
    assert [x["number"] for x in de] == [x["number"] for x in fr]
    first = de[0]
    assert first["title"] == "Weisung Aktenführung und Aktenordnung"
    assert first["number"] == "2.02" and first["doc_type"] == "weisung"
    assert first["section"].startswith("2. Titel")
    assert first["pdf_url"].startswith("https://www.staw.justice.be.ch/content/dam/")
    assert sum(x["doc_type"] == "richtlinie" for x in de) == 5
    assert sum(x["doc_type"] == "richtlinie" for x in fr) == 5


def test_be_crawl_delay_registered():
    assert sv._CRAWL_DELAY["www.staw.justice.be.ch"] == 10.0


# ── VD / FR / GE Ministère public ────────────────────────────────────────

def test_vd_directives_numbers_and_file_dates():
    items = sv.parse_vd(_read("strafverfolgung_vd.html"), sv._VD_PAGE)
    assert len(items) == 29
    by_no = {x["number"]: x for x in items}
    assert by_no["2.2bis"]["date"] == "2021-03-01"
    # a revised file appends its new date: the last one wins
    assert by_no["1.2bis"]["date"] == "2025-12-09"
    assert not any("TABLE_DES_MATIERES" in x["pdf_url"] for x in items)


def test_fr_directives_share_keys_across_languages():
    fr = sv.parse_fr(_read("strafverfolgung_fr_fr.html"), "fr", sv._FR_PAGES[0][1])
    de = sv.parse_fr(_read("strafverfolgung_fr_de.html"), "de", sv._FR_PAGES[1][1])
    assert len(fr) == len(de) == 29
    assert {x["key"] for x in fr} == {x["key"] for x in de}
    reg = next(x for x in fr if x["key"] == "Reglement 2011")
    assert reg["doc_type"] == "erlass" and reg["date"] == "2011-03-14"
    d11 = next(x for x in de if x["key"] == "Richtlinie 1.1")
    assert d11["date"] == "2010-12-22" and not d11["title"].endswith("k")
    assert next(x for x in fr if x["key"] == "Richtlinie 1.1")["pdf_url"].startswith("https://www.fr.ch/document/")


def test_ge_directives_and_annexes():
    items = sv.parse_ge(_read("strafverfolgung_ge.html"), sv._GE_PAGE)
    assert len(items) == 49
    numbers = [x["number"] for x in items]
    assert len(set(numbers)) == 49                 # the two D.7 annexes stay apart
    assert numbers[0] == "A.1"
    annexes = [x for x in items if x["doc_type"] == "weisung_anhang"]
    assert len(annexes) == 4
    assert not any("table-matieres" in x["pdf_url"] for x in items)


# ── BA / AB-BA ───────────────────────────────────────────────────────────

def test_ba_reports_2006_to_2025():
    items = sv.parse_ba(_read("strafverfolgung_ba_de.html"), sv._BA_PAGES[0][1])
    years = [x["year"] for x in items]
    assert len(items) == 20 and years[0] == "2025" and years[-1] == "2006"


def test_abba_reports_and_inspections():
    tb = sv.parse_abba(_read("strafverfolgung_abba_berichte_de.html"), sv._ABBA + "/berichte/")
    assert len(tb) == 15 and {x["year"] for x in tb} == {str(y) for y in range(2011, 2026)}
    assert all(x["pdf_url"].startswith("https://ab-ba.admin.ch/wp-content/") for x in tb)
    insp = sv.parse_abba(_read("strafverfolgung_abba_inspektion_fr.html"), sv._ABBA + "/fr/rapports-dinspection/")
    assert len(insp) == 9
    assert "­" not in insp[0]["title"]


# ── grundbuch_kantone: GE, VS, AR, GR, LU ────────────────────────────────

import re  # noqa: E402

from scrapers.practice import grundbuch_kantone as gbk  # noqa: E402


def test_ge_orf_directives_without_models_or_code_lists():
    rows = gbk.ge_orf_rows(_read("grundbuch_ge_orf_partenaires.html"), gbk._GE_ORF_PAGE)
    titles = [r["title"] for r in rows]
    assert len(rows) == 11
    assert "Directive concernant la novation des créances cédulaires" in titles
    assert "Instructions AGG - servitudes" in titles
    assert not any(re.match(r"\d\d - ", t) for t in titles)          # réquisition models
    assert not any(t.startswith(("Codes annotations", "Statistiques", "Liste institutions", "Mots-clés"))
                   for t in titles)
    assert gbk.ge_doc_type("Directive concernant les mises en communication") == "weisung"
    assert gbk.ge_doc_type("Principes applicables aux servitudes") == "merkblatt"


def test_ge_document_page_download_link():
    assert gbk.ge_download_url(_read("grundbuch_ge_orf_document.html"), "https://www.ge.ch/document/x") == \
        "https://www.ge.ch/document/25698/telecharger"
    assert gbk.ge_download_url("<html></html>", "https://www.ge.ch/") == ""


def test_vs_notaires_listing_and_article():
    html = _read("grundbuch_vs_notaires.html")
    refs = gbk.vs_article_refs(html)
    assert len(refs) == 6 and refs[0] == {"group": "33340762", "article": "52102168"}
    pages = gbk.vs_page_links(html, gbk._VS_NOTAIRES_PAGE)
    assert len(pages) == 3 and all("_cur=" in p for p in pages)
    art = gbk.parse_vs_article(_read("grundbuch_vs_article.html"))
    assert art["title"] == "Exigences relatives aux plans de servitudes (art. 732 al. 2 CC)"
    assert art["date"] == "2026-11-05"
    assert art["body_text"].startswith("Afin d’uniformiser la pratique")
    assert "ATF 138 III 742" in art["body_text"]


def test_ar_kreisschreiben_without_model_deeds():
    rows = gbk.pdf_links(_read("grundbuch_ar.html"), gbk._AR_PAGE, re.compile(r"^Kreisschreiben\b"))
    assert [r["title"] for r in rows] == ["Kreisschreiben Register-Schuldbrief", "Kreisschreiben Erwachsenenschutz",
                                          "Kreisschreiben Beglaubigung Unterschriften"]


def test_gr_richtlinie_only():
    rows = gbk.pdf_links(_read("grundbuch_gr.html"), gbk._GR_PAGE,
                         re.compile(r"Richtlinie|Weisung|Kreisschreiben|Merkblatt"))
    assert [r["title"] for r in rows] == ["Richtlinie Datenaustausch AVGBS im Kanton Graubünden"]


def test_lu_weisungen_page_without_breadcrumb_and_its_pdf():
    page = gbk.parse_lu_page(_read("grundbuch_lu_weisungen.html"))
    assert page["title"] == "Weisungen und Entscheide"
    assert page["body_text"].startswith("Weisungen und Entscheide Weisung betreffend Mehrämtergeschäfte")
    assert "Herzlich Willkommen" not in page["body_text"]
    pdfs = gbk.pdf_links(_read("grundbuch_lu_weisungen.html"), gbk._LU_PAGES[0],
                         re.compile(r"^Unterlagen|Weisung|Merkblatt|Kreisschreiben"))
    assert [p["title"] for p in pdfs] == ["Unterlagen Dienstbarkeitspläne"]     # not the LGVE rulings
