"""
Aargau: two closed decision collections on ag.ch
=================================================

``ag_baugesetzgebung``: the "Entscheidsammlung Baugesetzgebung" of the
Rechtsabteilung of the Departement Bau, Verkehr und Umwelt (BVU), 1991-2021.
Rulings of the BVU, the Regierungsrat, the Verwaltungsgericht, the
Schätzungskommission and the Bundesgericht on building and planning law, each
a PDF (headnote, the decisive Erwägungen, the deciding authority and date).
Page: https://www.ag.ch/de/themen/planen-bauen/baurecht/entscheidsammlung
"Since January 2022 these decisions are published in the AGVE portal; the
collection is no longer continued" (the page). The ag_gerichte scraper holds
the later ones under ag_departement_bvu.

The page renders the collection with a JavaScript "filelist" that POSTs to
``/app/search-service/api/v1/doctable`` (a JSON query: categories filter,
pagination, sort). 255 entries on 2026-11-08, in five categories (the
deciding authority): BVU 116, VG 83, RR 44, BGer 11, SKE 1. 254 link a PDF
(249 distinct files) under ``/media/kanton-aargau/bvu/bauen/baurecht/
entscheidsammlung-rechtsabteilung-bvu/``; one (VG, 1993-12-23,
"Parteientschädigung") has a headnote but no document.

``ag_weitere``: the Grundbuch und Notariat decisions of the Departement
Volkswirtschaft und Inneres (10 PDFs, 2001-2015) on
https://www.ag.ch/de/themen/planen-bauen/grundbuch-vermessung/grundbuch/rechtliche-grundlagen/entscheide
(a static HTML table: title, date, PDF). Its other rows in the corpus, the
"weitere Entscheide" of the Aargau courts, are frozen entscheidsuche copies
whose ag.ch PDFs return 404 since the 2022 site migration.

Identity
--------
Until 2026-11 both codes were fed only by entscheidsuche, which keyed each
entry on its TITLE: entries sharing a title ("Planbeschwerde" 6 times,
"Submission" 15, "Legitimation eines Nachbarn" twice) collapsed into one, so
51 rulings of the BVU collection were never held. Here the id is the PDF's
file stem, unique within each collection and the one thing ag.ch has kept
stable (``ag_baugesetzgebung_1993-12-03-missbraeuchliche-prozessfuehrung``),
and it is also the docket: these excerpts mostly print no case number, and
the build's dedup keys on (court, docket, date), so a shared docket would fold
the three excerpts of 1997-03-04 ("Planbeschwerde", "-i", "-ii") into one. A
case number the header does print (``WBE.2012.199``, ``BVURA.11.722``) goes
to docket_number_2.

The frozen entscheidsuche rows of both codes are folded into these by
scripts/ag_entscheidsammlungen_es_fold.py (matched by PDF; the direct row
takes the es id as previous_decision_id, the es row is quarantined), see
runbooks/ag_entscheidsammlungen_2026-11.md.

Dates
-----
The decision date is the one the PDF prints for the excerpted ruling: the
form field of the older PDFs ("Entscheid des Verwaltungsgerichts (III/7) vom
21.02.1994", a line of its own, often extracted last) or the header of the
newer ones ("Aus dem Entscheid des Departements Bau, Verkehr und Umwelt (EBVU)
vom 26. Oktober 2010 (BVU-RA.10.458)", before "Aus den Erwägungen"). Not the
first date in the text, which is usually a statute's ("USG vom 7. Oktober
1983"): most of the 21 entscheidsuche dates that differ from the PDF's came
from there. A PDF
without either takes the portal's document date. Measured over the 254 PDFs
on 2026-11-08: header = portal date 238, header = file-name date where the
portal date is off 9, no header 6 (portal date taken).
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Iterator
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from base_scraper import BaseScraper
from models import Decision, extract_citations, make_decision_id, parse_date
from scrapers.cantonal.ag_gerichte import extract_text_from_pdf

logger = logging.getLogger(__name__)

AG_HOST = "https://www.ag.ch"

BVU_PAGE_URL = f"{AG_HOST}/de/themen/planen-bauen/baurecht/entscheidsammlung"
DOCTABLE_API = f"{AG_HOST}/app/search-service/api/v1/doctable"
BVU_MEDIA_PREFIX = "/media/kanton-aargau/bvu/bauen/baurecht/entscheidsammlung-rechtsabteilung-bvu/"
# The page's data-filelist-categories (category key -> deciding authority).
BVU_CATEGORIES = {
    "f46f0fe7-d92e-45a9-b5b7-0f3402debb21": "Departement Bau, Verkehr und Umwelt",
    "a2f10c55-82f9-403c-b7fa-6a5dfc3cc01f": "Regierungsrat",
    "b8460a34-2978-4878-9f84-00b9c32b1114": "Schätzungskommission nach Baugesetz",
    "110b2681-6e2a-4e11-9bfa-8e270b6cee24": "Verwaltungsgericht",
    "9a6bb2aa-9a7c-4f2f-92d9-ceb395637d2a": "Bundesgericht",
}
# The API refuses responses over 256 KiB ("Exceeded limit on max bytes to
# buffer"): 100 entries per page stay far below it.
DOCTABLE_PAGE_SIZE = 100

GRUNDBUCH_PAGE_URL = (f"{AG_HOST}/de/themen/planen-bauen/grundbuch-vermessung/grundbuch/"
                      "rechtliche-grundlagen/entscheide")
GRUNDBUCH_MEDIA_PREFIX = "/media/kanton-aargau/dvi/dokumente/arp/grundbuch/entscheide/"

# ── dates ────────────────────────────────────────────────────────────────

_MONTHS = {"januar": 1, "jänner": 1, "februar": 2, "märz": 3, "maerz": 3, "april": 4,
           "mai": 5, "juni": 6, "juli": 7, "august": 8, "september": 9, "oktober": 10,
           "november": 11, "dezember": 12}
_MONTH_ALT = "|".join(sorted(_MONTHS, key=len, reverse=True))
_KIND = (r"(?:Entscheid|Urteil|Verfügung|Beschluss|Auskunft|Regierungsratsbeschluss|"
         r"Präsidialentscheid|Zwischenentscheid|Bundesgerichtsentscheid|Schreiben|"
         r"Stellungnahme|Gutachten)")
_DATE = (r"(?:(\d{1,2})\.\s?(\d{1,2})\.\s?(\d{2,4})|(\d{1,2})\.?\s*(" + _MONTH_ALT
         + r")\s+(\d{4}))")
_DATE_RE = re.compile(_DATE, re.IGNORECASE)
# The form field of the older PDFs: a line of its own that opens with the
# kind of ruling and its authority, dated DD.MM.YYYY, optionally followed by
# the parties or a page reference.
_FORM_START_RE = re.compile(r"^\s*(?:Aus dem\s+)?" + _KIND + r"\s+(?:des|der|vom|\()",
                            re.IGNORECASE)
_FORM_VOM_RE = re.compile(r"\bvom\s+(\d{2})\.(\d{2})\.(\d{4})\b")
# The header of the newer PDFs, before "Aus den Erwägungen".
_HEAD_RE = re.compile(r"(?:^|\n)\s*(?:Aus dem\s+)?" + _KIND + r"\b[^\n]{0,220}?\bvom\s+"
                      + _DATE, re.IGNORECASE)
_ERWAEGUNGEN_RE = re.compile(r"Aus den Erwägungen")
# A case number in the header's parentheses: (WBE.2012.199), (BVURA.11.722)
_CASE_NO_RE = re.compile(r"\(([A-Z]{2,}(?:-[A-Z]+)?\.\d{2,4}\.\d{1,5})\)")
_KEYWORDS_RE = re.compile(r"Stichw(?:ö|o)rt(?:e|er)?\s*:\s*(.+?)(?:\n\s*\n|\s*\d+ von \d+|$)",
                          re.DOTALL)


def _iso(day: int, month: int, year: int) -> str | None:
    if year < 100:
        year += 1900 if year > 30 else 2000
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def _match_date(m: re.Match, offset: int = 0) -> str | None:
    g = m.groups()[offset:offset + 6]
    if g[0]:
        return _iso(int(g[0]), int(g[1]), int(g[2]))
    return _iso(int(g[3]), _MONTHS[g[4].lower()], int(g[5]))


def _join_breaks(text: str) -> str:
    """Undo the line breaks that split a header: hyphenated words
    ("De-\\nzember"), "vom" at a line end or start, a year on its own line."""
    t = re.sub(r"(?<=[a-zäöü])-[ \t]*\n[ \t]*(?=[a-zäöü])", "", text)
    t = re.sub(r"(?<=[a-zäöü])- (?=[a-zäöü])", "", t)
    t = re.sub(r"(?<=[A-Z])-[ \t]*\n[ \t]*(?=[A-Z])", "-", t)
    t = re.sub(r"[ \t]*\n[ \t]*(?=vom\s+\d)", " ", t)
    t = re.sub(r"(\bvom)[ \t]*\n[ \t]*(?=\d)", r"\1 ", t)
    t = re.sub(r"(vom\s+\d{1,2}\.?\s*[A-Za-zäöü]+)[ \t]*\n[ \t]*(\d{4})", r"\1 \2", t)
    return t


def ruling_header(text: str) -> tuple[str | None, str | None, str | None]:
    """(decision date, how, header line) of an excerpt PDF's text.

    ``how`` is "form" (the older PDFs' own date line) or "head" (the newer
    PDFs' header before the Erwägungen); (None, None, None) when the PDF
    prints neither.
    """
    t = _join_breaks(text or "")
    for line in t.split("\n"):
        if len(line) > 240 or not _FORM_START_RE.match(line):
            continue
        if len(_DATE_RE.findall(line)) != 1:
            continue  # a citation line naming several acts, not the field
        m = _FORM_VOM_RE.search(line)
        if m:
            d = _iso(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            if d:
                return d, "form", line.strip()
    cut = _ERWAEGUNGEN_RE.search(t)
    head = t[:cut.start()] if cut else t[:1500]
    for m in _HEAD_RE.finditer(head):
        d = _match_date(m)
        if d:
            return d, "head", m.group(0).strip()
    return None, None, None


def case_number(header_line: str | None, text: str) -> str | None:
    """The case number the header prints in parentheses, if any."""
    for src in (header_line or "", _join_breaks(text or "")[:2500]):
        m = _CASE_NO_RE.search(src)
        if m:
            return m.group(1)
    return None


def keywords(text: str) -> str | None:
    m = _KEYWORDS_RE.search(text or "")
    if not m:
        return None
    kw = " ".join(m.group(1).split())
    kw = re.sub(r"(?<=[a-zäöü])- (?=[a-zäöü])", "", kw)
    return kw[:300] or None


def pdf_stem(url: str) -> str:
    """``.../1993-12-03-missbraeuchliche-prozessfuehrung.pdf`` ->
    ``1993-12-03-missbraeuchliche-prozessfuehrung``."""
    name = url.split("?", 1)[0].rstrip("/").split("/")[-1]
    return re.sub(r"\.pdf$", "", name, flags=re.IGNORECASE)


def _clean(s: str | None) -> str:
    return " ".join((s or "").split())


# ── ag_baugesetzgebung ───────────────────────────────────────────────────

def doctable_query(page: int, size: int = DOCTABLE_PAGE_SIZE) -> dict:
    """The JSON body the page's filelist POSTs (all five categories)."""
    return {
        "pagination": {"page": page, "size": size},
        "filter": {"operator": "in", "field": "categories",
                   "values": list(BVU_CATEGORIES)},
        "sort": [{"field": "documentDatetime", "direction": "desc"}],
    }


def _entry(f: dict) -> dict:
    cat = next((c for c in f.get("categories") or [] if c in BVU_CATEGORIES), None)
    return {"id": f.get("id"), "title": _clean(f.get("title")),
            "teaser": _clean(f.get("teaser")) or None,
            "date": (f.get("documentDatetime") or "")[:10] or None,
            "chamber": BVU_CATEGORIES.get(cat)}


def group_bvu_entries(files: list[dict]) -> tuple[list[dict], list[str]]:
    """One stub per PDF from the doctable entries, plus warnings.

    A stub keeps every entry that links its PDF; select_entries decides,
    against the PDF's text, which of them describe it. Entries without a
    document are reported and not taken.
    """
    warnings: list[str] = []
    by_pdf: dict[str, list[dict]] = defaultdict(list)
    for f in files:
        urls = [u.get("url") for u in (f.get("targetUrls") or []) if u.get("url")]
        if not urls:
            warnings.append(f"entry without a document, not taken: "
                            f"{(f.get('documentDatetime') or '')[:10]} {_clean(f.get('title'))!r}")
            continue
        by_pdf[urljoin(AG_HOST, urls[0])].append(_entry(f))
    stubs = []
    for pdf_url, entries in by_pdf.items():
        dates = [e["date"] for e in entries if e["date"]]
        stubs.append({
            "docket_number": pdf_stem(pdf_url),
            "pdf_url": pdf_url,
            "entries": entries,
            "portal_date": min(dates) if dates else None,
        })
    stubs.sort(key=lambda s: s["docket_number"])
    return stubs, warnings


def _norm_words(s: str | None) -> str:
    s = re.sub(r"(?<=[a-zäöü])-\s+(?=[a-zäöü])", "", (s or "").lower())
    return re.sub(r"[\W_]+", "", s)


def select_entries(stub: dict, text: str) -> tuple[list[dict], list[dict]]:
    """(entries describing this PDF, entries linking it by mistake).

    Two entries can link one PDF. On 2026-11-08 all five such pairs were a
    wrong link: the second entry is another ruling (2012-08-23 "Submission"
    links the 2012-07-26 Stützmauer PDF) whose headnote is not in the PDF. An
    entry belongs to the PDF when its headnote's opening is in the PDF's
    text; when none (or every) entry passes that test, the entry dated as
    the file name is taken (else the first).
    """
    entries = stub.get("entries") or []
    if len(entries) <= 1:
        return entries, []
    body = _norm_words(text)
    own = [e for e in entries if e["teaser"] and _norm_words(e["teaser"])[:80] in body]
    if not own or len(own) == len(entries) and len({e["date"] for e in entries}) > 1:
        stem = stub["docket_number"]
        file_date = stem[:10] if re.match(r"\d{4}-\d{2}-\d{2}$", stem[:10]) else None
        own = [e for e in entries if e["date"] == file_date][:1] or entries[:1]
    return own, [e for e in entries if e not in own]


class AGBaugesetzgebungScraper(BaseScraper):
    """BVU Rechtsabteilung Entscheidsammlung (closed 2022)."""

    REQUEST_DELAY = 1.5
    TIMEOUT = 60
    COURT = "ag_baugesetzgebung"
    COLLECTION = "Entscheidsammlung Baugesetzgebung (BVU Rechtsabteilung)"

    @property
    def court_code(self) -> str:
        return self.COURT

    def list_entries(self) -> list[dict]:
        """Every doctable entry of the collection (raises when the API fails
        or answers with nothing: the page or the API moved)."""
        files: list[dict] = []
        page = 0
        while True:
            resp = self.post(DOCTABLE_API, json=doctable_query(page),
                             headers={"Content-Type": "application/json",
                                      "Accept": "application/json"})
            data = resp.json()
            batch = data.get("files")
            control = data.get("control") or {}
            if batch is None:
                raise RuntimeError(f"[{self.COURT}] doctable answered without files: "
                                   f"{str(data)[:200]}")
            files.extend(batch)
            total_pages = int(control.get("totalPages") or 0)
            page += 1
            if page >= total_pages or not batch:
                break
        total = (control or {}).get("totalElements")
        if not files:
            raise RuntimeError(f"[{self.COURT}] doctable lists no entry; API or page moved?")
        if isinstance(total, int) and total != len(files):
            raise RuntimeError(f"[{self.COURT}] doctable listed {len(files)} of {total} entries")
        self.portal_count = total if isinstance(total, int) else len(files)
        return files

    def discover_new(self, since_date=None) -> Iterator[dict]:
        try:
            files = self.list_entries()
        except Exception as e:
            logger.error(f"[{self.COURT}] ERROR listing failed: {e}")
            raise
        stubs, warnings = group_bvu_entries(files)
        for w in warnings:
            logger.warning(f"[{self.COURT}] {w}")
        logger.info(f"[{self.COURT}] {len(files)} entries, {len(stubs)} documents")
        for stub in stubs:
            if not stub["pdf_url"].startswith(AG_HOST + BVU_MEDIA_PREFIX):
                logger.warning(f"[{self.COURT}] document outside the collection folder: "
                               f"{stub['pdf_url']}")
            if self.state.is_known(make_decision_id(self.COURT, stub["docket_number"])):
                continue
            if since_date and stub["portal_date"]:
                d = parse_date(stub["portal_date"])
                if d and d < since_date:
                    continue
            yield stub

    def fetch_decision(self, stub: dict) -> Decision | None:
        try:
            resp = self.get(stub["pdf_url"])
        except Exception as e:
            logger.error(f"[{self.COURT}] PDF fetch failed for {stub['docket_number']}: {e}")
            return None
        text = extract_text_from_pdf(resp.content)
        if not text or len(text.strip()) < 100:
            logger.warning(f"[{self.COURT}] no text in {stub['pdf_url']}")
            return None
        return self.build_decision(stub, self.clean_text(text))

    def build_decision(self, stub: dict, text: str) -> Decision:
        own, stray = select_entries(stub, text)
        for e in stray:
            logger.warning(f"[{self.COURT}] portal links {stub['docket_number']}.pdf from "
                           f"another ruling's entry too, not attached: {e['date']} {e['title']!r}")
        titles = list(dict.fromkeys(e["title"] for e in own if e["title"]))
        teasers = list(dict.fromkeys(e["teaser"] for e in own if e["teaser"]))
        d, how, header = ruling_header(text)
        portal = own[0]["date"] if own else stub.get("portal_date")
        if not d:
            d = portal
        elif portal and d != portal:
            logger.info(f"[{self.COURT}] {stub['docket_number']}: PDF dates the ruling {d} "
                        f"({how}), portal {portal}; taking the PDF's")
        chamber = own[0]["chamber"] if own else None
        if chamber == BVU_CATEGORIES["f46f0fe7-d92e-45a9-b5b7-0f3402debb21"] and header \
                and re.search(r"Baudepartement", header):
            chamber = "Baudepartement"  # the BVU's name until 2005, as the PDF prints it
        return Decision(
            decision_id=make_decision_id(self.COURT, stub["docket_number"]),
            court=self.COURT,
            canton="AG",
            chamber=chamber,
            docket_number=stub["docket_number"],
            docket_number_2=case_number(header, text),
            decision_date=parse_date(d) if d else None,
            language="de",
            title="; ".join(titles) or stub["docket_number"],
            legal_area=keywords(text),
            regeste="\n\n".join(teasers) or None,
            full_text=text,
            collection=self.COLLECTION,
            source_url=stub["pdf_url"],
            pdf_url=stub["pdf_url"],
            cited_decisions=extract_citations(text),
            scraped_at=datetime.now(timezone.utc),
        )


# ── ag_weitere (Grundbuch und Notariat) ──────────────────────────────────

_PDF_META_RE = re.compile(r"\s*\(PDF,[^)]*\)\s*$")
_LISTED_DATE_RE = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{4})")


def parse_grundbuch_page(html: str) -> list[dict]:
    """Rows of the Grundbuch decisions table: title, listed date, PDF."""
    soup = BeautifulSoup(html, "html.parser")
    out, seen = [], set()
    for tr in soup.find_all("tr"):
        a = tr.find("a", href=True)
        if not a or not re.search(r"\.pdf(?:$|\?)", a["href"], re.IGNORECASE):
            continue
        pdf_url = urljoin(AG_HOST, a["href"])
        if pdf_url in seen:
            continue
        seen.add(pdf_url)
        cells = tr.find_all("td")
        title = _PDF_META_RE.sub("", _clean(a.get_text(" ")))
        listed = None
        for td in cells:
            m = _LISTED_DATE_RE.fullmatch(_clean(td.get_text(" ")))
            if m:
                listed = _iso(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        out.append({"docket_number": pdf_stem(pdf_url), "pdf_url": pdf_url,
                    "title": title, "portal_date": listed})
    return out


_GB_AUTHORITIES = [
    (re.compile(r"verwaltungsgericht", re.I), "Verwaltungsgericht"),
    (re.compile(r"regierungsrat", re.I), "Regierungsrat"),
    (re.compile(r"departement\w*\s+volkswirtschaft und inneres", re.I),
     "Departement Volkswirtschaft und Inneres"),
    # the DVI's predecessor until 2005
    (re.compile(r"departement\w*\s+des\s+innern", re.I), "Departement des Innern"),
]


def grundbuch_chamber(title: str, text: str = "") -> str | None:
    """The deciding authority the title names, else the text's opening."""
    for src in (title or "", (text or "")[:1500]):
        for rx, name in _GB_AUTHORITIES:
            if rx.search(src):
                return name
    return None


class AGWeitereScraper(BaseScraper):
    """Grundbuch und Notariat decisions of the DVI (ag_weitere)."""

    REQUEST_DELAY = 1.5
    TIMEOUT = 60
    COURT = "ag_weitere"
    COLLECTION = "Entscheide Grundbuch und Notariat (DVI)"

    @property
    def court_code(self) -> str:
        return self.COURT

    def discover_new(self, since_date=None) -> Iterator[dict]:
        try:
            html = self.get(GRUNDBUCH_PAGE_URL).text
        except Exception as e:
            logger.error(f"[{self.COURT}] ERROR listing failed: {e}")
            raise
        rows = parse_grundbuch_page(html)
        if not rows:
            raise RuntimeError(f"[{self.COURT}] {GRUNDBUCH_PAGE_URL} links no PDF; page moved?")
        self.portal_count = len(rows)
        logger.info(f"[{self.COURT}] {len(rows)} Grundbuch decisions listed")
        for r in rows:
            if self.state.is_known(make_decision_id(self.COURT, r["docket_number"])):
                continue
            if since_date and r["portal_date"]:
                d = parse_date(r["portal_date"])
                if d and d < since_date:
                    continue
            yield r

    def fetch_decision(self, stub: dict) -> Decision | None:
        try:
            resp = self.get(stub["pdf_url"])
        except Exception as e:
            logger.error(f"[{self.COURT}] PDF fetch failed for {stub['docket_number']}: {e}")
            return None
        text = extract_text_from_pdf(resp.content)
        if not text or len(text.strip()) < 100:
            logger.warning(f"[{self.COURT}] no text in {stub['pdf_url']}")
            return None
        return self.build_decision(stub, self.clean_text(text))

    def build_decision(self, stub: dict, text: str) -> Decision:
        # The table's date is the ruling's (it is also in every title).
        d = stub.get("portal_date") or ruling_header(text)[0]
        return Decision(
            decision_id=make_decision_id(self.COURT, stub["docket_number"]),
            court=self.COURT,
            canton="AG",
            chamber=grundbuch_chamber(stub["title"], text),
            docket_number=stub["docket_number"],
            decision_date=parse_date(d) if d else None,
            language="de",
            title=stub["title"],
            full_text=text,
            collection=self.COLLECTION,
            source_url=stub["pdf_url"],
            pdf_url=stub["pdf_url"],
            cited_decisions=extract_citations(text),
            scraped_at=datetime.now(timezone.utc),
        )
