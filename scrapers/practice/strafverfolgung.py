"""
Prosecution guidance — WOSTA, cantonal prosecutors' directives, SSK-CPS
========================================================================

Four unrelated sites, one practice area (Strafverfolgung / poursuite pénale):

  * ZH Oberstaatsanwaltschaft: WOSTA (Weisungen für das Vorverfahren), the
    canton's binding pre-trial-procedure manual for prosecutors and police —
    split into one row per top-level chapter (its own "Inhaltsübersicht"
    numbering, currently 1-17) — plus its Strafmassempfehlungen (sentencing
    recommendations: general + a separate one for environmental offences).
    Page: /de/sicherheit-justiz/strafverfahren/informationszugang-interessen
    bindungen.html. The WOSTA PDF's filename encodes its "Stand" (edition)
    date and changes every edition — never hard-code it, discover it from
    the page.
  * SSK-CPS (Schweizerische Staatsanwälte-Konferenz / Conférence des
    procureurs de Suisse): 19 Empfehlungen (DE + FR), Drupal accordion page,
    one <article class="node--type-empfehlung"> per item grouped under an
    accordion-button heading (its topic: "Edition von Bankunterlagen",
    "Häusliche Gewalt", ...).
  * VS Ministère public: ~19 Weisungen ("Directives") + 2 Reglemente
    ("Règlements") (DE + FR), a Liferay document-library page.
  * AG Staatsanwaltschaft: Strafbefehlsempfehlungen + 3 Weisungen linked from
    the cantonal "Strafverfahren" hub page (DE only).

doc_number / versioning
------------------------
WOSTA chapters: "WOSTA Ziff. <n>", shared across editions; doc_id embeds the
edition's Stand date (REVISION_FIELD="pdf_url") so every retained edition is
kept as its own row — the chapter text of an old edition is exactly what a
reader needs for a decision made under it.

Every other document here: doc_number from its own title (SSK/VS keep their
German/French titles separately — the two language pages are not matched up
document-for-document, since neither site exposes a stable cross-language
key); REVISION_FIELD="pdf_url" since AG/VS/SSK and the ZH Strafmassempfeh-
lungen all re-upload under a new dated filename on revision.

issuing_authority: "ZH" (WOSTA + Strafmassempfehlungen), "SSK-CPS", "VS",
"AG". doc_type: "weisung" (WOSTA chapters, AG/VS Weisungen/Directives),
"empfehlung" (Strafmassempfehlungen, SSK, AG Strafbefehlsempfehlungen),
"erlass" (VS Reglemente/Règlements — internal organisational rules, not
case-handling instructions).
"""
from __future__ import annotations

import logging
import re
from typing import Iterator
from urllib.parse import unquote, urljoin

from bs4 import BeautifulSoup

from .base import PracticeScraper, first_date_iso, slugify
from .langdedup import TextDedupMixin

logger = logging.getLogger(__name__)

_ZH = "https://www.zh.ch"
_ZH_PAGE = ("/de/sicherheit-justiz/strafverfahren/"
            "informationszugang-interessenbindungen.html")

_SSK_PAGES = (
    ("de", "https://www.ssk-cmp.ch/de/dienstleistungen/empfehlungen"),
    ("fr", "https://www.ssk-cmp.ch/fr/dienstleistungen/empfehlungen"),
)

_VS_PAGES = (
    ("de", "https://www.vs.ch/de/web/mp/directives-et-reglements"),
    ("fr", "https://www.vs.ch/fr/web/mp/directives-et-reglements"),
)

_AG_PAGE = "https://www.ag.ch/de/themen/recht-justiz/strafverfolgung/strafverfahren"


def _clean(s: str) -> str:
    return " ".join((s or "").split())


# ──────────────────────────────────────────────────────────────────────
# WOSTA: filename date + chapter splitter
# ──────────────────────────────────────────────────────────────────────

_WOSTA_STAND = re.compile(r"WOSTA_Stand_(\d{2})\.(\d{2})\.(\d{4})", re.I)


def wosta_stand_date(pdf_url: str) -> str:
    """ISO date out of "WOSTA_Stand_09.09.2026_..." in the filename, or ""
    if the filename doesn't carry one (fall back to the PDF text then)."""
    m = _WOSTA_STAND.search(unquote(pdf_url))
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else ""


def split_wosta(text: str) -> list[dict]:
    """One stub per top-level WOSTA chapter: {"number", "title", "body"}.

    WOSTA prints two tables of contents: a short "Inhaltsübersicht" (just
    the 17 top-level chapters) and a long "Inhaltsverzeichnis" (every
    sub-heading, several hundred entries). Splitting on the short one keeps
    the unit at the granularity the task asks for ("WOSTA Ziff. 7") and
    stays correct across editions whose chapter titles or count changed,
    since it reads the count and the titles straight from the document
    rather than assuming 17.

    Chapter boundaries are found by matching each chapter's page number
    (from the Inhaltsübersicht) against "Seite N von M" markers in the body
    — every page number 1..M appears exactly once, and a chapter's heading
    reliably starts right after its own start page's marker (WOSTA's layout
    puts that marker ahead of the page's content in extraction order).
    Returns [] if the document's structure doesn't match this pattern
    (future edition reformatted) rather than guessing.
    """
    toc_start = text.find("Inhaltsübersicht")
    toc_end = text.find("Inhaltsverzeichnis")
    if toc_start < 0 or toc_end < 0 or toc_end <= toc_start:
        return []
    toc = text[toc_start + len("Inhaltsübersicht"):toc_end]
    entries: list[tuple[int, str, int]] = []
    cur_num: int | None = None
    cur_lines: list[str] = []
    for ln in (l.strip() for l in toc.splitlines()):
        if not ln:
            continue
        m = re.match(r"^(\d{1,2})\.$", ln)
        if m:
            cur_num, cur_lines = int(m.group(1)), []
            continue
        if re.match(r"^\d+$", ln) and cur_num is not None:
            entries.append((cur_num, _clean(" ".join(cur_lines)), int(ln)))
            cur_num = None
            continue
        if cur_num is not None:
            cur_lines.append(ln)
    if len(entries) < 2:
        return []
    markers: dict[int, int] = {}
    for m in re.finditer(r"Seite (\d+) von \d+", text):
        markers[int(m.group(1))] = m.end()
    offsets = []
    for num, title, page in entries:
        off = markers.get(page)
        if off is None or off <= toc_end:
            logger.warning("[strafverfolgung] WOSTA chapter %s (page %s) has "
                           "no body anchor; splitter needs review for this edition", num, page)
            return []
        offsets.append((num, title, off))
    chapters = []
    for i, (num, title, off) in enumerate(offsets):
        end = offsets[i + 1][2] if i + 1 < len(offsets) else len(text)
        body = re.sub(r"\s*Seite \d+ von \d+\s*", "\n", text[off:end]).strip()
        if body:
            chapters.append({"number": str(num), "title": title, "body": body})
    return chapters


# ──────────────────────────────────────────────────────────────────────
# ZH: Oberstaatsanwaltschaft download lists (AEM "mdl-download_list" markup)
# ──────────────────────────────────────────────────────────────────────

def parse_zh_page(html: str) -> list[dict]:
    """One stub per PDF in the page's download lists, tagged with the
    nearest preceding <h2>/<h3> section heading ("WOSTA",
    "Strafmassempfehlungen")."""
    soup = BeautifulSoup(html, "html.parser")
    out = []
    seen: set[str] = set()
    for lst in soup.find_all("ul", class_="mdl-download_list__list"):
        heading = lst.find_previous(["h2", "h3"])
        section = _clean(heading.get_text(" ", strip=True)) if heading else ""
        for a in lst.find_all("a", href=True):
            href = a["href"]
            if not href.lower().endswith(".pdf"):
                continue
            url = urljoin(_ZH, href)
            if url in seen:
                continue
            seen.add(url)
            text_span = a.find("span", class_="atm-linklist_item__text")
            title_span = text_span.find("span") if text_span else None
            title = (_clean(title_span.get_text(" ", strip=True)) if title_span
                      else _clean(a.get_text(" ", strip=True)))
            out.append({"title": title, "section": section, "pdf_url": url})
    return out


_VERSION_DATE = re.compile(r"Version\s+(\d+)\s*\n?\s*(\d{1,2})\.(\d{1,2})\.(\d{4})")


def latest_version_date(text: str) -> str:
    """ISO date of the highest-numbered "Version N / D.M.YYYY" entry in a
    revision table. ZH Oberstaatsanwaltschaft Merkblätter print every past
    version before the current one, so first_date_iso's "first date in
    reading order" rule would otherwise return Version 1's date — years
    older than the document in force. Returns "" when there's no such
    table (not every ZH Merkblatt carries one)."""
    best: tuple[int, str] | None = None
    for m in _VERSION_DATE.finditer(text):
        v = int(m.group(1))
        if best is None or v > best[0]:
            iso = f"{m.group(4)}-{int(m.group(3)):02d}-{int(m.group(2)):02d}"
            best = (v, iso)
    return best[1] if best else ""


# ──────────────────────────────────────────────────────────────────────
# SSK-CPS: Drupal accordion page
# ──────────────────────────────────────────────────────────────────────

_SSK_DATE_PREFIX = re.compile(r"/(\d{8})_")


def parse_ssk(html: str, lang: str, page_url: str) -> list[dict]:
    """One stub per <a class="file-download-pdf">, dated from the file
    path's "YYYYMMDD_" prefix when present, tagged with the accordion
    section (its button text) it sits under."""
    soup = BeautifulSoup(html, "html.parser")
    out = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        classes = a.get("class") or []
        if "file-download-pdf" not in classes:
            continue
        title = _clean(a.get_text(" ", strip=True))
        if not title:
            continue
        url = urljoin(page_url, a["href"])
        if url in seen:
            continue
        seen.add(url)
        m = _SSK_DATE_PREFIX.search(unquote(a["href"]))
        date = f"{m.group(1)[0:4]}-{m.group(1)[4:6]}-{m.group(1)[6:8]}" if m else ""
        btn = a.find_previous("button")
        section = _clean(btn.get_text(" ", strip=True)) if btn else ""
        out.append({"title": title, "pdf_url": url, "date": date,
                    "section": section, "language": lang})
    return out


# ──────────────────────────────────────────────────────────────────────
# VS Ministère public: Liferay document library
# ──────────────────────────────────────────────────────────────────────

def parse_vs(html: str, lang: str, page_url: str) -> list[dict]:
    """One stub per matomo_download anchor, classified Reglement/Règlement
    vs. Weisung/Directive from the nearest preceding <h2>."""
    soup = BeautifulSoup(html, "html.parser")
    out = []
    seen: set[str] = set()
    for a in soup.find_all("a", class_="matomo_download", href=True):
        url = urljoin(page_url, a["href"])
        if url in seen:
            continue
        seen.add(url)
        text = _clean(a.get_text(" ", strip=True))
        title = re.sub(r"^pdf\s+", "", text, flags=re.I)
        title = re.sub(r"\.pdf$", "", title, flags=re.I).strip()
        if not title:
            continue
        heading = a.find_previous("h2")
        section = _clean(heading.get_text(" ", strip=True)) if heading else ""
        is_reglement = bool(re.search(r"reglement|règlement", section, re.I))
        out.append({"title": title, "pdf_url": url, "section": section,
                    "language": lang,
                    "doc_type": "erlass" if is_reglement else "weisung"})
    return out


# ──────────────────────────────────────────────────────────────────────
# AG Staatsanwaltschaft: hub page
# ──────────────────────────────────────────────────────────────────────

_AG_DATE_PREFIX = re.compile(r"/(\d{8})-")


def parse_ag(html: str, page_url: str) -> list[dict]:
    """One stub per Oberstaatsanwaltschaft erlasse PDF linked from the AG
    cantonal "Strafverfahren" hub page."""
    soup = BeautifulSoup(html, "html.parser")
    out = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/erlasse/" not in href or not href.lower().endswith(".pdf"):
            continue
        url = urljoin(page_url, href)
        if url in seen:
            continue
        seen.add(url)
        span = a.find("span", class_="link__text")
        raw = _clean(span.get_text(" ", strip=True)) if span else _clean(a.get_text(" ", strip=True))
        title = re.sub(r"\s*\(PDF.*?\)\s*$", "", raw, flags=re.I).strip()
        m = _AG_DATE_PREFIX.search(unquote(href))
        date = f"{m.group(1)[0:4]}-{m.group(1)[4:6]}-{m.group(1)[6:8]}" if m else ""
        out.append({"title": title, "pdf_url": url, "date": date})
    return out


def _doc_type(title: str, section: str) -> str:
    if re.search(r"Strafbefehlsempfehlung", title, re.I):
        return "empfehlung"
    return "weisung"


class StrafverfolgungScraper(TextDedupMixin, PracticeScraper):
    SOURCE_KEY = "strafverfolgung"
    ISSUING_AUTHORITY = "ZH"          # overridden per row (ZH/SSK-CPS/VS/AG)
    DEFAULT_DOC_TYPE = "weisung"
    REVISION_FIELD = "pdf_url"        # every page here re-uploads under a new URL on revision
    REQUEST_DELAY = 1.5
    OCR_SCANS = True
    NO_TEXT_LAYER_BODY = "[Textlayer fehlt: gescanntes PDF]"

    def _make_doc_id(self, stub: dict) -> str:
        return f"{self.SOURCE_KEY}_{stub['sv_id']}"

    def _normalize(self, stub: dict, body_text: str) -> dict:
        doc = super()._normalize(stub, body_text)
        doc["issuing_authority"] = stub.get("authority", self.ISSUING_AUTHORITY)
        if not doc["date"]:
            doc["date"] = latest_version_date(body_text) or first_date_iso(body_text[:3000])
        return doc

    # ── ZH: WOSTA + Strafmassempfehlungen ──

    def _zh_stubs(self) -> Iterator[dict]:
        try:
            r = self.get(_ZH + _ZH_PAGE)
            r.raise_for_status()
        except Exception as e:
            logger.warning("[%s] ZH page fetch failed: %s", self.SOURCE_KEY, e)
            return
        items = parse_zh_page(r.text)
        logger.info("[%s] ZH: %d PDFs found", self.SOURCE_KEY, len(items))
        for item in items:
            if re.search(r"WOSTA|Vorverfahren", item["title"] + item["section"], re.I):
                stand = wosta_stand_date(item["pdf_url"])
                text = self.fetch_pdf_text(item["pdf_url"])
                if not stand:
                    stand = first_date_iso(text[:2000])
                chapters = split_wosta(text)
                if not chapters:
                    logger.warning("[%s] WOSTA splitter found no chapters; "
                                   "the document's structure may have changed", self.SOURCE_KEY)
                    continue
                ocr_tag = ["OCR"] if self._last_ocr else []
                for ch in chapters:
                    yield {
                        "pdf_url": item["pdf_url"],
                        "url": _ZH + _ZH_PAGE,
                        "title": f"WOSTA Ziff. {ch['number']} {ch['title']}",
                        "doc_number": f"WOSTA Ziff. {ch['number']}",
                        "date": stand,
                        "language": "de",
                        "doc_type": "weisung",
                        "topics": ["WOSTA", "Strafprozess", "Vorverfahren",
                                   "Oberstaatsanwaltschaft Zürich", ch["title"], *ocr_tag],
                        "authority": "ZH",
                        "body_text": ch["body"],
                        "sv_id": f"wosta_{ch['number']}_{slugify(stand or 'undated')}",
                    }
            else:
                # Strafmassempfehlungen (general + Umweltkriminalität)
                yield {
                    "pdf_url": item["pdf_url"],
                    "url": _ZH + _ZH_PAGE,
                    "title": item["title"],
                    "doc_number": f"ZH {item['title']}",
                    "language": "de",
                    "doc_type": "empfehlung",
                    "topics": ["Strafzumessung", "Oberstaatsanwaltschaft Zürich"],
                    "authority": "ZH",
                    "sv_id": f"zh_{slugify(item['title'])}",
                }

    # ── SSK-CPS ──

    def _ssk_stubs(self) -> Iterator[dict]:
        for lang, url in _SSK_PAGES:
            try:
                r = self.get(url)
                r.raise_for_status()
            except Exception as e:
                logger.warning("[%s] SSK %s fetch failed: %s", self.SOURCE_KEY, lang, e)
                continue
            items = parse_ssk(r.text, lang, url)
            logger.info("[%s] SSK %s: %d items", self.SOURCE_KEY, lang, len(items))
            for it in items:
                yield {
                    "pdf_url": it["pdf_url"],
                    "url": url,
                    "title": it["title"],
                    "doc_number": f"SSK {it['title']}",
                    "date": it["date"],
                    "language": lang,
                    "doc_type": "empfehlung",
                    "topics": ["SSK-CPS", it["section"]] if it["section"] else ["SSK-CPS"],
                    "authority": "SSK-CPS",
                    "sv_id": f"ssk_{slugify(it['title'])}_{lang}",
                }

    # ── VS Ministère public ──

    def _vs_stubs(self) -> Iterator[dict]:
        for lang, url in _VS_PAGES:
            try:
                r = self.get(url)
                r.raise_for_status()
            except Exception as e:
                logger.warning("[%s] VS %s fetch failed: %s", self.SOURCE_KEY, lang, e)
                continue
            items = parse_vs(r.text, lang, url)
            logger.info("[%s] VS %s: %d items", self.SOURCE_KEY, lang, len(items))
            for it in items:
                yield {
                    "pdf_url": it["pdf_url"],
                    "url": url,
                    "title": it["title"],
                    "doc_number": f"VS {it['title']}",
                    "language": lang,
                    "doc_type": it["doc_type"],
                    "topics": ["VS", "Ministère public", it["section"]],
                    "authority": "VS",
                    "sv_id": f"vs_{slugify(it['title'])}_{lang}",
                }

    # ── AG Staatsanwaltschaft ──

    def _ag_stubs(self) -> Iterator[dict]:
        try:
            r = self.get(_AG_PAGE)
            r.raise_for_status()
        except Exception as e:
            logger.warning("[%s] AG page fetch failed: %s", self.SOURCE_KEY, e)
            return
        items = parse_ag(r.text, _AG_PAGE)
        logger.info("[%s] AG: %d items", self.SOURCE_KEY, len(items))
        for it in items:
            yield {
                "pdf_url": it["pdf_url"],
                "url": _AG_PAGE,
                "title": it["title"],
                "doc_number": f"AG {it['title']}",
                "date": it["date"],
                "language": "de",
                "doc_type": _doc_type(it["title"], ""),
                "topics": ["AG", "Staatsanwaltschaft", "Strafprozess"],
                "authority": "AG",
                "sv_id": f"ag_{slugify(it['title'])}",
            }

    def discover_documents(self) -> Iterator[dict]:
        for stub in self._zh_stubs():
            if "body_text" in stub:
                yield stub            # WOSTA chapters already carry their text
                continue
            out = self.with_body(stub, stub["doc_number"])
            if out:
                yield out
        for stub in self._ssk_stubs():
            out = self.with_body(stub, stub["doc_number"])
            if out:
                yield out
        for stub in self._vs_stubs():
            out = self.with_body(stub, stub["doc_number"])
            if out:
                yield out
        for stub in self._ag_stubs():
            out = self.with_body(stub, stub["doc_number"])
            if out:
                yield out
