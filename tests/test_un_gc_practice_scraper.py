"""UN treaty-body General Comments / Recommendations (scrapers/practice/un_gc.py).

Golden fixtures captured 2026-10-25 from tbinternet.ohchr.org: the first two
pages of the CCPR listing (page 2 holds the old INT/ entries, whose symbol is
only in their download link) and two Download.aspx pages (CCPR/C/GC/37 with
doc/docx/pdf/html per language, INT/CCPR/GEC/6621 with .doc only). No network.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

FIX = REPO / "tests" / "fixtures" / "practice" / "un_gc"

from scrapers.practice import un_gc as g  # noqa: E402


def _read(name):
    return (FIX / name).read_bytes().decode("utf-8", "replace")


def test_listing_rows_and_pages():
    rows, pages = g.parse_listing(_read("listing_ccpr_p1.html"))
    assert pages == 4 and len(rows) == 10
    assert rows[0] == {"title": "General Comment No. 37 on Article 21 (Right of peaceful assembly)",
                       "symbol": "CCPR/C/GC/37", "date": "2020-07-23"}


def test_old_entries_take_the_symbol_from_their_link():
    rows, _ = g.parse_listing(_read("listing_ccpr_p2.html"))
    by_title = {r["title"][:22]: r for r in rows}
    assert by_title["General Comment No. 20"]["symbol"] == "INT/CCPR/GEC/6621"
    assert by_title["General Comment No. 20"]["date"] == ""
    assert by_title["General Comment No. 22"]["symbol"] == "CCPR/C/21/Rev.1/Add.4"
    assert len(rows) == 10


def test_postback_form_targets_the_next_page_button():
    form = g.postback_form(_read("listing_ccpr_p1.html"))
    assert "__VIEWSTATE" in form
    assert any(k.endswith("ctl14") for k in form)          # the rgPageNext input


def test_download_page_formats():
    new = g.parse_download_page(_read("download_ccpr_gc37.html"))
    assert set(new) == {"en", "fr"} and set(new["fr"]) == {"doc", "docx", "pdf", "html"}
    assert new["fr"]["html"].startswith("https://docstore.ohchr.org/")
    old = g.parse_download_page(_read("download_int_6621.html"))
    assert {lang: set(f) for lang, f in old.items()} == {"en": {"doc"}, "fr": {"doc"}}
    assert old["fr"]["doc"].startswith("https://tbinternet.ohchr.org/_layouts/15/TreatyBodyExternal/DownloadDraft.aspx")


def test_session_year_and_topics():
    assert g._session_year("General Comment No. 18 - Non-discrimination (Thirty-seventh session, 1989)") == "1989"
    assert g._session_year("no year") == ""
    assert g.topics_for("Joint general recommendation No. 31 of CEDAW", "UN-CEDAW") == [
        "CEDAW", "General recommendation", "General recommendation No. 31", "Joint"]


FR_BODY = "<p>" + ("1. Le Comité rappelle que l’article 7 du Pacte interdit la torture et les peines ou "
                   "traitements cruels, inhumains ou dégradants dans toutes les circonstances. ") * 20 + "</p>"
EN_BODY = "<p>" + ("1. The Committee recalls that article 7 of the Covenant prohibits torture and cruel, "
                   "inhuman or degrading treatment or punishment in all circumstances. ") * 20 + "</p>"


@pytest.fixture
def scraper(tmp_path, monkeypatch):
    monkeypatch.setattr(g.UnGeneralCommentsScraper, "OUTPUT_DIR", tmp_path / "practice")
    monkeypatch.setattr(g, "TREATIES", {8: "UN-CCPR"})
    monkeypatch.setattr(g.UnGeneralCommentsScraper, "REQUEST_DELAY", 0)
    s = g.UnGeneralCommentsScraper()
    p1, p2 = _read("listing_ccpr_p1.html"), _read("listing_ccpr_p2.html")
    new_dl, old_dl = _read("download_ccpr_gc37.html"), _read("download_int_6621.html")
    new_files = g.parse_download_page(new_dl)
    old_files = g.parse_download_page(old_dl)
    calls = []

    def fake_get(url, **_):
        calls.append(url)
        if "TBSearch.aspx" in url:
            body = p1
        elif "Download.aspx" in url:
            body = old_dl if "INT%2F" in url else new_dl
        elif url in (new_files["fr"]["html"],):
            body = FR_BODY
        elif url in (new_files["en"]["html"],):
            body = EN_BODY
        elif url in (old_files["fr"]["doc"], old_files["en"]["doc"]):
            return SimpleNamespace(content=b"DOC", text="", raise_for_status=lambda: None)
        else:
            body = ""                           # every other new-style file: same new template
        return SimpleNamespace(content=body.encode(), text=body, raise_for_status=lambda: None)

    posts = []

    def fake_post(url, data=None, **_):
        posts.append(data)
        return SimpleNamespace(text=p2, raise_for_status=lambda: None)

    s.get = fake_get
    s.session.post = fake_post
    monkeypatch.setattr(g, "_EXTRACT", {**g._EXTRACT,
                                        "doc": lambda data: EN_BODY.replace("<p>", "").replace("</p>", "")})
    s.calls, s.posts = calls, posts
    return s


def test_run_writes_both_languages_and_skips_them_next_time(scraper, monkeypatch):
    first = scraper.run()
    assert first["failed"] == 0 and first["new"] > 0
    assert len(scraper.posts) == 3                       # pages 2..4 via postback
    import json
    rows = [json.loads(line) for line in (scraper.OUTPUT_DIR / "un_gc.jsonl").read_text().splitlines()]
    gc37 = {r["language"]: r for r in rows if r["doc_number"] == "CCPR/C/GC/37"}
    assert set(gc37) == {"fr", "en"}
    assert gc37["fr"]["issuing_authority"] == "UN-CCPR" and gc37["fr"]["doc_type"] == "general_comment"
    assert gc37["fr"]["body_text"].startswith("1. Le Comité rappelle")
    assert gc37["fr"]["doc_id"] == "un_gc_ccpr_c_gc_37_fr"
    # the old .doc entries: the French "doc" here is English text -> dropped, English kept
    old = [r for r in rows if r["doc_number"] == "INT/CCPR/GEC/6621"]
    assert [r["language"] for r in old] == ["en"] and old[0]["date"] == ""
    again = g.UnGeneralCommentsScraper()
    again.get, again.session.post = scraper.get, scraper.session.post
    second = again.run()
    assert second["new"] == 0


def test_empty_listing_is_an_error(scraper):
    scraper.get = lambda url, **_: SimpleNamespace(text="<html></html>", content=b"",
                                                   raise_for_status=lambda: None)
    with pytest.raises(RuntimeError, match="no General Comments listed"):
        scraper.run()


def test_annex_cut_keeps_only_the_listed_annex():
    report = ("Rapport du Comité\n" + "x " * 3000 + "\nAnnexe VIII\nRapporteurs\n" + "y " * 500 +
              "\nAnnexe IX\nObservation générale du Comité contre la torture sur l’application de l’article 3\n"
              + "1. Le Comité. " * 300 + "\nAnnexe X\nConstatations et décisions\n" + "z " * 3000)
    part = g.cut_annex("Annex IX: General Comment No. 01: Implementation of article 3", report)
    assert part.startswith("Annexe IX\nObservation générale") and "Constatations" not in part
    assert g.cut_annex("General Comment No. 4", report) == ""           # no annex named: no row


@pytest.mark.parametrize("head, iso", [
    ("United Nations\nCAT/C/GC/3\nDistr.: General\n13 December 2012\nOriginal: English", "2012-12-13"),
    ("Nations Unies\nCAT/C/GC/3\nDistr. générale\n13 décembre 2012", "2012-12-13"),
    ("no date at all", ""),
])
def test_header_date(head, iso):
    assert g.header_date(head) == iso


def test_document_date_order():
    old = {"date": "", "symbol": "INT/CCPR/GEC/6621", "title": "General Comment No. 20 (Forty-fourth session, 1992)"}
    assert g.document_date(old, "Distr. 10 March 2008") == "1992"      # session year beats a compilation date
    new = {"date": "", "symbol": "CAT/C/GC/3", "title": "General comment No. 3 (2012)"}
    assert g.document_date(new, "Distr.: General\n13 December 2012") == "2012-12-13"
    assert g.document_date({**new, "date": "2012-11-19"}, "13 December 2012") == "2012-11-19"
