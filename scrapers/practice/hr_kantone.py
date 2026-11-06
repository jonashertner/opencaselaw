"""
Cantonal commercial registers — practice notes, directives, Merkblätter
=======================================================================

Most cantonal Handelsregisterämter publish only forms and copies of the
federal EHRA texts (those are in source 'ehra'). Three publish their own
practice:

  GE  Registre du commerce: the yearly "Note aux notaires" (2016-), in which
      the register tells the Geneva notaries what it will and will not
      enter — the closest cantonal equivalent of the EHRA Praxismitteilungen.
  VD  Registre du commerce: the note on the Vaud practice for LFAIE (Lex
      Koller) declarations. Everything else on the page is federal: the
      "Dir_171c_ORC" and "Dir_171d_ORC" files are the EHRA's 1998 Lex Koller
      Richtlinien and its 2001 Mitteilung on Sacheinlage/Sachübernahme on the
      OFRC letterhead (checked 2026-11-06 by OCR against source 'ehra'), and
      the OFRC communications and model entries are copies too.
  BE  Handelsregisteramt: the Merkblätter for each legal form (Neueintragung,
      Gründungsbericht, Verrechnungsliberierung, opting out, Sitzverlegung,
      Auflösung und Löschung, formelle Anforderungen), DE and FR. Forms and
      invoice samples are left out.

issuing_authority is the canton code, so search_practice(canton=GE) also
finds them.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Iterator
from urllib.parse import unquote, urljoin

from bs4 import BeautifulSoup

from .base import PracticeScraper, first_date_iso, slugify
from .langdedup import TextDedupMixin

logger = logging.getLogger(__name__)

_BE = "https://www.hra.dij.be.ch"
_BE_FORMS = ("aktiengesellschaft", "gmbh", "genossenschaft", "verein-stiftung", "einzelunternehmen",
             "kollektivgesellschaft-kommanditgesellschaft", "besondererechtsformen-zweigniederlassungen")


@dataclass(frozen=True)
class Page:
    canton: str
    url: str
    lang: str
    issuer: str
    href: str = r"\.pdf"          # regex the (unquoted) href must match
    text: str = ""                # regex the anchor text must match
    exclude: str = ""             # regex on anchor text + href


PAGES = (
    Page("GE", "https://www.ge.ch/document/notes-aux-notaires-editees-registre-du-commerce", "fr",
         "Registre du commerce de Genève", href=r"/annexe/file/\d+", text=r"Note aux notaires"),
    Page("VD", "https://www.vd.ch/economie/registre-du-commerce/modeles-et-communications", "fr",
         "Registre du commerce du canton de Vaud", href=r"pratique_RCVD\.pdf"),
    *(Page("BE", f"{_BE}/{lang}/start/rechtsformen/{form}.html", lang, "Handelsregisteramt des Kantons Bern",
           href=r"/merkbl(?:ä|%C3%A4|a)tter/|Merkblatt", exclude=r"HRA_Rechnung|Original Rechnung|Réquisition d.inscription-type|Formular")
      for form in _BE_FORMS for lang in ("de", "fr")),
)


def _clean(s: str) -> str:
    return " ".join((s or "").split())


def parse_page(html: str, page: Page) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    href_rx = re.compile(page.href, re.I)
    text_rx = re.compile(page.text, re.I) if page.text else None
    exc_rx = re.compile(page.exclude, re.I) if page.exclude else None
    out, seen = [], set()
    for a in soup.find_all("a", href=True):
        href = unquote(a["href"])
        if not href_rx.search(href):
            continue
        title = _clean(a.get_text(" ", strip=True))
        if text_rx and not text_rx.search(title):
            continue
        if exc_rx and exc_rx.search(f"{title} {href}"):
            continue
        url = urljoin(page.url, a["href"])
        if url in seen or not title:
            continue
        seen.add(url)
        out.append({"title": title, "url": url})
    return out


def _doc_type(title: str, canton: str) -> str:
    if canton == "GE":
        return "mitteilung"
    if re.search(r"^Dir|Directive|Acquisition d.immeubles|Apports en nature", title, re.I):
        return "weisung"
    return "merkblatt"


class HrKantoneScraper(TextDedupMixin, PracticeScraper):
    SOURCE_KEY = "hr_kantone"
    ISSUING_AUTHORITY = "GE"            # overridden per row
    DEFAULT_DOC_TYPE = "merkblatt"
    REVISION_FIELD = "pdf_url"
    REQUEST_DELAY = 1.5
    OCR_SCANS = True
    NO_TEXT_LAYER_BODY = "[Textlayer fehlt: gescanntes PDF]"

    def _make_doc_id(self, stub: dict) -> str:
        return f"{self.SOURCE_KEY}_{stub['hr_id']}"

    def _normalize(self, stub: dict, body_text: str) -> dict:
        doc = super()._normalize(stub, body_text)
        doc["issuing_authority"] = stub["canton"]
        if not doc["date"]:
            doc["date"] = first_date_iso(body_text[:2500])
        return doc

    def discover_documents(self) -> Iterator[dict]:
        taken: set[str] = set()          # BE lists the same Merkblatt on several pages
        for page in PAGES:
            try:
                r = self.get(page.url)
                r.raise_for_status()
            except Exception as e:
                logger.warning("[%s] %s fetch failed: %s", self.SOURCE_KEY, page.url, e)
                continue
            items = parse_page(r.text, page)
            logger.info("[%s] %s: %d documents", self.SOURCE_KEY, page.url, len(items))
            for it in items:
                if it["url"] in taken:
                    continue
                taken.add(it["url"])
                title = it["title"]
                key = (re.search(r"/annexe/file/(\d+)", it["url"]).group(1) if page.canton == "GE"
                       else slugify(unquote(it["url"].rsplit("/", 1)[-1]).rsplit(".", 1)[0])[:60])
                ym = re.search(r"\b(20\d{2})\b", title)
                stub = {
                    "pdf_url": it["url"],
                    "url": page.url,
                    "title": title,
                    "doc_number": f"{page.canton} HR {title}",
                    "date": first_date_iso(title) or (ym.group(1) if ym else ""),
                    "language": page.lang,
                    "doc_type": _doc_type(title, page.canton),
                    "topics": [page.canton, page.issuer, "Handelsregister"],
                    "canton": page.canton,
                    "hr_id": f"{page.canton.lower()}_{key}_{page.lang}",
                }
                out = self.with_body(stub, f"{page.canton} {key}")
                if out:
                    yield out
