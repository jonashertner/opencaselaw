"""
ZH scraper — Zurich cantonal laws from zh.ch / notes.zh.ch.

ZH publishes laws via an AEM-based CMS at zh.ch with PDFs stored on
a Lotus Notes/Domino backend at notes.zh.ch.

Enumeration:
  Paginated JSON API at /_jcr_content/main/lawcollectionsearch_*.zhweb-zhlex-ls.zhweb-cache.json

Detail pages:
  /zhlex-ls/erlass-{ordnr}-{date}-{version}.html → PDF download link

PDF:
  notes.zh.ch/appl/zhlex_r.nsf/OpenAttachment?Open&docid={ID}&file={filename}.pdf
"""
from __future__ import annotations

import html
import logging
import re
import time
from typing import Iterator

import requests

from scrapers.cantonal_laws import mount_retries

logger = logging.getLogger(__name__)

SEARCH_URL = (
    "https://www.zh.ch/de/politik-staat/gesetze-beschluesse/gesetzessammlung"
    "/_jcr_content/main/lawcollectionsearch_312548694"
    ".zhweb-zhlex-ls.zhweb-cache.json"
)

DETAIL_BASE = (
    "https://www.zh.ch/de/politik-staat/gesetze-beschluesse/gesetzessammlung"
)

USER_AGENT = (
    "SwissCaselawBot/1.0 (https://github.com/jonashertner/caselaw-repo; "
    "legal research; respects rate limits)"
)


class ZHScraper:
    """Scrape Zurich cantonal laws."""

    REQUEST_DELAY: float = 0.5

    def __init__(self, canton: str = "ZH"):
        self.canton = "ZH"
        self.lang = "de"
        self.session = requests.Session()
        mount_retries(self.session)
        self.session.headers.update({"User-Agent": USER_AGENT})
        self._last_request: float = 0
        self.portal_count: int | None = None

    def _get(self, url: str, **kwargs) -> requests.Response:
        elapsed = time.time() - self._last_request
        if elapsed < self.REQUEST_DELAY:
            time.sleep(self.REQUEST_DELAY - elapsed)
        r = self.session.get(url, timeout=30, **kwargs)
        self._last_request = time.time()
        r.raise_for_status()
        return r

    def enumerate_laws(self) -> Iterator[dict]:
        """Paginate through the ZH law collection API."""
        page = 0
        total_seen = 0
        total_expected = None

        while True:
            params = {"page": page} if page > 0 else {}
            r = self._get(SEARCH_URL, params=params)
            data = r.json()

            results = data.get("data", [])
            if total_expected is None:
                total_expected = data.get("numberOfResults", 0)
                self.portal_count = total_expected
                logger.info(f"ZH: {total_expected} laws in collection")

            if not results:
                break

            for law in results:
                total_seen += 1
                yield {
                    "link": law["link"],
                    "sr_number": law["referenceNumber"],
                    "title": law["enactmentTitle"],
                    "enactment_date": law.get("enactmentDate", ""),
                    "withdrawal_date": law.get("withdrawalDate", ""),
                }

            if total_seen >= total_expected:
                break

            page += 1

        logger.info(f"ZH: enumerated {total_seen} laws")

    def fetch_law(self, stub: dict) -> dict | None:
        """Fetch detail page, download PDF, extract text."""
        detail_url = f"https://www.zh.ch{stub['link']}"

        try:
            r = self._get(detail_url)
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                logger.warning(f"ZH {stub['sr_number']}: detail page 404")
                return None
            raise

        page_html = r.text

        # Extract PDF URL from detail page
        pdf_match = re.search(
            r'href="(https://www\.notes\.zh\.ch/[^"]+\.pdf)"',
            page_html,
        )
        if not pdf_match:
            logger.warning(f"ZH {stub['sr_number']}: no PDF link found")
            return None

        pdf_url = html.unescape(pdf_match.group(1))

        # Download PDF — notes.zh.ch returns a JS redirect, follow it
        try:
            pdf_resp = self._get(pdf_url)
            # Check for JS redirect: window.location="..."
            if len(pdf_resp.content) < 500 and b"window.location" in pdf_resp.content:
                redirect_match = re.search(
                    r'window\.location="([^"]+)"', pdf_resp.text
                )
                if redirect_match:
                    redirect_path = redirect_match.group(1)
                    if redirect_path.startswith("/"):
                        redirect_url = f"https://www.notes.zh.ch{redirect_path}"
                    else:
                        redirect_url = redirect_path
                    pdf_resp = self._get(redirect_url)
        except Exception as e:
            logger.error(f"ZH {stub['sr_number']}: PDF download failed: {e}")
            return None

        # Extract text from PDF
        full_text, articles = self._extract_pdf(pdf_resp.content, stub["sr_number"])

        if not full_text.strip():
            return None

        return {
            "canton": "ZH",
            "sr_number": stub["sr_number"],
            "title": stub["title"],
            "abbreviation": "",
            "language": "de",
            "is_active": not bool(stub.get("withdrawal_date")),
            "category": self._infer_category(stub["title"]),
            "original_url": detail_url,
            "version_active_since": stub.get("enactment_date", ""),
            "text_source": "zhlex_pdf",
            "full_text": full_text,
            "articles": articles,
        }

    def _extract_pdf(self, pdf_bytes: bytes, sr_number: str) -> tuple[str, list[dict]]:
        """Extract text and articles from PDF bytes."""
        try:
            import fitz  # PyMuPDF
        except ImportError:
            logger.error("PyMuPDF (fitz) not installed, cannot extract PDF text")
            return "", []

        try:
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            pages = []
            for page in doc:
                pages.append(page.get_text())
            doc.close()
        except Exception as e:
            logger.error(f"ZH {sr_number}: PDF extraction failed: {e}")
            return "", []

        full_text = "\n".join(pages)

        # Segment into articles
        articles = self._segment_articles(full_text)

        return full_text, articles

    def _segment_articles(self, text: str) -> list[dict]:
        """Segment text into articles."""
        pattern = re.compile(
            r"^(?:Art\.|§)\s*(\d+[a-z]?(?:bis|ter|quater|quinquies)?)\b\.?\s*(.*)",
            re.MULTILINE,
        )

        articles = []
        matches = list(pattern.finditer(text))

        for i, match in enumerate(matches):
            art_num = match.group(1)
            heading = match.group(2).strip()

            start = match.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            art_text = text[start:end].strip()

            articles.append({
                "article_num": art_num,
                "heading": heading,
                "text": art_text,
            })

        return articles

    def _infer_category(self, title: str) -> str:
        tl = title.lower()
        if "verordnung" in tl:
            return "Verordnung"
        if "gesetz" in tl:
            return "Gesetz"
        if "verfassung" in tl:
            return "Verfassung"
        if "vertrag" in tl or "konkordat" in tl:
            return "Vertrag"
        if "reglement" in tl:
            return "Reglement"
        return ""
