"""Statute lookups match every language variant of a law code.

The reference graph stores the code as the extractor read it, so one provision
is spread over its languages: "Art. 41 OR" and "art. 41 CO" are two rows of
`statutes`. Live 2026-10-26: analyze_legal_trend(law_code='OR', article='41')
answered 3,734 decisions while the CO rows held 6,837 more (VD, GE, BGer, TI).
These fixtures pin the pairing: OR and CO count together, a decision citing
both counts once, and a code that also names a cantonal act (LPA: the French
TSchG, but Geneva's administrative procedure act) is never expanded.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import mcp_server as m


def test_variants_pair_languages():
    assert m._law_code_variants("OR")[0] == "OR"
    assert "CO" in m._law_code_variants("OR")
    assert "OR" in m._law_code_variants("co")
    assert set(m._law_code_variants("ZGB")) >= {"ZGB", "CC"}
    assert set(m._law_code_variants("BGG")) >= {"BGG", "LTF"}


def test_variants_normalise_stored_form():
    v = m._law_code_variants("Cst.")
    assert v[0] == "CST"
    assert "BV" in v
    assert all("." not in c and c == c.upper() for c in v)


def test_homonyms_and_unknown_codes_stay_alone():
    assert m._law_code_variants("LPA") == ("LPA",)
    assert m._law_code_variants("StG") == ("STG",)
    assert "LPA" not in m._law_code_variants("TSchG")
    assert m._law_code_variants("XYZQ") == ("XYZQ",)
    assert m._law_code_variants("") == ()


def _make_graph(path: Path) -> Path:
    c = sqlite3.connect(path)
    c.executescript(
        """
        CREATE TABLE decisions(decision_id TEXT PRIMARY KEY, court TEXT, decision_date TEXT);
        CREATE TABLE statutes(statute_id INTEGER PRIMARY KEY, law_code TEXT, article TEXT,
                              paragraph TEXT);
        CREATE TABLE decision_statutes(decision_id TEXT, statute_id INTEGER, mention_count INTEGER);
        """
    )
    c.executemany("INSERT INTO statutes VALUES(?,?,?,NULL)",
                  [(1, "OR", "41"), (2, "CO", "41"), (3, "OR", "42")])
    rows = [
        ("bger_4A_1_2024", "bger", "2024-03-01", [1]),      # German
        ("vd_gerichte_X1", "vd_gerichte", "2024-05-01", [2]),  # French
        ("ti_gerichte_X2", "ti_gerichte", "2025-02-01", [2]),  # Italian
        ("bger_4A_2_2025", "bger", "2025-06-01", [1, 2]),   # both: counts once
        ("bger_4A_3_2025", "bger", "2025-07-01", [3]),      # another article
    ]
    for did, court, date, sids in rows:
        c.execute("INSERT INTO decisions VALUES(?,?,?)", (did, court, date))
        for sid in sids:
            c.execute("INSERT INTO decision_statutes VALUES(?,?,1)", (did, sid))
    c.commit()
    c.close()
    return path


@pytest.fixture
def graph(tmp_path, monkeypatch):
    gp = _make_graph(tmp_path / "graph.db")

    def conn():
        cx = sqlite3.connect(gp)
        cx.row_factory = sqlite3.Row
        return cx

    monkeypatch.setattr(m, "_get_graph_conn", conn)
    return gp


def test_trend_counts_or_and_co_once(graph):
    out = m.analyze_legal_trend(law_code="OR", article="41")
    assert out["total"] == 4
    assert {y["year"]: y["count"] for y in out["years"]} == {2024: 2, 2025: 2}
    assert set(out["law_codes_counted"]) >= {"OR", "CO"}
    # asked in French, the same answer
    assert m.analyze_legal_trend(law_code="CO", article="41")["total"] == 4


def test_sql_clause_matches_all_variants(graph):
    code_sql, codes = m._law_code_sql("OR")
    assert code_sql == f"s.law_code IN ({', '.join('?' for _ in codes)})"
    conn = m._get_graph_conn()
    ids = [r["decision_id"] for r in conn.execute(
        f"SELECT DISTINCT ds.decision_id FROM decision_statutes ds "
        f"JOIN statutes s ON s.statute_id = ds.statute_id WHERE {code_sql} AND s.article = ?",
        (*codes, "41"))]
    assert len(ids) == len(set(ids)) == 4
