"""The court's appeal note on the decision page and in get_decision.

gerichte-zh.ch publishes "Verweise" with 4,374 decisions ("Weiterzug ans
Bundesgericht, 6B_122/2024"). Until 2026-10-02 the scraper read the field and
threw it away.
"""
from __future__ import annotations

import json
import sqlite3

import seo_pages


def _db(tmp_path, monkeypatch, rows):
    db_path = tmp_path / "decisions.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE decisions (decision_id TEXT PRIMARY KEY, court TEXT, canton TEXT, "
        "chamber TEXT, docket_number TEXT, decision_date TEXT, language TEXT, title TEXT, "
        "regeste TEXT, full_text TEXT, source_url TEXT, pdf_url TEXT, json_data TEXT)"
    )
    conn.executemany("INSERT INTO decisions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()

    def _fake_get_db():
        c = sqlite3.connect(str(db_path))
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setattr(seo_pages, "_get_db", _fake_get_db)


def _row(did, court, docket, appeal=None):
    return (did, court, "ZH" if court.startswith("zh") else "CH", None, docket, "2023-11-01", "de",
            "Pornografie etc.", None, "Erwägungen ...", "https://x", None,
            json.dumps({"appeal_info": appeal}))


def test_note_is_shown_verbatim_and_the_held_decision_is_linked(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch, [
        _row("zh_obergericht_SB230343", "zh_obergericht", "SB230343",
             "Weiterzug ans Bundesgericht, 6B_122/2024"),
        _row("bger_6B_122_2024", "bger", "6B_122/2024"),
    ])
    html, status, _ = seo_pages.render_decision_page("zh_obergericht_SB230343")
    assert status == 200
    assert "Weiterzug / Verweise" in html
    assert 'Weiterzug ans Bundesgericht, <a href="https://mcp.opencaselaw.ch/entscheid/bger_6B_122_2024">6B_122/2024</a>' in html


def test_docket_we_do_not_hold_stays_plain_text(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch, [
        _row("zh_obergericht_SB230343", "zh_obergericht", "SB230343",
             "Weiterzug ans Bundesgericht, 6B_122/2024"),
    ])
    html, _, _ = seo_pages.render_decision_page("zh_obergericht_SB230343")
    assert "Weiterzug ans Bundesgericht, 6B_122/2024" in html
    assert "bger_6B_122_2024" not in html


def test_no_note_no_line(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch, [_row("zh_obergericht_SB230343", "zh_obergericht", "SB230343")])
    html, _, _ = seo_pages.render_decision_page("zh_obergericht_SB230343")
    assert 'class="decision-appeal"' not in html


def test_note_is_escaped(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch, [
        _row("zh_obergericht_SB230343", "zh_obergericht", "SB230343", "<b>x</b> & 6B_1/2020"),
    ])
    html, _, _ = seo_pages.render_decision_page("zh_obergericht_SB230343")
    assert "&lt;b&gt;x&lt;/b&gt; &amp; 6B_1/2020" in html
