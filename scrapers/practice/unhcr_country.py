"""
UNHCR country guidance (International Protection Considerations, Eligibility
Guidelines, Guidance Notes, Positions on Returns) — via ecoi.net
=========================================================================

  https://www.ecoi.net/en/document/{id}.html        document page
  https://www.ecoi.net/en/file/local/{id}/{file}    the UNHCR PDF

UNHCR's assessment of who from a country of origin needs international
protection, the guidance the Federal Administrative Court weighs in asylum
appeals. refworld.org and unhcr.org put every document behind a Cloudflare
challenge, which this project does not try to get around; ecoi.net (run by
the Austrian Red Cross, ACCORD, supported by UNHCR) republishes the same
PDFs, its robots.txt admits general crawlers, and its pages link each
document's other editions and language versions. UNHCR holds the copyright;
a permission request was drafted 2026-10-25 and the documents are taken in
meanwhile on Jonas's decision. Not part of any CC0 export (practice.db is
not exported).

Not on ecoi.net, so not here: the Handbook on Procedures and Criteria and
the Guidelines on International Protection Nos. 1-14 (only on the two
gated UNHCR sites; medbox.org disallows downloads in robots.txt).

Discovery. SEEDS holds, per country, the current edition verified on
2026-10-25. Each run reads that page and walks the related-documents list
(`ul.doc-list li.doc-listelement`) to further UNHCR documents on the same
country — earlier editions, the editions that replace them, translations —
up to MAX_HOPS away, so a new edition that replaces a seed is found without
a code change. Kept: UNHCR-authored documents in German, French, Italian or
English.

doc_number is shared by all editions of one country ("UNHCR country
guidance: Afghanistan"), so search_practice shows the newest edition by
default and include_superseded gives the one in force at the time of the
facts.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Iterator

from bs4 import BeautifulSoup

from .base import PracticeScraper

logger = logging.getLogger(__name__)

BASE = "https://www.ecoi.net"
DOC_URL = BASE + "/en/document/{id}.html"
UNHCR_SOURCE = "/en/source/11525.html"
MAX_HOPS = 3

# Current edition per country, verified 2026-10-25 (page title, UNHCR as
# author, PDF with a text layer).
SEEDS = {
    "Afghanistan": 2130459,   # Guidance Note - Update II, September 2025
    "Eritrea": 2142611,       # Guidance Note, April 2026
    "Syria": 2141685,         # International Protection Considerations, May 2026
    "Iraq": 2104031,          # International Protection Considerations - Update I, January 2024
    "Sudan": 2125819,         # Guidance Note, April 2025
    "Sri Lanka": 1344689,     # Eligibility Guidelines, 21 December 2012
    "Venezuela": 2009119,     # Guidance Note - Update I, May 2019
    "Libya": 1442373,         # Position on Returns - Update II, September 2018
    "Myanmar": 2109824,       # Guidance Note, May 2024
    "Nigeria": 1164735,       # International Protection Considerations (north-east), October 2013
}

_LANG = {"german": "de", "french": "fr", "italian": "it", "english": "en",
         "deutsch": "de", "französisch": "fr", "englisch": "en"}
_MONTHS = {m: i for i, m in enumerate((
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december"), 1)}


def _iso(published: str) -> str:
    """'30 August 2018' -> '2018-08-30'; 'September 2025' -> '2025-09'."""
    s = published.strip()
    for fmt in ("%d %B %Y",):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    m = re.match(r"([A-Za-z]+)\s+(\d{4})$", s)
    if m and m.group(1).lower() in _MONTHS:
        return f"{m.group(2)}-{_MONTHS[m.group(1).lower()]:02d}"
    m = re.search(r"\b(\d{4})\b", s)
    return m.group(1) if m else ""


def _field(text: str, label: str, stops: tuple[str, ...]) -> str:
    m = re.search(re.escape(label) + r"\s*(.*?)\s*(?:" + "|".join(map(re.escape, stops)) + r"|$)", text, re.S)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""


def parse_document(page_html: str) -> dict | None:
    """One ecoi.net document page -> metadata, PDF and related UNHCR documents."""
    soup = BeautifulSoup(page_html, "html.parser")
    h2 = soup.select_one("h2.original-title")
    if h2 is None:
        return None
    pdf = soup.select_one("a[href*='/file/local/']")
    dl = soup.find("dl")
    meta = re.sub(r"\s+", " ", dl.get_text(" ", strip=True)) if dl else ""
    stops = ("Original link:", "Document type:", "Language:", "Available on ecoi.net since:",
             "Document ID:", "Country:", "Source:", "Published:")
    doc_id = re.search(r"Document ID:\s*(\d+)", meta)
    related = []
    for li in soup.select("ul.doc-list li.doc-listelement[data-ecoi-id]"):
        if li.find("a", href=UNHCR_SOURCE) is None:
            continue
        metas = li.select("p.doc-metadata")
        related.append({"id": int(li["data-ecoi-id"]),
                        "country": metas[1].get_text(" ", strip=True) if len(metas) > 1 else ""})
    return {
        "id": int(doc_id.group(1)) if doc_id else None,
        "title": re.sub(r"\s+", " ", h2.get_text(" ", strip=True)),
        "country": _field(meta, "Country:", stops),
        "source": _field(meta, "Source:", stops),
        "published": _field(meta, "Published:", stops),
        "language": _LANG.get(_field(meta, "Language:", stops).lower(), ""),
        "doc_type": _field(meta, "Document type:", stops),
        "pdf_url": BASE + pdf["href"] if pdf else "",
        "related": related,
    }


class UnhcrCountryGuidanceScraper(PracticeScraper):
    SOURCE_KEY = "unhcr_country"
    ISSUING_AUTHORITY = "UNHCR"
    DEFAULT_DOC_TYPE = "country_guidance"
    REQUEST_DELAY = 3.0
    PDF_TIMEOUT = 120

    def __init__(self):
        super().__init__()
        self.session.headers["User-Agent"] = "OpenCaseLawBot/1.0 (+https://opencaselaw.ch; team@jonashertner.com)"

    def _make_doc_id(self, stub: dict) -> str:
        return f"{self.SOURCE_KEY}_{stub['ecoi_id']}"

    def _page(self, doc_id: int) -> dict | None:
        r = self.get(DOC_URL.format(id=doc_id))
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return parse_document(r.text)

    def discover_documents(self) -> Iterator[dict]:
        seen: set[int] = set()
        found = 0
        for country, seed in SEEDS.items():
            frontier = [(seed, 0)]
            while frontier:
                doc_id, hops = frontier.pop(0)
                if doc_id in seen:
                    continue
                seen.add(doc_id)
                doc = self._page(doc_id)
                if doc is None:
                    logger.warning(f"[unhcr_country] {country}: document {doc_id} not found")
                    continue
                if hops < MAX_HOPS:
                    frontier += [(r["id"], hops + 1) for r in doc["related"]
                                 if r["country"] == doc["country"] and r["id"] not in seen]
                if not doc["source"].startswith("UNHCR") or not doc["language"] or not doc["pdf_url"]:
                    continue
                found += 1
                yield {
                    "ecoi_id": doc_id,
                    "doc_number": f"UNHCR country guidance: {doc['country']}",
                    "title": doc["title"],
                    "date": _iso(doc["published"]),
                    "language": doc["language"],
                    "url": DOC_URL.format(id=doc_id),
                    "pdf_url": doc["pdf_url"],
                    "topics": [doc["country"], "UNHCR", doc["doc_type"], "Länderrichtlinien / country guidance"],
                }
        if found == 0:
            raise RuntimeError("[unhcr_country] no UNHCR document found from any seed — ecoi.net changed?")
        logger.info(f"[unhcr_country] {found} UNHCR documents on {len(SEEDS)} countries")
