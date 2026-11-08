"""Inter-cantonal penal-enforcement concordats (strafvollzug: CLDJP +
NWI/OSK) and the NKVF torture-prevention commission (nkvf). Offline: golden
fixtures captured from the live sites 2026-11-08 (CLDJP via its WordPress
media JSON API, NWI/OSK and NKVF via saved HTML)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scrapers.practice import nkvf  # noqa: E402
from scrapers.practice import strafvollzug as sv  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "practice"


def _read(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def _read_json(name: str):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


# ── CLDJP ────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def cldjp_items():
    return {i["id"]: i for i in _read_json("strafvollzug_cldjp_media_page.json")}


def test_cldjp_drops_rosters_minutes_agendas_press_mandates_concepts(cldjp_items):
    dropped_ids = (235, 3330, 1082, 3180, 2018, 1080, 542, 3718)
    for i in dropped_ids:
        title = (cldjp_items[i].get("title") or {}).get("rendered") or cldjp_items[i]["slug"]
        assert sv._cldjp_stub(cldjp_items[i]) is None, f"{i} ({title}) should have been dropped"


def test_cldjp_keeps_reglements_decisions_directives_reports(cldjp_items):
    kept_types = {
        2161: "erlass", 2171: "erlass", 2167: "erlass",          # TIG/EM règlements
        329: "konkordat",                                         # entrée en vigueur du concordat
        313: "weisung", 332: "weisung",                           # décisions/Beschluss/decisione
        2662: "empfehlung",                                       # recommandations
        353: "merkblatt", 354: "merkblatt", 355: "merkblatt",     # Merkblatt/nota/notice trio
        439: "handbuch",                                          # Wegleitung
        424: "bericht", 1037: "bericht",                          # exposé des motifs / rapport
        428: "richtlinie",                                        # "richlinie" misspelling
    }
    for i, expected in kept_types.items():
        stub = sv._cldjp_stub(cldjp_items[i])
        assert stub is not None, f"{i} should be kept"
        assert stub["doc_type"] == expected, (i, stub["doc_type"], expected)


def test_cldjp_organisation_reglement_is_konkordat_not_erlass(cldjp_items):
    # "Règlement d'organisation et de fonctionnement des Conférences CLDJP…"
    # is the concordat organ's own rules of procedure, not an implementing
    # regulation like "Règlement sur le TIG".
    stub = sv._cldjp_stub(cldjp_items[1000])
    assert stub["doc_type"] == "konkordat"


def test_cldjp_reglement_family_shares_doc_number_across_languages(cldjp_items):
    tig_fr = sv._cldjp_stub(cldjp_items[2161])    # Règlement TIG (FR)
    tig_it = sv._cldjp_stub(cldjp_items[2167])    # Regolamento sul LUP (IT)
    em_de = sv._cldjp_stub(cldjp_items[2171])     # Reglement über EM (DE)
    assert tig_fr["doc_number"] == tig_it["doc_number"]
    assert tig_fr["doc_number"] != em_de["doc_number"]
    # a later revision of the same French règlement keeps the same number
    tig_fr_2022 = sv._cldjp_stub(cldjp_items[3229])
    assert tig_fr_2022["doc_number"] == tig_fr["doc_number"]
    assert tig_fr_2022["date"] > tig_fr["date"]


def test_cldjp_language_is_not_a_reglement_coin_flip(cldjp_items):
    # "Règlement"/"Reglement" strip to the same ASCII token in FR and DE, so
    # it must never decide the language on its own.
    assert sv._cldjp_stub(cldjp_items[2161])["language"] == "fr"   # Règlement TIG (no "über")
    assert sv._cldjp_stub(cldjp_items[2171])["language"] == "de"   # Reglement ÜBER EM
    assert sv._cldjp_stub(cldjp_items[2167])["language"] == "it"   # Regolamento
    assert sv._cldjp_stub(cldjp_items[3229])["language"] == "fr"   # Règlement sur le TIG


def test_cldjp_merkblatt_nota_notice_trio_gets_its_own_language_each(cldjp_items):
    assert sv._cldjp_stub(cldjp_items[353])["language"] == "de"   # merkblatt-kkjpd
    assert sv._cldjp_stub(cldjp_items[354])["language"] == "it"   # nota-ccdgp
    assert sv._cldjp_stub(cldjp_items[355])["language"] == "fr"   # notice-ccdjp


def test_cldjp_date_prefers_filename_token_over_upload_date(cldjp_items):
    # "_170330_V190404" -> the edition date (190404), not the 2026 upload date
    assert sv._cldjp_stub(cldjp_items[2161])["date"] == "2019-04-04"
    # a later revision's "_mod221103" wins over the earlier "_170330"
    assert sv._cldjp_stub(cldjp_items[3229])["date"] == "2022-11-03"
    # bare 4-digit year in the slug beats the WP upload date
    assert sv._cldjp_stub(cldjp_items[424])["date"] == "2012"
    assert sv._cldjp_stub(cldjp_items[1037])["date"] == "2015"


def test_cldjp_media_pagination_dedups_and_stops_on_empty_page():
    class FakeResp:
        def __init__(self, data):
            self._data = data

        def raise_for_status(self):
            pass

        def json(self):
            return self._data

    class FakeScraper:
        def __init__(self, pages):
            self._pages = pages
            self.calls = 0

        def get(self, url):
            self.calls += 1
            page = self._pages[self.calls - 1] if self.calls <= len(self._pages) else []
            return FakeResp(page)

    all_items = _read_json("strafvollzug_cldjp_media_page.json")
    page1, page2 = all_items[:5], all_items[5:9]
    scraper = FakeScraper([page1, page2, []])   # 3rd (empty) page signals exhaustion
    seen = list(sv._iter_cldjp_media(scraper))
    assert len(seen) == 9
    assert scraper.calls == 3      # stopped after the empty page

    # defensive: a page that repeats ids already seen contributes no NEW
    # items and also ends the walk, even without an explicit empty page.
    scraper2 = FakeScraper([page1, page1])
    seen2 = list(sv._iter_cldjp_media(scraper2))
    assert len(seen2) == 5
    assert scraper2.calls == 2


# ── NWI/OSK ──────────────────────────────────────────────────────────────

def test_nwiosk_vorlagen_count_and_types():
    html = _read("strafvollzug_nwiosk_vorlagen.html")
    cards = sv._parse_webflow_cards(html, sv._NWIOSK_VORLAGEN)
    assert len(cards) == 23
    by_title = {c["title"]: sv.classify_vorlage(c["title"]) for c in cards}
    assert by_title["Vollzugsauftrag für Strafen und Massnahmen"] == ("formular", "NWI-OSK Vollzugsauftrag", "de")
    assert by_title["Kurzerläuterungen zum Vollzugsplan und Vollzugsbericht"][0] == "merkblatt"
    assert by_title["Basler Kriterienkatalog zur Beurteilung der Legalsprognose 2. Version"][0] == "richtlinie"


def test_nwiosk_richtziele_keeps_only_de_fr_it_en():
    html = _read("strafvollzug_nwiosk_vorlagen.html")
    cards = sv._parse_webflow_cards(html, sv._NWIOSK_VORLAGEN)
    richtziele = {c["title"]: sv.classify_vorlage(c["title"]) for c in cards if "Richtziele" in c["title"]}
    kept_langs = {v[2] for v in richtziele.values() if v is not None}
    assert kept_langs == {"de", "fr", "it", "en"}
    skipped = [t for t, v in richtziele.items() if v is None]
    assert len(skipped) == 4   # spanisch, portugiesisch, serbokroatisch, albanisch


def test_nwiosk_ripol_duplicate_pdf_collapses_to_one_url():
    html = _read("strafvollzug_nwiosk_vorlagen.html")
    cards = sv._parse_webflow_cards(html, sv._NWIOSK_VORLAGEN)
    ripol_urls = {c["pdf_url"] for c in cards if "RIPOL" in c["title"] or "Ripol" in c["pdf_url"]}
    # _parse_webflow_cards itself dedups by url; the catalogue's two labels
    # for the same file collapse to a single card.
    assert len(ripol_urls) == 1


def test_nwiosk_jahresberichte_count_and_three_series():
    html = _read("strafvollzug_nwiosk_jahresberichte.html")
    cards = sv._parse_webflow_cards(html, sv._NWIOSK_JAHRESBERICHTE)
    assert len(cards) == 44
    classified = [sv.classify_jahresbericht(c["title"]) for c in cards]
    assert all(classified)      # every title is recognised
    numbers = {c[1] for c in classified}
    assert any(n.startswith("NWI-OSK Jahresbericht") for n in numbers)
    assert any(n.startswith("NWI Tätigkeitsbericht") for n in numbers)
    assert any(n.startswith("OSK Jahresbericht") for n in numbers)


# ── NKVF ─────────────────────────────────────────────────────────────────

def test_nkvf_visit_language_is_the_titles_own_not_the_listing_pages():
    # the same 7 documents, listed on the DE, FR and IT pages (with the
    # cross-language fallback disclaimer on the "wrong" pages) must all
    # resolve to the SAME real language regardless of which page it came
    # from.
    results = {}
    for page_lang, fname in (("de", "nkvf_berichte_de.html"), ("fr", "nkvf_berichte_fr.html"),
                              ("it", "nkvf_berichte_it.html")):
        items = nkvf.parse_download_items(_read(fname), nkvf._BASE)
        for it in items:
            results.setdefault(it["pdf_url"], set()).add(nkvf.detect_lang(it["title"], page_lang))
    assert all(len(langs) == 1 for langs in results.values()), results
    sion = next(l for u, l in results.items() if "wallis-f" in u.lower())
    farera = next(l for u, l in results.items() if "farera" in u.lower())
    biberbrugg = next(l for u, l in results.items() if "biberbrugg" in u.lower())
    assert sion == {"fr"}
    assert farera == {"it"}
    assert biberbrugg == {"de"}


def test_nkvf_visit_doc_id_is_language_agnostic_so_fallback_copies_collapse():
    # the Sion visit's PDF is listed on the DE page too (no German
    # translation exists) under a "/dam/de/…" URL, while the FR page lists
    # the SAME file under "/dam/fr/…" — only the language segment of the
    # path differs, the hash and filename are identical. doc_id is built
    # from the filename alone, so it must match either way.
    de_items = nkvf.parse_download_items(_read("nkvf_berichte_de.html"), nkvf._BASE)
    fr_items = nkvf.parse_download_items(_read("nkvf_berichte_fr.html"), nkvf._BASE)
    de_url = next(it["pdf_url"] for it in de_items if "wallis-f" in it["pdf_url"])
    fr_url = next(it["pdf_url"] for it in fr_items if "wallis-f" in it["pdf_url"])
    assert de_url != fr_url                      # the /dam/<lang>/ prefix really differs
    from scrapers.practice.base import slugify
    id_de = f"visit_{slugify(de_url.rsplit('/', 1)[-1])[:60]}"
    id_fr = f"visit_{slugify(fr_url.rsplit('/', 1)[-1])[:60]}"
    assert id_de == id_fr                         # but the doc_id built from it is identical


def test_nkvf_visit_doc_number_ties_facility_and_year():
    items = nkvf.parse_download_items(_read("nkvf_berichte_de.html"), nkvf._BASE)
    momo = next(it for it in items if "Momo" in it["title"])
    number = nkvf.visit_doc_number(momo["title"])
    assert "2025" in number
    assert number.startswith("NKVF Bericht")


def test_nkvf_canton_lookup_matches_the_by_year_pages_urls():
    cantons = nkvf.canton_lookup(_read("nkvf_berichte_nach_kanton_de.html"))
    bl_url = next(u for u in cantons if "bericht-und-stellungnahme-BL" in u)
    assert cantons[bl_url] == "BL"
    ti_url = next(u for u in cantons if "Farera" in u)
    assert cantons[ti_url] == "TI"
    # the by-year fixture's Basel-Landschaft visit is the same URL
    de_items = nkvf.parse_download_items(_read("nkvf_berichte_de.html"), nkvf._BASE)
    bl_visit = next(it for it in de_items if "Basel-Landschaft" in it["title"])
    assert bl_visit["pdf_url"] == bl_url


def test_nkvf_taetigkeitsberichte_sixteen_years_both_languages():
    de = nkvf.parse_download_items(_read("nkvf_taetigkeitsberichte_de.html"), nkvf._BASE)
    fr = nkvf.parse_download_items(_read("nkvf_taetigkeitsberichte_fr.html"), nkvf._BASE)
    assert len(de) == 16 and len(fr) == 16
    s = nkvf.NkvfScraper.__new__(nkvf.NkvfScraper)
    nums_de = {s._taetigkeitsbericht_stub(it, "de", "/de/taetigkeitsberichte")["doc_number"] for it in de}
    nums_fr = {s._taetigkeitsbericht_stub(it, "fr", "/fr/rapports-d-activite")["doc_number"] for it in fr}
    assert nums_de == nums_fr    # canonical "NKVF Tätigkeitsbericht <year>" shared across languages
    assert "NKVF Tätigkeitsbericht 2025" in nums_de
    assert "NKVF Tätigkeitsbericht 2010" in nums_de


def test_nkvf_stellungnahmen_strip_boilerplate_leadin():
    items = nkvf.parse_download_items(_read("nkvf_stellungnahmen_de.html"), nkvf._BASE)
    titled = next(it for it in items if it["title"].startswith("Stellungnahme der Nationalen Kommission"))
    subject = nkvf.stellungnahme_subject(titled["title"])
    assert not subject.lower().startswith("stellungnahme der nationalen kommission")
    assert "asylgesetz" in subject.lower() or "zentren" in subject.lower()


def test_nkvf_studien_keeps_gutachten_drops_disclaimed_quarterly_bulletin():
    items = nkvf.parse_download_items(_read("nkvf_studien_de.html"), nkvf._BASE)
    s = nkvf.NkvfScraper.__new__(nkvf.NkvfScraper)
    kept = [it for it in items if s._studie_stub(it, "de", "/de/studien-und-gutachten") is not None]
    assert len(kept) == 1
    assert "Unterbringung" in kept[0]["title"]
    # every item on this page carries the SAME disclaimer — it is the
    # bulletin's own title, not the disclaimer, that must do the filtering
    assert "nicht verantwortlich" in kept[0]["description"]


def test_nkvf_factsheets_both_kept():
    items = nkvf.parse_download_items(_read("nkvf_factsheets_de.html"), nkvf._BASE)
    assert len(items) == 2
    s = nkvf.NkvfScraper.__new__(nkvf.NkvfScraper)
    for it in items:
        stub = s._factsheet_stub(it, "de", "/de/factsheets")
        assert stub["doc_type"] == "merkblatt"


# ── shared TextDedupMixin wiring (both scrapers mix it in) ────────────────

class _FakeNkvf(nkvf.NkvfScraper):
    TEXTS: dict[str, str] = {}

    def fetch_pdf_text(self, pdf_url):  # offline
        return self.TEXTS.get(pdf_url, "")


def test_nkvf_with_body_drops_the_cross_language_fallback_copy(tmp_path, monkeypatch):
    monkeypatch.setattr(_FakeNkvf, "OUTPUT_DIR", tmp_path)
    url = "https://www.nkvf.admin.ch/dam/de/sd-web/x/2025-06-15-bericht-wallis-f.pdf"
    _FakeNkvf.TEXTS = {url: "Rapport et prise de position texte complet"}
    s = _FakeNkvf()
    stub_de_page = {"pdf_url": url, "title": "Visite … Sion", "doc_number": "NKVF Bericht sion 2025",
                    "date": "2025-02-05", "language": "fr", "doc_type": "visit_report",
                    "topics": [], "nk_id": "visit_x"}
    out1 = s.with_body(dict(stub_de_page), "NKVF Bericht sion 2025")
    assert out1 and out1["body_text"]
    s._append(s._normalize(out1, out1["body_text"]))
    # a later run (or the FR page) discovers the SAME doc_id: run() would
    # skip it as already-known before with_body is even consulted.
    assert "nkvf_visit_x" in s._seen_ids


class _FakeStrafvollzug(sv.StrafvollzugScraper):
    TEXTS: dict[str, str] = {}

    def fetch_pdf_text(self, pdf_url):  # offline
        return self.TEXTS.get(pdf_url, "")


def test_strafvollzug_with_body_dedups_within_one_doc_number(tmp_path, monkeypatch):
    monkeypatch.setattr(_FakeStrafvollzug, "OUTPUT_DIR", tmp_path)
    _FakeStrafvollzug.TEXTS = {"fr.pdf": "texte français du règlement", "it.pdf": "testo italiano del regolamento"}
    s = _FakeStrafvollzug()

    def stub(url, lang, sv_id):
        return {"pdf_url": url, "title": "t", "doc_number": "CLDJP Règlement tig", "date": "2019-04-04",
                "language": lang, "doc_type": "erlass", "topics": [], "sv_id": sv_id,
                "issuing_authority": "CLDJP"}

    out_fr = s.with_body(stub("fr.pdf", "fr", "a"), "CLDJP Règlement tig")
    out_it = s.with_body(stub("it.pdf", "it", "b"), "CLDJP Règlement tig")
    assert out_fr["body_text"] and out_it["body_text"]
    assert out_fr["body_text"] != out_it["body_text"]   # genuinely different text, both kept


def test_body_language_overrides_a_filename_without_language():
    """'Vade mecum tri-PLESORR_V260101 – IT' has no French in it; the title
    vocabulary alone tagged it fr. The Italian règlements open with the
    secretariat's French letterhead, which the first 300 characters skip."""
    from scrapers.practice.strafvollzug import body_lang
    letterhead = "Secrétariat général des Conférences CLDJP, CLDAM et CLAMPP Av. Beauregard 1 " * 4
    it = letterhead + ("Il presente regolamento disciplina l'esecuzione delle pene per gli adulti e "
                       "per i giovani adulti nei cantoni del concordato, che sono tenuti alla "
                       "applicazione della decisione nel loro territorio. ") * 6
    de = "x" * 300 + ("Die Konferenz der für den Vollzug von Strafen und Massnahmen zuständigen "
                      "Behörden hat mit der Richtlinie beschlossen, dass die Kantone nicht von der "
                      "Regel abweichen und eine Bewilligung erteilt wird. ") * 6
    assert body_lang(it) == "it"
    assert body_lang(de) == "de"
    assert body_lang("x" * 300 + "Règlement TIG") == ""      # too little text: keep the tag
