"""
TI scraper — Ticino cantonal laws from www3.ti.ch/CAN/RLeggi.

The Ticino law collection is a PHP application that serves each law
as a single HTML page. The elenco-atti page lists all law IDs.

Enumeration:
  /raccolta-leggi/elenco-atti → links to /legge-piatta/num/{id}

Law pages:
  /raccolta-leggi/legge-piatta/num/{id} → full HTML with articles
"""
from __future__ import annotations

import html as html_mod
import logging
import re
import time
from typing import Iterator

import requests

from . import mount_retries
from .numbering import split_number_and_title

logger = logging.getLogger(__name__)

BASE_URL = "https://www3.ti.ch/CAN/RLeggi/public/index.php"
LIST_URL = f"{BASE_URL}/raccolta-leggi/elenco-atti"

USER_AGENT = (
    "SwissCaselawBot/1.0 (https://github.com/jonashertner/caselaw-repo; "
    "legal research; respects rate limits)"
)


class TIScraper:
    """Scrape Ticino cantonal laws."""

    REQUEST_DELAY: float = 1.0  # Polite — TYPO3 backend

    def __init__(self, canton: str = "TI"):
        self.canton = "TI"
        self.lang = "it"
        self.session = requests.Session()
        mount_retries(self.session)
        self.session.headers.update({"User-Agent": USER_AGENT})
        self._last_request: float = 0
        self.portal_count: int | None = None

    def _get(self, url: str) -> requests.Response:
        elapsed = time.time() - self._last_request
        if elapsed < self.REQUEST_DELAY:
            time.sleep(self.REQUEST_DELAY - elapsed)
        r = self.session.get(url, timeout=30)
        self._last_request = time.time()
        r.raise_for_status()
        return r

    def enumerate_laws(self) -> Iterator[dict]:
        """Get all law IDs from the elenco-atti listing page."""
        r = self._get(LIST_URL)
        html = r.text

        # Extract all unique law IDs from links
        ids_with_titles = []
        # Pattern: <a href="...legge-piatta/num/{id}">Title</a>
        for match in re.finditer(
            r'href="[^"]*legge-piatta/num/(\d+)"[^>]*>([^<]+)<', html
        ):
            law_id = int(match.group(1))
            title = html_mod.unescape(match.group(2).strip())
            ids_with_titles.append((law_id, title))

        # Deduplicate by ID, keep first title
        seen = {}
        for law_id, title in ids_with_titles:
            if law_id not in seen:
                seen[law_id] = title

        stubs = []
        for law_id in sorted(seen.keys()):
            stubs.append({
                "law_id": law_id,
                "title": seen[law_id],
                "url": f"{BASE_URL}/raccolta-leggi/legge-piatta/num/{law_id}",
            })

        self.portal_count = len(stubs)
        logger.info(f"TI: {len(stubs)} laws in elenco-atti")
        yield from stubs

    def fetch_law(self, stub: dict) -> dict | None:
        """Fetch and parse a TI law page."""
        try:
            r = self._get(stub["url"])
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                logger.warning(f"TI {stub['law_id']}: 404")
                return None
            raise

        html = r.text

        # Extract title from page
        title_match = re.search(r'<h1[^>]*>([^<]+)</h1>', html)
        sr_number = ""
        title = stub["title"]

        if title_match:
            # Title format: "101.000 Costituzione..." or bare title
            raw_title = html_mod.unescape(title_match.group(1).strip())
            sr_number, title = split_number_and_title(raw_title, fallback="")

        if not sr_number:
            # Try extracting from page content
            sr_match = re.search(r'<[^>]*class="[^"]*numero[^"]*"[^>]*>([\d.]+)', html)
            if sr_match:
                sr_number = sr_match.group(1).rstrip(".")
            else:
                # The index carries the number in the title ("101.000
                # Costituzione della Repubblica...") even when the detail
                # page's <h1> does not, and every TI law took this path:
                # all 623 ended up numbered 1..623 by row position, so no
                # real Ticino number resolved. The row counter is a last
                # resort, not the first one.
                sr_number, title = split_number_and_title(
                    stub["title"], fallback=str(stub["law_id"]))

        # Parse articles and full text from the HTML body
        articles, full_text = self._parse_law_html(html)

        if not full_text.strip():
            return None

        return {
            "canton": "TI",
            "sr_number": sr_number,
            "title": title,
            "abbreviation": "",
            "language": "it",
            "is_active": True,
            "category": self._infer_category(title),
            "original_url": stub["url"],
            "version_active_since": "",
            "text_source": "ti_rl",
            "full_text": full_text,
            "articles": articles,
        }

    def _parse_law_html(self, html_text: str) -> tuple[list[dict], str]:
        """Parse TI law HTML into articles and full text."""
        body_match = re.search(r'<body[^>]*>(.*)</body>', html_text, re.DOTALL | re.IGNORECASE)
        if not body_match:
            return [], ""

        body = body_match.group(1)

        # Remove scripts, styles, navigation
        body = re.sub(r'<script[^>]*>.*?</script>', '', body, flags=re.DOTALL | re.IGNORECASE)
        body = re.sub(r'<style[^>]*>.*?</style>', '', body, flags=re.DOTALL | re.IGNORECASE)
        body = re.sub(r'<nav[^>]*>.*?</nav>', '', body, flags=re.DOTALL | re.IGNORECASE)

        # Convert to text — TI uses span-based styling, so </span> needs newlines too
        body = re.sub(r'<br\s*/?>', '\n', body)
        body = re.sub(r'</(?:p|div|h[1-6]|li|tr|span)>', '\n', body, flags=re.IGNORECASE)
        body = re.sub(r'<(?:p|div|h[1-6]|li|tr)[^>]*>', '\n', body, flags=re.IGNORECASE)
        text = re.sub(r'<[^>]+>', '', body)
        text = html_mod.unescape(text)

        lines = []
        for line in text.split('\n'):
            line = re.sub(r'[ \t]+', ' ', line).strip()
            if line:
                lines.append(line)

        full_text = '\n'.join(lines)
        articles = self._segment_articles(full_text)

        return articles, full_text

    def _segment_articles(self, text: str) -> list[dict]:
        """Segment text into articles using Art. pattern."""
        pattern = re.compile(
            r'^Art\.\s*(\d+[a-z]?(?:bis|ter|quater|quinquies)?)\b\s*(.*)',
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
        if "regolamento" in tl:
            return "Regolamento"
        if "legge" in tl:
            return "Legge"
        if "decreto" in tl:
            return "Decreto"
        if "costituzione" in tl:
            return "Costituzione"
        if "concordato" in tl or "convenzione" in tl or "accordo" in tl:
            return "Convenzione"
        return ""
