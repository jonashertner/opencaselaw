"""SchKG sources: cantonal supervisory publications + KBKS (schkg_kantone), the
BJ pages added to bj_schkg in 2026-11, opt-in OCR, and the canton filter of
search_practice. Offline: golden listing pages under tests/fixtures/practice."""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scrapers.practice import base, bj_schkg  # noqa: E402
from scrapers.practice import schkg_kantone as sk  # noqa: E402

FIX = REPO / "tests" / "fixtures" / "practice"


def _entry(fragment: str) -> sk.Entry:
    return next(e for e in sk.REGISTRY if fragment in e.url)


def _parse(fixture: str, fragment: str) -> list[dict]:
    return sk.parse_listing((FIX / fixture).read_bytes(), _entry(fragment))


def _scraper() -> sk.SchkgKantoneScraper:
    s = sk.SchkgKantoneScraper.__new__(sk.SchkgKantoneScraper)
    s.registry = sk.REGISTRY
    return s


# ───────────────────────────────────────────── cantonal listings

def test_be_lists_every_circular_and_its_guideline():
    de = _parse("schkg_kantone_be_de.html", "/de/start/dienstleistungen")
    fr = _parse("schkg_kantone_be_fr.html", "/fr/start/dienstleistungen")
    assert len(de) == 37 and len(fr) == 37
    nums_de = {s["doc_number"] for s in de}
    assert {"BE KS A1", "BE KS A10", "BE KS B19", "BE KS C7", "BE Existenzminimum"} <= nums_de
    nums_fr = {s["doc_number"] for s in fr}
    # "Circulaire non C4" (sic) and C7, whose title quotes "Circulaire no 37"
    assert {"BE Circulaire A1", "BE Circulaire C4", "BE Circulaire C7", "BE Existenzminimum"} <= nums_fr
    assert "BE Circulaire 37" not in nums_fr


def test_ju_keeps_only_the_debt_enforcement_section():
    stubs = _parse("schkg_kantone_ju.html", "jura.ch")
    assert len(stubs) == 7
    assert {s["doc_number"] for s in stubs} >= {"JU Circulaire 17", "JU Circulaire 26", "JU Existenzminimum"}


def test_ti_circulars_dates_and_table_title():
    stubs = _parse("schkg_kantone_ti.html", "giustizia-civile/circolari")
    by_num = {s["doc_number"]: s for s in stubs}
    assert len(stubs) == 35
    assert by_num["TI Circolare 41"]["date"] == "2024-04-04"
    assert any("Tabella riassuntiva allegata alla Circolare n. 36/2009" in s["title"] for s in stubs)
    # 35/2009 adopts the table; the guideline itself is the HTML table page
    assert "TI Existenzminimum" not in by_num


def test_so_titles_lose_date_prefix_and_size_noise():
    stubs = _parse("schkg_kantone_so.html", "so.ch")
    assert len(stubs) == 12
    ex = next(s for s in stubs if s["doc_number"] == "SO Existenzminimum")
    assert ex["date"] == "2014-10-13" and not ex["title"].startswith("13.10.2014")
    assert all("(pdf" not in s["title"] for s in stubs)


def test_ag_files_under_one_title_get_distinct_numbers():
    stubs = _parse("schkg_kantone_ag.html", "ag.ch")
    assert len(stubs) == 5
    assert len({s["doc_number"] for s in stubs}) == 5
    assert sum(s["doc_number"] == "AG Existenzminimum" for s in stubs) == 1


def test_vd_guideline_by_href_and_leaflet_is_not_a_guideline():
    stubs = _parse("schkg_kantone_vd.html", "vous-etes-poursuivi")
    guide = [s for s in stubs if s["doc_number"] == "VD Existenzminimum"]
    assert len(guide) == 1 and "Lignes_directrices_minimum_vital" in guide[0]["pdf_url"]
    assert any(s["title"].startswith("Dépliant") and s["doc_type"] != "richtlinie" for s in stubs)


def test_ne_yearly_norms_and_info_sheet():
    stubs = _parse("schkg_kantone_ne.html", "ne.ch")
    nums = {s["doc_number"] for s in stubs}
    assert "NE Existenzminimum" in nums and len(stubs) == 2
    norms = next(s for s in stubs if s["doc_number"] == "NE Existenzminimum")
    assert _scraper()._make_doc_id(norms) == "schkg_kantone_ne_existenzminimum_2025_fr"


def test_sg_and_zh_guidelines():
    sg = _parse("schkg_kantone_sg.html", "sg.ch")
    assert {s["doc_number"] for s in sg} >= {"SG Existenzminimum"} and len(sg) == 3
    zh = _parse("schkg_kantone_zh_hilfen.html", "betreibung-und-konkurs/hilfen")
    ex = next(s for s in zh if s["doc_number"] == "ZH Existenzminimum")
    assert ex["date"] == "2009-09-16"


def test_ar_keeps_only_the_supervisory_section():
    ar = _parse("schkg_kantone_ar.html", "ar.ch/gerichte")
    assert [s["date"] for s in ar] == ["1993-07-14", "1998-11-18", "2009-08-26", "2014-11-25"]
    assert [s["doc_number"] for s in ar if s["doc_type"] == "richtlinie"] == ["AR Existenzminimum"]
    assert not any("Aktenf" in s["pdf_url"] or "Archivwesen" in s["pdf_url"] for s in ar)


def test_gr_relative_links_resolve_against_base_href():
    gr = _parse("schkg_kantone_gr.html", "justiz-gr.ch/gerichte")
    assert [s["pdf_url"] for s in gr] == [
        "https://www.justiz-gr.ch/fileadmin/dateien/Kantonsgericht/Kreisschreiben/Kreisschreiben_2009-08-18.pdf",
        "https://www.justiz-gr.ch/fileadmin/dateien/Kantonsgericht/Kreisschreiben/Kreisschreiben_2011-02-08.pdf",
    ]
    assert gr[0]["doc_number"] == "GR Existenzminimum" and gr[0]["date"] == "2009-08-18"
    assert gr[1]["doc_type"] != "richtlinie" and gr[1]["date"] == "2011-02-08"


def test_kbks_press_release_mentioning_existenzminimum_is_not_the_guideline():
    stubs = _parse("schkg_kantone_kbks_news_de.html", "ueber-uns/news")
    assert len(stubs) == 2
    assert all(s["doc_type"] != "richtlinie" for s in stubs)
    assert {s["date"] for s in stubs} == {"2022-12-08", "2023-01-25"}


def test_registry_has_no_duplicate_rows_and_known_hosts_tunnel():
    keys = Counter((e.url, e.lang) for e in sk.REGISTRY)
    assert not [k for k, v in keys.items() if v > 1]
    assert {e.canton for e in sk.REGISTRY} >= {"KBKS", "ZH", "BE", "GE", "TI", "VD", "NE", "JU"}


# ───────────────────────────────────────────── GE (HTML, windows-1252, yearly)

def test_ge_norms_title_from_bytes_and_yearly_doc_id():
    raw = (FIX / "schkg_kantone_ge.html").read_bytes()
    title = sk.page_title(raw)
    assert "Normes d'insaisissabilité pour l'année 2026" in title
    entry = _entry("silgeneve.ch")
    stub = sk.make_stub(entry, title, entry.url, body_text=sk.html_text(raw, None))
    assert stub["doc_number"] == "GE Existenzminimum" and stub["date"] == "2026"
    s = _scraper()
    assert s._make_doc_id(stub) == "schkg_kantone_ge_existenzminimum_2026_fr"
    nxt = sk.make_stub(entry, title.replace("2026", "2027"), entry.url, body_text="x")
    assert s._make_doc_id(nxt) != s._make_doc_id(stub)
    assert stub["revision"] != nxt["revision"]


def test_normalize_sets_canton_authority_and_body_date():
    entry = _entry("jura.ch")
    stub = sk.make_stub(entry, "Circulaire N° 21 - Inspection", "https://jura.ch/x.pdf")
    doc = _scraper()._normalize(stub, "Porrentruy, le 3 mars 2015. La Cour …")
    assert doc["issuing_authority"] == "JU"
    assert doc["date"] == "2015-03-03"
    assert doc["revision"] == "https://jura.ch/x.pdf"


def test_blocked_hosts_go_through_their_canton_proxy(monkeypatch):
    proxy = "socks5h://127.0.0.1:1080"
    monkeypatch.setenv("NE_PROXY", proxy)
    monkeypatch.setenv("TI_PROXY", proxy)
    monkeypatch.setenv("GL_PROXY", proxy)
    monkeypatch.delenv("JU_PROXY", raising=False)
    s = _scraper()
    s._last_request, s.REQUEST_DELAY = 0.0, 0
    seen = {}

    class _Sess:
        def get(self, url, **kw):
            seen[url] = kw.get("proxies")
    s.session = _Sess()
    for u in ("https://www.ne.ch/a.pdf", "https://www4.ti.ch/a.pdf", "https://jura.ch/a.pdf", "https://www.gl.ch/a.pdf",
              "https://www.ag.ch/a.pdf"):
        s.get(u)
    assert seen["https://www.ne.ch/a.pdf"] == {"http": proxy, "https": proxy}
    assert seen["https://www4.ti.ch/a.pdf"] == {"http": proxy, "https": proxy}
    assert seen["https://www.gl.ch/a.pdf"] == {"http": proxy, "https": proxy}
    assert seen["https://jura.ch/a.pdf"] is None
    assert seen["https://www.ag.ch/a.pdf"] is None


# ───────────────────────────────────────────── bj_schkg additions

def test_bj_information_letters():
    for lang in ("de", "it"):
        stubs, _ = bj_schkg.parse_page(
            (FIX / f"bj_schkg_information_{lang}.html").read_text(encoding="utf-8"),
            "information", lang, "u")
        assert len(stubs) == 24
        assert {s["doc_number"] for s in stubs} == {f"Information {n}" for n in range(1, 25)}
        assert all(s["doc_type"] == "mitteilung" and s["bj_stem"].startswith("information-")
                   for s in stubs)


def test_bj_model_forms_unique_and_study_is_a_report():
    stubs, _ = bj_schkg.parse_page(
        (FIX / "bj_schkg_musterformulare_de.html").read_text(encoding="utf-8"),
        "musterformulare", "de", "u")
    nums = Counter(s["doc_number"] for s in stubs)
    assert len(stubs) == 127 and not [k for k, v in nums.items() if v > 1]
    assert {"Form. 1", "Form. VZG 17"} <= set(nums)
    assert [s["doc_type"] for s in stubs if "potentiale" in s["bj_stem"]] == ["bericht"]
    # copies of Weisungen / Informationen linked from the forms page stay out
    assert not [s for s in stubs if s["bj_stem"].startswith(("musterformulare-weisung",
                                                             "musterformulare-info"))]


def test_bj_consultation_drafts_do_not_reuse_weisung_numbers():
    stubs, _ = bj_schkg.parse_page(
        (FIX / "bj_schkg_anhoerungen_de.html").read_text(encoding="utf-8"),
        "anhoerungen", "de", "u")
    assert len(stubs) == 11
    assert all(s["doc_type"] == "entwurf" and s["doc_number"].startswith("Anhörung") for s in stubs)


def test_bj_existing_doc_ids_unchanged():
    stubs, _ = bj_schkg.parse_page(
        (FIX / "bj_schkg_weisungen_de.html").read_text(encoding="utf-8"), "weisungen", "de", "u")
    s = bj_schkg.BjSchkgScraper.__new__(bj_schkg.BjSchkgScraper)
    assert "bj_schkg_weisung_1_de" in {s._make_doc_id(x) for x in stubs}


def test_bj_reports_are_yielded_even_when_pages_fail():
    s = bj_schkg.BjSchkgScraper.__new__(bj_schkg.BjSchkgScraper)

    def _fail(url, **kw):
        raise OSError("offline")
    s.get = _fail
    stubs = list(s.discover_documents())
    assert len(stubs) == len(bj_schkg.EXTRA_DOCS)
    assert all(x["doc_type"] == "bericht" for x in stubs)


# ───────────────────────────────────────────── OCR

class _OcrScraper(base.PracticeScraper):
    SOURCE_KEY = "ocr_test"
    ISSUING_AUTHORITY = "T"
    NO_TEXT_LAYER_BODY = "[Textlayer fehlt: gescanntes PDF]"
    OCR_SCANS = True
    CACHE_PDFS = False

    def __init__(self, out: Path, stubs: list[dict]):
        self.OUTPUT_DIR = out
        self._stubs = stubs
        super().__init__()

    def discover_documents(self):
        yield from self._stubs


class _Resp:
    content = b"%PDF-1.4 scan"

    def raise_for_status(self):
        pass


def _stub():
    return {"pdf_url": "https://x/s.pdf", "title": "Alte Weisung", "doc_number": "W 1",
            "date": "2000-01-01", "language": "de"}


def test_scan_is_ocred_and_tagged(tmp_path, monkeypatch):
    monkeypatch.setattr(base, "extract_pdf_text", lambda b: "")
    monkeypatch.setattr(base, "ocr_pdf_text", lambda b, langs="": "Erkannter Text " * 30)
    monkeypatch.setattr(base, "tesseract_langs", lambda w="": "deu")
    s = _OcrScraper(tmp_path, [_stub()])
    s.get = lambda url, **kw: _Resp()
    assert s.run()["new"] == 1
    rec = json.loads((tmp_path / "ocr_test.jsonl").read_text().splitlines()[-1])
    assert rec["body_text"].startswith("Erkannter Text") and "OCR" in rec["topics"]


def test_old_placeholder_is_refetched_once_ocr_is_available(tmp_path, monkeypatch):
    placeholder = {"doc_id": "ocr_test_w_1", "body_text": "Alte Weisung [Textlayer fehlt: gescanntes PDF]"}
    (tmp_path / "ocr_test.jsonl").write_text(json.dumps(placeholder) + "\n")
    monkeypatch.setattr(base, "extract_pdf_text", lambda b: "")
    monkeypatch.setattr(base, "ocr_pdf_text", lambda b, langs="": "Erkannter Text " * 30)
    monkeypatch.setattr(base, "tesseract_langs", lambda w="": "deu")
    s = _OcrScraper(tmp_path, [_stub()])
    s.get = lambda url, **kw: _Resp()
    assert s.run()["new"] == 1
    # second run: the OCR'd record is current, nothing to refetch
    s2 = _OcrScraper(tmp_path, [_stub()])
    s2.get = lambda url, **kw: _Resp()
    assert s2.run()["new"] == 0


def test_placeholder_not_refetched_without_tesseract(tmp_path, monkeypatch):
    placeholder = {"doc_id": "ocr_test_w_1", "body_text": "Alte Weisung [Textlayer fehlt: gescanntes PDF]"}
    (tmp_path / "ocr_test.jsonl").write_text(json.dumps(placeholder) + "\n")
    monkeypatch.setattr(base, "tesseract_langs", lambda w="": "")
    s = _OcrScraper(tmp_path, [_stub()])
    assert s.run()["new"] == 0


# ───────────────────────────────────────────── search_practice canton filter

@pytest.fixture
def practice_db(tmp_path, monkeypatch):
    import mcp_server
    from search_stack import build_practice_db
    rows = [
        {"doc_id": "schkg_kantone_sg_existenzminimum_2023_de", "source": "schkg_kantone",
         "issuing_authority": "SG", "doc_type": "richtlinie", "doc_number": "SG Existenzminimum",
         "title": "Kreisschreiben Existenzminimum", "date": "2023-07-01", "language": "de",
         "url": "u", "pdf_url": "p", "body_text": "Grundbetrag Existenzminimum alleinstehend 1230",
         "topics": ["SchKG", "SG"]},
        {"doc_id": "schkg_kantone_sg_existenzminimum_2019_de", "source": "schkg_kantone",
         "issuing_authority": "SG", "doc_type": "richtlinie", "doc_number": "SG Existenzminimum",
         "title": "Kreisschreiben Existenzminimum", "date": "2019-11-01", "language": "de",
         "url": "u", "pdf_url": "p0", "body_text": "Grundbetrag Existenzminimum alleinstehend 1200",
         "topics": ["SchKG", "SG"]},
        {"doc_id": "bj_schkg_14_zh_ks_d_de", "source": "bj_schkg", "issuing_authority": "BJ",
         "doc_type": "kreisschreiben", "doc_number": "ZH KS 14", "title": "ZH Richtlinien",
         "date": "2001-05-23", "language": "de", "url": "u", "pdf_url": "p",
         "body_text": "Grundbetrag Existenzminimum Zürich", "topics": ["SchKG", "ZH", "OCR"]},
        {"doc_id": "bj_schkg_weisung_11_de", "source": "bj_schkg", "issuing_authority": "BJ",
         "doc_type": "weisung", "doc_number": "Weisung 11", "title": "Weisung 11",
         "date": "2024-04-29", "language": "de", "url": "u", "pdf_url": "p",
         "body_text": "Existenzminimum KVG Prämien", "topics": ["SchKG", "CH"]},
    ]
    jdir = tmp_path / "practice"
    jdir.mkdir()
    (jdir / "x.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n")
    db = tmp_path / "practice.db"
    build_practice_db.build(jdir, db)
    monkeypatch.setattr(mcp_server, "PRACTICE_DB_PATH", db)
    return mcp_server


def test_canton_filter_reaches_both_sources(practice_db):
    sg = practice_db._search_practice(query="Existenzminimum", canton="SG")
    assert [r["doc_id"] for r in sg["results"]] == ["schkg_kantone_sg_existenzminimum_2023_de"]
    zh = practice_db._search_practice(query="Existenzminimum", canton="zh")
    assert [r["doc_id"] for r in zh["results"]] == ["bj_schkg_14_zh_ks_d_de"]
    assert zh["filters"]["canton"] == "zh"


def test_superseded_guideline_edition_is_reachable(practice_db):
    old = practice_db._search_practice(query="Existenzminimum", canton="SG", include_superseded=True)
    assert len(old["results"]) == 2


def test_get_practice_flags_ocr_text(practice_db):
    doc = practice_db._get_practice(doc_id="bj_schkg_14_zh_ks_d_de")
    assert doc["text_source"] == "ocr" and "OCR" in doc["ocr_note"]
    assert "text_source" not in practice_db._get_practice(doc_id="bj_schkg_weisung_11_de")


def test_schkg_kantone_registered_and_selectable():
    import mcp_server
    from scrapers.practice import runner
    assert runner.ENABLED_SCRAPERS["schkg_kantone"] is sk.SchkgKantoneScraper
    [t] = [t for t in mcp_server._list_tools() if t.name == "search_practice"]
    props = t.inputSchema["properties"]
    assert "schkg_kantone" in props["source"]["enum"]
    assert {"ZH", "GE", "KBKS"} <= set(props["canton"]["enum"])
    assert {"formular", "bericht"} <= set(props["doc_type"]["enum"])
