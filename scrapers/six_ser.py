"""
SIX Exchange Regulation — sanction decisions
============================================

SIX Exchange Regulation AG (SER) enforces the listing rules of SIX Swiss
Exchange — ad hoc publicity, financial reporting, management transactions,
regular reporting obligations, participant duties — under the self-regulation
the FinfraG delegates to the exchange. Its decisions are published, mostly
anonymised, on ser-ag.com:

  * Sanktionsbescheide of SER itself (SB-AHP-III/24, SER-MT-I/20 …),
  * decisions of the Sanktionskommission (SaKo-X/2023 …),
  * arbitral awards and settlements of the Schiedsgericht reviewing them,
  * decisions of the Beschwerdeinstanz and of Listing & Admission.

The two Federal Supreme Court judgments and the one Federal Administrative
Court judgment SER also lists are in the corpus under bger/bvger already and
are skipped here.

Listing: the page's React widget reads an AEM endpoint,
``<page>/_jcr_content/root/contentparsys/parsys/assets_taxonomy_filt.results.json``,
which returns every decision with its title (the SER reference), a
description ("KR 53, DAH 5 | <summary> | Fahrlässigkeit"), the decision date
(entryIntoForceDate), the publication date and the PDF URL.

Language: one row per decision, as everywhere in the decision corpus (the
build keeps one row per court + docket + date, so language versions of one
decision would collapse anyway). The row is the file SER's German page links.
Its "-de" file name does not mean German: SB-AHP-III/24 is a French
"Ordonnance de sanction" under that name. The language is detected from the
text. English courtesy translations are never ingested (no Swiss body may
carry language 'en').

Licence: SER is a private company and claims copyright over its site; Jonas
decided on 2026-11-06 to ingest the sanction decisions (official-function
decisions of an exchange regulator).
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timezone
from typing import Iterator

from base_scraper import BaseScraper
from models import Decision, detect_language, extract_citations, make_decision_id
from scrapers.elcom import _extract_pdf_text  # same PDF-text helper as rab / weko

logger = logging.getLogger(__name__)

BASE_URL = "https://www.ser-ag.com"
LISTING_PAGE = f"{BASE_URL}/de/resources/sanctions.html"
RESULTS_JSON = (f"{BASE_URL}/de/resources/sanctions/_jcr_content/root/contentparsys/parsys/"
                "assets_taxonomy_filt.results.json")

# Markers of court judgments SER lists but which the corpus holds as bger/bvger.
_SKIP_MARKERS = re.compile(r"Bundesgericht|Bundesverwaltungsgericht|Federal (Supreme |Administrative )?Court|"
                           r"Tribunal f[ée]d[ée]ral", re.I)
_ARBITRATION = re.compile(r"^(Schiedsentscheid|Schiedsspruch|Vergleich des Schiedsgerichts|Arbitration|Settlement)", re.I)
_REF = re.compile(r"\b((?:SaKo|SB|SER|BI|AB)(?:[- ][A-Za-z0-9]+)*[- ]?(?:[IVXLC]+|\d{3})/\d{2,4}"
                  r"(?:\s*/\s*[A-Z]+-[IVXLC]+/\d{2,4})*)")
_DECISION_TYPE = (
    (re.compile(r"^Vergleich", re.I), "Vergleich"),
    (_ARBITRATION, "Schiedsentscheid"),
    (re.compile(r"^SaKo", re.I), "Sanktionsentscheid"),
    (re.compile(r"^(SB|SER)", re.I), "Sanktionsbescheid"),
)
_LANGS = {"de", "fr", "it"}


def _clean(s: str) -> str:
    return " ".join((s or "").split())


def _ms_date(ms) -> date | None:
    if not ms:
        return None
    try:
        return datetime.fromtimestamp(int(ms) / 1000, timezone.utc).date()
    except (TypeError, ValueError, OSError):
        return None


def docket_for(item: dict) -> str:
    """SER's own reference. Arbitral awards and settlements are prefixed
    "Schiedsgericht" so they do not share a docket with the decision they
    review; items without a reference use the PDF's file stem."""
    title = _clean(item.get("title"))
    m = _REF.search(title)
    if m:
        ref = re.sub(r"\s+", " ", m.group(1)).strip()
        return f"Schiedsgericht {ref}" if _ARBITRATION.match(title) else ref
    stem = item["url"].rsplit("/", 1)[-1].rsplit(".", 1)[0]
    stem = re.sub(r"[-_]de$", "", stem)
    return stem.upper()


def decision_type_for(item: dict, docket: str) -> str:
    title = _clean(item.get("title"))
    for rx, label in _DECISION_TYPE:
        if rx.search(title) or rx.search(docket):
            return label
    marker = item.get("marker") or ""
    if "Beschwerde" in marker or docket.startswith(("BI-", "AB-")):
        return "Beschwerdeentscheid"
    return "Entscheid"


def parse_results(data: dict) -> list[dict]:
    """The results.json -> one stub per decision (court judgments skipped)."""
    stubs, seen = [], set()
    for item in data.get("results") or []:
        url = item.get("url") or ""
        if not url.lower().endswith(".pdf"):
            continue
        if _SKIP_MARKERS.search(item.get("marker") or ""):
            continue
        docket = docket_for(item)
        if docket in seen:
            docket = f"{docket} {url.rsplit('/', 1)[-1].rsplit('.', 1)[0]}"
        seen.add(docket)
        desc = _clean(item.get("description"))
        parts = [p.strip() for p in desc.split("|")]
        summary = parts[1] if len(parts) >= 2 else desc
        stubs.append({
            "docket_number": docket,
            "pdf_url": url if url.startswith("http") else BASE_URL + url,
            "decision_date": _ms_date(item.get("entryIntoForceDate")),
            "publication_date": _ms_date(item.get("publicationDate")),
            "chamber": _clean(item.get("marker")) or None,
            "regeste": desc or None,
            "title": f"{docket}: {summary}" if summary else docket,
            "decision_type": decision_type_for(item, docket),
            "topics": item.get("businessTopic") or [],
            "sanction": _clean(item.get("freeText")),
        })
    return stubs


class SIXSERScraper(BaseScraper):
    """SIX Exchange Regulation sanction decisions."""

    REQUEST_DELAY = 2.0
    TIMEOUT = 120

    @property
    def court_code(self) -> str:
        return "six_ser"

    def discover_new(self, since_date=None) -> Iterator[dict]:
        try:
            resp = self.get(RESULTS_JSON)
            data = resp.json()
        except Exception as e:
            logger.error(f"[six_ser] listing failed: {e}")
            return
        stubs = parse_results(data)
        logger.info(f"[six_ser] {len(stubs)} decisions listed")
        found = 0
        for stub in stubs:
            decision_id = make_decision_id("six_ser", stub["docket_number"])
            if self.state.is_known(decision_id):
                continue
            if since_date and stub["decision_date"] and stub["decision_date"] < since_date:
                continue
            found += 1
            yield stub
        logger.info(f"[six_ser] {found} new decisions")

    def fetch_decision(self, stub: dict) -> Decision | None:
        docket = stub["docket_number"]
        try:
            resp = self.get(stub["pdf_url"])
        except Exception as e:
            logger.error(f"[six_ser] PDF download failed for {docket}: {e}")
            return None
        if not resp.content.lstrip()[:5].startswith(b"%PDF"):
            logger.warning(f"[six_ser] {docket}: not a PDF ({stub['pdf_url']})")
            return None
        full_text = _extract_pdf_text(resp.content)
        if not full_text or len(full_text.strip()) < 200:
            logger.warning(f"[six_ser] no text extracted for {docket}")
            return None
        full_text = self.clean_text(full_text)
        language = detect_language(full_text)
        if language not in _LANGS:
            logger.warning(f"[six_ser] {docket}: language {language!r} not de/fr/it, skipped")
            return None
        return Decision(
            decision_id=make_decision_id("six_ser", docket),
            court="six_ser",
            canton="CH",
            chamber=stub.get("chamber"),
            docket_number=docket,
            decision_date=stub.get("decision_date"),
            publication_date=stub.get("publication_date"),
            language=language,
            title=stub.get("title"),
            legal_area="Börsenrecht (Kotierungsrecht)",
            regeste=stub.get("regeste"),
            decision_type=stub.get("decision_type"),
            full_text=full_text,
            source_url=LISTING_PAGE,
            pdf_url=stub["pdf_url"],
            cited_decisions=extract_citations(full_text),
            scraped_at=datetime.now(timezone.utc),
        )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Scrape SIX Exchange Regulation sanction decisions")
    parser.add_argument("--max", type=int, default=5)
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    scraper = SIXSERScraper()
    decisions = scraper.run(max_decisions=args.max)
    scraper.mark_run_complete(decisions)
    for d in decisions:
        print(f"  {d.decision_id}  {d.decision_date}  {d.language}  {len(d.full_text)} chars  {(d.title or '')[:60]}")
