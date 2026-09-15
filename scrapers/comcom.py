"""
ComCom Scraper (Eidgenössische Kommunikationskommission)
========================================================

Scrapes published decisions from the Swiss Federal Communications Commission
(ComCom) at comcom.admin.ch.

Architecture:
- Nuxt SSR app (server-side rendered, all content in initial HTML)
- Decisions organized by 2-year date ranges at /de/entscheide-YYYY-YYYY
- All documents are PDFs under /dam/{lang}/sd-web/{hash}/filename.pdf
- No docket numbers — decisions identified by title + date
- Each listing entry: <a href="/dam/...pdf"> wrapping <h4> title + <p> metadata

Coverage: ~64 unique decisions (1999-2025)
Rate limiting: 2.0 seconds (PDF downloads)
"""
from __future__ import annotations

import hashlib
import io
import logging
import re
from datetime import date, datetime, timezone
from typing import Iterator
from urllib.parse import urljoin

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

BASE_URL = "https://www.comcom.admin.ch"
INDEX_URL = f"{BASE_URL}/de/entscheide"

# Date ranges to crawl (non-overlapping, covers full history)
DATE_RANGES = [
    (2024, 2025), (2022, 2023), (2020, 2021), (2018, 2019),
    (2016, 2017), (2014, 2015), (2012, 2013), (2010, 2011),
    (2008, 2009), (2006, 2007), (2004, 2005), (2002, 2003),
    (2000, 2001), (1998, 1999),
]

# Extract date from metadata line: "Entscheid der Kommunikationskommission, DD.MM.YYYY"
DATE_PATTERN = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{4})")

# Extract DAM ID from URL: /dam/de/sd-web/{damId}/filename.pdf
DAM_ID_PATTERN = re.compile(r"/dam/\w{2}/sd-web/([^/]+)/")

MONTHS_DE = {
    "Januar": 1, "Februar": 2, "März": 3, "April": 4,
    "Mai": 5, "Juni": 6, "Juli": 7, "August": 8,
    "September": 9, "Oktober": 10, "November": 11, "Dezember": 12,
}


def _extract_pdf_text(data: bytes) -> str:
    """Extract text from PDF bytes using fitz (PyMuPDF) with pdfplumber fallback."""
    try:
        import fitz

        with fitz.open(stream=data, filetype="pdf") as doc:
            return "\n\n".join(page.get_text() for page in doc)
    except Exception:
        import pdfplumber

        with pdfplumber.open(io.BytesIO(data)) as pdf:
            return "\n\n".join(p.extract_text() or "" for p in pdf.pages)


def _dam_id(url: str) -> str | None:
    """Extract the unique DAM ID from a ComCom PDF URL."""
    m = DAM_ID_PATTERN.search(url)
    return m.group(1) if m else None


def _make_slug(title: str, decision_date: date | None) -> str:
    """Create a stable docket-like identifier from title + date."""
    # Shorten title to key words
    slug = re.sub(r"[^a-zA-Z0-9äöüÄÖÜ]+", "-", title)[:80].strip("-").lower()
    if decision_date:
        slug = f"{slug}-{decision_date.isoformat()}"
    # Add short hash for uniqueness
    h = hashlib.md5(title.encode()).hexdigest()[:6]
    return f"{slug}-{h}"


class ComComScraper(BaseScraper):
    """
    Scraper for ComCom decisions.

    Iterates date-range pages, extracts PDF links + metadata,
    downloads and extracts text from PDFs.
    """

    REQUEST_DELAY = 2.0
    TIMEOUT = 30

    @property
    def court_code(self) -> str:
        return "comcom"

    def _date_ranges(self) -> list[tuple[int, int]]:
        """Two-year range pages, newest first: the static list plus whatever
        /de/entscheide links today. DATE_RANGES was hard-coded up to 2024-2025, so
        the day the portal adds entscheide-2026-2027 nothing would have looked
        there (found in the 2026-09-14 scraper walkthrough; ComCom's newest
        decision, 19.12.2024, is still the last one published)."""
        ranges = set(DATE_RANGES)
        try:
            resp = self.get(f"{BASE_URL}/de/entscheide")
            # The index is a hydrated Vue page: the slugs sit in JSON payload strings
            # as well as in <a href>, so match the slug itself, not only href="…".
            for a, b in re.findall(r"/de/entscheide-(\d{4})-(\d{4})\b", resp.text):
                ranges.add((int(a), int(b)))
        except Exception as e:  # noqa: BLE001 — the static list still runs
            logger.warning(f"[comcom] range index unreadable ({e}); using the static list")
        found = ranges - set(DATE_RANGES)
        if found:
            logger.info(f"[comcom] range pages beyond the static list: {sorted(found)}")
        return sorted(ranges, reverse=True)

    def discover_new(self, since_date=None) -> Iterator[dict]:
        """
        Discover ComCom decisions from date-range listing pages.
        """
        seen_dam_ids = set()

        for start_year, end_year in self._date_ranges():
            # Skip old ranges if since_date is set
            if since_date:
                if isinstance(since_date, str):
                    since_date = parse_date(since_date) or date(1998, 1, 1)
                if end_year < since_date.year:
                    continue

            page_url = f"{BASE_URL}/de/entscheide-{start_year}-{end_year}"
            try:
                response = self.get(page_url)
                if response.status_code != 200:
                    logger.debug(f"[comcom] {page_url}: HTTP {response.status_code}")
                    continue

                soup = BeautifulSoup(response.text, "html.parser")

                # Find all PDF download links
                for link in soup.find_all("a", href=True):
                    href = link.get("href", "")
                    if "/dam/" not in href or not href.endswith(".pdf"):
                        continue

                    pdf_url = urljoin(BASE_URL, href)
                    dam_id = _dam_id(pdf_url)

                    # Dedup by DAM ID
                    if dam_id and dam_id in seen_dam_ids:
                        continue
                    if dam_id:
                        seen_dam_ids.add(dam_id)

                    # Extract title from <h4> inside the link
                    h4 = link.find("h4")
                    title = h4.get_text(strip=True) if h4 else ""

                    # Extract metadata from <p> siblings
                    paragraphs = link.find_all("p")
                    meta_text = " ".join(p.get_text(strip=True) for p in paragraphs)

                    # Parse decision date from metadata
                    decision_date = None
                    date_match = DATE_PATTERN.search(meta_text)
                    if date_match:
                        try:
                            decision_date = date(
                                int(date_match.group(3)),
                                int(date_match.group(2)),
                                int(date_match.group(1)),
                            )
                        except ValueError:
                            pass

                    # Legal status
                    legal_status = ""
                    if "rechtskräftig" in meta_text.lower():
                        if "nicht rechtskräftig" in meta_text.lower():
                            legal_status = "nicht rechtskräftig"
                        elif "teilweise rechtskräftig" in meta_text.lower():
                            legal_status = "teilweise rechtskräftig"
                        else:
                            legal_status = "rechtskräftig"

                    stub = {
                        "pdf_url": pdf_url,
                        "title": title,
                        "decision_date": decision_date,
                        "dam_id": dam_id,
                        "legal_status": legal_status,
                        "date_range": f"{start_year}-{end_year}",
                    }

                    # Pre-check decision_id
                    slug = _make_slug(title, decision_date)
                    candidate_id = make_decision_id("comcom", slug)
                    if self.state.is_known(candidate_id):
                        continue

                    yield stub

            except Exception as e:
                logger.warning(f"[comcom] Error on {page_url}: {e}")
                continue

        logger.info(f"[comcom] Found {len(seen_dam_ids)} unique decisions across all date ranges")

    def fetch_decision(self, stub: dict) -> Decision | None:
        """Download PDF and extract text for a ComCom decision."""
        try:
            pdf_url = stub["pdf_url"]
            title = stub.get("title", "")
            decision_date = stub.get("decision_date")

            slug = _make_slug(title, decision_date)
            decision_id = make_decision_id("comcom", slug)

            if self.state.is_known(decision_id):
                return None

            # Download PDF
            pdf_resp = self.get(pdf_url)
            if pdf_resp.status_code != 200:
                logger.warning(f"[comcom] PDF download failed: {pdf_url}")
                return None

            if len(pdf_resp.content) < 500:
                logger.warning(f"[comcom] PDF too small: {pdf_url}")
                return None

            full_text = _extract_pdf_text(pdf_resp.content)

            if len(full_text.strip()) < 100:
                logger.warning(f"[comcom] PDF text too short: {pdf_url}")
                return None

            lang = detect_language(full_text) if len(full_text) > 200 else "de"

            decision = Decision(
                decision_id=decision_id,
                court="comcom",
                canton="CH",
                docket_number=slug,
                decision_date=decision_date,
                language=lang,
                title=title,
                legal_area="Fernmelderecht",
                regeste=None,
                full_text=self.clean_text(full_text),
                decision_type="Entscheid",
                appeal_info=stub.get("legal_status"),
                source_url=f"{BASE_URL}/de/entscheide-{stub['date_range']}",
                pdf_url=pdf_url,
                cited_decisions=extract_citations(full_text) if len(full_text) > 200 else [],
                scraped_at=datetime.now(timezone.utc),
            )
            return decision

        except Exception as e:
            logger.error(f"[comcom] Failed to fetch decision: {e}", exc_info=True)
            return None


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Scrape ComCom decisions")
    parser.add_argument("--max", type=int, default=10, help="Max decisions")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    scraper = ComComScraper()
    decisions = scraper.run(max_decisions=args.max)
    scraper.mark_run_complete(decisions)
    print(f"Scraped {len(decisions)} ComCom decisions")
