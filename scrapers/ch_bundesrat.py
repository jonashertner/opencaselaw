"""
Federal Council (Bundesrat) Complaint Decisions Scraper
========================================================

Scrapes the Beschwerdeentscheide des Bundesrates published by the Federal
Office of Justice (BJ) at bj.admin.ch: administrative complaint decisions in
which the Federal Council is the appellate authority, plus the EJPD's
procedural letters filed on the same page (Zwischenverfügung, Überweisung,
Schreiben).

Source: https://www.bj.admin.ch/de/beschwerdeentscheide-des-bundesrates
(19 entries on 2026-11-08, 2012 to present, 2-4 new per year).

Identity
--------
One decision, one id: ``ch_bundesrat_<YYYY-MM-DD>-<descriptor>``, taken from
the detail slug the listing links (``/de/entscheid-des-bundesrates-vom-13-
juni-2025-eda`` -> ``2025-06-13-eda``). The slug's date is the date the BJ
gives the decision, its descriptor (``eda``, ``kanton-zuerich``,
``bundesgerichtliches-verfahren``) tells same-day decisions apart. The other
slug families on the page (``schreiben-des-ejpd-vom-...``,
``ueberweisung-ejpd-an-eda-vom-...``, ``zwischenverfuegung-des-ejpd-vom-...``)
take the same form.

bj.admin.ch has changed its URL scheme twice (``/bj/de/home/publiservice/
publikationen/beschwerdeentscheide/2025-10-08.html``, then the flat slugs).
Each change used to mint a second id for every decision: the corpus served 19
decisions as 39 rows (runbooks/ch_bundesrat_rekey_2026-11.md). So the id is
never the URL: a slug without a parseable date is an error, not a docket.

Dates
-----
The decision date is the one the decision itself prints on its place-date
line ("Bern, 13. Juni 2025", "Berne, le 7 novembre 2018"; the EJPD letters
carry it in their letterhead). Many scanned decisions lose that line to OCR or
print "3003 Bern," without a date (the date is stamped); those take the date
of the detail page's download entry ("Bundesrat, Bern, 8. Oktober 2025"),
then the slug's. The first date in the text is NOT the decision date: it is
usually the contested ruling's ("Regierungsratsbeschluss ... vom 7. Mai 2025"),
which is how the old scraper dated the 2025-10-08 decision 2025-05-07.
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

BASE_URL = "https://www.bj.admin.ch"
COURT = "ch_bundesrat"

# bj.admin.ch retired the legacy /bj/de/home/... tree in its 2026 CMS
# migration; the Beschwerdeentscheide now live at this flat path.
LISTING_URL = f"{BASE_URL}/de/beschwerdeentscheide-des-bundesrates"

# Slug families on the listing and the decision type each one is.
_KIND_TYPES = {
    "entscheid-des-bundesrates": "Beschwerdeentscheid",
    "zwischenverfuegung-des-ejpd": "Zwischenverfügung",
    "ueberweisung-ejpd-an-eda": "Überweisung",
    "schreiben-des-ejpd": "Schreiben",
}

# /de/<kind>-vom-<day>-<month>-<year>[-<descriptor>]
_DETAIL_RE = re.compile(
    r"^/de/(?P<kind>[a-z0-9-]+?)-vom-(?P<day>\d{1,2})-(?P<month>[a-z]+)-"
    r"(?P<year>\d{4})(?:-(?P<desc>[a-z0-9-]+))?/?$"
)

# German month names as they appear (transliterated, lowercase) in the slug.
_SLUG_MONTHS = {
    "januar": 1, "februar": 2, "maerz": 3, "marz": 3, "april": 4, "mai": 5,
    "juni": 6, "juli": 7, "august": 8, "september": 9, "oktober": 10,
    "november": 11, "dezember": 12,
}
_SLUG_DATE_RE = re.compile(
    r"vom-(\d{1,2})-([a-z]+)-(\d{4})(?:-(.+))?$", re.IGNORECASE
)

# Month names in decision texts (de/fr/it, with common OCR-free variants).
_TEXT_MONTHS = {
    "januar": 1, "janvier": 1, "gennaio": 1,
    "februar": 2, "février": 2, "fevrier": 2, "febbraio": 2,
    "märz": 3, "maerz": 3, "mars": 3, "marzo": 3,
    "april": 4, "avril": 4, "aprile": 4,
    "mai": 5, "maggio": 5,
    "juni": 6, "juin": 6, "giugno": 6,
    "juli": 7, "juillet": 7, "luglio": 7,
    "august": 8, "août": 8, "aout": 8, "agosto": 8,
    "september": 9, "septembre": 9, "settembre": 9,
    "oktober": 10, "octobre": 10, "ottobre": 10,
    "november": 11, "novembre": 11,
    "dezember": 12, "décembre": 12, "decembre": 12, "dicembre": 12,
}
_MONTH_ALT = "|".join(sorted(_TEXT_MONTHS, key=len, reverse=True))
# Place-date line: "Bern, 13. Juni 2025", "Berne, le 7 novembre 2018",
# "3003 Bern, 13. Juni 2025", "Berna, 1° marzo 2020".
_PLACE_DATE_RE = re.compile(
    r"\bBern[ea]?\s*,\s*(?:le\s+|den\s+|il\s+)?(\d{1,2})\s*(?:\.|er|°)?\s*"
    rf"({_MONTH_ALT})\s+(\d{{4}})\b",
    re.IGNORECASE,
)
# Download entry on the detail page: "Bundesrat, Bern, 8. Oktober 2025".
_ANY_DATE_RE = re.compile(
    rf"(\d{{1,2}})\s*(?:\.|er|°)?\s*({_MONTH_ALT})\s+(\d{{4}})", re.IGNORECASE
)
# "Veröffentlicht am 8. Oktober 2025"
_PUBLISHED_RE = re.compile(r"Veröffentlicht am\s+(.+)", re.IGNORECASE)

# Language suffix of a PDF rendition: ...-kanton-aargau-d.pdf
_PDF_STEM_RE = re.compile(r"/([^/]+?)\.pdf(?:\.download\.pdf/|$|\?)", re.IGNORECASE)


def _parse_bundesrat_slug(slug: str) -> tuple[str | None, str]:
    """Parse a detail slug into (ISO decision date, descriptor).

    ``entscheid-des-bundesrates-vom-13-juni-2025-eda`` -> ("2025-06-13", "eda").
    Returns (None, descriptor-or-"") when the date can't be parsed.
    """
    m = _SLUG_DATE_RE.search(slug)
    if not m:
        return None, ""
    day, month_name, year, descriptor = m.groups()
    month = _SLUG_MONTHS.get(month_name.lower())
    descriptor = descriptor or ""
    if not month:
        return None, descriptor
    try:
        return date(int(year), month, int(day)).isoformat(), descriptor
    except ValueError:
        return None, descriptor


def docket_for(date_str: str, descriptor: str) -> str:
    """The stable docket: ``YYYY-MM-DD[-descriptor]``."""
    return f"{date_str}-{descriptor}" if descriptor else date_str


def pdf_stem(url: str | None) -> str:
    """The BJ's file stem of a decision PDF, the same under every URL scheme
    the BJ has used (``/dam/bj/de/data/.../x.pdf.download.pdf/x-d.pdf``,
    ``/dam/de/sd-web/<key>/x.pdf``): ``2025-10-08-beschwerde-kanton-aargau``.
    Empty when the URL names no PDF."""
    if not url:
        return ""
    m = _PDF_STEM_RE.search(url)
    if not m:
        return ""
    return re.sub(r"-(?:d|f|i)$", "", m.group(1).lower())


def _date_from_match(day: str, month: str, year: str) -> str | None:
    mon = _TEXT_MONTHS.get(month.lower())
    if not mon:
        return None
    try:
        return date(int(year), mon, int(day)).isoformat()
    except ValueError:
        return None


def decision_date_from_text(text: str, expected: str | None = None) -> str | None:
    """The date the decision prints on its place-date line, or None.

    Several lines can match (the letterhead of an EJPD letter and a quoted
    "Bern, ..." of an earlier act); when they disagree, ``expected`` (the
    date the BJ gives the entry) wins if it is among them, else the last
    one (the signature line closes the decision).
    """
    dates = [d for d in (_date_from_match(*m.groups())
                         for m in _PLACE_DATE_RE.finditer(text or "")) if d]
    if not dates:
        return None
    if expected and expected in dates:
        return expected
    return dates[-1]


def parse_listing(html: str) -> list[dict]:
    """Detail entries of the listing page, in page order.

    Each entry: detail_url, slug, kind, decision_type, slug_date, descriptor,
    docket_number, title. Entries whose slug has no parseable date come back
    with slug_date None (the caller reports them; they are never given a
    URL-shaped docket).
    """
    soup = BeautifulSoup(html, "html.parser")
    entries: list[dict] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].replace(BASE_URL, "")
        m = _DETAIL_RE.match(href)
        if not m or m.group("kind") not in _KIND_TYPES:
            continue
        full_url = urljoin(BASE_URL, href)
        if full_url in seen:
            continue
        seen.add(full_url)
        slug = href.rstrip("/").split("/")[-1]
        date_str, descriptor = _parse_bundesrat_slug(slug)
        title = ""
        card = a.find_parent(class_=re.compile(r"\bcard\b"))
        if card is not None:
            h = card.find(["h3", "h2", "h4"])
            if h:
                title = h.get_text(" ", strip=True)
        if not title:
            label = a.get("aria-label") or a.get_text(" ", strip=True)
            title = re.sub(r"^Mehr über «(.*)»$", r"\1", label.strip())
        entries.append({
            "detail_url": full_url,
            "slug": slug,
            "kind": m.group("kind"),
            "decision_type": _KIND_TYPES[m.group("kind")],
            "slug_date": date_str,
            "descriptor": descriptor,
            "docket_number": docket_for(date_str, descriptor) if date_str else "",
            "title": title,
        })
    return entries


def parse_detail(html: str) -> dict:
    """Title, PDF link, keywords and dates of a detail page."""
    soup = BeautifulSoup(html, "html.parser")
    out: dict = {"title": None, "pdf_url": None, "keywords": None,
                 "publication_date": None, "download_date": None}
    h1 = soup.find("h1")
    if h1:
        out["title"] = h1.get_text(" ", strip=True) or None
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if re.search(r"\.pdf(?:$|\?|\.download\.pdf)", href, re.IGNORECASE):
            out["pdf_url"] = urljoin(BASE_URL, href)
            desc = a.find(class_="download-item__description")
            if desc:
                m = _ANY_DATE_RE.search(desc.get_text(" ", strip=True))
                if m:
                    out["download_date"] = _date_from_match(*m.groups())
            break
    for el in soup.find_all(class_="meta-info__item"):
        m = _PUBLISHED_RE.search(el.get_text(" ", strip=True))
        if m:
            dm = _ANY_DATE_RE.search(m.group(1))
            if dm:
                out["publication_date"] = _date_from_match(*dm.groups())
            break
    for h in soup.find_all(["h2", "h3"]):
        label = h.get_text(" ", strip=True)
        if label in ("Stichwörter", "Mots-clés", "Parole chiave"):
            nxt = h.find_next(["p", "ul", "div"])
            if nxt:
                kw = nxt.get_text(" ", strip=True)
                out["keywords"] = kw or None
            break
    return out


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


class CHBundesratScraper(BaseScraper):
    """Scraper for Federal Council complaint decisions."""

    REQUEST_DELAY = 2.0
    TIMEOUT = 60

    @property
    def court_code(self) -> str:
        return COURT

    def discover_new(self, since_date=None) -> Iterator[dict]:
        """Discover decisions from the BJ listing page."""
        try:
            response = self.get(LISTING_URL)
        except Exception as e:
            # A 404 / transport error here means the portal moved again (as in
            # the 2026 CMS migration that broke the old URL). Fail loudly rather
            # than silently reporting zero new decisions.
            logger.error(f"[ch_bundesrat] Listing fetch failed: {e}")
            raise

        entries = parse_listing(response.text)
        if not entries:
            # The page answered but links no decision: the markup changed.
            raise RuntimeError(
                f"[ch_bundesrat] listing {LISTING_URL} links no decision page; "
                "markup changed?"
            )
        self.portal_count = len(entries)
        dockets: dict[str, str] = {}
        for e in entries:
            if not e["slug_date"]:
                logger.error(
                    f"[ch_bundesrat] ERROR detail slug without a date, not taken: "
                    f"{e['detail_url']}"
                )
                continue
            docket = e["docket_number"]
            if docket in dockets:
                logger.error(
                    f"[ch_bundesrat] ERROR two entries map to docket {docket}: "
                    f"{dockets[docket]} and {e['detail_url']}; second not taken"
                )
                continue
            dockets[docket] = e["detail_url"]

            decision_id = make_decision_id(COURT, docket)
            if self.state.is_known(decision_id):
                continue
            if since_date:
                parsed = parse_date(e["slug_date"])
                if parsed and parsed < since_date:
                    continue
            yield {
                "docket_number": docket,
                "decision_date": e["slug_date"],
                "detail_url": e["detail_url"],
                "title": e["title"],
                "decision_type": e["decision_type"],
            }

        logger.info(f"[ch_bundesrat] Found {len(entries)} entries on listing page")

    def fetch_decision(self, stub: dict) -> Decision | None:
        """Fetch detail page, find PDF link, download and extract text."""
        detail_url = stub["detail_url"]
        docket = stub["docket_number"]

        try:
            response = self.get(detail_url)
        except Exception as e:
            logger.error(f"[ch_bundesrat] Failed to fetch detail page {docket}: {e}")
            return None

        detail = parse_detail(response.text)
        pdf_url = detail["pdf_url"]
        if not pdf_url:
            logger.warning(f"[ch_bundesrat] No PDF found on {detail_url}")
            return None

        try:
            pdf_response = self.get(pdf_url)
        except Exception as e:
            logger.error(f"[ch_bundesrat] Failed to download PDF for {docket}: {e}")
            return None

        full_text = _extract_pdf_text(pdf_response.content)
        if not full_text or len(full_text.strip()) < 50:
            logger.warning(
                f"[ch_bundesrat] No text extracted from {docket} "
                f"({len(pdf_response.content)} bytes PDF)"
            )
            return None

        full_text = self.clean_text(full_text)
        return build_decision(stub, detail, full_text)


def build_decision(stub: dict, detail: dict, full_text: str) -> Decision:
    """The Decision for one listing entry, its detail page and its PDF text."""
    docket = stub["docket_number"]
    slug_date = stub.get("decision_date") or None
    listed = detail.get("download_date") or slug_date
    text_date = decision_date_from_text(full_text, expected=listed)
    decision_date = text_date or listed
    if text_date and listed and text_date != listed:
        logger.warning(
            f"[ch_bundesrat] {docket}: decision text dated {text_date}, "
            f"BJ lists {listed}; taking the text's"
        )
    return Decision(
        decision_id=make_decision_id(COURT, docket),
        court=COURT,
        canton="CH",
        docket_number=docket,
        decision_date=parse_date(decision_date) if decision_date else None,
        publication_date=parse_date(detail["publication_date"]) if detail.get("publication_date") else None,
        language=detect_language(full_text),
        title=detail.get("title") or stub.get("title"),
        legal_area=detail.get("keywords"),
        decision_type=stub.get("decision_type") or "Beschwerdeentscheid",
        full_text=full_text,
        source_url=stub["detail_url"],
        pdf_url=detail.get("pdf_url"),
        cited_decisions=extract_citations(full_text),
        scraped_at=datetime.now(timezone.utc),
    )
