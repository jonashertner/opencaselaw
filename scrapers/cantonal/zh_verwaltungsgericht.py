"""
Zürich Verwaltungsgericht Scraper — DjiK system (vgrzh.djiktzh.ch)
====================================================================
Scrapes administrative court decisions from the DjiK/Omnis-based
decision database at vgrzh.djiktzh.ch.

Architecture:
- POST to /cgi-bin/nph-omniscgi.exe with form data → HTML result list
- Year-by-year iteration (cGeschaeftsjahr), 100 results per page
- Response: HTML tables with entscheid rows containing docket number,
  chamber, title, regeste, date+type
- Individual decisions: full HTML pages (no PDFs needed)
- Two-step: parse trefferliste → fetch individual HTML pages → extract text

Coverage:
- Verwaltungsgericht des Kantons Zürich
- Published since ~1996 (nearly all decisions per publication policy)
- ~1,000 decisions/year × 25+ years = ~25,000 decisions

API endpoint (DjiK/Omnis):
  POST https://vgrzh.djiktzh.ch/cgi-bin/nph-omniscgi.exe
  Key form fields: Schema=ZH_VG_WEB, cGeschaeftsjahr, nAnzahlTrefferProSeite=100

Reference: NeueScraper ZH_Verwaltungsgericht.py (Scrapy-based)
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
    parse_date,
)

logger = logging.getLogger(__name__)


# ============================================================
# Constants
# ============================================================

HOST = "https://vgrzh.djiktzh.ch"
SEARCH_URL = HOST + "/cgi-bin/nph-omniscgi.exe"

TREFFER_PRO_SEITE = 100
# cGeschaeftsjahr is the FILING year (the docket's own year segment), not the
# decision year: SR.1995.00044 was decided 1996-03-27 — inside the "published
# since ~1996" policy window — but filed in 1995, so a loop starting at 1996
# never queries cGeschaeftsjahr=1995 and the case is unreachable regardless of
# its decision date (parity audit 2026-11-09, one confirmed case). Earlier
# filing years with a post-1996 decision are a possible but unconfirmed
# further residual; reconfirm via the next parity_audit run before going
# back further.
START_YEAR = 1995

# Base form data for POST requests (from NeueScraper)
BASE_FORMDATA = {
    "OmnisPlatform": "WINDOWS",
    "WebServerUrl": "https://vgrzh.djiktzh.ch",
    "WebServerScript": "/cgi-bin/nph-omniscgi.exe",
    "OmnisLibrary": "JURISWEB",
    "OmnisClass": "rtFindinfoWebHtmlService",
    "OmnisServer": "JURISWEB,127.0.0.1:7000",
    "Schema": "ZH_VG_WEB",
    "Parametername": "WWW",
    "Aufruf": "search",
    "cTemplate": "standard/results/resultpage.fiw",
    "cTemplateSuchkriterien": "standard/results/searchcriteriarow.fiw",
    "cTemplate_SuchstringValidateError": "standard/results/resultpage.fiw",
    "cSprache": "GER",
    "cGeschaeftsart": "",
    "cGeschaeftsjahr": "",
    "cGeschaeftsnummer": "",
    "dEntscheiddatum": "",
    "bHasEntscheiddatumBis": "0",
    "dEntscheiddatumBis": "",
    "dPublikationsdatum": "",
    "bHasPublikationsdatumBis": "0",
    "dPublikationsdatumBis": "",
    "dErstPublikationsdatum": "",
    "bHasErstPublikationsdatumBis": "0",
    "dErstPublikationsdatumBis": "",
    "cSuchstringZiel": "F37_HTML",
    "cSuchstring": "",
    "nAnzahlTrefferProSeite": str(TREFFER_PRO_SEITE),
    "nSeite": "1",
}

# Regex patterns (from NeueScraper)
RE_DATUM = re.compile(r"(\d{2}\.\d{2}\.\d{4})")
RE_TYP = re.compile(r"(.+?)(?=\s+vom\s+\d{2}\.\d{2}\.\d{4})")


# ============================================================
# HTML parsing helpers
# ============================================================


def _clean_text(text: str) -> str:
    """Clean up extracted text: normalize whitespace, strip."""
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _extract_html_text(html: str) -> str:
    """
    Extract decision text from a full HTML decision page.

    The DjiK system returns full HTML pages. We extract all meaningful
    text content, skipping navigation elements.
    """
    if not html:
        return ""

    soup = BeautifulSoup(html, "html.parser")

    # Remove script and style elements
    for tag in soup(["script", "style", "meta", "link"]):
        tag.decompose()

    # Try to find the main content area
    # DjiK pages typically have the decision in the body or a main div
    # Based on NeueScraper, they just write the full HTML
    text = soup.get_text(separator="\n")

    # Clean up
    lines = []
    for line in text.split("\n"):
        line = line.strip()
        if line and len(line) > 1:
            lines.append(line)

    return "\n".join(lines)


def _parse_date_ddmmyyyy(text: str) -> date | None:
    """Parse DD.MM.YYYY date string."""
    if not text:
        return None
    m = RE_DATUM.search(text)
    if m:
        parts = m.group(1).split(".")
        try:
            return date(int(parts[2]), int(parts[1]), int(parts[0]))
        except (ValueError, IndexError):
            pass
    return None


# ============================================================
# Scraper
# ============================================================


class ZHVerwaltungsgerichtScraper(BaseScraper):
    """
    Scraper for Zürich Verwaltungsgericht decisions via DjiK system.

    Strategy:
    1. Iterate year by year from 1996 to present
    2. For each year: POST search form → parse HTML result table
    3. Paginate (100 results per page)
    4. For each result: extract metadata from table row
    5. Fetch individual HTML decision page → extract text
    6. State tracking prevents re-scraping

    Rate limit: 1.5s between requests (~25,000 decisions ≈ 50,000 requests ≈ 21 hours)
    """

    REQUEST_DELAY = 1.5
    TIMEOUT = 60
    MAX_ERRORS = 100

    @property
    def court_code(self) -> str:
        return "zh_verwaltungsgericht"

    def discover_new(self, since_date=None) -> Iterator[dict]:
        """
        Discover all VGR ZH decisions via year-by-year POST queries.

        Iterates from START_YEAR to current year, 100 results per page.
        """
        if since_date:
            if isinstance(since_date, str):
                since_date = parse_date(since_date)
            start_year = since_date.year
        else:
            start_year = START_YEAR

        current_year = date.today().year
        total_found = 0
        total_new = 0

        for year in range(start_year, current_year + 1):
            logger.info(f"VGR ZH: searching year {year}")

            try:
                year_found = 0
                year_new = 0

                for stub in self._search_year(year):
                    year_found += 1
                    total_found += 1
                    if not self.state.is_known(stub["decision_id"]):
                        year_new += 1
                        total_new += 1
                        yield stub

                logger.info(
                    f"VGR ZH year {year}: {year_found} found, {year_new} new"
                )
            except Exception as e:
                logger.error(f"VGR ZH year {year} failed: {e}", exc_info=True)

        logger.info(f"VGR ZH discovery complete: {total_found} total, {total_new} new")

    def _search_year(self, year: int | None = None) -> Iterator[dict]:
        """
        Search for all decisions in a given year, with pagination.
        """
        page = 1
        total_treffer = None

        while True:
            stubs, treffer_count = self._fetch_result_page(year, page)

            if total_treffer is None and treffer_count is not None:
                total_treffer = treffer_count
                logger.info(f"VGR ZH year {year}: {total_treffer} total results")

            if not stubs:
                break

            for stub in stubs:
                yield stub

            # Check if more pages
            if total_treffer is not None and page * TREFFER_PRO_SEITE < total_treffer:
                page += 1
                logger.info(
                    f"VGR ZH year {year}: fetching page {page} "
                    f"({page * TREFFER_PRO_SEITE}/{total_treffer})"
                )
            else:
                break

    def _fetch_result_page(
        self, year: int | None, page: int
    ) -> tuple[list[dict], int | None]:
        """
        Fetch a single page of search results.

        Returns (list of stubs, total treffer count or None).
        """
        formdata = dict(BASE_FORMDATA)
        formdata["nSeite"] = str(page)
        formdata["cGeschaeftsjahr"] = str(year) if year else ""

        resp = self.post(SEARCH_URL, data=formdata)
        html = resp.text

        if not html or len(html) < 100:
            logger.debug(f"VGR ZH year {year} page {page}: empty response")
            return [], None

        soup = BeautifulSoup(html, "html.parser")

        # Parse treffer count
        # HTML structure: Treffer <b>1</b> - <b>100</b> von <b>698</b>
        treffer_count = None
        import re as _re
        # Pattern: "von <b>NUMBER</b>" captures total
        m = _re.search(r'von\s*<b>\s*(\d+)\s*</b>', html)
        if m:
            treffer_count = int(m.group(1))
        # Only treat as empty if we found no treffer count
        # ("keine Treffer" appears in a hidden template on ALL pages)
        if treffer_count is None or treffer_count == 0:
            logger.info(f"VGR ZH year {year} page {page}: keine Treffer")
            return [], 0

        # Parse entscheid table rows
        # Structure: <table width="100%"><tr><td valign="top"><table>...entscheid rows...</table>
        stubs = []

        # Find entscheid tables - they're nested tables with decision data
        # Each entscheid is in a <table> inside <td valign="top">
        outer_tds = soup.find_all("td", attrs={"valign": "top"})
        for td in outer_tds:
            inner_tables = td.find_all("table", recursive=False)
            for table in inner_tables:
                stub = self._parse_result_row(table)
                if stub:
                    stubs.append(stub)

        # If we didn't find anything with the above approach, try broader
        if not stubs:
            all_tables = soup.find_all("table", attrs={"width": "100%"})
            for table in all_tables:
                rows = table.find_all("tr")
                for row in rows:
                    # Look for rows with links to decisions
                    links = row.find_all("a", href=True)
                    for link in links:
                        href = link.get("href", "")
                        if "getDocument" in href or "nF30_KEY" in href:
                            stub = self._parse_result_row_from_link(
                                row, link, table
                            )
                            if stub:
                                stubs.append(stub)

        return stubs, treffer_count

    def _parse_result_row(self, table) -> dict | None:
        """
        Parse an individual entscheid table from the result list.

        Structure (from NeueScraper):
        - .//a/@href → decision URL
        - .//a/font/text() → docket number (Geschäftsnummer)
        - .//tr[1]/td[4]/font/text() → Kammer
        - .//tr[2]/td[2]/b/text() → Titel
        - .//tr[2]/td[2]/text() → Regeste parts
        - .//td[@colspan='2']/i/text() → "Typ vom DD.MM.YYYY"
        """
        # Find the decision link
        link = table.find("a", href=True)
        if not link:
            return None

        href = link.get("href", "")
        if "getDocument" not in href and "nF30_KEY" not in href:
            return None

        # Build full URL
        if href.startswith("/"):
            url = HOST + href
        elif href.startswith("http"):
            url = href
        else:
            url = HOST + "/" + href

        # Docket number from link text
        num_elem = link.find("font")
        if num_elem:
            num = num_elem.get_text(strip=True)
        else:
            num = link.get_text(strip=True)

        if not num:
            return None

        # Kammer - from NeueScraper: .//tr[1]/td[4]/font/text()
        kammer = ""
        rows = table.find_all("tr")
        if rows:
            tds = rows[0].find_all("td")
            if len(tds) >= 4:
                font = tds[3].find("font")
                if font:
                    kammer = font.get_text(strip=True)

        # Titel and Regeste - from .//tr[2]/td[2]
        titel = ""
        regeste = ""
        if len(rows) >= 2:
            tds = rows[1].find_all("td")
            if len(tds) >= 2:
                b = tds[1].find("b")
                if b:
                    titel = b.get_text(strip=True)
                # Regeste: all direct text nodes in td[2]
                regeste_parts = []
                for child in tds[1].children:
                    if isinstance(child, str):
                        text = child.strip()
                        if text:
                            regeste_parts.append(text)
                regeste = " ".join(regeste_parts)

        # Date + Type - from .//td[@colspan='2']/i/text()
        edatum_str = ""
        entscheidart = ""
        i_tag = table.find("td", attrs={"colspan": "2"})
        if i_tag:
            italic = i_tag.find("i")
            if italic:
                datum_typ_text = italic.get_text(strip=True)
            else:
                datum_typ_text = i_tag.get_text(strip=True)
        else:
            # Fallback: search for italic text anywhere
            italic = table.find("i")
            datum_typ_text = italic.get_text(strip=True) if italic else ""

        if datum_typ_text:
            # Extract date
            dm = RE_DATUM.search(datum_typ_text)
            if dm:
                edatum_str = dm.group(1)

            # Extract type (everything before "vom DD.MM.YYYY")
            tm = RE_TYP.search(datum_typ_text)
            if tm:
                entscheidart = tm.group(1).strip()

        edatum = _parse_date_ddmmyyyy(edatum_str)
        if not edatum:
            logger.warning(f"VGR ZH no date for {num}: {datum_typ_text!r}")
            return None

        # Build decision ID
        decision_id = make_decision_id("zh_verwaltungsgericht", num)

        return {
            "decision_id": decision_id,
            "docket_number": num,
            "decision_date": edatum,
            "kammer": kammer,
            "title": titel,
            "regeste": regeste.strip(),
            "entscheidart": entscheidart,
            "url": url,
            "source_url": url,
        }

    def _parse_result_row_from_link(self, row, link, parent_table) -> dict | None:
        """
        Fallback parser: extract what we can from a row containing a decision link.
        """
        href = link.get("href", "")
        if href.startswith("/"):
            url = HOST + href
        elif href.startswith("http"):
            url = href
        else:
            url = HOST + "/" + href

        # Try to extract docket number
        font = link.find("font")
        num = font.get_text(strip=True) if font else link.get_text(strip=True)
        if not num or len(num) < 3:
            return None

        # Try to find date in surrounding text
        row_text = row.get_text()
        edatum = _parse_date_ddmmyyyy(row_text)
        if not edatum:
            # Try parent table
            parent_text = parent_table.get_text() if parent_table else ""
            edatum = _parse_date_ddmmyyyy(parent_text)

        if not edatum:
            logger.warning(f"VGR ZH fallback: no date for {num}")
            return None

        decision_id = make_decision_id("zh_verwaltungsgericht", num)

        return {
            "decision_id": decision_id,
            "docket_number": num,
            "decision_date": edatum,
            "kammer": "",
            "title": "",
            "regeste": "",
            "entscheidart": "",
            "url": url,
            "source_url": url,
        }

    def fetch_decision(self, stub: dict) -> Decision | None:
        """
        Fetch an individual decision HTML page and extract text.

        The DjiK system serves decisions as full HTML pages.
        """
        url = stub["url"]
        num = stub["docket_number"]

        try:
            resp = self.get(url, timeout=self.TIMEOUT)
        except Exception as e:
            logger.warning(f"VGR ZH fetch failed for {num}: {e}")
            return None

        html = resp.text
        if not html or len(html) < 100:
            logger.warning(f"VGR ZH empty page for {num}")
            return None

        # Extract text from HTML
        full_text = _extract_html_text(html)
        if not full_text or len(full_text) < 30:
            logger.warning(
                f"VGR ZH text extraction short for {num}: "
                f"{len(full_text or '')} chars"
            )
            if not full_text:
                full_text = f"[HTML text extraction failed for {num}]"

        # Language detection
        language = detect_language(full_text) if len(full_text) > 100 else "de"

        # Title
        title = stub.get("title") or f"Verwaltungsgericht ZH — {num}"

        # Regeste
        regeste = stub.get("regeste") or None

        # Decision type
        decision_type = stub.get("entscheidart") or None

        # Chamber
        chamber = stub.get("kammer") or None

        return Decision(
            decision_id=stub["decision_id"],
            court="zh_verwaltungsgericht",
            canton="ZH",
            chamber=chamber,
            docket_number=num,
            decision_date=stub["decision_date"],
            language=language,
            title=title,
            regeste=regeste,
            full_text=full_text,
            source_url=stub["source_url"],
            pdf_url=None,
            decision_type=decision_type,
            cited_decisions=(
                extract_citations(full_text) if len(full_text) > 200 else []
            ),
            external_id=f"zh_vgr_{num}",
        )
