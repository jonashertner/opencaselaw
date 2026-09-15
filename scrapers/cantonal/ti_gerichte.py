"""
Ticino Courts Scraper (TI Gerichte)
=====================================
Scrapes court decisions from the Omnis/FindInfo platform at
www.sentenze.ti.ch.

Architecture:
- POST to /cgi-bin/nph-omniscgi (search with form data) → result list HTML
- GET /cgi-bin/nph-omniscgi (Aufruf=getMarkupDocument&nF30_KEY=...) → full decision HTML
- No authentication required
- Full decision text in <div class="WordSection1">

Key differences from BS/SO FindInfo:
- Schema: TI_WEB
- Parametername: WWWTI
- OmnisServer: JURISWEB,193.246.182.54:6000
- Language: ITA (Italian)
- Wildcard * does NOT work — use empty search + date filters
- Session key nX40_KEY required for pagination
- 500 results per page works

Court authorities: ICCA, IICCA, IIICC, CCR, CCC, CEF, CDP,
    TRAM, TPT, TCA, CDT, TE, PENAL, CARP, CCRP, CRPTI, CRP, GPC, GIAR, PRPEN

Total: ~58,566 decisions (1995-2026)
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

HOST = "http://www.sentenze.ti.ch"
CGI_PATH = "/cgi-bin/nph-omniscgi"
CGI_URL = HOST + CGI_PATH

RESULTS_PER_PAGE = 500

# Fixed CGI parameters
BASE_PARAMS = {
    "OmnisPlatform": "WINDOWS",
    "WebServerUrl": "www.sentenze.ti.ch",
    "WebServerScript": "/cgi-bin/nph-omniscgi",
    "OmnisLibrary": "JURISWEB",
    "OmnisClass": "rtFindinfoWebHtmlService",
    "OmnisServer": "JURISWEB,193.246.182.54:6000",
    "Schema": "TI_WEB",
    "Parametername": "WWWTI",
}

# Regex patterns
RE_TOTAL = re.compile(r"di\s+(\d+)")
RE_NF30_KEY = re.compile(r"nF30_KEY=(\d+)")
RE_NX40_KEY = re.compile(r"nX40_KEY=(\d+)")
RE_DATE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")
RE_AUTORITA = re.compile(r"Autorit[àa]:\s*(\w+)")
RE_DOCKET = re.compile(r"(\d+\.\d{4}\.\d+)")
# Listing: "Autorità: TCA, data decisione: 26.05.2026, data pubblicazione: 14.09.2026".
# Document page: label and value sit in two cells, "Data decisione, Autorità:" /
# "26.05.2026, TCA" — capital D, no colon before the date (see fetch_decision).
RE_DATA_DEC = re.compile(r"(?i)data decisione[^\d]{0,40}(\d{2}\.\d{2}\.\d{4})")
RE_DATA_PUB = re.compile(r"data pubblicazione:\s*(\d{2}\.\d{2}\.\d{4})")


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


class TIGerichteScraper(BaseScraper):
    """
    Scraper for Ticino court decisions via Omnis/FindInfo.

    Strategy: iterate month-by-month with empty search + date filter,
    paginate within each month using nX40_KEY session.
    Total: ~58,566 decisions.
    """

    REQUEST_DELAY = 2.0
    TIMEOUT = 60
    MAX_ERRORS = 100

    @property
    def court_code(self):
        return "ti_gerichte"

    def discover_new(self, since_date=None) -> Iterator[dict]:
        if since_date and isinstance(since_date, str):
            since_date = date.fromisoformat(since_date)

        total_yielded = 0
        today = date.today()
        start_year = since_date.year if since_date else 1995
        start_month = since_date.month if since_date else 1

        for year in range(today.year, start_year - 1, -1):
            end_month = today.month if year == today.year else 12
            begin_month = start_month if year == start_year else 1

            for month in range(end_month, begin_month - 1, -1):
                logger.info(f"TI: searching {year}-{month:02d}")
                count = 0
                for stub in self._discover_month(year, month):
                    if not self.state.is_known(stub["decision_id"]):
                        if since_date and stub.get("decision_date") and stub["decision_date"] < since_date:
                            continue
                        total_yielded += 1
                        count += 1
                        yield stub
                if count > 0:
                    logger.info(f"TI: {year}-{month:02d}: {count} new stubs")

        logger.info(f"TI: discovery complete: {total_yielded} new stubs")

    def _discover_month(self, year: int, month: int) -> Iterator[dict]:
        """Discover decisions for a specific month."""
        # Build form data for month search
        formdata = dict(BASE_PARAMS)
        formdata.update({
            "Aufruf": "validate",
            "Template": "results/resultpage_ita.fiw",
            "cSprache": "ITA",
            "cSuchstring": "",
            "cSuchstringZiel": "testo",
            "cEntscheiddatumVonMonat": f"{month:02d}",
            "cEntscheiddatumVonJahr": str(year),
            "cEntscheiddatumBisMonat": f"{month:02d}",
            "cEntscheiddatumBisJahr": str(year),
            "nSeite": "1",
            "nAnzahlTrefferProSeite": str(RESULTS_PER_PAGE),
            "cButtonAction": "3. Trova",
        })

        try:
            r = self.post(CGI_URL, data=formdata)
        except Exception as e:
            logger.error(f"TI: search failed for {year}-{month:02d}: {e}")
            return

        html = r.text
        total_hits = self._parse_total(html)
        if not total_hits:
            return

        logger.debug(f"TI: {year}-{month:02d}: {total_hits} hits")

        # Extract session key for pagination
        session_key = self._extract_session_key(html)

        # Parse page 1
        for stub in self._parse_result_page(html):
            yield stub

        # Paginate if needed
        if total_hits > RESULTS_PER_PAGE and session_key:
            total_pages = (total_hits + RESULTS_PER_PAGE - 1) // RESULTS_PER_PAGE
            for page in range(2, total_pages + 1):
                try:
                    params = dict(BASE_PARAMS)
                    params.update({
                        "Aufruf": "validate",
                        "Template": "results/resultpage_ita.fiw",
                        "cSprache": "ITA",
                        "nX40_KEY": session_key,
                        "nAnzahlTrefferProSeite": str(RESULTS_PER_PAGE),
                        "nSeite": str(page),
                    })
                    r = self.get(CGI_URL, params=params)
                    for stub in self._parse_result_page(r.text):
                        yield stub
                except Exception as e:
                    logger.error(f"TI: page {page} failed for {year}-{month:02d}: {e}")
                    break

    def _parse_total(self, html: str) -> int | None:
        """Extract total from 'Trovati 1 - N di TOTAL'."""
        m = re.search(r"di\s+(\d+)\s*</span>", html)
        if m:
            return int(m.group(1))
        # Try alternate pattern
        m = re.search(r"Trovati.*?di\s+(\d+)", html)
        if m:
            return int(m.group(1))
        return None

    def _extract_session_key(self, html: str) -> str | None:
        m = RE_NX40_KEY.search(html)
        return m.group(1) if m else None

    def _parse_result_page(self, html: str) -> Iterator[dict]:
        """Parse result entries from search result HTML."""
        soup = BeautifulSoup(html, "html.parser")

        # Find all links with nF30_KEY (these are the decision links)
        for a in soup.find_all("a", href=True):
            href = a.get("href", "")
            m_key = RE_NF30_KEY.search(href)
            if not m_key:
                continue

            nf30_key = m_key.group(1)
            title_attr = a.get("title", "")

            # Extract docket from title attribute: "Sentenza numero incarto 34.2025.27"
            docket = None
            m_docket = RE_DOCKET.search(title_attr)
            if m_docket:
                docket = m_docket.group(1)

            # Metadata sits in the SIBLING rows of the per-result table
            # (<table cellpadding="0">: row 1 = title link, row 2 = "Autorità: …,
            # data decisione: …, data pubblicazione: …", row 3 = docket). Until
            # 2026-09-14 this looked at the link's own <tr> only, so every stub
            # left discovery undated (796 NULL-date TI rows, freshness invisible).
            parent = a.find_parent("table") or a.find_parent("tr") or a.find_parent("div")
            if parent:
                parent_text = parent.get_text()

                # Authority
                m_aut = RE_AUTORITA.search(parent_text)
                autorita = m_aut.group(1) if m_aut else None

                # Decision date
                m_date = RE_DATA_DEC.search(parent_text)
                decision_date = _parse_swiss_date(m_date.group(1)) if m_date else None

                # Publication date
                m_pub = RE_DATA_PUB.search(parent_text)
                pub_date = _parse_swiss_date(m_pub.group(1)) if m_pub else None
            else:
                autorita = None
                decision_date = None
                pub_date = None

            if not docket:
                docket = f"TI-{nf30_key}"

            # Build link text as title
            link_text = a.get_text(strip=True)
            title = link_text[:200] if link_text else None

            decision_id = make_decision_id("ti_gerichte", docket)

            yield {
                "decision_id": decision_id,
                "docket_number": docket,
                "nf30_key": nf30_key,
                "decision_date": decision_date,
                "publication_date": pub_date,
                "autorita": autorita,
                "title": title,
                "url": self._build_doc_url(nf30_key),
            }

    @staticmethod
    def _build_doc_url(nf30_key: str) -> str:
        """Build URL to fetch individual decision document."""
        return (
            f"{CGI_URL}?"
            f"OmnisPlatform=WINDOWS"
            f"&WebServerUrl=www.sentenze.ti.ch"
            f"&WebServerScript=/cgi-bin/nph-omniscgi"
            f"&OmnisLibrary=JURISWEB"
            f"&OmnisClass=rtFindinfoWebHtmlService"
            f"&OmnisServer=JURISWEB,193.246.182.54:6000"
            f"&Parametername=WWWTI"
            f"&Schema=TI_WEB"
            f"&Source="
            f"&Aufruf=getMarkupDocument"
            f"&cSprache=ITA"
            f"&nF30_KEY={nf30_key}"
            f"&Template=results/document_ita.fiw"
        )

    def fetch_decision(self, stub: dict) -> Decision | None:
        """Fetch full decision document and extract text."""
        url = stub.get("url")
        if not url:
            return None

        try:
            r = self.get(url)
        except Exception as e:
            logger.warning(f"TI: fetch failed for {stub['docket_number']}: {e}")
            return None

        html = r.text
        if len(html) < 500:
            logger.warning(f"TI: short doc for {stub['docket_number']}: {len(html)} chars")
            return None

        soup = BeautifulSoup(html, "html.parser")

        # Extract metadata from document page
        docket = stub["docket_number"]
        decision_date = stub.get("decision_date")
        autorita = stub.get("autorita")
        title = stub.get("title")

        # Parse metadata from document structure
        for td in soup.find_all("td"):
            text = td.get_text(strip=True)
            if not text:
                continue
            # Docket number
            if "Numero d'incarto" in text or "Numero d" in text:
                next_td = td.find_next("td")
                if next_td:
                    val = next_td.get_text(strip=True)
                    m = RE_DOCKET.search(val)
                    if m:
                        docket = m.group(1)
            # Date and authority: "Data decisione, Autorità:" | "26.05.2026, TCA"
            # (label and value in two cells; the old code searched the label only).
            if "data decisione" in text.lower():
                val = text
                if not RE_DATE.search(text):
                    next_td = td.find_next("td")
                    if next_td:
                        val = f"{text} {next_td.get_text(' ', strip=True)}"
                m_date = RE_DATE.search(val)
                if m_date:
                    decision_date = _parse_swiss_date(m_date.group(0))
                m_aut = (
                    re.search(r"\d{2}\.\d{2}\.\d{4}\s*,\s*([A-Za-z]{2,8})", val)
                    or RE_AUTORITA.search(val)
                )
                if m_aut and not m_aut.group(1).isdigit():
                    autorita = m_aut.group(1)
            # Title
            if "Titolo" in text:
                next_td = td.find_next("td")
                if next_td:
                    title = next_td.get_text(strip=True)

        # Extract keywords from sidebar
        keywords = []
        for td in soup.find_all("td", bgcolor="#EEEEEE"):
            text = td.get_text(strip=True)
            if text and len(text) < 200:
                keywords.append(text)

        # Extract full text
        full_text = _extract_document_text(soup)
        if not full_text or len(full_text) < 50:
            # No placeholder rows ("[Text extraction failed for …]" was stored as the
            # decision text until 2026-09-14); the page was fetched, so cache the id as
            # a gap and let the TTL re-probe it.
            logger.warning(
                f"TI: text too short for {docket}: {len(full_text or '')} chars — "
                f"cached as gap for {self.state.GAP_TTL_DAYS} days"
            )
            self.state.mark_gap(make_decision_id("ti_gerichte", docket))
            return None

        if not decision_date:
            logger.warning(f"TI: no date for {docket}")

        language = detect_language(full_text) if len(full_text) > 100 else "it"
        decision_id = make_decision_id("ti_gerichte", docket)

        return Decision(
            decision_id=decision_id,
            court="ti_gerichte",
            canton="TI",
            chamber=autorita,
            docket_number=docket,
            decision_date=decision_date,
            publication_date=stub.get("publication_date"),
            language=language,
            title=title,
            regeste="; ".join(keywords) if keywords else None,
            full_text=full_text,
            source_url=url,
            cited_decisions=extract_citations(full_text) if len(full_text) > 200 else [],
        )
