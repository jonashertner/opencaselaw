"""FINMA circulars: the version history has to be right, or it is worse
than not having one.

The fixture is a trimmed copy of FINMA's real 2008 archive page, kept
because it contains the two cases that broke the first implementation:

  * RS 2008/11 has a teaser whose "Zuletzt geändert" line repeats the
    previous entry's date. A regex that scanned forward from the link
    stamped the 25.01.2017 file as 13.09.2013 — a version history whose
    dates are wrong sends a practitioner to the wrong text.
  * RS 2008/30 carries .xls annexes, which share the circular number and
    date with the PDF and so collided on doc_id. The DB upserts on
    doc_id, so a collision silently deletes a document.

A second fixture holds real front pages (finma_rs_front_pages.json), for
the date FINMA prints on the text itself: the in-force listing's date
lags it (RS 2017/01 in force was listed 31.10.2019 while its text says
last amended 4 November 2020, the same date as its archived predecessor).

Offline by construction: no network, no PDF fetching.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import json  # noqa: E402

import pytest  # noqa: E402

from scrapers.practice.finma_rundschreiben import (  # noqa: E402
    FinmaRundschreibenScraper, _archive_date, _doc_number,
    _front_page_amendment, _in_force_date, _iso, _short_number,
    _version_from_filename,
)

FIXTURE = (REPO / "tests" / "fixtures" / "practice"
           / "finma_rs_archiv_2008_excerpt.html")
FRONT_PAGES = json.loads(
    (REPO / "tests" / "fixtures" / "practice"
     / "finma_rs_front_pages.json").read_text(encoding="utf-8"))
ARCHIVE_URL = "https://www.finma.ch/de/dokumentation/archiv/rundschreiben/archiv-2008/"


@pytest.fixture(scope="module")
def stubs():
    scraper = FinmaRundschreibenScraper()
    page = FIXTURE.read_text(encoding="utf-8")
    return scraper, list(scraper._parse_archive_page(page, "de", ARCHIVE_URL))


# ── numbering ────────────────────────────────────────────────────────

def test_doc_number_is_finmas_own_zero_padded():
    assert _doc_number('2008/1 FINMA-Rundschreiben "X"') == "FINMA-RS 2008/01"
    assert _doc_number('2013/03 FINMA-Rundschreiben "Prüfwesen"') == "FINMA-RS 2013/03"
    assert _doc_number("no number here") == ""


def test_short_number_is_the_other_spelling_practitioners_use():
    """Both 'FINMA-RS 2008/01' and 'FINMA-RS 08/1' appear in briefs; both
    are carried in topics so either finds the circular."""
    assert _short_number("FINMA-RS 2008/01") == "FINMA-RS 08/1"
    assert _short_number("FINMA-RS 2013/03") == "FINMA-RS 13/3"


def test_swiss_dates_parse_day_first():
    assert _iso("Zuletzt geändert: 13.09.2013") == "2013-09-13"
    assert _iso("Dernière modification: 1.6.2012") == "2012-06-01"
    assert _iso("no date") == ""


def test_filename_dates_are_only_taken_when_plausible():
    assert _version_from_filename("x/rs-08-01-letzte-aenderung-20130101.pdf") == "2013-01-01"
    assert _version_from_filename("x/rs-08_01_erlass_20081120.pdf") == "2008-11-20"
    assert _version_from_filename("x/rs-08-01-aendper20110901.pdf") == "2011-09-01"
    assert _version_from_filename("x/no-date-here.pdf") == ""
    # 20139999 is not a date; a bad parse must yield nothing, not a
    # fabricated day.
    assert _version_from_filename("x/rs-08-01-20139999.pdf") == ""


# ── the two bugs the fixture exists for ──────────────────────────────

def test_each_version_keeps_the_date_the_page_states_for_it(stubs):
    """The regression. FINMA's page shows 13.09.2013 for the file named
    ...20170125.pdf; that is FINMA's own metadata and we publish it as
    given. What must never happen again is a date lifted from a
    NEIGHBOURING entry because this one had no line of its own."""
    _, docs = stubs
    by_file = {d["pdf_url"].split("/")[-1].split("?")[0]: d["date"]
               for d in docs}
    expected = {
        "rs-08-11-erlass-20081120.pdf": "2008-11-20",
        "rs-08-11-letzte-aenderung-20110909.pdf": "2011-09-09",
        "rs-08-11-letzte-aenderung-20121206.pdf": "2012-12-06",
        "rs-08-11-letzte-aenderung-20130913.pdf": "2013-09-13",
    }
    for name, date in expected.items():
        assert by_file.get(name) == date, f"{name} mis-dated"


def test_no_document_can_overwrite_another(stubs):
    """doc_id is the DB's primary key with ON CONFLICT DO UPDATE, so two
    documents sharing one is silent data loss, not a cosmetic clash."""
    scraper, docs = stubs
    ids = [scraper._make_doc_id(d) for d in docs]
    assert len(set(ids)) == len(ids), "doc_id collision would delete a document"


def test_spreadsheet_annexes_are_skipped_and_counted(stubs):
    """RS 2008/30's annexes are .xls. There is no text to extract, and a
    body-less row is noise in a full-text index — but a skipped document
    must be visible in the run summary, never silently absent."""
    scraper, docs = stubs
    assert all(d["pdf_url"].split("?")[0].lower().endswith(".pdf")
               for d in docs)
    assert scraper.skipped_non_pdf > 0


# ── shape of what gets written ───────────────────────────────────────

def test_versions_of_one_circular_share_its_number(stubs):
    """A search for the number must return the whole history; `date` is
    what separates the versions within it."""
    _, docs = stubs
    hist = sorted((d["date"] for d in docs
                   if d["doc_number"] == "FINMA-RS 2008/11"))
    assert len(hist) >= 4
    assert hist == sorted(hist)
    assert all(d["doc_number"].startswith("FINMA-RS ") for d in docs)


def test_every_document_is_dated(stubs):
    """An undated version cannot be matched against a date of conduct,
    which is the only reason to keep superseded texts at all."""
    _, docs = stubs
    assert all(d["date"] for d in docs)


def test_superseded_documents_say_so_in_every_language(stubs):
    """topics is FTS-indexed, so status has to be searchable in the
    language the reader works in."""
    _, docs = stubs
    for word in ("aufgehoben oder ersetzt", "abrogé ou remplacé",
                 "abrogata o sostituita", "repealed or replaced"):
        assert all(word in d["topics"] for d in docs)


def test_doc_ids_are_stable_across_runs(stubs):
    """Re-running must not mint new ids for the same documents, or the
    incremental append and the DB upsert both break."""
    scraper, docs = stubs
    once = [scraper._make_doc_id(d) for d in docs]
    twice = [scraper._make_doc_id(d) for d in docs]
    assert once == twice
    assert all(i.startswith("finma_rs_") for i in once)


# ── front-page dates ─────────────────────────────────────────────────

def _front(key):
    return FRONT_PAGES[key]["front_matter"]


def test_front_page_date_is_read_in_every_layout():
    """The plain layout, and the two that PDF extraction scrambles: EN
    puts "Concordance:" between label and date, IT lists two labels
    before their two dates. In both, the date before the "[changes are
    marked]" bracket is the last-amendment date."""
    assert _front_page_amendment(_front("rs_2017_01_current_de")) == "2020-11-04"
    assert _front_page_amendment(_front("rs_2017_01_current_en")) == "2020-11-04"
    # "Entrata in vigore: Ultima modifica: 1° gennaio 2015 8 marzo 2023 [...":
    # the first date after the label is the entry into force, not the answer.
    assert _front_page_amendment(_front("rs_2015_02_current_it")) == "2023-03-08"
    assert _front_page_amendment(_front("rs_2016_07_current_en")) == "2021-05-06"
    # No bracket: only a date straight after the label counts.
    assert _front_page_amendment(_front("rs_2008_04_archive_2013_de")) == "2008-11-20"


def test_no_label_no_date():
    assert _front_page_amendment("Rundschreiben 2008/1\nErlass: 20. November 2008") == ""
    assert _front_page_amendment("") == ""
    # A label with nothing readable next to it is no date, not a guess.
    assert _front_page_amendment("Letzte Änderung:\nKonkordanz: vom 20. November 2008") == ""


def test_in_force_version_takes_a_later_front_page_date():
    """The LinkedIn case: listed 31.10.2019, text amended 4 November 2020."""
    fp = FRONT_PAGES["rs_2017_01_current_de"]
    assert fp["listed_date"] == "2019-10-31"
    assert _in_force_date(fp["listed_date"], fp["front_matter"]) == "2020-11-04"
    # An empty listing date is filled from the text.
    assert _in_force_date("", fp["front_matter"]) == "2020-11-04"


def test_in_force_version_keeps_its_listing_against_an_earlier_front_page():
    """FINMA leaves stale front pages: RS 2008/04's 2013 file still prints
    20 November 2008. An earlier printed date proves nothing."""
    assert _in_force_date("2013-01-01", _front("rs_2008_04_archive_2013_de")) == "2013-01-01"
    assert _in_force_date("2019-10-31", "no front page") == "2019-10-31"


_RS_2008_02_PANEL = ["2008-11-20", "2009-11-19", "2011-04-03",
                     "2012-06-01", "2013-06-26"]


def test_archive_version_takes_the_date_front_page_and_file_name_agree_on():
    """Listed 03.04.2011; front page and rs-08-02-letzte-aenderung-20110304
    both say 4 March 2011."""
    fp = FRONT_PAGES["rs_2008_02_archive_2011_de"]
    assert fp["listed_date"] == "2011-04-03"
    assert _archive_date(fp["listed_date"], fp["pdf_url"], fp["front_matter"],
                         _RS_2008_02_PANEL, "2024-01-01") == "2011-03-04"


def test_archive_front_page_alone_does_not_move_a_version():
    """RS 2008/01's Erlass file prints a last amendment of 20 November 2009,
    after the 01.01.2009 version: taken alone it would reorder the history."""
    fp = FRONT_PAGES["rs_2008_01_archive_erlass_de"]
    assert _front_page_amendment(fp["front_matter"]) == "2009-11-20"
    assert _archive_date(fp["listed_date"], fp["pdf_url"], fp["front_matter"],
                         ["2008-11-20", "2009-01-01", "2011-09-01"],
                         None) == "2008-11-20"


def test_archive_correction_never_reorders_or_ties():
    fp = FRONT_PAGES["rs_2008_02_archive_2011_de"]
    # Another version between the listed and the printed date: refused.
    assert _archive_date(fp["listed_date"], fp["pdf_url"], fp["front_matter"],
                         _RS_2008_02_PANEL + ["2011-03-20"], None) == "2011-04-03"
    # Landing on another version's date: refused.
    assert _archive_date(fp["listed_date"], fp["pdf_url"], fp["front_matter"],
                         _RS_2008_02_PANEL + ["2011-03-04"], None) == "2011-04-03"
    # RS 2018/01: front page and file name agree on 4 November 2020, which
    # is the in-force version's date. A tie with the version in force is
    # the error being removed, so the listed date stands.
    fp = FRONT_PAGES["rs_2018_01_archive_2017_de"]
    assert _archive_date(fp["listed_date"], fp["pdf_url"], fp["front_matter"],
                         [fp["listed_date"]], "2020-11-04") == "2017-01-25"


def _stub(key, version_key, **extra):
    fp = FRONT_PAGES[key]
    return {"pdf_url": fp["pdf_url"], "title": "x",
            "doc_number": "FINMA-RS " + key[3:10].replace("_", "/"),
            "date": fp["listed_date"], "language": "de",
            "version_key": version_key, **extra}


def test_scraper_dates_the_written_record_without_moving_its_id():
    scraper = FinmaRundschreibenScraper()
    stub = _stub("rs_2017_01_current_de", "current")
    doc = scraper._normalize(stub, _front("rs_2017_01_current_de"))
    assert doc["date"] == "2020-11-04"
    assert doc["doc_id"] == "finma_rs_2017_01_current_de"
    assert scraper.front_page_dated == 1

    # The archived predecessor's text agrees with its listing: untouched.
    stub = _stub("rs_2017_01_archive_2019_de", "20191031_4ff052a5",
                 panel_dates=["2016-09-22", "2019-10-31"])
    scraper._in_force_langs.add("de")
    doc = scraper._normalize(stub, _front("rs_2017_01_archive_2019_de"))
    assert doc["date"] == "2019-10-31"
    assert doc["doc_id"] == "finma_rs_2017_01_20191031_4ff052a5_de"


def test_scraper_leaves_archive_dates_alone_without_the_in_force_listing():
    """No in-force listing for the language means no upper bound."""
    scraper = FinmaRundschreibenScraper()
    stub = _stub("rs_2008_02_archive_2011_de", "20110403_e3444d71",
                 panel_dates=_RS_2008_02_PANEL)
    body = _front("rs_2008_02_archive_2011_de")
    assert scraper._normalize(stub, body)["date"] == "2011-04-03"
    assert scraper.front_page_disagreements == 1
    scraper._in_force_langs.add("de")
    assert scraper._normalize(stub, body)["date"] == "2011-03-04"


def test_archive_stubs_carry_their_circulars_version_dates(stubs):
    _, docs = stubs
    for d in docs:
        assert d["date"] in d["panel_dates"]
        assert d["panel_dates"] == sorted(d["panel_dates"])
    hist = {d["date"] for d in docs if d["doc_number"] == "FINMA-RS 2008/11"}
    assert all(set(d["panel_dates"]) == hist for d in docs
               if d["doc_number"] == "FINMA-RS 2008/11")


def test_backfill_redates_held_rows_by_the_same_rules(tmp_path):
    sys.path.insert(0, str(REPO / "scripts"))
    import backfill_finma_front_page_dates as backfill

    def row(key, doc_id, doc_number):
        fp = FRONT_PAGES[key]
        return {"doc_id": doc_id, "doc_number": doc_number, "language": "de",
                "date": fp["listed_date"], "pdf_url": fp["pdf_url"],
                "body_text": fp["front_matter"], "doc_type": "rundschreiben"}

    rows = [
        row("rs_2017_01_current_de", "finma_rs_2017_01_current_de", "FINMA-RS 2017/01"),
        row("rs_2017_01_archive_2019_de", "finma_rs_2017_01_20191031_4ff052a5_de",
            "FINMA-RS 2017/01"),
        row("rs_2008_02_archive_2011_de", "finma_rs_2008_02_20110403_e3444d71_de",
            "FINMA-RS 2008/02"),
        row("rs_2018_01_archive_2017_de", "finma_rs_2018_01_20170125_041ab6c0_de",
            "FINMA-RS 2018/01"),
        {**row("rs_2018_01_archive_2017_de", "finma_rs_2018_01_current_de",
               "FINMA-RS 2018/01"), "date": "2020-11-04"},
    ]
    path = tmp_path / "finma_rs.jsonl"
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    fixes = backfill.corrections(backfill._latest(path))
    assert {r["doc_id"]: r["date"] for r in fixes} == {
        "finma_rs_2008_02_20110403_e3444d71_de": "2011-03-04",
        "finma_rs_2017_01_current_de": "2020-11-04",
    }
