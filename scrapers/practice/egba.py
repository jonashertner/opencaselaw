"""
EGBA — Eidgenössisches Amt für Grundbuch- und Bodenrecht (in the BJ)
=====================================================================

The federal land-register and rural-land-law office supervises the cantonal
land registers (Grundbuchämter) and the Lex Koller / BewG regime for the
acquisition of real estate by persons abroad. Its guidance sits on five
bj.admin.ch pages, each with DE/FR/IT equivalents (same Nuxt + sd-web
platform as ehra.py; EGBA's own pages use the older `/dam/<lang>/sd-web/
<hash>/<file>.pdf` markup, so sdweb.download_items works unmodified):

  de /de/dokumentation-und-weisungen                  (Weisungen, Erläuterungen
      fr /fr/documentation-et-directives               GBV incl. Anhänge 1+3,
      it /it/documentazione-e-direttive                inspection aide-mémoire +
                                                         the 2024 cross-cutting
                                                         report + 5 cantonal
                                                         inspection reports)
  de /de/behoerdeninformationen                        (Lex Koller: the 2009
      fr /fr/informations-aux-autorites                 Wegleitung for the land
      it /it/informazioni-per-le-autorita                registries + the 2019/
                                                          2020 Brexit Infoschreiben)
  de /de/oberaufsicht-ueber-das-grundbuch-und-andere-register   (one 2016 report
      fr /fr/haute-surveillance-sur-le-registre-foncier-et-autres on the EGBA/
      it /it/alta-sorveglianza-sul-registro-fondiario-e-altri    cantonal
                                                                  oversight split)
  de /de/das-baeuerliche-bodenrecht                    (BGBB/LDFR: the Art. 79
      fr /fr/droit-foncier-rural                        register of recognised
      it /it/il-diritto-fondiario-rurale                 bodies, the Bundesrat
                                                           report + Gutachten on
                                                           postulate 15.3284
                                                           Vogler, its FR-only
                                                           Annexe II)
  de /de/schiffsregister                                (ship-register forms:
      fr /fr/registre-des-bateaux                        Anmeldung + the UBO
      it /it/registro-del-naviglio                       confirmation)

`/de/egris-weiterentwicklung` (eGRIS) was checked and carries no PDFs — an
informational page only, left out.

Cross-language identity and the "same text reused" trap
---------------------------------------------------------
Every document is grouped by its DAM hash (`_dam_key`, the `<hash>` segment
of the sd-web path — identical across the /de/, /fr/, /it/ URL variants,
exactly as in ehra.py). But unlike EHRA's two pages, these five pages mix:

  * genuinely trilingual documents (Weisung 1/2016 / Directive 1/2016 /
    Direttiva 1/2016 — three different PDFs, three different titles);
  * documents that exist in ONE language only and are simply RELISTED
    under the other two languages' pages with the byte-identical PDF and
    the SAME (untranslated) title — most of the cantonal inspection reports
    are French-only (Sion, Gruyère/Fribourg, Est vaudois) or German-only
    (Obwalden, Uri) and appear unchanged on all three language pages;
  * one page (IT `/it/documentazione-e-direttive`) that lists a single
    document under a THIRD, concatenated title ("Übersicht Inspektionen
    EGBA / Aide-mémoire relatif aux inspections de l'OFRF") instead of a
    real Italian translation — because no Italian version exists and the
    page falls back to showing both the German and French titles together;
  * one document (the postulate 15.3284 Vogler "Annexe II. Aperçu des
    mesures concernant la LDFR") that exists in French only and is NOT
    listed on the German page at all — found only by also scanning the
    FR/IT pages, not just DE.

`group_documents()` handles all four cases with one rule per DAM-hash group:
collapse two listings into one row whenever one title is a literal substring
of the other (this folds the IT concatenated title into whichever of the
German/French originals it quotes, and folds exact untranslated reuses
across language pages), and otherwise keep each genuinely distinct title as
its own row. The row's `language` is then detected from the TITLE TEXT
itself (`detect_lang`), not from which page it was found on — the only way
to correctly label the French-only Fribourg inspection report as `fr` even
though it is also reachable from the German listing page.

doc_number is minted once per DAM-hash group from the German title when one
exists in the group, else the first title found, so every language row of
one document shares it (EHRA's own convention). doc_type is classified from
that same reference title (`classify_doc_type`), so language rows of one
document also share a doc_type.

date: title first (first_date_iso handles "vom 4. April 2019" / "du 28
novembre 2023" / "del 20 settembre 2019" alike), then the PDF's own cover
dateline ("Bern, im Oktober 2025"), then the sd-web file-modified date as a
last resort (base.py's own documented fallback order).
"""
from __future__ import annotations

import logging
import re
from typing import Iterator
from urllib.parse import urlparse

from .base import PracticeScraper, dateline_iso, first_date_iso, slugify
from .langdedup import TextDedupMixin
from .sdweb import download_items

logger = logging.getLogger(__name__)

_BASE = "https://www.bj.admin.ch"

PAGES: dict[str, dict[str, str]] = {
    "weisungen": {
        "de": "/de/dokumentation-und-weisungen",
        "fr": "/fr/documentation-et-directives",
        "it": "/it/documentazione-e-direttive",
    },
    "behoerden": {
        "de": "/de/behoerdeninformationen",
        "fr": "/fr/informations-aux-autorites",
        "it": "/it/informazioni-per-le-autorita",
    },
    "oberaufsicht": {
        "de": "/de/oberaufsicht-ueber-das-grundbuch-und-andere-register",
        "fr": "/fr/haute-surveillance-sur-le-registre-foncier-et-autres",
        "it": "/it/alta-sorveglianza-sul-registro-fondiario-e-altri",
    },
    "bgbb": {
        "de": "/de/das-baeuerliche-bodenrecht",
        "fr": "/fr/droit-foncier-rural",
        "it": "/it/il-diritto-fondiario-rurale",
    },
    "schiffsregister": {
        "de": "/de/schiffsregister",
        "fr": "/fr/registre-des-bateaux",
        "it": "/it/registro-del-naviglio",
    },
}

TOPICS: dict[str, list[str]] = {
    "weisungen": ["Grundbuch"],
    "behoerden": ["Lex Koller", "BewG", "Grundbuch"],
    "oberaufsicht": ["Grundbuch", "Oberaufsicht"],
    "bgbb": ["BGBB", "Bäuerliches Bodenrecht"],
    "schiffsregister": ["Schiffsregister"],
}

# ── language detection from title text (not from the page a title was
# found on — see module docstring: the same French text is also listed,
# untranslated, on the German and Italian pages) ──
_DE_WORD = re.compile(
    r"\b(über|für|vom|zum|betreffend|Kanton|Bericht|Wegleitung|"
    r"Informationsschreiben|Update|Verzeichnis|Erläuterung|Anmeldung|"
    r"Bestätigung|Aufnahme|Gewässern|anderen|Weisung|Austausch|Bezug)\b",
    re.I,
)
_FR_WORD = re.compile(
    r"\b(relatif|concernant|Rapport|Canton|Directive|Commentaires|registre|"
    r"Liste|organismes|reconnus|Simplifications|Conseil|f[ée]d[ée]ral|"
    r"postulat|Aper[çc]u|mesures|Annexe|donnant|suite|sauvegarde|long|terme|"
    r"donn[ée]es|modification|ordonnance)\b",
    re.I,
)
_IT_WORD = re.compile(
    r"\b(Direttiva|Commento|libro|mastro|Rapporto|Cantone|dell['’]|ufficio|"
    r"registro|fondiario|modifiche|Elenco|organismi|riconosciuti|"
    r"salvaguardia|lungo|termine|dati)\b",
    re.I,
)


def detect_lang(title: str, hint: str = "de") -> str:
    """The language of `title`'s own text, scored by language-specific
    words — not the page it was listed on (see module docstring: several
    documents here are relisted, untranslated, on another language's
    page). `hint` (the page's language) only breaks a tie or a 0-0-0."""
    scores = {
        "de": len(_DE_WORD.findall(title)),
        "fr": len(_FR_WORD.findall(title)),
        "it": len(_IT_WORD.findall(title)),
    }
    best = max(scores.values())
    if best == 0:
        return hint
    top = [lang for lang, s in scores.items() if s == best]
    return hint if hint in top else top[0]


_STAND_AM = re.compile(
    r"\b(?:Stand\s+am|[ÉE]tat\s+au|Situazione\s+al|Stato\s+(?:il|al))\s+(.{3,25}?\d{4})", re.I)


def stand_am_iso(text: str) -> str:
    """The date of a "(Stand am 20. Oktober 2010)"/"État au ..."/
    "Situazione al ..." stamp — the actual as-of date of a register or
    list document, as opposed to a statute it cites by its own
    (much older) enactment date. Needed because first_date_iso's "first
    date in reading order wins" rule otherwise picks up the Art. 79 BGBB
    register's cover line "... Bundesgesetz vom 4. Oktober 1991 ..." (the
    LAW's date) ahead of the line two rows down that actually states the
    register's own date, "(Stand am 20. Oktober 2010)"."""
    m = _STAND_AM.search(text or "")
    return first_date_iso(m.group(1)) if m else ""


def _dam_key(url: str) -> str:
    """The language-independent part of an sd-web URL: <hash>/<file>
    (same convention as ehra._dam_key)."""
    parts = urlparse(url).path.split("/")
    try:
        i = parts.index("sd-web")
        return "/".join(parts[i + 1:])
    except ValueError:
        return urlparse(url).path


def _is_same_doc(a: str, b: str) -> bool:
    """True when `a` and `b` are the same document's title under the
    sd-web dedup rules: identical, or one is a literal substring of the
    other (the IT page's "<German title> / <French title>" fallback for
    a document with no Italian translation contains both)."""
    return a == b or a in b or b in a


_WEISUNG_RE = re.compile(r"^(Weisung|Directive|Direttiva)\b", re.I)
_ERLAEUT_RE = re.compile(r"(Erläuterung|Commentaires?\s+concernant|Commento\s+alle)", re.I)
_WEGLEIT_RE = re.compile(r"Wegleitung", re.I)
_FORMULAR_RE = re.compile(r"(Anmeldung|Bestätigung\s+betreffend)", re.I)
_MITTEIL_RE = re.compile(r"(Informationsschreiben|Update\s+vom)", re.I)
_BERICHT_RE = re.compile(
    r"(Bericht|Rapport|Rapporto|Übersicht|Aide-mémoire|Inspektionsbericht|"
    r"Abgrenzung|Verzeichnis|Liste\s+des\s+organismes|Elenco\s+degli\s+organismi|"
    r"Gutachten|Postulat|Annexe|Aper[çc]u)",
    re.I,
)


def classify_doc_type(title: str) -> str:
    """doc_type from the reference (German-preferred) title of a DAM-hash
    group — reused existing types only (see RULES in the build brief)."""
    if _WEISUNG_RE.search(title):
        return "weisung"
    if _ERLAEUT_RE.search(title):
        return "erlaeuterung"
    if _WEGLEIT_RE.search(title):
        return "wegleitung"
    if _FORMULAR_RE.search(title):
        return "formular"
    if _MITTEIL_RE.search(title):
        return "mitteilung"
    if _BERICHT_RE.search(title):
        return "bericht"
    return "mitteilung"


def group_documents(pages_html: dict[str, dict[str, str]]) -> list[dict]:
    """pages_html: page_key -> {lang: html}. Returns stubs (missing only
    `url`, filled in by the caller, which knows each page's own URL)."""
    out: list[dict] = []
    for page_key, by_lang in pages_html.items():
        groups: dict[str, list[dict]] = {}
        for lang in ("de", "fr", "it"):
            html = by_lang.get(lang)
            if not html:
                continue
            for item in download_items(html, _BASE):
                dk = _dam_key(item["pdf_url"])
                groups.setdefault(dk, []).append({**item, "page_lang": lang})
        for dk, items in groups.items():
            kept: list[dict] = []
            for it in items:
                if any(_is_same_doc(it["title"], k["title"]) for k in kept):
                    continue
                kept.append(it)
            de_title = next((it["title"] for it in kept if it["page_lang"] == "de"), None)
            reference = de_title or kept[0]["title"]
            doc_type = classify_doc_type(reference)
            number = f"EGBA {reference}"[:170]
            for it in kept:
                lang = detect_lang(it["title"], it["page_lang"])
                out.append({
                    "pdf_url": it["pdf_url"],
                    "title": it["title"],
                    "doc_number": number,
                    "date": first_date_iso(it["title"]) or "",
                    "language": lang,
                    "doc_type": doc_type,
                    "topics": [number, *TOPICS.get(page_key, [])],
                    "page_key": page_key,
                    "_file_date": it["file_date"],
                    "egba_id": f"{page_key}_{slugify(dk)[:50]}_{lang}",
                })
    return out


class EgbaScraper(TextDedupMixin, PracticeScraper):
    SOURCE_KEY = "egba"
    ISSUING_AUTHORITY = "EGBA"
    DEFAULT_DOC_TYPE = "weisung"
    REVISION_FIELD = "pdf_url"      # DAM-hashed: a re-upload changes the URL
    REQUEST_DELAY = 1.0
    OCR_SCANS = True
    NO_TEXT_LAYER_BODY = "[Textlayer fehlt: gescanntes PDF]"

    def _make_doc_id(self, stub: dict) -> str:
        return f"{self.SOURCE_KEY}_{stub['egba_id']}"

    def _normalize(self, stub: dict, body_text: str) -> dict:
        doc = super()._normalize(stub, body_text)
        if not doc["date"]:
            head = body_text[:3000]
            if doc["doc_type"] == "formular":
                # a form prints no issuance date; the first date in it is a
                # cited statute's (the ship-register UBO form read 1937)
                doc["date"] = stand_am_iso(head) or dateline_iso(head)
            else:
                doc["date"] = (stand_am_iso(head) or dateline_iso(head)
                                or first_date_iso(head) or stub.get("_file_date", ""))
        return doc

    def _page(self, path: str) -> str | None:
        try:
            r = self.get(_BASE + path)
            r.raise_for_status()
            return r.text
        except Exception as e:
            logger.warning("[%s] %s fetch failed: %s", self.SOURCE_KEY, path, e)
            return None

    def discover_documents(self) -> Iterator[dict]:
        pages_html: dict[str, dict[str, str]] = {}
        for page_key, by_lang in PAGES.items():
            htmls: dict[str, str] = {}
            for lang, path in by_lang.items():
                html = self._page(path)
                if html is not None:
                    htmls[lang] = html
            pages_html[page_key] = htmls
            logger.info("[%s] %s: %d/%d language pages fetched",
                        self.SOURCE_KEY, page_key, len(htmls), len(by_lang))

        stubs = group_documents(pages_html)
        logger.info("[%s] %d document rows across %d pages",
                     self.SOURCE_KEY, len(stubs), len(PAGES))
        for stub in stubs:
            stub["url"] = _BASE + PAGES[stub["page_key"]]["de"]
            out = self.with_body(stub, stub["doc_number"])
            if out:
                yield out
