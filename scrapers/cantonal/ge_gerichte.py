"""
Geneva Courts Scraper (GE Gerichte)
=====================================
Scrapes court decisions from all 18 subsections of
justice.ge.ch/apps/decis/fr/{section}/search.

Architecture:
- 18 court subsections, each queried year-by-year (bypasses 10k cap)
- GET /apps/decis/fr/{section}/search?search_meta=...&page_size=500&page=N
  → HTML result list with metadata (docket, date, descripteurs, normes, résumé)
- Each decision has a detail page with optional PDF link
- PDF text extracted via PyMuPDF

Pagination:
- page_size: 500 (max)
- page: 1-indexed
- Total from: <strong>N</strong> ... resultats
- Search year by year from 1995 to avoid 10k cap

Volume: ~60,000–80,000 decisions across all subsections (1995–present)
Platform: Drupal + Solr/Elasticsearch
"""
from __future__ import annotations

import logging
import re
from datetime import date
from typing import Iterator

from bs4 import BeautifulSoup

from base_scraper import BaseScraper
from models import (
    Decision,
    detect_language,
    extract_citations,
    make_decision_id,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://justice.ge.ch"

# All 18 court subsections with their internal court codes
SECTIONS = {
    "capj": "GE_CAPJ_001",   # Cour d'appel du Pouvoir judiciaire
    "acjc": "GE_CJ_001",     # Cour de justice, civil
    "sommaires": "GE_CJ_002",# Sommaires
    "caph": "GE_CJ_003",     # Cour de justice, pénal
    "cabl": "GE_CJ_004",     # Chambre des baux et loyers
    "aj": "GE_CJ_005",       # Autorité de surveillance
    "das": "GE_CJ_006",      # Direction
    "dcso": "GE_CJ_007",     # Chambre de surveillance
    "comtax": "GE_CJ_008",   # Commission de taxation
    "parp": "GE_CJ_009",     # Protection de l'adulte (requêtes)
    "cjp": "GE_CJ_010",      # Cour de justice, public
    "pcpr": "GE_CJ_011",     # Protection (curatelle/privation)
    "oca": "GE_CJ_012",      # OCA
    "ata": "GE_CJ_013",      # Tribunal administratif
    "atas": "GE_CJ_014",     # Tribunal admin. social
    "cst": "GE_CJ_015",      # CST
    "jtp": "GE_TP_001",      # Tribunal pénal
    "dccr": "GE_TAPI_001",   # Tribunal admin. première instance
    "dcba": "GE_DCBA_001",   # Commission du barreau (attorney discipline)
}

# The case (procedure) number printed in the result block's flag: C/16444/2023
# (civil), A/3880/2025 (public law), P/11247/2025 (criminal), AC/1811/2025,
# CAPJ/4/2024. It is what the Federal Supreme Court and the other Geneva
# courts cite, while the title link carries the ruling number (ACJC/1978/2026).
RE_CASE_NUMBER = re.compile(r"\b[A-Z]{1,5}/\d{1,6}/\d{4}\b")

RE_TOTAL = re.compile(r"<strong>(\d+)</strong>\s*(?:enregistrement|resultats?)")
RE_DATE_FR = re.compile(r"du\s+(\d{1,2})\.(\d{1,2})\.(\d{4})")

PAGE_SIZE = 500
START_YEAR = 1995


def parse_case_number(flag_text: str, ruling_number: str | None = None) -> str | None:
    """The case number(s) of a result block's flag, as printed ("C/16444/2023";
    several joined by ", "), None when the flag carries none. A number equal to
    the ruling number is not a case number."""
    found = []
    for n in RE_CASE_NUMBER.findall(flag_text or ""):
        if n != (ruling_number or "").strip() and n not in found:
            found.append(n)
    return ", ".join(found) or None


class GEGerichteScraper(BaseScraper):
    """
    Scraper for all Geneva court decisions across 18 subsections.

    Searches year by year to bypass the 10k result cap.
    Fetches individual decision pages and extracts PDF text where available.
    """

    REQUEST_DELAY = 1.5
    TIMEOUT = 60
    MAX_ERRORS = 100

    @property
    def court_code(self):
        return "ge_gerichte"

    def discover_new(self, since_date=None) -> Iterator[dict]:
        if since_date and isinstance(since_date, str):
            since_date = date.fromisoformat(since_date)

        total_yielded = 0
        today = date.today()
        start_year = since_date.year if since_date else START_YEAR

        for section, court_code in SECTIONS.items():
            section_count = 0
            for year in range(today.year, start_year - 1, -1):
                for stub in self._discover_section_year(section, year):
                    if not self.state.is_known(stub["decision_id"]):
                        if since_date and stub.get("decision_date") and stub["decision_date"] < since_date:
                            continue
                        total_yielded += 1
                        section_count += 1
                        yield stub
            if section_count > 0:
                logger.info(f"GE/{section}: {section_count} new stubs")

        logger.info(f"GE: discovery complete: {total_yielded} new stubs total")

    def _discover_section_year(self, section: str, year: int) -> Iterator[dict]:
        """Discover all decisions for a section and year."""
        search_url = f"{BASE_URL}/apps/decis/fr/{section}/search"
        search_meta = f"dt_decision:[01.01.{year} TO 31.12.{year}]"
        params = {
            "search_meta": search_meta,
            "sort_by": "date",
            "page_size": str(PAGE_SIZE),
            "page": "1",
        }

        try:
            r = self.get(search_url, params=params)
        except Exception as e:
            logger.error(f"GE/{section}: search failed for year {year}: {e}")
            return

        total = self._parse_total(r.text)
        if not total:
            return

        logger.debug(f"GE/{section} {year}: {total} decisions")

        # Parse page 1
        for stub in self._parse_result_page(r.text, section):
            yield stub

        # Paginate if needed
        if total > PAGE_SIZE:
            total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
            for page in range(2, total_pages + 1):
                params["page"] = str(page)
                try:
                    r = self.get(search_url, params=params)
                    for stub in self._parse_result_page(r.text, section):
                        yield stub
                except Exception as e:
                    logger.error(f"GE/{section}: page {page}/{total_pages} year {year} failed: {e}")
                    break

    def _parse_total(self, html: str) -> int | None:
        m = RE_TOTAL.search(html)
        if m:
            return int(m.group(1))
        return None

    def _parse_result_page(self, html: str, section: str) -> Iterator[dict]:
        """Parse decision blocks from search result HTML."""
        soup = BeautifulSoup(html, "html.parser")

        # Try both possible container IDs
        results_div = soup.find("div", id="lstDecis")
        if not results_div:
            results_div = soup
        blocks = results_div.find_all("div", class_="list-block")

        for block in blocks:
            try:
                stub = self._parse_block(block, section)
                if stub:
                    yield stub
            except Exception as e:
                logger.debug(f"GE/{section}: parse error: {e}")

    def _parse_block(self, block, section: str) -> dict | None:
        """Parse a single decision block from search results."""
        # The flag div carries the case number (it once carried "Fiche N")
        flag_div = block.find("div", class_="decis-block__flag")
        fiche_id = None
        flag_text = flag_div.get_text(" ", strip=True) if flag_div else ""
        m = re.search(r"Fiche\s+(\d+)", flag_text)
        if m:
            fiche_id = m.group(1)

        # Docket number from h3 link
        h3 = block.find("h3", class_="list-block__title")
        if not h3:
            return None
        link = h3.find("a")
        if not link:
            return None
        docket = link.get_text(strip=True)
        if not docket:
            return None

        # Detail page URL
        detail_href = link.get("href", "")
        detail_url = f"{BASE_URL}{detail_href}" if detail_href else ""

        # Decision date from "du DD.MM.YYYY"
        h3_text = h3.get_text(strip=True)
        decision_date = None
        m = RE_DATE_FR.search(h3_text)
        if m:
            try:
                decision_date = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
            except ValueError:
                pass

        # Court and chamber
        court_div = block.find("div", class_="col-lg-12")
        chamber = None
        if court_div:
            inner_div = court_div.find("div")
            if inner_div:
                court_info = inner_div.get_text(strip=True)
                parts = [p.strip() for p in court_info.split(",")]
                if len(parts) >= 2:
                    chamber = parts[1]

        # Descripteurs, normes, résumé
        descripteurs = ""
        normes = ""
        resume = ""
        for div in block.find_all("div"):
            text = div.get_text(strip=True)
            if text.startswith("Descripteurs"):
                descripteurs = text.replace("Descripteurs :", "").strip()
            elif text.startswith("Normes"):
                normes = text.replace("Normes :", "").strip()
            elif text.startswith("Résumé") or text.startswith("Resume"):
                resume = text.replace("Résumé :", "").replace("Resume :", "").strip()

        decision_id = make_decision_id("ge_gerichte", docket)
        case_number = parse_case_number(flag_text, docket)

        return {
            "decision_id": decision_id,
            "docket_number": docket,
            "case_number": case_number,
            "fiche_id": fiche_id,
            "decision_date": decision_date,
            "chamber": chamber,
            "section": section,
            "court_code": SECTIONS.get(section, ""),
            "descripteurs": descripteurs,
            "normes": normes,
            "resume": resume,
            "detail_url": detail_url,
        }

    def fetch_decision(self, stub: dict) -> Decision | None:
        """Fetch full text from decision detail page + PDF if available."""
        docket = stub["docket_number"]
        detail_url = stub.get("detail_url", "")

        full_text = ""
        pdf_url = None

        # Try to fetch PDF from detail page
        if detail_url:
            try:
                r = self.get(detail_url)
                soup = BeautifulSoup(r.text, "html.parser")

                # Look for PDF link (pattern from entscheidsuche)
                pdf_div = soup.find("div", class_="col-lg-12 mt-4")
                if pdf_div:
                    pdf_link = pdf_div.find("a", href=True)
                    if pdf_link:
                        href = pdf_link["href"].replace("//", "/")
                        pdf_url = f"{BASE_URL}{href}"

                # Also extract any HTML content from the detail page
                content_div = soup.find("div", class_="list-block")
                if content_div:
                    page_text = content_div.get_text(separator="\n", strip=True)
                    if len(page_text) > 100:
                        full_text = page_text
            except Exception as e:
                logger.debug(f"GE: detail page failed for {docket}: {e}")

        # Try PDF download
        if pdf_url:
            try:
                r = self.get(pdf_url)
                ct = r.headers.get("Content-Type", "")
                if "pdf" in ct.lower() and r.content[:4] == b"%PDF":
                    pdf_text = self._pdf_text(r.content)
                    if len(pdf_text) > 100:
                        full_text = pdf_text
            except Exception as e:
                logger.debug(f"GE: PDF download failed for {docket}: {e}")

        # Fallback: build text from search result metadata
        if not full_text or len(full_text) < 100:
            parts = []
            if stub.get("resume"):
                parts.append(stub["resume"])
            if stub.get("descripteurs"):
                parts.append(f"Descripteurs: {stub['descripteurs']}")
            if stub.get("normes"):
                parts.append(f"Normes: {stub['normes']}")
            full_text = "\n\n".join(parts) if parts else f"[Metadata only for {docket}]"

        decision_date = stub.get("decision_date")
        if not decision_date:
            logger.warning(f"[ge_gerichte] No date for {docket}")

        language = detect_language(full_text) if len(full_text) > 200 else "fr"

        return Decision(
            decision_id=stub["decision_id"],
            court="ge_gerichte",
            canton="GE",
            chamber=stub.get("chamber"),
            docket_number=docket,
            docket_number_2=stub.get("case_number"),
            decision_date=decision_date,
            language=language,
            regeste=stub.get("resume") or None,
            full_text=full_text,
            source_url=stub.get("detail_url") or f"{BASE_URL}/apps/decis/fr/{stub.get('section', 'ata')}/search",
            pdf_url=pdf_url,
            external_id=stub.get("fiche_id"),
            cited_decisions=extract_citations(full_text) if len(full_text) > 200 else [],
        )

    @staticmethod
    def _pdf_text(data: bytes) -> str:
        """Extract text from PDF bytes."""
        try:
            import fitz
            doc = fitz.open(stream=data, filetype="pdf")
            return "\n\n".join(p.get_text() for p in doc)
        except ImportError:
            pass
        try:
            from pdfminer.high_level import extract_text
            from io import BytesIO
            return extract_text(BytesIO(data))
        except ImportError:
            pass
        return ""
