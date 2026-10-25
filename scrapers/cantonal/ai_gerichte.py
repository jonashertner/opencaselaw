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


_MONTHS = {
    "januar": 1, "jänner": 1, "februar": 2, "märz": 3, "maerz": 3, "april": 4,
    "mai": 5, "juni": 6, "juli": 7, "august": 8, "september": 9,
    "oktober": 10, "november": 11, "dezember": 12,
}
_MONTH_ALT = "|".join(sorted(_MONTHS, key=len, reverse=True))
# The court's own source line, at the end of each published ruling:
# "Kantonsgericht Appenzell I.Rh., Entscheid K 7-2021 vom 16. November 2021",
# "..., Verwaltungsgericht,\nEntscheid V 8-2024 vom 03.12.2024". The docket may
# be wrapped ("KSE 15-\n2025").
_OWN_LINE_RE = re.compile(
    r"\b(?:Entscheid|Zwischenentscheid|Endentscheid|Urteil|Beschluss|Verfügung)\s+"
    r"(?P<docket>[A-Z]{1,4}\s?\d{1,4}\s?[-/]\s?\d{2,4})\s+vom\s+"
    r"(?:(?P<d>\d{1,2})\.\s*(?P<m>" + _MONTH_ALT + r")\s*(?P<y>(?:19|20)\d{2})"
    r"|(?P<nd>\d{1,2})\.(?P<nm>\d{1,2})\.(?P<ny>(?:19|20)\d{2}))(?!\d)",
    re.IGNORECASE)


def _docket_key(docket: str | None) -> str:
    """'K 7-2021', 'K_7-2021', 'kse-2-2025', 'KE 63/06' -> 'K72021' ..."""
    return re.sub(r"[\s_/-]", "", docket or "").upper()


def own_decision_date(text: str | None, docket: str | None) -> date | None:
    """The date the ruling's own source line gives for ``docket``, or None.

    The listing on ai.ch dates an entry by when it was put online, weeks or
    months after the ruling: K 7-2021 of 16 November 2021 was listed on
    19 January 2022, after the Federal Supreme Court had already declined to
    hear the appeal (report 2026-10-24). Only a line naming the row's own
    docket counts; the text also names the decisions it reviews and the
    Federal Supreme Court's ruling on it. The last such line wins (the source
    line closes the ruling)."""
    key = _docket_key(docket)
    if not text or not key:
        return None
    found = None
    for m in _OWN_LINE_RE.finditer(text):
        if _docket_key(m.group("docket")) != key:
            continue
        try:
            if m.group("y"):
                found = date(int(m.group("y")), _MONTHS[m.group("m").lower()], int(m.group("d")))
            else:
                found = date(int(m.group("ny")), int(m.group("nm")), int(m.group("nd")))
        except (ValueError, KeyError):
            continue
    if found is None:
        return None
    year = re.search(r"[-/_ ]((?:19|20)\d{2})$", (docket or "").strip())
    if year and found.year < int(year.group(1)):
        return None
    return found


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

        # The ruling's own line first; the listing date is when ai.ch put it
        # online, so it stays only as the publication date.
        listed = stub.get("decision_date")
        decision_date = own_decision_date(full_text, stub["docket_number"]) or listed
        if not decision_date:
            logger.warning(f"[ai_gerichte] No date for {stub['docket_number']}")

        language = detect_language(full_text) if len(full_text) > 100 else "de"

        return Decision(
            decision_id=stub["decision_id"],
            court="ai_gerichte",
            canton="AI",
            docket_number=stub["docket_number"],
            decision_date=decision_date,
            publication_date=listed if listed and listed != decision_date else None,
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
