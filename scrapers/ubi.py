"""
UBI Scraper (Unabhängige Beschwerdeinstanz für Radio und Fernsehen)
===================================================================

Scrapes published decisions from the Swiss Independent Complaints Authority
for Radio and Television (UBI) at ubi.admin.ch.

Architecture:
- TYPO3 CMS with tx_ubidb_list extension
- Single listing page at /de/entscheide with paginated AJAX results
- 10 decisions per page: 668 decisions on about 67 pages (2026-09-15)
- Each page shows a table with rich metadata per decision
- All decisions are PDFs, two URL patterns:
  - /inhalte/entscheide/b_NNNN.pdf (main pattern)
  - /fileadmin/user_upload/b_NNNN.pdf (some older decisions)
- Some decisions share a single PDF (joint decisions, e.g. b_998_1017_1021_1026.pdf)
- Pagination uses TYPO3 cHash tokens; scraper extracts "Naechster" link from each page

Metadata per decision:
- Decision number (b.NNNN)
- Description (medium, broadcaster, program, broadcast date)
- Outcome (Gutgeheissen, Abgewiesen, Teilweise gutgeheissen, Nicht eintreten)
- Decision date (DD.MM.YYYY)
- Language (Deutsch, Franzoesisch, Italienisch, Romanisch, Englisch)
- Medium (Fernsehen, Radio, Online, Teletext)
- Broadcaster (SRF, RTS, RSI, RTR, etc.)
- Program name
- Complaint type (Popular-/Individualbeschwerde, Aufsichtsbeschwerde)
- Legal provisions (RTVG articles)
- Keywords

Coverage: ~777 decisions (1992-present)
Rate limiting: 2.0 seconds (admin.ch government site)
"""
from __future__ import annotations

import logging
import os
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

BASE_URL = "https://www.ubi.admin.ch"
LISTING_URL = f"{BASE_URL}/de/entscheide/entscheide-suchen-sie-mit-suchkriterien"

# Map UBI's full language names to ISO codes
LANG_MAP = {
    "deutsch": "de",
    "französisch": "fr",
    "franzoesisch": "fr",
    "italienisch": "it",
    "romanisch": "rm",
    "rätoromanisch": "rm",
    "englisch": "de",  # English-language complaints handled in German proceedings
}

# Decision number pattern in link text: b.NNNN or b.NNN
DECISION_NUM_PATTERN = re.compile(r"b\.(\d+)")


def _extract_pdf_text(data: bytes) -> str:
    """Extract text from PDF bytes using fitz (PyMuPDF) with pdfplumber fallback."""
    text = ""
    try:
        import fitz

        doc = fitz.open(stream=data, filetype="pdf")
        text = "\n\n".join(p.get_text() for p in doc)
    except ImportError:
        pass
    except Exception as e:
        logger.warning(f"[ubi] fitz extraction failed: {e}")

    if not text.strip():
        try:
            import io
            import pdfplumber

            with pdfplumber.open(io.BytesIO(data)) as pdf:
                text = "\n\n".join(p.extract_text() or "" for p in pdf.pages)
        except ImportError:
            pass
        except Exception as e:
            logger.warning(f"[ubi] pdfplumber extraction failed: {e}")

    return text


def _parse_language(lang_text: str) -> str:
    """Convert UBI language name to ISO code."""
    return LANG_MAP.get(lang_text.strip().lower(), "de")


class UBIScraper(BaseScraper):
    """Scraper for UBI (Independent Complaints Authority for Radio and Television)."""

    REQUEST_DELAY = 2.0
    TIMEOUT = 60

    @property
    def court_code(self) -> str:
        return "ubi"

    def _fetch_page(self, url: str) -> BeautifulSoup:
        """Fetch a listing page and return parsed HTML."""
        response = self.get(url)
        return BeautifulSoup(response.text, "html.parser")

    def _parse_decision_rows(self, soup: BeautifulSoup) -> list[dict]:
        """Parse decision entries from a listing page.

        Each decision is a <tr> row in the results table with 7 cells:
        1. Beschreibung/Link Entscheid (description + PDF link)
        2. Medium (TV/Radio/Online icons)
        3. Beschluss/Datum/Sprache (outcome, date, language)
        4. Veranstalter + Sendung/Publikation (broadcaster + program)
        5. Beschwerdetyp (complaint type)
        6. Bestimmungen (legal provisions)
        7. Schluesselwoerter (keywords)
        """
        entries = []
        # Find the results table
        tables = soup.find_all("table")
        if not tables:
            return entries

        for table in tables:
            for row in table.find_all("tr"):
                cells = row.find_all("td")
                if len(cells) < 3:
                    continue

                entry = self._parse_row(cells)
                if entry:
                    entries.append(entry)

        return entries

    def _parse_row(self, cells) -> dict | None:
        """Parse a single table row into a decision stub dict."""
        # Cell 0: Description + PDF link
        desc_cell = cells[0]
        pdf_link = desc_cell.find("a", href=True)
        if not pdf_link:
            return None

        href = pdf_link["href"]
        if not href.endswith(".pdf"):
            return None

        # Extract decision number from link text (e.g., "b.1057")
        link_text = pdf_link.get_text(strip=True)
        num_match = DECISION_NUM_PATTERN.search(link_text)
        if not num_match:
            # Also try multi-number format like "b.1009_b.1010_b.1011_b.1012"
            # Use the first number found
            all_nums = re.findall(r"\d+", link_text)
            if all_nums:
                decision_num = all_nums[0]
            else:
                return None
        else:
            decision_num = num_match.group(1)

        docket_number = f"b.{decision_num}"

        # Build PDF URL
        if href.startswith("http"):
            pdf_url = href
        else:
            pdf_url = urljoin(BASE_URL, href)

        # Get all text in the cell, minus the label "Beschreibung/ Link Entscheid"
        full_desc = desc_cell.get_text(separator=" ", strip=True)
        # Remove the label prefix
        full_desc = re.sub(
            r"^Beschreibung/?[\s]*Link[\s]*Entscheid\s*", "", full_desc
        ).strip()
        # Remove the PDF link text suffix like "(de, pdf)"
        full_desc = re.sub(r"\s*\((?:de|fr|it|rm|en),\s*pdf\)\s*$", "", full_desc).strip()
        # Remove the decision number link text
        full_desc = re.sub(r"\s*b\.\d+(?:_b\.\d+)*\s*$", "", full_desc).strip()
        # Also handle multi-number format
        full_desc = re.sub(r"\s*b\.\d+(?:_\d+)*\s*$", "", full_desc).strip()

        # Cell 1: Medium (images with alt text)
        medium = ""
        if len(cells) > 1:
            medium_imgs = cells[1].find_all("img")
            if medium_imgs:
                medium = ", ".join(
                    img.get("alt", "").strip()
                    for img in medium_imgs
                    if img.get("alt")
                )

        # Cell 2: Outcome, Date, Language
        outcome = ""
        decision_date = ""
        language_text = ""
        if len(cells) > 2:
            verdict_cell = cells[2]
            # The cell contains "Beschluss/ Datum/ Sprache" as label,
            # then a div/span with the actual values
            inner_div = verdict_cell.find(["div", "span"])
            if inner_div:
                texts = [t.strip() for t in inner_div.stripped_strings]
            else:
                # Fallback: get all text, remove label
                cell_text = verdict_cell.get_text(separator="\n", strip=True)
                cell_text = re.sub(
                    r"^Beschluss/?[\s]*Datum/?[\s]*Sprache\s*", "", cell_text
                ).strip()
                texts = [t.strip() for t in cell_text.split("\n") if t.strip()]

            if len(texts) >= 3:
                outcome = texts[0]
                decision_date = texts[1]
                language_text = texts[2]
            elif len(texts) == 2:
                # Sometimes outcome is missing
                decision_date = texts[0]
                language_text = texts[1]
            elif len(texts) == 1:
                # Try to parse as date
                decision_date = texts[0]

        # Cell 3: Broadcaster and Program
        broadcaster = ""
        program = ""
        if len(cells) > 3:
            cell_text = cells[3].get_text(separator="\n", strip=True)
            # Parse "Veranstalter\nSRF\nSendung/ Publikation\nTagesschau"
            parts = [t.strip() for t in cell_text.split("\n") if t.strip()]
            for i, part in enumerate(parts):
                if part == "Veranstalter" and i + 1 < len(parts):
                    broadcaster = parts[i + 1]
                elif "Sendung" in part and i + 1 < len(parts):
                    program = parts[i + 1]

        # Cell 4: Complaint type
        complaint_type = ""
        if len(cells) > 4:
            cell_text = cells[4].get_text(separator="\n", strip=True)
            parts = [t.strip() for t in cell_text.split("\n") if t.strip()]
            for i, part in enumerate(parts):
                if part == "Beschwerdetyp" and i + 1 < len(parts):
                    complaint_type = parts[i + 1]

        # Cell 5: Legal provisions
        provisions = []
        if len(cells) > 5:
            for a in cells[5].find_all("a", href=True):
                prov_text = a.get_text(strip=True)
                if prov_text:
                    provisions.append(prov_text)

        # Cell 6: Keywords
        keywords = []
        if len(cells) > 6:
            for li in cells[6].find_all("li"):
                kw = li.get_text(strip=True)
                if kw:
                    keywords.append(kw)

        # Build language code
        lang = _parse_language(language_text) if language_text else ""

        # Also check the PDF link text for language hint
        if not lang:
            link_suffix = desc_cell.get_text(strip=True)
            lang_hint = re.search(r"\((de|fr|it|rm|en),\s*pdf\)", link_suffix)
            if lang_hint:
                lang = lang_hint.group(1)
                if lang == "en":
                    lang = "de"

        # Build title from description
        title = full_desc if full_desc else f"UBI Entscheid {docket_number}"

        return {
            "docket_number": docket_number,
            "decision_date": decision_date,
            "pdf_url": pdf_url,
            "title": title,
            "outcome": outcome,
            "language": lang or "de",
            "medium": medium,
            "broadcaster": broadcaster,
            "program": program,
            "complaint_type": complaint_type,
            "provisions": provisions,
            "keywords": keywords,
        }

    def _get_next_page_url(self, soup: BeautifulSoup) -> str | None:
        """Extract the 'Naechster' (Next) pagination link URL."""
        # Look for "Nächster" link in pagination
        for a in soup.find_all("a", href=True):
            text = a.get_text(strip=True)
            if text in ("Nächster", "Naechster", "Next"):
                href = a["href"]
                if href.startswith("javascript:"):
                    continue
                if href.startswith("http"):
                    return href
                return urljoin(BASE_URL, href)
        return None

    # Safety cap for the "Naechster" chain (about 67 pages on 2026-09-15).
    MAX_PAGES = 200

    def discover_new(self, since_date=None) -> Iterator[dict]:
        """Discover UBI decisions by paginating through the listing, newest first.

        The nightly run stops at the first page after page 1 whose entries are all
        known. A decision listed behind known ones is only reached by a full walk,
        which OCL_SCRAPER_RESCAN_ALL=1 enables: on 2026-09-15 the listing held 668
        decisions and production 667; the missing one, b.980 of 16.05.2024, sat behind
        known pages. A full walk takes about 14 minutes, so it is not the nightly default.
        """
        rescan_all = bool(os.environ.get("OCL_SCRAPER_RESCAN_ALL"))
        url = LISTING_URL
        page_num = 1
        total_found = 0
        seen_dockets = set()

        while url:
            logger.info(f"[ubi] Fetching page {page_num}: {url}")
            try:
                soup = self._fetch_page(url)
            except Exception as e:
                logger.error(f"[ubi] Failed to fetch page {page_num}: {e}")
                break

            entries = self._parse_decision_rows(soup)
            if not entries:
                logger.info(f"[ubi] No entries found on page {page_num}, stopping")
                break

            new_on_page = 0
            all_known = True
            for entry in entries:
                docket = entry["docket_number"]

                # Skip duplicates within this run (multi-docket PDFs can appear
                # multiple times on the listing)
                if docket in seen_dockets:
                    continue
                seen_dockets.add(docket)

                decision_id = make_decision_id("ubi", docket)
                if self.state.is_known(decision_id):
                    continue

                all_known = False

                # Filter by since_date if provided
                if since_date and entry.get("decision_date"):
                    parsed = parse_date(entry["decision_date"])
                    if parsed and parsed < since_date:
                        continue

                new_on_page += 1
                total_found += 1
                yield entry

            logger.info(
                f"[ubi] Page {page_num}: {len(entries)} entries, "
                f"{new_on_page} new, {total_found} total new so far"
            )

            # If all decisions on this page were known and we're doing
            # incremental scraping, we can stop early (never on a full rescan)
            if all_known and page_num > 1 and not rescan_all:
                logger.info(f"[ubi] All entries on page {page_num} already known, stopping")
                break

            if page_num >= self.MAX_PAGES:
                logger.warning(f"[ubi] Stopping at the {self.MAX_PAGES}-page safety cap")
                break

            # Get next page URL
            next_url = self._get_next_page_url(soup)
            if next_url and next_url != url:
                url = next_url
                page_num += 1
            else:
                logger.info(f"[ubi] No more pages after page {page_num}")
                break

    def fetch_decision(self, stub: dict) -> Decision | None:
        """Download PDF and extract decision text."""
        pdf_url = stub["pdf_url"]
        docket = stub["docket_number"]

        try:
            response = self.get(pdf_url)
        except Exception as e:
            logger.error(f"[ubi] Failed to download PDF for {docket}: {e}")
            return None

        if response.status_code != 200:
            logger.warning(f"[ubi] HTTP {response.status_code} for {docket} PDF")
            return None

        full_text = _extract_pdf_text(response.content)
        if not full_text or len(full_text.strip()) < 50:
            logger.warning(
                f"[ubi] No text extracted from {docket} "
                f"({len(response.content)} bytes PDF)"
            )
            return None

        full_text = self.clean_text(full_text)

        # Use language from listing metadata, or detect from text
        lang = stub.get("language", "")
        if not lang:
            lang = detect_language(full_text)

        decision_date = parse_date(stub.get("decision_date", ""))

        # Build title with metadata
        title = stub.get("title", "")

        # Build decision_type from outcome and complaint type
        decision_type = stub.get("outcome", "")
        complaint_type = stub.get("complaint_type", "")
        if complaint_type:
            decision_type = f"{decision_type} ({complaint_type})" if decision_type else complaint_type

        # Legal area: always Medienrecht (media law) for UBI
        legal_area = "Medienrecht"

        # Build source URL pointing to the listing page
        source_url = f"{LISTING_URL}"

        return Decision(
            decision_id=make_decision_id("ubi", docket),
            court="ubi",
            canton="CH",
            docket_number=docket,
            decision_date=decision_date,
            language=lang,
            title=title,
            legal_area=legal_area,
            decision_type=decision_type,
            full_text=full_text,
            source_url=source_url,
            pdf_url=pdf_url,
            cited_decisions=extract_citations(full_text),
            scraped_at=datetime.now(timezone.utc),
        )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Scrape UBI decisions")
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
    scraper = UBIScraper()
    decisions = scraper.run(since_date=since, max_decisions=args.max)
    scraper.mark_run_complete(decisions)
    for d in decisions:
        print(
            f"  {d.decision_id}  {d.decision_date}  "
            f"{len(d.full_text)} chars  {d.language}  {d.title[:80]}"
        )
    print(f"\nScraped {len(decisions)} UBI decisions")
