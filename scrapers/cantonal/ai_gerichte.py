"""
Appenzell Innerrhoden Courts Scraper (AI Gerichte)
===================================================
Scrapes court decisions from the cantonal website at www.ai.ch.

Architecture:
- GET /gerichte/rechtsprechung → HTML listing of recent decisions with PDF links
- GET /themen/staat-und-recht/veroeffentlichungen/verwaltungs-und-gerichtsentscheide
  → Historical annual compilations (PDF)
- No authentication required
- PDF download from ai.ch

Very small canton: ~104 decisions total.
Courts: Kantonsgericht (KG), Bezirksgericht (BZG)
Platform: Custom CMS (ai.ch)
"""
from __future__ import annotations

import logging
import re
from datetime import date
from typing import Iterator

from bs4 import BeautifulSoup

from base_scraper import BaseScraper
from models import (
    Decision,
    detect_language,
    extract_citations,
    make_decision_id,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://www.ai.ch"
LISTING_URLS = [
    f"{BASE_URL}/gerichte/rechtsprechung",
    f"{BASE_URL}/gerichte/gerichtsentscheide",
]

RE_DATE = re.compile(r"(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})")
RE_DOCKET = re.compile(r"([A-Z]{1,4}[-\s]\d{2,4}[-/]\d+)")


def _parse_swiss_date(text):
    if not text:
        return None
    m = RE_DATE.search(text)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            pass
    return None


class AIGerichteScraper(BaseScraper):
    """
    Scraper for Appenzell Innerrhoden court decisions.

    Strategy: fetch listing pages, extract PDF links, download and
    extract text. Very small volume (~104 decisions).
    """

    REQUEST_DELAY = 2.0
    TIMEOUT = 30
    MAX_ERRORS = 20

    @property
    def court_code(self):
        return "ai_gerichte"

    def discover_new(self, since_date=None) -> Iterator[dict]:
        if since_date and isinstance(since_date, str):
            since_date = date.fromisoformat(since_date)

        total_yielded = 0
        seen_ids = set()

        for listing_url in LISTING_URLS:
            try:
                r = self.get(listing_url)
            except Exception as e:
                logger.error(f"AI: failed to fetch {listing_url}: {e}")
                continue

            soup = BeautifulSoup(r.text, "html.parser")

            for a in soup.find_all("a", href=True):
                href = a.get("href", "")
                if not href.endswith(".pdf"):
                    continue

                # Build absolute URL
                if href.startswith("/"):
                    pdf_url = f"{BASE_URL}{href}"
                elif href.startswith("http"):
                    pdf_url = href
                else:
                    continue

                # Extract filename for dedup
                filename = href.split("/")[-1].replace(".pdf", "")
                if not filename:
                    continue

                # Build docket from filename or link text
                link_text = a.get_text(strip=True)
                docket = None
                m_docket = RE_DOCKET.search(link_text) or RE_DOCKET.search(filename)
                if m_docket:
                    docket = m_docket.group(1)
                else:
                    docket = filename[:60]

                decision_id = make_decision_id("ai_gerichte", docket)
                if decision_id in seen_ids:
                    continue
                seen_ids.add(decision_id)

                if self.state.is_known(decision_id):
                    continue

                # Try to extract date from surrounding text
                parent = a.find_parent("li") or a.find_parent("tr") or a.find_parent("div")
                decision_date = None
                if parent:
                    decision_date = _parse_swiss_date(parent.get_text())
                if not decision_date:
                    decision_date = _parse_swiss_date(link_text)

                if since_date and decision_date and decision_date < since_date:
                    continue

                title = link_text[:200] if link_text else filename

                total_yielded += 1
                yield {
                    "decision_id": decision_id,
                    "docket_number": docket,
                    "decision_date": decision_date,
                    "title": title,
                    "pdf_url": pdf_url,
                    "url": pdf_url,
                }

        logger.info(f"AI: discovery complete: {total_yielded} new stubs")

    def fetch_decision(self, stub: dict) -> Decision | None:
        """Download PDF and extract text."""
        pdf_url = stub.get("pdf_url")
        if not pdf_url:
            return None

        full_text = ""
        fetched = False
        try:
            r = self.get(pdf_url, timeout=30)
            if r.status_code == 200 and len(r.content) > 1000:
                fetched = True
                full_text = self._extract_pdf_text(r.content)
        except Exception as e:
            logger.warning(f"AI: PDF download failed for {stub['docket_number']}: {e}")

        # No placeholder rows (until 2026-09-14 the title, or "[Text extraction
        # failed …]", was stored as the full text): a failed download is retried
        # next run, a fetched PDF without a text layer is cached as a gap.
        if not fetched:
            return None
        if not full_text or len(full_text) < 50:
            logger.warning(
                f"AI: no usable text for {stub['docket_number']} — cached as gap for "
                f"{self.state.GAP_TTL_DAYS} days"
            )
            self.state.mark_gap(stub["decision_id"])
            return None

        decision_date = stub.get("decision_date")
        if not decision_date:
            logger.warning(f"[ai_gerichte] No date for {stub['docket_number']}")

        language = detect_language(full_text) if len(full_text) > 100 else "de"

        return Decision(
            decision_id=stub["decision_id"],
            court="ai_gerichte",
            canton="AI",
            docket_number=stub["docket_number"],
            decision_date=decision_date,
            language=language,
            title=stub.get("title"),
            full_text=full_text,
            source_url=stub.get("url"),
            pdf_url=stub.get("pdf_url"),
            cited_decisions=extract_citations(full_text) if len(full_text) > 200 else [],
        )

    @staticmethod
    def _extract_pdf_text(pdf_bytes: bytes) -> str:
        """Extract text from PDF bytes."""
        try:
            import fitz
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            pages = []
            for page in doc:
                pages.append(page.get_text())
            doc.close()
            return "\n\n".join(pages)
        except ImportError:
            pass

        try:
            import pdfplumber
            import io
            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                pages = []
                for page in pdf.pages:
                    text = page.extract_text()
                    if text:
                        pages.append(text)
                return "\n\n".join(pages)
        except ImportError:
            pass

        return ""
