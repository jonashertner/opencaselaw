"""Tribuna string table: escaped quotes, and the Betreff read from the row record.

Fixture: page 8 of the be_steuerrekurs listing, captured 2026-11-08
(tests/fixtures/tribuna_be_strk_page8_20261108.txt). One Betreff on it reads
'... französischer \\"valeur locative nicht massgebend ...'.

1. The parser tokenized the string table with '"([^"]*)"', which ends a string
   at the escaped quote and flips inside/outside for the rest of the page:
   8 of the page's 20 rows lost their docket and were never listed (the portal
   total 367 against 359 parsed). The tokenizer must honour \\" escapes.

2. The table stores each row's Betreff BEFORE its docket on this portal, so the
   docket-span title reading took the next field instead (a municipality such
   as "4401 Affoltern", or "Steuerrekurskommission"). The row record in the
   integer stream holds decisionDate, dossierNumber, Betreff in that order.
   TITLE_FROM_RECORD (opt-in) takes the title from there.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scrapers.cantonal.base_tribuna import (  # noqa: E402
    TribunaBaseScraper,
    _row_dates_from_stream,
    _row_records_from_stream,
)

FIX = REPO / "tests" / "fixtures"
STRK = (FIX / "tribuna_be_strk_page8_20261108.txt").read_text()
VG = (FIX / "tribuna_be_vg_page0.txt").read_text()
FR = (FIX / "tribuna_fr_page0_base64path_20260914.txt").read_text()


class _Span(TribunaBaseScraper):
    COURT_CODE_STR = "t_span"


class _Record(TribunaBaseScraper):
    COURT_CODE_STR = "t_record"
    TITLE_FROM_RECORD = True


def _rows(cls, text, tmp_path):
    return {r["docket_number"]: r for r in cls(state_dir=tmp_path)._parse_search_response(text)[1]}


def test_escaped_quote_keeps_every_row(tmp_path):
    rows = _rows(_Span, STRK, tmp_path)
    assert len(rows) == 20
    # the eight rows that sat behind the escaped quote
    for d in ("100 2022 20", "100 2021 349", "100 2022 14", "100 2021 404",
              "100 2021 400", "100 2022 72", "100 2018 490", "100 2018 621"):
        assert d in rows, d
        assert rows[d]["enc_path"]
        assert rows[d]["decision_date"]
    assert rows["100 2021 349"]["decision_date"] == "2022-12-13"


def test_record_title_is_the_betreff(tmp_path):
    rows = _rows(_Record, STRK, tmp_path)
    assert rows["100 2022 20"]["title"].startswith("Eigenmietwert einer Liegenschaft in Frankreich")
    assert '"valeur locative' in rows["100 2022 20"]["title"]      # unescaped
    assert rows["100 2021 349"]["title"].startswith("Versuchte Steuerhinterziehung")
    titles = [r["title"] for r in rows.values()]
    assert "Steuerrekurskommission" not in titles
    assert not any(t and t[:4].isdigit() and " " in t and len(t) < 30 for t in titles)  # "4401 Affoltern"


def test_span_title_stays_default(tmp_path):
    # Other portals keep their titles until switched on (FR's Betreff is the
    # chamber line, less useful than the legal area the span reading finds).
    assert TribunaBaseScraper.TITLE_FROM_RECORD is False
    fr_span = _rows(_Span, FR, tmp_path / "a")
    fr_rec = _rows(_Record, FR, tmp_path / "b")
    assert fr_span.keys() == fr_rec.keys()
    assert {r["title"] for r in fr_span.values()} != {r["title"] for r in fr_rec.values()}


def test_record_title_agrees_with_table_order():
    """Where a row's Betreff is new to the table it is entered just before the
    docket (GWT writes the row's fields in reverse); the stream reading must
    agree with that wherever it can be checked."""
    import re
    from scrapers.cantonal.base_tribuna import _RE_GWT_STRING, _gwt_unescape, _is_subject
    for text in (STRK, VG, FR):
        table = [_gwt_unescape(s) for s in _RE_GWT_STRING.findall(text[text.find('["'):])]
        checked = 0
        for docket, (_, title) in _row_records_from_stream(text).items():
            before = table[table.index(docket) - 1]
            if _is_subject(before):
                assert title == before, docket
                checked += 1
        assert checked >= 9


def test_dates_helper_unchanged():
    dates = _row_dates_from_stream(VG)
    assert len(dates) == 20
    assert all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", d) for d in dates.values())


import re  # noqa: E402  (used above)


def test_file_name_betreff_falls_back_to_span():
    from scrapers.cantonal.base_tribuna import _is_subject
    assert not _is_subject("20240604_171528_ANOM.docx")
    assert not _is_subject("Entscheid anonymisieren_ANOM.docx")
    assert _is_subject("Grundstückgewinnsteuer / Verbindlichkeit eines Rulings")
