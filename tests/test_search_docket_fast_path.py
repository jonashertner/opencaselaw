"""Search answers a docket or BGE reference from the exact resolvers (2026-10-10).

Measured on production that day: "BGE 147 I 1" and "BGE 140 III 86" timed out
at 60 s, "4A_82/2024" took 24 s and listed 4A_82/2025 beside it, "2C_340/2025"
ranked third. Two gaps in the docket fast path of _search_fts5_inner:
- the gate ran on the sanitised query, where "4A_82/2024" is "4A_82 2024";
- a docket stored with a space ("6B 1070/2018") was not among the variants.
Either way the query fell through to the full-text path (Haiku parse, vectors,
rerank). BGE references are out of scope here: by design (2026-10-02) they
keep the pinned ruling first and the full-text list of citing decisions. Offline: the FTS5 fixture of test_search_bge_tuple_pin.py; the full
path is stubbed and records whether it ran.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import mcp_server as m  # noqa: E402

# (decision_id, court, docket_number, decision_date, full_text)
ROWS = [
    ("bger_4A_82_2024", "bger", "4A_82/2024", "2024-05-02", "Mietrecht Kündigung Anfechtung"),
    ("bger_4A_82_2025", "bger", "4A_82/2025", "2025-03-01", "Arbeitsrecht"),
    # Cites 4A_82/2024 — full text would rank it beside or above the ruling.
    ("bger_4A_500_2024", "bger", "4A_500/2024", "2024-11-01",
     "Vgl. Urteil 4A_82/2024 E. 3; 4A_82/2024 4A_82/2024 Kündigung"),
    ("bge_BGE_147_I_1", "bge", "BGE 147 I 1", "2020-09-10", "Versammlungsfreiheit Covid"),
    ("bge_BGE_143_IV_457", "bge", "BGE 143 IV 457", "2017-10-04", "Vgl. BGE 147 I 1 147 I 1"),
    # Stored with a space, the form of older federal rows.
    ("bger_6B 1070_2018", "bger", "6B 1070/2018", "2019-02-06", "Strafzumessung"),
    # Its neighbours, stored the usual way: the related-docket family finds them.
    ("bger_6B_1069_2018", "bger", "6B_1069/2018", "2019-01-10", "Betrug"),
    ("bger_6B_1071_2018", "bger", "6B_1071/2018", "2019-01-12", "Diebstahl"),
]


def _build(path: Path) -> str:
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE VIRTUAL TABLE decisions_fts USING fts5("
        "decision_id, court, canton, docket_number, language, title, regeste, full_text);"
        "CREATE TABLE decisions (decision_id TEXT PRIMARY KEY, court TEXT, canton TEXT,"
        " chamber TEXT, docket_number TEXT, decision_date TEXT, language TEXT, title TEXT,"
        " regeste TEXT, full_text TEXT, source_url TEXT, pdf_url TEXT);"
    )
    for i, (did, court, docket, date, text) in enumerate(ROWS, start=1):
        conn.execute(
            "INSERT INTO decisions_fts (rowid, decision_id, court, canton, docket_number,"
            " language, title, regeste, full_text) VALUES (?,?,?,'CH',?,'de','',?,?)",
            (i, did, court, docket, text[:20], text))
        conn.execute(
            "INSERT INTO decisions (rowid, decision_id, court, canton, chamber, docket_number,"
            " decision_date, language, title, regeste, full_text, source_url, pdf_url)"
            " VALUES (?,?,?,'CH','',?,?,'de','',?,?,'http://x',NULL)",
            (i, did, court, docket, date, text[:20], text))
    conn.commit()
    conn.close()
    return str(path)


def _rconn(p: str) -> sqlite3.Connection:
    c = sqlite3.connect(p)
    c.row_factory = sqlite3.Row
    return c


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    dbp = _build(tmp_path / "d.db")
    monkeypatch.setattr(m, "get_db", lambda: _rconn(dbp))
    full_path = []

    def analyze(q, d, **kw):
        full_path.append(q)
        return ([{"query": q, "name": "nl_and", "weight": 1.0}], [], {}, "skipped")

    monkeypatch.setattr(m, "_analyze_query", analyze)
    monkeypatch.setattr(m, "_load_graph_signal_map", lambda *a, **k: {})
    monkeypatch.setattr(m, "_search_vectors_chunks", lambda *a, **k: {})
    monkeypatch.setattr(m, "_search_statute_graph", lambda *a, **k: [])
    monkeypatch.setattr(m, "_past_deadline", lambda d: False)
    monkeypatch.setattr(m, "_search_vectors", lambda *a, **k: {})
    monkeypatch.setattr(m, "_search_sparse", lambda *a, **k: {})
    monkeypatch.setattr(m, "_rerank_rows", lambda rows, *a, **k: [dict(r) for r in rows])
    m._FTS_TOTAL_CACHE.clear()
    m._SEARCH_RESULT_CACHE.clear()
    yield full_path
    m._FTS_TOTAL_CACHE.clear()
    m._SEARCH_RESULT_CACHE.clear()


def _ids(rows):
    return [r["decision_id"] for r in rows]


def test_a_slash_docket_is_answered_by_the_fast_path_ruling_first(corpus):
    rows, total = m.search_fts5("4A_82/2024", limit=5)
    assert _ids(rows)[0] == "bger_4A_82_2024"
    assert "bger_4A_500_2024" not in _ids(rows)      # the citing decision is not a match
    assert corpus == []                                # no full-text path


def test_a_bge_reference_keeps_its_design_ruling_first_then_citing(corpus):
    # 2026-10-02 design (test_search_bge_tuple_pin.py): the ruling is pinned
    # first and the full-text list of decisions citing it follows. Unchanged.
    rows, total = m.search_fts5("BGE 147 I 1", limit=5)
    assert _ids(rows)[0] == "bge_BGE_147_I_1"
    assert "bge_BGE_143_IV_457" in _ids(rows)


def test_a_docket_stored_with_a_space_is_found_by_the_exact_lookup(corpus):
    rows, _ = m.search_fts5("6B_1070/2018", limit=5)
    assert _ids(rows)[:1] == ["bger_6B 1070_2018"] and corpus == []


def test_filters_still_apply_on_the_fast_path(corpus):
    rows, total = m.search_fts5("4A_82/2024", language="fr", limit=5)
    assert "bger_4A_82_2024" not in _ids(rows)


def test_a_topic_query_still_takes_the_full_path(corpus):
    m.search_fts5("Kündigung Mietrecht", limit=5)
    assert corpus  # analyzed


def test_an_unknown_docket_falls_through_to_full_text(corpus):
    m.search_fts5("9C_999/2031", limit=5)
    assert corpus


def test_neighbouring_dockets_never_replace_the_ruling_stored_with_a_space(corpus):
    # Production 2026-10-10, first version of this fix: "6B_1070/2018" returned
    # 6B_1069/1071/1068/1073 and not the ruling, because the related-docket
    # family filled the list before the exact lookup was asked.
    rows, _ = m.search_fts5("6B_1070/2018", limit=5)
    assert _ids(rows)[0] == "bger_6B 1070_2018"


def test_a_slash_docket_nobody_holds_falls_back_to_full_text_not_to_neighbours(corpus):
    rows, _ = m.search_fts5("6B_1072/2018", limit=5)
    assert corpus  # the full-text path ran, as before the raw gate
