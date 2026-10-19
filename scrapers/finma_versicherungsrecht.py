"""
FINMA Versicherungsrechtliche Entscheide Scraper
==================================================

Scrapes insurance law court decisions published by FINMA under Art. 49 VAG
(Versicherungsaufsichtsgesetz) from finma.ch.

Architecture:
- Same Sitecore CMS API as FINMA Kasuistik, different dataset ID
- POST to /de/api/search/getresult with dataset {F475205A-A058-469A-88B2-FBAFA2C00FD1}
- Returns ~2,610 items, each linking to a PDF file
- PDFs are hosted at /~/media/finma/dokumente/dokumentencenter/myfinma/versicherungsrecht/
- Title encodes date, canton, and language: "21. Oktober 2024 Tessin Italienisch"
- Date field: DD.MM.YYYY

Coverage: 1994–present (~2,610 decisions)
Rate limiting: 1.5 seconds
"""

from __future__ import annotations

import io
import logging
from datetime import date, datetime, timezone
from typing import Iterator

from base_scraper import BaseScraper
from scrapers import pdf_ocr
from models import (
    Decision,
    detect_language,
    extract_citations,
    make_decision_id,
    parse_date,
)

logger = logging.getLogger(__name__)

# Sitecore search API — same endpoint, different dataset
SEARCH_URL = "https://www.finma.ch/de/api/search/getresult"
DATASET_ID = "{F475205A-A058-469A-88B2-FBAFA2C00FD1}"
BASE_URL = "https://www.finma.ch"

# Map German canton names (as they appear in titles) to canton codes
CANTON_MAP = {
    "Aargau": "AG", "Appenzell Ausserrhoden": "AR", "Appenzell Innerrhoden": "AI",
    "Basel-Landschaft": "BL", "Basel-Stadt": "BS", "Bern": "BE", "Freiburg": "FR",
    "Genf": "GE", "Glarus": "GL", "Graubünden": "GR", "Jura": "JU",
    "Luzern": "LU", "Neuenburg": "NE", "Nidwalden": "NW", "Obwalden": "OW",
    "Schaffhausen": "SH", "Schwyz": "SZ", "Solothurn": "SO", "St. Gallen": "SG",
    "Tessin": "TI", "Thurgau": "TG", "Uri": "UR", "Waadt": "VD",
    "Wallis": "VS", "Zug": "ZG", "Zürich": "ZH",
    # French variants
    "Genève": "GE", "Fribourg": "FR", "Neuchâtel": "NE", "Vaud": "VD",
    "Valais": "VS", "Argovie": "AG", "Bâle-Campagne": "BL", "Bâle-Ville": "BS",
    "Berne": "BE", "Glaris": "GL", "Grisons": "GR", "Lucerne": "LU",
    "Nidwald": "NW", "Obwald": "OW", "Schaffhouse": "SH", "Schwyz": "SZ",
    "Soleure": "SO", "Saint-Gall": "SG", "Thurgovie": "TG",
    "Zoug": "ZG", "Zurich": "ZH",
    # Italian variants
    "Ticino": "TI", "Grigioni": "GR",
    # Federal
    "Bund": "CH", "Bundesgericht": "CH", "Fédéral": "CH",
}

# Language names as they appear in FINMA titles
LANGUAGE_MAP = {
    "Deutsch": "de", "Französisch": "fr", "Italienisch": "it",
    "Allemand": "de", "Français": "fr", "Italien": "it",
    "Tedesco": "de", "Francese": "fr", "Italiano": "it",
}

# Max PDF size to download (20 MB)
MAX_PDF_SIZE = 20 * 1024 * 1024


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


def _parse_title(title: str) -> dict:
    """Parse FINMA insurance decision title.

    Title format: "21. Oktober 2024 Tessin Italienisch"
    Returns: {canton, canton_code, language_name, language_code}
    """
    result = {"canton": None, "canton_code": "CH", "language_name": None, "language_code": None}

    # Try to extract canton name
    for name, code in sorted(CANTON_MAP.items(), key=lambda x: -len(x[0])):
        if name in title:
            result["canton"] = name
            result["canton_code"] = code
            break

    # Try to extract language
    for name, code in LANGUAGE_MAP.items():
        if name in title:
            result["language_name"] = name
            result["language_code"] = code
            break

    return result


class FINMAVersicherungsrechtScraper(BaseScraper):
    """Scraper for FINMA insurance law decisions (Versicherungsrechtliche Entscheide)."""

    REQUEST_DELAY = 1.5
    TIMEOUT = 120  # Large listing requires longer timeout

    @property
    def court_code(self) -> str:
        return "finma_versicherungsrecht"

    def _fetch_listing(self) -> list[dict]:
        """Fetch all items from Sitecore search API."""
        response = self.post(
            SEARCH_URL,
            data=f"ds={DATASET_ID}&Order=4",
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": "https://www.finma.ch/de/dokumentation/versicherungsrechtliche-entscheide/",
            },
        )
        data = response.json()
        items = data.get("Items", [])
        logger.info(f"[finma_vr] Listing returned {len(items)} items")
        return items

    def discover_new(self, since_date=None) -> Iterator[dict]:
        """Discover FINMA insurance law decisions."""
        items = self._fetch_listing()

        for item in items:
            title = item.get("Title", "").strip()
            link = item.get("Link", "")
            if not title or not link:
                continue

            # Only process PDF items
            ext = item.get("Extension", "").lower()
            if ext != "pdf":
                logger.debug(f"[finma_vr] Skipping non-PDF: {title} ({ext})")
                continue

            # Build decision_id from the PDF filename (unique, stable)
            # Link: /~/media/finma/.../20241021_i_ti_o_01.pdf
            pdf_filename = link.rsplit("/", 1)[-1].replace(".pdf", "")
            decision_id = make_decision_id("finma_versicherungsrecht", pdf_filename)

            if self.state.is_known(decision_id):
                continue

            # Build full URL
            if not link.startswith("http"):
                link = BASE_URL + link

            # Parse date
            item_date_str = item.get("Date", "")
            item_date = parse_date(item_date_str)

            if since_date and item_date and item_date < since_date:
                continue

            stub = {
                "decision_id": decision_id,
                "docket_number": pdf_filename,
                "decision_date": item_date_str,
                "url": link,
                "title": title,
            }
            yield stub

    def fetch_decision(self, stub: dict) -> Decision | None:
        """Download PDF and extract text for a single insurance decision."""
        url = stub.get("url", "")
        docket = stub["docket_number"]
        title = stub.get("title", docket)
        decision_id = stub.get("decision_id", make_decision_id("finma_versicherungsrecht", docket))

        if not url:
            logger.warning(f"[finma_vr] No URL for {docket}")
            return None

        # Download PDF
        try:
            response = self.get(url)
            pdf_data = response.content
            if len(pdf_data) > MAX_PDF_SIZE:
                logger.warning(f"[finma_vr] PDF too large ({len(pdf_data)} bytes) for {docket}")
                return None

        except Exception as e:
            logger.error(f"[finma_vr] Failed to download {docket}: {e}")
            return None

        # Extract text from PDF
        full_text = _extract_pdf_text(pdf_data)
        if not full_text or len(full_text.strip()) < pdf_ocr.MIN_TEXT_CHARS:
            # Image-only scans (2 of 2,585 on the portal, 2026-09-24): OCR before
            # giving up, as elcom/postcom/eschk do.
            full_text = pdf_ocr.ocr_pdf_bytes(pdf_data)
            if len(full_text) < pdf_ocr.MIN_TEXT_CHARS:
                logger.warning(
                    f"[finma_vr] No text extracted from {docket} "
                    f"({len(pdf_data)} bytes PDF, OCR included) — cached as gap for "
                    f"{self.state.GAP_TTL_DAYS} days"
                )
                self.state.mark_gap(decision_id)
                return None

        full_text = self.clean_text(full_text)

        # Parse title for canton and language hints
        parsed = _parse_title(title)

        # Detect language — prefer title hint, fall back to text detection
        lang = parsed["language_code"] or detect_language(full_text)

        # Parse decision date
        decision_date = parse_date(stub.get("decision_date", ""))

        # FINMA is a federal authority — always CH regardless of insurer location
        canton = "CH"

        # Citations from full text
        citations = extract_citations(full_text)

        decision = Decision(
            decision_id=decision_id,
            court="finma_versicherungsrecht",
            canton=canton,
            docket_number=docket,
            decision_date=decision_date,
            language=lang,
            title=title,
            legal_area="Versicherungsrecht",
            full_text=full_text,
            source_url=url,
            pdf_url=url,
            cited_decisions=citations,
            scraped_at=datetime.now(timezone.utc),
        )
        return decision


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Scrape FINMA Versicherungsrechtliche Entscheide")
    parser.add_argument("--since", type=str, help="Start date YYYY-MM-DD")
    parser.add_argument("--max", type=int, default=5, help="Max decisions")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    since = date.fromisoformat(args.since) if args.since else None
    scraper = FINMAVersicherungsrechtScraper()
    decisions = scraper.run(since_date=since, max_decisions=args.max)
    scraper.mark_run_complete(decisions)
    for d in decisions:
        print(f"  {d.decision_id}  {d.decision_date}  {d.language}  {d.title[:60]}")
    print(f"\nScraped {len(decisions)} FINMA insurance decisions")
