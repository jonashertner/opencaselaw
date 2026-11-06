"""
Übernahmekommission (UEK / COPA / TOB) — Rundschreiben, Mitteilungen, Tätigkeitsberichte
=======================================================================================

The Takeover Board's own practice texts, beside its rulings (court 'uek' in
the decision corpus):

  * UEK-Rundschreiben Nr. 1-5 (Rückkaufprogramme, Liquidität, Prüfung von
    öffentlichen Kaufangeboten, Zustellung an die Medien, Sanierungsausnahme)
    with every revision the Board still publishes, the repealed ones
    (Rundschreiben Nr. 1 of 1997, Nr. 4 of 2009 on exchange offers) and the
    older Mitteilungen Nr. 1-4 they replaced;
  * the consultation drafts ("Vernehmlassungen und Anhörungen"), the
    Board's newsletters announcing a circular, the expert report behind
    Rundschreiben Nr. 2 and the conflict-of-interest Richtlinie;
  * the annual Tätigkeitsberichte (2009-), which summarise the year's
    practice.

Not taken: the "Erlasse" (BEHG, FinfraG, UEV, R-UEK — statutes, in the
legislation corpus) and the auditing standard PS 880, which is EXPERTsuisse's
text the Board only hosts.

Site (takeover.ch, plain PHP):

  /legaltexts                 one page; <h2> current / consultations / repealed,
                              <h3> Erlasse / Rundschreiben und Mitteilungen / Andere,
                              <article class="legaltext" id="legaltextN"> heads a
                              document, <article class="legaltextcontent parentP"
                              id="legaltextcontentN"> are its versions and annexes
  /legaltexts/detail/id/N     one text in HTML, "PDF Version" link
  /download/legaltexts/nr/N/lang/{de,fr,it,en}   the PDF
  /takeoverboard/report       the Tätigkeitsberichte (/public/data/...pdf)
  .../language/{xx}           sets the session language for the next pages

A translation that does not exist is NOT a 404: the download URL serves the
German file under a translated file name. Every non-German row is therefore
compared with the German bytes and dropped when they are identical (two
1997/2000 texts are single bilingual files, which this also handles).

Identity: doc_number is the circular's number ("UEK-Rundschreiben Nr. 3"),
shared by all its revisions so search_practice collapses to the newest and
include_superseded lists the history. The two repealed circulars whose
number was later reused for a different subject carry their year
("UEK-Rundschreiben Nr. 1 (1997)", "UEK-Rundschreiben Nr. 4 (2009)").
"""
from __future__ import annotations

import logging
import re
from typing import Iterator
from urllib.parse import unquote, urljoin

from bs4 import BeautifulSoup

from .base import PracticeScraper, first_date_iso, slugify
from .finma_rundschreiben import _STATUS_IN_FORCE, _STATUS_SUPERSEDED
from .langdedup import TextDedupMixin

logger = logging.getLogger(__name__)

BASE = "https://www.takeover.ch"
LANGS = ("de", "fr", "it", "en")

# <h3> sections that hold statutes, in all four languages.
_STATUTES = re.compile(r"^(Erlasse|Lois|Leggi|Acts|Laws|Legislation|Ordinanze)", re.I)
# Private auditing standard hosted by the Board (PS 880 and its draft).
_SKIP_TITLE = re.compile(r"\bPS\s*880\b", re.I)
_RS_NUMBER = re.compile(
    r"(?:Rundschreiben|Circulaire|Circolare|Circular)\s+(?:UEK\s+|COPA\s+|TOB\s+)?(?:Nr\.?|n[o°º]\.?|no\.?)?\s*(\d)\b"
    r"|(?:UEK|COPA|TOB)[-\s]+(?:Rundschreiben|Circulaire|Circolare|Circular)\s+(?:Nr\.?|n[o°º]\.?|no\.?)?\s*(\d)\b",
    re.I)
_MITT_NUMBER = re.compile(r"(?:Mitteilung|Communication|Comunicazione|Communicato|Communication)\s+(?:Nr\.?|n[o°º]\.?|no\.?)?\s*(\d)\b", re.I)
_YEAR = re.compile(r"\b(19[89]\d|20[0-4]\d)\b")
# Repealed circulars whose number now belongs to a different subject.
_NUMBER_OVERRIDE = {241: "UEK-Rundschreiben Nr. 1 (1997)", 222: "UEK-Rundschreiben Nr. 4 (2009)"}
_NEWSLETTER = re.compile(r"Newsletter|neues UEK-Rundschreiben|nouvelle circulaire|nuova circolare|new TOB circular"
                         r"|Neues UEK-Rundschreiben|^UEK Rundschreiben Nr\. \d\s*$", re.I)
_DRAFT = re.compile(r"Anhörung|Entwurf|Vernehmlassung|consultation|projet|audizione|progetto|draft", re.I)
_REPORT = re.compile(r"Expertenbericht|Rapport d.experts|Rapporto|Expert report", re.I)
_DIRECTIVE = re.compile(r"Richtlinie|Directive|Direttiva|Guideline", re.I)


def _clean(s: str) -> str:
    return " ".join((s or "").split())


def parse_legaltexts(html: str) -> list[dict]:
    """Every non-statute item on /legaltexts, in page order:
    {nr, parent_nr, title, parent_title, section (0 current, 1 consultation,
    2 repealed), group (h3 text), detail (bool)}, one per distinct nr. A head
    article (the document) and its first version usually share the nr; the
    version's own label ("Revision 2015 - Stand 1. Januar 2016") is kept in
    `title`, the document's name in `parent_title`."""
    soup = BeautifulSoup(html, "html.parser")
    out: dict[int, dict] = {}
    heads: dict[str, dict] = {}                 # toggle class -> head item
    head_items: list[dict] = []
    h2_index, group = -1, ""
    for el in soup.find_all(["h2", "h3", "article"]):
        if el.name == "h2":
            h2_index += 1
            continue
        if el.name == "h3":
            group = _clean(el.get_text(" ", strip=True))
            continue
        if _STATUTES.match(group):
            continue
        a = el.find("a", href=True)
        h4 = el.find("h4")
        if not a or not h4:
            continue
        m = re.search(r"/(?:detail/id|download/legaltexts/nr)/(\d+)", a["href"])
        if not m:
            continue
        item = {
            "nr": int(m.group(1)), "title": _clean(h4.get_text(" ", strip=True)),
            "section": h2_index, "group": group, "detail": "/detail/id/" in a["href"],
        }
        classes = el.get("class") or []
        if "legaltext" in classes:
            toggle = el.find("a", attrs={"data-toggleclass": True})
            if toggle:
                heads[toggle["data-toggleclass"]] = item
            head_items.append(item)
            continue
        head = next((heads[c] for c in classes if c in heads), None)
        if head is None and head_items and head_items[-1]["nr"] == item["nr"]:
            head = head_items[-1]                # repealed documents have no toggle
        item["parent_nr"] = head["nr"] if head else item["nr"]
        item["parent_title"] = head["title"] if head else item["title"]
        out.setdefault(item["nr"], item)
    for head in head_items:                     # a document listed without versions
        if head["nr"] not in out:
            out[head["nr"]] = {**head, "parent_nr": head["nr"], "parent_title": head["title"]}
    return [i for i in out.values()
            if not (_SKIP_TITLE.search(i["title"]) or _SKIP_TITLE.search(i["parent_title"]))]


def _doc_number(item: dict) -> str:
    if item["nr"] in _NUMBER_OVERRIDE:
        return _NUMBER_OVERRIDE[item["nr"]]
    if item["parent_nr"] in _NUMBER_OVERRIDE:
        return _NUMBER_OVERRIDE[item["parent_nr"]]
    for text in (item["parent_title"], item["title"]):
        m = _RS_NUMBER.search(text)
        if m:
            return f"UEK-Rundschreiben Nr. {m.group(1) or m.group(2)}"
        m = _MITT_NUMBER.search(text)
        if m:
            return f"UEK-Mitteilung Nr. {m.group(1)}"
    return ""


def classify(item: dict) -> tuple[str, str]:
    """(doc_type, doc_number) of an item. Items that are not a version of the
    circular itself (newsletter, expert report, consultation) get their own
    doc_number so the per-number collapse never hides the circular behind
    them."""
    title, nr = item["title"], item["nr"]
    base_number = _doc_number(item)
    if item["section"] == 1 or _DRAFT.search(title):
        return "entwurf", f"{base_number or 'UEK'} Entwurf {nr}".strip()
    if _REPORT.search(title):
        return "bericht", f"{base_number or 'UEK'} Bericht {nr}".strip()
    if _NEWSLETTER.search(title):
        return "mitteilung", f"UEK Newsletter {nr}"
    if _DIRECTIVE.search(item["parent_title"]) and not base_number:
        return "richtlinie", "UEK Richtlinie Interessenkonflikte"
    if base_number.startswith("UEK-Mitteilung"):
        return "mitteilung", base_number
    if base_number:
        return "rundschreiben", base_number
    return "mitteilung", f"UEK {nr}"


_REPORT_YEAR = re.compile(r"(20\d{2}|19\d{2})")


def parse_reports(html: str) -> list[dict]:
    """Tätigkeitsberichte on /takeoverboard/report: [{year, pdf_url}]."""
    soup = BeautifulSoup(html, "html.parser")
    out, seen = [], set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/public/data/" not in href or not href.lower().endswith(".pdf"):
            continue
        url = urljoin(BASE, href)
        if url in seen:
            continue
        seen.add(url)
        m = _REPORT_YEAR.search(unquote(href.rsplit("/", 1)[-1]))
        if m:
            out.append({"year": m.group(1), "pdf_url": url})
    return out


class UekRundschreibenScraper(TextDedupMixin, PracticeScraper):
    SOURCE_KEY = "uek_rs"
    ISSUING_AUTHORITY = "UEK"
    DEFAULT_DOC_TYPE = "rundschreiben"
    REQUEST_DELAY = 1.5
    OCR_SCANS = True
    NO_TEXT_LAYER_BODY = "[Textlayer fehlt: gescanntes PDF]"

    def _make_doc_id(self, stub: dict) -> str:
        return f"{self.SOURCE_KEY}_{stub['uek_id']}"

    def _page(self, url: str) -> str | None:
        try:
            r = self.get(url)
            r.raise_for_status()
            return r.text
        except Exception as e:
            logger.warning("[%s] %s fetch failed: %s", self.SOURCE_KEY, url, e)
            return None

    def _set_language(self, path: str, lang: str) -> bool:
        return self._page(f"{BASE}{path}/language/{lang}") is not None

    def discover_documents(self) -> Iterator[dict]:
        yield from self._legaltexts()
        yield from self._reports()

    def _legaltexts(self) -> Iterator[dict]:
        # Classification, number and date come from the German listing: the
        # other languages' titles are translations (FR "Norme d'audit" for
        # PS 880, a newsletter titled "Circulaire COPA n° 1") and two FR
        # titles carry the wrong date. A translation only contributes its
        # title and its file.
        meta: dict[int, dict] = {}
        for lang in LANGS:
            if not self._set_language("/legaltexts", lang):
                continue
            html = self._page(f"{BASE}/legaltexts")
            if html is None:
                continue
            items = parse_legaltexts(html)
            logger.info("[%s] %s: %d legal-text items", self.SOURCE_KEY, lang, len(items))
            if lang == "de":
                for item in items:
                    meta[item["nr"]] = {**item, "date": self._item_date(item)}
            for item in items:
                m = meta.get(item["nr"])
                if m is None:
                    continue          # not on the German page (e.g. PS 880 drafts)
                doc_type, number = classify(m)
                title = item["title"]
                if item["parent_title"] and item["parent_title"] not in title:
                    title = f"{item['parent_title']} | {title}"
                # In the current section the version sharing the document's
                # nr is the one in force; the older revisions listed under it
                # are superseded, as is everything in the repealed section.
                current = m["section"] == 0 and m["nr"] == m["parent_nr"]
                status = _STATUS_IN_FORCE if current else _STATUS_SUPERSEDED
                topics = [t for t in (number, m["group"]) if t]
                if doc_type != "entwurf":
                    topics += list(status)
                stub = {
                    "pdf_url": f"{BASE}/download/legaltexts/nr/{item['nr']}/lang/{lang}",
                    "url": f"{BASE}/legaltexts/detail/id/{item['nr']}" if m["detail"] else f"{BASE}/legaltexts",
                    "title": title,
                    "doc_number": number,
                    "date": m["date"],
                    "language": lang,
                    "doc_type": doc_type,
                    "topics": topics,
                    "uek_id": f"{item['nr']}_{lang}",
                }
                # Grouped by document number, so a version listed twice under two
                # ids (the conflict-of-interest Richtlinie) is taken once too.
                out = self.with_body(stub, number)
                if out:
                    yield out

    def _item_date(self, item: dict) -> str:
        """The version's date: from its own label, else from the German
        detail page (the text under the title opens with or announces its
        date), else from the document's title."""
        date = first_date_iso(item["title"])
        if date:
            return date
        if item["detail"]:
            page = self._page(f"{BASE}/legaltexts/detail/id/{item['nr']}")
            if page:
                soup = BeautifulSoup(page, "html.parser")
                h1 = soup.find("h1")
                text = _clean((h1.find_parent("div", id="wrapper") or soup).get_text(" ", strip=True)) if h1 else ""
                start = text.find(_clean(h1.get_text(" ", strip=True))) if h1 else -1
                date = first_date_iso(text[max(start, 0):][:1500])
        return date or first_date_iso(item["parent_title"])

    def _reports(self) -> Iterator[dict]:
        for lang in LANGS:
            if not self._set_language("/takeoverboard/report", lang):
                continue
            html = self._page(f"{BASE}/takeoverboard/report")
            if html is None:
                continue
            reports = parse_reports(html)
            logger.info("[%s] %s: %d Tätigkeitsberichte", self.SOURCE_KEY, lang, len(reports))
            for rep in reports:
                year, url = rep["year"], rep["pdf_url"]
                stub = {
                    "pdf_url": url,
                    "url": f"{BASE}/takeoverboard/report",
                    "title": {"de": f"Tätigkeitsbericht der Übernahmekommission {year}",
                              "fr": f"Rapport d'activité de la Commission des OPA {year}",
                              "it": f"Rapporto d'attività della Commissione delle OPA {year}",
                              "en": f"Annual report of the Swiss Takeover Board {year}"}[lang],
                    "doc_number": f"UEK Tätigkeitsbericht {year}",
                    "date": year,
                    "language": lang,
                    "doc_type": "bericht",
                    "topics": [f"UEK Tätigkeitsbericht {year}", "Tätigkeitsbericht"],
                    "uek_id": f"bericht_{year}_{lang}",
                }
                # The FR/IT/EN pages link the German (or French) file where
                # no translation exists; with_body drops those copies.
                out = self.with_body(stub, f"bericht {year}")
                if out:
                    yield out
