"""
WEKO Scraper (Wettbewerbskommission)
======================================

Scrapes published decisions from the Swiss Competition Commission (WEKO/COMCO)
at weko.admin.ch.

Architecture:
- Nuxt SSR app (same platform as ComCom, migrated from Adobe AEM ~2025)
- Single listing page at /de/entscheide with all PDF links
- All decisions are PDF-only (some 20+ MB)
- PDF links under /dam/{lang}/sd-web/{hash}/ path

Coverage: ~230 decisions
Rate limiting: 2.0 seconds (large PDFs)

Praxis Binnenmarktgesetz (added 2026-09-07 on a user's request): the hub
/de/praxis-binnenmarktgesetz links three WEKO pages — /de/weko (Marktzugang),
/de/weko-2 (Konzessionen), /de/weko-3 (Beschaffungen) — holding ~44
Empfehlungen, Gutachten and Stellungnahmen under Art. 8 and 10 BGBM that the
Entscheide listing never shows (1997 to 2023, many only in the RPW). Same
markup, different title grammar ("Empfehlung vom 27. Mai 2019 betreffend …"
instead of "Name: Verfügung vom …"). The hub's other pages list Bundesgericht
and cantonal rulings that the corpus already holds under their own courts and
cantonal authority decisions that are not the WEKO's; both are left alone.
These pages are a static archive, so the `since_date` filter is not applied
to them — the state's is_known() check keeps the nightly run to one fetch per
page once they are in.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timezone
from typing import Iterator
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from base_scraper import BaseScraper
from models import (
    Decision,
    detect_language,
    extract_citations,
    make_decision_id,
    parse_date,
)

logger = logging.getLogger(__name__)

LISTING_URL = "https://www.weko.admin.ch/de/entscheide"
BASE_URL = "https://www.weko.admin.ch"

# (url, legal_area, is_archive). An archive page gets neither the since_date
# filter nor the file-date fallback: its files carry the 2014/2021 migration
# stamp, which is not a decision date, and a title without "vom …" stays undated.
BGBM_LISTING_URLS = (
    (BASE_URL + "/de/weko", "Binnenmarktrecht", True),
    (BASE_URL + "/de/weko-2", "Binnenmarktrecht", True),
    (BASE_URL + "/de/weko-3", "Binnenmarktrecht", True),
)
LISTINGS = ((LISTING_URL, "Wettbewerbsrecht", False),) + BGBM_LISTING_URLS

# BGBM titles lead with the document type: "Empfehlung vom …", "Gutachten der
# Wettbewerbskommission vom …", "Expertise du …", "Recommandation du …".
LEAD_TYPE_PATTERN = re.compile(
    r"^(Empfehlung(?:en)?|Gutachten|Stellungnahme|Vernehmlassung|Expertise|"
    r"Recommandations?|Raccomandazione|Prise\s+de\s+position|Avis)\b",
    re.IGNORECASE,
)
LEAD_TYPE_NORMAL = {
    "empfehlungen": "Empfehlung", "expertise": "Gutachten", "recommandation": "Empfehlung",
    "recommandations": "Empfehlung", "raccomandazione": "Empfehlung",
    "prise de position": "Stellungnahme", "avis": "Gutachten",
}
# "(Französisch)", "(französisch)", "(nur auf Französich)", "(italienische Version)"
LANG_TAG_PATTERN = re.compile(r"\s*\((?:nur\s+auf\s+)?(?:franz|ital|frances|italian)[^)]*\)\s*$", re.IGNORECASE)

# Parse title: "Name: Type vom DD. Monat YYYY (PDF, size, DD.MM.YYYY)"
TITLE_PATTERN = re.compile(
    r"^(.+?):\s*(Verfügung|Schlussbericht|Stellungnahme|Beratung|Gutachten|Empfehlung|Sanktionsverfügung|Einstellungsverfügung|Genehmigung|Prüfung|Abklärung|Untersuchung|Vorsorgliche Massnahme|Zwischenverfügung)(?:\s+der\s+WEKO|\s+des\s+Sekretariats)?\s+vom\s+(.+?)(?:\s*\(|\s*$)",
    re.IGNORECASE,
)

# Fallback: extract date from "(PDF, size, DD.MM.YYYY)" suffix (old AEM format)
PUB_DATE_PATTERN = re.compile(r"\(PDF,\s*[\d.,]+\s*[kKmMgG][bB],?\s*(\d{2}\.\d{2}\.\d{4})\)")

# New Nuxt format: publication date "DD. Monat YYYY" in <p> metadata
META_DATE_PATTERN = re.compile(
    r"(\d{1,2})\.?\s*(Januar|Februar|März|April|Mai|Juni|Juli|August|September|Oktober|November|Dezember)\s+(\d{4})",
    re.IGNORECASE,
)

# Date from "vom DD. Monat YYYY" in title (German)
VOM_DATE_PATTERN = re.compile(
    r"vom\s+(\d{1,2})\.?\s*(Januar|Februar|März|April|Mai|Juni|Juli|August|September|Oktober|November|Dezember)\s+(\d{4})",
    re.IGNORECASE,
)

# Date from "du DD mois YYYY" in title (French)
DU_DATE_PATTERN = re.compile(
    r"du\s+(\d{1,2})\.?\s*(?:er)?\s*(janvier|février|mars|avril|mai|juin|juillet|août|septembre|octobre|novembre|décembre)\s+(\d{4})",
    re.IGNORECASE,
)


def _slugify(text: str) -> str:
    """Create a filesystem-safe slug from text."""
    text = text.lower().strip()
    text = re.sub(r"[äÄ]", "ae", text)
    text = re.sub(r"[öÖ]", "oe", text)
    text = re.sub(r"[üÜ]", "ue", text)
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    return text[:80]


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


def parse_listing(html: str, *, legal_area: str = "Wettbewerbsrecht",
                  file_date_fallback: bool = True) -> list[dict]:
    """Parse one sd-web download list into decision stubs (no network, no
    state). Shared by the Entscheide listing and the BGBM pages. With
    file_date_fallback a title without "vom …" is dated by the file's
    publication date (right on the Entscheide page, wrong on the archives)."""
    soup = BeautifulSoup(html, "html.parser")
    stubs: list[dict] = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        # PDF links under /dam/ path
        if "/dam/" not in href or not href.lower().endswith(".pdf"):
            continue

        pdf_url = urljoin(BASE_URL, href)

        # New Nuxt layout: title in <h4>, metadata in <p> inside the <a>
        h4 = a.find("h4")
        title = h4.get_text(strip=True) if h4 else a.get_text(strip=True)
        if not title:
            continue

        # Clean title: strip "(PDF, size, DD.MM.YYYY)" suffix (old AEM format)
        title = re.sub(r"\s*\(PDF,\s*[\d.,]+\s*[kKmMgG][bB],?\s*\d{2}\.\d{2}\.\d{4}\)\s*$", "", title).strip()
        # Strip language tags
        title_clean = LANG_TAG_PATTERN.sub("", title).strip()

        decision_date_str = None
        doc_type = None

        m = TITLE_PATTERN.match(title)
        if m:
            case_name = m.group(1).strip()
            doc_type = m.group(2).strip()
        else:
            case_name = title_clean.split(":")[0].strip() if ":" in title_clean else title_clean
            lead = LEAD_TYPE_PATTERN.match(title_clean)
            if lead:
                word = " ".join(lead.group(1).lower().split())
                doc_type = LEAD_TYPE_NORMAL.get(word, lead.group(1).capitalize())

        # Try to extract decision date from "vom DD. Monat YYYY" or "du DD mois YYYY"
        vom_m = VOM_DATE_PATTERN.search(title)
        if vom_m:
            decision_date_str = f"{vom_m.group(1)}. {vom_m.group(2)} {vom_m.group(3)}"
        else:
            du_m = DU_DATE_PATTERN.search(title)
            if du_m:
                decision_date_str = f"{du_m.group(1)} {du_m.group(2)} {du_m.group(3)}"

        # Fallback: publication date from <p> metadata (Nuxt) or old format
        pub_date_str = None
        p_tags = a.find_all("p")
        meta_text = " ".join(p.get_text(strip=True) for p in p_tags)
        meta_m = META_DATE_PATTERN.search(meta_text)
        if meta_m:
            pub_date_str = f"{meta_m.group(1)}. {meta_m.group(2)} {meta_m.group(3)}"
        else:
            pub_m = PUB_DATE_PATTERN.search(a.get_text(strip=True))
            pub_date_str = pub_m.group(1) if pub_m else None

        # Build docket from case name + date
        date_suffix = ""
        if decision_date_str:
            parsed = parse_date(decision_date_str)
            if parsed:
                date_suffix = f"-{parsed.isoformat()}"
        docket = _slugify(case_name) + date_suffix

        stubs.append({
            "docket_number": docket,
            "decision_date": decision_date_str or (pub_date_str if file_date_fallback else None) or "",
            "pdf_url": pdf_url,
            "title": title_clean,
            "case_name": case_name,
            "doc_type": doc_type,
            "pub_date": pub_date_str,
            "legal_area": legal_area,
        })
    return stubs


class WEKOScraper(BaseScraper):
    """Scraper for WEKO (Swiss Competition Commission) published decisions."""

    REQUEST_DELAY = 2.0
    TIMEOUT = 120  # Large PDFs

    @property
    def court_code(self) -> str:
        return "weko"

    def discover_new(self, since_date=None) -> Iterator[dict]:
        """Discover WEKO decisions from the Entscheide listing and the three
        Praxis-Binnenmarktgesetz pages."""
        seen: set[str] = set()
        for url, legal_area, is_archive in LISTINGS:
            try:
                response = self.get(url)
            except Exception as e:
                logger.warning(f"[weko] listing fetch failed {url}: {e}")
                continue
            found = 0
            for stub in parse_listing(response.text, legal_area=legal_area, file_date_fallback=not is_archive):
                decision_id = make_decision_id("weko", stub["docket_number"])
                if decision_id in seen or self.state.is_known(decision_id):
                    continue
                if not is_archive and since_date and stub.get("decision_date"):
                    parsed = parse_date(stub["decision_date"])
                    if parsed and parsed < since_date:
                        continue
                seen.add(decision_id)
                found += 1
                yield stub
            logger.info(f"[weko] Found {found} new decisions on {url}")

    def fetch_decision(self, stub: dict) -> Decision | None:
        """Download PDF and extract decision text."""
        pdf_url = stub["pdf_url"]
        docket = stub["docket_number"]

        try:
            response = self.get(pdf_url)
        except Exception as e:
            logger.error(f"[weko] Failed to download PDF for {docket}: {e}")
            return None

        full_text = _extract_pdf_text(response.content)
        if not full_text or len(full_text.strip()) < 50:
            logger.warning(
                f"[weko] No text extracted from {docket} "
                f"({len(response.content)} bytes PDF)"
            )
            return None

        full_text = self.clean_text(full_text)
        lang = detect_language(full_text)
        decision_date = parse_date(stub.get("decision_date", ""))

        return Decision(
            decision_id=make_decision_id("weko", docket),
            court="weko",
            canton="CH",
            docket_number=docket,
            decision_date=decision_date,
            language=lang,
            title=stub.get("title"),
            legal_area=stub.get("legal_area") or "Wettbewerbsrecht",
            decision_type=stub.get("doc_type"),
            full_text=full_text,
            source_url=pdf_url,
            pdf_url=pdf_url,
            cited_decisions=extract_citations(full_text),
            scraped_at=datetime.now(timezone.utc),
        )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Scrape WEKO decisions")
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
    scraper = WEKOScraper()
    decisions = scraper.run(since_date=since, max_decisions=args.max)
    scraper.mark_run_complete(decisions)
    for d in decisions:
        print(f"  {d.decision_id}  {d.decision_date}  {len(d.full_text)} chars  {d.title[:60]}")
    print(f"\nScraped {len(decisions)} WEKO decisions")
