"""get_commentary resolution + query-hardening tests (GH #23).

The handler filtered with `(sr_number = ? OR UPPER(abbr) = ?)`, so when one
filter was empty it matched rows whose corresponding field was *also* empty —
e.g. get_commentary(sr_number=<anything>) returned an unrelated commentary whose
abbr happened to be empty. After hardening, an empty filter matches nothing.
"""
import sqlite3

import mcp_server


def _make_ok_db(path):
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE commentaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ok_uuid TEXT,
            legislative_act_uuid TEXT, sr_number TEXT, abbr TEXT,
            article_num TEXT, title TEXT, language TEXT, date TEXT,
            authors TEXT, editors TEXT, suggested_citation TEXT, html_link TEXT,
            pdf_link TEXT, content_html TEXT, content_text TEXT, legal_text TEXT)"""
    )
    rows = [
        ("a", "act", "642.14", "StHG", "13", "Art. 13 StHG", "de", "STHG_CONTENT"),
        # orphan: empty sr_number AND abbr — must never be matched by accident
        ("b", "act", "", "", "13", "Art. 13 Orphan", "de", "ORPHAN_CONTENT"),
    ]
    conn.executemany(
        "INSERT INTO commentaries (ok_uuid,legislative_act_uuid,sr_number,abbr,"
        "article_num,title,language,content_text) VALUES (?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()


def test_empty_filter_does_not_match_orphan_row(tmp_path, monkeypatch):
    db = tmp_path / "ok.db"
    _make_ok_db(db)
    monkeypatch.setattr(mcp_server, "OK_COMMENTARIES_DB_PATH", db)
    # A non-existent sr_number must NOT return the empty-abbr orphan row.
    res = mcp_server.get_commentary(sr_number="999.99", article="13")
    assert res.get("content_text") != "ORPHAN_CONTENT"
    # The miss is now the never-empty fallback payload (no commentary, plus
    # whatever the corpus holds on the provision) — never a commentary body.
    assert "content_text" not in res
    assert res.get("no_commentary") is True


def test_get_commentary_by_abbreviation_still_resolves(tmp_path, monkeypatch):
    db = tmp_path / "ok.db"
    _make_ok_db(db)
    monkeypatch.setattr(mcp_server, "OK_COMMENTARIES_DB_PATH", db)
    # Regression guard: a properly-keyed row resolves by abbreviation (the
    # build-side backfill populates abbr so this path works).
    res = mcp_server.get_commentary(abbreviation="StHG", article="13")
    assert res.get("content_text") == "STHG_CONTENT"
