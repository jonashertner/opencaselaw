#!/usr/bin/env python3
"""
Build a local reference graph (decision->decision, decision->statute) from JSONL.

This enables:
- citation traversal ("what cites this decision?")
- statute traversal ("which decisions mention Art. 8 EMRK?")
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from datetime import date
from pathlib import Path
from typing import Iterator

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from search_stack.reference_extraction import (  # noqa: E402
    extract_case_citations,
    extract_prior_instance,
    extract_statute_references,
)


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS decisions (
    decision_id TEXT PRIMARY KEY,
    docket_number TEXT,
    docket_norm TEXT,
    court TEXT,
    canton TEXT,
    language TEXT,
    decision_date TEXT
);

CREATE INDEX IF NOT EXISTS idx_decisions_docket_norm ON decisions(docket_norm);
CREATE INDEX IF NOT EXISTS idx_decisions_court ON decisions(court);
CREATE INDEX IF NOT EXISTS idx_decisions_date ON decisions(decision_date);

CREATE TABLE IF NOT EXISTS statutes (
    statute_id TEXT PRIMARY KEY,
    law_code TEXT NOT NULL,
    article TEXT NOT NULL,
    paragraph TEXT
);

CREATE INDEX IF NOT EXISTS idx_statutes_law_article ON statutes(law_code, article);

CREATE TABLE IF NOT EXISTS decision_statutes (
    decision_id TEXT NOT NULL,
    statute_id TEXT NOT NULL,
    mention_count INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (decision_id, statute_id),
    FOREIGN KEY (decision_id) REFERENCES decisions(decision_id),
    FOREIGN KEY (statute_id) REFERENCES statutes(statute_id)
);

CREATE INDEX IF NOT EXISTS idx_decision_statutes_statute ON decision_statutes(statute_id);

CREATE TABLE IF NOT EXISTS decision_citations (
    source_decision_id TEXT NOT NULL,
    target_ref TEXT NOT NULL,
    target_type TEXT NOT NULL, -- decision|bge|docket
    mention_count INTEGER NOT NULL DEFAULT 1,
    is_prior_instance INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (source_decision_id, target_ref),
    FOREIGN KEY (source_decision_id) REFERENCES decisions(decision_id)
);

CREATE INDEX IF NOT EXISTS idx_decision_citations_target_ref ON decision_citations(target_ref);

CREATE TABLE IF NOT EXISTS citation_targets (
    source_decision_id TEXT NOT NULL,
    target_ref TEXT NOT NULL,
    target_decision_id TEXT NOT NULL,
    match_type TEXT NOT NULL DEFAULT 'docket_norm',
    confidence_score REAL NOT NULL DEFAULT 0.5,
    PRIMARY KEY (source_decision_id, target_ref, target_decision_id),
    FOREIGN KEY (source_decision_id, target_ref)
        REFERENCES decision_citations(source_decision_id, target_ref),
    FOREIGN KEY (target_decision_id) REFERENCES decisions(decision_id)
);

CREATE INDEX IF NOT EXISTS idx_citation_targets_target_decision_id
    ON citation_targets(target_decision_id);

-- Further docket forms a decision is cited under, read from its own caption
-- (ZH "Geschäfts-Nr.: LA200010-O/U" for a row stored as "LA200010"). Used
-- only for references the exact docket_norm match left unresolved.
CREATE TABLE IF NOT EXISTS docket_aliases (
    decision_id TEXT NOT NULL,
    docket_norm TEXT NOT NULL,
    PRIMARY KEY (decision_id, docket_norm)
);

CREATE INDEX IF NOT EXISTS idx_docket_aliases_norm ON docket_aliases(docket_norm);
"""


_BGE_PREFIX_PAT = re.compile(
    r"^(BGE|ATF|DTF)[\s_]+(\d{1,4})[\s_]+([IVX]{1,4})[\s_]+(\d{1,4})$"
)


def _docket_norm(value: str | None) -> str:
    if not value:
        return ""
    raw = value.strip().upper()
    # Preserve canonical BGE/ATF/DTF citation form ("BGE 147 I 268", single
    # spaces).  The citation extractor emits BGE/ATF/DTF refs in this form so
    # decision docket_norms must match bytewise.
    m = _BGE_PREFIX_PAT.match(raw)
    if m:
        return f"{m.group(1)} {m.group(2)} {m.group(3)} {m.group(4)}"
    # Standard normalization: unify hyphens, periods, slashes, AND spaces to
    # underscores.  Replacing spaces fixes a 2007-2009 BGer drift where the
    # docket "8C 862/2008" was stored as docket_norm "8C 862_2008" (with a
    # surviving space) while the citation extractor produced "8C_862_2008",
    # so the JOIN always missed.
    out = (
        raw
        .replace("-", "_")
        .replace(".", "_")
        .replace("/", "_")
        .replace(" ", "_")
    )
    while "__" in out:
        out = out.replace("__", "_")
    out = out.strip("_")
    # Bare BGE-style vol_div_page (e.g. "79_IV_170") → "79 IV 170" (canonical
    # space-separated BGE form), matching how the citation extractor emits
    # bare BGE refs without a prefix.
    if re.match(r"^\d{1,3}_[IVX]{1,4}_\d{1,4}$", out):
        out = out.replace("_", " ")
    return out


# ZH caption: "Geschäfts-Nr.: LA200010-O/U", "AH240003-G/U", "AN210006-L/U-anonymisiert".
_ZH_CAPTION_DOCKET_RE = re.compile(
    r"Gesch(?:ä|ae)fts-?\s*Nr\.?\s*:?\s*([A-Z]{2}\d{6})\s*-\s*([A-Z])"
    r"(?:\s*/\s*([A-Z]{1,2})(?![A-Za-z]))?"
)


def caption_docket_aliases(
    *, canton: str | None, docket_number: str | None, full_text: str | None
) -> list[str]:
    """Docket forms a ZH ruling is cited under that its stored docket lacks.

    ZH Geschäftsnummern carry a court letter and a document suffix
    ("LA200010-O/U"; the letter names the court, O = Obergericht, L = Zürich,
    G = Meilen, ...). The rows are stored with or without them depending on
    the source ("LA200010", "AH240003", "AN210006-L"), while a citing ruling
    writes what the caption says: BGer "(LA200010-O/U)", Obergericht
    "(AH240003-G)". Read from the decision's own caption, so the letter is the
    one the court printed, never a guess, and only when the caption's number
    is the stored one.
    """
    if (canton or "").upper() != "ZH" or not full_text:
        return []
    m = _ZH_CAPTION_DOCKET_RE.search(full_text[:1500])
    if not m:
        return []
    core, letter, sub = m.group(1), m.group(2), m.group(3)
    stored = _docket_norm(docket_number)
    if not stored.startswith(core):
        return []
    forms = [f"{core}_{letter}"]
    if sub:
        forms.append(f"{core}_{letter}_{sub}")
    return [f for f in forms if f != stored]


def insert_docket_aliases(conn: sqlite3.Connection, row: dict) -> None:
    for alias in caption_docket_aliases(
        canton=row.get("canton"),
        docket_number=row.get("docket_number"),
        full_text=row.get("full_text"),
    ):
        conn.execute(
            "INSERT OR IGNORE INTO docket_aliases (decision_id, docket_norm) VALUES (?, ?)",
            (row.get("decision_id"), alias),
        )


# BStGer two-letter prefixes
_BSTGER_PREFIXES = frozenset({
    "BB", "BG", "BH", "BK", "BN", "BP",
    "CA", "CB", "CR", "RR",
    "SK", "SN", "SP", "TP",
})


def _infer_court_from_docket(docket_norm: str) -> str | None:
    """Infer the likely court from a normalized docket reference.

    Swiss federal courts use distinctive docket prefixes:
    - BGer: digit+letter (e.g. 6B_1234_2025, 4A_291_2017)
    - BVGer: single letter A-F (e.g. E_5783_2024, D_8226_2025)
    - BStGer: two-letter codes (e.g. SK_2025_1234, BB_2024_100)
    """
    if not docket_norm:
        return None
    # BGer: starts with digit + uppercase letter + underscore + digit
    if re.match(r"^[1-9][A-Z]_\d", docket_norm):
        return "bger"
    # BVGer: single letter A-F + underscore + digits + underscore + 4-digit year
    if re.match(r"^[A-F]_\d{1,6}_\d{4}$", docket_norm):
        return "bvger"
    # BStGer: known two-letter prefix
    m = re.match(r"^([A-Z]{2})_", docket_norm)
    if m and m.group(1) in _BSTGER_PREFIXES:
        return "bstger"
    return None


def _parse_iso_date(value: str | None) -> date | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _citation_confidence(
    *,
    source_court: str | None,
    source_canton: str | None,
    source_date: str | None,
    target_court: str | None,
    target_canton: str | None,
    target_date: str | None,
    target_ref: str | None = None,
    candidate_rank: int,
    candidate_count: int,
) -> float:
    score = 0.55

    # Docket pattern implies court — strongest disambiguation signal.
    # E.g. "4A_291_2017" is almost certainly BGer, "E_5783_2024" is BVGer.
    if target_ref:
        inferred_court = _infer_court_from_docket(target_ref)
        if inferred_court and target_court:
            if target_court == inferred_court:
                score += 0.20
            else:
                score -= 0.20

    if source_canton and target_canton and source_canton == target_canton:
        score += 0.10
    if source_court and target_court and source_court == target_court:
        score += 0.08

    src_dt = _parse_iso_date(source_date)
    tgt_dt = _parse_iso_date(target_date)
    if src_dt and tgt_dt:
        delta_days = (src_dt - tgt_dt).days
        if delta_days >= 0:
            score += 0.15
        else:
            score -= 0.15

        abs_days = abs(delta_days)
        if abs_days <= 365:
            score += 0.10
        elif abs_days <= (3 * 365):
            score += 0.05

    if candidate_rank == 1:
        score += 0.05
    elif candidate_rank == 2:
        score += 0.02

    if candidate_count > 1:
        score -= min(0.15, 0.03 * (candidate_count - 1))

    return max(0.05, min(0.99, round(score, 4)))


def _resolve_via_docket_aliases(conn: sqlite3.Connection, insert_sql: str) -> None:
    """Resolve docket refs the exact pass left unmatched through
    ``docket_aliases`` (caption forms such as ``LA200010_O_U``). Additive:
    a ref that already has a target is never touched."""
    has_table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'docket_aliases'"
    ).fetchone()
    if not has_table:
        return
    # CROSS JOIN pins docket_aliases (tens of thousands of rows) as the outer
    # loop, so each alias probes decision_citations through its target_ref
    # index instead of the planner scanning millions of citation rows.
    rows = conn.execute(
        """
        WITH alias_matches AS (
            SELECT
                dc.source_decision_id,
                dc.target_ref,
                sd.court AS source_court,
                sd.canton AS source_canton,
                sd.decision_date AS source_date,
                td.decision_id AS target_decision_id,
                td.court AS target_court,
                td.canton AS target_canton,
                td.decision_date AS target_date,
                ROW_NUMBER() OVER (
                    PARTITION BY dc.source_decision_id, dc.target_ref
                    ORDER BY td.decision_date DESC, td.decision_id
                ) AS candidate_rank,
                COUNT(*) OVER (
                    PARTITION BY dc.source_decision_id, dc.target_ref
                ) AS candidate_count
            FROM docket_aliases da
            CROSS JOIN decision_citations dc
            JOIN decisions td ON td.decision_id = da.decision_id
            LEFT JOIN decisions sd ON sd.decision_id = dc.source_decision_id
            WHERE dc.target_ref = da.docket_norm
              AND dc.target_type = 'docket'
              AND td.decision_id <> dc.source_decision_id
              AND NOT EXISTS (
                  SELECT 1 FROM citation_targets ct
                   WHERE ct.source_decision_id = dc.source_decision_id
                     AND ct.target_ref = dc.target_ref
              )
        )
        SELECT * FROM alias_matches
        """
    ).fetchall()
    payload = [
        (
            row["source_decision_id"],
            row["target_ref"],
            row["target_decision_id"],
            "docket_alias",
            _citation_confidence(
                source_court=row["source_court"],
                source_canton=row["source_canton"],
                source_date=row["source_date"],
                target_court=row["target_court"],
                target_canton=row["target_canton"],
                target_date=row["target_date"],
                target_ref=row["target_ref"],
                candidate_rank=int(row["candidate_rank"] or 1),
                candidate_count=int(row["candidate_count"] or 1),
            ),
        )
        for row in rows
    ]
    conn.executemany(insert_sql, payload)


def _resolve_citation_targets(conn: sqlite3.Connection) -> None:
    cursor = conn.execute(
        """
        WITH candidate_matches AS (
            SELECT
                dc.source_decision_id,
                dc.target_ref,
                sd.court AS source_court,
                sd.canton AS source_canton,
                sd.decision_date AS source_date,
                td.decision_id AS target_decision_id,
                td.court AS target_court,
                td.canton AS target_canton,
                td.decision_date AS target_date,
                ROW_NUMBER() OVER (
                    PARTITION BY dc.source_decision_id, dc.target_ref
                    ORDER BY td.decision_date DESC, td.decision_id
                ) AS candidate_rank,
                COUNT(*) OVER (
                    PARTITION BY dc.source_decision_id, dc.target_ref
                ) AS candidate_count
            FROM decision_citations dc
            JOIN decisions td
              ON td.docket_norm = dc.target_ref
            LEFT JOIN decisions sd
              ON sd.decision_id = dc.source_decision_id
            WHERE dc.target_type = 'docket'
              AND td.decision_id <> dc.source_decision_id
        )
        SELECT
            source_decision_id,
            target_ref,
            target_decision_id,
            source_court,
            source_canton,
            source_date,
            target_court,
            target_canton,
            target_date,
            candidate_rank,
            candidate_count
        FROM candidate_matches
        ORDER BY source_decision_id, target_ref, candidate_rank
        """
    )
    insert_sql = """
        INSERT OR IGNORE INTO citation_targets
        (source_decision_id, target_ref, target_decision_id, match_type, confidence_score)
        VALUES (?, ?, ?, ?, ?)
    """
    batch_size = 10_000
    while True:
        rows = cursor.fetchmany(batch_size)
        if not rows:
            break
        payload: list[tuple[str, str, str, str, float]] = []
        for row in rows:
            payload.append(
                (
                    row["source_decision_id"],
                    row["target_ref"],
                    row["target_decision_id"],
                    "docket_norm",
                    _citation_confidence(
                        source_court=row["source_court"],
                        source_canton=row["source_canton"],
                        source_date=row["source_date"],
                        target_court=row["target_court"],
                        target_canton=row["target_canton"],
                        target_date=row["target_date"],
                        target_ref=row["target_ref"],
                        candidate_rank=int(row["candidate_rank"] or 1),
                        candidate_count=int(row["candidate_count"] or 1),
                    ),
                )
            )
        conn.executemany(insert_sql, payload)

    # Docket refs the exact pass left unmatched, through the caption forms in
    # docket_aliases (ZH "LA200010-O/U" for a row stored as "LA200010").
    _resolve_via_docket_aliases(conn, insert_sql)

    # Second pass: resolve BGE citations.
    # BGE target_ref = "BGE 147 I 268", BGE docket_norm = "BGE 147 I 268".
    # Match both full ref and stripped (without "BGE " prefix) against docket_norm.
    cursor = conn.execute(
        """
        WITH bge_matches AS (
            SELECT
                dc.source_decision_id,
                dc.target_ref,
                sd.court AS source_court,
                sd.canton AS source_canton,
                sd.decision_date AS source_date,
                td.decision_id AS target_decision_id,
                td.court AS target_court,
                td.canton AS target_canton,
                td.decision_date AS target_date,
                1 AS candidate_rank,
                1 AS candidate_count
            FROM decision_citations dc
            JOIN decisions td
              ON (td.docket_norm = dc.target_ref
                  OR td.docket_norm = SUBSTR(dc.target_ref, 5))
             AND td.court IN ('bge', 'bger', 'bge_historical')
            LEFT JOIN decisions sd
              ON sd.decision_id = dc.source_decision_id
            WHERE dc.target_type = 'bge'
              AND dc.target_ref LIKE 'BGE %'
              AND td.decision_id <> dc.source_decision_id
        )
        SELECT
            source_decision_id,
            target_ref,
            target_decision_id,
            source_court,
            source_canton,
            source_date,
            target_court,
            target_canton,
            target_date,
            candidate_rank,
            candidate_count
        FROM bge_matches
        ORDER BY source_decision_id, target_ref
        """
    )
    while True:
        rows = cursor.fetchmany(batch_size)
        if not rows:
            break
        payload = []
        for row in rows:
            payload.append(
                (
                    row["source_decision_id"],
                    row["target_ref"],
                    row["target_decision_id"],
                    "bge_norm",
                    _citation_confidence(
                        source_court=row["source_court"],
                        source_canton=row["source_canton"],
                        source_date=row["source_date"],
                        target_court=row["target_court"],
                        target_canton=row["target_canton"],
                        target_date=row["target_date"],
                        candidate_rank=int(row["candidate_rank"] or 1),
                        candidate_count=int(row["candidate_count"] or 1),
                    ),
                )
            )
        conn.executemany(insert_sql, payload)

    # Third pass: resolve bare BGE docket refs (target_type='docket', e.g. "131 III 115").
    # These are BGE volume references without the "BGE " prefix.
    cursor = conn.execute(
        """
        WITH bare_bge_matches AS (
            SELECT
                dc.source_decision_id,
                dc.target_ref,
                sd.court AS source_court,
                sd.canton AS source_canton,
                sd.decision_date AS source_date,
                td.decision_id AS target_decision_id,
                td.court AS target_court,
                td.canton AS target_canton,
                td.decision_date AS target_date,
                1 AS candidate_rank,
                1 AS candidate_count
            FROM decision_citations dc
            JOIN decisions td
              ON td.docket_norm = 'BGE ' || dc.target_ref
             AND td.court IN ('bge', 'bger', 'bge_historical')
            LEFT JOIN decisions sd
              ON sd.decision_id = dc.source_decision_id
            LEFT JOIN citation_targets ct
              ON ct.source_decision_id = dc.source_decision_id
             AND ct.target_ref = dc.target_ref
            WHERE dc.target_type = 'docket'
              AND dc.target_ref GLOB '[0-9]* [IVX]* [0-9]*'
              AND ct.source_decision_id IS NULL
              AND td.decision_id <> dc.source_decision_id
        )
        SELECT
            source_decision_id,
            target_ref,
            target_decision_id,
            source_court,
            source_canton,
            source_date,
            target_court,
            target_canton,
            target_date,
            candidate_rank,
            candidate_count
        FROM bare_bge_matches
        ORDER BY source_decision_id, target_ref
        """
    )
    while True:
        rows = cursor.fetchmany(batch_size)
        if not rows:
            break
        payload = []
        for row in rows:
            payload.append(
                (
                    row["source_decision_id"],
                    row["target_ref"],
                    row["target_decision_id"],
                    "bge_bare",
                    _citation_confidence(
                        source_court=row["source_court"],
                        source_canton=row["source_canton"],
                        source_date=row["source_date"],
                        target_court=row["target_court"],
                        target_canton=row["target_canton"],
                        target_date=row["target_date"],
                        candidate_rank=int(row["candidate_rank"] or 1),
                        candidate_count=int(row["candidate_count"] or 1),
                    ),
                )
            )
        conn.executemany(insert_sql, payload)

    # Fourth pass: pin-cite resolution for BGE references.
    # Swiss legal citations pinpoint into a case body: "BGE 125 V 352" references
    # page 352 of the case starting at "BGE 125 V 351".  Exact-match passes 2-3
    # miss these because no decision has first-page 352.  We resolve them to
    # the nearest preceding first-page in the same volume+division within a
    # bounded distance.
    _PIN_DISTANCE = 30
    _PIN_BGE_PAT = re.compile(r"^(?:BGE\s+)?(\d{1,4})\s+([IVX]{1,4})\s+(\d{1,4})$")
    bge_index: dict[tuple[int, str], list[tuple]] = {}
    for did, dn, court, canton, ddate in conn.execute(
        "SELECT decision_id, docket_norm, court, canton, decision_date FROM decisions "
        "WHERE court IN ('bge', 'bge_historical')"
    ):
        m = _PIN_BGE_PAT.match(dn or "")
        if m:
            vol, div, page = int(m.group(1)), m.group(2), int(m.group(3))
            bge_index.setdefault((vol, div), []).append(
                (page, did, court, canton, ddate)
            )
    for k in bge_index:
        bge_index[k].sort()

    cursor = conn.execute(
        """
        SELECT DISTINCT
            dc.source_decision_id,
            dc.target_ref,
            sd.court AS source_court,
            sd.canton AS source_canton,
            sd.decision_date AS source_date
        FROM decision_citations dc
        LEFT JOIN citation_targets ct
          ON ct.source_decision_id = dc.source_decision_id
         AND ct.target_ref = dc.target_ref
        LEFT JOIN decisions sd
          ON sd.decision_id = dc.source_decision_id
        WHERE ct.source_decision_id IS NULL
          AND (
                (dc.target_type = 'bge' AND dc.target_ref LIKE 'BGE %')
             OR (dc.target_type = 'docket'
                 AND dc.target_ref GLOB '[0-9]* [IVX]* [0-9]*')
          )
        """
    )
    payload = []
    for row in cursor.fetchall():
        src_id = row["source_decision_id"]
        ref = row["target_ref"]
        m = _PIN_BGE_PAT.match(ref)
        if not m:
            continue
        vol, div, page = int(m.group(1)), m.group(2), int(m.group(3))
        candidates = bge_index.get((vol, div))
        if not candidates:
            continue
        best = None
        for first_page, did, t_court, t_canton, t_date in candidates:
            if first_page > page:
                break
            if (page - first_page) <= _PIN_DISTANCE:
                best = (first_page, did, t_court, t_canton, t_date)
        # The page belongs to the nearest preceding first page, the citing
        # decision's own included. A BGE prints its own reference in every
        # page head ("BGE 139 III 120 S. 121"); skipping the source here
        # resolved that reference to the decision before it in the volume,
        # so every BGE "cited" its predecessor (report 2026-10-20: 17 of 18
        # sampled BGE). A reference into the source's own pages is no citation.
        if best is None or any(c[0] == best[0] and c[1] == src_id for c in candidates):
            continue
        first_page, target_did, t_court, t_canton, t_date = best
        confidence = _citation_confidence(
            source_court=row["source_court"],
            source_canton=row["source_canton"],
            source_date=row["source_date"],
            target_court=t_court,
            target_canton=t_canton,
            target_date=t_date,
            target_ref=ref,
            candidate_rank=1,
            candidate_count=1,
        )
        # Discount: pin-cite is an inferred match, not exact docket equality.
        confidence = max(0.50, round(confidence - 0.10, 4))
        payload.append(
            (src_id, ref, target_did, "bge_pincite", confidence)
        )

    for i in range(0, len(payload), batch_size):
        conn.executemany(insert_sql, payload[i : i + batch_size])


def build_graph(
    *,
    input_dir: Path,
    db_path: Path,
    limit: int | None = None,
    source_db: Path | None = None,
    courts: list[str] | None = None,
    compare_against: Path | None = None,
) -> dict:
    t0 = time.time()
    # Resolve symlinks so temp file is on same filesystem (atomic rename)
    db_path = db_path.resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # The resolution-rate guard below normally compares the new graph
    # against the one it is about to overwrite. A caller that stages the
    # build somewhere else (see _bootstrap_via_full_rebuild in
    # build_reference_graph_incremental.py) must be able to point the
    # guard at the real previous graph, otherwise db_path does not exist
    # yet and the guard silently no-ops.
    prev_path = (compare_against.resolve() if compare_against is not None
                 else db_path)
    tmp_path = db_path.with_name(f".{db_path.name}.tmp")
    # A hot -journal from a killed build must not outlive its file: SQLite
    # would replay it into whatever is at this path next.
    for stale in (tmp_path, *(Path(str(tmp_path) + ext)
                              for ext in ("-journal", "-wal", "-shm"))):
        if stale.exists():
            stale.unlink()

    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(str(tmp_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(SCHEMA_SQL)

        decisions = 0
        statute_edges = 0
        citation_edges = 0

        row_iter = _iter_rows_from_source(
            input_dir=input_dir,
            source_db=source_db,
            courts=courts,
        )
        for row in row_iter:
            decision_id = row.get("decision_id")
            if not decision_id:
                continue

            docket_number = row.get("docket_number") or ""
            conn.execute(
                """
                INSERT OR IGNORE INTO decisions
                (decision_id, docket_number, docket_norm, court, canton, language, decision_date)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision_id,
                    docket_number,
                    _docket_norm(docket_number),
                    row.get("court"),
                    row.get("canton"),
                    row.get("language"),
                    row.get("decision_date"),
                ),
            )
            insert_docket_aliases(conn, row)
            decisions += 1

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
                    VALUES (?, ?, ?)
                    ON CONFLICT(decision_id, statute_id)
                    DO UPDATE SET mention_count = mention_count + excluded.mention_count
                    """,
                    (decision_id, statute.normalized, statute.count),
                )
                statute_edges += 1

            for citation in citations:
                target_type = "bge" if citation.citation_type == "bge" else "docket"
                is_prior = 1 if citation.normalized in prior_instance_dockets else 0
                conn.execute(
                    """
                    INSERT INTO decision_citations
                    (source_decision_id, target_ref, target_type, mention_count, is_prior_instance)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(source_decision_id, target_ref)
                    DO UPDATE SET mention_count = mention_count + excluded.mention_count,
                                 is_prior_instance = MAX(is_prior_instance, excluded.is_prior_instance)
                    """,
                    (decision_id, citation.normalized, target_type, citation.count, is_prior),
                )
                citation_edges += 1

            # Insert prior instance dockets that weren't captured by citation patterns
            for pi_docket in prior_instance_dockets:
                conn.execute(
                    """
                    INSERT INTO decision_citations
                    (source_decision_id, target_ref, target_type, mention_count, is_prior_instance)
                    VALUES (?, ?, 'docket', 1, 1)
                    ON CONFLICT(source_decision_id, target_ref)
                    DO UPDATE SET is_prior_instance = 1
                    """,
                    (decision_id, pi_docket),
                )
                citation_edges += 1

            if decisions % 1000 == 0:
                conn.commit()
                if decisions % 10000 == 0:
                    elapsed = time.time() - t0
                    print(
                        f"  [{elapsed:.0f}s] {decisions:,} decisions processed, "
                        f"{statute_edges:,} statute edges, {citation_edges:,} citation edges",
                        file=sys.stderr,
                        flush=True,
                    )

            if limit and decisions >= limit:
                break

        conn.commit()
        _resolve_citation_targets(conn)
        conn.commit()

        # Completeness Step 1: additive BGer↔BGE cross-reference resolution —
        # recovers citations the docket_norm JOIN missed (separator drift,
        # decision_id forms, and BGer dockets whose decision is stored as a BGE).
        # Purely additive (only unresolved refs, INSERT OR IGNORE) and wrapped so
        # a failure can NEVER break the nightly build — the base graph still ships.
        if source_db is not None:
            try:
                _scripts = os.path.join(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
                if _scripts not in sys.path:
                    sys.path.insert(0, _scripts)
                from resolve_citation_crossref import build_key_index, resolve_crossref
                _src = sqlite3.connect(f"file:{source_db}?mode=ro&immutable=1", uri=True)
                try:
                    _xref_added = resolve_crossref(conn, build_key_index(_src))
                finally:
                    _src.close()
                conn.commit()
                print(f"  cross-reference resolution: +{_xref_added:,} edges",
                      file=sys.stderr, flush=True)
            except Exception as _e:
                print(f"  cross-reference resolution skipped (non-fatal): {_e}",
                      file=sys.stderr, flush=True)

        total_decisions = conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
        total_statutes = conn.execute("SELECT COUNT(*) FROM statutes").fetchone()[0]
        total_citations = conn.execute("SELECT COUNT(*) FROM decision_citations").fetchone()[0]
        resolved_refs = conn.execute(
            "SELECT COUNT(DISTINCT source_decision_id || '|' || target_ref) FROM citation_targets"
        ).fetchone()[0]
        resolved_links = conn.execute(
            "SELECT COUNT(*) FROM citation_targets"
        ).fetchone()[0]
        prior_instance_count = conn.execute(
            "SELECT COUNT(*) FROM decision_citations WHERE is_prior_instance = 1"
        ).fetchone()[0]
    except Exception:
        if conn is not None:
            conn.close()
        if tmp_path.exists():
            tmp_path.unlink()
        raise
    else:
        # Defensive guard: refuse atomic swap if the resolution RATE
        # (resolved/raw) has regressed materially.  Comparing raw row
        # counts is wrong when the input corpus changes size between
        # builds (e.g. an FTS5 dedup pass shrinks the source by 15 %),
        # because absolute row counts naturally drop with the corpus
        # without indicating a resolver-quality regression. Rate is
        # corpus-size-invariant.
        if prev_path.exists() and total_citations > 0:
            try:
                with sqlite3.connect(f"file:{prev_path}?mode=ro", uri=True) as prev:
                    prev_total = prev.execute(
                        "SELECT COUNT(*) FROM decision_citations"
                    ).fetchone()[0]
                    prev_resolved = prev.execute(
                        "SELECT COUNT(*) FROM ("
                        " SELECT 1 FROM citation_targets"
                        " GROUP BY source_decision_id, target_ref"
                        ")"
                    ).fetchone()[0]
                if prev_total > 0:
                    prev_rate = prev_resolved / prev_total
                    new_rate = resolved_refs / total_citations
                    # Allow a 2pp drop before refusing — gives wiggle
                    # room for normal fluctuations while still catching
                    # serious resolver regressions.
                    if new_rate < prev_rate - 0.02:
                        raise RuntimeError(
                            f"Resolution-rate regression: new "
                            f"{100*new_rate:.2f}% < prev {100*prev_rate:.2f}% - "
                            f"2pp; refusing atomic swap. Inspect {tmp_path} "
                            f"manually."
                        )
            except sqlite3.OperationalError:
                pass  # No previous DB or unreadable — first-build OK.
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.close()
        os.replace(tmp_path, db_path)

    return {
        "db_path": str(db_path),
        "source_db": str(source_db) if source_db else None,
        "courts_filter": courts or [],
        "decisions_ingested_lines": decisions,
        "statute_edges_ingested": statute_edges,
        "citation_edges_ingested": citation_edges,
        "decisions_total": total_decisions,
        "statutes_total": total_statutes,
        "citations_total": total_citations,
        "citations_resolved": resolved_refs,
        "citation_target_links": resolved_links,
        "prior_instance_links": prior_instance_count,
    }


def _iter_rows_from_source(
    *,
    input_dir: Path,
    source_db: Path | None,
    courts: list[str] | None,
) -> Iterator[dict]:
    if source_db:
        yield from _iter_rows_from_db(source_db=source_db, courts=courts)
        return
    yield from _iter_rows_from_jsonl(input_dir)


def _iter_rows_from_jsonl(input_dir: Path) -> Iterator[dict]:
    for jsonl_path in sorted(input_dir.glob("*.jsonl")):
        with open(jsonl_path, encoding="utf-8") as f:
            for line_no, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    print(f"  WARNING: Skipping bad JSON at {jsonl_path.name}:{line_no}", file=sys.stderr)


def _iter_rows_from_db(*, source_db: Path, courts: list[str] | None) -> Iterator[dict]:
    conn = _open_sqlite_readonly(source_db)
    conn.row_factory = sqlite3.Row
    try:
        params: list[str] = []
        where = ""
        if courts:
            placeholders = ",".join("?" for _ in courts)
            where = f"WHERE lower(court) IN ({placeholders})"
            params = [c.lower() for c in courts]
        sql = f"""
            SELECT decision_id, docket_number, court, canton, language, decision_date,
                   title, regeste, full_text
            FROM decisions
            {where}
            ORDER BY rowid
        """
        cur = conn.execute(sql, params)
        while True:
            rows = cur.fetchmany(1000)
            if not rows:
                break
            for row in rows:
                yield dict(row)
    finally:
        conn.close()


def _open_sqlite_readonly(path: Path) -> sqlite3.Connection:
    # immutable=1 (invariant #1): the fd pins the inode open at connect
    # time, so an atomic os.replace() of the path mid-read (quick_publish
    # swaps decisions.db intraday) can't yield "database disk image is
    # malformed" — the exact failure that killed the incremental shadow
    # graph builds on 2026-06-01/03/04 (~2 h into the corpus read). The
    # structure builder's _open_decisions_ro has used immutable=1 all
    # along and never hit it.
    last_error: Exception | None = None
    for _ in range(5):
        try:
            return sqlite3.connect(
                f"file:{path}?mode=ro&immutable=1",
                uri=True,
                timeout=1.0,
            )
        except sqlite3.OperationalError as e:
            last_error = e
            time.sleep(0.2)
    raise sqlite3.OperationalError(f"Unable to open source DB {path}: {last_error}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build citation/statute graph from decision JSONL files")
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("output/decisions"),
        help="Directory containing *.jsonl decision files",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=Path("output/reference_graph.db"),
        help="Output SQLite graph database path",
    )
    parser.add_argument(
        "--source-db",
        type=Path,
        help="Optional source SQLite decisions database (uses decisions table instead of JSONL input)",
    )
    parser.add_argument(
        "--courts",
        type=str,
        help="Optional comma-separated court filter when using --source-db (e.g. bger,bge,bvger)",
    )
    parser.add_argument("--limit", type=int, help="Optional limit for quick test runs")
    args = parser.parse_args()

    courts = None
    if args.courts:
        courts = [c.strip() for c in args.courts.split(",") if c.strip()]

    stats = build_graph(
        input_dir=args.input,
        db_path=args.db,
        limit=args.limit,
        source_db=args.source_db,
        courts=courts,
    )
    print(json.dumps(stats, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
