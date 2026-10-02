"""Site search missed every BGE of volumes 1–79 (2026-09-22, ATF 73 II 6).

Early volumes are stored as 'bge_73_II_6' with docket '73_II_6'; later ones
as 'bge_BGE_140_III_115' with 'BGE 140 III 115'; 12,367 rulings exist under a
prefixed AND a spaced id ('bge_112 IV 74'), 3,168 of the spaced ids with an
upper-cased division ('bge_100 IA 106'). The docket fast path only
re-separates the query with / _ . - so it never produced the early shapes,
and the reference fell through to full text, which ranked the decisions
CITING the ruling above the ruling itself. /api/lookup?exact=1 already went
through _bge_ref_candidates and was right; the default path was not.

Fix: search resolves a BGE/ATF/DTF or bare 'vol division page' reference by
exact tuple through the primary key (_search_bge_tuple) and pins that ruling
first, in the fast path and in the full-text merge alike; the shared dedupe
shows a ruling stored under two ids once. Offline: FTS5 fixture with every
stored id shape; the LLM/vector/graph stages are stubbed as in
test_search_total_exact.py.
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

# (decision_id, docket_number, decision_date, full_text)
ROWS = [
    ("bge_73_II_6", "73_II_6", "1947-01-01", "Tierhalterhaftung Art. 56 OR"),
    ("bge_BGE_140_III_115", "BGE 140 III 115", "2014-01-17", "Werkvertrag Mängelrüge"),
    ("bge_BGE_131_III_12", "BGE 131 III 12", "2004-09-14", "Prädisposition Art. 42 OR"),
    ("bge_BGE_131_III_121", "BGE 131 III 121", "2004-12-10", "Markenrecht"),
    # A later ruling that CITES 73 II 6 — full text beats the early docket in FTS.
    ("bge_BGE_115_V_368", "BGE 115 V 368", "1989-11-02",
     "Vgl. BGE 73 II 6 (ATF 73 II 6, DTF 73 II 6) E. 2; Tierhalterhaftung 73 II 6"),
    # One ruling under two ids: prefixed (fuller text) and spaced.
    ("bge_BGE_112_IV_74", "BGE 112 IV 74", "1986-12-03", "Notwehr Putativnotwehr BGE 112 IV 74"),
    ("bge_112 IV 74", "112 IV 74", "1986-12-03", "Notwehr Putativnotwehr 112 IV 74"),
    # Spaced id with upper-cased division suffix.
    ("bge_100 IA 106", "100 IA 106", "1974-03-20", "Gemeindeautonomie"),
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
    for i, (did, docket, date, text) in enumerate(ROWS, start=1):
        conn.execute(
            "INSERT INTO decisions_fts (rowid, decision_id, court, canton, docket_number,"
            " language, title, regeste, full_text) VALUES (?,?,'bge','CH',?,'de','',?,?)",
            (i, did, docket, text[:20], text))
        conn.execute(
            "INSERT INTO decisions (rowid, decision_id, court, canton, chamber, docket_number,"
            " decision_date, language, title, regeste, full_text, source_url, pdf_url)"
            " VALUES (?,?,'bge','CH','',?,?,'de','',?,?,'http://x',NULL)",
            (i, did, docket, date, text[:20], text))
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
    # Offline: no Haiku, vectors, sparse or graph; the FTS strategy is the
    # query itself; rerank is the identity (BM25 order).
    monkeypatch.setattr(
        m, "_analyze_query",
        lambda q, d, **kw: ([{"query": q, "name": "nl_and", "weight": 1.0}], [], {}, "skipped"))
    monkeypatch.setattr(m, "_load_graph_signal_map", lambda *a, **k: {})
    monkeypatch.setattr(m, "_search_vectors_chunks", lambda *a, **k: {})
    monkeypatch.setattr(m, "_search_statute_graph", lambda *a, **k: [])
    monkeypatch.setattr(m, "_past_deadline", lambda d: False)
    monkeypatch.setattr(m, "_search_vectors", lambda *a, **k: {})
    monkeypatch.setattr(m, "_search_sparse", lambda *a, **k: {})
    monkeypatch.setattr(m, "_rerank_rows", lambda rows, *a, **k: [dict(r) for r in rows])
    m._FTS_TOTAL_CACHE.clear()
    yield dbp
    m._FTS_TOTAL_CACHE.clear()


def _ids(rows):
    return [r["decision_id"] for r in rows]


# ── the resolver itself ───────────────────────────────────────────────────────

def test_candidates_include_the_upper_cased_spaced_shape():
    assert m._bge_ref_candidates("BGE 100 Ia 106") == [
        "bge_BGE_100_Ia_106", "bge_100_Ia_106", "bge_100 Ia 106", "bge_100 IA 106"]
    # No suffix -> the three shapes as before (pinned by test_decision_resolution_c1).
    assert len(m._bge_ref_candidates("BGE 73 II 6")) == 3


def test_tuple_key_is_shared_by_every_spelling_and_absent_elsewhere():
    assert m._bge_tuple_key("bge_112 IV 74") == "bge_BGE_112_IV_74"
    assert m._bge_tuple_key("bge_BGE_112_IV_74") == "bge_BGE_112_IV_74"
    assert m._bge_tuple_key("bge_100 IA 106") == "bge_BGE_100_Ia_106"
    assert m._bge_tuple_key("bger_4A_231_2014") is None
    assert m._bge_tuple_key("") is None


@pytest.mark.parametrize("ref", ["ATF 73 II 6", "BGE 73 II 6", "DTF 73 II 6", "73 II 6",
                                 "atf 73 II 6", "BGE 73 II 6 E. 2"])
def test_every_form_is_a_case_number_query(ref):
    assert m._looks_like_docket_query(ref) is True


def test_search_bge_tuple_resolves_the_early_shape_and_honors_filters(corpus):
    conn = _rconn(corpus)
    hit = m._search_bge_tuple(conn, "ATF 73 II 6", "", [])
    assert _ids(hit) == ["bge_73_II_6"]
    assert hit[0]["docket_number"] == "73_II_6"
    # A filter the ruling fails -> nothing pinned, never a different ruling.
    assert m._search_bge_tuple(conn, "ATF 73 II 6", " AND d.language = ?", ["fr"]) == []
    assert m._search_bge_tuple(conn, "Tierhalterhaftung", "", []) == []


def test_search_bge_tuple_prefers_the_prefixed_id_of_a_dual_ruling(corpus):
    conn = _rconn(corpus)
    assert _ids(m._search_bge_tuple(conn, "BGE 112 IV 74", "", [])) == ["bge_BGE_112_IV_74"]
    assert _ids(m._search_bge_tuple(conn, "BGE 100 Ia 106", "", [])) == ["bge_100 IA 106"]


# ── search_fts5: the ruling is first, the rest of the list is unchanged ──────

@pytest.mark.parametrize("ref", ["ATF 73 II 6", "BGE 73 II 6", "DTF 73 II 6", "73 II 6"])
def test_early_volume_ruling_is_first_not_the_decisions_citing_it(corpus, ref):
    rows, total = m.search_fts5(query=ref, limit=8)
    assert _ids(rows)[0] == "bge_73_II_6", _ids(rows)
    # The citing ruling still follows — the full-text list is kept, not replaced.
    assert "bge_BGE_115_V_368" in _ids(rows)
    assert total >= 2


def test_later_volume_ruling_unchanged(corpus):
    rows, _ = m.search_fts5(query="BGE 140 III 115", limit=8)
    assert _ids(rows)[0] == "bge_BGE_140_III_115"


def test_c1_guard_short_tuple_never_pins_the_longer_number(corpus):
    rows, _ = m.search_fts5(query="131 III 12", limit=8)
    assert _ids(rows)[0] == "bge_BGE_131_III_12"
    rows, _ = m.search_fts5(query="BGE 131 III 121", limit=8)
    assert _ids(rows)[0] == "bge_BGE_131_III_121"


def test_dual_id_ruling_appears_once(corpus):
    rows, _ = m.search_fts5(query="BGE 112 IV 74", limit=8)
    ids = _ids(rows)
    assert ids[0] == "bge_BGE_112_IV_74"
    assert "bge_112 IV 74" not in ids, ids


def test_dedupe_collapses_the_two_spellings_first_wins():
    rows = [{"decision_id": "bge_112 IV 74", "court": "bge", "docket_number": "112 IV 74",
             "decision_date": "1986-12-03"},
            {"decision_id": "bge_BGE_112_IV_74", "court": "bge", "docket_number": "BGE 112 IV 74",
             "decision_date": "1986-12-03"},
            {"decision_id": "bger_4A_1_2020", "court": "bger", "docket_number": "4A_1/2020",
             "decision_date": "2020-01-01"}]
    assert _ids(m._dedupe_results_by_decision_id(rows)) == ["bge_112 IV 74", "bger_4A_1_2020"]


def test_language_filter_that_excludes_the_ruling_does_not_pin_it(corpus):
    rows, _ = m.search_fts5(query="BGE 73 II 6", limit=8, language="fr")
    assert "bge_73_II_6" not in _ids(rows)


# ── /api/lookup default path (what the site search box calls) ────────────────

@pytest.mark.parametrize("ref", ["ATF 73 II 6", "BGE 73 II 6", "DTF 73 II 6", "73 II 6"])
def test_lookup_default_path_returns_the_ruling_first(corpus, ref):
    out = m._lookup_case_number(ref, limit=5)
    assert out["is_case_number"] is True
    assert out["results"][0]["decision_id"] == "bge_73_II_6", out
    assert out["results"][0]["citation"] == "BGE 73 II 6"   # R1: from the pipeline
    assert out["results"][0]["url"].endswith("/entscheid/bge_73_II_6")


def test_lookup_default_and_exact_agree(corpus):
    default = m._lookup_case_number("ATF 73 II 6", limit=5)
    exact = m._lookup_case_number("ATF 73 II 6", limit=5, exact=True)
    assert default["results"][0]["decision_id"] == exact["results"][0]["decision_id"]
