"""TI (sentenze.ti.ch): decision dates and authority are read from the result table
and from the document page (2026-09-14 regression).

Golden excerpts fetched 2026-09-15 (invariant #8: no network). Before the fix every
new TI row arrived without decision_date: the listing parser looked at the title link's
own <tr> (metadata is in the sibling rows of the per-result table) and the document
parser searched the label cell "Data decisione, Autorità:" for a date that sits in the
next cell.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import scrapers.cantonal.ti_gerichte as ti_mod  # noqa: E402
from scrapers.cantonal.ti_gerichte import RE_DATA_DEC, TIGerichteScraper  # noqa: E402

FIX = REPO / "tests" / "fixtures"
RESULTS = (FIX / "ti_gerichte_results_2026_05_excerpt.html").read_text(encoding="utf-8")
DOC_ROW = (FIX / "ti_gerichte_doc_daterow_2026.html").read_text(encoding="utf-8")


def test_result_page_yields_dated_stubs(tmp_path):
    s = TIGerichteScraper(state_dir=tmp_path)
    stubs = list(s._parse_result_page(RESULTS))
    assert len(stubs) == 3
    by = {st["docket_number"]: st for st in stubs}
    assert "35.2025.89" in by
    assert by["35.2025.89"]["decision_date"] == date(2026, 5, 26)
    assert by["35.2025.89"]["publication_date"] == date(2026, 9, 14)
    assert by["35.2025.89"]["autorita"] == "TCA"
    assert all(st["decision_date"] and st["decision_date"].year == 2026 for st in stubs)
    assert all(st["autorita"] for st in stubs)


def test_regex_accepts_listing_and_document_forms():
    assert RE_DATA_DEC.search("Autorità: TCA, data decisione: 26.05.2026, data pubblicazione: 14.09.2026").group(1) == "26.05.2026"
    assert RE_DATA_DEC.search("Data decisione, Autorità: 26.05.2026, TCA").group(1) == "26.05.2026"


def test_document_page_date_in_next_cell(monkeypatch, tmp_path):
    s = TIGerichteScraper(state_dir=tmp_path)
    html = DOC_ROW.replace("</body>", "<!-- " + "x" * 600 + " --></body>")   # past the 500-char guard

    class R:
        text = html

    monkeypatch.setattr(s, "get", lambda url, **k: R())
    # the fixture is the date row only; the body text is not under test here
    monkeypatch.setattr(ti_mod, "_extract_document_text", lambda soup: "Sentenza " * 40)
    d = s.fetch_decision({"docket_number": "35.2025.89", "url": "https://www.sentenze.ti.ch/doc",
                          "decision_date": None, "autorita": None, "title": "t"})
    assert d is not None
    assert d.decision_date == date(2026, 5, 26)
    assert d.chamber == "TCA"
