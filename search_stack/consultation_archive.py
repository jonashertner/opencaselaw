"""Vernehmlassungs-Materialien that Fedlex does not hold, fed into the
consultations.db work database of search_stack/build_consultation_corpus.py.

Fedlex lists every federal consultation procedure since 1993 but carries
files only from about 2005 on (2026-10-19 census: of the 407 procedures of
1995-2004, 126 have any document). Two other official sources held the
older documents; this module reads them and attaches what it finds to the
Fedlex procedure it belongs to, or adds a procedure Fedlex lacks:

``admin-pc`` -- **the Federal Chancellery's old consultation index**,
    ``www.admin.ch/ch/{d,f,i}/gg/pc/ind<YEAR>.html`` (1992-2015, 404 since
    the 2016 relaunch), read from the Internet Archive. One entry per
    procedure with its deadline and the documents of the opening
    ("Eröffnung": Vorlage, Bericht) and of the result ("Ergebnis"). From
    2006 the pages link the Chancellery's copies under
    ``gg/pc/documents/<id>/<Name>_<d|f|i>.pdf``; the earlier versions of the
    same page link the offices' own sites instead, so both the latest
    snapshot and the latest pre-2006 snapshot of each page are read. Many
    entries before 2000 never had a file ("Unterlagen können bezogen werden
    bei ..."); those are recorded without documents.

``bj`` -- **the Federal Office of Justice's legislative projects**,
    ``www.bj.admin.ch/<lang>/{laufende,abgeschlossene}-rechtsetzungsprojekte-*``
    (live, server-rendered): expert-commission drafts, Begleitberichte,
    Vernehmlassungs compilations from ZPO 2003 on. The live site carries
    the old archive projects too, so it is the primary source.

``bj-wayback`` -- the old BJ archive pages
    (``www.bj.admin.ch/bj/<lang>/home/<area>/gesetzgebung/archiv/<slug>.html``,
    live 404, archived to 2025-12): only the files the live project page no
    longer lists, and projects the live site dropped.

Provenance is never lost: every file row carries its source, its original
URL and the page that linked it; a Wayback file's ``file_url`` is the
snapshot URL (fetched in its ``id_`` form, see build_consultation_corpus.
download_url). Labels are the source's own link texts (R3): a document's
title is what the page printed ("Vorlage", "Ergebnis: Bericht", "Vorentwurf
für eine Schweizerische Zivilprozessordnung"); the doc_type slug is only the
filter class. Nothing here builds a citation (R1).

Matching to Fedlex (``consultation_procedures.event_id`` proj/<y>/<n>/cons_1):
a Fedlex link on the page wins; otherwise the deadline (or opening date)
printed on the page must equal Fedlex's and the titles agree, or the titles
agree closely and the dates do not contradict. Two equally good candidates
are recorded as ``ambiguous`` and attached to neither. Each language entry
of the Chancellery's index is matched on its own and grouped by the
procedure it matched. A BJ project page spans several consultations, so its
documents are distributed by their own dates (split_for_match). A file
whose type and language Fedlex already holds for the procedure is not
added again.

The Internet Archive is asked politely: one connection, >= 2 s between
requests, an identifying User-Agent, a stop on 429/403; availability comes
from the CDX index (one prefix query per language for the Chancellery's
document store, one exact query per other file), never from guessing.

    python3 -m search_stack.consultation_archive discover --work-db W \\
        --source admin-pc --cache-dir C
    python3 -m search_stack.consultation_archive census --work-db W --source all \\
        --cache-dir C --report census.json
    # then the usual: build_consultation_corpus fetch / publish
"""
from __future__ import annotations

import argparse
import hashlib
import html as htmllib
import json
import logging
import os
import re
import sqlite3
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from search_stack import build_consultation_corpus as bcc  # noqa: E402
from search_stack.build_botschaft_corpus import SourceUnavailable  # noqa: E402

log = logging.getLogger("consultation_archive")

SOURCE_ADMIN_PC = "admin_pc_wayback"
SOURCE_BJ = "bj"
SOURCE_BJ_WAYBACK = "bj_wayback"
SOURCES = (SOURCE_ADMIN_PC, SOURCE_BJ, SOURCE_BJ_WAYBACK)

USER_AGENT = ("opencaselaw-materialien/0.5 (Vernehmlassungen archive; "
              "+https://opencaselaw.ch)")
WAYBACK = "https://web.archive.org"
CDX = WAYBACK + "/cdx/search/cdx"
LANGS = ("de", "fr", "it")
URN = "urn:opencaselaw"

# Seconds between two requests to one host (the fetch side uses the same
# table, build_consultation_corpus.HOST_MIN_INTERVAL).
MIN_INTERVAL = dict(bcc.HOST_MIN_INTERVAL)
RETRIES = 3
RETRY_BASE_SECONDS = 10.0


# ── HTTP ───────────────────────────────────────────────────────────────

class NotFound(Exception):
    """The page or capture is not there (404/410), or the Wayback Machine
    excludes that one capture from replay (403)."""

    def __init__(self, url: str, status: int = 404):
        super().__init__(url)
        self.status = status


def _default_opener(url: str, timeout: int = 90) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


class ArchiveHttp:
    """Sequential GETs with a per-host minimum interval, retries on 5xx and
    timeouts, a stop (SourceUnavailable) on 403/429, and an optional cache
    keyed by URL so a re-run of discovery or the census costs no requests.
    ``opener(url) -> bytes`` is replaced in tests."""

    def __init__(self, cache_dir: str | Path | None = None,
                 opener: Callable[[str], bytes] | None = None,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic,
                 intervals: dict[str, float] | None = None,
                 retries: int = RETRIES):
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.opener = opener or _default_opener
        self.sleep, self.clock = sleep, clock
        self.intervals = MIN_INTERVAL if intervals is None else intervals
        self.retries = retries
        self._last: dict[str, float] = {}
        self.requests = 0

    def _cache_path(self, url: str) -> Path | None:
        if not self.cache_dir:
            return None
        return self.cache_dir / (hashlib.sha1(url.encode("utf-8")).hexdigest() + ".bin")

    def _wait(self, host: str) -> None:
        gap = self.intervals.get(host, 0.0)
        last = self._last.get(host)
        if gap and last is not None:
            left = last + gap - self.clock()
            if left > 0:
                self.sleep(left)

    def get(self, url: str) -> bytes:
        cp = self._cache_path(url)
        if cp is not None and cp.exists():
            return cp.read_bytes()
        host = urllib.parse.urlsplit(url).netloc.lower()
        attempt = 0
        while True:
            self._wait(host)
            self.requests += 1
            log.info("  GET %s", url if len(url) < 160 else url[:157] + "...")
            try:
                data = self.opener(url)
                self._last[host] = self.clock()
                break
            except urllib.error.HTTPError as e:
                self._last[host] = self.clock()
                if e.code == 403 and host == bcc.WAYBACK_HOST:
                    raise NotFound(url, 403) from e   # one capture excluded from replay
                if e.code in bcc.FATAL_HTTP_STATUSES:
                    raise SourceUnavailable(f"GET {url} answered HTTP {e.code}",
                                            status=e.code) from e
                if e.code in (404, 410):
                    raise NotFound(url, e.code) from e
                if e.code < 500 or attempt >= self.retries:
                    raise
            except (urllib.error.URLError, OSError):
                self._last[host] = self.clock()
                if attempt >= self.retries:
                    raise
            attempt += 1
            log.warning("  retry %d for %s", attempt, url[:120])
            self.sleep(RETRY_BASE_SECONDS * attempt)
        if cp is not None:
            tmp = cp.with_suffix(".part")
            tmp.write_bytes(data)
            os.replace(tmp, cp)
        return data


def get_page(http: ArchiveHttp, url: str, stats: dict) -> str | None:
    """A page, or None when it is gone (404) or keeps failing after the
    retries (a Wayback capture answering 500): listed in
    ``stats['pages_failed']``, never fatal. A refusal (429/403) still stops
    the run (SourceUnavailable)."""
    try:
        return decode_html(http.get(url))
    except NotFound as e:
        reason = str(e.status)
    except SourceUnavailable:
        raise
    except (urllib.error.URLError, OSError) as e:
        reason = f"{type(e).__name__}: {e}"[:200]
    stats.setdefault("pages_failed", []).append({"url": url, "reason": reason})
    log.warning("  page skipped (%s): %s", reason, url)
    return None


def decode_html(data: bytes) -> str:
    """The pages declare ISO-8859-1 (served as cp1252) or UTF-8."""
    head = data[:2048].lower()
    if b"charset=utf-8" in head or b'charset="utf-8"' in head:
        return data.decode("utf-8", errors="replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


# ── Wayback CDX ────────────────────────────────────────────────────────

CDX_FIELDS = ("urlkey", "timestamp", "original", "mimetype", "statuscode", "digest")


def cdx_query(http: ArchiveHttp, url: str, *, match_type: str | None = None,
              collapse: str | None = None, ok_only: bool = True,
              page_size: int = 5000) -> list[dict]:
    """All CDX rows for ``url`` (resume-key paging), as dicts of CDX_FIELDS."""
    rows: list[dict] = []
    resume = None
    while True:
        params = [("url", url), ("output", "json"), ("fl", ",".join(CDX_FIELDS)),
                  ("limit", str(page_size)), ("showResumeKey", "true")]
        if match_type:
            params.append(("matchType", match_type))
        if collapse:
            params.append(("collapse", collapse))
        if ok_only:
            params.append(("filter", "statuscode:200"))
        if resume:
            params.append(("resumeKey", resume))
        data = http.get(CDX + "?" + urllib.parse.urlencode(params))
        text = data.decode("utf-8", errors="replace").strip()
        table = json.loads(text) if text else []
        resume = None
        body = table[1:] if table and table[0] and table[0][0] == "urlkey" else table
        for r in body:
            if not r:
                continue
            if len(r) == 1:  # the resume key follows an empty row
                resume = r[0]
                continue
            rows.append(dict(zip(CDX_FIELDS, r)))
        if not resume:
            return rows


def norm_url_key(url: str) -> str:
    """host/path, lower case, without scheme, port, query or fragment: the
    shape CDX compares (its urlkey is case-folded)."""
    p = urllib.parse.urlsplit(url.strip())
    host = p.netloc.lower().split("@")[-1].split(":")[0]
    path = urllib.parse.unquote(p.path)
    return (host + path).lower()


def snapshot_url(timestamp: str, original: str) -> str:
    return f"{WAYBACK}/web/{timestamp}/{original}"


def latest_by_key(rows: Iterable[dict], prefer_pdf: bool = False) -> dict[str, dict]:
    """Newest 200 capture per normalised original URL (a PDF capture over an
    HTML soft-404 when ``prefer_pdf``)."""
    out: dict[str, dict] = {}
    for r in rows:
        if r.get("statuscode") not in (None, "200"):
            continue
        key = norm_url_key(r["original"])
        cur = out.get(key)
        rank = ((not prefer_pdf or "pdf" in (r.get("mimetype") or "")), r["timestamp"])
        if cur is None or rank > ((not prefer_pdf or "pdf" in (cur.get("mimetype") or "")),
                                  cur["timestamp"]):
            out[key] = r
    return out


# ── text helpers ───────────────────────────────────────────────────────

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_DATE_RE = r"(\d{1,2})\.\s?(\d{1,2})\.\s?(\d{4})"


def html_text(fragment: str) -> str:
    s = re.sub(r"<br\s*/?>", "\n", fragment, flags=re.IGNORECASE)
    s = re.sub(r"<!--.*?-->", " ", s, flags=re.DOTALL)
    s = _TAG_RE.sub(" ", s)
    s = htmllib.unescape(s).replace("\xa0", " ")
    return "\n".join(_WS_RE.sub(" ", ln).strip() for ln in s.split("\n")).strip()


def one_line(fragment: str) -> str:
    return _WS_RE.sub(" ", html_text(fragment)).strip()


def _iso(d: str, m: str, y: str) -> str | None:
    try:
        return date(int(y), int(m), int(d)).isoformat()
    except ValueError:
        return None


def norm_title(s: str | None) -> str:
    s = unicodedata.normalize("NFKD", (s or "").lower())
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"[^\w]+", " ", s)
    return _WS_RE.sub(" ", s).strip()


# Words that say nothing about WHICH act a title is about (norm_title form:
# lower case, no accents): articles, prepositions, and the generic words of
# legislative titles ("Änderung", "Verordnung", "Übereinkommen", ...).
_STOPWORDS = frozenset("""
    der die das des dem den und oder fur uber zur zum vom von mit eine einer eines
    les des une sur pour par aux avec dans della delle degli dello per sul sulla con nel
    nella alla dell
    revision revisione teilrevision totalrevision anderung anderungen aenderung
    modification modifications modifica modifiche bundesgesetz bundesgesetzes gesetz
    gesetzes bundesbeschluss verordnung verordnungen ordonnance ordonnances ordinanza
    ordinanze loi legge federale federal schweizerischen schweizerisches schweizerische
    suisse svizzera umsetzung genehmigung approbation approvazione entwurf vorentwurf
    projet avant progetto avamprogetto ubereinkommen convention convenzione
    protokoll protocole protocollo bundesrat bundesrates
    bericht berichts erlauternder eroffnung vernehmlassung vernehmlassungen
    vernehmlassungsverfahren vernehmlassungsverfahrens vorlage ergebnis ergebnisse
    rapport rapports explicatif ouverture consultation procedure resultats resultat
    rapporto esplicativo apertura consultazione procedura risultati risultato
    schutz protection protezione massnahmen mesures misure recht rechts droit diritto
    international internationale internationalen internationales internazionale
    internazionali
""".split())


def _tokens(norm: str) -> set[str]:
    return {t for t in norm.split() if len(t) >= 4 and t not in _STOPWORDS}


def title_similarity(a: str | None, b: str | None) -> float:
    """Character similarity of the normalised titles, or — when one title's
    significant words all appear in the other ("Vereinheitlichung des
    Zivilprozessrechts" in Fedlex's "Vereinheitlichung des
    Zivilprozessrechts (Schaffung einer ...)") — a containment score: 0.8
    for two or more words, 0.6 for one long word."""
    na, nb = norm_title(a), norm_title(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    ratio = SequenceMatcher(None, na, nb, autojunk=False).ratio()
    ta, tb = _tokens(na), _tokens(nb)
    short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    contained = 0.0
    if short and short <= long_:
        if len(short) >= 2:
            contained = 0.8
        elif len(next(iter(short))) >= 10:
            contained = 0.6
    return max(ratio, contained)


# ── document type classes ──────────────────────────────────────────────
# The slug is a FILTER CLASS for search_botschaft(doc_type=...); the label
# users see is always the page's own text (consultation_documents.title_*).

_RESULT_RE = re.compile(r"ergebnis|r[ée]sultat|risultat|auswertung|zusammenfassung der verneh",
                        re.IGNORECASE)
_SUBMISSIONS_RE = re.compile(r"stellungnahmen|zusammenstellung|prises de position|"
                             r"r[ée]ponses|pareri|risposte", re.IGNORECASE)
_REPORT_RE = re.compile(r"bericht|rapport|rapporto|erl[äa]uter|explicati|esplicativ|"
                        r"message|messaggio", re.IGNORECASE)
_DRAFT_RE = re.compile(r"vorlage|entwurf|projet|progetto|avamprogetto|avant-projet|"
                       r"gesetzestext|texte|testo", re.IGNORECASE)
_MINUTES_RE = re.compile(r"protokoll|proc[èe]s-verbal|verbale", re.IGNORECASE)


def classify(group: str | None, label: str, filename: str = "") -> int:
    """Fedlex draft-document-type code for a document the page lists
    (11 Vorlage, 12 Erläuternder Bericht, 15 Diverses, 21 Stellungnahmen,
    31 Ergebnisbericht), from the page's group heading, link text and file
    name, most specific first."""
    g, t = group or "", f"{label} {filename.replace('_', ' ').replace('-', ' ')}"
    if _MINUTES_RE.search(t):
        return 15
    if _RESULT_RE.search(g) or _RESULT_RE.search(t):
        return 21 if _SUBMISSIONS_RE.search(t) and not re.search(r"bericht|rapport", label, re.IGNORECASE) else 31
    if _SUBMISSIONS_RE.search(t):
        return 21
    if _REPORT_RE.search(t):
        return 12
    if _DRAFT_RE.search(t):
        return 11
    return 15


# ── source 1: the Chancellery's index, 1995-2004 ───────────────────────

PC_YEARS = tuple(range(1995, 2005))
PC_LANG_DIR = {"de": "d", "fr": "f", "it": "i"}
PC_NEW_TEMPLATE_FROM = "2006"     # snapshots from then on link gg/pc/documents/
_PC_OPEN_RE = re.compile(r"(?:er[öo]ffnet am|ouverte le|avviata il)\W{0,12}" + _DATE_RE, re.IGNORECASE)
_PC_DEADLINE_RE = re.compile(
    r"(?:Frist|abgelaufen am|Date limite[^:0-9]{0,40}|D[ée]lai|Termine[^:0-9]{0,40})"
    r"\s*:?\s*" + _DATE_RE, re.IGNORECASE)
_PC_GROUP_RE = re.compile(r"^(er[öo]ffnung|ergebnis|ouverture|r[ée]sultats?|apertura|risultat[oi])$",
                          re.IGNORECASE)
_LINK_RE = re.compile(r"<a\b([^>]*)>(.*?)</a>", re.IGNORECASE | re.DOTALL)
_HREF_RE = re.compile(r"""href\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""", re.IGNORECASE)
_BOLD_RE = re.compile(r"<b>(.*?)</b>", re.IGNORECASE | re.DOTALL)
_DEPT_RE = re.compile(r"<h[23][^>]*>\s*<a[^>]*name\s*=\s*['\"]?([A-Za-z]+)['\"]?[^>]*>(.*?)</a>",
                      re.IGNORECASE | re.DOTALL)
_LANG_SUFFIX_RE = re.compile(r"[_-](d|f|i)$", re.IGNORECASE)
_MULTI_LANG_SUFFIX_RE = re.compile(r"(?:[_-][dfi]){2,}$", re.IGNORECASE)   # Ergebnisse_d_f_i.pdf
# The pages' department anchors per language, keyed to the German ones (the
# FR/IT pages name the Chancellery CHF/CAF, the EJPD DFJP/DFGP, ...).
DEPT_CANON = {
    "CHF": "BK", "CAF": "BK", "DFAE": "EDA", "DFI": "EDI", "DFJP": "EJPD", "DFGP": "EJPD",
    "DDPS": "VBS", "DFF": "EFD", "DFE": "EVD", "DETEC": "UVEK", "DATEC": "UVEK",
    "DEFR": "WBF",
    # before 1998: Verkehrs- und Energiewirtschafts-, Militär-, Volkswirtschaftsdepartement
    "EVED": "UVEK", "DFTCE": "UVEK", "EMD": "VBS", "DMF": "VBS", "DFEP": "EVD",
}
_PC_DOC_ID_RE = re.compile(r"/gg/pc/documents/(\d+)/", re.IGNORECASE)


def pc_index_url(lang: str, year: int) -> str:
    return f"http://www.admin.ch/ch/{PC_LANG_DIR[lang]}/gg/pc/ind{year}.html"


@dataclass
class PcLink:
    url: str                 # absolute original URL
    label: str               # the page's text for it ("Ergebnis: Bericht")
    group: str | None        # Eröffnung / Ergebnis / ... (as printed), None in the old layout
    text: str                # the link text alone


@dataclass
class PcEntry:
    lang: str
    year: int
    department: str | None
    title: str
    opened: str | None
    deadline: str | None
    links: list[PcLink] = field(default_factory=list)
    other_links: int = 0     # HTML pages and mail links: counted, not ingested
    ordinal: int = 0         # position within its department on the page


def _href(attrs: str) -> str | None:
    m = _HREF_RE.search(attrs)
    if not m:
        return None
    return htmllib.unescape(next(g for g in m.groups() if g is not None)).strip()


_OFFICIAL_PUB_RE = re.compile(r"/(?:ff|as|sr|ro|rs|fu|ru)/\d", re.IGNORECASE)


def _is_document_link(url: str) -> bool:
    low = url.lower().split("?")[0]
    if _OFFICIAL_PUB_RE.search(low):
        return False          # Bundesblatt / AS / SR: Fedlex and materialien.db hold them
    host = urllib.parse.urlsplit(url).netloc.lower()
    if not host.endswith("admin.ch") and not host.endswith("fedpol.ch") and \
            not host.endswith("bakom.ch"):
        return False
    return low.endswith(".pdf") or "/gg/pc/documents/" in low


def parse_pc_index(page: str, *, lang: str, year: int, base_url: str) -> list[PcEntry]:
    """Every procedure entry of one ind<YEAR>.html page, either layout."""
    starts = [m.start() for m in re.finditer(r"<li>\s*<strong>", page, re.IGNORECASE)]
    depts = [(m.start(), m.group(1).upper(), one_line(m.group(2)))
             for m in _DEPT_RE.finditer(page)]
    entries: list[PcEntry] = []
    ordinal: dict[str | None, int] = {}
    for i, st in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(page)
        nxt_dept = next((p for p, _, _ in depts if st < p < end), None)
        if nxt_dept is not None:
            end = nxt_dept
        body = page[st:end]
        m = re.search(r"<strong>(.*?)</strong>", body, re.IGNORECASE | re.DOTALL)
        title = one_line(m.group(1)) if m else ""
        if not title:
            continue
        dept = next((code for p, code, _ in reversed(depts) if p < st), None)
        dept = DEPT_CANON.get(dept, dept) if dept else None
        text = html_text(body)
        mo, md = _PC_OPEN_RE.search(text), _PC_DEADLINE_RE.search(text)
        entry = PcEntry(lang=lang, year=year, department=dept, title=title,
                        opened=_iso(*mo.groups()) if mo else None,
                        deadline=_iso(*md.groups()) if md else None)
        ordinal[dept] = ordinal.get(dept, 0) + 1
        entry.ordinal = ordinal[dept]
        seen: set[str] = set()
        for lm in _LINK_RE.finditer(body):
            href = _href(lm.group(1))
            if not href or href.startswith(("#", "mailto:", "javascript:")):
                continue
            url = urllib.parse.urljoin(base_url, href)
            if not _is_document_link(url):
                if not _OFFICIAL_PUB_RE.search(url):
                    entry.other_links += 1
                continue
            if url in seen:
                continue
            seen.add(url)
            before = body[:lm.start()]
            group = None
            for bm in _BOLD_RE.finditer(before):
                g = one_line(bm.group(1)).rstrip(":").strip()
                if _PC_GROUP_RE.match(g):
                    group = g
            link_text = one_line(lm.group(2))
            # The result group prints "<b>Ergebnis</b>: <a>Bericht</a>";
            # the page's own words for the link are then "Ergebnis: Bericht".
            label = link_text
            if group and _RESULT_RE.search(group) and not _RESULT_RE.search(link_text):
                label = f"{group}: {link_text}"
            entry.links.append(PcLink(url=url, label=label, group=group, text=link_text))
        entries.append(entry)
    return entries


def pc_document_key(url: str) -> tuple[str, str | None]:
    """(document key, language of the file or None): Bericht_d.pdf,
    Bericht_f.pdf and Bericht_i.pdf of one procedure are one document."""
    p = urllib.parse.urlsplit(url)
    path = urllib.parse.unquote(p.path)
    stem = path.rsplit("/", 1)[-1]
    stem = re.sub(r"\.pdf$", "", stem, flags=re.IGNORECASE)
    lang = None
    m = None if _MULTI_LANG_SUFFIX_RE.search(stem) else _LANG_SUFFIX_RE.search(stem)
    if m:
        lang = {"d": "de", "f": "fr", "i": "it"}[m.group(1).lower()]
        stem = stem[:m.start()]
    dm = _PC_DOC_ID_RE.search(path)
    if dm:
        return f"{dm.group(1)}/{stem}", lang
    # an office's own site: /ch/d/... and /ch/f/... are the language branches
    norm_dir = re.sub(r"/([dfi])/", "/x/", path.rsplit("/", 1)[0].lower())
    return f"{p.netloc.lower().split(':')[0]}{norm_dir}/{stem}", lang


@dataclass
class PcProcedure:
    """One procedure across the DE/FR/IT pages of one year."""
    year: int
    entries: dict[str, PcEntry] = field(default_factory=dict)   # lang -> entry
    old_entries: dict[str, PcEntry] = field(default_factory=dict)
    pages: dict[str, str] = field(default_factory=dict)          # lang -> snapshot URL
    match_key: str | None = None    # the Fedlex procedure its entries matched

    def match(self) -> Match | None:
        """The match of the entries (all name the same procedure, or none)."""
        best = None
        for e in list(self.entries.values()) + list(self.old_entries.values()):
            m = getattr(e, "match", None)
            if m is not None and m.procedure_uri == self.match_key and m.procedure_uri:
                if best is None or (m.score or 0) > (best.score or 0):
                    best = m
        return best

    def title(self, lang: str) -> str | None:
        e = self.entries.get(lang) or self.old_entries.get(lang)
        return e.title if e else None

    @property
    def deadline(self) -> str | None:
        for e in list(self.entries.values()) + list(self.old_entries.values()):
            if e.deadline:
                return e.deadline
        return None

    @property
    def opened(self) -> str | None:
        for e in list(self.entries.values()) + list(self.old_entries.values()):
            if e.opened:
                return e.opened
        return None

    @property
    def department(self) -> str | None:
        for e in list(self.entries.values()) + list(self.old_entries.values()):
            if e.department:
                return e.department
        return None

    def doc_ids(self) -> set[str]:
        return {m.group(1) for e in self.entries.values() for lk in e.links
                for m in [_PC_DOC_ID_RE.search(lk.url)] if m}

    def key(self) -> str:
        ids = sorted(self.doc_ids(), key=int)
        if ids:
            return ids[0]
        basis = "|".join([str(self.year), self.department or "",
                          norm_title(self.title("de") or self.title("fr") or self.title("it")),
                          self.deadline or ""])
        return "h" + hashlib.sha1(basis.encode("utf-8")).hexdigest()[:10]


def fill_dates_from_old_layout(new: list[PcEntry], old: list[PcEntry]) -> int:
    """Some later captures of a page print no deadline (the Italian pages of
    1995-2003 as captured 2011-2019) where the 2005 capture of the SAME page
    does: take the dates from the old entry with the same department and a
    near-identical title. Returns how many entries were filled."""
    n = 0
    for e in new:
        if e.deadline and e.opened:
            continue
        best = max(((title_similarity(e.title, o.title), o) for o in old
                    if o.department == e.department), key=lambda x: x[0], default=(0, None))
        if best[1] is not None and best[0] >= 0.9:
            e.deadline = e.deadline or best[1].deadline
            e.opened = e.opened or best[1].opened
            n += 1
    return n


def merge_pc_languages(year: int, new: dict[str, list[PcEntry]],
                       old: dict[str, list[PcEntry]] | None = None,
                       match: Callable[[PcEntry], Match] | None = None) -> list[PcProcedure]:
    """Align the DE/FR/IT entries of one year into procedures.

    ``match(entry)`` (when given) matches one entry ALONE, by its own
    language's title against the same language's Fedlex title: entries that
    match the same Fedlex procedure are one procedure, and an entry is never
    joined to a procedure matched to another. Then a shared Chancellery
    document id; then department + deadline when exactly one procedure
    qualifies. Never position: the language pages do not list a department's
    entries in the same order (1998: AHV 11th and BVG 1st revision, same
    deadline, swapped). Old-layout entries join by match, else by title in
    their own language with an equal deadline."""
    procs: list[PcProcedure] = []

    def key_of(e: PcEntry) -> str | None:
        if match is None:
            return None
        m = match(e)
        e.match = m  # type: ignore[attr-defined]
        return m.procedure_uri

    def compatible(p: PcProcedure, key: str | None) -> bool:
        return p.match_key is None or key is None or p.match_key == key

    def attach(e: PcEntry, slot: str) -> bool:
        key = key_of(e)
        cands = [p for p in procs if e.lang not in getattr(p, slot) and compatible(p, key)]
        hit = next((p for p in cands if key and p.match_key == key), None)
        ids = {m.group(1) for lk in e.links for m in [_PC_DOC_ID_RE.search(lk.url)] if m}
        if hit is None and ids:
            hit = next((p for p in cands if ids & p.doc_ids()), None)
        if hit is None and slot == "old_entries":
            scored = [(title_similarity(e.title, p.title(e.lang)), p) for p in cands
                      if not (e.deadline and p.deadline and e.deadline != p.deadline)]
            scored = [x for x in scored if x[0] >= 0.8]
            if scored:
                hit = max(scored, key=lambda x: x[0])[1]
        if hit is None and e.deadline:
            same = [p for p in cands if p.department == e.department
                    and p.deadline == e.deadline and p.match_key == key]
            if len(same) == 1:
                hit = same[0]
        if hit is None:
            procs.append(PcProcedure(year=year, match_key=key, **{slot: {e.lang: e}}))
            return False
        getattr(hit, slot)[e.lang] = e
        hit.match_key = hit.match_key or key
        return True

    for lg in LANGS:
        for e in new.get(lg, []):
            attach(e, "entries")
    for lg in LANGS:
        for e in (old or {}).get(lg, []):
            attach(e, "old_entries")
    return procs


# ── matching ───────────────────────────────────────────────────────────

# Title agreement needed with an equal deadline / opening date, and without.
# Calibrated on the 2026-10-20 census (runbooks/materialien_completeness.md).
DATED_MIN_SIMILARITY = 0.55
UNDATED_MIN_SIMILARITY = 0.88


@dataclass
class Match:
    procedure_uri: str | None
    method: str
    score: float | None = None


def load_candidates(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute(
        """SELECT p.procedure_uri, p.event_id, p.year, p.title_de, p.title_fr, p.title_it,
                  p.start_date, p.end_date, p.source, v.label_de AS institution_label
           FROM consultation_procedures p
           LEFT JOIN consultation_vocab v ON v.uri = p.institution_uri""")]


_JUSTICE_RE = re.compile(r"\bJustiz|\bEJPD\b|Justiz- und Polizeidepartement", re.IGNORECASE)


def is_justice_institution(c: dict) -> bool:
    """Fedlex files the BJ's consultations under ~30 institution URIs (the
    office, its divisions, the EJPD): all carry "Justiz"/"EJPD" in the label."""
    return bool(_JUSTICE_RE.search(c.get("institution_label") or ""))


def _year_of(s: str | None) -> int | None:
    return int(s[:4]) if s and s[:4].isdigit() else None


def match_procedure(titles: dict[str, str | None], *, year: int | None,
                    opened: str | None, deadline: str | None,
                    candidates: list[dict], fedlex_event_ids: Iterable[str] = (),
                    same_source_only: str | None = None,
                    institution: Callable[[dict], bool] | None = None) -> Match:
    """The procedure an archive entry belongs to (see the module docstring)."""
    ids = set(fedlex_event_ids)
    if ids:
        hit = [c for c in candidates if c["event_id"] in ids]
        if len(hit) == 1:
            return Match(hit[0]["procedure_uri"], "fedlex_link", 1.0)
    scored = []
    for c in candidates:
        if same_source_only is not None and (c.get("source") or "fedlex") != same_source_only:
            continue
        cy = [y for y in (_year_of(c.get("start_date")), c.get("year")) if y]
        if year and cy and min(abs(year - y) for y in cy) > 2:
            continue
        sims = {lg: title_similarity(titles.get(lg), c.get(f"title_{lg}")) for lg in LANGS}
        sim = max(sims.values(), default=0.0)
        # an undated match also needs one title's significant words inside
        # the other's, in the same language: "Haager Wertpapier-Übereinkommen"
        # is 0.92 like "Haager Trust-Übereinkommen" but not the same act
        words_agree = any(
            sims[lg] == 1.0 or (
                sims[lg] >= UNDATED_MIN_SIMILARITY
                and (lambda a, b: bool(a) and bool(b) and (a <= b or b <= a))(
                    _tokens(norm_title(titles.get(lg))), _tokens(norm_title(c.get(f"title_{lg}")))))
            for lg in LANGS)
        dl_eq = bool(deadline and (c.get("end_date") or "")[:10] == deadline)
        op_eq = bool(opened and (c.get("start_date") or "")[:10] == opened)
        # a BJ page: opened the same day by the same office, and the titles
        # share a word ("Erbrecht" / "Änderung des Zivilgesetzbuches (Erbrecht)")
        inst_eq = bool(op_eq and institution is not None and institution(c)
                       and any(_tokens(norm_title(titles.get(lg))) &
                               _tokens(norm_title(c.get(f"title_{lg}"))) for lg in LANGS))
        if sim < 0.4 and not inst_eq:
            continue
        conflict = False
        if deadline and c.get("end_date"):
            try:
                conflict = abs((date.fromisoformat(c["end_date"][:10])
                                - date.fromisoformat(deadline)).days) > 45
            except ValueError:
                conflict = False
        if dl_eq and sim >= DATED_MIN_SIMILARITY:
            method = "deadline_title"
        elif op_eq and sim >= DATED_MIN_SIMILARITY:
            method = "opened_title"
        elif inst_eq:
            method = "opened_institution"
        elif words_agree and not conflict:
            method = "title"
        else:
            continue
        scored.append((sim + 0.3 * dl_eq + 0.2 * op_eq + 0.1 * inst_eq, method, c))
    if not scored:
        return Match(None, "unmatched")
    scored.sort(key=lambda x: -x[0])
    if len(scored) > 1 and scored[0][0] - scored[1][0] < 0.05 \
            and scored[0][2]["procedure_uri"] != scored[1][2]["procedure_uri"]:
        return Match(None, "ambiguous", round(scored[0][0], 3))
    s, method, c = scored[0]
    return Match(c["procedure_uri"], method, round(s, 3))


# ── storing ────────────────────────────────────────────────────────────

@dataclass
class ArchiveFile:
    language: str
    original_url: str
    source_page_url: str | None
    snapshot: str | None = None      # file_url: Wayback snapshot or live URL; None = not archived
    file_name: str | None = None
    publication_date: str | None = None


@dataclass
class ArchiveDocument:
    key: str
    doc_type_code: int
    titles: dict[str, str] = field(default_factory=dict)   # the page's own labels
    files: dict[str, ArchiveFile] = field(default_factory=dict)


@dataclass
class ArchiveProcedure:
    source: str
    key: str
    year: int | None
    titles: dict[str, str | None]
    opened: str | None
    deadline: str | None
    department: str | None
    pages: dict[str, str]
    documents: list[ArchiveDocument] = field(default_factory=list)
    fedlex_event_ids: list[str] = field(default_factory=list)
    match: Match | None = None     # decided while reading the source (admin-pc)


def _held_types(conn: sqlite3.Connection, procedure_uri: str, *,
                exclude_source: str | None) -> set[tuple[int, str]]:
    """(type code, language) pairs the procedure already holds with a file
    from Fedlex or another source."""
    return {(r[0], r[1]) for r in conn.execute(
        f"""SELECT d.doc_type_code, f.language FROM consultation_documents d
            JOIN consultation_files f ON f.document_uri = d.document_uri
            WHERE d.procedure_uri = ? AND f.file_url IS NOT NULL
              AND f.status NOT IN ('{bcc.ST_NOT_FOUND}', '{bcc.ST_NOT_ARCHIVED}')
              AND COALESCE(d.source, 'fedlex') <> COALESCE(?, '')""",
        (procedure_uri, exclude_source))}


SUPPRESSED_META_KEY = "archive_suppressed_documents"


def suppressed_documents(conn: sqlite3.Connection) -> set[str]:
    """document_uris taken down on purpose (meta key, JSON list): discovery
    never adds them again."""
    row = conn.execute("SELECT value FROM meta WHERE key=?", (SUPPRESSED_META_KEY,)).fetchone()
    try:
        return set(json.loads(row[0])) if row else set()
    except ValueError:
        return set()


def suppress_document(conn: sqlite3.Connection, document_uri: str) -> dict:
    """Take an archive document down: delete its text, files and row, and
    remember it so a later discovery does not bring it back."""
    files = [r[0] for r in conn.execute(
        "SELECT file_id FROM consultation_files WHERE document_uri=?", (document_uri,))]
    with conn:
        for fid in files:
            conn.execute("DELETE FROM consultation_paragraphs WHERE file_id=?", (fid,))
        conn.execute("DELETE FROM consultation_files WHERE document_uri=?", (document_uri,))
        conn.execute("DELETE FROM consultation_documents WHERE document_uri=?", (document_uri,))
        bcc.set_meta(conn, SUPPRESSED_META_KEY,
                     sorted(suppressed_documents(conn) | {document_uri}))
    return {"document_uri": document_uri, "files_deleted": len(files)}


def store_procedure(conn: sqlite3.Connection, ap: ArchiveProcedure, match: Match,
                    stats: dict, now: str) -> str | None:
    """Write one archive procedure: the entry row, a new procedure when it
    matched none (and was not ambiguous), its documents and file rows.
    Idempotent; a file whose URL changed is re-queued; never deletes."""
    entry_id = f"{ap.source}:{ap.key}"
    proc_uri = match.procedure_uri
    method = match.method
    if proc_uri is None and not ap.documents and ap.source != SOURCE_ADMIN_PC:
        # a BJ project page is not a consultation: without documents and
        # without a Fedlex match it is recorded (entry row), never a procedure
        method = "no_documents"
        stats["entries_without_documents_unmatched"] = \
            stats.get("entries_without_documents_unmatched", 0) + 1
    elif proc_uri is None and method != "ambiguous":
        proc_uri = f"{URN}:{ap.source}:{ap.key}"
        method = "created"
        conn.execute(
            """INSERT INTO consultation_procedures
               (procedure_uri, event_id, year, title_de, title_fr, title_it,
                start_date, end_date, discovered_at, updated_at, source, source_url)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(procedure_uri) DO UPDATE SET
                 year=excluded.year, title_de=excluded.title_de,
                 title_fr=excluded.title_fr, title_it=excluded.title_it,
                 start_date=excluded.start_date, end_date=excluded.end_date,
                 updated_at=excluded.updated_at, source_url=excluded.source_url""",
            (proc_uri, f"{ap.source}/{ap.key}", ap.year, ap.titles.get("de"),
             ap.titles.get("fr"), ap.titles.get("it"), ap.opened, ap.deadline,
             now, now, ap.source, next(iter(ap.pages.values()), None)))
        stats["procedures_created"] += 1
    elif proc_uri is not None:
        stats["procedures_matched"] += 1
        stats["match_methods"][method] = stats["match_methods"].get(method, 0) + 1
    elif method == "ambiguous":
        stats["procedures_ambiguous"] += 1
    n_docs = 0
    if proc_uri is not None:
        held = _held_types(conn, proc_uri, exclude_source=ap.source)
        gone = suppressed_documents(conn)
        for d in ap.documents:
            if f"{URN}:{ap.source}:doc:{d.key}" in gone:
                stats["documents_suppressed"] = stats.get("documents_suppressed", 0) + 1
                continue
            files = {lg: f for lg, f in d.files.items() if (d.doc_type_code, lg) not in held}
            stats["files_already_held"] += len(d.files) - len(files)
            if not files:
                stats["documents_already_held"] += 1
                continue
            n_docs += 1
            doc_uri = f"{URN}:{ap.source}:doc:{d.key}"
            slug = bcc.DOC_TYPES.get(d.doc_type_code, f"type_{d.doc_type_code}")
            conn.execute(
                """INSERT INTO consultation_documents
                   (document_uri, procedure_uri, doc_type_code, doc_type,
                    title_de, title_fr, title_it, source)
                   VALUES (?,?,?,?,?,?,?,?)
                   ON CONFLICT(document_uri) DO UPDATE SET
                     procedure_uri=excluded.procedure_uri,
                     doc_type_code=excluded.doc_type_code, doc_type=excluded.doc_type,
                     title_de=excluded.title_de, title_fr=excluded.title_fr,
                     title_it=excluded.title_it, source=excluded.source""",
                (doc_uri, proc_uri, d.doc_type_code, slug, d.titles.get("de"),
                 d.titles.get("fr"), d.titles.get("it"), ap.source))
            stats["documents"] += 1
            stats["documents_by_type"][slug] = stats["documents_by_type"].get(slug, 0) + 1
            for lg, f in files.items():
                status = bcc.ST_PENDING if f.snapshot else bcc.ST_NOT_ARCHIVED
                if d.doc_type_code not in bcc.TEXT_TYPES:
                    status = bcc.ST_LINK_ONLY if f.snapshot else bcc.ST_NOT_ARCHIVED
                policy = "text" if d.doc_type_code in bcc.TEXT_TYPES else "link"
                stats["files"] += 1
                stats["files_by_status"][status] = stats["files_by_status"].get(status, 0) + 1
                prev = conn.execute(
                    "SELECT file_id, file_url FROM consultation_files "
                    "WHERE document_uri=? AND language=?", (doc_uri, lg)).fetchone()
                if prev is None:
                    conn.execute(
                        """INSERT INTO consultation_files
                           (document_uri, language, file_url, format, file_name,
                            publication_date, text_policy, status, source,
                            original_url, source_page_url)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                        (doc_uri, lg, f.snapshot, "pdf", f.file_name, f.publication_date,
                         policy, status, ap.source, f.original_url, f.source_page_url))
                    stats["files_new"] += 1
                elif prev["file_url"] != f.snapshot:
                    conn.execute("DELETE FROM consultation_paragraphs WHERE file_id=?",
                                 (prev["file_id"],))
                    conn.execute(
                        """UPDATE consultation_files SET file_url=?, format='pdf', file_name=?,
                           publication_date=?, text_policy=?, status=?, attempts=0,
                           error=NULL, http_status=NULL, sha256=NULL, prefix_sha256=NULL,
                           byte_size=NULL, same_as_file_id=NULL, page_count=NULL,
                           char_count=NULL, empty_pages=NULL, ingested_at=NULL,
                           source=?, original_url=?, source_page_url=? WHERE file_id=?""",
                        (f.snapshot, f.file_name, f.publication_date, policy, status,
                         ap.source, f.original_url, f.source_page_url, prev["file_id"]))
                    stats["files_requeued"] += 1
                else:
                    conn.execute(
                        "UPDATE consultation_files SET file_name=?, source=?, original_url=?, "
                        "source_page_url=? WHERE file_id=?",
                        (f.file_name, ap.source, f.original_url, f.source_page_url,
                         prev["file_id"]))
    conn.execute(
        """INSERT INTO consultation_archive_entries
           (entry_id, source, year, title_de, title_fr, title_it, opened, deadline,
            department, page_urls_json, procedure_uri, match_method, match_score,
            documents, discovered_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(entry_id) DO UPDATE SET
             year=excluded.year, title_de=excluded.title_de, title_fr=excluded.title_fr,
             title_it=excluded.title_it, opened=excluded.opened, deadline=excluded.deadline,
             department=excluded.department, page_urls_json=excluded.page_urls_json,
             procedure_uri=excluded.procedure_uri, match_method=excluded.match_method,
             match_score=excluded.match_score, documents=excluded.documents,
             discovered_at=excluded.discovered_at""",
        (entry_id, ap.source, ap.year, ap.titles.get("de"), ap.titles.get("fr"),
         ap.titles.get("it"), ap.opened, ap.deadline, ap.department,
         json.dumps(ap.pages, ensure_ascii=False, sort_keys=True), proc_uri, method,
         match.score, n_docs, now))
    return proc_uri


def _new_stats() -> dict:
    return {"entries": 0, "entries_with_documents": 0, "procedures_matched": 0,
            "procedures_created": 0, "procedures_ambiguous": 0, "match_methods": {},
            "documents": 0, "documents_by_type": {}, "documents_already_held": 0,
            "files": 0, "files_by_status": {}, "files_already_held": 0,
            "files_new": 0, "files_requeued": 0, "links_seen": 0, "requests": 0}


# A match strong enough to attach an undated document to the procedure.
STRONG_METHODS = frozenset({"fedlex_link", "deadline_title", "opened_title", "title"})
# Days from a procedure's opening within which a BJ document belongs to it:
# the Begleitbericht and Vorentwurf come with the opening, the result report
# and the compilation of submissions up to ~3 years later.
BJ_WINDOW_DAYS = (-365, 1100)


def _doc_date(d: ArchiveDocument) -> str | None:
    dates = [f.publication_date for f in d.files.values() if f.publication_date]
    return min(dates) if dates else None


ANCHOR_DAYS = 10   # an opening document's date vs a procedure's opening date


def _days(a: str, b: str) -> int | None:
    try:
        return (date.fromisoformat(a[:10]) - date.fromisoformat(b[:10])).days
    except (ValueError, TypeError):
        return None


def _words(texts: Iterable[str | None]) -> set[str]:
    return {t for x in texts for t in _tokens(norm_title(x))}


def find_anchors(ap: ArchiveProcedure, candidates: list[dict]) -> dict[str, dict]:
    """Fedlex procedures a BJ project's documents date themselves to: for
    each opening document (Vorlage / Erläuternder Bericht) dated D, the
    BJ/EJPD procedure opened within ANCHOR_DAYS of D whose title shares a
    significant word with the project's or the document's title (same
    language); the nearest when several qualify, none when two are equally
    near."""
    anchors: dict[str, dict] = {}
    fed = [c for c in candidates if not c.get("source") and c.get("start_date")
           and is_justice_institution(c)]
    for d in ap.documents:
        dd = _doc_date(d)
        if d.doc_type_code not in (11, 12) or not dd:
            continue
        hits = []
        for c in fed:
            delta = _days(c["start_date"], dd)
            if delta is None or abs(delta) > ANCHOR_DAYS:
                continue
            if any(_words([ap.titles.get(lg), d.titles.get(lg)])
                   & _tokens(norm_title(c.get(f"title_{lg}"))) for lg in LANGS):
                hits.append((abs(delta), c))
        hits.sort(key=lambda x: x[0])
        if hits and (len(hits) == 1 or hits[0][0] < hits[1][0]
                     or hits[0][1]["procedure_uri"] == hits[1][1]["procedure_uri"]):
            c = hits[0][1]
            anchors[c["procedure_uri"]] = c
    return anchors


def split_for_match(conn: sqlite3.Connection, ap: ArchiveProcedure, m: Match,
                    candidates: list[dict] | None = None,
                    ) -> list[tuple[str | None, Match, list[ArchiveDocument]]]:
    """Distribute a project's documents over the procedures they belong to:
    [(entry key suffix, match, documents)]. The Chancellery's entries are
    one procedure each (everything to ``m``). A BJ project page tells of
    several consultations (ZPO 2003 and its later amendments under one
    project): its page-level match and the procedures its opening documents
    date themselves to (find_anchors) are the anchors; a document goes to
    the anchor whose opening it follows within BJ_WINDOW_DAYS (the nearest),
    an undated one to ``m`` on a STRONG match; the rest stays on the
    project's own procedure (suffix "/rest")."""
    if ap.source == SOURCE_ADMIN_PC:
        return [(None, m, list(ap.documents))]
    anchors: dict[str, tuple[Match, str | None]] = {}
    if m.procedure_uri:
        row = conn.execute("SELECT start_date FROM consultation_procedures WHERE procedure_uri=?",
                           (m.procedure_uri,)).fetchone()
        anchors[m.procedure_uri] = (m, row[0][:10] if row and row[0] else None)
    for uri, c in find_anchors(ap, candidates or []).items():
        if uri not in anchors:
            anchors[uri] = (Match(uri, "document_date", 1.0), c["start_date"][:10])
    groups: dict[str | None, list[ArchiveDocument]] = {}
    for d in ap.documents:
        dd = _doc_date(d)
        best = None
        if dd:
            for uri, (_, start) in anchors.items():
                delta = _days(dd, start) if start else None
                if delta is None or not (BJ_WINDOW_DAYS[0] <= delta <= BJ_WINDOW_DAYS[1]):
                    continue
                rank = (delta < 0, abs(delta))   # follows the opening, nearest
                if best is None or rank < best[0]:
                    best = (rank, uri)
        elif m.procedure_uri and m.method in STRONG_METHODS:
            best = (None, m.procedure_uri)
        groups.setdefault(best[1] if best else None, []).append(d)
    out = []
    for uri, docs in groups.items():
        if uri is None:
            out.append(("/rest", Match(None, "unmatched"), docs))
        elif uri == m.procedure_uri:
            out.append((None, m, docs))
        else:
            out.append((f"@{anchors[uri][0].procedure_uri.rsplit('/eli/dl/', 1)[-1]}",
                        anchors[uri][0], docs))
    if not any(sfx is None for sfx, _, _ in out):
        out.append((None, m, []))     # the page-level entry is always recorded
    return out


def store_split(conn: sqlite3.Connection, ap: ArchiveProcedure, m: Match, stats: dict,
                now: str, candidates: list[dict] | None = None) -> str | None:
    """store_procedure for each group of split_for_match: the page-level
    entry ``<key>``, one ``<key>@<proj/...>`` per procedure the documents
    date themselves to, ``<key>/rest`` for the project's own procedure.
    Returns the procedure of the page-level entry."""
    from dataclasses import replace
    groups = split_for_match(conn, ap, m, candidates)
    main_uri = None
    for suffix, gm, docs in groups:
        sub = ap if suffix is None and len(groups) == 1 else replace(
            ap, key=ap.key + (suffix or ""), documents=docs, match=None)
        if suffix == "/rest":
            stats["documents_outside_matched_window"] = \
                stats.get("documents_outside_matched_window", 0) + len(docs)
        elif suffix:
            stats["documents_dated_to_procedure"] = \
                stats.get("documents_dated_to_procedure", 0) + len(docs)
        uri = store_procedure(conn, sub, gm, stats, now)
        if suffix is None:
            main_uri = uri
            if uri is None and not docs and ap.documents:
                # the page's documents all went to the procedures they date
                # themselves to (or the project's own): not "no_documents"
                conn.execute("UPDATE consultation_archive_entries SET match_method='documents_dated' "
                             "WHERE entry_id=?", (f"{ap.source}:{ap.key}",))
                stats["entries_without_documents_unmatched"] -= 1
    return main_uri


def store_all(conn: sqlite3.Connection, procs: list[ArchiveProcedure], stats: dict) -> dict:
    now = bcc._now()
    candidates = load_candidates(conn)
    for ap in procs:
        stats["entries"] += 1
        stats["entries_with_documents"] += bool(ap.documents)
        m = ap.match or match_procedure(
            ap.titles, year=ap.year, opened=ap.opened, deadline=ap.deadline,
            candidates=candidates, fedlex_event_ids=ap.fedlex_event_ids,
            institution=is_justice_institution if ap.source != SOURCE_ADMIN_PC else None)
        uri = store_split(conn, ap, m, stats, now, candidates)
        if m.method == "unmatched" and uri:
            # later entries of the same run (another language page, the BJ
            # project of the same procedure) can match the new procedure
            candidates.append({"procedure_uri": uri, "event_id": f"{ap.source}/{ap.key}",
                               "year": ap.year, "start_date": ap.opened,
                               "end_date": ap.deadline, "source": ap.source,
                               **{f"title_{lg}": ap.titles.get(lg) for lg in LANGS}})
    conn.commit()
    return stats


# ── admin-pc discovery ─────────────────────────────────────────────────

def _pick_pc_snapshots(rows: list[dict], year: int) -> tuple[dict | None, dict | None]:
    """(latest snapshot, latest snapshot before the 2006 layout) of
    ind<year>.html (variants like ind2004.html%23EDA are other URLs)."""
    want = f"/gg/pc/ind{year}.html"
    ok = sorted((r for r in rows if r.get("statuscode") == "200"
                 and norm_url_key(r["original"]).endswith(want)), key=lambda r: r["timestamp"])
    if not ok:
        return None, None
    latest = ok[-1]
    old = [r for r in ok if r["timestamp"] < PC_NEW_TEMPLATE_FROM]
    old_latest = old[-1] if old else None
    if latest["timestamp"] < PC_NEW_TEMPLATE_FROM:
        return latest, None
    return latest, old_latest


def _pc_archived_files(http: ArchiveHttp, urls: Iterable[str]) -> dict[str, dict]:
    """Newest PDF capture per URL: one prefix query per language for the
    Chancellery's document store, one exact query for each other file."""
    found: dict[str, dict] = {}
    urls = sorted(set(urls))
    for lang_dir in ("d", "f", "i"):
        prefix = f"www.admin.ch/ch/{lang_dir}/gg/pc/documents/"
        if any(norm_url_key(u).startswith(prefix) for u in urls):
            found.update(latest_by_key(cdx_query(http, prefix, match_type="prefix"),
                                       prefer_pdf=True))
    for u in urls:
        k = norm_url_key(u)
        if "/gg/pc/documents/" in k or k in found:
            continue
        try:
            found.update(latest_by_key(cdx_query(http, u), prefer_pdf=True))
        except SourceUnavailable:
            raise
        except (urllib.error.URLError, OSError, ValueError) as e:
            # unknown availability: the file is listed as not archived
            log.warning("  CDX failed for %s: %s", u, e)
    return found


def pc_procedures(http: ArchiveHttp, years: Iterable[int] = PC_YEARS,
                  stats: dict | None = None,
                  candidates: list[dict] | None = None) -> list[ArchiveProcedure]:
    """Read the index pages from the Wayback Machine and turn them into
    ArchiveProcedures (documents with their archive status), each entry
    matched to ``candidates`` (the work DB's procedures) on its own."""
    stats = stats if stats is not None else _new_stats()

    def match_one(e: PcEntry) -> Match:
        if not candidates:
            return Match(None, "unmatched")
        return match_procedure({e.lang: e.title}, year=e.year, opened=e.opened,
                               deadline=e.deadline, candidates=candidates)

    merged: list[PcProcedure] = []
    pages_read = {}
    for year in years:
        new: dict[str, list[PcEntry]] = {}
        old: dict[str, list[PcEntry]] = {}
        pages: dict[str, str] = {}
        for lang in LANGS:
            rows = cdx_query(http, pc_index_url(lang, year).split("://", 1)[1], ok_only=True)
            latest, old_latest = _pick_pc_snapshots(rows, year)
            for snap, bucket in ((latest, new), (old_latest, old)):
                if snap is None:
                    continue
                url = snapshot_url(snap["timestamp"], snap["original"])
                page = get_page(http, bcc.download_url(url), stats)
                if page is None:
                    continue
                entries = parse_pc_index(page, lang=lang, year=year, base_url=snap["original"])
                for e in entries:
                    e.page_url = url  # type: ignore[attr-defined]
                bucket[lang] = entries
                if bucket is new:
                    pages[lang] = url
            pages_read[f"{year}/{lang}"] = {"latest": latest and latest["timestamp"],
                                            "pre_2006": old_latest and old_latest["timestamp"],
                                            "entries": len(new.get(lang, []))}
        for lang in LANGS:
            fill_dates_from_old_layout(new.get(lang, []), old.get(lang, []))
        for p in merge_pc_languages(year, new, old, match=match_one):
            p.pages = pages
            merged.append(p)
    stats.setdefault("pages", pages_read)

    links = [lk.url for p in merged for e in list(p.entries.values()) + list(p.old_entries.values())
             for lk in e.links]
    stats["links_seen"] += len(links)
    archived = _pc_archived_files(http, links)

    out: list[ArchiveProcedure] = []
    for p in merged:
        docs: dict[str, ArchiveDocument] = {}
        types_new: set[int] = set()
        used_urls: set[str] = set()   # a trilingual PDF linked from all three pages
        for slot, layout_new in ((p.entries, True), (p.old_entries, False)):
            for lang, e in slot.items():
                for lk in e.links:
                    key, flang = pc_document_key(lk.url)
                    flang = flang or lang
                    code = classify(lk.group, lk.text, lk.url.rsplit("/", 1)[-1])
                    if not layout_new and code in types_new:
                        continue   # the 2006+ page links the Chancellery's copy of it
                    d = docs.setdefault(key, ArchiveDocument(key=key, doc_type_code=code))
                    if layout_new:
                        types_new.add(code)
                    d.titles.setdefault(lang, lk.label)
                    if flang in d.files or lk.url in used_urls:
                        continue
                    used_urls.add(lk.url)
                    hit = archived.get(norm_url_key(lk.url))
                    d.files[flang] = ArchiveFile(
                        language=flang, original_url=lk.url,
                        source_page_url=getattr(e, "page_url", None),
                        snapshot=snapshot_url(hit["timestamp"], hit["original"]) if hit else None,
                        file_name=urllib.parse.unquote(lk.url.rsplit("/", 1)[-1]))
        titles = {lg: p.title(lg) for lg in LANGS}
        ap = ArchiveProcedure(
            source=SOURCE_ADMIN_PC, key=f"{p.year}/{p.key()}", year=p.year, titles=titles,
            opened=p.opened, deadline=p.deadline, department=p.department,
            pages=p.pages, documents=list(docs.values()))
        ap.match = p.match()
        out.append(ap)
    return out


# ── source 2: bj.admin.ch legislative projects ─────────────────────────

BJ = "https://www.bj.admin.ch"
BJ_AREAS = ("staat-und-buerger", "gesellschaft", "wirtschaft", "sicherheit")
BJ_LISTING_KINDS = ("laufende", "abgeschlossene")
BJ_OLD_AREAS = ("staat", "gesellschaft", "wirtschaft", "sicherheit")
_BJ_DAM_RE = re.compile(r"^https://www\.bj\.admin\.ch/dam/", re.IGNORECASE)
_FEDLEX_PROJ_RE = re.compile(r"fedlex\.admin\.ch/(?:eli/dl/)?(?:filestore/[^\"']*?/)?"
                             r"(?:eli/dl/)?(proj/\d{4}/\d+/cons_\d+)", re.IGNORECASE)
_DE_MONTHS = {m: i for i, m in enumerate(
    ["januar", "februar", "märz", "april", "mai", "juni", "juli", "august",
     "september", "oktober", "november", "dezember"], 1)}
_DE_DATE_RE = re.compile(r"(\d{1,2})\.\s*(" + "|".join(_DE_MONTHS) + r")\s+(\d{4})", re.IGNORECASE)


def bj_listing_url(kind: str, area: str, lang: str = "de") -> str:
    return f"{BJ}/{lang}/{kind}-rechtsetzungsprojekte-{area}"


def parse_bj_listing(page: str) -> list[tuple[str, str]]:
    """(slug, title) of every project teaser on a listing page."""
    out, seen = [], set()
    for chunk in page.split('identifier="teaser-item"')[1:]:
        m = re.search(r'href="/(?:de|fr|it)/([a-z0-9-]+)"', chunk)
        t = re.search(r'<div class="card__title">.*?<h3[^>]*>(.*?)</h3>', chunk, re.DOTALL)
        if m and m.group(1) not in seen:
            seen.add(m.group(1))
            out.append((m.group(1), one_line(t.group(1)) if t else ""))
    return out


def nuxt_page_meta(page: str) -> dict:
    """groupId, slug, lang and translationPages of the page record in Nuxt's
    __NUXT_DATA__ payload (a devalue-flattened array: values are indices)."""
    m = re.search(r'<script[^>]*id="__NUXT_DATA__"[^>]*>(.*?)</script>', page, re.DOTALL)
    if not m:
        return {}
    try:
        arr = json.loads(m.group(1))
    except ValueError:
        return {}

    def val(i):
        return arr[i] if isinstance(i, int) and 0 <= i < len(arr) else None

    for obj in arr:
        if isinstance(obj, dict) and {"groupId", "slug", "translationPages", "lang"} <= obj.keys():
            trans = {}
            for ref in val(obj["translationPages"]) or []:
                t = val(ref)
                if isinstance(t, dict):
                    lg, sl = val(t.get("lang")), val(t.get("slug"))
                    if isinstance(lg, str) and isinstance(sl, str):
                        trans[lg] = sl
            return {"group_id": val(obj["groupId"]), "slug": val(obj["slug"]),
                    "lang": val(obj["lang"]), "translations": trans}
    return {}


@dataclass
class BjItem:
    group: str | None
    title: str
    url: str
    meta: str            # "PDF 1.68 MB 31. Januar 2024" as printed


@dataclass
class BjProject:
    lang: str
    url: str
    title: str
    items: list[BjItem]
    group_id: str | None = None
    slug: str | None = None
    translations: dict[str, str] = field(default_factory=dict)
    fedlex_event_ids: list[str] = field(default_factory=list)
    opened: str | None = None     # "Der Bundesrat eröffnet am 25. Juni 2003 die Vernehmlassung"


def _opening_date_de(text: str) -> str | None:
    """The earliest date of a sentence saying a Vernehmlassung / Anhörung was
    opened ("Der Bundesrat eröffnet am 25. Juni 2003 das Vernehmlassungs-
    verfahren"). A project page can tell of several; the first is the one
    the project's documents start with."""
    found = []
    # sentence ends: "...verabschiedet. Der" but not "am 25. Juni"
    for sent in re.split(r"(?<=[^\d\s][.;])\s+(?=[A-ZÄÖÜ])|\n", text):
        if re.search(r"er[öo]ffne", sent, re.IGNORECASE) and re.search(r"vernehmlassung|anh[öo]rung",
                                                               sent, re.IGNORECASE):
            m = _DE_DATE_RE.search(sent)
            if m:
                d = _iso(m.group(1), str(_DE_MONTHS[m.group(2).lower()]), m.group(3))
                if d:
                    found.append(d)
    return min(found) if found else None


def parse_bj_project(page: str, *, url: str, lang: str) -> BjProject:
    """Title, download items (with the heading they sit under), Fedlex
    consultation links and, on German pages, the opening date of the
    Vernehmlassung as the page states it."""
    h1 = re.search(r"<h1[^>]*>(.*?)</h1>", page, re.DOTALL)
    items: list[BjItem] = []
    group = None
    token = re.compile(
        r'<h[23][^>]*class="(?:accordion__title|h2)"[^>]*>(.*?)</h[23]>'
        r'|<a class="download-item"([^>]*)>(.*?)</a>', re.DOTALL)
    for m in token.finditer(page):
        if m.group(1) is not None:
            group = one_line(m.group(1))
            continue
        href = _href(m.group(2)) or ""
        if not _BJ_DAM_RE.match(href) or any(i.url == href for i in items):
            continue
        t = re.search(r'<h4 class="download-item__title">(.*?)</h4>', m.group(3), re.DOTALL)
        meta = " ".join(one_line(x) for x in
                        re.findall(r'<span class="meta-info__item">(.*?)</span>', m.group(3), re.DOTALL))
        items.append(BjItem(group=group, title=one_line(t.group(1)) if t else "", url=href,
                            meta=meta))
    meta = nuxt_page_meta(page)
    # the page's own text ("Worum geht es?", "Was ist bisher geschehen?"),
    # not the teasers of related news below the documentation
    start = h1.end() if h1 else 0
    end = page.find('id="Dokumentation"', start)
    body = page[start:end] if end > 0 else page.split("<main", 1)[-1]
    text = html_text(re.sub(r'<script[^>]*>.*?</script>', " ", body, flags=re.DOTALL))
    return BjProject(
        lang=lang, url=url, title=one_line(h1.group(1)) if h1 else "", items=items,
        group_id=meta.get("group_id"), slug=meta.get("slug"),
        translations=meta.get("translations", {}),
        fedlex_event_ids=sorted(set(_FEDLEX_PROJ_RE.findall(page))),
        opened=_opening_date_de(text) if lang == "de" else None)


_MONTHS = {**_DE_MONTHS, **{m: i for i, m in enumerate(
    ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
     "septembre", "octobre", "novembre", "décembre"], 1)}, **{m: i for i, m in enumerate(
    ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto",
     "settembre", "ottobre", "novembre", "dicembre"], 1)}}
_ANY_DATE_RE = re.compile(r"(\d{1,2})\.?\s*(" + "|".join(_MONTHS) + r")\s+(\d{4})", re.IGNORECASE)


def _bj_item_date(meta: str) -> str | None:
    """The file date a download item prints ("26. Juni 2003", "26 juin 2003",
    "26 giugno 2003", "29.09.2010")."""
    m = _ANY_DATE_RE.search(meta)
    if m:
        return _iso(m.group(1), str(_MONTHS[m.group(2).lower()]), m.group(3))
    m = re.search(_DATE_RE, meta)
    return _iso(*m.groups()) if m else None


def _bj_file_stem(url: str) -> str:
    """vn-ber.pdf, vn-ber-d.pdf and .../vn-ber-d.pdf.download.pdf/vn-ber-d.pdf -> 'vn-ber'."""
    name = urllib.parse.unquote(url.rstrip("/").rsplit("/", 1)[-1]).lower()
    name = re.sub(r"\.pdf$", "", name)
    return _LANG_SUFFIX_RE.sub("", name)


def bj_documents(projects: dict[str, BjProject], source: str,
                 snapshots: dict[str, dict] | None = None) -> list[ArchiveDocument]:
    # The old archive prints the 2010 AEM migration date on every file
    # ("PDF, 426 kB, 29.09.2010"), not the document's: no publication_date.
    """One document per file across the language versions of a project. The
    first language's items define the documents; another language's item
    joins the one with the same file stem (vn-ber.pdf / vn-ber-f.pdf), else
    the one at the same position when both pages list as many files, else it
    is a document of its own. ``snapshots`` (old archive): the Wayback
    capture per file, None = not archived."""
    langs = [lg for lg in LANGS if lg in projects]
    if not langs:
        return []
    first = projects[langs[0]]
    base = first.group_id or first.slug or "x"
    docs: list[ArchiveDocument] = []
    by_stem: dict[str, ArchiveDocument] = {}
    for lang in langs:
        pr = projects[lang]
        same_len = len(pr.items) == len(first.items)
        for idx, it in enumerate(pr.items):
            stem = _bj_file_stem(it.url)
            d = by_stem.get(stem)
            if d is None and lang != langs[0] and same_len and idx < len(docs) \
                    and lang not in docs[idx].files:
                d = docs[idx]
            if d is None or lang in d.files:
                key, n = f"{base}/{stem}", 1
                taken = {x.key for x in docs}
                while key in taken:      # one file name listed several times
                    n += 1
                    key = f"{base}/{stem}~{n}"
                d = ArchiveDocument(key=key, doc_type_code=classify(it.group, it.title, stem))
                docs.append(d)
            by_stem.setdefault(stem, d)
            d.titles.setdefault(lang, it.title)
            if snapshots is None:
                file_url = it.url
            else:
                hit = snapshots.get(norm_url_key(_bj_old_file_url(it.url)))
                file_url = snapshot_url(hit["timestamp"], hit["original"]) if hit else None
            d.files[lang] = ArchiveFile(
                language=lang, original_url=it.url, source_page_url=pr.url,
                snapshot=file_url, file_name=urllib.parse.unquote(it.url.rsplit("/", 1)[-1]),
                publication_date=_bj_item_date(it.meta) if snapshots is None else None)
    return docs


def _bj_project_procedure(projects: dict[str, BjProject], source: str,
                          snapshots: dict[str, dict] | None = None) -> ArchiveProcedure:
    de = projects.get("de") or next(iter(projects.values()))
    key = de.group_id or de.slug or _bj_file_stem(de.url)
    opened = de.opened
    return ArchiveProcedure(
        source=source, key=key, year=_year_of(opened), opened=opened, deadline=None,
        titles={lg: (projects[lg].title if lg in projects else None) for lg in LANGS},
        department="EJPD/BJ", pages={lg: p.url for lg, p in projects.items()},
        documents=bj_documents(projects, source, snapshots),
        fedlex_event_ids=sorted({e for p in projects.values() for e in p.fedlex_event_ids}))


def bj_live_procedures(http: ArchiveHttp, stats: dict | None = None,
                       areas: Iterable[str] = BJ_AREAS) -> list[ArchiveProcedure]:
    stats = stats if stats is not None else _new_stats()
    slugs: dict[str, str] = {}
    for kind in BJ_LISTING_KINDS:
        for area in areas:
            page = get_page(http, bj_listing_url(kind, area), stats)
            for slug, title in parse_bj_listing(page or ""):
                slugs.setdefault(slug, title)
    stats["bj_listed_projects"] = len(slugs)
    out = []
    for slug in sorted(slugs):
        url = f"{BJ}/de/{slug}"
        page = get_page(http, url, stats)
        if page is None:
            continue
        de = parse_bj_project(page, url=url, lang="de")
        projects = {"de": de}
        for lg in ("fr", "it"):
            sl = de.translations.get(lg)
            if not sl:
                continue
            u = f"{BJ}/{lg}/{sl}"
            page = get_page(http, u, stats)
            if page is not None:
                projects[lg] = parse_bj_project(page, url=u, lang=lg)
        stats["links_seen"] += sum(len(p.items) for p in projects.values())
        out.append(_bj_project_procedure(projects, SOURCE_BJ))
    return out


# ── source 3: the old BJ archive, Wayback ──────────────────────────────

def _bj_old_file_url(url: str) -> str:
    """…/x.pdf.download.pdf/x.pdf -> …/x.pdf (the archived and the linked
    forms of one AEM asset)."""
    return re.sub(r"(\.pdf)\.download\.pdf/[^/]+$", r"\1", url, flags=re.IGNORECASE)


def parse_bj_old_project(page: str, *, url: str, lang: str, base_url: str) -> BjProject:
    """The pre-2026 AEM layout: multilist boxes with an <h3> and links to
    /dam/bj/<lang>/data/…; the link title is the label."""
    h1 = re.search(r"<h1[^>]*>(.*?)</h1>", page, re.DOTALL)
    items: list[BjItem] = []
    group = None
    token = re.compile(r'<h3[^>]*>(.*?)</h3>|<a\b([^>]*)>(.*?)</a>', re.DOTALL)
    for m in token.finditer(page):
        if m.group(1) is not None:
            group = one_line(m.group(1))
            continue
        href = _href(m.group(2)) or ""
        if "/dam/bj/" not in href:
            continue
        absu = urllib.parse.urljoin(base_url, href)
        if not absu.lower().split("?")[0].endswith(".pdf") or any(i.url == absu for i in items):
            continue
        tm = re.search(r'title="([^"]*)"', m.group(2))
        label = htmllib.unescape(tm.group(1)).strip() if tm and tm.group(1).strip() else \
            one_line(re.sub(r'<span class="text-dimmed">.*?</span>', "", m.group(3), flags=re.DOTALL))
        dim = re.search(r'<span class="text-dimmed">(.*?)</span>', m.group(3), re.DOTALL)
        items.append(BjItem(group=group, title=label, url=absu,
                            meta=one_line(dim.group(1)) if dim else ""))
    return BjProject(lang=lang, url=url, title=one_line(h1.group(1)) if h1 else "",
                     items=items, slug=base_url.rstrip("/").rsplit("/", 1)[-1][:-5],
                     fedlex_event_ids=sorted(set(_FEDLEX_PROJ_RE.findall(page))))


def bj_wayback_procedures(http: ArchiveHttp, live: list[ArchiveProcedure],
                          stats: dict | None = None,
                          areas: Iterable[str] = BJ_OLD_AREAS) -> list[ArchiveProcedure]:
    """Old-archive projects and files the live site no longer lists: a file
    whose stem the matching live project already has is skipped; an old
    project without a live counterpart becomes its own procedure."""
    stats = stats if stats is not None else _new_stats()
    live_by_title = [(p, p.titles.get("de")) for p in live]
    out = []
    for area in areas:
        pages: dict[str, dict[str, dict]] = {}   # slug -> lang -> newest capture
        for lang in LANGS:
            prefix = f"www.bj.admin.ch/bj/{lang}/home/{area}/gesetzgebung/archiv/"
            for k, r in latest_by_key(cdx_query(http, prefix, match_type="prefix")).items():
                if not k.endswith(".html"):
                    continue
                slug = k.rsplit("/", 1)[-1][:-5]
                pages.setdefault(slug, {})[lang] = r
        files: dict[str, dict] = {}
        for lang in LANGS:
            prefix = f"www.bj.admin.ch/dam/bj/{lang}/data/{area}/gesetzgebung/archiv/"
            rows = cdx_query(http, prefix, match_type="prefix")
            # captured as …/x.pdf or …/x.pdf.download.pdf/x.pdf: one asset
            for r in rows:
                r["_key"] = norm_url_key(_bj_old_file_url(r["original"]))
            for r in sorted(rows, key=lambda r: ("pdf" in (r.get("mimetype") or ""),
                                                 r["timestamp"])):
                files[r["_key"]] = r
        stats.setdefault("bj_wayback_projects", 0)
        stats["bj_wayback_projects"] += len(pages)
        for slug in sorted(pages):
            projects: dict[str, BjProject] = {}
            for lang, cap in sorted(pages[slug].items()):
                snap = snapshot_url(cap["timestamp"], cap["original"])
                page = get_page(http, bcc.download_url(snap), stats)
                if page is None:
                    continue
                projects[lang] = parse_bj_old_project(page, url=snap, lang=lang,
                                                      base_url=cap["original"])
            if not projects or not any(p.items for p in projects.values()):
                continue
            de = projects.get("de") or next(iter(projects.values()))
            de.slug = f"{area}/{slug}"
            best, best_s = None, 0.0
            for lp, t in live_by_title:
                s = title_similarity(de.title, t)
                if s > best_s:
                    best, best_s = lp, s
            twin = best if best_s >= 0.85 else None
            if twin is None:   # same files under another name?
                stems = {_bj_file_stem(i.url) for i in de.items}
                for lp, _ in live_by_title:
                    lstems = {d.key.rsplit("/", 1)[-1] for d in lp.documents}
                    if stems and len(stems & lstems) >= max(2, len(stems) // 2):
                        twin = lp
                        break
            if twin is not None:
                have = {d.key.rsplit("/", 1)[-1] for d in twin.documents}
                for p in projects.values():
                    p.items = [i for i in p.items if _bj_file_stem(i.url) not in have]
                if not any(p.items for p in projects.values()):
                    stats.setdefault("bj_wayback_covered_by_live", 0)
                    stats["bj_wayback_covered_by_live"] += 1
                    continue
            stats["links_seen"] += sum(len(p.items) for p in projects.values())
            ap = _bj_project_procedure(projects, SOURCE_BJ_WAYBACK, snapshots=files)
            ap.key = f"{area}/{slug}"
            if twin is not None:
                # attach to whatever the live project matched (stored first)
                ap.titles = dict(twin.titles)
                ap.opened, ap.year = twin.opened, twin.year
                ap.fedlex_event_ids = list(twin.fedlex_event_ids)
                ap.twin_key = twin.key  # type: ignore[attr-defined]
            out.append(ap)
    return out


# ── driver ─────────────────────────────────────────────────────────────

def discover(conn: sqlite3.Connection, sources: Iterable[str], http: ArchiveHttp,
             *, years: Iterable[int] = PC_YEARS) -> dict:
    """Discover the given sources into the work DB. BJ live first, so the
    old BJ archive only adds what the live site lacks."""
    report: dict = {}
    srcs = list(sources)
    live: list[ArchiveProcedure] = []
    if SOURCE_BJ in srcs or SOURCE_BJ_WAYBACK in srcs:
        st = _new_stats()
        live = bj_live_procedures(http, st)
        if SOURCE_BJ in srcs:
            store_all(conn, live, st)
            st["requests"] = http.requests
            report[SOURCE_BJ] = st
    if SOURCE_BJ_WAYBACK in srcs:
        st = _new_stats()
        before = http.requests
        old = bj_wayback_procedures(http, live, st)
        twins = {}
        for row in conn.execute("SELECT entry_id, procedure_uri, match_method "
                                "FROM consultation_archive_entries WHERE source=?", (SOURCE_BJ,)):
            twins[row["entry_id"].split(":", 1)[1]] = (row["procedure_uri"], row["match_method"])
        now = bcc._now()
        cands = load_candidates(conn)
        for ap in old:
            st["entries"] += 1
            st["entries_with_documents"] += bool(ap.documents)
            tk = getattr(ap, "twin_key", None)
            main = twins.get(tk or "", (None, None))
            rest = twins.get(f"{tk}/rest", (None, None))
            if tk and main[0] and main[1] in STRONG_METHODS | {"created"}:
                # the old archive's files (undated: its dates are the 2010
                # migration's) follow the live project's main procedure ...
                store_procedure(conn, ap, Match(main[0], "bj_live_twin", 1.0), st, now)
            elif tk and rest[0]:
                # ... or the project's own procedure when the main match was weak
                store_procedure(conn, ap, Match(rest[0], "bj_live_twin", 1.0), st, now)
            else:
                m = match_procedure(ap.titles, year=ap.year, opened=ap.opened, deadline=None,
                                    candidates=cands, fedlex_event_ids=ap.fedlex_event_ids,
                                    institution=is_justice_institution)
                store_split(conn, ap, m, st, now, cands)
        conn.commit()
        st["requests"] = http.requests - before
        report[SOURCE_BJ_WAYBACK] = st
    if SOURCE_ADMIN_PC in srcs:
        st = _new_stats()
        before = http.requests
        procs = pc_procedures(http, years, st, candidates=load_candidates(conn))
        store_all(conn, procs, st)
        st["requests"] = http.requests - before
        report[SOURCE_ADMIN_PC] = st
    bcc.set_meta(conn, "last_archive_discovery", {"at": bcc._now(), **{
        k: {kk: vv for kk, vv in v.items() if kk != "pages"} for k, v in report.items()}})
    conn.commit()
    return report


def census(conn: sqlite3.Connection) -> dict:
    """What the work DB holds from the archive sources, per source: entries
    and how they matched, documents by type, files by status — measured
    from the rows, for the runbook and check_materialien_completeness."""
    out: dict = {}
    has_entries = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name='consultation_archive_entries'").fetchone()
    if not has_entries:
        return out
    for src, in conn.execute("SELECT DISTINCT source FROM consultation_archive_entries"):
        e = {r[0]: r[1] for r in conn.execute(
            "SELECT match_method, COUNT(*) FROM consultation_archive_entries WHERE source=? "
            "GROUP BY 1", (src,))}
        with_docs = conn.execute(
            "SELECT COUNT(*) FROM consultation_archive_entries WHERE source=? AND documents>0",
            (src,)).fetchone()[0]
        docs = {r[0]: r[1] for r in conn.execute(
            "SELECT doc_type, COUNT(*) FROM consultation_documents WHERE source=? GROUP BY 1",
            (src,))}
        files = {r[0]: r[1] for r in conn.execute(
            "SELECT status, COUNT(*) FROM consultation_files WHERE source=? GROUP BY 1", (src,))}
        langs = {r[0]: r[1] for r in conn.execute(
            "SELECT language, COUNT(*) FROM consultation_files WHERE source=? GROUP BY 1", (src,))}
        by_year = {str(r[0]): r[1] for r in conn.execute(
            "SELECT year, COUNT(*) FROM consultation_archive_entries WHERE source=? "
            "GROUP BY 1 ORDER BY 1", (src,))}
        out[src] = {
            "entries": sum(e.values()), "entries_by_match": e,
            "entries_attaching_documents": with_docs, "entries_by_year": by_year,
            "documents": sum(docs.values()), "documents_by_type": docs,
            "files": sum(files.values()), "files_by_status": files, "files_by_language": langs,
        }
    return out


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s",
                        datefmt="%H:%M:%S")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("command", choices=["discover", "census", "suppress"])
    ap.add_argument("--work-db", required=True)
    ap.add_argument("--source", default="all",
                    help="admin-pc, bj, bj-wayback or all (comma-separated)")
    ap.add_argument("--years", help="admin-pc years, e.g. 1995,1996 (default 1995-2004)")
    ap.add_argument("--cache-dir", help="keep every page and CDX answer here")
    ap.add_argument("--report", help="write the discovery stats + census as JSON")
    ap.add_argument("--document-uri", help="suppress: the archive document to take down")
    args = ap.parse_args(argv)
    names = {"admin-pc": SOURCE_ADMIN_PC, "bj": SOURCE_BJ, "bj-wayback": SOURCE_BJ_WAYBACK}
    wanted = list(SOURCES) if args.source == "all" else \
        [names[s.strip()] for s in args.source.split(",")]
    conn = bcc.connect_work_db(args.work_db)
    result: dict = {}
    rc = 0
    if args.command == "discover":
        years = [int(y) for y in args.years.split(",")] if args.years else PC_YEARS
        http = ArchiveHttp(cache_dir=args.cache_dir)
        try:
            result["discovery"] = discover(conn, wanted, http, years=years)
        except SourceUnavailable as e:
            log.error("stopped: %s", e)
            result["stopped"] = str(e)
            rc = bcc.INGEST_STOPPED_EXIT_CODE
    if args.command == "suppress":
        if not args.document_uri:
            ap.error("--document-uri is required for suppress")
        result["suppressed"] = suppress_document(conn, args.document_uri)
    result["census"] = census(conn)
    text = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    if args.report:
        rp = Path(args.report)
        rp.parent.mkdir(parents=True, exist_ok=True)
        tmp = rp.with_suffix(rp.suffix + ".tmp")
        tmp.write_text(text)
        os.replace(tmp, rp)
    print(text)
    conn.close()
    return rc


if __name__ == "__main__":
    sys.exit(main())
