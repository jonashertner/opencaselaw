"""Build materialien.db — legislative history for Swiss federal law.

Two data sources, merged into one DB:

1. **Fedlex statute footnotes** (automatic, covers 2,357 laws):
   Parse amendment references (AS/BBl) from existing statute article text
   in statutes.db.  Every article that was added or amended has footnotes
   like "( AS 2020 4525 ; BBl 2019 4747)" embedded in the text.  This
   gives instant coverage for 509 laws (6,554 articles with BBl refs,
   21,663 with AS refs) — no API calls needed.

2. **openlegalcommentary digests** (enriched, currently 1 law):
   Per-article structured JSON with legislative_intent, key_arguments,
   design_choices, rejected_alternatives.  Higher quality but requires
   LLM processing in the sister project.

Output: output/materialien.db (atomic swap via .db.tmp)

Usage:
    python3 -m search_stack.build_materialien_db
    python3 -m search_stack.build_materialien_db --input-dir data/materialien
    python3 -m search_stack.build_materialien_db --skip-fedlex  # digests only
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("build_materialien_db")

# Default input: look for JSON files in data/materialien/ within the repo,
# then fall back to the openlegalcommentary sibling directory.
DEFAULT_INPUT_DIRS = [
    Path("data/materialien"),
    Path("../openlegalcommentary/scripts/preparatory_materials"),
]

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS materialien (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    law_code TEXT NOT NULL,
    sr_number TEXT,
    article TEXT NOT NULL,
    bbl_ref TEXT NOT NULL,
    bbl_page_refs TEXT,
    legislative_intent TEXT,
    key_arguments TEXT,
    design_choices TEXT,
    rejected_alternatives TEXT,
    general_context TEXT
);

CREATE INDEX IF NOT EXISTS idx_materialien_law_art
    ON materialien(law_code, article);

CREATE TABLE IF NOT EXISTS parliamentary_modifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    law_code TEXT NOT NULL,
    council TEXT,
    date TEXT,
    text TEXT
);

CREATE INDEX IF NOT EXISTS idx_parl_mod_law
    ON parliamentary_modifications(law_code);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE VIRTUAL TABLE IF NOT EXISTS materialien_fts USING fts5(
    law_code,
    article,
    bbl_ref,
    legislative_intent,
    key_arguments,
    design_choices,
    rejected_alternatives,
    general_context,
    content=materialien,
    content_rowid=id,
    tokenize='unicode61 remove_diacritics 2'
);

-- Triggers to keep FTS in sync
CREATE TRIGGER IF NOT EXISTS materialien_ai AFTER INSERT ON materialien BEGIN
    INSERT INTO materialien_fts(
        rowid, law_code, article, bbl_ref,
        legislative_intent, key_arguments, design_choices,
        rejected_alternatives, general_context
    ) VALUES (
        new.id, new.law_code, new.article, new.bbl_ref,
        new.legislative_intent, new.key_arguments, new.design_choices,
        new.rejected_alternatives, new.general_context
    );
END;

CREATE TRIGGER IF NOT EXISTS materialien_ad AFTER DELETE ON materialien BEGIN
    INSERT INTO materialien_fts(
        materialien_fts, rowid, law_code, article, bbl_ref,
        legislative_intent, key_arguments, design_choices,
        rejected_alternatives, general_context
    ) VALUES (
        'delete', old.id, old.law_code, old.article, old.bbl_ref,
        old.legislative_intent, old.key_arguments, old.design_choices,
        old.rejected_alternatives, old.general_context
    );
END;

-- Amendment references extracted from Fedlex statute footnotes.
-- One row per (law, article, reference) — an article amended by
-- AS 2020 4525 with Botschaft BBl 2019 4747 produces two rows.
CREATE TABLE IF NOT EXISTS amendment_refs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sr_number TEXT NOT NULL,
    law_abbr TEXT,
    article TEXT NOT NULL,
    ref_type TEXT NOT NULL,     -- 'AS', 'BBl', 'FF', 'RO', 'RU'
    year INTEGER NOT NULL,
    page INTEGER NOT NULL,
    context TEXT,               -- surrounding text snippet (max 200 chars)
    fedlex_url TEXT             -- link to the Fedlex page for this reference
);

CREATE INDEX IF NOT EXISTS idx_amendment_refs_law_art
    ON amendment_refs(sr_number, article);
CREATE INDEX IF NOT EXISTS idx_amendment_refs_bbl
    ON amendment_refs(ref_type, year, page);

-- Parliamentary debate transcripts (Amtliches Bulletin).
-- Page-level chunks from OCR'd debate proceedings.
CREATE TABLE IF NOT EXISTS debate_pages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    law_code TEXT NOT NULL,
    council TEXT NOT NULL,     -- 'nationalrat' or 'staenderat'
    page_num INTEGER NOT NULL,
    text TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_debate_law_council
    ON debate_pages(law_code, council);

CREATE VIRTUAL TABLE IF NOT EXISTS debate_fts USING fts5(
    law_code,
    council,
    text,
    content=debate_pages,
    content_rowid=id,
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TRIGGER IF NOT EXISTS debate_ai AFTER INSERT ON debate_pages BEGIN
    INSERT INTO debate_fts(rowid, law_code, council, text)
    VALUES (new.id, new.law_code, new.council, new.text);
END;
"""

# Laws we know about (SR number mapping)
SR_NUMBERS: dict[str, str] = {
    "BV": "101", "ZGB": "210", "OR": "220", "ZPO": "272",
    "StGB": "311.0", "StPO": "312.0", "SchKG": "281.1",
    "VwVG": "172.021", "BGFA": "935.61",
}


# Regex for AS/BBl/FF/RO/RU references in statute footnotes
_REF_RE = re.compile(
    r"\b(AS|BBl|FF|RO|RU)\s+(\d{4})\s+(\d+)"
)

# Fedlex URL templates for each reference type
_FEDLEX_URL_TEMPLATES = {
    "AS": "https://www.fedlex.admin.ch/eli/oc/{year}/{page}",
    "RO": "https://www.fedlex.admin.ch/eli/oc/{year}/{page}",
    "RU": "https://www.fedlex.admin.ch/eli/oc/{year}/{page}",
    "BBl": "https://www.fedlex.admin.ch/eli/fga/{year}/{page}",
    "FF": "https://www.fedlex.admin.ch/eli/fga/{year}/{page}",
}


def extract_amendment_refs_from_statutes(
    conn: sqlite3.Connection, statutes_db_path: Path,
) -> int:
    """Extract AS/BBl references from statute article text and insert into amendment_refs.

    Parses the footnotes embedded in Fedlex statute articles. For example,
    StGB Art. 111 contains "( AS 2006 3459 ; BBl 1999 1979)" which yields
    two rows: one for AS 2006 3459 and one for BBl 1999 1979.

    Returns the number of rows inserted.
    """
    if not statutes_db_path.exists():
        logger.warning("statutes.db not found at %s — skipping Fedlex extraction", statutes_db_path)
        return 0

    try:
        src = sqlite3.connect(f"file:{statutes_db_path}?mode=ro", uri=True)
        src.row_factory = sqlite3.Row
    except sqlite3.Error as e:
        logger.warning("Failed to open statutes.db: %s", e)
        return 0

    try:
        # Build abbreviation lookup: sr_number → abbreviation
        abbr_map: dict[str, str] = {}
        try:
            for r in src.execute("SELECT sr_number, abbr_de FROM laws WHERE abbr_de IS NOT NULL AND abbr_de != ''"):
                abbr_map[r["sr_number"]] = r["abbr_de"]
        except sqlite3.Error:
            pass

        count = 0
        for row in src.execute("SELECT sr_number, article_num, text, footnote FROM articles WHERE lang = 'de'"):
            # Scan both body text AND footnote for AS/BBl references.
            # Body text catches amendments to existing paragraphs;
            # footnote catches INSERTED articles (footnote is on heading).
            combined = (row["text"] or "")
            footnote_text = ""
            try:
                footnote_text = row["footnote"] or ""
            except (IndexError, KeyError):
                pass  # footnote column may not exist in older DBs
            if footnote_text:
                combined += "\n" + footnote_text
            text = combined
            sr = row["sr_number"]
            article = row["article_num"]
            abbr = abbr_map.get(sr, "")

            for match in _REF_RE.finditer(text):
                ref_type = match.group(1)
                year = int(match.group(2))
                page = int(match.group(3))

                # Extract surrounding context (max 200 chars around the match)
                start = max(0, match.start() - 80)
                end = min(len(text), match.end() + 80)
                context = text[start:end].replace("\n", " ").strip()

                # Build Fedlex URL
                template = _FEDLEX_URL_TEMPLATES.get(ref_type)
                fedlex_url = template.format(year=year, page=page) if template else None

                conn.execute(
                    """INSERT INTO amendment_refs
                       (sr_number, law_abbr, article, ref_type, year, page, context, fedlex_url)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (sr, abbr, article, ref_type, year, page, context, fedlex_url),
                )
                count += 1

        return count
    finally:
        src.close()


def ingest_debate_transcripts(
    conn: sqlite3.Connection, input_dir: Path,
) -> int:
    """Ingest Amtliches Bulletin debate transcripts into debate_pages.

    Looks for files named ``{law}-ab-{council}.txt`` (e.g.,
    ``bv-ab-nationalrat.txt``) in the input directory.  Splits on
    ``[Page N]`` markers and stores each page as a searchable chunk.

    Returns total pages inserted.
    """
    count = 0
    for path in sorted(input_dir.glob("*-ab-*.txt")):
        # Parse filename: bv-ab-nationalrat.txt → law=BV, council=nationalrat
        parts = path.stem.split("-ab-")
        if len(parts) != 2:
            continue
        law_code = parts[0].upper()
        council = parts[1].lower()

        logger.info("  Ingesting %s %s debate transcript: %s", law_code, council, path.name)
        text = path.read_text(errors="replace")

        # Split by [Page N] markers
        page_chunks = re.split(r"\[Page\s+(\d+)\]", text)
        # page_chunks alternates: [pre_text, page_num, page_text, page_num, page_text, ...]
        if len(page_chunks) < 3:
            # No page markers found — store as single page
            conn.execute(
                "INSERT INTO debate_pages (law_code, council, page_num, text) VALUES (?,?,?,?)",
                (law_code, council, 1, text[:50000]),
            )
            count += 1
            continue

        for i in range(1, len(page_chunks) - 1, 2):
            try:
                page_num = int(page_chunks[i])
            except ValueError:
                continue
            page_text = page_chunks[i + 1].strip()
            if not page_text or len(page_text) < 20:
                continue
            # Cap per-page text to avoid bloat on malformed pages
            conn.execute(
                "INSERT INTO debate_pages (law_code, council, page_num, text) VALUES (?,?,?,?)",
                (law_code, council, page_num, page_text[:20000]),
            )
            count += 1

    return count


def _list_join(items: list) -> str:
    """Join a list of strings into a single searchable text block."""
    if not items:
        return ""
    return "\n".join(f"- {item}" for item in items)


def ingest_law(conn: sqlite3.Connection, path: Path) -> int:
    """Ingest a single law's digest JSON into the database.

    Returns the number of article-source rows inserted.
    """
    data = json.loads(path.read_text())
    law = data.get("law", "")
    sr = data.get("sr_number", SR_NUMBERS.get(law, ""))
    articles = data.get("articles", {})

    if not law or not articles:
        logger.warning("Skipping %s: no law code or articles", path.name)
        return 0

    # Track parliamentary modifications at law level (deduplicated)
    seen_mods: set[str] = set()
    count = 0

    for art_key, art_data in articles.items():
        for source in art_data.get("sources", []):
            conn.execute(
                """INSERT INTO materialien
                   (law_code, sr_number, article, bbl_ref, bbl_page_refs,
                    legislative_intent, key_arguments, design_choices,
                    rejected_alternatives, general_context)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    law,
                    sr,
                    art_key,
                    source.get("bbl_ref", ""),
                    json.dumps(source.get("bbl_page_refs", [])),
                    source.get("legislative_intent", ""),
                    _list_join(source.get("key_arguments", [])),
                    _list_join(source.get("design_choices", [])),
                    _list_join(source.get("rejected_alternatives", [])),
                    source.get("general_context", ""),
                ),
            )
            count += 1

        for mod in art_data.get("parliamentary_modifications", []):
            mod_key = f"{mod.get('council')}|{mod.get('date')}|{mod.get('text')}"
            if mod_key not in seen_mods:
                seen_mods.add(mod_key)
                conn.execute(
                    """INSERT INTO parliamentary_modifications
                       (law_code, council, date, text) VALUES (?, ?, ?, ?)""",
                    (law, mod.get("council", ""), mod.get("date", ""), mod.get("text", "")),
                )

    return count


_FTS5_VIRTUAL_RE = re.compile(
    r"CREATE\s+VIRTUAL\s+TABLE\b.*?\bUSING\s+fts5\s*\(", re.I | re.S,
)
_FTS5_CONTENT_RE = re.compile(
    r"\bcontent\s*=\s*(?:'([^']*)'|\"([^\"]*)\"|([A-Za-z0-9_]+))", re.I,
)


def _is_fts5_virtual_table(create_sql: str) -> bool:
    return bool(_FTS5_VIRTUAL_RE.search(create_sql or ""))


def _fts5_content_option(create_sql: str) -> str | None:
    """The ``content=`` option of an FTS5 CREATE statement.

    ``None`` when the option is absent (the FTS table stores its own rows
    in a ``_content`` shadow table), ``""`` for a contentless table, else
    the name of the external content table.
    """
    m = _FTS5_CONTENT_RE.search(create_sql or "")
    if not m:
        return None
    return next(g for g in m.groups() if g is not None)


def _fts5_shadow_tables(name: str) -> set[str]:
    return {f"{name}_{s}" for s in ("data", "idx", "docsize", "config", "content")}


def _copy_fts5_table(
    conn: sqlite3.Connection,
    name: str,
    create_sql: str,
    pending_rebuild: list[tuple[str, str]],
) -> int:
    """Copy an FTS5 table from the attached ``live`` DB, rowids intact.

    ``INSERT INTO fts SELECT * FROM live.fts`` — the copy used until
    2026-09-09 — drops the rowid: the rebuilt index numbers its rows
    1..N in scan order. With ``content_rowid='paragraph_id'`` that is
    only right while paragraph_id has no gap; the first re-ingest of a
    changed Botschaft (``DELETE FROM botschaft_paragraphs``) leaves one
    and every MATCH after the gap then returns the text of a different
    paragraph — an R1-R3 breach that no error surfaces. Reproduced with
    one gap, ``MATCH 'epsilon'`` returning another paragraph.

    External-content tables are queued for ``'rebuild'`` from the copied
    content table (the caller runs it once the live DB is detached);
    self-contained tables get an explicit rowid-preserving INSERT here.
    Contentless tables cannot be read back and are left empty.
    """
    content = _fts5_content_option(create_sql)
    if content:
        pending_rebuild.append((name, content))
        return 0
    if content == "":
        logger.warning(
            "FTS5 table %s is contentless; it cannot be copied and is left empty",
            name,
        )
        return 0
    cols = [
        row[1] for row in conn.execute(f"PRAGMA live.table_info(\"{name}\")")
    ]
    col_list = ", ".join(f"\"{c}\"" for c in cols)
    conn.execute(
        f"INSERT INTO \"{name}\"(rowid, {col_list}) "
        f"SELECT rowid, {col_list} FROM live.\"{name}\""
    )
    return conn.execute(f"SELECT COUNT(*) FROM \"{name}\"").fetchone()[0]


def build_db(
    input_dir: Path, output_path: Path, *, skip_fedlex: bool = False,
) -> None:
    """Build materialien.db from Fedlex footnotes + openlegalcommentary digests."""
    # Build to a .tmp file, then atomic swap. The .tmp lives next to the
    # REAL file, not next to the path we were given: on the VPS the DBs
    # over 500 MB are symlinks from output/ to the data volume, and a
    # cross-device rename at the swap below raises EXDEV (materialien.db
    # is due to move there; ops critic 2026-09-09). Same filesystem, or
    # the swap is not atomic either.
    tmp_path = Path(os.path.realpath(str(output_path))).with_suffix(".db.tmp")
    if tmp_path.exists():
        tmp_path.unlink()

    # uri=True so the live-DB ATTACH below can use a ?mode=ro URI (read-only,
    # no writer lock). busy_timeout so any residual lock contention with the
    # materialien.service botschaft writer waits instead of failing instantly
    # ("database live is locked" — the recurring Step 2f failure, 2026-05).
    conn = sqlite3.connect(f"file:{tmp_path}", uri=True)
    conn.execute("PRAGMA busy_timeout=60000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(SCHEMA_SQL)

    # ── PRESERVE: bulk-ingest tables from any prior materialien.db ──
    # Other pipelines write tables into materialien.db that this script
    # does not know about:
    #   * build_botschaft_corpus.py → botschaft_documents,
    #     botschaft_paragraphs (multi-hour SPARQL+PDF ingest)
    #   * enrich_botschaft_links.py → article_botschaft_links
    # Before 2026-05-27 the atomic-swap pattern below silently wiped
    # these tables every publish (Step 2f rebuilt a fresh .tmp from
    # SCHEMA_SQL only, then os.replace'd over the live DB). The daily
    # materialien.service then re-built them from scratch the next
    # morning — a ~5h round trip lost each day. Bug discovered when
    # Simon Betschmann's amendment-ref report triggered an audit
    # (2026-05-27); the same audit then traced the 12% coverage hit
    # in /article-purpose to this wipe-and-rebuild cycle.
    #
    # Fix: open the live DB read-only, attach it to the .tmp, and
    # copy any tables that are NOT in our SCHEMA_SQL before swap.
    _real_path = Path(os.path.realpath(str(output_path)))
    if _real_path.exists() and _real_path.stat().st_size > 4096:
        # READ-ONLY attach (CLAUDE.md invariant #1): this is a pure copy-out of
        # tables we don't own. mode=ro (NOT immutable — the live DB may be
        # concurrently written by materialien.service) takes no writer lock, so
        # it no longer collides with the 04:30 botschaft writer.
        conn.execute(f"ATTACH DATABASE 'file:{_real_path}?mode=ro' AS live")
        # Tables OUR schema owns — never preserve these from live
        # (they're being rebuilt from authoritative source data).
        _OWN_TABLES = {
            "materialien", "parliamentary_modifications", "meta",
            "amendment_refs", "debate_pages",
            "materialien_fts", "materialien_fts_data",
            "materialien_fts_idx", "materialien_fts_docsize",
            "materialien_fts_config",
            "debate_fts", "debate_fts_data", "debate_fts_idx",
            "debate_fts_docsize", "debate_fts_config",
            "sqlite_sequence",
        }
        preserved: list[tuple[str, int]] = []
        # External-content FTS5 tables (botschaft_paragraphs_fts) are
        # re-indexed from their content table AFTER the live DB is
        # detached — see _fts5_content_option and the rebuild below.
        pending_rebuild: list[tuple[str, str]] = []
        live_tables = conn.execute(
            "SELECT name, sql FROM live.sqlite_master "
            "WHERE type='table' AND sql IS NOT NULL ORDER BY rowid"
        ).fetchall()
        # FTS5 shadow tables (_data/_idx/_docsize/_config/_content) are
        # created by the virtual table itself; never copy them as plain
        # tables (before this list the CREATE failed and they were
        # skipped by accident, with a warning per table).
        shadow_tables: set[str] = set()
        for live_name, live_sql in live_tables:
            if _is_fts5_virtual_table(live_sql):
                shadow_tables.update(_fts5_shadow_tables(live_name))
        for live_name, live_sql in live_tables:
            if live_name in _OWN_TABLES or live_name in shadow_tables:
                continue
            # Recreate the table schema in the .tmp from the live
            # sqlite_master.sql definition, then copy the rows.
            try:
                conn.execute(live_sql)
                if _is_fts5_virtual_table(live_sql):
                    n = _copy_fts5_table(
                        conn, live_name, live_sql, pending_rebuild,
                    )
                else:
                    # Plain table: SELECT * carries every declared
                    # column, INTEGER PRIMARY KEY included, so rowids
                    # (paragraph_id, botschaft_id) survive the copy.
                    conn.execute(
                        f"INSERT INTO \"{live_name}\" SELECT * FROM live.\"{live_name}\""
                    )
                    n = conn.execute(
                        f"SELECT COUNT(*) FROM \"{live_name}\""
                    ).fetchone()[0]
                preserved.append((live_name, n))
            except sqlite3.Error as e:
                logger.warning(
                    "Could not preserve table %s: %s", live_name, e,
                )
                continue
            # Re-create any indexes/triggers on that table from live
            for (idx_sql,) in conn.execute(
                "SELECT sql FROM live.sqlite_master "
                "WHERE type IN ('index','trigger') AND tbl_name=? "
                "AND sql IS NOT NULL AND name NOT LIKE 'sqlite_%'",
                (live_name,),
            ).fetchall():
                try:
                    conn.execute(idx_sql)
                except sqlite3.Error:
                    pass
        # Commit the preserve-loop INSERTs BEFORE detaching: an open write
        # transaction on the attached DB makes DETACH fail with "database live is
        # locked" (the recurring Step 2f failure since the 2026-05-27 preserve
        # feature; long misdiagnosed as cross-process contention — cf2adb2/flock).
        conn.commit()
        conn.execute("DETACH DATABASE live")
        # Re-index external-content FTS5 tables from the content table
        # now living in the .tmp. Doing it after DETACH keeps the SHARED
        # lock on the live file to the raw copy only; the 04:30
        # botschaft writer's busy_timeout is 60 s and the tokenising is
        # the slow part (30 s today, minutes at 4-5M paragraphs).
        for fts_name, content_name in pending_rebuild:
            if not conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (content_name,),
            ).fetchone():
                logger.warning(
                    "FTS5 table %s: content table %s was not preserved, "
                    "index left empty", fts_name, content_name,
                )
                continue
            conn.execute(
                f"INSERT INTO \"{fts_name}\"(\"{fts_name}\") VALUES ('rebuild')"
            )
            n = conn.execute(
                f"SELECT COUNT(*) FROM \"{content_name}\""
            ).fetchone()[0]
            preserved = [
                (t, n if t == fts_name else c) for t, c in preserved
            ]
            logger.info(
                "Rebuilt FTS5 index %s from %s (%d rows)",
                fts_name, content_name, n,
            )
        conn.commit()
        if preserved:
            logger.info(
                "Preserved %d table(s) from live materialien.db: %s",
                len(preserved),
                ", ".join(f"{t}({n})" for t, n in preserved),
            )

    # ── Layer 1: Fedlex statute footnotes (AS/BBl refs) ──────────
    fedlex_count = 0
    if not skip_fedlex:
        statutes_db = Path(
            os.environ.get("SWISS_CASELAW_DIR", "output")
        ) / "statutes.db"
        logger.info("Extracting amendment refs from %s ...", statutes_db)
        fedlex_count = extract_amendment_refs_from_statutes(conn, statutes_db)
        logger.info("  Fedlex: %d amendment references extracted", fedlex_count)
        conn.commit()

    # ── Layer 2: openlegalcommentary digests ──────────────────────
    json_files = []
    if input_dir and input_dir.exists():
        json_files = sorted(
            p for p in input_dir.glob("*.json")
            if p.name != "registry.json" and p.stat().st_size > 100
        )

    digest_total = 0
    if json_files:
        logger.info("Found %d digest files in %s", len(json_files), input_dir)
        for path in json_files:
            law_name = path.stem.upper()
            n = ingest_law(conn, path)
            logger.info("  %s: %d article-source rows", law_name, n)
            digest_total += n

    # ── Layer 3: Parliamentary debate transcripts (AB) ─────────
    debate_count = 0
    if input_dir and input_dir.exists():
        debate_count = ingest_debate_transcripts(conn, input_dir)
        if debate_count:
            logger.info("  Debate transcripts: %d pages ingested", debate_count)
            conn.commit()

    if fedlex_count == 0 and digest_total == 0 and debate_count == 0:
        logger.error("No data found — provide digest JSON or ensure statutes.db exists")
        conn.close()
        tmp_path.unlink(missing_ok=True)
        sys.exit(1)

    # ── Metadata ─────────────────────────────────────────────────
    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES ('built_at', ?)",
        (datetime.now(timezone.utc).isoformat(),),
    )
    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES ('fedlex_refs', ?)",
        (str(fedlex_count),),
    )
    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES ('digest_rows', ?)",
        (str(digest_total),),
    )
    if json_files:
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('digest_laws', ?)",
            (json.dumps([p.stem.upper() for p in json_files]),),
        )

    # Count distinct laws + articles from both sources
    n_laws_fedlex = conn.execute(
        "SELECT COUNT(DISTINCT sr_number) FROM amendment_refs"
    ).fetchone()[0]
    n_arts_fedlex = conn.execute(
        "SELECT COUNT(DISTINCT sr_number || '/' || article) FROM amendment_refs"
    ).fetchone()[0]
    n_bbl = conn.execute(
        "SELECT COUNT(DISTINCT year || '/' || page) FROM amendment_refs WHERE ref_type IN ('BBl', 'FF')"
    ).fetchone()[0]
    logger.info(
        "Summary: %d Fedlex refs across %d laws / %d articles, %d distinct BBl, %d digest rows",
        fedlex_count, n_laws_fedlex, n_arts_fedlex, n_bbl, digest_total,
    )

    # Optimize FTS5 (only if digest rows exist)
    if digest_total > 0:
        conn.execute("INSERT INTO materialien_fts(materialien_fts) VALUES ('optimize')")
    conn.commit()

    # Switch to DELETE journal mode for immutable=1 compatibility
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.close()

    # Atomic swap. tmp_path was created next to the real file (see the
    # top of this function), so this is a same-filesystem rename: a
    # symlinked output/materialien.db keeps pointing at the volume and
    # the workers' immutable readers see the new inode on their next open.
    real_path = Path(os.path.realpath(str(output_path)))
    os.replace(str(tmp_path), str(real_path))

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input-dir", type=Path, default=None,
        help="Directory containing per-law digest JSON files "
             "(default: data/materialien/ or ../openlegalcommentary/scripts/preparatory_materials/)",
    )
    parser.add_argument(
        "--output", type=Path,
        default=Path(os.environ.get("SWISS_CASELAW_DIR", "output")) / "materialien.db",
        help="Output SQLite DB path",
    )
    parser.add_argument(
        "--skip-fedlex", action="store_true",
        help="Skip Fedlex amendment-ref extraction (digest files only)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    # Find input directory
    input_dir = args.input_dir
    if input_dir is None:
        for candidate in DEFAULT_INPUT_DIRS:
            if candidate.exists() and any(candidate.glob("*.json")):
                input_dir = candidate
                break
    if input_dir is None or not input_dir.exists():
        logger.error(
            "No input directory found. Provide --input-dir or place JSON files in %s",
            " or ".join(str(d) for d in DEFAULT_INPUT_DIRS),
        )
        sys.exit(1)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    build_db(input_dir, args.output, skip_fedlex=args.skip_fedlex)


if __name__ == "__main__":
    main()
