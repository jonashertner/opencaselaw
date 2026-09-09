"""
Zug GVP Scraper (Gerichts- und Verwaltungspraxis des Kantons Zug)
==================================================================
Scrapes the published leading decisions of the Zug courts and authorities
from the DecWork platform behind bgs.zg.ch — the same LexWork/DecWork
(Sitrox AG) backend that serves canton AG (scrapers/cantonal/ag_gerichte.py).

Architecture (identical to AG, parametrised for ZG):
- Single POST to /api/main/v1/de/decrees_chronology returns ALL decisions
  (1,357 on 2026-09-08, 1995–2026; 1,264 pre-2019)
- Each decision has a PDF at /api/main/v1/de/decrees_pdf/{decree_id}
- Detail endpoint /api/main/v1/de/decrees/{decree_id} adds publication_date,
  in_force and retracted (one extra call per new decision — 1,357 total, so
  unlike AG we do fetch it)
- Text must be extracted from PDFs

Data source: https://bgs.zg.ch (frontend, "Entscheide")
API backend: https://decwork.zg.ch

What the PDFs are:
- Pre-2017 PDFs are page ranges of the PRINTED GVP volume (e.g. GVP 1996
  S. 125–127). They carry a text layer, but the first and last page can
  contain the tail / head of the neighbouring decision, and the printed page
  number sits at the page end. We store the text verbatim (no trimming).
- 2017+ PDFs are born-digital extracts; pdfplumber glues words together on
  some of them (font kerning), so extraction prefers pymupdf when the
  pdfplumber result looks glued.

Chronology entry fields (same shape as AG):
- id: chronology entry ID (15 entries differ from decree_id — never use it)
- decree_id: the decree ID → PDF and detail endpoints
- number: docket as published. Pre-1999 entries carry an "N/A " prefix
  (DecWork's placeholder for a missing Geschäftsnummer, followed by the GVP
  sequence: "N/A RR 1995 016"); Datenschutzstelle entries are bare sequence
  numbers ("64"). A few numbers have stray leading whitespace.
- decree_date, institution_name, guiding_decree, guidance_summary, year, month
- chronology is nested {year: {German month name: [entries]}} — "Dezember",
  not "12"; discovery orders entries by decree_date, never by the key
- institution_name is COMMA-separated here ("Obergericht, Justizkommission"),
  not slash-separated as in AG, and sometimes repeats the umbrella
  ("Obergericht, Obergericht, Justizkommission").

Court codes: the 2019+ ZG scrapers (zg_obergericht.py, zg_gerichte.py) mint
ids as make_decision_id("zg_obergericht", "<docket>") with dockets shaped
"Z2 2020 47" / "V 2024 109" — the same shape the GVP uses since 1999. We reuse
those two codes so a decision published both on the Tribuna portal and in the
GVP gets the SAME decision_id and canonical_key, which build_fts5 collapses
(INSERT OR IGNORE + same canonical_key ⇒ skip / text upgrade). No new
_COURT_OVERLAP_GROUPS entry is needed for that.

Regeste: guidance_summary is the court-authored headnote as published in the
GVP; stored verbatim (R1–R3: never synthesised).

Collection: the API does not expose the GVP volume/page (the frontend label
is an i18n key, DECREE_COLLECTION_ABBREVIATION); `collection` stays None.
"""

from __future__ import annotations

import logging
import re
from typing import Iterator

from base_scraper import BaseScraper
from models import Decision, detect_language, extract_citations, make_decision_id, parse_date
from scrapers.cantonal.ag_gerichte import extract_text_from_pdf as _extract_text_pdfplumber_first

logger = logging.getLogger(__name__)

# ============================================================
# Constants
# ============================================================

HOST = "https://decwork.zg.ch"
ORIGIN = "https://bgs.zg.ch"
CHRONOLOGY_URL = f"{HOST}/api/main/v1/de/decrees_chronology"
DETAIL_URL = f"{HOST}/api/main/v1/de/decrees"  # /{decree_id}
PDF_URL = f"{HOST}/api/main/v1/de/decrees_pdf"  # /{decree_id}

HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "Origin": ORIGIN,
}

# Source URL template (links to the frontend, not the API)
SOURCE_URL_TEMPLATE = f"{ORIGIN}/app/de/decrees/{{decree_id}}"

CANTON = "ZG"
EXTERNAL_ID_PREFIX = "decwork_zg_"
FALLBACK_COURT = "zg_gvp"

# ============================================================
# Institution name → court code mapping
# ============================================================

# ZG institution names seen in the chronology (2026-09-08, count):
#   Verwaltungsgericht 402 · Regierungsrat 303 · Obergericht, Justizkommission 178
#   Kantonsgericht 134 · Datenschutzstelle 101 · Obergericht, Zivilabteilung 76
#   Obergericht, Beschwerdeabteilung 47 · Obergericht, Obergericht, Justizkommission 44
#   Aufsichtskommission über die Rechtsanwältinnen und Rechtsanwälte 26
#   Strafgericht 20 · Obergericht, Strafabteilung 17 · Landammann 3
#   Strafgericht, Berufungskammer 3 · Obergericht 3
#
# The FIRST part is the institution; the rest is the chamber. Keys are
# matched as substrings of the lower-cased first part, longest key first
# (so "spezialverwaltungsgericht", should it ever appear, would not be
# swallowed by "verwaltungsgericht").
_COURT_MAP: dict[str, str] = {
    # existing codes (Tribuna scrapers since 2019) — rows merge by docket
    "verwaltungsgericht": "zg_verwaltungsgericht",
    "obergericht": "zg_obergericht",
    # new codes
    "kantonsgericht": "zg_kantonsgericht",
    "strafgericht": "zg_strafgericht",
    "regierungsrat": "zg_regierungsrat",
    "landammann": "zg_regierungsrat",  # chamber "Landammann" (see parse_institution)
    "datenschutzstelle": "zg_datenschutzstelle",
    "aufsichtskommission": "zg_anwaltsaufsicht",
}

_COURT_KEYS_LONGEST_FIRST = sorted(_COURT_MAP, key=len, reverse=True)

# "N/A " placeholder in front of pre-1999 dockets ("N/A RR 1995 016").
_NA_PREFIX_RE = re.compile(r"^\s*N/A\s+", re.IGNORECASE)


def clean_docket(number: str | None) -> str:
    """Normalise the published number to the docket we store.

    Strips the DecWork "N/A " placeholder and surrounding whitespace, and
    collapses internal runs of whitespace. Nothing else is touched — the
    docket must stay the string the court published so that it matches the
    Tribuna-scraped rows ("Z2 2020 47") and cite() can render it.
    """
    if not number:
        return ""
    docket = _NA_PREFIX_RE.sub("", number)
    return re.sub(r"\s+", " ", docket).strip()


def parse_institution(institution_name: str | None) -> tuple[str, str | None]:
    """Parse the comma-separated institution_name into (court_code, chamber).

    "Obergericht, Justizkommission"               → ("zg_obergericht", "Justizkommission")
    "Obergericht, Obergericht, Justizkommission"  → ("zg_obergericht", "Justizkommission")
    "Strafgericht, Berufungskammer"               → ("zg_strafgericht", "Berufungskammer")
    "Landammann"                                  → ("zg_regierungsrat", "Landammann")
    "Verwaltungsgericht"                          → ("zg_verwaltungsgericht", None)
    """
    if not institution_name:
        return FALLBACK_COURT, None

    raw_parts = [p.strip() for p in re.split(r"[,/]", institution_name)]
    parts: list[str] = []
    for part in raw_parts:
        if part and part not in parts:  # drop repeats ("Obergericht, Obergericht, …")
            parts.append(part)
    if not parts:
        return FALLBACK_COURT, None

    head = parts[0].lower()
    court_code = FALLBACK_COURT
    for key in _COURT_KEYS_LONGEST_FIRST:
        if key in head:
            court_code = _COURT_MAP[key]
            break

    chamber_parts = parts[1:]
    if "landammann" in head:
        # The Landammann decides as the head of the Regierungsrat; keep the
        # organ visible as the chamber rather than minting a fourth-tier code.
        chamber_parts = [parts[0]] + chamber_parts
    chamber = " / ".join(chamber_parts).strip() or None
    return court_code, chamber


# ============================================================
# PDF text extraction
# ============================================================

_GLUED_WORD_LEN = 40
_GLUED_RATIO = 0.01  # >1% of tokens longer than 40 chars ⇒ spaces were lost


def looks_glued(text: str) -> bool:
    """True when the extractor dropped inter-word spaces.

    German compounds are long but rarely exceed 40 characters; a text where
    more than 1% of the tokens do has lost its spaces (seen with pdfplumber
    on the 2024 GVP PDFs: 119 of 1,588 tokens over 40 chars, pymupdf 0).
    """
    words = text.split()
    if len(words) < 20:
        return False
    long_words = sum(1 for w in words if len(w) > _GLUED_WORD_LEN)
    return long_words / len(words) > _GLUED_RATIO


def _extract_with_pymupdf(pdf_bytes: bytes) -> str:
    try:
        import fitz
    except ImportError:
        return ""
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        pages = [page.get_text() for page in doc]
        doc.close()
        return "\n\n".join(p for p in pages if p.strip())
    except Exception as e:  # pragma: no cover - defensive
        logger.debug(f"pymupdf failed: {e}")
        return ""


def extract_pdf_text(pdf_bytes: bytes) -> str:
    """AG's extractor (pdfplumber → pymupdf → pdfminer), with a glue check.

    If the first result has lost its spaces, prefer pymupdf's rendering of
    the same PDF when that one is intact.
    """
    text = _extract_text_pdfplumber_first(pdf_bytes)
    if text and looks_glued(text):
        alt = _extract_with_pymupdf(pdf_bytes)
        if len(alt) >= 50 and not looks_glued(alt):
            logger.debug("pdfplumber output glued; using pymupdf text")
            return alt
    return text


# ============================================================
# Scraper
# ============================================================


class ZGGVPScraper(BaseScraper):
    """
    Zug GVP (Gerichts- und Verwaltungspraxis) via the DecWork API.

    Strategy (as AG):
    1. POST chronology → all decree stubs in one call
    2. For each unknown stub: detail JSON (publication_date, retracted),
       PDF download, text extraction, Decision
    3. Polite rate limit (1.5 s between requests)

    Rows land in output/decisions/zg_gvp.jsonl under the SPECIFIC court code
    (zg_verwaltungsgericht, zg_obergericht, zg_kantonsgericht, …); the
    scraper key only names the source and its state file.
    """

    REQUEST_DELAY = 1.5
    TIMEOUT = 60
    MAX_ERRORS = 50

    @property
    def court_code(self) -> str:
        return "zg_gvp"

    # ---- discovery ------------------------------------------------------

    def _fetch_chronology(self) -> dict:
        logger.info("Fetching ZG GVP chronology index...")
        response = self.post(CHRONOLOGY_URL, headers=HEADERS, json={})
        data = response.json()
        count = data.get("count", "?")
        if isinstance(count, int):
            self.portal_count = count
        logger.info(f"ZG GVP chronology: {count} total decisions")
        return data

    @staticmethod
    def stub_from_entry(entry: dict) -> dict:
        """Build the discovery stub for one chronology entry (pure)."""
        decree_id = str(entry["decree_id"])
        docket = clean_docket(entry.get("number"))
        institution = entry.get("institution_name") or ""
        specific_court, _ = parse_institution(institution)
        # Deterministic id from court + docket — the same id the Tribuna
        # scrapers mint, so overlapping rows collapse in the build.
        decision_id = (
            make_decision_id(specific_court, docket)
            if docket
            else f"{specific_court}_{EXTERNAL_ID_PREFIX}{decree_id}"
        )
        summary = entry.get("guidance_summary") or ""
        return {
            "decree_id": decree_id,
            "docket_number": docket,
            "decree_date": entry.get("decree_date") or "",
            "institution_name": institution,
            "guiding_decree": bool(entry.get("guiding_decree", False)),
            "guidance_summary": summary.strip(),
            "decision_id": decision_id,
        }

    def discover_new(self, since_date=None) -> Iterator[dict]:
        """Yield stubs for decisions not yet in state, newest first."""
        data = self._fetch_chronology()
        chronology = data.get("chronology", {}) or {}

        if since_date and isinstance(since_date, str):
            since_date = parse_date(since_date)

        total = 0
        yielded = 0
        for year in sorted(chronology.keys(), key=int, reverse=True):
            if since_date and int(year) < since_date.year:
                continue
            # Month buckets are keyed by German month NAME ("Dezember"), not
            # by number; every entry carries its own decree_date, so order a
            # year's entries by that instead of trusting the key.
            months = chronology[year] or {}
            entries = [e for bucket in months.values() for e in (bucket or [])]
            entries.sort(key=lambda e: e.get("decree_date") or "", reverse=True)
            total += len(entries)
            for entry in entries:
                if since_date and entry.get("decree_date"):
                    entry_date = parse_date(entry["decree_date"])
                    if entry_date and entry_date < since_date:
                        continue
                stub = self.stub_from_entry(entry)
                if self.state.is_known(stub["decision_id"]):
                    continue
                yielded += 1
                yield stub

        logger.info(f"ZG GVP discovery: {total} total entries, {yielded} new to scrape")

    # ---- fetch ----------------------------------------------------------

    def _fetch_detail(self, decree_id: str) -> dict:
        """Detail JSON for one decree ({} on any failure — never blocks the PDF)."""
        try:
            response = self.get(
                f"{DETAIL_URL}/{decree_id}",
                headers={"Origin": ORIGIN, "Accept": "application/json"},
                timeout=self.TIMEOUT,
            )
            data = response.json()
        except Exception as e:
            logger.debug(f"ZG GVP detail fetch failed for id={decree_id}: {e}")
            return {}
        detail = data.get("decree") if isinstance(data, dict) else None
        return detail if isinstance(detail, dict) else {}

    def fetch_decision(self, stub: dict) -> Decision | None:
        decree_id = stub["decree_id"]
        docket = stub["docket_number"]

        # === 1. Detail metadata (best effort) ===
        detail = self._fetch_detail(decree_id)
        if detail.get("retracted"):
            logger.warning(f"ZG GVP {docket} (id={decree_id}) is retracted upstream — skipped")
            return None

        # === 2. Download PDF ===
        pdf_endpoint = f"{PDF_URL}/{decree_id}"
        try:
            response = self.get(
                pdf_endpoint,
                headers={"Origin": ORIGIN},
                timeout=self.TIMEOUT,
            )
        except Exception as e:
            logger.warning(f"ZG GVP PDF download failed for {docket} (id={decree_id}): {e}")
            return None

        content_type = response.headers.get("Content-Type", "")
        if "pdf" not in content_type and len(response.content) < 100:
            logger.warning(
                f"ZG GVP unexpected response for {docket}: "
                f"Content-Type={content_type}, size={len(response.content)}"
            )
            return None

        # === 3. Extract text ===
        full_text = extract_pdf_text(response.content)
        if not full_text or len(full_text) < 50:
            logger.warning(
                f"ZG GVP PDF text extraction too short for {docket}: "
                f"{len(full_text or '')} chars"
            )
            if not full_text:
                full_text = f"[PDF text extraction failed for {docket}]"

        # === 4. Metadata ===
        institution = stub.get("institution_name", "")
        specific_court, chamber = parse_institution(institution)
        decree_date = parse_date(stub["decree_date"])
        if not decree_date:
            logger.warning(f"ZG GVP unparseable date for {docket}: {stub['decree_date']}")
            return None
        publication_date = parse_date(detail.get("publication_date") or "") or None

        language = detect_language(full_text) if len(full_text) > 100 else "de"

        # Court-authored headnote, verbatim (chronology; detail as fallback)
        regeste = stub.get("guidance_summary") or (detail.get("guidance_summary") or "").strip() or None

        title = f"{institution} — {docket}" if institution else docket

        return Decision(
            decision_id=stub["decision_id"],
            court=specific_court,
            canton=CANTON,
            chamber=chamber,
            docket_number=docket or f"{EXTERNAL_ID_PREFIX}{decree_id}",
            decision_date=decree_date,
            publication_date=publication_date,
            language=language,
            title=title,
            regeste=regeste,
            full_text=full_text,
            source_url=SOURCE_URL_TEMPLATE.format(decree_id=decree_id),
            pdf_url=pdf_endpoint,
            decision_type="Leitentscheid" if stub.get("guiding_decree") else None,
            cited_decisions=extract_citations(full_text) if len(full_text) > 200 else [],
            external_id=f"{EXTERNAL_ID_PREFIX}{decree_id}",
        )
