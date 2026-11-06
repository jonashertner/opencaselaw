"""
RAB / ASR — Eidgenössische Revisionsaufsichtsbehörde: Rundschreiben, Aufsichtsmitteilungen, Tätigkeitsberichte
=============================================================================================================

The Federal Audit Oversight Authority supervises audit firms of public
companies and licenses auditors (RAG). Its circulars say how it applies the
RAG and RAV: what an application for admission must contain (RS 1/2007), which
auditing standards it recognises (RS 1/2008), the comprehensive report to the
board (RS 1/2009), reporting by state-supervised audit firms (RS 1/2010),
internal quality control (RS 1/2014), key audit matters (RS 1/2015, repealed),
and RS 1/2022. Its enforcement rulings are in the decision corpus (court
'rab'); the circulars were not in the corpus before 2026-11.

Pages (Drupal, one page each, no pagination, every language on the DE page):

  /de/rundschreiben        one tile per circular: subject, "RS 01/2007", the
                           date of the version in force and its DE/FR/IT/EN
                           files, then the history ("Vorversion", "Erstversion",
                           "Vorversion mit Anzeige der Änderungen"), each with
                           <time datetime> and its files
  /de/aufsichtsmitteilungen  Aufsichtsmitteilung 1-2022 (practice notes on fraud risk)
  /de/geschaeftsberichte   Tätigkeitsberichte 2006/07 onwards

Rows: one per (circular, version, language). The redline versions ("mit
Anzeige der Änderungen") are left out — each is the next version's full text
with markup, so it would only duplicate that version. doc_number
"RAB-RS 1/2007" is shared by all versions; search_practice collapses to the
newest, include_superseded gives the history.
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

BASE = "https://www.rab-asr.ch"
RS_URL = f"{BASE}/de/rundschreiben"
NOTICES_URL = f"{BASE}/de/aufsichtsmitteilungen"
REPORTS_URL = f"{BASE}/de/geschaeftsberichte"

_LANGS = {"de", "fr", "it", "en"}
_RS_LABEL = re.compile(r"RS\s*0?(\d+)\s*/\s*(\d{4})")
_REDLINE = re.compile(r"mit\s+Anzeige\s+der\s+Änderungen|avec\s+modifications|con\s+modifiche|marked", re.I)
_REPEALED = re.compile(r"\(aufgehoben\)|aufgehoben", re.I)
_NOTICE = re.compile(r"Aufsichtsmitteilung\s+(\d+)\s*[-/]\s*(\d{4})", re.I)
_YEAR = re.compile(r"(20\d{2}|19\d{2})(?:\s*/\s*(20\d{2}|\d{2}))?")


def _clean(s: str) -> str:
    return " ".join((s or "").split())


def _files(tile) -> list[tuple[str, str]]:
    """(language, absolute url) of the tile's OWN download list — not those of
    tiles nested in its history."""
    ul = tile.find("ul", class_="rab-download-tile__items")
    if ul is None:
        return []
    out = []
    for a in ul.find_all("a", href=True):
        lang = _clean(a.get_text()).lower()
        if lang in _LANGS and a["href"].lower().endswith(".pdf"):
            out.append((lang, urljoin(BASE, a["href"])))
    return out


def _time(tile) -> str:
    t = tile.find("time")
    if t and t.get("datetime"):
        return t["datetime"][:10]
    return first_date_iso(_clean(t.get_text())) if t else ""


def parse_circulars(html: str) -> list[dict]:
    """One dict per (circular, version): {number, subject, label, date,
    current (bool), repealed (bool), files [(lang, url)]}."""
    soup = BeautifulSoup(html, "html.parser")
    out: list[dict] = []
    for tile in soup.select("div.rab-circulation"):
        label_el = tile.select_one("p.circulation-label")
        m = _RS_LABEL.search(_clean(label_el.get_text()) if label_el else "")
        if not m:
            continue
        number = f"RAB-RS {int(m.group(1))}/{m.group(2)}"
        desc = tile.select_one(".circulation-desc")
        subject = _clean(desc.get_text(" ", strip=True)) if desc else ""
        repealed = bool(_REPEALED.search(subject))
        info = tile.find(class_="rab-download-tile__info-extra")
        out.append({
            "number": number, "subject": subject, "label": "aufgehoben" if repealed else "geltende Fassung",
            "date": _time(info) if info else "", "current": True, "repealed": repealed,
            "files": _files(tile),
        })
        extra = tile.find(class_="rab-download-tile__extra")
        for sub in (extra.select("div.rab-download-tile") if extra else []):
            lab = sub.select_one("p.circulation-label")
            label = _clean(lab.get_text()) if lab else ""
            if _REDLINE.search(label):
                continue
            out.append({
                "number": number, "subject": subject, "label": label, "date": _time(sub),
                "current": False, "repealed": repealed, "files": _files(sub),
            })
    return out


def parse_simple_tiles(html: str) -> list[dict]:
    """Plain download tiles (reports, Aufsichtsmitteilungen): {title, files}."""
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for tile in soup.select("div.rab-download-tile"):
        info = tile.find(class_="rab-download-tile__info")
        p = info.find("p") if info else None
        files = _files(tile)
        if p and files:
            out.append({"title": _clean(p.get_text(" ", strip=True)), "files": files})
    return out


_TITLE_PREFIX = {"de": "Rundschreiben", "fr": "Circulaire", "it": "Circolare", "en": "Circular"}


class RabRundschreibenScraper(TextDedupMixin, PracticeScraper):
    SOURCE_KEY = "rab_rs"
    ISSUING_AUTHORITY = "RAB"
    DEFAULT_DOC_TYPE = "rundschreiben"
    REQUEST_DELAY = 1.5
    OCR_SCANS = True
    NO_TEXT_LAYER_BODY = "[Textlayer fehlt: gescanntes PDF]"

    def _make_doc_id(self, stub: dict) -> str:
        return f"{self.SOURCE_KEY}_{stub['rab_id']}"

    def _normalize(self, stub: dict, body_text: str) -> dict:
        doc = super()._normalize(stub, body_text)
        if not doc["date"]:
            # Aufsichtsmitteilungen carry no date on the page; the text opens with it.
            doc["date"] = first_date_iso(body_text[:2000])
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
        html = self._page(RS_URL)
        if html is not None:
            versions = parse_circulars(html)
            logger.info("[%s] %d circular versions", self.SOURCE_KEY, len(versions))
            seen: set[str] = set()
            for v in versions:
                status = _STATUS_IN_FORCE if (v["current"] and not v["repealed"]) else _STATUS_SUPERSEDED
                for lang, url in v["files"]:
                    rid = f"{slugify(v['number'])}_{v['date'].replace('-', '') or 'undated'}_{lang}"
                    if rid in seen:          # two history entries on one day
                        rid = f"{rid}_{slugify(unquote(url.rsplit('/', 1)[-1]))[:30]}"
                    seen.add(rid)
                    # Grouped by circular: the page links the current file of
                    # RS 1/2007 as its 2007 "Erstversion" too; the copy is dropped.
                    out = self.with_body({
                        "pdf_url": url,
                        "url": RS_URL,
                        "title": f"{_TITLE_PREFIX[lang]} {v['number'].split(' ', 1)[1]} — {v['subject']} | {v['label']}",
                        "doc_number": v["number"],
                        "date": v["date"],
                        "language": lang,
                        "doc_type": "rundschreiben",
                        "topics": [v["number"], v["label"], *status],
                        "rab_id": rid,
                    }, v["number"])
                    if out:
                        yield out
        for page_url, kind in ((NOTICES_URL, "mitteilung"), (REPORTS_URL, "bericht")):
            html = self._page(page_url)
            if html is None:
                continue
            tiles = parse_simple_tiles(html)
            logger.info("[%s] %s: %d tiles", self.SOURCE_KEY, page_url, len(tiles))
            for t in tiles:
                if kind == "mitteilung":
                    m = _NOTICE.search(t["title"])
                    number = f"RAB-Aufsichtsmitteilung {m.group(1)}/{m.group(2)}" if m else t["title"]
                    date = ""
                else:
                    m = _YEAR.search(t["title"])
                    if not m:
                        continue
                    year = m.group(1)
                    number, date = f"RAB Tätigkeitsbericht {t['title'][m.start():].strip()}", year
                for lang, url in t["files"]:
                    out = self.with_body({
                        "pdf_url": url,
                        "url": page_url,
                        "title": t["title"],
                        "doc_number": number,
                        "date": date,
                        "language": lang,
                        "doc_type": kind,
                        "topics": [number],
                        "rab_id": f"{slugify(number)}_{slugify(unquote(url.rsplit('/', 1)[-1]))[:40]}_{lang}",
                    }, number)
                    if out:
                        yield out
