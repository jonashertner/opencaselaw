#!/usr/bin/env python3
"""
export_parquet.py — Export decisions to per-court Parquet files
================================================================

Reads from the deduplicated FTS5 SQLite database (preferred) or falls
back to JSONL files.  One Parquet file per court in output/dataset/.

Usage:
    python3 export_parquet.py                          # auto-detect DB
    python3 export_parquet.py --db output/decisions.db  # explicit DB
    python3 export_parquet.py --jsonl                   # force JSONL
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

logger = logging.getLogger("export_parquet")

# Explicit PyArrow schema matching the Decision model
DECISION_SCHEMA = pa.schema([
    # Identity
    pa.field("decision_id", pa.string(), nullable=False),
    pa.field("court", pa.string(), nullable=False),
    pa.field("canton", pa.string(), nullable=False),
    pa.field("chamber", pa.string(), nullable=True),
    # Coarse legal branch (zivil|straf|oeffentlich|sozialversicherung),
    # derived by branch_map.derive_branch — NULL where unknown (P1.1).
    pa.field("branch", pa.string(), nullable=True),
    # Fine proceeding classification (P1.3): wishlist taxonomy slug +
    # code family, from proceeding_map — register codes and uniform
    # courts only, NULL over guess.
    pa.field("proceeding_type", pa.string(), nullable=True),
    pa.field("procedural_code", pa.string(), nullable=True),
    # The decision under review, from the rubrum (P0.2, appeal_extract):
    # BGer only in v1, 86% of 2007+ rows.
    pa.field("appealed_court_raw", pa.string(), nullable=True),
    pa.field("appealed_date", pa.string(), nullable=True),
    pa.field("appealed_docket", pa.string(), nullable=True),
    # Case identification
    pa.field("docket_number", pa.string(), nullable=False),
    pa.field("docket_number_2", pa.string(), nullable=True),
    pa.field("decision_date", pa.string(), nullable=True),
    pa.field("publication_date", pa.string(), nullable=True),
    # BGE-bound signal: BGer Neuheiten "*" ("für die Publikation vorgesehen").
    pa.field("marked_for_publication", pa.bool_(), nullable=True),
    # Content
    pa.field("language", pa.string(), nullable=False),
    pa.field("title", pa.string(), nullable=True),
    pa.field("legal_area", pa.string(), nullable=True),
    pa.field("regeste", pa.string(), nullable=True),
    pa.field("abstract_de", pa.string(), nullable=True),
    pa.field("abstract_fr", pa.string(), nullable=True),
    pa.field("abstract_it", pa.string(), nullable=True),
    pa.field("full_text", pa.string(), nullable=False),
    # Metadata
    pa.field("outcome", pa.string(), nullable=True),
    pa.field("decision_type", pa.string(), nullable=True),
    pa.field("judges", pa.string(), nullable=True),
    pa.field("clerks", pa.string(), nullable=True),
    pa.field("collection", pa.string(), nullable=True),
    pa.field("appeal_info", pa.string(), nullable=True),
    # References
    pa.field("source_url", pa.string(), nullable=False),
    pa.field("pdf_url", pa.string(), nullable=True),
    pa.field("bge_reference", pa.string(), nullable=True),
    pa.field("cited_decisions", pa.string(), nullable=True),  # JSON array as string
    # Provenance
    pa.field("scraped_at", pa.string(), nullable=True),
    pa.field("external_id", pa.string(), nullable=True),
    pa.field("source", pa.string(), nullable=True),           # "entscheidsuche", "direct_scrape"
    pa.field("source_id", pa.string(), nullable=True),        # Source-specific ID (e.g. Signatur)
    pa.field("source_spider", pa.string(), nullable=True),    # Spider/scraper name at source
    pa.field("content_hash", pa.string(), nullable=True),     # MD5 of full_text for dedup
    # Computed fields
    pa.field("has_full_text", pa.bool_(), nullable=False),
    pa.field("text_length", pa.int32(), nullable=False),
])


def _coerce_bool(v):
    """SQLite has no boolean type; map a stored 0/1/NULL (or '0'/'1' string) to
    a real bool or None so a pa.bool_() Parquet column accepts it. Without this,
    pa.Table.from_pylist raises ArrowInvalid converting int 0 to boolean."""
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    return str(v).strip().lower() in ("1", "true", "t", "yes")


def normalize_row(row: dict) -> dict:
    """Normalize a decision dict for Parquet export."""
    # Convert date/datetime objects to ISO strings
    for key in ("decision_date", "publication_date", "scraped_at"):
        val = row.get(key)
        if isinstance(val, (date, datetime)):
            row[key] = val.isoformat()
        elif val == "None" or val is None:
            row[key] = None

    # Ensure non-nullable fields have defaults
    if not row.get("decision_id"):
        row["decision_id"] = "unknown"
    if not row.get("court"):
        row["court"] = "unknown"
    if not row.get("canton"):
        row["canton"] = "XX"
    if not row.get("docket_number"):
        row["docket_number"] = "unknown"
    if row.get("decision_date") == "1970-01-01":
        row["decision_date"] = None  # Don't invent dates
    if not row.get("language"):
        row["language"] = "de"
    if not row.get("full_text"):
        row["full_text"] = ""
    if not row.get("source_url"):
        row["source_url"] = ""

    # Ensure cited_decisions is a JSON string
    cited = row.get("cited_decisions", [])
    if isinstance(cited, list):
        row["cited_decisions"] = json.dumps(cited)

    # Map entscheidsuche-specific provenance fields to generic names
    if row.get("entscheidsuche_signatur") and not row.get("source_id"):
        row["source_id"] = row["entscheidsuche_signatur"]
    if row.get("entscheidsuche_spider") and not row.get("source_spider"):
        row["source_spider"] = row["entscheidsuche_spider"]

    # Computed fields
    full_text = row.get("full_text") or ""
    row["has_full_text"] = bool(full_text.strip())
    row["text_length"] = len(full_text)
    # Chamber fill (P1.2) then coarse branch (P1.1) — derived when the source
    # row doesn't carry them (JSONL shards never do; decisions.db does from
    # 2026-07-03 builds). Order matters: the filled code feeds branch rules.
    from branch_map import derive_branch, docket_chamber_code
    if not row.get("chamber"):
        row["chamber"] = docket_chamber_code(row.get("court"),
                                             row.get("docket_number"))
    if not row.get("branch"):
        row["branch"] = derive_branch(row.get("court"), row.get("chamber"),
                                      row.get("docket_number"))
    # Fine proceeding layer (P1.3) — same derive-when-missing pattern.
    if not row.get("proceeding_type"):
        from proceeding_map import derive_proceeding
        slug, pcode = derive_proceeding(row.get("court"), row.get("chamber"),
                                        row.get("docket_number"))
        row["proceeding_type"] = slug
        row["procedural_code"] = row.get("procedural_code") or pcode
    # Appealed decision (P0.2) — BGer rubrum extraction when missing.
    if row.get("court") == "bger" and not row.get("appealed_date"):
        from appeal_extract import extract_appealed
        ap = extract_appealed(full_text)
        if ap:
            row["appealed_court_raw"] = ap["appealed_court_raw"]
            row["appealed_date"] = ap["appealed_date"]
            row["appealed_docket"] = ap["appealed_docket"]
    # marked_for_publication is stored as SQLite 0/1/NULL but the schema is bool.
    row["marked_for_publication"] = _coerce_bool(row.get("marked_for_publication"))

    # Ensure all schema fields exist
    for field in DECISION_SCHEMA:
        if field.name not in row:
            row[field.name] = None

    return row


def load_decisions(input_dir: Path) -> dict[str, dict]:
    """Load all JSONL files, deduplicating by decision_id (keeps first-seen)."""
    decisions: dict[str, dict] = {}
    jsonl_files = sorted(input_dir.glob("*.jsonl"))

    if not jsonl_files:
        logger.warning(f"No JSONL files found in {input_dir}")
        return decisions

    for jsonl_file in jsonl_files:
        count = 0
        with open(jsonl_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                    did = row.get("decision_id")
                    if did and did not in decisions:
                        decisions[did] = row
                        count += 1
                except json.JSONDecodeError:
                    continue
        logger.debug(f"  {jsonl_file.name}: {count} decisions")

    logger.info(f"Loaded {len(decisions)} unique decisions from {len(jsonl_files)} files")
    return decisions


BATCH_SIZE = 5000  # rows per batch to stay under memory limits


def export_parquet(input_dir: Path, output_dir: Path) -> dict[str, int]:
    """Export decisions to per-court Parquet files. Returns {court: count}.

    Two-pass approach to stay memory-efficient:
    1. First pass: collect all unique decision_ids per court (just IDs, not data)
    2. Second pass: stream data, write per-court Parquet using ParquetWriter

    This avoids loading full texts into memory.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    jsonl_files = sorted(input_dir.glob("*.jsonl"))

    if not jsonl_files:
        logger.warning(f"No JSONL files found in {input_dir}")
        return {}

    schema_fields = {f.name for f in DECISION_SCHEMA}
    results = {}

    # Global dedup: keep first-seen immutable record for each decision_id.
    global_seen: set[str] = set()

    # Use per-court ParquetWriter objects for streaming writes
    writers: dict[str, pq.ParquetWriter] = {}

    try:
        for jsonl_file in jsonl_files:
            file_count = 0
            batch_by_court: dict[str, list[dict]] = {}

            with open(jsonl_file, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                        did = row.get("decision_id")
                        if not did or did in global_seen:
                            continue
                        global_seen.add(did)

                        # Skip rows missing required fields (match FTS5 constraints)
                        if not all(row.get(k) for k in ("court", "canton", "docket_number", "language")):
                            missing = [k for k in ("court", "canton", "docket_number", "language") if not row.get(k)]
                            logger.warning(f"Skipping {did}: missing {', '.join(missing)}")
                            continue

                        court = row.get("court", "unknown")
                        batch_by_court.setdefault(court, []).append(row)
                        file_count += 1

                        # Flush per-court batches when they get large
                        if len(batch_by_court.get(court, [])) >= BATCH_SIZE:
                            rows = batch_by_court.pop(court)
                            _write_rows(rows, court, output_dir, writers, schema_fields)
                            results[court] = results.get(court, 0) + len(rows)
                    except json.JSONDecodeError:
                        continue

            # Flush remaining rows for this file
            for court, rows in batch_by_court.items():
                _write_rows(rows, court, output_dir, writers, schema_fields)
                results[court] = results.get(court, 0) + len(rows)

            if file_count:
                logger.info(f"  Processed {jsonl_file.name}: {file_count} decisions")

    finally:
        # Close all writers and atomically rename .tmp → .parquet
        for court, writer in writers.items():
            writer.close()
            tmp_path = output_dir / f"{court}.parquet.tmp"
            final_path = output_dir / f"{court}.parquet"
            if tmp_path.exists():
                os.replace(str(tmp_path), str(final_path))
            logger.info(f"  {court}: {results.get(court, 0)} total")

    logger.info(f"Exported {sum(results.values())} decisions across {len(results)} courts")
    return results


def _write_rows(
    rows: list[dict],
    court: str,
    output_dir: Path,
    writers: dict[str, pq.ParquetWriter],
    schema_fields: set,
):
    """Write rows to a per-court ParquetWriter (streaming, no read-back)."""
    normalized = [normalize_row(row) for row in rows]
    clean_rows = [{k: r.get(k) for k in schema_fields} for r in normalized]
    table = pa.Table.from_pylist(clean_rows, schema=DECISION_SCHEMA)

    if court not in writers:
        filepath = output_dir / f"{court}.parquet.tmp"
        writers[court] = pq.ParquetWriter(str(filepath), DECISION_SCHEMA, compression="zstd")

    writers[court].write_table(table)


# Strasbourg judgment text is © ECHR-CEDH, not CC0. The Court's reuse terms
# are conditioned on free-of-charge information/education use and carve out
# commercial use; art. 5 URG (Swiss official texts are not copyrightable) is
# jurisdiction-specific and cannot reach a body asserting its own copyright.
# CC0 is a rights WAIVER — we cannot waive rights we do not hold. Until
# written Registry permission is on file these courts are excluded from the
# CC0 mirror at the SOURCE of the export, not filtered downstream.
# The supporting legal review is archived in the private maintenance workspace.
EXCLUDED_COURTS = frozenset({
    "ecthr_chamber",
    "ecthr_committee",
    "ecthr_grand_chamber",
    "hudoc_ch",
    "bge_egmr",
})


def export_from_db(db_path: Path, output_dir: Path) -> dict[str, int]:
    """Export decisions from the deduplicated FTS5 SQLite DB to Parquet.

    Reads from the database built by build_fts5.py, ensuring Parquet files
    match the search index exactly (same dedup, same row count).
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(f"file:{db_path}?immutable=1", uri=True)
    conn.row_factory = sqlite3.Row

    schema_fields = {f.name for f in DECISION_SCHEMA}
    results: dict[str, int] = {}
    writers: dict[str, pq.ParquetWriter] = {}

    try:
        total = conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
        courts = [r[0] for r in conn.execute(
            "SELECT DISTINCT court FROM decisions ORDER BY court"
        ).fetchall()]
        held_back = sorted(c for c in courts if c in EXCLUDED_COURTS)
        courts = [c for c in courts if c not in EXCLUDED_COURTS]
        if held_back:
            logger.warning(
                "Excluding %d court(s) from the CC0 export (non-CC0 licence): %s",
                len(held_back), ", ".join(held_back),
            )
        logger.info(f"Exporting {total} decisions from {len(courts)} courts")

        for court in courts:
            # flushed_count tracks rows that have been _write_rows'd to parquet
            # for THIS court. The defensive fallback uses it to skip already-
            # flushed rowids when the batched cursor crashes mid-court, so no
            # duplicates land in the parquet output. ORDER BY rowid below is
            # essential — both the fast path and the fallback's rowid list
            # must iterate in the same order for the skip to be correct.
            flushed_count = 0
            try:
                cursor = conn.execute(
                    "SELECT * FROM decisions WHERE court = ? ORDER BY rowid",
                    (court,),
                )
                col_names = [desc[0] for desc in cursor.description]

                batch: list[dict] = []
                for row_tuple in cursor:
                    d = dict(zip(col_names, row_tuple))
                    batch.append(d)

                    if len(batch) >= BATCH_SIZE:
                        _write_rows(batch, court, output_dir, writers, schema_fields)
                        flushed_count += len(batch)
                        batch = []

                if batch:
                    _write_rows(batch, court, output_dir, writers, schema_fields)
                    flushed_count += len(batch)

                results[court] = flushed_count
                logger.info(f"  {court}: {flushed_count} decisions")
            except sqlite3.DatabaseError as e:
                # Batched cursor died mid-court. Defensive against single-row
                # page corruption (2026-05-16 incident, ecthr_chamber_29447_17):
                # fall back to per-rowid fetches for THIS court, skipping the
                # rowids whose batches have already been flushed AND the row
                # whose individual SELECT raises. Avoids both losing an entire
                # court's parquet AND writing duplicates from rebuilding rows
                # we already flushed.
                logger.warning(
                    f"  {court}: batched cursor hit DatabaseError "
                    f"(already flushed {flushed_count} rows to parquet); "
                    f"falling back to per-rowid for the remainder: {e}"
                )
                # rowids of this court via the court index — cheap, no overflow read
                # ORDER BY rowid matches the original cursor's iteration order so
                # the [flushed_count:] slice is the correct "remainder".
                all_rowids = [r[0] for r in conn.execute(
                    "SELECT rowid FROM decisions WHERE court = ? ORDER BY rowid",
                    (court,),
                )]
                remaining_rowids = all_rowids[flushed_count:]
                retry_batch: list[dict] = []
                skipped = 0
                col_names_retry = None
                for rowid in remaining_rowids:
                    try:
                        cur2 = conn.execute(
                            "SELECT * FROM decisions WHERE rowid = ?", (rowid,)
                        )
                        row = cur2.fetchone()
                        if row is None:
                            continue
                        if col_names_retry is None:
                            col_names_retry = [desc[0] for desc in cur2.description]
                        d = dict(zip(col_names_retry, row))
                        retry_batch.append(d)
                        if len(retry_batch) >= BATCH_SIZE:
                            _write_rows(retry_batch, court, output_dir, writers, schema_fields)
                            flushed_count += len(retry_batch)
                            retry_batch = []
                    except sqlite3.DatabaseError as e2:
                        skipped += 1
                        logger.error(
                            f"  {court}: SKIPPING rowid={rowid} — page "
                            f"corruption (likely overflow chain): {e2}"
                        )
                        continue
                if retry_batch:
                    _write_rows(retry_batch, court, output_dir, writers, schema_fields)
                    flushed_count += len(retry_batch)
                results[court] = flushed_count
                logger.info(
                    f"  {court}: {flushed_count} decisions "
                    f"(recovered via per-rowid, skipped {skipped})"
                )

    finally:
        conn.close()
        for court, writer in writers.items():
            writer.close()
            tmp_path = output_dir / f"{court}.parquet.tmp"
            final_path = output_dir / f"{court}.parquet"
            if tmp_path.exists():
                os.replace(str(tmp_path), str(final_path))

    # Remove stale parquet files for courts no longer in DB
    existing_pq = {p.stem for p in output_dir.glob("*.parquet")}
    stale = existing_pq - set(results.keys())
    for court_name in stale:
        stale_path = output_dir / f"{court_name}.parquet"
        stale_path.unlink()
        logger.info(f"  Removed stale {court_name}.parquet")

    logger.info(f"Exported {sum(results.values())} decisions across {len(results)} courts")
    return results


# ── Citation-graph exports (backlog P2.4) ───────────────────────────────
# The 8.65M resolved decision-to-decision edges and 11.86M statute
# references existed only inside reference_graph.db (MCP-only). Exported as
# self-contained parquet tables so researchers get the graph without
# re-mining citations from text. Written to <output>/graph/ — deliberately
# NOT into the load_dataset data/ directory (a different-schema parquet
# there breaks the HF dataset config); publish Step 4 uploads graph/
# to its own repo path.

CITATION_EDGE_SCHEMA = pa.schema([
    pa.field("source_decision_id", pa.string(), nullable=False),
    pa.field("target_decision_id", pa.string(), nullable=False),
    pa.field("target_ref", pa.string(), nullable=True),      # raw citation string
    pa.field("match_type", pa.string(), nullable=True),
    pa.field("confidence_score", pa.float32(), nullable=True),
])

STATUTE_REF_SCHEMA = pa.schema([
    pa.field("decision_id", pa.string(), nullable=False),
    pa.field("law_code", pa.string(), nullable=True),
    pa.field("article", pa.string(), nullable=True),
    pa.field("paragraph", pa.string(), nullable=True),
    pa.field("mention_count", pa.int32(), nullable=True),
])


def _stream_query_to_parquet(conn, sql: str, schema, out_path: Path,
                             batch_size: int = 50_000) -> int:
    """Stream a query into a parquet file (atomic .tmp + replace)."""
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    writer = pq.ParquetWriter(str(tmp), schema, compression="zstd",
                              use_dictionary=True)
    names = [f.name for f in schema]
    # SQLite has no boolean type; Arrow refuses int->bool, so coerce 0/1
    # (and NULL) for bool-typed schema fields here.
    bool_idx = {i for i, f in enumerate(schema) if pa.types.is_boolean(f.type)}
    total = 0
    try:
        cur = conn.execute(sql)
        while True:
            rows = cur.fetchmany(batch_size)
            if not rows:
                break
            batch = {
                n: [(None if r[i] is None else bool(r[i])) for r in rows]
                if i in bool_idx else [r[i] for r in rows]
                for i, n in enumerate(names)
            }
            writer.write_table(pa.Table.from_pydict(batch, schema=schema))
            total += len(rows)
    finally:
        writer.close()
    os.replace(tmp, out_path)
    return total


def export_citation_graph(graph_db: Path, output_dir: Path) -> dict[str, int]:
    """Export resolved citation edges + statute references from
    reference_graph.db. Returns row counts; empty dict if the DB is absent
    (dev machines / tests) — the caller treats that as a clean skip."""
    if not graph_db.exists():
        logger.info(f"reference_graph.db not found at {graph_db} — skipping graph export")
        return {}
    out = output_dir / "graph"
    out.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(f"file:{graph_db}?mode=ro&immutable=1", uri=True)
    try:
        n_edges = _stream_query_to_parquet(
            conn,
            "SELECT source_decision_id, target_decision_id, target_ref, "
            "match_type, CAST(confidence_score AS REAL) "
            "FROM citation_targets WHERE target_decision_id IS NOT NULL",
            CITATION_EDGE_SCHEMA, out / "citations.parquet")
        logger.info(f"  graph/citations.parquet: {n_edges} resolved edges")
        n_refs = _stream_query_to_parquet(
            conn,
            "SELECT ds.decision_id, s.law_code, s.article, s.paragraph, "
            "CAST(ds.mention_count AS INTEGER) "
            "FROM decision_statutes ds JOIN statutes s USING (statute_id)",
            STATUTE_REF_SCHEMA, out / "statute_references.parquet")
        logger.info(f"  graph/statute_references.parquet: {n_refs} references")
    finally:
        conn.close()
    return {"citations": n_edges, "statute_references": n_refs}


# ── Decision-structure exports (backlog P1.4) ───────────────────────────
# The structure sidecar (Rubrum/Sachverhalt/Erwägungen/Dispositiv extraction
# + per-paragraph segmentation with E-numbers) was MCP-only. Exported so
# researchers can do dispositiv-anchored extraction without re-parsing:
# structure.parquet = lean per-decision metadata; erwaegungen_paragraphs
# .parquet = the segmentation WITH verbatim text (the anchoring substrate).

STRUCTURE_META_SCHEMA = pa.schema([
    pa.field("decision_id", pa.string(), nullable=False),
    pa.field("court", pa.string(), nullable=True),
    pa.field("language", pa.string(), nullable=True),
    pa.field("has_sachverhalt", pa.bool_(), nullable=False),
    pa.field("has_erwaegungen", pa.bool_(), nullable=False),
    pa.field("has_dispositiv", pa.bool_(), nullable=False),
    pa.field("sachverhalt_method", pa.string(), nullable=True),
    pa.field("erwaegungen_method", pa.string(), nullable=True),
    pa.field("dispositiv_method", pa.string(), nullable=True),
    pa.field("erwaegungen_paragraph_count", pa.int32(), nullable=True),
])

PARAGRAPH_SCHEMA = pa.schema([
    pa.field("decision_id", pa.string(), nullable=False),
    pa.field("e_number", pa.string(), nullable=True),
    pa.field("depth", pa.int32(), nullable=True),
    pa.field("parent", pa.string(), nullable=True),
    pa.field("text", pa.string(), nullable=False),
])


def export_decision_structure(structure_db: Path, output_dir: Path,
                              include_paragraphs: bool = False) -> dict[str, int]:
    """Export the structure sidecar. Clean skip if the DB is absent.

    structure.parquet (per-decision metadata, ~7 MB) is cheap enough for
    every run. erwaegungen_paragraphs.parquet measured 4.8 GB on the full
    corpus (9.07M paragraphs WITH text, 10 min) — re-uploading that nightly
    for slowly-changing data is waste, so it is opt-in
    (``include_paragraphs``; the publish pipeline passes it on Sundays,
    aligned with the weekly full-snapshot cadence)."""
    if not structure_db.exists():
        logger.info(f"decision_structure.db not found at {structure_db} — skipping structure export")
        return {}
    out = output_dir / "structure"
    out.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(f"file:{structure_db}?mode=ro&immutable=1", uri=True)
    try:
        n_meta = _stream_query_to_parquet(
            conn,
            "SELECT decision_id, court, language, "
            "CAST(sachverhalt IS NOT NULL AND sachverhalt != '' AS INTEGER), "
            "CAST(erwaegungen IS NOT NULL AND erwaegungen != '' AS INTEGER), "
            "CAST(dispositiv IS NOT NULL AND dispositiv != '' AS INTEGER), "
            "sachverhalt_method, erwaegungen_method, dispositiv_method, "
            "CAST(erwaegungen_paragraph_count AS INTEGER) FROM structure",
            STRUCTURE_META_SCHEMA, out / "structure.parquet")
        logger.info(f"  structure/structure.parquet: {n_meta} decisions")
        counts = {"structure": n_meta}
        if include_paragraphs:
            n_para = _stream_query_to_parquet(
                conn,
                "SELECT decision_id, e_number, CAST(depth AS INTEGER), parent, text "
                "FROM erwaegungen_paragraph WHERE text IS NOT NULL AND text != ''",
                PARAGRAPH_SCHEMA, out / "erwaegungen_paragraphs.parquet")
            logger.info(f"  structure/erwaegungen_paragraphs.parquet: {n_para} paragraphs")
            counts["erwaegungen_paragraphs"] = n_para
    finally:
        conn.close()
    return counts


def main():
    parser = argparse.ArgumentParser(description="Export decisions to Parquet")
    parser.add_argument(
        "--db", type=str, default="output/decisions.db",
        help="FTS5 SQLite database (preferred source, default: output/decisions.db)",
    )
    parser.add_argument(
        "--jsonl", action="store_true",
        help="Force reading from JSONL files instead of the database",
    )
    parser.add_argument(
        "--input", type=str, default="output/decisions",
        help="Input directory for JSONL files (fallback, default: output/decisions)",
    )
    parser.add_argument(
        "--output", type=str, default="output/dataset",
        help="Output directory for Parquet files (default: output/dataset)",
    )
    parser.add_argument(
        "--graph-db", type=str, default="output/reference_graph.db",
        help="reference_graph.db for the citation-graph export "
             "(skipped cleanly if absent)",
    )
    parser.add_argument(
        "--structure-db", type=str, default="output/decision_structure.db",
        help="decision_structure.db for the sections export "
             "(skipped cleanly if absent)",
    )
    parser.add_argument(
        "--structure-paragraphs", action="store_true",
        help="also export erwaegungen_paragraphs.parquet (4.8 GB, ~10 min; "
             "weekly cadence — publish passes this on Sundays)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    db_path = Path(args.db)
    if not args.jsonl and db_path.exists():
        logger.info(f"Reading from FTS5 database: {db_path}")
        results = export_from_db(db_path, Path(args.output))
    else:
        if not args.jsonl:
            logger.warning(f"No database at {db_path}, falling back to JSONL")
        results = export_parquet(Path(args.input), Path(args.output))
    graph_counts = export_citation_graph(Path(args.graph_db), Path(args.output))
    structure_counts = export_decision_structure(
        Path(args.structure_db), Path(args.output),
        include_paragraphs=args.structure_paragraphs)
    if structure_counts:
        print(f"Structure: {structure_counts.get('structure', 0)} decisions, "
              f"{structure_counts.get('erwaegungen_paragraphs', 0)} paragraphs")
    if results:
        total = sum(results.values())
        print(f"\nExported {total} decisions to {len(results)} Parquet files")
        if graph_counts:
            print(f"Graph: {graph_counts.get('citations', 0)} citation edges, "
                  f"{graph_counts.get('statute_references', 0)} statute references")
    else:
        print("No decisions exported", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
