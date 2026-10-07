"""DFR codes a page from 1000 on with a letter for its hundreds.

The index of BGE 20-29 links "1239" as c1021C39.pdf and "1012" as c1022A12.pdf
(A = 10 ... J = 19). The digit-only code pattern skipped all 230 such links
(20 I, 21 I, 22 I, 23 I, 25 II), so no historical BGE page above 999 was ever
scraped. Four decoded references were checked on the scans
(runbooks/historical_bge_source_errors_2026-10-07.md, "Side finding").
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from scrapers import bge_historical
from scrapers.bge_historical import BGEHistoricalScraper, decode_page

INDEX = """<html><body>
BGE 21 I: <a href="https://www.fallrecht.ch/c1021997.pdf">997</a> ,
<a href="https://www.fallrecht.ch/c1021C39.pdf">1239</a> ,
BGE 22 I: <a href="c1022012.html">12</a> ,
<a href="https://www.fallrecht.ch/c1022A12.pdf">1012</a> ,
<a href="https://www.fallrecht.ch/c1022A12.pdf">1012</a> ,
BGE 23 I: <a href="https://www.fallrecht.ch/c1023J55.pdf">1955</a> ,
BGE 25 II: <a href="https://www.fallrecht.ch/c2025A05.pdf">1005</a> ,
BGE 1 I: <a href="c1001003.html">3</a> ,
BGE 80 I: <a href="https://www.fallrecht.ch/c1080A01.pdf">1001</a> ,
<a href="dfr_bge02.html">Index</a>
</body></html>"""


@pytest.mark.parametrize("code, page", [
    ("003", 3), ("997", 997), ("1012", 1012),
    ("A00", 1000), ("A12", 1012), ("C39", 1239), ("J55", 1955),
])
def test_decode_page(code, page):
    assert decode_page(code) == page


def test_the_code_pattern_takes_letter_pages_and_leaves_other_links():
    m = bge_historical.DECISION_CODE_RE.search("https://www.fallrecht.ch/c1021C39.pdf")
    assert m.groups() == ("1", "021", "C39")
    assert bge_historical.DECISION_CODE_RE.search("c1001003.html").groups() == ("1", "001", "003")
    assert bge_historical.DECISION_CODE_RE.search("dfr_bge02.html") is None
    # K and beyond would mean page 2000+, which no volume reaches
    assert bge_historical.DECISION_CODE_RE.search("c1023K01.pdf") is None


def _scraper(index_html: str, known: set[str] = frozenset()):
    s = BGEHistoricalScraper.__new__(BGEHistoricalScraper)
    s.state = SimpleNamespace(is_known=lambda decision_id: decision_id in known)

    def _get(url, **kw):
        return SimpleNamespace(text=index_html if url == bge_historical.INDEX_URLS[0] else "")

    s.get = _get
    return s


def test_discovery_yields_letter_coded_pages_with_their_real_page():
    stubs = list(_scraper(INDEX).discover_new())
    by_docket = {s["docket_number"]: s for s in stubs}
    assert list(by_docket) == ["21_I_997", "21_I_1239", "22_I_12", "22_I_1012",
                               "23_I_1955", "25_II_1005", "1_I_3"]   # vol 80 dropped, duplicate once
    s = by_docket["21_I_1239"]
    assert s["bge_ref"] == "BGE 21 I 1239" and s["page"] == 1239 and s["volume"] == 21
    assert s["url"] == "https://www.fallrecht.ch/c1021C39.pdf" and s["is_pdf"] is True
    assert by_docket["25_II_1005"]["section"] == "II"
    # DFR's own miscoding: the HTML at the code of page 12 is titled 22 I 1012;
    # the letter-coded PDF is a separate reference and must not be folded into it
    assert by_docket["22_I_1012"]["url"].endswith("c1022A12.pdf")
    assert by_docket["22_I_12"]["url"].endswith("c1022012.html")


def test_known_rows_are_not_fetched_again():
    known = {"bge_historical_21_I_997", "bge_historical_22_I_12", "bge_historical_1_I_3"}
    dockets = [s["docket_number"] for s in _scraper(INDEX, known).discover_new()]
    assert dockets == ["21_I_1239", "22_I_1012", "23_I_1955", "25_II_1005"]
