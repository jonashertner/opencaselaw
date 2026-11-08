"""
BJ — international mutual legal assistance in criminal matters (Rechtshilfe)
=============================================================================

Federal Office of Justice (BJ), Fachbereich internationale Rechtshilfe in
Strafsachen (the "RHF" unit, also the operator of its own rhf.admin.ch
site). Four pages, all on the current Nuxt "download-item" markup shared
with EHRA/WEKO/OAK BV — but with a twist two of them don't have: the
Wegleitungen page prints a <p class="download-item__description"> between
the <h4> title and the <p class="download-item__meta-info">, which
sdweb.download_items (positional: `a.find("p")`) would misread as the meta
text. `_dam_items` below picks each <p> by its class instead.

Pages
-----
  de /de/internationale-rechtshilfe-in-strafsachen
  fr /fr/entraide-judiciaire-internationale-en-matiere-penale
  it /it/assistenza-giudiziaria-internazionale-in-materia-penale
      -> the 10 annual "Tätigkeitsbericht" reports (2015-2024) + the
         yearly Statistik, DE/FR/IT (genuinely distinct per language: same
         DAM hash, different /dam/{lang}/ content — verified 2026-11-08 by
         Content-Length, not assumed).

  de /de/auslieferung   fr /fr/extradition   it /it/estradizione
      -> extradition guidance: a Fact Sheet, a "Darlegung des Auslieferungs-
         verfahrens" explainer and a Checkliste, each DE/FR/IT (same hash,
         real per-language content) — plus the Darlegung's additional
         translations into es/pt/ro/ru/sq/sr/hr, which exist ONCE (same
         hash and byte-identical regardless of which site-language page
         links them) and are tagged by the language the FILENAME's suffix
         names, not by the page they were found on.

  rhf.admin.ch /de/muster-und-formulare (DE/FR/IT via the same slug under
      /fr/ and /it/) -> muster-ersuchen-strafrecht.pdf, the Muster-Ersuchen
      the task named (the other model-request files on that page are civil
      law — HBewUe70/HUE54, the Hague evidence/service conventions — and
      are left out).

  rhf.admin.ch/de/wegleitungen-checklisten-und-rundschreiben (DE only; no
      FR/IT equivalent found) -> the Wegleitung "Die internationale
      Rechtshilfe in Strafsachen" (current edition, 15.10.2025; its old
      English translation is the 2009 edition — rhf.admin.ch keeps no
      newer English edition) and the bilateral Wegleitung Schweiz-Italien.
      This page also carries ~30 further checklists, Rundschreiben Nr. 1-7,
      asset-recovery and prisoner-transfer Merkblätter not picked up here —
      left for a follow-up pass (see the build report).

NOTE: the old rhf.admin.ch/dam/rhf/... URL pattern and the
backend.bj.admin.ch/fileservice/... host named when this scraper was
scoped both 404 now; every document above was re-found live on
2026-11-08 under the current /dam/{lang}/sd-web/<hash>/<file> pattern.

Excluded entirely: /de/gesuchsunterlagen-und-formulare — despite living
under bj.admin.ch's Rechtshilfe section, it is the international MAINTENANCE
(Alimente) collection process, a civil-law matter, not criminal.
"""
from __future__ import annotations

import logging
import re
from typing import Iterator
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .base import PracticeScraper, first_date_iso, slugify
from .langdedup import TextDedupMixin

logger = logging.getLogger(__name__)

_BJ = "https://www.bj.admin.ch"
_RHF = "https://www.rhf.admin.ch"

MAIN_PAGES = (
    ("de", _BJ + "/de/internationale-rechtshilfe-in-strafsachen"),
    ("fr", _BJ + "/fr/entraide-judiciaire-internationale-en-matiere-penale"),
    ("it", _BJ + "/it/assistenza-giudiziaria-internazionale-in-materia-penale"),
)

AUSLIEFERUNG_PAGES = (
    ("de", _BJ + "/de/auslieferung"),
    ("fr", _BJ + "/fr/extradition"),
    ("it", _BJ + "/it/estradizione"),
)

MUSTER_PAGES = (
    ("de", _RHF + "/de/muster-und-formulare"),
    ("fr", _RHF + "/fr/modeles-et-formulaires"),
    ("it", _RHF + "/it/modelli-e-formulari"),
)

WEGLEITUNGEN_PAGE = ("de", _RHF + "/de/wegleitungen-checklisten-und-rundschreiben")

_AUSLIEFERUNG_TITLES = {
    "auslieferung": "BJ Merkblatt Auslieferung",
    "darlegung-auslieferung": "BJ Darlegung des Auslieferungsverfahrens",
    "checkliste-personenfahndung": "BJ Checkliste internationale Personenfahndung",
}

# Filename language suffixes seen on the Auslieferung page's extra
# translations (not Swiss official languages, so they're not reachable any
# other way than from the filename).
_LANG_SUFFIX = {"es": "es", "pt": "pt", "ro": "ro", "ru": "ru", "sq": "sq",
                "sr": "sr", "hr": "hr", "ar": "ar", "uk": "uk", "e": "en"}


def _clean(s: str) -> str:
    return " ".join((s or "").split())


def _dam_items(html: str, base_url: str) -> list[dict]:
    """download-item anchors, admin.ch's current Nuxt markup. Like
    sdweb.download_items but picks the description and meta-info
    paragraphs by CLASS, not position — needed because the Wegleitungen
    page prints both, and sdweb.download_items' `a.find("p")` would catch
    the description where only the meta-info one carries a parseable
    date."""
    soup = BeautifulSoup(html, "html.parser")
    seen: set[str] = set()
    out = []
    for a in soup.find_all("a", href=True):
        classes = a.get("class") or []
        if "download-item" not in classes:
            continue
        href = a["href"]
        pdf_url = urljoin(base_url, href)
        if not urlparse(pdf_url).path.lower().endswith(".pdf"):
            continue
        if pdf_url in seen:
            continue
        seen.add(pdf_url)
        h4 = a.find("h4")
        title = _clean(h4.get_text(" ", strip=True)) if h4 else ""
        meta_p = a.find("p", class_="download-item__meta-info")
        meta = _clean(meta_p.get_text(" ", strip=True)) if meta_p else ""
        heading = a.find_previous(["h1", "h2", "h3"])
        out.append({
            "title": title,
            "meta": meta,
            "file_date": first_date_iso(meta),
            "pdf_url": pdf_url,
            "section": _clean(heading.get_text(" ", strip=True)) if heading else "",
        })
    return out


def _dam_hash(pdf_url: str) -> str:
    """The hash segment of an admin.ch /dam/{lang}/sd-web/<hash>/<file>
    URL — shared across a document's language prefixes even when the
    served content differs per language (verified for every family here by
    Content-Length, not assumed)."""
    parts = urlparse(pdf_url).path.split("/")
    try:
        return parts[parts.index("sd-web") + 1]
    except (ValueError, IndexError):
        return urlparse(pdf_url).path


def _filename_stem(pdf_url: str) -> str:
    name = urlparse(pdf_url).path.rsplit("/", 1)[-1]
    return name[:-4] if name.lower().endswith(".pdf") else name


def split_lang_suffix(stem: str) -> tuple[str, str | None]:
    """("darlegung-auslieferung-es", "es"), ("auslieferung", None) — the
    longest matching suffix wins so "-es" isn't mistaken for a shorter one."""
    for suf in sorted(_LANG_SUFFIX, key=len, reverse=True):
        if stem.endswith(f"-{suf}"):
            return stem[: -(len(suf) + 1)], _LANG_SUFFIX[suf]
    return stem, None


_YEAR = re.compile(r"\b(20\d{2})\b")

# "10. Auflage 2025", "9th edition 2009", "1re édition 2003", "1a edizione 2003"
_EDITION = re.compile(
    r"\b\d{1,2}(?:\.|st|nd|rd|th|e|re|ème|a)?\s+(?:Auflage|edition|édition|edizione)\s+((?:19|20)\d{2})\b",
    re.I)


def edition_year(text: str) -> str:
    """The year of the edition printed on a guide's cover, or ""."""
    m = _EDITION.search(text or "")
    return m.group(1) if m else ""


class BjRechtshilfeScraper(TextDedupMixin, PracticeScraper):
    SOURCE_KEY = "bj_rechtshilfe"
    ISSUING_AUTHORITY = "BJ"
    DEFAULT_DOC_TYPE = "bericht"
    REVISION_FIELD = "pdf_url"
    REQUEST_DELAY = 1.5
    OCR_SCANS = True
    NO_TEXT_LAYER_BODY = "[Textlayer fehlt: gescanntes PDF]"

    def _make_doc_id(self, stub: dict) -> str:
        return f"{self.SOURCE_KEY}_{stub['bj_id']}"

    def _normalize(self, stub: dict, body_text: str) -> dict:
        doc = super()._normalize(stub, body_text)
        if doc["doc_type"] == "wegleitung":
            # rhf.admin.ch stamped several old files with its 2020-07-13
            # migration date; the edition printed on the cover is the date a
            # reader cites ("9th edition 2009", "1. Auflage 2003").
            year = edition_year(body_text[:1500])
            if year and not doc["date"].startswith(year):
                doc["date"] = year
        if not doc["date"]:
            doc["date"] = first_date_iso(body_text[:2500])
        return doc

    # ── annual reports + statistics ──

    def _report_stubs(self) -> Iterator[dict]:
        for lang, url in MAIN_PAGES:
            try:
                r = self.get(url)
                r.raise_for_status()
            except Exception as e:
                logger.warning("[%s] main page %s fetch failed: %s", self.SOURCE_KEY, lang, e)
                continue
            items = _dam_items(r.text, url)
            logger.info("[%s] %s main page: %d PDFs", self.SOURCE_KEY, lang, len(items))
            for it in items:
                m = re.search(r"jb-irh-(\d{4})\.pdf", it["pdf_url"])
                if m:
                    year = m.group(1)
                    yield {
                        "pdf_url": it["pdf_url"], "url": url, "title": it["title"],
                        "doc_number": f"BJ Tätigkeitsbericht Internationale Rechtshilfe {year}",
                        "date": first_date_iso(it["meta"]) or it["file_date"] or year,
                        "language": lang, "doc_type": "bericht",
                        "topics": ["Internationale Rechtshilfe", "Tätigkeitsbericht", year],
                        "bj_id": f"jb_irh_{year}_{lang}",
                    }
                elif "rhf-statistik.pdf" in it["pdf_url"]:
                    yield {
                        "pdf_url": it["pdf_url"], "url": url, "title": it["title"],
                        "doc_number": "BJ Statistik Internationale Rechtshilfe",
                        "date": it["file_date"],
                        "language": lang, "doc_type": "bericht",
                        "topics": ["Internationale Rechtshilfe", "Statistik"],
                        "bj_id": f"rhf_statistik_{lang}",
                    }

    # ── Auslieferung ──

    def _auslieferung_stubs(self) -> Iterator[dict]:
        for page_lang, url in AUSLIEFERUNG_PAGES:
            try:
                r = self.get(url)
                r.raise_for_status()
            except Exception as e:
                logger.warning("[%s] Auslieferung %s fetch failed: %s", self.SOURCE_KEY, page_lang, e)
                continue
            items = _dam_items(r.text, url)
            logger.info("[%s] Auslieferung %s: %d PDFs", self.SOURCE_KEY, page_lang, len(items))
            for it in items:
                base, suffix_lang = split_lang_suffix(_filename_stem(it["pdf_url"]))
                number = _AUSLIEFERUNG_TITLES.get(base)
                if not number:
                    continue       # a Word template (muster-haftbefehl etc.) or unrecognised file
                lang = suffix_lang or page_lang
                yield {
                    "pdf_url": it["pdf_url"], "url": url, "title": it["title"],
                    "doc_number": number,
                    "date": it["file_date"],
                    "language": lang, "doc_type": "merkblatt",
                    "topics": ["Auslieferung", "Internationale Rechtshilfe", number],
                    "bj_id": f"ausl_{base}_{lang}",
                }

    # ── Muster-Ersuchen ──

    def _muster_stubs(self) -> Iterator[dict]:
        for lang, url in MUSTER_PAGES:
            try:
                r = self.get(url)
                r.raise_for_status()
            except Exception as e:
                logger.warning("[%s] Muster page %s fetch failed: %s", self.SOURCE_KEY, lang, e)
                continue
            items = _dam_items(r.text, url)
            for it in items:
                if "muster-ersuchen-strafrecht.pdf" not in it["pdf_url"]:
                    continue       # the civil-law HBewUe70/HUE54 model requests on the same page
                yield {
                    "pdf_url": it["pdf_url"], "url": url, "title": it["title"],
                    "doc_number": "BJ Muster-Ersuchen Strafrecht",
                    # undated form; its file date (2020-07-13) is rhf.admin.ch's
                    # migration stamp, shared with unrelated older files
                    "date": "",
                    "language": lang, "doc_type": "formular",
                    "topics": ["Muster-Ersuchen", "Internationale Rechtshilfe"],
                    "bj_id": f"muster_ersuchen_strafrecht_{lang}",
                }

    # ── Wegleitungen (DE only) ──

    def _wegleitung_stubs(self) -> Iterator[dict]:
        lang, url = WEGLEITUNGEN_PAGE
        try:
            r = self.get(url)
            r.raise_for_status()
        except Exception as e:
            logger.warning("[%s] Wegleitungen page fetch failed: %s", self.SOURCE_KEY, e)
            return
        items = _dam_items(r.text, url)
        logger.info("[%s] Wegleitungen page: %d PDFs", self.SOURCE_KEY, len(items))
        for it in items:
            stem = _filename_stem(it["pdf_url"])
            if stem == "wegleitung-strafsachen":
                yield {
                    "pdf_url": it["pdf_url"], "url": url, "title": it["title"],
                    "doc_number": "BJ Wegleitung Internationale Rechtshilfe Strafsachen",
                    "date": it["file_date"],
                    "language": "de", "doc_type": "wegleitung",
                    "topics": ["Wegleitung", "Internationale Rechtshilfe"],
                    "bj_id": "wegleitung_strafsachen_de",
                }
            elif stem == "wegleitung-strafsachen-e":
                yield {
                    "pdf_url": it["pdf_url"], "url": url, "title": it["title"],
                    "doc_number": "BJ Wegleitung Internationale Rechtshilfe Strafsachen",
                    "date": it["file_date"],
                    "language": "en", "doc_type": "wegleitung",
                    "topics": ["Wegleitung", "Internationale Rechtshilfe"],
                    "bj_id": "wegleitung_strafsachen_en",
                }
            elif stem == "wegleitung-rechtshilfe-italien":
                yield {
                    "pdf_url": it["pdf_url"], "url": url, "title": it["title"],
                    "doc_number": "BJ Wegleitung Rechtshilfe Schweiz-Italien",
                    "date": it["file_date"],
                    "language": "de", "doc_type": "wegleitung",
                    "topics": ["Wegleitung", "Internationale Rechtshilfe", "Italien"],
                    "bj_id": "wegleitung_italien_de",
                }

    def discover_documents(self) -> Iterator[dict]:
        for gen in (self._report_stubs, self._auslieferung_stubs,
                    self._muster_stubs, self._wegleitung_stubs):
            for stub in gen():
                out = self.with_body(stub, stub["doc_number"])
                if out:
                    yield out
