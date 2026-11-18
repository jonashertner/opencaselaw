"""
BAFU Vollzugshilfen — Federal Office for the Environment
=========================================================

The BAFU "UV"-series (Umwelt-Vollzug). Currently ~400 documents
covering: Boden, Wasser, Wald, Klima, Lärm, Luft, Naturgefahren,
Altlasten, Abfall, Biodiversität, Stoffe, etc.

Index page (chronological, with filterable list — easiest single
endpoint that lists everything):
  https://www.bafu.admin.ch/de/liste-der-vollzugshilfen-in-chronologischer-reihenfolge

Each entry has the form:
  <article>
    <h3>Title (Year)</h3>
    <a href="/dam/de/sd-web/HASH/UV-NNNN_topic_DE.pdf"> ... PDF, 1.2 MB</a>
  </article>

Naming convention: ``UV-NNNN[_modifier]_DE.pdf`` where NNNN is the
publication number. Some legacy docs without UV-prefix.
"""
from __future__ import annotations

import logging
import re
from typing import Iterator
from urllib.parse import urljoin, unquote

from bs4 import BeautifulSoup

from .base import PracticeScraper

logger = logging.getLogger(__name__)

_BASE = "https://www.bafu.admin.ch"
INDEX_URL = f"{_BASE}/de/liste-der-vollzugshilfen-in-chronologischer-reihenfolge"

_UV_NUM = re.compile(r"UV[-_]?(\d{3,4}[a-z]?)", re.IGNORECASE)
_YEAR = re.compile(r"\((\d{4})\)\s*$")  # "Title (2024)" trailing year


class BafuVollzugshilfenScraper(PracticeScraper):
    SOURCE_KEY = "bafu_vollzug"
    ISSUING_AUTHORITY = "BAFU"
    DEFAULT_DOC_TYPE = "vollzugshilfe"
    REQUEST_DELAY = 1.5
    # "Altlasten: erfassen, bewerten, sanieren" is a 44-page Kodak scan with
    # no text layer; it came back "empty body" on 16 weekly runs to
    # 2026-11-14. OCR it, and index the title with a marker if OCR finds
    # nothing, like the other practice sources that carry old scans.
    OCR_SCANS = True
    NO_TEXT_LAYER_BODY = "[Textlayer fehlt: gescanntes PDF]"

    def discover_documents(self) -> Iterator[dict]:
        try:
            r = self.get(INDEX_URL)
            r.raise_for_status()
        except Exception as e:
            logger.warning("BAFU index fetch failed: %s", e)
            return

        soup = BeautifulSoup(r.text, "html.parser")
        seen: set[str] = set()

        # Each entry sits in an article-style block; PDF anchor + title nearby.
        # Iterate over every PDF anchor and reconstruct the surrounding block.
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if not href.lower().endswith(".pdf"):
                continue
            if "/dam/" not in href:
                continue
            pdf_url = urljoin(_BASE, href)
            if pdf_url in seen:
                continue
            seen.add(pdf_url)

            # Title heuristic — walk up to find the nearest heading
            title = ""
            container = a.find_parent(["article", "li", "div"])
            if container:
                for heading_tag in ("h2", "h3", "h4", "strong"):
                    h = container.find(heading_tag)
                    if h and h.get_text(strip=True):
                        title = re.sub(r"\s+", " ", h.get_text(" ", strip=True))
                        break
            if not title:
                # Fallback — derive from PDF filename
                fname = unquote(pdf_url.rsplit("/", 1)[-1])
                title = fname.replace(".pdf", "").replace("_", " ").replace("-", " ").strip()

            # UV number — from URL or title
            m = _UV_NUM.search(pdf_url) or _UV_NUM.search(title)
            doc_number = f"UV-{m.group(1)}" if m else ""

            # Year from title trailing "(YYYY)"
            ym = _YEAR.search(title)
            date_iso = f"{ym.group(1)}-01-01" if ym else ""

            yield {
                "pdf_url": pdf_url,
                "url": INDEX_URL,
                "title": title,
                "doc_number": doc_number,
                "date": date_iso,
                "language": "de",
                "doc_type": "vollzugshilfe",
                "topics": ["Umwelt-Vollzug"],
            }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    BafuVollzugshilfenScraper().run(max_new=10)  # smoke-test with first 10 only
