"""
Vaud Tribunal cantonal, Cour de droit administratif et public (vd_omni)
=======================================================================
Direct scraper for jurisprudence.vd.ch (Omnis JURISWEB, schema VD_TA_WEB):
the rulings of the CDAP, of its predecessor the Tribunal administratif (TA,
until 2007), of the Cour constitutionnelle (CCST) and of a few small
authorities filed on the same portal (JI, TN, CF, DF, TC, CE).

Until 2026-03 the only intake for these rulings was the entscheidsuche
VD_Omni spider (es_vd_omni.jsonl, frozen 2026-03-12, newest ruling
2026-02-09). This scraper writes the same court code, vd_omni, with the same
id shape, vd_omni_<docket>, so a ruling the frozen shard already holds keeps
its id: the build imports direct shards first and drops the es copy of the
same id and date (build_fts5.insert_decision).

Portal (probed 2026-11-08):
  - POST /scripts/nph-omniscgi.exe (Aufruf=search) with a decision-date
    window -> result page "résultats: 1 - N de N fiche(s) trouvée(s)".
    The page size is honoured up to at least 2,000 rows; a zero-hit window
    still prints "0 de 0 fiche(s)". A failed request prints "#ERROR-...".
  - Each hit: docket (AC.2026.0320), authority + decision date
    ("CDAP  28.10.2026"), nF30_KEY, parties, résumé, judge/clerk
    abbreviations, "publié le", Jurivoc keywords and provisions.
  - GET Aufruf=getMarkupDocument&nF30_KEY=<k>&Template=.../document.fiw ->
    the ruling as HTML (div.WordSection1, older rulings div.Section1). The
    URL works without the session key (W10_KEY), so it is the stable
    source_url. The portal offers no PDF.
  - 50,399 fiches on 2026-11-08: CDAP 21,913, TA 21,887, TF 6,186, CCST
    272, JI 91, TAF 16, TN 11, CF 9, DF 7, TC 4, CE/XX/blank 1 each.

What is not taken: the Federal Supreme Court (TF) and Federal Administrative
Court (TAF) rulings the portal files under the cantonal docket of the case
they decided. They are federal rulings the corpus holds under bger / bvger
from the federal courts' own sites; a cantonal copy would be served twice
(the 2026-11-06 fold exists to undo exactly that for BGer copies).

Headnotes. The portal files a ruling once per headnote: one docket and
decision date can be 2-11 fiches with the same document and a different
résumé each (3,433 such groups). They are one decision: fetched once (lowest
nF30_KEY), every résumé kept in regeste (group_headnotes).

Identity. One docket can carry several rulings: the arrêt, a later ruling on
costs after a remand, an Erläuterung. The first ruling seen for a docket
takes vd_omni_<docket>; another ruling of the docket on another date takes
vd_omni_<docket>_dYYYYMMDD (the suffix the build itself gives a same-id,
other-date row), a second one on the same date vd_omni_<docket>_n<nF30_KEY>.
Which id a docket's rulings already use is kept in the sidecar
state/vd_omni.rulings.tsv (<base id>, date, nF30_KEY, decision id, origin),
seeded once from the frozen es shard and the scraper's own shard, appended
after every durable write (on_decision_persisted). A listing whose docket and
date match a frozen es row is held and is not fetched again unless
VD_OMNI_REFETCH_HELD=1 (then it is fetched under the es row's id, and the
direct copy replaces the es copy at the next build).

Windows. One request per calendar month, oldest first (so the earliest
ruling of a docket takes the plain id), plus one window for everything
decided before 1991 (17 fiches). Nightly runs walk the last NIGHTLY_MONTHS
months by decision date: publication follows the decision by 20 days at the
median, 78 at p90, 280 at most (2026 rulings, measured 2026-11-08). --since
walks from that date; the first backfill is --since 1800-01-01.

Health (CLAUDE.md invariant 7): the portal fails intermittently (a 302 whose
Location carries "An error occurred while executing a method"; the same
request succeeds seconds later), so a listing page is tried three times, then
the window is split in halves with three tries each. A window that still
cannot be listed completely (failure, no hit counter, fewer rows than the
counter) is logged at ERROR as "VD Omni: search failed ...", which
run_all_scrapers counts as a discovery error; after three such windows in a
row the run stops listing. A dead portal therefore yields >= 3 discovery
errors and 0 new, the "silent success" rule's FAILED case.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Iterator

from bs4 import BeautifulSoup

from base_scraper import BaseScraper
from models import Decision, detect_language, extract_citations, make_decision_id

logger = logging.getLogger(__name__)

COURT = "vd_omni"
HOST = "https://jurisprudence.vd.ch"
CGI_PATH = "/scripts/nph-omniscgi.exe"
CGI_URL = HOST + CGI_PATH

# Page size of one listing request. A month holds at most ~200 fiches (the
# busiest year, 2006, ~1,600); a window whose counter exceeds the page is
# split in halves rather than paged (the W10_KEY cursor is short-lived on
# this platform family, see bs_gerichte.py).
PAGE_SIZE = 1000

# Everything decided before this year comes in one window (17 fiches).
FIRST_MONTHLY_YEAR = 1991
EARLIEST = date(1800, 1, 1)

# Nightly window by decision date (see module docstring for the lag data).
NIGHTLY_MONTHS = 24

# A run stops listing after this many windows failed one after another (each
# already retried and split): the portal is down, and every further window
# would only cost its retries. Three ERROR lines also meet the health rule.
MAX_FAILED_WINDOWS_IN_A_ROW = 3

# Résumé key: length kept, and the shortest key trusted to say "same ruling"
# (a generic one-liner like "Recours irrecevable." must not match).
RESUME_KEY_CHARS = 150
MIN_RESUME_KEY = 80
MIN_EXACT_RESUME_KEY = 20

# Authorities whose rulings are not this court's (see module docstring).
FEDERAL_AUTHORITIES = frozenset({"TF", "TAF"})

BASE_FORM = {
    "OmnisPlatform": "WINDOWS",
    "WebServerUrl": "",
    "WebServerScript": CGI_PATH,
    "OmnisLibrary": "JURISWEB",
    "OmnisClass": "rtFindinfoWebHtmlService",
    "OmnisServer": "7001",
    "Schema": "VD_TA_WEB",
    "Parametername": "WWW_V4",
    "Source": "search.fiw",
}

SEARCH_FORM = {
    **BASE_FORM,
    "Aufruf": "search",
    "cTemplate": "search/standard/results/resultpage.fiw",
    "cTemplate_SuchstringValidateError": "search/standard/search.fiw",
    "cSprache": "FRE",
    "cGeschaeftsart": "",
    "cGeschaeftsjahr": "",
    "cGeschaeftsnummer": "",
    "cHerkunft": "",
    "dEntscheiddatum": "",
    "dEntscheiddatumBis": "",
    "dPublikationsdatum": "",
    "cPublikation": "",
    "cReferent": "",
    "cTitel": "",
    "cSekretaer": "",
    "cResume": "",
    "cSuchstringZiel": "F37_HTML",
    "cSuchstring": "",
    "nAnzahlTrefferProSeite": str(PAGE_SIZE),
    "nSeite": "1",
}

RE_HITS = re.compile(r"de\s+(\d+)\s+fiche", re.IGNORECASE)
RE_NF30 = re.compile(r"nF30_KEY=(\d+)")
RE_DATE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")
RE_PUBLISHED = re.compile(r"publi[ée]\s+le\s*:\s*(\d{2}\.\d{2}\.\d{4})", re.IGNORECASE)
# "AC.2026.0320", "CCST.2005.0001", "MPU.2019.0012"
RE_DOCKET = re.compile(r"^[A-Z]{1,6}\.\d{4}\.\d{1,6}$")
RE_SECTION_CLASS = re.compile(r"^(?:Word)?Section\d+$")

REPO_ROOT = Path(__file__).resolve().parents[2]
ES_SHARD_ENV = "VD_OMNI_ES_SHARD"
SHARD_ENV = "VD_OMNI_SHARD"
REFETCH_ENV = "VD_OMNI_REFETCH_HELD"
# Comma-separated decision ids to fetch under their held id although the
# corpus holds them (a ruling the court re-issued: FO.2023.0004, runbook).
REFETCH_IDS_ENV = "VD_OMNI_REFETCH_IDS"


def doc_url(nf30_key: str | int) -> str:
    """The stable URL of a ruling: no session key, works on its own."""
    return (
        f"{CGI_URL}?OmnisPlatform=WINDOWS&WebServerUrl=&WebServerScript={CGI_PATH}"
        "&OmnisLibrary=JURISWEB&OmnisClass=rtFindinfoWebHtmlService&OmnisServer=7001"
        "&Parametername=WWW_V4&Schema=VD_TA_WEB&Source=search.fiw"
        f"&Aufruf=getMarkupDocument&cSprache=FRE&nF30_KEY={nf30_key}"
        "&Template=search/standard/results/document.fiw"
    )


def parse_ch_date(text: str | None) -> date | None:
    m = RE_DATE.search(text or "")
    if not m:
        return None
    try:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


def _clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\xa0", " ")).strip()


def month_windows(start: date, end: date) -> list[tuple[date, date]]:
    """Decision-date windows covering [start, end], oldest first: one window
    for everything before FIRST_MONTHLY_YEAR, then one per calendar month."""
    windows: list[tuple[date, date]] = []
    if start.year < FIRST_MONTHLY_YEAR:
        windows.append((start, min(end, date(FIRST_MONTHLY_YEAR - 1, 12, 31))))
        start = date(FIRST_MONTHLY_YEAR, 1, 1)
    cur = date(start.year, start.month, 1)
    while cur <= end:
        nxt = date(cur.year + (cur.month == 12), cur.month % 12 + 1, 1)
        lo = max(cur, start)
        hi = min(nxt - timedelta(days=1), end)
        if lo <= hi:
            windows.append((lo, hi))
        cur = nxt
    return windows


def parse_hit_count(html: str) -> int | None:
    m = RE_HITS.search(html or "")
    return int(m.group(1)) if m else None


def parse_listing(html: str) -> list[dict]:
    """Every hit on a result page, federal ones included (the caller
    filters), in page order."""
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for a in soup.find_all("a", href=RE_NF30):
        cell = a.find_parent("td")
        if cell is None or "fontsize12" not in (cell.get("class") or []):
            continue
        key = RE_NF30.search(a["href"]).group(1)
        docket = _clean(a.get_text(" "))
        auth_cell = cell.find_next_sibling("td")
        auth_text = _clean(auth_cell.get_text(" ")) if auth_cell else ""
        decision_date = parse_ch_date(auth_text)
        authority = RE_DATE.sub("", auth_text).strip()

        block = cell.find_parent("td", attrs={"valign": "top"})
        block_table = block.find_parent("table") if block else None
        parties = resume = judge_clerk = None
        publication_date = None
        keywords: list[str] = []
        provisions: list[str] = []
        if block is not None:
            bold = block.find("b")
            parties = _clean(bold.get_text(" ")) if bold else None
            if bold is not None:
                row = bold.find_parent("tr")
                nxt = row.find_next_sibling("tr") if row else None
                if nxt is not None:
                    resume = nxt.get_text("\n").strip() or None
            for td in block.find_all("td", class_="fontsize8"):
                t = _clean(td.get_text(" "))
                pm = RE_PUBLISHED.search(t)
                if pm:
                    publication_date = parse_ch_date(pm.group(1))
                elif " - " in f" {t} " and not t.isdigit():
                    judge_clerk = t
        if block_table is not None:
            mots = block_table.find("td", class_="motscles")
            if mots is not None:
                italic = mots.find("i")
                provisions = [_clean(s.get_text()) for s in italic.find_all("span")] if italic else []
                provisions = [p for p in provisions if p]
                for s in mots.find_all("span"):
                    if italic is not None and s in italic.find_all("span"):
                        continue
                    t = _clean(s.get_text())
                    if t:
                        keywords.append(t)
        judge, clerk = _split_judge_clerk(judge_clerk)
        out.append({
            "nf30_key": key,
            "docket_number": docket,
            "authority": authority,
            "decision_date": decision_date,
            "publication_date": publication_date,
            "parties": parties,
            "resume": _tidy_block(resume),
            "judge": judge,
            "clerk": clerk,
            "keywords": keywords,
            "provisions": provisions,
            "url": doc_url(key),
        })
    return out


def _split_judge_clerk(text: str | None) -> tuple[str | None, str | None]:
    """"ATZ -  ESN -" -> ("ATZ", "ESN"); "ATZ -   -" -> ("ATZ", None)."""
    if not text:
        return None, None
    parts = [p.strip() for p in text.split("-")]
    judge = parts[0] if parts and parts[0] else None
    clerk = parts[1] if len(parts) > 1 and parts[1] else None
    return judge, clerk


def _tidy_block(text: str | None) -> str | None:
    if not text:
        return None
    lines = [_clean(ln) for ln in text.split("\n")]
    out = "\n".join(ln for ln in lines if ln)
    return out or None


def parse_document(html: str) -> dict:
    """Header fields, résumé and text of a ruling's document page."""
    soup = BeautifulSoup(html, "html.parser")
    header: dict[str, str] = {}
    for tr in soup.find_all("tr"):
        tds = tr.find_all("td", recursive=False)
        if len(tds) != 2:
            continue
        label = _clean(tds[0].get_text(" ")).rstrip(":").strip()
        if label in {"N° affaire", "Autorité:, Date décision", "Juge", "Greffier",
                     "Publication (revue juridique)", "Ref. TF",
                     "Nom des parties contenant", "Résumé contenant"}:
            header[label] = (tds[1].get_text("\n") if label == "Résumé contenant"
                             else _clean(tds[1].get_text(" ")))
    auth_date = header.get("Autorité:, Date décision", "")
    # Word's HTML export splits a document into WordSection1, WordSection2, ...
    # (a new section per page-layout change); older rulings use Section1, ...
    sections = soup.find_all("div", class_=RE_SECTION_CLASS)
    return {
        "docket_number": header.get("N° affaire") or None,
        "authority": auth_date.split(",")[0].strip() or None if auth_date else None,
        "decision_date": parse_ch_date(auth_date),
        "judge": header.get("Juge") or None,
        "clerk": header.get("Greffier") or None,
        "publication": header.get("Publication (revue juridique)") or None,
        "ref_tf": header.get("Ref. TF") or None,
        "parties": header.get("Nom des parties contenant") or None,
        "resume": _tidy_block(header.get("Résumé contenant")),
        "full_text": "\n\n".join(t for t in (_document_text(d) for d in sections) if t),
    }


def _document_text(content) -> str:
    paragraphs = []
    for p in content.find_all("p"):
        t = _clean(p.get_text(" "))
        if t:
            paragraphs.append(t)
    if paragraphs:
        return "\n\n".join(paragraphs)
    return content.get_text("\n", strip=True)


class VDOmniScraper(BaseScraper):
    """CDAP / TA / CCST rulings from jurisprudence.vd.ch (see module doc)."""

    REQUEST_DELAY = 2.0
    TIMEOUT = 180  # a 1,000-row page takes ~1-2 min server-side
    MAX_ERRORS = 50
    # A handful of old TA fiches (1990s) carry no markup body at all on the
    # live portal and fall back to a résumé-only Decision (see
    # fetch_decision); the rare ones with no résumé either still return None,
    # cached as a gap so a nightly run never re-probes them, with headroom
    # above the generic 200 default so a cluster of them early in a
    # since=1800 backfill (fetched oldest-first) cannot abort the whole run.
    CACHE_NONE_AS_GAP = True
    MAX_NONE_RETURNS = 2000
    # Pauses before the 2nd and 3rd attempt at a listing page (seconds).
    RETRY_PAUSES = (10, 30)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.refetch_held = os.environ.get(REFETCH_ENV, "") == "1"
        self.refetch_ids = {x.strip() for x in os.environ.get(REFETCH_IDS_ENV, "").split(",") if x.strip()}
        self.skipped_federal = 0
        self.held_es = 0
        # (decision year, "listed" | "federal" | "held_es" | "held_direct" | "new")
        self.tally: Counter = Counter()
        # (es decision id, es date, portal date, nF30_KEY) found this run
        self.es_date_mismatches: list[tuple[str, str, str, str]] = []
        self._load_rulings()

    @property
    def court_code(self):
        return COURT

    # ── identity sidecar ────────────────────────────────────────────────
    def _sidecar(self) -> Path:
        return Path(getattr(self, "state_dir", None) or "state") / f"{COURT}.rulings.tsv"

    @staticmethod
    def _es_shard() -> Path:
        return Path(os.environ.get(ES_SHARD_ENV, REPO_ROOT / "output" / "decisions" / "es_vd_omni.jsonl"))

    @staticmethod
    def _own_shard() -> Path:
        return Path(os.environ.get(SHARD_ENV, REPO_ROOT / "output" / "decisions" / f"{COURT}.jsonl"))

    def _seed(self) -> int:
        """Write the sidecar from the frozen es shard and the scraper's own
        shard. Returns the number of rows written (0 = nothing to seed)."""
        rows: list[tuple[str, ...]] = []
        for shard, origin in ((self._es_shard(), "es"), (self._own_shard(), "direct")):
            try:
                if not shard.is_file():
                    continue
                with open(shard, "rb") as f:
                    for raw in f:
                        try:
                            rec = json.loads(raw)
                        except Exception:
                            continue
                        docket = (rec.get("docket_number") or "").strip()
                        did = rec.get("decision_id") or ""
                        if not docket or not did:
                            continue
                        regeste = rec.get("regeste") or ""
                        if origin == "es" and " | " in regeste:
                            regeste = regeste.split(" | ", 1)[1]  # es: "parties | résumé"
                        rows.append((make_decision_id(COURT, docket),
                                     str(rec.get("decision_date") or "")[:10],
                                     _nf30_from_url(rec.get("source_url")), did, origin,
                                     resume_key(regeste),
                                     es_header_date(rec.get("full_text"), docket) if origin == "es" else ""))
            except OSError as e:
                logger.warning(f"[{COURT}] cannot read {shard} to seed the sidecar: {e}")
        if not rows:
            return 0
        p = self._sidecar()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("# <base id>\\t<decision date>\\t<nF30_KEY>\\t<decision id>\\t<origin>\\t<résumé key>\\t<es header date>\n")
            for r in rows:
                f.write("\t".join(r) + "\n")
        os.replace(tmp, p)
        logger.info(f"[{COURT}] rulings sidecar seeded: {len(rows)} rows")
        return len(rows)

    def _load_rulings(self) -> None:
        # base id -> list of {"date", "nf30", "id", "origin"}
        self._rulings: dict[str, list[dict]] = {}
        p = self._sidecar()
        if not p.exists() and self._seed() == 0:
            logger.info(f"[{COURT}] no sidecar and nothing to seed it from: every listed ruling is new")
            return
        try:
            with open(p, encoding="utf-8") as f:
                for line in f:
                    if not line.strip() or line.startswith("#"):
                        continue
                    parts = line.rstrip("\n").split("\t")
                    if len(parts) < 4:
                        continue
                    self._add(parts[0], parts[1], parts[2], parts[3],
                              parts[4] if len(parts) > 4 else "",
                              parts[5] if len(parts) > 5 else "",
                              parts[6] if len(parts) > 6 else "")
        except OSError as e:
            logger.warning(f"[{COURT}] unreadable sidecar ({e}); delete it to reseed")
        logger.info(f"[{COURT}] rulings sidecar: {sum(map(len, self._rulings.values()))} rulings "
                    f"under {len(self._rulings)} dockets")

    def _add(self, base: str, d: str, nf30: str, did: str, origin: str, rk: str = "",
             hdate: str = "") -> None:
        self._rulings.setdefault(base, []).append(
            {"date": d, "nf30": nf30, "id": did, "origin": origin, "rk": rk, "hdate": hdate})

    def resolve(self, docket: str, decision_date: date | None, nf30,
                resume=None) -> tuple[str, str | None]:
        """(decision_id, held_by) for one listed ruling, reserving the id: the
        three passes of discover_new applied to it alone. held_by is "direct"
        or "es" when the corpus already holds the ruling, "es_other_date" when
        a frozen es row of the docket carries the same résumé under another
        date, else None (see the pass methods)."""
        return (self._match_held(docket, decision_date, nf30)
                or self._match_resume(docket, decision_date, nf30, resume)
                or self._new_id(docket, decision_date, nf30))

    @staticmethod
    def _keys(nf30) -> list[str]:
        return [nf30] if isinstance(nf30, str) else list(nf30)

    def _match_held(self, docket, decision_date, nf30) -> tuple[str, str] | None:
        """Pass 1: a ruling the sidecar knows by nF30_KEY, or a frozen es row of
        the docket with the same decision date: the date its own header gives
        ("Vaud Tribunal cantonal ... 10.07.1991 AC.1990.6992"), else its date
        field. When the header matches and the field does not, the field is
        wrong (often the date of the decision under appeal: AC.1990.6992 field
        1990-08-06, header and portal 1991-07-10): "es_other_date", reported
        for a date repair. An es row matches one listed ruling only: it is
        bound, so a second ruling of the docket on that date is not "held"
        too."""
        keys = self._keys(nf30)
        d = decision_date.isoformat() if decision_date else ""
        entries = self._rulings.get(make_decision_id(COURT, docket), [])
        for e in entries:
            if e["nf30"] and set(e["nf30"].split(",")) & set(keys):
                return e["id"], e["origin"] or "direct"
        for e in entries:
            # A frozen row's own header outranks its date field: 820 of the 821
            # rows whose field is wrong carry the portal's date in the header,
            # and 4 fields equal ANOTHER ruling of the docket (PS.1996.0189:
            # field 1997-01-20, header and résumé = the 1997-01-09 ruling).
            if not e["nf30"] and d and (e.get("hdate") or e["date"]) == d:
                e["nf30"] = ",".join(keys)
                if e["origin"] == "es" and e["date"] != d:
                    self.es_date_mismatches.append((e["id"], e["date"], d, keys[0]))
                    return e["id"], "es_other_date"
                return e["id"], e["origin"] or "es"
        return None

    def _match_resume(self, docket, decision_date, nf30, resume) -> tuple[str, str] | None:
        """Pass 2, after every date match of the walk is bound: a frozen es row
        of the docket with the same résumé under another date (FO.2023.0004:
        field and header 2023-06-27, portal 2023-07-11). The ruling is not fetched: a direct copy
        under the right date would be served next to the es copy (the build's
        date-agnostic pass folds only prefix-identical texts). The pair goes
        to state/vd_omni.es_date_mismatches.tsv for a date repair instead."""
        keys = self._keys(nf30)
        resumes = [resume] if isinstance(resume, str) or resume is None else list(resume)
        d = decision_date.isoformat() if decision_date else ""
        entries = self._rulings.get(make_decision_id(COURT, docket), [])
        for rk in (resume_key(r) for r in resumes):
            if len(rk) < MIN_EXACT_RESUME_KEY:
                continue
            for e in entries:
                if not e["nf30"] and e["origin"] == "es" and _same_resume(rk, e["rk"]):
                    e["nf30"] = ",".join(keys)
                    self.es_date_mismatches.append((e["id"], e["date"], d, keys[0]))
                    return e["id"], "es_other_date"
        return None

    def _new_id(self, docket, decision_date, nf30) -> tuple[str, None]:
        """Pass 3: the id of a ruling the corpus lacks, reserved at once."""
        keys = self._keys(nf30)
        base = make_decision_id(COURT, docket)
        d = decision_date.isoformat() if decision_date else ""
        used = {e["id"] for e in self._rulings.get(base, [])}
        if base not in used:
            did = base
        elif d and f"{base}_d{d.replace('-', '')}" not in used:
            did = f"{base}_d{d.replace('-', '')}"
        else:
            did = f"{base}_n{keys[0]}"
        self._add(base, d, ",".join(keys), did, "pending")
        return did, None

    def on_decision_persisted(self, decision) -> None:
        """run_scraper calls this right after it marks state: record the id
        so a later ruling of the docket gets a suffix, never this id."""
        nf30 = _nf30_from_url(getattr(decision, "source_url", None))
        base = make_decision_id(COURT, decision.docket_number)
        d = decision.decision_date.isoformat() if decision.decision_date else ""
        for e in self._rulings.get(base, []):
            if e["id"] == decision.decision_id:
                if e["origin"] == "direct":
                    return
                # every fiche of the ruling (resolve reserved them all)
                keys = [k for k in e["nf30"].split(",") if k]
                nf30 = ",".join(keys if nf30 in keys else [nf30, *keys])
                e.update(origin="direct", nf30=nf30, date=d)
                break
        else:
            self._add(base, d, nf30, decision.decision_id, "direct")
        rk = resume_key(getattr(decision, "regeste", None))
        p = self._sidecar()
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(f"{base}\t{d}\t{nf30}\t{decision.decision_id}\tdirect\t{rk}\n")

    def mark_run_complete(self, decisions: list) -> None:
        super().mark_run_complete(decisions)
        for d in decisions:
            self.on_decision_persisted(d)

    # ── discovery ───────────────────────────────────────────────────────
    def _listing_page(self, lo: date, hi: date) -> tuple[str | None, str]:
        """(html, "") for a result page that carries the hit counter, else
        (None, reason) after RETRY_PAUSES are spent. The portal fails
        intermittently: the same month answered 302 "Location:
        http://jurisprudence.vd.chAn error occurred while executing a method",
        then 200 with all 136 hits 13 s later (2026-11-08). Redirects are not
        followed (requests would try to resolve that host), and a retry is
        logged without "Max retries exceeded", which the health parser counts
        as a discovery error at any level."""
        form = dict(SEARCH_FORM)
        form["dEntscheiddatum"] = lo.strftime("%d.%m.%Y")
        form["dEntscheiddatumBis"] = hi.strftime("%d.%m.%Y")
        reason = ""
        for attempt in range(len(self.RETRY_PAUSES) + 1):
            if attempt:
                logger.warning(f"VD Omni: listing {lo}..{hi} attempt {attempt} of "
                               f"{len(self.RETRY_PAUSES) + 1} failed ({reason}); retrying")
                time.sleep(self.RETRY_PAUSES[attempt - 1])
            try:
                r = self.post(CGI_URL, data=form, allow_redirects=False)
            except Exception as e:
                reason = f"{type(e).__name__}: {e}".replace("Max retries exceeded", "retries exhausted")[:200]
                continue
            status = getattr(r, "status_code", 200)
            if status != 200:
                reason = f"HTTP {status} {(getattr(r, 'headers', {}) or {}).get('Location', '')[:80]}".strip()
                continue
            if parse_hit_count(r.text) is None:
                reason = f"no hit counter ({r.text[:60]!r})"
                continue
            return r.text, ""
        return None, reason

    def _search(self, lo: date, hi: date, split_on_failure: bool = True) -> list[dict] | None:
        """All hits decided in [lo, hi]; None (logged at ERROR, which the
        health check counts) when the window cannot be listed completely."""
        html, reason = self._listing_page(lo, hi)
        if html is None:
            if split_on_failure and lo < hi:
                # Smaller pages fail less often; each half gets its own retries.
                logger.warning(f"VD Omni: listing {lo}..{hi} kept failing ({reason}); splitting it")
                mid = lo + (hi - lo) // 2
                left = self._search(lo, mid, split_on_failure=False)
                right = self._search(mid + timedelta(days=1), hi, split_on_failure=False)
                return None if left is None or right is None else left + right
            logger.error(f"VD Omni: search failed for {lo}..{hi}: {reason}")
            return None
        hits = parse_hit_count(html)
        if hits > PAGE_SIZE:
            if lo >= hi:
                logger.error(f"VD Omni: search failed for {lo}: {hits} hits on one day exceed the page")
                return None
            mid = lo + (hi - lo) // 2
            left = self._search(lo, mid)
            right = self._search(mid + timedelta(days=1), hi)
            return None if left is None or right is None else left + right
        rows = parse_listing(html)
        if len(rows) != hits:
            logger.error(f"VD Omni: search failed for {lo}..{hi}: listed {len(rows)} of {hits}")
            return None
        return rows

    def discover_new(self, since_date=None) -> Iterator[dict]:
        if since_date and isinstance(since_date, str):
            since_date = date.fromisoformat(since_date)
        today = date.today()
        if since_date is None:
            m = today.month - NIGHTLY_MONTHS
            since_date = date(today.year + (m - 1) // 12, (m - 1) % 12 + 1, 1)
        windows = month_windows(max(since_date, EARLIEST), today)
        logger.info(f"VD Omni: {len(windows)} windows from {windows[0][0]} to {today}"
                    f"{' (refetching held es rows)' if self.refetch_held else ''}")
        # List every window first: an exact date match must win over a
        # résumé match, so all date matches of the walk are bound before any
        # résumé is compared (an es row dated like a later ruling of its
        # docket otherwise went to an earlier one with the same résumé).
        rulings: list[dict] = []
        failed_in_a_row = 0
        for lo, hi in windows:
            rows = self._search(lo, hi)
            if rows is None:
                failed_in_a_row += 1
                if failed_in_a_row >= MAX_FAILED_WINDOWS_IN_A_ROW:
                    logger.error(f"VD Omni: search failed for {failed_in_a_row} windows in a row; "
                                 f"portal down, stopping discovery at {lo}")
                    break
                continue
            failed_in_a_row = 0
            rows.sort(key=lambda r: (r["decision_date"] or date.min, int(r["nf30_key"])))
            cantonal = []
            for row in rows:
                year = row["decision_date"].year if row["decision_date"] else 0
                self.tally[(year, "listed")] += 1
                if row["authority"] in FEDERAL_AUTHORITIES:
                    self.skipped_federal += 1
                    self.tally[(year, "federal")] += 1
                    continue
                if not row["docket_number"]:
                    logger.warning(f"VD Omni: hit {row['nf30_key']} has no docket, skipped")
                    continue
                cantonal.append(row)
            rulings.extend(group_headnotes(cantonal))

        resolved: list[tuple[dict, str, str | None]] = []
        pending = []
        for row in rulings:
            hit = self._match_held(row["docket_number"], row["decision_date"], row["nf30_keys"])
            if hit:
                resolved.append((row, *hit))
            else:
                pending.append(row)
        still = []
        for row in pending:
            hit = self._match_resume(row["docket_number"], row["decision_date"],
                                     row["nf30_keys"], row["resumes"])
            if hit:
                resolved.append((row, *hit))
            else:
                still.append(row)
        for row in still:  # rulings come oldest first: the earliest takes the plain id
            resolved.append((row, *self._new_id(row["docket_number"], row["decision_date"],
                                                row["nf30_keys"])))
        resolved.sort(key=lambda x: (x[0]["decision_date"] or date.min, int(x[0]["nf30_key"])))
        # Before the first yield: a run stopped by --max, a timeout or
        # RuntimeMaxSec never reaches the end of this generator.
        self._write_date_mismatches()

        yielded = 0
        for row, did, held in resolved:
            year = row["decision_date"].year if row["decision_date"] else 0
            self.tally[(year, "rulings")] += 1
            if held == "es_other_date":
                self.tally[(year, "held_es_other_date")] += 1
                continue
            if held == "es":
                self.held_es += 1
                self.tally[(year, "held_es")] += 1
                if not (self.refetch_held or did in self.refetch_ids):
                    continue
            elif held or self.state.is_known(did):
                self.tally[(year, "held_direct")] += 1
                continue
            else:
                self.tally[(year, "new")] += 1
            row["decision_id"] = did
            yielded += 1
            yield row
        logger.info(f"VD Omni: discovery complete: {yielded} to fetch, "
                    f"{self.held_es} held as es rows, "
                    f"{len(self.es_date_mismatches)} held as es rows under another date, "
                    f"{self.skipped_federal} federal rulings skipped")

    def _write_date_mismatches(self) -> None:
        """Append this run's es-date mismatches to the report (one line per
        es id; earlier lines kept, so a nightly window never drops them)."""
        if not self.es_date_mismatches:
            return
        p = Path(getattr(self, "state_dir", None) or "state") / f"{COURT}.es_date_mismatches.tsv"
        seen = set()
        if p.exists():
            seen = {ln.split("\t", 1)[0] for ln in p.read_text(encoding="utf-8").splitlines()}
        new = [m for m in self.es_date_mismatches if m[0] not in seen]
        if not new:
            return
        with open(p, "a", encoding="utf-8") as f:
            if not seen:
                f.write("es_decision_id\tes_date\tportal_date\tnF30_KEY\n")
            for m in new:
                f.write("\t".join(m) + "\n")
        logger.info(f"VD Omni: {len(new)} es rows whose date differs from the portal's -> {p}")

    # ── fetch ───────────────────────────────────────────────────────────
    def fetch_decision(self, stub: dict) -> Decision | None:
        url = stub["url"]
        html, reason = None, ""
        for attempt in range(len(self.RETRY_PAUSES) + 1):
            if attempt:
                time.sleep(self.RETRY_PAUSES[attempt - 1])
            try:
                r = self.get(url, allow_redirects=False)
            except Exception as e:
                reason = f"{type(e).__name__}: {e}".replace("Max retries exceeded", "retries exhausted")[:200]
                continue
            if getattr(r, "status_code", 200) != 200:
                reason = f"HTTP {r.status_code}"
                continue
            html = r.text
            break
        if html is None:
            logger.warning(f"VD Omni: fetch failed for {stub['docket_number']} "
                           f"(nF30_KEY {stub['nf30_key']}): {reason}")
            return None
        if html.lstrip().startswith("#ERROR") or len(html) < 500:
            logger.warning(f"VD Omni: no document for {stub['docket_number']} "
                           f"(nF30_KEY {stub['nf30_key']}): {html[:40]!r}")
            return None
        doc = parse_document(html)
        if doc["docket_number"] and doc["docket_number"] != stub["docket_number"]:
            logger.warning(f"VD Omni: nF30_KEY {stub['nf30_key']} lists {stub['docket_number']} "
                           f"but the document says {doc['docket_number']}; skipped")
            return None
        full_text = doc["full_text"]
        if len(full_text) < 50:
            # Confirmed 2026-11-10 on ~200 old TA fiches (CR/PE/PS/RE dockets,
            # 1991-92): getMarkupDocument serves the registry card only (no
            # Section/WordSection div), and so does the print preview
            # (Aufruf=getDocument&Template=.../printdocument.fiw) -- there is
            # no markup body for these on the live portal at all, not a fetch
            # error. The registry card's own résumé is real court text (never
            # fabricated), so a ruling with one is still worth holding; only
            # a ruling with neither a body nor a usable résumé is unrecoverable.
            resume = _usable_resume(stub.get("resume") or doc["resume"])
            if resume:
                full_text = resume
            else:
                logger.warning(f"VD Omni: no markup body and no usable résumé for "
                               f"{stub['docket_number']} (nF30_KEY {stub['nf30_key']}): "
                               f"{len(full_text)} chars")
                return None
        decision_date = stub["decision_date"] or doc["decision_date"]
        if doc["decision_date"] and stub["decision_date"] and doc["decision_date"] != stub["decision_date"]:
            logger.warning(f"VD Omni: {stub['docket_number']} listed {stub['decision_date']}, "
                           f"document says {doc['decision_date']}; listing date kept")
        authority = stub.get("authority") or doc["authority"] or None
        ref_tf = doc["ref_tf"]
        return Decision(
            decision_id=stub["decision_id"],
            court=COURT,
            canton="VD",
            chamber=authority if authority and authority != "!" else None,
            docket_number=stub["docket_number"],
            decision_date=decision_date,
            publication_date=stub.get("publication_date"),
            language=detect_language(full_text) if len(full_text) > 100 else "fr",
            title=doc["parties"] or stub.get("parties"),
            # every headnote of the ruling (the document page shows one)
            regeste=stub.get("resume") or doc["resume"],
            full_text=full_text,
            judges=doc["judge"] or stub.get("judge"),
            clerks=doc["clerk"] or stub.get("clerk"),
            collection=doc["publication"],
            appeal_info=f"Ref. TF: {ref_tf}" if ref_tf else None,
            source_url=url,
            pdf_url=None,
            cited_decisions=extract_citations(full_text) if len(full_text) > 200 else [],
        )


def group_headnotes(rows: list[dict]) -> list[dict]:
    """One entry per ruling. The portal files a ruling once per headnote: the
    fiches of one docket and decision date carry the same document (identical
    text, checked on four such groups 2026-11-08) and a different résumé each
    (3,433 of the 44,196 cantonal fiches' docket+date groups have 2-11
    fiches). The entry keeps the lowest nF30_KEY's fields, every key in
    nf30_keys, every résumé in resumes, and the résumés joined in "resume"."""
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        groups.setdefault((r["docket_number"], r["decision_date"]), []).append(r)
    out = []
    for members in groups.values():
        members.sort(key=lambda r: int(r["nf30_key"]))
        head = dict(members[0])
        head["nf30_keys"] = [m["nf30_key"] for m in members]
        head["resumes"] = [m.get("resume") for m in members]
        seen, parts = set(), []
        for m in members:
            t = (m.get("resume") or "").strip()
            if t and t not in seen:
                seen.add(t)
                parts.append(t)
        head["resume"] = "\n\n".join(parts) or None
        out.append(head)
    out.sort(key=lambda r: (r["decision_date"] or date.min, int(r["nf30_key"])))
    return out


def es_header_date(text: str | None, docket: str) -> str:
    """ISO date the entscheidsuche header of a frozen row gives for its docket
    ("Vaud Tribunal cantonal Cour de droit administratif et public 10.07.1991
    AC.1990.6992" opens the text), else ""."""
    if not text or not docket:
        return ""
    m = re.search(r"(\d{2})\.(\d{2})\.(\d{4})\s+" + re.escape(docket), text[:400])
    if not m:
        return ""
    try:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1))).isoformat()
    except ValueError:
        return ""


def _same_resume(a: str, b: str) -> bool:
    """Two résumé keys describe the same ruling: identical and at least
    MIN_EXACT_RESUME_KEY long ("Etendue de l'obligation d'entretien en cas de
    déneigement"; never the bare placeholder "Résumé"), or one a prefix of the
    other and at least MIN_RESUME_KEY long."""
    if len(a) >= MIN_EXACT_RESUME_KEY and a == b:
        return True
    return (len(a) >= MIN_RESUME_KEY and len(b) >= MIN_RESUME_KEY
            and (a.startswith(b) or b.startswith(a)))


def resume_key(text: str | None) -> str:
    """The first RESUME_KEY_CHARS letters/digits of a résumé, lower-cased:
    what the listing and the frozen es regeste share when they describe the
    same ruling."""
    return re.sub(r"[^0-9a-zà-ÿ]", "", (text or "").lower())[:RESUME_KEY_CHARS]


def _nf30_from_url(url: str | None) -> str:
    m = RE_NF30.search(url or "")
    return m.group(1) if m else ""


# A fiche whose markup body is missing (fetch_decision) still carries real
# text here unless the registry itself has nothing but the bare placeholder.
PLACEHOLDER_RESUME = "Résumé"


def _usable_resume(text: str | None) -> str | None:
    """A non-empty, non-placeholder résumé, stripped; None when there is
    truly nothing (the bare "Résumé" the registry prints for a case with no
    headnote on file, or blank/whitespace)."""
    t = (text or "").strip()
    return t if t and t != PLACEHOLDER_RESUME else None
