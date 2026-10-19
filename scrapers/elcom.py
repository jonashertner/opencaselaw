"""
ElCom Scraper (Eidgenössische Elektrizitätskommission)
======================================================

Scrapes published Verfügungen from the Swiss Federal Electricity Commission
(ElCom) at elcom.admin.ch.

Architecture:
- Nuxt SSR app (server-side rendered, all content in initial HTML)
- Single listing page with collapsible year-based accordion sections
- All documents are PDFs under /dam/de/sd-web/{hash}/filename.pdf
- Same PDFs appear in both "Nach Datum" and "Nach Thema" sections (deduped)
- <a class="download-item"> elements with <h4 class="download-item__title">

Entry format:
  Title: "{docket_nr} {description}, {date}"
  e.g. "232-00095 Verwendung der Einnahmen..., 3.2.2026"
  Status: "rechtskräftig" or "noch nicht rechtskräftig"
  Meta: "PDF|{size}|{publication_date}"

Coverage: ~430 unique Verfügungen (2008-2026)
Rate limiting: 2.0 seconds (PDF downloads)
"""
from __future__ import annotations

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

from scrapers import pdf_ocr

logger = logging.getLogger(__name__)

LISTING_URL = "https://www.elcom.admin.ch/de/verfuegungen"
BASE_URL = "https://www.elcom.admin.ch"

# Docket number at the start of a title: "232-00095", "211-00506", "25-00187"
DOCKET_PATTERN = re.compile(r"^(\d{2,3}-\d{4,5})")

# Date at the end of a title after the last comma: ", 3.2.2026" or ", 16.12.2025"
# Also handles: ", 15. August 2025", ", 04.02.2025", ", 4. März 2025"
TRAILING_DATE_PATTERN = re.compile(
    r",\s+(\d{1,2}[\./]\s*\d{1,2}[\./]\s*\d{4}"
    r"|\d{1,2}\.?\s+(?:Januar|Februar|März|April|Mai|Juni|Juli|August|September|Oktober|November|Dezember)\s+\d{4}"
    r"|\d{1,2}\.?\s+(?:janvier|février|mars|avril|mai|juin|juillet|août|septembre|octobre|novembre|décembre)\s+\d{4})"
    r"\s*$",
    re.IGNORECASE,
)

# Publication date from meta-info span: "2. März 2026", "22. Januar 2026"
PUB_DATE_PATTERN = re.compile(
    r"(\d{1,2})\.?\s*(Januar|Februar|März|April|Mai|Juni|Juli|August|September|Oktober|November|Dezember)\s+(\d{4})",
    re.IGNORECASE,
)


def _slugify(text: str) -> str:
    """Create a filesystem-safe slug from text."""
    text = text.lower().strip()
    text = re.sub(r"[äÄ]", "ae", text)
    text = re.sub(r"[öÖ]", "oe", text)
    text = re.sub(r"[üÜ]", "ue", text)
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    return text[:80]


def _extract_pdf_text(data: bytes) -> str:
    """Extract text from PDF bytes using fitz (PyMuPDF) with pdfplumber fallback.

    Robust to corrupt/malformed PDFs: extraction errors are caught and logged so a single
    bad PDF returns "" rather than raising into a caller's fetch_decision (which would abort
    the scrape run). All callers already treat "" as a skip.
    """
    try:
        import fitz

        doc = fitz.open(stream=data, filetype="pdf")
        return "\n\n".join(p.get_text() for p in doc)
    except ImportError:
        pass
    except Exception as e:
        logger.warning(f"fitz PDF extraction failed: {e}")
    try:
        import pdfplumber

        with pdfplumber.open(io.BytesIO(data)) as pdf:
            return "\n\n".join(p.extract_text() or "" for p in pdf.pages)
    except ImportError:
        pass
    except Exception as e:
        logger.warning(f"pdfplumber PDF extraction failed: {e}")
    return ""


def _extract_content_hash(href: str) -> str | None:
    """Extract the unique content hash from an ElCom DAM URL.

    URL pattern: /dam/{lang}/sd-web/{HASH}/{filename}.pdf
    The hash uniquely identifies a document regardless of language variant.
    """
    m = re.search(r"/sd-web/([^/]+)/", href)
    return m.group(1) if m else None


class ElComScraper(BaseScraper):
    """Scraper for ElCom (Swiss Federal Electricity Commission) Verfügungen."""

    REQUEST_DELAY = 2.0
    TIMEOUT = 120  # Some PDFs are large

    # Eight listed Verfügungen are image-only scans (a 512 KB PDF with no
    # text layer, e.g. 211-00008): fetch_decision returns None for them on
    # every run, which re-downloaded the same 3-4 MB nightly since March 2026
    # and made the health check read "8 not downloadable" as a portal outage.
    # fetch_decision caches exactly those as gaps (re-probed after
    # GAP_TTL_DAYS in case a text layer appears); a failed download is NOT
    # cached, so the 09:00 UTC retry unit still gets the PDFs that 502 at
    # 01:00 (admin.ch DAM). The real fix for the scans is OCR, tracked
    # separately. (The blanket CACHE_NONE_AS_GAP flag used here until
    # 2026-09-14 would have cached the 502s too once run_scraper honoured it.)

    @property
    def court_code(self) -> str:
        return "elcom"

    def discover_new(self, since_date=None) -> Iterator[dict]:
        """Discover ElCom Verfügungen from the listing page.

        The page contains all decisions in a single SSR HTML page, organized
        in year-based accordion sections. Both "Nach Datum" and "Nach Thema"
        sections link to the same PDFs, so we deduplicate by content hash.
        """
        response = self.get(LISTING_URL)
        soup = BeautifulSoup(response.text, "html.parser")

        seen_hashes = set()
        found = 0

        for a in soup.find_all("a", class_="download-item", href=True):
            href = a["href"]
            if not href.endswith(".pdf"):
                continue

            # Deduplicate: same PDF appears in both "Nach Datum" and "Nach Thema"
            content_hash = _extract_content_hash(href)
            if content_hash:
                if content_hash in seen_hashes:
                    continue
                seen_hashes.add(content_hash)

            pdf_url = href if href.startswith("http") else urljoin(BASE_URL, href)

            # Extract title from h4.download-item__title
            h4 = a.find("h4", class_="download-item__title")
            title = h4.get_text(strip=True) if h4 else ""
            if not title:
                # Fallback: aria-label or link text
                title = a.get("aria-label", "").replace("Download ", "")
                if not title:
                    title = a.get_text(strip=True)
            if not title or len(title) < 5:
                logger.debug(f"[elcom] Skipping entry with no title: {href[:80]}")
                continue

            # Extract docket number from title start
            docket_m = DOCKET_PATTERN.match(title)
            docket_prefix = docket_m.group(1) if docket_m else ""

            # Extract decision date from title end
            decision_date_str = None
            date_m = TRAILING_DATE_PATTERN.search(title)
            if date_m:
                decision_date_str = date_m.group(1).strip()
                # Clean title: remove trailing date
                clean_title = title[:date_m.start()].strip()
            else:
                clean_title = title

            # Extract status from p.download-item__description
            status_p = a.find("p", class_="download-item__description")
            status = status_p.get_text(strip=True) if status_p else None

            # Extract publication date from meta-info spans
            pub_date_str = None
            meta_p = a.find("p", class_="download-item__meta-info")
            if meta_p:
                meta_text = meta_p.get_text(" ", strip=True)
                pub_m = PUB_DATE_PATTERN.search(meta_text)
                if pub_m:
                    pub_date_str = f"{pub_m.group(1)}. {pub_m.group(2)} {pub_m.group(3)}"

            # Build docket: prefer explicit docket number, fall back to slug.
            # Append short content hash to disambiguate multiple PDFs with
            # the same docket number and date (e.g. 211-00008 has several
            # distinct Verfügungen on the same day).
            hash_suffix = f"-{content_hash[:6]}" if content_hash else ""

            if docket_prefix:
                date_suffix = ""
                if decision_date_str:
                    parsed = parse_date(decision_date_str)
                    if parsed:
                        date_suffix = f"-{parsed.isoformat()}"
                docket = docket_prefix + date_suffix + hash_suffix
            else:
                slug = _slugify(clean_title)
                date_suffix = ""
                if decision_date_str:
                    parsed = parse_date(decision_date_str)
                    if parsed:
                        date_suffix = f"-{parsed.isoformat()}"
                docket = slug + date_suffix + hash_suffix

            decision_id = make_decision_id("elcom", docket)
            if self.state.is_known(decision_id):
                continue

            # Filter by since_date
            if since_date and decision_date_str:
                parsed = parse_date(decision_date_str)
                if parsed and parsed < since_date:
                    continue

            found += 1
            yield {
                "docket_number": docket,
                "decision_date": decision_date_str or pub_date_str or "",
                "pdf_url": pdf_url,
                "title": clean_title,
                "status": status,
                "pub_date": pub_date_str,
            }

        logger.info(
            f"[elcom] Found {found} new Verfügungen "
            f"({len(seen_hashes)} unique PDFs on page)"
        )

    def fetch_decision(self, stub: dict) -> Decision | None:
        """Download PDF and extract decision text."""
        pdf_url = stub["pdf_url"]
        docket = stub["docket_number"]

        try:
            response = self.get(pdf_url)
        except Exception as e:
            logger.error(f"[elcom] Failed to download PDF for {docket}: {e}")
            return None

        full_text = _extract_pdf_text(response.content)
        if not full_text or len(full_text.strip()) < pdf_ocr.MIN_TEXT_CHARS:
            # Image-only scans: OCR before caching the id as a gap.
            full_text = pdf_ocr.ocr_pdf_bytes(response.content)
            if len(full_text) < pdf_ocr.MIN_TEXT_CHARS:
                logger.warning(
                    f"[elcom] No text extracted from {docket} "
                    f"({len(response.content)} bytes PDF, OCR included) — cached as gap for "
                    f"{self.state.GAP_TTL_DAYS} days"
                )
                self.state.mark_gap(make_decision_id("elcom", docket))
                return None
            logger.info(f"[elcom] OCR recovered {len(full_text)} chars for {docket}")

        full_text = self.clean_text(full_text)
        lang = detect_language(full_text)
        decision_date = parse_date(stub.get("decision_date", ""))

        return Decision(
            decision_id=make_decision_id("elcom", docket),
            court="elcom",
            canton="CH",
            docket_number=docket,
            decision_date=decision_date,
            language=lang,
            title=stub.get("title"),
            legal_area="Elektrizitätsrecht",
            decision_type="Verfügung",
            full_text=full_text,
            source_url=pdf_url,
            pdf_url=pdf_url,
            cited_decisions=extract_citations(full_text),
            scraped_at=datetime.now(timezone.utc),
        )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Scrape ElCom Verfügungen")
    parser.add_argument("--since", type=str, help="Start date YYYY-MM-DD")
    parser.add_argument("--max", type=int, default=5, help="Max decisions")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    for noisy in ("pdfminer", "pdfplumber", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    since = date.fromisoformat(args.since) if args.since else None
    scraper = ElComScraper()
    decisions = scraper.run(since_date=since, max_decisions=args.max)
    scraper.mark_run_complete(decisions)
    for d in decisions:
        print(f"  {d.decision_id}  {d.decision_date}  {len(d.full_text)} chars  {d.title[:60]}")
    print(f"\nScraped {len(decisions)} ElCom Verfügungen")
