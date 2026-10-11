#!/usr/bin/env python3
"""Generic OAI-PMH harvester for Swiss OA legal scholarship sources.

Speaks vanilla OAI-PMH 2.0 (`oai_dc` metadata format). Streams records into
a JSONL file at output/legal_scholarship/<source>.jsonl.

Reused by every source that exposes OAI-PMH:
  - sui-generis.ch (OJS, set=suigeneris)
  - University IRs (ZORA, BORIS, SERVAL, UNIGE, edoc.unibas, Alexandria SG,
    FOLIA, LIBRA, e-Helvetica, …) with source-specific set filters
  - Other OJS-based Swiss law journals (LeGes if exposed)

Each record is normalized to a JSON dict with the canonical fields the
build_legal_scholarship.py builder expects.

Usage:
    python -m scrapers.scholarship.oai_pmh \
        --source sui_generis \
        --base-url https://sui-generis.ch/oai \
        --set suigeneris
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from xml.etree import ElementTree as ET

log = logging.getLogger("scholarship.oai_pmh")

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = REPO_ROOT / "output" / "legal_scholarship"

OAI_NS = "http://www.openarchives.org/OAI/2.0/"
DC_NS = "http://purl.org/dc/elements/1.1/"
OAI_DC_NS = "http://www.openarchives.org/OAI/2.0/oai_dc/"

_DOI_RE = re.compile(r"^(?:info:doi/|doi:|https?://(?:dx\.)?doi\.org/)(.+)$", re.I)
_ISSN_RE = re.compile(r"^urn:issn:(.+)$", re.I)
_URN_NBN_RE = re.compile(r"^urn:nbn:.+$", re.I)
_HTTP_RE = re.compile(r"^https?://", re.I)

# Strip XML 1.0-invalid control characters (everything 0x00-0x1F except
# tab/LF/CR). Some OAI providers (e.g. ex-ante.ch) emit \x17 in
# dc:description, which makes the response not well-formed.
_STRIP_INVALID_XML = re.compile(
    rb"[\x00-\x08\x0b\x0c\x0e-\x1f]",
)

# Map dc:language values onto ISO-639-1 short codes used elsewhere in the repo.
_LANG_MAP = {
    "deu": "de", "ger": "de", "de": "de", "de-DE": "de", "de_DE": "de",
    "fra": "fr", "fre": "fr", "fr": "fr", "fr-FR": "fr", "fr_FR": "fr",
    "ita": "it", "it": "it", "it-IT": "it",
    "eng": "en", "en": "en", "en-US": "en", "en-GB": "en",
    "roh": "rm", "rm": "rm",
}


def _normalize_lang(raw: str | None) -> str | None:
    if not raw:
        return None
    return _LANG_MAP.get(raw.strip(), raw.strip()[:2].lower() or None)


def _normalize_license(raw_list: list[str]) -> tuple[str | None, str | None]:
    """Return (license-code, license-url) from a list of dc:rights values.

    Strategy: ALL dc:rights values are scanned FIRST for a CC license URL.
    If one is found anywhere in the list, it wins regardless of position.
    Only when no CC URL is present do we fall back to the first non-empty
    free-form value (typically a copyright statement).

    Why: many OJS sources (ex-ante.ch, OpenLegalCommentary, …) emit two
    dc:rights — the copyright statement first, the CC URL second. Earlier
    logic took the first value it saw and stored "Copyright (c) 2024 …"
    as the license; we'd lose the CC code. Now CC wins.
    """
    cc_re = re.compile(
        r"creativecommons\.org/licenses/([a-z\-]+)/(\d+\.\d+)", re.I,
    )
    cleaned = [(r or "").strip() for r in raw_list if (r or "").strip()]
    # Pass 1: prefer any CC URL match
    for r in cleaned:
        m = cc_re.search(r)
        if m:
            kind = m.group(1).upper()
            ver = m.group(2)
            code = f"CC-{kind}-{ver}"
            url = r if _HTTP_RE.match(r) else None
            if not url:
                # Reconstruct canonical CC URL when only the path is given
                url = f"https://creativecommons.org/licenses/{m.group(1).lower()}/{ver}/"
            return code, url
    # Pass 2: fall back to first non-empty free-form value
    for r in cleaned:
        return r[:120], None
    return None, None


def _split_identifiers(ids: list[str]) -> dict:
    """Pick out DOI, ISSN, urn:nbn, and canonical URL from a list of dc:identifier."""
    out = {"doi": None, "issn": None, "urn": None, "url": None}
    for ident in ids:
        ident = (ident or "").strip()
        if not ident:
            continue
        m = _DOI_RE.match(ident)
        if m and not out["doi"]:
            out["doi"] = m.group(1).strip()
            continue
        m = _ISSN_RE.match(ident)
        if m and not out["issn"]:
            out["issn"] = m.group(1).strip()
            continue
        if _URN_NBN_RE.match(ident) and not out["urn"]:
            out["urn"] = ident
            continue
        if _HTTP_RE.match(ident) and not out["url"]:
            out["url"] = ident
    return out


def _parse_year(date_raw: str | None) -> int | None:
    if not date_raw:
        return None
    m = re.match(r"(\d{4})", date_raw)
    return int(m.group(1)) if m else None


def _record_to_dict(rec: ET.Element, source: str) -> dict | None:
    """Turn a single OAI-PMH <record> into our unified publication dict.

    Returns None for deleted records (status="deleted").
    """
    header = rec.find(f"{{{OAI_NS}}}header")
    if header is None:
        return None
    if (header.get("status") or "").lower() == "deleted":
        return None
    ident = header.findtext(f"{{{OAI_NS}}}identifier", default="").strip()
    datestamp = header.findtext(f"{{{OAI_NS}}}datestamp", default="").strip()
    setspecs = [
        e.text.strip() for e in header.findall(f"{{{OAI_NS}}}setSpec")
        if e.text and e.text.strip()
    ]

    md = rec.find(f"{{{OAI_NS}}}metadata")
    if md is None:
        return None
    dc = md.find(f"{{{OAI_DC_NS}}}dc")
    if dc is None:
        return None

    def _all(tag: str) -> list[str]:
        return [
            (e.text or "").strip()
            for e in dc.findall(f"{{{DC_NS}}}{tag}")
            if (e.text or "").strip()
        ]

    titles = _all("title")
    creators = _all("creator")
    descriptions = _all("description")
    publishers = _all("publisher")
    dates = _all("date")
    types = _all("type")
    identifiers = _all("identifier")
    sources = _all("source")
    languages = _all("language")
    rights = _all("rights")
    subjects = _all("subject")
    contributors = _all("contributor")
    formats = _all("format")
    relations = _all("relation")

    if not titles:
        return None

    idinfo = _split_identifiers(identifiers)
    lic_code, lic_url = _normalize_license(rights)

    # pub_type: derive from dc:type values
    pub_type = "article"
    type_lower = " ".join(t.lower() for t in types)
    if "doctoralthesis" in type_lower or "dissertation" in type_lower:
        pub_type = "dissertation"
    elif "masterthesis" in type_lower:
        pub_type = "master_thesis"
    elif "bachelorthesis" in type_lower:
        pub_type = "bachelor_thesis"
    elif "book" in type_lower:
        pub_type = "book"
    elif "bookpart" in type_lower or "chapter" in type_lower:
        pub_type = "chapter"
    elif "workingpaper" in type_lower or "preprint" in type_lower:
        pub_type = "working_paper"
    elif "report" in type_lower:
        pub_type = "report"

    return {
        "source": source,
        "source_record_id": ident,
        "datestamp": datestamp,
        "set_specs": setspecs,
        "pub_type": pub_type,
        "title": titles[0],
        "title_alt": titles[1:] or None,
        "authors": creators,
        "contributors": contributors,
        "abstract": descriptions[0] if descriptions else None,
        "abstract_alt": descriptions[1:] or None,
        "publisher": publishers[0] if publishers else None,
        "publication_date": dates[0] if dates else None,
        "year": _parse_year(dates[0] if dates else None),
        "types_raw": types,
        "doi": idinfo["doi"],
        "issn": idinfo["issn"],
        "urn": idinfo["urn"],
        "url": idinfo["url"],
        "all_identifiers": identifiers,
        "sources_raw": sources,
        "language": _normalize_lang(languages[0] if languages else None),
        "languages_raw": languages,
        "license": lic_code,
        "license_url": lic_url,
        "rights_raw": rights,
        "subjects": subjects,
        "formats": formats,
        "relations": relations,
    }


def _record_matches_subject(record_dict: dict, subject_keywords: list[str]) -> bool:
    """Return True if any of the record's dc:subject values contain any
    keyword (case-insensitive).

    Used to post-hoc filter big multi-faculty IRs to law content only.
    Tests against:
      - dc:subject strings (typically Dewey codes, keywords, MeSH terms)
      - record types (for DDC-style 'info:eu-repo/classification/ddc/340')
      - title (some IRs put DDC code in subject as plain '340')
    """
    if not subject_keywords:
        return True
    haystack = " ".join([
        " | ".join(record_dict.get("subjects") or []),
        " | ".join(record_dict.get("types_raw") or []),
        record_dict.get("title") or "",
        " | ".join(record_dict.get("languages_raw") or []),
    ]).lower()
    return any(k.lower() in haystack for k in subject_keywords)


# ── Windowed harvesting (2026-08-22) ─────────────────────────────────
#
# Some repositories silently truncate long ListRecords chains: UNIGE ends
# its chain after ~209 pages (~21k of ~124k records) with a clean "no more
# token" — no error, nothing to distinguish it from a genuinely complete
# harvest. The weekly harvest therefore "succeeded" at 1/6th coverage for
# months, freezing the source at records datestamped ≤ 2012 (GitHub #89's
# French-scholarship gap traces directly here).
#
# The countermeasure uses the one verifiable signal OAI gives us: the
# first page of a chain carries resumptionToken/@completeListSize. Harvest
# in datestamp windows; when a window's chain ends short of its declared
# size — or declares more than MAX_WINDOW_RECORDS up front — bisect the
# window and recurse. Windows small enough to finish in a few minutes also
# stay comfortably inside short server-side token TTLs (UNIGE issues
# ~minutes-scale expirationDate values).
#
# Datestamp caveat: OAI from/until filter on last-MODIFIED, not publication
# date, and platform migrations re-stamp wholesale (UNIGE's Identify claims
# earliestDatestamp 2016-04-01 while its oldest records carry 2008-10-29).
# So the floor is a hard constant, not Identify's word.
WINDOW_FLOOR = "1995-01-01"
MAX_WINDOW_RECORDS = 8000
_MAX_BISECT_DEPTH = 16
_FETCH_RETRIES = 3
# Day-atomic fallback (2026-08-24, UNIGE postmortem): when a window can't
# be shrunk — bisect floor, sub-day-lying server, or repeated chain
# errors — walk it WHOLE (force=True skips the oversize probe), retrying
# a bounded number of times, and record a gap instead of aborting the
# source. A forced chain gets a hard page ceiling so the fix for one
# pathology (abort spiral) can't hand the runtime budget to another
# (token loop / lying completeListSize walking forever).
_DAY_ATOMIC_ATTEMPTS = 2
_FORCE_MAX_PAGES = 500
_RETRY_BACKOFF_S = (5, 20)


class DeadlineReached(Exception):
    """The caller's time budget ran out before the next request."""


def _oai_error_body(e: urllib.error.HTTPError) -> bytes | None:
    """The body of a 4xx that carries an OAI-PMH <error>, else None.

    SONAR answers an empty window with HTTP 422 and a perfectly valid
    <error code="noRecordsMatch"/>. Treating the status as a fetch failure
    turned every empty window into a gap and sent bisection down through
    1995 one sub-day window at a time; unifr_law never produced a file
    (2026-09-22 catch-up, killed after 4 h at 0 records).
    """
    if not 400 <= e.code < 500:
        return None
    try:
        body = e.read()
    except Exception:                               # noqa: BLE001
        return None
    if b"OAI-PMH" in body and b"<error" in body:
        return body
    return None


def _fetch_with_retry(url: str, *, user_agent: str, timeout: int,
                      source: str, page: int,
                      deadline: float | None = None) -> bytes:
    """GET with retries — a transient 5xx must not end a harvest silently.

    With a `deadline` (time.monotonic()), no attempt starts after it and no
    attempt's timeout reaches past it; running out raises DeadlineReached so
    the caller can checkpoint instead of recording a failure.
    """
    last: Exception | None = None
    for attempt in range(_FETCH_RETRIES):
        attempt_timeout: float = timeout
        if deadline is not None:
            left = deadline - time.monotonic()
            if left <= 0:
                raise DeadlineReached()
            attempt_timeout = min(timeout, max(5.0, left))
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": user_agent,
                              "Accept": "application/xml"})
            with urllib.request.urlopen(req, timeout=attempt_timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            body = _oai_error_body(e)
            if body is not None:
                return body
            last = e
        except Exception as e:                      # noqa: BLE001
            last = e
        if deadline is not None and time.monotonic() >= deadline:
            raise DeadlineReached()
        if attempt < _FETCH_RETRIES - 1:
            wait = _RETRY_BACKOFF_S[min(attempt, len(_RETRY_BACKOFF_S) - 1)]
            log.warning("OAI fetch retry %d/%d (%s, page %d) in %ds: %s",
                        attempt + 1, _FETCH_RETRIES, source, page, wait, last)
            time.sleep(wait)
    raise last  # type: ignore[misc]


def _identify_granularity(base_url: str, *, user_agent: str,
                          timeout: int) -> bool:
    """True when the server declares seconds granularity via Identify.

    Matters twice: SONAR (Invenio) was believed to reject date-only
    from/until with HTTP 422 (re-probed 2026-10-11: its 422s are
    noRecordsMatch bodies on empty windows — see _oai_error_body), so
    windows are sent as full 'YYYY-MM-DDThh:mm:ssZ' there; and only seconds-granularity servers
    can accept the sub-day bisection boundaries that migration-burst days
    require. Defaults to False (date-only) when Identify is unreadable.
    """
    try:
        qs = urllib.parse.urlencode({"verb": "Identify"})
        req = urllib.request.Request(
            f"{base_url}?{qs}",
            headers={"User-Agent": user_agent, "Accept": "application/xml"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            root = ET.fromstring(r.read())
        gran = root.findtext(f"{{{OAI_NS}}}Identify/{{{OAI_NS}}}granularity") or ""
        return "hh:mm:ss" in gran.lower()
    except Exception as e:                          # noqa: BLE001
        log.warning("Identify/granularity probe failed (%s): %s — "
                    "using date-only windows", base_url, e)
        return False


def _day_bounds(a: str | None, b: str | None,
                seconds: bool) -> tuple[str | None, str | None]:
    """Format window boundaries at the server's granularity."""
    def lo(s):
        if s is None or "T" in s or not seconds:
            return s
        return f"{s[:10]}T00:00:00Z"

    def hi(s):
        if s is None or "T" in s or not seconds:
            return s
        return f"{s[:10]}T23:59:59Z"
    return lo(a), hi(b)


def _bisect_window(a: str, b: str, *,
                   seconds_granularity: bool = True) -> tuple[str, str, str] | None:
    """Split [a, b] at its midpoint; None when it cannot split further.

    Descends from date granularity into time-of-day when a window narrows
    below two days — platform migrations re-stamp tens of thousands of
    records onto a single day (UNIGE), so date-level splitting bottoms out
    while the day itself still exceeds every cap. Sub-day boundaries use
    the full OAI datetime form (UNIGE's Identify declares seconds
    granularity); servers restricted to day granularity reject those with
    badArgument, which surfaces as an aborted window rather than silence.
    """
    from datetime import datetime, timedelta, timezone

    def _parse(s: str, *, end: bool) -> datetime:
        if "T" in s:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        d = datetime.fromisoformat(s[:10]).replace(tzinfo=timezone.utc)
        return d.replace(hour=23, minute=59, second=59) if end else d

    def _fmt(dt: datetime, *, date_only_ok: bool) -> str:
        if date_only_ok and dt.hour == 0 and dt.minute == 0 and dt.second == 0:
            return dt.date().isoformat()
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    da = _parse(a, end=False)
    db = _parse(b, end=True)
    span = db - da
    if span <= timedelta(minutes=2):
        return None
    mid = da + span / 2
    mid = mid.replace(microsecond=0)
    sub_day = span < timedelta(days=2)
    if sub_day and not seconds_granularity:
        return None                      # server can't address sub-day ranges
    if not sub_day:
        # Stay at date granularity while it still has room.
        mid_date = da.date() + timedelta(days=span.days // 2)
        return (a, mid_date.isoformat(),
                (mid_date + timedelta(days=1)).isoformat())
    nxt = mid + timedelta(seconds=1)
    return (_fmt(da, date_only_ok=False) if "T" in a else a,
            _fmt(mid, date_only_ok=False),
            _fmt(nxt, date_only_ok=False))


Window = tuple  # (from, until, depth, parent_size)


def _window_plan(windowed: bool, from_date: str | None,
                 until_date: str | None) -> list[Window]:
    """The initial window stack for a harvest over [from_date, until_date]."""
    if windowed:
        from datetime import date, timedelta
        w_start = (from_date or WINDOW_FLOOR)[:10]
        w_end = (until_date or (date.today() + timedelta(days=1)).isoformat())[:10]
        return [(w_start, w_end, 0, None)]
    return [(from_date, until_date, 0, None)]


class _Walk:
    """Walks a LIFO stack of datestamp windows into an open JSONL handle.

    The window stack IS the cursor: windows are popped, harvested whole, and
    discarded; a window that comes back truncated or oversize is split and
    its halves pushed. With a `deadline`, the walk stops before the next
    request once the budget is spent and pushes the in-flight window back,
    so `stack` is exactly the work that remains — persist it and the walk
    resumes next run (resumption tokens die within minutes, windows don't).

    `record_removals` additionally writes `{"_removed": id}` marker lines for
    records the server reports deleted and records that fail the subject
    filter, so an incremental delta can be merged into an existing shard.
    """

    def __init__(self, base_url: str, source: str, *,
                 set_spec: str | None, metadata_prefix: str,
                 rate_limit: float, windowed: bool, max_window_records: int,
                 subject_filter: list[str] | None, fetch_timeout: int,
                 license_override: tuple[str, str] | None, user_agent: str,
                 max_records: int | None = None,
                 deadline: float | None = None,
                 record_removals: bool = False,
                 seen_ids: set[str] | None = None,
                 checkpoint=None,
                 newest_first: bool = False):
        self.base_url = base_url
        self.source = source
        self.set_spec = set_spec
        self.metadata_prefix = metadata_prefix
        self.rate_limit = rate_limit
        self.windowed = windowed
        self.max_window_records = max_window_records
        self.subject_filter = subject_filter
        self.fetch_timeout = fetch_timeout
        self.license_override = license_override
        self.user_agent = user_agent
        self.max_records = max_records
        self.deadline = deadline
        self.record_removals = record_removals
        # Split windows are walked oldest-first by default; newest-first
        # makes a full re-harvest that spans several budgeted runs deliver
        # recent changes in its first run rather than its last.
        self.newest_first = newest_first
        self.seen_ids: set[str] = seen_ids if seen_ids is not None else set()
        # Called after each finished window with the walk itself, so the
        # caller can persist `stack`/`gaps` while the walk is still running.
        self.checkpoint = checkpoint

        self.total = 0
        self.deleted = 0
        self.removed = 0
        self.pages = 0
        self.windows_done = 0
        self.aborted = False
        self.hit_cap = False
        self.interrupted = False
        # Windows that could not be harvested after day-atomic retries. Each
        # entry: {"window", "reason", "declared", "scanned"} — scanned is the
        # LAST attempt's count (attempts re-scan already-written records, so a
        # cumulative number would overstate coverage and mislead diagnosis).
        self.gaps: list[dict] = []
        self.chain_fail_reason: str | None = None
        self.seconds_gran = False
        self.stack: list[Window] = []

    # ── one resumption chain ────────────────────────────────────────────
    def _params_for(self, w_from: str | None,
                    w_until: str | None) -> dict[str, str]:
        p: dict[str, str] = {"verb": "ListRecords",
                             "metadataPrefix": self.metadata_prefix}
        if self.set_spec:
            p["set"] = self.set_spec
        if w_from:
            p["from"] = w_from
        if w_until:
            p["until"] = w_until
        return p

    def _remove(self, fh, rid: str, reason: str) -> None:
        if self.record_removals and rid:
            fh.write(json.dumps({"_removed": rid, "_reason": reason}) + "\n")
            self.removed += 1

    def _run_chain(self, fh, w_from: str | None, w_until: str | None,
                   force: bool = False):
        """One resumption chain over [w_from, w_until].

        Returns (scanned, complete_size, clean, oversize):
          scanned       — record elements seen (kept + dupes + filtered + deleted)
          complete_size — resumptionToken/@completeListSize from page 1, or None
          clean         — chain ended without error
          oversize      — windowed mode: page 1 declared more than
                          max_window_records, chain abandoned for bisection
                          (its page-1 records were NOT consumed)

        Raises DeadlineReached when the budget runs out before a request.

        force=True walks the chain regardless of the oversize probe, with a
        hard _FORCE_MAX_PAGES ceiling. In windowed mode chain failures no
        longer poison the harvest-level `aborted` flag — the window loop
        owns that decision (gap-and-continue semantics); non-windowed
        harvests keep the original abort-on-error behavior.
        """
        source = self.source
        token: str | None = None
        scanned = 0
        chain_pages = 0
        complete_size: int | None = None
        while True:
            if self.deadline is not None and time.monotonic() >= self.deadline:
                raise DeadlineReached()
            self.pages += 1
            chain_pages += 1
            if force and chain_pages > _FORCE_MAX_PAGES:
                self.chain_fail_reason = "force_walk_runaway"
                log.error("%s window [%s..%s]: forced walk exceeded %d pages "
                          "— giving up on this window", source, w_from,
                          w_until, _FORCE_MAX_PAGES)
                return scanned, complete_size, False, False
            if token:
                qs = urllib.parse.urlencode(
                    {"verb": "ListRecords", "resumptionToken": token})
            else:
                qs = urllib.parse.urlencode(self._params_for(
                    *_day_bounds(w_from, w_until, self.seconds_gran)))
            url = f"{self.base_url}?{qs}"
            try:
                raw = _fetch_with_retry(url, user_agent=self.user_agent,
                                        timeout=self.fetch_timeout,
                                        source=source, page=self.pages,
                                        deadline=self.deadline)
            except DeadlineReached:
                raise
            except Exception as e:                  # noqa: BLE001
                log.error("OAI fetch failed after %d retries (%s, page %d): %s",
                          _FETCH_RETRIES, source, self.pages, e)
                self.chain_fail_reason = "fetch_error"
                if not self.windowed:
                    self.aborted = True
                return scanned, complete_size, False, False

            try:
                root = ET.fromstring(raw)
            except ET.ParseError:
                # Some IRs (e.g. ex-ante.ch as of 2026-05-27) emit control
                # characters in dc:description that make the document not
                # well-formed under XML 1.0. Strip and retry once.
                cleaned = _STRIP_INVALID_XML.sub(b"", raw)
                try:
                    root = ET.fromstring(cleaned)
                    log.warning("OAI XML had invalid control chars "
                                "(%s, page %d) — stripped and retried",
                                source, self.pages)
                except ET.ParseError as e2:
                    log.error("OAI XML parse failed (%s, page %d): %s",
                              source, self.pages, e2)
                    self.chain_fail_reason = "xml_error"
                    if not self.windowed:
                        self.aborted = True
                    return scanned, complete_size, False, False

            err = root.find(f"{{{OAI_NS}}}error")
            if err is not None:
                code = err.get("code", "?")
                if code == "noRecordsMatch":
                    return scanned, 0, True, False   # empty window, fine
                log.error("OAI error (%s, page %d) code=%s msg=%s",
                          source, self.pages, code, (err.text or "").strip())
                self.chain_fail_reason = "oai_error_" + code
                if not self.windowed:
                    self.aborted = True
                return scanned, complete_size, False, False

            list_recs = root.find(f"{{{OAI_NS}}}ListRecords")
            if list_recs is None:
                log.warning("OAI: no ListRecords element (%s, page %d)",
                            source, self.pages)
                self.chain_fail_reason = "no_listrecords"
                if not self.windowed:
                    self.aborted = True
                return scanned, complete_size, False, False

            rt = list_recs.find(f"{{{OAI_NS}}}resumptionToken")
            if token is None and rt is not None:
                cls = rt.get("completeListSize")
                if cls and cls.isdigit():
                    complete_size = int(cls)
                # Oversize probe: don't walk a chain the server may truncate —
                # one request spent, records re-arrive via the sub-windows.
                # force=True (day-atomic fallback) walks it regardless.
                if (self.windowed and not force and complete_size
                        and complete_size > self.max_window_records
                        and (w_from or w_until)):
                    return 0, complete_size, True, True

            page_kept = 0
            page_filtered = 0
            for rec in list_recs.findall(f"{{{OAI_NS}}}record"):
                scanned += 1
                hdr = rec.find(f"{{{OAI_NS}}}header")
                if hdr is not None and (hdr.get("status") or "").lower() == "deleted":
                    self.deleted += 1
                    self._remove(fh, hdr.findtext(
                        f"{{{OAI_NS}}}identifier", default="").strip(),
                        "deleted")
                    continue
                d = _record_to_dict(rec, source)
                if d is None:
                    continue
                rid = d.get("source_record_id") or ""
                if rid and rid in self.seen_ids:
                    continue                        # window-boundary duplicate
                if self.subject_filter and not _record_matches_subject(
                        d, self.subject_filter):
                    page_filtered += 1
                    self._remove(fh, rid, "filtered")
                    continue
                if self.license_override and (
                    not d.get("license")
                    or not d["license"].upper().startswith("CC-")
                ):
                    d["license"] = self.license_override[0]
                    d["license_url"] = self.license_override[1]
                fh.write(json.dumps(d, ensure_ascii=False) + "\n")
                if rid:
                    self.seen_ids.add(rid)
                self.total += 1
                page_kept += 1
                if self.max_records and self.total >= self.max_records:
                    break

            log.info(
                "%s page %d [%s..%s]: %d records (running total: %d, "
                "deleted: %d, subject-filtered: %d)",
                source, self.pages, w_from or "*", w_until or "*",
                page_kept, self.total, self.deleted, page_filtered,
            )

            if self.max_records and self.total >= self.max_records:
                self.hit_cap = True
                return scanned, complete_size, True, False

            rt = list_recs.find(f"{{{OAI_NS}}}resumptionToken")
            if rt is None or not (rt.text or "").strip():
                return scanned, complete_size, True, False
            token = rt.text.strip()
            time.sleep(self.rate_limit)

    # ── the window loop ─────────────────────────────────────────────────
    def run(self, fh, stack: list[Window]) -> None:
        """Pop windows until the stack is empty, the record cap is hit, or
        the deadline passes (then `interrupted` is set and the in-flight
        window is back on `stack`)."""
        self.stack = stack
        if self.windowed:
            gran_timeout = self.fetch_timeout
            if self.deadline is not None:
                left = self.deadline - time.monotonic()
                if left <= 0:
                    self.interrupted = True
                    return
                gran_timeout = int(min(self.fetch_timeout, max(5.0, left)))
            self.seconds_gran = _identify_granularity(
                self.base_url, user_agent=self.user_agent,
                timeout=gran_timeout)
        source = self.source
        while self.stack and not self.hit_cap:
            window = self.stack.pop()
            try:
                self._window(fh, window)
            except DeadlineReached:
                self.stack.append(window)
                self.interrupted = True
                log.info("%s: time budget reached — %d window(s) left, "
                         "resuming at [%s..%s] next run", source,
                         len(self.stack), window[0] or "*", window[1] or "*")
                break
            if self.checkpoint is not None:
                self.checkpoint(self)
        # A windowed harvest where nothing at all came back but gaps exist is
        # a wholesale failure — keep the abort semantics for the installer.
        if (self.windowed and self.gaps and self.total == 0
                and not self.interrupted):
            self.aborted = True

    def _window(self, fh, window: Window) -> None:
        source = self.source
        w_from, w_until, depth, parent_size = window
        self.chain_fail_reason = None
        scanned, size, clean, oversize = self._run_chain(fh, w_from, w_until)
        self.windows_done += 1
        windowed = self.windowed
        truncated = (windowed and clean and not oversize
                     and size is not None and size > 0 and scanned < size)
        failed = windowed and not clean
        if not (oversize or truncated or failed):
            return
        if not windowed:
            return          # non-windowed: _run_chain set `aborted`

        if truncated:
            log.warning(
                "%s window [%s..%s]: chain ended at %d of %d declared "
                "records (silent server cap)",
                source, w_from, w_until, scanned, size)

        # Sub-day-lie short-circuit (UNIGE, probed 2026-08-23): a child
        # window reporting its PARENT's completeListSize means the
        # server accepted sub-day bounds but ignored the time-of-day
        # component — descending further just burns probes on identical
        # lies. Fall through to the day-atomic walk instead.
        subday_lie = (size is not None and parent_size is not None
                      and size == parent_size
                      and ("T" in (w_from or "") or "T" in (w_until or "")))
        split = None
        if not subday_lie and depth < _MAX_BISECT_DEPTH:
            split = _bisect_window(w_from or WINDOW_FLOOR,
                                   w_until or "2100-01-01",
                                   seconds_granularity=self.seconds_gran)
        if split is not None:
            a, mid, mid_next = split
            halves = [(mid_next, w_until, depth + 1, size),
                      (a, mid, depth + 1, size)]
            if self.newest_first:
                halves.reverse()
            self.stack.extend(halves)       # LIFO: the last one goes next
            return

        # Day-atomic fallback: walk the window WHOLE. Windowing manages
        # CHAIN LENGTH, not output correctness — subject_filter and
        # seen_ids own the output — so force-walking a window the
        # server mis-serves (out-of-range records, lying sizes) is
        # content-safe; the worst case is re-scanning records already
        # written. Bounded attempts; a window that still fails becomes
        # a recorded gap and the SOURCE CONTINUES — one bad day must
        # never veto a whole repository again (2026-08-23 postmortem).
        gap_scanned = scanned
        gap_reason: str | None = None
        for _attempt in range(_DAY_ATOMIC_ATTEMPTS):
            if self.hit_cap:
                gap_reason = None          # cap is an exit, not a failure
                break
            self.chain_fail_reason = None
            f_scanned, f_size, f_clean, _ = self._run_chain(
                fh, w_from, w_until, force=True)
            gap_scanned = f_scanned
            if f_clean:
                if f_size and f_size > 0 and f_scanned < f_size:
                    gap_reason = "residual_truncation"
                    size = f_size
                else:
                    gap_reason = None
                    log.info("%s window [%s..%s]: day-atomic walk "
                             "recovered %d records", source, w_from,
                             w_until, f_scanned)
                break
            gap_reason = self.chain_fail_reason or "force_walk_failed"
        if gap_reason is not None:
            self.gaps.append({
                "window": [w_from, w_until],
                "reason": gap_reason,
                "declared": size,
                "scanned": gap_scanned,
            })
            log.error("%s window [%s..%s]: GAP recorded (%s, %s of %s) "
                      "— continuing with remaining windows",
                      source, w_from, w_until, gap_reason,
                      gap_scanned, size)


def harvest(
    base_url: str,
    source: str,
    *,
    set_spec: str | None = None,
    metadata_prefix: str = "oai_dc",
    from_date: str | None = None,
    until_date: str | None = None,
    output_dir: Path = DEFAULT_OUT,
    rate_limit: float = 1.0,
    max_records: int | None = None,
    windowed: bool = False,
    max_window_records: int = MAX_WINDOW_RECORDS,
    subject_filter: list[str] | None = None,
    fetch_timeout: int = 180,
    # If set, applied to records where dc:rights does NOT contain a CC URL
    # (i.e. the per-record license parse falls back to a copyright string).
    # Used for sources whose editorial license is documented externally
    # (e.g. ex-ante.ch publishes CC-BY-NC-ND on the journal homepage but
    # only emits a per-author copyright line in OAI dc:rights).
    license_override: tuple[str, str] | None = None,
    user_agent: str = "OpenCaseLaw-scholarship/0.1 (+https://opencaselaw.ch)",
) -> dict:
    """Stream OAI-PMH ListRecords into a JSONL file (one full, unbudgeted
    pass that replaces the shard). The weekly pipeline uses
    `scrapers.scholarship.incremental.harvest_source` instead, which walks
    the same windows incrementally, under a deadline, and resumably.

    Returns a summary dict.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"{source}.jsonl"
    tmp_path = output_dir / f"{source}.jsonl.tmp"
    started = time.time()

    walk = _Walk(
        base_url, source, set_spec=set_spec, metadata_prefix=metadata_prefix,
        rate_limit=rate_limit, windowed=windowed,
        max_window_records=max_window_records, subject_filter=subject_filter,
        fetch_timeout=fetch_timeout, license_override=license_override,
        user_agent=user_agent, max_records=max_records)
    with tmp_path.open("w", encoding="utf-8") as fh:
        walk.run(fh, _window_plan(windowed, from_date, until_date))
    total, aborted, gaps = walk.total, walk.aborted, walk.gaps

    # Atomic install — never let a broken harvest truncate last week's data
    # (the old code opened the real file with mode "w" before the first
    # request, so any mid-harvest death clobbered a good file).
    prev_count = 0
    if out_path.exists():
        try:
            with out_path.open("r", encoding="utf-8") as prev:
                prev_count = sum(1 for _ in prev)
        except OSError:
            prev_count = 0
    replaced = True
    # Install protection now also covers gapped harvests: a first full run
    # that gapped most days could otherwise replace a good file with a
    # smaller one and look like success (advisor review, 2026-08-24).
    if (aborted or gaps) and total <= prev_count:
        keep_as = output_dir / f"{source}.jsonl.aborted"
        os.replace(tmp_path, keep_as)
        replaced = False
        log.error("%s harvest %s with %d records (existing file has %d) "
                  "— keeping existing file; partial saved to %s",
                  source, "ABORTED" if aborted else "GAPPED",
                  total, prev_count, keep_as.name)
    else:
        os.replace(tmp_path, out_path)
        if aborted or gaps:
            log.warning("%s harvest %s but yielded %d > %d existing "
                        "records — installing the larger file",
                        source, "aborted" if aborted else
                        f"has {len(gaps)} gap(s)", total, prev_count)

    elapsed = time.time() - started
    summary = {
        "source": source,
        "base_url": base_url,
        "set": set_spec,
        "metadata_prefix": metadata_prefix,
        "pages": walk.pages,
        "total_records": total,
        "deleted_records": walk.deleted,
        "windows": walk.windows_done,
        "aborted": aborted,
        "gaps": gaps,
        "replaced": replaced,
        "elapsed_seconds": round(elapsed, 1),
        "output_path": str(out_path),
    }
    log.info("Done %s: %s", source, summary)
    return summary


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--source", required=True,
                   help="Short source name (used as JSONL filename)")
    p.add_argument("--base-url", required=True, help="OAI-PMH base URL")
    p.add_argument("--set", dest="set_spec", default=None, help="OAI setSpec to filter")
    p.add_argument("--metadata-prefix", default="oai_dc")
    p.add_argument("--from-date", default=None, help="OAI 'from' (YYYY-MM-DD)")
    p.add_argument("--until-date", default=None, help="OAI 'until' (YYYY-MM-DD)")
    p.add_argument("--output-dir", type=Path, default=DEFAULT_OUT)
    p.add_argument("--rate-limit", type=float, default=1.0,
                   help="Sleep between resumption pages (s)")
    p.add_argument("--max-records", type=int, default=None,
                   help="Stop after N records (for smoke tests)")
    args = p.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    summary = harvest(
        args.base_url,
        args.source,
        set_spec=args.set_spec,
        metadata_prefix=args.metadata_prefix,
        from_date=args.from_date,
        until_date=args.until_date,
        output_dir=args.output_dir,
        rate_limit=args.rate_limit,
        max_records=args.max_records,
    )
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
