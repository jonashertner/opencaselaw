"""
Thurgau Courts Scraper (TG Gerichte)
=====================================
Scrapes court decisions from the Scroll Viewport / Confluence portal at
rechtsprechung.tg.ch.

Architecture:
- GET /og/rbog-{year} → OG year listing page with decision links
- GET /og/rbog-{year}-nr-{nn} → OG full decision HTML page
- GET /vg/tvr-{year} → VG year listing page with decision links
- GET /vg/tvr-{year}-nr-{nn} → VG full decision HTML page
- No authentication, no AJAX — pure server-rendered HTML
- Full text inline in HTML

Additional sections (Medienschaffende + pending publication):
- GET /og/entscheide-fur-medienschaffende-anonymisiert → year links
- GET /og/{year} → decision links (PDF-only pages)
- GET /vg/entscheide-fur-medienschaffende → year links
- GET /vg/{year} → decision links (PDF-only pages)
- GET /og/zur-publikation-in-rbog-vorgesehen → decision links (inline HTML)
- GET /vg/zur-publikation-in-tvr-vorgesehen → decision links (inline HTML)

Content: RBOG (Obergericht, ~1,200) + TVR (Verwaltungsgericht, ~900) series
  + Medienschaffende (~12) + pending publication (~1-5)
Total: ~2,200+ decisions (1994/2000-present)
Platform: Atlassian Confluence Cloud + Scroll Viewport
"""
from __future__ import annotations

import io
import logging
import re
from datetime import date
from typing import Iterator
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from base_scraper import BaseScraper
from models import (
    Decision,
    detect_language,
    extract_citations,
    make_decision_id,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://rechtsprechung.tg.ch"

# Regex patterns
RE_DECISION_LINK = re.compile(r"/(og/rbog|vg/tvr)-(\d{4})-nr-(\d+)")
RE_DOCKET = re.compile(r"([A-Z]+\.\d{4}\.\d+)")
RE_DATE = re.compile(r"(\d{1,2})\.\s*(\w+)\s+(\d{4})")
RE_DATE_NUMERIC = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")

# Court sections: (path_prefix, docket_prefix, start_year)
SECTIONS = [
    ("og", "rbog", "RBOG", 1994),   # Obergericht
    ("vg", "tvr", "TVR", 2000),      # Verwaltungsgericht
]

# Additional sections: Medienschaffende (PDF-only) + pending publication (inline HTML)
# (section_path, index_url_suffix, label)
MEDIA_SECTIONS = [
    ("og", "entscheide-fur-medienschaffende-anonymisiert", "OG/Media"),
    ("vg", "entscheide-fur-medienschaffende", "VG/Media"),
]
PENDING_SECTIONS = [
    ("og", "zur-publikation-in-rbog-vorgesehen", "OG/Pending"),
    ("vg", "zur-publikation-in-tvr-vorgesehen", "VG/Pending"),
]

# Match decision slugs like "sbr-2023-51", "zbr-2024-35", "vg-2023-9", "zr-2025-16"
RE_EXTRA_SLUG = re.compile(r"/(?:og|vg)/([a-z]+-\d{4}-\d+)")

# Match year links like "/og/2024", "/vg/2025"
RE_YEAR_LINK = re.compile(r"/(?:og|vg)/(\d{4})$")

# Month name to number (German)
MONTH_MAP = {
    "januar": 1, "februar": 2, "märz": 3, "april": 4, "mai": 5, "juni": 6,
    "juli": 7, "august": 8, "september": 9, "oktober": 10, "november": 11, "dezember": 12,
}


def _extract_pdf_text(data: bytes) -> str:
    """Extract text from PDF bytes using fitz (PyMuPDF) with pdfplumber fallback."""
    try:
        import fitz

        with fitz.open(stream=data, filetype="pdf") as doc:
            return "\n\n".join(page.get_text() for page in doc)
    except Exception:
        import pdfplumber

        with pdfplumber.open(io.BytesIO(data)) as pdf:
            return "\n\n".join(p.extract_text() or "" for p in pdf.pages)


def _slug_to_docket(slug: str) -> str:
    """Convert URL slug like 'sbr-2023-51' to docket 'SBR.2023.51'."""
    # Strip trailing suffixes like "-zur-publikation-vorgesehen"
    m = re.match(r"([a-z]+)-(\d{4})-(\d+)", slug)
    if m:
        return f"{m.group(1).upper()}.{m.group(2)}.{m.group(3)}"
    return slug.upper()


def _parse_date_text(text):
    """Parse date from German text like '15. Oktober 2023'."""
    if not text:
        return None
    m = RE_DATE_NUMERIC.search(text)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            pass
    m = RE_DATE.search(text)
    if m:
        day = int(m.group(1))
        month_name = m.group(2).lower()
        year = int(m.group(3))
        month = MONTH_MAP.get(month_name)
        if month:
            try:
                return date(year, month, day)
            except ValueError:
                pass
    return None


RE_SIGNATURE_DATE = re.compile(
    r"(?:Obergericht|Verwaltungsgericht),\s*(?:\d+\.\s*)?(?:Zivil)?[Aa]bteilung,?\s*"
    r"(\d{1,2})\.\s*(\w+)\s+(\d{4})"
)


def _soup(r) -> BeautifulSoup:
    """Parse a portal response from its bytes. rechtsprechung.tg.ch sends
    ``Content-Type: text/html`` without a charset, so ``requests`` decodes the
    UTF-8 pages as ISO-8859-1 ("Entschädigung" became "EntschÃ¤digung" in
    2,285 of 2,304 rows scraped 2026-02..06, and "März" defeated the date
    parse). From the bytes BeautifulSoup honours the page's
    ``<meta charset="UTF-8">``."""
    return BeautifulSoup(r.content, "html.parser")


# One UTF-8 sequence read as Latin-1: a lead byte and exactly its continuation bytes.
_MOJIBAKE_RE = re.compile(r"[\xc2-\xdf][\x80-\xbf]|[\xe0-\xef][\x80-\xbf]{2}|[\xf0-\xf4][\x80-\xbf]{3}")


def fix_mojibake(text: str | None) -> str | None:
    """Undo UTF-8-read-as-Latin-1 text sequence by sequence ("Ã¤" -> "ä",
    "â\\x80\\x99" -> "’"). A sequence that is not valid UTF-8 stays as it is,
    so clean text passes through unchanged."""
    if not text:
        return text

    def _one(m):
        try:
            return m.group(0).encode("latin-1").decode("utf-8")
        except UnicodeError:
            return m.group(0)

    return _MOJIBAKE_RE.sub(_one, text)


# The yearbook entry's own signature line: the body that decided, the date
# and the underlying docket, as printed at the end of every RBOG/TVR entry.
#   "Obergericht, 1. Abteilung, 13. Juni 2016, KES.2016.32"
#   "Rekurskommission, 5. August 1999, ZBR.1999.4"
#   "Obergericht, 1. Abteilung, 21. November 2024 / 5. März 2025, SBR.2023.51"
#   "Entscheid des Verwaltungsgerichts VG.2024.42/E vom 11. Juni 2025"
#   "Entscheid der Steuerrekurskommission vom 24. Juni 2015"
#   "Entscheid vom 17. Dezember 2008"
_MONTHS_RE = "|".join(MONTH_MAP)
# A day and month with an optional year: "7. Juli / 12. August 2021" prints
# the year once.
RE_SIG_DATE = re.compile(rf"(\d{{1,2}})\.\s*({_MONTHS_RE})(?:\s+(\d{{4}}))?\b", re.IGNORECASE)
# A run of dates ending in a full one: "13. Juni 2016", "10. / 22. Dezember
# 2020", "7./28. Februar 2001", "21. November 2024 / 5. März 2025".
_DAY_OR_DATE = rf"\d{{1,2}}\.(?:\s*(?:{_MONTHS_RE}))?(?:\s+\d{{4}})?"
RE_SIG_DATE_RUN = re.compile(
    rf"(?:{_DAY_OR_DATE}\s*(?:/|und|-)\s*)*\d{{1,2}}\.\s*(?:{_MONTHS_RE})\s+\d{{4}}\b",
    re.IGNORECASE)
# The court's own case numbers: VG.2024.42/E, Z1.2001.3, SBR. 2021.87,
# STRE.2025.127-130, ZB 97 61 (1990s), Nr. 51/14, § 8 / 2021, StRE Nrn.
# 238-245/2016, 200/2014.
RE_SIG_DOCKET = re.compile(
    r"(?:\b[A-Z][A-Za-z0-9]{0,5}\.\s?\d{4}\.\d{1,4}(?:-\d{1,4})?(?:/[A-Z0-9]{1,3}(?![A-Za-z0-9.]))*"
    r"|\b[A-Z]{1,4}\s\d{2}\s\d{1,4}\b"
    r"|(?:\b(?:StRE\s+)?Nrn?\.\s*|§\s*)?\b\d{1,4}(?:\s*(?:-|und)\s*\d{1,4})?\s*/\s*(?:\d{4}|\d{2})\b)")
_SIG_PLACE_NAMES = frozenset({"frauenfeld", "weinfelden"})


def _run_date(run: str) -> date | None:
    """The last full date of a date run (the day the ruling was completed
    where two days are printed)."""
    full = [m for m in RE_SIG_DATE.finditer(run) if m.group(3)]
    if not full:
        return None
    m = full[-1]
    return _parse_german_date(m.group(1), m.group(2), m.group(3))


def _chamber_name(body: str) -> str | None:
    """'Verwaltungsgerichts als Versicherungsgericht' -> 'Verwaltungsgericht als
    Versicherungsgericht'; a place name (a letter's dateline) is no chamber."""
    body = re.sub(r"\s+", " ", body).strip(" ,")
    if not body or body.lower() in _SIG_PLACE_NAMES:
        return None
    body = re.sub(r"^(\w*(?:gericht|rat))e?s\b", r"\1", body)
    return body[0].upper() + body[1:]


def _docket_text(text: str) -> str | None:
    """Docket text as printed when it consists of case numbers only."""
    text = text.strip(" ,;")
    if not text or len(text) > 80 or not RE_SIG_DOCKET.search(text):
        return None
    rest = RE_SIG_DOCKET.sub("", text)
    # "BR.1999.24, 25, 31 und 32": further numbers of the series
    rest = re.sub(r"\b\d{1,4}\b", "", rest)
    if re.sub(r"(?:\bund\b|[,;/\s])", "", rest):
        return None
    return text


def _parse_signature_line(line: str) -> dict | None:
    """One line read as an entry signature, or None. Two shapes:
    "<Body>[, <n>. Abteilung][ (Einzelrichter)], <dates>[, <dockets>]" and
    "[...]Entscheid [des|der <Body>] [<dockets>] vom <dates>[, <dockets>]"
    (also "Urteil des Verwaltungsgerichts ...", never the Federal Supreme
    Court's "Urteil [des Bundesgerichts] ... vom ...")."""
    line = re.sub(r"[\s\xa0]+", " ", line).strip()
    if not line or len(line) > 220:
        return None
    m = re.match(
        r"^(?:\([^)]*\))?(?P<kind>\w*ntscheide?|Urteil|(?=[A-Z][A-Za-z0-9]{0,5}\.\s?\d{4}\.))"
        r"(?P<mid>.*?)\bvom,?\s+(?P<rest>.+)$", line)
    if m:
        kind, mid, rest = m.group("kind"), m.group("mid").strip(), m.group("rest")
        if kind == "Urteil" and (not mid.startswith(("des ", "der ")) or "Bundesgericht" in mid):
            return None
        run = RE_SIG_DATE_RUN.match(rest)
        if not run:
            return None
        tail = rest[run.end():].strip().rstrip(".").strip()
        after = None
        if tail:
            tail = re.sub(r"^\((?:[^)]*)\)\.?$|^;\s*vgl\..*$", "", tail).strip()
            if tail:
                after = _docket_text(tail)
                if not after:
                    return None
        d = _run_date(run.group(0))
        if not d:
            return None
        dockets = list(RE_SIG_DOCKET.finditer(mid))
        if dockets:
            body, before = mid[:dockets[0].start()], _docket_text(mid[dockets[0].start():])
            if not before:
                return None
        else:
            body, before = mid, None
        body = re.sub(r"^(?:des|der)\s+", "", body.strip())
        if len(body) > 80 or re.search(r"\d", body):
            return None
        return {"decision_date": d, "docket_number_2": before or after,
                "chamber": _chamber_name(body)}
    run = start = None
    for c in re.finditer(r",\s*(?=\d{1,2}\.)", line):
        run = RE_SIG_DATE_RUN.match(line, c.end())
        if run:
            start = c.end()
            break
    if not run or start < 4:
        return None
    head = line[:start].rstrip()
    # "Obergericht. 1. Abteilung" (a stop for the comma)
    head = re.sub(r"^(\w+)\.\s+(?=\d+\.\s*\w*[Aa]bteilung)", r"\1, ", head.rstrip(" ,"))
    parts = [p.strip() for p in head.split(",")]
    if (len(parts) > 4 or not re.match(r"^[A-ZÄÖÜ]", parts[0])
            or re.match(r"(?i)\w*ntscheid|urteil", parts[0])
            or any(len(p) > 60 for p in parts)
            or any(re.search(r"\d", p) and not re.match(r"^\d+\.\s*\w*[Aa]bteilung(?:\s*\([^)]*\))?$", p)
                   for p in parts)):
        return None
    d = _run_date(run.group(0))
    if not d:
        return None
    tail = line[run.end():].strip().rstrip(".")
    docket = None
    if tail:
        docket = _docket_text(tail)  # ", KES.2016.32" (the comma is sometimes missing)
        if not docket:
            return None
    return {"decision_date": d, "docket_number_2": docket,
            "chamber": _chamber_name(", ".join(parts))}


def parse_signature(full_text: str | None) -> dict | None:
    """The entry's signature line, read from the end of the text: decision
    date (the last date where two are printed), underlying docket as printed,
    and the deciding body. None when the entry prints none. Only a line that
    is a signature as a whole counts, so a date or docket cited in the
    reasons, or the Federal Supreme Court's judgment noted after the
    signature ("Urteil 9C_442/2025 vom ..."), is never taken."""
    if not full_text:
        return None
    for line in reversed(full_text.split("\n")):
        sig = _parse_signature_line(line)
        if sig:
            return sig
    return None


def extract_head_regeste(soup: BeautifulSoup) -> str | None:
    """The court's own head block of a yearbook entry: the subject line (the
    bold first paragraph), the provisions line and, where the entry has one,
    the Leitsatz (the paragraph set in italics), each verbatim, one per line.
    Stops at the first paragraph that is none of these (the facts). None when
    the page has no such block."""
    body = soup.select_one("section.article-body") or soup.find("article")
    if not body:
        return None
    parts: list[str] = []
    for el in body.children:
        name = getattr(el, "name", None)
        if name is None:
            if str(el).strip():
                break
            continue
        if name == "hr":
            continue
        if name != "p":
            break
        text = re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip()
        if not text:
            continue
        text = re.sub(r"\s+,", ",", text)
        children = [c for c in el.children if getattr(c, "name", None) or str(c).strip()]
        tags = {getattr(c, "name", None) for c in children}
        str_only = all(getattr(c, "name", None) in ("strong", "b") for c in children)
        em_only = all(getattr(c, "name", None) in ("em", "i") for c in children)
        links_only = tags <= {"a", None} and "a" in tags and all(
            getattr(c, "name", None) == "a" or str(c).strip() in {",", ";", "/", "und"}
            for c in children)
        if not parts and str_only:
            parts.append(text)          # subject line
        elif len(parts) == 1 and (links_only or (
                len(text) < 300 and re.match(r"(§|Art\.|Ziff\.)", text))):
            parts.append(text)          # provisions
        elif parts and em_only:
            parts.append(text)          # Leitsatz
            break
        else:
            break
    if not parts:
        return None
    if len(parts) == 1 and re.match(r"(?i)(zusammenfassung|sachverhalt|aus den erw)", parts[0]):
        return None
    return "\n".join(parts)


def _extract_signature_date(text: str, docket_year: int | None = None) -> date | None:
    """
    Extract the decision date from the TG signature block.

    Strategy 1 (RBOG): Match "Obergericht, 3. Abteilung, 19. Februar 2024, ZR.2024.9"
    Strategy 2 (TVR fallback): Take the last German date in the text that's
    within ±1 year of the docket year — TVR decisions end with the decision date.
    """
    # Strategy 1: explicit signature pattern (RBOG)
    matches = list(RE_SIGNATURE_DATE.finditer(text))
    if matches:
        m = matches[-1]
        d = _parse_german_date(m.group(1), m.group(2), m.group(3))
        if d:
            return d

    # Strategy 2: last date near the docket year (TVR)
    all_dates = list(RE_DATE.finditer(text))
    if not all_dates:
        return None

    # Work backwards — the last date matching the docket year is usually correct
    for m in reversed(all_dates):
        d = _parse_german_date(m.group(1), m.group(2), m.group(3))
        if d and docket_year and abs(d.year - docket_year) <= 1:
            return d

    # Last resort: just use the very last date found
    m = all_dates[-1]
    return _parse_german_date(m.group(1), m.group(2), m.group(3))


def _parse_german_date(day_str: str, month_str: str, year_str: str) -> date | None:
    """Parse a German date from captured groups."""
    month = MONTH_MAP.get(month_str.lower())
    if not month:
        return None
    try:
        return date(int(year_str), month, int(day_str))
    except ValueError:
        return None


RE_YEARBOOK_DOCKET = re.compile(r"^(RBOG|TVR)-(\d{4})-(\d+)$")
# A full ruling's caption: "Das Verwaltungsgericht ... hat am 14. September
# 2022 in Sachen ... entschieden" (the PDFs of the media and pending sections).
RE_CAPTION_DATE = re.compile(rf"\bhat\s+am\s+(\d{{1,2}})\.\s*({_MONTHS_RE})\s+(\d{{4}})\b", re.IGNORECASE)


def derive_entry_fields(full_text: str, stub_docket: str, is_pdf: bool = False) -> dict:
    """Docket, underlying docket, decision date and chamber of one entry,
    from its text.

    A yearbook entry (RBOG/TVR) is dated only by its own signature line; one
    that prints none (it ends with the Federal Supreme Court's judgment, or the
    source truncates the year) stays undated rather than taking the volume's
    1 January or a date from the reasons (TVR 2008 Nr. 19 was dated from the
    taxpayer's birthday, 1928-06-14). A full ruling (PDF) is dated by its
    caption. Other pages keep the earlier heuristics, without the 1 January
    placeholder."""
    yearbook = bool(RE_YEARBOOK_DOCKET.match(stub_docket or ""))
    docket = stub_docket
    if not yearbook:
        m_docket = RE_DOCKET.search(full_text[:1000])
        if m_docket:
            docket = m_docket.group(1)
    out = {"docket_number": docket, "docket_number_2": None, "decision_date": None, "chamber": None}
    sig = parse_signature(full_text)
    if sig:
        out.update(sig)
        if out["docket_number_2"] == docket:
            out["docket_number_2"] = None
        return out
    if is_pdf or not yearbook:
        m = RE_CAPTION_DATE.search(full_text[:4000])
        if m:
            out["decision_date"] = _parse_german_date(m.group(1), m.group(2), m.group(3))
            return out
    if not yearbook:
        m_yr = re.search(r"\.(\d{4})\.", docket)
        out["decision_date"] = (_extract_signature_date(full_text, int(m_yr.group(1)) if m_yr else None)
                                or _parse_date_text(full_text[:500]))
    return out


def parse_entry_page(soup: BeautifulSoup, url: str, stub_docket: str, pdf_text_fn=None) -> dict:
    """Everything the scraper stores from one entry page: full text (the PDF's
    when the page carries one and ``pdf_text_fn`` reads it, else the page's),
    the court's head block as regeste (inline pages only) and the fields of
    derive_entry_fields."""
    regeste = extract_head_regeste(soup)  # before the inline extraction strips the page
    full_text, pdf_url = None, None
    pdf_link = soup.find("a", href=re.compile(r"__attachments/.*\.pdf"))
    if pdf_link:
        pdf_url = urljoin(url, pdf_link["href"])
        if pdf_text_fn:
            full_text = pdf_text_fn(pdf_url)
    is_pdf = bool(full_text and len(full_text) >= 50)
    if not is_pdf:
        full_text = TGGerichteScraper._extract_inline_text(soup, stub_docket)
    full_text = full_text or ""
    fields = derive_entry_fields(full_text, stub_docket, is_pdf=is_pdf)
    return {**fields, "full_text": full_text, "pdf_url": pdf_url,
            "regeste": None if is_pdf else regeste}


class TGGerichteScraper(BaseScraper):
    """
    Scraper for Thurgau Obergericht decisions via Scroll Viewport HTML.

    Strategy: iterate year pages (1994-present), extract decision links,
    fetch each decision page for full text.
    """

    REQUEST_DELAY = 2.0
    TIMEOUT = 30
    MAX_ERRORS = 50

    @property
    def court_code(self):
        return "tg_gerichte"

    def discover_new(self, since_date=None) -> Iterator[dict]:
        if since_date and isinstance(since_date, str):
            since_date = date.fromisoformat(since_date)

        total_yielded = 0
        today = date.today()
        seen_ids: set[str] = set()  # Run-level dedup across all sections

        # 1. Main RBOG/TVR series
        for section_path, series_slug, series_prefix, default_start in SECTIONS:
            start_year = since_date.year if since_date else default_start
            section_count = 0

            for year in range(today.year, start_year - 1, -1):
                for stub in self._discover_year(section_path, series_slug, series_prefix, year):
                    did = stub["decision_id"]
                    if did in seen_ids or self.state.is_known(did):
                        continue
                    seen_ids.add(did)
                    if since_date and stub.get("decision_date") and stub["decision_date"] < since_date:
                        continue
                    total_yielded += 1
                    section_count += 1
                    yield stub

            if section_count > 0:
                logger.info(f"TG/{series_prefix}: {section_count} new stubs")

        # 2. Medienschaffende sections (PDF-only decisions)
        for section_path, index_suffix, label in MEDIA_SECTIONS:
            section_count = 0
            for stub in self._discover_extra_section(section_path, index_suffix, label, since_date):
                did = stub["decision_id"]
                if did in seen_ids or self.state.is_known(did):
                    continue
                seen_ids.add(did)
                total_yielded += 1
                section_count += 1
                yield stub
            if section_count > 0:
                logger.info(f"TG/{label}: {section_count} new stubs")

        # 3. Pending publication sections (inline HTML)
        for section_path, index_suffix, label in PENDING_SECTIONS:
            section_count = 0
            for stub in self._discover_pending_section(section_path, index_suffix, label, since_date):
                did = stub["decision_id"]
                if did in seen_ids or self.state.is_known(did):
                    continue
                seen_ids.add(did)
                total_yielded += 1
                section_count += 1
                yield stub
            if section_count > 0:
                logger.info(f"TG/{label}: {section_count} new stubs")

        logger.info(f"TG: discovery complete: {total_yielded} new stubs")

    def _discover_year(self, section_path: str, series_slug: str,
                       series_prefix: str, year: int) -> Iterator[dict]:
        """Fetch year listing page and extract decision links."""
        url = f"{BASE_URL}/{section_path}/{series_slug}-{year}"
        try:
            r = self.get(url)
        except requests.HTTPError as e:
            # RBOG/TVR are ANNUAL volumes: the loop always probes a year that
            # has not been published yet, and base_scraper.raise_for_status()
            # turns that 404 into an exception. 632 of this log's 824 ERROR
            # lines were this expected condition. It self-heals — rbog-2025
            # 404'd nightly 02-23 -> 06-18, then the volume appeared and the
            # same run ingested +81. Note the PRIOR year 404s for months too
            # (rbog-2025 x126), so this is not keyed on "current year".
            status = getattr(e.response, "status_code", None)
            if status == 404:
                logger.info(
                    f"TG/{series_prefix}: volume {year} not published yet (404)")
            else:
                logger.error(
                    f"TG/{series_prefix}: year page {year} returned {status}: {e}")
            return
        except Exception as e:
            logger.error(f"TG/{series_prefix}: failed to fetch year page {year}: {e}")
            return

        soup = _soup(r)

        # Find all links to individual decision pages
        seen = set()
        for a in soup.find_all("a", href=True):
            href = a.get("href", "")
            m = RE_DECISION_LINK.search(href)
            if not m:
                continue

            link_year = int(m.group(2))
            nr = int(m.group(3))
            key = (series_prefix, link_year, nr)
            if key in seen:
                continue
            seen.add(key)

            link_text = a.get_text(strip=True)
            docket = f"{series_prefix}-{link_year}-{nr}"

            decision_id = make_decision_id("tg_gerichte", docket)

            # Use the actual href from the page (portal switched to
            # zero-padded URLs like -nr-01 in 2024).
            # Hrefs may be relative (e.g. "../og/rbog-2024-nr-01").
            raw_href = urljoin(url, href)

            yield {
                "decision_id": decision_id,
                "docket_number": docket,
                "year": link_year,
                "nr": nr,
                "title": link_text[:200] if link_text else None,
                "url": raw_href,
            }

    def _discover_extra_section(self, section_path: str, index_suffix: str,
                                label: str, since_date=None) -> Iterator[dict]:
        """Discover decisions from Medienschaffende index → year pages."""
        index_url = f"{BASE_URL}/{section_path}/{index_suffix}"
        try:
            r = self.get(index_url)
        except Exception as e:
            logger.error(f"TG/{label}: failed to fetch index: {e}")
            return

        if r.status_code != 200:
            logger.debug(f"TG/{label}: index returned {r.status_code}")
            return

        soup = _soup(r)

        # Find year links (e.g. /og/2024, /og/2025), deduped
        years = set()
        for a in soup.find_all("a", href=True):
            m = RE_YEAR_LINK.search(a["href"])
            if m:
                years.add(int(m.group(1)))

        for year in sorted(years, reverse=True):
            if since_date and year < since_date.year - 1:
                continue
            yield from self._discover_extra_year(section_path, year, label)

    def _discover_extra_year(self, section_path: str, year: int,
                             label: str) -> Iterator[dict]:
        """Fetch a Medienschaffende year page and extract decision links."""
        url = f"{BASE_URL}/{section_path}/{year}"
        try:
            r = self.get(url)
        except Exception as e:
            logger.error(f"TG/{label}: failed to fetch year {year}: {e}")
            return

        if r.status_code != 200:
            logger.debug(f"TG/{label}: year {year} returned {r.status_code}")
            return

        soup = _soup(r)
        seen = set()

        for a in soup.find_all("a", href=True):
            href = a["href"]
            m = RE_EXTRA_SLUG.search(href)
            if not m:
                continue
            slug = m.group(1)
            if slug in seen:
                continue
            # Skip RBOG/TVR links that might appear in nav
            if slug.startswith(("rbog-", "tvr-")):
                continue
            seen.add(slug)

            docket = _slug_to_docket(slug)
            decision_id = make_decision_id("tg_gerichte", docket)
            link_url = urljoin(url, href)
            link_text = a.get_text(strip=True)

            yield {
                "decision_id": decision_id,
                "docket_number": docket,
                "title": link_text[:200] if link_text else None,
                "url": link_url,
                "pdf_mode": True,  # Signal that this is a PDF-only page
            }

    def _discover_pending_section(self, section_path: str, index_suffix: str,
                                  label: str, since_date=None) -> Iterator[dict]:
        """Discover decisions from 'Zur Publikation vorgesehen' pages."""
        url = f"{BASE_URL}/{section_path}/{index_suffix}"
        try:
            r = self.get(url)
        except Exception as e:
            logger.error(f"TG/{label}: failed to fetch pending page: {e}")
            return

        if r.status_code != 200:
            logger.debug(f"TG/{label}: pending page returned {r.status_code}")
            return

        soup = _soup(r)
        seen = set()

        for a in soup.find_all("a", href=True):
            href = a["href"]
            m = RE_EXTRA_SLUG.search(href)
            if not m:
                continue
            slug = m.group(1)
            if slug in seen:
                continue
            if slug.startswith(("rbog-", "tvr-")):
                continue
            seen.add(slug)

            docket = _slug_to_docket(slug)
            decision_id = make_decision_id("tg_gerichte", docket)
            link_url = urljoin(url, href)
            link_text = a.get_text(strip=True)

            yield {
                "decision_id": decision_id,
                "docket_number": docket,
                "title": link_text[:200] if link_text else None,
                "url": link_url,
                "pdf_mode": False,  # Inline HTML like regular decisions
            }

    def fetch_decision(self, stub: dict) -> Decision | None:
        """Fetch full decision page and extract content."""
        url = stub.get("url")
        if not url:
            return None

        try:
            r = self.get(url)
        except Exception as e:
            logger.warning(f"TG: fetch failed for {stub['docket_number']}: {e}")
            return None

        if r.status_code != 200:
            logger.warning(f"TG: {stub['docket_number']} returned {r.status_code}")
            return None

        soup = _soup(r)
        page = parse_entry_page(soup, url, stub["docket_number"],
                                pdf_text_fn=lambda u: self._fetch_pdf_text(u, stub["docket_number"]))
        full_text = page["full_text"]
        if len(full_text) < 50:
            logger.warning(f"TG: text too short for {stub['docket_number']}: {len(full_text)} chars")
            if not full_text:
                full_text = f"[Text extraction failed for {stub['docket_number']}]"
        if not page["decision_date"]:
            logger.warning(f"TG: no date for {stub.get('docket_number', '?')}")

        language = detect_language(full_text) if len(full_text) > 100 else "de"

        return Decision(
            decision_id=stub["decision_id"],
            court="tg_gerichte",
            canton="TG",
            chamber=page["chamber"],
            docket_number=page["docket_number"],
            docket_number_2=page["docket_number_2"],
            decision_date=page["decision_date"],
            language=language,
            title=fix_mojibake(stub.get("title")),
            regeste=page["regeste"],
            full_text=full_text,
            source_url=url,
            pdf_url=page["pdf_url"],
            cited_decisions=extract_citations(full_text) if len(full_text) > 200 else [],
        )

    def _fetch_pdf_text(self, pdf_url: str, docket: str) -> str | None:
        """Download PDF and extract text."""
        try:
            self._rate_limit()
            r = self.session.get(pdf_url, timeout=self.TIMEOUT)
            if r.status_code != 200:
                logger.warning(f"TG: PDF download failed for {docket}: {r.status_code}")
                return None
            return _extract_pdf_text(r.content)
        except Exception as e:
            logger.warning(f"TG: PDF extraction failed for {docket}: {e}")
            return None

    @staticmethod
    def _extract_inline_text(soup, docket: str) -> str:
        """Extract decision text from inline HTML content."""
        main = soup.find("main") or soup.find("article") or soup.find("div", class_="content-body")
        if not main:
            best = None
            best_len = 0
            for div in soup.find_all("div"):
                tlen = len(div.get_text(strip=True))
                if tlen > best_len:
                    best = div
                    best_len = tlen
            if best and best_len > 200:
                main = best

        if not main:
            return ""

        for tag in main.find_all(["nav", "footer", "header"]):
            tag.decompose()
        for tag in main.find_all(class_=re.compile(r"breadcrumb|sidebar|footer|nav")):
            tag.decompose()

        return main.get_text(separator="\n", strip=True)
