"""
admin.ch "sd-web" download lists (Nuxt platform: weko.admin.ch, oak-bv.admin.ch,
comcom.admin.ch since the 2025 migration off Adobe AEM).

Every downloadable document is one anchor:

    <a class="download-item" href="https://…/dam/de/sd-web/<hash>/<file>.pdf">
      <h4 class="download-item__title">Vertikalbekanntmachung vom 12. Dezember 2022</h4>
      <p class="download-item__meta-info">PDF 443.03 kB 23. Juli 2025</p>
    </a>

The <h4> is the document title as the authority wrote it; the <p> carries the
file type, size and the file's last-modified date (NOT the issuance date — the
2025 migration re-stamped most files with a 2025/2026 date). The nearest
preceding heading names the section ("Archiv", "Aufgehobene Weisungen",
"Gültige Weisungen (inkl. Zusatzdokumente)"), which is the only place the
in-force / superseded status is stated.

Shared by the WEKO Bekanntmachungen and OAK BV practice scrapers; the WEKO
decision scraper (scrapers/weko.py) reads the same markup with its own parser.
"""
from __future__ import annotations

import re
from typing import Iterator
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .base import first_date_iso

# "PDF 443.03 kB 23. Juli 2025" appended to the anchor text when the <h4> is
# missing (old AEM markup) — the <h4> form never carries it.
_META_TAIL = re.compile(r"\s*PDF\s+[\d.,']+\s*[kKmMgG][bB].*$", re.IGNORECASE)


def _clean(text: str) -> str:
    return " ".join((text or "").split())


def download_items(html: str, base_url: str) -> Iterator[dict]:
    """Yield one dict per PDF download anchor, in page order:

        title      the <h4> text (or the anchor text without the meta tail)
        meta       the <p> meta text ("PDF 443.03 kB 23. Juli 2025"), may be ""
        file_date  ISO date parsed from `meta`, "" when absent
        pdf_url    absolute URL
        section    text of the nearest preceding h1/h2/h3, "" at page top

    Anchors are de-duplicated by URL (a page may list the same file twice).
    """
    soup = BeautifulSoup(html, "html.parser")
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/dam/" not in href:
            continue
        pdf_url = urljoin(base_url, href)
        path = urlparse(pdf_url).path.lower()
        if not (path.endswith(".pdf") or (a.get("download") or "").lower().endswith(".pdf")):
            continue
        if pdf_url in seen:
            continue
        seen.add(pdf_url)
        h4 = a.find("h4")
        title = _clean(h4.get_text(" ", strip=True)) if h4 else _META_TAIL.sub("", _clean(a.get_text(" ", strip=True)))
        p = a.find("p")
        meta = _clean(p.get_text(" ", strip=True)) if p else ""
        if not meta:
            m = re.search(r"PDF\s+[\d.,']+\s*[kKmMgG][bB].*$", _clean(a.get_text(" ", strip=True)), re.IGNORECASE)
            meta = m.group(0) if m else ""
        heading = a.find_previous(["h1", "h2", "h3"])
        yield {
            "title": title,
            "meta": meta,
            "file_date": first_date_iso(meta),
            "pdf_url": pdf_url,
            "section": _clean(heading.get_text(" ", strip=True)) if heading else "",
        }
