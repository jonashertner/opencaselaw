"""
EHRA — Eidgenössisches Amt für das Handelsregister: Praxismitteilungen, Weisungen
=================================================================================

The federal commercial-register office (in the Federal Office of Justice)
supervises the cantonal commercial registers. Two of its series are the
practice of the register:

  * Praxismitteilungen EHRA (Communications OFRC, Comunicazioni UFRC),
    1/08 (17.10.2008) to today, three or four a year: how the register
    applies the HRegV and the OR after each revision (Aktienrecht 2023,
    capital band, foreign-currency capital, virtual general meetings, ...).
  * Weisungen und Mitteilungen des EHRA to the cantonal registers: the
    Firmenrecht-Weisung (company names), the internal Weisung on name
    identity, the FusG model entries, the data-structure and format
    Weisungen, the 2001 Mitteilung on Sacheinlage/Sachübernahme, the 1998
    Lex Koller Richtlinien, the 2007 Weisung on public financial controls.

Pages (admin.ch sd-web, parsed by sdweb.download_items):

  de /de/praxismitteilungen-ehra     fr /fr/communications-ofrc   it /it/comunicazione-ufrc
  de /de/rechtliche-grundlagen-ehra  fr /fr/bases-legales-ofrc    it /it/basi-legali-ufrc

Each language has its own file (/dam/{lang}/sd-web/<hash>/...). The
"2008 - 2026 (alle)" compilation is skipped (it is the sum of the single
issues), as is the English XML data model ("BlueBook").

doc_number: "EHRA-Praxismitteilung 3/2026" for the series; a Weisung gets
its German title without the date, looked up by the DAM hash shared across
languages, so the three language rows of one Weisung share a number.
"""
from __future__ import annotations

import logging
import re
from typing import Iterator
from urllib.parse import urlparse

from .base import PracticeScraper, first_date_iso, slugify
from .langdedup import TextDedupMixin
from .sdweb import download_items

logger = logging.getLogger(__name__)

_BASE = "https://www.bj.admin.ch"
PRAXIS_PAGES = (("de", "/de/praxismitteilungen-ehra"), ("fr", "/fr/communications-ofrc"),
                ("it", "/it/comunicazione-ufrc"))
WEISUNG_PAGES = (("de", "/de/rechtliche-grundlagen-ehra"), ("fr", "/fr/bases-legales-ofrc"),
                 ("it", "/it/basi-legali-ufrc"))

# "Praxismitteilung EHRA 3/26", "Communication OFRC 3/26", "Comunicazione UFRC 3/26"
_PM_NUMBER = re.compile(r"(?:EHRA|OFRC|UFRC)\s+(\d{1,2})\s*/\s*(\d{2,4})\b")
_COMPILATION = re.compile(r"\((?:alle|toutes|tutte)\)|ab-2008", re.I)
_SKIP = re.compile(r"BlueBook|xml-data-model", re.I)
# "… vom 1. Mai 2010", "… du 1er mai 2010", "… del 1° maggio 2010", "…, March 2010"
_DATE_TAIL = re.compile(r"\s*(?:,|vom|du|del|dell[’'])\s+\d.*$|\s*,?\s+(?:vom|du|del)\s*$", re.I)


def _dam_key(url: str) -> str:
    """The language-independent part of an sd-web URL: <hash>/<file>."""
    parts = urlparse(url).path.split("/")
    try:
        i = parts.index("sd-web")
        return "/".join(parts[i + 1:])
    except ValueError:
        return urlparse(url).path


def _year4(yy: str) -> str:
    return yy if len(yy) == 4 else f"20{yy}"


_BEILAGE = re.compile(r"Beilage|annexe|allegato|-beilage\.pdf", re.I)


def parse_praxis(html: str, lang: str, page_url: str) -> list[dict]:
    """One stub per Praxismitteilung and per Beilage (1/14 and 3/14 carry
    one: a sanctions note and a Federal Administrative Court judgment). A
    Beilage has its own number and id and takes its Praxismitteilung's date;
    its description is the line the page prints under it."""
    items = [i for i in download_items(html, _BASE)
             if not (_COMPILATION.search(i["title"]) or _COMPILATION.search(i["pdf_url"]))]
    dates: dict[str, str] = {}
    for item in items:
        m = _PM_NUMBER.search(item["title"])
        if m and not _BEILAGE.search(item["title"] + " " + item["pdf_url"]):
            dates[f"{int(m.group(1))}/{_year4(m.group(2))}"] = first_date_iso(item["title"]) or item["file_date"]
    stubs = []
    for item in items:
        title = item["title"]
        m = _PM_NUMBER.search(title)
        if not m:
            logger.warning("[ehra] unnumbered Praxismitteilung skipped: %s", title)
            continue
        nr, year = int(m.group(1)), _year4(m.group(2))
        number = f"EHRA-Praxismitteilung {nr}/{year}"
        if _BEILAGE.search(title + " " + item["pdf_url"]):
            desc = item["meta"] if item["meta"] and not item["meta"].upper().startswith("PDF") else ""
            stubs.append({
                "pdf_url": item["pdf_url"], "url": page_url,
                "title": f"{title}: {desc}" if desc else title,
                "doc_number": f"{number} Beilage",
                "date": dates.get(f"{nr}/{year}", ""),
                "language": lang, "doc_type": "weisung_anhang",
                "topics": [number, "Beilage", "Praxismitteilung", "Handelsregister"],
                "ehra_id": f"pm_{year}_{nr:02d}_beilage_{lang}",
            })
            continue
        stubs.append({
            "pdf_url": item["pdf_url"],
            "url": page_url,
            "title": title,
            "doc_number": number,
            "date": first_date_iso(title) or item["file_date"],
            "language": lang,
            "doc_type": "mitteilung",
            "topics": [number, "Praxismitteilung", "Handelsregister"],
            "ehra_id": f"pm_{year}_{nr:02d}_{lang}",
        })
    return stubs


def _weisung_type(german_title: str) -> str:
    if re.match(r"Mitteilung\b", german_title):
        return "mitteilung"
    if re.match(r"Richtlinien?\b", german_title):
        return "richtlinie"
    if re.search(r"Mustertext", german_title):
        return "formular"
    return "weisung"


def weisung_numbers(german_html: str) -> dict[str, tuple[str, str, str]]:
    """DAM key -> (doc_number, doc_type), both from the German title (the
    German title without its date is the number; FR "Directive" would
    otherwise read as a Richtlinie)."""
    out = {}
    for item in download_items(german_html, _BASE):
        name = _DATE_TAIL.sub("", item["title"]).strip(" ,")
        out[_dam_key(item["pdf_url"])] = (f"EHRA {name}", _weisung_type(item["title"]), item["title"])
    return out


def parse_weisungen(html: str, lang: str, page_url: str, numbers: dict[str, tuple[str, str, str]]) -> list[dict]:
    stubs = []
    for item in download_items(html, _BASE):
        title = item["title"]
        if _SKIP.search(title) or _SKIP.search(item["pdf_url"]):
            continue
        key = _dam_key(item["pdf_url"])
        if key not in numbers:
            logger.warning("[ehra] %s file not on the German page, skipped: %s", lang, title)
            continue
        number, doc_type, german_title = numbers[key]
        if lang != "de" and title == german_title:
            continue      # the German file listed on the FR/IT page (no translation)
        stubs.append({
            "pdf_url": item["pdf_url"],
            "url": page_url,
            "title": title,
            "doc_number": number,
            "date": first_date_iso(title) or item["file_date"],
            "language": lang,
            "doc_type": doc_type,
            "topics": [number, item["section"], "Handelsregister"],
            "ehra_id": f"w_{slugify(key)[:60]}_{lang}",
        })
    return stubs


class EhraScraper(TextDedupMixin, PracticeScraper):
    SOURCE_KEY = "ehra"
    ISSUING_AUTHORITY = "EHRA"
    DEFAULT_DOC_TYPE = "mitteilung"
    REVISION_FIELD = "pdf_url"      # DAM-hashed: a re-upload changes the URL
    REQUEST_DELAY = 1.0
    OCR_SCANS = True
    NO_TEXT_LAYER_BODY = "[Textlayer fehlt: gescanntes PDF]"

    def _make_doc_id(self, stub: dict) -> str:
        return f"{self.SOURCE_KEY}_{stub['ehra_id']}"

    def _page(self, path: str) -> str | None:
        try:
            r = self.get(_BASE + path)
            r.raise_for_status()
            return r.text
        except Exception as e:
            logger.warning("[%s] %s fetch failed: %s", self.SOURCE_KEY, path, e)
            return None

    def discover_documents(self) -> Iterator[dict]:
        for lang, path in PRAXIS_PAGES:
            html = self._page(path)
            if html is None:
                continue
            stubs = parse_praxis(html, lang, _BASE + path)
            logger.info("[%s] %s Praxismitteilungen: %d", self.SOURCE_KEY, lang, len(stubs))
            for stub in stubs:
                out = self.with_body(stub, stub["doc_number"])
                if out:
                    yield out
        numbers: dict[str, tuple[str, str, str]] = {}
        for lang, path in WEISUNG_PAGES:
            html = self._page(path)
            if html is None:
                continue
            if lang == "de":
                numbers = weisung_numbers(html)
            stubs = parse_weisungen(html, lang, _BASE + path, numbers)
            logger.info("[%s] %s Weisungen: %d", self.SOURCE_KEY, lang, len(stubs))
            for stub in stubs:
                out = self.with_body(stub, stub["doc_number"])
                if out:
                    yield out
