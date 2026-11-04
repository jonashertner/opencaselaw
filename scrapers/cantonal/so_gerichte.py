"""
Solothurn Courts Scraper (SO Gerichte)
=======================================
Scrapes court decisions from the Omnis/FindInfo platform at
gerichtsentscheide.so.ch.

Architecture:
- GET /cgi-bin/nph-omniscgi.exe (search with params) → result list HTML
- GET /cgi-bin/nph-omniscgi.exe (Aufruf=getMarkupDocument&nF30_KEY=...) → full decision HTML
- No authentication required
- Full decision text in <div class="WordSection1">

Pagination:
- nSeite (1-indexed), nAnzahlTrefferProSeite (100)
- Total count in "von {N} gefundenen Geschaft(en)"
- nF30_KEY links each result to its full document

Court instances: AK, BK, JK, OG, SC, SG, SK, ST, VS, VW, ZK
Total: ~9,087 decisions (1974-2026)
Platform: Omnis/FindInfo (JurisWeb)
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

HOST = "https://gerichtsentscheide.so.ch"
CGI_PATH = "/cgi-bin/nph-omniscgi.exe"
CGI_URL = HOST + CGI_PATH

RESULTS_PER_PAGE = 100

# Fixed CGI parameters
BASE_PARAMS = {
    "OmnisPlatform": "WINDOWS",
    "WebServerUrl": "https://gerichtsentscheide.so.ch",
    "WebServerScript": "/cgi-bin/nph-omniscgi.exe",
    "OmnisLibrary": "JURISWEB",
    "OmnisClass": "rtFindinfoWebHtmlService",
    "OmnisServer": "7001",
    "Schema": "JGWEB",
    "Parametername": "WEB",
}

# Regex patterns
RE_HIT_COUNT = re.compile(r"von\s+(\d+)\s+gefundenen")
RE_NF30_KEY = re.compile(r"nF30_KEY=(\d+)")
RE_DATE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")

_MONTHS_DE = {
    "Januar": 1, "Februar": 2, "März": 3, "April": 4,
    "Mai": 5, "Juni": 6, "Juli": 7, "August": 8,
    "September": 9, "Oktober": 10, "November": 11, "Dezember": 12,
}
_MONTH_NAMES = "|".join(_MONTHS_DE.keys())
# The ruling's own opening, "Urteil vom 4. Juli 2022" (the portal's text
# often loses the space: "Urteilvom4. Juli 2022"). A bare "vom <Datum>" is
# NOT enough: the first one in a social-insurance or administrative ruling is
# usually the decision under appeal ("Einspracheentscheid vom 21. Dezember
# 2017"), which is how 4,174 rows of the 2026-02 crawl came to carry the lower
# instance's date (user test 2026-11-04). The lookbehinds keep compounds such
# as "Einspracheentscheid" and narrative "Mit Verfügung vom …" from matching.
RE_LONG_DATE = re.compile(
    rf"(?<![A-Za-zÄÖÜäöü])(?<!Mit )(?<!mit )(?:Urteil|Beschluss|Entscheid|Verfügung)\s*vom\s*"
    rf"(\d{{1,2}})\.?\s*({_MONTH_NAMES})\s+(\d{{4}})"
)


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


def _extract_document_text(soup):
    """Extract full text from div.WordSection1."""
    content = soup.find("div", class_="WordSection1")
    if not content:
        content = soup.find("div", class_="Section1")
    if not content:
        # Fallback: find largest div
        best = None
        best_len = 0
        for div in soup.find_all("div"):
            tlen = len(div.get_text(strip=True))
            if tlen > best_len:
                best = div
                best_len = tlen
        if best and best_len > 500:
            content = best

    if not content:
        return ""

    paragraphs = []
    for p in content.find_all("p", class_="MsoNormal"):
        text = p.get_text(strip=True)
        if text:
            paragraphs.append(text)

    if paragraphs:
        return "\n\n".join(paragraphs)

    return content.get_text(separator="\n", strip=True)


class SOGerichteScraper(BaseScraper):
    """
    Scraper for Solothurn court decisions via Omnis/FindInfo.

    Strategy: paginate through all results using wildcard search,
    then fetch each decision's full text by nF30_KEY.
    Total: ~9,087 decisions.
    """

    REQUEST_DELAY = 2.0
    TIMEOUT = 60
    MAX_ERRORS = 50

    @property
    def court_code(self):
        return "so_gerichte"

    def discover_new(self, since_date=None) -> Iterator[dict]:
        if since_date and isinstance(since_date, str):
            since_date = date.fromisoformat(since_date)

        total_yielded = 0

        if since_date:
            # Search with date filter for incremental updates
            yield from self._discover_date_range(since_date, date.today())
        else:
            # Full scrape: iterate year by year to avoid session issues
            import datetime as dt
            current_year = dt.date.today().year
            for year in range(current_year, 1973, -1):
                logger.info(f"SO: searching year {year}")
                count = 0
                for stub in self._discover_year(year):
                    if not self.state.is_known(stub["decision_id"]):
                        total_yielded += 1
                        count += 1
                        yield stub
                logger.info(f"SO: year {year}: {count} new stubs")

        logger.info(f"SO: discovery complete: {total_yielded} new stubs")

    def _discover_year(self, year: int) -> Iterator[dict]:
        """Discover decisions for a specific year using date range."""
        date_from = f"01.01.{year}"
        date_to = f"31.12.{year}"
        yield from self._discover_date_range_str(date_from, date_to)

    def _discover_date_range(self, start: date, end: date) -> Iterator[dict]:
        """Discover decisions in a date range."""
        date_from = start.strftime("%d.%m.%Y")
        date_to = end.strftime("%d.%m.%Y")
        yield from self._discover_date_range_str(date_from, date_to)

    def _discover_date_range_str(self, date_from: str, date_to: str) -> Iterator[dict]:
        """Search with date range and paginate through results."""
        params = dict(BASE_PARAMS)
        params.update({
            "Aufruf": "validate",
            "cTemplate": "/simple/search_resulttable.html",
            "cSprache": "DE",
            "nSeite": "1",
            "nAnzahlTrefferProSeite": str(RESULTS_PER_PAGE),
            "bInstanzInt": "all",
            "cSuchWorte": "*",
            "dEntscheiddatum": date_from,
            "bHasEntscheiddatumBis": "1",
            "dEntscheiddatumBis": date_to,
        })

        try:
            r = self.get(CGI_URL, params=params)
        except Exception as e:
            logger.error(f"SO: search failed for {date_from}-{date_to}: {e}")
            return

        html = r.text
        total_hits = self._parse_hit_count(html)
        if not total_hits:
            return

        logger.info(f"SO: {date_from}-{date_to}: {total_hits} hits")

        # Parse page 1
        for stub in self._parse_result_page(html):
            yield stub

        # Paginate remaining pages
        total_pages = (total_hits + RESULTS_PER_PAGE - 1) // RESULTS_PER_PAGE
        for page in range(2, total_pages + 1):
            params["nSeite"] = str(page)
            params["nAnzahlTreffer"] = str(total_hits)
            try:
                r = self.get(CGI_URL, params=params)
                for stub in self._parse_result_page(r.text):
                    yield stub
            except Exception as e:
                logger.error(f"SO: page {page} failed: {e}")
                break

    def _parse_hit_count(self, html: str) -> int | None:
        m = RE_HIT_COUNT.search(html)
        if m:
            return int(m.group(1))
        return None

    def _parse_result_page(self, html: str) -> Iterator[dict]:
        """Parse result table rows from search result HTML."""
        soup = BeautifulSoup(html, "html.parser")

        # Results are in tables with border-top style
        decision_tables = soup.find_all(
            "table", attrs={"style": re.compile(r"border-top")}
        )

        for table in decision_tables:
            try:
                stub = self._parse_single_result(table)
                if stub:
                    yield stub
            except Exception as e:
                logger.debug(f"SO: parse error: {e}")

        # If no border-top tables, try extracting nF30_KEY links directly
        if not decision_tables:
            for a in soup.find_all("a", href=True):
                href = a.get("href", "")
                m = RE_NF30_KEY.search(href)
                if m:
                    nf30_key = m.group(1)
                    docket = a.get_text(strip=True)
                    if docket:
                        decision_id = make_decision_id("so_gerichte", docket)
                        yield {
                            "decision_id": decision_id,
                            "docket_number": docket,
                            "nf30_key": nf30_key,
                            "decision_date": None,
                            "url": self._build_doc_url(nf30_key),
                        }

    def _parse_single_result(self, table) -> dict | None:
        """Parse a single result table into a stub dict."""
        # Find the link with nF30_KEY
        link = None
        nf30_key = None
        for a in table.find_all("a", href=True):
            href = a.get("href", "")
            m = RE_NF30_KEY.search(href)
            if m:
                nf30_key = m.group(1)
                link = a
                break

        if not link or not nf30_key:
            return None

        docket = link.get_text(strip=True)
        if not docket:
            return None

        # Extract metadata from table cells
        tds = table.find_all("td")
        title = None
        decision_date = None
        instance = None

        for td in tds:
            text = td.get_text(strip=True)
            if not text:
                continue
            # Instance is usually one of the court codes
            if text in ("Verwaltungsgericht", "Obergericht", "Steuergericht",
                        "Versicherungsgericht", "Anklagekammer", "Beschwerdekammer",
                        "Strafkammer", "Zivilkammer", "Jugendgerichtskammer",
                        "Schuldbetreibungs- und Konkurskammer", "Schatzungskommission"):
                instance = text
            # Date in DD.MM.YYYY format
            d = _parse_swiss_date(text)
            if d and not decision_date:
                decision_date = d

        # Title from colspan=2 TD
        for td in table.find_all("td", attrs={"colspan": "2"}):
            text = td.get_text(strip=True)
            if text and "publikation" not in text.lower():
                title = text
                break

        decision_id = make_decision_id("so_gerichte", docket)

        return {
            "decision_id": decision_id,
            "docket_number": docket,
            "nf30_key": nf30_key,
            "decision_date": decision_date,
            "title": title,
            "instance": instance,
            "url": self._build_doc_url(nf30_key),
        }

    @staticmethod
    def _build_doc_url(nf30_key: str) -> str:
        """Build URL to fetch individual decision document."""
        return (
            f"{CGI_URL}?"
            f"OmnisPlatform=WINDOWS"
            f"&WebServerUrl="
            f"&WebServerScript=/cgi-bin/nph-omniscgi.exe"
            f"&OmnisLibrary=JURISWEB"
            f"&OmnisClass=rtFindinfoWebHtmlService"
            f"&OmnisServer=7001"
            f"&Parametername=WEB"
            f"&Schema=JGWEB"
            f"&Aufruf=getMarkupDocument"
            f"&cSprache=DE"
            f"&nF30_KEY={nf30_key}"
            f"&Template=/simple/search_result_document.html"
        )

    def fetch_decision(self, stub: dict) -> Decision | None:
        """Fetch full decision document and extract text."""
        url = stub.get("url")
        if not url:
            logger.warning(f"SO: no URL for {stub['docket_number']}")
            return None

        try:
            r = self.get(url)
        except Exception as e:
            logger.warning(f"SO: fetch failed for {stub['docket_number']}: {e}")
            return None

        html = r.text
        if len(html) < 500:
            logger.warning(f"SO: short doc for {stub['docket_number']}: {len(html)} chars")
            return None

        soup = BeautifulSoup(html, "html.parser")

        # Extract metadata from the document page
        docket = stub["docket_number"]
        decision_date = stub.get("decision_date")
        title = stub.get("title")
        instance = stub.get("instance")
        findinfo_nr = None
        resume = None

        # Parse metadata table
        for tr in soup.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) >= 2:
                label = tds[0].get_text(strip=True)
                value = tds[1].get_text(strip=True)

                if "Geschäftsnummer" in label or "Gesch\u00e4ftsnummer" in label:
                    if value and not docket:
                        docket = value
                elif "Instanz" in label:
                    instance = value
                elif "Entscheiddatum" in label:
                    d = _parse_swiss_date(value)
                    if d:
                        decision_date = d
                elif "FindInfo-Nummer" in label:
                    findinfo_nr = value
                elif "Titel" in label:
                    title = value
                elif "Resümee" in label or "Resumee" in label or "mee" in label:
                    resume = value if value else None

        # Extract full text
        full_text = _extract_document_text(soup)
        if not full_text or len(full_text) < 50:
            logger.warning(f"SO: text too short for {docket}: {len(full_text or '')} chars")
            if not full_text:
                full_text = f"[Text extraction failed for {docket}]"

        if not decision_date:
            # Try extracting from document body
            ws = soup.find("div", class_="WordSection1")
            if ws:
                # Only the opening: the ruling names its own date before
                # anything else; further down every "vom" is another decision's.
                body_text = re.sub(r"\s+", " ", ws.get_text())[:400]
                m = RE_LONG_DATE.search(body_text)
                if m:
                    day = int(m.group(1))
                    month = _MONTHS_DE.get(m.group(2))
                    year = int(m.group(3))
                    if month:
                        try:
                            decision_date = date(year, month, day)
                        except ValueError:
                            pass

        if not decision_date:
            logger.warning(f"SO: no date for {docket}")

        language = detect_language(full_text) if len(full_text) > 100 else "de"

        decision_id = make_decision_id("so_gerichte", docket)

        return Decision(
            decision_id=decision_id,
            court="so_gerichte",
            canton="SO",
            chamber=instance,
            docket_number=docket,
            decision_date=decision_date,
            language=language,
            title=title,
            regeste=resume,
            full_text=full_text,
            source_url=url,
            external_id=findinfo_nr,
            cited_decisions=extract_citations(full_text) if len(full_text) > 200 else [],
        )
