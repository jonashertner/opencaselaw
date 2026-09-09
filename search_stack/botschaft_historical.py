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
  collision / not_found / no_text / error, with attempt counts, so a run
  can stop at any point and the next one continues where it left off.

Text handling (R1-R3: the corpus is verbatim). The cleaner removes what is
not the document — the Bundesblatt masthead of an issue's first page, the
running page number, the ``#ST#`` scan markers — rejoins words the
typesetter hyphenated at line ends, and groups lines into paragraphs. It
never rewrites a word; OCR errors in the source ("Eeihe" for "Reihe")
stay as they are.

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
import re
import sqlite3
import statistics
import time
from datetime import datetime, timezone
from typing import Iterable

from search_stack.bar_fetcher import FetchStopped, PoliteFetcher

log = logging.getLogger("botschaft_historical")

SOURCE_COLLECTION_BAR = "bar-amtsdruckschriften"
SOURCE_COLLECTION_FEDLEX = "fedlex-filestore"

# Terminal states: the candidate is never fetched again by a later run.
TERMINAL_STATES = {"done", "exists", "collision", "no_text"}
# Retried on a later run, up to --max-attempts.
RETRY_STATES = {"error", "not_found"}


# ── OCR page cleaning ─────────────────────────────────────────────────

_ST_MARK = "#ST#"
_PAGE_NO_RE = re.compile(r"^\s*\d{1,5}\s*$")
# Lines of the Bundesblatt / Feuille fédérale / Foglio federale masthead that
# opens every issue (the first page of a document that starts an issue
# carries it above the title). Matched only in the head zone of a page.
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
# "Bundesblatt 127 Jahrg Bd I 23", "Feuille fédérale. 107e année. Vol. I.") and
# the print-job number ("1975-45", "4042").
_FOOTER_RE = re.compile(
    r"^\s*(?:Bundesblatt|Feuille\s+f[ée]d[ée]rale|Foglio\s+federale)\b.*"
    r"(?:Jahrg|ann[ée]e|anno|Bd\.?|Vol\.?)"
    r"|^\s*\d{4}-\d{1,3}\s*$|^\s*\d{1,5}\s*$",
    re.IGNORECASE,
)
_TAIL_ZONE_LINES = 4
# The Bundesarchiv appends one sheet with the catalogue record of the scan
# ("Schweizerisches Bundesarchiv, Digitale Amtsdruckschriften …"). It is not
# part of the document and is dropped as a whole page.
_ARCHIVE_SHEET_RE = re.compile(
    r"^\s*Schweizerisches\s+Bundesarchiv,\s+Digitale\s+Amtsdruckschriften", re.IGNORECASE,
)
_HEAD_ZONE_LINES = 12
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


def _strip_head_zone(lines: list[str]) -> list[str]:
    """Drop page numbers, masthead lines and scan markers from the top of a
    page. Stops at the first line that is none of those."""
    i = 0
    n = len(lines)
    while i < n and i < _HEAD_ZONE_LINES:
        ln = lines[i]
        if not ln.strip() or _ST_MARK in ln or _PAGE_NO_RE.match(ln) or _is_masthead(ln):
            i += 1
            continue
        break
    return lines[i:]


def _strip_tail(lines: list[str]) -> list[str]:
    """Drop the page number, the printer's signature and the print-job number
    from the foot of a page (at most a few lines)."""
    dropped = 0
    while lines and dropped < _TAIL_ZONE_LINES and (
        not lines[-1].strip() or _ST_MARK in lines[-1] or _FOOTER_RE.match(lines[-1])
    ):
        lines.pop()
        dropped += 1
    return lines


# A suspended hyphen ("Kriegs- und Nachkriegszeit") continues with a
# conjunction on the next line; the hyphen and the space stay.
_SUSPENDED_NEXT_RE = re.compile(r"^(?:und|oder|bzw\.|sowie|beziehungsweise|et|ou|e|o|ed|od)\b")


def _join_hyphen(prev: str, nxt: str) -> str:
    """Join two physical lines. A line-end hyphen followed by a lower-case
    continuation is a broken word ("Schweize-" / "rischen" → one word);
    followed by an upper-case line it is a broken compound ("Basel-" /
    "Stadt" → "Basel-Stadt"); a suspended hyphen before "und" / "et" keeps
    its space."""
    if prev.endswith("-") and not prev.endswith(" -"):
        if nxt[:1].islower() and not _SUSPENDED_NEXT_RE.match(nxt):
            return prev[:-1] + nxt
        if nxt[:1].isupper():
            return prev + nxt
    return prev + " " + nxt


def _page_paragraphs(page_text: str) -> list[str]:
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
        if _PAGE_NO_RE.match(s):
            continue
        short = len(s) < short_cut
        terminal = bool(_TERMINAL_RE.search(s))
        nxt = lines[idx + 1].strip() if idx + 1 < n else ""
        if title_block:
            cur = _join_hyphen(cur, s) if cur else s
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
        cur = _join_hyphen(cur, s) if cur else s
        if is_heading or standalone or salutation or (short and terminal):
            _close(cur)
            cur = ""
    if cur:
        _close(cur)
    elif article_head:
        _close(article_head)
    return [p.strip() for p in paragraphs if p.strip()]


def clean_historical_page(page_text: str) -> str:
    """One OCR page → paragraphs separated by blank lines (no stitching)."""
    return "\n\n".join(_page_paragraphs(page_text))


def clean_historical_pages(pages: Iterable[str]) -> list[str]:
    """Clean every page and stitch paragraphs that run over a page break.

    A paragraph that ends a page without terminal punctuation continues
    on the next page; its continuation (the next page's first paragraph)
    is pulled back so the paragraph stays whole and keeps the page number
    on which it starts. Returns one string per input page, blank-line
    separated paragraphs inside — the shape ``parse_botschaft_text`` takes.
    """
    per_page = [_page_paragraphs(p) for p in pages]
    for i in range(len(per_page) - 1):
        cur, nxt = per_page[i], per_page[i + 1]
        if not cur or not nxt:
            continue
        last = cur[-1]
        if (_TERMINAL_RE.search(last) or _ENUMERATOR_RE.match(nxt[0])
                or _ARTICLE_LINE_RE.match(nxt[0]) or _TITLE_KIND_RE.match(nxt[0].split("\n", 1)[0])
                or not nxt[0][:1].islower()):
            continue
        cur[-1] = _join_hyphen(last, nxt[0])
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
    from search_stack.build_botschaft_corpus import parse_botschaft_text

    cleaned = clean_historical_pages(pages)
    paragraphs = list(parse_botschaft_text(cleaned, language=cand.language))
    if not paragraphs:
        return None, 0, "no_text"
    text_hash = hashlib.sha256(pdf_bytes).hexdigest()
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        """
        INSERT INTO botschaft_documents
            (bbl_year, bbl_page, bbl_citation, eli_uri, title, publication_date,
             source_url, format, language, page_count, text_hash, ingested_at,
             source_collection, bbl_volume)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'pdf', ?, ?, ?, ?, ?, ?)
        """,
        (cand.year, cand.page, cand.citation, cand.eli_uri, cand.title,
         cand.publication_date, cand.file_url, cand.language, len(pages),
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
    return botschaft_id, len(paragraphs), "done"


def ingest_historical_one(
    conn: sqlite3.Connection, cand, fetcher: PoliteFetcher,
    *, source_collection: str = SOURCE_COLLECTION_BAR,
) -> str:
    """Fetch + store one candidate. Returns the state recorded. Raises
    FetchStopped when the fetcher hit its burst limit (state for this
    candidate is written first)."""
    lang = cand.language
    # Already there (an earlier run, or the typed 1999 overlap): no request.
    row = conn.execute(
        "SELECT botschaft_id FROM botschaft_documents WHERE eli_uri = ? AND language = ?",
        (cand.eli_uri, lang),
    ).fetchone()
    if row:
        record_state(conn, cand.eli_uri, lang, "exists", botschaft_id=row[0],
                     bump_attempts=False)
        conn.commit()
        return "exists"
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
    record_state(conn, cand.eli_uri, lang, status, http_status=res.status,
                 botschaft_id=bid, source_url=cand.file_url,
                 error=None if status == "done" else "no paragraphs after cleaning")
    conn.commit()
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


def run_historical_backfill(
    conn: sqlite3.Connection, *, language: str, host_allow: set[str],
    min_year: int, max_year: int, max_per_run: int | None, max_attempts: int,
    min_interval: float, dry_run: bool = False, limit: int | None = None,
    discover=None, fetcher: PoliteFetcher | None = None,
) -> dict:
    """Discover, filter, fetch and store; returns a stats dict (also printed
    as JSON by the CLI). ``discover`` and ``fetcher`` are injectable for
    tests."""
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
        "error": 0, "fetched": 0, "stopped": None,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    log.info("historical backfill (%s): %d discovered, %d eligible, cap %d, skipped %s",
             language, len(found), len(todo), cap, skipped)
    if dry_run:
        for c in todo[:25]:
            log.info("  candidate: %s  %s  %s", c.citation, c.file_host, c.title[:80])
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
            stats["error"] += 1
            record_state(conn, cand.eli_uri, language, "error",
                         error=f"{type(e).__name__}: {e}", source_url=cand.file_url)
            conn.commit()
            log.warning("  %s failed: %s", cand.citation, e)
            status = "error"
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
                     "no_text=%d error=%d) %.2f req/s eta=%dmin",
                     i, len(todo), stats["done"], stats["exists"], stats["collision"],
                     stats["not_found"], stats["no_text"], stats["error"], rate,
                     int(eta / 60))
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
    "SOURCE_COLLECTION_BAR", "clean_historical_page", "clean_historical_pages",
    "ingest_historical_one", "record_state", "load_state", "run_historical_backfill",
    "select_candidates", "state_summary", "store_historical_document",
]
