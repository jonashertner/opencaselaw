"""Verbatim Vernehmlassungs corpus from Fedlex — Erläuternde Berichte,
Vernehmlassungsvorlagen, Ergebnisberichte, Stellungnahmen.

Fedlex publishes every federal consultation procedure (Vernehmlassung /
consultation / consultazione, 1993–today) as a ``jolux:Consultation``
(``eli/dl/proj/<y>/<n>/cons_<k>``). Its documents hang off the procedure's
sub-tasks and are typed by ``jolux:draftProcessDocumentType``::

    ?cons jolux:hasSubTask ?task .
    ?task jolux:opinionHasDraftRelatedDocument | jolux:opinionIsAboutDraftDocument ?doc .
    ?doc  jolux:draftProcessDocumentType <vocabulary/draft-document-type/N> ;
          jolux:isRealizedBy ?expr .                     # one per language
    ?expr jolux:language ?lang ; jolux:isEmbodiedBy ?m .  # pdf-a, docx, ...
    ?m    jolux:isExemplifiedBy ?file .                   # Fedlex file store

Census 2026-10-19: 2,581 procedures; 2,434 Erläuternde Berichte, 3,275
Vorlagen, 1,943 Ergebnisberichte, 1,214 Stellungnahmen collections, 243
synoptic tables; Erläuternde Berichte from 2005 on (earlier procedures are
mostly metadata without files on Fedlex).

The corpus lives in its own database, not in materialien.db: publish.py's
Step 2f copies every foreign table of materialien.db on each build and is a
fatal step with a time cap, and the Stellungnahmen alone are hundreds of
pages per procedure. Two files:

* the WORK database (``--work-db``, writable, never served) holds the
  discovery metadata, the per-file fetch state and the text;
* ``publish`` snapshots it with ``VACUUM INTO`` next to the real path of
  ``--out`` and ``os.replace``s it over the served file, so MCP workers
  (``?mode=ro&immutable=1``, one connection per call) never see a
  half-written database.

Documents Fedlex does not hold (the Chancellery's 1995-2004 index and the
BJ legislative projects, live and from the Wayback Machine) are discovered
into the same work DB by search_stack/consultation_archive.py and fetched
here like Fedlex's, each host at its own pace (HOST_MIN_INTERVAL); their
rows carry their provenance (source, original_url, source_page_url).

Text policy: the documents that carry legal reasoning or the draft itself are
ingested verbatim (Erläuternder Bericht, Ergebnisbericht, Vorlage, synoptic
table, Diverses, Stellungnahmen); cover letters, address lists, answer forms
and questionnaires are recorded with their Fedlex file link only.

Usage::

    python3 -m search_stack.build_consultation_corpus discover --work-db W
    python3 -m search_stack.build_consultation_corpus fetch --work-db W \\
        --max-seconds 10800 --rate-limit 0.5
    python3 -m search_stack.build_consultation_corpus publish --work-db W \\
        --out output/consultations.db
    python3 -m search_stack.build_consultation_corpus status --work-db W
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import logging
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Iterator

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from search_stack.build_botschaft_corpus import (  # noqa: E402
    ARTICLE_HEADER_RE,
    BACKOFF_BASE_SECONDS,
    BACKOFF_MAX_SECONDS,
    FATAL_HTTP_STATUSES,
    INGEST_STOPPED_EXIT_CODE,
    STOP_AFTER_CONSECUTIVE_FAILURES,
    SourceUnavailable,
    http_status_of,
)
from search_stack.botschaft_historical import _join_hyphen  # noqa: E402
from scrapers.fedlex_materialien import fix_cp1252_controls  # noqa: E402

log = logging.getLogger("build_consultation_corpus")

JOLUX = "http://data.legilux.public.lu/resource/ontology/jolux#"
SKOS = "http://www.w3.org/2004/02/skos/core#"
VOCAB = "https://fedlex.data.admin.ch/vocabulary/"
LANG_FROM_URI = {
    "http://publications.europa.eu/resource/authority/language/DEU": "de",
    "http://publications.europa.eu/resource/authority/language/FRA": "fr",
    "http://publications.europa.eu/resource/authority/language/ITA": "it",
    "http://publications.europa.eu/resource/authority/language/ROH": "rm",
    "http://publications.europa.eu/resource/authority/language/ENG": "en",
}

USER_AGENT = ("opencaselaw-materialien/0.4 (Vernehmlassungen; "
              "+https://opencaselaw.ch)")
FEDLEX_FILE_HOST = "fedlex.data.admin.ch"
# Fedlex's file store, plus the two sources of search_stack.consultation_archive:
# Wayback copies of the admin.ch and bj.admin.ch pages Fedlex never took over,
# and the live bj.admin.ch file store.
ALLOWED_FILE_HOSTS = frozenset({FEDLEX_FILE_HOST, "web.archive.org", "www.bj.admin.ch"})
# Minimum seconds after a request to a host before the next request of the
# run (one connection; the Internet Archive asks for >= 2 s between requests).
HOST_MIN_INTERVAL: dict[str, float] = {"web.archive.org": 2.0, "www.bj.admin.ch": 1.0}
# The Wayback Machine answers 403 for a single capture excluded from replay:
# that file is final (not_archived), only 429 is a refusal of the client.
WAYBACK_HOST = "web.archive.org"

# draft-document-type code → slug. Labels are never synthesised: they are
# read from Fedlex's vocabulary at discovery time (consultation_vocab).
DOC_TYPES: dict[int, str] = {
    11: "vernehmlassungsvorlage",
    12: "erlaeuternder_bericht",
    13: "begleitschreiben",
    14: "adressatenliste",
    15: "vernehmlassung_diverses",
    16: "synoptische_tabelle",
    21: "stellungnahmen",
    22: "antwortformular",
    23: "fragebogen",
    31: "ergebnisbericht",
}
# Ingest order: the documents most cited for legislative intent first, the
# bulky collections of submissions last.
TEXT_TYPE_PRIORITY: tuple[int, ...] = (12, 31, 11, 16, 15, 21)
TEXT_TYPES = frozenset(TEXT_TYPE_PRIORITY)

# Manifestation preference when one expression has several files.
_FORMAT_PRIORITY = ("pdf-a", "pdf", "docx", "doc", "xlsx", "zip")
TEXT_FORMATS = frozenset({"pdf-a", "pdf", "docx"})

MAX_FILE_BYTES = 250 * 1024 * 1024
SAME_FILE_PREFIX_BYTES = 256 * 1024
MAX_ATTEMPTS = 5
FIRST_YEAR = 1990

# Fetch-state values of consultation_files.status.
ST_PENDING = "pending"
ST_DONE = "done"                  # text stored in consultation_paragraphs
ST_SAME_AS = "same_as"            # byte-identical to another language's file
ST_NO_TEXT = "no_text"            # scan without a text layer (OCR backlog)
ST_LINK_ONLY = "link_only"        # type outside the text policy
ST_UNSUPPORTED = "unsupported_format"
ST_NOT_FOUND = "not_found"        # file store answered 404/410
ST_TOO_LARGE = "too_large"
ST_PARSE_ERROR = "parse_error"    # downloaded but unreadable (corrupt / encrypted)
ST_ERROR = "error"                # retried until MAX_ATTEMPTS
ST_NOT_ARCHIVED = "not_archived"  # linked by an archived page, never captured by the Wayback Machine
FINAL_STATUSES = frozenset({ST_DONE, ST_SAME_AS, ST_NO_TEXT, ST_LINK_ONLY,
                            ST_UNSUPPORTED, ST_NOT_FOUND, ST_TOO_LARGE,
                            ST_PARSE_ERROR, ST_NOT_ARCHIVED})


# ── Schema ─────────────────────────────────────────────────────────────

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS consultation_procedures (
    procedure_uri    TEXT PRIMARY KEY,       -- https://fedlex.data.admin.ch/eli/dl/proj/2023/45/cons_1
    event_id         TEXT NOT NULL,          -- proj/2023/45/cons_1
    year             INTEGER,
    status_uri       TEXT,
    title_de         TEXT, title_fr TEXT, title_it TEXT,
    description_de   TEXT, description_fr TEXT, description_it TEXT,
    start_date       TEXT,                   -- opening of the consultation phase
    end_date         TEXT,                   -- deadline for submissions
    institution_uri  TEXT,                   -- most specific of Level3/2/1
    discovered_at    TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_cp_year ON consultation_procedures(year);

-- The acts a procedure foresees to change (jolux:foreseenImpactToLegalResource).
CREATE TABLE IF NOT EXISTS consultation_laws (
    procedure_uri    TEXT NOT NULL REFERENCES consultation_procedures(procedure_uri),
    work_uri         TEXT NOT NULL,
    sr_number        TEXT,
    PRIMARY KEY (procedure_uri, work_uri)
);
CREATE INDEX IF NOT EXISTS ix_cl_sr ON consultation_laws(sr_number);

CREATE TABLE IF NOT EXISTS consultation_documents (
    document_uri     TEXT PRIMARY KEY,       -- …/cons_1/doc_10
    procedure_uri    TEXT NOT NULL REFERENCES consultation_procedures(procedure_uri),
    doc_type_code    INTEGER NOT NULL,
    doc_type         TEXT NOT NULL,          -- DOC_TYPES slug
    title_de         TEXT, title_fr TEXT, title_it TEXT   -- Fedlex's own ("Erläuternder Bericht-2")
);
CREATE INDEX IF NOT EXISTS ix_cd_proc ON consultation_documents(procedure_uri);
CREATE INDEX IF NOT EXISTS ix_cd_type ON consultation_documents(doc_type);

CREATE TABLE IF NOT EXISTS consultation_files (
    file_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    document_uri     TEXT NOT NULL REFERENCES consultation_documents(document_uri),
    language         TEXT NOT NULL,
    file_url         TEXT,
    format           TEXT,
    file_name        TEXT,                   -- the expression title, usually the upload's file name
    publication_date TEXT,
    alt_files_json   TEXT,                   -- other manifestations [{format, url}]
    text_policy      TEXT NOT NULL,          -- 'text' | 'link'
    status           TEXT NOT NULL,
    http_status      INTEGER,
    attempts         INTEGER NOT NULL DEFAULT 0,
    last_attempt_at  TEXT,
    error            TEXT,
    byte_size        INTEGER,
    sha256           TEXT,
    prefix_sha256    TEXT,                   -- sha-256 of the first SAME_FILE_PREFIX_BYTES
    same_as_file_id  INTEGER,
    page_count       INTEGER,
    char_count       INTEGER,
    empty_pages      INTEGER,
    ingested_at      TEXT,
    UNIQUE(document_uri, language)
);
CREATE INDEX IF NOT EXISTS ix_cf_status ON consultation_files(status);

CREATE TABLE IF NOT EXISTS consultation_paragraphs (
    paragraph_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id          INTEGER NOT NULL REFERENCES consultation_files(file_id) ON DELETE CASCADE,
    para_order       INTEGER NOT NULL,
    page_number      INTEGER,
    section_path     TEXT,
    article_anchor   TEXT,
    text             TEXT NOT NULL,
    text_length      INTEGER NOT NULL,
    UNIQUE(file_id, para_order)
);
CREATE INDEX IF NOT EXISTS ix_cpa_file ON consultation_paragraphs(file_id);

CREATE VIRTUAL TABLE IF NOT EXISTS consultation_paragraphs_fts USING fts5(
    text,
    section_path,
    article_anchor UNINDEXED,
    content='consultation_paragraphs',
    content_rowid='paragraph_id',
    tokenize='unicode61 remove_diacritics 1'
);
CREATE TRIGGER IF NOT EXISTS consultation_paragraphs_ai AFTER INSERT ON consultation_paragraphs BEGIN
    INSERT INTO consultation_paragraphs_fts(rowid, text, section_path, article_anchor)
    VALUES (new.paragraph_id, new.text, new.section_path, new.article_anchor);
END;
CREATE TRIGGER IF NOT EXISTS consultation_paragraphs_ad AFTER DELETE ON consultation_paragraphs BEGIN
    INSERT INTO consultation_paragraphs_fts(consultation_paragraphs_fts, rowid, text, section_path, article_anchor)
    VALUES ('delete', old.paragraph_id, old.text, old.section_path, old.article_anchor);
END;

-- Fedlex vocabulary labels (document types, status, institutions) in DE/FR/IT.
CREATE TABLE IF NOT EXISTS consultation_vocab (
    uri              TEXT PRIMARY KEY,
    label_de         TEXT, label_fr TEXT, label_it TEXT
);

CREATE TABLE IF NOT EXISTS meta (
    key              TEXT PRIMARY KEY,
    value            TEXT
);

-- One row per procedure entry read from a non-Fedlex source page
-- (search_stack.consultation_archive): what the page said, where it was read,
-- and how it was matched to a procedure. Kept for every entry, matched or
-- not, so a match can be audited and re-done.
CREATE TABLE IF NOT EXISTS consultation_archive_entries (
    entry_id         TEXT PRIMARY KEY,       -- <source>:<key>
    source           TEXT NOT NULL,
    year             INTEGER,
    title_de         TEXT, title_fr TEXT, title_it TEXT,
    opened           TEXT,                   -- as printed on the page (ISO date)
    deadline         TEXT,
    department       TEXT,
    page_urls_json   TEXT NOT NULL,          -- {lang: page URL (Wayback snapshot or live)}
    procedure_uri    TEXT,                   -- matched or created procedure; NULL when ambiguous
    match_method     TEXT NOT NULL,          -- fedlex_link | deadline_title | opened_title | opened_institution | title | document_date | bj_live_twin | created | ambiguous | no_documents | documents_dated
    match_score      REAL,
    documents        INTEGER NOT NULL DEFAULT 0,
    discovered_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_cae_proc ON consultation_archive_entries(procedure_uri);
"""

# Provenance of rows that do not come from Fedlex (2026-10-20). Added with
# ALTER TABLE so a work database from before keeps its rows; NULL = Fedlex.
#   source           admin_pc_wayback | bj | bj_wayback
#   original_url     the file's own URL on the source site (dead for Wayback rows)
#   source_page_url  the page that linked it (Wayback snapshot or live page)
# consultation_files.file_url is what is fetched and shown: the Wayback
# snapshot URL for archived files, the live URL otherwise.
ARCHIVE_COLUMNS: dict[str, tuple[str, ...]] = {
    "consultation_procedures": ("source", "source_url"),
    "consultation_documents": ("source",),
    "consultation_files": ("source", "original_url", "source_page_url"),
}


def ensure_archive_columns(conn: sqlite3.Connection) -> None:
    for table, cols in ARCHIVE_COLUMNS.items():
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        for col in cols:
            if col not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} TEXT")


def connect_work_db(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA_SQL)
    ensure_archive_columns(conn)
    conn.commit()
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def set_meta(conn: sqlite3.Connection, key: str, value) -> None:
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True)
    conn.execute("INSERT INTO meta(key, value) VALUES (?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


# ── SPARQL ─────────────────────────────────────────────────────────────

def _default_sparql(query: str) -> list[dict]:
    from scrapers.fedlex_materialien import sparql_query
    return sparql_query(query, timeout=300)


def _v(row: dict, key: str) -> str | None:
    cell = row.get(key)
    return cell["value"] if cell else None


def _year_filter(year: int) -> str:
    return f'FILTER(STRSTARTS(STR(?id), "proj/{year}/"))'


def q_procedures(year: int) -> str:
    return f"""
PREFIX jolux: <{JOLUX}>
SELECT DISTINCT ?c ?id ?status ?title ?tl ?desc WHERE {{
  ?c a jolux:Consultation ; jolux:eventId ?id .
  {_year_filter(year)}
  OPTIONAL {{ ?c jolux:consultationStatus ?status }}
  OPTIONAL {{
    ?c jolux:eventTitle ?title BIND(LANG(?title) AS ?tl)
    OPTIONAL {{ ?c jolux:eventDescription ?desc FILTER(LANG(?desc) = LANG(?title)) }}
  }}
}}"""


def q_phases(year: int) -> str:
    return f"""
PREFIX jolux: <{JOLUX}>
SELECT DISTINCT ?c ?start ?end ?i1 ?i2 ?i3 WHERE {{
  ?c a jolux:Consultation ; jolux:eventId ?id ; jolux:hasSubTask ?p .
  {_year_filter(year)}
  ?p a jolux:ConsultationPhase .
  OPTIONAL {{ ?p jolux:eventStartDate ?start }}
  OPTIONAL {{ ?p jolux:eventEndDate ?end }}
  OPTIONAL {{ ?p jolux:institutionInChargeOfTheEvent ?i1 }}
  OPTIONAL {{ ?p jolux:institutionInChargeOfTheEventLevel2 ?i2 }}
  OPTIONAL {{ ?p jolux:institutionInChargeOfTheEventLevel3 ?i3 }}
}}"""


def q_laws(year: int) -> str:
    # SR numbers: taxonomy notation UNION historicalLegalId — totally revised
    # acts carry no historicalLegalId (memory: fedlex-work-identity-trap).
    return f"""
PREFIX jolux: <{JOLUX}>
PREFIX skos: <{SKOS}>
SELECT DISTINCT ?c ?w ?sr WHERE {{
  ?c a jolux:Consultation ; jolux:eventId ?id ; jolux:foreseenImpactToLegalResource ?w .
  {_year_filter(year)}
  OPTIONAL {{
    {{ ?w jolux:classifiedByTaxonomyEntry ?tax . ?tax skos:notation ?n BIND(STR(?n) AS ?sr) }}
    UNION
    {{ ?w jolux:historicalLegalId ?h BIND(STR(?h) AS ?sr) }}
  }}
}}"""


def q_documents(year: int) -> str:
    return f"""
PREFIX jolux: <{JOLUX}>
SELECT DISTINCT ?c ?doc ?dt ?dtitle ?dtl ?lang ?fname ?fmt ?file ?pd WHERE {{
  ?c a jolux:Consultation ; jolux:eventId ?id ; jolux:hasSubTask ?s .
  {_year_filter(year)}
  VALUES ?rel {{ jolux:opinionHasDraftRelatedDocument jolux:opinionIsAboutDraftDocument }}
  ?s ?rel ?doc .
  ?doc jolux:draftProcessDocumentType ?dt .
  OPTIONAL {{ ?doc jolux:title ?dtitle BIND(LANG(?dtitle) AS ?dtl) }}
  OPTIONAL {{
    ?doc jolux:isRealizedBy ?e . ?e jolux:language ?lang .
    OPTIONAL {{ ?e jolux:title ?fname }}
    OPTIONAL {{
      ?e jolux:isEmbodiedBy ?m . ?m jolux:isExemplifiedBy ?file .
      OPTIONAL {{ ?m jolux:userFormat ?fmt }}
      OPTIONAL {{ ?m jolux:publicationDate ?pd }}
    }}
  }}
}}"""


def q_vocab() -> str:
    return f"""
PREFIX skos: <{SKOS}>
SELECT DISTINCT ?s ?l ?lg WHERE {{
  ?s skos:prefLabel ?l BIND(LANG(?l) AS ?lg)
  FILTER(?lg IN ("de", "fr", "it"))
  FILTER(STRSTARTS(STR(?s), "{VOCAB}draft-document-type/")
      || STRSTARTS(STR(?s), "{VOCAB}consultation-status/")
      || STRSTARTS(STR(?s), "{VOCAB}legal-institution/"))
}}"""


def q_year_counts() -> str:
    return f"""
PREFIX jolux: <{JOLUX}>
SELECT ?y (COUNT(DISTINCT ?c) AS ?n) WHERE {{
  ?c a jolux:Consultation ; jolux:eventId ?id .
  BIND(STR(SUBSTR(STR(?id), 6, 4)) AS ?y)
}} GROUP BY ?y ORDER BY ?y"""


def _format_of(fmt_uri: str | None, file_url: str | None) -> str | None:
    if fmt_uri:
        return fmt_uri.rstrip("/").rsplit("/", 1)[-1]
    if file_url and "." in file_url.rsplit("/", 1)[-1]:
        return file_url.rsplit(".", 1)[-1].lower()
    return None


def _pick_manifestation(files: list[dict]) -> tuple[dict | None, list[dict]]:
    """Choose one file per expression by _FORMAT_PRIORITY; the rest are kept
    as alternatives."""
    if not files:
        return None, []

    def rank(f):
        fmt = f.get("format") or ""
        return (_FORMAT_PRIORITY.index(fmt) if fmt in _FORMAT_PRIORITY
                else len(_FORMAT_PRIORITY), f.get("url") or "")

    ordered = sorted(files, key=rank)
    return ordered[0], ordered[1:]


def discover_years(sparql: Callable[[str], list[dict]]) -> list[int]:
    years = []
    for r in sparql(q_year_counts()):
        y = _v(r, "y")
        if y and y.isdigit() and int(y) >= FIRST_YEAR:
            years.append(int(y))
    return sorted(set(years))


def discover(conn: sqlite3.Connection, *,
             sparql: Callable[[str], list[dict]] = _default_sparql,
             years: Iterable[int] | None = None,
             pause: float = 0.5,
             sleep: Callable[[float], None] = time.sleep) -> dict:
    """Upsert procedures, laws, documents and file rows from Fedlex.

    Idempotent. Never deletes: a document that vanished from Fedlex keeps its
    row (and text) so a takedown is an explicit act, not a side effect of a
    flaky SPARQL answer. Existing file rows keep their fetch state unless the
    file URL changed, in which case they are re-queued."""
    now = _now()
    stats = {"years": 0, "procedures": 0, "documents": 0, "files": 0,
             "files_new": 0, "files_requeued": 0, "vocab": 0}

    for r in sparql(q_vocab()):
        uri, label, lg = _v(r, "s"), _v(r, "l"), _v(r, "lg")
        if not (uri and label and lg in ("de", "fr", "it")):
            continue
        conn.execute(
            f"INSERT INTO consultation_vocab(uri, label_{lg}) VALUES (?, ?) "
            f"ON CONFLICT(uri) DO UPDATE SET label_{lg}=excluded.label_{lg}",
            (uri, label))
        stats["vocab"] += 1
    conn.commit()

    for year in (sorted(years) if years else discover_years(sparql)):
        stats["years"] += 1
        procs: dict[str, dict] = {}
        for r in sparql(q_procedures(year)):
            c = _v(r, "c")
            if not c:
                continue
            p = procs.setdefault(c, {"event_id": _v(r, "id"), "status": None,
                                     "title": {}, "desc": {}})
            p["status"] = p["status"] or _v(r, "status")
            tl = _v(r, "tl")
            if tl in ("de", "fr", "it"):
                if _v(r, "title"):
                    p["title"].setdefault(tl, fix_cp1252_controls(_v(r, "title")))
                if _v(r, "desc"):
                    p["desc"].setdefault(tl, fix_cp1252_controls(_v(r, "desc")))
        sleep(pause)
        for r in sparql(q_phases(year)):
            p = procs.get(_v(r, "c") or "")
            if p is None:
                continue
            # Several phases (re-opened consultations): the earliest start
            # and the latest deadline describe the procedure.
            st, en = _v(r, "start"), _v(r, "end")
            if st and (not p.get("start") or st < p["start"]):
                p["start"] = st
            if en and (not p.get("end") or en > p["end"]):
                p["end"] = en
            inst = _v(r, "i3") or _v(r, "i2") or _v(r, "i1")
            p.setdefault("inst", inst)
        sleep(pause)
        for uri, p in procs.items():
            conn.execute(
                """INSERT INTO consultation_procedures
                   (procedure_uri, event_id, year, status_uri,
                    title_de, title_fr, title_it,
                    description_de, description_fr, description_it,
                    start_date, end_date, institution_uri, discovered_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(procedure_uri) DO UPDATE SET
                     event_id=excluded.event_id, year=excluded.year,
                     status_uri=excluded.status_uri,
                     title_de=excluded.title_de, title_fr=excluded.title_fr,
                     title_it=excluded.title_it,
                     description_de=excluded.description_de,
                     description_fr=excluded.description_fr,
                     description_it=excluded.description_it,
                     start_date=excluded.start_date, end_date=excluded.end_date,
                     institution_uri=excluded.institution_uri,
                     updated_at=excluded.updated_at""",
                (uri, p["event_id"] or "", year, p["status"],
                 p["title"].get("de"), p["title"].get("fr"), p["title"].get("it"),
                 p["desc"].get("de"), p["desc"].get("fr"), p["desc"].get("it"),
                 p.get("start"), p.get("end"), p.get("inst"), now, now))
        stats["procedures"] += len(procs)

        for r in sparql(q_laws(year)):
            c, w = _v(r, "c"), _v(r, "w")
            if c not in procs or not w:
                continue
            sr = _v(r, "sr")
            conn.execute(
                "INSERT INTO consultation_laws(procedure_uri, work_uri, sr_number) "
                "VALUES (?,?,?) ON CONFLICT(procedure_uri, work_uri) DO UPDATE SET "
                "sr_number=COALESCE(consultation_laws.sr_number, excluded.sr_number)",
                (c, w, sr))
        sleep(pause)

        docs: dict[str, dict] = {}
        for r in sparql(q_documents(year)):
            c, doc, dt = _v(r, "c"), _v(r, "doc"), _v(r, "dt")
            if c not in procs or not doc or not dt:
                continue
            code_s = dt.rstrip("/").rsplit("/", 1)[-1]
            if not code_s.isdigit():
                continue
            d = docs.setdefault(doc, {"proc": c, "code": int(code_s),
                                      "title": {}, "files": {}})
            dtl = _v(r, "dtl")
            if dtl in ("de", "fr", "it") and _v(r, "dtitle"):
                d["title"].setdefault(dtl, _v(r, "dtitle"))
            lang = LANG_FROM_URI.get(_v(r, "lang") or "")
            if not lang:
                continue
            f = d["files"].setdefault(lang, {"name": None, "pd": None, "files": {}})
            f["name"] = f["name"] or _v(r, "fname")
            f["pd"] = f["pd"] or _v(r, "pd")
            url = _v(r, "file")
            if url:
                f["files"][url] = {"url": url, "format": _format_of(_v(r, "fmt"), url)}
        sleep(pause)

        for doc_uri, d in docs.items():
            code = d["code"]
            slug = DOC_TYPES.get(code, f"type_{code}")
            conn.execute(
                """INSERT INTO consultation_documents
                   (document_uri, procedure_uri, doc_type_code, doc_type,
                    title_de, title_fr, title_it)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(document_uri) DO UPDATE SET
                     procedure_uri=excluded.procedure_uri,
                     doc_type_code=excluded.doc_type_code, doc_type=excluded.doc_type,
                     title_de=excluded.title_de, title_fr=excluded.title_fr,
                     title_it=excluded.title_it""",
                (doc_uri, d["proc"], code, slug, d["title"].get("de"),
                 d["title"].get("fr"), d["title"].get("it")))
            stats["documents"] += 1
            for lang, f in d["files"].items():
                chosen, alts = _pick_manifestation(list(f["files"].values()))
                url = chosen["url"] if chosen else None
                fmt = chosen["format"] if chosen else None
                if code not in TEXT_TYPES:
                    policy, status = "link", ST_LINK_ONLY
                elif not url:
                    policy, status = "text", ST_NOT_FOUND
                elif fmt not in TEXT_FORMATS:
                    policy, status = "text", ST_UNSUPPORTED
                else:
                    policy, status = "text", ST_PENDING
                alt_json = json.dumps(alts, ensure_ascii=False) if alts else None
                prev = conn.execute(
                    "SELECT file_id, file_url, status FROM consultation_files "
                    "WHERE document_uri=? AND language=?", (doc_uri, lang)).fetchone()
                stats["files"] += 1
                if prev is None:
                    conn.execute(
                        """INSERT INTO consultation_files
                           (document_uri, language, file_url, format, file_name,
                            publication_date, alt_files_json, text_policy, status)
                           VALUES (?,?,?,?,?,?,?,?,?)""",
                        (doc_uri, lang, url, fmt, f["name"], f["pd"], alt_json,
                         policy, status))
                    stats["files_new"] += 1
                elif prev["file_url"] != url:
                    # Replaced upload: drop the old text, fetch again.
                    conn.execute("DELETE FROM consultation_paragraphs WHERE file_id=?",
                                 (prev["file_id"],))
                    conn.execute(
                        """UPDATE consultation_files SET file_url=?, format=?,
                           file_name=?, publication_date=?, alt_files_json=?,
                           text_policy=?, status=?, attempts=0, error=NULL,
                           http_status=NULL, sha256=NULL, prefix_sha256=NULL,
                           byte_size=NULL, same_as_file_id=NULL, page_count=NULL,
                           char_count=NULL, empty_pages=NULL, ingested_at=NULL
                           WHERE file_id=?""",
                        (url, fmt, f["name"], f["pd"], alt_json, policy, status,
                         prev["file_id"]))
                    stats["files_requeued"] += 1
                else:
                    conn.execute(
                        "UPDATE consultation_files SET file_name=?, publication_date=?, "
                        "alt_files_json=? WHERE file_id=?",
                        (f["name"], f["pd"], alt_json, prev["file_id"]))
        conn.commit()
        log.info("discover %d: %d procedures, %d documents", year, len(procs), len(docs))
    set_meta(conn, "last_discovery", {"at": now, **stats})
    conn.commit()
    return stats


# ── Text extraction ────────────────────────────────────────────────────

_PAGE_NUMBER_RE = re.compile(r"^\s*(?:Seite\s+|Page\s+|Pagina\s+)?\d{1,4}\s*(?:/\s*\d{1,4}|von\s+\d{1,4}|sur\s+\d{1,4}|di\s+\d{1,4})?\s*$",
                             re.IGNORECASE)
_SOFT_HYPHEN = "­"


def _norm_block(text: str) -> str:
    return re.sub(r"\d+", "#", re.sub(r"\s+", " ", text)).strip().lower()


def _join_lines(raw: str, language: str) -> str:
    lines = [ln.strip() for ln in raw.replace(_SOFT_HYPHEN, "").replace(" ", " ").split("\n")]
    lines = [ln for ln in lines if ln]
    if not lines:
        return ""
    out = lines[0]
    for nxt in lines[1:]:
        out = _join_hyphen(out, nxt, language)
    return re.sub(r"[ \t]{2,}", " ", out).strip()


def pdf_page_blocks(pdf_bytes: bytes) -> list[list[str]]:
    """One list of raw text blocks per page (PyMuPDF block order)."""
    import fitz  # PyMuPDF
    pages: list[list[str]] = []
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        for page in doc:
            try:
                blocks = page.get_text("blocks", sort=False)
            except Exception as e:  # noqa: BLE001 - a broken page must not lose the document
                log.warning("    page %d extract failed: %s", page.number + 1, e)
                blocks = []
            pages.append([b[4] for b in blocks if len(b) > 6 and b[6] == 0 and b[4].strip()])
    return pages


def docx_page_blocks(docx_bytes: bytes) -> list[list[str]]:
    """A .docx has no pages: one pseudo-page of its paragraphs (and table
    cells, row by row)."""
    import docx  # python-docx
    d = docx.Document(io.BytesIO(docx_bytes))
    blocks = [p.text for p in d.paragraphs if p.text.strip()]
    for table in d.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                blocks.append(" | ".join(dict.fromkeys(cells)))
    return [blocks]


_TOC_ENTRY_RE = re.compile(r"(?:\s|\.{2,})\d{1,3}$")
_CITATION_IN_TEXT_RE = re.compile(r"\b(?:BBl|AS|SR|FF|RO|RS|BU|AB|BO)\s+\d")
_NUMBERED_HEADING_RE = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){0,5})\.?\s+([A-ZÄÖÜÀ-Ý][^\W\d_]{2,}.*)$")
SHORT_BLOCK = 40


def _is_heading(text: str) -> bool:
    """A numbered section heading ("3.1.2 Auswirkungen auf die Kantone",
    number dotted or <= 20) or an all-capitals title line; never a sentence."""
    if (len(text) > 120 or text.endswith((".", ":", ";", ","))
            or _TOC_ENTRY_RE.search(text) or _CITATION_IN_TEXT_RE.search(text)):
        return False
    m = _NUMBERED_HEADING_RE.match(text)
    if m:
        num = m.group(1)
        return "." in num or int(num) <= 20
    if any(ch.isdigit() for ch in text):
        return False
    words = [w for w in re.findall(r"[^\W\d_]+", text) if len(w) >= 3]
    return len(words) >= 2 and all(w.isupper() for w in words)


def _is_toc_page(blocks: list[str]) -> bool:
    entries = sum(1 for b in blocks if _TOC_ENTRY_RE.search(re.sub(r"\s+", " ", b).strip()))
    return entries >= 5


def _recurring(pages: list[list[str]], keyfn) -> set[str]:
    """Normalised keys present on at least max(3, 30 %) of the pages."""
    if len(pages) < 3:
        return set()
    seen: dict[str, int] = {}
    for blocks in pages:
        for key in {k for b in blocks for k in keyfn(b)}:
            seen[key] = seen.get(key, 0) + 1
    threshold = max(3, int(0.3 * len(pages)))
    # A key needs words: a bare number ("#", a section number on its own
    # line) recurs on every page and must never be stripped as a running head.
    return {k for k, n in seen.items()
            if n >= threshold and len(k) < 200 and len(re.findall(r"[^\W\d_]", k)) >= 3}


def _strip_running_lines(raw: str, recurring_lines: set[str]) -> str:
    """Drop running-head / footer lines at the start or end of a block
    (a heading block often begins with the document's running head)."""
    lines = raw.split("\n")
    while lines and (not lines[0].strip() or _norm_block(lines[0]) in recurring_lines):
        lines.pop(0)
    while lines and (not lines[-1].strip() or _norm_block(lines[-1]) in recurring_lines):
        lines.pop()
    return "\n".join(lines)


def segment_blocks(pages: list[list[str]], language: str = "de",
                   paginated: bool = True) -> Iterator[dict]:
    """Paragraph dicts (para_order, page_number, section_path, article_anchor,
    text, text_length) from per-page text blocks.

    Running heads and footers are dropped: a whole block, or a block's
    leading/trailing line, whose digit-normalised text recurs on at least
    max(3, 30 %) of the pages; so are bare page numbers. Lines inside a block
    are joined with the historical pipeline's conservative de-hyphenation and
    runs of short blocks on one page (table cells) are joined with a space;
    nothing else in the wording changes, so every paragraph is quotable
    verbatim. A heading sets section_path (table-of-contents lines, which end
    in a page number, do not); an "Art. N" heading sets article_anchor until
    the next heading."""
    rec_blocks = _recurring(pages, lambda b: [_norm_block(b)]) if paginated else set()
    rec_lines = (_recurring(pages, lambda b: [_norm_block(ln) for ln in b.split("\n")])
                 if paginated else set())

    section: str | None = None
    anchor: str | None = None
    order = 0
    pending: list[str] = []
    pending_page = None

    def emit(text: str, page_no):
        nonlocal order
        order += 1
        return {
            "para_order": order,
            "page_number": page_no if paginated else None,
            "section_path": section,
            "article_anchor": anchor,
            "text": text,
            "text_length": len(text),
        }

    for page_no, blocks in enumerate(pages, start=1):
        toc = _is_toc_page(blocks)
        for raw in blocks:
            if paginated:
                if _norm_block(raw) in rec_blocks:
                    continue
                raw = _strip_running_lines(raw, rec_lines)
            text = _join_lines(raw, language)
            if len(text) < 3 or _PAGE_NUMBER_RE.match(text):
                continue
            first_line = raw.strip().split("\n", 1)[0].strip()
            art = ARTICLE_HEADER_RE.match(first_line) or (
                ARTICLE_HEADER_RE.match(text) if len(text) <= 120 else None)
            heading = not toc and _is_heading(text)
            short = len(text) < SHORT_BLOCK and not heading and not art
            if pending and (not short or page_no != pending_page):
                yield emit(" ".join(pending), pending_page)
                pending = []
            if short:
                pending.append(text)
                pending_page = page_no
                continue
            if heading:
                section, anchor = text, None
            if art:
                anchor = art.group(1)
            yield emit(text, page_no)
    if pending:
        yield emit(" ".join(pending), pending_page)


# ── HTTP ───────────────────────────────────────────────────────────────

class _Http:
    """urllib wrapper: identifying User-Agent, typed errors. Replaced in tests.

    ``cache_dir`` (optional) keeps every downloaded file under the sha-1 of
    its URL, so a re-segmentation or a re-built work DB never asks Fedlex
    for the same bytes twice. A cached GET makes no request at all."""

    def __init__(self, timeout: int = 180, cache_dir: str | Path | None = None):
        self.timeout = timeout
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.last_from_cache = False

    def _cache_path(self, url: str) -> Path | None:
        if not self.cache_dir:
            return None
        return self.cache_dir / (hashlib.sha1(url.encode("utf-8")).hexdigest() + ".bin")

    def _req(self, url: str, method: str = "GET", headers: dict | None = None):
        import urllib.request
        h = {"User-Agent": USER_AGENT}
        h.update(headers or {})
        return urllib.request.Request(url, method=method, headers=h)

    def head_length(self, url: str) -> int | None:
        import urllib.request
        with urllib.request.urlopen(self._req(url, "HEAD"), timeout=60) as r:
            cl = r.headers.get("Content-Length")
            return int(cl) if cl and cl.isdigit() else None

    def get_prefix(self, url: str, n: int) -> bytes:
        import urllib.request
        with urllib.request.urlopen(
                self._req(url, headers={"Range": f"bytes=0-{n - 1}"}),
                timeout=self.timeout) as r:
            return r.read(n)

    def get(self, url: str, max_bytes: int) -> bytes:
        import urllib.request
        cp = self._cache_path(url)
        if cp is not None and cp.exists():
            self.last_from_cache = True
            return cp.read_bytes()
        self.last_from_cache = False
        with urllib.request.urlopen(self._req(url), timeout=self.timeout) as r:
            cl = r.headers.get("Content-Length")
            if cl and cl.isdigit() and int(cl) > max_bytes:
                raise _TooLarge(int(cl))
            data = r.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise _TooLarge(len(data))
        if cp is not None:
            tmp = cp.with_suffix(".part")
            tmp.write_bytes(data)
            os.replace(tmp, cp)
        return data


class _TooLarge(Exception):
    def __init__(self, size: int):
        super().__init__(f"{size} bytes > {MAX_FILE_BYTES}")
        self.size = size


def _host_of(url: str | None) -> str:
    from urllib.parse import urlsplit
    return urlsplit(url or "").netloc.lower()


def _file_host_allowed(url: str | None) -> bool:
    return bool(url) and _host_of(url) in ALLOWED_FILE_HOSTS


_WAYBACK_SNAPSHOT_RE = re.compile(r"^(https://web\.archive\.org/web/\d{14})/")


def download_url(file_url: str) -> str:
    """The URL whose bytes are the file. A Wayback snapshot URL (the one
    shown to users) answers with the replay frame; the ``id_`` form of the
    same snapshot answers with the archived bytes as captured."""
    return _WAYBACK_SNAPSHOT_RE.sub(r"\1id_/", file_url, count=1)


# ── Fetch ──────────────────────────────────────────────────────────────

def _queue(conn: sqlite3.Connection, max_attempts: int = MAX_ATTEMPTS) -> list[sqlite3.Row]:
    prio = " ".join(f"WHEN {c} THEN {i}" for i, c in enumerate(TEXT_TYPE_PRIORITY))
    return conn.execute(
        f"""SELECT f.file_id, f.document_uri, f.language, f.file_url, f.format,
                   f.attempts, d.doc_type_code, p.year
            FROM consultation_files f
            JOIN consultation_documents d ON d.document_uri = f.document_uri
            JOIN consultation_procedures p ON p.procedure_uri = d.procedure_uri
            WHERE f.text_policy = 'text'
              AND (f.status = '{ST_PENDING}' OR (f.status = '{ST_ERROR}' AND f.attempts < ?))
            ORDER BY CASE d.doc_type_code {prio} ELSE 99 END,
                     p.year DESC, f.document_uri,
                     CASE f.language WHEN 'de' THEN 0 WHEN 'fr' THEN 1 WHEN 'it' THEN 2 ELSE 3 END""",
        (max_attempts,)).fetchall()


def _sibling_done(conn: sqlite3.Connection, document_uri: str, file_id: int):
    return conn.execute(
        f"""SELECT file_id, byte_size, prefix_sha256, sha256 FROM consultation_files
            WHERE document_uri=? AND file_id<>? AND status IN ('{ST_DONE}', '{ST_NO_TEXT}')
              AND byte_size IS NOT NULL AND prefix_sha256 IS NOT NULL""",
        (document_uri, file_id)).fetchall()


def store_text(conn: sqlite3.Connection, file_id: int, paragraphs: list[dict]) -> None:
    conn.execute("DELETE FROM consultation_paragraphs WHERE file_id=?", (file_id,))
    conn.executemany(
        """INSERT INTO consultation_paragraphs
           (file_id, para_order, page_number, section_path, article_anchor, text, text_length)
           VALUES (?,?,?,?,?,?,?)""",
        [(file_id, p["para_order"], p["page_number"], p["section_path"],
          p["article_anchor"], p["text"], p["text_length"]) for p in paragraphs])


def fetch_one(conn: sqlite3.Connection, row, http: _Http) -> str:
    """Fetch, extract and store one file. Returns the final status. Raises
    SourceUnavailable on a refusal (403/429) so the loop stops."""
    import urllib.error
    fid, url, fmt, lang = row["file_id"], row["file_url"], row["format"], row["language"]
    if not _file_host_allowed(url):
        conn.execute("UPDATE consultation_files SET status=?, error=? WHERE file_id=?",
                     (ST_UNSUPPORTED, f"host not allowed: {url}", fid))
        conn.commit()
        return ST_UNSUPPORTED
    try:
        # Same bytes as another language's file of this document? Stellungnahmen
        # and many Ergebnisberichte are one PDF filed under all three languages.
        # (Fedlex only: the archive sources file one PDF per language, and a
        # HEAD + range request would be two more Wayback requests per file.)
        siblings = (_sibling_done(conn, row["document_uri"], fid)
                    if _host_of(url) == FEDLEX_FILE_HOST else [])
        if siblings:
            size = http.head_length(url)
            match = [s for s in siblings if s["byte_size"] == size]
            if match:
                prefix = http.get_prefix(url, SAME_FILE_PREFIX_BYTES)
                psha = hashlib.sha256(prefix).hexdigest()
                for s in match:
                    if s["prefix_sha256"] == psha:
                        conn.execute(
                            """UPDATE consultation_files SET status=?, same_as_file_id=?,
                               byte_size=?, prefix_sha256=?, sha256=?, attempts=attempts+1,
                               last_attempt_at=?, error=NULL, http_status=NULL,
                               ingested_at=? WHERE file_id=?""",
                            (ST_SAME_AS, s["file_id"], size, psha, s["sha256"],
                             _now(), _now(), fid))
                        conn.commit()
                        return ST_SAME_AS
        data = http.get(download_url(url), MAX_FILE_BYTES)
    except _TooLarge as e:
        conn.execute("UPDATE consultation_files SET status=?, byte_size=?, attempts=attempts+1, "
                     "last_attempt_at=?, error=? WHERE file_id=?",
                     (ST_TOO_LARGE, e.size, _now(), str(e), fid))
        conn.commit()
        return ST_TOO_LARGE
    except urllib.error.HTTPError as e:
        if e.code == 403 and _host_of(url) == WAYBACK_HOST:
            conn.execute("UPDATE consultation_files SET status=?, http_status=?, "
                         "attempts=attempts+1, last_attempt_at=?, error=? WHERE file_id=?",
                         (ST_NOT_ARCHIVED, 403, _now(), "capture excluded from replay (HTTP 403)",
                          fid))
            conn.commit()
            return ST_NOT_ARCHIVED
        if e.code in FATAL_HTTP_STATUSES:
            raise SourceUnavailable(f"GET {url} answered HTTP {e.code}", status=e.code) from e
        status = ST_NOT_FOUND if e.code in (404, 410) else ST_ERROR
        conn.execute("UPDATE consultation_files SET status=?, http_status=?, attempts=attempts+1, "
                     "last_attempt_at=?, error=? WHERE file_id=?",
                     (status, e.code, _now(), f"HTTP {e.code}", fid))
        conn.commit()
        if status == ST_ERROR:
            raise
        return status
    except (urllib.error.URLError, OSError) as e:
        conn.execute("UPDATE consultation_files SET status=?, attempts=attempts+1, "
                     "last_attempt_at=?, error=? WHERE file_id=?",
                     (ST_ERROR, _now(), f"{type(e).__name__}: {e}"[:500], fid))
        conn.commit()
        raise

    sha = hashlib.sha256(data).hexdigest()
    psha = hashlib.sha256(data[:SAME_FILE_PREFIX_BYTES]).hexdigest()
    try:
        if fmt == "docx":
            pages, paginated = docx_page_blocks(data), False
        else:
            pages, paginated = pdf_page_blocks(data), True
    except Exception as e:  # noqa: BLE001 - corrupt file: record, keep going
        # Not the host's fault: final status, no retry, no failure streak.
        conn.execute("UPDATE consultation_files SET status=?, byte_size=?, sha256=?, "
                     "prefix_sha256=?, attempts=attempts+1, last_attempt_at=?, error=? "
                     "WHERE file_id=?",
                     (ST_PARSE_ERROR, len(data), sha, psha, _now(), f"parse: {e}"[:500], fid))
        conn.commit()
        return ST_PARSE_ERROR
    if _host_of(url) != FEDLEX_FILE_HOST:
        # Archive sources: one trilingual PDF is often linked from each
        # language's page under its own path, and a BJ project page can list
        # the very file Fedlex holds; index the text once (any document).
        twin = conn.execute(
            f"SELECT file_id FROM consultation_files WHERE file_id<>? AND sha256=? "
            f"AND status='{ST_DONE}' ORDER BY (source IS NOT NULL), file_id LIMIT 1",
            (fid, sha)).fetchone()
        if twin is not None:
            conn.execute(
                """UPDATE consultation_files SET status=?, same_as_file_id=?, byte_size=?,
                   sha256=?, prefix_sha256=?, attempts=attempts+1, last_attempt_at=?,
                   error=NULL, http_status=NULL, ingested_at=? WHERE file_id=?""",
                (ST_SAME_AS, twin[0], len(data), sha, psha, _now(), _now(), fid))
            conn.commit()
            return ST_SAME_AS
    paragraphs = list(segment_blocks(pages, lang, paginated=paginated))
    chars = sum(p["text_length"] for p in paragraphs)
    empty = sum(1 for blocks in pages if sum(len(b.strip()) for b in blocks) < 20)
    status = ST_DONE if chars >= 200 else ST_NO_TEXT
    with conn:
        if status == ST_DONE:
            store_text(conn, fid, paragraphs)
        conn.execute(
            """UPDATE consultation_files SET status=?, byte_size=?, sha256=?, prefix_sha256=?,
               page_count=?, char_count=?, empty_pages=?, attempts=attempts+1,
               last_attempt_at=?, error=NULL, http_status=NULL, ingested_at=?
               WHERE file_id=?""",
            (status, len(data), sha, psha, len(pages) if paginated else None, chars,
             empty if paginated else None, _now(), _now(), fid))
    return status


def fetch(conn: sqlite3.Connection, *, rate_limit: float = 0.5,
          max_seconds: float | None = None, max_files: int | None = None,
          http: _Http | None = None,
          cache_dir: str | Path | None = None,
          sleep: Callable[[float], None] = time.sleep,
          clock: Callable[[], float] = time.monotonic,
          max_consecutive_failures: int = STOP_AFTER_CONSECUTIVE_FAILURES) -> dict:
    """Work the queue in priority order under the Fedlex fetch policy: stop
    at once on 429/403, after N consecutive failures, or at the time/file
    budget; back off exponentially after a failure. Every file commits on its
    own, so a stopped run resumes where it ended.

    The archive hosts (Wayback, bj.admin.ch) are paced by HOST_MIN_INTERVAL
    and stopped on their own: a refusal or a failure streak there ends that
    host's files for the run (``stopped_hosts``), never the Fedlex files."""
    http = http or _Http(cache_dir=cache_dir)
    queue = _queue(conn)
    stats: dict = {"queued": len(queue), "processed": 0, "stopped": None,
                   "by_status": {}, "errors": 0, "stopped_hosts": {}}
    t0 = clock()
    consecutive: dict[str, int] = {}
    for i, row in enumerate(queue, 1):
        if max_files is not None and stats["processed"] >= max_files:
            stats["stopped"] = "max_files"
            break
        if max_seconds is not None and clock() - t0 >= max_seconds:
            stats["stopped"] = "max_seconds"
            break
        host = _host_of(row["file_url"])
        if host in stats["stopped_hosts"]:
            continue
        # Fedlex keeps the run-wide semantics (one streak, one stop for all).
        key = host if host in HOST_MIN_INTERVAL else ""
        pause = max(rate_limit, HOST_MIN_INTERVAL.get(host, 0.0))
        failed = False
        if hasattr(http, "last_from_cache"):
            http.last_from_cache = False
        try:
            st = fetch_one(conn, row, http)
            stats["by_status"][st] = stats["by_status"].get(st, 0) + 1
            consecutive[key] = 0
        except Exception as e:  # noqa: BLE001
            failed = True
            stats["errors"] += 1
            consecutive[key] = n_fail = consecutive.get(key, 0) + 1
            code = http_status_of(e)
            log.warning("  %s (%s) failed: %s", row["document_uri"], row["language"], e)
            reason = None
            if code in FATAL_HTTP_STATUSES:
                reason = f"http_{code}"
            elif n_fail >= max_consecutive_failures:
                reason = f"{n_fail}_consecutive_failures"
            else:
                pause = max(pause, min(BACKOFF_MAX_SECONDS,
                                       BACKOFF_BASE_SECONDS * 2 ** (n_fail - 1)))
            if reason and key:
                stats["stopped_hosts"][host] = reason
                log.error("  %s stopped (%s); its files wait for the next run", host, reason)
            elif reason:
                stats["stopped"] = reason
        stats["processed"] += 1
        if stats["stopped"]:
            log.error("  stopping (%s) at %d/%d; the next run resumes", stats["stopped"],
                      i, len(queue))
            break
        if i % 50 == 0:
            log.info("  progress %d/%d %s", i, len(queue), stats["by_status"])
        if failed or not getattr(http, "last_from_cache", False):
            sleep(pause)
    stats["elapsed_seconds"] = round(clock() - t0, 1)
    set_meta(conn, "last_fetch", {"at": _now(), **stats})
    conn.commit()
    return stats


# ── Publish ────────────────────────────────────────────────────────────

def coverage_counts(conn: sqlite3.Connection) -> dict:
    """The served corpus in numbers: procedures (all / from Fedlex), the
    documents with searchable text (by type), paragraphs, year range."""
    readable = f"f.status IN ('{ST_DONE}', '{ST_SAME_AS}')"
    procs, fedlex, lo, hi = conn.execute(
        "SELECT COUNT(*), SUM(source IS NULL), MIN(year), MAX(year) FROM consultation_procedures"
    ).fetchone()
    by_type = {r[0]: int(r[1]) for r in conn.execute(
        f"""SELECT d.doc_type, COUNT(DISTINCT d.document_uri) FROM consultation_documents d
            JOIN consultation_files f ON f.document_uri = d.document_uri
            WHERE {readable} GROUP BY 1""")}
    docs = conn.execute(f"SELECT COUNT(DISTINCT document_uri) FROM consultation_files f "
                        f"WHERE {readable}").fetchone()[0]
    return {
        "procedures": int(procs or 0), "procedures_fedlex": int(fedlex or 0),
        "documents": int(docs or 0), "by_doc_type": by_type,
        "paragraphs": int(conn.execute("SELECT COUNT(*) FROM consultation_paragraphs").fetchone()[0]),
        "year_range": {"from": lo, "to": hi} if lo else None,
        "counted_at": _now(),
    }


def publish(conn: sqlite3.Connection, out_path: str | Path) -> Path:
    """Atomic snapshot of the work DB onto the served path. The .tmp sits
    next to the REAL file (a symlinked output/ entry pointing at the data
    volume must be replaced through the link, on the same filesystem)."""
    out = Path(out_path)
    real = Path(os.path.realpath(str(out)))
    tmp = real.with_name(real.name + ".tmp")
    if tmp.exists():
        tmp.unlink()
    set_meta(conn, "published_at", _now())
    # Counts for stats.json (generate_stats reads them from meta instead of
    # counting ~10M paragraphs during the nightly build).
    set_meta(conn, "coverage", coverage_counts(conn))
    conn.commit()
    conn.execute("VACUUM INTO ?", (str(tmp),))
    chk = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True)
    try:
        ok = chk.execute("PRAGMA quick_check").fetchone()[0]
        n = chk.execute("SELECT COUNT(*) FROM consultation_procedures").fetchone()[0]
    finally:
        chk.close()
    if ok != "ok" or n == 0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"snapshot failed its check (quick_check={ok!r}, procedures={n})")
    os.replace(str(tmp), str(real))
    log.info("published %s (%d procedures)", real, n)
    return real


def publish_due(conn: sqlite3.Connection, out_path: str | Path, max_age_days: int = 7) -> bool:
    """A publish without new files is still due when the served file is
    missing or older than ``max_age_days`` (procedure metadata — status,
    deadlines — changes without new files)."""
    if not Path(os.path.realpath(str(out_path))).exists():
        return True
    row = conn.execute("SELECT value FROM meta WHERE key='published_at'").fetchone()
    if not row:
        return True
    try:
        last = datetime.fromisoformat(row[0])
    except ValueError:
        return True
    return (datetime.now(timezone.utc) - last).days >= max_age_days


# ── Status ─────────────────────────────────────────────────────────────

def status_summary(conn: sqlite3.Connection) -> dict:
    out: dict = {
        "procedures": conn.execute("SELECT COUNT(*) FROM consultation_procedures").fetchone()[0],
        "documents": conn.execute("SELECT COUNT(*) FROM consultation_documents").fetchone()[0],
        "paragraphs": conn.execute("SELECT COUNT(*) FROM consultation_paragraphs").fetchone()[0],
        "files": {},
        "by_source": {},
    }
    for r in conn.execute(
            """SELECT d.doc_type, f.status, COUNT(*) n FROM consultation_files f
               JOIN consultation_documents d USING(document_uri)
               GROUP BY 1, 2 ORDER BY 1, 2"""):
        out["files"].setdefault(r["doc_type"], {})[r["status"]] = r["n"]
    for r in conn.execute(
            """SELECT COALESCE(source, 'fedlex') src, status, COUNT(*) n
               FROM consultation_files GROUP BY 1, 2 ORDER BY 1, 2"""):
        out["by_source"].setdefault(r["src"], {})[r["status"]] = r["n"]
    return out


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s",
                        datefmt="%H:%M:%S")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("command", choices=["discover", "fetch", "publish", "status", "run"])
    ap.add_argument("--work-db", required=True)
    ap.add_argument("--out", help="served database path (publish / run)")
    ap.add_argument("--years", help="discover only these years, e.g. 2023,2024")
    ap.add_argument("--rate-limit", type=float, default=0.5)
    ap.add_argument("--max-seconds", type=float)
    ap.add_argument("--max-files", type=int)
    ap.add_argument("--cache-dir", help="keep downloaded files here (optional)")
    args = ap.parse_args(argv)

    conn = connect_work_db(args.work_db)
    rc = 0
    changed = False
    if args.command in ("discover", "run"):
        years = [int(y) for y in args.years.split(",")] if args.years else None
        ds = discover(conn, years=years)
        print(json.dumps(ds, ensure_ascii=False))
        changed |= bool(ds["files_new"] or ds["files_requeued"])
    if args.command in ("fetch", "run"):
        st = fetch(conn, rate_limit=args.rate_limit, max_seconds=args.max_seconds,
                   max_files=args.max_files, cache_dir=args.cache_dir)
        print(json.dumps(st, ensure_ascii=False))
        changed |= bool(st["processed"])
        if st["stopped"] and st["stopped"] not in ("max_seconds", "max_files"):
            rc = INGEST_STOPPED_EXIT_CODE
    if args.command in ("publish", "run"):
        if not args.out:
            ap.error("--out is required for publish")
        if args.command == "publish" or changed or publish_due(conn, args.out):
            publish(conn, args.out)
        else:
            log.info("nothing new since the last publish; %s left as it is", args.out)
    if args.command == "status":
        print(json.dumps(status_summary(conn), ensure_ascii=False, indent=2))
    conn.close()
    return rc


if __name__ == "__main__":
    sys.exit(main())
