"""
NKVF / CNPT / CNPT — Nationale Kommission zur Verhütung von Folter
====================================================================

Switzerland's National Commission for the Prevention of Torture visits every
place of deprivation of liberty (prisons, police custody, psychiatric and
care institutions, federal asylum centres, deportation flights) and
publishes a report on each visit, almost always together with the facility's
or canton's/federal office's response in the SAME PDF (see _RESPONSE_WORDS
below — on the current site essentially every visit publishes one combined
"Bericht und Stellungnahme" / "Rapport et prise de position" / "Rapporto e
parere" file, not two separate ones; only 2 of the 178 documents on the
by-year page are a report with no response).

Pages (admin.ch sd-web; a different, slightly newer markup than
scrapers/practice/sdweb.py's download_items expects — a `<p
class="download-item__description">` sits between the `<h4>` title and the
`<p class="…meta-info">` size/date line, so this module parses the markup
itself rather than importing that helper):

  /de/berichte-nach-jahr      visit reports (+ authority response), 178 PDFs,
                               2010-; same DE/FR/IT page lists the OTHER
                               language's file with a disclaimer
                               ("dieses Dokument steht auf Deutsch nicht zur
                               Verfügung") when no local translation exists —
                               the <h4> title itself is always written in the
                               document's real language regardless of which
                               page lists it, which is what language
                               detection here keys on.
  /de/berichte-nach-kanton    the SAME visits grouped under <h3> canton-code
                               headings; fetched once, DE only, purely to
                               look up each PDF's canton for `topics` (not
                               re-scraped as its own set of documents — 174
                               of its 178 links are the by-year page's).
  /de/taetigkeitsberichte     annual activity reports, 2010-2025 (16).
  /de/stellungnahmen          the NKVF's OWN position papers to Parliament/
                               Federal Council/UN bodies on draft legislation
                               and country reports (24) — doc_type
                               'stellungnahme', but note this is a different
                               thing from a VISITED AUTHORITY's response: that
                               kind of Stellungnahme is embedded inside the
                               combined visit PDF above, not a separate row.
  /de/factsheets              2 short thematic factsheets.
  /de/studien-und-gutachten   15 PDFs, of which only 1 is the NKVF's own
                               commissioned Gutachten; the other 14 are a
                               third party's disclaimed quarterly literature
                               bulletin ("Die NKVF ist für den Inhalt nicht
                               verantwortlich") and are skipped.
  /de/schwerpunktberichte     descriptive text only, no reports published
                               there yet — nothing to scrape.
  /de/medienmitteilungen      press releases — not practice guidance, skipped.

FR/IT slugs differ per section (rapports-par-annee / rapporti-per-anno,
rapports-d-activite / rapporti-di-attivita, prises-de-position / pareri,
fiches-d-information / schede-informative, etudes-et-rapports /
studi-e-rapporti); see PAGES below.

doc_id is derived from the PDF's own filename (language-agnostic), so the
cross-language fallback case above collapses to one row for free, the same
way it does in scrapers/practice/strafvollzug.py.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from typing import Iterator
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .base import PracticeScraper, first_date_iso, month_year_year, slugify
from .langdedup import TextDedupMixin, body_lang

logger = logging.getLogger(__name__)

_BASE = "https://www.nkvf.admin.ch"

PAGES = {
    "visits": {"de": "/de/berichte-nach-jahr", "fr": "/fr/rapports-par-annee", "it": "/it/rapporti-per-anno"},
    "taetigkeitsberichte": {"de": "/de/taetigkeitsberichte", "fr": "/fr/rapports-d-activite",
                            "it": "/it/rapporti-di-attivita"},
    "stellungnahmen": {"de": "/de/stellungnahmen", "fr": "/fr/prises-de-position", "it": "/it/pareri"},
    "factsheets": {"de": "/de/factsheets", "fr": "/fr/fiches-d-information", "it": "/it/schede-informative"},
    "studien": {"de": "/de/studien-und-gutachten", "fr": "/fr/etudes-et-rapports", "it": "/it/studi-e-rapporti"},
}
_CANTON_LOOKUP_PAGE = "/de/berichte-nach-kanton"
_CANTON_CODES = {"AG", "AI", "AR", "BE", "BL", "BS", "FR", "GE", "GL", "GR", "JU", "LU", "NE", "NW",
                 "OW", "SG", "SH", "SO", "SZ", "TG", "TI", "UR", "VD", "VS", "ZG", "ZH"}


def _clean(s: str) -> str:
    return " ".join((s or "").split())


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def parse_download_items(html: str, base_url: str) -> list[dict]:
    """admin.ch sd-web anchors, the 2025/2026 markup: <a class="download-item">
    wraps an <h4 class="download-item__title">, a
    <p class="download-item__description"> (the document's own genre label —
    "Bericht und Stellungnahme", "Rapport et prise de position", a
    disclaimer when no local translation exists, ...) and a
    <p class="…meta-info…"> with PDF size + date spans."""
    soup = BeautifulSoup(html, "html.parser")
    out: list[dict] = []
    seen: set[str] = set()
    for a in soup.find_all("a", class_="download-item", href=True):
        href = a["href"]
        if "/dam/" not in href or not href.lower().endswith(".pdf"):
            continue
        pdf_url = urljoin(base_url, href)
        if pdf_url in seen:
            continue
        seen.add(pdf_url)
        h4 = a.find("h4")
        title = _clean(h4.get_text(" ", strip=True)) if h4 else _clean(a.get("aria-label", ""))
        desc = a.find("p", class_="download-item__description")
        description = _clean(desc.get_text(" ", strip=True)) if desc else ""
        meta_p = a.find("p", class_=re.compile("meta-info"))
        meta = _clean(meta_p.get_text(" ", strip=True)) if meta_p else ""
        out.append({"title": title, "description": description, "meta": meta, "pdf_url": pdf_url})
    return out


def canton_lookup(html: str) -> dict[str, str]:
    """pdf_url -> canton code, from the <h3>CODE</h3> headings on
    /de/berichte-nach-kanton (used only to enrich topics)."""
    soup = BeautifulSoup(html, "html.parser")
    out: dict[str, str] = {}
    current = ""
    for el in soup.find_all(["h3", "a"]):
        if el.name == "h3":
            text = _clean(el.get_text(" ", strip=True))
            current = text if text in _CANTON_CODES else ""
            continue
        if not current:
            continue
        href = el.get("href") or ""
        if href.lower().endswith(".pdf"):
            out[urljoin(_BASE, href)] = current
    return out


# ── language detection: the <h4> title's own vocabulary, independent of
# which language's page happened to list it ──
_LANG_MARKERS = {
    "de": re.compile(r"\bbesuch\b|\bim\b|\bam\b|\bund\b|\bder\b|\bdes\b|\bvon\b|\bfur\b|\bin\b", re.I),
    "fr": re.compile(r"\bvisite\b|\bdans\b|\ble\b|\bla\b|\bdu\b|\bet\b|\bau\b|\baux\b|\bavec\b|\bune\b", re.I),
    "it": re.compile(r"\bvisita\b|\bal\b|\bnel\b|\bdel\b|\bdella\b|\bdi\b|\bcon\b|\buna\b", re.I),
}


def detect_lang(title: str, fallback: str) -> str:
    t = _strip_accents(title)
    scores = {lang: len(rx.findall(t)) for lang, rx in _LANG_MARKERS.items()}
    best_lang, best_score = max(scores.items(), key=lambda kv: kv[1])
    return best_lang if best_score > 0 else fallback


# ── visit subject/doc_number ──
_VISIT_NOISE = re.compile(
    r"\b(besuch|visite|visita|der|des|im|in|am|und|nationalen?|kommission|zur|verhutung|von|"
    r"folter|nkvf|cnpt|cpt|le|les|la|du|de|dans|au|aux|et|une|avec|particuliere|attention|"
    r"portant|sur|il|al|nel|della|un|una|con|particolare|attenzione|mit|fokus|schwerpunkt|"
    r"sowie|der|am|bis|avec|ainsi|que|mesures?|contrainte|nach|vom)\b", re.I,
)
_YEAR = re.compile(r"\b(19|20)\d{2}\b")
_RESPONSE_WORDS = re.compile(r"stellungnahme|prise de position|parere", re.I)


def _subject(title: str, max_words: int = 6) -> str:
    t = _strip_accents(title.lower())
    t = re.sub(r"[,;:()–‑-]", " ", t)
    t = _VISIT_NOISE.sub(" ", t)
    t = _YEAR.sub(" ", t)
    words = [w for w in re.findall(r"[a-z]+", t) if len(w) > 2]
    seen: set[str] = set()
    out = []
    for w in words:
        if w in seen:
            continue
        seen.add(w)
        out.append(w)
        if len(out) >= max_words:
            break
    return " ".join(out)


def visit_doc_number(title: str) -> str:
    years = re.findall(r"\b(?:19|20)\d{2}\b", title)
    year = years[-1] if years else ""
    subject = _subject(title) or slugify(title)[:40]
    return f"NKVF Bericht {subject} {year}".strip()


# ── Stellungnahmen (the NKVF's own position papers) ──
_STN_LEADIN = re.compile(
    r"^(stellungnahme|prise de position|parere)\s+(der|de la|del(la)?)\s+"
    r"(nationalen?\s+kommission[^.]*?folter|commission\s+nationale[^.]*?torture|"
    r"commissione\s+nazionale[^.]*?tortura)\s*(\(n?kvf\)|\(cnpt\))?\s*",
    re.I,
)
_STN_CONNECT = re.compile(r"^(zur|zum|au|aux?|a|alla|al)\s+", re.I)


def stellungnahme_subject(title: str) -> str:
    rest = _STN_LEADIN.sub("", title).strip()
    rest = _STN_CONNECT.sub("", rest).strip()
    return rest or title


_UPDATE_BULLETIN = re.compile(r"quartal|update\s+freiheitsentzug|trimestre|trimestrale", re.I)


class NkvfScraper(TextDedupMixin, PracticeScraper):
    SOURCE_KEY = "nkvf"
    ISSUING_AUTHORITY = "NKVF"
    DEFAULT_DOC_TYPE = "visit_report"
    REQUEST_DELAY = 1.0
    OCR_SCANS = True
    NO_TEXT_LAYER_BODY = "[Textlayer fehlt: gescanntes PDF]"

    def _make_doc_id(self, stub: dict) -> str:
        return f"{self.SOURCE_KEY}_{stub['nk_id']}"

    def _normalize(self, stub: dict, body_text: str) -> dict:
        doc = super()._normalize(stub, body_text)
        if not doc["date"]:
            # titles with no day-precision date (English Stellungnahmen,
            # "August – Dezember 2025" ranges) fall back to the document's
            # own text.
            doc["date"] = first_date_iso(body_text[:3000]) or month_year_year(body_text[:3000])
        return doc

    def _page(self, path: str) -> str | None:
        try:
            r = self.get(_BASE + path)
            r.raise_for_status()
            return r.text
        except Exception as e:
            logger.warning("[nkvf] %s fetch failed: %s", path, e)
            return None

    def discover_documents(self) -> Iterator[dict]:
        yield from self._visits()
        yield from self._simple_series("taetigkeitsberichte", self._taetigkeitsbericht_stub)
        yield from self._simple_series("stellungnahmen", self._stellungnahme_stub)
        yield from self._simple_series("factsheets", self._factsheet_stub)
        yield from self._simple_series("studien", self._studie_stub)

    # ── visit reports ──
    def _visits(self) -> Iterator[dict]:
        canton_html = self._page(_CANTON_LOOKUP_PAGE)
        cantons = canton_lookup(canton_html) if canton_html else {}
        for lang, path in PAGES["visits"].items():
            html = self._page(path)
            if html is None:
                continue
            items = parse_download_items(html, _BASE)
            logger.info("[nkvf] visits %s: %d PDFs", lang, len(items))
            for it in items:
                real_lang = detect_lang(it["title"], lang)
                number = visit_doc_number(it["title"])
                topics = ["NKVF", "Freiheitsentzug", number]
                canton = cantons.get(it["pdf_url"])
                if canton:
                    topics.append(canton)
                if _RESPONSE_WORDS.search(it["description"]):
                    topics.append("Stellungnahme der Behörde enthalten")
                date = first_date_iso(it["title"]) or month_year_year(it["title"]) or ""
                stub = {
                    "pdf_url": it["pdf_url"],
                    "url": _BASE + path,
                    "title": it["title"],
                    "doc_number": number,
                    "date": date,
                    "language": real_lang,
                    "doc_type": "visit_report",
                    "topics": topics,
                    "nk_id": f"visit_{slugify(it['pdf_url'].rsplit('/', 1)[-1])[:60]}",
                }
                out = self.with_body(stub, number)
                if out:
                    # a French-only report also sits on the German page
                    lang = body_lang(out.get("body_text", ""))
                    if lang and lang != out["language"]:
                        out = {**out, "language": lang}
                    yield out

    # ── the other, simpler series ──
    def _simple_series(self, key: str, make_stub) -> Iterator[dict]:
        for lang, path in PAGES[key].items():
            html = self._page(path)
            if html is None:
                continue
            items = parse_download_items(html, _BASE)
            logger.info("[nkvf] %s %s: %d PDFs", key, lang, len(items))
            for it in items:
                stub = make_stub(it, lang, path)
                if stub is None:
                    continue
                out = self.with_body(stub, stub["doc_number"])
                if out:
                    yield out

    def _taetigkeitsbericht_stub(self, it: dict, lang: str, path: str) -> dict | None:
        years = re.findall(r"\b(?:19|20)\d{2}\b", it["title"])
        year = years[-1] if years else ""
        number = f"NKVF Tätigkeitsbericht {year}".strip()
        real_lang = detect_lang(it["title"], lang)
        return {
            "pdf_url": it["pdf_url"], "url": _BASE + path, "title": it["title"],
            "doc_number": number, "date": year, "language": real_lang,
            "doc_type": "bericht", "topics": ["NKVF", number, "Tätigkeitsbericht"],
            "nk_id": f"tb_{slugify(it['pdf_url'].rsplit('/', 1)[-1])[:60]}",
        }

    def _stellungnahme_stub(self, it: dict, lang: str, path: str) -> dict | None:
        subject = stellungnahme_subject(it["title"])
        number = f"NKVF Stellungnahme {subject}"[:120]
        real_lang = detect_lang(it["title"], lang)
        date = first_date_iso(it["title"]) or month_year_year(it["title"]) or ""
        return {
            "pdf_url": it["pdf_url"], "url": _BASE + path, "title": it["title"],
            "doc_number": number, "date": date, "language": real_lang,
            "doc_type": "stellungnahme", "topics": ["NKVF", number],
            "nk_id": f"stn_{slugify(it['pdf_url'].rsplit('/', 1)[-1])[:60]}",
        }

    def _factsheet_stub(self, it: dict, lang: str, path: str) -> dict | None:
        number = f"NKVF Factsheet {it['title']}"[:120]
        real_lang = detect_lang(it["title"], lang)
        date = first_date_iso(it["meta"]) or month_year_year(it["meta"]) or ""
        return {
            "pdf_url": it["pdf_url"], "url": _BASE + path, "title": it["title"],
            "doc_number": number, "date": date, "language": real_lang,
            "doc_type": "merkblatt", "topics": ["NKVF", number, "Freiheitsentzug"],
            "nk_id": f"fs_{slugify(it['pdf_url'].rsplit('/', 1)[-1])[:60]}",
        }

    def _studie_stub(self, it: dict, lang: str, path: str) -> dict | None:
        # Keep the commissioned Gutachten; skip the quarterly "Update
        # Freiheitsentzug" literature bulletin. Both carry the SAME "Die
        # NKVF ist für den Inhalt nicht verantwortlich" disclaimer (every
        # item on this page is third-party-authored), so the disclaimer
        # itself cannot be the filter — only the bulletin's own title is.
        if _UPDATE_BULLETIN.search(it["title"]):
            return None
        number = f"NKVF Bericht {_subject(it['title']) or it['title']}"[:120]
        real_lang = detect_lang(it["title"], lang)
        date = (first_date_iso(it["title"]) or month_year_year(it["title"])
                or first_date_iso(it["meta"]) or month_year_year(it["meta"]) or "")
        return {
            "pdf_url": it["pdf_url"], "url": _BASE + path, "title": it["title"],
            "doc_number": number, "date": date,
            "language": real_lang, "doc_type": "bericht", "topics": ["NKVF", number, "Freiheitsentzug"],
            "nk_id": f"st_{slugify(it['pdf_url'].rsplit('/', 1)[-1])[:60]}",
        }
