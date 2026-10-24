"""
Federal Verwaltungspraxis scraper framework.
=============================================

Parallel pipeline to the existing court-decision scrapers (BaseScraper),
purpose-built for federal *administrative practice* documents:
Kreisschreiben, Rundschreiben, Weisungen, Vollzugshilfen, Handbücher.

These are NOT court decisions. Different schema, different consumers,
different MCP tool surface (search_practice / get_practice). Keeping
them in a separate pipeline avoids polluting the decision corpus and
lets us evolve each independently.

Architecture
------------

  scrapers/practice/{source}.py     subclass PracticeScraper
        ↓ run()
  output/practice/{source}.jsonl    one JSONL per source, append-mode
        ↓ search_stack/build_practice_db.py
  output/practice.db                FTS5 + sources table
        ↓ mcp_server.py
  search_practice() / get_practice() tools

JSONL schema (one document per line)
------------------------------------

  (scrapers/practice/un_uhri.py fills the same schema with UN human-rights
  recommendations to Switzerland — not federal practice, but the same shape
  of document: issuing body, number, date, text, topics.)

  {
    "doc_id":           str,    # source-key + slug, e.g. "estv_ks_28"
    "source":           str,    # "estv_ks" | "ssk_ks" | "sem_weisungen"
                                # | "bafu_vollzug" | "are_vollzug" | "epa_personalrecht"
    "issuing_authority": str,   # "ESTV" | "SSK" | "SEM" | "BAFU" | "ARE" | "EPA"
    "doc_type":         str,    # "kreisschreiben" | "weisung"
                                # | "rundschreiben" | "vollzugshilfe"
                                # | "handbuch" | "merkblatt"
    "doc_number":       str,    # source-specific identifier ("KS Nr. 28", "UV-2552")
    "title":            str,
    "date":             str,    # ISO YYYY-MM-DD or YYYY
    "language":         str,    # "de" | "fr" | "it" | "en"
    "url":              str,    # source page URL
    "pdf_url":          str,    # canonical PDF URL
    "body_text":        str,    # extracted full text (UTF-8)
    "topics":           list,   # source-specific tags (e.g. ["DBG", "Quellensteuer"])
    "scraped_at":       str,    # ISO datetime UTC
    "content_hash":     str,    # SHA-256 of body_text — dedup key
  }
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import re
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from urllib.parse import urljoin

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)


# ── PDF text extraction (lazy import; fitz is optional but recommended) ──
def extract_pdf_text(pdf_bytes: bytes) -> str:
    """Best-effort PDF→text. Tries fitz (PyMuPDF) first, falls back to
    pdfplumber. Returns "" on failure rather than raising — a missing
    body doesn't kill the whole scrape."""
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        return "\n\n".join(page.get_text() for page in doc).strip()
    except Exception as e:
        logger.debug("fitz PDF extract failed: %s", e)
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            return "\n\n".join((p.extract_text() or "") for p in pdf.pages).strip()
    except Exception as e:
        logger.warning("pdfplumber PDF extract also failed: %s", e)
    return ""


def slugify(s: str) -> str:
    """URL-safe slug for doc IDs."""
    s = re.sub(r"[^A-Za-z0-9]+", "_", s.strip().lower())
    return s.strip("_")[:80]


def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


# ── Localized date parsing (shared by BAG / BJ / SECO-ALV scrapers) ──
_MONTHS_ALL: dict[str, int] = {
    "januar": 1, "februar": 2, "märz": 3, "maerz": 3, "april": 4, "mai": 5,
    "juni": 6, "juli": 7, "august": 8, "september": 9, "oktober": 10,
    "november": 11, "dezember": 12,
    "janvier": 1, "février": 2, "fevrier": 2, "mars": 3, "avril": 4,
    "juin": 6, "juillet": 7, "août": 8, "aout": 8, "septembre": 9,
    "octobre": 10, "novembre": 11, "décembre": 12, "decembre": 12,
    "gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4, "maggio": 5,
    "giugno": 6, "luglio": 7, "agosto": 8, "settembre": 9, "ottobre": 10,
    "dicembre": 12,
}
_DATE_WORDS = re.compile(r"(\d{1,2})(?:\.|er|re|º)?\s+([A-Za-zÀ-ÿ]+)\s+(\d{4})")
_DATE_DMY = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b")
_DATE_YMD = re.compile(r"\b(\d{4})\.(\d{2})\.(\d{2})\b")


def first_date_iso(text: str) -> str:
    """Return the FIRST date in `text` as ISO YYYY-MM-DD, or "".

    Understands "17. Dezember 2021", "11 août 2005", "25 giugno 2024",
    "1.7.2026" and admin.ch's "2014.10.14". The first date in reading order
    wins on purpose: on admin.ch download lists the issuance date precedes
    the trailing file-modification date ("Weisung Nr. 7 vom 16. April 2020
    … PDF 200 kB 6. April 2020"), and the issuance date is the one a reader
    cites.
    """
    text = text or ""
    best: tuple[int, str] | None = None
    for m in _DATE_WORDS.finditer(text):
        month = _MONTHS_ALL.get(m.group(2).lower())
        if month:
            iso = f"{m.group(3)}-{month:02d}-{int(m.group(1)):02d}"
            if best is None or m.start() < best[0]:
                best = (m.start(), iso)
            break
    for rx, order in ((_DATE_DMY, "dmy"), (_DATE_YMD, "ymd")):
        m = rx.search(text)
        if m and (best is None or m.start() < best[0]):
            if order == "dmy":
                iso = f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
            else:
                iso = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
            best = (m.start(), iso)
    return best[1] if best else ""


_NO_TEXT_LAYER_SENTINEL = "\x00no-text-layer"


def build_session(retries: int = 3) -> requests.Session:
    """HTTP session with sensible retries for admin.ch endpoints."""
    s = requests.Session()
    retry = Retry(
        total=retries,
        backoff_factor=1.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("HEAD", "GET"),
    )
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.headers.update({
        "User-Agent": "OpenCaseLawBot/1.0 (+https://opencaselaw.ch; team@jonashertner.com)",
        "Accept-Language": "de-CH,de;q=0.9,fr;q=0.7,it;q=0.5,en;q=0.3",
    })
    return s


class PracticeScraper(ABC):
    """Abstract base for federal Verwaltungspraxis scrapers."""

    # ── Required class attributes (override in subclass) ──
    SOURCE_KEY: str = ""              # e.g. "estv_ks"
    ISSUING_AUTHORITY: str = ""       # e.g. "ESTV"
    DEFAULT_DOC_TYPE: str = ""        # e.g. "kreisschreiben"

    # Opt-in re-issue detection. Dedup is doc_id-only by default, which is
    # correct for sources that mint a NEW id per edition — but wrong for
    # sources that re-publish a revised edition at a STABLE id (SECO
    # overwrites 'Weisung AVIG ALE.pdf' in place; BSV bumps a version number
    # behind the same document id). There, a doc_id hit meant "skip forever"
    # and the corpus silently froze at the first edition ever fetched.
    #
    # Set REVISION_FIELD to a stub key whose value changes on re-issue
    # (e.g. "pdf_url", "date", "version"). When it differs from the stored
    # record, the document is re-fetched and re-appended;
    # build_practice_db.py upserts ON CONFLICT(doc_id), so the index keeps
    # one current row per document. Default None = previous behaviour,
    # byte-identical for every existing scraper.
    REVISION_FIELD: str | None = None

    # fetch_pdf_text caches every PDF forever under tempfile.gettempdir()
    # (no eviction). That is fine for a few hundred small circulars and
    # wrong for BSV (~2,600 documents × versions × languages, ~30 GB) or for
    # sources whose PDF URL is stable across re-issues (a cache hit would
    # re-index the OLD text after a re-issue). Subclasses opt out here.
    CACHE_PDFS: bool = True

    # Old circulars are often image-only scans (BAG KS 1.1/1.2/2.2/5.2,
    # cantonal SchKG Kreisschreiben from 2000). extract_pdf_text returns ""
    # and run() counts the document as failed — invisible even by title.
    # A subclass may set a short marker so such a document is indexed under
    # its own title plus the marker and the reader is told to open the PDF.
    # The stored body is "<title> <marker>": a shared multi-sentence
    # placeholder would make BM25 rank every scan first for any query that
    # happens to contain one of its words. None keeps the historical
    # behaviour (empty body = failure).
    NO_TEXT_LAYER_BODY: str | None = None

    # ── Tunables ──
    REQUEST_DELAY = 1.0               # seconds between PDF fetches
    PDF_TIMEOUT = 60
    OUTPUT_DIR = Path(__file__).parent.parent.parent / "output" / "practice"

    def __init__(self):
        assert self.SOURCE_KEY, "subclass must set SOURCE_KEY"
        assert self.ISSUING_AUTHORITY, "subclass must set ISSUING_AUTHORITY"
        self.session = build_session()
        self._last_request = 0.0
        self.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        self.output_path = self.OUTPUT_DIR / f"{self.SOURCE_KEY}.jsonl"
        # In-memory dedup of doc_ids already in the output file
        self._seen_ids: set[str] = self._load_seen_ids()

    # ────────────────────────────────────────────────────────────────
    # Subclass contract
    # ────────────────────────────────────────────────────────────────

    @abstractmethod
    def discover_documents(self) -> Iterator[dict]:
        """Yield document stubs from the source's listing page(s).

        Each stub MUST include: pdf_url, title, doc_number (best effort),
        date (best effort), language. Optional: url, topics, doc_type.
        The base class fills in source, doc_id, body_text, content_hash,
        scraped_at, etc.
        """
        ...

    # ────────────────────────────────────────────────────────────────
    # HTTP helpers
    # ────────────────────────────────────────────────────────────────

    def _rate_limit(self):
        elapsed = time.time() - self._last_request
        if elapsed < self.REQUEST_DELAY:
            time.sleep(self.REQUEST_DELAY - elapsed)
        self._last_request = time.time()

    def get(self, url: str, **kw) -> requests.Response:
        self._rate_limit()
        kw.setdefault("timeout", self.PDF_TIMEOUT)
        return self.session.get(url, **kw)

    def fetch_pdf_text(self, pdf_url: str) -> str:
        """Download a PDF + return extracted text. Caches in /tmp keyed by
        URL hash so re-runs don't re-download unchanged PDFs."""
        import tempfile
        cache_path: Path | None = None
        if self.CACHE_PDFS:
            cache_dir = Path(tempfile.gettempdir()) / "ocl_practice_cache"
            cache_dir.mkdir(exist_ok=True)
            url_hash = sha256_hex(pdf_url)[:16]
            cache_path = cache_dir / f"{url_hash}.pdf"
            if cache_path.exists() and cache_path.stat().st_size > 1024:
                try:
                    return self._text_or_placeholder(extract_pdf_text(cache_path.read_bytes()))
                except Exception:
                    pass
        try:
            r = self.get(pdf_url)
            r.raise_for_status()
            if not r.content.lstrip()[:5].startswith(b"%PDF"):
                # A 200 that is not a PDF (login bounce, error page, HTML
                # listing) must be a failure, never a "scanned document":
                # with NO_TEXT_LAYER_BODY set it would otherwise be indexed
                # under the document's title as if it were the file.
                logger.warning("PDF fetch returned non-PDF content (%s, %d bytes): %s",
                               r.headers.get("Content-Type", "?"), len(r.content), pdf_url)
                return ""
            if cache_path is not None:
                cache_path.write_bytes(r.content)
            return self._text_or_placeholder(extract_pdf_text(r.content))
        except requests.HTTPError as e:
            logger.warning("PDF fetch failed [%s]: %s", e.response.status_code, pdf_url)
            return ""
        except Exception as e:
            logger.warning("PDF fetch error: %s — %s", pdf_url, e)
            return ""

    def _text_or_placeholder(self, text: str) -> str:
        """A downloaded PDF with no text layer returns a sentinel (when the
        class opted in) instead of "", so run() can tell "scanned" from
        "download failed" and build the title-based placeholder body."""
        if text or not self.NO_TEXT_LAYER_BODY:
            return text
        return _NO_TEXT_LAYER_SENTINEL

    def _placeholder_body(self, stub: dict) -> str:
        return f"{(stub.get('title') or '').strip()} {self.NO_TEXT_LAYER_BODY}".strip()

    # ────────────────────────────────────────────────────────────────
    # State + output
    # ────────────────────────────────────────────────────────────────

    def _load_seen_ids(self) -> set[str]:
        """Populate the dedup set and, when REVISION_FIELD is set, the
        doc_id -> revision map used to detect re-issued editions."""
        self._seen_revisions: dict[str, str] = {}
        if not self.output_path.exists():
            return set()
        ids = set()
        try:
            with open(self.output_path) as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    did = rec.get("doc_id")
                    if not did:
                        continue
                    ids.add(did)
                    if self.REVISION_FIELD:
                        # Last line wins: the file is append-only, so the most
                        # recently appended record is the current edition.
                        self._seen_revisions[did] = str(
                            rec.get(self.REVISION_FIELD, "") or "")
        except Exception as e:
            logger.warning("Failed to read existing output: %s", e)
        return ids

    def _is_reissue(self, doc_id: str, stub: dict) -> bool:
        """True when we hold this doc_id but the source now offers a
        different edition of it."""
        if not self.REVISION_FIELD:
            return False
        stored = self._seen_revisions.get(doc_id)
        if stored is None:
            return False
        return str(stub.get(self.REVISION_FIELD, "") or "") != stored

    def _make_doc_id(self, stub: dict) -> str:
        if "doc_number" in stub and stub["doc_number"]:
            return f"{self.SOURCE_KEY}_{slugify(stub['doc_number'])}"
        return f"{self.SOURCE_KEY}_{slugify(stub['title'])[:60]}"

    def _normalize(self, stub: dict, body_text: str) -> dict:
        return {
            "doc_id": self._make_doc_id(stub),
            "source": self.SOURCE_KEY,
            "issuing_authority": self.ISSUING_AUTHORITY,
            "doc_type": stub.get("doc_type") or self.DEFAULT_DOC_TYPE,
            "doc_number": stub.get("doc_number", ""),
            "title": stub.get("title", "").strip(),
            "date": stub.get("date", ""),
            "language": stub.get("language", "de"),
            "url": stub.get("url", ""),
            "pdf_url": stub.get("pdf_url", ""),
            "body_text": body_text,
            "topics": stub.get("topics", []),
            "scraped_at": datetime.now(timezone.utc).isoformat(),
            "content_hash": sha256_hex(body_text) if body_text else "",
        }

    def _append(self, doc: dict):
        with open(self.output_path, "a") as f:
            f.write(json.dumps(doc, ensure_ascii=False) + "\n")
        self._seen_ids.add(doc["doc_id"])

    # ────────────────────────────────────────────────────────────────
    # Main loop
    # ────────────────────────────────────────────────────────────────

    def run(self, *, max_new: int | None = None, force_refresh: bool = False) -> dict:
        """Discover + fetch + write. Returns per-run summary stats."""
        t0 = time.time()
        new_count, skipped_count, failed_count = 0, 0, 0

        for stub in self.discover_documents():
            doc_id = self._make_doc_id(stub)

            reissued = self._is_reissue(doc_id, stub)
            if doc_id in self._seen_ids and not force_refresh and not reissued:
                skipped_count += 1
                continue
            if reissued:
                logger.info("[%s] re-issue detected for %s (%s changed)",
                            self.SOURCE_KEY, doc_id, self.REVISION_FIELD)

            # A source whose listing already carries the text (UHRI's JSON
            # export) puts it in the stub; every other source has a PDF.
            pdf_url = stub.get("pdf_url")
            if "body_text" not in stub and not pdf_url:
                logger.warning("[%s] stub without pdf_url: %s",
                               self.SOURCE_KEY, stub.get("title"))
                failed_count += 1
                continue

            body = stub["body_text"] if "body_text" in stub else self.fetch_pdf_text(pdf_url)
            if body == _NO_TEXT_LAYER_SENTINEL:
                body = self._placeholder_body(stub)
            if not body:
                logger.warning("[%s] empty body for %s", self.SOURCE_KEY, doc_id)
                failed_count += 1
                continue

            doc = self._normalize(stub, body)
            self._append(doc)
            if self.REVISION_FIELD:
                self._seen_revisions[doc_id] = str(
                    stub.get(self.REVISION_FIELD, "") or "")
            new_count += 1
            logger.info("[%s] +%s '%s'", self.SOURCE_KEY, doc["doc_number"] or "—",
                        doc["title"][:80])

            if max_new and new_count >= max_new:
                logger.info("[%s] hit max_new=%d, stopping early",
                            self.SOURCE_KEY, max_new)
                break

        elapsed = time.time() - t0
        summary = {
            "source": self.SOURCE_KEY,
            "new": new_count,
            "skipped": skipped_count,
            "failed": failed_count,
            "duration_s": round(elapsed, 1),
        }
        logger.info("[%s] done: %s", self.SOURCE_KEY, summary)
        return summary
