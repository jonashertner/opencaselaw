"""BGE ids after the entscheidsuche feed is retired (#40).

A BGE is stored under the direct scraper's id ("bge_140 III 244"; Ia/Ib
upper-case, "bge_116 IA 28") and, while es_bge.jsonl is served, also under
"bge_BGE_140_III_244" / "bge_BGE_116_Ia_28". Every read path must reach the
ruling when only the direct id exists: reference resolution, /entscheid/
links minted with the old form, and search's leading-BGE boost.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import decision_ref  # noqa: E402
import mcp_server  # noqa: E402


def test_references_reach_the_upper_case_ia_id():
    for ref in ("BGE 116 Ia 28", "ATF 116 IA 28", "bge_BGE_116_Ia_28", "bge_BGE_116_IA_28"):
        assert "bge_116 IA 28" in mcp_server._bge_ref_candidates(ref), ref
        assert "bge_116 IA 28" in decision_ref.resolve_decision_ref(ref), ref
    # unlettered divisions: unchanged, still exact on the page
    assert mcp_server._bge_ref_candidates("BGE 140 III 244") == [
        "bge_BGE_140_III_244", "bge_140_III_244", "bge_140 III 244"]
    assert "bge_131 III 121" not in mcp_server._bge_ref_candidates("BGE 131 III 12")


def test_bge_id_candidates_name_the_other_forms_only():
    assert decision_ref.bge_id_candidates("bge_BGE_140_III_244") == [
        "bge_140_III_244", "bge_140 III 244"]
    assert "bge_116 IA 28" in decision_ref.bge_id_candidates("bge_BGE_116_IA_28")
    assert "bge_BGE_116_Ia_28" in decision_ref.bge_id_candidates("bge_116 IA 28")
    for other in ("bger_4A_1_2024", "bge_egmr_20251211_9087_18", "", None):
        assert decision_ref.bge_id_candidates(other) == []


def _fixture_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        "CREATE VIRTUAL TABLE decisions_fts USING fts5("
        "decision_id, court, canton, docket_number, language, title, regeste, full_text);"
        "CREATE TABLE decisions (decision_id TEXT, court TEXT, canton TEXT, chamber TEXT,"
        " docket_number TEXT, decision_date TEXT, language TEXT, title TEXT, regeste TEXT,"
        " full_text TEXT, source_url TEXT, pdf_url TEXT);"
    )
    rows = [
        (1, "bger_x", "bger", "1A_1/2020", "Kündigung Mietvertrag Formular"),
        (2, "bge_140 III 244", "bge", "140 III 244", "amtliches Formular"),
        (3, "bge_116 IA 28", "bge", "116 IA 28", "Stimmrecht"),
    ]
    for rid, did, court, docket, text in rows:
        conn.execute(
            "INSERT INTO decisions_fts (rowid, decision_id, court, canton, docket_number,"
            " language, title, regeste, full_text) VALUES (?,?,?,?,?,?,?,?,?)",
            (rid, did, court, "CH", docket, "de", "", "r", text))
        conn.execute(
            "INSERT INTO decisions (rowid, decision_id, court, canton, chamber, docket_number,"
            " decision_date, language, title, regeste, full_text, source_url, pdf_url)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (rid, did, court, "CH", "", docket, "2014-05-19", "de", "", "r", text,
             "http://x", ""))
    conn.commit()
    return conn


def test_search_boost_finds_leading_bges_stored_under_the_direct_id(monkeypatch):
    conn = _fixture_conn()
    parse = {"leading_bge": ["BGE 140 III 244", "BGE 116 Ia 28"]}
    monkeypatch.setattr(mcp_server, "_analyze_query",
                        lambda q, d, **kw: ([{"query": "Kündigung", "name": "nl_and", "weight": 1.0}],
                                            [], parse, "ok"))
    seen: list[str] = []

    def capture(rows, *a, **k):
        seen.extend(r["decision_id"] for r in rows)
        return []

    monkeypatch.setattr(mcp_server, "_rerank_rows", capture)
    monkeypatch.setattr(mcp_server, "_load_graph_signal_map", lambda *a, **k: {})
    for nm in ("_search_vectors", "_search_vectors_chunks", "_search_sparse", "_search_statute_graph"):
        monkeypatch.setattr(mcp_server, nm, lambda *a, **k: {})
    mcp_server._search_fts5_inner(conn, "Kündigung", None, None, None, None, None, None,
                                  None, None, 5)
    # both leading BGEs entered the candidate pool, though the query matches
    # neither's text; before, the boost looked only for bge_BGE_ ids
    assert "bge_140 III 244" in seen
    assert "bge_116 IA 28" in seen


def test_entscheid_link_with_the_retired_form_redirects(tmp_path, monkeypatch):
    import seo_pages
    db = tmp_path / "d.db"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE decisions (decision_id TEXT PRIMARY KEY, court TEXT, docket_number TEXT)")
    c.executemany("INSERT INTO decisions VALUES (?,?,?)", [
        ("bge_140 III 244", "bge", "140 III 244"), ("bge_116 IA 28", "bge", "116 IA 28")])
    c.commit()
    c.close()

    def conn():
        k = sqlite3.connect(db)
        k.row_factory = sqlite3.Row
        return k

    monkeypatch.setattr(seo_pages, "_get_db", conn)
    assert seo_pages.render_decision_page("bge_BGE_140_III_244")[1:] == (
        301, "/entscheid/bge_140%20III%20244")
    assert seo_pages.render_decision_page("bge_BGE_116_Ia_28")[1:] == (
        301, "/entscheid/bge_116%20IA%2028")
    # a different page of the same volume is not a match
    assert seo_pages.render_decision_page("bge_BGE_140_III_24")[1] == 404
