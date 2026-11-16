"""
Bundespatentgericht (BPatGer) — Federal Patent Court Scraper
==============================================================

Scrapes decisions from bundespatentgericht.ch (TYPO3 CMS).

Architecture:
- TYPO3-based website at www.bundespatentgericht.ch
- POST form search to get results listing
- Each result links to a detail page with structured <table> metadata
- PDF links available on detail pages
- Relatively small corpus (few hundred decisions)

Endpoints:
- Search: /rechtsprechung/datenbankabfrage/?tx_iscourtcases_entscheidesuche[action]=suche...
- Detail: /rechtsprechung/datenbankabfrage/{slug}
- PDFs: linked from detail page table

Coverage: All available decisions (small court, few hundred total)
Rate limiting: 3 seconds
"""

from __future__ import annotations

import io
import logging
import re
from datetime import date, datetime, timezone
from typing import Iterator

from bs4 import BeautifulSoup

from base_scraper import BaseScraper
from scrapers._date_identity import DateAwareIdentityMixin
from models import (
    Decision,
    detect_language,
    make_decision_id,
    parse_date,
)

logger = logging.getLogger(__name__)

HOST = "https://www.bundespatentgericht.ch"


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
SEARCH_URL = (
    f"{HOST}/rechtsprechung/datenbankabfrage/"
    "?tx_iscourtcases_entscheidesuche[action]=suche"
    "&tx_iscourtcases_entscheidesuche[controller]=Entscheide"
    "&cHash=51983dbfa80eb8fd29d1420cc573e72c"
)

# Form data for TYPO3 search — searches all decisions
FORM_DATA = {
    "tx_iscourtcases_entscheidesuche[__referrer][@extension]": "IsCourtcases",
    "tx_iscourtcases_entscheidesuche[__referrer][@controller]": "Entscheide",
    "tx_iscourtcases_entscheidesuche[__referrer][@action]": "suche",
    "tx_iscourtcases_entscheidesuche[__referrer][arguments]": (
        "YToyOntzOjY6ImFjdGlvbiI7czo1OiJzdWNoZSI7czoxMDoiY29udHJvbGxlciI7czoxMDoiRW50c2NoZWlkZSI7fQ=="
        "34f3c6454ebbcf14c77c24f9fce4c741960727ed"
    ),
    "tx_iscourtcases_entscheidesuche[__referrer][@request]": (
        '{"@extension":"IsCourtcases","@controller":"Entscheide","@action":"suche"}'
        "176dd2db7e68517d648f8483d6068ac8c0851bf6"
    ),
    "tx_iscourtcases_entscheidesuche[match-fall_nummer]": "",
    "tx_iscourtcases_entscheidesuche[match-titel_kurz]": "",
    "tx_iscourtcases_entscheidesuche[match-pdfinhalt]": "",
    "tx_iscourtcases_entscheidesuche[range-urteils_datum-from]": "",
    "tx_iscourtcases_entscheidesuche[range-urteils_datum-to]": "",
    "tx_iscourtcases_entscheidesuche[multi-verfahren_typ]": "",
    "tx_iscourtcases_entscheidesuche[multi-verfahren_art]": "",
    "tx_iscourtcases_entscheidesuche[multi-status]": "",
    "tx_iscourtcases_entscheidesuche[join-gegenstand]": "",
    "tx_iscourtcases_entscheidesuche[join-technischesgebiet]": "",
    "tx_iscourtcases_entscheidesuche[formsearch]": "1",
}


class BPatGerScraper(DateAwareIdentityMixin, BaseScraper):
    """
    Scraper for Bundespatentgericht decisions.

    Simple architecture: POST search form → listing → detail pages.
    """

    REQUEST_DELAY = 3.0
    TIMEOUT = 30

    @property
    def court_code(self) -> str:
        return "bpatger"

    def discover_new(self, since_date=None) -> Iterator[dict]:
        """
        Discover BPatGer decisions from listing pages.

        The portal has two stable decision listings:
          /rechtsprechung/aktuelle-entscheide  — most-recent ~10 judgments
          /rechtsprechung/leitentscheide       — landmark rulings
        Historical year-based archive pages (entscheide-YYYY/…) were
        discontinued upstream; probing them returned 404s and wasted
        ~11 min/run in the daily scrape. Audit on 2026-04-24 confirmed
        the corpus is complete (189 unique dockets on our side == 189
        unique dockets on portal, across 227 detail pages that include
        language/stage variants the scraper already dedups by docket).

        Correction 2026-11-16: many of those extra detail pages are separate
        rulings under one docket (e.g. S2017_003 of 2017-01-31 and of
        2017-04-25), which docket-keyed ids dropped. Identity is now
        date-aware (scrapers/_date_identity.py): the listing date only
        pre-filters, the detail page's Entscheiddatum decides in
        fetch_decision. Legacy docket-keyed mode until the sidecar is seeded.
        """
        self._load_known_dates()
        seen_urls: set[str] = set()

        pages = [
            f"{HOST}/rechtsprechung/aktuelle-entscheide",
            f"{HOST}/rechtsprechung/leitentscheide",
        ]

        for page_url in pages:
            try:
                response = self.get(page_url)
                if response.status_code != 200:
                    continue
                yield from self._stubs_from_listing(response.text, seen_urls)
            except Exception as e:
                logger.debug(f"BPatGer listing page {page_url}: {e}")
                continue

    def discover_database(self) -> Iterator[dict]:
        """Every detail page of the portal's database search (an empty search
        lists all of them, 233 on 2026-11-16), through the same pre-filter.
        Used by backfill_docket_collisions.py to recover held dockets' second
        rulings; daily runs keep the two short listings above."""
        self._load_known_dates()
        response = self.post(SEARCH_URL, data=dict(FORM_DATA))
        if response.status_code != 200:
            raise RuntimeError(f"BPatGer database search: HTTP {response.status_code}")
        yield from self._stubs_from_listing(response.text, set())

    def _stubs_from_listing(self, html: str, seen_urls: set[str]) -> Iterator[dict]:
        soup = BeautifulSoup(html, "html.parser")
        # Find all links to decision detail pages
        for link in soup.find_all("a", href=True):
            href = link.get("href", "")
            if "entscheidanzeige" not in href:
                continue
            # Normalize URL
            url = f"{HOST}{href}" if not href.startswith("http") else href
            if url in seen_urls:
                continue
            seen_urls.add(url)

            listing_text = link.get_text(strip=True)

            # Extract docket and date from listing text (e.g.
            # "S2023_011:\n04.12.2025:...") to skip held rulings without
            # fetching the detail page
            docket_from_listing = None
            listing_date = None
            if listing_text:
                first_line = listing_text.split(":")[0].strip()
                if first_line and "_" in first_line:
                    docket_from_listing = first_line
                dm = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", listing_text)
                if dm:
                    listing_date = f"{dm.group(3)}-{dm.group(2)}-{dm.group(1)}"

            if docket_from_listing and self._listing_held(
                    docket_from_listing, listing_date):
                continue

            yield {"url": url, "listing_text": listing_text}

    def _listing_held(self, docket: str, listing_date: str | None) -> bool:
        """Pre-filter on the listing line, without claiming an id (the detail
        page decides). Legacy: skip any held docket, as before. Date-aware:
        skip only the exact held (docket, date) pair, or a held docket whose
        listing carries no date."""
        base = make_decision_id("bpatger", docket)
        if self._known_dates is None:
            return self.state.is_known(base)
        if not self.state.is_known(base):
            return False
        if not listing_date:
            return True
        return f"{base}|{listing_date}" in self._known_dates

    def fetch_decision(self, stub: dict) -> Decision | None:
        """
        Fetch detail page for a single BPatGer decision.

        XPath selectors for decision detail pages:
        - Docket: //table[@class='tx-is-courtcases']/tr/td[contains(.,'Prozessnummer')]/following-sibling::td
        - Date: //table[@class='tx-is-courtcases']/tr/td[contains(.,'Entscheiddatum')]/following-sibling::td
        - PDF: //table[@class='tx-is-courtcases']/tr/td[contains(.,'Entscheid als PDF')]/following-sibling::td/a/@href
        - Type: //table[@class='tx-is-courtcases']/tr/td[contains(.,'Art des Verfahrens')]/following-sibling::td
        - Status: //table[@class='tx-is-courtcases']/tr/td[contains(.,'Status')]/following-sibling::td
        - Title: //div[@class='klassifizierung']/h2[contains(.,'Stichwort')]/following-sibling::p
        - Subject: //div[@class='klassifizierung']/h2[contains(.,'Gegenstand')]/following-sibling::*/li
        """
        try:
            response = self.get(stub["url"])
            soup = BeautifulSoup(response.text, "html.parser")

            # Helper to extract table cell content
            def get_cell(label: str) -> str | None:
                # Try class selector first, then fallback to any table with "Prozessnummer"
                table = soup.find("table", class_="tx-is-courtcases")
                if not table:
                    for t in soup.find_all("table"):
                        if "Prozessnummer" in t.get_text():
                            table = t
                            break
                if not table:
                    return None
                for row in table.find_all("tr"):
                    cells = row.find_all("td")
                    if len(cells) >= 2 and label in cells[0].get_text():
                        return cells[1].get_text(strip=True)
                return None

            def get_cell_link(label: str) -> str | None:
                table = soup.find("table", class_="tx-is-courtcases")
                if not table:
                    for t in soup.find_all("table"):
                        if "Prozessnummer" in t.get_text():
                            table = t
                            break
                if not table:
                    return None
                for row in table.find_all("tr"):
                    cells = row.find_all("td")
                    if len(cells) >= 2 and label in cells[0].get_text():
                        link = cells[1].find("a")
                        return link.get("href") if link else None
                return None

            # Extract all metadata
            docket = get_cell("Prozessnummer")
            if not docket:
                logger.warning(f"No docket number found at {stub['url']}")
                return None

            decision_date_str = get_cell("Entscheiddatum")
            decision_date_parsed = parse_date(decision_date_str) if decision_date_str else None
            if not decision_date_parsed:
                logger.warning(f"[bpatger] No date for {docket}")

            # Date-aware identity: the detail page's Entscheiddatum decides
            # whether this is a held ruling (skip), a new docket (plain id)
            # or a second ruling under a held docket (-D id).
            classified = self._stub_filter(
                {"docket_number": docket, "decision_date": decision_date_parsed})
            if classified is None:
                return None
            decision_id = classified["decision_id"]

            pdf_href = get_cell_link("Entscheid als PDF")
            pdf_url = (
                pdf_href if pdf_href and pdf_href.startswith("http")
                else f"{HOST}{pdf_href}" if pdf_href
                else None
            )

            decision_type = get_cell("Art des Verfahrens")

            status = get_cell("Status") or ""
            entscheid_art = get_cell("Art des Entscheids") or ""
            bger_link = get_cell("Link Bundesgericht")
            appeal = f"{status} {entscheid_art}".strip()
            if bger_link:
                appeal += f" {bger_link}"

            # Title/Stichwort — try multiple selector strategies
            title = None
            for container in [soup.find("div", class_="klassifizierung"), soup]:
                if not container:
                    continue
                stichwort_h2 = container.find(
                    ["h2", "h3"],
                    string=lambda s: s and "Stichwort" in s,
                )
                if stichwort_h2:
                    p = stichwort_h2.find_next_sibling("p")
                    if p:
                        title = p.get_text(strip=True)
                        break
                    # Sometimes text is directly after the heading without <p>
                    nxt = stichwort_h2.next_sibling
                    if nxt and hasattr(nxt, 'get_text'):
                        title = nxt.get_text(strip=True)
                        break
                    elif nxt and isinstance(nxt, str) and nxt.strip():
                        title = nxt.strip()
                        break

            # Subject/Gegenstand
            subject = None
            for container in [soup.find("div", class_="klassifizierung"), soup]:
                if not container:
                    continue
                gegenstand_h2 = container.find(
                    ["h2", "h3"],
                    string=lambda s: s and "Gegenstand" in s,
                )
                if gegenstand_h2:
                    ul = gegenstand_h2.find_next_sibling()
                    if ul:
                        items = [li.get_text(strip=True) for li in ul.find_all("li")]
                        if items:
                            subject = ", ".join(items)
                            break

            # Try to extract full text from PDF
            full_text = ""
            if pdf_url:
                try:
                    pdf_resp = self.get(pdf_url)
                    if pdf_resp.status_code == 200 and len(pdf_resp.content) > 100:
                        full_text = _extract_pdf_text(pdf_resp.content)
                        logger.info(f"[bpatger] PDF text extracted: {len(full_text)} chars for {docket}")
                except Exception as e:
                    logger.warning(f"[bpatger] PDF extraction failed for {docket}: {e}")

            # Fallback to metadata if PDF extraction failed
            if len(full_text.strip()) < 100:
                full_text = f"{title or ''}\n\n{subject or ''}"

            lang = detect_language(full_text) if full_text.strip() else "de"

            decision = Decision(
                decision_id=decision_id,
                court="bpatger",
                canton="CH",
                docket_number=docket,
                decision_date=decision_date_parsed,
                language=lang,
                title=title,
                legal_area=subject,
                regeste=subject,
                full_text=self.clean_text(full_text) if full_text.strip() else "",
                decision_type=decision_type,
                appeal_info=appeal.strip() or None,
                source_url=stub["url"],
                pdf_url=pdf_url,
                cited_decisions=[],
                scraped_at=datetime.now(timezone.utc),
            )
            # Date-aware identity bookkeeping (no-op in legacy mode).
            self._mark_decision_date(decision)
            return decision

        except Exception as e:
            logger.error(f"Failed to fetch BPatGer detail page: {e}", exc_info=True)
            return None


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Scrape BPatGer decisions")
    parser.add_argument("--max", type=int, default=10, help="Max decisions")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    scraper = BPatGerScraper()
    decisions = scraper.run(max_decisions=args.max)
    scraper.mark_run_complete(decisions)
    print(f"Scraped {len(decisions)} BPatGer decisions")
