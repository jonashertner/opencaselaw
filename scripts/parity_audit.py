#!/usr/bin/env python3
"""
parity_audit.py — weekly check that we hold every decision entscheidsuche.ch lists.

Why: from March to November 2026 VD CDAP and BE BVD fell behind silently after
the entscheidsuche feed was switched off, and nobody noticed for eight months.
This audit turns the one-off comparison of 2026-11-08
(docs/claude/entscheidsuche-comparison-2026-11-08.md and the parity addendum)
into a weekly job.

Policy (memory no-entscheidsuche-dependency): entscheidsuche is never a feed
and never ground truth. Its public inventory is only an outside list that tells
us where to look. A residual here means: go to the court's own portal and fix
our direct scraper. Nothing in this script writes to the corpus.

Their side, fetched politely (generic UA, one request per second):
  1. docs/Sitemaps/*.xml: every listed document ID (SIGNATUR_DOCKET_DATE, the
     docket truncated to 20 characters).
  2. docs/Snapshots/<today>.json: index counts per court signature.
  3. For signatures whose index count exceeds the listing, the IDs, dates and
     references from the public search endpoint (_searchV2.php), paged with
     search_after. IDs, dates and references only, never text.
  4. For each residual, one look at its document JSON for the dockets it
     names (Num) and the source URL (metadata only; cached, so each ID costs
     one request ever). This resolves hashed VD/TI IDs, file-name slugs (ZH
     Baurekurs "2021-15" = BRGE III Nr. 0006/2021) and their duplicate IDs.

Our side: the published HuggingFace mirror (voilaj/swiss-caselaw), light
columns only (court, canton, dockets, date, URLs) via HTTP range requests,
pinned to one commit so a mid-upload read stays consistent.

  Why the mirror and not production decisions.db: the build window runs from
  03:30 UTC for ~17 h and a corpus-wide scan of decisions.db inside it starves
  the quality gate (CLAUDE.md invariant 9). A docket-index read outside the
  window would need a window check, a VPS-only path and a fall-back when the
  build runs late. The mirror needs none of that, runs anywhere (VPS or a
  laptop), and audits what users actually receive. Its 1-2 day lag is absorbed
  by the two-run confirmation below.

Matching, per court (rules from the 11-08 prototypes):
  - dockets normalised: HTML stripped, upper-cased, split on separators;
    leading NR/BGE/CAUSE/NO/OG dropped (AR "OG O1S-15-14"), NR/NRN dropped
    anywhere; one key joined raw, one with leading zeros stripped (TG
    "RBOG-2025-Nr--01" = "RBOG-2025-1").
  - their truncated 20-character dockets are prefix-matched.
  - GE: they key by case number (A/1411/2026), we by ruling number
    (ACJC/...); the case number is read off our PDF name
    (ATAS_000573_2022_A_97_2020.pdf).
  - ZH Baurekurs, Bundesrat, NW, AG Grundbuch/Baugesetzgebung: their slug is
    the source's file or page name, so our URL stems are keys too; ZH file
    names also lose the site's file counter ("BRGE-I-0165-0167-2012-664").
  - UR: "2026-OG-V-25-16-Leis" is publication year + docket + title word.
  - old EVG dockets: "I-601-2006" on their side, "I 601/06" on ours.
  - XX_Upload: the canton comes from the signature (ZG_UPL_001 -> ZG).
  - CH signatures also search canton CE. Note the mirror holds no ECtHR rows
    (export_parquet.EXCLUDED_COURTS, a licensing decision), so CH_BGE_012 is
    allow-listed as not auditable here.
  - VD: hashed IDs resolved to the prestations.vd.ch uuid (see 4 above).
  - same-date fallback only for spiders whose keys cannot be compared
    (DATE_FALLBACK_SPIDERS) and for unkeyable dockets (hashes, slugs without
    a number). Anywhere else it would hide exactly the kind of gap this audit
    exists to find: on 11-08 it hid 652 VD CDAP and 22 TA_SST misses.

Output: logs/parity_audit.json (per spider: theirs, matched by method,
allow-listed, open residuals with docket / date / their URL, first_seen) plus a
dated copy under logs/parity_audit/. A residual has to be open in two runs at
least six days apart before it counts as confirmed: the mirror lags, and a
ruling decided last week may simply not be published yet. One ntfy message
goes out per run, and only when NEW residuals were confirmed. The first run
(no previous report) is a baseline and sends nothing. Known and explained
residuals live in scripts/parity_audit_allowlist.json, each with a reason.

Usage:
    python3 scripts/parity_audit.py                    # weekly run (systemd)
    python3 scripts/parity_audit.py --no-ntfy          # by hand
    python3 scripts/parity_audit.py --offline          # rematch cached inputs
"""
from __future__ import annotations

import argparse
import bisect
import collections
import gzip
import html
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

ES_BASE = "https://entscheidsuche.ch"
SITEMAP_DIR_URL = ES_BASE + "/docs/Sitemaps/"
SNAPSHOT_URL = ES_BASE + "/docs/Snapshots/{day}.json"
SEARCH_URL = ES_BASE + "/_searchV2.php"
DOC_URL = ES_BASE + "/docs/{spider}/{id}.json"
VIEW_URL = ES_BASE + "/view/{id}"

HF_REPO = "voilaj/swiss-caselaw"
HF_API = "https://huggingface.co/api/datasets/" + HF_REPO
HF_RESOLVE = "https://huggingface.co/datasets/" + HF_REPO + "/resolve/{rev}/data/{name}"
OUR_COLUMNS = ("court", "canton", "docket_number", "docket_number_2",
               "decision_date", "bge_reference", "source_url", "pdf_url")

# A generic client name (no contact details, no project name in the UA).
UA = "Mozilla/5.0 (compatible; research-audit/1.0)"
ES_INTERVAL = 1.0                      # seconds between entscheidsuche requests

DEFAULT_OUT = REPO / "logs" / "parity_audit.json"
DEFAULT_HISTORY = REPO / "logs" / "parity_audit"
DEFAULT_CACHE = REPO / "logs" / "parity_audit_cache"
DEFAULT_ALLOWLIST = REPO / "scripts" / "parity_audit_allowlist.json"

# Operator topic from /opt/caselaw/ops.env (systemd drop-in), never in the code
# (see publish.NTFY_TOPIC, 2026-10-29). Unset, nothing is sent.
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")
NTFY_URL = f"https://ntfy.sh/{NTFY_TOPIC}" if NTFY_TOPIC else ""

CONFIRM_MIN_DAYS = 6                   # open in two runs at least this far apart
MAX_SITEMAP_FAILURE_SHARE = 0.2        # abort above this; a partial list is noise

# Spider prefix -> canton group searched on our side. CH also covers our
# Council-of-Europe rows: CH_BGE_012 lists the ECtHR judgments the BGE
# collection published, which we hold under bge_egmr / hudoc_ch.
CANTON_OVERRIDE = {"TA": "CH"}
CANTON_SEARCH = {"CH": ("CH", "CE")}

# Spiders whose dockets cannot be compared with ours, so a same-date match is
# the only evidence available: GE lists case numbers for rulings we key by
# ruling number and whose PDF name carries no case number; VD FindInfo lists
# hashed IDs; EDOEB, WEKO and Bundesrat use title slugs. Elsewhere a date match
# would hide a real gap (2026-11-08: 652 VD CDAP date "matches" were all false).
DATE_FALLBACK_SPIDERS = frozenset({
    "GE_Gerichte", "VD_FindInfo", "CH_EDOEB", "CH_WEKO", "CH_Bundesrat",
    "GL_Omni", "CH_VB",
})

# Our courts whose URL stems are keys: their slug is the source's file name
# (ZH Baurekurs PDFs, bj.admin.ch pages, nw.ch publication ids, ag.ch PDFs).
URL_STEM_COURTS = frozenset({
    "zh_baurekursgericht", "ch_bundesrat", "nw_gerichte", "ag_weitere",
    "ag_baugesetzgebung", "ch_edoeb", "ch_weko",
})

UUID_RX = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
HASH_DOCKET_RX = re.compile(r"^[0-9a-f]{20}$")
GE_CASE_RX = re.compile(r"_([A-Z]{1,4})_(\d{1,6})_(\d{4})$")
ID_RX = re.compile(r"^([A-Z]{2}_[^\W_]+_\d{3})_(.*?)(?:_(\d{4}-\d{2}-\d{2}))?$")
LOC_RX = re.compile(r"<loc>\s*https?://entscheidsuche\.ch/view/([^<\s]+)\s*</loc>")
SITEMAP_HREF_RX = re.compile(r'href="([^"?/]+\.xml)"')
LEAF_SIG_RX = re.compile(r"^[A-Z]{2}_[^\W_]+_\d{3}$")


# ── normalisation and keys ──────────────────────────────────────────────

TAG_RX = re.compile(r"<[^>]+>")
LEAD_DROP = {"NR", "NRN", "BGE", "CAUSE", "NO", "OG"}
ANY_DROP = {"NR", "NRN"}
HTML_MARK = {"DIV", "SPAN", "STYLE", "BR", "FONT"}
HTML_WORDS = HTML_MARK | {"P", "COLOR", "RGB", "CLASS"}
PLACEHOLDER_DOCKETS = {"", "UNKNOWN", "NONE", "NULL"}


def tokens(s: str | None) -> list[str]:
    s = TAG_RX.sub(" ", s or "").upper().strip()
    t = [x for x in re.split(r"[^0-9A-Z]+", s) if x]
    # Slugs of HTML-polluted dockets (SH "-div-Nr--50-2020-2--").
    if any(x in HTML_MARK for x in t) or (len(t) >= 3 and t[0] == "P" and t[-1] == "P"):
        t = [x for x in t if x not in HTML_WORDS]
    while t and t[0] in LEAD_DROP:
        t = t[1:]
    return [x for x in t if x not in ANY_DROP]


def keys(s: str | None) -> list[str]:
    """Exact-match keys of a docket: joined raw, joined with zeros stripped, and
    (for a trailing four-digit year) with the year shortened to two digits."""
    t = tokens(s)
    if not t or "".join(t) in PLACEHOLDER_DOCKETS:
        return []
    k1 = "".join(t)
    norm = [str(int(x)) if x.isdigit() else x for x in t]
    out = [k1, "|".join(norm)]
    # Old EVG dockets: they list "I-601-2006", the court and we write "I 601/06".
    last = t[-1]
    if len(t) >= 3 and len(last) == 4 and last.isdigit() and last[:2] in ("19", "20"):
        out.append("|".join(norm[:-1] + [str(int(last[2:]))]))
    return list(dict.fromkeys(out))


def unkeyable(docket: str) -> bool:
    """Hash IDs (VD, TI) and slugs without a number cannot be compared."""
    return bool(HASH_DOCKET_RX.match(docket or "")) or not re.search(r"\d", "".join(tokens(docket)))


def url_stem(url: str | None) -> str | None:
    if not url:
        return None
    path = urllib.parse.unquote(urllib.parse.urlsplit(url).path).rstrip("/")
    stem = path.rsplit("/", 1)[-1]
    stem = re.sub(r"\.(pdf|html?|docx?|rtf)$", "", stem, flags=re.I)
    if len(stem) < 5 or not re.search(r"\d", stem) or UUID_RX.fullmatch(stem.lower()):
        return None
    return stem


def norm_url(url: str | None) -> str | None:
    if not url:
        return None
    u = urllib.parse.unquote(url.strip()).lower()
    u = re.sub(r"^https?://(www\.)?", "", u).split("#", 1)[0].rstrip("/")
    return u or None


def ge_case_number(pdf_url: str | None) -> str | None:
    """ATAS_000573_2022_A_97_2020.pdf -> A/97/2020 (the cause number)."""
    stem = url_stem(pdf_url)
    m = GE_CASE_RX.search(stem or "")
    return f"{m.group(1)}/{int(m.group(2))}/{m.group(3)}" if m else None


def es_canton(spider: str, sig: str | None = None) -> str:
    # XX_Upload holds uploads for many courts: the signature names the canton.
    code = sig[:2] if spider.startswith("XX") and sig else spider[:2]
    return CANTON_OVERRIDE.get(code, code)


def _ur_docket(d: str) -> list[str]:
    m = re.search(r"OG-[A-Z]{1,3}-\d+-\d+", d)
    return [m.group(0)] if m else []


# Their docket -> extra candidates, per spider.
THEIR_DOCKET_RULES = {
    # "2026-OG-V-25-16-Leis": publication year + docket + the first title word.
    "UR_Gerichte": _ur_docket,
    # File names "BRGE-I-0165-0167-2012-664": docket + the site's file counter.
    "ZH_Baurekurs": lambda d: [re.sub(r"^((?:BRGE|BRKE)-[IV]+-.*-(?:19|20)\d\d)-\d+$", r"\1", d)],
}


def their_dockets(spider: str, docket: str, extra: list[str] = ()) -> list[str]:
    rule = THEIR_DOCKET_RULES.get(spider)
    out = list(extra) + (rule(docket) if rule else []) + [docket]
    return list(dict.fromkeys(x for x in out if x))


# ── their side ──────────────────────────────────────────────────────────

class Http:
    """urllib with retries and a minimum interval per host family."""

    def __init__(self, interval: float = ES_INTERVAL, retries: int = 3,
                 opener=None, sleep=time.sleep):
        self.interval = interval
        self.retries = retries
        self.open = opener or urllib.request.urlopen
        self.sleep = sleep
        self._last = 0.0
        self.requests = 0

    def _wait(self):
        delta = time.monotonic() - self._last
        if delta < self.interval:
            self.sleep(self.interval - delta)
        self._last = time.monotonic()

    def get(self, url: str, data: bytes | None = None, headers: dict | None = None,
            method: str | None = None, timeout: int = 90, polite: bool = True,
            allow_404: bool = False):
        h = {"User-Agent": UA, **(headers or {})}
        last = None
        for attempt in range(self.retries):
            if polite:
                self._wait()
            self.requests += 1
            try:
                req = urllib.request.Request(url, data=data, headers=h, method=method)
                with self.open(req, timeout=timeout) as r:
                    return r.read(), r
            except urllib.error.HTTPError as e:
                if e.code == 404 and allow_404:
                    return None, None
                if e.code in (403, 404, 410, 429) and attempt == self.retries - 1:
                    raise
                last = e
            except Exception as e:      # network errors: retry with backoff
                last = e
            self.sleep(5 * (attempt + 1))
        raise RuntimeError(f"GET {url} failed: {last}")


def parse_es_id(es_id: str) -> dict:
    m = ID_RX.match(es_id)
    if not m:
        return {"sig": None, "docket": es_id, "date": None}
    # Undated IDs end in "_nodate" (AR) or a bare year (TG, TPF: "TPF-2004-1_2004").
    docket = re.sub(r"_(nodate|\d{4})$", "", m.group(2))
    return {"sig": m.group(1), "docket": docket, "date": m.group(3)}


def parse_sitemap(text: str) -> list[str]:
    return [urllib.parse.unquote(html.unescape(m)) for m in LOC_RX.findall(text)]


def spider_of(filename: str) -> str:
    return re.sub(r"_\d+\.xml$", "", filename.rsplit("/", 1)[-1])


def list_sitemaps(http: Http) -> list[str]:
    body, _ = http.get(SITEMAP_DIR_URL)
    names = sorted(set(SITEMAP_HREF_RX.findall(body.decode("utf-8", "replace"))))
    if not names:
        raise RuntimeError("sitemap directory listing has no *.xml entries")
    return names


def fetch_sitemaps(http: Http, cache: Path | None) -> tuple[dict[str, str], list[str]]:
    """Return {sitemap filename: xml text} and the names that failed."""
    out, failed = {}, []
    names = list_sitemaps(http)
    for name in names:
        try:
            body, _ = http.get(SITEMAP_DIR_URL + name, timeout=180)
            out[name] = body.decode("utf-8", "replace")
            if cache:
                (cache / "sitemaps").mkdir(parents=True, exist_ok=True)
                (cache / "sitemaps" / name).write_text(out[name], encoding="utf-8")
        except Exception as e:
            print(f"sitemap {name} failed: {e}", file=sys.stderr)
            failed.append(name)
    if names and len(failed) / len(names) > MAX_SITEMAP_FAILURE_SHARE:
        raise RuntimeError(f"{len(failed)} of {len(names)} sitemaps failed; not auditing a partial list")
    return out, failed


def fetch_snapshot(http: Http, today: date) -> tuple[str | None, dict]:
    for back in range(0, 4):
        day = (today - timedelta(days=back)).isoformat()
        body, _ = http.get(SNAPSHOT_URL.format(day=day), allow_404=True)
        if body:
            return day, json.loads(body)
    return None, {}


def build_listing(sitemaps: dict[str, str]) -> dict[str, dict]:
    """{es_id: entry}, deduplicated by ID (their sitemaps overlap)."""
    listing: dict[str, dict] = {}
    for name in sorted(sitemaps):
        spider = spider_of(name)
        for es_id in parse_sitemap(sitemaps[name]):
            if es_id in listing:
                continue
            e = parse_es_id(es_id)
            e.update(id=es_id, spider=spider, dockets=their_dockets(spider, e["docket"]),
                     listed=True)
            listing[es_id] = e
    return listing


def enumeration_targets(listing: dict[str, dict], snapshot: dict) -> list[tuple[str, int, int]]:
    """Leaf signatures whose index count exceeds what the sitemaps list."""
    listed = collections.Counter(e["sig"] for e in listing.values() if e["sig"])
    out = []
    for sig, n in (snapshot.get("total") or {}).items():
        if LEAF_SIG_RX.match(sig) and n > listed.get(sig, 0):
            out.append((sig, n, listed.get(sig, 0)))
    return sorted(out, key=lambda x: x[0])


def enumerate_signature(http: Http, sig: str, max_pages: int) -> list[dict]:
    hits_out, after = [], None
    for _ in range(max_pages):
        body = {"size": 1000, "_source": ["id", "date", "reference"],
                "track_total_hits": True,
                "query": {"bool": {"filter": [{"terms": {"hierarchy": [sig]}}]}},
                "sort": [{"id": "asc"}]}
        if after:
            body["search_after"] = after
        raw, _ = http.get(SEARCH_URL, data=json.dumps(body).encode(),
                          headers={"Content-Type": "application/json"}, method="POST")
        hits = json.loads(raw)["hits"]["hits"]
        for h in hits:
            s = h.get("_source") or {}
            if s.get("id"):
                hits_out.append({"id": s["id"], "date": s.get("date"),
                                 "reference": s.get("reference") or []})
        if len(hits) < 1000:
            return hits_out
        after = hits[-1].get("sort")
    print(f"enumeration of {sig} stopped at {max_pages} pages", file=sys.stderr)
    return hits_out


def add_unlisted(listing: dict[str, dict], enumerated: dict[str, list[dict]]) -> int:
    """Fold index entries the sitemaps do not list into the inventory."""
    sig_spider: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for e in listing.values():
        if e["sig"]:
            sig_spider[e["sig"]][e["spider"]] += 1
    n = 0
    for sig, hits in enumerated.items():
        spider = (sig_spider[sig].most_common(1)[0][0] if sig_spider.get(sig)
                  else sig.rsplit("_", 1)[0])
        for h in hits:
            if h["id"] in listing:
                continue
            e = parse_es_id(h["id"])
            refs = [r for r in (h.get("reference") or []) if isinstance(r, str)]
            d = (h.get("date") or "")[:10] or e["date"]
            e.update(id=h["id"], spider=spider, sig=e["sig"] or sig,
                     dockets=their_dockets(spider, e["docket"], refs),
                     date=d or None, listed=False)
            listing[h["id"]] = e
            n += 1
    return n


def fetch_doc(http: Http, entry: dict) -> dict:
    """Their document JSON: the dockets it names (Num) and the source URLs.

    Metadata only; the text is never read. Some spiders store the PDF block as
    a stringified dict (TA_SST)."""
    raw, _ = http.get(DOC_URL.format(spider=entry["spider"],
                                     id=urllib.parse.quote(entry["id"])),
                      allow_404=True)
    info: dict = {"num": [], "urls": []}
    if not raw:
        info["missing"] = True
        return info
    try:
        doc = json.loads(raw)
    except ValueError:
        return info
    info["num"] = [n for n in (doc.get("Num") or []) if isinstance(n, str)][:5]
    for part in ("PDF", "HTML"):
        block = doc.get(part)
        url = None
        if isinstance(block, dict):
            url = block.get("URL")
        elif isinstance(block, str):
            m = re.search(r"""['"]URL['"]:\s*['"]([^'"]+)['"]""", block)
            url = m.group(1) if m else None
        if url:
            info["urls"].append(url)
    return info


# ── our side (HF mirror) ────────────────────────────────────────────────

class RangeFile(io.RawIOBase):
    """Seekable read-only view of a remote file through HTTP range requests.

    read_parquet_rows() prefetches the exact byte ranges of the column chunks it
    needs (from the parquet footer), coalesced, so the heavy columns between
    them (full_text) are never downloaded. Anything else (the footer) is read
    in small blocks."""

    BLOCK = 256 << 10
    GAP = 128 << 10                   # merge ranges closer than this

    def __init__(self, url: str, http: Http, size: int):
        self.url, self.http, self.size, self.pos = url, http, size, 0
        self._spans: list[tuple[int, bytes]] = []     # prefetched, kept
        self._blocks: collections.OrderedDict[int, bytes] = collections.OrderedDict()
        self.fetched = 0

    def seekable(self):
        return True

    def readable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, off, whence=0):
        self.pos = off if whence == 0 else (self.pos + off if whence == 1 else self.size + off)
        return self.pos

    def _get(self, start: int, end: int) -> bytes:
        body, _ = self.http.get(self.url, headers={"Range": f"bytes={start}-{end - 1}"},
                                polite=False, timeout=300)
        if len(body) != end - start:
            raise RuntimeError(f"short range read {start}-{end}: {len(body)} bytes")
        self.fetched += len(body)
        return body

    def prefetch(self, ranges: list[tuple[int, int]]) -> None:
        merged: list[list[int]] = []
        for a, b in sorted(ranges):
            if merged and a - merged[-1][1] <= self.GAP:
                merged[-1][1] = max(merged[-1][1], b)
            else:
                merged.append([a, b])
        self._spans = [(a, self._get(a, min(b, self.size))) for a, b in merged]

    def _block(self, i: int) -> bytes:
        if i not in self._blocks:
            start = i * self.BLOCK
            self._blocks[i] = self._get(start, min(start + self.BLOCK, self.size))
            if len(self._blocks) > 16:
                self._blocks.popitem(last=False)
        return self._blocks[i]

    def read(self, n=-1):
        if n is None or n < 0:
            n = self.size - self.pos
        n = max(0, min(n, self.size - self.pos))
        for a, data in self._spans:
            if a <= self.pos and self.pos + n <= a + len(data):
                out = data[self.pos - a:self.pos - a + n]
                self.pos += n
                return out
        out = bytearray()
        while n > 0:
            i, off = divmod(self.pos, self.BLOCK)
            chunk = self._block(i)[off:off + n]
            if not chunk:
                break
            out += chunk
            self.pos += len(chunk)
            n -= len(chunk)
        return bytes(out)

    def readinto(self, b):
        d = self.read(len(b))
        b[:len(d)] = d
        return len(d)


def read_parquet_rows(fileobj, court_hint: str | None = None) -> list[dict]:
    import pyarrow.parquet as pq
    pf = pq.ParquetFile(fileobj)
    names = pf.schema_arrow.names
    cols = [c for c in OUR_COLUMNS if c in names]
    if hasattr(fileobj, "prefetch"):
        md, ranges = pf.metadata, []
        for g in range(md.num_row_groups):
            rg = md.row_group(g)
            for j in range(rg.num_columns):
                c = rg.column(j)
                if c.path_in_schema in cols:
                    start = c.data_page_offset
                    if c.has_dictionary_page and c.dictionary_page_offset is not None:
                        start = min(start, c.dictionary_page_offset)
                    ranges.append((start, start + c.total_compressed_size))
        fileobj.prefetch(ranges)
    rows = pf.read(columns=cols, use_threads=False).to_pylist()
    if court_hint and "court" not in cols:
        for r in rows:
            r["court"] = court_hint
    return rows


def hf_revision(http: Http) -> str:
    raw, _ = http.get(HF_API + "/revision/main", polite=False)
    return json.loads(raw)["sha"]


def hf_parquet_files(http: Http, rev: str) -> list[tuple[str, int]]:
    raw, _ = http.get(f"{HF_API}/tree/{rev}/data", polite=False)
    out = []
    for f in json.loads(raw):
        name = f.get("path", "").rsplit("/", 1)[-1]
        if f.get("type") == "file" and name.endswith(".parquet") and not name.startswith("delta-"):
            out.append((name, int(f.get("size") or 0)))
    if not out:
        raise RuntimeError("HF mirror lists no court parquets")
    return out


def load_ours_hf(http: Http, cache: Path | None) -> tuple[list[dict], dict]:
    rev = hf_revision(http)
    cached = cache / f"ours-{rev[:12]}.jsonl.gz" if cache else None
    if cached and cached.exists():
        with gzip.open(cached, "rt", encoding="utf-8") as fh:
            rows = [json.loads(l) for l in fh]
        return rows, {"source": "hf", "revision": rev, "files": None, "cached": True}
    rows, files = [], hf_parquet_files(http, rev)
    for name, size in files:
        url = HF_RESOLVE.format(rev=rev, name=name)
        # The resolve URL redirects to a signed CDN URL; range-read that one.
        _, resp = http.get(url, method="HEAD", polite=False)
        final = resp.geturl() if resp is not None else url
        length = int((resp.headers.get("Content-Length") if resp is not None else 0) or size)
        part = read_parquet_rows(RangeFile(final, http, length), court_hint=name[:-8])
        rows.extend(part)
    if cached:
        cache.mkdir(parents=True, exist_ok=True)
        for old in cache.glob("ours-*.jsonl.gz"):
            old.unlink()
        tmp = cached.with_suffix(".tmp")
        with gzip.open(tmp, "wt", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        tmp.replace(cached)
    return rows, {"source": "hf", "revision": rev, "files": len(files), "cached": False}


def load_ours_dir(path: Path) -> tuple[list[dict], dict]:
    """Local *.parquet / *.jsonl (tests, or a mirror already on disk)."""
    rows = []
    for f in sorted(path.iterdir()):
        if f.name.startswith("delta-"):
            continue
        if f.suffix == ".parquet":
            with open(f, "rb") as fh:
                rows.extend(read_parquet_rows(fh, court_hint=f.stem))
        elif f.suffix == ".jsonl":
            for line in f.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    r = json.loads(line)
                    r.setdefault("court", f.stem)
                    rows.append(r)
    return rows, {"source": "dir", "path": str(path)}


# ── matching ────────────────────────────────────────────────────────────

class OurIndex:
    def __init__(self, rows: list[dict]):
        self.rows = rows
        self.key: dict[str, dict[str, int]] = collections.defaultdict(dict)
        self.by_date: dict[str, dict[str, list[int]]] = collections.defaultdict(
            lambda: collections.defaultdict(list))
        self.uuid: dict[str, int] = {}
        for i, r in enumerate(rows):
            cant = r.get("canton") or "?"
            court = r.get("court") or ""
            ds = [r.get("docket_number"), r.get("docket_number_2"), r.get("bge_reference")]
            if court.startswith("ge_"):
                ds.append(ge_case_number(r.get("pdf_url")))
            if court in URL_STEM_COURTS:
                ds += [url_stem(r.get("pdf_url")), url_stem(r.get("source_url"))]
            for d in ds:
                for k in keys(d):
                    self.key[cant].setdefault(k, i)
            if cant == "VD":
                for u in (r.get("pdf_url"), r.get("source_url")):
                    m = UUID_RX.search((u or "").lower())
                    if m:
                        self.uuid.setdefault(m.group(0), i)
            if r.get("decision_date"):
                self.by_date[cant][str(r["decision_date"])[:10]].append(i)
        self.sorted_keys = {c: sorted(k for k in d if "|" not in k) for c, d in self.key.items()}
        self._url: dict[str, int] | None = None

    def url_index(self) -> dict[str, int]:
        """Normalised source URL -> row, built on first use. URLs shared by more
        than three rows (portal landing pages) are left out."""
        if self._url is None:
            seen: dict[str, int] = {}
            count: collections.Counter = collections.Counter()
            for i, r in enumerate(self.rows):
                for u in {norm_url(r.get("pdf_url")), norm_url(r.get("source_url"))}:
                    if u:
                        count[u] += 1
                        seen.setdefault(u, i)
            self._url = {u: i for u, i in seen.items() if count[u] <= 3}
        return self._url

    def resolve(self, cantons, info: dict) -> int | None:
        """Match on what their document JSON names: dockets, then URLs / uuid."""
        hit = self.exact(cantons, info.get("num") or [])
        if hit is not None:
            return hit
        urls = self.url_index()
        for u in info.get("urls") or []:
            i = urls.get(norm_url(u))
            if i is not None:
                return i
            m = UUID_RX.search(u.lower())
            if m and m.group(0) in self.uuid:
                return self.uuid[m.group(0)]
        return None

    def exact(self, cantons, dockets) -> int | None:
        for d in dockets:
            for k in keys(d):
                for c in cantons:
                    i = self.key.get(c, {}).get(k)
                    if i is not None:
                        return i
        return None

    def prefix(self, cantons, docket, want_date, claimed) -> int | None:
        t = tokens(docket)
        p = "".join(t)
        if len(p) < 6:
            return None
        best = None
        for c in cantons:
            L = self.sorted_keys.get(c, [])
            j = bisect.bisect_left(L, p)
            for k in L[j:j + 50]:
                if not k.startswith(p):
                    break
                i = self.key[c][k]
                score = (str(self.rows[i].get("decision_date") or "")[:10] == want_date,
                         i not in claimed)
                if best is None or score > best[0]:
                    best = (score, i)
        return best[1] if best else None


def match(listing: dict[str, dict], idx: OurIndex) -> tuple[dict, list[dict]]:
    """Return (per-spider counters, unmatched entries)."""
    stats: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    claimed: dict[str, set] = collections.defaultdict(set)
    pending = []
    for e in listing.values():
        cant = es_canton(e["spider"], e.get("sig"))
        cants = CANTON_SEARCH.get(cant, (cant,))
        s = stats[e["spider"]]
        s["theirs"] += 1
        hit = idx.exact(cants, e["dockets"])
        how = "exact"
        if hit is None and len(e["docket"]) >= 19:   # docket truncated in the ID
            hit = idx.prefix(cants, e["docket"], e.get("date"), claimed[cant])
            how = "prefix"
        if hit is None:
            pending.append(e)
            continue
        s[how] += 1
        claimed[cant].add(hit)
    unmatched, used = [], collections.Counter()
    for e in pending:
        cant = es_canton(e["spider"], e.get("sig"))
        dt = e.get("date")
        if dt and (e["spider"] in DATE_FALLBACK_SPIDERS or unkeyable(e["docket"])):
            free = len([i for i in idx.by_date[cant].get(dt, ()) if i not in claimed[cant]])
            if free - used[(cant, dt)] > 0:
                used[(cant, dt)] += 1
                stats[e["spider"]]["date"] += 1
                continue
        unmatched.append(e)
    return stats, unmatched


def resolve_docs(unmatched: list[dict], idx: OurIndex, http: Http | None, cache: dict,
                 stats: dict, max_fetch: int) -> tuple[list[dict], int]:
    """Second look at each residual through their document JSON (Num dockets,
    source URL, VD portal uuid). Cached per ID forever, so after the first run
    only new residuals cost a request; unkeyable IDs go first."""
    order = sorted(unmatched, key=lambda e: (not unkeyable(e["docket"]), e["spider"], e["id"]))
    left, fetched = [], 0
    for e in order:
        info = cache.get(e["id"])
        if info is None:
            if http is None or fetched >= max_fetch:
                left.append(e)
                continue
            fetched += 1
            try:
                info = fetch_doc(http, e)
            except Exception as ex:
                print(f"doc {e['id']}: {ex}", file=sys.stderr)
                left.append(e)
                continue
            cache[e["id"]] = info
        cant = es_canton(e["spider"], e.get("sig"))
        if idx.resolve(CANTON_SEARCH.get(cant, (cant,)), info) is not None:
            stats[e["spider"]]["doc"] += 1
        else:
            left.append(e)
    return left, fetched


# ── allow-list, history, notification ───────────────────────────────────

def load_allowlist(path: Path) -> list[dict]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = data.get("entries", [])
    for a in entries:
        if not a.get("reason"):
            raise ValueError(f"allow-list entry without a reason: {a}")
        a["_ids"] = set(a.get("ids") or [])
        a["_rx"] = re.compile(a["id_pattern"]) if a.get("id_pattern") else None
    return entries


def allow_reason(e: dict, allow: list[dict]) -> str | None:
    for a in allow:
        if a.get("spider") and a["spider"] != e["spider"]:
            continue
        if e["id"] in a["_ids"] or (a["_rx"] and a["_rx"].search(e["id"])):
            return a["reason"]
    return None


def stale_allowlist(allow: list[dict], residual_ids: set[str]) -> list[str]:
    """Allow-listed IDs that are no longer residual (fixed or delisted)."""
    out = []
    for a in allow:
        out += sorted(i for i in a["_ids"] if i not in residual_ids)
    return out


def classify(unmatched: list[dict], allow: list[dict], previous: dict | None,
             today: date) -> tuple[list[dict], list[dict], list[dict]]:
    """Split residuals into (open, allow-listed, newly confirmed).

    first_seen / notified carry over from the previous report. A residual is
    confirmed once it was open in an earlier run at least CONFIRM_MIN_DAYS
    ago; it is announced once. With no previous report every open residual is
    the baseline: recorded, never announced."""
    prev = {}
    if previous:
        for r in previous.get("residuals", []):
            prev[r["id"]] = r
    baseline = previous is None
    open_, allowed, confirmed = [], [], []
    for e in sorted(unmatched, key=lambda x: (x["spider"], x["id"])):
        rec = {"id": e["id"], "spider": e["spider"], "sig": e.get("sig"),
               "docket": e["docket"], "date": e.get("date"),
               "listed": e.get("listed", True), "url": VIEW_URL.format(id=e["id"])}
        reason = allow_reason(e, allow)
        if reason:
            rec["reason"] = reason
            allowed.append(rec)
            continue
        p = prev.get(e["id"])
        rec["first_seen"] = p["first_seen"] if p else today.isoformat()
        rec["notified"] = p.get("notified") if p else ("baseline" if baseline else None)
        age = (today - date.fromisoformat(rec["first_seen"])).days
        if not rec["notified"] and age >= CONFIRM_MIN_DAYS:
            rec["notified"] = today.isoformat()
            confirmed.append(rec)
        open_.append(rec)
    return open_, allowed, confirmed


def ntfy_message(confirmed: list[dict], report_path: str) -> tuple[str, str]:
    by = collections.Counter(r["spider"] for r in confirmed)
    title = f"Parity audit: {len(confirmed)} new residual(s) vs entscheidsuche"
    lines = [f"{sp}: {n}" for sp, n in by.most_common(12)]
    if len(by) > 12:
        lines.append(f"... and {len(by) - 12} more spiders")
    lines.append("")
    for r in confirmed[:8]:
        lines.append(f"{r['docket']} {r['date'] or ''} {r['url']}")
    lines.append("")
    lines.append(f"Open {report_path}. Fix the direct scraper against the court portal; "
                 "entscheidsuche is never a feed.")
    return title, "\n".join(lines)


def send_ntfy(title: str, body: str, url: str | None = None) -> bool:
    url = url if url is not None else NTFY_URL
    if not url:
        print("ntfy skipped: NTFY_TOPIC not set", file=sys.stderr)
        return False
    try:
        req = urllib.request.Request(url, data=body.encode("utf-8", "replace"),
                                     headers={"Title": title, "Tags": "mag", "Priority": "default"})
        urllib.request.urlopen(req, timeout=15)
        return True
    except Exception as e:
        print(f"ntfy post failed: {e}", file=sys.stderr)
        return False


# ── report ──────────────────────────────────────────────────────────────

def build_report(stats, open_, allowed, confirmed, stale, meta, today) -> dict:
    per = {}
    by_open = collections.defaultdict(list)
    for r in open_:
        by_open[r["spider"]].append(r)
    by_allow = collections.Counter(r["spider"] for r in allowed)
    for sp in sorted(stats):
        s = stats[sp]
        per[sp] = {
            "theirs": s["theirs"],
            "matched": s["exact"] + s["prefix"] + s["date"] + s["doc"],
            "matched_by": {k: s[k] for k in ("exact", "prefix", "date", "doc") if s[k]},
            "allowlisted": by_allow.get(sp, 0),
            "residual": len(by_open.get(sp, [])),
            "residual_by_year": dict(sorted(collections.Counter(
                (r["date"] or "none")[:4] for r in by_open.get(sp, [])).items())),
        }
    return {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "day": today.isoformat(),
        "policy": "entscheidsuche is an outside list only; a residual means fixing "
                  "our direct scraper against the court portal",
        "inputs": meta,
        "totals": {
            "theirs": sum(v["theirs"] for v in per.values()),
            "matched": sum(v["matched"] for v in per.values()),
            "allowlisted": len(allowed),
            "residual": len(open_),
            "newly_confirmed": len(confirmed),
        },
        "spiders": per,
        "newly_confirmed": [r["id"] for r in confirmed],
        "stale_allowlist": stale,
        "residuals": open_,
        "allowlisted": allowed,
    }


def write_json_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    tmp.replace(path)


def load_previous(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# ── main ────────────────────────────────────────────────────────────────

def gather_theirs(args, http: Http | None, cache: Path, today: date) -> tuple[dict, dict]:
    meta: dict = {}
    if args.sitemaps_dir or args.offline:
        sm_dir = Path(args.sitemaps_dir) if args.sitemaps_dir else cache / "sitemaps"
        sitemaps = {f.name: f.read_text(encoding="utf-8") for f in sorted(sm_dir.glob("*.xml"))}
        failed = []
    else:
        sitemaps, failed = fetch_sitemaps(http, cache)
    listing = build_listing(sitemaps)
    meta.update(sitemaps=len(sitemaps), sitemaps_failed=failed, listed_ids=len(listing))

    if args.snapshot:
        snap_day, snapshot = "file", json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
    elif args.offline:
        p = cache / "snapshot.json"
        snap_day, snapshot = ("cache", json.loads(p.read_text())) if p.exists() else (None, {})
    else:
        snap_day, snapshot = fetch_snapshot(http, today)
        if snapshot:
            (cache / "snapshot.json").write_text(json.dumps(snapshot), encoding="utf-8")
    meta.update(snapshot=snap_day, index_total=snapshot.get("total_alle"))

    enumerated: dict[str, list[dict]] = {}
    enum_cache = cache / "enumerated.json"
    if args.offline and enum_cache.exists():
        enumerated = json.loads(enum_cache.read_text(encoding="utf-8"))
    elif not args.no_enum and http is not None and snapshot:
        targets = enumeration_targets(listing, snapshot)
        for sig, n_index, n_listed in targets:
            try:
                enumerated[sig] = enumerate_signature(http, sig, args.max_enum_pages)
                print(f"enumerated {sig}: {len(enumerated[sig])} (index {n_index}, "
                      f"listed {n_listed})", file=sys.stderr)
            except Exception as e:
                print(f"enumeration {sig} failed: {e}", file=sys.stderr)
        enum_cache.write_text(json.dumps(enumerated), encoding="utf-8")
    meta["enumerated_signatures"] = sorted(enumerated)
    meta["unlisted_ids"] = add_unlisted(listing, enumerated)
    return listing, meta


def run(args) -> int:
    today = date.fromisoformat(args.today) if args.today else datetime.now(timezone.utc).date()
    cache = Path(args.cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    out = Path(args.out)
    http = None if args.offline else Http()

    listing, meta = gather_theirs(args, http, cache, today)
    if not listing:
        print("their inventory is empty; nothing to audit", file=sys.stderr)
        return 2

    if args.ours_dir:
        rows, ours_meta = load_ours_dir(Path(args.ours_dir))
    elif args.offline:
        files = sorted(cache.glob("ours-*.jsonl.gz"))
        if not files:
            print("--offline: no cached HF rows in the cache dir", file=sys.stderr)
            return 2
        with gzip.open(files[-1], "rt", encoding="utf-8") as fh:
            rows = [json.loads(l) for l in fh]
        ours_meta = {"source": "hf-cache", "file": files[-1].name}
    else:
        rows, ours_meta = load_ours_hf(Http(interval=0, retries=4), cache)
    ours_meta["rows"] = len(rows)
    meta["ours"] = ours_meta
    if not rows:
        print("our side is empty; refusing to report every entry as missing", file=sys.stderr)
        return 2

    idx = OurIndex(rows)
    stats, unmatched = match(listing, idx)

    doc_cache_path = cache / "doc_cache.json"
    try:
        doc_cache = json.loads(doc_cache_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        doc_cache = {}
    if not args.no_doc_resolve:
        unmatched, meta["doc_fetched"] = resolve_docs(unmatched, idx, http, doc_cache, stats,
                                                      args.max_doc_fetch)
        meta["doc_unresolved_uncached"] = sum(1 for e in unmatched if e["id"] not in doc_cache)
        tmp = doc_cache_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(doc_cache, ensure_ascii=False), encoding="utf-8")
        tmp.replace(doc_cache_path)

    allow = load_allowlist(Path(args.allowlist))
    previous = load_previous(out)
    open_, allowed, confirmed = classify(unmatched, allow, previous, today)
    stale = stale_allowlist(allow, {e["id"] for e in unmatched})
    meta["es_requests"] = http.requests if http else 0

    # Announce before writing, so a residual counts as notified only when the
    # message went out (a --no-ntfy run by hand must not swallow the alert).
    sent = False
    if confirmed and not args.no_ntfy:
        title, body = ntfy_message(confirmed, str(out))
        sent = send_ntfy(title, body)
    if not sent:
        for r in confirmed:
            r["notified"] = None
    report = build_report(stats, open_, allowed, confirmed, stale, meta, today)
    report["ntfy_sent"] = sent
    write_json_atomic(out, report)
    if args.history_dir:
        write_json_atomic(Path(args.history_dir) / f"{today.isoformat()}.json", report)

    t = report["totals"]
    print(f"parity audit {today}: theirs {t['theirs']:,}, matched {t['matched']:,}, "
          f"allow-listed {t['allowlisted']}, residual {t['residual']}, "
          f"newly confirmed {t['newly_confirmed']}"
          + (" (baseline run)" if previous is None else ""))
    for sp, v in report["spiders"].items():
        if v["residual"]:
            print(f"  {sp:30s} theirs {v['theirs']:>7}  residual {v['residual']:>5}  "
                  f"{v['residual_by_year']}")
    if stale:
        print(f"  stale allow-list IDs (no longer residual): {len(stale)}")
    if confirmed:
        print(f"  newly confirmed: {len(confirmed)}; ntfy {'sent' if sent else 'NOT sent'}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--history-dir", default=str(DEFAULT_HISTORY),
                    help="dated copies of each report ('' to skip)")
    ap.add_argument("--cache-dir", default=str(DEFAULT_CACHE))
    ap.add_argument("--allowlist", default=str(DEFAULT_ALLOWLIST))
    ap.add_argument("--ours-dir", help="local *.parquet/*.jsonl instead of the HF mirror")
    ap.add_argument("--sitemaps-dir", help="local sitemap XML files instead of fetching")
    ap.add_argument("--snapshot", help="local snapshot JSON instead of fetching")
    ap.add_argument("--offline", action="store_true",
                    help="no network: rematch the inputs cached by the last run")
    ap.add_argument("--no-enum", action="store_true", help="skip _searchV2.php enumeration")
    ap.add_argument("--no-doc-resolve", action="store_true",
                    help="skip the document-JSON second look at residuals")
    ap.add_argument("--max-enum-pages", type=int, default=400,
                    help="per signature, 1000 IDs a page")
    ap.add_argument("--max-doc-fetch", type=int, default=2000,
                    help="their document JSONs fetched per run (cached forever)")
    ap.add_argument("--no-ntfy", action="store_true")
    ap.add_argument("--today", help="override the run date (YYYY-MM-DD)")
    args = ap.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
