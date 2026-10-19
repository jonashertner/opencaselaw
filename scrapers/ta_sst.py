"""
Swiss Sports Tribunal — Schweizer Sportgericht (SSG) / Tribunal du sport suisse (TSS) /
Tribunale dello sport svizzero (TDS)
====================================================================================

Scrapes the tribunal's own case-law page directly (original court data, no aggregator):

    https://www.sportstribunal.ch/rechtsprechung

History: until 2026-09 this scraper read entscheidsuche.ch stubs (es_ta_sst.jsonl). That
file stopped growing at 49 decisions / 2025-10-03 while the tribunal kept publishing, so
the corpus lagged the source by ~9 months. The stub path is gone; nothing here depends on
entscheidsuche any more.

Page shape (one static page listing every decision, two <h3> sections):

    <h3>Schweizer Sportgericht</h3>
        SSG / TSS / TDS awards and orders since the tribunal started in 2024
    <h3>Disziplinarkammer des Schweizer Sports von Swiss Olympic</h3>
        DK / CD decisions 2016-2024 of the predecessor body, published by the SSG

Each decision is one link:

    <a class="file pdf" href="https://www.sportstribunal.ch/customer/files/82/<file>.pdf">
        SSG 2025/E/65 – Schiedsspruch vom 28. Mai 2026</a>

Link text = docket(s) + separator + type + date, in the language of the decision:

    SSG 2025/DO/58 - Schiedsspruch vom 20. März 2026
    TSS 2025/E/64 - Sentence du 20 avril 2026
    TDS 2024/E/40 - Decisione del 2 Giugno 2025
    SSG 2025/E/59 & SSG 2025/E/60 - Abschreibungsverfügung vom 3. Oktober 2025  (one PDF, two dockets)
    SSG 2024/DO/22 - Entscheide vom 2. und 14. März 2025                         (two rulings, one docket)
    CD 2016/DO/1 - Décision du 3 mars 2016 resp. 7 avril 2016                    (rectified decision)

Identity: decision_id = make_decision_id("ta_sst", docket) with the docket exactly as
printed ("SSG 2025/E/65" → "ta_sst_SSG 2025_E_65"). These are the ids the corpus has
carried since 2026-03, so the existing rows stay valid and are skipped via state. A joint
PDF becomes one record keyed by its first docket, the full link text (all dockets) is kept
in `title`; an entry is skipped when ANY of its dockets is already known — the two joint
PDFs already in the corpus are keyed by their second docket.

Dates come from the link text via models.parse_date (same semantics as the rest of the
corpus), with the PDF file name as fallback for month-name typos on the page ("27 décember
2024"). Never fabricated: an undatable entry keeps decision_date=None.

Rate limiting: 2 s between requests. Volume: ~110 PDFs total, a handful of new ones per
month; a full first run takes ~4 minutes.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from datetime import date, datetime, timezone
from typing import Iterator
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from base_scraper import BaseScraper
from models import Decision, detect_language, extract_citations, make_decision_id, parse_date
from scrapers.elcom import _extract_pdf_text

logger = logging.getLogger(__name__)

COURT_CODE = "ta_sst"
BASE_URL = "https://www.sportstribunal.ch"
RECHTSPRECHUNG_URL = f"{BASE_URL}/rechtsprechung"

# Decisions of the predecessor body (2016-2024) sit under their own <h3> on the same page.
PREDECESSOR_CHAMBER = "Disziplinarkammer des Schweizer Sports"

# A PDF with less text than this has no usable text layer (scan) → skipped, cached as gap.
MIN_TEXT_CHARS = 100

# "SSG 2025/E/65", "TSS 2025/DO/61", "TDS 2024/E/40", "DK 2019/DO/1", "CD 2016/DO/1"
DOCKET_RE = re.compile(r"\b(SSG|TSS|TDS|DK|CD)\s?(\d{4})/([A-Z]{1,3})/(\d{1,4})\b")

# Type word at the start of the link-text tail → canonical decision_type (German labels
# for all three languages so the field filters consistently within the court).
# Order matters: longer/more specific keys first.
_TYPE_MAP: list[tuple[tuple[str, ...], str]] = [
    (("abschreibungsverfügung", "ordonnance de clôture", "ordonnance de radiation",
      "decreto di stralcio"), "Abschreibungsverfügung"),
    (("zwischenentscheid", "décision incidente", "decisione incidentale"), "Zwischenentscheid"),
    (("schiedsspruch", "schiedssprüche", "sentence", "lodo", "sentenza"), "Schiedsspruch"),
    (("verfügung", "ordonnance", "decreto"), "Verfügung"),
    (("entscheid", "décision", "decision", "decisione"), "Entscheid"),
]

# Accent-folded month names (de/fr/it, plus the typos seen on the page and in file names).
_FOLDED_MONTHS = {
    "januar": 1, "februar": 2, "marz": 3, "maerz": 3, "april": 4, "mai": 5, "juni": 6,
    "juli": 7, "august": 8, "september": 9, "oktober": 10, "november": 11, "dezember": 12,
    "janvier": 1, "fevrier": 2, "mars": 3, "avril": 4, "juin": 6, "juillet": 7, "aout": 8,
    "septembre": 9, "octobre": 10, "novembre": 11, "decembre": 12, "december": 12,
    "gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4, "maggio": 5, "giugno": 6,
    "luglio": 7, "agosto": 8, "settembre": 9, "ottobre": 10, "dicembre": 12,
}
_FOLDED_DATE_RE = re.compile(r"\b(\d{1,2})\.?\s+([a-z]+)\s+(\d{4})\b")
_NUMERIC_DATE_RE = re.compile(r"\b(\d{1,2})[ .\-](\d{1,2})[ .\-](\d{4})\b")


def _fold(text: str) -> str:
    """Lower-case and strip accents: 'Décember' → 'december', 'März' → 'marz'."""
    return "".join(
        c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)
    ).lower()


def _folded_date(text: str) -> date | None:
    """Accent/typo-tolerant month-name date, then dd-mm-yyyy; None if neither parses."""
    folded = _fold(text)
    for m in _FOLDED_DATE_RE.finditer(folded):
        month = _FOLDED_MONTHS.get(m.group(2))
        if month:
            try:
                return date(int(m.group(3)), month, int(m.group(1)))
            except ValueError:
                continue
    m = _NUMERIC_DATE_RE.search(folded)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            return None
    return None


def _parse_entry_date(tail: str, pdf_url: str) -> date | None:
    """Decision date from the link-text tail, falling back to the PDF file name."""
    d = parse_date(tail)
    if d:
        return d
    d = _folded_date(tail)
    if d:
        return d
    filename = pdf_url.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    return _folded_date(re.sub(r"[-_]+", " ", filename))


def _decision_type(tail: str) -> str:
    low = tail.lower()
    for keys, label in _TYPE_MAP:
        if low.startswith(keys):
            return label
    return "Entscheid"


def parse_listing(html: str) -> list[dict]:
    """Parse the Rechtsprechung page into one entry per PDF link, in page order.

    Entry: {"dockets": [...], "pdf_url", "title", "decision_type", "decision_date",
    "chamber"}. Links without a docket in their text (none on the page today) are ignored;
    a PDF linked twice is kept once.
    """
    soup = BeautifulSoup(html, "html.parser")
    entries: list[dict] = []
    seen_urls: set[str] = set()
    chamber: str | None = None
    for el in soup.find_all(["h3", "a"]):
        if el.name == "h3":
            heading = el.get_text(" ", strip=True).lower()
            if "disziplinarkammer" in heading:
                chamber = PREDECESSOR_CHAMBER
            elif "sportgericht" in heading:
                chamber = None
            continue
        href = el.get("href") or ""
        if not href.lower().endswith(".pdf"):
            continue
        text = " ".join(el.get_text(" ", strip=True).split())
        matches = list(DOCKET_RE.finditer(text))
        if not matches:
            logger.debug(f"[ta_sst] PDF link without docket ignored: {text[:80]!r}")
            continue
        pdf_url = urljoin(BASE_URL, href)
        if pdf_url in seen_urls:
            continue
        seen_urls.add(pdf_url)
        dockets = [f"{m.group(1)} {m.group(2)}/{m.group(3)}/{m.group(4)}" for m in matches]
        tail = re.sub(r"^[\s–\-:]+", "", text[matches[-1].end():])
        entries.append({
            "dockets": dockets,
            "pdf_url": pdf_url,
            "title": text,
            "decision_type": _decision_type(tail),
            "decision_date": _parse_entry_date(tail, pdf_url),
            "chamber": chamber,
        })
    return entries


class TaSSTScraper(BaseScraper):
    """Swiss Sports Tribunal scraper — sportstribunal.ch/rechtsprechung, PDFs by link."""

    REQUEST_DELAY = 2.0
    TIMEOUT = 60

    @property
    def court_code(self) -> str:
        return COURT_CODE

    def discover_new(self, since_date=None) -> Iterator[dict]:
        """Yield one stub per PDF on the page whose docket(s) are not yet known."""
        resp = self.get(RECHTSPRECHUNG_URL)
        entries = parse_listing(resp.text)
        logger.info(f"[ta_sst] {len(entries)} decisions listed on {RECHTSPRECHUNG_URL}")
        found = 0
        for entry in entries:
            ids = [make_decision_id(COURT_CODE, d) for d in entry["dockets"]]
            if any(self.state.is_known(i) for i in ids):
                continue
            d = entry["decision_date"]
            if since_date and d and d < since_date:
                continue
            found += 1
            yield {
                "docket_number": entry["dockets"][0],
                "dockets": entry["dockets"],
                "decision_date": d.isoformat() if d else "",
                "url": entry["pdf_url"],
                "pdf_url": entry["pdf_url"],
                "source_url": entry["pdf_url"],
                "title": entry["title"],
                "decision_type": entry["decision_type"],
                "chamber": entry["chamber"],
            }
        logger.info(f"[ta_sst] {found} new decisions to fetch")

    def fetch_decision(self, stub: dict) -> Decision | None:
        """Download the PDF, extract its text layer, build the Decision."""
        docket = stub["docket_number"]
        pdf_url = stub["url"]
        decision_id = make_decision_id(COURT_CODE, docket)
        try:
            resp = self.get(pdf_url)
        except Exception as e:
            logger.warning(f"[ta_sst] PDF download failed for {docket}: {e}")
            return None
        text = _extract_pdf_text(resp.content) or ""
        if len(text.strip()) < MIN_TEXT_CHARS:
            # Scan without a text layer (or an empty file): not re-probed nightly.
            logger.warning(
                f"[ta_sst] no text layer in PDF for {docket} ({len(text)} chars) — "
                f"cached as gap for {self.state.GAP_TTL_DAYS} days"
            )
            self.state.mark_gap(decision_id)
            return None
        text = self.clean_text(text)
        return Decision(
            decision_id=decision_id,
            court=COURT_CODE,
            canton="CH",
            chamber=stub.get("chamber"),
            docket_number=docket,
            decision_date=parse_date(stub.get("decision_date") or ""),
            language=detect_language(text),
            title=stub.get("title"),
            legal_area="Sportrecht",
            decision_type=stub.get("decision_type") or "Entscheid",
            full_text=text,
            source_url=stub.get("source_url") or pdf_url,
            pdf_url=pdf_url,
            cited_decisions=extract_citations(text),
            scraped_at=datetime.now(timezone.utc),
        )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Scrape the Swiss Sports Tribunal")
    parser.add_argument("--since", type=str)
    parser.add_argument("--max", type=int, default=5)
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    for noisy in ("pdfminer", "pdfplumber", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    since = date.fromisoformat(args.since) if args.since else None
    scraper = TaSSTScraper()
    decisions = scraper.run(since_date=since, max_decisions=args.max)
    scraper.mark_run_complete(decisions)
    for d in decisions:
        print(f"  {d.decision_id}  {d.decision_date}  {d.language}  {len(d.full_text)} chars  {(d.title or '')[:60]}")
    print(f"\nScraped {len(decisions)} Swiss Sports Tribunal decisions")
