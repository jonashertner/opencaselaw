"""
BGE Historical Scraper (DFR — Volumes 1-79, 1875-1953)
======================================================

Scrapes digitized historical BGE (Bundesgerichtsentscheide) from the
DFR (Dokumentationsstelle für Rechtsprechung) at servat.unibe.ch.

Architecture:
- Volume index pages at servat.unibe.ch/dfr/dfr_bge{NN}.html
  (NN = 00 for 1-9, 01 for 10-19, ..., 07 for 70-79)
- Individual decisions as HTML: servat.unibe.ch/dfr/c{SVVVPPP}.html
  or as PDF: www.fallrecht.ch/c{SVVVPPP}.pdf
  where S = section (1-5), VVV = volume (zero-padded), PPP+ = page
- Decision code example: c1001003 = section 1 (I), volume 001, page 003 = BGE 1 I 3
- Sections: 1=I (public law), 2=II (civil), 3=III (debt/bankruptcy),
  4=IV (criminal), 5=V (social insurance)

Coverage: BGE 1 (1875) through BGE 79 (1953), ~15,000 decisions
Rate limiting: 1.5 seconds (university server, be gentle)
"""
from __future__ import annotations

import logging
import re
import subprocess
from datetime import date, datetime, timezone
from typing import Iterator
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from base_scraper import BaseScraper
from models import (
    Decision,
    detect_language,
    extract_citations,
    make_decision_id,
)
from scrapers import pdf_ocr

logger = logging.getLogger(__name__)

DFR_BASE = "https://servat.unibe.ch/dfr"

# Volume index pages group volumes by decade
INDEX_URLS = [f"{DFR_BASE}/dfr_bge{i:02d}.html" for i in range(8)]

# BGE section codes: digit -> Roman numeral
SECTION_MAP = {
    "1": "I",    # Öffentliches Recht / Droit public
    "2": "II",   # Zivilrecht / Droit civil
    "3": "III",  # Schuldbetreibung und Konkurs / LP
    "4": "IV",   # Strafrecht / Droit pénal
    "5": "V",    # Sozialversicherungsrecht
}

# Match decision code in href: c{section 1 digit}{volume 3 digits}{page 3+ digits}
# Examples: c1001003 = BGE 1 I 3, c2045123 = BGE 45 II 123
DECISION_CODE_RE = re.compile(r"c([1-5])(\d{3})(\d{3,})")

# Volume year lookup (approximate: BGE 1 = 1875, BGE 79 = 1953)
VOLUME_YEAR = {v: 1874 + v for v in range(1, 80)}


def _extract_pdf_text(data: bytes) -> str:
    """Extract text from PDF bytes using fitz (PyMuPDF) with pdfplumber fallback."""
    try:
        import fitz
        doc = fitz.open(stream=data, filetype="pdf")
        return "\n\n".join(p.get_text() for p in doc)
    except ImportError:
        pass
    try:
        import pdfplumber
        import io
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            return "\n\n".join(p.extract_text() or "" for p in pdf.pages)
    except ImportError:
        pass
    return ""


# ---------------------------------------------------------------------------
# Image-only rulings (2026-09-17)
# ---------------------------------------------------------------------------
# 161 rulings of 1877 and 1909 exist only as scans on www.fallrecht.ch. The German
# ones are set in Fraktur, with French rulings in Antiqua on the same pages.
# Tesseract's Antiqua models turn blackletter into German-looking nonsense ("Die
# Natur diefer Klage ift", "bas Verhältnis quifchen", measured on BGE 3 I 457 and
# 35 I 773): long enough to pass every length check, served as the ruling, found by
# no search. OCR therefore runs only with the Fraktur script model (tessdata_best
# script/Fraktur), which reads both scripts. On a host without that model a scan
# stays a gap, re-probed weekly, rather than being ingested garbled.
OCR_LANG = "script/Fraktur"

# The model transcribes the long s as printed and renders the ch and ck ligatures
# as "<" and ">"; directly after a letter neither sign occurs in these volumes.
# Line-end hyphens stay as printed: a running header ends in "Schuldbetreibungs-" and
# German prose in "Zivil- und Strafrecht", so joining would fuse unrelated words.
_OCR_FIXES = (
    (re.compile("\u017f"), "s"),
    (re.compile(r"(?<=[A-Za-zÄÖÜäöü])<"), "ch"),
    (re.compile(r"(?<=[A-Za-zÄÖÜäöü])>"), "ck"),
)

_OCR_MODEL_STATE: dict[str, bool] = {}


def _ocr_model_installed() -> bool:
    """True when Tesseract lists OCR_LANG; asked once per process.

    pytesseract.get_languages() keeps only names made of lowercase letters and
    underscores, so it never reports "script/Fraktur"; the listing is read directly.
    """
    if "installed" not in _OCR_MODEL_STATE:
        try:
            import pytesseract

            proc = subprocess.run(
                [pytesseract.pytesseract.tesseract_cmd, "--list-langs"],
                capture_output=True, text=True, timeout=30,
            )
            # Tesseract has printed the listing on stdout or stderr depending on the version.
            listing = f"{proc.stdout or ''}\n{proc.stderr or ''}"
            installed = OCR_LANG in {line.strip() for line in listing.splitlines()}
        except Exception as e:
            logger.warning(f"[bge_historical] OCR unavailable: {e}")
            installed = False
        if not installed:
            logger.warning(
                f"[bge_historical] Tesseract model {OCR_LANG} is not installed; "
                f"image-only rulings stay gap-cached"
            )
        _OCR_MODEL_STATE["installed"] = installed
    return _OCR_MODEL_STATE["installed"]


def _normalise_ocr(text: str) -> str:
    for pattern, replacement in _OCR_FIXES:
        text = pattern.sub(replacement, text)
    return text


def _ocr_scan(data: bytes) -> str:
    """Text of an image-only PDF; "" when the Fraktur model is missing or OCR fails."""
    if not _ocr_model_installed():
        return ""
    return _normalise_ocr(pdf_ocr.ocr_pdf_bytes(data, lang=OCR_LANG))


class BGEHistoricalScraper(BaseScraper):
    """Scraper for historical BGE decisions (volumes 1-79, 1875-1953) from DFR."""

    REQUEST_DELAY = 1.5
    TIMEOUT = 60  # PDFs can be large
    MAX_ERRORS = 200
    MAX_NONE_RETURNS = 2000  # Many old PDFs have poor OCR
    # Some DFR pages return 404 or unparseable PDFs — cache those so they
    # don't count as daily "None"s. Re-probed weekly (GAP_TTL_DAYS).
    CACHE_NONE_AS_GAP = True

    # Decisions come from two hosts: HTML from servat.unibe.ch, PDF-only ones
    # from www.fallrecht.ch. When one host goes dark, every remaining stub on
    # it costs TIMEOUT x urllib3 retries (~4 min each), so a few hundred dead
    # stubs exhaust the 7200s systemd budget and the run is reported FAILED
    # having fetched nothing. www.fallrecht.ch went unreachable on 2026-07-26
    # and did exactly that. After this many consecutive connection failures,
    # skip the rest of that host for this run — nothing is written to state,
    # so the next run re-probes from scratch and recovers by itself.
    UNREACHABLE_HOST_STREAK = 3
    _host_failures: dict[str, int] | None = None

    @property
    def court_code(self) -> str:
        return "bge_historical"

    def discover_new(self, since_date=None) -> Iterator[dict]:
        """Crawl DFR index pages to find all BGE decision links for volumes 1-79."""
        found = 0
        seen_dockets = set()  # Avoid duplicates across index pages

        for index_url in INDEX_URLS:
            logger.info(f"[bge_historical] Scanning {index_url}")

            try:
                response = self.get(index_url)
            except Exception as e:
                logger.error(f"[bge_historical] Failed to fetch index {index_url}: {e}")
                continue

            soup = BeautifulSoup(response.text, "html.parser")

            for a in soup.find_all("a", href=True):
                href = a["href"]

                # Extract decision code from href
                # Matches both relative (c1001003.html) and absolute (fallrecht.ch/c1001003.pdf)
                m = DECISION_CODE_RE.search(href)
                if not m:
                    continue

                sect_num = m.group(1)
                vol = int(m.group(2))
                page = int(m.group(3))

                # Only historical volumes (1-79)
                if vol < 1 or vol > 79:
                    continue

                section = SECTION_MAP.get(sect_num, sect_num)
                bge_ref = f"BGE {vol} {section} {page}"
                docket = f"{vol}_{section}_{page}"

                if docket in seen_dockets:
                    continue
                seen_dockets.add(docket)

                decision_id = make_decision_id("bge_historical", docket)
                if self.state.is_known(decision_id):
                    continue

                # Resolve full URL
                is_pdf = href.endswith(".pdf")
                if href.startswith("http"):
                    full_url = href
                else:
                    full_url = urljoin(index_url, href)

                year = VOLUME_YEAR.get(vol, 1875)

                found += 1
                yield {
                    "docket_number": docket,
                    "bge_ref": bge_ref,
                    "volume": vol,
                    "section": section,
                    "page": page,
                    "year": year,
                    "url": full_url,
                    "is_pdf": is_pdf,
                }

        logger.info(f"[bge_historical] Found {found} new decisions to fetch")

    def fetch_decision(self, stub: dict) -> Decision | None:
        """Fetch a single historical BGE decision (HTML or PDF)."""
        url = stub["url"]
        docket = stub["docket_number"]
        bge_ref = stub["bge_ref"]

        if self._host_failures is None:
            self._host_failures = {}
        host = urlsplit(url).netloc
        if self._host_failures.get(host, 0) >= self.UNREACHABLE_HOST_STREAK:
            logger.debug(f"[bge_historical] {docket}: skipped, {host} unreachable")
            return None

        try:
            response = self.get(url)
        except Exception as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status is not None:
                # The host answered. Whatever the status says, it is reachable,
                # so this must not count toward the unreachable streak — only
                # connection-level failures (no response at all) do.
                self._host_failures[host] = 0
                if status == 404:
                    logger.debug(f"[bge_historical] {docket}: 404")
                else:
                    logger.warning(
                        f"[bge_historical] Failed to fetch {docket}: {e}")
                return None
            streak = self._host_failures.get(host, 0) + 1
            self._host_failures[host] = streak
            logger.warning(f"[bge_historical] Failed to fetch {docket}: {e}")
            if streak == self.UNREACHABLE_HOST_STREAK:
                logger.error(
                    f"[bge_historical] {host} unreachable after {streak} "
                    f"consecutive connection failures — skipping its remaining "
                    f"stubs this run (nothing marked known; next run re-probes)"
                )
            return None

        self._host_failures[host] = 0

        if stub["is_pdf"]:
            full_text = _extract_pdf_text(response.content)
            if len((full_text or "").strip()) < pdf_ocr.MIN_TEXT_CHARS:
                # Image-only scan: read it with the Fraktur model or leave it a gap.
                full_text = _ocr_scan(response.content)
                if full_text:
                    logger.info(
                        f"[bge_historical] {docket}: no text layer, read by OCR "
                        f"({len(full_text)} chars)"
                    )
        else:
            soup = BeautifulSoup(response.text, "html.parser")
            for tag in soup.find_all(["script", "style"]):
                tag.decompose()
            full_text = soup.get_text(separator="\n", strip=True)

        if not full_text or len(full_text.strip()) < 50:
            logger.debug(
                f"[bge_historical] {docket}: no text extracted "
                f"({'PDF' if stub['is_pdf'] else 'HTML'}, {len(response.content)} bytes)"
            )
            return None

        full_text = self.clean_text(full_text)
        lang = detect_language(full_text)
        decision_date = date(stub["year"], 1, 1)

        section_areas = {
            "I": "Öffentliches Recht",
            "II": "Zivilrecht",
            "III": "Schuldbetreibung und Konkurs",
            "IV": "Strafrecht",
            "V": "Sozialversicherungsrecht",
        }

        return Decision(
            decision_id=make_decision_id("bge_historical", docket),
            court="bge_historical",
            canton="CH",
            docket_number=docket,
            decision_date=decision_date,
            language=lang,
            title=bge_ref,
            legal_area=section_areas.get(stub["section"]),
            bge_reference=bge_ref,
            collection=bge_ref,
            full_text=full_text,
            source_url=url,
            cited_decisions=extract_citations(full_text) if len(full_text) > 200 else [],
            scraped_at=datetime.now(timezone.utc),
        )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Scrape historical BGE (1875-1953)")
    parser.add_argument("--max", type=int, default=5, help="Max decisions")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    for noisy in ("pdfminer", "pdfplumber", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    scraper = BGEHistoricalScraper()
    decisions = scraper.run(max_decisions=args.max)
    scraper.mark_run_complete(decisions)
    for d in decisions:
        print(f"  {d.decision_id}  {d.decision_date}  {len(d.full_text)} chars  {d.title}")
    print(f"\nScraped {len(decisions)} historical BGE decisions")
