#!/usr/bin/env python3
"""Backfill title / publication_date on botschaft_documents from Fedlex SPARQL.

Background (2026-09-09): every one of the 6,166 rows in the live
``output/materialien.db`` has ``title`` and ``publication_date`` NULL — the
nightly ingest discovered the SPARQL title but never wrote it, and never asked
for a date. The ingest now writes both; this script fills the existing rows.

What it does
------------
For every row whose ``title`` or ``publication_date`` is NULL (and that has an
``eli_uri``), grouped by language, it asks Fedlex SPARQL in ``VALUES`` batches
of ``--batch-size`` ELIs (default 50, one round trip per batch) for the
language-specific ``jolux:title``, ``jolux:dateDocument`` and
``jolux:publicationDate``, then UPDATEs the row:

* ``title``            ← Fedlex title verbatim (whitespace normalised), never
                         synthesised;
* ``publication_date`` ← ``jolux:publicationDate``, else ``jolux:dateDocument``,
                         else the bare BBl year;
* ``date_document``    ← ``jolux:dateDocument``;
* ``date_source``      ← which of the three fed ``publication_date``.

Write pattern
-------------
The same as the nightly ``search_stack.build_botschaft_corpus`` ingest that
the 04:30 UTC unit runs: open the live DB directly (``sqlite3.connect`` on the
path, ``PRAGMA busy_timeout=60000``), UPDATE in place, ``commit()`` per batch.
materialien.db is single-writer (journal_mode=delete); there is no
``.tmp`` + ``os.replace`` here because the nightly build_materialien_db step
copies these tables from the live file by ``sqlite_master`` SQL, so the
added columns ride along. Do not run it while the 04:30 unit is ingesting
(busy_timeout would make one of them wait a minute, then fail).

Idempotent: a second run finds no NULL rows and issues no SPARQL query.
``--dry-run`` opens the DB read-only and prints what would change.

Usage::

    python3 scripts/backfill_botschaft_metadata.py --db output/materialien.db --dry-run
    python3 scripts/backfill_botschaft_metadata.py --db output/materialien.db
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scrapers import fedlex_materialien  # noqa: E402
from search_stack.build_botschaft_corpus import (  # noqa: E402
    DATE_SOURCE_BBL_YEAR,
    ensure_schema,
    resolve_publication_date,
)

log = logging.getLogger("backfill_botschaft_metadata")

_COLS_NEEDED = ("date_document", "date_source")


def _has_columns(conn: sqlite3.Connection) -> bool:
    have = {r[1] for r in conn.execute("PRAGMA table_info(botschaft_documents)")}
    return all(c in have for c in _COLS_NEEDED)


def candidate_rows(
    conn: sqlite3.Connection,
    language: str | None = None,
    limit: int | None = None,
    force: bool = False,
) -> list[sqlite3.Row]:
    """Rows still missing a title or a publication_date, oldest id first."""
    sql = (
        "SELECT botschaft_id, eli_uri, language, bbl_year, bbl_citation, "
        "title, publication_date FROM botschaft_documents "
        "WHERE eli_uri IS NOT NULL AND eli_uri != ''"
    )
    params: list = []
    if not force:
        sql += " AND (title IS NULL OR title = '' OR publication_date IS NULL OR publication_date = '')"
    if language:
        sql += " AND language = ?"
        params.append(language)
    sql += " ORDER BY language, botschaft_id"
    if limit:
        sql += " LIMIT ?"
        params.append(int(limit))
    return conn.execute(sql, params).fetchall()


def plan_updates(rows: list[sqlite3.Row], meta: dict[str, dict]) -> list[tuple]:
    """``(title, publication_date, date_document, date_source, botschaft_id)``
    for every row whose metadata Fedlex answered or whose date can at least
    fall back to the BBl year. Existing non-empty titles are kept."""
    out: list[tuple] = []
    for r in rows:
        m = meta.get(r["eli_uri"]) or {}
        title = r["title"] or m.get("title")
        pub, src = resolve_publication_date(
            m.get("publication_date"), m.get("date_document"), r["bbl_year"],
        )
        if r["publication_date"] and src == DATE_SOURCE_BBL_YEAR:
            # Keep an existing date rather than downgrade it to a year.
            pub, src = r["publication_date"], None
        if not m and not (title or pub):
            continue
        out.append((title, pub, m.get("date_document"), src, r["botschaft_id"]))
    return out


def apply_updates(conn: sqlite3.Connection, updates: list[tuple]) -> int:
    conn.executemany(
        "UPDATE botschaft_documents SET title = ?, publication_date = ?, "
        "date_document = COALESCE(?, date_document), "
        "date_source = COALESCE(?, date_source) WHERE botschaft_id = ?",
        updates,
    )
    conn.commit()
    return len(updates)


def run(
    db_path: Path,
    *,
    dry_run: bool = False,
    batch_size: int = 50,
    rate_limit: float = 1.0,
    language: str | None = None,
    limit: int | None = None,
    force: bool = False,
    fetch=fedlex_materialien.fetch_fga_metadata,
    sleep=time.sleep,
) -> dict:
    """Backfill loop. Returns a stats dict. ``fetch``/``sleep`` are injectable
    for offline tests."""
    if dry_run:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    else:
        # Same write pattern as the nightly ingest (build_botschaft_corpus.main).
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA busy_timeout=60000")
        conn.execute("PRAGMA foreign_keys = ON")
        ensure_schema(conn)  # adds date_document / date_source if missing
    conn.row_factory = sqlite3.Row
    stats = {
        "candidates": 0, "batches": 0, "updated": 0,
        "title_filled": 0, "date_from_fedlex": 0, "date_from_bbl_year": 0,
        "unanswered": 0, "dry_run": dry_run,
    }
    try:
        if dry_run and not _has_columns(conn):
            log.info("dry-run: date_document/date_source columns would be added")
        rows = candidate_rows(conn, language=language, limit=limit, force=force)
        stats["candidates"] = len(rows)
        log.info("%d rows missing title or publication_date", len(rows))
        by_lang: dict[str, list[sqlite3.Row]] = {}
        for r in rows:
            by_lang.setdefault(r["language"], []).append(r)
        for lang, lrows in by_lang.items():
            for i in range(0, len(lrows), batch_size):
                batch = lrows[i:i + batch_size]
                if stats["batches"]:
                    sleep(rate_limit)
                meta = fetch([r["eli_uri"] for r in batch], language=lang)
                stats["batches"] += 1
                updates = plan_updates(batch, meta)
                for title, pub, _doc, src, bid in updates:
                    row = next(r for r in batch if r["botschaft_id"] == bid)
                    if title and not row["title"]:
                        stats["title_filled"] += 1
                    if src == DATE_SOURCE_BBL_YEAR:
                        stats["date_from_bbl_year"] += 1
                    elif src:
                        stats["date_from_fedlex"] += 1
                    if dry_run:
                        log.info("would update %s (%s): title=%r publication_date=%s [%s]",
                                 row["bbl_citation"], lang, (title or "")[:70], pub, src)
                stats["unanswered"] += sum(1 for r in batch if r["eli_uri"] not in meta)
                if not dry_run:
                    stats["updated"] += apply_updates(conn, updates)
                else:
                    stats["updated"] += len(updates)
                log.info("%s batch %d: %d/%d answered by Fedlex",
                         lang, stats["batches"], sum(1 for r in batch if r["eli_uri"] in meta),
                         len(batch))
    finally:
        conn.close()
    return stats


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s",
                        datefmt="%H:%M:%S")
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--db", default=str(REPO / "output" / "materialien.db"))
    ap.add_argument("--dry-run", action="store_true", help="read-only; print the planned updates")
    ap.add_argument("--batch-size", type=int, default=50, help="ELIs per SPARQL VALUES query (default 50)")
    ap.add_argument("--rate-limit", type=float, default=1.0, help="seconds between SPARQL queries (default 1.0)")
    ap.add_argument("--language", choices=["de", "fr", "it"], help="only this language")
    ap.add_argument("--limit", type=int, help="cap the number of rows considered")
    ap.add_argument("--force", action="store_true",
                    help="re-query rows that already have a title and date (titles are kept, dates refreshed)")
    args = ap.parse_args(argv)
    db_path = Path(args.db).resolve()
    if not db_path.exists():
        log.error("DB not found: %s", db_path)
        return 1
    stats = run(
        db_path, dry_run=args.dry_run, batch_size=max(1, args.batch_size),
        rate_limit=max(0.0, args.rate_limit), language=args.language,
        limit=args.limit, force=args.force,
    )
    import json
    print(json.dumps(stats, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
