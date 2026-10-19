"""
ESBK Scraper (Eidgenössische Spielbankenkommission / Federal Gambling Board)
============================================================================
Scrapes published Strafbescheide / Strafverfügungen / Einziehungsbescheide and
Verwaltungssanktionen (gambling-law enforcement decisions) from esbk.admin.ch.

Since 2026 /de/rechtsprechung is only a hub ("Mehr über Strafrecht / Verwaltungsrecht")
without download tiles; the decisions live on /de/strafrecht and /de/verwaltungsrecht.
Reading the hub alone yielded "Found 0 new" with exit 0 for months (12 decisions missed
by 2026-09-14), so a listing without a single tile is now an error, not a quiet zero.

Same admin.ch DAM "download-item" component as ElCom (scrapers/elcom.py) — so the
DAM content-hash dedup and the PDF-text extraction are reused directly:
  <a class="download-item" href="/dam/de/sd-web/{hash}/62-2021-021-01-d.pdf">
    <h4 class="download-item__title">62-2021-021-01</h4>                 # the docket
    <p class="download-item__description">Strafbescheid der ESBK vom 20. August 2021</p>  # type + date

ESBK-specific vs ElCom: the title IS the docket (62-YYYY-NNN-NN), and the decision
date lives in the description ("... vom DD. Monat YYYY"). This is the post-2016 output
that entscheidsuche's CH_VB (VPB, ended 2016) does not carry — a verified beyond-es gap.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timezone
from typing import Iterator
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from base_scraper import BaseScraper
from models import Decision, detect_language, extract_citations, make_decision_id, parse_date
# Reuse ElCom's admin.ch DAM helpers (same infrastructure) rather than duplicating them.
from scrapers.elcom import PUB_DATE_PATTERN, _extract_content_hash, _extract_pdf_text

logger = logging.getLogger(__name__)

BASE_URL = "https://www.esbk.admin.ch"
LISTING_URL = f"{BASE_URL}/de/rechtsprechung"  # hub page since 2026: no tiles, kept for reference
LISTING_URLS = (
    f"{BASE_URL}/de/strafrecht",         # Strafbescheide, Strafverfügungen, Einziehungsbescheide
    f"{BASE_URL}/de/verwaltungsrecht",   # Verwaltungssanktionen (Art. 100 BGS)
)

# Most specific first ("Strafverfügung" before "Verfügung").
_TYPE_WORDS = (
    "Einziehungsbescheid", "Einstellungsverfügung", "Strafbescheid", "Strafverfügung",
    "Verwaltungssanktion", "Verfügung",
)


def _decision_type(*texts: str) -> str | None:
    for text in texts:
        for word in _TYPE_WORDS:
            if word in text:
                return word
    return None

# ESBK docket in the title / filename: 62-2021-021-01  (prefix-year-number-sub)
DOCKET_PATTERN = re.compile(r"(\d{2}-\d{4}-\d{2,4}(?:-\d{1,2})?)")


class ESBKScraper(BaseScraper):
    """Scraper for ESBK (Swiss Federal Gambling Board) decisions."""

    REQUEST_DELAY = 2.0
    TIMEOUT = 120

    @property
    def court_code(self) -> str:
        return "esbk"

    def discover_new(self, since_date=None) -> Iterator[dict]:
        seen_hashes: set[str] = set()
        found = 0
        tiles_total = 0
        for listing_url in LISTING_URLS:
            response = self.get(listing_url)
            soup = BeautifulSoup(response.text, "html.parser")
            tiles = soup.find_all("a", class_="download-item", href=True)
            tiles_total += len(tiles)
            if not tiles:
                logger.warning(f"[esbk] no download-item tiles on {listing_url} (page restructured?)")
            for stub in self._stubs_from_tiles(tiles, seen_hashes, since_date):
                found += 1
                yield stub
        if tiles_total == 0:
            raise RuntimeError(
                "[esbk] no download-item tiles on any listing page — esbk.admin.ch "
                "restructured again? (2026: /de/rechtsprechung became a hub)"
            )
        logger.info(f"[esbk] Found {found} new decisions ({len(seen_hashes)} unique PDFs on {len(LISTING_URLS)} pages)")

    def _stubs_from_tiles(self, tiles, seen_hashes: set[str], since_date) -> Iterator[dict]:
        for a in tiles:
            href = a["href"]
            if not href.endswith(".pdf"):
                continue
            content_hash = _extract_content_hash(href)
            if content_hash:
                if content_hash in seen_hashes:
                    continue
                seen_hashes.add(content_hash)
            pdf_url = href if href.startswith("http") else urljoin(BASE_URL, href)

            h4 = a.find("h4", class_="download-item__title")
            docket = (h4.get_text(strip=True) if h4 else "").strip()
            if not docket:
                m = DOCKET_PATTERN.search(href.split("/")[-1])
                docket = m.group(1) if m else ""
            if not docket:
                logger.debug(f"[esbk] no docket for {href[:80]}")
                continue

            desc_p = a.find("p", class_="download-item__description")
            desc = desc_p.get_text(" ", strip=True) if desc_p else ""
            title_text = h4.get_text(" ", strip=True) if h4 else ""
            # Strafrecht tiles: date in the description ("Strafbescheid der ESBK vom
            # 4. Februar 2026 ..."); Verwaltungssanktion tiles: date in the title
            # ("Verwaltungssanktion der ESBK vom 23. Februar 2021 (Art. 100 BGS)").
            decision_date_str = None
            pm = PUB_DATE_PATTERN.search(desc) or PUB_DATE_PATTERN.search(title_text)
            if pm:
                decision_date_str = f"{pm.group(1)}. {pm.group(2)} {pm.group(3)}"
            decision_type = _decision_type(desc, title_text)

            decision_id = make_decision_id("esbk", docket)
            if self.state.is_known(decision_id):
                continue
            if since_date and decision_date_str:
                parsed = parse_date(decision_date_str)
                if parsed and parsed < since_date:
                    continue

            yield {
                "docket_number": docket,
                "decision_date": decision_date_str or "",
                "pdf_url": pdf_url,
                "title": desc or docket,
                "decision_type": decision_type,
            }

    def fetch_decision(self, stub: dict) -> Decision | None:
        pdf_url = stub["pdf_url"]
        docket = stub["docket_number"]
        try:
            response = self.get(pdf_url)
        except Exception as e:
            logger.error(f"[esbk] Failed to download PDF for {docket}: {e}")
            return None

        full_text = _extract_pdf_text(response.content)
        if not full_text or len(full_text.strip()) < 50:
            logger.warning(f"[esbk] No text extracted from {docket} ({len(response.content)} bytes)")
            return None
        full_text = self.clean_text(full_text)

        return Decision(
            decision_id=make_decision_id("esbk", docket),
            court="esbk",
            canton="CH",
            docket_number=docket,
            decision_date=parse_date(stub.get("decision_date", "")),
            language=detect_language(full_text),
            title=stub.get("title"),
            legal_area="Spielbankenrecht",
            decision_type=stub.get("decision_type") or "Verfügung",
            full_text=full_text,
            source_url=pdf_url,
            pdf_url=pdf_url,
            cited_decisions=extract_citations(full_text),
            scraped_at=datetime.now(timezone.utc),
        )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Scrape ESBK decisions")
    parser.add_argument("--since", type=str, help="Start date YYYY-MM-DD")
    parser.add_argument("--max", type=int, default=5, help="Max decisions")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    for noisy in ("pdfminer", "pdfplumber", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    since = date.fromisoformat(args.since) if args.since else None
    scraper = ESBKScraper()
    decisions = scraper.run(since_date=since, max_decisions=args.max)
    scraper.mark_run_complete(decisions)
    for d in decisions:
        print(f"  {d.decision_id}  {d.decision_date}  {len(d.full_text)} chars  {(d.title or '')[:55]}")
    print(f"\nScraped {len(decisions)} ESBK decisions")
