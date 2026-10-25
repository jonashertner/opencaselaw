"""
International Court of Justice — judgments, advisory opinions and orders
=======================================================================

  https://www.icj-cij.org/list-of-all-cases       every concluded case
  https://www.icj-cij.org/pending-cases           cases still before the Court
  https://www.icj-cij.org/case/{n}                English case name + tab links
  https://www.icj-cij.org/fr/affaire/{n}          French case name
  https://www.icj-cij.org/case/{n}/{tab}          judgments / advisory-opinions /
                                                  orders, each with its PDFs
  https://web.archive.org/cdx/search/cdx          Internet Archive capture index

Every main document of every case: judgments, advisory opinions, and the
orders that give reasons (provisional measures, admissibility of an
intervention, counter-claims). Orders that only fix or extend time-limits or
record a discontinuance are recorded in state/icj.skipped.tsv and not taken. Separate
and dissenting opinions are separate PDFs and are not taken either.

The case pages are open, but since December 2023 the Court's PDFs sit
behind a Cloudflare JavaScript challenge. Each PDF is requested from the
Court first; when the answer is not a PDF, the Internet Archive's latest
archived copy of that file — under the current path or the pre-2023
/public/files/ path, looked up in two CDX queries per run — is used
instead. A document with neither — everything published after the challenge
went up, until the Court lifts it or the Archive captures the file — returns
no text and is retried after the gap TTL. source_url is always the Court's
own URL.

One row per document (the build deduplicates on court + docket + date):
French text, English where no French text layer exists — both are authentic.
title = the French case name, regeste = '[EN: <English case name>]' so
English queries find it. decision_id = icj_<case>-<yyyymmdd>-<JUD|ADV|ORD>-<nn>,
the Court's own file code, which is also the docket (a judgment and an order
of the same day are two rows).

The Court's website terms forbid reproduction for commercial purposes, so the
rows stay out of the CC0 export.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterator

from bs4 import BeautifulSoup

from base_scraper import BaseScraper
from models import Decision, extract_citations, make_decision_id

logger = logging.getLogger(__name__)

BASE = "https://www.icj-cij.org"
LIST_URLS = (f"{BASE}/list-of-all-cases", f"{BASE}/pending-cases")
PENDING_URL = f"{BASE}/pending-cases"
CDX_URL = "https://web.archive.org/cdx/search/cdx"
# The site moved its files from /public/files/ to /sites/default/files/ in
# 2023; most archived copies are under the old path.
CDX_PREFIXES = ("icj-cij.org/public/files/case-related/*",
                "icj-cij.org/sites/default/files/case-related/*")
WAYBACK_URL = "https://web.archive.org/web/{ts}id_/{url}"
TABS = ("judgments", "advisory-opinions", "orders")
LEGAL_AREA = "Völkerrecht / Droit international public (IGH)"

KINDS = {
    #       FR type              EN type             DE         FR                  IT
    "JUD": ("Arrêt", "Judgment", "Urteil", "arrêt", "sentenza"),
    "ADV": ("Avis consultatif", "Advisory Opinion", "Gutachten", "avis consultatif", "parere consultivo"),
    "ORD": ("Ordonnance", "Order", "Anordnung", "ordonnance", "ordinanza"),
}

_FILE = re.compile(r"/(\d+)-(\d{8})-(JUD|ADV|ORD)-(\d{2})-00-(EN|FR|BI)\.pdf$", re.I)
_CASE_LINK = re.compile(r'href="(?:https://www\.icj-cij\.org)?/?case/(\d+)"')
SUBSTANTIVE_CHARS = 10000


def case_ids(html: str) -> set[int]:
    return {int(n) for n in _CASE_LINK.findall(html)}


def case_title(html: str) -> str:
    h1 = BeautifulSoup(html, "html.parser").find("h1")
    return re.sub(r"\s+", " ", h1.get_text(" ", strip=True)).strip() if h1 else ""


def case_tabs(html: str, case: int) -> set[str]:
    return set(re.findall(rf'href="/case/{case}/([a-z-]+)"', html)) & set(TABS)


def file_code(url: str) -> tuple | None:
    """'…/143-20120203-JUD-01-00-FR.pdf' → (143, '20120203', 'JUD', '01', 'FR')."""
    m = _FILE.search(url)
    return (int(m[1]), m[2], m[3].upper(), m[4], m[5].upper()) if m else None


def parse_documents(html: str) -> list[dict]:
    """Main documents of one tab: {code, case, date, kind, files: {EN/FR/BI: url}}.
    Opinions (div.subdocument) are left out."""
    out = []
    for doc in BeautifulSoup(html, "html.parser").select("div.cases-document"):
        files = {}
        buttons = doc.find("div", class_="file-buttons", recursive=False)
        links = buttons.find_all("a", href=True) if buttons else []
        title = doc.select_one("a.title[href]")
        if title:
            links = [title, *links]
        for a in links:
            code = file_code(a["href"])
            if code:
                files.setdefault(code[4], a["href"])
        if not files:
            continue
        case, day, kind, seq, _ = file_code(next(iter(files.values())))
        out.append({"code": f"{case}-{day}-{kind}-{seq}", "case": case, "kind": kind,
                    "date": f"{day[:4]}-{day[4:6]}-{day[6:]}", "files": files})
    return out


def parse_cdx(text: str, best: dict | None = None) -> dict[str, tuple[str, str, int]]:
    """CDX lines 'original timestamp length' (200 + application/pdf only) →
    (timestamp, original URL, length) of the largest capture per file, keyed
    'case-related/…' in lower case so the old and the new path of one file
    share a key. The Court replaced some files over the years, and the
    smaller versions are the bad ones: 001-19490409-JUD-01-00-FR was for a
    while only its annex (980 KB against 4.2 MB), 001-19480325-JUD-01-00-FR
    a re-encoded file whose accents read 'dØtroit'."""
    best = {} if best is None else best
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 3 or not parts[2].isdigit():
            continue
        key = _url_key(parts[0])
        if not key:
            continue
        old = best.get(key)
        if old is None or (int(parts[2]), parts[1]) > (old[2], old[0]):
            best[key] = (parts[1], parts[0], int(parts[2]))
    return best


def _url_key(url: str) -> str:
    m = re.search(r"/(?:sites/default|public)/files/(case-related/.+\.pdf)$", url, re.I)
    return m[1].lower() if m else ""


def pdf_text(data: bytes) -> str:
    try:
        import fitz
        with fitz.open(stream=data, filetype="pdf") as doc:
            text = "\n".join(page.get_text() for page in doc)
    except Exception as e:  # noqa: BLE001 — a bad PDF is a missing text, not a crash
        logger.debug(f"[icj] PDF text layer unreadable: {e}")
        return ""
    lines = [re.sub(r"[ \t\xa0]+", " ", ln).strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def substantive_order(text: str) -> bool:
    """Orders with reasons — provisional measures, admissibility of an
    intervention, counter-claims — run to well over 10,000 characters; the
    orders that only fix or extend time-limits, or record a discontinuance,
    are 2,000–6,000. A keyword test does not work: the time-limit order made
    the same day as a provisional-measures order cites it ('Vu l'ordonnance …
    indiquant des mesures conservatoires', 178-20200123-ORD-02)."""
    return len(text) >= SUBSTANTIVE_CHARS


class ICJScraper(BaseScraper):
    """ICJ judgments, advisory opinions and substantive orders."""

    REQUEST_DELAY = 2.0
    TIMEOUT = 120
    MAX_ERRORS = 20
    USER_AGENT = "OpenCaseLawBot/1.0 (+https://opencaselaw.ch; team@jonashertner.com)"
    CACHE_NONE_AS_GAP = True

    def __init__(self, state_dir: Path = Path("state")):
        super().__init__(state_dir=state_dir)
        self.skipped_path = self.state_dir / "icj.skipped.tsv"
        self.done_cases_path = self.state_dir / "icj.cases_done.tsv"
        self.skipped = self._read_ids(self.skipped_path)
        self.done_cases = {int(x) for x in self._read_ids(self.done_cases_path)}
        self.archive: dict[str, tuple[str, str, int]] | None = None

    @property
    def court_code(self) -> str:
        return "icj"

    @staticmethod
    def _read_ids(path: Path) -> set[str]:
        if not path.exists():
            return set()
        return {ln.split("\t", 1)[0] for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()}

    def _append(self, path: Path, line: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def _archive_index(self) -> dict[str, tuple[str, str, int]]:
        if self.archive is None:
            self.archive = {}
            for prefix in CDX_PREFIXES:
                try:
                    r = self.get(CDX_URL, params={
                        "url": prefix, "filter": ["statuscode:200", "mimetype:application/pdf"],
                        "fl": "original,timestamp,length", "limit": "500000"}, timeout=600)
                    parse_cdx(r.text, self.archive)
                except Exception as e:  # noqa: BLE001 — without the index only the Court's own URLs are tried
                    logger.warning(f"[icj] Internet Archive index {prefix} unavailable: {e}")
            logger.info(f"[icj] {len(self.archive)} archived PDFs")
        return self.archive

    def _pdf(self, url: str) -> bytes | None:
        """The PDF from the Court, else its latest archived copy, else None."""
        self._rate_limit()
        try:
            r = self.session.get(url, timeout=self.TIMEOUT)
            if r.status_code == 200 and r.content[:5] == b"%PDF-":
                return r.content
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[icj] {url}: {e}")
        ts, original, _ = self._archive_index().get(_url_key(url), ("", "", 0))
        if not ts:
            return None
        self._rate_limit()
        r = self.session.get(WAYBACK_URL.format(ts=ts, url=original), timeout=self.TIMEOUT)
        if r.status_code == 200 and r.content[:5] == b"%PDF-":
            return r.content
        logger.warning(f"[icj] archived copy {ts} of {url} unusable (HTTP {r.status_code})")
        return None

    def discover_new(self, since_date=None) -> Iterator[dict]:
        pending: set[int] = set()
        cases: set[int] = set()
        for url in LIST_URLS:
            ids = case_ids(self.get(url).text)
            cases |= ids
            if url == PENDING_URL:
                pending = ids
        if not cases:
            raise RuntimeError("[icj] no case links on the case lists — the site changed?")
        todo = sorted((c for c in cases if c in pending or c not in self.done_cases), reverse=True)
        logger.info(f"[icj] {len(cases)} cases, {len(pending)} pending, {len(todo)} to list")
        for case in todo:
            page = self.get(f"{BASE}/case/{case}").text
            name_en = case_title(page)
            name_fr = case_title(self.get(f"{BASE}/fr/affaire/{case}").text) or name_en
            docs = []
            for tab in sorted(case_tabs(page, case)):
                docs += parse_documents(self.get(f"{BASE}/case/{case}/{tab}").text)
            settled = True
            for doc in docs:
                did = make_decision_id("icj", doc["code"])
                if did in self.skipped or self.state.is_known(did):
                    settled = settled and (did in self.skipped or did in self.state._seen)
                    continue
                settled = False
                if since_date and doc["date"] < str(since_date):
                    continue
                yield {**doc, "decision_id": did, "docket_number": doc["code"],
                       "name_fr": name_fr, "name_en": name_en}
            if settled and docs and case not in pending:
                self.done_cases.add(case)
                self._append(self.done_cases_path, f"{case}\t{date.today().isoformat()}")

    def fetch_decision(self, stub: dict) -> Decision | None:
        text, lang = "", ""
        for lang in ("FR", "EN"):
            url = stub["files"].get(lang)
            data = self._pdf(url) if url else None
            text = pdf_text(data) if data else ""
            if len(text) >= 1000:
                break
        if len(text) < 1000:
            logger.info(f"[icj] {stub['code']}: no French or English text (yet)")
            return None
        if stub["kind"] == "ORD" and not substantive_order(text):
            self.skipped.add(stub["decision_id"])
            self._append(self.skipped_path, f"{stub['decision_id']}\tprocedural order")
            logger.info(f"[icj] {stub['code']}: procedural order, skipped")
            return None
        lang = lang.lower()
        type_fr, type_en = KINDS[stub["kind"]][:2]
        title = stub["name_fr"] if lang == "fr" else stub["name_en"]
        return Decision(
            decision_id=stub["decision_id"],
            court="icj",
            canton="UN",
            docket_number=stub["code"],
            docket_number_2=f"General List No. {stub['case']}",
            decision_date=date.fromisoformat(stub["date"]),
            language=lang,
            title=title or stub["code"],
            legal_area=LEGAL_AREA,
            regeste=f"[EN: {stub['name_en']}]" if lang == "fr" and stub.get("name_en") else None,
            decision_type=type_fr if lang == "fr" else type_en,
            full_text=text,
            source_url=url,
            pdf_url=url,
            cited_decisions=extract_citations(text),
            scraped_at=datetime.now(timezone.utc),
        )
