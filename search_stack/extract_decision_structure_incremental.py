"""Incremental decision_structure rebuild — shadow mode v0.1 (2026-05-06).

The full extractor (``extract_decision_structure.py --build``) reads every
JSONL shard, runs the regex/heuristic extractor over each row's
``full_text``, and writes a fresh sidecar DB. With 110 shards × 971 K
decisions × silent FTS5 paragraph rebuild, that's ~85 min on the data
volume — most of which is wasted when only a handful of decisions
changed since the last build.

This module mirrors ``build_reference_graph_incremental.py`` (which we
shipped earlier today): track an extractor_hash per decision_id in a
``processed_decisions`` state table inside decision_structure.db, diff
against the live decisions.db, and re-extract only new/changed rows.

Key invariants (same shape as the reference-graph one)
------------------------------------------------------
* Default output is a SIBLING file (``decision_structure_incremental.db``);
  must opt into ``--in-place`` to touch the live sidecar.
* ``EXTRACTOR_VERSION`` bump forces a full rebuild on the next run; any
  schema or extraction-logic change must bump this.
* The hash covers every field the extractor reads from a decision
  (``full_text``, ``language``, ``decision_id``, plus the metadata that
  flows to the structure table).
* Atomic-swap pattern with sidecar cleanup (post-mortem 2026-05-04 → 05).
* FTS5 keeps in lockstep via triggers — no end-of-build silent rebuild.
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

from search_stack.extract_decision_structure import (  # noqa: E402
    SCHEMA,
    extract,
)


# v2 (2026-07-03): ne/ti/so direct-format Erwaegungen markers added to
# extract_decision_structure (commit 5cb5434) — letter-spaced NE
# "C O N S I D E R A N T", glued TI "consideratoin diritto:"/"decreta:",
# glued SO "inErwaegung:". A patterns-only change leaves each decision's
# extractor_hash unchanged, so WITHOUT this bump the checkpoint logic skips
# the 6,029 already-seen decisions and the fix never reaches the sidecar
# (proven: 2026-07-03 shadow drift stayed at 0.797%). Bumping forces one
# full re-extract on the next run, healing the frozen backlog.
# v3 (2026-09-08): "regeste" joined _HASHED_FIELDS (it flows into structure.regeste
# but was invisible to the diff, so a regeste set by build_fts5 after extraction
# stayed stale for ever once the nightly --force-full went away). The bump
# bootstraps once instead of pushing 1.07M rows through the changed-row cascade.
EXTRACTOR_VERSION = 3  # the source hash of extract_decision_structure.py already forces a full re-extraction on change

# Derived version — the state tables carry this, so a change to the
# structure extractor bootstraps the sidecar without a remembered bump.
# 9cf68db5 shipping without one is how 3,995 decisions' coverage stayed
# LOST vs the full rebuild despite this file's own comment narrating the
# identical 2026-07-03 incident. See search_stack/extractor_version.py.
from search_stack.extractor_version import effective_version as _eff_ver  # noqa: E402

EFFECTIVE_EXTRACTOR_VERSION = _eff_ver(
    EXTRACTOR_VERSION,
    Path(__file__).parent / "extract_decision_structure.py",
)

# Schema additions on top of the existing decision_structure schema. The
# triggers keep FTS5 lockstep so the post-build "rebuild" silent phase
# isn't needed on incremental runs.
INCREMENTAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS processed_decisions (
    decision_id TEXT PRIMARY KEY,
    extractor_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS erwaegungen_paragraph_ai
AFTER INSERT ON erwaegungen_paragraph BEGIN
    INSERT INTO erwaegungen_paragraph_fts(rowid, text)
    VALUES (new.rowid, new.text);
END;

CREATE TRIGGER IF NOT EXISTS erwaegungen_paragraph_ad
AFTER DELETE ON erwaegungen_paragraph BEGIN
    INSERT INTO erwaegungen_paragraph_fts(erwaegungen_paragraph_fts, rowid, text)
    VALUES ('delete', old.rowid, old.text);
END;
"""

# Fields the extractor reads from each row. If the extractor is ever
# extended to read additional fields, bump EXTRACTOR_VERSION above so
# existing sidecars are forced through a full rebuild on the next run.
_HASHED_FIELDS = (
    "decision_id",
    "court",
    "canton",
    "language",
    "decision_date",
    "full_text",
    "regeste",
)


def _extractor_hash(row: dict) -> str:
    parts = [(row.get(f) or "") for f in _HASHED_FIELDS]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


# Streaming parameters (2026-09-08). The first production run of the
# served-text step 2g (2026-09-07) loaded every decision's full_text into
# one dict before writing a row, reached 34 GB against the unit's 32 GiB
# MemoryHigh, printed nothing for 9,000 s and was killed by publish.py's
# stall watchdog; the build fell back to the shard builder. The production
# path now extracts and upserts row by row, commits every COMMIT_EVERY
# writes and prints a progress line every PROGRESS_EVERY scanned rows so
# the watchdog sees a live process. Memory is O(number of ids), not O(text).
COMMIT_EVERY = 2_000
PROGRESS_EVERY = 10_000


def _progress(message: str) -> None:
    print(f"[extract_structure] {message}", flush=True)


def _ensure_state_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.executescript(INCREMENTAL_SCHEMA)


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
        except sqlite3.DatabaseError:      # no meta table, or not a database at all
            return None
    finally:
        peek.close()


def _peek_meta(db_path: Path, key: str) -> str | None:
    """Read one meta value from a candidate DB read-only, or None."""
    if not db_path.exists():
        return None
    try:
        peek = sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True)
    except sqlite3.Error:
        return None
    try:
        try:
            return _get_meta(peek, key)
        except sqlite3.DatabaseError:
            return None
    finally:
        peek.close()


def _state_consistent(db_path: Path) -> bool:
    """A base whose processed_decisions is out of step with structure (a
    killed run, a partial manual DELETE) would make every later diff write
    nothing while the coverage gate, measured on the same content, passes.
    Cheap count check; a mismatch forces a bootstrap."""
    try:
        peek = sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True)
    except sqlite3.Error:
        return False
    try:
        try:
            processed = peek.execute("SELECT COUNT(*) FROM processed_decisions").fetchone()[0]
            structure = peek.execute("SELECT COUNT(*) FROM structure").fetchone()[0]
        except sqlite3.DatabaseError:
            # One of the tables is missing: nothing to compare. The version
            # check already vouched for the base; _ensure_state_tables adds
            # what is absent. (A file that is not a database never gets
            # here — _peek_extractor_version returns None for it.)
            return True
        return processed == structure
    finally:
        peek.close()


def _select_diff_base(live_db: Path, output_path: Path,
                      force_full: bool) -> tuple[Path | None, str | None]:
    """Pick the DB whose processed-state we diff against.

    Mirrors the reference-graph incremental builder: prefer the PREVIOUS
    incremental output (the sibling) — in shadow mode the live DB is
    full-rebuilt nightly WITHOUT state tables, so peeking only at the
    live DB forced a full bootstrap every night. Falls back to the live
    DB (also the in-place path, where output_path == live_db). Returns
    (base, bootstrap_reason); base is None when a full bootstrap is
    required.
    """
    if force_full:
        return None, "force_full"
    candidates = ([output_path, live_db]
                  if output_path != live_db else [live_db])
    mismatches = []
    for cand in candidates:
        stored = _peek_extractor_version(cand)
        if stored == EFFECTIVE_EXTRACTOR_VERSION:
            if _state_consistent(cand):
                return cand, None
            return None, f"state_inconsistent:{cand.name}"
        if stored is not None:
            mismatches.append(f"{cand.name}:{stored}")
    if mismatches:
        return None, "version_mismatch:" + ",".join(mismatches)
    return None, "no_state"


def _open_decisions_ro(decisions_db: Path) -> sqlite3.Connection:
    last: Exception | None = None
    for _ in range(5):
        try:
            return sqlite3.connect(
                f"file:{decisions_db}?mode=ro&immutable=1",
                uri=True, timeout=1.0,
            )
        except sqlite3.OperationalError as e:
            last = e
            time.sleep(0.2)
    raise sqlite3.OperationalError(f"Unable to open decisions.db: {last}")


def _iter_decision_rows(decisions_db: Path) -> Iterator[dict]:
    conn = _open_decisions_ro(decisions_db)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(
            """
            SELECT decision_id, court, canton, language, decision_date,
                   full_text, regeste
            FROM decisions
            WHERE full_text IS NOT NULL AND length(full_text) >= 500
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
    sc_conn: sqlite3.Connection,
) -> tuple[set[str], set[str], set[str], dict[str, str], dict[str, dict]]:
    """Return (new, changed, deleted, hashes_by_id, rows_for_writes).

    Diagnostic/test helper: it keeps the full row (with full_text) of every
    new or changed decision in memory. The production path is
    ``_stream_extract``, which never holds more than one row."""
    processed: dict[str, str] = {
        r[0]: r[1]
        for r in sc_conn.execute(
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


def _delete_for_decisions(conn: sqlite3.Connection, ids: set[str]) -> None:
    """Cascade-delete every artifact for ``ids``. FTS5 trigger fires on
    erwaegungen_paragraph DELETE so the FTS5 vtab stays in lockstep."""
    if not ids:
        return
    cur = conn.cursor()
    for did in ids:
        cur.execute(
            "DELETE FROM erwaegungen_paragraph WHERE decision_id = ?", (did,),
        )
        cur.execute(
            "DELETE FROM structure WHERE decision_id = ?", (did,),
        )
        cur.execute(
            "DELETE FROM processed_decisions WHERE decision_id = ?", (did,),
        )


def _apply_one(
    conn: sqlite3.Connection,
    did: str,
    row: dict,
    extractor_hash: str,
    now: str,
) -> int | None:
    """Extract one decision and upsert ``structure``, its paragraphs and its
    ``processed_decisions`` hash. Returns the number of paragraphs written,
    or None when the text is too short to extract."""
    written = 0
    ft = row.get("full_text") or ""
    if len(ft) < 500:
        return None
    s = extract(ft, row.get("language", "de"), did)

    # Upsert into structure
    conn.execute(
        """
        INSERT INTO structure
        (decision_id, court, canton, language, decision_date, regeste,
         sachverhalt, sachverhalt_method,
         erwaegungen, erwaegungen_method, erwaegungen_paragraph_count,
         dispositiv, dispositiv_method, dispositiv_orders, extracted_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(decision_id) DO UPDATE SET
            court = excluded.court,
            canton = excluded.canton,
            language = excluded.language,
            decision_date = excluded.decision_date,
            regeste = excluded.regeste,
            sachverhalt = excluded.sachverhalt,
            sachverhalt_method = excluded.sachverhalt_method,
            erwaegungen = excluded.erwaegungen,
            erwaegungen_method = excluded.erwaegungen_method,
            erwaegungen_paragraph_count = excluded.erwaegungen_paragraph_count,
            dispositiv = excluded.dispositiv,
            dispositiv_method = excluded.dispositiv_method,
            dispositiv_orders = excluded.dispositiv_orders,
            extracted_at = excluded.extracted_at
        """,
        (
            did, row.get("court"), row.get("canton"),
            s.language, row.get("decision_date"),
            row.get("regeste") or None,
            s.sachverhalt, s.sachverhalt_method,
            s.erwaegungen, s.erwaegungen_method,
            len(s.erwaegungen_paragraphs),
            s.dispositiv, s.dispositiv_method,
            json.dumps(s.dispositiv_orders, ensure_ascii=False)
            if s.dispositiv_orders else None,
            now,
        ),
    )

    # Replace this decision's paragraphs (delete then insert; trigger
    # keeps FTS5 in sync). Skip the synthetic depth=0 fallback.
    # Use INSERT OR REPLACE to match extract_decision_structure.py's
    # full-builder semantics — the extractor can emit two paragraphs
    # with the same e_number for a single decision when the regex
    # backtracks across nested numbering (e.g., "2." inside an
    # "Erwägung 2"). The full builder silently last-wins on those;
    # the incremental builder was crashing with UNIQUE constraint
    # violations on the first-real-run today 2026-05-18 16:51 UTC
    # (decision_structure_incremental.py:273).
    conn.execute(
        "DELETE FROM erwaegungen_paragraph WHERE decision_id = ?", (did,),
    )
    for p in s.erwaegungen_paragraphs:
        if p.get("depth", 0) == 0:
            continue
        conn.execute(
            """
            INSERT OR REPLACE INTO erwaegungen_paragraph
            (decision_id, e_number, depth, parent, text)
            VALUES (?, ?, ?, ?, ?)
            """,
            (did, p["e_number"], p["depth"], p.get("parent"), p["text"]),
        )
        written += 1

    conn.execute(
        "INSERT INTO processed_decisions(decision_id, extractor_hash) "
        "VALUES (?, ?) "
        "ON CONFLICT(decision_id) DO UPDATE SET extractor_hash = excluded.extractor_hash",
        (did, extractor_hash),
    )
    return written


def _apply_extraction(
    conn: sqlite3.Connection,
    rows: dict[str, dict],
    hashes_by_id: dict[str, str],
) -> tuple[int, int]:
    """Run extract() over already-materialised rows (tests, small batches);
    the production path streams (see ``_stream_extract``).
    Returns (decisions_written, paragraphs_written)."""
    decisions_n = 0
    paragraphs_n = 0
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    for did, row in rows.items():
        n = _apply_one(conn, did, row, hashes_by_id[did], now)
        if n is None:
            continue
        decisions_n += 1
        paragraphs_n += n
    return decisions_n, paragraphs_n


def _stream_extract(
    conn: sqlite3.Connection,
    decisions_db: Path,
    processed: dict[str, str] | None,
) -> dict:
    """One pass over decisions.db, writing as it goes.

    ``processed`` is the id -> extractor_hash map of the base sidecar, or
    None for a bootstrap (every row is written). A row whose hash is new or
    changed is (re)extracted immediately; ids present in ``processed`` but
    absent from decisions.db are cascade-deleted at the end. Commits every
    COMMIT_EVERY writes and prints progress every PROGRESS_EVERY scanned
    rows. Returns counts and totals for the run summary.
    """
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    t0 = time.time()
    seen: set[str] = set()
    counts = {"new": 0, "changed": 0, "deleted": 0}
    decisions_n = 0
    paragraphs_n = 0
    scanned = 0
    for row in _iter_decision_rows(decisions_db):
        did = row.get("decision_id") or ""
        if not did:
            continue
        seen.add(did)
        scanned += 1
        h = _extractor_hash(row)
        if processed is None:
            kind = "new"
        else:
            prior = processed.get(did)
            if prior is None:
                kind = "new"
            elif prior != h:
                kind = "changed"
                _delete_for_decisions(conn, {did})
            else:
                kind = None
        if kind is not None:
            n = _apply_one(conn, did, row, h, now)
            if n is not None:
                counts[kind] += 1
                decisions_n += 1
                paragraphs_n += n
                if decisions_n % COMMIT_EVERY == 0:
                    conn.commit()
        if scanned % PROGRESS_EVERY == 0:
            _progress(f"scanned {scanned:,} decisions, written {decisions_n:,} "
                      f"({paragraphs_n:,} paragraphs) in {time.time() - t0:.0f}s")
    if processed is not None:
        deleted = sorted(set(processed.keys()) - seen)
        for i in range(0, len(deleted), COMMIT_EVERY):
            _delete_for_decisions(conn, set(deleted[i:i + COMMIT_EVERY]))
            conn.commit()
            if len(deleted) > COMMIT_EVERY:
                _progress(f"deleted {min(i + COMMIT_EVERY, len(deleted)):,} of {len(deleted):,} retired decisions")
        counts["deleted"] = len(deleted)
    conn.commit()
    _progress(f"done: scanned {scanned:,}, written {decisions_n:,} decisions / "
              f"{paragraphs_n:,} paragraphs, deleted {counts['deleted']:,} "
              f"in {time.time() - t0:.0f}s")
    return {
        "counts": counts,
        "decisions_written": decisions_n,
        "paragraphs_written": paragraphs_n,
    }


def _cleanup_sidecars(target: Path) -> None:
    """Remove -wal/-shm and a stale rollback -journal. A -journal left by a
    killed run is "hot": SQLite would replay its page images into whatever
    file is at the path next time (a fresh copy of the base, in the diff
    path) and silently corrupt it. It must only survive when the same file
    is reopened, which is the resume case in _bootstrap_via_full."""
    for ext in ("-wal", "-shm", "-journal"):
        p = Path(str(target) + ext)
        if p.exists():
            p.unlink()


def _bootstrap_via_full(
    *,
    decisions_db: Path,
    output_path: Path,
) -> dict:
    """No prior state — write a fresh DB by running ``extract()`` over
    every decision in decisions.db (mirrors what extract_decision_structure
    does over JSONL, but keyed on the decisions.db row set so we stay
    consistent with downstream tools).

    Resumable (2026-09-08): a production bootstrap takes ~3 h, longer than
    the step's wall clock on a busy weekday. The tmp file therefore carries
    meta.extractor_version and meta.bootstrap_in_progress from the first
    commit; if a previous attempt left such a file, this run reopens it and
    only extracts the decisions its processed_decisions does not hold yet.
    A tmp of another version, or one without the marker, is discarded.
    """
    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = output_path.with_name(f".{output_path.name}.tmp")

    resume = (
        tmp.exists()
        and _peek_extractor_version(tmp) == EFFECTIVE_EXTRACTOR_VERSION
        and _peek_meta(tmp, "bootstrap_in_progress") == "1"
    )
    if tmp.exists() and not resume:
        tmp.unlink()
        _cleanup_sidecars(tmp)

    conn = sqlite3.connect(str(tmp))
    processed: dict[str, str] | None = None
    if resume:
        processed = {
            r[0]: r[1]
            for r in conn.execute(
                "SELECT decision_id, extractor_hash FROM processed_decisions"
            )
        }
        _progress(f"bootstrap: resuming {tmp.name} with {len(processed):,} decisions already extracted")
    else:
        conn.executescript(SCHEMA)
        conn.executescript(INCREMENTAL_SCHEMA)
        _set_meta(conn, "extractor_version", EFFECTIVE_EXTRACTOR_VERSION)
        _set_meta(conn, "bootstrap_in_progress", "1")
        conn.commit()
        _progress(f"bootstrap: extracting every decision of {decisions_db.name} into {tmp.name}")

    # Read every decision once, streaming: extract and write row by row.
    streamed = _stream_extract(conn, decisions_db, processed)
    del processed
    decisions_n = streamed["decisions_written"]
    paragraphs_n = streamed["paragraphs_written"]

    # The shard builder finished with an FTS5 rebuild + optimize; trigger
    # inserts leave many segments per level, so merge them once here.
    _progress("bootstrap: optimizing erwaegungen_paragraph_fts")
    conn.execute(
        "INSERT INTO erwaegungen_paragraph_fts(erwaegungen_paragraph_fts) VALUES('optimize')"
    )
    _progress("bootstrap: optimize done")

    _set_meta(conn, "extractor_version", EFFECTIVE_EXTRACTOR_VERSION)
    _set_meta(
        conn,
        "last_full_rebuild_at",
        datetime.now(timezone.utc).isoformat(),
    )
    conn.execute("DELETE FROM meta WHERE key = 'bootstrap_in_progress'")

    conn.commit()
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.close()
    _cleanup_sidecars(tmp)

    os.replace(tmp, output_path)
    _cleanup_sidecars(output_path)

    return {
        "mode": "full_bootstrap",
        "resumed": bool(resume),
        "decisions_written": decisions_n,
        "paragraphs_written": paragraphs_n,
    }


def _refuse_without_space(output_path: Path, structure_db: Path) -> None:
    """Both paths write a sidecar-sized file next to ``output_path`` (the
    bootstrap streams one, the diff copies the base). In production the live
    sidecar is ~55 GB on the data volume while output/ itself is on a 150 GB
    root disk with ~30 GB free; a caller that hands us a tmp beside the
    symlink instead of beside the real file would fill the root disk. Refuse
    loudly instead: the caller keeps its current sidecar."""
    if not structure_db.exists():
        return
    need = int(structure_db.stat().st_size * 1.2)
    free = shutil.disk_usage(output_path.parent).free
    if free < need:
        raise SystemExit(
            f"[extract_structure] {output_path.parent} has {free / 1e9:.1f} GB free, "
            f"a sidecar rebuild needs ~{need / 1e9:.1f} GB (1.2 x {structure_db.name}); "
            "refusing to write there"
        )


def build_structure_incremental(
    *,
    decisions_db: Path,
    structure_db: Path,
    output_path: Path | None = None,
    force_full: bool = False,
) -> dict:
    """Mirrors ``build_graph_incremental`` from the reference graph
    incremental builder."""
    decisions_db = decisions_db.resolve()
    structure_db = structure_db.resolve()
    output_path = (
        output_path.resolve()
        if output_path is not None
        else structure_db.with_name(
            structure_db.stem + "_incremental" + structure_db.suffix
        )
    )
    _refuse_without_space(output_path, structure_db)

    t0 = time.time()
    stats: dict = {
        "decisions_db": str(decisions_db),
        "structure_db": str(structure_db),
        "output_path": str(output_path),
        "extractor_version": EFFECTIVE_EXTRACTOR_VERSION,
    }

    base, bootstrap_reason = _select_diff_base(
        structure_db, output_path, force_full)

    if base is None:
        stats["bootstrap_reason"] = bootstrap_reason
        full_stats = _bootstrap_via_full(
            decisions_db=decisions_db,
            output_path=output_path,
        )
        full_stats["elapsed_seconds"] = round(time.time() - t0, 2)
        stats.update(full_stats)
        return stats

    # Copy base → tmp; clean any sidecars that came along.
    stats["diff_base"] = str(base)
    tmp_path = output_path.with_name(f".{output_path.name}.tmp")
    if tmp_path.exists():
        tmp_path.unlink()
    _cleanup_sidecars(tmp_path)      # a hot -journal from a killed run must not replay into the copy
    output_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(base, tmp_path)
    _cleanup_sidecars(tmp_path)

    conn = sqlite3.connect(str(tmp_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    _ensure_state_tables(conn)

    try:
        processed: dict[str, str] = {
            r[0]: r[1]
            for r in conn.execute(
                "SELECT decision_id, extractor_hash FROM processed_decisions"
            )
        }
        _progress(f"incremental: diffing {decisions_db.name} against "
                  f"{len(processed):,} processed decisions in {base.name}")
        streamed = _stream_extract(conn, decisions_db, processed)
        del processed
        stats["counts"] = streamed["counts"]
        stats["decisions_written"] = streamed["decisions_written"]
        stats["paragraphs_written"] = streamed["paragraphs_written"]

        _set_meta(conn, "extractor_version", EFFECTIVE_EXTRACTOR_VERSION)
        _set_meta(
            conn,
            "last_incremental_run_at",
            datetime.now(timezone.utc).isoformat(),
        )

        conn.commit()
        stats["totals"] = {
            "structure": conn.execute(
                "SELECT COUNT(*) FROM structure"
            ).fetchone()[0],
            "erwaegungen_paragraphs": conn.execute(
                "SELECT COUNT(*) FROM erwaegungen_paragraph"
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
            "Incremental decision_structure rebuild. Default writes to a "
            "sibling .incremental.db file — pass --in-place to mutate the "
            "live sidecar."
        ),
    )
    parser.add_argument(
        "--decisions-db", type=Path, required=True,
        help="Source decisions.db (read-only, immutable=1)",
    )
    parser.add_argument(
        "--structure-db", type=Path, required=True,
        help="Existing decision_structure.db (state diffed against this)",
    )
    parser.add_argument(
        "--output", type=Path, default=None,
        help=("Where to write the rebuilt sidecar. Defaults to a sibling "
              "with `_incremental` suffix (shadow mode)."),
    )
    parser.add_argument(
        "--in-place", action="store_true",
        help="Overwrite --structure-db at the end. NOT default; opt-in.",
    )
    parser.add_argument(
        "--force-full", action="store_true",
        help="Skip diff path and run a full rebuild.",
    )
    args = parser.parse_args()

    output_path = args.output
    if output_path is None and args.in_place:
        output_path = args.structure_db

    stats = build_structure_incremental(
        decisions_db=args.decisions_db,
        structure_db=args.structure_db,
        output_path=output_path,
        force_full=args.force_full,
    )
    print(json.dumps(stats, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
