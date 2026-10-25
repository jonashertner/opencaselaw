"""UN treaty-body decisions on individual communications (scrapers/untb.py).

Golden fixtures captured 2026-10-24 from juris.ohchr.org and docstore.ohchr.org:
four case pages (R.D. v. Switzerland, a CAT inadmissibility decision against
Canada, a 1990 CCPR case with only an image-only English PDF, an unused case
id) and the French Word-HTML of R.D. v. Switzerland. No network: the
scraper's get() is replaced by a URL -> bytes table.
"""
from __future__ import annotations

import io
import sys
import zipfile
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

FIX = REPO / "tests" / "fixtures" / "untb"

from scrapers import untb  # noqa: E402


def _page(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    """Invariant #8: any request a test did not route itself is a failure,
    not a 5-second call to the UN."""
    def refuse(self, url, **_):
        raise AssertionError(f"network access in a test: {url}")
    monkeypatch.setattr(untb.UNTreatyBodyScraper, "get", refuse)


# ───────────────────────────────────────────── case page

def test_case_page_metadata():
    m = untb.parse_case_page(_page("case_2_rd_v_switzerland.html"))
    assert m["title"] == "R.D. v. Switzerland"
    assert m["symbol"] == "CAT/C/51/D/426/2010"
    assert m["communication_numbers"] == "426/2010"
    assert m["decision_type"] == "Decision on merits"
    assert m["country"] == "Switzerland"
    assert m["decision_date"] == date(2013, 11, 8)
    assert m["submission_date"] == date(2010, 6, 14)
    assert set(m["links"]) == {"en", "fr"}
    assert set(m["links"]["fr"]) == {"html", "docx", "pdf"}
    assert all(u.startswith("https://docstore.ohchr.org/") for u in m["links"]["fr"].values())


def test_case_page_index_terms_and_pdf_only_case():
    m = untb.parse_case_page(_page("case_300_pdf_only.html"))
    assert m["symbol"] == "CCPR/C/39/D/195/1985"
    assert m["substantive_articles"] == "9 (1) 18 (1) 19 (2) 25 (c) 26"
    assert m["procedural_issues"] == "exhaustion of domestic remedies"
    assert m["links"] == {"en": {"pdf": m["links"]["en"]["pdf"]}}


def test_unused_case_id_is_none():
    assert untb.parse_case_page(_page("case_4200_unused.html")) is None


@pytest.mark.parametrize("symbol, court", [
    ("CCPR/C/143/D/4367/2023", "untb_ccpr"),
    ("CAT/C/51/D/426/2010", "untb_cat"),
    ("E/C.12/66/D/5/2015", "untb_cescr"),
    ("CRC/C/95/D/123/2020", "untb_crc"),
    ("CED/C/25/D/1/2022", "untb_ced"),
])
def test_committee_from_symbol(symbol, court):
    assert untb.committee_for(symbol)[0] == court


def test_spt_and_unknown_symbols_are_not_complaint_bodies():
    assert untb.committee_for("CAT/OP/CHE/ROSP/1") is None
    assert untb.committee_for("A/HRC/53/12") is None


# ───────────────────────────────────────────── text

def test_word_html_text():
    text = untb.clean_text(untb.html_text((FIX / "doc_2_fr.html").read_bytes()))
    assert "Communication no 426/2010" in text
    # the tab Word dropped after the paragraph number is restored
    assert "\n9.2 Le Comité doit déterminer si, en renvoyant la requérante en Éthiopie" in text
    assert "\n10. À la lumière de ce qui précède" in text
    assert not untb.in_other_language(text, "fr")
    assert untb.in_other_language(text, "en")


def test_docx_text_without_python_docx():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml",
                   '<w:document><w:body><w:p><w:r><w:t>1.</w:t></w:r><w:r><w:tab/><w:t>The '
                   'author is &amp; was</w:t></w:r></w:p><w:p><w:r><w:t xml:space="preserve">'
                   'Second</w:t></w:r></w:p></w:body></w:document>')
    assert untb.docx_text(buf.getvalue()) == "1.\tThe author is & was\nSecond"
    assert untb.docx_text(b"not a zip") == ""


def test_pdf_text_layer():
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "The Committee is of the view")
    assert "The Committee is of the view" in untb.pdf_text(doc.tobytes())
    assert untb.pdf_text(b"%PDF-broken") == ""


def test_regeste_drops_placeholders_and_bridges_languages():
    m = untb.parse_case_page(_page("case_2_rd_v_switzerland.html"))
    reg = untb.build_regeste(m, "CAT")
    assert "No issues found" not in reg and "No articles found" not in reg
    assert reg.startswith("Decision on merits; [CAT: UNO-Antifolterkonvention")
    m3218 = untb.parse_case_page(_page("case_3218.html"))
    assert "Substantive issues: non-refoulement; Articles: 3" in untb.build_regeste(m3218, "CAT")


# ───────────────────────────────────────────── fetch_decision

def _scraper(tmp_path, routes: dict[str, bytes]):
    s = untb.UNTreatyBodyScraper(state_dir=tmp_path)
    calls: list[str] = []

    def fake_get(url, **_):
        calls.append(url)
        data = routes[url]
        return SimpleNamespace(content=data, text=data.decode("utf-8", "replace"))

    s.get = fake_get
    s.calls = calls
    return s


def _stub(case_id: int) -> dict:
    return {"case_id": case_id, "decision_id": untb.UNTreatyBodyScraper.gap_id(case_id),
            "docket_number": f"juris {case_id}", "url": untb.CASE_URL.format(case_id=case_id)}


def test_fetch_prefers_french_html(tmp_path):
    page = (FIX / "case_2_rd_v_switzerland.html").read_bytes()
    links = untb.parse_case_page(page.decode())["links"]
    s = _scraper(tmp_path, {untb.CASE_URL.format(case_id=2): page,
                            links["fr"]["html"]: (FIX / "doc_2_fr.html").read_bytes()})
    d = s.fetch_decision(_stub(2))
    assert d.decision_id == "untb_cat_CAT_C_51_D_426_2010"
    assert d.court == "untb_cat" and d.canton == "CH" and d.language == "fr"
    assert d.docket_number == "CAT/C/51/D/426/2010" and d.docket_number_2 == "426/2010"
    assert d.decision_date == date(2013, 11, 8)
    assert d.title == "R.D. v. Switzerland"
    assert d.outcome is None                         # a merits decision can go either way
    assert d.source_url == "https://juris.ohchr.org/casedetails/2/en-US"
    assert d.pdf_url == "https://docs.un.org/fr/CAT/C/51/D/426/2010"
    assert d.external_id == "juris:2"
    assert len(s.calls) == 2                         # case page + one document

    s.on_decision_persisted(d)
    sidecar = (tmp_path / "untb.juris_ids.tsv").read_text()
    assert sidecar == "2\tuntb_cat_CAT_C_51_D_426_2010\n"
    again = untb.UNTreatyBodyScraper(state_dir=tmp_path)
    assert 2 in again._done_case_ids


def test_misfiled_language_is_not_stored_under_it(tmp_path):
    """A French document behind the English row must not become an 'en' row."""
    page = (FIX / "case_3218.html").read_bytes()
    links = untb.parse_case_page(page.decode())["links"]
    french = (FIX / "doc_2_fr.html").read_bytes()
    routes = {untb.CASE_URL.format(case_id=3218): page, links["en"]["html"]: french}
    for fmt in ("html", "docx", "pdf"):
        routes[links["fr"][fmt]] = b"<html><body>x</body></html>"      # empty French files
    s = _scraper(tmp_path, routes)
    assert s.fetch_decision(_stub(3218)) is None


def test_image_only_pdf_goes_to_ocr(tmp_path, monkeypatch):
    fitz = pytest.importorskip("fitz")
    page = (FIX / "case_300_pdf_only.html").read_bytes()
    links = untb.parse_case_page(page.decode())["links"]
    blank = fitz.open(); blank.new_page()
    s = _scraper(tmp_path, {untb.CASE_URL.format(case_id=300): page,
                            links["en"]["pdf"]: blank.tobytes()})
    seen = {}

    def fake_ocr(data, lang):
        seen["lang"] = lang
        return "The Human Rights Committee, established under article 28 of the Covenant. " * 10

    import scrapers.pdf_ocr
    monkeypatch.setattr(scrapers.pdf_ocr, "ocr_pdf_bytes", fake_ocr)
    d = s.fetch_decision(_stub(300))
    assert seen["lang"] == "eng"
    assert d.court == "untb_ccpr" and d.language == "en" and d.canton == "UN"
    assert d.decision_id == "untb_ccpr_CCPR_C_39_D_195_1985"


def test_unused_case_returns_none(tmp_path):
    s = _scraper(tmp_path, {untb.CASE_URL.format(case_id=4200): (FIX / "case_4200_unused.html").read_bytes()})
    assert s.fetch_decision(_stub(4200)) is None


# ───────────────────────────────────────────── discovery

def test_discovery_skips_done_and_gap_cached_ids(tmp_path, monkeypatch):
    monkeypatch.setattr(untb, "SEED_MAX_CASE_ID", 5)
    monkeypatch.setattr(untb, "PROBE_WINDOW", 3)
    monkeypatch.setattr(untb.UNTreatyBodyScraper, "_discover_dl", lambda self: iter(()))
    (tmp_path / "untb.juris_ids.tsv").write_text("2\tuntb_cat_X\n9\tuntb_cat_Y\n")
    s = untb.UNTreatyBodyScraper(state_dir=tmp_path)
    s.state.mark_gap(s.gap_id(4))
    ids = [st["case_id"] for st in s.discover_new()]
    # top = max(seed 5, done 9) + window 3 = 12
    assert ids == [1, 3, 5, 6, 7, 8, 10, 11, 12]
    assert all(st["decision_id"].startswith("untb_juris_") for st in s.discover_new())


def test_polite_by_construction():
    assert untb.UNTreatyBodyScraper.REQUEST_DELAY >= 5.0
    assert "opencaselaw.ch" in untb.UNTreatyBodyScraper.USER_AGENT
    assert "@" in untb.UNTreatyBodyScraper.USER_AGENT


# ───────────────────────────────────────────── registries + citation

def test_every_registry_knows_every_untb_court():
    import branch_map
    import cli_ch
    import ecli
    import export_parquet
    import mcp_server
    import proceeding_map
    import run_all_scrapers
    import run_scraper
    from quality.checks import languages

    assert run_scraper.SCRAPERS["untb"] == ("scrapers.untb", "UNTreatyBodyScraper")
    assert "untb" in run_all_scrapers.SKIP_BY_DEFAULT
    courts = untb.UN_COURTS
    assert courts <= export_parquet.EXCLUDED_COURTS
    assert courts == languages.UNTB_COURTS
    assert courts <= set(branch_map.COURT_BRANCH)
    assert courts <= set(proceeding_map.COURT_PROCEEDING)
    assert courts <= set(mcp_server.COURT_DISPLAY_NAMES)
    assert all(mcp_server.COURT_LEVELS[c] == "international" for c in courts)
    for c in courts:
        assert ecli.mint_ecli(f"{c}_X_1_2020", c, "CAT/C/1/D/1/2020", "2020-01-01") is None
        assert cli_ch.mint_cli_ch(f"{c}_X", c, "CAT/C/1/D/1/2020") is None


def test_citation_strings_come_from_the_record():
    import mcp_server

    row = {"decision_id": "untb_cat_CAT_C_51_D_426_2010", "court": "untb_cat",
           "docket_number": "CAT/C/51/D/426/2010", "docket_number_2": "426/2010",
           "title": "R.D. v. Switzerland", "decision_date": "2013-11-08"}
    c = mcp_server._build_citation_strings(row)
    assert c["citation_string_de"] == "CAT, R.D. v. Switzerland, Nr. 426/2010 vom 8. November 2013 (CAT/C/51/D/426/2010)"
    assert c["citation_string_fr"] == "CAT, R.D. v. Switzerland, n° 426/2010 du 8 novembre 2013 (CAT/C/51/D/426/2010)"
    assert c["citation_string_it"] == "CAT, R.D. v. Switzerland, n. 426/2010 del 8 novembre 2013 (CAT/C/51/D/426/2010)"
    pin = mcp_server._build_citation_strings(row, pinpoint="9.7")
    assert pin["citation_string_de"].endswith("(CAT/C/51/D/426/2010), Ziff. 9.7")
    assert pin["citation_string_fr"].endswith(", par. 9.7")


# ───────────────────────────────────────────── UN Digital Library (second index)

def test_canonical_symbols():
    assert untb.canon_symbol("CAT/C/33/D/163/2000/REV.1") == "CAT/C/33/D/163/2000/Rev.1"
    assert untb.canon_symbol("A/HRC/45/15/ADD.1") == "A/HRC/45/15/Add.1"
    assert untb.symbol_key("CAT/C/33/D/163/2000/Rev.1") == untb.symbol_key("CAT_C_33_D_163_2000_Rev.1")


def test_dl_records():
    recs = untb.parse_dl_records(_page("dl_ccpr_143.xml"))
    assert len(recs) == 3
    r = recs[0]
    assert r["recid"] == 4090924 and r["symbol"] == "CCPR/C/143/D/3665/2019"
    assert r["adopted"] == date(2025, 3, 25)              # from "Adopted 25 Mar. 2025 on …"
    assert r["title245"].startswith("Views adopted by the Committee")
    assert set(r["pdfs"]) == {"en", "fr"} and r["pdfs"]["fr"].endswith("-FR.pdf")
    assert "AUSTRALIA" in r["subjects"]


def test_header_parties_and_type():
    head = untb.clean_text(_page("dl_3665_fr_head.txt"))
    assert untb.header_parties(head) == ("Huyen Thu Thi Tran", "Australie")
    en = "Communication submitted by:\nA.B. (represented by counsel)\nState party:\nSwitzerland\n"
    assert untb.header_parties(en) == ("A.B.", "Switzerland")
    assert untb.dl_decision_type("Views adopted by the Committee", "") == "Decision on merits"
    assert untb.dl_decision_type("Decision adopted", "7. The Committee therefore decides: (a) That the "
                                 "communication is inadmissible") == "Inadmissibility decision"
    assert untb.dl_decision_type("Decision adopted", "Le Comité décide de mettre fin à l’examen "
                                 "de la communication") == "Discontinuance decision"
    assert untb.dl_decision_type("Decision adopted", "") == "Decision"


def _dl_scraper(tmp_path, monkeypatch, sessions_with_records: dict[int, str]):
    """juris exhausted; the Library answers `sessions_with_records` with the fixture page."""
    monkeypatch.setattr(untb, "SEED_MAX_CASE_ID", 0)
    monkeypatch.setattr(untb, "PROBE_WINDOW", 0)
    monkeypatch.setattr(untb, "DL_PATTERNS", {"CCPR": "CCPR/C/{s}/D/"})
    monkeypatch.setattr(untb, "SESSION_HINT", {"CCPR": 6})
    monkeypatch.setattr(untb, "RECENT_SESSIONS", 2)
    monkeypatch.setattr(untb, "WGAD_FIRST_YEAR", date.today().year + 1)     # no WGAD sweep here
    s = untb.UNTreatyBodyScraper(state_dir=tmp_path)
    page = _page("dl_ccpr_143.xml")
    empty = page.split("<record>", 1)[0] + "</collection>"
    queried: list[int] = []

    def fake_get(url, params=None, **_):
        assert url == untb.DL_SEARCH and params["of"] == "xm"
        session = int(params["p"].split("/")[2])
        queried.append(session)
        text = page if session in sessions_with_records else empty
        return SimpleNamespace(text=text, content=text.encode())

    s.get = fake_get
    s.queried = queried
    return s


def test_dl_sweep_yields_only_what_juris_lacks(tmp_path, monkeypatch):
    s = _dl_scraper(tmp_path, monkeypatch, {5: "page"})
    s.state.mark_scraped("untb_ccpr_CCPR_C_143_D_3665_2019")         # juris already holds this one
    stubs = list(s.discover_new())
    assert [st["symbol"] for st in stubs] == [r["symbol"] for r in untb.parse_dl_records(_page("dl_ccpr_143.xml"))][1:]
    assert all(st["source"] == "dl" and st["court"] == "untb_ccpr" for st in stubs)
    assert stubs[0]["decision_id"] == untb.make_decision_id("untb_ccpr", stubs[0]["symbol"])
    # sessions 1..6, then 3 empty ones above the hint end the sweep
    assert s.queried == [1, 2, 3, 4, 5, 6, 7, 8, 9]
    # empty, non-recent sessions are recorded as complete; recent ones (5, 6) never are
    assert (tmp_path / "untb.dl_sessions.tsv").read_text() == "CCPR\t1\t0\nCCPR\t2\t0\nCCPR\t3\t0\nCCPR\t4\t0\n"
    again = _dl_scraper(tmp_path, monkeypatch, {5: "page"})
    list(again.discover_new())
    assert again.queried == [5, 6, 7, 8, 9]


def test_dl_fetch_builds_the_decision(tmp_path, monkeypatch):
    fitz = pytest.importorskip("fitz")
    s = _dl_scraper(tmp_path, monkeypatch, {})
    rec = untb.parse_dl_records(_page("dl_ccpr_143.xml"))[0]
    doc = fitz.open()
    page = doc.new_page()
    for i, line in enumerate(_page("dl_3665_fr_head.txt").splitlines()[:60]):
        page.insert_text((40, 40 + 12 * i), line[:95], fontsize=8)     # the real header, one page
    pdf = doc.tobytes()
    s.get = lambda url, **_: SimpleNamespace(content=pdf, text="")
    stub = {**rec, "source": "dl", "court": "untb_ccpr", "acronym": "CCPR",
            "decision_id": untb.make_decision_id("untb_ccpr", rec["symbol"]),
            "docket_number": rec["symbol"], "url": untb.DL_RECORD.format(recid=rec["recid"])}
    d = s.fetch_decision(stub)
    assert d.court == "untb_ccpr" and d.language == "fr" and d.canton == "UN"
    assert d.title == "Huyen Thu Thi Tran c. Australie"
    assert d.docket_number == "CCPR/C/143/D/3665/2019" and d.docket_number_2 == "3665/2019"
    assert d.decision_date == date(2025, 3, 25)
    assert d.decision_type == "Decision on merits"
    assert d.source_url == "https://digitallibrary.un.org/record/4090924"
    assert d.pdf_url.endswith("-FR.pdf") and d.external_id == "undl:4090924"
    assert "Subjects: CIVIL AND POLITICAL RIGHTS, AUSTRALIA" in d.regeste


# ───────────────────────────────────────────── Working Group on Arbitrary Detention

def test_wgad_records_and_titles():
    recs = {r["symbol"]: r for r in untb.parse_dl_records(_page("dl_wgad.xml"))}
    assert set(recs) == {"A/HRC/WGAD/2024/58", "A/HRC/WGAD/2012/41"}
    assert untb.committee_for("A/HRC/WGAD/2024/58") == ("unwgad", "WGAD")
    new = untb.wgad_meta(recs["A/HRC/WGAD/2024/58"]["title245"])
    assert new == {"title": "Opinion No. 58/2024 concerning Mohammad Arfat (India)",
                   "opinion_no": "58/2024", "state": "India", "session_end": date(2024, 11, 15)}
    old = untb.wgad_meta(recs["A/HRC/WGAD/2012/41"]["title245"])
    assert old == {"title": "Opinion No. 41/2012, Togo", "opinion_no": "41/2012",
                   "state": "Togo", "session_end": date(2012, 11, 23)}
    span = untb.wgad_meta("Opinions adopted … at its 63rd session, 30 April-4 May 2012 : No. 2/2012, Panama")
    assert span["session_end"] == date(2012, 5, 4)


def test_wgad_year_sweep(tmp_path, monkeypatch):
    monkeypatch.setattr(untb, "SEED_MAX_CASE_ID", 0)
    monkeypatch.setattr(untb, "PROBE_WINDOW", 0)
    monkeypatch.setattr(untb, "DL_PATTERNS", {})
    this_year = date.today().year
    monkeypatch.setattr(untb, "WGAD_FIRST_YEAR", this_year - 3)
    s = untb.UNTreatyBodyScraper(state_dir=tmp_path)
    page = _page("dl_wgad.xml")
    empty = page.split("<record>", 1)[0] + "</collection>"
    queried = []

    def fake_get(url, params=None, **_):
        year = int(params["p"].split("/")[3])
        queried.append(year)
        text = page if year == this_year - 3 else empty
        return SimpleNamespace(text=text, content=text.encode())

    s.get = fake_get
    stubs = list(s.discover_new())
    assert queried == [this_year - 3, this_year - 2, this_year - 1, this_year]
    assert {st["court"] for st in stubs} == {"unwgad"} and len(stubs) == 2
    # the empty, non-recent year is complete; the two recent years never are
    assert (tmp_path / "untb.dl_sessions.tsv").read_text() == f"WGAD\t{this_year - 2}\t0\n"


def test_wgad_fetch_and_dateless_citation(tmp_path, monkeypatch):
    fitz = pytest.importorskip("fitz")
    import mcp_server
    s = untb.UNTreatyBodyScraper(state_dir=tmp_path)
    rec = next(r for r in untb.parse_dl_records(_page("dl_wgad.xml")) if r["symbol"].endswith("2024/58"))
    doc = fitz.open()
    page = doc.new_page()
    for i in range(40):
        page.insert_text((40, 40 + 12 * i), "The Working Group considers that the deprivation of "
                         "liberty of Mr. Arfat is arbitrary and falls within categories I and III.",
                         fontsize=7)
    pdf = doc.tobytes()
    s.get = lambda url, **_: SimpleNamespace(content=pdf, text="")
    stub = {**rec, "source": "dl", "court": "unwgad", "acronym": "WGAD",
            "decision_id": untb.make_decision_id("unwgad", rec["symbol"]),
            "docket_number": rec["symbol"], "url": untb.DL_RECORD.format(recid=rec["recid"])}
    d = s.fetch_decision(stub)
    assert d.court == "unwgad" and d.decision_type == "Opinion" and d.canton == "UN"
    assert d.title == "Opinion No. 58/2024 concerning Mohammad Arfat (India)"
    assert d.docket_number_2 == "58/2024" and d.decision_date == date(2024, 11, 15)
    assert "Groupe de travail sur la détention arbitraire" in d.regeste
    row = {"decision_id": d.decision_id, "court": d.court, "docket_number": d.docket_number,
           "docket_number_2": d.docket_number_2, "title": d.title,
           "decision_date": d.decision_date.isoformat()}
    c = mcp_server._build_citation_strings(row)
    assert c["citation_string_de"] == ("WGAD, Opinion No. 58/2024 concerning Mohammad Arfat (India) "
                                       "(A/HRC/WGAD/2024/58)")
    assert "2024" not in c["citation_string_fr"].split("(A/HRC")[0].replace("58/2024", "")


# ───────────────────────────────────────────── broken files vs blocks

def _status_error(code):
    import requests
    resp = requests.Response()
    resp.status_code = code
    return requests.HTTPError(response=resp)


def test_broken_file_falls_through_to_the_next_rendition(tmp_path):
    """docstore answers some files with 5xx for good; that file is skipped,
    the next format or language is used, and nothing counts toward MAX_ERRORS."""
    import requests
    page = (FIX / "case_2_rd_v_switzerland.html").read_bytes()
    links = untb.parse_case_page(page.decode())["links"]
    s = untb.UNTreatyBodyScraper(state_dir=tmp_path)

    def fake_get(url, **_):
        if url == untb.CASE_URL.format(case_id=2):
            return SimpleNamespace(content=page, text=page.decode())
        if url == links["fr"]["html"]:
            raise requests.exceptions.RetryError("Max retries exceeded (too many 500 error responses)")
        if url == links["fr"]["docx"]:
            raise _status_error(500)
        if url == links["fr"]["pdf"]:
            raise requests.ConnectionError("reset")
        if url == links["en"]["html"]:
            body = "<p>9.2 The Committee must decide whether the forcible return of the complainant " \
                   "to Ethiopia would violate the State party's obligation under article 3.</p>" * 8
            return SimpleNamespace(content=f"<html><body>{body}</body></html>".encode(), text="")
        raise AssertionError(url)

    s.get = fake_get
    d = s.fetch_decision(_stub(2))
    assert d is not None and d.language == "en"           # all three French files broken
    assert "forcible return of the complainant" in d.full_text


@pytest.mark.parametrize("exc", ["403", "429-retry"])
def test_refusal_still_stops_the_run(tmp_path, exc):
    import requests
    page = (FIX / "case_2_rd_v_switzerland.html").read_bytes()
    s = untb.UNTreatyBodyScraper(state_dir=tmp_path)

    def fake_get(url, **_):
        if url == untb.CASE_URL.format(case_id=2):
            return SimpleNamespace(content=page, text=page.decode())
        if exc == "403":
            raise _status_error(403)
        raise requests.exceptions.RetryError("Max retries exceeded (too many 429 error responses)")

    s.get = fake_get
    with pytest.raises(requests.RequestException):
        s.fetch_decision(_stub(2))
