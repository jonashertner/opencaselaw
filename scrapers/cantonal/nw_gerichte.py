"""
Nidwalden Courts Scraper (NW Gerichte)
======================================
Scrapes court decisions from nw.ch/rechtsprechung.

Platform: ICMS CMS with jQuery DataTables
Coverage: Obergericht + Verwaltungsgericht (498 decisions, 2016-present)
Language: de

Architecture:
- Single GET to /rechtsprechung → all 498 entries as inline JSON in data-entities
- Each entry has a PDF download link: /_rte/publikation/{id} → redirect to PDF
- No pagination needed, no API — everything in one page load
"""
from __future__ import annotations

import io
import json
import logging
import re
from datetime import date
from html import unescape
from typing import Iterator

import chamber_rules
import source_dates
from base_scraper import BaseScraper
from models import (
    Decision,
    detect_language,
    extract_citations,
    make_decision_id,
    parse_date,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://www.nw.ch"
LIST_URL = f"{BASE_URL}/rechtsprechung"

# Extract docket from title like "Nichtanhandnahme (BAS 21 7)" or "AHV Beiträge (SV 17 13)"
RE_DOCKET = re.compile(r"\(([A-Z]{1,5}\s+\d{2,4}\s+\d+)\)")
RE_PUB_ID = re.compile(r"/_rte/publikation/(\d+)")


class NWGerichteScraper(BaseScraper):
    """Scraper for Nidwalden court decisions from nw.ch."""

    REQUEST_DELAY = 1.5
    TIMEOUT = 60
    MAX_ERRORS = 20

    @property
    def court_code(self):
        return "nw_gerichte"

    def discover_new(self, since_date=None) -> Iterator[dict]:
        if since_date and isinstance(since_date, str):
            since_date = date.fromisoformat(since_date)

        try:
            r = self.get(LIST_URL)
        except Exception as e:
            logger.error(f"NW: failed to fetch listing page: {e}")
            return

        # Extract JSON from data-entities attribute
        m = re.search(r'data-entities="(.*?)"', r.text, re.DOTALL)
        if not m:
            logger.error("NW: no data-entities found")
            return

        raw = unescape(m.group(1))
        data = json.loads(raw)
        items = data.get("data", [])
        logger.info(f"NW: found {len(items)} decisions")

        total_yielded = 0
        for item in items:
            stub = self._parse_item(item)
            if not stub:
                continue

            if self.state.is_known(stub["decision_id"]):
                continue

            if since_date and stub.get("decision_date") and stub["decision_date"] < since_date:
                continue

            total_yielded += 1
            yield stub

        logger.info(f"NW: discovery complete: {total_yielded} new stubs")

    def _parse_item(self, item: dict) -> dict | None:
        name = item.get("name", "")
        date_str = item.get("datum-sort", "") or item.get("datum", "")
        download_html = item.get("_downloadBtn", "")

        # Extract docket from name
        m = RE_DOCKET.search(name)
        docket = m.group(1) if m else None

        # Extract publication ID from download button
        m2 = RE_PUB_ID.search(download_html)
        pub_id = m2.group(1) if m2 else None

        if not pub_id:
            return None

        if not docket:
            docket = f"NW-{pub_id}"

        decision_date = parse_date(date_str)
        decision_id = make_decision_id("nw_gerichte", docket)

        return {
            "decision_id": decision_id,
            "pub_id": pub_id,
            "docket_number": docket,
            "decision_date": decision_date,
            "title": name,
            "pdf_url": f"{BASE_URL}/_rte/publikation/{pub_id}",
        }

    def fetch_decision(self, stub: dict) -> Decision | None:
        pdf_url = stub.get("pdf_url", "")
        if not pdf_url:
            return None

        full_text = self._extract_pdf_text(pdf_url)
        if not full_text or len(full_text) < 50:
            logger.warning(f"NW: no text for {stub['docket_number']}")
            return None

        # The listing's datum is the reliable source; on 2026-06-24 it came
        # back empty for 15 rulings. Their PDF heads state the date ("VA 25 26 /
        # Entscheid vom 23. Februar 2026"), read before the bench and parties.
        decision_date = stub.get("decision_date")
        if not decision_date:
            head = source_dates.head_vom_date(full_text)
            if head and source_dates.plausible(head, stub["docket_number"], None):
                decision_date = date.fromisoformat(head)
        if not decision_date:
            logger.warning(f"NW: no date for {stub['docket_number']}")
        language = detect_language(full_text) if len(full_text) > 100 else "de"

        return Decision(
            decision_id=stub["decision_id"],
            court="nw_gerichte",
            canton="NW",
            # The PDF head: "OBERGERICHT / ... / ZA 24 19 / Urteil vom ... /
            # Zivilabteilung" (chamber_rules.nw_gerichte).
            chamber=chamber_rules.chamber_or(
                None, "nw_gerichte", docket=stub["docket_number"], text=full_text,
                language=language),
            docket_number=stub["docket_number"],
            decision_date=decision_date,
            language=language,
            title=stub.get("title"),
            full_text=full_text,
            source_url=stub.get("pdf_url", ""),
            pdf_url=stub.get("pdf_url"),
            cited_decisions=extract_citations(full_text) if len(full_text) > 200 else [],
        )

    def _extract_pdf_text(self, pdf_url: str) -> str:
        try:
            import pdfplumber
        except ImportError:
            logger.warning("pdfplumber not installed")
            return ""

        try:
            self._rate_limit()
            r = self.session.get(pdf_url, timeout=self.TIMEOUT, allow_redirects=True)
            if r.status_code != 200:
                return ""

            with pdfplumber.open(io.BytesIO(r.content)) as pdf:
                pages = []
                for page in pdf.pages:
                    text = page.extract_text()
                    if text:
                        pages.append(text)
                return "\n\n".join(pages)
        except Exception as e:
            logger.warning(f"NW: PDF extraction failed for {pdf_url}: {e}")
            return ""
