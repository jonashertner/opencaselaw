"""Historical Botschaften (1850-1999) from the Bundesarchiv PDF scans.

Companion to ``build_botschaft_corpus.py`` for the ~20,000 Federal Council
messages that Fedlex lists without a ``typeDocument`` and whose only file
is an OCR'd PDF on ``www.amtsdruckschriften.bar.admin.ch``. Discovery is
``scrapers.fedlex_materialien.discover_fga_botschaften_untyped``; the
download goes through ``search_stack.bar_fetcher.PoliteFetcher`` (single
connection, >= 2.5 s spacing, backoff, hard stop); this module does the
rest: clean the OCR page text, split paragraphs, store the document with
the Fedlex title and citation, and keep the resume state.

What is stored (all additive to the existing schema):

* ``botschaft_documents`` rows with ``format='pdf'``,
  ``source_collection='bar-amtsdruckschriften'``, ``bbl_volume``, the
  Fedlex ``title`` / ``publication_date`` / ``dct:identifier`` citation
  ("BBl 1975 I 609"), ``page_count`` and ``text_hash``. PDF bytes are not
  retained — only text.
* ``botschaft_paragraphs`` (+ FTS5 via the existing triggers).
* ``botschaft_fetch_state`` keyed by (eli_uri, language): done / exists /
  collision / not_found / no_text / no_citation / error, with attempt
  counts, so a run can stop at any point and the next one continues where
  it left off.

Every document is one transaction: the ``botschaft_documents`` row, its
paragraphs and the state row commit together or not at all; a failure
inside the insert rolls back and records ``error``. One run at a time per
host: ``run_historical_backfill`` holds an ``flock`` on a lock file for the
whole run and a second run refuses to start (CLI exit 3).

The citation string is Fedlex's (``dct:identifier``, else
``jolux:historicalLegalId``, both "BBl 1975 I 609" — verified by SPARQL
2026-09-09). It is never assembled from year / volume / page. When Fedlex
has neither, the work is recorded as ``no_citation`` and not fetched:
``botschaft_documents.bbl_citation`` is NOT NULL, and making it nullable
is a table rebuild (see the runbook).

Text handling (R1-R3: the corpus is verbatim). The cleaner removes what is
not the document — the Bundesblatt masthead of an issue's first page, the
running page number, the ``#ST#`` scan markers — rejoins words the
typesetter hyphenated at line ends, and groups lines into paragraphs. It
never rewrites a word; OCR errors in the source ("Eeihe" for "Reihe")
stay as they are. Two rules keep it from deleting body text: a line is
only ever dropped by POSITION (the first or last one or two lines of a
page, or the masthead block at the top of an issue-first page) AND by
pattern, never anywhere else on the page; and a line-end hyphen is only
joined when the result is plausibly one word (lower-case continuation,
no known hyphenated prefix or compound such as "peut-|être",
"c'est-à-|dire", "porte-|parole"). When in doubt the hyphen stays.

The collision rule: ``botschaft_documents`` is UNIQUE on (bbl_year,
bbl_page, language), a constraint from the volume-less post-1998
Bundesblatt. Before 1998 a year has several volumes and the same page
number recurs (301 (year, page) pairs in the German set). The first work
to claim a pair keeps it; later ones are recorded as ``collision`` with the
id of the row that holds the pair and are not fetched. Lifting this needs
a table rebuild (not additive) — see the runbook's open questions.
"""
from __future__ import annotations

import hashlib
import io
import logging
import os
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from search_stack.bar_fetcher import FetchStopped, PoliteFetcher

log = logging.getLogger("botschaft_historical")

SOURCE_COLLECTION_BAR = "bar-amtsdruckschriften"
SOURCE_COLLECTION_FEDLEX = "fedlex-filestore"

# Terminal states: the candidate is never fetched again by a later run.
TERMINAL_STATES = {"done", "exists", "collision", "no_text", "no_citation"}
# Retried on a later run, up to --max-attempts.
RETRY_STATES = {"error", "not_found"}


# ── OCR page cleaning ─────────────────────────────────────────────────

_ST_MARK = "#ST#"
_PAGE_NO_RE = re.compile(r"^\s*\d{1,5}\s*$")
# The gazette's title alone on a line: the masthead of an issue-first page
# opens with it. Only when it is present in the head zone are the other
# masthead patterns applied — on every other page the words "Nachnahme",
# "Halbjahr", "Preis … Franken", "Band II" are body text.
_MASTHEAD_TITLE_RE = re.compile(
    r"^\s*(?:Bundesblatt|Feuille\s+f[ée]d[ée]rale|Foglio\s+federale)\s*$", re.IGNORECASE,
)
# Lines of the Bundesblatt / Feuille fédérale / Foglio federale masthead that
# opens every issue (the first page of a document that starts an issue
# carries it above the title). Matched only in the head zone of a page
# whose head zone also carries the title line.
_MASTHEAD_RE = re.compile(
    r"^\s*(?:Bundesblatt|Feuille\s+f[ée]d[ée]rale|Foglio\s+federale)\s*$"
    r"|\b\d{1,3}\.?\s*(?:Jahrgang|ann[ée]e|anno)\b"
    r"|\bBand\s+[IVX]+\b|\bVolume\s+[IVX]+\b|\bVol(?:ume)?\.\s*[IVX]+\b"
    r"|Erscheint\s*w[öo]chentlich|Para[iî]t\s+chaque|Esce\s+(?:una|ogni)"
    r"|Einr[üu]ckungsgeb[üu]hr|Nachnahme|Inseratenverwaltung|Publicitas"
    r"|St[äa]mpfli|Petitzeile|Insertions?\b|Inserzioni|Annonces\b"
    r"|\bPreis\b.*\b(?:Franken|Fr\.)|\bPrix\b.*\bfr(?:ancs|\.)|\bPrezzo\b.*\bfr"
    r"|Postbestellungsgeb|Postzustellungsgeb|Zentraldienst|Periodika|Halbjahr"
    r"|^\s*N[or]?\.?[°º]?\s*\d{1,3}\s*$",
    re.IGNORECASE,
)
# Printer's signature at the foot of a page ("Bundesblatt. 107. Jahrg. Bd. I 77",
# "Bundesblatt 127 Jahrg Bd I 23", "Feuille fédérale. 107e année. Vol. I 12"):
# starts with the gazette's name, names the volume year ("Jahrg" / "année" /
# "anno") and ends with a small integer (the sheet number). A citation in
# the body ("Bundesblatt 1934, Bd. I, S. 350 und die dortigen Ausführungen.")
# has no "Jahrg" and ends in a word or punctuation, so it is never matched.
_SIGNATURE_RE = re.compile(
    r"^\s*(?:Bundesblatt|Feuille\s+f[ée]d[ée]rale|Foglio\s+federale)\b"
    r".*\b(?:Jahrg|ann[ée]e|anno)\b.*?\b\d{1,3}\s*\.?\s*$",
    re.IGNORECASE,
)
# The print-job number ("1975-45") on the last line of a document.
_PRINT_JOB_RE = re.compile(r"^\s*\d{4}-\d{1,3}\s*$")
# A foot line is dropped only in the last _TAIL_ZONE_LINES non-blank lines.
_TAIL_ZONE_LINES = 2
# The Bundesarchiv appends one sheet with the catalogue record of the scan
# ("Schweizerisches Bundesarchiv, Digitale Amtsdruckschriften …"). It is not
# part of the document and is dropped as a whole page.
_ARCHIVE_SHEET_RE = re.compile(
    r"^\s*Schweizerisches\s+Bundesarchiv,\s+Digitale\s+Amtsdruckschriften", re.IGNORECASE,
)
# The masthead block of an issue-first page (title, year, price, printer,
# issue number) runs to about ten lines; searched for the gazette title.
_HEAD_ZONE_LINES = 12
# On every page: the page number (and, on a document's first page, the
# Geschäftsnummer above the title) sit in the first two lines.
_HEAD_NUMBER_LINES = 2
_ENUMERATOR_RE = re.compile(
    r"^\s*(?:[IVXLC]+\.|\d+(?:\.\d+)*\.?|[A-Za-z][.)]|[a-z]\)|[-–•])\s+\S"
)
_ARTICLE_LINE_RE = re.compile(r"^\s*Art(?:\.|ikel|icle|icolo)\s+\d+[a-z]?\b", re.IGNORECASE)
_SALUTATION_RE = re.compile(
    r"^\s*(?:Sehr\s+geehrte|Herr\s+Pr[äa]sident|Frau\s+Pr[äa]sidentin|Hochgeehrte|"
    r"Tit\.|Monsieur\s+le\s+Pr[ée]sident|Madame\s+la\s+Pr[ée]sidente|Messieurs|"
    r"Mesdames|Signor\s+Presidente|Signora\s+Presidente|Onorevoli)",
    re.IGNORECASE,
)
_TERMINAL_RE = re.compile(r"""[.!?:;»")\]]\s*$""")
# The kind word that opens the title block ("Botschaft", "6918 Botschaft",
# "Message", "Messaggio"; also reports). The block runs to the first line
# that closes with punctuation — usually "(Vom 8. März 1935.)".
_TITLE_KIND_RE = re.compile(
    r"^\s*(?:\d{2,5}(?:\.\d{3})?\s+)?(?:Botschaft|Message|Messaggio|Bericht|Rapport|Rapporto)\s*$",
    re.IGNORECASE,
)


def _is_masthead(line: str) -> bool:
    return bool(_MASTHEAD_RE.search(line))


def _next_nonblank(lines: list[str], start: int) -> str:
    for ln in lines[start:]:
        if ln.strip():
            return ln
    return ""


def _strip_head_zone(lines: list[str]) -> list[str]:
    """Drop what sits above the text at the top of a page — by position.

    * On every page: blank lines and scan markers, and a bare number in the
      first line (the page number). A bare number in the second line is
      the Geschäftsnummer only when the title block follows it; any other
      second-line number (a year in a table that continues from the
      previous page) is body text and stays.
    * On an issue-first page — recognised by the gazette's title alone on
      a line in the head zone — the masthead block: lines matching the
      masthead patterns, from the top, up to the first line that is not
      one. A page without that title line never loses a masthead-pattern
      line ("Die Nachnahme wird …", "Im ersten Halbjahr …" are text).
    """
    head = [ln for ln in lines[:_HEAD_ZONE_LINES] if ln.strip()]
    issue_first = any(_MASTHEAD_TITLE_RE.match(ln) for ln in head)
    i = 0
    n = len(lines)
    pos = 0        # non-blank, non-masthead lines seen: the number rule is positional
    while i < n and i < _HEAD_ZONE_LINES:
        ln = lines[i]
        if not ln.strip() or _ST_MARK in ln:
            i += 1
            continue
        if issue_first and _is_masthead(ln):
            i += 1
            continue
        if _PAGE_NO_RE.match(ln) and pos < _HEAD_NUMBER_LINES:
            following = _next_nonblank(lines, i + 1)
            if pos == 0 or _TITLE_KIND_RE.match(following) or _MASTHEAD_TITLE_RE.match(following):
                i += 1
                pos += 1
                continue
        break
    return lines[i:]


def _is_foot_line(line: str) -> bool:
    return bool(_PAGE_NO_RE.match(line) or _PRINT_JOB_RE.match(line)
                or _SIGNATURE_RE.match(line))


def _strip_tail(lines: list[str]) -> list[str]:
    """Drop the page number, the printer's signature and the print-job number
    from the foot of a page — only in the last two non-blank lines, only
    when the line is nothing but one of those."""
    dropped = 0
    while lines and (not lines[-1].strip() or _ST_MARK in lines[-1]):
        lines.pop()
    while lines and dropped < _TAIL_ZONE_LINES and _is_foot_line(lines[-1]):
        lines.pop()
        dropped += 1
        while lines and (not lines[-1].strip() or _ST_MARK in lines[-1]):
            lines.pop()
    return lines


# A suspended hyphen ("Kriegs- und Nachkriegszeit") continues with a
# conjunction on the next line; the hyphen and the space stay.
_SUSPENDED_NEXT_RE = re.compile(r"^(?:und|oder|bzw\.|sowie|beziehungsweise|et|ou|e|o|ed|od)\b")

# Fragments before a line-end hyphen that carry a real hyphen in the
# language: the typesetter breaks "peut-être" at its own hyphen, so
# "peut-" / "être" must come back as "peut-être", not "peutêtre". Lower-case,
# compared against the last word before the hyphen. Kept small on purpose:
# a fragment that is also a syllable of ordinary words ("pré-|sident",
# "au-|torité", "entre-|prise", "co-|mité", "Pro-|blem") is not listed, so
# those words are rejoined; the doubtful cases lean towards keeping the
# hyphen, which is verbatim-safe.
_HYPHEN_PREFIXES: dict[str, frozenset[str]] = {
    "de": frozenset({"quasi", "semi", "non"}),
    "fr": frozenset({
        "peut", "est", "sont", "ont", "faut", "doit", "avons", "avez",
        "sous", "non", "demi", "semi", "vice", "quasi", "anti", "contre", "avant",
        "arrière", "arriere", "sans", "porte", "garde", "après", "apres", "outre",
        "pseudo", "néo", "neo", "socio", "franco", "italo", "germano", "nord", "sud",
        "ouest", "grand", "grande", "petit", "petite", "haut", "haute", "saint",
        "sainte", "lieu", "chef", "vis", "tout", "toute", "même", "meme",
    }),
    "it": frozenset({
        "ex", "vice", "anti", "semi", "quasi", "neo", "post", "non", "filo", "ultra",
        "extra", "socio", "franco", "italo", "svizzero", "nord", "ovest", "medio",
        "afro", "euro", "multi", "pluri", "mono",
    }),
}
# Fragments after a line-end hyphen that close a hyphenated compound
# ("c'est-à-" / "dire", "au-" / "dessus", "ci-" / "joint", "moi-" / "même",
# "dit-" / "il"): the hyphen stays. Words that are also common second
# halves of broken words ("commer-|ce", "moy-|en", "ai-|les", "va-|leur")
# are deliberately absent.
_HYPHEN_SUFFIXES: dict[str, frozenset[str]] = {
    "de": frozenset(),
    "fr": frozenset({
        "être", "etre", "dire", "dessus", "dessous", "delà", "dela", "là", "vis",
        "même", "meme", "mêmes", "memes", "parole", "après", "apres", "contre",
        "joint", "jointe", "joints", "jointes", "annexé", "annexée", "inclus", "incluse",
        "il", "ils", "elle", "elles", "je", "nous", "vous", "moi", "toi", "lui",
    }),
    "it": frozenset({"presidente", "direttore", "console"}),
}


def _last_word(s: str) -> str:
    """The alphabetic word closing ``s`` (which ends in "-"), lower-cased;
    "" when the fragment is not a word (digits, punctuation)."""
    body = s[:-1]
    m = re.search(r"([^\W\d_]+)$", body, re.UNICODE)
    return m.group(1).lower() if m else ""


def _first_word(s: str) -> str:
    m = re.match(r"([^\W\d_]+)", s, re.UNICODE)
    return m.group(1).lower() if m else ""


def _join_hyphen(prev: str, nxt: str, language: str = "de") -> str:
    """Join two physical lines across a line-end hyphen — conservatively.

    * lower-case continuation whose halves are plain word fragments and
      not a known hyphenated prefix / suffix of the language: one word
      ("Schweize-" / "rischen" → "Schweizerischen", "fé-" / "déral" →
      "fédéral");
    * a known hyphenated prefix or suffix ("peut-" / "être", "c'est-à-" /
      "dire", "porte-" / "parole", "au-" / "dessus"), or a fragment that
      is itself preceded by a hyphen ("vis-à-", "a-t-"): the hyphen stays
      and the lines meet without a space ("peut-être", "c'est-à-dire");
    * an upper-case continuation is a broken compound ("Basel-" / "Stadt",
      "Saint-" / "Gall"): the hyphen stays;
    * digits on both sides are a range ("1934-" / "1935"): the hyphen stays;
    * a suspended hyphen before "und" / "et", and everything else in doubt
      (punctuation, an empty fragment): the hyphen stays and the line
      break becomes a space — never a rewrite.
    """
    if prev.endswith("-") and not prev.endswith(" -"):
        if nxt[:1].isupper():
            return prev + nxt
        if nxt[:1].isdigit() and prev[-2:-1].isdigit():
            # A range broken at its dash ("1934-" / "1935").
            return prev + nxt
        if nxt[:1].islower() and not _SUSPENDED_NEXT_RE.match(nxt):
            before = _last_word(prev)
            after = _first_word(nxt)
            if not before or not after:
                return prev + " " + nxt
            body = prev[:-1]
            # "c'est-à-" / "vis-à-" / "a-t-": the fragment is itself the
            # tail of a hyphenated compound.
            tail = body[-(len(before) + 1):-len(before)] if len(body) > len(before) else ""
            compound = tail == "-"
            prefixes = _HYPHEN_PREFIXES.get(language, frozenset())
            suffixes = _HYPHEN_SUFFIXES.get(language, frozenset())
            if compound or before in prefixes or after in suffixes:
                return prev + nxt
            return prev[:-1] + nxt
    return prev + " " + nxt


def _page_paragraphs(page_text: str, language: str = "de") -> list[str]:
    """One OCR page → list of paragraph strings (lines joined by spaces).

    The OCR layer carries one physical line per text line and no blank
    lines, so the corpus parser (which splits on blank lines) would see
    each page as one paragraph. Lines are grouped: a short line ending in
    terminal punctuation closes a paragraph; an enumerated heading
    ("I. …", "2.1 …", "a) …", "Art. 2"), a salutation line and a short
    unpunctuated line that opens a paragraph stand on their own; a line
    ending in a hyphen is joined to the next.
    """
    raw = [ln.rstrip() for ln in (page_text or "").split("\n")]
    if raw and _ARCHIVE_SHEET_RE.match(raw[0]):
        return []
    lines = [ln for ln in raw if _ST_MARK not in ln]
    lines = _strip_head_zone(lines)
    lines = _strip_tail(lines)
    if not lines:
        return []
    # Full line width: justified body lines all sit near the maximum, so
    # take the 80th percentile (the median is pulled down by title lines).
    widths = sorted(len(ln) for ln in lines if len(ln) >= 30)
    full = widths[int(0.8 * (len(widths) - 1))] if widths else 60
    short_cut = 0.85 * full

    paragraphs: list[str] = []
    cur = ""
    title_block = False
    article_head = ""       # a bare "Art. N" line, prefixed to the next paragraph
    date_re = re.compile(r"^\(?\s*(?:Vom|Du|Dal|Del|Della)\b", re.IGNORECASE)

    def _close(text: str) -> None:
        nonlocal article_head
        if article_head:
            text = article_head + "\n" + text
            article_head = ""
        paragraphs.append(text)

    n = len(lines)
    for idx, ln in enumerate(lines):
        s = ln.strip()
        if not s:
            if cur:
                _close(cur)
                cur = ""
            continue
        short = len(s) < short_cut
        terminal = bool(_TERMINAL_RE.search(s))
        nxt = lines[idx + 1].strip() if idx + 1 < n else ""
        if title_block:
            cur = _join_hyphen(cur, s, language) if cur else s
            # The block runs to its first closing line; the date line
            # "(Vom 8. März 1935.)" that may follow belongs to it.
            if terminal and not date_re.match(nxt):
                _close(cur)
                cur = ""
                title_block = False
            continue
        if not cur and _TITLE_KIND_RE.match(s):
            title_block = True
            cur = s
            continue
        # A heading starts at a sentence boundary, is short and enumerated
        # ("I. …", "2.1 …", "a) …", "- …", "Art. 2"). Never mid-sentence: a
        # date at the start of a continuation line ("12. März 1954 an den …")
        # is not a heading.
        at_boundary = not cur or bool(_TERMINAL_RE.search(cur))
        is_article = _ARTICLE_LINE_RE.match(s) is not None and short and len(s) <= 40 \
            and not terminal and at_boundary
        is_heading = (
            at_boundary and short
            and (bool(_ENUMERATOR_RE.match(s)) or _ARTICLE_LINE_RE.match(s) is not None)
            and not s.endswith(",")
        )
        standalone = not cur and short and not terminal and len(s) <= 90
        salutation = short and _SALUTATION_RE.match(s) is not None
        if (is_heading or salutation) and cur:
            _close(cur)
            cur = ""
        if is_article:
            # Keep "Art. 2" as the first line of what follows so the corpus
            # parser anchors those paragraphs to article 2.
            if article_head:
                _close(article_head)
            article_head = s
            continue
        cur = _join_hyphen(cur, s, language) if cur else s
        if is_heading or standalone or salutation or (short and terminal):
            _close(cur)
            cur = ""
    if cur:
        _close(cur)
    elif article_head:
        _close(article_head)
    return [p.strip() for p in paragraphs if p.strip()]


def clean_historical_page(page_text: str, language: str = "de") -> str:
    """One OCR page → paragraphs separated by blank lines (no stitching)."""
    return "\n\n".join(_page_paragraphs(page_text, language))


def clean_historical_pages(pages: Iterable[str], language: str = "de") -> list[str]:
    """Clean every page and stitch paragraphs that run over a page break.

    A paragraph that ends a page without terminal punctuation continues
    on the next page; its continuation (the next page's first paragraph)
    is pulled back so the paragraph stays whole and keeps the page number
    on which it starts. Returns one string per input page, blank-line
    separated paragraphs inside — the shape ``parse_botschaft_text`` takes.
    """
    per_page = [_page_paragraphs(p, language) for p in pages]
    for i in range(len(per_page) - 1):
        cur, nxt = per_page[i], per_page[i + 1]
        if not cur or not nxt:
            continue
        last = cur[-1]
        if (_TERMINAL_RE.search(last) or _ENUMERATOR_RE.match(nxt[0])
                or _ARTICLE_LINE_RE.match(nxt[0]) or _TITLE_KIND_RE.match(nxt[0].split("\n", 1)[0])
                or not nxt[0][:1].islower()):
            continue
        cur[-1] = _join_hyphen(last, nxt[0], language)
        del nxt[0]
    return ["\n\n".join(p) for p in per_page]


# ── Resume state ──────────────────────────────────────────────────────

def record_state(
    conn: sqlite3.Connection, eli_uri: str, language: str, status: str, *,
    http_status: int | None = None, error: str | None = None,
    botschaft_id: int | None = None, source_url: str | None = None,
    bump_attempts: bool = True,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO botschaft_fetch_state
            (eli_uri, language, status, http_status, attempts, first_attempt_at,
             last_attempt_at, error, botschaft_id, source_url)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(eli_uri, language) DO UPDATE SET
            status = excluded.status,
            http_status = excluded.http_status,
            attempts = botschaft_fetch_state.attempts + excluded.attempts,
            last_attempt_at = excluded.last_attempt_at,
            error = excluded.error,
            botschaft_id = COALESCE(excluded.botschaft_id, botschaft_fetch_state.botschaft_id),
            source_url = COALESCE(excluded.source_url, botschaft_fetch_state.source_url)
        """,
        (eli_uri, language, status, http_status, 1 if bump_attempts else 0,
         now, now, error, botschaft_id, source_url),
    )


def load_state(conn: sqlite3.Connection, language: str) -> dict[str, tuple[str, int]]:
    """{eli_uri: (status, attempts)} for one language."""
    return {
        r[0]: (r[1], int(r[2] or 0))
        for r in conn.execute(
            "SELECT eli_uri, status, attempts FROM botschaft_fetch_state WHERE language = ?",
            (language,),
        )
    }


# ── Per-document ingest ───────────────────────────────────────────────

def _extract_pages(pdf_bytes: bytes) -> list[str]:
    try:
        import pdfplumber
    except ImportError as e:  # pragma: no cover
        raise SystemExit("pdfplumber not installed. Run: pip install pdfplumber") from e
    out: list[str] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            try:
                out.append(page.extract_text() or "")
            except Exception as e:  # noqa: BLE001
                log.warning("    page extract failed: %s", e)
                out.append("")
    return out


def store_historical_document(
    conn: sqlite3.Connection, cand, pages: list[str], pdf_bytes: bytes,
    *, source_collection: str = SOURCE_COLLECTION_BAR,
) -> tuple[int | None, int, str]:
    """Clean, parse and insert one document. Returns
    (botschaft_id or None, paragraph count, status)."""
    from search_stack.build_botschaft_corpus import (
        DATE_SOURCE_PUBLICATION, parse_botschaft_text,
    )

    cleaned = clean_historical_pages(pages, cand.language)
    paragraphs = list(parse_botschaft_text(cleaned, language=cand.language))
    if not paragraphs:
        return None, 0, "no_text"
    if not cand.citation:
        # NOT NULL column; the citation is Fedlex's or nothing (R1-R3).
        return None, 0, "no_citation"
    text_hash = hashlib.sha256(pdf_bytes).hexdigest()
    now = datetime.now(timezone.utc).isoformat()
    # One transaction: the document row never outlives a failed paragraph
    # insert. The caller adds the state row to the same transaction and
    # commits; any exception here leaves the connection clean.
    if not conn.in_transaction:
        conn.execute("BEGIN")
    try:
        cur = conn.execute(
            """
            INSERT INTO botschaft_documents
                (bbl_year, bbl_page, bbl_citation, eli_uri, title, publication_date,
                 date_source, source_url, format, language, page_count, text_hash,
                 ingested_at, source_collection, bbl_volume)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pdf', ?, ?, ?, ?, ?, ?)
            """,
            (cand.year, cand.page, cand.citation, cand.eli_uri, cand.title,
             cand.publication_date,
             # publication_date is Fedlex's jolux:publicationDate or nothing;
             # the historical path never falls back to the BBl year.
             DATE_SOURCE_PUBLICATION if cand.publication_date else None,
             cand.file_url, cand.language, len(pages),
             text_hash, now, source_collection, cand.volume),
        )
        botschaft_id = cur.lastrowid
        conn.executemany(
            """
            INSERT INTO botschaft_paragraphs
                (botschaft_id, para_order, page_number, section_path, article_anchor,
                 text, text_length)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [(botschaft_id, p["para_order"], p["page_number"], p["section_path"],
              p["article_anchor"], p["text"], p["text_length"]) for p in paragraphs],
        )
    except BaseException:
        conn.rollback()
        raise
    return botschaft_id, len(paragraphs), "done"


def ingest_historical_one(
    conn: sqlite3.Connection, cand, fetcher: PoliteFetcher,
    *, source_collection: str = SOURCE_COLLECTION_BAR,
) -> str:
    """Fetch + store one candidate. Returns the state recorded. Raises
    FetchStopped when the fetcher hit its burst limit (state for this
    candidate is written first)."""
    lang = cand.language
    if not cand.citation:
        # Fedlex has no citation string for this expression; nothing is
        # assembled from the parts. Recorded, not fetched (see module doc).
        record_state(conn, cand.eli_uri, lang, "no_citation", bump_attempts=False,
                     error=f"no dct:identifier / historicalLegalId "
                           f"(year {cand.year}, vol {cand.volume}, page {cand.page})",
                     source_url=cand.file_url)
        conn.commit()
        return "no_citation"
    # Already there (an earlier run, or the typed 1999 overlap): no request.
    # A BAR row without paragraphs (a run that died between the two
    # inserts before every document became one transaction) is not
    # "there": it is removed and the document fetched again.
    row = conn.execute(
        "SELECT d.botschaft_id, d.source_collection, "
        "  (SELECT COUNT(*) FROM botschaft_paragraphs p WHERE p.botschaft_id = d.botschaft_id) "
        "FROM botschaft_documents d WHERE d.eli_uri = ? AND d.language = ?",
        (cand.eli_uri, lang),
    ).fetchone()
    if row and (row[2] > 0 or row[1] != source_collection):
        record_state(conn, cand.eli_uri, lang, "exists", botschaft_id=row[0],
                     bump_attempts=False)
        conn.commit()
        return "exists"
    if row:
        log.warning("  %s: row %d has no paragraphs; re-fetching", cand.citation, row[0])
        conn.execute("DELETE FROM botschaft_documents WHERE botschaft_id = ?", (row[0],))
        conn.commit()
    holder = conn.execute(
        "SELECT botschaft_id, eli_uri FROM botschaft_documents "
        "WHERE bbl_year = ? AND bbl_page = ? AND language = ?",
        (cand.year, cand.page, lang),
    ).fetchone()
    if holder:
        record_state(conn, cand.eli_uri, lang, "collision", botschaft_id=holder[0],
                     error=f"(year, page) held by {holder[1]}", bump_attempts=False)
        conn.commit()
        return "collision"

    try:
        res = fetcher.fetch(cand.file_url)
    except FetchStopped:
        record_state(conn, cand.eli_uri, lang, "error", http_status=None,
                     error="run stopped: burst limit", source_url=cand.file_url)
        conn.commit()
        raise
    if not res.ok:
        status = "not_found" if res.status in (404, 410) else "error"
        record_state(conn, cand.eli_uri, lang, status, http_status=res.status,
                     error=res.error, source_url=cand.file_url)
        conn.commit()
        return status
    body = res.content or b""
    if not body.startswith(b"%PDF"):
        record_state(conn, cand.eli_uri, lang, "error", http_status=res.status,
                     error=f"not a PDF ({body[:40]!r})", source_url=cand.file_url)
        conn.commit()
        return "error"
    try:
        pages = _extract_pages(body)
    except Exception as e:  # noqa: BLE001
        record_state(conn, cand.eli_uri, lang, "error", http_status=res.status,
                     error=f"pdf extract: {type(e).__name__}: {e}", source_url=cand.file_url)
        conn.commit()
        return "error"
    try:
        bid, n_paras, status = store_historical_document(
            conn, cand, pages, body, source_collection=source_collection,
        )
    except sqlite3.IntegrityError as e:
        conn.rollback()
        record_state(conn, cand.eli_uri, lang, "collision", error=str(e),
                     source_url=cand.file_url)
        conn.commit()
        return "collision"
    except Exception as e:  # noqa: BLE001 — store() has rolled back
        if conn.in_transaction:
            conn.rollback()
        record_state(conn, cand.eli_uri, lang, "error", http_status=res.status,
                     error=f"store: {type(e).__name__}: {e}", source_url=cand.file_url)
        conn.commit()
        log.warning("  %s store failed, rolled back: %s", cand.citation, e)
        return "error"
    try:
        record_state(conn, cand.eli_uri, lang, status, http_status=res.status,
                     botschaft_id=bid, source_url=cand.file_url,
                     error=None if status == "done" else "no paragraphs after cleaning")
        conn.commit()
    except BaseException:
        conn.rollback()      # the document goes with its state row, or not at all
        raise
    log.info("  %s %s: %d pages -> %d paragraphs [%s]",
             cand.citation, lang, len(pages), n_paras, status)
    return status


# ── Run loop ──────────────────────────────────────────────────────────

def select_candidates(
    candidates: list, state: dict[str, tuple[str, int]], *,
    host_allow: set[str], min_year: int, max_year: int, max_attempts: int,
) -> tuple[list, dict[str, int]]:
    """Filter the discovered list down to what this run may fetch."""
    skipped: dict[str, int] = {"terminal": 0, "attempts_exhausted": 0,
                               "host": 0, "year": 0}
    out = []
    for c in candidates:
        if not (min_year <= c.year <= max_year):
            skipped["year"] += 1
            continue
        if c.file_host not in host_allow:
            skipped["host"] += 1
            continue
        st = state.get(c.eli_uri)
        if st:
            status, attempts = st
            if status in TERMINAL_STATES:
                skipped["terminal"] += 1
                continue
            if attempts >= max_attempts:
                skipped["attempts_exhausted"] += 1
                continue
        out.append(c)
    # Newest year first (same convention as the typed path), volumes and
    # pages in print order within a year.
    out.sort(key=lambda c: (-c.year, c.volume or 0, c.page))
    return out, skipped


# ── Host-level run lock ───────────────────────────────────────────────

# One backfill process per host, whatever its language or unit name: the
# rate the runbook promises the Bundesarchiv is per host, not per run.
DEFAULT_LOCK_PATH = Path(__file__).resolve().parents[1] / "logs" / "bar_fetch.lock"


class BackfillLocked(RuntimeError):
    """Another backfill run holds the lock file; this one must not start."""


class _RunLock:
    """``flock(LOCK_EX | LOCK_NB)`` on ``path`` for the life of the object."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._fd: int | None = None

    def __enter__(self) -> "_RunLock":
        import fcntl
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            os.close(fd)
            raise BackfillLocked(f"{self.path} is held by another run ({e})") from e
        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()} {datetime.now(timezone.utc).isoformat()}\n".encode())
        self._fd = fd
        return self

    def __exit__(self, *exc) -> None:
        import fcntl
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            finally:
                os.close(self._fd)
                self._fd = None


def run_historical_backfill(
    conn: sqlite3.Connection, *, language: str, host_allow: set[str],
    min_year: int, max_year: int, max_per_run: int | None, max_attempts: int,
    min_interval: float, dry_run: bool = False, limit: int | None = None,
    discover=None, fetcher: PoliteFetcher | None = None,
    lock_path: Path | str | None = DEFAULT_LOCK_PATH,
) -> dict:
    """Discover, filter, fetch and store; returns a stats dict (also printed
    as JSON by the CLI). ``discover`` and ``fetcher`` are injectable for
    tests. A non-dry run holds the host lock (``lock_path``) from before the
    first request to the end; raises :class:`BackfillLocked` at once when
    another run holds it."""
    if dry_run or lock_path is None:
        return _run_historical_backfill(
            conn, language=language, host_allow=host_allow, min_year=min_year,
            max_year=max_year, max_per_run=max_per_run, max_attempts=max_attempts,
            min_interval=min_interval, dry_run=dry_run, limit=limit,
            discover=discover, fetcher=fetcher,
        )
    with _RunLock(lock_path):
        return _run_historical_backfill(
            conn, language=language, host_allow=host_allow, min_year=min_year,
            max_year=max_year, max_per_run=max_per_run, max_attempts=max_attempts,
            min_interval=min_interval, dry_run=dry_run, limit=limit,
            discover=discover, fetcher=fetcher,
        )


def _run_historical_backfill(
    conn: sqlite3.Connection, *, language: str, host_allow: set[str],
    min_year: int, max_year: int, max_per_run: int | None, max_attempts: int,
    min_interval: float, dry_run: bool, limit: int | None,
    discover, fetcher: PoliteFetcher | None,
) -> dict:
    if discover is None:
        from scrapers.fedlex_materialien import discover_fga_botschaften_untyped
        discover = discover_fga_botschaften_untyped
    t0 = time.time()
    log.info("Discovering untyped Botschaften by title via Fedlex SPARQL (%s)…", language)
    found = discover(language)
    state = load_state(conn, language)
    todo, skipped = select_candidates(
        found, state, host_allow=host_allow, min_year=min_year, max_year=max_year,
        max_attempts=max_attempts,
    )
    if limit:
        todo = todo[:limit]
    cap = max_per_run if max_per_run else len(todo)
    stats = {
        "language": language, "discovered": len(found), "skipped": skipped,
        "eligible": len(todo), "cap": cap, "host_allow": sorted(host_allow),
        "done": 0, "exists": 0, "collision": 0, "not_found": 0, "no_text": 0,
        "no_citation": 0, "error": 0, "fetched": 0, "stopped": None,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    log.info("historical backfill (%s): %d discovered, %d eligible, cap %d, skipped %s",
             language, len(found), len(todo), cap, skipped)
    if dry_run:
        for c in todo[:25]:
            log.info("  candidate: %s  %s  %s", c.citation or f"(no citation) {c.eli_uri}",
                     c.file_host, c.title[:80])
        if len(todo) > 25:
            log.info("  … and %d more", len(todo) - 25)
        stats["elapsed_seconds"] = round(time.time() - t0, 2)
        return stats

    if fetcher is None:
        fetcher = PoliteFetcher(min_interval=min_interval)
    processed = 0
    for i, cand in enumerate(todo, 1):
        if fetcher.requests_made >= cap:
            log.info("--max-per-run %d reached; stopping for this run", cap)
            stats["stopped"] = "max_per_run"
            break
        before = fetcher.requests_made
        try:
            status = ingest_historical_one(conn, cand, fetcher)
        except FetchStopped as e:
            stats["error"] += 1
            stats["stopped"] = str(e)
            log.error("hard stop: %s", e)
            break
        except Exception as e:  # noqa: BLE001
            # Last resort: ingest_historical_one records its own errors
            # inside the document's transaction; reaching here means a
            # state write itself failed (locked DB, disk). Roll back —
            # never commit — and stop: requests without persistence are
            # requests wasted.
            stats["error"] += 1
            try:
                conn.rollback()
            except sqlite3.Error:
                pass
            stats["stopped"] = f"persistence failure: {type(e).__name__}: {e}"
            log.error("  %s failed outside the document transaction: %s; stopping",
                      cand.citation or cand.eli_uri, e)
            break
        else:
            stats[status] = stats.get(status, 0) + 1
        stats["fetched"] += fetcher.requests_made - before
        processed += 1
        if processed % 25 == 0 or i == len(todo):
            elapsed = time.time() - t0
            rate = stats["fetched"] / elapsed if elapsed > 0 else 0
            remaining = min(cap, len(todo)) - fetcher.requests_made
            eta = remaining / rate if rate > 0 else 0
            log.info("  progress %d/%d (done=%d exists=%d collision=%d not_found=%d "
                     "no_text=%d no_citation=%d error=%d) %.2f req/s eta=%dmin",
                     i, len(todo), stats["done"], stats["exists"], stats["collision"],
                     stats["not_found"], stats["no_text"], stats["no_citation"],
                     stats["error"], rate, int(eta / 60))
    stats["requests_made"] = fetcher.requests_made
    stats["bytes_received"] = fetcher.bytes_received
    stats["elapsed_seconds"] = round(time.time() - t0, 2)
    return stats


def state_summary(conn: sqlite3.Connection) -> dict:
    out: dict = {}
    for lang, status, n in conn.execute(
        "SELECT language, status, COUNT(*) FROM botschaft_fetch_state GROUP BY 1, 2"
    ):
        out.setdefault(lang, {})[status] = n
    return out


__all__ = [
    "BackfillLocked", "DEFAULT_LOCK_PATH", "SOURCE_COLLECTION_BAR",
    "clean_historical_page", "clean_historical_pages", "ingest_historical_one",
    "record_state", "load_state", "run_historical_backfill", "select_candidates",
    "state_summary", "store_historical_document",
]
