"""
ECHR Swiss Cases Scraper (bge_egmr)
====================================

Scrapes European Court of Human Rights (ECHR/EGMR) decisions with Swiss
involvement, as published on the BGer's CLIR endpoint.

Uses the same infrastructure as the BGE Leitentscheide scraper (session
management, Incapsula bypass) but only fetches from the CEDH endpoint:
  search.bger.ch/ext/eurospider/live/de/php/clir/http/index_cedh.php

Coverage: ~500 decisions, all in French (the ECHR's working language).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
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
from incapsula_bypass import IncapsulaCookieManager

logger = logging.getLogger(__name__)

# CLIR endpoint for ECHR decisions
EGMR_URL = (
    "https://search.bger.ch/ext/eurospider/live/de/php/clir/http/index_cedh.php"
    "?lang=de"
)

# Initial URL to establish session cookie
INITIAL_URL = (
    "https://search.bger.ch/ext/eurospider/live/de/php/clir/http/index_atf.php"
    "?lang=de"
)


class BGEEGMRScraper(BaseScraper):
    """Scraper for ECHR (EGMR) decisions with Swiss involvement."""

    REQUEST_DELAY = 3.0
    TIMEOUT = 60

    @property
    def court_code(self) -> str:
        return "bge_egmr"

    def __init__(self, state_dir: Path = Path("state")):
        super().__init__(state_dir)
        self._session_cookies: dict = {}
        self._incapsula = IncapsulaCookieManager(cache_dir=state_dir)

    # ---------------------------------------------------------------
    # Session management (same as BGE scraper)
    # ---------------------------------------------------------------

    def _apply_ua(self) -> None:
        ua = self._incapsula.user_agent("search.bger.ch")
        if ua:
            self.session.headers["User-Agent"] = ua

    def _establish_session(self) -> None:
        """Establish CLIR session with Incapsula cookies. Through the residential
        proxy no cookies are harvested up front; a challenge is answered by a
        harvest through the proxy, never from the datacenter address."""
        self._incapsula.proxy = self.session.proxies.get("https") or self.session.proxies.get("http") or None
        if not self._incapsula.proxy:
            try:
                incap_cookies = self._incapsula.get_cookies("search.bger.ch")
                self.session.cookies.update(incap_cookies)
                self._apply_ua()
                logger.info(f"Applied {len(incap_cookies)} Incapsula cookies")
            except Exception as e:
                logger.warning(f"Incapsula cookie harvest failed: {e}")

        response = self.get(INITIAL_URL)

        if self._incapsula.is_incapsula_blocked(response.text):
            logger.warning("Incapsula block, refreshing cookies")
            try:
                incap_cookies = self._incapsula.refresh_cookies("search.bger.ch")
                self.session.cookies.update(incap_cookies)
                self._apply_ua()
                response = self.get(INITIAL_URL)
            except Exception as e:
                logger.error(f"Incapsula refresh failed: {e}")

        self._session_cookies = {c.name: c.value for c in response.cookies}
        for c in self.session.cookies:
            self._session_cookies[c.name] = c.value
        logger.info(f"Session established. Cookies: {list(self._session_cookies.keys())}")

    def _safe_get(self, url: str, retry: int = 0, max_retries: int = 3):
        """GET with Incapsula auto-refresh on block."""
        resp = self.get(url, cookies=self._session_cookies)

        if self._incapsula.is_incapsula_blocked(resp.text) and retry < max_retries:
            logger.info(f"Incapsula block, refreshing ({retry+1}/{max_retries})")
            try:
                self._incapsula.proxy = self.session.proxies.get("https") or self.session.proxies.get("http") or None
                incap_cookies = self._incapsula.refresh_cookies("search.bger.ch")
                self.session.cookies.update(incap_cookies)
                self._session_cookies.update(incap_cookies)
                self._apply_ua()
            except Exception as e:
                logger.error(f"Incapsula refresh failed: {e}")
            return self._safe_get(url, retry + 1, max_retries)

        return resp

    # ---------------------------------------------------------------
    # Listing parser
    # ---------------------------------------------------------------

    def _parse_egmr_listing(self, html: str) -> list[dict]:
        """
        Parse EGMR listing page.

        Table format: width='75%', border-collapse style.
        Columns: date | link(docket) | ? | case_name
        """
        import re

        soup = BeautifulSoup(html, "html.parser")
        stubs = []

        tables = soup.find_all(
            "table",
            attrs={"width": "75%", "style": re.compile("border.*collapse")},
        )

        if not tables:
            logger.warning("No EGMR table found in listing")
            return stubs

        for table in tables:
            for row in table.find_all("tr"):
                cells = row.find_all("td")
                if len(cells) < 2:
                    continue

                link = cells[1].find("a") if len(cells) > 1 else None
                if not link:
                    continue

                datum_str = cells[0].get_text(strip=True) if cells[0] else ""
                num = link.get_text(strip=True)
                href = link.get("href", "")
                case_name = cells[3].get_text(strip=True) if len(cells) > 3 else None

                stubs.append({
                    "docket_number": num,
                    "decision_date": datum_str,
                    "url": href,
                    "case_name": case_name,
                })

        logger.info(f"EGMR listing: {len(stubs)} decisions")
        return stubs

    # ---------------------------------------------------------------
    # Document parser
    # ---------------------------------------------------------------

    def _parse_egmr_document(self, html: str) -> dict:
        """Parse an EGMR document page for full text."""
        soup = BeautifulSoup(html, "html.parser")
        meta = {"chamber": "EGMR"}

        content = soup.find("div", id="highlight_content")
        if content:
            content_div = content.find("div", class_="content")
            if content_div:
                meta["text"] = content_div.get_text(separator="\n", strip=True)
            else:
                meta["text"] = content.get_text(separator="\n", strip=True)

        return meta

    # ---------------------------------------------------------------
    # Discovery
    # ---------------------------------------------------------------

    def discover_new(self, since_date=None) -> Iterator[dict]:
        """Discover ECHR decisions from the CEDH listing page."""
        self._establish_session()

        response = self._safe_get(EGMR_URL)
        stubs = self._parse_egmr_listing(response.text)

        for stub in stubs:
            decision_id = make_decision_id("bge_egmr", stub["docket_number"])
            if self.state.is_known(decision_id):
                continue

            if since_date:
                d = parse_date(stub.get("decision_date", ""))
                if d and d < since_date:
                    continue

            yield stub

    # ---------------------------------------------------------------
    # Fetch
    # ---------------------------------------------------------------

    def fetch_decision(self, stub: dict) -> Decision | None:
        """Fetch a single ECHR decision."""
        docket = stub["docket_number"]
        base_url = stub["url"]

        try:
            doc_url = base_url if base_url.startswith("http") else f"https://search.bger.ch{base_url}"
            response = self._safe_get(doc_url)
            meta = self._parse_egmr_document(response.text)

            full_text = meta.get("text", "")
            if not full_text:
                logger.warning(f"No text content for EGMR {docket}")
                return None

            # Keep None when the portal provides no parsable date — fabricating
            # date.today() as fallback poisons the corpus with fake dates and
            # inflates "today's new decisions" in downstream stats.
            decision_date = parse_date(stub.get("decision_date", ""))
            lang = detect_language(full_text)

            return Decision(
                decision_id=make_decision_id("bge_egmr", docket),
                court="bge_egmr",
                canton="CH",
                chamber="EGMR",
                docket_number=docket,
                decision_date=decision_date,
                language=lang,
                title=stub.get("case_name"),
                full_text=self.clean_text(full_text),
                source_url=doc_url,
                cited_decisions=extract_citations(full_text),
                scraped_at=datetime.now(timezone.utc),
            )

        except Exception as e:
            logger.error(f"Failed to fetch EGMR {docket}: {e}", exc_info=True)
            return None
