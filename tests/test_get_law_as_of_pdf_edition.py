"""get_law(as_of=...) for editions Fedlex publishes only as PDF.

Before: every pre-2021 date returned "No XML available … XML versions are
available from ~2021 onward" and no text. Fedlex has a pdf-a for ~97 % of
2000-2020 editions. The text is now served whole-edition and flagged
verbatim_quotation="not_guaranteed", because a per-article split of
pdftotext output was measured silently wrong for one article in four.

Offline: the SPARQL seam, requests.get and the PDF extractor are stubbed.
"""
import sys
from pathlib import Path

import requests

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import mcp_server as m  # noqa: E402

OR_WORK = "https://fedlex.data.admin.ch/eli/cc/27/317_321_377"
SNAP_2017 = OR_WORK + "/20170401"
IN_FORCE = "https://fedlex.data.admin.ch/vocabulary/enforcement-status/0"
FMT = "https://fedlex.data.admin.ch/vocabulary/user-format/"

WORKS = [{"work": OR_WORK, "eif": "1912-01-01", "status": IN_FORCE}]
SNAPS = {OR_WORK: [{"snapshot": SNAP_2017, "date": "2017-04-01"}]}
PDF_URL = "https://fedlex.data.admin.ch/filestore/or-2017.pdf"
EXPR_PDF_ONLY = {SNAP_2017: [
    {"fmt": FMT + "pdf-a", "url": PDF_URL, "title": "Obligationenrecht", "abbr": "OR"},
    {"fmt": FMT + "doc", "url": "https://fedlex.data.admin.ch/filestore/or-2017.doc",
     "title": "Obligationenrecht", "abbr": "OR"},
]}

# A table of contents line, then the body. Art. 60 in the 2017 edition still
# read "in einem Jahre" (the reporter's probe).
EDITION_TEXT = "\n".join([
    "Bundesgesetz betreffend die Ergänzung des Schweizerischen Zivilgesetzbuches",
    "Inhaltsverzeichnis",
    "Art. 59 Haftung für Werke 21",
    "Art. 60 Verjährung 21",
    "Art. 61 Haftung öffentlicher Beamter 22",
    "Art. 59",
    "1 Der Eigentümer eines Gebäudes oder eines andern Werkes hat für den Schaden einzustehen.",
    "Art. 60",
    "1 Der Anspruch auf Schadenersatz oder Genugtuung verjährt in einem Jahre von dem Tage hinweg,",
    "wo der Geschädigte Kenntnis vom Schaden und von der Person des Ersatzpflichtigen erlangt hat,",
    "jedenfalls aber mit dem Ablaufe von zehn Jahren, vom Tage der schädigenden Handlung an gerechnet.",
    "2 Wird die Klage aus einer strafbaren Handlung hergeleitet, so gilt die längere Verjährung.",
    "Art. 61",
    "1 Über die Pflicht von öffentlichen Beamten oder Angestellten können die Kantone abweichende Bestimmungen aufstellen.",
])


def _stub(monkeypatch, expr=EXPR_PDF_ONLY, *, pdf_ok=True, record=None, cache_keys=None):
    def fake(query, timeout=15):
        if record is not None:
            record.append(query)
        if "historicalLegalId" in query:
            return WORKS
        if "isMemberOf" in query:
            return SNAPS[OR_WORK] if f"<{OR_WORK}>" in query else []
        if "isRealizedBy" in query:
            for uri, rows in expr.items():
                if f"<{uri}>" in query:
                    return rows
            return []
        raise AssertionError("unexpected SPARQL: " + query)

    class _Resp:
        def __init__(self, url):
            self.status_code = 200 if pdf_ok else 404
            self.content = b"%PDF-1.4 synthetic" if pdf_ok else b""
            self.url = url

    monkeypatch.setattr(m, "_fedlex_sparql_select", fake)
    monkeypatch.setattr(requests, "get", lambda url, timeout=30: _Resp(url))
    monkeypatch.setattr(m, "_extract_pdf_text", lambda content: (EDITION_TEXT, 3))
    monkeypatch.setattr(m, "_lexfind_cache_get", lambda k: None)
    monkeypatch.setattr(m, "_lexfind_cache_set",
                        (lambda k, v: cache_keys.append(k)) if cache_keys is not None else (lambda k, v: None))


def test_pdf_only_edition_returns_a_flagged_excerpt_not_the_toc_line(monkeypatch):
    _stub(monkeypatch)
    res = m.get_law(sr_number="220", article="60", as_of="2019-06-01")
    assert "error" not in res
    assert res["snapshot_date"] == "2017-04-01"
    assert res["text_source"] == "fedlex_pdf"
    assert res["structure"] == "none"
    assert res["verbatim_quotation"] == "not_guaranteed"
    assert res["formats_available"] == ["doc", "pdf-a"]
    assert res["edition_pages"] == 3 and res["edition_chars"] == len(EDITION_TEXT)
    assert res["excerpt_candidates"] == 2
    (art,) = res["articles"]
    assert art["excerpt"] is True and art["article_num"] == "60"
    assert "verjährt in einem Jahre" in art["text"]
    assert "Verjährung 21" not in art["text"]          # the TOC candidate lost
    assert "Art. 61" not in art["text"]               # window stops at the next marker
    # Provenance is the same as on the XML path.
    assert res["title"] == "Obligationenrecht" and res["abbreviation"] == "OR"
    assert res["source_url"] == "https://www.fedlex.admin.ch/eli/cc/27/317_321_377/20170401/de"


def test_missing_marker_returns_no_article_and_a_note(monkeypatch):
    _stub(monkeypatch)
    res = m.get_law(sr_number="220", article="999", as_of="2019-06-01")
    assert res["articles"] == []
    assert res["note"].startswith("No 'Art. 999' marker found in the PDF text")


def test_whole_edition_request_returns_a_preview_not_the_full_text(monkeypatch):
    _stub(monkeypatch)
    res = m.get_law(sr_number="220", as_of="2019-06-01")
    assert res["articles"] == [] and res["article_count"] is None
    assert len(res["edition_preview"]) <= 1500
    assert res["edition_preview"].startswith("Bundesgesetz betreffend")
    assert "Request a specific article" in res["note"]
    text = m._format_get_law_response(res)
    assert "Edition preview (unstructured PDF text, first 1500 characters):" in text
    assert "No articles found" not in text


def test_rendering_carries_the_do_not_quote_warning(monkeypatch):
    _stub(monkeypatch)
    text = m._format_get_law_response(m.get_law(sr_number="220", article="60", as_of="2019-06-01"))
    assert "Version: HISTORICAL — Fedlex edition of 2017-04-01, applicable on 2019-06-01" in text
    assert "verbatim quotation NOT guaranteed" in text
    assert "WARNING: do not quote this text." in text
    assert '### Excerpt around "Art. 60" (unstructured PDF text, 1 of 2 candidate positions)' in text
    assert "### Art. 60" not in text


def test_edition_text_is_cached_by_snapshot_not_by_as_of(monkeypatch):
    keys = []
    _stub(monkeypatch, cache_keys=keys)
    m.get_law(sr_number="220", article="60", as_of="2019-06-01")
    m.get_law(sr_number="220", article="41", as_of="2019-10-01")
    edition_keys = [k for k in keys if k.startswith("hist_edition:")]
    assert edition_keys == ["hist_edition:v2:220:2017-04-01:de:pdf-a"] * 2
    assert m._ttl_for_key(edition_keys[0]) == 30 * 86400


def test_xml_is_preferred_when_both_formats_exist(monkeypatch):
    expr = {SNAP_2017: EXPR_PDF_ONLY[SNAP_2017] + [
        {"fmt": FMT + "xml", "url": "https://fedlex.data.admin.ch/filestore/or-2017.xml",
         "title": "Obligationenrecht", "abbr": "OR"}]}
    _stub(monkeypatch, expr=expr)
    xml = ('<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0"><act><body>'
           '<article eId="art_60"><num>Art. 60</num><paragraph><content><p>verjährt in einem Jahre</p>'
           '</content></paragraph></article></body></act></akomaNtoso>').encode()

    class _Resp:
        status_code = 200
        content = xml

    monkeypatch.setattr(requests, "get", lambda url, timeout=30: _Resp())
    res = m.get_law(sr_number="220", article="60", as_of="2019-06-01")
    assert res["text_source"] == "fedlex_xml" and res["verbatim_quotation"] == "verbatim"
    assert res["formats_available"] == ["doc", "pdf-a", "xml"]


def test_pdf_download_failure_is_reported(monkeypatch):
    _stub(monkeypatch, pdf_ok=False)
    res = m.get_law(sr_number="220", article="60", as_of="2019-06-01")
    assert "download failed" in res["error"] and "(PDF)" in res["error"]


def test_excerpt_helper_picks_the_longest_candidate_and_stops_at_next_marker():
    excerpt, n = m._pdf_article_excerpt(EDITION_TEXT, "60")
    assert n == 2 and excerpt.startswith("Art. 60\n1 Der Anspruch")
    assert excerpt.endswith("so gilt die längere Verjährung.")
    assert m._pdf_article_excerpt(EDITION_TEXT, "6") == (None, 0)      # no false prefix hit on 60/61
    assert m._pdf_article_excerpt(EDITION_TEXT, "") == (None, 0)


def test_system_prompt_forbids_quoting_unguaranteed_text():
    src = (REPO / "mcp_server.py").read_text(encoding="utf-8")
    r2 = src[src.index('"R2. NEVER write a direct quotation'):]
    r2 = r2[:r2.index('"R3. NEVER')]
    assert "not_guaranteed" in r2 and "NEVER quote from it" in r2
    assert "Step 5: if `version` is historical, state the edition date" in src
