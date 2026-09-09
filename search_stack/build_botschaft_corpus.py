"""Verbatim Botschaft corpus — Phase 2 of the Materialien commitment.

Per ``MEMORY.md → materialien_build_commitment``:

    verbatim text + FTS5 + ELI URI links (NOT Sonnet digests);
    ~3 wks, $0 LLM. Fedlex SPARQL serves Acts/Consultations as
    first-class data; only AmtlBull needs parlament.ch separately.

This module extends ``output/materialien.db`` with a verbatim, paragraph-
level corpus of Federal Council Botschaften. The downstream MCP tool
``get_article_purpose(sr_number, article)`` joins:

    article_botschaft_links.sr_number/article
       → botschaft_documents
       → botschaft_paragraphs (where article_anchor matches)

…and returns verbatim paragraphs the LLM can quote with a
verifiable citation.

Pipeline
--------
    amendment_refs (existing)        → unique (year, page) tuples
                                     ↓
    fetch_bbl_pdf(year, page)        → Fedlex PDF URL → raw bytes
                                     ↓
    parse_botschaft(pdf_bytes)       → paragraph dicts with article anchors
                                     ↓
    upsert_paragraphs                → botschaft_paragraphs + FTS5
                                     ↓
    upsert_links                     → article_botschaft_links

Usage::

    # Schema migration (idempotent, safe to re-run)
    python3 -m search_stack.build_botschaft_corpus --schema-only

    # Ingest a single Botschaft (PoC validation)
    python3 -m search_stack.build_botschaft_corpus --ingest-one \\
        --year 1999 --page 6013 --sr 935.61

    # Ingest all unique BBl refs from amendment_refs (run nightly)
    python3 -m search_stack.build_botschaft_corpus --ingest-all
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from xml.etree import ElementTree as ET

# Reuse the existing Fedlex SPARQL manifestation resolver. v0.1 used a
# naive URL builder that returned the SPA HTML wrapper; this resolver
# returns actual filestore URLs. (Imported lazily so unit tests can
# monkeypatch without spinning up the SPARQL endpoint.)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    from scrapers.fedlex_materialien import fetch_manifestations  # noqa: E402
except ImportError:
    fetch_manifestations = None  # type: ignore[assignment]
try:
    from scrapers.fedlex_materialien import (  # noqa: E402
        DEFAULT_DOC_TYPE, FGA_DOC_TYPES, parse_doc_types,
    )
except ImportError:  # pragma: no cover - scrapers/ always ships with this module
    DEFAULT_DOC_TYPE = "botschaft"
    FGA_DOC_TYPES = {23: "botschaft"}

    def parse_doc_types(spec):  # type: ignore[misc]
        return (23,)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("build_botschaft_corpus")


# ── Fetch policy (2026-09-09) ─────────────────────────────────────────
#
# The --ingest-all loop talks to two Fedlex hosts per candidate: the SPARQL
# endpoint (manifestation lookup) and the file store (HEAD/GET). Before this
# policy a refusal was one more line in stats['errors'] and the loop kept
# going at the configured rate. Now a run stops on the first 429/403 or after
# STOP_AFTER_CONSECUTIVE_FAILURES failures in a row, and each failure widens
# the pause before the next candidate (exponential backoff, capped). A stop
# exits non-zero so the unit's && chain halts; every document commits on its
# own, so the next run resumes after the rows already stored.
FATAL_HTTP_STATUSES = frozenset({403, 429})
STOP_AFTER_CONSECUTIVE_FAILURES = 5
BACKOFF_BASE_SECONDS = 2.0
BACKOFF_MAX_SECONDS = 60.0
INGEST_STOPPED_EXIT_CODE = 4


class SourceUnavailable(RuntimeError):
    """A Fedlex lookup or download failed for a reason that is the host's,
    not the document's (HTTP error, timeout, refused connection). Carries
    the HTTP status when there was one so the loop can tell a 429 from a
    flaky read."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def http_status_of(exc: BaseException) -> int | None:
    """The HTTP status behind an exception, whichever client raised it:
    urllib (``.code``), requests (``.response.status_code``) or our own
    ``SourceUnavailable`` (``.status``). None when there is no status."""
    for attr in ("status", "code"):
        v = getattr(exc, attr, None)
        if isinstance(v, int) and 100 <= v < 600:
            return v
    resp = getattr(exc, "response", None)
    v = getattr(resp, "status_code", None)
    if isinstance(v, int) and 100 <= v < 600:
        return v
    return None


# ── Schema ────────────────────────────────────────────────────────────

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS botschaft_documents (
    botschaft_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    bbl_year        INTEGER NOT NULL,
    bbl_page        INTEGER NOT NULL,
    bbl_citation    TEXT NOT NULL,            -- "BBl 1999 6013"
    eli_uri         TEXT,                     -- https://fedlex.data.admin.ch/eli/fga/1999/6013
    title           TEXT,
    publication_date TEXT,
    source_url      TEXT NOT NULL,
    format          TEXT NOT NULL CHECK(format IN ('akoma-ntoso-xml','pdf')),
    language        TEXT NOT NULL,
    page_count      INTEGER,
    text_hash       TEXT,                     -- sha-256 of full text, for delta detection
    ingested_at     TEXT NOT NULL,
    doc_type        TEXT NOT NULL DEFAULT 'botschaft',  -- Fedlex resource-type slug (FGA_DOC_TYPES)
    UNIQUE(bbl_year, bbl_page, language)
);

CREATE INDEX IF NOT EXISTS ix_bd_eli ON botschaft_documents(eli_uri);

CREATE TABLE IF NOT EXISTS botschaft_paragraphs (
    paragraph_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    botschaft_id    INTEGER NOT NULL REFERENCES botschaft_documents(botschaft_id) ON DELETE CASCADE,
    para_order      INTEGER NOT NULL,
    page_number     INTEGER,
    section_path    TEXT,                     -- "Besonderer Teil > Zu Artikel 1"
    article_anchor  TEXT,                     -- "1" if paragraph belongs to a specific article
    text            TEXT NOT NULL,
    text_length     INTEGER NOT NULL,
    UNIQUE(botschaft_id, para_order)
);

CREATE INDEX IF NOT EXISTS ix_bp_anchor ON botschaft_paragraphs(article_anchor);
CREATE INDEX IF NOT EXISTS ix_bp_doc ON botschaft_paragraphs(botschaft_id);

CREATE VIRTUAL TABLE IF NOT EXISTS botschaft_paragraphs_fts USING fts5(
    text,
    section_path,
    article_anchor UNINDEXED,
    content='botschaft_paragraphs',
    content_rowid='paragraph_id',
    tokenize='unicode61 remove_diacritics 1'
);

-- Keep FTS5 in lockstep with the parent table.
CREATE TRIGGER IF NOT EXISTS botschaft_paragraphs_ai AFTER INSERT ON botschaft_paragraphs BEGIN
    INSERT INTO botschaft_paragraphs_fts(rowid, text, section_path, article_anchor)
    VALUES (new.paragraph_id, new.text, new.section_path, new.article_anchor);
END;
CREATE TRIGGER IF NOT EXISTS botschaft_paragraphs_ad AFTER DELETE ON botschaft_paragraphs BEGIN
    INSERT INTO botschaft_paragraphs_fts(botschaft_paragraphs_fts, rowid, text, section_path, article_anchor)
    VALUES ('delete', old.paragraph_id, old.text, old.section_path, old.article_anchor);
END;

CREATE TABLE IF NOT EXISTS article_botschaft_links (
    sr_number       TEXT NOT NULL,
    article         TEXT NOT NULL,
    botschaft_id    INTEGER NOT NULL REFERENCES botschaft_documents(botschaft_id) ON DELETE CASCADE,
    relation        TEXT NOT NULL CHECK(relation IN ('enacted','amended','considered')),
    evidence        TEXT,                     -- e.g. "amendment_refs row 12345"
    PRIMARY KEY (sr_number, article, botschaft_id, relation)
);

CREATE INDEX IF NOT EXISTS ix_abl_lookup ON article_botschaft_links(sr_number, article);
CREATE INDEX IF NOT EXISTS ix_abl_botschaft ON article_botschaft_links(botschaft_id);

-- Resume state of the historical (1850-1999) backfill from the Bundesarchiv
-- scans (search_stack/botschaft_historical.py). One row per (work, language);
-- 'done' rows point at their botschaft_documents row, 'error'/'not_found'
-- rows are retried on a later run, 'collision' rows lost the
-- UNIQUE(bbl_year, bbl_page, language) slot to another volume of the same
-- year. Lives in materialien.db so build_materialien_db's table copy
-- carries it through the nightly swap like the corpus tables.
CREATE TABLE IF NOT EXISTS botschaft_fetch_state (
    eli_uri         TEXT NOT NULL,
    language        TEXT NOT NULL,
    status          TEXT NOT NULL,
    http_status     INTEGER,
    attempts        INTEGER NOT NULL DEFAULT 0,
    first_attempt_at TEXT,
    last_attempt_at TEXT,
    error           TEXT,
    botschaft_id    INTEGER,
    source_url      TEXT,
    PRIMARY KEY (eli_uri, language)
);
CREATE INDEX IF NOT EXISTS ix_bfs_status ON botschaft_fetch_state(language, status);
"""

# Columns added after the table first shipped. Each is applied with
# ``ALTER TABLE … ADD COLUMN`` only when ``PRAGMA table_info`` lacks it, so
# the migration is additive, idempotent, and leaves every existing reader
# (mcp_server, build_materialien_db's table copy, generate_stats) working:
# rows written before the column existed read back as the default / NULL.
# Order matters: SQLite appends columns in this order, and readers that
# select them by name must tolerate their absence (fixture DBs built from
# older schemas). Append new columns at the END, never reorder.
_ADDITIVE_COLUMNS: tuple[tuple[str, str, str], ...] = (
    # (table, column, declaration)
    # jolux:dateDocument — the date the message is "vom" (2011-09-07 for
    # "Botschaft vom 7. September 2011"). publication_date holds
    # jolux:publicationDate, the Bundesblatt issue date.
    ("botschaft_documents", "date_document", "TEXT"),
    # Where publication_date came from: 'fedlex:publicationDate',
    # 'fedlex:dateDocument' (publicationDate missing on Fedlex) or
    # 'bbl_year' (neither on Fedlex — the value is just the BBl year).
    ("botschaft_documents", "date_source", "TEXT"),
    # Fedlex resource-type slug (FGA_DOC_TYPES); every row from before the
    # column existed IS a Botschaft.
    ("botschaft_documents", "doc_type", "TEXT NOT NULL DEFAULT 'botschaft'"),
    # 'bar-amtsdruckschriften' for the 1850-1999 scans
    # (search_stack/botschaft_historical.py); NULL for rows ingested from
    # Fedlex's file store.
    ("botschaft_documents", "source_collection", "TEXT"),
    # Bundesblatt volume (BBl 1975 I 609 → 1); NULL for the volume-less
    # post-1998 gazette.
    ("botschaft_documents", "bbl_volume", "INTEGER"),
)
_ADDITIVE_INDEXES: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS ix_bd_doctype ON botschaft_documents(doc_type)",
)


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")').fetchall()}


def migrate_additive(conn: sqlite3.Connection) -> list[str]:
    """Apply the guarded ``ALTER TABLE … ADD COLUMN`` migrations. Returns the
    ``table.column`` names actually added (empty when already current)."""
    added: list[str] = []
    for table, column, decl in _ADDITIVE_COLUMNS:
        have = _table_columns(conn, table)
        if not have or column in have:
            continue
        conn.execute(f'ALTER TABLE "{table}" ADD COLUMN {column} {decl}')
        added.append(f"{table}.{column}")
        log.info(f"  schema: added column {table}.{column} ({decl})")
    for ddl in _ADDITIVE_INDEXES:
        conn.execute(ddl)
    return added


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    migrate_additive(conn)
    conn.commit()


# publication_date / date_source resolution shared by the nightly ingest and
# scripts/backfill_botschaft_metadata.py.
DATE_SOURCE_PUBLICATION = "fedlex:publicationDate"
DATE_SOURCE_DOCUMENT = "fedlex:dateDocument"
DATE_SOURCE_BBL_YEAR = "bbl_year"


def resolve_publication_date(
    publication_date: str | None,
    date_document: str | None,
    bbl_year: int | None,
) -> tuple[str | None, str | None]:
    """``(publication_date, date_source)`` for a document row.

    Prefers Fedlex's ``jolux:publicationDate``; falls back to
    ``jolux:dateDocument``; when Fedlex has neither, the BBl citation year
    is stored (as a bare "YYYY") and ``date_source`` says so.
    """
    if publication_date:
        return publication_date, DATE_SOURCE_PUBLICATION
    if date_document:
        return date_document, DATE_SOURCE_DOCUMENT
    if bbl_year:
        return str(int(bbl_year)), DATE_SOURCE_BBL_YEAR
    return None, None


# Only Fedlex's own file store is fetched by this module. The BAR
# amtsdruckschriften portal (pre-1999 scans) has its own robots-aware
# fetcher; a manifestation hosted elsewhere is skipped here, never
# downloaded through this unthrottled path.
ALLOWED_FILE_HOSTS: frozenset[str] = frozenset({"fedlex.data.admin.ch"})


def file_host_allowed(url: str | None) -> bool:
    if not url:
        return False
    from urllib.parse import urlsplit
    return urlsplit(url).netloc.lower() in ALLOWED_FILE_HOSTS


# ── Fedlex BBl URL helpers ────────────────────────────────────────────

# v0.2 (2026-05-07): the v0.1 ``bbl_pdf_url`` returned the Fedlex SPA's
# HTML wrapper, not the actual PDF. Real downloads live on the SPARQL-
# resolved filestore URL (``https://fedlex.data.admin.ch/filestore/...``).
# Use ``resolve_manifestation()`` below.

# Format priority. Akoma Ntoso XML when available (clean, structured,
# avoids pdfplumber column-detection issues); plain XML next; PDF/A as
# the durable fallback. ``-an`` suffix marks Akoma Ntoso variants.
_FORMAT_PRIORITY = ("xml-an", "xml", "pdf-a-an", "pdf-a", "pdf")


def bbl_eli_uri(year: int, page: int) -> str:
    return f"https://fedlex.data.admin.ch/eli/fga/{year}/{page}"


def bbl_citation(year: int, page: int, language: str = "de") -> str:
    # German = Bundesblatt (BBl); French/Italian = Feuille fédérale / Foglio
    # federale, both cited "FF". The page is the print/PDF-edition gazette page.
    label = "BBl" if language == "de" else "FF"
    return f"{label} {year} {page}"


_LANG_URI_DEU = "http://publications.europa.eu/resource/authority/language/DEU"
_LANG_URI_FRA = "http://publications.europa.eu/resource/authority/language/FRA"
_LANG_URI_ITA = "http://publications.europa.eu/resource/authority/language/ITA"
_LANG_URI = {"de": _LANG_URI_DEU, "fr": _LANG_URI_FRA, "it": _LANG_URI_ITA}

# typeDocument vocabulary URIs that mark "this is a Federal Council
# Message". Probed empirically on 2026-05-07 against the live SPARQL
# endpoint:
#   td=23 → Botschaft (~1,874 with German title 'Botschaft*' + td=23)
#   td=21 → Bundesgesetz (law text — useless for purpose-of-article)
#   td=8  → Bundesbeschluss (federal decree)
#   td=33 → Mitteilung / Notifikation / Allgemeinverfügung (noise)
#   td=40 → Vernehmlassungsverfahren
# The Botschaft type is td=23. The other ~8,766 FGA URIs whose title
# starts 'Botschaft*' but have no typeDocument are caught by the title
# fallback below.
_BOTSCHAFT_TYPE_URI = "https://fedlex.data.admin.ch/vocabulary/resource-type/23"
_BOTSCHAFT_TITLE_PREFIX = {"de": "botschaft", "fr": "message", "it": "messaggio"}


def is_botschaft(eli_uri: str, language: str = "de") -> bool:
    """Two-pronged check: SPARQL ASK true if either the URI's
    typeDocument is the Botschaft vocabulary URI OR its title in the
    requested language starts with 'Botschaft' / 'Message' / 'Messaggio'.

    Filters out the ~204K noise FGA URIs (Mitteilungen, Allgemein-
    verfügungen, Notifikationen) before the expensive PDF/XML fetch.
    """
    if fetch_manifestations is None:  # SPARQL helper not importable
        return True  # fail-open: keep v0.2's behavior
    import urllib.parse
    import urllib.request
    import json as _json
    lang_iri = _LANG_URI.get(language, _LANG_URI_DEU)
    prefix = _BOTSCHAFT_TITLE_PREFIX.get(language, "botschaft")
    ask_q = f"""
    PREFIX jolux: <http://data.legilux.public.lu/resource/ontology/jolux#>
    ASK {{
      <{eli_uri}> jolux:isRealizedBy ?expr .
      ?expr jolux:language <{lang_iri}> .
      {{ <{eli_uri}> jolux:typeDocument <{_BOTSCHAFT_TYPE_URI}> . }}
      UNION
      {{ ?expr jolux:title ?title .
        FILTER(STRSTARTS(LCASE(STR(?title)), "{prefix}")) }}
    }}
    """
    body = urllib.parse.urlencode({"query": ask_q}).encode()
    req = urllib.request.Request(
        "https://fedlex.data.admin.ch/sparqlendpoint",
        data=body,
        headers={
            "Accept": "application/sparql-results+json",
            "User-Agent": "opencaselaw-materialien/0.3",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return bool(_json.loads(r.read()).get("boolean", False))
    except Exception as e:
        log.warning(f"  is_botschaft ASK failed for {eli_uri}: {e}")
        # fail-open: don't lose data on a transient SPARQL error
        return True


def resolve_manifestation(
    eli_uri: str,
    language: str = "de",
    strict_language: bool = True,
    skip_formats: tuple[str, ...] = (),
) -> tuple[str | None, str | None]:
    """Return ``(file_url, format)`` for the best manifestation in
    ``language``, or ``(None, None)`` if Fedlex SPARQL has no entry.

    With ``strict_language=True`` (the default since v0.3) we never fall
    back to a different language — get_article_purpose returning a
    French Botschaft to a German caller is a worse failure mode than
    returning empty (the caller can retry with the canonical language).
    Pass ``strict_language=False`` to opt back into v0.2 behaviour.

    Coverage: post-~2003 BBl publications. Pre-2003 returns no
    manifestations and callers must fall through to a different source
    (amtsdruckschriften.bar.admin.ch — v0.5 work).
    """
    if fetch_manifestations is None:
        return (None, None)
    try:
        mans = fetch_manifestations(eli_uri)
    except Exception as e:
        # A failed lookup is the endpoint's problem, not "no manifestation":
        # surface it (with the HTTP status when there is one) so the ingest
        # loop counts it and stops on a refusal, instead of skipping the
        # document as pre-2003 and moving on at full rate.
        log.warning(f"  SPARQL manifestation lookup failed for {eli_uri}: {e}")
        raise SourceUnavailable(
            f"SPARQL manifestation lookup failed for {eli_uri}: {e}",
            status=http_status_of(e),
        ) from e

    # Filter to requested language; prefer best format.
    same_lang = [m for m in mans if m.language == language]
    if strict_language:
        pool = same_lang
    else:
        pool = same_lang or mans  # any-language fallback (legacy)
    by_format: dict[str, str] = {m.format: m.file_url for m in pool}
    for fmt in _FORMAT_PRIORITY:
        if fmt in skip_formats:
            continue
        if fmt in by_format:
            return (by_format[fmt], fmt)
    # Otherwise return whatever's first (usually docx/html — last resort).
    if pool:
        for m in pool:
            if m.format not in skip_formats:
                return (m.file_url, m.format)
    return (None, None)


# ── Akoma Ntoso XML parser ────────────────────────────────────────────

_AKN_NS = "{http://docs.oasis-open.org/legaldocml/ns/akn/3.0}"


def _strip_ns(tag: str) -> str:
    """Strip the Akoma Ntoso namespace from an element tag."""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _akn_text(elem: ET.Element) -> str:
    """Concatenate all text within an element, normalising whitespace."""
    parts: list[str] = []
    for x in elem.iter():
        if x.text:
            parts.append(x.text)
        if x.tail:
            parts.append(x.tail)
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


def parse_akoma_ntoso_xml(xml_bytes: bytes) -> Iterator[dict]:
    """Parse a Fedlex Akoma Ntoso BBl document into paragraph dicts.

    Yielded shape matches ``parse_botschaft_text``:
        {para_order, page_number, section_path, article_anchor, text, text_length}

    Notes
    -----
    Akoma Ntoso uses ``<article eId="art_N">`` to anchor per-article
    sections. Sections / chapters provide the breadcrumb. ``<p>`` and
    ``<paragraph>`` carry the text. Many Fedlex FGA publications are
    metadata-only wrappers (no ``<body>``) — yielding 0 paragraphs is
    a valid signal to fall through to PDF.
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        log.warning(f"  XML parse error: {e}")
        return

    # Walk the body. If there's no body, we yield nothing.
    body = next(
        (x for x in root.iter() if _strip_ns(x.tag) in ("body", "mainBody")),
        None,
    )
    if body is None:
        return

    section_stack: list[str] = []
    current_anchor: str | None = None
    para_order = 0

    def _emit(text: str) -> dict | None:
        nonlocal para_order
        text = text.strip()
        if len(text) < 20:
            return None
        para_order += 1
        return {
            "para_order": para_order,
            "page_number": None,  # XML doesn't carry pages
            "section_path": " > ".join(section_stack) or None,
            "article_anchor": current_anchor,
            "text": text,
            "text_length": len(text),
        }

    for elem in body.iter():
        tag = _strip_ns(elem.tag)
        if tag == "article":
            # Anchor change. eId="art_41" → "41"
            eid = elem.attrib.get("eId") or elem.attrib.get("id") or ""
            m = re.match(r"art_(\d+[a-z]?)$", eid)
            if m:
                current_anchor = m.group(1)
        elif tag in ("chapter", "section", "subsection", "part"):
            heading_el = next(
                (c for c in elem if _strip_ns(c.tag) == "heading"), None,
            )
            if heading_el is not None:
                section_stack = [_akn_text(heading_el)[:80]]
        elif tag in ("p", "paragraph", "blockList", "intro"):
            # Skip nested paragraphs — outer iter() already visits them.
            # Only emit when this element has no <p>/<paragraph> children
            # (it's a leaf paragraph).
            has_inner = any(
                _strip_ns(c.tag) in ("p", "paragraph") for c in elem
            )
            if has_inner:
                continue
            row = _emit(_akn_text(elem))
            if row is not None:
                yield row


# ── Article-anchor parsing ────────────────────────────────────────────

# Header patterns Botschaften use for per-article comments. Captures
# the article number (with optional letter suffix like "41a"). Designed
# to match a line that starts with the marker after possible leading
# whitespace and a chapter indent — but NOT a mid-sentence reference
# like "wie in Artikel 41 vorgesehen".
ARTICLE_HEADER_RE = re.compile(
    r"""^\s{0,8}                       # optional indent
        (?:Zu\s+|Ad\s+|All['’]\s*)?    # German "Zu", French "Ad", Italian "All'"
        (?:Art(?:ikel|\.|icolo)?\.?)   # Art / Artikel / Articolo / Art.
        \s+
        (\d+[a-z]?)                    # captured article number, e.g. "41" or "41a"
        \b
        (?:.{0,80}?)?                  # optional title text on the same line
        $""",
    re.IGNORECASE | re.VERBOSE | re.MULTILINE,
)

# A "section header" = capitalised line of <= 80 chars surrounded by
# blank lines. Used to track section_path while walking the PDF.
SECTION_HEADER_RE = re.compile(
    r"^\s{0,8}([A-ZÄÖÜ][^a-z\n]{2,80}|\d+(?:\.\d+)*\s+[A-ZÄÖÜ].{0,80})\s*$",
    re.MULTILINE,
)


def parse_botschaft_text(
    pages: list[str],
    language: str = "de",
) -> Iterator[dict]:
    """Yield paragraph dicts (page_number, section_path, article_anchor, text).

    Input ``pages`` is a list of plain-text page strings (one per PDF page).
    Each non-empty paragraph (split on double newline) becomes one dict.
    The article anchor is set to the most recent ``Zu Art. N`` header
    above this paragraph, until the next such header is seen. The
    section path is a slash-joined trail of the recent section headers.
    """
    section_path_stack: list[str] = []
    current_anchor: str | None = None
    para_order = 0

    for page_idx, page_text in enumerate(pages, start=1):
        # Split on 2+ newlines to approximate paragraph boundaries.
        for chunk in re.split(r"\n\s*\n", page_text):
            chunk = chunk.strip()
            if len(chunk) < 20:
                continue

            # Update section trail if this chunk is itself a header.
            sh = SECTION_HEADER_RE.match(chunk)
            if sh and len(chunk.split("\n")) == 1:
                # Treat as section header — push and don't emit as paragraph.
                # Reset article anchor: a top-level section ("Übersicht",
                # "Schlussbestimmungen", …) ends the run of "Zu Art. N"
                # paragraphs. Without this reset the previous anchor
                # leaks into unrelated paragraphs of the next section.
                section_path_stack = [chunk]
                current_anchor = None
                continue

            # Detect article anchor on the FIRST line of the chunk.
            first_line = chunk.split("\n", 1)[0]
            ah = ARTICLE_HEADER_RE.match(first_line)
            if ah:
                current_anchor = ah.group(1)

            para_order += 1
            yield {
                "para_order": para_order,
                "page_number": page_idx,
                "section_path": " > ".join(section_path_stack) or None,
                "article_anchor": current_anchor,
                "text": chunk,
                "text_length": len(chunk),
            }


# ── Ingest one Botschaft ──────────────────────────────────────────────


def fetch_pdf_bytes(url: str, timeout: int = 60) -> bytes:
    import urllib.request
    log.info(f"  fetching {url}")
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "opencaselaw-materialien/0.3"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


_PART_SUFFIX_RE = re.compile(r"-(\d+)\.pdf$", re.IGNORECASE)


def _url_exists(url: str, timeout: int = 15) -> bool:
    """HEAD-probe a URL; True only if it returns 2xx AND the response
    Content-Type is ``application/pdf``.

    Fedlex sometimes responds 200 with an HTML SPA wrapper for missing
    PDF parts; we don't want those treated as real parts. Checking the
    declared Content-Type catches that case before we waste a GET.
    """
    import urllib.error
    import urllib.request
    req = urllib.request.Request(
        url,
        method="HEAD",
        headers={"User-Agent": "opencaselaw-materialien/0.3"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if not (200 <= r.status < 300):
                return False
            ct = (r.headers.get("Content-Type") or "").lower()
            return "application/pdf" in ct
    except urllib.error.HTTPError as e:
        if e.code in FATAL_HTTP_STATUSES:
            # Not "no part N" — the host is refusing us. Let the loop stop.
            raise SourceUnavailable(
                f"HEAD {url} answered HTTP {e.code}", status=e.code,
            ) from e
        return False
    except Exception:
        return False


def fetch_pdf_parts(
    first_url: str,
    timeout: int = 60,
    max_parts: int = 200,
) -> list[bytes]:
    """Fetch ``first_url`` and any sibling parts.

    Fedlex splits big Botschaften into ``…-1.pdf``, ``…-2.pdf``, …
    SPARQL only reports the first manifestation; we have to discover
    ``-2.pdf``, ``-3.pdf`` etc. by HEAD-probing siblings until we hit a
    404. Returns a list of raw bytes objects, one per part, in order.

    For URLs that don't follow the ``-N.pdf`` Fedlex convention this
    degrades to a single-element list (caller behaviour unchanged from
    v0.2).
    """
    parts: list[bytes] = [fetch_pdf_bytes(first_url, timeout=timeout)]
    m = _PART_SUFFIX_RE.search(first_url)
    if not m or int(m.group(1)) != 1:
        return parts  # not a multi-part URL we can extend
    prefix = first_url[: m.start()]
    for n in range(2, max_parts + 1):
        url = f"{prefix}-{n}.pdf"
        if not _url_exists(url, timeout=15):
            break
        log.info(f"  multi-part: discovered part {n}")
        parts.append(fetch_pdf_bytes(url, timeout=timeout))
    if len(parts) > 1:
        log.info(f"  multi-part: stitched {len(parts)} parts total")
    return parts


def extract_pdf_pages(pdf_bytes: bytes) -> list[str]:
    """Extract text per page using pdfplumber. Falls back to empty
    string for pages that fail. Returns one entry per page."""
    import io
    try:
        import pdfplumber
    except ImportError as e:
        raise SystemExit(
            "pdfplumber not installed. Run: pip install pdfplumber"
        ) from e

    pages_out: list[str] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            try:
                pages_out.append(page.extract_text() or "")
            except Exception as e:
                log.warning(f"    page extract failed: {e}")
                pages_out.append("")
    return pages_out


def _fill_metadata(
    conn: sqlite3.Connection,
    botschaft_id: int,
    title: str | None,
    publication_date: str | None,
    date_source: str | None,
    date_document: str | None,
) -> bool:
    """Fill empty metadata on an existing document row. Never overwrites a
    Fedlex-sourced value with an empty one; a Fedlex date does replace a
    bbl_year fallback. Returns True when a column changed."""
    cur = conn.execute(
        "SELECT title, publication_date, date_document, date_source "
        "FROM botschaft_documents WHERE botschaft_id = ?", (botschaft_id,),
    ).fetchone()
    if cur is None:
        return False
    old_title, old_pub, old_doc, old_src = cur
    new_title = old_title or title
    if publication_date and (not old_pub or old_src == DATE_SOURCE_BBL_YEAR
                             and date_source != DATE_SOURCE_BBL_YEAR):
        new_pub, new_src = publication_date, date_source
    else:
        new_pub, new_src = old_pub, old_src
    new_doc = old_doc or date_document
    if (new_title, new_pub, new_doc, new_src) == (old_title, old_pub, old_doc, old_src):
        return False
    conn.execute(
        "UPDATE botschaft_documents SET title=?, publication_date=?, "
        "date_document=?, date_source=? WHERE botschaft_id=?",
        (new_title, new_pub, new_doc, new_src, botschaft_id),
    )
    return True


def heal_existing_metadata(
    conn: sqlite3.Connection,
    sparql_rows: list[dict],
    language: str,
    min_year: int = 0,
) -> dict:
    """Fill title / publication_date on rows the nightly run will NOT
    re-fetch, from the discovery metadata it already holds.

    ``main --discover-via-sparql`` drops every (year, page) already present in
    ``botschaft_documents`` before ``ingest_one`` runs, so the fill inside
    ``ingest_one`` never reaches an old row. This pass is what makes the
    nightly run self-healing: one indexed SELECT of the rows in ``language``
    still lacking a title or a Fedlex date (or carrying only the ``bbl_year``
    fallback), then ``_fill_metadata`` per row with the SPARQL title/dates
    for its (year, page) — no fetch, no text change, one commit. Rows whose
    (year, page) discovery did not return are left alone.
    ``scripts/backfill_botschaft_metadata.py`` remains the bulk path (it
    asks Fedlex for ELIs discovery no longer lists).
    """
    stats = {"candidates": 0, "healed": 0, "no_discovery_meta": 0}
    by_key = {(m["year"], m["page"]): m for m in sparql_rows}
    rows = conn.execute(
        "SELECT botschaft_id, bbl_year, bbl_page FROM botschaft_documents "
        "WHERE language = ? AND bbl_year >= ? AND ("
        "title IS NULL OR title = '' OR publication_date IS NULL OR "
        "publication_date = '' OR date_source = ?)",
        (language, min_year, DATE_SOURCE_BBL_YEAR),
    ).fetchall()
    stats["candidates"] = len(rows)
    for botschaft_id, year, page in rows:
        m = by_key.get((year, page))
        if m is None:
            stats["no_discovery_meta"] += 1
            continue
        pub_date, date_source = resolve_publication_date(
            m.get("publication_date"), m.get("date_document"), year,
        )
        if _fill_metadata(conn, botschaft_id, m.get("title"), pub_date,
                          date_source, m.get("date_document")):
            stats["healed"] += 1
    conn.commit()
    return stats


def ingest_one(
    conn: sqlite3.Connection,
    year: int,
    page: int,
    language: str = "de",
    sr_number: str | None = None,
    article: str | None = None,
    relation: str = "considered",
    prefiltered: bool = False,
    eli_uri: str | None = None,
    title: str | None = None,
    publication_date: str | None = None,
    date_document: str | None = None,
    doc_type: str = DEFAULT_DOC_TYPE,
) -> int | None:
    """Fetch, parse, and store one Bundesblatt document. Returns
    botschaft_id, or None if Fedlex has no manifestation (pre-~2003 BBl),
    the URI isn't a real Botschaft, its file lives off fedlex.data.admin.ch,
    or its citation page is already taken by a different document.

    Pass ``prefiltered=True`` when the caller has already validated the
    URI's type (e.g. SPARQL discovery via typeDocument) — it skips the
    per-URI is_botschaft ASK probe. ``doc_type`` is the FGA_DOC_TYPES slug
    stored on the row ('botschaft', 'bericht_bundesrat', …); the parse and
    anchor path is identical for every type.

    ``title`` / ``publication_date`` / ``date_document`` are the Fedlex
    SPARQL metadata for this Work in ``language`` (see
    ``discover_fga_documents``). They are stored verbatim on the document
    row; an existing row that lacks them is filled in even when its text is
    unchanged. Note that ``main --discover-via-sparql`` never calls this for
    a (year, page) already in the table — old rows are healed by
    ``heal_existing_metadata`` (no fetch), which main() runs on every SPARQL
    pass.
    """
    ensure_schema(conn)
    if doc_type not in FGA_DOC_TYPES.values():
        raise ValueError(f"Unknown doc_type {doc_type!r}")
    pub_date, date_source = resolve_publication_date(publication_date, date_document, year)

    citation = bbl_citation(year, page, language)
    # The ELI keeps its real fga segment (for fetch/resolve); the citation +
    # bbl_page use the printed memorialPage. These differ (issue #30), so never
    # rebuild the ELI from ``page``.
    eli = eli_uri if eli_uri else bbl_eli_uri(year, page)
    log.info(f"Ingesting {citation} ({language}) for sr={sr_number} art={article}")

    # v0.3 (2026-05-07): pre-filter via Fedlex SPARQL — only ingest
    # publications whose typeDocument == resource-type/23 OR whose
    # title starts with 'Botschaft'. Drops Mitteilungen / decrees /
    # notices that the amendment_refs index pulls in as false matches.
    if not prefiltered:
        if not is_botschaft(eli, language=language):
            log.info(
                f"  skipping {citation} ({language}) — not a Botschaft "
                f"(typeDocument != 23 AND title doesn't start with 'Botschaft')"
            )
            return None

    url, fmt = resolve_manifestation(eli, language=language)
    if url is None:
        log.warning(
            f"  no Fedlex manifestation for {citation} ({language}) — "
            f"pre-2003 publications need v0.3 amtsdruckschriften adapter"
        )
        return None
    if not file_host_allowed(url):
        log.info(
            f"  skipping {citation} ({language}) — file hosted off Fedlex "
            f"({url[:100]}); the BAR portal is not part of this ingest"
        )
        return None
    log.info(f"  resolved → {fmt}: {url[:120]}")

    # Identity: the ELI is the stable key; (year, page, language) is the
    # citation and the table's UNIQUE constraint. Match the ELI first so a
    # re-ingest never binds to a different work that happens to share a
    # gazette page, and refuse to overwrite such a neighbour.
    row = conn.execute(
        "SELECT botschaft_id, text_hash, bbl_year, bbl_page FROM botschaft_documents "
        "WHERE eli_uri=? AND language=?",
        (eli, language),
    ).fetchone()
    if row is not None and (row[2], row[3]) != (year, page):
        log.warning(
            f"  {eli} ({language}) already stored as BBl {row[2]} {row[3]}, "
            f"now discovered as {citation} — keeping the stored citation"
        )
        year, page = row[2], row[3]
    if row is None:
        row = conn.execute(
            "SELECT botschaft_id, text_hash, eli_uri FROM botschaft_documents "
            "WHERE bbl_year=? AND bbl_page=? AND language=?",
            (year, page, language),
        ).fetchone()
        if row is not None and row[2] and row[2] != eli:
            log.warning(
                f"  citation-page collision: {citation} ({language}) is held by "
                f"{row[2]}, refusing to overwrite it with {eli}"
            )
            return None

    # v0.3 (2026-05-07): for PDF formats we probe Fedlex's multi-part
    # convention (``-1.pdf``/``-2.pdf``…) so big Botschaften aren't
    # truncated to their first part. XML formats are single-file.
    if fmt and fmt.startswith("pdf"):
        parts = fetch_pdf_parts(url)
    else:
        parts = [fetch_pdf_bytes(url)]
    # Hash the concatenation so a new part triggers re-ingest.
    text_hash = hashlib.sha256(b"".join(parts)).hexdigest()
    raw = parts[0]  # XML parser only needs the first (single) part

    if row and row[1] == text_hash:
        log.info(f"  unchanged ({citation}) — keeping existing rows")
        botschaft_id = row[0]
        _fill_metadata(conn, botschaft_id, title, pub_date, date_source, date_document)
    else:
        # Parse: prefer XML/Akoma Ntoso, fall back to PDF.
        paragraphs: list[dict] = []
        page_count = 0
        if fmt and fmt.startswith("xml"):
            paragraphs = list(parse_akoma_ntoso_xml(raw))
            log.info(f"  XML parsed {len(paragraphs)} paragraphs")
            if not paragraphs:
                # XML was an FRBR metadata wrapper (common in Fedlex —
                # no <body> content). Re-resolve with PDF format and
                # try the PDF parser.
                log.info("  XML had 0 paragraphs — retrying with PDF format")
                pdf_url, pdf_fmt = resolve_manifestation(
                    eli, language=language,
                    skip_formats=("xml-an", "xml"),
                )
                if pdf_url and pdf_fmt and pdf_fmt.startswith("pdf"):
                    parts = fetch_pdf_parts(pdf_url)
                    text_hash = hashlib.sha256(b"".join(parts)).hexdigest()
                    fmt = pdf_fmt
                    url = pdf_url
                else:
                    parts = []  # nothing else to try

        if not paragraphs:
            # PDF path — either fmt was pdf-a from the start, or we
            # just re-resolved to PDF after empty XML.
            try:
                pdf_pages: list[str] = []
                for part_bytes in parts:
                    pdf_pages.extend(extract_pdf_pages(part_bytes))
                page_count = len(pdf_pages)
                paragraphs = list(parse_botschaft_text(pdf_pages, language=language))
                log.info(
                    f"  PDF parsed {page_count} pages "
                    f"({len(parts)} part{'s' if len(parts) != 1 else ''}) "
                    f"→ {len(paragraphs)} paragraphs"
                )
            except Exception as e:
                log.warning(f"  PDF parse failed: {e}")
                paragraphs = []

        # Storage format label: keep XML if we successfully parsed XML,
        # else mark PDF.
        stored_format = "akoma-ntoso-xml" if fmt and fmt.startswith("xml") and paragraphs else "pdf"

        if row:
            conn.execute(
                "DELETE FROM botschaft_paragraphs WHERE botschaft_id = ?",
                (row[0],),
            )
            conn.execute(
                "UPDATE botschaft_documents SET text_hash=?, page_count=?, "
                "format=?, source_url=?, ingested_at=?, doc_type=? "
                "WHERE botschaft_id=?",
                (text_hash, page_count, stored_format, url,
                 datetime.now(timezone.utc).isoformat(), doc_type, row[0]),
            )
            botschaft_id = row[0]
            _fill_metadata(conn, botschaft_id, title, pub_date, date_source, date_document)
        else:
            cur = conn.execute(
                """
                INSERT INTO botschaft_documents
                (bbl_year, bbl_page, bbl_citation, eli_uri, source_url,
                 format, language, page_count, text_hash, ingested_at,
                 title, publication_date, date_document, date_source,
                 doc_type)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (year, page, citation, eli, url, stored_format, language,
                 page_count, text_hash,
                 datetime.now(timezone.utc).isoformat(),
                 title, pub_date, date_document, date_source, doc_type),
            )
            botschaft_id = cur.lastrowid

        n_paras = 0
        for p in paragraphs:
            conn.execute(
                """
                INSERT INTO botschaft_paragraphs
                (botschaft_id, para_order, page_number, section_path,
                 article_anchor, text, text_length)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (botschaft_id, p["para_order"], p["page_number"],
                 p["section_path"], p["article_anchor"],
                 p["text"], p["text_length"]),
            )
            n_paras += 1
        log.info(f"  inserted {n_paras} paragraphs into botschaft_paragraphs")

    if sr_number and article:
        conn.execute(
            """
            INSERT OR IGNORE INTO article_botschaft_links
            (sr_number, article, botschaft_id, relation, evidence)
            VALUES (?, ?, ?, ?, ?)
            """,
            (sr_number, article, botschaft_id, relation,
             "build_botschaft_corpus.ingest_one v0.2"),
        )
    conn.commit()
    return botschaft_id


# ── CLI ────────────────────────────────────────────────────────────────


def link_from_amendment_refs(conn: sqlite3.Connection) -> dict:
    """Populate ``article_botschaft_links`` from ``amendment_refs``.

    For every Botschaft already in ``botschaft_documents``, find every
    matching ``amendment_refs`` row (same year + page) and create a link
    row tying the Botschaft to that article. Idempotent — uses
    ``INSERT OR IGNORE`` against the link table's PK.

    The relation is set to ``considered`` because ``amendment_refs``
    doesn't carry enacted-vs-amended semantics; a future enrichment can
    upgrade specific links via Fedlex's history API.

    Returns a stats dict.
    """
    ensure_schema(conn)
    # Sanity: the existing materialien.db builder may not have created
    # amendment_refs yet on a fresh checkout.
    try:
        conn.execute("SELECT 1 FROM amendment_refs LIMIT 1").fetchone()
    except sqlite3.OperationalError:
        log.warning(
            "  amendment_refs table not present — run build_materialien_db "
            "first to populate it from statute footnotes"
        )
        return {"linked": 0, "reason": "no_amendment_refs"}

    before = conn.execute(
        "SELECT COUNT(*) FROM article_botschaft_links"
    ).fetchone()[0]

    cur = conn.execute(
        """
        INSERT OR IGNORE INTO article_botschaft_links
            (sr_number, article, botschaft_id, relation, evidence)
        SELECT DISTINCT
            ar.sr_number,
            ar.article,
            bd.botschaft_id,
            'considered' AS relation,
            'amendment_refs:' || ar.id AS evidence
        FROM amendment_refs ar
        JOIN botschaft_documents bd
          ON bd.bbl_year = ar.year AND bd.bbl_page = ar.page
        WHERE ar.ref_type = 'BBl'
          AND ar.sr_number IS NOT NULL
          AND ar.article IS NOT NULL
          AND ar.year IS NOT NULL
          AND ar.page IS NOT NULL
        """
    )
    conn.commit()
    after = conn.execute(
        "SELECT COUNT(*) FROM article_botschaft_links"
    ).fetchone()[0]
    new_links = after - before
    log.info(
        f"  article_botschaft_links: {before} → {after} (+{new_links} new)"
    )

    # Coverage breakdown
    by_sr = conn.execute(
        """
        SELECT sr_number, COUNT(*) AS n
        FROM article_botschaft_links
        GROUP BY sr_number
        ORDER BY n DESC
        LIMIT 5
        """
    ).fetchall()
    if by_sr:
        log.info("  top SR coverage: " + ", ".join(
            f"{s}={n}" for s, n in by_sr
        ))
    return {
        "linked": new_links,
        "total_links": after,
        "top_sr": [{"sr_number": s, "links": n} for s, n in by_sr],
    }


# ── Candidates ────────────────────────────────────────────────────────
#
# One dict per document to ingest. Built from SPARQL discovery
# (``candidate_from_discovery``) or from amendment_refs; ``ingest_candidates``
# also accepts the legacy ``(year, page, eli, doc_type)`` tuple.
_CANDIDATE_KEYS = ("year", "page", "eli_uri", "doc_type", "title",
                   "publication_date", "date_document")


def candidate_from_discovery(d: dict) -> dict:
    """A candidate dict from one ``discover_fga_documents`` row. Missing
    metadata keys read as None; a missing ``doc_type`` is a Botschaft."""
    return {
        "year": int(d["year"]),
        "page": int(d["page"]),
        "eli_uri": d.get("eli_uri"),
        "doc_type": d.get("doc_type") or DEFAULT_DOC_TYPE,
        "title": d.get("title") or None,
        "publication_date": d.get("publication_date") or None,
        "date_document": d.get("date_document") or None,
    }


def as_candidate(c) -> dict:
    """Normalise a candidate: a dict is returned as is (missing keys read as
    None via ``.get``); a ``(year, page[, eli[, doc_type]])`` tuple becomes
    a dict."""
    if isinstance(c, dict):
        return c
    seq = list(c)
    return {
        "year": seq[0],
        "page": seq[1],
        "eli_uri": seq[2] if len(seq) > 2 else None,
        "doc_type": (seq[3] if len(seq) > 3 else None) or DEFAULT_DOC_TYPE,
    }


def ingest_candidates(
    conn: sqlite3.Connection,
    candidates: list,
    *,
    language: str,
    prefiltered: bool,
    rate_limit: float,
    max_consecutive_failures: int = STOP_AFTER_CONSECUTIVE_FAILURES,
    sleep=None,
    log_every: int = 25,
) -> dict:
    """Run ``ingest_one`` over ``candidates`` — candidate dicts (see
    ``as_candidate``: ``year``, ``page``, ``eli_uri``, ``doc_type`` and the
    Fedlex ``title`` / ``publication_date`` / ``date_document``), newest
    first — under the fetch policy at the top of this module.

    Returns the stats dict. ``stats['stopped']`` is None for a run that saw
    every candidate, else the reason: ``'http_429'`` / ``'http_403'`` (the
    host refused; stop at once) or ``'N_consecutive_failures'``. Rows
    committed before a stop are kept (``ingest_one`` commits per document),
    so the next run's dedup resumes after them.

    ``sleep`` is injectable for tests; the pause after a failure is
    ``BACKOFF_BASE_SECONDS * 2**(n-1)`` capped at ``BACKOFF_MAX_SECONDS``
    and replaces the rate-limit sleep for that candidate.
    """
    import time as _time
    sleep = sleep or _time.sleep
    total = len(candidates)
    stats = {
        "accepted": 0, "rejected_by_filter": 0, "errors": 0,
        "total": total, "processed": 0, "stopped": None,
        "max_consecutive_failures_seen": 0,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    consecutive = 0
    t0 = _time.time()
    for i, cand in enumerate(candidates, 1):
        c = as_candidate(cand)
        year, page, eli = c["year"], c["page"], c.get("eli_uri")
        stats["processed"] = i
        pause = rate_limit
        try:
            bid = ingest_one(
                conn, year=year, page=page, eli_uri=eli,
                language=language,
                sr_number=None, article=None,
                relation="considered",
                prefiltered=prefiltered,
                title=c.get("title"),
                publication_date=c.get("publication_date"),
                date_document=c.get("date_document"),
                doc_type=c.get("doc_type") or DEFAULT_DOC_TYPE,
            )
        except Exception as e:
            stats["errors"] += 1
            consecutive += 1
            stats["max_consecutive_failures_seen"] = max(
                stats["max_consecutive_failures_seen"], consecutive)
            status = http_status_of(e)
            log.warning(f"  BBl {year} {page} ({language}) failed: {e}")
            if status in FATAL_HTTP_STATUSES:
                stats["stopped"] = f"http_{status}"
            elif consecutive >= max_consecutive_failures:
                stats["stopped"] = f"{consecutive}_consecutive_failures"
            else:
                pause = min(BACKOFF_MAX_SECONDS,
                            BACKOFF_BASE_SECONDS * 2 ** (consecutive - 1))
                log.warning(
                    f"  backing off {pause:.0f}s after {consecutive} "
                    f"consecutive failure{'s' if consecutive != 1 else ''}"
                )
        else:
            consecutive = 0
            if bid is None:
                stats["rejected_by_filter"] += 1
            else:
                stats["accepted"] += 1
        if stats["stopped"]:
            log.error(
                f"  stopping this run ({stats['stopped']}) at {i}/{total}; "
                "committed rows are kept, the next run resumes after them"
            )
            break
        sleep(pause)
        if i % log_every == 0 or i == total:
            elapsed = _time.time() - t0
            rate = i / elapsed if elapsed > 0 else 0
            eta = (total - i) / rate if rate > 0 else 0
            log.info(
                f"  progress {i}/{total} "
                f"(accepted={stats['accepted']} "
                f"filtered={stats['rejected_by_filter']} "
                f"errors={stats['errors']}) "
                f"rate={rate:.2f}/s eta={int(eta/60)}min"
            )
    stats["elapsed_seconds"] = round(_time.time() - t0, 2)
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--db",
        default=str(Path("output/materialien.db")),
        help="materialien.db path",
    )
    parser.add_argument(
        "--schema-only", action="store_true",
        help="just create the new tables and exit",
    )
    parser.add_argument(
        "--ingest-one", action="store_true",
        help="ingest a single Botschaft (PoC); requires --year + --page",
    )
    parser.add_argument("--year", type=int)
    parser.add_argument("--page", type=int)
    parser.add_argument("--sr", help="SR number to link the article to")
    parser.add_argument(
        "--doc-type", default=DEFAULT_DOC_TYPE,
        choices=sorted(set(FGA_DOC_TYPES.values())),
        help="doc_type slug stored by --ingest-one (default botschaft)",
    )
    parser.add_argument("--article", help="article number (links to the Botschaft)")
    parser.add_argument(
        "--language", default="de", choices=["de", "fr", "it"],
    )
    parser.add_argument(
        "--ingest-all", action="store_true",
        help="discover BBl refs from amendment_refs and ingest all Botschaften (live)",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Cap the number of candidates processed in --ingest-all (smoke-test).",
    )
    parser.add_argument(
        "--rate-limit", type=float, default=0.3,
        help="Seconds to sleep between fetches in --ingest-all (default 0.3).",
    )
    parser.add_argument(
        "--max-per-run", type=int, default=None,
        help=(
            "Cap per --ingest-all run: documents processed (Fedlex path, "
            "newest first) or HTTP requests made (historical path, one "
            "night's budget); the rest wait for the next run. Unlike --limit "
            "this is a production setting: it bounds a catch-up night."
        ),
    )
    parser.add_argument(
        "--max-consecutive-failures", type=int,
        default=STOP_AFTER_CONSECUTIVE_FAILURES,
        help=(
            "Stop the run (exit 4) after this many failed candidates in a "
            f"row (default {STOP_AFTER_CONSECUTIVE_FAILURES}). Any HTTP "
            "429/403 stops it at once regardless."
        ),
    )
    parser.add_argument(
        "--min-year", type=int, default=None,
        help=(
            "Lowest BBl year to consider in --ingest-all (default 2003, where "
            "Fedlex's own file store starts; 1850 with --discover-untyped-by-title)."
        ),
    )
    parser.add_argument(
        "--max-year", type=int, default=None,
        help="Highest BBl year to consider (historical path only; default 1999).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="--ingest-all dry-run: list candidates without fetching.",
    )
    parser.add_argument(
        "--discover-via-sparql", action="store_true",
        help=(
            "Use Fedlex SPARQL (typeDocument in --types, default 23) as "
            "candidate source instead of amendment_refs. Yields ~2000 real "
            "Botschaften across post-2003 coverage; bypasses the per-URI "
            "is_botschaft ASK probe since SPARQL pre-filters at query time."
        ),
    )
    parser.add_argument(
        "--types", default="23",
        help=(
            "Comma-separated Fedlex resource-type ids to discover with "
            "--discover-via-sparql (default 23 = Botschaft). Known: "
            + ", ".join(f"{k}={v}" for k, v in sorted(FGA_DOC_TYPES.items()))
            + ". Rows are tagged botschaft_documents.doc_type."
        ),
    )
    parser.add_argument(
        "--discover-untyped-by-title", action="store_true",
        help=(
            "Historical backfill (1850-1999): candidates are the FGA works whose "
            "title starts with Botschaft/Message/Messaggio and which carry NO "
            "typeDocument (search_stack/botschaft_historical.py). Their files "
            "live on the Bundesarchiv portal, which is only fetched when "
            "--host-allow includes 'bar'. Requires --ingest-all."
        ),
    )
    parser.add_argument(
        "--host-allow", default="fedlex",
        help=(
            "Comma-separated file hosts the historical path may download from: "
            "'fedlex' (Fedlex file store, default) and/or 'bar' "
            "(www.amtsdruckschriften.bar.admin.ch — explicit opt-in; its "
            "robots.txt disallows the document URLs, fetched by decision of "
            "2026-09-09 with the polite fetcher only)."
        ),
    )
    parser.add_argument(
        "--max-attempts", type=int, default=3,
        help="Historical path: give up on a document after this many failed runs.",
    )
    parser.add_argument(
        "--bar-min-interval", type=float, default=2.5,
        help="Historical path: seconds between requests to the Bundesarchiv (>= 2.0; default 2.5).",
    )
    parser.add_argument(
        "--state-summary", action="store_true",
        help="Print the historical backfill state counts per language and exit.",
    )
    parser.add_argument(
        "--lock-file", default=None,
        help=(
            "Historical path: host-level lock held for the whole run (flock); a "
            "second run exits 3 at once. Default <repo>/logs/bar_fetch.lock."
        ),
    )
    parser.add_argument(
        "--link-amendment-refs", action="store_true",
        help=(
            "Auto-populate article_botschaft_links from the existing "
            "amendment_refs table for every ingested Botschaft. Idempotent."
        ),
    )
    args = parser.parse_args()

    db_path = Path(args.db).resolve()
    if not db_path.exists():
        log.error(f"DB not found: {db_path}")
        return 1

    conn = sqlite3.connect(db_path)
    # busy_timeout: wait (don't instantly fail) on any transient contention with a
    # concurrent reader. (The recurring Step 2f "database live is locked" turned out
    # NOT to be cross-process — it was a DETACH-before-commit bug in
    # build_materialien_db.py, fixed 2026-06-03. No flock needed here.)
    conn.execute("PRAGMA busy_timeout=60000")
    conn.execute("PRAGMA foreign_keys = ON")

    # ALWAYS ensure schema before any operation. The 2026-05-11 incident:
    # --ingest-all was run on a materialien.db that pre-dated the Phase 2
    # tables, threw `no such table: botschaft_documents`, and the bulk
    # ingest never started. ensure_schema is idempotent (CREATE TABLE
    # IF NOT EXISTS) — calling it unconditionally costs <1 ms.
    ensure_schema(conn)

    if args.schema_only:
        log.info("schema migrated")
        return 0

    if args.ingest_one:
        if not args.year or not args.page:
            log.error("--ingest-one requires --year and --page")
            return 2
        bid = ingest_one(
            conn,
            year=args.year, page=args.page,
            language=args.language,
            sr_number=args.sr,
            article=args.article,
            relation="enacted" if args.article else "considered",
            doc_type=args.doc_type,
        )
        log.info(f"botschaft_id = {bid}")
        return 0

    if args.link_amendment_refs:
        stats = link_from_amendment_refs(conn)
        import json as _json
        print(_json.dumps(stats, indent=2, ensure_ascii=False))
        return 0

    if args.state_summary:
        from search_stack.botschaft_historical import state_summary
        print(json.dumps(state_summary(conn), indent=2, ensure_ascii=False))
        return 0

    if args.discover_untyped_by_title:
        if not args.ingest_all:
            log.error("--discover-untyped-by-title requires --ingest-all")
            return 2
        from search_stack.botschaft_historical import (
            DEFAULT_LOCK_PATH, BackfillLocked, run_historical_backfill,
        )
        host_allow = {h.strip().lower() for h in args.host_allow.split(",") if h.strip()}
        unknown = host_allow - {"fedlex", "bar"}
        if unknown:
            log.error(f"--host-allow: unknown host(s) {sorted(unknown)}; use fedlex,bar")
            return 2
        try:
            stats = run_historical_backfill(
                conn, language=args.language, host_allow=host_allow,
                min_year=args.min_year if args.min_year is not None else 1850,
                max_year=args.max_year if args.max_year is not None else 1999,
                max_per_run=args.max_per_run, max_attempts=args.max_attempts,
                min_interval=args.bar_min_interval, dry_run=args.dry_run,
                limit=args.limit,
                lock_path=Path(args.lock_file) if args.lock_file else DEFAULT_LOCK_PATH,
            )
        except BackfillLocked as e:
            log.error(f"another historical backfill is running: {e}")
            return 3
        print(json.dumps(stats, indent=2, ensure_ascii=False))
        return 0

    if args.min_year is None:
        args.min_year = 2003

    if args.ingest_all:
        if args.discover_via_sparql:
            # Pull every FGA URI of the requested resource types from Fedlex
            # SPARQL, then dedupe against already-ingested rows in
            # botschaft_documents (by ELI, then by citation page).
            try:
                from scrapers.fedlex_materialien import discover_fga_documents
            except ImportError as e:
                log.error(f"Cannot import SPARQL discoverer: {e}")
                return 3
            try:
                type_ids = parse_doc_types(args.types)
            except ValueError as e:
                log.error(str(e))
                return 2
            log.info(
                "Discovering Bundesblatt documents via Fedlex SPARQL "
                f"(typeDocument in {list(type_ids)})…"
            )
            sparql_docs = discover_fga_documents(
                language=args.language, doc_types=type_ids,
            )
            log.info(f"  SPARQL returned {len(sparql_docs)} documents")

            existing_rows = conn.execute(
                "SELECT bbl_year, bbl_page, eli_uri FROM botschaft_documents "
                "WHERE language = ? AND bbl_year >= ?",
                (args.language, args.min_year),
            ).fetchall()
            existing = {(y, p) for (y, p, _e) in existing_rows}
            existing_eli = {e for (_y, _p, e) in existing_rows if e}
            # One candidate dict per work: the SPARQL title and dates travel
            # with it so the document row is written complete, and doc_type
            # is the Fedlex resource type it was discovered under.
            candidates = [
                candidate_from_discovery(d)
                for d in sparql_docs
                if d["year"] >= args.min_year
                and d["eli_uri"] not in existing_eli
                and (d["year"], d["page"]) not in existing
            ]
            # Newest first — same convention as amendment_refs path.
            candidates.sort(key=lambda c: (c["year"], c["page"]), reverse=True)
            by_type: dict[str, int] = {}
            for c in candidates:
                by_type[c["doc_type"]] = by_type.get(c["doc_type"], 0) + 1
            log.info(
                f"--ingest-all (SPARQL): {len(candidates)} candidates "
                f"≥ {args.min_year} not yet in materialien.db, "
                f"lang={args.language} (skipped {len(existing)} already ingested; "
                + ", ".join(f"{k}={v}" for k, v in sorted(by_type.items()))
                + ")"
            )
        else:
            rows = conn.execute(
                """
                SELECT DISTINCT ar.year, ar.page
                FROM amendment_refs ar
                LEFT JOIN botschaft_documents bd
                  ON bd.bbl_year = ar.year AND bd.bbl_page = ar.page
                  AND bd.language = ?
                WHERE ar.ref_type = 'BBl'
                  AND ar.year IS NOT NULL AND ar.page IS NOT NULL
                  AND ar.year >= ?
                  AND bd.botschaft_id IS NULL
                ORDER BY ar.year DESC, ar.page DESC
                """,
                (args.language, args.min_year),
            ).fetchall()
            candidates = [
                {"year": int(r[0]), "page": int(r[1]), "eli_uri": None,
                 "doc_type": DEFAULT_DOC_TYPE}
                for r in rows
            ]
            log.info(
                f"--ingest-all (amendment_refs): {len(candidates)} unique "
                f"(year, page) candidates ≥ {args.min_year} not yet in "
                f"materialien.db, lang={args.language}"
            )
        for cap_flag in ("limit", "max_per_run"):
            cap = getattr(args, cap_flag)
            if cap and len(candidates) > cap:
                log.info(
                    f"--{cap_flag.replace('_', '-')} {cap}: processing the "
                    f"first {cap} of {len(candidates)} candidates (newest "
                    "first); the rest wait for the next run"
                )
                candidates = candidates[:cap]
        if args.dry_run:
            for c in candidates[:25]:
                log.info(f"  candidate: BBl {c['year']} {c['page']} [{c['doc_type']}]")
            if len(candidates) > 25:
                log.info(f"  … and {len(candidates) - 25} more")
            return 0

        heal = None
        if args.discover_via_sparql:
            # The rows filtered out above as "already ingested" are never
            # re-fetched, so their missing title/date must be filled from
            # the discovery metadata here (indexed SELECT + UPDATE, no
            # network). This is the nightly heal; the backfill script is
            # the bulk path for ELIs discovery no longer returns.
            heal = heal_existing_metadata(
                conn, sparql_docs, language=args.language, min_year=args.min_year,
            )
            log.info(
                f"  healed metadata on {heal['healed']}/{heal['candidates']} "
                f"existing rows lacking title/date "
                f"({heal['no_discovery_meta']} without discovery metadata)"
            )
        stats = ingest_candidates(
            conn, candidates,
            language=args.language,
            prefiltered=args.discover_via_sparql,
            rate_limit=args.rate_limit,
            max_consecutive_failures=args.max_consecutive_failures,
        )
        if heal is not None:
            stats["healed_existing"] = heal

        # Auto-link from amendment_refs after bulk ingest (local, idempotent —
        # worth doing for the rows a stopped run did commit).
        log.info("Auto-linking from amendment_refs…")
        link_stats = link_from_amendment_refs(conn)
        stats["link_stats"] = link_stats
        print(json.dumps(stats, indent=2, ensure_ascii=False))
        if stats["stopped"]:
            log.error(
                f"run stopped ({stats['stopped']}) after "
                f"{stats['processed']}/{stats['total']} candidates — "
                f"exit {INGEST_STOPPED_EXIT_CODE}"
            )
            return INGEST_STOPPED_EXIT_CODE
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
