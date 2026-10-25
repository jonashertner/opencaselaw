"""
UN treaty bodies — General Comments and General Recommendations
================================================================

  https://tbinternet.ohchr.org/_layouts/15/treatybodyexternal/TBSearch.aspx?Lang=en&TreatyID={id}&DocTypeID=11
  https://tbinternet.ohchr.org/_layouts/15/treatybodyexternal/Download.aspx?symbolno={symbol}&Lang={lang}

The committees' authoritative readings of their treaties — CAT General
Comment No. 4 on non-refoulement, CRC General Comment No. 14 on the child's
best interests, CCPR General Comment No. 35 on liberty, CEDAW General
Recommendation No. 35 on gender-based violence — the UN texts Swiss courts
quote most. Every committee with a reporting procedure: CCPR, CESCR, CAT,
CERD, CEDAW, CRC, CRPD, CED, CMW and the Subcommittee on Prevention of
Torture; ~214 documents (2026-10-25), in French and English.

Why OHCHR's treaty-body database and not the UN Digital Library: only the
former lists every comment as its own document. Before ~2008 the comments
have no individual Library record (they were published inside annual
reports and the HRI/GEN/1 compilation), while OHCHR lists them under
internal symbols (INT/CCPR/GEC/6621) with a file per language. The database
disallows robots; it is crawled on Jonas's decision of 2026-10-24, under
the terms of the permission request sent to the UN (5 s per request,
identifying user agent) — see scrapers/untb.py.

Listing. One TBSearch page per treaty, a Telerik grid of 10 rows paged by
ASP.NET postback (the PageNext button and the page's hidden form fields).
Each row: title, symbol, date, and a Download.aspx link that resolves to a
language x format table (doc/docx/pdf/html on docstore.ohchr.org; the old
INT/ documents only as .doc behind DownloadDraft.aspx).

Text. Per language HTML, then DOCX, then the PDF text layer, then legacy
.doc through LibreOffice (`soffice --headless --convert-to txt`, present on
the production host). A rendition that reads as the other language is
dropped (OHCHR files some English texts under French).

One row per (document, language), doc_number = the UN symbol.
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Iterator
from urllib.parse import quote, unquote, urljoin

from bs4 import BeautifulSoup

from scrapers.untb import clean_text, docx_text, html_text, in_other_language, pdf_text

from .base import PracticeScraper, first_date_iso, slugify

logger = logging.getLogger(__name__)

BASE = "https://tbinternet.ohchr.org"
LISTING_URL = BASE + "/_layouts/15/treatybodyexternal/TBSearch.aspx?Lang=en&TreatyID={tid}&DocTypeID=11"
DOWNLOAD_URL = BASE + "/_layouts/15/treatybodyexternal/Download.aspx?symbolno={symbol}&Lang={lang}"

# OHCHR TreatyID -> issuing authority (as un_uhri spells them).
TREATIES = {
    8: "UN-CCPR", 9: "UN-CESCR", 1: "UN-CAT", 6: "UN-CERD", 3: "UN-CEDAW",
    5: "UN-CRC", 4: "UN-CRPD", 2: "UN-CED", 7: "UN-CMW", 12: "UN-SPT",
}
LANGUAGES = ("fr", "en")
_LANG_ROW = {"Français": "fr", "English": "en"}
_FORMATS = ("doc", "docx", "pdf", "html")          # column order of the download table
_PREFERENCE = ("html", "docx", "pdf", "doc")
MIN_TEXT_CHARS = 1500
_COUNT = re.compile(r"(\d+)\s*items in\s*(\d+)\s*pages")


def parse_listing(page_html: str) -> tuple[list[dict], int]:
    """Rows of one listing page and the total number of pages."""
    soup = BeautifulSoup(page_html, "html.parser")
    rows = []
    for tr in soup.select("tr.rgRow, tr.rgAltRow"):
        td = tr.find_all("td")
        if len(td) < 6:
            continue
        # The old comments (INT/CCPR/GEC/6621) show no symbol in the grid;
        # their Download.aspx link carries it.
        link = tr.find("a", href=re.compile(r"Download\.aspx\?symbolno="))
        linked = unquote(re.search(r"symbolno=([^&]+)", link["href"]).group(1)) if link else ""
        symbol = td[4].get_text(" ", strip=True) or linked
        if not symbol:
            continue
        rows.append({
            "title": re.sub(r"\s+", " ", td[0].get_text(" ", strip=True)),
            "symbol": symbol,
            "date": _iso(td[5].get_text(" ", strip=True)),
        })
    m = _COUNT.search(re.sub(r"<[^>]+>", " ", page_html))
    return rows, int(m.group(2)) if m else 1


def _iso(value: str) -> str:
    try:
        return datetime.strptime(value.strip(), "%d %b %Y").date().isoformat()
    except ValueError:
        return ""


def _session_year(text: str) -> str:
    m = re.search(r"(?i)session[^)\n]{0,40}?,?\s*((?:19|20)\d{2})\)", text)
    return m.group(1) if m else ""


_REPORT_SYMBOL = re.compile(r"^A/\d+/\d+")
_ANNEX = re.compile(r"(?m)^\s*ANNEXE?\s+([IVXL]+)\b", re.I)


def cut_annex(title: str, text: str) -> str:
    """CAT General Comment No. 1 is listed under its annual report (A/53/44,
    'Annex IX: General Comment No. 01 …'): keep that annex, not the report.
    "" when the annex cannot be found — better no row than the whole report."""
    m = re.search(r"Annex\s+([IVXL]+)", title, re.I)
    if not m:
        return ""
    heads = [h for h in _ANNEX.finditer(text)]
    for i, h in enumerate(heads):
        if h.group(1).upper() == m.group(1).upper():
            end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
            part = text[h.start():end].strip()
            if MIN_TEXT_CHARS <= len(part) < 100_000:
                return part
    return ""


_EN_MONTHS = {m: i for i, m in enumerate((
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december"), 1)}
_EN_DATE = re.compile(r"\b(\d{1,2})\s+([A-Z][a-z]+)\s+((?:19|20)\d{2})\b")


def header_date(text: str) -> str:
    """First date in the header: French/German/Italian via first_date_iso,
    English ('13 December 2012') here."""
    head = text[:1500]
    iso = first_date_iso(head)
    m = _EN_DATE.search(head)
    if m and m.group(2).lower() in _EN_MONTHS and (not iso or m.start() < head.find(iso[:4])):
        return f"{m.group(3)}-{_EN_MONTHS[m.group(2).lower()]:02d}-{int(m.group(1)):02d}"
    return iso


def document_date(row: dict, text: str) -> str:
    """The grid's date; for the old INT/ entries (and the report-listed CAT
    GC 1) the session year; for the rest the issue date in the header."""
    if row["date"]:
        return row["date"]
    old = row["symbol"].startswith("INT/") or bool(_REPORT_SYMBOL.match(row["symbol"]))
    year = _session_year(row["title"] + "\n" + text[:3000])
    if old and year:
        return year
    return header_date(text) or year


def postback_form(page_html: str) -> dict | None:
    """Form fields that move the grid to its next page, or None on the last."""
    soup = BeautifulSoup(page_html, "html.parser")
    nxt = soup.find("input", attrs={"class": re.compile(r"\brgPageNext\b|PageNext")})
    if nxt is None or nxt.get("onclick", "").strip().startswith("return false"):
        return None
    form = {i["name"]: i.get("value", "") for i in soup.select("input[type=hidden]") if i.get("name")}
    form[nxt["name"]] = nxt.get("value", " ")
    return form


def parse_download_page(page_html: str) -> dict[str, dict[str, str]]:
    """language -> {format: absolute file url} from a Download.aspx page."""
    soup = BeautifulSoup(page_html, "html.parser")
    out: dict[str, dict[str, str]] = {}
    for tr in soup.find_all("tr"):
        cells = tr.find_all("td")
        lang = _LANG_ROW.get(cells[0].get_text(strip=True)) if cells else None
        if not lang:
            continue
        for fmt, cell in zip(_FORMATS, cells[1:]):
            a = cell.find("a", href=True)
            if a and a["href"] != "#":
                out.setdefault(lang, {})[fmt] = urljoin(BASE, a["href"])
    return out


def doc_text(data: bytes) -> str:
    """Legacy Word 97 .doc -> text via LibreOffice; "" where it is missing."""
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        logger.info("[un_gc] LibreOffice not installed — .doc rendition skipped")
        return ""
    with tempfile.TemporaryDirectory(prefix="ocl_un_gc_") as tmp:
        src = Path(tmp) / "in.doc"
        src.write_bytes(data)
        try:
            subprocess.run([soffice, "--headless", "--convert-to", "txt:Text (encoded):UTF8",
                            "--outdir", tmp, str(src)], capture_output=True, timeout=120, check=True)
            return (Path(tmp) / "in.txt").read_text(encoding="utf-8", errors="replace")
        except (subprocess.SubprocessError, OSError) as e:
            logger.warning(f"[un_gc] .doc conversion failed: {e}")
            return ""


_EXTRACT = {"html": html_text, "docx": docx_text, "pdf": pdf_text, "doc": doc_text}


def topics_for(title: str, authority: str) -> list[str]:
    kind = "General recommendation" if re.search(r"(?i)recommendation", title) else "General comment"
    topics = [authority.replace("UN-", ""), kind]
    n = re.search(r"(?i)No\.?\s*(\d+)", title)
    if n:
        topics.append(f"{kind} No. {n.group(1)}")
    if re.search(r"(?i)\bjoint\b", title):
        topics.append("Joint")
    return topics


class UnGeneralCommentsScraper(PracticeScraper):
    SOURCE_KEY = "un_gc"
    ISSUING_AUTHORITY = "UN"          # per row: UN-CCPR, UN-CAT, …
    DEFAULT_DOC_TYPE = "general_comment"
    REQUEST_DELAY = 5.0               # the rate promised to the UN on 2026-10-23
    PDF_TIMEOUT = 90

    def __init__(self):
        super().__init__()
        self.session.headers["User-Agent"] = "OpenCaseLawBot/1.0 (+https://opencaselaw.ch; team@jonashertner.com)"

    def _make_doc_id(self, stub: dict) -> str:
        return f"{self.SOURCE_KEY}_{slugify(stub['doc_number'])}_{stub['language']}"

    def _normalize(self, stub: dict, body_text: str) -> dict:
        doc = super()._normalize(stub, body_text)
        doc["issuing_authority"] = stub["issuing_authority"]
        return doc

    def _listing(self, tid: int) -> Iterator[dict]:
        url = LISTING_URL.format(tid=tid)
        r = self.get(url)
        r.raise_for_status()
        page, pages, seen = r.text, None, 0
        while True:
            rows, total_pages = parse_listing(page)
            pages = pages or total_pages
            yield from rows
            seen += 1
            form = postback_form(page) if seen < pages else None
            if form is None:
                return
            self._rate_limit()
            r = self.session.post(url, data=form, timeout=self.PDF_TIMEOUT)
            r.raise_for_status()
            page = r.text

    def _text(self, files: dict[str, str], lang: str) -> str:
        for fmt in _PREFERENCE:
            href = files.get(fmt)
            if not href:
                continue
            try:
                resp = self.get(href)
                resp.raise_for_status()
            except Exception as e:  # noqa: BLE001 — one broken file is not a failed run
                logger.warning(f"[un_gc] {fmt} unavailable: {e}")
                continue
            text = clean_text(_EXTRACT[fmt](resp.content))
            if in_other_language(text, lang):
                return ""                      # mis-filed; the other formats hold the same text
            if len(text) >= MIN_TEXT_CHARS:
                return text
        return ""

    def discover_documents(self) -> Iterator[dict]:
        seen_symbols: set[str] = set()
        rows_total = 0
        for tid, authority in TREATIES.items():
            for row in self._listing(tid):
                rows_total += 1
                if row["symbol"] in seen_symbols:       # joint comments are listed twice
                    continue
                seen_symbols.add(row["symbol"])
                pending = [lang for lang in LANGUAGES
                           if self._make_doc_id({"doc_number": row["symbol"], "language": lang})
                           not in self._seen_ids]
                if not pending:
                    continue
                files = parse_download_page(self.get(DOWNLOAD_URL.format(
                    symbol=quote(row["symbol"], safe=""), lang="en")).text)
                for lang in pending:
                    text = self._text(files.get(lang, {}), lang)
                    if text and _REPORT_SYMBOL.match(row["symbol"]):
                        text = cut_annex(row["title"], text)
                    if not text:
                        logger.info(f"[un_gc] {row['symbol']} ({lang}): no usable text")
                        continue
                    url = DOWNLOAD_URL.format(symbol=quote(row["symbol"], safe=""), lang=lang)
                    date = document_date(row, text)
                    yield {
                        "doc_number": row["symbol"],
                        "title": row["title"],
                        "date": date,
                        "language": lang,
                        "issuing_authority": authority,
                        "doc_type": "general_recommendation" if re.search(
                            r"(?i)recommendation", row["title"]) else "general_comment",
                        "url": url,
                        "pdf_url": url,
                        "topics": topics_for(row["title"], authority),
                        "body_text": text,
                    }
        if rows_total == 0:
            raise RuntimeError("[un_gc] no General Comments listed — OHCHR listing changed?")
        logger.info(f"[un_gc] {len(seen_symbols)} documents listed")
