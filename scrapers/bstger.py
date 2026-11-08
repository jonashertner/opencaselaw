"""
Bundesstrafgericht (BStGer) — Federal Criminal Court Scraper
==============================================================

Scrapes decisions from bstger.weblaw.ch (Weblaw Lawsearch v4 JSON API).

Architecture:
- Uses Weblaw-hosted JSON API at bstger.weblaw.ch
- POST JSON to /api/getDocuments (returns structured data)
- Date-range windowing: start with 64-day ranges, halve if >100 results
- PDF/source available via /api/getDocumentContent/{leid} (the /cache web view and
  /api/getDocumentFile were retired in the 2026 Weblaw portal migration to a Next.js SPA)
- userID and sessionDuration randomized per request
- Results contain: docket number, ruling date, publication date, content summary

Coverage: 2005–present
Rate limiting: 3 seconds

Official collection (TPF, "Entscheide des Bundesstrafgerichts"):
- The same portal holds the TPF volumes as rulingType "Entscheide der Amtlichen
  Sammlung" (688 entries 2004–2025 on 2026-11-08). They carry no rulingDate, so the
  date-windowed search above never returns them; they are listed per volume year
  through the rulingType + year keyword filter instead.
- Each entry is a page extract of the printed volume: the PDF opens with the end of
  the previous entry and ends with the start of the next. The text is cut to the
  entry's own heading ("29. Auszug aus dem Beschluss der Beschwerdekammer ... vom
  11. Dezember 2024 (BG.2024.60)"), which also gives the decision date and the
  docket of the underlying ruling (docket_number_2). The portal's bstgerDossier
  field names the same docket where it is filled.
- Ids keep the form of the entscheidsuche rows these replace (bstger_TPF_2004_12),
  so the public URLs stay and the direct row wins the build's dedup.
- Daily runs list the last TPF_DAILY_YEARS volumes; --since lists every volume from
  that year on. OCL_BSTGER_TPF_ONLY=1 skips the date-windowed pass (TPF backfill).
"""

from __future__ import annotations

import io
import json
import logging
import os
import random
import re
import time
from datetime import date, datetime, timedelta, timezone
from typing import Iterator

from base_scraper import BaseScraper
from models import (
    Decision,
    detect_language,
    extract_citations,
    make_decision_id,
    parse_date,
)

logger = logging.getLogger(__name__)


# ============================================================
# Constants
# ============================================================

HOST = "https://bstger.weblaw.ch"
DOCUMENTS_URL = f"{HOST}/api/getDocuments?withAggregations=false"
PDF_URL = f"{HOST}/api/getDocumentFile/"
# Public, auth-free document endpoint. The /cache?id= web view was retired when the
# portal migrated to a Next.js SPA (now 404), and /api/getDocumentFile now 500s.
# getDocumentContent still serves the official PDF by leid with no userID, so it is
# used for BOTH source_url and pdf_url. (Same endpoint already used for full-text fetch.)
CONTENT_URL = f"{HOST}/api/getDocumentContent/"

# Starting date range (days) — halved adaptively if >100 results
INITIAL_WINDOW_DAYS = 64
START_DATE = "2004-01-01"
MAX_RESULTS_PER_WINDOW = 100

# Request headers
HEADERS = {
    "Content-Type": "application/json",
    "Accept": "*/*",
    "Origin": HOST,
    "Referer": f"{HOST}/?sort-field=relevance&sort-direction=relevance",
}


def _extract_pdf_text(data: bytes) -> str:
    """Extract text from PDF bytes using fitz (PyMuPDF) with pdfplumber fallback."""
    try:
        import fitz
        doc = fitz.open(stream=data, filetype="pdf")
        return "\n\n".join(p.get_text() for p in doc)
    except ImportError:
        pass
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            return "\n\n".join(p.extract_text() or "" for p in pdf.pages)
    except ImportError:
        pass
    return ""

# JSON search template
BASE_JSON = {
    "guiLanguage": "de",
    "aggs": {
        "fields": ["year", "language", "court", "rulingDate"],
        "size": "10",
    },
}


def _random_user_id() -> str:
    """Generate random userID for API sessions."""
    chars = "0123456789abcdefghijklmnopqrstuvwxyz"
    return "_" + "".join(random.choice(chars) for _ in range(8))


def _dedupe_multilingual(text: str) -> str:
    """'de;;fr;;it;;de' → the distinct parts joined by blank lines."""
    if not text or ";;" not in text:
        return text
    seen: set[str] = set()
    unique = []
    for p in (p.strip() for p in text.split(";;")):
        if p and p not in seen:
            seen.add(p)
            unique.append(p)
    return "\n\n".join(unique)


# ============================================================
# Official collection (TPF)
# ============================================================

TPF_RULING_TYPE = (
    "Entscheide der Amtlichen Sammlung;;Décisions du Recueil officiel;;"
    "Decisioni della raccolta ufficiale;;Entscheide der Amtlichen Sammlung"
)
TPF_FIRST_YEAR = 2004
# A volume appears in the following year (TPF 2024 was indexed in July 2025), so a
# daily run lists the current volume year and the two before it.
TPF_DAILY_YEARS = 3
TPF_ONLY_ENV = "OCL_BSTGER_TPF_ONLY"

_TPF_TITLE_RE = re.compile(r"^\s*TPF\s+(\d{4})\s+(\d{1,4})\s*$")
# An entry's reference line with its numbered heading right below (blank lines
# between at most): "TPF 2008 180 \n  \n44. Präsidialentscheid der Strafkammer".
_TPF_MARKED_HEADING_RE = re.compile(
    r"(?m)^[ \t]*TPF[ \t]+(\d{4})[ \t]+(\d{1,4})[ \t]*\n\s*?[ \t]*(\d{1,3})\.[ \t]*\n?[ \t]*(?=[A-ZÀ-Ý])"
)
_TPF_RUNNING_HEAD_RE = re.compile(r"\n[ \t]*TPF[ \t]+\d{4}[ \t]+\d{1,4}[ \t]*\n?\s*$")
# An entry heading: "29. Auszug aus dem Beschluss ...", "4. Estratto della Sentenza
# ...", "3. Extrait de l'arrêt ...", at the start of a line.
_TPF_HEADING_RE = re.compile(
    r"(?m)^[ \t]*(\d{1,3})\.[ \t]*\n?[ \t]*(?=(?:Auszug|Extrait|Estratto|Urteil|Arr[êe]t|Sentenza"
    r"|Beschluss|Entscheid|D[ée]cision|Decisione|Ordonnance|Ordinanza|Verf[üu]gung"
    r"|Decreto|Jugement|Zwischenentscheid)\b)"
)
_TPF_MONTHS = {
    m: i for i, names in enumerate([
        ("januar", "janvier", "gennaio"), ("februar", "février", "fevrier", "febbraio"),
        ("märz", "maerz", "mars", "marzo"), ("april", "avril", "aprile"),
        ("mai", "maggio"), ("juni", "juin", "giugno"),
        ("juli", "juillet", "juilet", "luglio"),  # "juilet": misprint in TPF 2012 97
        ("august", "août", "aout", "agosto"),
        ("september", "septembre", "settembre"), ("oktober", "octobre", "ottobre"),
        ("november", "novembre"), ("dezember", "décembre", "decembre", "dicembre"),
    ], start=1) for m in names
}
# "vom 11. Dezember 2024", "du 1er mars 2022", "del 27 maggio 2004", "dell'8 giugno
# 2004", and joined rulings "vom 2. und 14. Juni 2005" (the first date is kept, as
# the first docket is). The ordinal suffix must not eat a month's first letter
# ("25 octobre").
_TPF_DAY = r"(\d{1,2})\s*(?:\.|er\b|°|º)?"
_TPF_DATE_RE = re.compile(
    r"(?<![A-Za-zÀ-ÿ])(?:vom|du|del|dell['’]|dello|des)\s*" + _TPF_DAY
    + r"(?:\s*(?:und|et|e|,)\s*\d{1,2}\s*(?:\.|er\b|°|º)?)*\s*([A-Za-zÀ-ÿ]+)\s+(\d{4})"
)
# A docket parenthesis: digits plus a separator ("BB.2021.123", "BK_B 023/04").
# Two rulings with their own months, the year printed once: "vom 6. Juni und
# 23. Juli 2012" → the first day and month, the year that follows.
_TPF_SPLIT_DATE_RE = re.compile(
    r"(?<![A-Za-zÀ-ÿ])(?:vom|du|del|dell['’]|dello|des)\s*" + _TPF_DAY
    + r"\s*([A-Za-zÀ-ÿ]+)\s+(?:und|et|e|bzw\.)\s+[^()]{0,30}?(\d{4})"
)
_TPF_DOCKET_PAREN_RE = re.compile(r"\(([^()]*\d[./_][^()]*)\)")


def tpf_reference(title: str) -> str | None:
    """'TPF 2024  205' → 'TPF 2024 205'; None for anything else."""
    m = _TPF_TITLE_RE.match(title or "")
    return f"TPF {m.group(1)} {int(m.group(2))}" if m else None


def tpf_decision_id(reference: str) -> str:
    """'TPF 2004 12' → 'bstger_TPF_2004_12', the id the collection has always
    been served under (make_decision_id keeps the spaces)."""
    return make_decision_id("bstger", reference.replace(" ", "_"))


def _norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def _docket_key(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def tpf_dossiers(doc: dict) -> list[str]:
    """Dockets of the underlying ruling(s) the portal links to the entry."""
    out: list[str] = []
    for item in (doc.get("nestedMetadataMap") or {}).get("bstgerDossier") or []:
        term = _norm_ws(((item or {}).get("termMap") or {}).get("de") or "")
        if term and term not in out:
            out.append(term)
    return out


def _parse_tpf_heading(block: str) -> tuple[str | None, str | None]:
    """(ISO date, docket as printed) read from an entry heading. The heading
    ends with its docket parenthesis; a date after it is in the regeste."""
    # Rejoin words broken at a line end ("8. Feb-\nruar 2006", "Minis-\ntère").
    head = _norm_ws(re.sub(r"(?<=[a-zà-ÿ])-[ \t]*\n\s*(?=[a-zà-ÿ])", "", block or ""))[:600]
    p = _TPF_DOCKET_PAREN_RE.search(head)
    if p and p.start() < 450:
        # "(SK.2011.5; «Montecristo»)": the case name is not part of the docket.
        docket = _norm_ws(re.sub(r"\s*[;,]\s*«[^»]*»", "", p.group(1)))
        head = head[: p.end()]
    else:
        docket = None
        head = head[:450]
    iso = None
    for m in [*_TPF_DATE_RE.finditer(head), *_TPF_SPLIT_DATE_RE.finditer(head)]:
        month = _TPF_MONTHS.get(m.group(2).lower())
        if not month:
            continue
        try:
            iso = date(int(m.group(3)), month, int(m.group(1))).isoformat()
        except ValueError:
            continue
        break
    return iso, docket


def _tpf_headings(text: str) -> list[tuple[int, str | None]]:
    """Entry headings in an extract as (offset, reference printed above it).

    The volume prints each entry's reference on the line right above its
    numbered heading ("TPF 2008 180\n44. Präsidialentscheid ..."); a page's
    running head is followed by the page number instead. A numbered line under
    such a reference line is a heading whatever its first word; without one, a
    line starting with a ruling type ("4. Estratto della Sentenza") still counts.
    """
    found: dict[int, str | None] = {}
    for m in _TPF_MARKED_HEADING_RE.finditer(text):
        found[m.start(3)] = f"TPF {m.group(1)} {int(m.group(2))}"
    for h in _TPF_HEADING_RE.finditer(text):
        if h.start() not in found and not any(abs(h.start() - k) < 3 for k in found):
            found[h.start()] = None
    return sorted(found.items())


def tpf_entry(text: str, reference: str, dossiers: list[str] | None = None) -> dict:
    """Cut a TPF page extract to its own entry.

    Returns {"text", "decision_date", "docket", "heading_found"}. The entry's
    heading is the one under its own reference line, else the one whose docket
    the portal names (bstgerDossier), else the first unmarked heading. A heading
    under another entry's reference line is never taken: an undated entry is
    better than one dated and linked from its neighbour. The text runs to the
    next marked heading (the next entry, which the last page also prints). The
    decision date is kept only when it lies within three years before the volume
    year; without a heading the whole extract is kept, undated.
    """
    text = text or ""
    heads = _tpf_headings(text)
    parsed = [_parse_tpf_heading(text[pos:pos + 900]) for pos, _ in heads]
    mine = [i for i, (_, ref) in enumerate(heads) if ref == reference]
    own = next((i for i in mine if parsed[i][0] or parsed[i][1]), mine[0] if mine else None)
    if own is None and dossiers:
        want = {_docket_key(d) for d in dossiers}
        for i, (_, dk) in enumerate(parsed):
            if (heads[i][1] in (None, reference) and dk
                    and any(_docket_key(part) in want for part in re.split(r"[,;]| und | et | e ", dk))):
                own = i
                break
    if own is None:
        own = next((i for i, (iso, dk) in enumerate(parsed)
                    if heads[i][1] is None and (iso or dk)), None)
    if own is None:
        return {"text": text, "decision_date": None, "docket": None, "heading_found": False}
    start = heads[own][0]
    later = [pos for pos, ref in heads[own + 1:] if ref and ref != reference]
    if not later:
        later = [pos for pos, ref in heads[own + 1:] if ref is None]
    end = later[0] if later else len(text)
    section = _TPF_RUNNING_HEAD_RE.sub("\n", text[start:end]).strip()
    iso, docket = parsed[own]
    volume = int(reference.split()[1])
    if iso and not (volume - 3 <= int(iso[:4]) <= volume):
        logger.warning(f"[bstger] {reference}: heading date {iso} outside the volume; not used")
        iso = None
    return {"text": section, "decision_date": iso, "docket": docket, "heading_found": True}


class BStGerScraper(BaseScraper):
    """
    Scraper for Bundesstrafgericht decisions.

    Uses adaptive date windowing to stay under 100 results per request.
    """

    REQUEST_DELAY = 1.5
    TIMEOUT = 60

    @property
    def court_code(self) -> str:
        return "bstger"

    def _build_request_body(
        self,
        ab_date: date,
        bis_date: date,
        from_offset: int = 0,
    ) -> dict:
        """Build JSON request body for a date range."""
        body = dict(BASE_JSON)
        body["userID"] = _random_user_id()
        body["sessionDuration"] = str(int(time.time()))
        body["metadataDateMap"] = {
            "rulingDate": {
                "from": ab_date.strftime("%Y-%m-%dT00:00:00.000Z"),
                "to": bis_date.strftime("%Y-%m-%dT23:59:59.999Z"),
            }
        }
        if from_offset > 0:
            body["from"] = from_offset
        return body

    def _fetch_window(
        self, ab_date: date, window_days: int, from_offset: int = 0
    ) -> tuple[list[dict], int, bool, date]:
        """
        Fetch a single date-range window.

        Returns: (documents, total_count, has_more, bis_date)
        If total > MAX_RESULTS_PER_WINDOW, caller should halve window.
        """
        bis_date = ab_date + timedelta(days=window_days)
        body = self._build_request_body(ab_date, bis_date, from_offset)

        response = self.post(
            DOCUMENTS_URL,
            headers=HEADERS,
            json=body,
        )

        data = response.json()
        if data.get("status") != "success":
            logger.error(f"BStGer API error: {json.dumps(data)[:500]}")
            return [], 0, False, bis_date

        result = data["data"]
        total = result.get("totalNumberOfDocuments", 0)
        documents = result.get("documents", [])
        has_more = result.get("hasMoreResults", False)

        return documents, total, has_more, bis_date

    # Daily runs look back this many days instead of scanning from 2005
    DAILY_LOOKBACK_DAYS = 365

    def _discover_windowed(self, since_date=None) -> Iterator[dict]:
        """
        Discover BStGer decisions using adaptive date windowing.

        Strategy:
        - Start with 64-day windows
        - If >100 results, halve the window and retry
        - Move forward through time until today
        - Daily runs (no since_date) look back 365 days, not from 2005
        """
        start = date.fromisoformat(START_DATE)
        if since_date:
            if isinstance(since_date, str):
                since_date = parse_date(since_date) or start
            start = max(since_date, start)
        else:
            # Daily mode: only look back DAILY_LOOKBACK_DAYS
            daily_start = date.today() - timedelta(days=self.DAILY_LOOKBACK_DAYS)
            start = max(daily_start, start)
            logger.info(f"BStGer daily mode: looking back {self.DAILY_LOOKBACK_DAYS} days from {start}")

        window_days = INITIAL_WINDOW_DAYS
        current_date = start
        today = date.today()

        while current_date < today:
            documents, total, has_more, bis_date = self._fetch_window(
                current_date, window_days
            )

            if total > MAX_RESULTS_PER_WINDOW:
                # Too many results — halve window and retry
                window_days = max(1, window_days // 2)
                logger.info(
                    f"Window {current_date}–{bis_date} had {total} results. "
                    f"Reducing to {window_days} days."
                )
                continue

            logger.info(
                f"BStGer {current_date}–{bis_date}: {total} decisions, "
                f"{len(documents)} on this page"
            )

            user_id = _random_user_id()

            for doc in documents:
                stub = self._parse_document(doc, user_id)
                if stub:
                    decision_id = make_decision_id("bstger", stub["docket_number"])
                    if not self.state.is_known(decision_id):
                        yield stub

            # Handle pagination within window
            offset = len(documents)
            while has_more and offset < total:
                documents, _, has_more, _ = self._fetch_window(
                    current_date, window_days, offset
                )
                for doc in documents:
                    stub = self._parse_document(doc, user_id)
                    if stub:
                        decision_id = make_decision_id("bstger", stub["docket_number"])
                        if not self.state.is_known(decision_id):
                            yield stub
                offset += len(documents)

            # Move to next window
            current_date = bis_date + timedelta(days=1)

    def discover_new(self, since_date=None) -> Iterator[dict]:
        """Date-windowed decisions, then the official collection (TPF)."""
        if os.environ.get(TPF_ONLY_ENV, "") not in ("1", "true", "yes"):
            yield from self._discover_windowed(since_date)
        if since_date:
            if isinstance(since_date, str):
                since_date = parse_date(since_date)
            first_year = max(TPF_FIRST_YEAR, since_date.year if since_date else TPF_FIRST_YEAR)
        else:
            first_year = date.today().year - TPF_DAILY_YEARS + 1
        yield from self._discover_tpf(first_year, date.today().year)

    def _fetch_tpf_page(self, year: int, from_offset: int = 0) -> tuple[list[dict], int, bool]:
        body = dict(BASE_JSON)
        body["userID"] = _random_user_id()
        body["sessionDuration"] = str(int(time.time()))
        body["metadataKeywordsMap"] = {"rulingType": TPF_RULING_TYPE, "year": str(year)}
        if from_offset > 0:
            body["from"] = from_offset
        data = self.post(DOCUMENTS_URL, headers=HEADERS, json=body).json()
        if data.get("status") != "success" or "documents" not in (data.get("data") or {}):
            raise RuntimeError(f"BStGer TPF listing {year} failed: {json.dumps(data)[:300]}")
        result = data["data"]
        return (result.get("documents") or [], result.get("totalNumberOfDocuments", 0),
                bool(result.get("hasMoreResults")))

    def list_tpf_year(self, year: int) -> tuple[list[dict], int]:
        """Every entry of one TPF volume as stubs, plus the portal's own count."""
        stubs: list[dict] = []
        seen: set[str] = set()
        documents, total, has_more = self._fetch_tpf_page(year)
        offset = 0
        while True:
            for doc in documents:
                stub = self._parse_tpf_document(doc)
                if stub and stub["decision_id"] not in seen:
                    seen.add(stub["decision_id"])
                    stubs.append(stub)
            offset += len(documents)
            if not has_more or not documents or offset >= total:
                break
            documents, _, has_more = self._fetch_tpf_page(year, offset)
        if len(stubs) != total:
            logger.warning(f"BStGer TPF {year}: listed {len(stubs)} of {total}")
        return stubs, total

    def _discover_tpf(self, first_year: int, last_year: int) -> Iterator[dict]:
        for year in range(first_year, last_year + 1):
            stubs, total = self.list_tpf_year(year)
            logger.info(f"BStGer TPF {year}: {total} entries")
            if total == 0 and year <= date.today().year - 2:
                # A published volume never empties; the facet or its filter changed.
                logger.error(f"BStGer TPF {year}: the collection lists no entries")
            for stub in stubs:
                if not self.state.is_known(stub["decision_id"]):
                    yield stub

    def _parse_tpf_document(self, doc: dict) -> dict | None:
        meta_kw = doc.get("metadataKeywordTextMap") or {}
        reference = tpf_reference((meta_kw.get("title") or [""])[0])
        leid = doc.get("leid") or ""
        if not reference or not leid:
            logger.warning(f"BStGer TPF: unusable entry {meta_kw.get('title')} {leid}")
            return None
        return {
            "kind": "tpf",
            "decision_id": tpf_decision_id(reference),
            "docket_number": reference,
            "doc_id": leid,
            "language": (meta_kw.get("language") or [None])[0],
            "headnote": doc.get("content") or "",
            "dossiers": tpf_dossiers(doc),
            "pdf_url": f"{CONTENT_URL}{leid}",
        }

    def _fetch_tpf_decision(self, stub: dict) -> Decision | None:
        reference = stub["docket_number"]
        leid = stub["doc_id"]
        resp = self.get(f"{CONTENT_URL}{leid}", timeout=60)
        if resp.content[:5] != b"%PDF-":
            logger.warning(f"[bstger] {reference}: no PDF at {CONTENT_URL}{leid}")
            return None
        raw = _extract_pdf_text(resp.content)
        if not raw.strip():
            logger.warning(f"[bstger] {reference}: PDF has no text layer")
            return None
        entry = tpf_entry(raw, reference, stub.get("dossiers"))
        if not entry["heading_found"]:
            logger.warning(f"[bstger] {reference}: entry heading not found; whole extract kept")
        if not entry["decision_date"]:
            logger.warning(f"[bstger] {reference}: no decision date in the heading")
        # The underlying ruling: the docket the heading prints, else the portal's.
        dossiers = stub.get("dossiers") or []
        docket_2 = entry["docket"] or (dossiers[0] if dossiers else None)
        text = entry["text"]
        lang = stub.get("language")
        if lang not in ("de", "fr", "it"):
            lang = detect_language(text)
        headnote = _dedupe_multilingual(stub.get("headnote") or "")
        return Decision(
            decision_id=stub["decision_id"],
            court="bstger",
            canton="CH",
            docket_number=reference,
            docket_number_2=docket_2,
            decision_date=parse_date(entry["decision_date"] or ""),
            language=lang,
            regeste=headnote or None,
            full_text=self.clean_text(text),
            source_url=stub["pdf_url"],
            pdf_url=stub["pdf_url"],
            cited_decisions=extract_citations(text),
            scraped_at=datetime.now(timezone.utc),
        )

    def _parse_document(self, doc: dict, user_id: str) -> dict | None:
        """Parse a single document from the API response."""
        try:
            meta_kw = doc.get("metadataKeywordTextMap", {})
            meta_date = doc.get("metadataDateMap", {})

            title_list = meta_kw.get("title", [])
            if not title_list:
                return None

            num_raw = title_list[0]
            nums = num_raw.split(", ")
            docket = nums[0]

            leid = doc.get("leid", "")
            content = doc.get("content", "")

            # Parse dates
            ruling_date = None
            if "rulingDate" in meta_date:
                ruling_date = meta_date["rulingDate"][:10]

            pub_date = None
            if "publicationDate" in meta_date:
                pub_date = meta_date["publicationDate"][:10]

            # Decision type
            decision_type = None
            if "tipoSentenza" in meta_kw:
                decision_type = meta_kw["tipoSentenza"][0]

            # PDF URL
            file_name = meta_kw.get("fileName", [""])[0]
            pdf_url = f"{CONTENT_URL}{leid}"

            return {
                "docket_number": docket,
                "docket_numbers": nums,
                "decision_date": ruling_date,
                "publication_date": pub_date,
                "headnote": content,
                "decision_type": decision_type,
                "doc_id": leid,
                "pdf_url": pdf_url,
                "file_name": file_name,
            }
        except Exception as e:
            logger.error(f"Failed to parse BStGer document: {e}")
            return None

    def fetch_decision(self, stub: dict) -> Decision | None:
        """Build Decision from BStGer API data, fetching full text when available."""
        if stub.get("kind") == "tpf":
            try:
                return self._fetch_tpf_decision(stub)
            except Exception as e:
                logger.error(f"Failed to process BStGer {stub.get('docket_number', '?')}: {e}", exc_info=True)
                return None
        try:
            docket = stub["docket_number"]
            decision_date_parsed = parse_date(stub.get("decision_date", ""))
            if not decision_date_parsed:
                logger.warning(f"[bstger] No date for {docket}")

            pub_date_parsed = parse_date(stub.get("publication_date", ""))
            headnote = stub.get("headnote", "")

            # Fetch full text by downloading PDF and extracting text
            full_text = ""
            leid = stub.get("doc_id", "")
            if leid:
                try:
                    resp = self.get(
                        f"{HOST}/api/getDocumentContent/{leid}",
                        timeout=30,
                    )
                    if resp.status_code == 200 and resp.content[:5] == b"%PDF-":
                        full_text = _extract_pdf_text(resp.content)
                        if full_text.strip():
                            logger.debug(f"Extracted {len(full_text)} chars from PDF for {docket}")
                        else:
                            logger.debug(f"PDF text extraction empty for {docket}")
                    elif resp.status_code == 200 and len(resp.text) > 100:
                        # Non-PDF text response (future-proofing)
                        full_text = resp.text
                except Exception as e:
                    logger.debug(f"Content fetch failed for {leid}: {e}")

            if not full_text.strip():
                full_text = headnote or ""

            # Deduplicate multilingual regeste (e.g. "de text;;fr text;;it text")
            if headnote and ";;" in headnote:
                parts = [p.strip() for p in headnote.split(";;") if p.strip()]
                # Take unique parts only
                seen = set()
                unique = []
                for p in parts:
                    if p not in seen:
                        seen.add(p)
                        unique.append(p)
                headnote = "\n\n".join(unique)

            lang = detect_language(full_text) if full_text else "de"

            decision = Decision(
                decision_id=make_decision_id("bstger", docket),
                court="bstger",
                canton="CH",
                docket_number=docket,
                decision_date=decision_date_parsed,
                publication_date=pub_date_parsed,
                language=lang,
                regeste=headnote or None,
                full_text=self.clean_text(full_text) if full_text.strip() else "(metadata only — PDF available)",
                decision_type=stub.get("decision_type"),
                appeal_info=stub.get("decision_type"),
                source_url=f"{CONTENT_URL}{leid}" if leid else f"{HOST}/",
                pdf_url=stub.get("pdf_url"),
                cited_decisions=extract_citations(full_text) if full_text else [],
                scraped_at=datetime.now(timezone.utc),
            )
            return decision

        except Exception as e:
            logger.error(f"Failed to process BStGer {stub.get('docket_number', '?')}: {e}", exc_info=True)
            return None


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Scrape BStGer decisions")
    parser.add_argument("--since", type=str, help="Start date YYYY-MM-DD")
    parser.add_argument("--max", type=int, default=20, help="Max decisions")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    since = date.fromisoformat(args.since) if args.since else None
    scraper = BStGerScraper()
    decisions = scraper.run(since_date=since, max_decisions=args.max)
    scraper.mark_run_complete(decisions)
    print(f"Scraped {len(decisions)} BStGer decisions")