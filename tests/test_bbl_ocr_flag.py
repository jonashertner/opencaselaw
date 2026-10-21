"""The 1850-1999 Bundesblatt text (Federal Archives scans) is OCR: every tool
that returns it says so (text_source / verbatim_quotation 'ocr', R2), and two
documents on the same page of different volumes stay two documents."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import mcp_server  # noqa: E402
from search_stack import build_botschaft_corpus as bbc  # noqa: E402

FGA = "https://fedlex.data.admin.ch/eli/fga/"


@pytest.fixture
def mdb(tmp_path, monkeypatch):
    db = tmp_path / "materialien.db"
    c = sqlite3.connect(db)
    bbc.ensure_schema(c)
    rows = [  # id, year, vol, page, citation, eli, collection, title
        (1, 1975, 1, 609, "BBl 1975 I 609", FGA + "1975/1_609__", "bar-amtsdruckschriften", "Botschaft über die Heizöltanks"),
        (2, 1975, 2, 609, "BBl 1975 II 609", FGA + "1975/2_609__", "bar-amtsdruckschriften", "Botschaft über die Rheinschifffahrt"),
        (3, 2011, None, 7705, "BBl 2011 7705", FGA + "2011/1325", None, "Botschaft VVG"),
    ]
    for bid, y, v, pg, cite, eli, coll, title in rows:
        c.execute("INSERT INTO botschaft_documents (botschaft_id, bbl_year, bbl_volume, bbl_page, bbl_citation, "
                  "eli_uri, source_url, format, language, title, ingested_at, source_collection) "
                  "VALUES (?,?,?,?,?,?,'u','pdf','de',?,'now',?)", (bid, y, v, pg, cite, eli, title, coll))
        c.execute("INSERT INTO botschaft_paragraphs (botschaft_id, para_order, page_number, article_anchor, text, text_length) "
                  "VALUES (?, 1, 1, '5', ?, 60)", (bid, f"Zu Art. 5 Heizöltank Erläuterung des Dokuments {bid}"))
        c.execute("INSERT INTO article_botschaft_links VALUES ('210', '5', ?, 'considered', 'x')", (bid,))
    c.commit(); c.close()
    monkeypatch.setenv("SWISS_CASELAW_MATERIALIEN_DB", str(db))
    return db


def test_search_botschaft_flags_historical_passages(mdb):
    out = mcp_server._handle_search_botschaft(query="Heizöltank")
    by = {r["bbl_citation"]: r for r in out["results"]}
    assert by["BBl 1975 I 609"]["text_source"] == "ocr"
    assert by["BBl 1975 I 609"]["verbatim_quotation"] == "ocr" and "OCR" in by["BBl 1975 I 609"]["ocr_note"]
    assert "text_source" not in by["BBl 2011 7705"]


def test_linked_documents_keep_two_volumes_apart_and_flag_them(mdb):
    conn = sqlite3.connect(f"file:{mdb}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        docs = mcp_server._linked_botschaft_documents(conn, "210", "5")
    finally:
        conn.close()
    cites = sorted(d["bbl_citation"] for d in docs)
    assert cites == ["BBl 1975 I 609", "BBl 1975 II 609", "BBl 2011 7705"]
    flags = {d["bbl_citation"]: d.get("text_source") for d in docs}
    assert flags == {"BBl 1975 I 609": "ocr", "BBl 1975 II 609": "ocr", "BBl 2011 7705": None}


def test_article_purpose_flags_historical_sources(mdb, monkeypatch):
    out = mcp_server._handle_get_article_purpose(sr_number="210", article="5", language="de")
    by = {s["bbl_citation"]: s for s in out["sources"]}
    assert by["BBl 1975 I 609"]["verbatim_quotation"] == "ocr"
    assert "verbatim_quotation" not in by["BBl 2011 7705"]


def test_r2_instruction_names_the_historical_ocr():
    text = mcp_server.server.instructions
    assert "1850-1999 Bundesblatt passage" in text and "text_source: ocr" in text
