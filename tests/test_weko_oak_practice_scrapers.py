"""WEKO Bekanntmachungen/Erläuterungen and OAK BV practice scrapers (user request
2026-09-07, together with the BSV BV Mitteilungen the BSV crawl already holds).

Golden fixtures captured 2026-09-07 (<main> of each page, scripts stripped),
parsers called directly — no network, no output dir. The run() tests stub
fetch_pdf_text.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

FIX = REPO / "tests" / "fixtures" / "practice"

from scrapers.practice import weko_bekanntmachungen as wb  # noqa: E402
from scrapers.practice import oak_bv  # noqa: E402
from scrapers.practice.finma_rundschreiben import _STATUS_IN_FORCE, _STATUS_SUPERSEDED  # noqa: E402
from scrapers.practice.sdweb import download_items  # noqa: E402


def _read(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


# ───────────────────────────────────────────── shared sd-web list parser

def test_sdweb_reads_title_from_h4_and_file_date_from_meta():
    items = list(download_items(_read("weko_bekanntmachungen_de.html"), "https://www.weko.admin.ch"))
    assert len(items) == 20
    first = items[0]
    assert first["title"] == "Vertikalbekanntmachung vom 12. Dezember 2022"
    assert first["file_date"] == "2025-07-23"            # the file's date, not the issuance date
    assert first["pdf_url"].startswith("https://www.weko.admin.ch/dam/de/sd-web/")
    assert first["section"] == "Vertikalbekanntmachung"
    assert items[-1]["section"] == "Archiv"


# ───────────────────────────────────────────── WEKO Bekanntmachungen

@pytest.fixture(scope="module")
def weko_pages():
    return {lang: wb.parse_page(_read(f"weko_bekanntmachungen_{lang}.html"), lang, f"https://www.weko.admin.ch/{lang}/x")
            for lang in ("de", "fr", "it")}


def test_weko_every_download_becomes_a_row(weko_pages):
    # 20 PDFs per page; the IT page borrows two German files, which come in from the DE page
    assert {lang: len(stubs) for lang, stubs in weko_pages.items()} == {"de": 20, "fr": 20, "it": 18}
    for lang, stubs in weko_pages.items():
        assert all(s["pdf_url"] and s["title"] and s["date"] for s in stubs), lang


def test_weko_versions_share_the_document_key_and_carry_their_own_date(weko_pages):
    de = weko_pages["de"]
    vert = [s for s in de if s["doc_number"] == "Vertikalbekanntmachung"]
    assert [s["date"] for s in vert] == ["2022-12-12", "2017-05-22", "2010-06-28"]
    # the "(Stand 22. Mai 2017)" row is dated by its Stand and remembers the Erlass
    stand = vert[1]
    assert "Erlass 2010-06-28" in stand["topics"]
    assert stand["title"].startswith("Nicht aktuell:")          # the authority's own wording stays
    assert set(_STATUS_SUPERSEDED) <= set(stand["topics"])
    assert set(_STATUS_IN_FORCE) <= set(vert[0]["topics"])
    assert not set(_STATUS_SUPERSEDED) & set(vert[0]["topics"])
    # "KFZ Bekanntmachung" (2002) and "KFZ-Bekanntmachung" (2015) slug to one key
    kfz = [s for s in de if s["weko_id_key"].startswith("kfz_bekanntmachung_")]
    assert {s["date"] for s in kfz} == {"2019-09-09", "2015-06-29", "2002-10-21"}


def test_weko_doc_types_from_the_title_in_three_languages(weko_pages):
    full = {"bekanntmachung": 9, "erlaeuterung": 7, "richtlinie": 2, "mitteilung": 2}
    assert Counter(s["doc_type"] for s in weko_pages["de"]) == full
    assert Counter(s["doc_type"] for s in weko_pages["fr"]) == full
    # the IT page borrows two German files (one Erläuterung, one Richtlinie), dropped here
    assert Counter(s["doc_type"] for s in weko_pages["it"]) == {
        "bekanntmachung": 9, "erlaeuterung": 6, "richtlinie": 1, "mitteilung": 2}


def test_weko_foreign_language_files_are_left_to_their_own_page(weko_pages):
    it = weko_pages["it"]
    assert all(s["language"] == "it" for s in it)
    assert not [s for s in it if s["doc_number"] in (
        "Erläuterungen zur KG-Sanktionsverordnung (SVKG)", "Richtlinien für ökonomische Gutachten")]
    de_keys = {s["doc_number"] for s in weko_pages["de"]}
    assert {"Erläuterungen zur KG-Sanktionsverordnung (SVKG)", "Richtlinien für ökonomische Gutachten"} <= de_keys
    assert all(s["language"] == "fr" for s in weko_pages["fr"])


def test_weko_italian_page_dates_with_hyphens_and_degree_sign(weko_pages):
    it = {s["title"]: s for s in weko_pages["it"]}
    assert it["Non aggiornata: Comunicazione autoveicoli del 29-06-2015 (stato al 09-09-2019)"]["date"] == "2019-09-09"
    assert it["Non aggionrato: Opuscolo esplicativo comunicazione autoveicoli (in vigore dal 1° gennaio 2016)"]["date"] == "2016-01-01"


def test_weko_doc_ids_unique_per_page_and_stable(weko_pages):
    s = wb.WekoBekanntmachungenScraper.__new__(wb.WekoBekanntmachungenScraper)
    for lang, stubs in weko_pages.items():
        ids = [s._make_doc_id(x) for x in stubs]
        assert len(ids) == len(set(ids)), lang
    assert s._make_doc_id(weko_pages["de"][0]) == "weko_bekanntmachungen_vertikalbekanntmachung_20221212_de"


def test_weko_scraper_contract():
    assert wb.WekoBekanntmachungenScraper.SOURCE_KEY == "weko_bekanntmachungen"
    assert wb.WekoBekanntmachungenScraper.ISSUING_AUTHORITY == "WEKO"
    assert wb.WekoBekanntmachungenScraper.DEFAULT_DOC_TYPE == "bekanntmachung"
    assert wb.WekoBekanntmachungenScraper.REVISION_FIELD == "pdf_url"
    assert wb.WekoBekanntmachungenScraper.NO_TEXT_LAYER_BODY


# ───────────────────────────────────────────── OAK BV

@pytest.fixture(scope="module")
def oak_listing_de():
    html = _read("oak_bv_weisungen_de.html")
    return (oak_bv.parse_subpage_links(html, "de"),
            oak_bv.parse_listing_downloads(html, "de", "weisung", "https://www.oak-bv.admin.ch/de/weisungen"))


def test_oak_listing_links_every_current_weisung_and_lists_the_repealed_ones(oak_listing_de):
    subpages, repealed = oak_listing_de
    assert len(subpages) == 18
    assert subpages[0] == "https://www.oak-bv.admin.ch/de/weisungen-w-01-2026"
    assert all(u.startswith("https://www.oak-bv.admin.ch/de/weisungen-w-") for u in subpages)
    # 8 archive downloads: 5 versions of three repealed Weisungen + 3 Zusatzdokumente
    assert len(repealed) == 8
    assert all(set(_STATUS_SUPERSEDED) <= set(s["topics"]) for s in repealed)
    versions = [s for s in repealed if s["doc_type"] == "weisung"]
    assert [(s["doc_number"], s["date"]) for s in versions] == [
        ("W – 04/2014", "2014-07-02"), ("W – 01/2014", "2017-03-23"), ("W – 01/2014", "2014-02-20"),
        ("W – 01/2013", "2013-04-30"), ("W – 01/2013", "2013-01-17")]
    annexes = [s for s in repealed if s["doc_type"] == "weisung_anhang"]
    # the number comes from the PDF filename when the title lacks it
    assert [(s["title"][:40], s["doc_number"], s["date"]) for s in annexes] == [
        ("Informationsschreiben vom 09.12.2020 zur", "W – 04/2014", "2020-12-09"),
        ("Informationsschreiben vom 23.03.2017 | W", "W – 01/2014", "2017-03-23"),
        ("FAQ zu den Weisungen W – 01/2014", "W – 01/2014", "2026-06-11")]


def test_oak_subpage_versions_and_annexes():
    url = "https://www.oak-bv.admin.ch/de/weisungen-w-01-2021"
    stubs = oak_bv.parse_subpage(_read("oak_bv_weisungen_w_01_2021_de.html"), "de", "weisung", url)
    assert len(stubs) == 4
    v = stubs[0]
    assert v["doc_type"] == "weisung" and v["doc_number"] == "W – 01/2021" and v["date"] == "2021-01-26"
    assert v["title"].startswith("Anforderungen an Transparenz und interne Kontrolle")
    assert v["title"].endswith("| W – 01/2021 | Erstversion | 26.01.2021")
    assert v["url"] == url and set(_STATUS_IN_FORCE) <= set(v["topics"])
    annex = stubs[1:]
    assert [s["doc_type"] for s in annex] == ["weisung_anhang"] * 3
    assert all(s["doc_number"] == "W – 01/2021" for s in annex)
    assert annex[0]["title"] == "Informationsschreiben vom 18.02.2021 | W – 01/2021"
    assert annex[0]["date"] == "2021-02-18"
    assert "Formular zu den Weisungen W – 01/2021" in annex[1]["title"] and "|" not in annex[1]["title"][-3:]


def test_oak_french_and_italian_numbers_normalise_to_the_german_form():
    fr = oak_bv.parse_subpage(_read("oak_bv_directives_d_01_2026_fr.html"), "fr", "weisung",
                              "https://www.oak-bv.admin.ch/fr/directives-d-01-2026")
    assert fr[0]["doc_number"] == "W – 01/2026" and fr[0]["language"] == "fr"
    assert fr[0]["title"].endswith("| D – 01/2026 | Première version | 22.04.2026")
    assert fr[1]["doc_type"] == "weisung_anhang" and fr[1]["date"] == "2026-05-01"
    # the IT site borrows the FR files ("(disponibile in francese)"): nothing to add from it
    it_html = _read("oak_bv_comunicazioni_it.html")
    assert len(list(download_items(it_html, oak_bv._BASE))) == 3
    assert oak_bv.parse_listing_downloads(it_html, "it", "mitteilung", "https://www.oak-bv.admin.ch/it/comunicazioni") == []
    assert oak_bv.parse_subpage(_read("oak_bv_comunicazioni_c_01_2025_it.html"), "it", "mitteilung",
                                "https://www.oak-bv.admin.ch/it/comunicazioni-c-01-2025") == []
    # a FR-page row keeps its FR language and the FR title
    fr_list = oak_bv.parse_listing_downloads(_read("oak_bv_directives_fr.html"), "fr", "weisung", "u")
    assert [(s["doc_number"], s["language"]) for s in fr_list if s["doc_type"] == "weisung"][:2] == [
        ("W – 04/2014", "fr"), ("W – 01/2014", "fr")]


def test_oak_consultation_drafts_are_typed_entwurf_and_dated_from_the_file():
    de = oak_bv.parse_listing_downloads(_read("oak_bv_anhoerungen_de.html"), "de", "entwurf",
                                        "https://www.oak-bv.admin.ch/de/anhoerungen")
    assert len(de) == 12 and all(s["doc_type"] == "entwurf" for s in de)
    assert all("Anhörung abgeschlossen" in s["topics"] for s in de)
    def one(prefix):
        [s] = [s for s in de if s["title"].startswith(prefix)]
        return s
    assert one("Mitteilungsentwurf « Übertragung")["date"] == "2023-12-05"          # 20231205_ in the filename
    assert one("Weisungsentwurf \"Bestätigung")["date"] == "2022-01-05"
    assert all(s["date"] for s in de)
    # three "Information betreffend die Anhörung zum Weisungsentwurf «…»" of one day stay apart
    s = oak_bv.OakBvScraper.__new__(oak_bv.OakBvScraper)
    infos = [s._make_doc_id(x) for x in de if x["title"].startswith("Information betreffend die Anhörung zum Weisungsentwurf")]
    assert len(infos) == 4 and len(set(infos)) == 4
    fr = oak_bv.parse_listing_downloads(_read("oak_bv_auditions_fr.html"), "fr", "entwurf", "u")
    it = oak_bv.parse_listing_downloads(_read("oak_bv_audizioni_it.html"), "it", "entwurf", "u")
    assert len(fr) == 12 and it == []                                                 # IT drafts are the FR files
    assert all(s["language"] == "fr" for s in fr)


def test_oak_doc_ids_unique_across_listing_and_subpages(oak_listing_de):
    s = oak_bv.OakBvScraper.__new__(oak_bv.OakBvScraper)
    _, repealed = oak_listing_de
    stubs = list(repealed)
    for f, url in (("oak_bv_weisungen_w_01_2021_de.html", "https://www.oak-bv.admin.ch/de/weisungen-w-01-2021"),
                   ("oak_bv_weisungen_w_02_2013_de.html", "https://www.oak-bv.admin.ch/de/weisungen-w-02-2013"),
                   ("oak_bv_mitteilungen_m_01_2025_de.html", "https://www.oak-bv.admin.ch/de/mitteilungen-m-01-2025")):
        stubs += oak_bv.parse_subpage(_read(f), "de", "weisung" if "weisungen" in f else "mitteilung", url)
    stubs += oak_bv.parse_listing_downloads(_read("oak_bv_anhoerungen_de.html"), "de", "entwurf", "u")
    ids = [s._make_doc_id(x) for x in stubs]
    assert len(ids) == len(set(ids))
    assert "oak_bv_w_01_2021_20210126_de" in ids
    assert "oak_bv_w_01_2014_20170323_de" in ids and "oak_bv_w_01_2014_20140220_de" in ids


def test_oak_scraper_contract():
    assert oak_bv.OakBvScraper.SOURCE_KEY == "oak_bv"
    assert oak_bv.OakBvScraper.ISSUING_AUTHORITY == "OAK BV"
    assert oak_bv.OakBvScraper.DEFAULT_DOC_TYPE == "weisung"
    assert oak_bv.OakBvScraper.REVISION_FIELD == "pdf_url"


def test_oak_run_walks_listings_then_subpages_with_stubbed_network(tmp_path):
    """run() over the DE fixtures only: repealed rows from the listing, then
    the versions and annexes of one subpage; every row its own JSONL record."""
    pages = {
        "https://www.oak-bv.admin.ch/de/weisungen": _read("oak_bv_weisungen_de.html"),
        "https://www.oak-bv.admin.ch/de/weisungen-w-01-2021": _read("oak_bv_weisungen_w_01_2021_de.html"),
    }

    class _Resp:
        def __init__(self, text): self.text = text
        def raise_for_status(self): pass

    class _Fixture(oak_bv.OakBvScraper):
        REQUEST_DELAY = 0
        LISTINGS = {"de": (("/de/weisungen", "weisung"),)}
        def __init__(self):
            self.OUTPUT_DIR = tmp_path
            super().__init__()
        def get(self, url, **kw):
            if url not in pages:
                raise RuntimeError("404 " + url)
            return _Resp(pages[url])
        def fetch_pdf_text(self, pdf_url):
            return "Weisungen OAK BV Text " + pdf_url[-12:]
        def discover_documents(self):
            # the module-level LISTINGS is global; narrow it for the test
            for lang, listings in self.LISTINGS.items():
                for path, kind in listings:
                    html = pages[oak_bv._BASE + path]
                    yield from oak_bv.parse_listing_downloads(html, lang, kind, oak_bv._BASE + path)
                    for sub in oak_bv.parse_subpage_links(html, lang):
                        if sub in pages:
                            yield from oak_bv.parse_subpage(pages[sub], lang, kind, sub)

    summary = _Fixture().run()
    assert summary["new"] == 12 and summary["failed"] == 0
    rows = [json.loads(l) for l in (tmp_path / "oak_bv.jsonl").read_text().splitlines()]
    assert Counter(r["doc_type"] for r in rows) == {"weisung": 6, "weisung_anhang": 6}
    assert {r["issuing_authority"] for r in rows} == {"OAK BV"}
    assert all(r["body_text"].startswith("Weisungen OAK BV Text") for r in rows)


# ───────────────────────────────────────────── registry / schema

def test_new_sources_registered_and_advertised():
    from scrapers.practice import runner
    import mcp_server
    assert runner.ENABLED_SCRAPERS["weko_bekanntmachungen"] is wb.WekoBekanntmachungenScraper
    assert runner.ENABLED_SCRAPERS["oak_bv"] is oak_bv.OakBvScraper
    [t] = [t for t in mcp_server._list_tools() if t.name == "search_practice"]
    props = t.inputSchema["properties"]
    for key in ("weko_bekanntmachungen", "oak_bv"):
        assert key in props["source"]["enum"]
        cls = runner.ENABLED_SCRAPERS[key]
        assert cls.ISSUING_AUTHORITY in props["issuing_authority"]["enum"]
        assert cls.DEFAULT_DOC_TYPE in props["doc_type"]["enum"]
    for doc_type in ("bekanntmachung", "erlaeuterung", "entwurf", "weisung_anhang", "richtlinie", "mitteilung"):
        assert doc_type in props["doc_type"]["enum"], doc_type
    d = t.description or ""
    assert "WEKO" in d.split("NOT covered:", 1)[0] and "OAK BV" in d.split("NOT covered:", 1)[0]
    assert len(d) <= 1024
    ins = mcp_server.server.instructions or ""
    assert "OAK BV Weisungen" in ins and "WEKO Bekanntmachungen" in ins
