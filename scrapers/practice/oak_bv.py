"""
OAK BV — Oberaufsichtskommission Berufliche Vorsorge: Weisungen, Mitteilungen, Anhörungen
=========================================================================================

  de  /de/weisungen   /de/mitteilungen   /de/anhoerungen      (hub: /de/regulierung)
  fr  /fr/directives  /fr/communications /fr/auditions
  it  /it/direttive   /it/comunicazioni  /it/audizioni

The Oberaufsichtskommission issues Weisungen under Art. 64a Abs. 1 lit. a BVG
that bind the cantonal supervisory authorities, the Experten für berufliche
Vorsorge and the Revisionsstellen (W – 01/2012 Zulassung von Experten,
W – 02/2013 Ausweis der Vermögensverwaltungskosten, W – 01/2021 Transparenz
und interne Kontrolle …), and Mitteilungen (M – nn/yyyy) that state its
supervisory position without binding force. Both are the practice behind
BVG/BVV 2 questions and are cited as such; nothing of it was in the corpus
before 2026-09-07.

Site layout (admin.ch sd-web, same platform as weko.admin.ch):

  * each listing page shows the documents IN FORCE as cards linking to one
    subpage per document (/de/weisungen-w-01-2026), and lists the REPEALED
    ones ("Archiv / Aufgehobene Weisungen") as direct downloads;
  * a subpage carries the title, an "In Kürze" summary and the download list
    "Gültige Weisungen (inkl. Zusatzdokumente)": the versions of the Weisung
    ("W – 01/2014 | Erstversion | 20.02.2014", "… | Überarbeitete Version |
    23.03.2017") plus its Zusatzdokumente (Informationsschreiben, FAQ,
    Formular, Liste);
  * the Anhörungen page lists consultation drafts ("Weisungsentwurf «…»")
    with the accompanying information note, open ones first, closed ones
    under "Archiv / Abgeschlossene Anhörungen".

Rows and ids. One row per (document, version, language), FINMA/BSV style:

  oak_bv_w_01_2014_20170323_de              a version of a Weisung/Mitteilung
  oak_bv_w_01_2021_informationsschreiben…_<hash>_<date>_de   a Zusatzdokument (doc_type weisung_anhang)
  oak_bv_entwurf_<title>_<hash>_<date>_de                    a consultation draft (doc_type entwurf)

doc_number is the German form for every language ("W – 01/2014", "M – 01/2025";
FR "D –", "C –" and the IT mix are normalised), so search_practice's per-
doc_number collapse shows the newest version of a Weisung in each language
and include_superseded lists the history. A repealed Weisung has no newer
version and therefore stays visible; its topics say "aufgehoben oder ersetzt"
(FINMA wording) and its url is the listing page, not a subpage. Drafts are
never in force: doc_type "entwurf" keeps them out of a filtered search and
the title keeps the authority's own "Weisungsentwurf" wording.

Language: the page's. A title saying "(disponibile in francese)" marks a
file the IT page borrows from the FR one; that row is dropped here and comes
in from the FR page (the IT site is a thin mirror: nothing is Italian-only).

Volume: ~35 documents in force, ~10 repealed, ~12 drafts, per language, with
versions and annexes ~120 rows per language. PDF URLs are DAM-hashed ->
REVISION_FIELD="pdf_url".
"""
from __future__ import annotations

import logging
import re
from typing import Iterator
from urllib.parse import urljoin, urlparse, unquote

from bs4 import BeautifulSoup

from .base import PracticeScraper, first_date_iso, sha256_hex, slugify
from .finma_rundschreiben import _STATUS_IN_FORCE, _STATUS_SUPERSEDED
from .sdweb import download_items

logger = logging.getLogger(__name__)

_BASE = "https://www.oak-bv.admin.ch"
LISTINGS = {
    "de": (("/de/weisungen", "weisung"), ("/de/mitteilungen", "mitteilung"), ("/de/anhoerungen", "entwurf")),
    "fr": (("/fr/directives", "weisung"), ("/fr/communications", "mitteilung"), ("/fr/auditions", "entwurf")),
    "it": (("/it/direttive", "weisung"), ("/it/comunicazioni", "mitteilung"), ("/it/audizioni", "entwurf")),
}
_SUBPAGE = re.compile(
    r"^/(de|fr|it)/(?:weisungen|mitteilungen|directives|communications|direttive|comunicazioni)-([a-z])-(\d{2})-(\d{4})$"
)
# "W – 01/2021 | Erstversion | 26.01.2021", "D – 01/2014 | Version révisée | 23.03.2017"
_VERSION = re.compile(
    r"^\s*([WMDC])\s*[–\-]\s*(\d{2})\s*/\s*(\d{4})\s*\|\s*([^|]+?)\s*\|\s*(\d{1,2}\.\d{1,2}\.\d{4})",
)
_NUMBER_ANY = re.compile(r"\b([WMDC])\s*[–\-]\s*(\d{2})\s*/\s*(\d{4})\b")
_NUMBER_FILE = re.compile(r"(?<![A-Za-z])([WMDC])-(\d{2})_(\d{4})(?!\d)")   # "…_W-01_2014_OAK BV_…pdf"
_FILE_DATE = re.compile(r"(?<!\d)(20\d{2})(\d{2})(\d{2})_")           # "20231205_Entwurf_…pdf"
_LANG_NOTE = re.compile(
    r"\(\s*(?:disponibile(?:\s+solo)?\s+in\s+(?:francese|tedesco|italiano)|"
    r"disponible(?:\s+uniquement)?\s+en\s+(?:allemand|français|italien)|"
    r"nur\s+auf\s+(?:Deutsch|Französisch|Italienisch))\s*\)",
    re.IGNORECASE,
)
_ARCHIVE_SECTION = re.compile(
    r"^(?:Archiv|Archive|Archivio|Aufgehobene|Directives\s+abrogées|Communications\s+abrogées|"
    r"Direttive\s+abrogate|Comunicazioni\s+abrogate|Abgeschlossene|Auditions\s+achevées|Audizioni\s+concluse)",
    re.IGNORECASE,
)
_LETTER = {"w": "W", "d": "W", "m": "M", "c": "M"}
_LANG_FROM_NOTE = (
    (re.compile(r"tedesc|allemand|deutsch", re.I), "de"),
    (re.compile(r"frances|français|französisch", re.I), "fr"),
    (re.compile(r"italian|italien", re.I), "it"),
)


def _number(letter: str, nn: str, yyyy: str) -> str:
    return f"{_LETTER[letter.lower()]} – {nn}/{yyyy}"


def _iso_dmy(text: str) -> str:
    return first_date_iso(text or "")


def _language(page_lang: str, title: str) -> str | None:
    """The page's language, or None when the title says the file is in
    another language ("(disponibile in francese)"): that language's own page
    lists the same file, and the IT site is a thin mirror of the FR one, so
    such a row would only duplicate the FR row under an Italian title."""
    m = _LANG_NOTE.search(title)
    if not m:
        return page_lang
    for rx, lang in _LANG_FROM_NOTE:
        if rx.search(m.group(0)):
            return page_lang if lang == page_lang else None
    return page_lang


def _number_from(title: str, pdf_url: str) -> str:
    m = _NUMBER_ANY.search(title)
    if m:
        return _number(*m.groups())
    m = _NUMBER_FILE.search(unquote(urlparse(pdf_url).path))
    if m:
        return _number(*m.groups())
    return ""


def parse_subpage_links(html: str, lang: str) -> list[str]:
    """Absolute URLs of the per-document subpages a listing page links to."""
    soup = BeautifulSoup(html, "html.parser")
    out: list[str] = []
    for a in soup.find_all("a", href=True):
        m = _SUBPAGE.match(a["href"])
        if m and m.group(1) == lang and urljoin(_BASE, a["href"]) not in out:
            out.append(urljoin(_BASE, a["href"]))
    return out


def parse_listing_downloads(html: str, lang: str, kind: str, page_url: str) -> list[dict]:
    """Direct downloads on a listing page: repealed documents (weisung /
    mitteilung) or consultation drafts (entwurf)."""
    stubs: list[dict] = []
    for item in download_items(html, _BASE):
        if kind == "entwurf":
            stub = _draft_stub(item, lang, page_url)
        else:
            stub = _document_stub(item, lang, kind, page_url, parent_number="", superseded=True)
        if stub:
            stubs.append(stub)
    return stubs


def parse_subpage(html: str, lang: str, kind: str, page_url: str) -> list[dict]:
    """Versions and Zusatzdokumente of one document in force."""
    soup = BeautifulSoup(html, "html.parser")
    h1 = soup.find("h1")
    subject = " ".join(h1.get_text(" ", strip=True).split()) if h1 else ""
    m = _SUBPAGE.match(urlparse(page_url).path)
    parent = _number(m.group(2), m.group(3), m.group(4)) if m else ""
    stubs: list[dict] = []
    for item in download_items(html, _BASE):
        s = _document_stub(item, lang, kind, page_url, parent_number=parent, superseded=False, subject=subject)
        if s:
            stubs.append(s)
    return stubs


def _document_stub(item: dict, lang: str, kind: str, page_url: str, *, parent_number: str,
                   superseded: bool, subject: str = "") -> dict | None:
    title = item["title"]
    language = _language(lang, title)
    if language is None:
        logger.debug("[oak_bv] %s row skipped, file is in another language: %s", lang, title)
        return None
    status = _STATUS_SUPERSEDED if superseded else _STATUS_IN_FORCE
    vm = _VERSION.match(title)
    if vm:
        number = _number(vm.group(1), vm.group(2), vm.group(3))
        label = " ".join(vm.group(4).split())
        date = _iso_dmy(vm.group(5))
        full_title = f"{subject} | {title}" if subject and subject not in title else title
        return {
            "pdf_url": item["pdf_url"],
            "url": page_url,
            "title": full_title,
            "doc_number": number,
            "date": date,
            "language": language,
            "doc_type": kind,
            "topics": [t for t in (number, label, item["section"]) if t] + list(status),
            "oak_id": f"{slugify(number)}_{date.replace('-', '') or 'undated'}_{language}",
        }
    # Zusatzdokument: Informationsschreiben, FAQ, Formular, Liste, Lettre d'information …
    number = parent_number or _number_from(title, item["pdf_url"])
    date = _iso_dmy(title) or item["file_date"]
    full_title = title if (not number or number in title) else f"{title} | {number}"
    return {
        "pdf_url": item["pdf_url"],
        "url": page_url,
        "title": full_title,
        "doc_number": number,
        "date": date,
        "language": language,
        "doc_type": "weisung_anhang",
        "topics": [t for t in (number, "Zusatzdokument", item["section"]) if t] + list(status),
        # a title hash keeps three "Information betreffend die Anhörung zum
        # Weisungsentwurf «…»" of one day apart once the slug is cut
        "oak_id": f"{slugify(number) or 'ohne_nummer'}_{slugify(title)[:40]}_{sha256_hex(title)[:6]}_{date.replace('-', '') or 'undated'}_{language}",
    }


def _draft_stub(item: dict, lang: str, page_url: str) -> dict | None:
    title = item["title"]
    language = _language(lang, title)
    if language is None:
        logger.debug("[oak_bv] %s draft skipped, file is in another language: %s", lang, title)
        return None
    fm = _FILE_DATE.search(unquote(urlparse(item["pdf_url"]).path))
    date = f"{fm.group(1)}-{fm.group(2)}-{fm.group(3)}" if fm else (_iso_dmy(title) or item["file_date"])
    closed = bool(_ARCHIVE_SECTION.match(item["section"]))
    number = _number_from(title, "")
    topics = [t for t in (number, "Anhörung", "Entwurf", item["section"]) if t]
    topics.append("Anhörung abgeschlossen" if closed else "Anhörung laufend")
    return {
        "pdf_url": item["pdf_url"],
        "url": page_url,
        "title": title,
        "doc_number": number,
        "date": date,
        "language": language,
        "doc_type": "entwurf",
        "topics": topics,
        "oak_id": f"entwurf_{slugify(title)[:50]}_{sha256_hex(title)[:6]}_{date.replace('-', '') or 'undated'}_{language}",
    }


class OakBvScraper(PracticeScraper):
    SOURCE_KEY = "oak_bv"
    ISSUING_AUTHORITY = "OAK BV"
    DEFAULT_DOC_TYPE = "weisung"
    REVISION_FIELD = "pdf_url"
    REQUEST_DELAY = 1.0
    NO_TEXT_LAYER_BODY = "[Textlayer fehlt: gescanntes PDF]"

    def _make_doc_id(self, stub: dict) -> str:
        return f"{self.SOURCE_KEY}_{stub['oak_id']}"

    def _fetch(self, url: str) -> str | None:
        try:
            r = self.get(url)
            r.raise_for_status()
            return r.text
        except Exception as e:
            logger.warning("[%s] %s fetch failed: %s", self.SOURCE_KEY, url, e)
            return None

    def discover_documents(self) -> Iterator[dict]:
        for lang, pages in LISTINGS.items():
            for path, kind in pages:
                url = _BASE + path
                html = self._fetch(url)
                if html is None:
                    continue
                direct = parse_listing_downloads(html, lang, kind, url)
                subpages = [] if kind == "entwurf" else parse_subpage_links(html, lang)
                logger.info("[%s] %s %s: %d direct downloads, %d subpages",
                            self.SOURCE_KEY, lang, path, len(direct), len(subpages))
                yield from direct
                for sub_url in subpages:
                    sub_html = self._fetch(sub_url)
                    if sub_html is None:
                        continue
                    yield from parse_subpage(sub_html, lang, kind, sub_url)
