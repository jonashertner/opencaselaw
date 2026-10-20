"""Regression tests for two citation-resolution fixes (May 2026).

Background
----------
Empirical analysis of the production reference graph (run via
``benchmarks/citation_resolution_analysis.py``) showed that the
deployed exact-match resolver missed ~70 % of "unresolved" mentions
to two engineering bugs rather than to genuine corpus gaps:

1. **Docket-normalization drift** — 2007-2009 Bundesgericht decisions
   were stored with a docket like ``"8C 862/2008"`` whose docket_norm
   came out as ``"8C 862_2008"`` (with a surviving space) while the
   citation extractor produced ``"8C_862_2008"`` (underscore).  The
   exact-match JOIN never fired.

2. **Pin-cite failure** — Swiss legal citations such as
   ``"BGE 125 V 352"`` pinpoint into the body of a case (page 352
   of the case starting at ``"BGE 125 V 351"``), but the resolver
   only matched (vol, div, page) triples that were a case's *first*
   page.

These tests pin both fixes against regression.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from search_stack.build_reference_graph import (  # noqa: E402
    _docket_norm,
    build_graph,
)


# ── Fix 1: docket_norm canonicalization ─────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        # Bug fix: 2007-2009 BGer with space between letters and number
        ("8C 862/2008", "8C_862_2008"),
        ("9C 100/2009", "9C_100_2009"),
        # Modern BGer (unchanged behavior)
        ("5A_438/2012", "5A_438_2012"),
        ("4A_215/2017", "4A_215_2017"),
        # 1999 BGer (unchanged)
        ("4P.253/1999", "4P_253_1999"),
        # BGE prefix-form preserved canonical (with space)
        ("BGE 121 I 102", "BGE 121 I 102"),
        ("ATF 121 I 102", "ATF 121 I 102"),
        ("DTF 121 I 102", "DTF 121 I 102"),
        # BGE underscore variants normalized to space form
        ("BGE_140_III_86", "BGE 140 III 86"),
        # Bare BGE (no prefix) — historical form with hyphens
        ("79-IV-170", "79 IV 170"),
        ("151 III 481", "151 III 481"),
        # BStGer / cantonal codes pass through cleanly
        ("SK_2024_99", "SK_2024_99"),
        # Defensive: empty / None / whitespace
        ("", ""),
        ("  ", ""),
        (None, ""),
    ],
)
def test_docket_norm_canonical(raw, expected):
    assert _docket_norm(raw) == expected


# ── Fix 2: end-to-end pin-cite resolution ───────────────────────────


def _make_decisions_db(tmp_path: Path, rows: list[dict]) -> Path:
    """Stub decisions.db with the columns the graph builder consumes."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    db_path = tmp_path / "decisions.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE decisions (
            decision_id TEXT PRIMARY KEY,
            docket_number TEXT,
            court TEXT,
            canton TEXT,
            language TEXT,
            decision_date TEXT,
            title TEXT,
            regeste TEXT,
            full_text TEXT
        )
        """
    )
    for r in rows:
        conn.execute(
            "INSERT INTO decisions(decision_id, docket_number, court, canton,"
            " language, decision_date, title, regeste, full_text)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (
                r["decision_id"],
                r.get("docket_number"),
                r.get("court"),
                r.get("canton"),
                r.get("language"),
                r.get("decision_date"),
                r.get("title", ""),
                r.get("regeste", ""),
                r.get("full_text", ""),
            ),
        )
    conn.commit()
    conn.close()
    return db_path


def test_pincite_resolves_to_nearest_preceding_first_page(tmp_path):
    """A citation to a non-first-page lands on the case starting before it."""
    rows = [
        {
            "decision_id": "BGE_125_V_351",
            "docket_number": "BGE 125 V 351",
            "court": "bge",
            "canton": "CH",
            "language": "de",
            "decision_date": "1999-09-01",
            "full_text": "Foundational case on party-medical reports.",
        },
        {
            "decision_id": "BGE_125_V_400",
            "docket_number": "BGE 125 V 400",
            "court": "bge",
            "canton": "CH",
            "language": "de",
            "decision_date": "1999-10-01",
            "full_text": "Unrelated later case in the same volume.",
        },
        {
            "decision_id": "BGER_4A_1_2024",
            "docket_number": "4A_1/2024",
            "court": "bger",
            "canton": "CH",
            "language": "de",
            "decision_date": "2024-06-01",
            # Cites BGE 125 V 351 with a pin-cite into page 352.
            "full_text": "Vgl. BGE 125 V 352 (m.w.H. zur Beweiswuerdigung).",
        },
    ]
    src_db = _make_decisions_db(tmp_path, rows)
    graph_db = tmp_path / "graph.db"
    build_graph(input_dir=tmp_path, db_path=graph_db, source_db=src_db)

    conn = sqlite3.connect(graph_db)
    try:
        # The pin-cite "BGE 125 V 352" must resolve to the case starting at 351.
        target = conn.execute(
            "SELECT target_decision_id, match_type FROM citation_targets "
            "WHERE source_decision_id = ? AND target_ref = ?",
            ("BGER_4A_1_2024", "BGE 125 V 352"),
        ).fetchone()
        assert target is not None, "pin-cite should resolve"
        assert target[0] == "BGE_125_V_351", f"wrong target: {target}"
        assert target[1] == "bge_pincite"
    finally:
        conn.close()


def test_pincite_does_not_overshoot_distance_limit(tmp_path):
    """Pin-cite must not jump from page 1 to page 999."""
    rows = [
        {
            "decision_id": "BGE_140_III_1",
            "docket_number": "BGE 140 III 1",
            "court": "bge",
            "canton": "CH",
            "language": "de",
            "decision_date": "2014-01-01",
            "full_text": ".",
        },
        {
            "decision_id": "BGER_4A_2_2024",
            "docket_number": "4A_2/2024",
            "court": "bger",
            "canton": "CH",
            "language": "de",
            "decision_date": "2024-06-01",
            # Cite "BGE 140 III 999" — too far from page 1, should stay unresolved.
            "full_text": "Siehe BGE 140 III 999.",
        },
    ]
    src_db = _make_decisions_db(tmp_path, rows)
    graph_db = tmp_path / "graph.db"
    build_graph(input_dir=tmp_path, db_path=graph_db, source_db=src_db)

    conn = sqlite3.connect(graph_db)
    try:
        # 999 is >> 30 pages from 1, so no pincite resolution.
        target = conn.execute(
            "SELECT * FROM citation_targets WHERE source_decision_id = ? "
            "AND target_ref = ?",
            ("BGER_4A_2_2024", "BGE 140 III 999"),
        ).fetchone()
        assert target is None, "pin-cite distance bound was not enforced"
    finally:
        conn.close()


def test_docketnorm_fix_resolves_2008_bger(tmp_path):
    """Citation '8C_862_2008' resolves the case stored as '8C 862/2008' (space form)."""
    rows = [
        {
            "decision_id": "BGER_8C_862_2008",
            # Note the SPACE between letters and number — the bug pattern.
            "docket_number": "8C 862/2008",
            "court": "bger",
            "canton": "CH",
            "language": "de",
            "decision_date": "2008-12-15",
            "full_text": ".",
        },
        {
            "decision_id": "BGER_4A_3_2024",
            "docket_number": "4A_3/2024",
            "court": "bger",
            "canton": "CH",
            "language": "de",
            "decision_date": "2024-06-01",
            # Citation extractor emits "8C_862_2008" (underscore).
            "full_text": "Wie in 8C_862/2008 dargelegt.",
        },
    ]
    src_db = _make_decisions_db(tmp_path, rows)
    graph_db = tmp_path / "graph.db"
    build_graph(input_dir=tmp_path, db_path=graph_db, source_db=src_db)

    conn = sqlite3.connect(graph_db)
    try:
        target = conn.execute(
            "SELECT target_decision_id FROM citation_targets "
            "WHERE source_decision_id = ?",
            ("BGER_4A_3_2024",),
        ).fetchone()
        assert target is not None, (
            "docket-norm space/underscore drift was not fixed"
        )
        assert target[0] == "BGER_8C_862_2008"
    finally:
        conn.close()


def test_a_bge_s_own_page_head_reference_is_not_a_citation_of_its_neighbour(tmp_path):
    """Report 2026-10-20: every BGE prints its own reference in its page
    heads ("BGE 139 III 120 S. 121"). The pin-cite pass skipped the source
    and resolved that reference to the nearest *other* first page, so BGE
    139 III 120 "cited" BGE 139 III 110 (17 of 18 sampled BGE). A reference
    into the source's own pages, its first page or a later one, is no edge."""
    rows = [
        {"decision_id": "BGE_139_III_110", "docket_number": "BGE 139 III 110", "court": "bge",
         "canton": "CH", "language": "de", "decision_date": "2013-02-05",
         "full_text": "Zuständigkeit für Patentverletzungsklagen gegen den Bund."},
        {"decision_id": "BGE_139_III_120", "docket_number": "BGE 139 III 120", "court": "bge",
         "canton": "CH", "language": "de", "decision_date": "2013-02-26",
         "full_text": "BGE 139 III 120 S. 121\nAus den Erwägungen (vgl. BGE 139 III 112).\n"
                      "BGE 139 III 120 S. 122\nund weiter auf BGE 139 III 123."},
    ]
    src_db = _make_decisions_db(tmp_path, rows)
    graph_db = tmp_path / "graph.db"
    build_graph(input_dir=tmp_path, db_path=graph_db, source_db=src_db)
    conn = sqlite3.connect(graph_db)
    try:
        edges = dict(conn.execute(
            "SELECT target_ref, target_decision_id FROM citation_targets "
            "WHERE source_decision_id = ?", ("BGE_139_III_120",)).fetchall())
    finally:
        conn.close()
    assert "BGE 139 III 120" not in edges
    assert "BGE 139 III 123" not in edges          # a page of its own
    assert edges.get("BGE 139 III 112") == "BGE_139_III_110"  # a real pin-cite still resolves
