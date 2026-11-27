"""
SEM Weisungen und Kreisschreiben
================================

State Secretariat for Migration: Weisungen, Kreisschreiben and Rundschreiben
for AIG, FZA, Asyl, Integration, BüG (Bürgerrecht), DSG/ÖG and Visa.

Index page (DE), since the 2026 relaunch of sem.admin.ch:
  https://www.sem.admin.ch/de/weisungen-und-kreisschreiben

The old /sem/de/home/publiservice/weisungen-kreisschreiben.html tree is gone
(index 404; six section pages redirect, two 404), and the PDFs moved from
/dam/sem/de/data/rechtsgrundlagen/weisungen/... to /dam/de/sd-web/<id>/<file>.
The scraper kept reporting "+0 new" for weeks because its link filter only
knew the old path; discovery now refuses to pass when it finds too little.

Page structure:
  section page  /de/i-auslaenderbereich ... /de/weitere-weisungen-und-...
                download items (a.download-item) and, for I and III, one
                card per chapter ("Mehr über «04 - Aufenthalt mit ...»")
  chapter page  /de/4-aufenthalt-mit-erwerbstaetigkeit, /de/1-asylverfahren,
                ... the chapter's Weisungen, Rundschreiben and annexes
  /de/chronologie  the AIG amendment notices ("Weisungsänderung AIG")

A download item carries the title (h4.download-item__title), an optional
description ("(Stand 1. November 2026)") and meta spans (type, size, file
date). Forms published as .doc/.docx/.xlsx are not collected.
"""
from __future__ import annotations

import logging
import re
from typing import Iterator
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .base import PracticeScraper, first_date_iso

logger = logging.getLogger(__name__)

_BASE = "https://www.sem.admin.ch"
INDEX_URL = f"{_BASE}/de/weisungen-und-kreisschreiben"

# (path, label, default doc_type). The labels are the topics already stored.
SECTIONS = [
    ("/de/i-auslaenderbereich",          "AIG / Ausländerbereich",       "weisung"),
    ("/de/ii-freizuegigkeitsabkommen",   "FZA / Freizügigkeitsabkommen", "rundschreiben"),
    ("/de/iii-asylgesetz",               "Asylgesetz",                   "weisung"),
    ("/de/iv-integration",               "Integration",                  "weisung"),
    ("/de/v-buergerrecht",               "Bürgerrecht",                  "weisung"),
    ("/de/vi-datenschutz-und-offentlichkeitsgesetz", "DSG / ÖG",         "weisung"),
    ("/de/vii-visa",                     "Visa",                         "weisung"),
    ("/de/weitere-weisungen-und-rundschreiben-des-sem", "Weitere",       "weisung"),
]
# Pages linked from a chapter page as plain links rather than cards.
EXTRA_PAGES = [
    ("/de/chronologie", "AIG / Ausländerbereich", "weisung", "Chronologie der Weisungsänderungen"),
]

# The site published 185 PDFs on 2026-11-27. Far fewer means the structure
# changed again; a run that finds that little must fail, not report "+0 new".
MIN_DOCUMENTS = 120

_CARD_LABEL = re.compile(r"^Mehr über «(.+)»$")

# Russian and Ukrainian copies of a form whose German version is collected
# (Schutzstatus S, Anhang III Verzichtserklärung): no language label in the
# corpus fits them, and their text adds nothing searchable in DE/FR/IT.
_OTHER_LANGUAGE_FORM = re.compile(r"-(?:rus|ukr)$")

# SEM renamed its files from "20020708-rs-fza.pdf" to "20020708-rs-fza-d.pdf"
# (and changed case: "rs-CH-FL"). The file name is the identity, so both
# names were stored as separate documents: 37 twins with identical text.
# The trailing language code is not part of the identity; it gives the
# language when it is not German.
_LANG_SUFFIX = re.compile(r"[-_](d|de|f|fr|i|it|e|en)$", re.IGNORECASE)
_SUFFIX_LANG = {"d": "de", "de": "de", "f": "fr", "fr": "fr",
                "i": "it", "it": "it", "e": "en", "en": "en"}

# Stopwords for a document-level language check: the DE index page also
# links French and Italian files.
_STOPWORDS = {
    "de": re.compile(r"\b(?:der|die|und|des|nicht|wird|ist|mit|auf|für)\b", re.I),
    "fr": re.compile(r"\b(?:le|la|les|des|et|est|pour|dans|une|du)\b", re.I),
    "it": re.compile(r"\b(?:il|della|che|per|del|una|sono|nel|alla|di)\b", re.I),
}


def file_identity(pdf_url: str) -> tuple[str, str | None]:
    """(doc_number, language from the file name or None) for a SEM PDF url."""
    fname = pdf_url.rsplit("/", 1)[-1]
    stem = re.sub(r"\.pdf$", "", fname, flags=re.IGNORECASE).lower()
    m = _LANG_SUFFIX.search(stem)
    lang = None
    if m:
        lang = _SUFFIX_LANG[m.group(1).lower()]
        stem = stem[: m.start()]
    return stem[:80], lang


def text_language(text: str, default: str = "de") -> str:
    """The text's language when it clearly is not `default` (a 1.5x
    stopword lead over it), else `default`. Mixed documents (the Bürgerrecht
    handbook's trilingual form annexes) keep the default."""
    sample = text[:20000]
    scores = {k: len(rx.findall(sample)) for k, rx in _STOPWORDS.items()}
    best = max(scores, key=scores.get)
    if best != default and scores[best] >= 20 and scores[best] > 1.5 * scores[default]:
        return best
    return default


def classify(title: str, default: str) -> str:
    """doc_type from the item's title; the section's default otherwise."""
    t = title.strip().lower()
    if t.startswith("rundschreiben") or "gemeinsames rundschreiben" in t:
        return "rundschreiben"
    if t.startswith(("anhang", "anh.", "beilage")):
        return "weisung_anhang"
    if t.startswith("merkblatt"):
        return "merkblatt"
    if t.startswith("faq"):
        return "faq"
    if t.startswith("bericht"):
        return "bericht"
    if "formular" in t or t.startswith(("zusatzblatt", "erhebungsbericht (muster)")):
        return "formular"
    return default


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def parse_download_items(html: str, page_url: str) -> list[dict]:
    """The PDF download items in a page's main content, in page order:
    {pdf_url, title, description, file_date}."""
    soup = BeautifulSoup(html, "html.parser")
    main = soup.find("main") or soup
    items = []
    for a in main.find_all("a", href=True):
        pdf_url = urljoin(page_url, a["href"])
        u = urlparse(pdf_url)
        if not u.path.lower().endswith(".pdf"):
            continue
        if not u.netloc.endswith("sem.admin.ch") or "/dam/" not in u.path:
            continue
        h = a.find(class_="download-item__title")
        title = _clean(h.get_text(" ")) if h else _clean(a.get_text(" "))
        d = a.find(class_="download-item__description")
        description = _clean(d.get_text(" ")) if d else ""
        meta = a.find(class_="download-item__meta-info")
        file_date = ""
        if meta:
            for span in meta.find_all(class_="meta-info__item"):
                file_date = first_date_iso(span.get_text(" ")) or file_date
        items.append({"pdf_url": pdf_url, "title": title or "(untitled)",
                      "description": description, "file_date": file_date})
    return items


def parse_chapter_cards(html: str) -> list[tuple[str, str]]:
    """(path, chapter title) for each "Mehr über «...»" card in main content."""
    soup = BeautifulSoup(html, "html.parser")
    main = soup.find("main") or soup
    out, seen = [], set()
    for a in main.find_all("a", href=True):
        m = _CARD_LABEL.match(_clean(a.get("aria-label", "")))
        href = a["href"]
        if not m or not href.startswith("/de/") or href in seen:
            continue
        seen.add(href)
        out.append((href, _clean(m.group(1))))
    return out


def item_date(item: dict) -> str:
    """Issuance or "Stand" date: the title's date ("Rundschreiben: ... (23.
    Dezember 2010)"), else the description's ("(Stand 1. November 2026)"),
    else the file date shown in the meta line."""
    return (first_date_iso(item["title"]) or first_date_iso(item["description"])
            or item["file_date"])



class SemWeisungenScraper(PracticeScraper):
    SOURCE_KEY = "sem_weisungen"
    ISSUING_AUTHORITY = "SEM"
    DEFAULT_DOC_TYPE = "weisung"
    REQUEST_DELAY = 1.5
    # SEM revises a document under the same file name (weisungen-aig-d.pdf,
    # Stand 1.1.2026 -> 1.11.2026). The revision is the URL (its sd-web id)
    # plus the file date; a change re-fetches the document. A URL can stay
    # the same across a revision, so PDFs are not cached by URL.
    REVISION_FIELD = "revision"
    CACHE_PDFS = False
    # The same text is published under two file names
    # (20170228-rs-transportdienstleistungen.pdf on the FZA page,
    # transportdienstleistungen.pdf in AIG chapter 4).
    DEDUP_CONTENT = True
    # Some forms are scans without a text layer (Hotelmeldeschein, 2013).
    OCR_SCANS = True
    NO_TEXT_LAYER_BODY = "[Textlayer fehlt: gescanntes PDF]"

    def _make_doc_id(self, stub: dict) -> str:
        # German keeps the historical id (sem_weisungen_<file>), so a renamed
        # "-d" file maps onto the row already stored instead of a twin.
        base = super()._make_doc_id(stub)
        lang = stub.get("language") or "de"
        return base if lang == "de" else f"{base}_{lang}"

    def _normalize(self, stub: dict, body_text: str) -> dict:
        # The id stays the one run() checked against the stored rows (from
        # the stub); only the language label follows the text.
        doc = super()._normalize(stub, body_text)
        doc["revision"] = stub.get("revision", "")
        if body_text:
            doc["language"] = text_language(body_text, doc["language"])
        return doc

    def _page(self, path: str) -> str:
        r = self.get(urljoin(_BASE, path))
        r.raise_for_status()
        return r.text

    def discover_documents(self) -> Iterator[dict]:
        stubs: list[dict] = []
        errors: list[str] = []
        seen: set[str] = set()

        def collect(path: str, label: str, default_type: str, chapter: str | None,
                    html: str):
            page_url = urljoin(_BASE, path)
            for it in parse_download_items(html, page_url):
                if it["pdf_url"] in seen:      # first page listing a PDF wins
                    continue
                seen.add(it["pdf_url"])
                doc_number, file_lang = file_identity(it["pdf_url"])
                if _OTHER_LANGUAGE_FORM.search(doc_number):
                    continue
                title = it["title"]
                if it["description"] and it["description"] not in title:
                    title = f"{title} {it['description']}"
                stubs.append({
                    "pdf_url": it["pdf_url"],
                    "url": page_url,
                    "title": f"{label} — {title}",
                    "doc_number": doc_number,
                    "date": item_date(it),
                    "language": file_lang or "de",
                    "doc_type": classify(it["title"], default_type),
                    "topics": [label] + ([chapter] if chapter else []),
                    "revision": f"{it['pdf_url']}|{it['file_date']}",
                })

        for path, label, default_type in SECTIONS:
            try:
                html = self._page(path)
            except Exception as e:
                errors.append(f"{path}: {e}")
                continue
            collect(path, label, default_type, None, html)
            for sub, chapter in parse_chapter_cards(html):
                try:
                    collect(sub, label, default_type, chapter, self._page(sub))
                except Exception as e:
                    errors.append(f"{sub}: {e}")
        for path, label, default_type, chapter in EXTRA_PAGES:
            try:
                collect(path, label, default_type, chapter, self._page(path))
            except Exception as e:
                errors.append(f"{path}: {e}")

        # Refuse before fetching anything: a partial crawl would otherwise
        # look like a quiet week.
        if errors or len(stubs) < MIN_DOCUMENTS:
            raise RuntimeError(
                f"SEM discovery incomplete: {len(stubs)} documents "
                f"(expected >= {MIN_DOCUMENTS}); page errors: {errors or 'none'}")
        logger.info("[%s] discovered %d documents", self.SOURCE_KEY, len(stubs))

        # Held documents first, so a held row is refreshed before a new file
        # with the same text can claim that text (DEDUP_CONTENT).
        held = [s for s in stubs if self._make_doc_id(s) in self._seen_ids]
        new = [s for s in stubs if self._make_doc_id(s) not in self._seen_ids]
        yield from held
        yield from new


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    SemWeisungenScraper().run()
