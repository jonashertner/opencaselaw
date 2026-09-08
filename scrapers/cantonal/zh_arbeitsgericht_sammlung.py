"""
Arbeitsgericht Zürich — annual "Entscheide des Arbeitsgerichtes Zürich"
=======================================================================
Ingests the court's own yearbook (Zitiervorschlag "AGer-Z <Jahr> Nr. <n>"),
published as one PDF per year at

  https://www.gerichte-zh.ch/themen/arbeit/hilfen/entscheidsammlung

Volumes 2003–2023 exist (21 files). From 2024 the court stopped printing and
publishes the same selection as individual PDFs in the gerichte-zh.ch TYPO3
decision database, which `zh_gerichte` scrapes (titles "AGer-Z 2024 Nr. 6: …").
This scraper therefore stops at MAX_VOLUME_YEAR.

Each volume is an edited selection of ~20–30 rulings: a short statement of
facts followed by verbatim excerpts of the Erwägungen («…»), closed by a
parenthetical trailer "(AH210141-L Urteil vom 21. Juni 2023, gegen diesen
Entscheid wurde kein Rechtsmittel ergriffen)". The trailer yields the real
Geschäftsnummer (docket_number_2), the judgment date and the appeal status.

Record shape:
  court            zh_arbeitsgericht (shared with the TYPO3 rows)
  docket_number    "AGer-Z 2023 Nr. 1"   — the court's citation form
  docket_number_2  "AH210141-L"          — from the trailer, when present
  decision_date    from the trailer, when present (else NULL — never guessed)
  collection       "AGer-Z 2023"
  external_id      "ager_z_2023_1"

Layout notes (all handled in split_volume):
  - 2003/2004/2005/2011 are landscape two-page scans: pages are cropped into
    halves before text extraction so the columns do not interleave.
  - Printer's marks ("rz_arbeitsgericht_08 16.6.2009 9:38 Uhr Seite 1",
    doubled-character InDesign slugs), running heads ("Kapitel I. Aus den
    Entscheiden") and bare page numbers are dropped.
  - Headings are "<n>. <Gesetz> <Art>; <Titel>" and are matched in sequence
    against the table of contents so numbered paragraphs inside a ruling
    ("3. Würdigung", "9. Januar 2015 …") cannot start a new record.
  - Volume-level idempotency: a volume is skipped when its "Nr. 1" is
    already in state (volumes never change once published).
"""
from __future__ import annotations

import difflib
import io
import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date

from bs4 import BeautifulSoup

from base_scraper import BaseScraper
from models import Decision, extract_citations, make_decision_id

logger = logging.getLogger(__name__)

HOST = "https://www.gerichte-zh.ch"
INDEX_URL = HOST + "/themen/arbeit/hilfen/entscheidsammlung"
COURT = "zh_arbeitsgericht"
MIN_VOLUME_YEAR = 2003
MAX_VOLUME_YEAR = 2023  # 2024+ are per-decision PDFs in the TYPO3 DB (zh_gerichte)

# ---------------------------------------------------------------- regexes

_VOLUME_YEAR_RE = re.compile(r"AGer-Z[ _%20]*(20\d\d)", re.IGNORECASE)
# Multi-line entry in the table of contents: "12. OR 336; Titel 34"
_TOC_ENTRY_RE = re.compile(r"^(\d{1,2})\.\s+(.+?)(?:\s+(\d{1,3}))?\s*$")
_TOC_STOP_RE = re.compile(
    r"^(?:Hinweis\b|EDITORIAL\b|Editorial\b|Vorwort\b)", re.IGNORECASE
)
# Roman section entries in the TOC, e.g. "II. Ergänzungen zu weitergezogenen Entscheiden 79"
_TOC_SECTION_RE = re.compile(
    r"^(?:[IVX]{1,4}|l{1,3}|1{1,3}|Il|lI)\.\s+([A-Za-zÄÖÜäöü].+?)(?:\s+\d{1,3})?\s*$"
)
_BODY_HEADING_RE = re.compile(r"^(?:[a-z]{1,4}\s+)?(\d{1,2})\s*[Oo]?\s*\.\s+(\S.*)$")
_BARE_NUMBER_RE = re.compile(r"^(\d{1,2})\s*\.\s*$")   # "23." with the title on the next line
# "<Gesetz> [<Art>[,/ <Art>…]];" — every yearbook title starts with a statute
_STATUTE_PREFIX_RE = re.compile(
    r"^[A-ZÄÖÜ][\w./\-]{0,18}(?:\s*[\w./\-]{1,12}){0,5}\s*[;:,]"
)
_MONTH_RE = (
    r"(Januar|Februar|März|Maerz|April|Mai|Juni|Juli|August|September|"
    r"Oktober|November|Dezember)"
)
_DATE_RE = re.compile(r"vom\s+(\d{1,2})\.\s*" + _MONTH_RE + r"\s+(\d{4})")
_DATE_NUM_RE = re.compile(r"vom\s+(\d{1,2})\.(\d{1,2})\.(\d{4})")   # 2003/2004: "vom 28.06.2004"
_MONTHS = {
    "januar": 1, "februar": 2, "märz": 3, "maerz": 3, "april": 4, "mai": 5,
    "juni": 6, "juli": 7, "august": 8, "september": 9, "oktober": 10,
    "november": 11, "dezember": 12,
}
# Closing trailer: "(AH210141-L Urteil vom 21. Juni 2023, …)" /
# "(AH120185 vom 30. April 2013. …)" / "(Abwesenheitsentscheid AH150177 vom …)"
_TRAILER_RE = re.compile(
    r"\((?:AGer\.?(?:\s*ZH)?\s*[,;]?\s*)?(?:Abwesenheitsentscheid\s+)?(A[A-Z]\d{5,6}(?:-[A-Z])?)\b([^()]{0,700})\)",
    re.DOTALL,
)
_PRINT_MARK_RES = (
    re.compile(r"^\S*arbeitsgericht_\d\d.*\bSeite\s+\d+\s*$", re.IGNORECASE),   # rz_arbeitsgericht_08 … Seite 1
    re.compile(r"^[0-9A-Za-z_.]{10,}\s+\d{1,4}\s+[\d.]{6,}\s+[\d:]{4,}\s*$"),  # doubled-char slug
    re.compile(r"^\d{1,3}\s*$"),                                       # bare page number
    re.compile(r"^Kapitel\s+[IVXl1-9]{1,4}[.:]?\s+\S.*$"),             # running head "Kapitel I. Aus den Entscheiden"
    re.compile(r"^Aus den Entscheiden\s+Kapitel\s+\d+\s*$", re.IGNORECASE),
    re.compile(r"^Ergänzungen Publikation [\d/]+\s+Kapitel\s+\d+\s*$"),      # 2003/2004 running head
    re.compile(r"^ARBEITSGERICHT ZÜRICH(\s+ARBEITSGERICHT ZÜRICH)*\s*$"),
)
_KERNING_RE = re.compile(r"\b([A-Z]) (?=[A-Z]{1,3}\s*\d)")  # "O R 1" / "Z PO 53"


# ---------------------------------------------------------------- helpers

def _norm(s: str) -> str:
    """Alphanumerics only, lower-case — for tolerant prefix comparison."""
    return re.sub(r"[^a-z0-9äöü]", "", (s or "").lower())


def _clean_title(t: str) -> str:
    t = " ".join((t or "").split())
    t = _KERNING_RE.sub(r"\1", t)
    # "S tatistischer" / "N oven": single capital + space + lowercase word at the start
    t = re.sub(r"^([A-ZÄÖÜ]) (?=[a-zäöü]{3,})", r"\1", t)
    t = re.sub(r"^(\d)[Oo]\b", r"\g<1>0", t)  # OCR "1O" → "10"
    return t.strip()


def _clean_lines(raw: str) -> str:
    """Drop printer's marks / running heads, de-hyphenate, normalise blanks."""
    out: list[str] = []
    for line in raw.replace("\f", "\n").split("\n"):
        line = line.rstrip()
        s = line.strip()
        if s and any(r.match(s) for r in _PRINT_MARK_RES):
            continue
        out.append(s)
    text = "\n".join(out)
    text = text.replace("­\n", "").replace("­", "")
    # "Ent-\nscheid" → "Entscheid", but keep "Zivil-\nund" as is.
    text = re.sub(r"(?<=\w)-\n(?!(?:und|oder|bzw|sowie|resp|beziehungsweise)\b)(?=[a-zäöü])", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_pdf_text(pdf_bytes: bytes) -> str:
    """Page texts joined by form feeds; landscape two-up scans are split in halves."""
    import pdfplumber

    parts: list[str] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            if page.width > page.height * 1.15:
                mid = page.width / 2
                halves = (
                    page.crop((0, 0, mid, page.height)),
                    page.crop((mid, 0, page.width, page.height)),
                )
            else:
                halves = (page,)
            for h in halves:
                try:
                    parts.append(h.extract_text() or "")
                except Exception as e:  # pragma: no cover - pdfplumber edge cases
                    logger.warning(f"AGer-Z page extraction failed: {e}")
                    parts.append("")
    return "\n\f\n".join(parts)


@dataclass
class VolumeEntry:
    number: int
    title: str
    text: str
    docket: str | None = None          # Geschäftsnummer from the trailer
    decision_date: date | None = None
    appeal_info: str | None = None


@dataclass
class VolumeParse:
    year: int
    entries: list[VolumeEntry] = field(default_factory=list)
    toc_count: int = 0
    missing: list[int] = field(default_factory=list)


def _parse_toc(lines: list[str]) -> tuple[dict[int, str], list[str], int]:
    """Return ({n: title}, [section titles], index of the last TOC line)."""
    start = None
    for i, ln in enumerate(lines):
        if ln.strip().lower().startswith("inhaltsverzeichnis"):
            start = i
            break
    if start is None:
        return {}, [], 0

    titles: dict[int, str] = {}
    sections: list[str] = []
    last = start
    pending: int | None = None   # entry number awaiting its page number
    seen_entry = False
    for i in range(start + 1, min(start + 120, len(lines))):
        ln = lines[i].strip()
        if not ln:
            continue
        ln = re.sub(r"^(\d)[Oo]\s*\.", r"\g<1>0.", ln)
        # imprint text bleeding into a two-up TOC
        ln = re.sub(r"^(?:Erscheint jährlich\.|Preis Fr\.\s*[\d.'’–-]+|Layout(?: und Druck)?:.*?Zürich|Druck:.*?Zürich)\s*", "", ln)
        if _TOC_STOP_RE.match(ln):
            if seen_entry:
                break
            continue  # "Editorial 3" above the entries
        m_sec = _TOC_SECTION_RE.match(ln)
        m = _TOC_ENTRY_RE.match(ln)
        if m and int(m.group(1)) <= 60 and (not m_sec or not re.match(r"^(?:[IVX]{1,4}|Il|lI|l{1,3})\.", ln)):
            n, title, page = int(m.group(1)), m.group(2), m.group(3)
            if n == 1 and re.search(r"aus den entscheiden", title, re.IGNORECASE):
                # "1. Aus den Entscheiden" is a section in the 2003–2005 layout
                seen_entry = False
                continue
            if seen_entry and n in titles and n < max(titles):
                break  # numbering restarted: we ran into a different list
            titles[n] = _clean_title(title)
            pending = None if page else n
            seen_entry = True
            last = i
            continue
        if m_sec and re.match(r"^(?:[IVX]{1,4}|Il|lI|l{1,3}|1{1,3})\.\s", ln):
            sections.append(_clean_title(m_sec.group(1)))
            pending = None
            last = i
            if seen_entry and re.search(r"statistisch|überblick", ln, re.IGNORECASE):
                break
            continue
        # continuation line of a wrapped title
        if seen_entry and titles:
            n = pending if pending is not None else max(titles)
            cont = re.sub(r"\s+\d{1,3}\s*$", "", ln) if pending is not None else ln
            if len(cont) < 90:
                titles[n] = _clean_title(titles[n] + " " + cont)
                last = i
                if pending is not None and re.search(r"\s\d{1,3}\s*$", ln):
                    pending = None
    return titles, sections, last


def _is_heading(line: str, n: int, toc_title: str | None, next_line: str = "") -> bool:
    line = line.strip()
    if _BARE_NUMBER_RE.match(line) and next_line.strip():
        line = f"{line} {next_line.strip()}"
    m = _BODY_HEADING_RE.match(line)
    if not m:
        # OCR lost the number: accept the bare TOC title, verbatim at line start
        if toc_title and len(_norm(toc_title)) >= 12:
            return _norm(line)[:14] == _norm(toc_title)[:14]
        return False
    num = m.group(1)
    if re.search(r"\d\s*[Oo]\s*\.", line):      # "1 O." → 10
        num = num + "0"
    if int(num) != n:
        return False
    rest = m.group(2)
    if re.match(r"^" + _MONTH_RE + r"\b", rest):  # "9. Januar 2015 …"
        return False
    if toc_title:
        a, b = _norm(rest)[:10], _norm(toc_title)[:10]
        if a and b and (a == b or difflib.SequenceMatcher(None, a, b).ratio() >= 0.8):
            return True
        # Wrapped heading: TOC title continues on the next line, compare the shorter one
        if a and b and len(a) >= 4 and (b.startswith(a) or a.startswith(b)):
            return True
        return False
    return bool(_STATUTE_PREFIX_RE.match(_KERNING_RE.sub(r"\1", rest)))


def _find_section_end(lines: list[str], start: int, sections: list[str]) -> int:
    """First line after `start` that opens the next part of the volume."""
    keys = [_norm(s)[:14] for s in sections if s] + [
        _norm(s)[:14] for s in (
            "Ergänzungen zu weitergezogenen Entscheiden", "Statistischer Überblick",
            "Datenschutz am Arbeitsplatz", "Tücken bei der Klageeinleitung",
            "Umfrage über die Tätigkeit",
        )
    ]
    keys = [k for k in keys if k and not k.startswith("ausdenentscheid")]
    for i in range(start, len(lines)):
        s = lines[i].strip()
        if not s:
            continue
        m = re.match(r"^(?:[IVX]{1,4}|Il|lI|l{1,3}|1{1,3})\.\s+(.+)$", s)
        if m:
            body = _norm(m.group(1))[:14]
            if any(body.startswith(k[:10]) for k in keys):
                return i
            if re.match(r"^[A-ZÄÖÜ]{5,}", m.group(1)):
                return i
        elif any(_norm(s)[:14].startswith(k[:12]) for k in keys if len(k) >= 12):
            return i
    return len(lines)


def _parse_trailer(text: str, year: int) -> tuple[str | None, date | None, str | None]:
    last = None
    for m in _TRAILER_RE.finditer(text):
        last = m
    if not last:
        return None, None, None
    docket, rest = last.group(1), " ".join(last.group(2).split())
    dec_date = None
    dm = _DATE_RE.search(rest)
    try:
        if dm:
            d = date(int(dm.group(3)), _MONTHS[dm.group(2).lower()], int(dm.group(1)))
        else:
            dm = _DATE_NUM_RE.search(rest)
            d = date(int(dm.group(3)), int(dm.group(2)), int(dm.group(1))) if dm else None
        if d and year - 3 <= d.year <= year + 1:
            dec_date = d
    except (ValueError, KeyError):
        pass
    appeal = rest.strip(" ;,.")
    if dm:
        appeal = rest[dm.end():].strip(" ;,.") or None
    return docket, dec_date, appeal


def split_volume(raw_text: str, year: int) -> VolumeParse:
    """Split one yearbook's text into its numbered rulings."""
    text = _clean_lines(raw_text)
    lines = text.split("\n")
    titles, sections, toc_end = _parse_toc(lines)
    result = VolumeParse(year=year, toc_count=max(titles) if titles else 0)

    # Body starts after the TOC; prefer the "I. AUS DEN ENTSCHEIDEN" banner.
    body_start = toc_end + 1
    for i in range(toc_end + 1, len(lines)):
        if re.match(r"^(?:[IVX1l]{1,3}\.\s+)?AUS DEN ENTSCHEIDEN\s*$", lines[i].strip()):
            body_start = i + 1
            break
    else:
        for i in range(toc_end + 1, len(lines)):
            if re.match(r"^(?:\d\.\s+)?Aus den Entscheiden\s*$", lines[i].strip()) or lines[i].strip() == "EDITORIAL":
                body_start = i + 1

    max_n = max(titles) if titles else 60
    starts: list[tuple[int, int]] = []   # (number, line index)
    pos = body_start
    for n in range(1, max_n + 1):
        found = None
        for i in range(pos, len(lines)):
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            if _is_heading(lines[i], n, titles.get(n), nxt):
                found = i
                break
            # untitled volumes: stop at the next part of the book
            if not titles and re.match(r"^(?:II|III|IV|V)\.\s+[A-ZÄÖÜ]{5,}", lines[i].strip()):
                break
        if found is None:
            if titles:
                result.missing.append(n)
                continue
            break
        starts.append((n, found))
        pos = found + 1

    if not starts:
        return result
    end = _find_section_end(lines, starts[-1][1] + 1, sections)

    for idx, (n, s) in enumerate(starts):
        e = starts[idx + 1][1] if idx + 1 < len(starts) else end
        chunk_lines = lines[s:e]
        # OCR margin crumbs after the trailer ("v", "ten", ".--.")
        while len(chunk_lines) > 3 and len(chunk_lines[-1].strip()) < 5:
            chunk_lines.pop()
        chunk = "\n".join(chunk_lines).strip()
        hm = _BODY_HEADING_RE.match(lines[s].strip())
        heading_rest = hm.group(2) if hm else lines[s].strip()
        title = titles.get(n) or _clean_title(heading_rest)
        docket, dec_date, appeal = _parse_trailer(chunk, year)
        result.entries.append(VolumeEntry(
            number=n, title=title, text=chunk,
            docket=docket, decision_date=dec_date, appeal_info=appeal,
        ))
    return result


# ---------------------------------------------------------------- scraper

class ZHArbeitsgerichtSammlungScraper(BaseScraper):
    """One stub per yearbook ruling; the volume PDF is fetched once per year."""

    REQUEST_DELAY = 2.0
    TIMEOUT = 120
    MAX_ERRORS = 5

    @property
    def court_code(self) -> str:
        return "zh_arbeitsgericht_sammlung"

    # -- discovery ------------------------------------------------------

    def list_volumes(self) -> list[tuple[int, str]]:
        """[(year, pdf_url)] from the index page, oldest first, one per year."""
        resp = self.get(INDEX_URL, timeout=self.TIMEOUT)
        soup = BeautifulSoup(resp.text, "html.parser")
        found: dict[int, str] = {}
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if not href.lower().endswith(".pdf"):
                continue
            m = _VOLUME_YEAR_RE.search(href) or _VOLUME_YEAR_RE.search(a.get_text(" ", strip=True))
            if not m:
                continue
            year = int(m.group(1))
            if MIN_VOLUME_YEAR <= year <= MAX_VOLUME_YEAR and year not in found:
                found[year] = self.normalize_url(href, HOST)
        return sorted(found.items())

    def discover_new(self, since_date=None) -> Iterator[dict]:
        volumes = self.list_volumes()
        logger.info(f"AGer-Z: {len(volumes)} volumes listed on {INDEX_URL}")
        for year, url in volumes:
            probe = make_decision_id(COURT, f"AGer-Z {year} Nr. 1")
            if self.state.is_known(probe):
                logger.debug(f"AGer-Z {year}: volume already ingested, skipping")
                continue
            try:
                resp = self.get(url, timeout=self.TIMEOUT)
            except Exception as e:
                logger.error(f"AGer-Z {year}: download failed: {e}")
                continue
            if len(resp.content) < 10_000:
                logger.warning(f"AGer-Z {year}: tiny PDF ({len(resp.content)} bytes), skipping")
                continue
            parsed = split_volume(extract_pdf_text(resp.content), year)
            logger.info(
                f"AGer-Z {year}: {len(parsed.entries)} rulings "
                f"(TOC {parsed.toc_count}, missing {parsed.missing or 'none'})"
            )
            for entry in parsed.entries:
                docket = f"AGer-Z {year} Nr. {entry.number}"
                did = make_decision_id(COURT, docket)
                if self.state.is_known(did):
                    continue
                yield {
                    "decision_id": did,
                    "docket_number": docket,
                    "year": year,
                    "pdf_url": url,
                    "entry": entry,
                }

    # -- fetch ----------------------------------------------------------

    def fetch_decision(self, stub: dict) -> Decision | None:
        entry: VolumeEntry = stub["entry"]
        year = stub["year"]
        docket = stub["docket_number"]
        body = entry.text
        if len(body) < 200:
            logger.warning(f"{docket}: only {len(body)} chars, skipping")
            return None
        full_text = f"{docket}\n{entry.title}\n\n{body}"
        return Decision(
            decision_id=stub["decision_id"],
            court=COURT,
            canton="ZH",
            chamber=None,
            docket_number=docket,
            docket_number_2=entry.docket,
            decision_date=entry.decision_date,
            language="de",
            title=entry.title,
            legal_area="Arbeitsrecht",
            regeste=None,
            full_text=full_text,
            decision_type="Urteilsauszug",
            collection=f"AGer-Z {year}",
            appeal_info=entry.appeal_info,
            source_url=stub["pdf_url"],
            pdf_url=stub["pdf_url"],
            cited_decisions=extract_citations(body) if len(body) > 200 else [],
            external_id=f"ager_z_{year}_{entry.number}",
        )
