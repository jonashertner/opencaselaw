"""
Cantonal land-register (Grundbuch) and notarial supervisors' own practice
==========================================================================

Every canton runs its own Grundbuchinspektorat / Notariatsinspektorat; what
they publish for notaries and Grundbuchämter lives on as many different
sites. This source reads four cantons with a clean public body of practice
guidance:

  ZH  Notariate des Kantons Zürich (notariate-zh.ch): the "Grundbuch"
      section's own practice handbook (~100 HTML pages reached from its own
      table of contents at /de/grundbuch/inhaltsuebersicht — Stockwerk-
      eigentum, Schuldbrief, Grundpfandrechte, Dienstbarkeiten, Lex Koller
      under "Bewilligungsgesetz", …), the Merkblätter under
      /de/downloads/grundbuch, the Dienstanweisung zur NotGebV and a few
      non-form notarial Merkblätter under /de/downloads/notariat, and the
      one /de/notariat page squarely about land transactions
      (grundstueckgeschaefte). /de/erbrecht does not exist (404); the rest
      of /de/notariat/* (Ehegüterrecht, Gesellschaftsrecht, Erbrecht,
      Stiftungen, …) is general notarial-law consumer guidance, not land-
      register practice, and is out of scope here.
  BS  Grundbuch- und Vermessungsamt: the "Informationen zum Grundbuch für
      Notariate" newsletter, Nr. 1-17 (2013-). The compiled "Nr. 1-17" file
      (226k characters, the single issues bound together) is skipped: it
      would rank above every single issue it repeats. No separate Weisungen/Merkblätter page was found on
      bs.ch under the Grundbuch section.
  BE  Grundbuchämter: the Handbuch für den Verkehr mit den Grundbuchämtern
      und die Grundbuchführung (DE + FR, "inkl. Weisungen" — the appendix
      folds the circulars into the Handbuch itself, so there is no separate
      Weisung/Kreisschreiben page to scrape), split into one row per
      top-level chapter (see split_be_chapters), plus its 1982 predecessor
      (DE + FR) as a single historical row each.
  AG  Weisung betreffend die Grundbuchführung sowie den Geschäftsverkehr
      zwischen amtlicher Vermessung und Grundbuch, plus the Kreisschreiben
      and Merkblätter listed on
      .../grundbuch/rechtliche-grundlagen/weisung-und-kreisschreiben and
      .../grundbuch/praxishilfen. Pure forms (Grundbuchanmeldung,
      Verzichtserklärung, Vollmacht, …) and two 2022 conference
      handouts/slide decks ("Veranstaltung der ANG", "Austausch zwischen
      Urkundspersonen...") are left out: the first because they are forms,
      the second because they are training-event material, not issued
      agency guidance.

Surveyed 2026-11-08 for a clean public listing of their OWN land-register
Weisungen/Kreisschreiben/Merkblätter (not forms, not copies of federal
texts): GR (GIHA says by statute it "erlässt Weisungen", no public list
found), LU, SG (same: Kreisschreiben/Weisungen are named in the cantonal
Grundbuchverordnung, no public list found), TG (archival Weisungen 1950-84
only; the live portal is gni.tg.ch, not surveyed further), SO, ZG, FR, VS,
VD (LRF names an Inspectorat du registre foncier and departmental
directives, no public list found), GE (RRF gives the conservateur a
directive power, no public list found), NE — none had a page of their own
circulars/guidelines that this canton's time-box turned up; FR, VS and NE
were not actively searched within the box. Re-check with more time or a
direct inquiry to the cantonal inspectorate if this source is extended.

issuing_authority = canton code.
"""
from __future__ import annotations

import logging
import re
from typing import Iterator
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .base import PracticeScraper, first_date_iso, month_year_year, sha256_hex, slugify
from .langdedup import TextDedupMixin

logger = logging.getLogger(__name__)

_ZH = "https://www.notariate-zh.ch"
_ZH_ISSUER = "Notariate des Kantons Zürich (Notariatsinspektorat)"

# Pages reachable from the Grundbuch section's own table of contents that are
# tools/service pages, not practice guidance.
_ZH_SKIP_PATH = re.compile(r"/(auszugsbestellung|elektronische-eigentumsabfrage|inhaltsuebersicht)$")

# /de/notariat/* beyond this one page is general notarial-law consumer
# guidance (Ehegüterrecht, Gesellschaftsrecht, Erbrecht, Stiftungen...), not
# land-register practice; see module docstring.
_ZH_NOTARIAT_EXTRA = ("/de/notariat/grundstueckgeschaefte",)

# /de/downloads/notariat lists ~110 blank notarial-deed templates (AG/GmbH
# Gründung, GV-Beschluss, ...); only these are practice guidance rather than
# fillable forms.
_ZH_NOTARIAT_DOWNLOADS_INCLUDE = re.compile(
    r"Dienstanweisung|Hinterbliebenen|Gesetzliche Erbfolgeregelung|Partnerschaftsgesetz")

_BS_NEWSLETTER_URL = ("https://www.bs.ch/bvd/grundbuch-und-vermessungsamt/grundbuch/"
                      "informationen-zum-grundbuch-fuer-notariate")
_BS_ISSUER = "Grundbuch- und Vermessungsamt Basel-Stadt"
_BS_NUMBER = re.compile(r"Newsletter\s+Nr\.\s*([\d-]+)", re.I)

_BE = "https://www.gba.dij.be.ch"
_BE_ISSUER = "Grundbuchämter des Kantons Bern (Grundbuchinspektorat)"
_BE_HANDBUCH = {
    "de": f"{_BE}/content/dam/gba_dij/dokumente/de/handbuch/Handbuch-de.pdf",
    "fr": f"{_BE}/content/dam/gba_dij/dokumente/fr/handbuch/Handbuch-fr.pdf",
}
_BE_HANDBUCH_1982 = {
    "de": f"{_BE}/content/dam/gba_dij/dokumente/de/handbuch/Handbuch 1982-de.pdf",
    "fr": f"{_BE}/content/dam/gba_dij/dokumente/fr/handbuch/Handbuch 1982-fr.pdf",
}
# Chapter start: the per-page running header "<n>/<total>" immediately
# followed by the chapter's own "<N>. \n<Title>" line (the same heading
# repeats, with a trailing "....N" leader, in the table of contents, where
# it never follows a "<n>/<total>" marker — that occurrence is excluded for
# free). Works for both languages: DE sub-items are numbered ("1.1"), FR
# sub-items are not, so nothing after the title is required.
_BE_CHAPTER_START = re.compile(r"\d+/\d+\s*\n(\d{1,2})\.\s*\n([^\n]+?)\s*\n")
_BE_VERSION = re.compile(r"(?:Bearbeitungsdatum|Modifié le)\s*\n(.+?)\s*\nVersion\s*\n(\d+)", re.S)

_AG = "https://www.ag.ch"
_AG_ISSUER = "Departement Volkswirtschaft und Inneres, Abteilung Register und Personenstand"
_AG_WEISUNG_URL = f"{_AG}/de/verwaltung/dvi/grundbuch-vermessung/grundbuch/rechtliche-grundlagen/weisung-und-kreisschreiben"
_AG_PRAXISHILFEN_URL = f"{_AG}/de/verwaltung/dvi/grundbuch-vermessung/grundbuch/praxishilfen"
# Blank forms and the two 2026-06-14 conference slide decks / ANG event
# handouts (training material, not issued guidance) on the Praxishilfen page.
_AG_PRAXISHILFEN_EXCLUDE = re.compile(
    r"^Grundbuchanmeldung$|^Nachtrag zur Grundbuchanmeldung$|Verzichtserklärung|"
    r"Erklärung über die erleichterte Umwandlung|Erklärung betr\. Gläubigerwechsel|"
    r"Austausch zwischen Urkundspersonen|Veranstaltung der ANG")


def _clean(s: str) -> str:
    return " ".join((s or "").split())


# ── ZH: the Grundbuch handbook's own HTML pages ─────────────────────────

def zh_navtree_paths(toc_html: str) -> list[str]:
    """/de/grundbuch/* paths linked from the section's own table of
    contents at /de/grundbuch/inhaltsuebersicht, service/tool pages
    dropped, in document order (first occurrence wins)."""
    soup = BeautifulSoup(toc_html, "html.parser")
    seen: list[str] = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not re.match(r"^/de/grundbuch(/|$)", href):
            continue
        href = href.split("?")[0].split("#")[0]
        if _ZH_SKIP_PATH.search(href) or href in seen:
            continue
        seen.append(href)
    return seen


def zh_breadcrumb(path: str) -> str:
    """A path like "/de/grundbuch/grundpfandrechte/grundpfand-verschreibung/
    glaeubigerwechsel" into "Grundpfandrechte > Grundpfand Verschreibung >
    Glaeubigerwechsel". Several handbook pages share the same H1 ("Gebühren"
    appears on three different pages, "Stockwerkeigentum" on two — a child
    page can repeat its section's own heading), so doc_number is built from
    the path, not the title: two different pages never share a path, and
    "search_practice collapses to the newest row per ... doc_number" (see
    mcp_server.py _search_practice) would otherwise silently hide one of
    them behind the other."""
    tail = re.sub(r"^/de/(grundbuch|notariat)/?", "", path).strip("/")
    segments = [s.replace("-", " ").strip().title() for s in tail.split("/") if s]
    return " > ".join(segments) or "Grundbuch"


def parse_zh_page(html: str) -> dict | None:
    """Title + main-content body text (no nav/footer/sidebar/related-links)
    for one notariate-zh.ch Grundbuch/Notariat page. The main content
    column (".lg:col-span-8") holds, in document order: an intro
    ".text-content" div, then for a page with an accordion one
    ".text-content" div per open/close section (headings live in a sibling
    <h4>, skipped — body text alone is what matters for search), then
    sometimes a trailing "Weitere Informationen ..." link list that carries
    no ".text-content" class and is dropped for free. A page with no
    accordion (a short landing page) has exactly one such div. The sidebar
    column (".lg:col-span-4": glossary box, downloads box) is never
    touched, since it sits outside this selector entirely."""
    soup = BeautifulSoup(html, "html.parser")
    h1 = soup.select_one("h1")
    title = _clean(h1.get_text(" ", strip=True)) if h1 else ""
    col8 = soup.select_one(".lg\\:col-span-8")
    if not title or not col8:
        return None
    parts = [el.get_text(" ", strip=True) for el in col8.select(".text-content")]
    body = _clean(" ".join(p for p in parts if p))
    return {"title": title, "body_text": body}


def zh_download_rows(html: str) -> list[dict]:
    """[{"title", "pdf_url"}] from a notariate-zh.ch /de/downloads/<x>
    listing: one ".download-file-row" per document, the PDF link within it
    (rows with only a DOCX are form-only and have none)."""
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for row in soup.select(".download-file-row"):
        name_el = row.select_one(".download-file-name")
        pdf = row.find("a", href=lambda h: h and h.lower().endswith(".pdf"))
        if not name_el or not pdf:
            continue
        out.append({"title": _clean(name_el.get_text(" ", strip=True)),
                    "pdf_url": urljoin(_ZH, pdf["href"])})
    return out


_ZH_FORM_TITLES = re.compile(r"^(Vollmacht|Generalvollmacht|Auskunftsformular)\b", re.I)


def zh_grundbuch_doc_type(title: str) -> str:
    return "formular" if _ZH_FORM_TITLES.match(title) else "merkblatt"


# ── BS: the Grundbuch newsletter ────────────────────────────────────────

def bs_newsletter_rows(html: str) -> list[dict]:
    """[{"title", "number", "pdf_url"}] for every Grundbuch-Newsletter PDF
    (Nr. 1-17 individually; the "1-17" compilation of all issues is
    skipped, as EHRA's "(alle)" compilation is); the one AHV-Nummer declaration form on the same page has no "Newsletter
    Nr." in its title and is dropped by the number match."""
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for a in soup.find_all("a", href=lambda h: h and h.lower().endswith(".pdf")):
        title = _clean(a.get_text(" ", strip=True))
        m = _BS_NUMBER.search(title)
        if not m or "-" in m.group(1):
            continue
        out.append({"title": re.sub(r"\s*\(Startet einen Download\).*$", "", title),
                    "number": m.group(1), "pdf_url": a["href"]})
    return out


# ── BE: the Handbuch, split into one row per top-level chapter ─────────

def split_be_chapters(text: str) -> list[dict]:
    """[{"number", "title", "body_text"}] for each top-level numbered
    chapter of the BE Handbuch (DE or FR): the Inhaltsverzeichnis's
    occurrences of the same heading never match (no "<n>/<total>" marker
    precedes a TOC line), so only the real chapter starts, in the running
    page header, are found. The trailing, unnumbered "Anhang 1:
    Kreisschreiben, Weisungen und 'Informationen'" section rides along
    with the last chapter."""
    starts = [(m.start(), m.end(), m.group(1), _clean(m.group(2)))
              for m in _BE_CHAPTER_START.finditer(text)]
    out = []
    for i, (start, end, num, title) in enumerate(starts):
        stop = starts[i + 1][0] if i + 1 < len(starts) else len(text)
        out.append({"number": num, "title": title, "body_text": _clean(text[end:stop])})
    return out


def be_handbuch_version(text: str) -> tuple[str, str]:
    """(date_iso, version) from the Handbuch's cover page ("Bearbeitungs-
    datum .../Modifié le ... Version N"), or ("", "") if not found."""
    m = _BE_VERSION.search(text[:600])
    if not m:
        return "", ""
    date = first_date_iso(m.group(1)) or month_year_year(m.group(1))
    return date, m.group(2)


# ── AG: Weisung, Kreisschreiben, Merkblätter ────────────────────────────

def ag_pdf_rows(html: str, base_url: str) -> list[dict]:
    """[{"title", "pdf_url"}] for every PDF linked from an ag.ch Grundbuch
    rechtliche-grundlagen/praxishilfen listing page."""
    soup = BeautifulSoup(html, "html.parser")
    out, seen = [], set()
    for a in soup.find_all("a", href=lambda h: h and h.lower().endswith(".pdf")):
        url = urljoin(base_url, a["href"])
        if url in seen:
            continue
        seen.add(url)
        title = re.sub(r"\s*\(PDF.*$", "", _clean(a.get_text(" ", strip=True)))
        out.append({"title": title, "pdf_url": url})
    return out


def ag_doc_type(title: str) -> str:
    if title.lower().startswith("weisung"):
        return "weisung"
    if title.lower().startswith("kreisschreiben"):
        return "kreisschreiben"
    return "merkblatt"


class GrundbuchKantoneScraper(TextDedupMixin, PracticeScraper):
    SOURCE_KEY = "grundbuch_kantone"
    ISSUING_AUTHORITY = "ZH"            # overridden per row
    DEFAULT_DOC_TYPE = "merkblatt"
    REVISION_FIELD = "revision"         # page_hash (ZH HTML) | pdf_url (PDFs) | version (BE Handbuch)
    REQUEST_DELAY = 1.5
    PDF_TIMEOUT = 90
    # The BE 1982 Handbuch (both languages) is a pre-digital scan with no
    # text layer; without this it would come back as "" from fetch_pdf_text
    # and with_body would drop it silently (confirmed live 2026-11-08).
    OCR_SCANS = True
    NO_TEXT_LAYER_BODY = "[Textlayer fehlt: gescanntes PDF]"

    def _make_doc_id(self, stub: dict) -> str:
        return f"{self.SOURCE_KEY}_{stub['gbk_id']}"

    def _normalize(self, stub: dict, body_text: str) -> dict:
        doc = super()._normalize(stub, body_text)
        doc["issuing_authority"] = stub["canton"]
        return doc

    # ── ZH ───────────────────────────────────────────────────────────

    def _zh_page_stub(self, path: str) -> dict | None:
        try:
            r = self.get(urljoin(_ZH, path))
            r.raise_for_status()
        except Exception as e:
            logger.warning("[%s] ZH %s fetch failed: %s", self.SOURCE_KEY, path, e)
            return None
        parsed = parse_zh_page(r.text)
        # image-only pages ("Image", a diagram) carry nothing searchable
        if not parsed or len(parsed["body_text"]) < 50:
            logger.info("[%s] ZH %s: no body text, skipped", self.SOURCE_KEY, path)
            return None
        subject = path.rstrip("/").rsplit("/", 1)[-1].replace("-", " ")
        return {
            "gbk_id": f"zh_{slugify(path)}_de",
            "canton": "ZH",
            "url": urljoin(_ZH, path),
            "title": parsed["title"],
            "doc_number": f"ZH Notariate {zh_breadcrumb(path)}",
            "date": "",
            "language": "de",
            "doc_type": "handbuch",
            "topics": ["ZH", _ZH_ISSUER, "Grundbuch", subject],
            "body_text": parsed["body_text"],
            "revision": sha256_hex(parsed["body_text"])[:16],
        }

    def _discover_zh_navtree(self) -> Iterator[dict]:
        try:
            r = self.get(f"{_ZH}/de/grundbuch/inhaltsuebersicht")
            r.raise_for_status()
        except Exception as e:
            logger.warning("[%s] ZH table of contents fetch failed: %s", self.SOURCE_KEY, e)
            return
        paths = zh_navtree_paths(r.text)
        if "/de/grundbuch" not in paths:
            paths = ["/de/grundbuch"] + paths
        paths += list(_ZH_NOTARIAT_EXTRA)
        for path in paths:
            stub = self._zh_page_stub(path)
            if stub:
                yield stub

    def _discover_zh_downloads(self) -> Iterator[dict]:
        try:
            r = self.get(f"{_ZH}/de/downloads/grundbuch")
            r.raise_for_status()
            rows = zh_download_rows(r.text)
        except Exception as e:
            logger.warning("[%s] ZH downloads/grundbuch fetch failed: %s", self.SOURCE_KEY, e)
            rows = []
        for row in rows:
            doc_type = zh_grundbuch_doc_type(row["title"])
            stub = {
                "gbk_id": f"zh_dl_{slugify(row['title'])}_de",
                "canton": "ZH", "pdf_url": row["pdf_url"], "url": f"{_ZH}/de/downloads/grundbuch",
                "title": row["title"], "doc_number": f"ZH Notariate {row['title']}",
                "date": "", "language": "de", "doc_type": doc_type,
                "topics": ["ZH", _ZH_ISSUER, "Grundbuch"], "revision": row["pdf_url"],
            }
            out = self.with_body(stub, stub["gbk_id"])
            if out:
                if not out.get("date"):
                    out["date"] = first_date_iso(out["body_text"][:1000])
                yield out

        try:
            r = self.get(f"{_ZH}/de/downloads/notariat")
            r.raise_for_status()
            rows = [row for row in zh_download_rows(r.text)
                    if _ZH_NOTARIAT_DOWNLOADS_INCLUDE.search(row["title"])]
        except Exception as e:
            logger.warning("[%s] ZH downloads/notariat fetch failed: %s", self.SOURCE_KEY, e)
            rows = []
        for row in rows:
            doc_type = "weisung" if "Dienstanweisung" in row["title"] else "merkblatt"
            stub = {
                "gbk_id": f"zh_dl_{slugify(row['title'])}_de",
                "canton": "ZH", "pdf_url": row["pdf_url"], "url": f"{_ZH}/de/downloads/notariat",
                "title": row["title"], "doc_number": f"ZH Notariate {row['title']}",
                "date": "", "language": "de", "doc_type": doc_type,
                "topics": ["ZH", _ZH_ISSUER, "Notariat"], "revision": row["pdf_url"],
            }
            out = self.with_body(stub, stub["gbk_id"])
            if out:
                if not out.get("date"):
                    out["date"] = first_date_iso(out["body_text"][:1500])
                yield out

    # ── BS ───────────────────────────────────────────────────────────

    def _discover_bs(self) -> Iterator[dict]:
        try:
            r = self.get(_BS_NEWSLETTER_URL)
            r.raise_for_status()
        except Exception as e:
            logger.warning("[%s] BS newsletter page fetch failed: %s", self.SOURCE_KEY, e)
            return
        for row in bs_newsletter_rows(r.text):
            stub = {
                "gbk_id": f"bs_newsletter_{slugify(row['number'])}_de",
                "canton": "BS", "pdf_url": row["pdf_url"], "url": _BS_NEWSLETTER_URL,
                "title": row["title"], "doc_number": f"BS Grundbuch-Newsletter Nr. {row['number']}",
                "date": month_year_year(row["title"]), "language": "de", "doc_type": "mitteilung",
                "topics": ["BS", _BS_ISSUER, "Grundbuch"], "revision": row["pdf_url"],
            }
            out = self.with_body(stub, stub["gbk_id"])
            if out:
                yield out

    # ── BE ───────────────────────────────────────────────────────────

    def _discover_be(self) -> Iterator[dict]:
        # Chapter split bypasses with_body (one PDF -> many rows), so the
        # cross-language "serves the German file under the FR link" risk
        # langdedup.py warns about needs its own guard here: a later
        # language's chapter is dropped if its text is byte-identical to an
        # earlier language's chapter already yielded (German, the first
        # key in _BE_HANDBUCH, goes first on purpose).
        chapter_hashes: dict[str, str] = {}
        for lang, url in _BE_HANDBUCH.items():
            try:
                r = self.get(url)
                r.raise_for_status()
            except Exception as e:
                logger.warning("[%s] BE Handbuch (%s) fetch failed: %s", self.SOURCE_KEY, lang, e)
                continue
            text = self._extract(r.content)
            if not text:
                logger.warning("[%s] BE Handbuch (%s): no text extracted", self.SOURCE_KEY, lang)
                continue
            date, version = be_handbuch_version(text)
            chapters = split_be_chapters(text)
            if not chapters:
                logger.warning("[%s] BE Handbuch (%s): chapter split found nothing, "
                               "indexing as one row", self.SOURCE_KEY, lang)
                chapters = [{"number": "0", "title": "Handbuch", "body_text": _clean(text)}]
            title_word = "Kapitel" if lang == "de" else "Chapitre"
            for ch in chapters:
                h = sha256_hex(ch["body_text"])
                if chapter_hashes.get(ch["number"]) == h:
                    logger.warning("[%s] BE Handbuch (%s) chapter %s: same text as an earlier "
                                   "language, skipped (German fallback?)",
                                   self.SOURCE_KEY, lang, ch["number"])
                    continue
                chapter_hashes.setdefault(ch["number"], h)
                yield {
                    "gbk_id": f"be_handbuch_{ch['number']}_{lang}",
                    "canton": "BE", "url": url, "pdf_url": url,
                    "title": f"Handbuch für den Verkehr mit den Grundbuchämtern: {ch['title']}"
                             if lang == "de" else
                             f"Manuel concernant les relations avec les bureaux du registre foncier: {ch['title']}",
                    "doc_number": f"BE Handbuch {title_word} {ch['number']}",
                    "date": date, "language": lang, "doc_type": "handbuch",
                    "topics": ["BE", _BE_ISSUER, "Grundbuch", ch["title"]],
                    "body_text": ch["body_text"], "revision": f"v{version}",
                }

        for lang, url in _BE_HANDBUCH_1982.items():
            stub = {
                "gbk_id": f"be_handbuch_1982_{lang}",
                "canton": "BE", "pdf_url": url, "url": url,
                "title": ("Handbuch der Justizdirektion des Kantons Bern von 1982" if lang == "de"
                          else "Manuel de la Direction de la justice de 1982"),
                "doc_number": "BE Handbuch 1982", "date": "1982", "language": lang,
                "doc_type": "handbuch", "topics": ["BE", _BE_ISSUER, "Grundbuch"],
                "revision": url,
            }
            out = self.with_body(stub, stub["gbk_id"])
            if out:
                yield out

    # ── AG ───────────────────────────────────────────────────────────

    def _discover_ag(self) -> Iterator[dict]:
        rows: list[dict] = []
        try:
            r = self.get(_AG_WEISUNG_URL)
            r.raise_for_status()
            rows += ag_pdf_rows(r.text, _AG_WEISUNG_URL)
        except Exception as e:
            logger.warning("[%s] AG weisung-und-kreisschreiben fetch failed: %s", self.SOURCE_KEY, e)
        try:
            r = self.get(_AG_PRAXISHILFEN_URL)
            r.raise_for_status()
            rows += [row for row in ag_pdf_rows(r.text, _AG_PRAXISHILFEN_URL)
                     if not _AG_PRAXISHILFEN_EXCLUDE.search(row["title"])]
        except Exception as e:
            logger.warning("[%s] AG praxishilfen fetch failed: %s", self.SOURCE_KEY, e)

        taken: set[str] = set()
        for row in rows:
            if row["pdf_url"] in taken:
                continue
            taken.add(row["pdf_url"])
            stub = {
                "gbk_id": f"ag_{slugify(row['title'])}_de",
                "canton": "AG", "pdf_url": row["pdf_url"], "url": _AG_WEISUNG_URL,
                "title": row["title"], "doc_number": f"AG {row['title']}",
                "date": "", "language": "de", "doc_type": ag_doc_type(row["title"]),
                "topics": ["AG", _AG_ISSUER, "Grundbuch"], "revision": row["pdf_url"],
            }
            out = self.with_body(stub, stub["gbk_id"])
            if out:
                if not out.get("date"):
                    out["date"] = first_date_iso(out["body_text"][:2000])
                yield out

    # ── entry point ──────────────────────────────────────────────────

    def discover_documents(self) -> Iterator[dict]:
        yield from self._discover_zh_navtree()
        yield from self._discover_zh_downloads()
        yield from self._discover_bs()
        yield from self._discover_be()
        yield from self._discover_ag()
