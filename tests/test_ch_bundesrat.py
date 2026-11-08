"""ch_bundesrat scraper tests (offline).

The bj.admin.ch CMS migration (2026) retired the legacy
``/bj/de/home/publiservice/publikationen/beschwerdeentscheide.html`` listing
(now a hard 404, which crashed discovery) and replaced the detail URLs with
flat descriptive slugs of the form
``/de/entscheid-des-bundesrates-vom-13-juni-2025-eda``.

Each URL scheme used to mint its own id, so 19 decisions were served as 39
rows (runbooks/ch_bundesrat_rekey_2026-11.md). These tests pin the stable id
(``YYYY-MM-DD-<descriptor>`` from the slug, never the URL), the four EJPD slug
families the old matcher skipped, the date taken from the decision text (not
the first date in it) and the PDF stem shared by all URL schemes.

Fixtures: the 19 listing cards and one detail page of bj.admin.ch, read
2026-11-08 (svg/script stripped).
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scrapers.ch_bundesrat import (  # noqa: E402
    CHBundesratScraper,
    _parse_bundesrat_slug,
    build_decision,
    decision_date_from_text,
    parse_detail,
    parse_listing,
    pdf_stem,
)

FIX = Path(__file__).parent / "fixtures"
LISTING = (FIX / "ch_bundesrat_listing_20261108.html").read_text(encoding="utf-8")
DETAIL = (FIX / "ch_bundesrat_detail_20261108.html").read_text(encoding="utf-8")


def test_parse_slug_basic():
    assert _parse_bundesrat_slug("entscheid-des-bundesrates-vom-13-juni-2025-eda") == (
        "2025-06-13", "eda")


def test_parse_slug_transliterated_month_and_multiword_descriptor():
    assert _parse_bundesrat_slug(
        "entscheid-des-bundesrates-vom-13-maerz-2015-kanton-graubuenden"
    ) == ("2015-03-13", "kanton-graubuenden")


def test_parse_slug_no_descriptor():
    assert _parse_bundesrat_slug("entscheid-des-bundesrates-vom-8-mai-2020") == ("2020-05-08", "")


def test_parse_slug_non_matching_and_impossible_date():
    assert _parse_bundesrat_slug("some-other-page") == (None, "")
    assert _parse_bundesrat_slug("entscheid-des-bundesrates-vom-31-juni-2025-eda")[0] is None


# ── listing ──────────────────────────────────────────────────────────────

def test_listing_has_all_19_entries_including_the_ejpd_letters():
    entries = parse_listing(LISTING)
    dockets = [e["docket_number"] for e in entries]
    assert len(dockets) == 19 == len(set(dockets))
    # the four slug families the old "entscheid-des-bundesrates" matcher skipped
    by = {e["docket_number"]: e for e in entries}
    assert by["2024-09-20-dpsa"]["decision_type"] == "Schreiben"
    assert by["2024-10-04-dfae"]["decision_type"] == "Überweisung"
    assert by["2024-10-04-konsularische-direktion"]["decision_type"] == "Überweisung"
    assert by["2024-07-09-eda"]["decision_type"] == "Zwischenverfügung"
    assert by["2025-10-08-kanton-aargau"]["decision_type"] == "Beschwerdeentscheid"
    # three decisions of 2025-06-13 stay apart
    assert {d for d in dockets if d.startswith("2025-06-13")} == {
        "2025-06-13-eda", "2025-06-13-kanton-zuerich",
        "2025-06-13-bundesgerichtliches-verfahren"}


def test_listing_title_is_the_card_heading_not_the_link_label():
    by = {e["docket_number"]: e for e in parse_listing(LISTING)}
    t = by["2025-10-08-kanton-aargau"]["title"]
    assert t == "Entscheid des Bundesrates in der Beschwerdesache A. und B. gegen Kanton Aargau"
    assert not any(e["title"].startswith("Mehr über") for e in by.values())


def test_listing_excludes_non_detail_links():
    urls = [e["detail_url"] for e in parse_listing(LISTING)]
    assert not any("entscheidsuche.ch" in u for u in urls)
    assert not any(u.rstrip("/").endswith("beschwerdeentscheide-des-bundesrates") for u in urls)


class _Resp:
    def __init__(self, text="", content=b""):
        self.text = text
        self.content = content


def _scraper(tmp_path):
    return CHBundesratScraper(state_dir=tmp_path)


def test_discover_yields_stable_dockets_and_slug_dates(tmp_path):
    s = _scraper(tmp_path)
    s.get = lambda url, **k: _Resp(LISTING)
    stubs = {st["docket_number"]: st for st in s.discover_new()}
    assert len(stubs) == 19 and s.portal_count == 19
    assert stubs["2025-06-13-eda"]["decision_date"] == "2025-06-13"
    assert stubs["2024-09-20-dpsa"]["decision_type"] == "Schreiben"
    # never a URL-shaped docket
    assert not any("/" in d or d.endswith(".html") for d in stubs)


def test_discover_skips_known_ids(tmp_path):
    s = _scraper(tmp_path)
    s.state.mark_scraped("ch_bundesrat_2025-06-13-eda")
    s.get = lambda url, **k: _Resp(LISTING)
    assert "2025-06-13-eda" not in {st["docket_number"] for st in s.discover_new()}


def test_slug_without_date_is_an_error_not_a_docket(tmp_path, caplog):
    html = LISTING.replace("vom-8-oktober-2025-kanton-aargau", "vom-8-oktobre-2025-kanton-aargau")
    s = _scraper(tmp_path)
    s.get = lambda url, **k: _Resp(html)
    with caplog.at_level(logging.ERROR):
        dockets = {st["docket_number"] for st in s.discover_new()}
    assert len(dockets) == 18
    assert not any("oktobre" in d for d in dockets)
    assert "slug without a date" in caplog.text


def test_empty_listing_raises(tmp_path):
    s = _scraper(tmp_path)
    s.get = lambda url, **k: _Resp("<html><body><p>Wartung</p></body></html>")
    with pytest.raises(RuntimeError):
        list(s.discover_new())


# ── detail page ──────────────────────────────────────────────────────────

def test_parse_detail():
    d = parse_detail(DETAIL)
    assert d["title"] == "Entscheid des Bundesrates in der Beschwerdesache A. und B. gegen Kanton Aargau"
    assert d["pdf_url"].endswith("/2025-10-08-beschwerde-kanton-aargau.pdf")
    assert d["publication_date"] == "2025-10-08"
    assert d["download_date"] == "2025-10-08"
    assert d["keywords"].startswith("Nichteintreten, Sachliche Zuständigkeit")


# ── dates ────────────────────────────────────────────────────────────────

AARGAU_TEXT = (
    "1/5\nDer SCHWEIZERISCHE BUNDESRAT\nhat in der Beschwerdesache\nA. und B.,\n"
    "gegen\nKanton Aargau, vertreten durch den Regierungsrat, Postfach, 5001 Aarau\n"
    "betreffend Regierungsratsbeschluss Nr. 2025-000485 vom 7. Mai 2025: A. und B.; Auf-\n"
    "sichtsanzeige vom 3. April 2024\nbefunden und erwogen:\n...\n"
    "2. Die Beschwerdeführenden haben die Verfahrenskosten von CHF 1000 zu bezahlen.\n"
    "3003 Bern,\nIM AUFTRAG DES SCHWEIZERISCHEN BUNDESRATES\nDer Bundeskanzler\n"
)


def test_date_comes_from_the_place_date_line_not_the_first_date():
    text = ("hat in der Beschwerdesache A. gegen EDA betreffend Verfügung vom 20. Dezember 2024 "
            "...\n2. Es werden keine Verfahrenskosten erhoben.\n3003 Bern, 13. Juni 2025\n"
            "IM AUFTRAG DES SCHWEIZERISCHEN BUNDESRATES")
    assert decision_date_from_text(text) == "2025-06-13"


def test_letterhead_date_of_an_ejpd_letter_and_french_form():
    letter = ("Numéro du dossier : 361-3769/6\nBerne, le 4 octobre 2024\nAffaire A. contre DFAE : "
              "transmission\n... prise de position du DFAE du 17 septembre 2024 ...")
    assert decision_date_from_text(letter) == "2024-10-04"
    assert decision_date_from_text("Berne, le 14 mars 2014\nAU NOM DU CONSEIL FÉDÉRAL") == "2014-03-14"


def test_no_place_date_line_gives_none():
    assert decision_date_from_text(AARGAU_TEXT) is None
    assert decision_date_from_text("Berne, le 7 novernbie 2018") is None  # OCR-damaged


def test_build_decision_falls_back_to_the_listed_date():
    stub = {"docket_number": "2025-10-08-kanton-aargau", "decision_date": "2025-10-08",
            "detail_url": "https://www.bj.admin.ch/de/entscheid-des-bundesrates-vom-8-oktober-2025-kanton-aargau",
            "title": "x", "decision_type": "Beschwerdeentscheid"}
    d = build_decision(stub, parse_detail(DETAIL), AARGAU_TEXT * 3)
    assert d.decision_id == "ch_bundesrat_2025-10-08-kanton-aargau"
    assert d.decision_date.isoformat() == "2025-10-08"     # not 2025-05-07
    assert d.publication_date.isoformat() == "2025-10-08"
    assert d.legal_area.startswith("Nichteintreten")
    assert d.title.startswith("Entscheid des Bundesrates in der Beschwerdesache A. und B.")


def test_fetch_decision_end_to_end(tmp_path, monkeypatch):
    import scrapers.ch_bundesrat as mod
    s = _scraper(tmp_path)
    s.get = lambda url, **k: _Resp(DETAIL) if url.endswith("kanton-aargau") else _Resp(content=b"%PDF")
    monkeypatch.setattr(mod, "_extract_pdf_text", lambda data: AARGAU_TEXT * 3)
    stub = {"docket_number": "2025-10-08-kanton-aargau", "decision_date": "2025-10-08",
            "detail_url": "https://www.bj.admin.ch/de/entscheid-des-bundesrates-vom-8-oktober-2025-kanton-aargau",
            "title": "x", "decision_type": "Beschwerdeentscheid"}
    d = s.fetch_decision(stub)
    assert d is not None and d.pdf_url.endswith("2025-10-08-beschwerde-kanton-aargau.pdf")


# ── identity across URL schemes ──────────────────────────────────────────

@pytest.mark.parametrize("url", [
    "https://www.bj.admin.ch/dam/bj/de/data/publiservice/publikationen/beschwerdeentscheide/"
    "2025-10-08-beschwerde-kanton-aargau.pdf.download.pdf/2025-10-08-beschwerde-kanton-aargau-d.pdf",
    "https://www.bj.admin.ch/dam/de/sd-web/IG1DrxEbiAI5/2025-10-08-beschwerde-kanton-aargau.pdf",
    "https://www.bj.admin.ch/bj/de/home/publiservice/2025-10-08-beschwerde-kanton-aargau.pdf",
])
def test_pdf_stem_is_the_same_under_every_url_scheme(url):
    assert pdf_stem(url) == "2025-10-08-beschwerde-kanton-aargau"


def test_pdf_stem_empty_for_non_pdf():
    assert pdf_stem("https://www.bj.admin.ch/de/beschwerdeentscheide-des-bundesrates") == ""
    assert pdf_stem(None) == ""


def test_rekey_table_matches_the_listing_fixture():
    """The committed re-key table is the listing the fixture shows."""
    import csv
    table = REPO_TABLE.read_text(encoding="utf-8").splitlines()
    rows = list(csv.DictReader(table, delimiter="\t"))
    assert {r["docket_number"] for r in rows} == {e["docket_number"] for e in parse_listing(LISTING)}
    assert len({r["pdf_stem"] for r in rows}) == 19


REPO_TABLE = Path(__file__).resolve().parents[1] / "runbooks" / "ch_bundesrat_rekey_2026-11.tsv"
