"""
FINMA — Aufsichtsmitteilungen and the Bulletin "Ausgewählte Verfügungen"
========================================================================

Two FINMA series that are neither Rundschreiben (finma_rs) nor enforcement
case summaries (court 'finma', the Kasuistik):

1. **FINMA-Aufsichtsmitteilungen** (2017-) and the older **FINMA-Mitteilungen**
   (2009-2017), the supervisory notices in which FINMA states its practice on a
   current question: climate risks, stablecoins, operational resilience,
   tokenisation, sanctions, the Credit Suisse lessons, FATCA, benchmarks.
   Listed by FINMA's Sitecore search API — the same /{lang}/api/search/getresult
   POST the Kasuistik and Rundschreiben scrapers use — with three datasets:

     {ED0E8A7F-…}  /dokumentation/finma-aufsichtsmitteilungen/  (current, ~67, 25 per page, ?Skip=)
     {6DF6F403-…}  same page, the old Mitteilungen still listed (subset of the above)
     {32B76B1D-…}  …/archiv/  withdrawn Mitteilungen (~25)

   Each item names its language versions (OtherLanguageLinks DE/FR/IT/EN).
   Sitecore serves a media item per language under the same file name, but
   falls back to the German file where no translation exists; every
   non-German file is therefore compared with the German bytes and dropped
   when identical.

2. **FINMA-Bulletin, Ausgewählte Verfügungen** (Bulletin 1/2010 - 6/2017, two
   Sonderbulletins): anonymised extracts of FINMA rulings by topic —
   Angebotspflicht, Mindestpreisregel und Begründungspflicht, Verletzung der
   börsenrechtlichen Meldepflicht, Marktmanipulation, Gewähr, Berufsverbot.
   One static page. Each extract exists in ONE language (the ruling's), named
   by the DE/FR/IT label beside it; the "Vollständige Publikation" compilations
   are the sum of the extracts and are skipped.

3. **Enforcementberichte 2014-2018** (dataset {334F12EB-…}, archive page), each
   in DE/FR/IT/EN; from 2019 FINMA reports on enforcement in the annual report.

4. **Jahresberichte** (Geschäftsberichte page, one per language): the annual
   reports, 2013-, each language FINMA publishes. The financial statements
   (Jahresrechnung, "jr" files) are left out. The year comes from the file
   name — the French page lists the 2021 file under 2022 and 2023.

5. **Licensing guidance** under /de/bewilligung/ (≈80 pages, crawled): the
   Wegleitungen for licence applications (bank and securities-firm
   confirmations, Fintech licence, DLT trading facility, trading venues,
   remote members, ICO and stablecoin enquiries, insurance business-plan
   changes, insurance-intermediary registration, fund notifications), the FAQ
   on the licensing requirement, the Faktenblätter and FINMA's explanatory
   notes. Not taken: forms, templates, declarations, lists of licensed
   institutions, copies of Rundschreiben and Aufsichtsmitteilungen (in
   finma_rs / above), MoUs, Basel/IOSCO texts, presentations, portal how-tos,
   and the Swiss Bankers Association's guide (a private text).
"""
from __future__ import annotations

import logging
import re
from typing import Iterator
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .base import PracticeScraper, first_date_iso, slugify
from .finma_rundschreiben import _STATUS_IN_FORCE, _STATUS_SUPERSEDED
from .langdedup import TextDedupMixin

logger = logging.getLogger(__name__)

BASE = "https://www.finma.ch"
API = BASE + "/de/api/search/getresult"
DATASETS = (
    ("{ED0E8A7F-25EA-4DB1-B53B-C2327A435359}", False),   # current Aufsichtsmitteilungen + Mitteilungen
    ("{6DF6F403-FDA9-4888-B672-F4A34397C64A}", False),   # older Mitteilungen still listed
    ("{32B76B1D-7401-4B12-9542-1BD1F0602CAB}", True),    # archive: withdrawn
)
LIST_URL = BASE + "/de/dokumentation/finma-aufsichtsmitteilungen/"
ARCHIVE_URL = LIST_URL + "archiv/"
BULLETIN_URL = BASE + "/en/documentation/enforcement-reporting/selected-finma-rulings/"   # lists every extract; the DE path 404s
ENFORCEMENT_REPORTS = "{334F12EB-8F55-4F22-9DA6-1780FC3C249D}"
ENFORCEMENT_URL = BASE + "/de/dokumentation/archiv/enforcementbericht/"
ANNUAL_PAGES = (
    ("de", BASE + "/de/dokumentation/finma-publikationen/geschaeftsberichte/"),
    ("fr", BASE + "/fr/documentation/publications-finma/rapport-d-activite/"),
    ("it", BASE + "/it/documentazione/pubblicazioni-della-finma/rapporto-di-gestione/"),
    ("en", BASE + "/en/documentation/finma-publications/annual-reports--and-financial-statements/"),
)
LICENSING_ROOT = "/de/bewilligung/"
_ANNUAL_FILE = re.compile(r"(?:jb|jahresbericht)[_-]?(\d{2,4})|(\d{4})[^/]*?(?:jb|jahresbericht)", re.I)
_GUIDANCE = re.compile(r"wegleitung|\bfaq\b|faktenblatt|erläuterungen|aktualisierte informationen", re.I)
_NOT_GUIDANCE = re.compile(r"sbvg|formular|gesuchsvorlage|erklärung|prüfbericht|liste der|rundschreiben|"
                           r"aufsichtsmitteilung|anleitung|präsentation|symposium|porträt|iosco|basel", re.I)
_LANG_LABELS = {"DE": "de", "FR": "fr", "IT": "it", "EN": "en"}
_PREDECESSORS = {"_ebk": "EBK", "_bpv": "BPV", "_kstgwg": "Kst GwG"}
_FILE_DATE = re.compile(r"(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)")


def file_date(url: str) -> str:
    """ISO date a FINMA file name carries ('20250408-finma_jb24.pdf'), or ''."""
    m = _FILE_DATE.search(url.split("?")[0].rsplit("/", 1)[-1])
    if not m or not (1 <= int(m.group(2)) <= 12 and 1 <= int(m.group(3)) <= 31):
        return ""
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"

_AM_NUMBER = re.compile(r"Aufsichtsmitteilung\s+(\d{1,2})\s*/\s*(\d{4})", re.I)
_M_NUMBER = re.compile(r"FINMA-Mitteilung\s+(\d{1,3})\b", re.I)
_LANG = {"DE": "de", "FR": "fr", "IT": "it", "EN": "en"}
_BULLETIN_HEAD = re.compile(r"(Sonderbulletin|Special bulletin edition|Bulletin)\s*(\d)?\s*/?\s*(\d{4})?", re.I)
_BULLETIN_FILE = re.compile(r"(sonder)?bulletin[-_]?0?(\d)(?:[-_](\d{4}))?", re.I)
_COMPILATION = re.compile(r"gesamt|-00\.pdf|Vollständige Publikation|portraet", re.I)


def _clean(s: str) -> str:
    return " ".join((s or "").split())


def am_number(title: str) -> str:
    m = _AM_NUMBER.search(title)
    if m:
        return f"FINMA-Aufsichtsmitteilung {int(m.group(1)):02d}/{m.group(2)}"
    m = _M_NUMBER.search(title)
    if m:
        return f"FINMA-Mitteilung {int(m.group(1))}"
    return ""


def parse_am_items(items: list[dict], withdrawn: bool) -> list[dict]:
    """API items -> one dict per notice: {number, title, date, withdrawn,
    files {lang: url}}."""
    out = []
    for it in items:
        title = _clean(it.get("Title"))
        desc = _clean(it.get("Description"))
        number = am_number(title)
        if not number:
            continue
        files = {}
        for link in it.get("OtherLanguageLinks") or []:
            lang = _LANG.get((link.get("Name") or "").upper())
            if lang and link.get("Url"):
                files[lang] = urljoin(BASE, link["Url"])
        if not files and it.get("Link"):
            files["de"] = urljoin(BASE, it["Link"])
        full = title if (not desc or desc in title) else f"{title} — {desc}"
        out.append({"number": number, "title": full, "date": first_date_iso(it.get("Date") or ""),
                    "withdrawn": withdrawn, "files": files})
    return out


def annual_year(url: str) -> str:
    """The report year from the file name: 'finma_jb24' -> 2024,
    'finma-jahresbericht-2020' -> 2020, '20240320_finma_jb2023' -> 2023."""
    name = url.split("?")[0].rsplit("/", 1)[-1].lower()
    if "jr" in re.sub(r"[^a-z]", " ", name).split() or "jahresrechnung" in name or "_jr" in name or "-jr" in name:
        return ""
    m = re.search(r"(?:jb|jahresbericht)[_-]?(\d{4}|\d{2})(?!\d)", name)
    if not m:
        return ""
    y = m.group(1)
    return y if len(y) == 4 else f"20{y}"


def parse_annual(html: str) -> list[dict]:
    """Annual-report PDFs on a Geschäftsberichte page: {year, url}."""
    soup = BeautifulSoup(html, "html.parser")
    out, seen = [], set()
    for a in soup.find_all("a", href=True):
        h = a["href"]
        if "/geschaeftsbericht/" not in h or ".pdf" not in h.lower():
            continue
        year = annual_year(h)
        url = urljoin(BASE, h)
        if year and url not in seen:
            seen.add(url)
            name = h.split("?")[0].rsplit("/", 1)[-1].lower()
            # 2008: the last reports of FINMA's three predecessors
            issuer = next((v for k, v in _PREDECESSORS.items() if k in name), "FINMA")
            out.append({"year": year, "url": url, "issuer": issuer})
    return out


def parse_guidance(html: str, page: str) -> tuple[list[dict], list[str]]:
    """Licensing guidance on one /de/bewilligung/ page: ([{title, files
    {lang: url}}], [sub-page paths to crawl]). A document is the titled
    anchor plus the DE/FR/IT/EN-labelled anchors in the same list item."""
    soup = BeautifulSoup(html, "html.parser")
    main = soup.find("main") or soup
    docs, subs = [], []
    for a in main.find_all("a", href=True):
        h = a["href"].split("?")[0]
        if h.startswith(LICENSING_ROOT) and "mitglieder-suche" not in h:
            subs.append(h)
    for a in main.find_all("a", href=True):
        if ".pdf" not in a["href"].lower():
            continue
        title = _clean(a.get_text(" ", strip=True))
        if not title or title.upper() in _LANG_LABELS:
            continue
        name = a["href"].split("?")[0].rsplit("/", 1)[-1]
        if not _GUIDANCE.search(f"{title} {name}") or _NOT_GUIDANCE.search(f"{title} {name}"):
            continue
        m = re.search(r"/(de|fr|it|en)/~", a["href"]) or re.search(r"sc_lang=(de|fr|it|en)", a["href"])
        files = {(m.group(1) if m else "de"): urljoin(BASE, a["href"])}
        box = a.find_parent("li") or a.parent
        for b in box.find_all("a", href=True) if box else []:
            lab = _clean(b.get_text(" ", strip=True)).upper()
            if lab in _LANG_LABELS and ".pdf" in b["href"].lower():
                files.setdefault(_LANG_LABELS[lab], urljoin(BASE, b["href"]))
        docs.append({"title": title, "files": files, "page": page, "name": name})
    return docs, subs


def parse_bulletins(html: str) -> list[dict]:
    """One dict per ruling extract: {bulletin, index, title, lang, url}."""
    soup = BeautifulSoup(html, "html.parser")
    files: dict[str, dict] = {}
    order: list[str] = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if ".pdf" not in href.lower():
            continue
        name = href.split("?")[0].rsplit("/", 1)[-1]
        if _COMPILATION.search(name):
            continue
        d = files.get(name)
        if d is None:
            head = a.find_previous(["h2", "h3", "h4"])
            d = files[name] = {"head": _clean(head.get_text(" ", strip=True)) if head else "",
                               "title": "", "lang": "", "url": urljoin(BASE, href.split("?")[0]),
                               "name": name}
            order.append(name)
        text = _clean(a.get_text(" ", strip=True))
        if text.upper() in _LANG:
            d["lang"] = _LANG[text.upper()]
            d["url"] = urljoin(BASE, href)      # the labelled link carries the right sc_lang
        elif text and not d["title"]:
            d["title"] = text
    out, counters = [], {}
    for name in order:
        d = files[name]
        if not d["lang"] or not d["title"]:
            continue
        fm = _BULLETIN_FILE.search(name)
        special = bool(fm and fm.group(1)) or "sonder" in d["head"].lower() or "special" in d["head"].lower()
        hm = re.search(r"(\d)\s*/\s*(\d{4})", d["head"])
        if hm:
            nr, year = hm.group(1), hm.group(2)
        elif fm:
            nr = fm.group(2)
            ym = re.search(r"(20\d{2})", name)
            year = ym.group(1) if ym else ""
        else:
            continue
        if special and not year:
            year = {"1": "2011", "2": "2013"}.get(nr, "")
        label = f"FINMA-{'Sonderbulletin' if special else 'Bulletin'} {nr}/{year}".rstrip("/")
        counters[label] = counters.get(label, 0) + 1
        out.append({"bulletin": label, "index": counters[label], "title": d["title"],
                    "lang": d["lang"], "url": d["url"], "year": year})
    return out


class FinmaAufsichtScraper(TextDedupMixin, PracticeScraper):
    SOURCE_KEY = "finma_aufsicht"
    ISSUING_AUTHORITY = "FINMA"
    DEFAULT_DOC_TYPE = "mitteilung"
    REQUEST_DELAY = 1.5
    OCR_SCANS = True
    NO_TEXT_LAYER_BODY = "[Textlayer fehlt: gescanntes PDF]"

    def _make_doc_id(self, stub: dict) -> str:
        return f"{self.SOURCE_KEY}_{stub['fa_id']}"

    def _normalize(self, stub: dict, body_text: str) -> dict:
        doc = super()._normalize(stub, body_text)
        if not doc["date"]:
            doc["date"] = first_date_iso(body_text[:3000])
        if len(doc["date"]) == 4 and doc["doc_number"].startswith("FINMA-Bulletin"):
            # Bulletin extracts carry only their year on the page.
            d = first_date_iso(body_text[:3000])
            if d.startswith(doc["date"]):
                doc["date"] = d
        return doc

    def _post(self, dataset: str, skip: int) -> dict | None:
        self._rate_limit()
        try:
            r = self.session.post(
                f"{API}?Skip={skip}" if skip else API,
                data=f"ds={dataset}&Order=4",
                headers={"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                         "X-Requested-With": "XMLHttpRequest"},
                timeout=60)
            r.raise_for_status()
            return r.json() or {}
        except Exception as e:
            logger.warning("[%s] search API %s skip=%d failed: %s", self.SOURCE_KEY, dataset, skip, e)
            return None

    def _notices(self) -> Iterator[dict]:
        seen: set[str] = set()
        for dataset, withdrawn in DATASETS:
            skip, items = 0, []
            while True:
                d = self._post(dataset, skip)
                if not d:
                    break
                batch = d.get("Items") or []
                items += batch
                count = int(d.get("Count") or 0)
                skip += len(batch)
                if not batch or skip >= count:
                    break
            notices = parse_am_items(items, withdrawn)
            logger.info("[%s] dataset %s: %d notices", self.SOURCE_KEY, dataset[:9], len(notices))
            for n in notices:
                if n["number"] in seen:
                    continue
                seen.add(n["number"])
                status = _STATUS_SUPERSEDED if n["withdrawn"] else _STATUS_IN_FORCE
                # German first, so a fallback copy under FR/IT/EN is the one dropped.
                for lang, url in sorted(n["files"].items(), key=lambda kv: kv[0] != "de"):
                    stub = {
                        "pdf_url": url,
                        "url": ARCHIVE_URL if n["withdrawn"] else LIST_URL,
                        "title": n["title"],
                        "doc_number": n["number"],
                        "date": n["date"],
                        "language": lang,
                        "doc_type": "mitteilung",
                        "topics": [n["number"], "Aufsichtsmitteilung", *status],
                        "fa_id": f"{slugify(n['number'])}_{lang}",
                    }
                    out = self.with_body(stub, n["number"])
                    if out:
                        yield out

    def _bulletins(self) -> Iterator[dict]:
        try:
            r = self.get(BULLETIN_URL)
            r.raise_for_status()
        except Exception as e:
            logger.warning("[%s] bulletin page failed: %s", self.SOURCE_KEY, e)
            return
        extracts = parse_bulletins(r.text)
        logger.info("[%s] bulletin extracts: %d", self.SOURCE_KEY, len(extracts))
        for x in extracts:
            number = f"{x['bulletin']} Nr. {x['index']}"
            yield {
                "pdf_url": x["url"],
                "url": BULLETIN_URL,
                "title": f"{x['title']} ({x['bulletin']})",
                "doc_number": number,
                "date": x["year"],
                "language": x["lang"],
                "doc_type": "rechtsprechung",
                "topics": [x["bulletin"], "Ausgewählte Verfügungen", "FINMA-Verfügung"],
                "fa_id": f"{slugify(number)}_{x['lang']}",
            }

    def _enforcement_reports(self) -> Iterator[dict]:
        d = self._post(ENFORCEMENT_REPORTS, 0) or {}
        for it in d.get("Items") or []:
            m = re.search(r"(\d{4})", it.get("Title") or "")
            if not m:
                continue
            year = m.group(1)
            number = f"FINMA-Enforcementbericht {year}"
            files = {}
            for link in it.get("OtherLanguageLinks") or []:
                lang = _LANG.get((link.get("Name") or "").upper())
                if lang and link.get("Url"):
                    files[lang] = urljoin(BASE, link["Url"])
            for lang, url in sorted(files.items(), key=lambda kv: kv[0] != "de"):
                stub = {
                    "pdf_url": url, "url": ENFORCEMENT_URL,
                    "title": {"de": f"Enforcementbericht {year}", "fr": f"Rapport sur l'enforcement {year}",
                              "it": f"Rapporto sull'enforcement {year}", "en": f"Enforcement report {year}"}[lang],
                    "doc_number": number, "date": first_date_iso(it.get("Date") or "") or year,
                    "language": lang, "doc_type": "bericht",
                    "topics": [number, "Enforcementbericht"],
                    "fa_id": f"{slugify(number)}_{lang}",
                }
                out = self.with_body(stub, number)
                if out:
                    yield out

    def _annual_reports(self) -> Iterator[dict]:
        for lang, page in sorted(ANNUAL_PAGES, key=lambda x: x[0] != "de"):
            try:
                r = self.get(page)
                r.raise_for_status()
            except Exception as e:
                logger.warning("[%s] %s failed: %s", self.SOURCE_KEY, page, e)
                continue
            for rep_ in parse_annual(r.text):
                year = rep_["year"]
                number = f"{rep_['issuer']}-Jahresbericht {year}"
                stub = {
                    "pdf_url": rep_["url"], "url": page,
                    "title": {"de": f"{rep_['issuer']} Jahresbericht {year}", "fr": f"{rep_['issuer']} Rapport annuel {year}",
                              "it": f"{rep_['issuer']} Rapporto annuale {year}", "en": f"{rep_['issuer']} Annual Report {year}"}[lang],
                    "doc_number": number, "date": file_date(rep_["url"]) or year, "language": lang,
                    "doc_type": "bericht", "topics": [number, "Jahresbericht"],
                    "fa_id": f"{slugify(number)}_{lang}",
                }
                out = self.with_body(stub, number)
                if out:
                    yield out

    def _licensing_guidance(self) -> Iterator[dict]:
        todo, done, taken = [LICENSING_ROOT], set(), set()
        while todo:
            path = todo.pop(0)
            if path in done:
                continue
            done.add(path)
            try:
                r = self.get(BASE + path)
                r.raise_for_status()
            except Exception as e:
                logger.warning("[%s] %s failed: %s", self.SOURCE_KEY, path, e)
                continue
            docs, subs = parse_guidance(r.text, path)
            todo += [p for p in subs if p not in done]
            for d in docs:
                key = d["name"].lower()
                if key in taken:
                    continue          # the same Wegleitung is linked from several pages
                taken.add(key)
                title = d["title"]
                if len(title) < 16:
                    # a bare "Wegleitung" link: name it after its page
                    topic = d["page"].rstrip("/").rsplit("/", 1)[-1].replace("-", " ")
                    title = f"{title} ({topic})"
                number = f"FINMA {title}"
                # translations without a date in their file name take the German one's
                doc_date = next((file_date(u) for u in d["files"].values() if file_date(u)), "")
                for lang, url in sorted(d["files"].items(), key=lambda kv: kv[0] != "de"):
                    stub = {
                        "pdf_url": url, "url": BASE + d["page"], "title": title,
                        "doc_number": number, "date": file_date(url) or doc_date, "language": lang,
                        "doc_type": "wegleitung" if re.search(r"wegleitung", d["title"] + key, re.I) else "merkblatt",
                        "topics": [number, "Bewilligung"],
                        "fa_id": f"bew_{slugify(key)[:60]}_{lang}",
                    }
                    out = self.with_body(stub, number)
                    if out:
                        yield out
        logger.info("[%s] licensing pages crawled: %d", self.SOURCE_KEY, len(done))

    def discover_documents(self) -> Iterator[dict]:
        yield from self._notices()
        yield from self._bulletins()
        yield from self._enforcement_reports()
        yield from self._annual_reports()
        yield from self._licensing_guidance()
