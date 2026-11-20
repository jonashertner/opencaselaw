"""
SEM Weisungen und Kreisschreiben
================================

State Secretariat for Migration. The most comprehensive federal
Verwaltungspraxis corpus — covers AIG, Asyl, BüG (Bürgerrecht), FZA,
Visa, Integration, Datenschutz/Öffentlichkeitsgesetz.

Index page (DE):
  https://www.sem.admin.ch/sem/de/home/publiservice/weisungen-kreisschreiben.html

Section structure:
  I.   Ausländerbereich          → AIG-Weisungen (chapters 0-11)
  II.  Freizügigkeitsabkommen     → FZA-Rundschreiben
  III. Asylgesetz                 → Asyl-Handbuch
  IV.  Integration
  V.   Bürgerrecht                → BüG-Handbuch + Rundschreiben
  VI.  Datenschutz und ÖG
  VII. Visa                       → Visa-Handbuch
  VIII Weitere Weisungen

Each section is a sub-page listing PDFs (some single multi-MB files,
some chapter-split, some thematic Rundschreiben).

PDF naming: /dam/sem/de/data/rechtsgrundlagen/weisungen/{topic}/*.pdf
"""
from __future__ import annotations

import logging
import re
from typing import Iterator
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .base import PracticeScraper

logger = logging.getLogger(__name__)

_BASE = "https://www.sem.admin.ch"
INDEX_URL = "https://www.sem.admin.ch/sem/de/home/publiservice/weisungen-kreisschreiben.html"

SECTIONS = {
    "auslaenderbereich":        ("AIG / Ausländerbereich",  "weisung"),
    "fza":                      ("FZA / Freizügigkeitsabkommen", "rundschreiben"),
    "asylgesetz":               ("Asylgesetz",               "weisung"),
    "integration":              ("Integration",              "weisung"),
    "buergerrecht":             ("Bürgerrecht",              "weisung"),
    "datenschutz_und_oeffentlichkeitsgesetz": ("DSG / ÖG",   "weisung"),
    "visa":                     ("Visa",                     "weisung"),
    "weitere_weisungen":        ("Weitere",                  "weisung"),
}

_DATE = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{4})")

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
_VERSION_DATE = re.compile(r"(?:Stand|Version|gültig ab)\s*[:\-]?\s*(\d{1,2})\.(\d{1,2})\.(\d{4})", re.I)


class SemWeisungenScraper(PracticeScraper):
    SOURCE_KEY = "sem_weisungen"
    ISSUING_AUTHORITY = "SEM"
    DEFAULT_DOC_TYPE = "weisung"
    REQUEST_DELAY = 1.5

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
        if body_text:
            doc["language"] = text_language(body_text, doc["language"])
        return doc

    def discover_documents(self) -> Iterator[dict]:
        for section_slug, (section_label, doc_type) in SECTIONS.items():
            section_url = (f"{_BASE}/sem/de/home/publiservice/weisungen-kreisschreiben/"
                           f"{section_slug}.html")
            try:
                r = self.get(section_url)
                r.raise_for_status()
            except Exception as e:
                logger.warning("SEM section [%s] fetch failed: %s", section_slug, e)
                continue

            soup = BeautifulSoup(r.text, "html.parser")
            seen: set[str] = set()
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if not href.lower().endswith(".pdf"):
                    continue
                if "/dam/sem/" not in href and "/dam/data/sem/" not in href:
                    continue
                pdf_url = urljoin(_BASE, href)
                if pdf_url in seen:
                    continue
                seen.add(pdf_url)

                title = re.sub(r"\s+", " ", a.get_text(" ", strip=True)) or "(untitled)"
                # Surrounding text often carries "Stand: DD.MM.YYYY"
                ctx = a.get_text(" ", strip=True) + " " + (
                    a.find_parent().get_text(" ", strip=True) if a.find_parent() else "")
                vm = _VERSION_DATE.search(ctx) or _DATE.search(ctx)
                date_iso = (f"{vm.group(3)}-{int(vm.group(2)):02d}-{int(vm.group(1)):02d}"
                            if vm else "")

                doc_number, file_lang = file_identity(pdf_url)

                yield {
                    "pdf_url": pdf_url,
                    "url": section_url,
                    "title": f"{section_label} — {title}",
                    "doc_number": doc_number,
                    "date": date_iso,
                    "language": file_lang or "de",
                    "doc_type": doc_type,
                    "topics": [section_label],
                }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    SemWeisungenScraper().run()
