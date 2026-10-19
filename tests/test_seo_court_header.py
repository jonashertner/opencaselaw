"""Court line of the /entscheid/<id> header.

2026-10-02: a ruling of the Arbeitsgericht at the Bezirksgericht Dielsdorf
(AH260006) was shown as "Zh Arbeitsgericht": the page title-cased the court
code and never printed the chamber, so the deciding court was not on the page.
"""
from __future__ import annotations

import sqlite3

import pytest

import seo_pages


def _db(tmp_path, monkeypatch, rows):
    db_path = tmp_path / "decisions.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE decisions (decision_id TEXT PRIMARY KEY, court TEXT, canton TEXT, "
        "chamber TEXT, docket_number TEXT, decision_date TEXT, language TEXT, title TEXT, "
        "regeste TEXT, full_text TEXT, source_url TEXT, pdf_url TEXT)"
    )
    conn.executemany("INSERT INTO decisions VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()

    def _fake_get_db():
        c = sqlite3.connect(str(db_path))
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setattr(seo_pages, "_get_db", _fake_get_db)


def _row(did, court, chamber):
    return (did, court, "ZH", chamber, "AH260006", "2026-06-09", "de", "Forderung",
            None, "Erwägungen ...", "https://www.gerichte-zh.ch/x", None)


def test_district_court_and_its_division_are_both_on_the_page(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch,
        [_row("zh_bezirksgericht_dielsdorf_AH260006", "zh_bezirksgericht_dielsdorf", "Arbeitsgericht")])
    html, status, _ = seo_pages.render_decision_page("zh_bezirksgericht_dielsdorf_AH260006")
    assert status == 200
    assert "<strong>Bezirksgericht Dielsdorf</strong>" in html
    assert "<span>Arbeitsgericht</span>" in html
    assert "Zh " not in html


def test_no_chamber_no_extra_separator(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch, [_row("zh_arbeitsgericht_AH250118", "zh_arbeitsgericht", "-")])
    html, status, _ = seo_pages.render_decision_page("zh_arbeitsgericht_AH250118")
    assert status == 200
    assert "<strong>Arbeitsgericht Zürich</strong>" in html
    assert "<span>-</span>" not in html


@pytest.mark.parametrize("code,name", [
    ("zh_mietgericht", "Mietgericht Zürich"),
    ("zh_bezirksgericht_buelach", "Bezirksgericht Bülach"),
    ("xx_unbekanntes_gericht", "XX Unbekanntes Gericht"),
])
def test_court_display_name(code, name):
    assert seo_pages._court_display_name(code) == name
