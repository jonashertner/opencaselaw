"""
Consolidate scrapers/practice/* JSONL output into a single SQLite FTS5
database for the MCP search_practice / get_practice tools.

Usage:
    python3 -m search_stack.build_practice_db
        --jsonl-dir output/practice
        --db output/practice.db

Schema
------

  practice (
    doc_id TEXT PRIMARY KEY,
    source TEXT,
    issuing_authority TEXT,
    doc_type TEXT,
    doc_number TEXT,
    title TEXT,
    date TEXT,
    language TEXT,
    url TEXT,
    pdf_url TEXT,
    body_text TEXT,
    topics_json TEXT,
    scraped_at TEXT,
    content_hash TEXT
  )

  practice_fts USING fts5(
    doc_number, title, body_text, topics_json,
    content='practice', content_rowid='rowid',
    tokenize='unicode61 remove_diacritics 2'
  )

The build is idempotent + atomic — writes to ``{db}.tmp`` then
``os.replace()`` so MCP workers reading with ``immutable=1`` never see
a half-built DB.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
from pathlib import Path

# Run as a script (the practice timer) the repo root is not on sys.path;
# dehyphenate lives there.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from dehyphenate import configured_lexicon, dehyphenate  # noqa: E402

logger = logging.getLogger("build_practice_db")


def build(jsonl_dir: Path, db_path: Path) -> dict:
    # A symlinked practice.db (data volume) must be replaced THROUGH the
    # link: os.replace() onto the link itself would turn it into a regular
    # file on the root disk, with the multi-GB .tmp twin beside it.
    db_path = Path(db_path).resolve() if Path(db_path).exists() else Path(db_path)
    tmp_path = db_path.parent / (db_path.name + ".tmp")
    if tmp_path.exists():
        tmp_path.unlink()

    conn = sqlite3.connect(str(tmp_path))
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("PRAGMA synchronous=NORMAL")

    conn.executescript("""
        CREATE TABLE practice (
            doc_id           TEXT PRIMARY KEY,
            source           TEXT NOT NULL,
            issuing_authority TEXT NOT NULL,
            doc_type         TEXT,
            doc_number       TEXT,
            title            TEXT,
            date             TEXT,
            language         TEXT,
            url              TEXT,
            pdf_url          TEXT,
            body_text        TEXT,
            topics_json      TEXT,
            scraped_at       TEXT,
            content_hash     TEXT
        );

        CREATE INDEX practice_source_idx     ON practice(source);
        CREATE INDEX practice_authority_idx  ON practice(issuing_authority);
        CREATE INDEX practice_date_idx       ON practice(date);
        CREATE INDEX practice_doctype_idx    ON practice(doc_type);

        CREATE VIRTUAL TABLE practice_fts USING fts5(
            doc_number, title, body_text, topics_json,
            content='practice', content_rowid='rowid',
            tokenize='unicode61 remove_diacritics 2'
        );

        -- Sources catalog (one row per source key)
        CREATE TABLE sources (
            source           TEXT PRIMARY KEY,
            issuing_authority TEXT,
            doc_count        INTEGER,
            last_scraped_at  TEXT
        );
    """)

    by_source: dict[str, dict] = {}
    inserted = 0
    # Rejoin words the source PDF / OCR broke across lines ("ge-⏎stützt"),
    # as build_fts5 does for decisions (dehyphenate.py). Off unless
    # OPENCASELAW_DEHYPHEN_LEXICON names a lexicon. content_hash stays the
    # scraper's hash of the source text.
    lexicon = configured_lexicon()

    for jsonl in sorted(jsonl_dir.glob("*.jsonl")):
        logger.info("Reading %s", jsonl.name)
        with open(jsonl) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if lexicon is not None:
                    lang = d.get("language") or "de"
                    for field in ("body_text", "title"):
                        if isinstance(d.get(field), str):
                            d[field] = dehyphenate(d[field], lang, lexicon)

                # Dedup at the row level — last write wins on doc_id collision
                conn.execute("""
                    INSERT INTO practice
                      (doc_id, source, issuing_authority, doc_type, doc_number,
                       title, date, language, url, pdf_url, body_text,
                       topics_json, scraped_at, content_hash)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(doc_id) DO UPDATE SET
                      title=excluded.title,
                      body_text=excluded.body_text,
                      scraped_at=excluded.scraped_at,
                      content_hash=excluded.content_hash,
                      -- A re-issued edition (REVISION_FIELD sources: SECO,
                      -- BAG, BJ, SEM-Handbuch) must also refresh its
                      -- metadata, or the index keeps the FIRST edition's
                      -- date/pdf_url under the NEW text. NULLIF/COALESCE
                      -- keeps the stored value when the new record's field
                      -- is empty (a parse regression must not blank a
                      -- good date).
                      date=COALESCE(NULLIF(excluded.date, ''), practice.date),
                      pdf_url=COALESCE(NULLIF(excluded.pdf_url, ''), practice.pdf_url),
                      url=COALESCE(NULLIF(excluded.url, ''), practice.url),
                      doc_number=COALESCE(NULLIF(excluded.doc_number, ''), practice.doc_number),
                      doc_type=COALESCE(NULLIF(excluded.doc_type, ''), practice.doc_type),
                      language=COALESCE(NULLIF(excluded.language, ''), practice.language),
                      topics_json=CASE WHEN excluded.topics_json IN ('', '[]')
                                       THEN practice.topics_json
                                       ELSE excluded.topics_json END
                """, (
                    d.get("doc_id"), d.get("source"), d.get("issuing_authority"),
                    d.get("doc_type"), d.get("doc_number"), d.get("title"),
                    d.get("date"), d.get("language"), d.get("url"),
                    d.get("pdf_url"), d.get("body_text"),
                    json.dumps(d.get("topics") or [], ensure_ascii=False),
                    d.get("scraped_at"), d.get("content_hash"),
                ))
                inserted += 1

                src = d.get("source") or "unknown"
                bs = by_source.setdefault(src, {
                    "issuing_authority": d.get("issuing_authority", ""),
                    "doc_count": 0,
                    "last_scraped_at": "",
                })
                bs["doc_count"] += 1
                if (d.get("scraped_at") or "") > bs["last_scraped_at"]:
                    bs["last_scraped_at"] = d["scraped_at"]

    # Re-build FTS5 from base table — single transaction
    conn.execute("INSERT INTO practice_fts(practice_fts) VALUES('rebuild')")

    # doc_count must be ROWS, not JSONL lines: append-only files re-append a
    # re-issued edition, and the upsert collapses it to one row. The line
    # count is kept as `lines` for the build summary only.
    for src, meta in by_source.items():
        meta["lines"] = meta["doc_count"]
    for src, n in conn.execute(
            "SELECT source, COUNT(*) FROM practice GROUP BY source"):
        if src in by_source:
            by_source[src]["doc_count"] = n

    # Sources catalog
    for src, meta in by_source.items():
        conn.execute("""
            INSERT INTO sources (source, issuing_authority, doc_count, last_scraped_at)
            VALUES (?,?,?,?)
        """, (src, meta["issuing_authority"], meta["doc_count"], meta["last_scraped_at"]))

    conn.commit()
    conn.close()

    # Atomic swap
    db_path.parent.mkdir(parents=True, exist_ok=True)
    os.replace(str(tmp_path), str(db_path))

    return {
        "rows": inserted,
        "by_source": by_source,
        "db_size_mb": round(db_path.stat().st_size / 1e6, 2),
    }


def main():
    ap = argparse.ArgumentParser(description="Build practice.db from JSONL")
    ap.add_argument("--jsonl-dir", type=Path,
                    default=Path("output/practice"),
                    help="Directory containing *.jsonl files")
    ap.add_argument("--db", type=Path,
                    default=Path("output/practice.db"),
                    help="Target SQLite path")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    if not args.jsonl_dir.is_dir():
        logger.error("JSONL dir not found: %s", args.jsonl_dir)
        sys.exit(1)

    summary = build(args.jsonl_dir, args.db)
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
