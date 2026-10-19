"""
SIL scraper — covers NE and GE cantonal law portals.

SIL (Système d'Information Législatif) is a server-rendered HTML platform
used by several French-speaking cantons. Laws are stored as Word-generated
HTML files accessible via predictable URL paths.

Structure:
  /DATA/program/books/{book}/content.htm  → systematic TOC
  /DATA/program/books/{book}/htm/{sr}.htm → individual law (Word HTML)
"""
from __future__ import annotations

import html
import logging
import re
import time
from typing import Iterator

import requests

from . import mount_retries
from .numbering import split_number_and_title

logger = logging.getLogger(__name__)

USER_AGENT = (
    "SwissCaselawBot/1.0 (https://github.com/jonashertner/caselaw-repo; "
    "legal research; respects rate limits)"
)

# SIL portal configuration per canton
SIL_CONFIG = {
    "NE": {
        "host": "rsn.ne.ch",
        "base_path": "/DATA/program/books/rsne",
        "language": "fr",
    },
    "GE": {
        "host": "silgeneve.ch",
        "base_path": "/legis/program/books/rsg",
        "language": "fr",
    },
}


class SILScraper:
    """Scrape cantonal laws from a SIL portal."""

    REQUEST_DELAY: float = 0.5

    def __init__(self, canton: str):
        if canton not in SIL_CONFIG:
            raise ValueError(f"No SIL config for canton {canton}")
        self.canton = canton
        cfg = SIL_CONFIG[canton]
        self.host = cfg["host"]
        self.lang = cfg["language"]
        self.base_url = f"https://{self.host}"
        self.data_base = f"{self.base_url}{cfg['base_path']}"
        self.session = requests.Session()
        mount_retries(self.session)
        self.session.headers.update({"User-Agent": USER_AGENT})
        self._last_request: float = 0
        self.portal_count: int | None = None

    def _get(self, url: str) -> requests.Response:
        """Rate-limited GET request."""
        elapsed = time.time() - self._last_request
        if elapsed < self.REQUEST_DELAY:
            time.sleep(self.REQUEST_DELAY - elapsed)
        r = self.session.get(url, timeout=30)
        self._last_request = time.time()
        r.raise_for_status()
        return r

    def enumerate_laws(self) -> Iterator[dict]:
        """Parse the content.htm table of contents to find all laws."""
        r = self._get(f"{self.data_base}/content.htm")
        # SIL uses windows-1252 encoding
        r.encoding = "windows-1252"
        html_text = r.text

        # Parse law entries: href='htm/{sr}.htm'>{sr} Title<
        pattern = re.compile(
            r"href=['\"]htm/([^'\"]+\.htm)['\"]>([^<]+)<",
            re.IGNORECASE,
        )

        stubs = []
        for match in pattern.finditer(html_text):
            filename = match.group(1)
            raw_title = match.group(2).strip()

            # Parse SR number from title: "101 Constitution de la ...",
            # or Geneva's "A 1 01 Acte d'union ...". Geneva's RSG numbers
            # are alphanumeric, so the numeric-only pattern this used to
            # apply never matched and every GE law fell through to the
            # filename slug (rsg_a1_01), which no practitioner cites and
            # which get_law cannot resolve. NE shares this scraper and is
            # numeric, so it takes the same branch it always did.
            sr_number, title = split_number_and_title(
                raw_title, fallback=filename.replace(".htm", ""))

            # Clean encoding artifacts
            title = html.unescape(title)

            stubs.append({
                "sr_number": sr_number,
                "title": title,
                "filename": filename,
                "url": f"{self.data_base}/htm/{filename}",
            })

        self.portal_count = len(stubs)
        logger.info(f"{self.canton}: {len(stubs)} laws in content.htm")

        # Sort by SR number
        stubs.sort(key=lambda s: s["sr_number"])
        yield from stubs

    def fetch_law(self, stub: dict) -> dict | None:
        """Fetch and parse a Word-generated HTML law page."""
        try:
            r = self._get(stub["url"])
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                logger.warning(f"{self.canton} {stub['sr_number']}: 404")
                return None
            raise

        # SIL uses windows-1252
        r.encoding = "windows-1252"
        law_html = r.text

        # Extract title from <title> tag
        title_match = re.search(r"<title>([^<]+)</title>", law_html)
        page_title = html.unescape(title_match.group(1).strip()) if title_match else stub["title"]

        # Parse articles from the HTML body
        articles, full_text = self._parse_law_html(law_html)

        if not full_text.strip():
            return None

        return {
            "canton": self.canton,
            "sr_number": stub["sr_number"],
            "title": stub["title"],
            "abbreviation": self._extract_abbreviation(page_title),
            "language": self.lang,
            "is_active": True,
            "category": self._infer_category(stub["title"]),
            "original_url": stub["url"],
            "version_active_since": "",
            "text_source": "sil",
            "full_text": full_text,
            "articles": articles,
        }

    def _parse_law_html(self, html_text: str) -> tuple[list[dict], str]:
        """Parse Word-generated HTML into articles and full text.

        Returns (articles, full_text).
        """
        # Extract body content
        body_match = re.search(r"<body[^>]*>(.*)</body>", html_text, re.DOTALL | re.IGNORECASE)
        if not body_match:
            return [], ""

        body = body_match.group(1)

        # Remove scripts and styles
        body = re.sub(r"<script[^>]*>.*?</script>", "", body, flags=re.DOTALL | re.IGNORECASE)
        body = re.sub(r"<style[^>]*>.*?</style>", "", body, flags=re.DOTALL | re.IGNORECASE)

        # Convert to text preserving structure
        # Replace block elements with newlines
        body = re.sub(r"<br\s*/?>", "\n", body)
        body = re.sub(r"</(?:p|div|h[1-6]|li|tr)>", "\n", body, flags=re.IGNORECASE)
        body = re.sub(r"<(?:p|div|h[1-6]|li|tr)[^>]*>", "\n", body, flags=re.IGNORECASE)

        # Strip remaining tags
        text = re.sub(r"<[^>]+>", "", body)
        text = html.unescape(text)

        # Clean up whitespace
        lines = []
        for line in text.split("\n"):
            line = re.sub(r"[ \t]+", " ", line).strip()
            if line:
                lines.append(line)

        full_text = "\n".join(lines)

        # Segment into articles
        articles = self._segment_articles(full_text)

        return articles, full_text

    def _segment_articles(self, text: str) -> list[dict]:
        """Segment text into articles using Art./§ patterns."""
        # Pattern: Art. N or § N at start of line
        pattern = re.compile(
            r"^(Art\.\s*(\d+[a-z]?(?:bis|ter|quater|quinquies|sexies)?)\s*(.*))",
            re.MULTILINE,
        )

        articles = []
        matches = list(pattern.finditer(text))

        for i, match in enumerate(matches):
            art_num = match.group(2)
            rest_of_line = match.group(3).strip()

            # Text extends from this match to the next article
            start = match.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            art_text = text[start:end].strip()

            # Heading is the remainder on the same line as Art. N
            heading = rest_of_line

            articles.append({
                "article_num": art_num,
                "heading": heading,
                "text": art_text,
            })

        return articles

    def _extract_abbreviation(self, title: str) -> str:
        """Extract abbreviation from title: '... (Cst. NE), du ...' → 'Cst. NE'."""
        m = re.search(r"\(([A-Z][^)]{0,30})\)", title)
        return m.group(1) if m else ""

    def _infer_category(self, title: str) -> str:
        """Infer law category from French title patterns."""
        tl = title.lower()
        if "ordonnance" in tl:
            return "Ordonnance"
        if "loi" in tl:
            return "Loi"
        if any(w in tl for w in ["décret", "decret"]):
            return "Décret"
        if "règlement" in tl or "reglement" in tl:
            return "Règlement"
        if "constitution" in tl:
            return "Constitution"
        if "concordat" in tl or "convention" in tl or "accord" in tl:
            return "Convention"
        if any(w in tl for w in ["arrêté", "arrete"]):
            return "Arrêté"
        return ""
