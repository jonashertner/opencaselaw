"""
ESchK Scraper (Eidg. Schiedskommission für die Verwertung von Urheberrechten)
=============================================================================
Scrapes the copyright-tariff Beschlüsse of the Federal Arbitration Commission from
eschk.admin.ch. The Beschlüsse are split across per-year pages /de/beschluesse-{YYYY}
(1991..present), each using the same admin.ch DAM "download-item" component as ESBK/ElCom:
  <a class="download-item" href="/dam/de/sd-web/{hash}/tarif-a-suisa-2024.pdf">
    <h4 class="download-item__title">Tarif A SUISA (Beschluss vom 3. November 2023)</h4>

ESchK first-instance tariff approvals are appealable to the BVGer; they are a verified
beyond-es gap (not aggregated by entscheidsuche). Docket = the PDF filename stem; the
decision date is parsed from the title ("Beschluss vom DD. Monat YYYY").
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timezone
from typing import Iterator
from urllib.parse import unquote, urljoin

from bs4 import BeautifulSoup

from base_scraper import BaseScraper
from models import Decision, detect_language, extract_citations, make_decision_id, parse_date
from scrapers import pdf_ocr
from scrapers.elcom import PUB_DATE_PATTERN, _extract_content_hash, _extract_pdf_text

logger = logging.getLogger(__name__)

BASE_URL = "https://www.eschk.admin.ch"
YEAR_URL = "https://www.eschk.admin.ch/de/beschluesse-{year}"
START_YEAR = 1991


class ESchKScraper(BaseScraper):
    """Scraper for ESchK (Federal Copyright Arbitration Commission) Beschlüsse."""

    REQUEST_DELAY = 2.0
    TIMEOUT = 120

    @property
    def court_code(self) -> str:
        return "eschk"

    def discover_new(self, since_date=None) -> Iterator[dict]:
        end_year = datetime.now(timezone.utc).year
        start_year = max(START_YEAR, since_date.year) if since_date else START_YEAR
        seen_hashes: set[str] = set()
        found = 0
        for year in range(end_year, start_year - 1, -1):
            try:
                resp = self.get(YEAR_URL.format(year=year))
            except Exception as e:
                logger.debug(f"[eschk] year {year} fetch failed: {e}")
                continue
            soup = BeautifulSoup(resp.text, "html.parser")
            for a in soup.find_all("a", class_="download-item", href=True):
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
                title = h4.get_text(strip=True) if h4 else ""
                docket = unquote(href.split("/")[-1]).rsplit(".pdf", 1)[0] or f"eschk-{year}"

                pm = PUB_DATE_PATTERN.search(title)
                # On a date-miss leave it None (do NOT fabricate "1. Januar {year}", which would
                # be a wrong, real-looking date); fetch_decision keeps decision_date=None.
                decision_date_str = f"{pm.group(1)}. {pm.group(2)} {pm.group(3)}" if pm else None

                decision_id = make_decision_id("eschk", docket)
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
                    "title": title or docket,
                }
        logger.info(f"[eschk] Found {found} new Beschlüsse ({len(seen_hashes)} unique PDFs)")

    def fetch_decision(self, stub: dict) -> Decision | None:
        pdf_url = stub["pdf_url"]
        docket = stub["docket_number"]
        try:
            resp = self.get(pdf_url)
        except Exception as e:
            logger.error(f"[eschk] Failed to download PDF for {docket}: {e}")
            return None
        full_text = _extract_pdf_text(resp.content)
        if not full_text or len(full_text.strip()) < pdf_ocr.MIN_TEXT_CHARS:
            # Image-only scans (tarif_b_15-11-2004, tarif_vn_2004) were re-downloaded
            # every night; OCR them first. A failed download above is NOT cached, only a
            # file that stays text-less after OCR.
            full_text = pdf_ocr.ocr_pdf_bytes(resp.content)
            if len(full_text) < pdf_ocr.MIN_TEXT_CHARS:
                logger.warning(
                    f"[eschk] No text extracted from {docket} (OCR included) — cached as gap for "
                    f"{self.state.GAP_TTL_DAYS} days"
                )
                self.state.mark_gap(make_decision_id("eschk", docket))
                return None
            logger.info(f"[eschk] OCR recovered {len(full_text)} chars for {docket}")
        full_text = self.clean_text(full_text)
        return Decision(
            decision_id=make_decision_id("eschk", docket),
            court="eschk",
            canton="CH",
            docket_number=docket,
            decision_date=parse_date(stub.get("decision_date", "")),
            language=detect_language(full_text),
            title=stub.get("title"),
            legal_area="Urheberrecht (Tarife)",
            decision_type="Beschluss",
            full_text=full_text,
            source_url=pdf_url,
            pdf_url=pdf_url,
            cited_decisions=extract_citations(full_text),
            scraped_at=datetime.now(timezone.utc),
        )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Scrape ESchK Beschlüsse")
    parser.add_argument("--since", type=str)
    parser.add_argument("--max", type=int, default=5)
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    for noisy in ("pdfminer", "pdfplumber", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    since = date.fromisoformat(args.since) if args.since else None
    scraper = ESchKScraper()
    decisions = scraper.run(since_date=since, max_decisions=args.max)
    scraper.mark_run_complete(decisions)
    for d in decisions:
        print(f"  {d.decision_id}  {d.decision_date}  {len(d.full_text)} chars  {(d.title or '')[:50]}")
    print(f"\nScraped {len(decisions)} ESchK Beschlüsse")
