"""C3b — search_botschaft year_min/year_max filter (temporal scoping of legislative history).

The verbatim Botschaft search was topic-only; you couldn't scope "on X from the 1990s".
Year filtering runs on bbl_year (the authoritative BBl-publication year, populated for
the whole corpus), NOT publication_date (sparse, often NULL). These fixtures deliberately
leave publication_date NULL to prove filtering keys off bbl_year.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import mcp_server  # noqa: E402


def _make_db(path: str):
    c = sqlite3.connect(path)
    c.executescript(
        """
        CREATE TABLE botschaft_documents (
            botschaft_id TEXT PRIMARY KEY, bbl_citation TEXT, eli_uri TEXT,
            language TEXT, title TEXT, publication_date TEXT, bbl_year INTEGER
        );
        CREATE TABLE botschaft_paragraphs (
            paragraph_id INTEGER PRIMARY KEY, botschaft_id TEXT, page_number INTEGER,
            section_path TEXT, article_anchor TEXT, text TEXT
        );
        CREATE VIRTUAL TABLE botschaft_paragraphs_fts USING fts5(text);
        """
    )
    # publication_date deliberately NULL: filtering must key off bbl_year.
    c.execute("INSERT INTO botschaft_documents VALUES ('d1990','BBl 1990 I 1','eli1','de',NULL,NULL,1990)")
    c.execute("INSERT INTO botschaft_documents VALUES ('d2020','BBl 2020 II 2','eli2','de',NULL,NULL,2020)")
    for pid, bid, txt in [
        (1, "d1990", "Text ueber Vaterschaftsurlaub aus 1990"),
        (2, "d2020", "Text ueber Vaterschaftsurlaub aus 2020"),
    ]:
        c.execute("INSERT INTO botschaft_paragraphs VALUES (?,?,?,?,?,?)",
                  (pid, bid, 10, "1.1", "Art. 1", txt))
        c.execute("INSERT INTO botschaft_paragraphs_fts (rowid, text) VALUES (?,?)", (pid, txt))
    c.commit()
    c.close()


def _setup(monkeypatch, tmp_path):
    db = tmp_path / "materialien.db"
    _make_db(str(db))
    monkeypatch.setenv("SWISS_CASELAW_MATERIALIEN_DB", str(db))


def test_no_year_filter_returns_all(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    res = mcp_server._handle_search_botschaft(query="Vaterschaftsurlaub")
    assert res["total"] == 2, res
    assert res["year_filter"] is None


def test_year_min_excludes_older(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    res = mcp_server._handle_search_botschaft(query="Vaterschaftsurlaub", year_min=2000)
    assert res["total"] == 1, res
    assert res["results"][0]["bbl_year"] == 2020
    assert res["year_filter"] == {"min": 2000, "max": None}


def test_year_max_excludes_newer(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    res = mcp_server._handle_search_botschaft(query="Vaterschaftsurlaub", year_max=2000)
    assert res["total"] == 1, res
    assert res["results"][0]["bbl_year"] == 1990


def test_year_range(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    res = mcp_server._handle_search_botschaft(query="Vaterschaftsurlaub", year_min=2000, year_max=2025)
    assert res["total"] == 1, res
    assert res["results"][0]["bbl_year"] == 2020
