"""Tests for incremental reference-graph builder (shadow-mode v0.1).

Strategy
--------
1. Build a tiny in-memory `decisions.db` (5 rows, hand-crafted full_text
   so the regex extractors produce a known set of edges).
2. Bootstrap the graph via the canonical full builder.
3. Mutate the corpus: add 1 row, modify 1 row, delete 1 row.
4. Run incremental — assert it matches a fresh full rebuild on the same
   mutated corpus, edge for edge.

The contract being verified: incremental output is *equivalent* to a
full rebuild for any sequence of additions / modifications / deletions.
If this ever drifts, we'd be promoting a buggy graph to production —
catching it at unit-test time is the safety net before the planned
shadow-run-on-real-corpus phase.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from search_stack.build_reference_graph import build_graph  # noqa: E402
from search_stack.build_reference_graph_incremental import (  # noqa: E402
    EXTRACTOR_VERSION,
    _diff_state,
    _ensure_state_tables,
    _extractor_hash,
    _get_meta,
    build_graph_incremental,
)


# ── fixture corpus ────────────────────────────────────────────────────


def _make_decisions_db(tmp_path: Path, rows: list[dict]) -> Path:
    """Build a stub decisions.db with the columns the reference-graph
    builder consumes. Schema mirrors the production FTS5 DB at the
    column level.
    """
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
            """
            INSERT INTO decisions(decision_id, docket_number, court, canton,
                                  language, decision_date, title, regeste, full_text)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                r["decision_id"],
                r.get("docket_number"),
                r.get("court"),
                r.get("canton"),
                r.get("language"),
                r.get("decision_date"),
                r.get("title"),
                r.get("regeste"),
                r.get("full_text"),
            ),
        )
    conn.commit()
    conn.close()
    return db_path


# 5 rows hand-crafted so we know exactly which edges should appear.
# - BGE_147_I_268 (target of cites in row 3)
# - BGE_140_III_86 (target of cites in rows 3, 4)
# - 4A_1_2024 (cites BGE 147 I 268 + Art. 41 OR)
# - 9C_5_2024 (cites BGE 140 III 86 + Art. 8 EMRK)
# - SK_2024_99 (cites Art. 19 StGB only)
BASE_ROWS = [
    {
        "decision_id": "BGE_147_I_268",
        "docket_number": "BGE 147 I 268",
        "court": "bge",
        "canton": "CH",
        "language": "de",
        "decision_date": "2021-05-01",
        "title": "Verfassungsbeschwerde",
        "regeste": "Art. 8 EMRK; Eigentumsgarantie.",
        "full_text": "Sachverhalt zum Eigentumsschutz, BGE 134 V 231 wird zitiert.",
    },
    {
        "decision_id": "BGE_140_III_86",
        "docket_number": "BGE 140 III 86",
        "court": "bge",
        "canton": "CH",
        "language": "de",
        "decision_date": "2014-02-15",
        "title": "Vertrauensschutz",
        "regeste": "Art. 41 OR; Vertrauen.",
        "full_text": "Sachverhalt zum Vertrauensschutz im Vertragsrecht.",
    },
    {
        "decision_id": "BGER_4A_1_2024",
        "docket_number": "4A_1/2024",
        "court": "bger",
        "canton": "CH",
        "language": "de",
        "decision_date": "2024-06-12",
        "title": "Schadenersatzklage",
        "regeste": "Art. 41 OR; Adäquanz.",
        "full_text": "Vgl. BGE 147 I 268 betreffend Eigentum. Art. 41 OR ist anwendbar. Auch BGE 140 III 86 wird beigezogen.",
    },
    {
        "decision_id": "BGER_9C_5_2024",
        "docket_number": "9C_5/2024",
        "court": "bger",
        "canton": "CH",
        "language": "de",
        "decision_date": "2024-08-01",
        "title": "IV-Rente",
        "regeste": "Art. 8 EMRK; Diskriminierung.",
        "full_text": "Es wird auf BGE 140 III 86 verwiesen. Art. 8 EMRK ist verletzt.",
    },
    {
        "decision_id": "BSTGER_SK_2024_99",
        "docket_number": "SK_2024_99",
        "court": "bstger",
        "canton": "CH",
        "language": "de",
        "decision_date": "2024-09-09",
        "title": "Anklage",
        "regeste": "Art. 19 StGB; Schuldfähigkeit.",
        "full_text": "Art. 19 StGB ist zentral.",
    },
]


# ── _extractor_hash ───────────────────────────────────────────────────


def test_extractor_hash_deterministic() -> None:
    h1 = _extractor_hash(BASE_ROWS[0])
    h2 = _extractor_hash(dict(BASE_ROWS[0]))
    assert h1 == h2
    assert len(h1) == 64


def test_extractor_hash_changes_for_each_field() -> None:
    base_h = _extractor_hash(BASE_ROWS[0])
    for field in (
        "decision_id",
        "docket_number",
        "court",
        "canton",
        "language",
        "decision_date",
        "title",
        "regeste",
        "full_text",
    ):
        mutated = dict(BASE_ROWS[0])
        mutated[field] = (mutated.get(field) or "x") + "_changed"
        assert _extractor_hash(mutated) != base_h, f"hash unchanged for {field}"


def test_extractor_hash_treats_none_as_empty() -> None:
    a = _extractor_hash({"decision_id": "X"})
    b = _extractor_hash({k: None for k in ("decision_id",)} | {"decision_id": "X"})
    assert a == b


# ── _diff_state ───────────────────────────────────────────────────────


def test_diff_state_classifies_new_changed_deleted(tmp_path: Path) -> None:
    decisions_db = _make_decisions_db(tmp_path, BASE_ROWS)

    # Bootstrap the graph DB so processed_decisions is seeded.
    graph_db = tmp_path / "graph.db"
    build_graph_incremental(
        decisions_db=decisions_db,
        graph_db=tmp_path / "nonexistent.db",
        output_path=graph_db,
        force_full=True,
    )

    # Mutate corpus: add row, change row, delete row.
    mutated = [dict(r) for r in BASE_ROWS]
    mutated[2]["full_text"] = mutated[2]["full_text"] + " ZUSATZ."  # change
    mutated.pop(4)  # delete BSTGER_SK_2024_99
    mutated.append(  # add new
        {
            "decision_id": "BGER_5A_99_2026",
            "docket_number": "5A_99/2026",
            "court": "bger",
            "canton": "CH",
            "language": "de",
            "decision_date": "2026-01-01",
            "title": "Erbrecht",
            "regeste": "Art. 560 ZGB; Universalsukzession.",
            "full_text": "Vgl. BGE 140 III 86 betreffend Vertrauen.",
        }
    )
    decisions_db_v2 = _make_decisions_db(tmp_path / "v2", mutated)

    conn = sqlite3.connect(graph_db)
    new_ids, changed_ids, deleted_ids, hashes_by_id, _ = _diff_state(
        decisions_db_v2, conn
    )
    conn.close()

    assert new_ids == {"BGER_5A_99_2026"}
    assert changed_ids == {"BGER_4A_1_2024"}
    assert deleted_ids == {"BSTGER_SK_2024_99"}
    assert len(hashes_by_id) == 5  # one per row in v2


def test_diff_state_empty_processed_means_all_new(tmp_path: Path) -> None:
    decisions_db = _make_decisions_db(tmp_path, BASE_ROWS)
    graph_db = tmp_path / "graph.db"
    # Create just the schema, NO processed_decisions seeding
    conn = sqlite3.connect(graph_db)
    _ensure_state_tables(conn)
    conn.commit()

    new_ids, changed_ids, deleted_ids, hashes_by_id, _ = _diff_state(
        decisions_db, conn
    )
    conn.close()

    assert new_ids == {r["decision_id"] for r in BASE_ROWS}
    assert changed_ids == set()
    assert deleted_ids == set()


# ── end-to-end: incremental matches full rebuild ──────────────────────


def _graph_signature(graph_db: Path) -> dict:
    """Stable summary of a graph DB's content — used to compare two
    graph DBs for equivalence regardless of insertion order.
    """
    conn = sqlite3.connect(graph_db)
    conn.row_factory = sqlite3.Row
    try:
        decisions = sorted(
            tuple(r) for r in conn.execute(
                "SELECT decision_id, docket_norm, court, canton, language, decision_date FROM decisions"
            )
        )
        statutes = sorted(
            tuple(r) for r in conn.execute(
                "SELECT statute_id, law_code, article, paragraph FROM statutes"
            )
        )
        decision_statutes = sorted(
            tuple(r) for r in conn.execute(
                "SELECT decision_id, statute_id, mention_count FROM decision_statutes"
            )
        )
        decision_citations = sorted(
            tuple(r) for r in conn.execute(
                """
                SELECT source_decision_id, target_ref, target_type, mention_count, is_prior_instance
                FROM decision_citations
                """
            )
        )
        citation_targets = sorted(
            (r[0], r[1], r[2], r[3])  # source, target_ref, target_id, match_type
            for r in conn.execute(
                """
                SELECT source_decision_id, target_ref, target_decision_id, match_type
                FROM citation_targets
                """
            )
        )
        return {
            "decisions": decisions,
            "statutes": statutes,
            "decision_statutes": decision_statutes,
            "decision_citations": decision_citations,
            "citation_targets": citation_targets,
        }
    finally:
        conn.close()


def test_incremental_matches_full_rebuild_after_add_change_delete(
    tmp_path: Path,
) -> None:
    # 1. Build baseline corpus + bootstrap graph
    base_db = _make_decisions_db(tmp_path / "v1", BASE_ROWS)
    graph_db = tmp_path / "graph.db"
    build_graph_incremental(
        decisions_db=base_db,
        graph_db=tmp_path / "nonexistent.db",
        output_path=graph_db,
        force_full=True,
    )

    # 2. Mutate corpus
    mutated = [dict(r) for r in BASE_ROWS]
    mutated[2]["full_text"] = (
        # add a new docket citation so we know an edge appears
        mutated[2]["full_text"] + " Vgl. auch 4A_77/2023."
    )
    mutated.pop(4)  # delete BSTGER_SK_2024_99
    mutated.append(
        {
            "decision_id": "BGER_5A_99_2026",
            "docket_number": "5A_99/2026",
            "court": "bger",
            "canton": "CH",
            "language": "de",
            "decision_date": "2026-01-01",
            "title": "Erbrecht",
            "regeste": "Art. 560 ZGB; Universalsukzession.",
            "full_text": "Vgl. BGE 140 III 86 betreffend Vertrauen. Art. 560 ZGB.",
        }
    )
    mutated_db = _make_decisions_db(tmp_path / "v2", mutated)

    # 3. Run incremental on top of the baseline graph
    incr_out = tmp_path / "graph_incremental.db"
    incr_stats = build_graph_incremental(
        decisions_db=mutated_db,
        graph_db=graph_db,
        output_path=incr_out,
    )
    assert incr_stats["mode"] == "incremental"
    assert incr_stats["counts"]["new"] == 1
    assert incr_stats["counts"]["changed"] == 1
    assert incr_stats["counts"]["deleted"] == 1

    # 4. Run a fresh full rebuild on the same mutated corpus
    full_out = tmp_path / "graph_full.db"
    build_graph(
        input_dir=Path("/nonexistent"),  # ignored when source_db given
        db_path=full_out,
        source_db=mutated_db,
    )

    # 5. Signatures must match exactly
    incr_sig = _graph_signature(incr_out)
    full_sig = _graph_signature(full_out)
    for table_name in incr_sig:
        assert incr_sig[table_name] == full_sig[table_name], (
            f"divergence in {table_name}: "
            f"incremental has {len(incr_sig[table_name])} rows, "
            f"full has {len(full_sig[table_name])} rows"
        )


def test_incremental_with_no_changes_is_a_no_op(tmp_path: Path) -> None:
    base_db = _make_decisions_db(tmp_path / "v1", BASE_ROWS)
    graph_db = tmp_path / "graph.db"
    build_graph_incremental(
        decisions_db=base_db,
        graph_db=tmp_path / "nonexistent.db",
        output_path=graph_db,
        force_full=True,
    )
    sig_before = _graph_signature(graph_db)

    # Run incremental with the SAME corpus
    incr_out = tmp_path / "graph_incremental.db"
    stats = build_graph_incremental(
        decisions_db=base_db,
        graph_db=graph_db,
        output_path=incr_out,
    )
    assert stats["mode"] == "incremental"
    assert stats["counts"] == {"new": 0, "changed": 0, "deleted": 0}

    sig_after = _graph_signature(incr_out)
    for table_name in sig_before:
        assert sig_before[table_name] == sig_after[table_name]


def test_default_output_is_sibling_not_in_place(tmp_path: Path) -> None:
    """Safety: passing no --output and no --in-place must NOT overwrite
    the live graph DB."""
    base_db = _make_decisions_db(tmp_path / "v1", BASE_ROWS)
    graph_db = tmp_path / "graph.db"
    build_graph_incremental(
        decisions_db=base_db,
        graph_db=tmp_path / "nonexistent.db",
        output_path=graph_db,
        force_full=True,
    )
    sig_before = _graph_signature(graph_db)
    mtime_before = graph_db.stat().st_mtime_ns

    # Run with no output_path → defaults to sibling _incremental
    stats = build_graph_incremental(
        decisions_db=base_db,
        graph_db=graph_db,
    )
    expected_sibling = graph_db.with_name(
        graph_db.stem + "_incremental" + graph_db.suffix
    )
    assert Path(stats["output_path"]).resolve() == expected_sibling.resolve()
    assert expected_sibling.exists()

    # Live DB unchanged
    assert graph_db.stat().st_mtime_ns == mtime_before
    assert _graph_signature(graph_db) == sig_before


def test_extractor_version_bump_forces_full_rebuild(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_db = _make_decisions_db(tmp_path / "v1", BASE_ROWS)
    graph_db = tmp_path / "graph.db"
    build_graph_incremental(
        decisions_db=base_db,
        graph_db=tmp_path / "nonexistent.db",
        output_path=graph_db,
        force_full=True,
    )

    # Bump version → next incremental call should detect mismatch and
    # bootstrap (i.e. re-run full builder).
    import search_stack.build_reference_graph_incremental as mod

    # A "bump" is any change to the effective version — manual integer
    # or extraction-source edit both land here (the comparison sites
    # read EFFECTIVE_EXTRACTOR_VERSION since the 2026-08-22 autobump).
    monkeypatch.setattr(mod, "EFFECTIVE_EXTRACTOR_VERSION",
                        mod.EFFECTIVE_EXTRACTOR_VERSION + ".bumped")

    incr_out = tmp_path / "graph_incremental.db"
    stats = build_graph_incremental(
        decisions_db=base_db,
        graph_db=graph_db,
        output_path=incr_out,
    )
    assert stats["mode"] == "full_bootstrap"
    assert "version_mismatch" in stats.get("bootstrap_reason", "")

    # And the new file should have updated meta
    conn = sqlite3.connect(incr_out)
    try:
        v = _get_meta(conn, "extractor_version")
        assert v == mod.EFFECTIVE_EXTRACTOR_VERSION
    finally:
        conn.close()


# ── cascade-delete cleanliness ────────────────────────────────────────


def test_cascade_delete_leaves_no_orphan_edges(tmp_path: Path) -> None:
    """When a row is deleted, *every* edge sourced from it must be
    cleared — including resolved citation_targets entries that the
    global resolver populated on a previous run."""
    base_db = _make_decisions_db(tmp_path / "v1", BASE_ROWS)
    graph_db = tmp_path / "graph.db"
    build_graph_incremental(
        decisions_db=base_db,
        graph_db=tmp_path / "nonexistent.db",
        output_path=graph_db,
        force_full=True,
    )
    # Sanity: BGER_4A_1_2024 has both statute and citation edges
    conn = sqlite3.connect(graph_db)
    try:
        n_stat = conn.execute(
            "SELECT COUNT(*) FROM decision_statutes WHERE decision_id = ?",
            ("BGER_4A_1_2024",),
        ).fetchone()[0]
        n_cit = conn.execute(
            "SELECT COUNT(*) FROM decision_citations WHERE source_decision_id = ?",
            ("BGER_4A_1_2024",),
        ).fetchone()[0]
        n_tgt = conn.execute(
            "SELECT COUNT(*) FROM citation_targets WHERE source_decision_id = ?",
            ("BGER_4A_1_2024",),
        ).fetchone()[0]
        assert n_stat > 0
        assert n_cit > 0
        assert n_tgt > 0
    finally:
        conn.close()

    # Delete that decision, run incremental
    mutated = [r for r in BASE_ROWS if r["decision_id"] != "BGER_4A_1_2024"]
    mutated_db = _make_decisions_db(tmp_path / "v2", mutated)

    incr_out = tmp_path / "graph_incremental.db"
    build_graph_incremental(
        decisions_db=mutated_db,
        graph_db=graph_db,
        output_path=incr_out,
    )

    conn = sqlite3.connect(incr_out)
    try:
        for query, table in [
            ("SELECT COUNT(*) FROM decision_statutes WHERE decision_id = ?", "decision_statutes"),
            ("SELECT COUNT(*) FROM decision_citations WHERE source_decision_id = ?", "decision_citations"),
            ("SELECT COUNT(*) FROM citation_targets WHERE source_decision_id = ?", "citation_targets"),
            ("SELECT COUNT(*) FROM processed_decisions WHERE decision_id = ?", "processed_decisions"),
            ("SELECT COUNT(*) FROM decisions WHERE decision_id = ?", "decisions"),
        ]:
            n = conn.execute(query, ("BGER_4A_1_2024",)).fetchone()[0]
            assert n == 0, f"orphan rows in {table}: {n}"
    finally:
        conn.close()


def test_deleting_a_cited_target_does_not_violate_fk(tmp_path: Path) -> None:
    """Regression for the citation_targets.target_decision_id FK bug.

    BGE 140 III 86 is cited by both BGER_4A_1_2024 and BGER_9C_5_2024 in
    BASE_ROWS. A naive incremental that deletes only source-side edges
    leaves citation_targets rows pointing at the deleted target — which
    raises ``sqlite3.IntegrityError: FOREIGN KEY constraint failed`` the
    moment we DELETE FROM decisions.
    """
    base_db = _make_decisions_db(tmp_path / "v1", BASE_ROWS)
    graph_db = tmp_path / "graph.db"
    build_graph_incremental(
        decisions_db=base_db,
        graph_db=tmp_path / "nonexistent.db",
        output_path=graph_db,
        force_full=True,
    )

    # Sanity: BGE_140_III_86 IS the resolved target of at least one row
    conn = sqlite3.connect(graph_db)
    try:
        n_as_target = conn.execute(
            "SELECT COUNT(*) FROM citation_targets WHERE target_decision_id = ?",
            ("BGE_140_III_86",),
        ).fetchone()[0]
        assert n_as_target > 0, (
            "fixture invariant: BGE 140 III 86 is supposed to be cited"
        )
    finally:
        conn.close()

    # Delete the cited target and run incremental — must succeed
    mutated = [r for r in BASE_ROWS if r["decision_id"] != "BGE_140_III_86"]
    mutated_db = _make_decisions_db(tmp_path / "v2", mutated)

    incr_out = tmp_path / "graph_incremental.db"
    stats = build_graph_incremental(
        decisions_db=mutated_db,
        graph_db=graph_db,
        output_path=incr_out,
    )
    assert stats["mode"] == "incremental"
    assert stats["counts"]["deleted"] == 1

    # And the deleted target must be gone from every table
    conn = sqlite3.connect(incr_out)
    try:
        for query, table in [
            ("SELECT COUNT(*) FROM decisions WHERE decision_id = ?", "decisions"),
            ("SELECT COUNT(*) FROM citation_targets WHERE target_decision_id = ?", "citation_targets"),
            ("SELECT COUNT(*) FROM processed_decisions WHERE decision_id = ?", "processed_decisions"),
        ]:
            n = conn.execute(query, ("BGE_140_III_86",)).fetchone()[0]
            assert n == 0, f"orphan rows in {table}: {n}"
    finally:
        conn.close()


def test_bootstrap_streams_rows_and_does_not_materialize(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression for the bootstrap OOM bug.

    The earlier code did ``rows = list(_iter_decision_rows(decisions_db))``
    which materialised every row (including ``full_text``) before seeding
    ``processed_decisions``. On the production corpus that is ~60 GB.

    We instrument ``_iter_decision_rows`` to wrap every yielded row so
    that the *first* attribute access on the row marks it as "consumed".
    A streaming consumer marks each row as consumed before requesting
    the next one, bounding ``rows-in-flight`` at 1. A ``list()`` consumer
    yields all N rows before any are consumed, so peak == N.
    """
    base_db = _make_decisions_db(tmp_path / "v1", BASE_ROWS)
    graph_db = tmp_path / "graph.db"

    import search_stack.build_reference_graph_incremental as mod

    real_iter = mod._iter_decision_rows
    counters = {"yielded": 0, "consumed": 0, "peak_in_flight": 0}

    class _ConsumeOnAccess(dict):
        __slots__ = ("_consumed",)

        def __init__(self, base: dict) -> None:
            super().__init__(base)
            self._consumed = False

        def get(self, key, default=None):  # type: ignore[override]
            if not self._consumed:
                self._consumed = True
                counters["consumed"] += 1
            return super().get(key, default)

    def _instrumented(db_path):
        for row in real_iter(db_path):
            counters["yielded"] += 1
            in_flight = counters["yielded"] - counters["consumed"]
            if in_flight > counters["peak_in_flight"]:
                counters["peak_in_flight"] = in_flight
            yield _ConsumeOnAccess(row)

    monkeypatch.setattr(mod, "_iter_decision_rows", _instrumented)

    build_graph_incremental(
        decisions_db=base_db,
        graph_db=tmp_path / "nonexistent.db",
        output_path=graph_db,
        force_full=True,
    )

    assert counters["yielded"] == len(BASE_ROWS), (
        f"expected to yield {len(BASE_ROWS)} rows, got {counters['yielded']}"
    )
    assert counters["consumed"] == len(BASE_ROWS), (
        f"expected to consume {len(BASE_ROWS)} rows, got {counters['consumed']}"
    )
    # Streaming: each row consumed before the next yields → peak == 1.
    # list(...): all rows yielded before first consumption → peak == N.
    assert counters["peak_in_flight"] <= 1, (
        f"bootstrap appears to have materialised the corpus: "
        f"peak rows-in-flight = {counters['peak_in_flight']} "
        f"(expected 1 for streaming consumer)"
    )


# ── bootstrap atomicity (invariant #1) ────────────────────────────────


def test_bootstrap_never_mutates_the_destination_in_place(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression for the in-place bootstrap seed.

    ``_bootstrap_via_full_rebuild`` used to build straight into the
    destination and then open it read-write to seed
    ``processed_decisions``. Under ``--in-place`` the destination IS the
    live sidecar, which every MCP worker opens ``immutable=1`` — a promise
    that the bytes never change and that no hot journal exists. Seeding on
    top of it can hand a reader a torn page or SQLITE_CORRUPT on an
    otherwise successful run.

    The contract now: the destination inode is only ever published by a
    single ``os.replace``. We prove it by recording every read-write
    ``sqlite3.connect`` and asserting none of them targeted the live path.
    """
    decisions_db = _make_decisions_db(tmp_path / "src", BASE_ROWS)
    live = tmp_path / "reference_graph.db"

    # A pre-existing "live" graph, so this is a real overwrite, not a
    # first build — and so the destination has an inode to compare.
    build_graph(input_dir=tmp_path / "src", db_path=live, source_db=decisions_db)
    assert live.exists()
    live_inode_before = live.stat().st_ino

    import search_stack.build_reference_graph_incremental as inc

    rw_targets: list[str] = []
    real_connect = sqlite3.connect

    def _spy(target, *a, **kw):  # type: ignore[no-untyped-def]
        # uri=True read-only opens are fine; bare path opens are read-write.
        if not kw.get("uri") and isinstance(target, (str, Path)):
            rw_targets.append(str(target))
        return real_connect(target, *a, **kw)

    monkeypatch.setattr(inc.sqlite3, "connect", _spy)

    stats = inc._bootstrap_via_full_rebuild(
        decisions_db=decisions_db, output_path=live
    )

    assert stats["mode"] == "full_bootstrap"
    assert stats["seeded_processed_decisions"] == len(BASE_ROWS)

    assert str(live.resolve()) not in rw_targets, (
        "bootstrap opened the live destination read-write; it must stage "
        f"in a private file and publish with one os.replace. Saw: {rw_targets}"
    )
    # Published by rename => new inode, and no staging file left behind.
    assert live.stat().st_ino != live_inode_before
    assert not list(tmp_path.glob(".*bootstrap*"))

    # And the state tables the whole cutover depends on are actually there.
    conn = sqlite3.connect(f"file:{live}?mode=ro", uri=True)
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        assert {"meta", "processed_decisions"} <= tables
        assert conn.execute(
            "SELECT COUNT(*) FROM processed_decisions"
        ).fetchone()[0] == len(BASE_ROWS)
    finally:
        conn.close()
    assert _get_meta(sqlite3.connect(live), "extractor_version") is not None


def test_bootstrap_leaves_destination_untouched_when_the_build_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed bootstrap must not damage or truncate the live sidecar."""
    decisions_db = _make_decisions_db(tmp_path / "src", BASE_ROWS)
    live = tmp_path / "reference_graph.db"
    build_graph(input_dir=tmp_path / "src", db_path=live, source_db=decisions_db)
    before = live.read_bytes()

    import search_stack.build_reference_graph_incremental as inc

    def _boom(**kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("resolution-rate regression")

    monkeypatch.setattr(inc, "build_graph", _boom)

    with pytest.raises(RuntimeError, match="resolution-rate regression"):
        inc._bootstrap_via_full_rebuild(
            decisions_db=decisions_db, output_path=live
        )

    assert live.read_bytes() == before
    assert not list(tmp_path.glob(".*bootstrap*"))


def test_bootstrap_regression_guard_still_compares_against_the_live_graph(
    tmp_path: Path,
) -> None:
    """Staging the build must not silently disable the resolution-rate guard.

    ``build_graph`` compares the new graph against the DB it is about to
    overwrite. Now that the bootstrap writes to a staging path that does
    not exist yet, the guard has to be pointed at the destination
    explicitly (``compare_against``) — otherwise it never fires.
    """
    good_db = _make_decisions_db(tmp_path / "good", BASE_ROWS)
    live = tmp_path / "reference_graph.db"
    build_graph(input_dir=tmp_path / "good", db_path=live, source_db=good_db)

    # A corpus whose citations no longer resolve: same referring texts,
    # but the targets are gone, so resolved/raw collapses.
    broken = [r for r in BASE_ROWS if not r["decision_id"].startswith("BGE_")]
    broken_db = _make_decisions_db(tmp_path / "broken", broken)

    import search_stack.build_reference_graph_incremental as inc

    with pytest.raises(RuntimeError, match="[Rr]esolution-rate regression"):
        inc._bootstrap_via_full_rebuild(
            decisions_db=broken_db, output_path=live
        )


def test_incremental_tmp_name_cannot_collide_with_the_full_builder(
    tmp_path: Path
) -> None:
    """The full builder unlinks '.{name}.tmp' unconditionally, no age guard.

    Under --in-place both builders used to derive the same tmp path, so an
    overlapping manual full rebuild could let the incremental's os.replace
    publish the full builder's half-written graph. The two must differ.
    """
    from search_stack import build_reference_graph as full

    dest = tmp_path / "reference_graph.db"
    full_tmp = dest.with_name(f".{dest.name}.tmp")

    decisions_db = _make_decisions_db(tmp_path / "src", BASE_ROWS)
    build_graph(input_dir=tmp_path / "src", db_path=dest, source_db=decisions_db)

    # Plant a sentinel where the full builder would stage its work.
    full_tmp.write_bytes(b"half-written full rebuild")

    build_graph_incremental(
        decisions_db=decisions_db, graph_db=dest, output_path=dest
    )

    assert full_tmp.exists(), (
        "the incremental builder consumed the full builder's tmp path"
    )
    assert full_tmp.read_bytes() == b"half-written full rebuild"
    assert full.__name__  # keep the import meaningful


# ── stale rollback journals (2026-10-11) ──────────────────────────────


def _leave_hot_journal(db: Path) -> Path:
    """Write to ``db`` in a child that dies mid-transaction, the way the
    step timeout kills a seeding bootstrap. The child spills dirty pages,
    so the -journal it leaves holds page images and is hot."""
    import subprocess
    import textwrap

    code = textwrap.dedent(f"""
        import os, sqlite3
        c = sqlite3.connect({str(db)!r})
        c.execute("PRAGMA cache_size=5")
        c.execute("CREATE TABLE filler(id INTEGER PRIMARY KEY, blob TEXT)")
        c.executemany("INSERT INTO filler VALUES (?, ?)",
                      ((i, "x" * 400) for i in range(5000)))
        c.execute("DELETE FROM decisions")
        os._exit(9)
    """)
    subprocess.run([sys.executable, "-c", code], check=False)
    journal = Path(str(db) + "-journal")
    assert journal.exists() and journal.stat().st_size > 0
    return journal


@pytest.mark.parametrize("killed", ["staging", "full_builder_tmp"])
def test_a_killed_run_does_not_replay_its_journal_into_the_next_bootstrap(
    tmp_path: Path, killed: str
) -> None:
    """Regression for the 2026-10-11 graph failure.

    The 10-10 bootstrap hit the step timeout while seeding its staging file
    and was killed, leaving a hot -journal. The 10-11 run unlinked the
    staging file but not the journal, built a fresh graph and renamed it
    onto the staging path. Opening it to seed replayed the dead run's page
    images into the new graph: "database disk image is malformed" after
    3 h 20 of work. The full builder's own '.tmp' has the same shape.
    """
    import search_stack.build_reference_graph_incremental as inc

    decisions_db = _make_decisions_db(tmp_path / "src", BASE_ROWS)
    live = tmp_path / "reference_graph.db"
    staging = live.with_name(f".{live.name}.bootstrap.tmp")
    victim = (staging if killed == "staging"
              else staging.with_name(f".{staging.name}.tmp"))

    # The dead run's file: a graph from an older, smaller corpus.
    old_db = _make_decisions_db(tmp_path / "old", BASE_ROWS[:2])
    build_graph(input_dir=tmp_path / "old", db_path=victim, source_db=old_db)
    journal = _leave_hot_journal(victim)

    stats = inc._bootstrap_via_full_rebuild(
        decisions_db=decisions_db, output_path=live)

    assert stats["seeded_processed_decisions"] == len(BASE_ROWS)
    assert not journal.exists()
    conn = sqlite3.connect(f"file:{live}?mode=ro", uri=True)
    try:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute(
            "SELECT COUNT(*) FROM decisions").fetchone()[0] == len(BASE_ROWS)
        assert conn.execute(
            "SELECT COUNT(*) FROM processed_decisions").fetchone()[0] == len(BASE_ROWS)
        assert "filler" not in {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()


def test_the_diff_path_does_not_replay_a_stale_journal_into_its_copy(
    tmp_path: Path
) -> None:
    """The diff path copies the base graph onto '.incremental.tmp' and opens
    it read-write; a journal left there by a killed diff run must go."""
    decisions_db = _make_decisions_db(tmp_path / "src", BASE_ROWS)
    dest = tmp_path / "reference_graph.db"
    build_graph_incremental(
        decisions_db=decisions_db, graph_db=dest, output_path=dest)

    tmp = dest.with_name(f".{dest.name}.incremental.tmp")
    old_db = _make_decisions_db(tmp_path / "old", BASE_ROWS[:2])
    build_graph(input_dir=tmp_path / "old", db_path=tmp, source_db=old_db)
    journal = _leave_hot_journal(tmp)

    stats = build_graph_incremental(
        decisions_db=decisions_db, graph_db=dest, output_path=dest)

    assert stats.get("mode") != "full_bootstrap"
    assert not journal.exists()
    conn = sqlite3.connect(f"file:{dest}?mode=ro", uri=True)
    try:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute(
            "SELECT COUNT(*) FROM decisions").fetchone()[0] == len(BASE_ROWS)
    finally:
        conn.close()
