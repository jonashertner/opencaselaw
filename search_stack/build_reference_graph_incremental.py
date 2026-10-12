#!/usr/bin/env python3
"""Incremental reference-graph rebuild — shadow mode v0.1 (2026-05-05).

Diffs the live `decisions.db` against a `processed_decisions` state table
in `reference_graph.db` and re-extracts only the changed rows. The full
rebuild scans 971 K rows × regex-extraction in ~90 min; on a typical
nightly the change set is < 1 K rows, so this path runs in 2-5 min.

Key invariants
--------------
* Default output is a SIBLING file (e.g. ``reference_graph_incremental.db``)
  — never overwrites the live graph DB unless ``--in-place`` is passed.
* The hash used for change detection covers *every* field the extractor
  reads from a decision row (title + regeste + full_text plus all
  metadata that flows into resolution). Bumping ``EXTRACTOR_VERSION``
  forces a full rebuild on the next run.
* citation_targets is truncated and re-resolved globally each run; the
  cost (~5-10 min on 6.5 M edges) is unavoidable because resolution
  depends on the global state of `decisions` (a new target decision can
  resolve yesterday's edge).
* Atomic-swap pattern matches build_fts5: copy live → .tmp, mutate,
  ``os.replace`` + sidecar cleanup (post-mortem 2026-05-04 → 2026-05-05).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from search_stack.build_reference_graph import (  # noqa: E402
    SCHEMA_SQL,
    _docket_norm,
    _open_sqlite_readonly,
    _resolve_citation_targets,
    build_graph,
    insert_docket_aliases,
)
from search_stack.reference_extraction import (  # noqa: E402
    extract_case_citations,
    extract_prior_instance,
    extract_statute_references,
)


EXTRACTOR_VERSION = 1

# The version the state tables actually carry. Derived from the extraction
# source, so a code change to reference_extraction.py or the full builder
# (which owns the BGer↔BGE cross-ref pass, 48d213d4) forces a bootstrap
# without anyone remembering to bump the integer above. c5edb71c shipping
# without a bump is how the shadow spent ten weeks 13% short on
# citation_targets. See search_stack/extractor_version.py.
from search_stack.extractor_version import effective_version as _eff_ver  # noqa: E402

EFFECTIVE_EXTRACTOR_VERSION = _eff_ver(
    EXTRACTOR_VERSION,
    Path(__file__).parent / "reference_extraction.py",
    Path(__file__).parent / "build_reference_graph.py",
)

# Bootstrap streaming-batch size. Module-scoped so tests can monkey-patch
# down to a small value to verify the streaming contract on a tiny corpus.
BOOTSTRAP_BATCH_SIZE = 1000

STATE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS processed_decisions (
    decision_id TEXT PRIMARY KEY,
    extractor_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# Fields the extractor consumes. If this list ever grows, bump
# EXTRACTOR_VERSION above so existing graphs are forced through a full
# rebuild on the next nightly.
_HASHED_FIELDS = (
    "decision_id",
    "docket_number",
    "court",
    "canton",
    "language",
    "decision_date",
    "title",
    "regeste",
    "full_text",
)


def _extractor_hash(row: dict) -> str:
    """Deterministic SHA-256 over every field the extractor reads.

    Pipe-separated to avoid collision if two adjacent fields swap content
    (rare in practice, defensive).
    """
    parts = [(row.get(field) or "") for field in _HASHED_FIELDS]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def _ensure_state_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(STATE_SCHEMA_SQL)


def _get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def _set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def _peek_extractor_version(db_path: Path) -> str | None:
    """Read meta.extractor_version from a candidate base DB, or None."""
    if not db_path.exists():
        return None
    try:
        peek = sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True)
    except sqlite3.Error:
        return None
    try:
        try:
            return _get_meta(peek, "extractor_version")
        except sqlite3.OperationalError:
            return None
    finally:
        peek.close()


def _select_diff_base(live_db: Path, output_path: Path,
                      force_full: bool) -> tuple[Path | None, str | None]:
    """Pick the DB whose processed-state we diff against.

    Prefer the PREVIOUS incremental output (the sibling): in shadow mode
    the live DB is full-rebuilt nightly by the legacy pipeline WITHOUT
    state tables, so peeking only at the live DB forced a full ~2.5 h
    bootstrap every single night (bootstrap_reason "no_state" on every
    shadow run through 2026-06-11) — the sibling written by the previous
    incremental run is the one that carries processed_decisions state.
    Falls back to the live DB (which is also the in-place path, where
    output_path == live_db). Returns (base, bootstrap_reason); base is
    None when a full bootstrap is required.
    """
    if force_full:
        return None, "force_full"
    candidates = ([output_path, live_db]
                  if output_path != live_db else [live_db])
    mismatches = []
    for cand in candidates:
        stored = _peek_extractor_version(cand)
        if stored == EFFECTIVE_EXTRACTOR_VERSION:
            return cand, None
        if stored is not None:
            mismatches.append(f"{cand.name}:{stored}")
    if mismatches:
        return None, "version_mismatch:" + ",".join(mismatches)
    return None, "no_state"


def _iter_decision_rows(decisions_db: Path) -> Iterator[dict]:
    """Yield every row from decisions.db with the same dict shape the
    full builder consumes (so we can reuse the extraction code path
    unchanged).
    """
    conn = _open_sqlite_readonly(decisions_db)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(
            """
            SELECT decision_id, docket_number, court, canton, language,
                   decision_date, title, regeste, full_text
            FROM decisions
            ORDER BY rowid
            """
        )
        while True:
            rows = cur.fetchmany(1000)
            if not rows:
                break
            for row in rows:
                yield dict(row)
    finally:
        conn.close()


def _diff_state(
    decisions_db: Path,
    graph_conn: sqlite3.Connection,
) -> tuple[set[str], set[str], set[str], dict[str, str], dict[str, dict]]:
    """Compare current decisions.db against processed_decisions in graph_conn.

    Returns:
        (new_ids, changed_ids, deleted_ids, hashes_by_id, rows_by_id_for_writes)

    rows_by_id_for_writes only holds rows for new ∪ changed (so we don't
    keep the whole 60 GB corpus in memory).
    """
    processed: dict[str, str] = {
        r[0]: r[1]
        for r in graph_conn.execute(
            "SELECT decision_id, extractor_hash FROM processed_decisions"
        )
    }

    new_ids: set[str] = set()
    changed_ids: set[str] = set()
    seen: set[str] = set()
    hashes_by_id: dict[str, str] = {}
    rows_for_writes: dict[str, dict] = {}

    for row in _iter_decision_rows(decisions_db):
        did = row.get("decision_id") or ""
        if not did:
            continue
        seen.add(did)
        h = _extractor_hash(row)
        hashes_by_id[did] = h
        prior = processed.get(did)
        if prior is None:
            new_ids.add(did)
            rows_for_writes[did] = row
        elif prior != h:
            changed_ids.add(did)
            rows_for_writes[did] = row

    deleted_ids = set(processed.keys()) - seen
    return new_ids, changed_ids, deleted_ids, hashes_by_id, rows_for_writes


def _delete_source_edges(conn: sqlite3.Connection, ids: set[str]) -> None:
    """Clear per-decision edge state for ``ids`` so that re-extraction
    starts from a clean slate.

    Caller must have truncated ``citation_targets`` upstream (the table
    is re-resolved globally below, and clearing it up front is what
    makes ``_delete_decisions`` safe under ``PRAGMA foreign_keys=ON`` —
    otherwise a deleted decision that is cited by another row triggers
    the ``citation_targets.target_decision_id`` FK).
    """
    if not ids:
        return
    cur = conn.cursor()
    for did in ids:
        cur.execute(
            "DELETE FROM decision_citations WHERE source_decision_id = ?",
            (did,),
        )
        cur.execute(
            "DELETE FROM decision_statutes WHERE decision_id = ?",
            (did,),
        )
        cur.execute(
            "DELETE FROM processed_decisions WHERE decision_id = ?",
            (did,),
        )
        cur.execute(
            "DELETE FROM docket_aliases WHERE decision_id = ?",
            (did,),
        )


def _delete_decisions(conn: sqlite3.Connection, ids: set[str]) -> None:
    """Drop rows from ``decisions`` table when a decision is no longer in
    the source corpus. Edges are cascade-deleted by ``_delete_source_edges``
    first.
    """
    if not ids:
        return
    cur = conn.cursor()
    for did in ids:
        cur.execute("DELETE FROM decisions WHERE decision_id = ?", (did,))


def _upsert_decision(conn: sqlite3.Connection, row: dict) -> None:
    conn.execute(
        """
        INSERT INTO decisions
        (decision_id, docket_number, docket_norm, court, canton, language, decision_date)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(decision_id) DO UPDATE SET
            docket_number = excluded.docket_number,
            docket_norm   = excluded.docket_norm,
            court         = excluded.court,
            canton        = excluded.canton,
            language      = excluded.language,
            decision_date = excluded.decision_date
        """,
        (
            row.get("decision_id"),
            row.get("docket_number") or "",
            _docket_norm(row.get("docket_number") or ""),
            row.get("court"),
            row.get("canton"),
            row.get("language"),
            row.get("decision_date"),
        ),
    )
    # Caption docket forms (ZH "LA200010-O/U"), as the full builder records
    # them; _delete_source_edges cleared this row's old ones.
    insert_docket_aliases(conn, row)


def _apply_extraction(
    conn: sqlite3.Connection,
    rows: dict[str, dict],
    hashes_by_id: dict[str, str],
) -> tuple[int, int]:
    """Run the same extraction the full builder runs, but only over the
    changed/new ``rows``. Returns (statute_edges, citation_edges)."""
    statute_edges = 0
    citation_edges = 0

    for did, row in rows.items():
        _upsert_decision(conn, row)

        text = " ".join(
            [
                row.get("title") or "",
                row.get("regeste") or "",
                row.get("full_text") or "",
            ]
        )
        statutes = extract_statute_references(text)
        citations = extract_case_citations(text)
        prior_instance_dockets = set(extract_prior_instance(row.get("full_text")))

        for statute in statutes:
            conn.execute(
                """
                INSERT OR IGNORE INTO statutes(statute_id, law_code, article, paragraph)
                VALUES (?, ?, ?, ?)
                """,
                (
                    statute.normalized,
                    statute.law_code,
                    statute.article,
                    statute.paragraph,
                ),
            )
            conn.execute(
                """
                INSERT INTO decision_statutes(decision_id, statute_id, mention_count)
                VALUES (?, ?, 1)
                ON CONFLICT(decision_id, statute_id)
                DO UPDATE SET mention_count = mention_count + 1
                """,
                (did, statute.normalized),
            )
            statute_edges += 1

        for citation in citations:
            target_type = "bge" if citation.citation_type == "bge" else "docket"
            is_prior = 1 if citation.normalized in prior_instance_dockets else 0
            conn.execute(
                """
                INSERT INTO decision_citations
                (source_decision_id, target_ref, target_type, mention_count, is_prior_instance)
                VALUES (?, ?, ?, 1, ?)
                ON CONFLICT(source_decision_id, target_ref)
                DO UPDATE SET mention_count = mention_count + 1,
                             is_prior_instance = MAX(is_prior_instance, excluded.is_prior_instance)
                """,
                (did, citation.normalized, target_type, is_prior),
            )
            citation_edges += 1

        for pi_docket in prior_instance_dockets:
            conn.execute(
                """
                INSERT INTO decision_citations
                (source_decision_id, target_ref, target_type, mention_count, is_prior_instance)
                VALUES (?, ?, 'docket', 1, 1)
                ON CONFLICT(source_decision_id, target_ref)
                DO UPDATE SET is_prior_instance = 1
                """,
                (did, pi_docket),
            )
            citation_edges += 1

        conn.execute(
            "INSERT INTO processed_decisions(decision_id, extractor_hash) "
            "VALUES (?, ?) ON CONFLICT(decision_id) DO UPDATE SET extractor_hash = excluded.extractor_hash",
            (did, hashes_by_id[did]),
        )

    return statute_edges, citation_edges


def _cleanup_sidecars(target: Path) -> None:
    """Remove -wal/-shm and a stale rollback -journal. A -journal left by a
    killed run is "hot": SQLite replays its page images into whatever file
    is at the path next time. The full builder renames a finished graph onto
    the staging path, and the diff path copies the base onto its tmp, so the
    replay lands in a fresh 5 GB graph and corrupts it. On 2026-10-11 the
    seeding of a bootstrap failed with "database disk image is malformed"
    after the 10-10 run was killed mid-seed at the step timeout. No path
    here is ever reopened to resume, so a journal never has to survive."""
    for ext in ("-wal", "-shm", "-journal"):
        p = Path(str(target) + ext)
        if p.exists():
            p.unlink()


def _bootstrap_via_full_rebuild(
    *,
    decisions_db: Path,
    output_path: Path,
) -> dict:
    """No prior state (or version mismatch) — call the canonical full
    builder and seed processed_decisions on top.
    """
    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Stage the whole bootstrap in a private file and publish it with ONE
    # os.replace at the end.
    #
    # Building into output_path directly — and then opening it read-write
    # to seed processed_decisions — mutates the live sidecar whenever this
    # runs under --in-place. Every MCP worker opens these DBs with
    # immutable=1, which promises SQLite that the bytes never change and
    # that no hot journal exists; a reader that connects mid-seed can get
    # a torn page or SQLITE_CORRUPT on an otherwise successful run. That
    # is invariant #1. The diff path below has always staged through a
    # .tmp; the bootstrap path did not.
    staging = output_path.with_name(f".{output_path.name}.bootstrap.tmp")
    if staging.exists():
        staging.unlink()
    _cleanup_sidecars(staging)

    try:
        stats = build_graph(
            input_dir=Path("output/decisions"),  # ignored when source_db given
            db_path=staging,
            source_db=decisions_db,
            # Keep the resolution-rate regression guard pointed at the
            # real previous graph; staging does not exist yet, so without
            # this the guard would never fire on a bootstrap.
            compare_against=output_path,
        )
    except BaseException:
        if staging.exists():
            staging.unlink()
        _cleanup_sidecars(staging)
        raise

    # Seed state table. We stream rows from disk (NOT ``list(...)``):
    # the corpus is ~60 GB on production and full_text is in every row,
    # so materialising would OOM on the typical 8-16 GB instance.
    conn = sqlite3.connect(str(staging))
    try:
        _ensure_state_tables(conn)
        cur = conn.cursor()
        batch: list[tuple[str, str]] = []
        seeded = 0

        def _flush() -> None:
            nonlocal seeded
            if not batch:
                return
            cur.executemany(
                "INSERT INTO processed_decisions(decision_id, extractor_hash) "
                "VALUES (?, ?) "
                "ON CONFLICT(decision_id) DO UPDATE SET extractor_hash = excluded.extractor_hash",
                batch,
            )
            seeded += len(batch)
            batch.clear()

        for row in _iter_decision_rows(decisions_db):
            did = row.get("decision_id")
            if not did:
                continue
            batch.append((did, _extractor_hash(row)))
            if len(batch) >= BOOTSTRAP_BATCH_SIZE:
                _flush()
        _flush()

        _set_meta(conn, "extractor_version", EFFECTIVE_EXTRACTOR_VERSION)
        _set_meta(
            conn,
            "last_full_rebuild_at",
            datetime.now(timezone.utc).isoformat(),
        )
        conn.commit()
        stats["seeded_processed_decisions"] = seeded
    except BaseException:
        # finally: below still closes the connection.
        if staging.exists():
            staging.unlink()
        _cleanup_sidecars(staging)
        raise
    finally:
        conn.close()

    # Single publish point. Everything above touched only `staging`.
    _cleanup_sidecars(staging)
    os.replace(staging, output_path)

    stats["db_path"] = str(output_path)
    stats["mode"] = "full_bootstrap"
    return stats


def build_graph_incremental(
    *,
    decisions_db: Path,
    graph_db: Path,
    output_path: Path | None = None,
    force_full: bool = False,
) -> dict:
    """Incremental rebuild. ``output_path`` defaults to a sibling file
    (NOT the live DB) so callers must opt-in to in-place mutation.
    """
    decisions_db = decisions_db.resolve()
    graph_db = graph_db.resolve()
    output_path = (
        output_path.resolve()
        if output_path is not None
        else graph_db.with_name(
            graph_db.stem + "_incremental" + graph_db.suffix
        )
    )

    t0 = time.time()
    stats: dict = {
        "decisions_db": str(decisions_db),
        "graph_db": str(graph_db),
        "output_path": str(output_path),
        "extractor_version": EFFECTIVE_EXTRACTOR_VERSION,
    }

    base, bootstrap_reason = _select_diff_base(
        graph_db, output_path, force_full)

    if base is None:
        stats["bootstrap_reason"] = bootstrap_reason
        full_stats = _bootstrap_via_full_rebuild(
            decisions_db=decisions_db,
            output_path=output_path,
        )
        full_stats["elapsed_seconds"] = round(time.time() - t0, 2)
        stats.update(full_stats)
        return stats

    # Copy base → tmp; remove any sidecars that came along.
    stats["diff_base"] = str(base)
    # Distinct from build_reference_graph.py's ".{name}.tmp": under
    # --in-place both resolve to the same path, and the full builder
    # unlinks its tmp unconditionally with no age guard. The timers are
    # day-disjoint after the cutover, but a manual full rebuild overlapping
    # this one could otherwise let our os.replace rename the full
    # builder's half-written graph over the live DB.
    tmp_path = output_path.with_name(f".{output_path.name}.incremental.tmp")
    if tmp_path.exists():
        tmp_path.unlink()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(base, tmp_path)
    _cleanup_sidecars(tmp_path)

    conn = sqlite3.connect(str(tmp_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_SQL)  # idempotent — picks up any new indexes
    _ensure_state_tables(conn)

    try:
        new_ids, changed_ids, deleted_ids, hashes_by_id, rows_for_writes = (
            _diff_state(decisions_db, conn)
        )
        stats["counts"] = {
            "new": len(new_ids),
            "changed": len(changed_ids),
            "deleted": len(deleted_ids),
        }

        # Truncate citation_targets up front. Two reasons:
        #   1. We re-resolve it globally below regardless (cross-row
        #      resolution: a new target decision today resolves an edge
        #      extracted yesterday), so keeping stale rows is moot.
        #   2. With FKs on, ``citation_targets.target_decision_id`` would
        #      block ``_delete_decisions`` for any deleted decision that
        #      is cited by another row. Clearing the table first removes
        #      that constraint.
        conn.execute("DELETE FROM citation_targets")

        _delete_source_edges(conn, changed_ids | deleted_ids)
        _delete_decisions(conn, deleted_ids)

        statute_edges, citation_edges = _apply_extraction(
            conn,
            rows_for_writes,
            hashes_by_id,
        )
        stats["statute_edges_inserted"] = statute_edges
        stats["citation_edges_inserted"] = citation_edges

        # Drop orphan dimension rows (statutes nobody references after
        # the deletes). Keeps signature parity with a fresh full rebuild.
        conn.execute(
            """
            DELETE FROM statutes
             WHERE statute_id NOT IN (SELECT statute_id FROM decision_statutes)
            """
        )

        _resolve_citation_targets(conn)

        _set_meta(conn, "extractor_version", EFFECTIVE_EXTRACTOR_VERSION)
        _set_meta(
            conn,
            "last_incremental_run_at",
            datetime.now(timezone.utc).isoformat(),
        )

        conn.commit()

        stats["totals"] = {
            "decisions": conn.execute(
                "SELECT COUNT(*) FROM decisions"
            ).fetchone()[0],
            "statutes": conn.execute(
                "SELECT COUNT(*) FROM statutes"
            ).fetchone()[0],
            "decision_citations": conn.execute(
                "SELECT COUNT(*) FROM decision_citations"
            ).fetchone()[0],
            "citation_targets": conn.execute(
                "SELECT COUNT(*) FROM citation_targets"
            ).fetchone()[0],
            "processed_decisions": conn.execute(
                "SELECT COUNT(*) FROM processed_decisions"
            ).fetchone()[0],
        }
    except Exception:
        conn.close()
        if tmp_path.exists():
            tmp_path.unlink()
        _cleanup_sidecars(tmp_path)
        raise

    conn.execute("PRAGMA journal_mode=DELETE")
    conn.close()
    _cleanup_sidecars(tmp_path)

    os.replace(tmp_path, output_path)
    _cleanup_sidecars(output_path)

    stats["mode"] = "incremental"
    stats["elapsed_seconds"] = round(time.time() - t0, 2)
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Incremental reference-graph rebuild. Default writes to a "
            "sibling .incremental.db file — pass --in-place to mutate the "
            "live graph DB."
        ),
    )
    parser.add_argument(
        "--decisions-db",
        type=Path,
        required=True,
        help="Source decisions.db (read-only)",
    )
    parser.add_argument(
        "--graph-db",
        type=Path,
        required=True,
        help="Existing reference_graph.db whose state we diff against",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help=(
            "Where to write the rebuilt graph DB. Defaults to a sibling "
            "file with `_incremental` suffix (shadow mode)."
        ),
    )
    parser.add_argument(
        "--in-place",
        action="store_true",
        help=(
            "Overwrite --graph-db at the end. NOT default; opt-in. "
            "Ignored if --output is also set."
        ),
    )
    parser.add_argument(
        "--force-full",
        action="store_true",
        help="Skip diff path and run a full rebuild (writes processed_decisions).",
    )
    args = parser.parse_args()

    output_path = args.output
    if output_path is None and args.in_place:
        output_path = args.graph_db

    stats = build_graph_incremental(
        decisions_db=args.decisions_db,
        graph_db=args.graph_db,
        output_path=output_path,
        force_full=args.force_full,
    )
    print(json.dumps(stats, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
