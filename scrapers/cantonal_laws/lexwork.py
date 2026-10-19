"""
LexWork scraper — covers 17 cantons via identical REST API.

LexWork (by Ronorp AG) is an Angular SPA with a structured JSON API.
Each canton gets its own hostname but the API is identical.

API:
  /api/{lang}/texts_of_law/lightweight_index  → list all laws
  /api/{lang}/texts_of_law/{sr}/show_as_json  → full structured law

The JSON content is a nested tree:
  document → content (title) → children → articles → paragraphs → enumerations
"""
from __future__ import annotations

import html
import logging
import re
import time
from typing import Iterator

import requests

from scrapers.cantonal_laws import CANTON_LANG, LEXWORK_HOSTS, mount_retries

logger = logging.getLogger(__name__)

USER_AGENT = (
    "SwissCaselawBot/1.0 (https://github.com/jonashertner/caselaw-repo; "
    "legal research; respects rate limits)"
)


class LexWorkScraper:
    """Scrape cantonal laws from a LexWork portal."""

    REQUEST_DELAY: float = 0.5

    def __init__(self, canton: str):
        if canton not in LEXWORK_HOSTS:
            raise ValueError(f"No LexWork host for canton {canton}")
        self.canton = canton
        self.host = LEXWORK_HOSTS[canton]
        self.lang = CANTON_LANG[canton]
        self.base_url = f"https://{self.host}"
        self.session = requests.Session()
        mount_retries(self.session)
        self.session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        })
        self._last_request: float = 0
        self.portal_count: int | None = None

    def _get(self, path: str) -> requests.Response:
        """Rate-limited GET request."""
        elapsed = time.time() - self._last_request
        if elapsed < self.REQUEST_DELAY:
            time.sleep(self.REQUEST_DELAY - elapsed)
        url = f"{self.base_url}{path}"
        r = self.session.get(url, timeout=30)
        self._last_request = time.time()
        r.raise_for_status()
        return r

    def enumerate_laws(self) -> Iterator[dict]:
        """Yield law stubs from the lightweight_index endpoint."""
        r = self._get(f"/api/{self.lang}/texts_of_law/lightweight_index")
        data = r.json()

        total = 0
        stubs = []
        for _cat_id, laws in data.items():
            for law in laws:
                total += 1
                stubs.append({
                    "id": law["id"],
                    "sr_number": law["systematic_number"],
                    "title": law["title"],
                    "abrogated": law.get("abrogated", False),
                    "structured_document_id": law.get("structured_document_id"),
                })

        self.portal_count = total
        logger.info(f"{self.canton}: {total} laws in lightweight_index")

        # Sort by SR number for deterministic ordering
        stubs.sort(key=lambda s: s["sr_number"])
        yield from stubs

    def fetch_law(self, stub: dict) -> dict | None:
        """Fetch full law text + articles from show_as_json endpoint."""
        sr = stub["sr_number"]
        try:
            r = self._get(f"/api/{self.lang}/texts_of_law/{sr}/show_as_json")
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                logger.warning(f"{self.canton} {sr}: 404 (no structured content)")
                return None
            raise

        data = r.json()
        tol = data["text_of_law"]
        sv = tol.get("selected_version") or tol.get("current_version", {})

        jc = sv.get("json_content")
        if not jc:
            logger.debug(f"{self.canton} {sr}: no json_content, skipping")
            return None

        # Extract articles from the structured document tree
        doc = jc["document"]
        content_node = doc.get("content", {})
        articles = []
        full_text_parts = []
        self._walk_tree(content_node, articles, full_text_parts)

        full_text = "\n\n".join(full_text_parts)

        return {
            "canton": self.canton,
            "sr_number": tol["systematic_number"],
            "title": tol["title"],
            "abbreviation": tol.get("abbreviation") or "",
            "language": self.lang,
            "is_active": not tol.get("abrogated", False),
            "category": self._infer_category(tol),
            "original_url": f"{self.base_url}/app/{self.lang}/texts_of_law/{sr}",
            "version_active_since": sv.get("publication_enactment") or tol.get("publication_enactment") or "",
            "date_of_decision": tol.get("date_of_decision") or "",
            "text_source": "lexwork",
            "full_text": full_text,
            "articles": articles,
        }

    def _walk_tree(
        self,
        node: dict,
        articles: list[dict],
        full_text_parts: list[str],
        *,
        current_heading: str = "",
    ) -> None:
        """Recursively walk the LexWork document tree, extracting articles."""
        node_type = node.get("type", "")
        text_dict = node.get("text", {})
        title_text = text_dict.get(self.lang, "")
        html_content = node.get("html_content", {}).get(self.lang, "")

        if node_type == "title":
            # Section heading — remember for subsequent articles
            heading = title_text or self._html_to_text(html_content)
            if heading:
                number = node.get("number", {}).get(self.lang, "")
                if number:
                    heading = f"{number} {heading}"
                full_text_parts.append(heading)
                current_heading = heading

        elif node_type == "article":
            # Extract article number
            number_raw = node.get("number", {}).get(self.lang, "")
            art_num = self._clean_article_number(number_raw)

            # Article title/heading
            art_heading = title_text or ""

            # Collect paragraph texts from children
            para_parts = []
            for child in node.get("children", []):
                para_text = self._extract_paragraph(child)
                if para_text:
                    para_parts.append(para_text)

            art_text = "\n".join(para_parts)

            if art_text or art_heading:
                articles.append({
                    "article_num": art_num,
                    "heading": art_heading,
                    "text": art_text,
                })

                # Build full-text representation
                header = f"Art. {art_num}" if art_num else ""
                if art_heading:
                    header = f"{header} {art_heading}".strip()
                full_text_parts.append(header)
                if art_text:
                    full_text_parts.append(art_text)

            return  # Don't recurse further — children already processed

        # Recurse into children
        for child in node.get("children", []):
            self._walk_tree(child, articles, full_text_parts, current_heading=current_heading)

    def _extract_paragraph(self, node: dict) -> str:
        """Extract text from a paragraph or enumeration node."""
        node_type = node.get("type", "")
        html_content = node.get("html_content", {}).get(self.lang, "")

        if node_type == "paragraph":
            text = self._html_to_text(html_content)
            # Recursively get child enumerations
            child_texts = []
            for child in node.get("children", []):
                ct = self._extract_paragraph(child)
                if ct:
                    child_texts.append(ct)
            if child_texts:
                text = text + "\n" + "\n".join(child_texts)
            return text

        elif node_type == "enumeration":
            text = self._html_to_text(html_content)
            child_texts = []
            for child in node.get("children", []):
                ct = self._extract_paragraph(child)
                if ct:
                    child_texts.append(ct)
            if child_texts:
                text = text + "\n" + "\n".join(child_texts)
            return text

        elif node_type in ("footnote_reference", "footnote"):
            return ""

        else:
            # Generic: extract text from html_content
            return self._html_to_text(html_content)

    def _html_to_text(self, html_str: str) -> str:
        """Convert LexWork HTML snippets to clean text preserving structure."""
        if not html_str:
            return ""

        # Replace <br> and block elements with newlines
        text = re.sub(r"<br\s*/?>", "\n", html_str)
        text = re.sub(r"</(?:p|div|li|tr)>", "\n", text)

        # Extract number spans and text content
        # <span class='number'>1</span> → "1 "
        text = re.sub(r"<span\s+class=['\"]number['\"]>([^<]*)</span>", r"\1 ", text)
        # <span class='text_content'>...</span> → content
        text = re.sub(r"<span\s+class=['\"]text_content['\"]>", "", text)
        # <span class='article_symbol'>§</span> → §
        text = re.sub(r"<span\s+class=['\"]article_symbol['\"]>([^<]*)</span>", r"\1", text)

        # Strip remaining tags
        text = re.sub(r"<[^>]+>", "", text)

        # Decode HTML entities
        text = html.unescape(text)

        # Clean up whitespace (preserve newlines)
        lines = []
        for line in text.split("\n"):
            line = re.sub(r"[ \t]+", " ", line).strip()
            if line:
                lines.append(line)

        return "\n".join(lines)

    def _clean_article_number(self, raw: str) -> str:
        """Clean article number: '§&nbsp;1' → '1', 'Art.&nbsp;5a' → '5a'."""
        text = html.unescape(raw)
        text = re.sub(r"^[§Art.\s]+", "", text).strip()
        return text

    def _infer_category(self, tol: dict) -> str:
        """Infer law category from title patterns."""
        title = tol.get("title", "")
        if "verordnung" in title.lower():
            return "Verordnung"
        if "gesetz" in title.lower():
            return "Gesetz"
        if "dekret" in title.lower():
            return "Dekret"
        if "konkordat" in title.lower() or "vereinbarung" in title.lower():
            return "Konkordat"
        if "reglement" in title.lower():
            return "Reglement"
        if "verfassung" in title.lower():
            return "Verfassung"
        return ""
