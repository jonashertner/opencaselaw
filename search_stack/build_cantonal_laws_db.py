#!/usr/bin/env python3
"""
Build cantonal laws SQLite database from cantonal JSONL dumps.

Reads from two sources (direct takes priority over LexFind):
    1. output/cantonal_laws_direct/{canton}.jsonl  (direct from official portals)
    2. output/lexfind_cantonal/{canton}.jsonl       (LexFind PDF fallback)

For cantons with both, direct-source data wins (better text quality). A canton
in LEXFIND_COMPLEMENT_CANTONS also takes the laws in force that its direct shard
does not hold.

Schema:
    laws             — one row per (lexfind_id, language) pair
    articles         — one row per article
    articles_fts     — FTS5 virtual table over title + heading + text

Usage:
    python -m search_stack.build_cantonal_laws_db
    python -m search_stack.build_cantonal_laws_db --input-direct output/cantonal_laws_direct
"""

from __future__ import annotations

import argparse
import hashlib
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
log = logging.getLogger("build_cantonal_laws")

DIRECT_DIR = Path(os.environ.get("CANTONAL_LAWS_DIRECT", "output/cantonal_laws_direct"))
LEXFIND_DIR = Path(os.environ.get("LEXFIND_CANTONAL_OUTPUT", "output/lexfind_cantonal"))
OUTPUT_DB = Path(os.environ.get("CANTONAL_LAWS_DB", "output/cantonal_laws.db"))


# Direct portals that publish only part of a collection. ZH's holds 135 acts
# where LexFind lists 944 in force, the Planungs- und Baugesetz and Steuergesetz
# among those missing, so a failed LexFind lookup answered "no such law". These
# cantons keep their direct shard and add LexFind's in-force laws it lacks.
LEXFIND_COMPLEMENT_CANTONS = ("ZH",)


# Synthetic ids are namespaced above every real LexFind id (the largest
# observed is ~35k) so the two can never be confused, and kept under
# 2**53 so a JSON client does not silently lose precision on them.
_SYNTHETIC_FLOOR = 1 << 52


def _synthetic_id(canton: str, sr_number: str) -> int:
    """A stable id for a law LexFind does not number.

    Was `hash(key)`, which Python salts per process: the same law was
    given a different id on every build, so nothing could reference one
    across runs. sha1 makes it actually stable, as the name always
    claimed.

    Stability raises the stakes on collisions — a colliding pair would
    now collide on EVERY build rather than one night, and `INSERT OR
    REPLACE` would drop one of the two laws permanently. Hence 48 bits
    of digest (~1e-7 over a corpus this size) rather than the 31 the
    old mask left, plus the uniqueness assertion in build().
    """
    key = f"{canton}_{sr_number}"
    digest = hashlib.sha1(key.encode("utf-8")).digest()[:6]
    return _SYNTHETIC_FLOOR | int.from_bytes(digest, "big")


SCHEMA = """
CREATE TABLE IF NOT EXISTS laws (
    lexfind_id             INTEGER NOT NULL,
    language               TEXT NOT NULL,
    canton                 TEXT NOT NULL,
    sr_number              TEXT,
    title                  TEXT NOT NULL,
    category               TEXT,
    is_active              INTEGER DEFAULT 1,
    original_url           TEXT,
    version_active_since   TEXT,
    text_length            INTEGER,
    article_count          INTEGER,
    text_source            TEXT,
    full_text              TEXT,
    fetched_at             TEXT,
    PRIMARY KEY (lexfind_id, language)
);

CREATE INDEX IF NOT EXISTS idx_laws_canton       ON laws(canton);
CREATE INDEX IF NOT EXISTS idx_laws_canton_lang  ON laws(canton, language);
CREATE INDEX IF NOT EXISTS idx_laws_sr           ON laws(sr_number);

-- Every name a cantonal law answers to, one row per name.
--
-- Not an "abbreviations" table: only ~40% of cantonal laws have an
-- abbreviation at all (Zug's own portal leaves 60% blank, and those acts
-- genuinely have none). The thing that makes a law reachable is a
-- canton-qualified NAME, and a full title is a perfectly good one —
-- ZH/Steuergesetz works as well as ZH/StG. Titles are already in `laws`,
-- so every law gets a name for free and short names enrich the subset
-- that has them.
--
-- `qualified` is the canonical form: the canton is part of the name
-- because a cantonal name is unique only inside its canton — StG is the
-- tax act in ZH, BE and AG. Federal law takes no prefix and lives in
-- statutes.db.
--
-- Kept out of `laws` deliberately: one law has several names, they
-- arrive from different sources on different schedules, and a name can
-- be added without rebuilding the corpus.
CREATE TABLE IF NOT EXISTS law_names (
    canton      TEXT NOT NULL,
    language    TEXT NOT NULL,
    sr_number   TEXT NOT NULL,
    name        TEXT NOT NULL,
    -- Python casefold of `name`. SQLite's NOCASE collation folds a-z
    -- only, so "Bürgerrechtsgesetz" would never match its own lookup.
    -- Folding at build time keeps the comparison exact AND indexable,
    -- where a per-row callback would be neither.
    name_folded TEXT NOT NULL,
    -- 'abbreviation' | 'short_title' | 'title', best first when resolving
    -- and when choosing how to display the law.
    name_type   TEXT NOT NULL,
    qualified   TEXT,
    -- 'lexwork_api' (the canton published it), 'title' (derived from the
    -- title and acronym-checked) or 'corpus_title' (the stored title).
    -- A derived name must never outrank a published one, and a wrong
    -- entry has to be traceable to where it came from.
    source      TEXT NOT NULL,
    PRIMARY KEY (canton, language, sr_number, name)
);

CREATE INDEX IF NOT EXISTS idx_names_lookup ON law_names(canton, language, name_folded);
CREATE INDEX IF NOT EXISTS idx_names_qual   ON law_names(qualified);
CREATE INDEX IF NOT EXISTS idx_names_law    ON law_names(canton, sr_number);

CREATE TABLE IF NOT EXISTS articles (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    lexfind_id     INTEGER NOT NULL,
    language       TEXT NOT NULL,
    canton         TEXT NOT NULL,
    seq            INTEGER NOT NULL,
    article_num    TEXT,
    heading        TEXT,
    text           TEXT NOT NULL,
    FOREIGN KEY (lexfind_id, language) REFERENCES laws(lexfind_id, language)
);

CREATE INDEX IF NOT EXISTS idx_articles_law
    ON articles(lexfind_id, language);
CREATE INDEX IF NOT EXISTS idx_articles_canton
    ON articles(canton);

CREATE VIRTUAL TABLE IF NOT EXISTS articles_fts USING fts5(
    title,
    heading,
    article_num,
    text,
    lexfind_id UNINDEXED,
    canton UNINDEXED,
    language UNINDEXED,
    tokenize='unicode61 remove_diacritics 2'
);
"""


def _add_name(conn: sqlite3.Connection, canton: str, language: str,
              sr_number: str, name: str, name_type: str, source: str) -> int:
    name = (name or "").strip()
    if not name or not canton or not sr_number:
        return 0
    qualified = f"{canton.upper()}/{name}"
    try:
        conn.execute(
            """INSERT OR IGNORE INTO law_names
               (canton, language, sr_number, name, name_folded,
                name_type, qualified, source)
               VALUES (?,?,?,?,?,?,?,?)""",
            (canton.upper(), language, sr_number, name, name.casefold(),
             name_type, qualified, source),
        )
        return 1
    except sqlite3.Error:
        return 0


def _build_law_names(conn: sqlite3.Connection, output_dir: Path) -> int:
    """Give every cantonal law a canton-qualified name.

    Two passes. The titles already in `laws` come first and cost nothing,
    so every law is reachable as ZH/Steuergesetz whether or not anyone
    ever harvested a short form for it. Then the harvested abbreviations
    and short titles are layered on, giving the subset that has them the
    name a practitioner would actually type.
    """
    n = 0
    for row in conn.execute(
            "SELECT canton, language, sr_number, title FROM laws").fetchall():
        n += _add_name(conn, row[0], row[1], row[2], row[3],
                       "title", "corpus_title")
    log.info("Named %d laws from their titles", n)

    path = output_dir / "cantonal_abbreviations.jsonl"
    if not path.exists():
        log.info("No %s — laws are reachable by title only", path.name)
        return n
    # The harvest walks the cantons' own registers, which list laws this
    # corpus does not hold. Naming one of those would resolve a lookup to
    # a law we cannot then serve, so short names are only attached to
    # laws that are actually here.
    have = {(c, s) for c, s in conn.execute(
        "SELECT DISTINCT canton, sr_number FROM laws")}
    short = skipped = 0
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            canton, lang = (r.get("canton") or "").upper(), r.get("language")
            sr, src = r.get("sr_number"), r.get("source") or "unknown"
            if (canton, sr) not in have:
                skipped += 1
                continue
            short += _add_name(conn, canton, lang, sr,
                               r.get("abbreviation") or "", "abbreviation", src)
            short += _add_name(conn, canton, lang, sr,
                               r.get("short_title") or "", "short_title", src)
    if skipped:
        log.info("Skipped %d harvested names for laws absent from the corpus",
                 skipped)
    log.info("Added %d short names from %s", short, path.name)
    return n + short


def build(direct_dir: Path, lexfind_dir: Path, output_db: Path) -> None:
    # Resolve symlinks so the tmp file lives on the same filesystem for os.replace
    output_db = Path(os.path.realpath(output_db))
    output_db.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_db.with_suffix(output_db.suffix + ".tmp")
    if tmp_path.exists():
        tmp_path.unlink()

    # Collect JSONL files: direct takes priority over LexFind per canton
    canton_files: dict[str, Path] = {}

    # LexFind first (will be overridden by direct)
    if lexfind_dir.exists():
        for f in sorted(lexfind_dir.glob("*.jsonl")):
            canton_files[f.stem.upper()] = f

    # Direct sources override LexFind. The override is wholesale — a canton
    # comes entirely from one file — so a direct shard that is present but
    # truncated silently replaces the fallback with less than it held. The
    # "non-empty" test below is in bytes, which does not catch that: on
    # 2026-08-19 a stale 126 KB ZH shard holding 3 laws displaced 1,374, and
    # nothing in the log said so. Shadowing a much larger fallback is
    # therefore worth a warning, even though it is legitimate when the direct
    # scrape is simply more selective than LexFind's index.
    if direct_dir.exists():
        for f in sorted(direct_dir.glob("*.jsonl")):
            if f.stat().st_size > 0:  # Only override if non-empty
                canton = f.stem.upper()
                prev = canton_files.get(canton)
                if (prev is not None and canton not in LEXFIND_COMPLEMENT_CANTONS
                        and f.stat().st_size * 5 < prev.stat().st_size):
                    log.warning(
                        "[%s] direct shard is %.1f MB but shadows a %.1f MB "
                        "LexFind shard — check %s is current",
                        canton, f.stat().st_size / 1e6,
                        prev.stat().st_size / 1e6, f)
                canton_files[canton] = f

    if not canton_files:
        log.error("No JSONL files found in %s or %s", direct_dir, lexfind_dir)
        raise SystemExit(1)

    log.info("Building %s from %d cantons (direct: %s, lexfind: %s)",
             output_db, len(canton_files), direct_dir, lexfind_dir)
    conn = sqlite3.connect(tmp_path)
    conn.executescript(SCHEMA)
    conn.execute("PRAGMA journal_mode = MEMORY")
    conn.execute("PRAGMA synchronous = OFF")

    jsonl_files = [canton_files[c] for c in sorted(canton_files)]
    complements = {
        lexfind_dir / f"{c}.jsonl" for c in LEXFIND_COMPLEMENT_CANTONS
        if (lexfind_dir / f"{c}.jsonl").exists()
        and canton_files.get(c) != lexfind_dir / f"{c}.jsonl"
    }
    jsonl_files.extend(sorted(complements))
    log.info("Processing %d canton JSONL files", len(jsonl_files))

    # id -> the canton_sr it was minted from. Two DIFFERENT laws hashing
    # to one id would have one of them silently dropped by INSERT OR
    # REPLACE, and now that the ids are stable it would happen on every
    # build rather than once. The same law arriving twice is a different
    # thing (source duplicate, handled by the REPLACE) and must not trip
    # this, so the check is on the key, not on the id alone.
    synthetic_keys: dict[int, str] = {}
    total_laws = 0
    total_articles = 0
    for path in jsonl_files:
        canton = path.stem.upper()
        c_laws = 0
        c_arts = 0
        # When the text was last known current, not when this build ran: a
        # shard nobody refreshed must not look new. Rows carry their own date;
        # older rows take the date the shard was written.
        shard_date = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                   time.gmtime(path.stat().st_mtime))
        direct_held = set()
        if path in complements:
            direct_held = set(conn.execute(
                "SELECT sr_number, language FROM laws WHERE canton = ?",
                (canton,)))
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as e:
                    log.warning("[%s] bad JSON: %s", canton, e)
                    continue

                if path in complements and (
                        not row.get("is_active", True)
                        or (row.get("sr_number"), row.get("language") or "de")
                        in direct_held):
                    continue

                full_text = row.get("full_text") or ""
                articles = row.get("articles") or []
                lexfind_id = row.get("lexfind_id")
                if lexfind_id is None:
                    # Generate synthetic ID for direct-source laws
                    sr = row.get("sr_number")
                    if not sr:
                        continue
                    lexfind_id = _synthetic_id(
                        row.get("canton") or canton, sr
                    )
                    key = f"{row.get('canton') or canton}_{sr}"
                    prior = synthetic_keys.setdefault(lexfind_id, key)
                    if prior != key:
                        raise SystemExit(
                            f"synthetic id collision: {prior!r} and {key!r} "
                            f"both hash to {lexfind_id} — one law would be "
                            f"lost; change the digest width in _synthetic_id"
                        )
                language = row.get("language") or "de"

                conn.execute(
                    """INSERT OR REPLACE INTO laws
                    (lexfind_id, language, canton, sr_number, title, category,
                     is_active, original_url, version_active_since, text_length,
                     article_count, text_source, full_text, fetched_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        lexfind_id,
                        language,
                        row.get("canton") or canton,
                        row.get("sr_number"),
                        row.get("title") or "",
                        row.get("category"),
                        1 if row.get("is_active", True) else 0,
                        row.get("original_url"),
                        row.get("version_active_since"),
                        len(full_text),
                        len(articles),
                        row.get("text_source"),
                        full_text,
                        row.get("checked_at") or row.get("fetched_at") or shard_date,
                    ),
                )

                title = row.get("title") or ""
                for seq, art in enumerate(articles):
                    art_num = art.get("article_num")
                    heading = art.get("heading")
                    text = art.get("text") or ""
                    if not text and not heading:
                        continue
                    conn.execute(
                        """INSERT INTO articles
                        (lexfind_id, language, canton, seq, article_num, heading, text)
                        VALUES (?,?,?,?,?,?,?)""",
                        (lexfind_id, language, row.get("canton") or canton,
                         seq, art_num, heading, text),
                    )
                    conn.execute(
                        """INSERT INTO articles_fts
                        (title, heading, article_num, text, lexfind_id, canton, language)
                        VALUES (?,?,?,?,?,?,?)""",
                        (title, heading or "", art_num or "", text,
                         lexfind_id, row.get("canton") or canton, language),
                    )
                    c_arts += 1
                c_laws += 1

        log.info("[%s] %d laws, %d articles (%s)", canton, c_laws, c_arts,
                 "direct" if path.parent == direct_dir
                 else "lexfind complement" if path in complements else "lexfind")
        total_laws += c_laws
        total_articles += c_arts

    _build_law_names(conn, direct_dir.parent)
    log.info("Optimising FTS5 index...")
    conn.execute("INSERT INTO articles_fts(articles_fts) VALUES ('optimize')")
    conn.commit()
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.close()

    log.info("Atomic swap: %s → %s", tmp_path, output_db)
    os.replace(tmp_path, output_db)

    log.info(
        "DONE — %d laws, %d articles indexed. DB size: %.1f MB",
        total_laws, total_articles, output_db.stat().st_size / (1024 * 1024),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-direct", default=str(DIRECT_DIR),
                        help="Direct-source JSONL directory (priority)")
    parser.add_argument("--input-lexfind", default=str(LEXFIND_DIR),
                        help="LexFind JSONL directory (fallback)")
    parser.add_argument("--output", default=str(OUTPUT_DB))
    args = parser.parse_args()
    build(Path(args.input_direct), Path(args.input_lexfind), Path(args.output))


if __name__ == "__main__":
    main()
