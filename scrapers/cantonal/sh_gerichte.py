"""
Schaffhausen Courts Scraper (SH Gerichte)
==========================================
Scrapes court decisions from the KSD Backend CMS at
obergerichtsentscheide.sh.ch.

Architecture:
- GET /CMS/content/list?filter_customposttypeid_int=402&... → JSON list
- GET /CMS/get/file/{UUID} → PDF download
- No authentication required
- JSON API with excerpts; full text via PDF

Total: ~709 decisions (2000-present)
Platform: KSD Backend v1.0 (custom CMS)
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

BASE_URL = "https://obergerichtsentscheide.sh.ch"
LIST_URL = f"{BASE_URL}/CMS/content/list"
FILE_URL = f"{BASE_URL}/CMS/get/file"

# Root content ID for all decisions
ROOT_PATH_ID = "2272926"
CONTENT_TYPE_DECISION = 402
PAGE_SIZE = 100

RE_DATE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")
RE_NR = re.compile(r"Nr\.\s*(\d+/\d{4}/\d+)")

# The CMS metadata fields (custom_publication_date_date, publication_date) are
# PUBLICATION dates, never the judgment date (verified 2026-07: a case docketed
# 60/2017/43 carries custom_publication_date_date=10.01.2020). The real decision
# date lives only in the PDF/document header, so it is parsed from there.
_GERMAN_MONTHS = {
    "Januar": 1, "Februar": 2, "März": 3, "April": 4, "Mai": 5, "Juni": 6,
    "Juli": 7, "August": 8, "September": 9, "Oktober": 10, "November": 11,
    "Dezember": 12,
}
_MONTH_NAMES = "|".join(_GERMAN_MONTHS)
RE_HEAD_LONG = re.compile(
    r"(?:Entscheid|Urteil|Beschluss|Verf[üu]gung)?\s*vom\s+(\d{1,2})\.?\s*("
    + _MONTH_NAMES + r")\s+(\d{4})")
RE_HEAD_NUM = re.compile(
    r"(?:Entscheid|Urteil|Beschluss|Verf[üu]gung)?\s*vom\s+(\d{1,2})\.(\d{1,2})\.(\d{4})")


def clean_html_text(value) -> str:
    """Text of a CMS field that may carry markup: the portal's kachellabel and
    headline sometimes hold the editor's HTML ("<div><span style=...>Nr.
    63/2020/6</span><br></div>", "Nr. 60/2020/31 – <br>Wahl ..."). Tags become
    spaces, entities are decoded, whitespace collapses."""
    if not value:
        return ""
    text = str(value)
    if "<" in text or "&" in text:
        text = BeautifulSoup(text, "html.parser").get_text(" ")
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


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


def _parse_head_date(text):
    """Judgment date from the document header, anchored on 'vom <date>' so it does
    not grab case dates scattered through the body. German-month and numeric
    forms; scans only the first ~2500 chars (the header/rubrum)."""
    if not text:
        return None
    head = text[:2500]
    m = RE_HEAD_LONG.search(head)
    if m:
        month = _GERMAN_MONTHS.get(m.group(2))
        if month:
            try:
                return date(int(m.group(3)), month, int(m.group(1)))
            except ValueError:
                pass
    m = RE_HEAD_NUM.search(head)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            pass
    return None


class SHGerichteScraper(BaseScraper):
    """
    Scraper for Schaffhausen Obergericht decisions via KSD CMS API.

    Strategy: paginate through JSON list API, extract metadata + excerpt,
    optionally download PDF for full text.
    """

    REQUEST_DELAY = 1.5
    TIMEOUT = 30
    MAX_ERRORS = 50

    @property
    def court_code(self):
        return "sh_gerichte"

    def discover_new(self, since_date=None) -> Iterator[dict]:
        if since_date and isinstance(since_date, str):
            since_date = date.fromisoformat(since_date)

        total_yielded = 0
        offset = 0

        while True:
            try:
                params = {
                    "filter_customposttypeid_int": CONTENT_TYPE_DECISION,
                    "filter_approvedpaths_string": f"*{ROOT_PATH_ID}*",
                    "rows": PAGE_SIZE,
                    "start": offset,
                    "status": "published",
                }
                r = self.get(LIST_URL, params=params)
                items = r.json()
            except Exception as e:
                logger.error(f"SH: list failed at offset {offset}: {e}")
                break

            if not items or not isinstance(items, list):
                break

            if offset == 0:
                logger.info(f"SH: fetched first batch of {len(items)} items")

            for item in items:
                stub = self._parse_item(item)
                if not stub:
                    continue

                if self.state.is_known(stub["decision_id"]) or (
                        stub.get("legacy_decision_id")
                        and self.state.is_known(stub["legacy_decision_id"])):
                    continue

                # decision_date is not known until the PDF is fetched, so gate the
                # incremental window on the publication date (a safe proxy: a
                # decision is published at/after it is issued).
                if since_date and stub.get("publication_date") and stub["publication_date"] < since_date:
                    continue

                total_yielded += 1
                yield stub

            if len(items) < PAGE_SIZE:
                break

            offset += len(items)

        logger.info(f"SH: discovery complete: {total_yielded} new stubs")

    def _parse_item(self, item: dict) -> dict | None:
        """Parse a CMS list item into a stub dict."""
        content_id = item.get("contentid")
        if not content_id:
            return None

        # Docket number from kachellabel (e.g., "Nr. 60/2017/43")
        kachellabel = item.get("kachellabel", "") or ""
        docket = clean_html_text(kachellabel)
        if not docket:
            docket = f"SH-{content_id}"
        # Rows scraped before 2026-11 hold the label as served, markup included,
        # in their decision_id; discovery treats that id as known too.
        raw_docket = kachellabel.strip()
        legacy_id = (make_decision_id("sh_gerichte", raw_docket)
                     if raw_docket and raw_docket != docket else None)

        # Title/summary
        headline = clean_html_text(item.get("articleHeadline", ""))
        listlabel = item.get("listlabel", "")

        # Publication date. custom_publication_date_date is the PORTAL publication
        # date (the better proxy); the plain `publication_date` field is a CMS
        # migration timestamp (bulk-set), so prefer the former. Neither is the
        # judgment date, which fetch_decision extracts from the PDF header.
        pub_date = (_parse_swiss_date(item.get("custom_publication_date_date", ""))
                    or _parse_swiss_date(item.get("publication_date", "")))

        # Excerpt text (HTML)
        post_content = item.get("post_content", "")

        # PDF UUID from sliderguid
        pdf_uuid = item.get("sliderguid", "")

        # Permalink
        permalink = item.get("permalink", "")

        decision_id = make_decision_id("sh_gerichte", docket)

        return {
            "decision_id": decision_id,
            "legacy_decision_id": legacy_id,
            "content_id": content_id,
            "docket_number": docket,
            # decision_date is deliberately omitted here: it is extracted from the
            # PDF header in fetch_decision, never from CMS publication metadata.
            "publication_date": pub_date,
            "title": headline,
            "listlabel": listlabel,
            "post_content": post_content,
            "pdf_uuid": pdf_uuid,
            "permalink": permalink,
            "url": f"{BASE_URL}{permalink}" if permalink else f"{BASE_URL}/CMS/content/{content_id}",
        }

    def fetch_decision(self, stub: dict) -> Decision | None:
        """Extract decision from CMS data + optional PDF."""
        docket = stub["docket_number"]

        # Start with the excerpt from post_content
        full_text = ""
        post_content = stub.get("post_content", "")
        if post_content:
            # Strip HTML tags from post_content
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(post_content, "html.parser")
            full_text = soup.get_text(separator="\n", strip=True)

        # Try to download PDF for full text
        pdf_uuid = stub.get("pdf_uuid", "")
        if pdf_uuid:
            try:
                r = self.get(f"{FILE_URL}/{pdf_uuid}", timeout=30)
                if r.status_code == 200 and len(r.content) > 1000:
                    pdf_text = self._extract_pdf_text(r.content)
                    if pdf_text and len(pdf_text) > len(full_text):
                        full_text = pdf_text
            except Exception as e:
                logger.debug(f"SH: PDF download failed for {docket}: {e}")

        if not full_text or len(full_text) < 20:
            # Build text from available metadata
            parts = []
            if stub.get("title"):
                parts.append(stub["title"])
            if stub.get("listlabel"):
                parts.append(stub["listlabel"])
            full_text = "\n\n".join(parts) if parts else f"[Text extraction failed for {docket}]"

        # The judgment date lives only in the document header. Extract it there;
        # never substitute the publication date (that is the historical bug that
        # put a publication date up to years off into decision_date).
        decision_date = _parse_head_date(full_text)
        if not decision_date:
            logger.warning(
                f"SH: no judgment date in header for {stub.get('docket_number', '?')}; "
                f"leaving decision_date null")

        language = detect_language(full_text) if len(full_text) > 100 else "de"

        pdf_url = f"{FILE_URL}/{pdf_uuid}" if pdf_uuid else None

        return Decision(
            decision_id=stub["decision_id"],
            court="sh_gerichte",
            canton="SH",
            chamber="Obergericht",
            docket_number=docket,
            decision_date=decision_date,
            publication_date=stub.get("publication_date"),
            language=language,
            title=stub.get("title"),
            full_text=full_text,
            source_url=stub.get("url"),
            pdf_url=pdf_url,
            cited_decisions=extract_citations(full_text) if len(full_text) > 200 else [],
        )

    @staticmethod
    def _extract_pdf_text(pdf_bytes: bytes) -> str:
        """Extract text from PDF bytes."""
        try:
            import fitz
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            pages = []
            for page in doc:
                pages.append(page.get_text())
            doc.close()
            return "\n\n".join(pages)
        except ImportError:
            pass

        try:
            import pdfplumber
            import io
            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                pages = []
                for page in pdf.pages:
                    text = page.extract_text()
                    if text:
                        pages.append(text)
                return "\n\n".join(pages)
        except ImportError:
            pass

        return ""
