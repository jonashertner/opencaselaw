"""
BSV Weisungen, Kreisschreiben, Rundschreiben, Mitteilungen (BSV Vollzug)
=======================================================================

Bundesamt für Sozialversicherungen — the practice corpus behind AHV, IV, EL,
ÜL, BVG, KV/UV coordination, EO and Familienzulagen: WEL, RWL, KSIH, KSBIL,
KSRP, IV-Rundschreiben, Mitteilungen an die Ausgleichskassen, the AHI-Praxis
Rechtsprechung archive.

Host: https://sozialversicherungen.admin.ch  (DNN/2sxc "BSV Vollzug")
  /{lang}/home            nav#secondary-navigation lists every folder with its
                          section path (AHV › Grundlagen AHV › Weisungen Renten)
  /{lang}/f/<folder>      one table.bsv-documents per folder: a parent row per
                          document plus an inline versions table listing EVERY
                          retained version with date, version number, language
                          availability and Dokumentennummer
  /{lang}/d/<doc>/download?version=N   the PDF of one version in one language
                          (verified 2026-09-02: /fr/… serves the French file;
                          HEAD answers 405, so never probe with HEAD)

Versioning model (FINMA precedent): one row per (document, version, language),
version and language baked into the doc_id, superseded versions retained so a
question about past conduct can read the version then in force. No
REVISION_FIELD is needed. No status tag is written — it would decay the moment
a new version appears; currency is carried by `date`, the version number and
the "Version N" topic, and search_practice collapses to the newest row per
document unless include_superseded is set.

Scope: sections AHV, IV, EL, ÜL, BV, KV, UV, EO, FamZ, ATSG and the
cross-cutting Rechtsprechung folders. International (EESSI/BUC/SED tooling,
treaty texts that live in Fedlex), eGov and Altersfragen are excluded, as are
folders of links, statistics, forms, contracts, tariffs, address lists.

Volume: ~2,600 documents; with versions and languages well above 10k PDFs.
CACHE_PDFS is off (the base cache never evicts and would exceed 30 GB). The
first full run takes hours: run it by hand with nohup, never inside
opencaselaw-practice.service (TimeoutStartSec=3600). This scraper is therefore
registered as EXPERIMENTAL until the unit is adjusted.
"""
from __future__ import annotations

import logging
import re
from typing import Iterator

from bs4 import BeautifulSoup

from .margin_numbers import MarginNumbersMixin
from .base import PracticeScraper, slugify

logger = logging.getLogger(__name__)

_BASE = "https://sozialversicherungen.admin.ch"

INCLUDE_SECTIONS = {
    "Alle Sozialversicherungen", "ATSG", "AHV", "IV", "EL", "ÜL",
    "BV (2. Säule)", "KV", "UV", "EO", "FamZ",
}
EXCLUDE_LABEL = re.compile(
    r"Links|Statistiken|Formulare|Verträge|Tarife|Spitex|Support|Standards"
    r"|Vorlagen|Adresslisten|Anträge|Verzeichnisse",
    re.IGNORECASE,
)

_FOLDER_HREF = re.compile(r"^/(?:de|fr|it)/f/(\d+)$")
_DOC_HREF = re.compile(r"/(?:de|fr|it)/d/(\d+)")
_VERSION_LABEL = re.compile(r"Version\s+(\d+)", re.IGNORECASE)
_DMY = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{4})")

_TYPE_RULES = (
    (re.compile(r"\bWegleitung|\bDirectives?\b|\bDirettive\b", re.I), "wegleitung"),
    (re.compile(r"\bKreisschreiben|\bCirculaire|\bCircolare", re.I), "kreisschreiben"),
    (re.compile(r"\bRundschreiben|\bLettre[- ]circulaire|\bLettera circolare", re.I), "rundschreiben"),
    (re.compile(r"\bMitteilung|\bBulletin|\bComunicazion", re.I), "mitteilung"),
    (re.compile(r"\bNachtrag|\bSupplément|\bSupplemento", re.I), "nachtrag"),
)
_RECHTSPRECHUNG = re.compile(r"Rechtsprechung|AHI-Praxis", re.IGNORECASE)


def _iso(dmy: str) -> str:
    m = _DMY.search(dmy or "")
    if not m:
        return ""
    return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"


def _clean(text: str) -> str:
    return " ".join((text or "").split())


def parse_nav(html: str) -> list[dict]:
    """Return every leaf folder from the home page's secondary navigation as
    {"id", "label", "path": [section, group, …]} — path[0] is the top-level
    section (AHV, IV, …). Group nodes (href '#') are not folders."""
    soup = BeautifulSoup(html, "html.parser")
    nav = soup.find("nav", id="secondary-navigation") or soup
    out: list[dict] = []

    def walk(ul, path: list[str]):
        # Markup: li.list-sub > a(label) + nav.drilldown-sub > ul > li …
        # Leaf folders are li > a[href=/de/f/<id>]; group nodes have href '#'.
        for li in ul.find_all("li", recursive=False):
            label_el = li.find("a", recursive=False)
            label = _clean(label_el.get_text(" ", strip=True)) if label_el else ""
            href = label_el.get("href", "") if label_el is not None else ""
            m = _FOLDER_HREF.match(href or "")
            if m:
                out.append({"id": m.group(1), "label": label, "path": list(path)})
            sub_path = path + [label] if label else path
            for child in li.find_all(recursive=False):
                if child.name == "ul":
                    walk(child, sub_path)
                elif child.name == "nav":
                    for sub in child.find_all("ul", recursive=False):
                        walk(sub, sub_path)

    for ul in nav.find_all("ul", recursive=False):
        walk(ul, [])
    if not out:
        top = nav.find("ul")
        if top is not None:
            walk(top, [])
    return out


def in_scope(folder: dict) -> bool:
    path = folder.get("path") or []
    # The home nav wraps everything in one "Alle Sozialversicherungen" root;
    # the meaningful section is the first path element that is a real section.
    sections = [p for p in path if p in INCLUDE_SECTIONS or p in
                ("International", "eGov", "Altersfragen")]
    section = sections[-1] if sections else (path[0] if path else "")
    if section not in INCLUDE_SECTIONS:
        return False
    if EXCLUDE_LABEL.search(folder.get("label", "")):
        return False
    return True


def section_of(folder: dict) -> str:
    for p in reversed(folder.get("path") or []):
        if p in INCLUDE_SECTIONS and p != "Alle Sozialversicherungen":
            return p
    return "Alle Sozialversicherungen"


def parse_folder(html: str, folder: dict, lang: str) -> list[dict]:
    """Parse one folder page into one stub per (document, version, language
    == `lang`). Only rows whose version lists `lang` are yielded, so the
    French page yields the French files only."""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", class_="bsv-documents")
    if table is None:
        return []
    tbody = table.find("tbody") or table
    stubs: list[dict] = []
    section = section_of(folder)
    rows = tbody.find_all("tr", recursive=False)
    for row in rows:
        classes = row.get("class") or []
        if "bsv-inline-versions-container" in classes or "tablesorter-childRow" in classes:
            continue
        col = row.find("div", class_="bsv-document-title-column")
        if col is None:
            continue
        strong = col.find("strong")
        abbrev = _clean(strong.get_text(" ", strip=True)) if strong else ""
        title_div = None
        for d in col.find_all("div", recursive=False):
            if not d.get("class"):
                title_div = d
        doc_title = _clean(title_div.get_text(" ", strip=True)) if title_div else abbrev
        doc_a = row.find("a", href=_DOC_HREF)
        if doc_a is None:
            continue
        doc_id_num = _DOC_HREF.search(doc_a["href"]).group(1)

        # inline versions live in the NEXT row (tr.bsv-inline-versions-container)
        versions_row = row.find_next_sibling("tr")
        has_versions_table = (versions_row is not None
                              and versions_row.find("table") is not None)
        details = (versions_row.select("tr.bsv-version-details[data-version]")
                   if has_versions_table else [])
        if not details:
            if has_versions_table:
                # a versions table with no parseable rows: markup drift, not a
                # single-version document — skip rather than guess
                logger.warning("[bsv_weisungen] doc %s: versions table without "
                               "details rows on the %s page", doc_id_num, lang)
                continue
            # no versions table at all: the document exists only in the
            # languages the parent row lists (the Italian page renders
            # de/fr-only documents without an Italian file)
            parent_langs = [_clean(a.get_text()) for a in row.select("a.bsv-versions-lang")]
            if parent_langs and lang not in parent_langs:
                continue
            stubs.append(_stub(folder, section, lang, doc_id_num, abbrev, doc_title,
                               version=None, date="", doknr="", filetype="PDF"))
            continue
        for det in details:
            n = det.get("data-version")
            langs = [_clean(a.get_text()) for a in det.select("a.bsv-versions-lang")]
            if langs and lang not in langs:
                continue
            p = det.find("p")
            v_title = _clean(p.get_text(" ", strip=True)) if p else doc_title
            meta: dict[str, str] = {}
            for dt in det.find_all("dt"):
                key = _clean(dt.get_text())
                dd = dt.find_next_sibling("dd")
                meta[key] = _clean(dd.get_text(" ", strip=True)) if dd else ""
            date = _iso(meta.get("Geändert") or meta.get("Modifié") or meta.get("Modificato") or "")
            if not date:
                head = det.find_previous_sibling("tr")
                if head is not None:
                    td = head.find("td", class_="text-right")
                    date = _iso(td.get_text() if td else "")
            doknr = meta.get("Dokumentennummer") or meta.get("Numéro du document") or meta.get("Numero del documento") or ""
            filetype = (meta.get("Dateityp") or meta.get("Type de fichier") or meta.get("Tipo di file") or "PDF").upper()
            stubs.append(_stub(folder, section, lang, doc_id_num, abbrev, v_title or doc_title,
                               version=n, date=date, doknr=doknr, filetype=filetype))
    return stubs


def _doc_type(title: str, folder_label: str) -> str:
    if _RECHTSPRECHUNG.search(folder_label or ""):
        return "rechtsprechung"
    for rx, kind in _TYPE_RULES:
        if rx.search(title or ""):
            return kind
    if _RECHTSPRECHUNG.search(title or ""):
        return "rechtsprechung"
    return "weisung"


def _stub(folder, section, lang, doc_num, abbrev, title, *, version, date, doknr, filetype) -> dict:
    v = version or "current"
    pdf_url = f"{_BASE}/{lang}/d/{doc_num}/download" + (f"?version={version}" if version else "")
    doc_number = abbrev or doknr or slugify(title)[:30]
    topics = [section, folder.get("label", "")]
    if abbrev:
        topics.append(abbrev)
    if version:
        topics.append(f"Version {version}")
    if doknr:
        topics.append(f"Dokumentennummer {doknr}")
    return {
        "pdf_url": pdf_url,
        "url": f"{_BASE}/{lang}/d/{doc_num}",
        "title": title,
        "doc_number": doc_number,
        "date": date,
        "language": lang,
        "doc_type": _doc_type(title, folder.get("label", "")),
        "topics": [t for t in topics if t],
        "bsv_doc": doc_num,
        "bsv_version": v,
        "bsv_filetype": filetype,
    }


class BsvWeisungenScraper(MarginNumbersMixin, PracticeScraper):
    SOURCE_KEY = "bsv_weisungen"
    ISSUING_AUTHORITY = "BSV"
    DEFAULT_DOC_TYPE = "weisung"
    REVISION_FIELD = None          # version is part of the doc_id
    CACHE_PDFS = False             # >30 GB otherwise; JSONL dedup gives resumability
    REQUEST_DELAY = 1.0
    LANGUAGES = ("de", "fr", "it")
    FOLDER_ALLOWLIST: set[str] | None = None   # e.g. {"5638"} for a scoped run
    NO_TEXT_LAYER_BODY = "[Textlayer fehlt: gescanntes PDF]"

    def _make_doc_id(self, stub: dict) -> str:
        return f"{self.SOURCE_KEY}_{stub['bsv_doc']}_v{stub['bsv_version']}_{stub.get('language', 'de')}"

    def folders(self) -> list[dict]:
        r = self.get(f"{_BASE}/de/home")
        r.raise_for_status()
        allf = parse_nav(r.text)
        scoped = [f for f in allf if in_scope(f)]
        if self.FOLDER_ALLOWLIST:
            scoped = [f for f in scoped if f["id"] in self.FOLDER_ALLOWLIST]
        logger.info("[%s] %d folders in scope of %d", self.SOURCE_KEY, len(scoped), len(allf))
        return scoped

    def discover_documents(self) -> Iterator[dict]:
        for folder in self.folders():
            for lang in self.LANGUAGES:
                url = f"{_BASE}/{lang}/f/{folder['id']}"
                try:
                    r = self.get(url)
                    r.raise_for_status()
                except Exception as e:
                    logger.warning("[%s] folder %s [%s] fetch failed: %s",
                                   self.SOURCE_KEY, folder["id"], lang, e)
                    continue
                stubs = parse_folder(r.text, folder, lang)
                skipped = [s for s in stubs if s["bsv_filetype"] != "PDF"]
                if skipped:
                    logger.info("[%s] folder %s [%s]: %d non-PDF versions skipped",
                                self.SOURCE_KEY, folder["id"], lang, len(skipped))
                for s in stubs:
                    if s["bsv_filetype"] == "PDF":
                        # Journals (ZAK, AHI-Praxis, EVG lists, registers) have
                        # no Rz; their page numbers, one per page, rise like
                        # Rz and were placed as such (re-run 2026-11-20). The
                        # runner fetches each stub before asking for the next.
                        self.margin_numbers_off = s["doc_type"] == "rechtsprechung"
                        yield s
