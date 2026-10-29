#!/usr/bin/env python3
"""
Build the commentaries SQLite database from scraped JSON.

Reads output/onlinekommentar/commentaries.json and, when present,
output/openlegalcommentary/commentaries.jsonl (OpenLegalCommentary.ch BV,
written by scrapers.openlegalcommentary), and builds a searchable SQLite DB
with FTS5 for commentary text.

Output: output/ok_commentaries.db

Usage:
    python -m search_stack.build_ok_commentaries_db
    python -m search_stack.build_ok_commentaries_db --input output/onlinekommentar/commentaries.json
"""

import argparse
import json
import logging
import os
import sqlite3
import time
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("build_ok_commentaries")

INPUT_FILE = Path(os.environ.get("OK_INPUT", "output/onlinekommentar/commentaries.json"))
OLC_INPUT_FILE = Path(os.environ.get("OLC_INPUT", "output/openlegalcommentary/commentaries.jsonl"))
OUTPUT_DB = Path(os.environ.get("OK_DB", "output/ok_commentaries.db"))
STATUTES_DB = Path(os.environ.get("SWISS_CASELAW_STATUTES_DB", "output/statutes.db"))


def load_abbr_index(statutes_conn: sqlite3.Connection) -> dict[str, tuple[str, str]]:
    """Map every Fedlex law abbreviation (de/fr/it, uppercased) to
    (sr_number, canonical_de_abbr).

    Used to backfill commentaries whose scraped sr_number/abbr were empty
    because the OnlineKommentar scraper only assigns them for a core ~18-law
    set (GH #23). statutes.db is the comprehensive Fedlex abbreviation source.
    """
    idx: dict[str, tuple[str, str]] = {}
    try:
        rows = statutes_conn.execute(
            "SELECT sr_number, abbr_de, abbr_fr, abbr_it FROM laws"
        ).fetchall()
    except sqlite3.Error:
        return idx
    for sr, de, fr, it in rows:
        canonical = (de or fr or it or "").strip()
        if not canonical:
            continue
        for ab in (de, fr, it):
            ab = (ab or "").strip()
            if ab:
                idx.setdefault(ab.upper(), (sr, canonical))
    return idx


def resolve_law_from_title(
    title: str | None, abbr_index: dict[str, tuple[str, str]]
) -> tuple[str, str] | None:
    """Derive (sr_number, canonical_abbr) from a commentary title whose last
    token is the law abbreviation, e.g. 'Art. 13 StHG' -> ('642.14', 'StHG').

    Returns None if the title is empty or its trailing token isn't a known
    abbreviation (leave sr_number/abbr empty rather than guess).
    """
    if not title:
        return None
    tokens = title.strip().split()
    if not tokens:
        return None
    token = tokens[-1].strip(".,;:()[]")
    return abbr_index.get(token.upper())


def load_olc_commentaries(path: Path) -> list[dict]:
    """Read the OpenLegalCommentary.ch JSONL into commentary rows.

    Same field mapping as scrapers.openlegalcommentary._insert_into_db: a
    deterministic olc_ ok_uuid (get_commentary and build_legal_scholarship
    tell the source by that prefix), the synthetic act olc_bv_101, and
    authors ["AI-generated"] — the site names no human author. A missing
    file is not an error: the OnlineKommentar rows still build.
    """
    if not path.exists():
        log.warning("OLC input not found: %s — building without OpenLegalCommentary", path)
        return []
    by_id: dict[str, dict] = {}
    skipped = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
                olc_id = f"olc_{e['abbr']}_{e['article_num']}_{e['language']}"
                content = e["content_text"]
            except (ValueError, KeyError, TypeError):
                skipped += 1
                continue
            if not content:
                skipped += 1
                continue
            # Later lines win, as INSERT OR REPLACE did in the direct insert.
            by_id[olc_id] = {
                "ok_uuid": olc_id,
                "legislative_act_uuid": "olc_bv_101",
                "sr_number": e.get("sr_number") or "101",
                "abbr": e["abbr"],
                "article_num": e["article_num"],
                "title": e.get("title") or "",
                "language": e["language"],
                "date": None,
                "authors": ["AI-generated"],
                "editors": [],
                "suggested_citation": None,
                "html_link": e.get("html_link"),
                "pdf_link": None,
                "content_html": None,
                "content_text": content,
                "legal_text": e.get("legal_text", ""),
            }
    if skipped:
        log.warning("Skipped %d malformed OLC lines in %s", skipped, path)
    log.info("Loaded %d OpenLegalCommentary commentaries from %s", len(by_id), path)
    return list(by_id.values())


def create_schema(conn: sqlite3.Connection):
    """Create database schema."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS legislative_acts (
            ok_uuid TEXT PRIMARY KEY,
            sr_number TEXT,
            abbr TEXT,
            title_en TEXT
        );

        CREATE TABLE IF NOT EXISTS commentaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ok_uuid TEXT NOT NULL,
            legislative_act_uuid TEXT NOT NULL,
            sr_number TEXT,
            abbr TEXT,
            article_num TEXT NOT NULL,
            title TEXT NOT NULL,
            language TEXT NOT NULL,
            date TEXT,
            authors TEXT,
            editors TEXT,
            suggested_citation TEXT,
            html_link TEXT,
            pdf_link TEXT,
            content_html TEXT,
            content_text TEXT NOT NULL,
            legal_text TEXT,
            FOREIGN KEY (legislative_act_uuid) REFERENCES legislative_acts(ok_uuid)
        );

        CREATE INDEX IF NOT EXISTS idx_commentaries_sr_art
            ON commentaries(sr_number, article_num);
        CREATE INDEX IF NOT EXISTS idx_commentaries_sr_art_lang
            ON commentaries(sr_number, article_num, language);
        CREATE INDEX IF NOT EXISTS idx_commentaries_abbr
            ON commentaries(abbr);

        CREATE VIRTUAL TABLE IF NOT EXISTS commentaries_fts USING fts5(
            sr_number, abbr, article_num, title, content_text, language,
            content='commentaries', content_rowid='id',
            tokenize='unicode61 remove_diacritics 2'
        );
    """)


def build_db():
    """Main build pipeline."""
    if not INPUT_FILE.exists():
        log.error("Input file not found: %s — run scrapers.onlinekommentar first", INPUT_FILE)
        return

    with open(INPUT_FILE, encoding="utf-8") as f:
        commentaries = json.load(f)

    log.info("Loaded %d commentaries from %s", len(commentaries), INPUT_FILE)

    # Backfill empty sr_number/abbr from the title via statutes.db (GH #23):
    # the scraper only assigns sr/abbr for a core ~18-law set, leaving e.g.
    # StHG/DBG/HMG commentaries unreachable by abbreviation. statutes.db is the
    # comprehensive Fedlex abbreviation source.
    abbr_index: dict[str, tuple[str, str]] = {}
    if STATUTES_DB.exists():
        sconn = sqlite3.connect(f"file:{STATUTES_DB}?mode=ro", uri=True)
        abbr_index = load_abbr_index(sconn)
        sconn.close()
    else:
        log.warning("statutes.db not found at %s — skipping sr/abbr backfill", STATUTES_DB)
    backfilled = 0
    if abbr_index:
        for c in commentaries:
            if (c.get("sr_number") or "").strip():
                continue
            resolved = resolve_law_from_title(c.get("title"), abbr_index)
            if resolved:
                c["sr_number"], c["abbr"] = resolved
                backfilled += 1
    log.info("Backfilled sr_number/abbr from title for %d commentaries", backfilled)

    # OpenLegalCommentary rows join the same tmp DB, so they ride the same
    # rename; nothing writes into the live DB.
    commentaries.extend(load_olc_commentaries(OLC_INPUT_FILE))

    # Prepare output
    tmp_db = OUTPUT_DB.with_suffix(".tmp")
    tmp_db.unlink(missing_ok=True)
    OUTPUT_DB.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(tmp_db))
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA cache_size = -64000")  # 64MB
    create_schema(conn)

    # Collect unique legislative acts
    acts_seen: set[str] = set()
    for c in commentaries:
        act_uuid = c.get("legislative_act_uuid", "")
        if act_uuid and act_uuid not in acts_seen:
            acts_seen.add(act_uuid)
            conn.execute(
                "INSERT OR IGNORE INTO legislative_acts (ok_uuid, sr_number, abbr) VALUES (?, ?, ?)",
                (act_uuid, c.get("sr_number", ""), c.get("abbr", "")),
            )

    # Insert commentaries
    for c in commentaries:
        conn.execute(
            """INSERT INTO commentaries
               (ok_uuid, legislative_act_uuid, sr_number, abbr, article_num,
                title, language, date, authors, editors, suggested_citation,
                html_link, pdf_link, content_html, content_text, legal_text)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                c.get("ok_uuid", ""),
                c.get("legislative_act_uuid", ""),
                c.get("sr_number", ""),
                c.get("abbr", ""),
                c.get("article_num", ""),
                c.get("title", ""),
                c.get("language", "de"),
                c.get("date", ""),
                json.dumps(c.get("authors", []), ensure_ascii=False),
                json.dumps(c.get("editors", []), ensure_ascii=False),
                c.get("suggested_citation", ""),
                c.get("html_link", ""),
                c.get("pdf_link", ""),
                c.get("content_html", ""),
                c.get("content_text", ""),
                c.get("legal_text", ""),
            ),
        )

    conn.commit()

    # Populate FTS5 index
    log.info("Building FTS5 index...")
    conn.execute("""
        INSERT INTO commentaries_fts(rowid, sr_number, abbr, article_num, title, content_text, language)
        SELECT id, sr_number, abbr, article_num, title, content_text, language FROM commentaries
    """)
    conn.commit()

    # Optimize
    log.info("Optimizing FTS5...")
    conn.execute("INSERT INTO commentaries_fts(commentaries_fts) VALUES('optimize')")
    conn.commit()

    # Stats
    total = conn.execute("SELECT COUNT(*) FROM commentaries").fetchone()[0]
    acts = conn.execute("SELECT COUNT(*) FROM legislative_acts").fetchone()[0]
    log.info("Built OK commentaries DB: %d commentaries, %d legislative acts", total, acts)

    # Per-law counts
    top = conn.execute("""
        SELECT abbr, sr_number, COUNT(*) as cnt
        FROM commentaries
        WHERE abbr != ''
        GROUP BY abbr
        ORDER BY cnt DESC
    """).fetchall()
    log.info("Commentaries per law:")
    for abbr, sr, cnt in top:
        log.info("  %s (SR %s): %d", abbr, sr, cnt)

    # Language breakdown
    langs = conn.execute("""
        SELECT language, COUNT(*) FROM commentaries GROUP BY language ORDER BY COUNT(*) DESC
    """).fetchall()
    log.info("Languages: %s", ", ".join(f"{lang}={cnt}" for lang, cnt in langs))

    conn.execute("PRAGMA journal_mode=DELETE")
    conn.close()

    # Atomic rename
    if OUTPUT_DB.exists():
        OUTPUT_DB.unlink()
    tmp_db.rename(OUTPUT_DB)
    log.info("Saved to %s (%.1f KB)", OUTPUT_DB, OUTPUT_DB.stat().st_size / 1024)


def main():
    global INPUT_FILE, OLC_INPUT_FILE, OUTPUT_DB

    parser = argparse.ArgumentParser(description="Build OK commentaries DB from JSON")
    parser.add_argument("--input", type=Path, default=INPUT_FILE)
    parser.add_argument("--olc-input", type=Path, default=OLC_INPUT_FILE)
    parser.add_argument("--output", type=Path, default=OUTPUT_DB)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    INPUT_FILE = args.input
    OLC_INPUT_FILE = args.olc_input
    OUTPUT_DB = args.output

    t0 = time.time()
    build_db()
    log.info("Total time: %.1f seconds", time.time() - t0)


if __name__ == "__main__":
    main()
