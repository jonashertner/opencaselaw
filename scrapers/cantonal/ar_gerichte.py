"""
Appenzell Ausserrhoden Courts Scraper (AR Gerichte)
====================================================
Scrapes court decisions from the Weblaw LEv4 platform.

Architecture:
- POST /api/.netlify/functions/searchQueryService → paginated JSON
- GET /api/.netlify/functions/singleDocQueryService/{leid} → full HTML content
- PDF download via metadataKeywordTextMap.originalUrl
- No authentication required
- ~2,500 decisions

Platform: Weblaw Lawsearch Enterprise v4 (same as BVGer)
"""
from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from typing import Iterator

from bs4 import BeautifulSoup

import chamber_rules
import source_dates
from base_scraper import BaseScraper
from models import (
    Decision,
    detect_language,
    extract_citations,
    make_decision_id,
)

logger = logging.getLogger(__name__)

HOST = "https://rechtsprechung.ar.ch"
SEARCH_URL = f"{HOST}/api/.netlify/functions/searchQueryService"
CONTENT_URL = f"{HOST}/api/.netlify/functions/singleDocQueryService"

HEADERS = {
    "Content-Type": "text/plain;charset=UTF-8",
    "Accept": "*/*",
    "Origin": HOST,
    "Referer": f"{HOST}/dashboard?guiLanguage=de",
}

# Window-based discovery to avoid hitting search limits
WINDOW_DAYS = 180
MAX_PER_WINDOW = 100
START_YEAR = 2000


def _strip_html(html_str: str) -> str:
    """Strip HTML tags and return clean text."""
    if not html_str:
        return ""
    soup = BeautifulSoup(html_str, "html.parser")
    return soup.get_text(separator="\n", strip=True)


class ARGerichteScraper(BaseScraper):
    """
    Scraper for AR court decisions via Weblaw LEv4 Netlify API.

    Strategy: iterate date windows, paginate within each, fetch full text
    from singleDocQueryService.
    """

    REQUEST_DELAY = 2.0
    TIMEOUT = 30
    MAX_ERRORS = 50

    @property
    def court_code(self):
        return "ar_gerichte"

    def discover_new(self, since_date=None) -> Iterator[dict]:
        if since_date and isinstance(since_date, str):
            since_date = date.fromisoformat(since_date)

        start = since_date or date(START_YEAR, 1, 1)
        today = date.today()
        total_yielded = 0
        cur = start

        while cur < today:
            bis = min(cur + timedelta(days=WINDOW_DAYS), today)
            offset = 0

            while True:
                docs, total, has_more = self._search(cur, bis, offset)

                if offset == 0 and total > 0:
                    logger.debug(f"AR: {cur} to {bis}: {total} decisions")

                for doc in docs:
                    stub = self._parse_doc(doc)
                    if not stub:
                        continue
                    if self.state.is_known(stub["decision_id"]):
                        continue
                    if since_date and stub.get("decision_date") and stub["decision_date"] < since_date:
                        continue
                    total_yielded += 1
                    yield stub

                offset += len(docs)
                if not has_more or offset >= total or not docs:
                    break

            cur = bis + timedelta(days=1)

        logger.info(f"AR: discovery complete: {total_yielded} new stubs")

    def _search(self, ab: date, bis: date, offset: int = 0) -> tuple[list, int, bool]:
        """Execute a Weblaw LEv4 search query."""
        body = {
            "guiLanguage": "de",
            "from": offset,
            "size": 10,
            "aggs": {"fields": ["treePath", "decisionDate"], "size": "10"},
            "metadataDateMap": {
                "decisionDate": {
                    "from": ab.strftime("%Y-%m-%dT00:00:00.000Z"),
                    "to": bis.strftime("%Y-%m-%dT23:59:59.999Z"),
                }
            },
        }

        try:
            r = self.post(
                SEARCH_URL,
                headers=HEADERS,
                data=json.dumps(body),
            )
            data = r.json()
        except Exception as e:
            logger.error(f"AR: search failed for {ab}-{bis}: {e}")
            return [], 0, False

        if "totalNumberOfDocuments" not in data:
            logger.error(f"AR: unexpected response: {str(data)[:500]}")
            return [], 0, False

        return (
            data.get("documents", []),
            data.get("totalNumberOfDocuments", 0),
            data.get("hasMoreResults", False),
        )

    def _parse_doc(self, doc: dict) -> dict | None:
        """Parse a Weblaw LEv4 document into a stub dict."""
        kw = doc.get("metadataKeywordTextMap", {})
        dt = doc.get("metadataDateMap", {})
        leid = doc.get("leid", "")

        titles = kw.get("title", [])
        if not titles:
            return None

        # Title is the docket number (e.g., "OG O1Z-23-1")
        docket = titles[0].strip()
        if not docket:
            return None

        # Decision date
        decision_date = None
        dd_str = dt.get("decisionDate")
        if dd_str:
            try:
                decision_date = date.fromisoformat(dd_str[:10])
            except (ValueError, TypeError):
                pass

        # Publication date
        pub_date = None
        pd_str = dt.get("publicationDate")
        if pd_str:
            try:
                pub_date = date.fromisoformat(pd_str[:10])
            except (ValueError, TypeError):
                pass

        # Authority (OG, VG, KG)
        behoerde_list = kw.get("argvpBehoerde", [])
        behoerde = behoerde_list[0] if behoerde_list else ""

        # Category
        kategorie_list = kw.get("entscheidKategorie", [])
        kategorie = kategorie_list[0] if kategorie_list else ""

        # PDF URL
        orig_list = kw.get("originalUrl", [])
        pdf_url = orig_list[0] if orig_list else None
        if pdf_url and not pdf_url.startswith("http"):
            pdf_url = f"{HOST}{pdf_url}"

        # AR GVP yearbook entries: decisionDate is sometimes a date the entry
        # only cites (ARGVP 2002 3410: 1995-04-30, the Landsgemeinde decision
        # it applies); the PDF's file name carries the entry's own, the one its
        # trailer prints ("JuAK 14.11.02" -> OG-20021114-...-ARGVP-2002-3410.pdf).
        if pdf_url and source_dates.is_ar_gvp(pdf_url):
            from_name = source_dates.ar_filename_date(pdf_url)
            if from_name:
                decision_date = date.fromisoformat(from_name)

        # Content snippet
        content_html = doc.get("content", "")

        decision_id = make_decision_id("ar_gerichte", docket)

        return {
            "decision_id": decision_id,
            "leid": leid,
            "docket_number": docket,
            "decision_date": decision_date,
            "publication_date": pub_date,
            "behoerde": behoerde,
            "kategorie": kategorie,
            "pdf_url": pdf_url,
            "content_snippet": _strip_html(content_html)[:500] if content_html else "",
            "url": f"{HOST}/cache?id={leid}&guiLanguage=de",
        }

    def fetch_decision(self, stub: dict) -> Decision | None:
        """Fetch full decision content from singleDocQueryService."""
        leid = stub.get("leid")
        if not leid:
            return None

        full_text = ""
        try:
            r = self.get(f"{CONTENT_URL}/{leid}")
            data = r.json()
            content_html = data.get("content", "")
            if content_html:
                full_text = _strip_html(content_html)
        except Exception as e:
            logger.warning(f"AR: content fetch failed for {stub['docket_number']}: {e}")

        if not full_text or len(full_text) < 50:
            # Fall back to snippet
            full_text = stub.get("content_snippet", "")
            if not full_text:
                full_text = f"[Text extraction failed for {stub['docket_number']}]"

        decision_date = stub.get("decision_date")
        if not decision_date:
            logger.warning(f"[ar_gerichte] No date for {stub['docket_number']}")

        language = detect_language(full_text) if len(full_text) > 100 else "de"

        # Map behoerde to chamber name
        behoerde_map = {
            "OG": "Obergericht",
            "VG": "Verwaltungsgericht",
            "KG": "Kantonsgericht",
        }
        chamber = behoerde_map.get(stub.get("behoerde", ""), stub.get("behoerde"))
        # The Abteilung, from the ruling's head ("Obergericht Appenzell
        # Ausserrhoden / 3. Abteilung") or the docket ("O3V-19-40"); it
        # completes a bare "Obergericht" and fills an empty behoerde.
        chamber = chamber_rules.chamber_or(
            chamber or None, "ar_gerichte", docket=stub["docket_number"],
            text=full_text, language=language)

        return Decision(
            decision_id=stub["decision_id"],
            court="ar_gerichte",
            canton="AR",
            chamber=chamber,
            docket_number=stub["docket_number"],
            decision_date=decision_date,
            publication_date=stub.get("publication_date"),
            language=language,
            title=stub.get("kategorie") or None,
            full_text=full_text,
            source_url=stub.get("url"),
            pdf_url=stub.get("pdf_url"),
            cited_decisions=extract_citations(full_text) if len(full_text) > 200 else [],
        )
