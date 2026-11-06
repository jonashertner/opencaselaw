"""
Stiftungsaufsicht — the supervisors of classic (non-pension) foundations
========================================================================

Foundation law (Art. 80-89a ZGB) is applied by the supervisory authorities,
and what they publish is the practice: how a foundation board must report,
when a deed may be amended (Art. 85/86/86b ZGB), how a foundation merges
under the FusG or is dissolved, what a model deed looks like, what board
pay is acceptable. Nothing of it was in the corpus before 2026-11.

  ESA    Eidgenössische Stiftungsaufsicht (foundations with a national or
         international purpose): the FAQ (~115 questions, one row each,
         DE/FR/IT), the model statutes and compensation regulations, the
         annual reviews (DE/EN).
  BBSA   BVG- und Stiftungsaufsicht des Kantons Bern: the yearly Rundschreiben
         to classic foundations 2013- and its Merkblätter (DE/FR).
  BSABB  BVG- und Stiftungsaufsicht beider Basel: Merkblätter, model deeds and
         regulations, checklists, information letters, a 2019 legal opinion,
         the annual report (DE). Forms, fee notices, customer surveys and the
         governing statutes are left out.

issuing_authority is the body ("ESA", "BBSA", "BSABB"). Word templates
(.docx) are not text-extracted by the practice pipeline and are skipped.
The regional supervisors of Central Switzerland (ZBSA), Western Switzerland
(As-So) and Eastern Switzerland (ATIOZ, formerly OSTA) publish no listable
documents as of 2026-11.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Iterator
from urllib.parse import unquote, urljoin

from bs4 import BeautifulSoup

from .base import PracticeScraper, first_date_iso, sha256_hex, slugify
from .langdedup import TextDedupMixin
from .sdweb import download_items

logger = logging.getLogger(__name__)

ESA = "https://www.esa.admin.ch"
BBSA = "https://www.aufsichtbern.ch"
BSABB = "https://www.bsabb.ch"


@dataclass(frozen=True)
class Page:
    authority: str
    url: str
    lang: str
    kind: str                 # "faq" | "sdweb" | "links"
    include: str = ""         # regex on anchor text (links pages)
    exclude: str = ""         # regex on anchor text (links pages)


PAGES = (
    Page("ESA", f"{ESA}/de/fragen-und-antworten", "de", "faq"),
    Page("ESA", f"{ESA}/fr/questions-et-reponses", "fr", "faq"),
    Page("ESA", f"{ESA}/it/domande-e-risposte", "it", "faq"),
    Page("ESA", f"{ESA}/de/mustervorlagen", "de", "links"),
    Page("ESA", f"{ESA}/fr/modeles", "fr", "links"),
    Page("ESA", f"{ESA}/it/modelli", "it", "links"),
    Page("ESA", f"{ESA}/de/jahresberichte-esa", "de", "sdweb"),
    Page("BBSA", f"{BBSA}/de/klassische-stiftungen/rundschreiben", "de", "links"),
    Page("BBSA", f"{BBSA}/fr/fondations-classiques/circulaires", "fr", "links"),
    Page("BBSA", f"{BBSA}/de/klassische-stiftungen/merkblaetter-formulare", "de", "links",
         include=r"Merkbl|Merblatt|Muster", exclude=r"Gesuch|Bestätigung|Bestaetigung"),
    Page("BBSA", f"{BBSA}/fr/fondations-classiques/mementos-formulaires", "fr", "links",
         include=r"M[ée]mento|Mod[èe]le|Notice", exclude=r"Demande|Confirmation|Requ[êe]te"),
    Page("BSABB", f"{BSABB}/publikationen", "de", "links",
         include=r"Merkblatt|Muster|Checkliste|Rechtsgutachten|Informations?(?:schreiben|rundschreiben)"
                 r"|Mitteilung Konferenz|Positionspapier|Geschäftsbericht|Verfahrensordnung",
         exclude=r"Formular|Fristerstreckung|Bestätigung|Gebühr|Kundenbefragung|Medienmitteilung"),
)

_FILE_TITLE_FROM_URL = re.compile(r"[-_]+")


def _clean(s: str) -> str:
    return " ".join((s or "").split())


def _doc_type(title: str) -> str:
    t = title.lower()
    if re.search(r"rundschreiben|circulaire|informations?(schreiben|rundschreiben)", t):
        return "rundschreiben"
    if re.search(r"muster|mod[èe]le|modell|checkliste", t):
        return "formular"
    if re.search(r"geschäftsbericht|jahresrückblick|review|rapport|rapporto|rechtsgutachten", t):
        return "bericht"
    if re.search(r"positionspapier|mitteilung|verfahrensordnung", t):
        return "mitteilung"
    return "merkblatt"


def parse_faq(html: str) -> list[dict]:
    """One dict per question: {section, question, answer}."""
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for li in soup.select("li.accordion__item"):
        q = li.select_one(".accordion__title")
        a = li.select_one(".accordion__content")
        if not q or not a:
            continue
        sec = li.find_previous("h2")
        out.append({"section": _clean(sec.get_text(" ", strip=True)) if sec else "",
                    "question": _clean(q.get_text(" ", strip=True)),
                    "answer": _clean(a.get_text(" ", strip=True))})
    return out


def parse_links(html: str, base: str, include: str = "", exclude: str = "") -> list[dict]:
    """PDF anchors: {title, url, section}. The anchor text is the title; an
    empty anchor (BBSA's download buttons) falls back to the file name."""
    soup = BeautifulSoup(html, "html.parser")
    inc = re.compile(include, re.I) if include else None
    exc = re.compile(exclude, re.I) if exclude else None
    out, seen = [], set()
    for a in soup.find_all("a", href=True):
        url = urljoin(base, a["href"])
        if not url.lower().split("?")[0].endswith(".pdf") and "fileservice" not in url:
            continue
        if url in seen:
            continue
        title = _clean(a.get_text(" ", strip=True))
        if not title or title.lower() in ("pdf", "download", "herunterladen", "télécharger"):
            stem = unquote(url.split("?")[0].rsplit("/", 1)[-1]).rsplit(".", 1)[0]
            title = _clean(_FILE_TITLE_FROM_URL.sub(" ", stem))
        if inc and not inc.search(title):
            continue
        if exc and exc.search(title):
            continue
        seen.add(url)
        head = a.find_previous(["h2", "h3"])
        out.append({"title": title, "url": url, "section": _clean(head.get_text(" ", strip=True)) if head else ""})
    return out


class StiftungsaufsichtScraper(TextDedupMixin, PracticeScraper):
    SOURCE_KEY = "stiftungsaufsicht"
    ISSUING_AUTHORITY = "ESA"            # overridden per row
    DEFAULT_DOC_TYPE = "merkblatt"
    # FAQ answers are edited in place under the same question: the hash of
    # the text is the revision. PDFs use their URL.
    REVISION_FIELD = "rev"
    REQUEST_DELAY = 1.5
    OCR_SCANS = True
    NO_TEXT_LAYER_BODY = "[Textlayer fehlt: gescanntes PDF]"

    def _make_doc_id(self, stub: dict) -> str:
        return f"{self.SOURCE_KEY}_{stub['sa_id']}"

    def _normalize(self, stub: dict, body_text: str) -> dict:
        doc = super()._normalize(stub, body_text)
        doc["issuing_authority"] = stub["authority"]
        if not doc["date"] and doc["doc_type"] != "faq":
            doc["date"] = first_date_iso(body_text[:2500])
        return doc

    def _page(self, url: str) -> str | None:
        try:
            r = self.get(url)
            r.raise_for_status()
            return r.text
        except Exception as e:
            logger.warning("[%s] %s fetch failed: %s", self.SOURCE_KEY, url, e)
            return None

    def discover_documents(self) -> Iterator[dict]:
        for page in PAGES:
            html = self._page(page.url)
            if html is None:
                continue
            if page.kind == "faq":
                yield from self._faq(page, html)
                continue
            if page.kind == "sdweb":
                items = [{"title": i["title"], "url": i["pdf_url"], "section": i["section"]}
                         for i in download_items(html, ESA)]
            else:
                items = parse_links(html, page.url, page.include, page.exclude)
            logger.info("[%s] %s: %d documents", self.SOURCE_KEY, page.url, len(items))
            for it in items:
                title = it["title"]
                lang = page.lang
                if page.kind == "sdweb" and re.search(r"\bReview\b|FSAF", title):
                    lang = "en"
                key = slugify(unquote(it["url"].split("?")[0].rsplit("/", 1)[-1]))[:60]
                number = f"{page.authority} {title}"
                stub = {
                    "pdf_url": it["url"],
                    "url": page.url,
                    "title": title,
                    "doc_number": number,
                    "date": first_date_iso(title) or (re.search(r"(20\d{2})", title).group(1)
                                                        if re.search(r"(20\d{2})", title) else ""),
                    "language": lang,
                    "doc_type": _doc_type(title),
                    "topics": [t for t in (page.authority, it["section"], "Stiftungsaufsicht") if t],
                    "authority": page.authority,
                    "sa_id": f"{slugify(page.authority)}_{key}_{lang}",
                    "rev": it["url"],
                }
                out = self.with_body(stub, f"{page.authority} {key}")
                if out:
                    yield out

    def _faq(self, page: Page, html: str) -> Iterator[dict]:
        rows = parse_faq(html)
        logger.info("[%s] %s: %d FAQ entries", self.SOURCE_KEY, page.url, len(rows))
        for i, r in enumerate(rows, 1):
            body = f"{r['question']}\n\n{r['answer']}"
            yield {
                "pdf_url": "",
                "url": page.url,
                "title": f"{r['question']} ({r['section']})" if r["section"] else r["question"],
                # one number per question; the language versions are separate
                # pages with their own wording, so the number is per language
                "doc_number": f"ESA FAQ {page.lang.upper()} {sha256_hex(r['question'])[:8]}",
                "date": "",
                "language": page.lang,
                "doc_type": "faq",
                "topics": ["ESA", "FAQ", r["section"]] if r["section"] else ["ESA", "FAQ"],
                "authority": "ESA",
                "body_text": body,
                "sa_id": f"esa_faq_{page.lang}_{sha256_hex(r['question'])[:12]}",
                "rev": sha256_hex(body)[:16],
            }
