"""
Zürich Courts Scraper (ZH Gerichte) — gerichte-zh.ch
=====================================================
Scrapes court decisions from the TYPO3-based decision database at
www.gerichte-zh.ch.

Architecture:
- GET to livesearch.php with date range params → HTML with decision entries
- Date chunking: 500-day windows from 2011-07-01 to present
  (decisions published since July 1, 2011 per Obergericht policy)
- Response HTML: paired <div class="entscheid entscheid_nummer_{id}">
  and <div class="entscheidDetails container_{id}">
- PDF URLs: relative paths under /fileadmin/user_upload/entscheide/
- Text extracted from PDFs via pdfplumber

API endpoint (reverse-engineered from TYPO3 extension):
  GET https://www.gerichte-zh.ch/typo3conf/ext/frp_entscheidsammlung_extended/
      res/php/livesearch.php
  Params: q, geschaeftsnummer, gericht, kammer,
          entscheiddatum_von, entscheiddatum_bis, erweitert=1

Courts covered:
  - Obergericht des Kantons Zürich (I./II. Zivilkammer, I./II. Strafkammer)
  - Handelsgericht des Kantons Zürich
  - Kassationsgericht des Kantons Zürich
  - 11+ Bezirksgerichte (Zürich, Winterthur, Uster, Pfäffikon, Meilen,
    Horgen, Hinwil, Dietikon, Dielsdorf, Bülach, Andelfingen, Affoltern)
  - Mietgericht Zürich, Arbeitsgericht Zürich
  - Other specialized chambers

Estimated volume: 20,000–30,000+ decisions.

Reference: NeueScraper ZH_Obergericht.py (Scrapy-based, TAGSCHRITTE=500)
"""

from __future__ import annotations

import io
import logging
import re
from datetime import date, timedelta
from typing import Iterator

from bs4 import BeautifulSoup

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

HOST = "https://www.gerichte-zh.ch"
LIVESEARCH_URL = (
    HOST
    + "/typo3conf/ext/frp_entscheidsammlung_extended"
    "/res/php/livesearch.php"
)

# Date chunking parameters (from NeueScraper)
TAG_SCHRITTE = 500  # days per window
START_DATE = date(2011, 7, 1)  # publication policy start date
# Also scrape older decisions that may exist
EARLY_START = date(1980, 1, 1)

# Default GET params that stay constant
FIXED_PARAMS = {
    "q": "",
    "geschaeftsnummer": "",
    "gericht": "gerichtTitel",
    "kammer": "kammerTitel",
    "erweitert": "1",
    "usergroup": "0",
    "sysOrdnerPid": "0",
    "sucheErlass": "Erlass",
    "sucheArt": "Art.",
    "sucheAbs": "Abs.",
    "sucheZiff": "Ziff./lit.",
    "sucheErlass2": "Erlass",
    "sucheArt2": "Art.",
    "sucheAbs2": "Abs.",
    "sucheZiff2": "Ziff./lit.",
    "sucheErlass3": "Erlass",
    "sucheArt3": "Art.",
    "sucheAbs3": "Abs.",
    "sucheZiff3": "Ziff./lit.",
    "suchfilter": "1",
}

# Source URL template (for the print view)
PRINT_URL_TEMPLATE = (
    HOST + "/entscheide/entscheide-drucken.html"
    "?tx_frpentscheidsammlungextended_pi3[entscheidDrucken]={doc_id}"
)


# ============================================================
# Court mapping
# ============================================================

# Maps keywords in Gericht/Behörde + Abteilung/Kammer to standardized codes
_COURT_MAP = {
    # --- Obergericht divisions ---
    "obergericht": "zh_obergericht",
    "handelsgericht": "zh_handelsgericht",
    "kassationsgericht": "zh_kassationsgericht",
    # --- Bezirksgerichte ---
    "bezirksgericht zürich": "zh_bezirksgericht_zuerich",
    "bezirksgericht winterthur": "zh_bezirksgericht_winterthur",
    "bezirksgericht uster": "zh_bezirksgericht_uster",
    "bezirksgericht pfäffikon": "zh_bezirksgericht_pfaeffikon",
    "bezirksgericht meilen": "zh_bezirksgericht_meilen",
    "bezirksgericht horgen": "zh_bezirksgericht_horgen",
    "bezirksgericht hinwil": "zh_bezirksgericht_hinwil",
    "bezirksgericht dietikon": "zh_bezirksgericht_dietikon",
    "bezirksgericht dielsdorf": "zh_bezirksgericht_dielsdorf",
    "bezirksgericht bülach": "zh_bezirksgericht_buelach",
    "bezirksgericht andelfingen": "zh_bezirksgericht_andelfingen",
    "bezirksgericht affoltern": "zh_bezirksgericht_affoltern",
    # Generic fallback for any bezirksgericht not listed
    "bezirksgericht": "zh_bezirksgericht",
    # --- Specialized courts ---
    "mietgericht": "zh_mietgericht",
    "arbeitsgericht": "zh_arbeitsgericht",
}

# Keywords that take precedence over the Bezirksgericht they sit in.
_SPECIALISED_COURTS = ("arbeitsgericht", "mietgericht")


def _map_court(gericht: str, kammer: str) -> tuple[str, str | None]:
    """
    Map Gericht/Behörde + Abteilung/Kammer to (court_code, chamber_str).

    Strategy: check combined text against court map, longest match first.
    The kammer field becomes the chamber string.
    """
    combined = f"{gericht} {kammer}".lower().strip()
    kammer_clean = kammer.strip() if kammer and kammer.strip() else None

    # Specialised first-instance courts win over their host Bezirksgericht.
    # Since 2024 the portal files Arbeitsgericht / Mietgericht rulings as
    # Gericht="Bezirksgericht Zürich", Abteilung/Kammer="Arbeitsgericht";
    # before that as Gericht="Arbeitsgericht Zürich", Kammer="4. Abteilung".
    # The longest-keyword rule below picked "bezirksgericht zürich" over
    # "arbeitsgericht", so 67 Arbeitsgericht rulings (2006–2026) sat under
    # zh_bezirksgericht_zuerich while zh_arbeitsgericht held 34 (2026-09-04).
    for keyword in _SPECIALISED_COURTS:
        if keyword in combined:
            code = _COURT_MAP[keyword]
            # A Kammer that only repeats the court name carries no information.
            if kammer_clean and kammer_clean.lower() == keyword:
                return code, None
            return code, kammer_clean

    # Check longest keywords first to avoid substring collisions
    for keyword, code in sorted(_COURT_MAP.items(), key=lambda x: len(x[0]), reverse=True):
        if keyword in combined:
            # Chamber is the kammer field if it adds info beyond the court name
            return code, kammer_clean

    # Fallback
    logger.debug(f"ZH unmapped court: gericht={gericht!r}, kammer={kammer!r}")
    return "zh_gerichte", kammer.strip() if kammer and kammer.strip() else None


# ============================================================
# PDF text extraction
# ============================================================


def _extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """Extract text from PDF bytes using pdfplumber, fallback to pdfminer."""
    try:
        import pdfplumber

        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            pages = []
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    pages.append(text)
            return "\n\n".join(pages)
    except ImportError:
        pass
    except Exception as e:
        logger.warning(f"pdfplumber failed: {e}")

    try:
        from pdfminer.high_level import extract_text as pdfminer_extract

        return pdfminer_extract(io.BytesIO(pdf_bytes))
    except ImportError:
        pass
    except Exception as e:
        logger.warning(f"pdfminer failed: {e}")

    logger.error("PDF text extraction failed.")
    return ""


# ============================================================
# HTML parsing helpers
# ============================================================


def _get_detail_field(details_soup, label: str) -> str | None:
    """
    Extract a field value from the entscheidDetails block.

    Structure: <p><span>Label</span><span>Value</span></p>
    """
    for p in details_soup.find_all("p"):
        spans = p.find_all("span")
        if len(spans) >= 2:
            if spans[0].get_text(strip=True) == label:
                return spans[1].get_text(strip=True)
    return None


def _parse_date_ddmmyyyy(text: str) -> date | None:
    """Parse DD.MM.YYYY date string."""
    if not text:
        return None
    m = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", text)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            pass
    return None


# ============================================================
# Scraper
# ============================================================


class ZHGerichteScraper(BaseScraper):
    """
    Scraper for Zürich cantonal court decisions via gerichte-zh.ch TYPO3 API.

    Strategy:
    1. Iterate date windows (500 days each) from 1980 to present
    2. For each window: GET livesearch.php → parse HTML response
    3. Extract paired entscheid + entscheidDetails divs
    4. For each decision: download PDF, extract text, build Decision
    5. State tracking prevents re-scraping

    Rate limit: 1.5s between requests (20,000+ PDFs = ~8 hours)
    """

    REQUEST_DELAY = 1.5
    TIMEOUT = 60
    MAX_ERRORS = 100  # Higher tolerance for large corpus

    @property
    def court_code(self) -> str:
        return "zh_gerichte"

    def discover_new(self, since_date=None) -> Iterator[dict]:
        """
        Discover all ZH decisions via date-windowed API calls.

        Iterates from EARLY_START to today in TAG_SCHRITTE-day windows.
        Each API call returns all decisions in that date range.
        """
        if since_date and isinstance(since_date, str):
            since_date = parse_date(since_date)

        # Determine start date
        start = since_date if since_date else EARLY_START
        today = date.today()
        delta = timedelta(days=TAG_SCHRITTE - 1)
        one_day = timedelta(days=1)

        total_found = 0
        total_new = 0

        # First: fetch everything before the start date (pre-1980 edge cases)
        # NeueScraper does this: request("", AUFSETZTAG) for anything before 1980
        if not since_date:
            logger.info("ZH: fetching pre-1980 decisions...")
            try:
                for stub in self._fetch_window("", "01.01.1980"):
                    total_found += 1
                    if not self.state.is_known(stub["decision_id"]):
                        total_new += 1
                        yield stub
            except Exception as e:
                logger.warning(f"ZH pre-1980 fetch failed: {e}")

        # Main date-windowed iteration
        von = start
        while von <= today:
            bis = von + delta
            if bis > today:
                bis = today

            von_str = von.strftime("%d.%m.%Y")
            bis_str = bis.strftime("%d.%m.%Y")

            logger.info(f"ZH: fetching window {von_str} – {bis_str}")

            try:
                window_count = 0
                window_new = 0
                for stub in self._fetch_window(von_str, bis_str):
                    window_count += 1
                    total_found += 1
                    if not self.state.is_known(stub["decision_id"]):
                        window_new += 1
                        total_new += 1
                        yield stub

                logger.info(
                    f"ZH window {von_str}–{bis_str}: "
                    f"{window_count} found, {window_new} new"
                )
            except Exception as e:
                logger.error(f"ZH window {von_str}–{bis_str} failed: {e}")

            von = bis + one_day

        logger.info(f"ZH discovery complete: {total_found} total, {total_new} new")

    def _fetch_window(self, von: str, bis: str) -> Iterator[dict]:
        """
        Fetch a single date window from the livesearch API.

        Returns iterator of decision stubs.
        """
        params = dict(FIXED_PARAMS)
        params["entscheiddatum_von"] = von
        params["entscheiddatum_bis"] = bis

        resp = self.get(LIVESEARCH_URL, params=params)
        html = resp.text

        if not html or len(html) < 100:
            logger.debug(f"ZH window {von}–{bis}: empty response")
            return

        soup = BeautifulSoup(html, "html.parser")

        # Parse count from <div id="entscheideText"><strong>N</strong>
        count_div = soup.find("div", id="entscheideText")
        if count_div:
            strong = count_div.find("strong")
            if strong:
                try:
                    count = int(strong.get_text(strip=True))
                    logger.debug(f"ZH window {von}–{bis}: {count} decisions")
                except ValueError:
                    pass

        # Find paired entscheid + entscheidDetails divs
        entscheide = soup.find_all(
            "div", class_=re.compile(r"^entscheid\s+entscheid_nummer_")
        )
        details_divs = soup.find_all(
            "div", class_=re.compile(r"^entscheidDetails\s+container_")
        )

        if len(entscheide) != len(details_divs):
            logger.warning(
                f"ZH window {von}–{bis}: mismatched counts: "
                f"{len(entscheide)} entscheide vs {len(details_divs)} details"
            )

        # Pair them by extracting IDs
        entscheid_map = {}
        for div in entscheide:
            cls = div.get("class", [])
            cls_str = " ".join(cls) if isinstance(cls, list) else cls
            m = re.search(r"entscheid_nummer_(\S+)", cls_str)
            if m:
                entscheid_map[m.group(1)] = div

        details_map = {}
        for div in details_divs:
            cls = div.get("class", [])
            cls_str = " ".join(cls) if isinstance(cls, list) else cls
            m = re.search(r"container_(\S+)", cls_str)
            if m:
                details_map[m.group(1)] = div

        # Process matched pairs
        for doc_id, entscheid_div in entscheid_map.items():
            if doc_id not in details_map:
                logger.warning(f"ZH no details for doc_id={doc_id}")
                continue

            details_div = details_map[doc_id]
            stub = self._parse_entry(doc_id, entscheid_div, details_div)
            if stub:
                yield stub

    def _parse_entry(self, doc_id: str, entscheid_div, details_div) -> dict | None:
        """Parse a single entscheid + details pair into a stub dict."""

        # Geschäftsnummer (docket number)
        num = _get_detail_field(details_div, "Geschäftsnummer")
        if not num:
            logger.warning(f"ZH no Geschäftsnummer for doc_id={doc_id}")
            return None

        # Entscheiddatum
        edatum_str = _get_detail_field(details_div, "Entscheiddatum")
        if not edatum_str:
            logger.warning(f"ZH no Entscheiddatum for {num} (doc_id={doc_id})")
            return None

        edatum = _parse_date_ddmmyyyy(edatum_str)
        if not edatum:
            logger.warning(f"ZH unparseable date {edatum_str!r} for {num}")
            return None

        # Gericht/Behörde
        gericht = _get_detail_field(details_div, "Gericht/Behörde") or ""

        # Abteilung/Kammer
        kammer = _get_detail_field(details_div, "Abteilung/Kammer") or ""

        # Map to court code
        court_code, chamber = _map_court(gericht, kammer)

        # Titel (from entscheid div: <p><strong>Title</strong></p>)
        titel = ""
        strong = entscheid_div.find("strong")
        if strong:
            titel = strong.get_text(strip=True)

        # Leitsatz (italic text in entscheid div)
        leitsatz = ""
        em = entscheid_div.find("em")
        if em:
            leitsatz = em.get_text(strip=True)

        # Entscheidart
        entscheidart = _get_detail_field(details_div, "Entscheidart") or ""

        # Verweise (Weiterzug)
        verweise = _get_detail_field(details_div, "Verweise") or ""

        # Gesetz/e
        gesetze = _get_detail_field(details_div, "Gesetz/e, Verordnung/en etc") or ""

        # PDF URL
        pdf_link = details_div.find("a", class_="pdf-icon")
        pdf_url = None
        if pdf_link and pdf_link.get("href"):
            pdf_url = HOST + pdf_link["href"]

        if not pdf_url:
            # Also check for PDF icon image link
            img = details_div.find("img", src=re.compile(r"pdf-icon"))
            if img:
                parent_a = img.find_parent("a")
                if parent_a and parent_a.get("href"):
                    pdf_url = HOST + parent_a["href"]

        if not pdf_url:
            # Try entscheid div too
            for a in entscheid_div.find_all("a", href=True):
                href = a.get("href", "")
                if ".pdf" in href.lower():
                    pdf_url = HOST + href if href.startswith("/") else href
                    break

        if not pdf_url:
            logger.warning(f"ZH no PDF URL for {num} (doc_id={doc_id})")
            return None

        # Build decision ID
        decision_id = make_decision_id(court_code, num)

        # Source URL (print view)
        source_url = PRINT_URL_TEMPLATE.format(doc_id=doc_id)

        return {
            "decision_id": decision_id,
            "doc_id": doc_id,
            "docket_number": num,
            "decision_date": edatum,
            "court_code": court_code,
            "chamber": chamber,
            "gericht": gericht,
            "kammer": kammer,
            "title": titel,
            "leitsatz": leitsatz,
            "entscheidart": entscheidart,
            "verweise": verweise,
            "gesetze": gesetze,
            "pdf_url": pdf_url,
            "source_url": source_url,
        }

    def fetch_decision(self, stub: dict) -> Decision | None:
        """
        Download PDF for a decision and extract text.

        Steps:
        1. Download PDF
        2. Extract text with pdfplumber
        3. Build Decision object with all metadata
        """
        pdf_url = stub["pdf_url"]
        num = stub["docket_number"]

        # Download PDF
        try:
            resp = self.get(pdf_url, timeout=self.TIMEOUT)
        except Exception as e:
            logger.warning(f"ZH PDF download failed for {num}: {e}")
            return None

        content_type = resp.headers.get("Content-Type", "")
        if len(resp.content) < 100:
            logger.warning(
                f"ZH tiny PDF for {num}: {len(resp.content)} bytes, "
                f"Content-Type={content_type}"
            )
            return None

        # Extract text
        full_text = _extract_text_from_pdf(resp.content)
        if not full_text or len(full_text) < 30:
            logger.warning(
                f"ZH PDF text extraction short for {num}: "
                f"{len(full_text or '')} chars"
            )
            if not full_text:
                full_text = f"[PDF text extraction failed for {num}]"

        # Language detection
        language = detect_language(full_text) if len(full_text) > 100 else "de"

        # Build title
        title = stub.get("title") or f"{stub['gericht']} — {num}"

        # Regeste: combine leitsatz if available
        regeste = stub.get("leitsatz") or None

        # Decision type
        decision_type = stub.get("entscheidart") or None

        # Weiterzug / references
        stub.get("verweise") or None

        return Decision(
            decision_id=stub["decision_id"],
            court=stub["court_code"],
            canton="ZH",
            chamber=stub.get("chamber"),
            docket_number=num,
            decision_date=stub["decision_date"],
            language=language,
            title=title,
            regeste=regeste,
            full_text=full_text,
            source_url=stub["source_url"],
            pdf_url=pdf_url,
            decision_type=decision_type,
            cited_decisions=extract_citations(full_text) if len(full_text) > 200 else [],
            external_id=f"zh_gerichte_{stub['doc_id']}",
        )
