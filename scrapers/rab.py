"""
RAB/ASR Scraper (Eidg. Revisionsaufsichtsbehörde / Federal Audit Oversight Authority)
=====================================================================================
Scrapes RAB enforcement Verfügungen from rab-asr.ch/de/verfuegungen-der-rab (Drupal).

Each decision is a tile:
  <div class="rab-download-tile">
    <div class="rab-download-tile__info"><p>RAB-Verfügung 2020-01</p></div>   # docket
    <ul class="rab-download-tile__items"><li>
      <a class="rab-download-tile__link" aria-label="Download Deutsch document"
         href="/sites/default/files/2025-10/Verfügung_der_RAB_vom_28__September_2020.pdf">de</a>
    </li>...</ul></div>
Paginated ?page=0..N; the decision date is encoded in the filename ("vom_DD__Monat_YYYY").
Beyond-es: federal audit-oversight enforcement, not aggregated by entscheidsuche.
"""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import date, datetime, timezone
from typing import Iterator
from urllib.parse import unquote, urljoin

from bs4 import BeautifulSoup

from base_scraper import BaseScraper
from models import Decision, detect_language, extract_citations, make_decision_id, parse_date
from scrapers.elcom import _extract_pdf_text  # same PDF-text helper

logger = logging.getLogger(__name__)

LISTING_URL = "https://www.rab-asr.ch/de/verfuegungen-der-rab"
BASE_URL = "https://www.rab-asr.ch"

DOCKET_RE = re.compile(r"(\d{4}-\d+)")                              # "RAB-Verfügung 2020-01" -> 2020-01
FN_DATE_RE = re.compile(r"vom_(\d{1,2})_+([A-Za-zäöüÄÖÜ]+)_(\d{4})")  # ...vom_28__September_2020.pdf


class RABScraper(BaseScraper):
    """Scraper for RAB/ASR (Swiss Federal Audit Oversight Authority) Verfügungen."""

    REQUEST_DELAY = 2.0
    TIMEOUT = 120
    MAX_PAGES = 30

    @property
    def court_code(self) -> str:
        return "rab"

    def discover_new(self, since_date=None) -> Iterator[dict]:
        seen_urls: set[str] = set()
        found = 0
        for page in range(self.MAX_PAGES):
            try:
                resp = self.get(f"{LISTING_URL}?page={page}")
            except Exception as e:
                logger.error(f"[rab] page {page} failed: {e}")
                break
            soup = BeautifulSoup(resp.text, "html.parser")
            tiles = soup.select("div.rab-download-tile")
            if not tiles:
                break  # genuinely empty page = end of pager
            # The "Leading Cases" tile block is static: ?page=N paginates the enforcement
            # digests underneath, not the tiles, so every page repeats the same 5 PDFs.
            # Stop as soon as a page brings no tile URL we have not seen on this run
            # (verified 2026-09-14: pages 0, 1, 19 and 25 all return the same hrefs) —
            # this used to cost 30 identical listing requests per night.
            page_urls = {
                urljoin(BASE_URL, a["href"]) if not a["href"].startswith("http") else a["href"]
                for a in soup.select("div.rab-download-tile a.rab-download-tile__link[href$='.pdf']")
            }
            if page > 0 and not (page_urls - seen_urls):
                logger.info(f"[rab] page {page} repeats the tile block — pager exhausted")
                break
            for tile in tiles:
                info = tile.select_one(".rab-download-tile__info p")
                m = DOCKET_RE.search(info.get_text(strip=True)) if info else None
                docket = m.group(1) if m else None

                links = tile.select("a.rab-download-tile__link[href$='.pdf']")
                if not links:
                    continue
                de = next((a for a in links if "Deutsch" in (a.get("aria-label") or "")), links[0])
                href = de["href"]
                pdf_url = href if href.startswith("http") else urljoin(BASE_URL, href)
                if pdf_url in seen_urls:
                    continue
                seen_urls.add(pdf_url)

                fn = unquote(href.split("/")[-1])
                fdm = FN_DATE_RE.search(fn)
                decision_date_str = f"{fdm.group(1)}. {fdm.group(2)} {fdm.group(3)}" if fdm else None
                if not docket:
                    # fall back to date/filename, but append a PDF-URL hash so two dateless
                    # tiles can never collapse to the same decision_id (elcom.py pattern).
                    url_hash = hashlib.sha1(pdf_url.encode()).hexdigest()[:6]
                    base = (decision_date_str or fn.rsplit(".", 1)[0]).replace(" ", "-")[:33]
                    docket = f"{base}-{url_hash}"

                decision_id = make_decision_id("rab", docket)
                if self.state.is_known(decision_id):
                    continue
                if since_date and decision_date_str:
                    parsed = parse_date(decision_date_str)
                    if parsed and parsed < since_date:
                        continue

                found += 1
                yield {
                    "docket_number": docket,
                    "decision_date": decision_date_str or "",
                    "pdf_url": pdf_url,
                    "title": f"RAB-Verfügung {docket}",
                }
        logger.info(f"[rab] Found {found} new Verfügungen ({len(seen_urls)} PDFs seen)")

    def fetch_decision(self, stub: dict) -> Decision | None:
        pdf_url = stub["pdf_url"]
        docket = stub["docket_number"]
        try:
            resp = self.get(pdf_url)
        except Exception as e:
            logger.error(f"[rab] Failed to download PDF for {docket}: {e}")
            return None
        full_text = _extract_pdf_text(resp.content)
        if not full_text or len(full_text.strip()) < 50:
            logger.warning(f"[rab] No text extracted from {docket}")
            return None
        full_text = self.clean_text(full_text)
        return Decision(
            decision_id=make_decision_id("rab", docket),
            court="rab",
            canton="CH",
            docket_number=docket,
            decision_date=parse_date(stub.get("decision_date", "")),
            language=detect_language(full_text),
            title=stub.get("title"),
            legal_area="Revisionsaufsichtsrecht",
            decision_type="Verfügung",
            full_text=full_text,
            source_url=pdf_url,
            pdf_url=pdf_url,
            cited_decisions=extract_citations(full_text),
            scraped_at=datetime.now(timezone.utc),
        )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Scrape RAB/ASR Verfügungen")
    parser.add_argument("--since", type=str)
    parser.add_argument("--max", type=int, default=5)
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    for noisy in ("pdfminer", "pdfplumber", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    since = date.fromisoformat(args.since) if args.since else None
    scraper = RABScraper()
    decisions = scraper.run(since_date=since, max_decisions=args.max)
    scraper.mark_run_complete(decisions)
    for d in decisions:
        print(f"  {d.decision_id}  {d.decision_date}  {len(d.full_text)} chars  {(d.title or '')[:50]}")
    print(f"\nScraped {len(decisions)} RAB Verfügungen")
