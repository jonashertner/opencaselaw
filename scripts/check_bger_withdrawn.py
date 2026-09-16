#!/usr/bin/env python3
"""
check_bger_withdrawn.py — periodic "withdrawn BGer decision" check.

The Federal Supreme Court occasionally withdraws a decision it has already
published on search.bger.ch: the document URL then answers a normal HTTP 200
whose body carries an embedded "Dokument nicht gefunden / Dieser AZA-Entscheid
ist in elektronischer Form nicht verfügbar" document, and the docket drops out
of the AZA search index. We keep serving our copy. 6B_499/2026 (judgment of
27.08.2026, FR) was listed on the Neuheiten page of 11.09.2026, scraped the
same morning, withdrawn by 16.09.2026 and still withdrawn on 17.09.2026.

Withdrawals are not always permanent: 7B_723/2026 (listed 31.07, relisted
05.08) and 4F_14/2026 (listed 10.08, relisted 17.08) came back within a week.
So the check waits out a grace period before it calls anything a candidate.

What it does, once a day:

  1. Candidates are the BGer rows scraped in the last --window-days (default
     21), read from the tail of output/decisions/bger.jsonl the way
     bger_poller.py does (bounded byte tail, newest rows last). Production
     decisions.db is never opened — CLAUDE.md invariant 9. Rows younger than
     --min-age-hours are skipped: on a batch day the document service answers
     "nicht gefunden" for 30-90 min after the Neuheiten listing while the
     scraper already holds the text from the relevancy mirror.
  2. Each due row is re-fetched at its search.bger.ch document URL (built from
     decision_date + docket, the authoritative host — relevancy.bger.ch is a
     lagging mirror) through the real BgerScraper request path, so the
     residential SOCKS tunnel, Incapsula handling, PoW and the CA bundle all
     apply. The page is classified: not_found / present / service_error /
     blocked / error / unknown.
  3. A ledger (logs/bger_withdrawal_state.json) records per decision the first
     and last not_found observation, the distinct days on which it was seen,
     the last present observation and when it was last checked. A present
     observation after a not_found clears the streak (a relist). Transient
     classes never advance last_checked, so the row is retried next run.
  4. A row is a REMOVAL CANDIDATE when its not_found streak is at least
     --grace-days old (default 10), was observed on at least two distinct days
     and no present observation came in between. For matured candidates only,
     the AZA docket search is fetched once as a second witness: the docket
     absent from page 1 -> "withdrawn"; the docket present under a different
     decision date -> "replaced" (re-sync, not removal; the dated id the
     scraper would give the relist is printed).
  5. The candidate list is written to logs/bger_withdrawal_candidates.json,
     printed, and ONE ntfy message goes out per newly matured candidate set
     (NTFY_TOPIC from /opt/caselaw/ops.env, legacy topic as fallback). A relist
     of an already alerted candidate posts one informational line.

Nothing is ever deleted. Removal, de-listing or re-sync remain a maintainer
decision under docs/governance-and-removal-policy.md; the runbook is
runbooks/bger_withdrawn_decisions.md.

Usage:
    python3 scripts/check_bger_withdrawn.py                     # daily run
    python3 scripts/check_bger_withdrawn.py --dry-run           # fetch, classify, write nothing
    python3 scripts/check_bger_withdrawn.py --decision-id bger_6B_499_2026   # one id, always dry
    python3 scripts/check_bger_withdrawn.py --docket 6B_499/2026 --decision-date 2026-08-27
    python3 scripts/check_bger_withdrawn.py --no-ntfy --fail-on-candidates   # by hand / make

Offline tests: tests/test_check_bger_withdrawn.py (golden pages captured
2026-09-17 in tests/fixtures/bger_aza_*).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import quote, unquote

REPO = Path(__file__).resolve().parents[1]

# ── ntfy push channel ─────────────────────────────────────────────
# One operator topic via NTFY_TOPIC (set in /opt/caselaw/ops.env through the
# unit's drop-in); the scrapers topic stays as the legacy fallback, like
# check_scraper_freshness.py. Computed at import so the reload-based
# tests/test_ntfy_topic_unification.py pattern applies.
NTFY_URL = (
    os.environ.get("NTFY_URL", "https://ntfy.sh").rstrip("/")
    + "/"
    + os.environ.get("NTFY_TOPIC", "opencaselaw-scrapers")
)

DEFAULT_JSONL = REPO / "output" / "decisions" / "bger.jsonl"
DEFAULT_LEDGER = REPO / "logs" / "bger_withdrawal_state.json"
DEFAULT_REPORT = REPO / "logs" / "bger_withdrawal_candidates.json"
DEFAULT_STATE_DIR = REPO / "state"
SERVED_URL = "https://mcp.opencaselaw.ch/entscheid/{decision_id}"

SEARCH_HOST = "https://search.bger.ch"
AZA_INDEX = SEARCH_HOST + "/ext/eurospider/live/de/php/aza/http/index.php"

# Defaults (all CLI-overridable)
WINDOW_DAYS = 21
GRACE_DAYS = 10
MIN_AGE_HOURS = 12
RECHECK_DAYS = 3
MIN_NOT_FOUND_DAYS = 2
MAX_REQUESTS = 300
TAIL_MB = 32
ABORT_AFTER_ERRORS = 3      # consecutive fetch errors before any success → tunnel dead
MAX_TRANSIENT_RETRIES = 3   # transient answers in a row before the slow cadence
LEDGER_VERSION = 1

# Page classes
NOT_FOUND = "not_found"
PRESENT = "present"
SERVICE_ERROR = "service_error"
BLOCKED = "blocked"
ERROR = "error"
UNKNOWN = "unknown"
TRANSIENT = frozenset({SERVICE_ERROR, BLOCKED, ERROR, UNKNOWN})

# Witness verdicts (AZA docket search, page 1)
WITNESS_WITHDRAWN = "withdrawn"     # docket absent from the index
WITNESS_REPLACED = "replaced"       # docket present under another decision date
WITNESS_LISTED = "listed"           # docket present under the same date — inconclusive
WITNESS_UNKNOWN = "unknown"         # page unusable (blocked / error)

# ASCII-only markers: the page is served as text/html;charset=iso-8859-1 and
# the umlaut in "verfügbar" survives neither a utf-8 decode with replacement
# nor a byte-level grep, so nothing below depends on it. The markers sit in a
# nested <HTML> document inside the site chrome. "Dokument nicht gefunden"
# alone is too generic (a judgment can say that a document was not found in
# the file), so the phrase from the nested body or the nested <TITLE> is
# required.
NOT_FOUND_MARKERS = ("in elektronischer Form nicht verf",)
_NOT_FOUND_TITLE_RE = re.compile(r"<title>\s*Dokument nicht gefunden\s*</title>", re.I)
# Same string scrapers.bger.DOCUMENT_SERVICE_ERROR keys on (kept literal here so
# the module imports without the scraper stack).
DOCUMENT_SERVICE_ERROR = "Document Dienstes ist fehlgeschlagen"
# incapsula_bypass.IncapsulaCookieManager.is_incapsula_blocked, inlined.
INCAPSULA_MARKERS = ("_Incapsula_Resource", "Incapsula", "robots")

_DOCID_RE = re.compile(r"aza://(\d{2})-(\d{2})-(\d{4})-([0-9A-Za-z_]+)-(\d{4})")


# ═══════════════════════════════════════════════════════════════════════════
# URLs and identifiers
# ═══════════════════════════════════════════════════════════════════════════

def docket_stem(docket: str) -> str:
    """'6B_499/2026' -> '6B_499-2026' (the form inside aza:// document ids)."""
    return docket.strip().replace("/", "-")


def stem_to_docket(stem: str, year: str) -> str:
    return f"{stem}/{year}"


def aza_docid(decision_date: date, docket: str) -> str:
    return f"aza://{decision_date:%d-%m-%Y}-{docket_stem(docket)}"


def document_url(decision_date: date, docket: str) -> str:
    """The authoritative document page on search.bger.ch (the scraper's own
    Neuheiten stub URL shape, minus the empty zoom= parameter)."""
    return (f"{AZA_INDEX}?lang=de&type=show_document"
            f"&highlight_docid={quote(aza_docid(decision_date, docket), safe='')}")


def search_url(docket: str) -> str:
    """AZA simple query for the docket string. Full-text: the hits are mostly
    decisions CITING the docket, so callers look at highlight_docid, never at
    the hit count."""
    return (f"{AZA_INDEX}?lang=de&type=simple_query"
            f"&query_words={quote(docket, safe='')}&top_subcollection_aza=all")


def dated_decision_id(docket: str, decision_date: date) -> str:
    """The id scrapers.bger gives a second ruling under a held docket."""
    base = "bger_" + re.sub(r"[^0-9A-Za-z]+", "_", docket.strip()).strip("_")
    return f"{base}-D{decision_date:%Y%m%d}"


# ═══════════════════════════════════════════════════════════════════════════
# Page classification (pure)
# ═══════════════════════════════════════════════════════════════════════════

def _content_text(html: str) -> str | None:
    """Text of div#highlight_content > div.content, the scraper's primary
    selector; None when the container is absent."""
    from bs4 import BeautifulSoup  # local: keeps import cheap for the reload test
    soup = BeautifulSoup(html, "html.parser")
    content = soup.select_one("div#highlight_content > div.content")
    if content is None:
        return None
    return " ".join(content.get_text(separator=" ").split())


def classify_document_page(html: str, docket: str, final_url: str = "") -> str:
    """Classify a fetched document page.

    Order matters: the not-found page carries the full site chrome (hundreds
    of characters of "Wichtiger Hinweis..." in the content area), so a
    non-empty container is not evidence of a decision. `present` requires the
    docket string inside the decision content box.
    """
    text = html or ""
    if any(m in text for m in NOT_FOUND_MARKERS) or _NOT_FOUND_TITLE_RE.search(text):
        return NOT_FOUND
    if DOCUMENT_SERVICE_ERROR in text:
        return SERVICE_ERROR
    if "pow.php" in (final_url or ""):
        return BLOCKED
    if len(text) < 500 and any(m in text for m in INCAPSULA_MARKERS):
        return BLOCKED
    if len(text) < 200:
        return BLOCKED
    content = _content_text(text)
    if content and docket and docket in content:
        return PRESENT
    return UNKNOWN


def parse_search_hits(html: str) -> list[tuple[date, str]]:
    """(decision_date, docket) for every highlight_docid on a search page, in
    rank order, duplicates removed."""
    out: list[tuple[date, str]] = []
    seen: set[str] = set()
    for m in re.finditer(r"highlight_docid=([^&\"'\s<>]+)", html or ""):
        docid = unquote(m.group(1))
        dm = _DOCID_RE.search(docid)
        if not dm:
            continue
        dd, mm, yyyy, stem, year = dm.groups()
        try:
            d = date(int(yyyy), int(mm), int(dd))
        except ValueError:
            continue
        key = f"{dm.group(0)}"
        if key in seen:
            continue
        seen.add(key)
        out.append((d, stem_to_docket(stem, year)))
    return out


def classify_search_page(html: str, docket: str, decision_date: date | None) -> dict:
    """Second witness for a matured candidate. Returns a dict with `verdict`
    and the dates under which the docket is listed."""
    text = html or ""
    hits = parse_search_hits(text)
    usable = bool(hits) or "Treffer" in text or "Urteile gefunden" in text
    if not usable:
        return {"verdict": WITNESS_UNKNOWN, "listed_dates": []}
    same = sorted({d for d, dk in hits if dk == docket})
    listed = [d.isoformat() for d in same]
    if not same:
        return {"verdict": WITNESS_WITHDRAWN, "listed_dates": listed}
    if decision_date is not None and decision_date in same:
        return {"verdict": WITNESS_LISTED, "listed_dates": listed}
    return {
        "verdict": WITNESS_REPLACED,
        "listed_dates": listed,
        "relist_ids": [dated_decision_id(docket, d) for d in same],
    }


# ═══════════════════════════════════════════════════════════════════════════
# Candidate rows from the JSONL tail
# ═══════════════════════════════════════════════════════════════════════════

def _parse_ts(value) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_date(value) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def read_jsonl_tail(path: Path, max_bytes: int) -> list[dict]:
    """Parsed rows from the last max_bytes of a JSONL file, in file order.
    The first (possibly partial) line is dropped when the read started
    mid-file. Mirrors bger_poller._recently_ingested_dockets."""
    if not path.exists():
        return []
    with open(path, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        seek_back = min(max_bytes, size)
        f.seek(size - seek_back, 0)
        chunk = f.read().decode("utf-8", errors="replace")
    if seek_back < size and "\n" in chunk:
        chunk = chunk.split("\n", 1)[1]
    rows: list[dict] = []
    for line in chunk.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        if isinstance(d, dict):
            rows.append(d)
    return rows


def row_to_candidate(row: dict) -> dict | None:
    """Normalise a bger.jsonl row to what the ledger needs; None if unusable."""
    did = row.get("decision_id")
    docket = row.get("docket_number")
    d = _parse_date(row.get("decision_date"))
    scraped = _parse_ts(row.get("scraped_at"))
    if not did or not docket or d is None or scraped is None:
        return None
    if row.get("court", "bger") != "bger":
        return None
    return {
        "decision_id": did,
        "docket_number": docket,
        "decision_date": d.isoformat(),
        "publication_date": (_parse_date(row.get("publication_date")) or d).isoformat()
        if row.get("publication_date") else None,
        "language": row.get("language"),
        "scraped_at": scraped.isoformat(),
        "url": document_url(d, docket),
    }


def recent_candidates(path: Path, *, now: datetime, window_days: int,
                      min_age_hours: float, max_bytes: int) -> tuple[list[dict], bool]:
    """Rows scraped within the window and older than min_age. The second
    value is True when the tail cap may have cut the window short (the
    oldest parsed row is itself inside the window)."""
    rows = read_jsonl_tail(path, max_bytes)
    since = now - timedelta(days=window_days)
    newest_allowed = now - timedelta(hours=min_age_hours)
    out: list[dict] = []
    oldest: datetime | None = None
    for row in rows:
        c = row_to_candidate(row)
        if c is None:
            continue
        scraped = _parse_ts(c["scraped_at"])
        if oldest is None or scraped < oldest:
            oldest = scraped
        if since <= scraped <= newest_allowed:
            out.append(c)
    truncated = bool(rows) and oldest is not None and oldest >= since \
        and path.exists() and path.stat().st_size > max_bytes
    return out, truncated


# ═══════════════════════════════════════════════════════════════════════════
# Ledger
# ═══════════════════════════════════════════════════════════════════════════

def load_ledger(path: Path) -> dict:
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("entries"), dict):
                return data
        except Exception as e:  # noqa: BLE001
            print(f"ledger unreadable, starting fresh: {e}", file=sys.stderr)
    return {"version": LEDGER_VERSION, "entries": {}}


def atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False, sort_keys=True),
                   encoding="utf-8")
    os.replace(tmp, path)


def _push_event(entry: dict, now: datetime, event: str, **extra) -> None:
    events = entry.setdefault("events", [])
    events.append({"at": now.isoformat(), "event": event, **extra})
    del events[:-20]


def new_entry(candidate: dict) -> dict:
    return {
        **candidate,
        "checks": 0,
        "last_checked": None,
        "last_status": None,
        "last_transient": None,
        "transient_streak": 0,
        "first_not_found": None,
        "last_not_found": None,
        "not_found_days": [],
        "last_present": None,
        "alerted_at": None,
        "relist_alerted_at": None,
        "witness": None,
        "events": [],
    }


def observe(entry: dict, status: str, now: datetime) -> None:
    """Apply one observation to a ledger entry."""
    entry["checks"] = int(entry.get("checks") or 0) + 1
    entry["last_status"] = status
    if status in TRANSIENT:
        # Retry next run: last_checked is deliberately not advanced.
        entry["last_transient"] = now.isoformat()
        entry["transient_streak"] = int(entry.get("transient_streak") or 0) + 1
        return
    entry["last_checked"] = now.isoformat()
    entry["transient_streak"] = 0
    if status == NOT_FOUND:
        if not entry.get("first_not_found"):
            entry["first_not_found"] = now.isoformat()
            # A new streak closes any earlier relist chapter, so a
            # withdraw -> relist -> withdraw sequence alerts again.
            entry["relisted_at"] = None
            entry["relist_alerted_at"] = None
            _push_event(entry, now, "not_found_first_seen")
        entry["last_not_found"] = now.isoformat()
        days = set(entry.get("not_found_days") or [])
        days.add(now.date().isoformat())
        entry["not_found_days"] = sorted(days)
    elif status == PRESENT:
        entry["last_present"] = now.isoformat()
        if entry.get("first_not_found"):
            _push_event(entry, now, "relisted",
                        first_not_found=entry["first_not_found"],
                        not_found_days=list(entry.get("not_found_days") or []))
            entry["relisted_at"] = now.isoformat()
            entry["first_not_found"] = None
            entry["last_not_found"] = None
            entry["not_found_days"] = []
            entry["witness"] = None


def is_candidate(entry: dict, now: datetime, grace_days: int,
                 min_days: int = MIN_NOT_FOUND_DAYS) -> bool:
    first = _parse_ts(entry.get("first_not_found"))
    if first is None or entry.get("last_status") != NOT_FOUND:
        return False
    if (now - first) < timedelta(days=grace_days):
        return False
    return len(entry.get("not_found_days") or []) >= min_days


def is_due(entry: dict, now: datetime, recheck_days: int) -> bool:
    """Not-found rows daily, healthy rows every recheck_days, new rows now.
    A few hours of slack absorb timer jitter."""
    last = _parse_ts(entry.get("last_checked"))
    if entry.get("last_status") in TRANSIENT:
        # Retry next run — but a row that answers transiently three runs in
        # a row (layout change, odd docket rendering) drops to the healthy
        # cadence instead of eating the request cap every day.
        if int(entry.get("transient_streak") or 0) < MAX_TRANSIENT_RETRIES:
            return True
        last_try = _parse_ts(entry.get("last_transient"))
        return last_try is None or \
            (now - last_try) >= timedelta(days=recheck_days) - timedelta(hours=4)
    if last is None:
        return True
    if entry.get("last_status") == NOT_FOUND:
        return (now - last) >= timedelta(hours=20)
    return (now - last) >= timedelta(days=recheck_days) - timedelta(hours=4)


def prune(ledger: dict, now: datetime, window_days: int, grace_days: int) -> int:
    """Drop entries that left the window and are not on a not-found streak.
    Streaks (and matured candidates) are kept until they resolve or are 90
    days past the window."""
    horizon = now - timedelta(days=window_days + grace_days + 7)
    hard = now - timedelta(days=window_days + 90)
    dropped = 0
    for did in list(ledger["entries"]):
        e = ledger["entries"][did]
        scraped = _parse_ts(e.get("scraped_at"))
        if scraped is None or scraped < hard:
            del ledger["entries"][did]
            dropped += 1
        elif scraped < horizon and not e.get("first_not_found"):
            del ledger["entries"][did]
            dropped += 1
    return dropped


# ═══════════════════════════════════════════════════════════════════════════
# Fetching (real scraper path; injectable for tests)
# ═══════════════════════════════════════════════════════════════════════════

Fetcher = Callable[[str], tuple[int, str, str]]   # url -> (status, text, final_url)


class ScraperFetcher:
    """GET through scrapers.bger.BgerScraper: proxy (BGER_PROXY / SCRAPER_PROXY),
    Incapsula cookies, PoW mining, retries and the 2 s rate limit come from the
    scraper itself. Imported lazily so the module stays importable without
    the scraper stack."""

    def __init__(self, state_dir: Path = DEFAULT_STATE_DIR):
        if str(REPO) not in sys.path:
            sys.path.insert(0, str(REPO))
        from scrapers.bger import BgerScraper  # noqa: WPS433
        self.scraper = BgerScraper(state_dir=Path(state_dir))
        self.scraper._init_session()

    def __call__(self, url: str) -> tuple[int, str, str]:
        resp = self.scraper._get_with_pow(url)
        return resp.status_code, resp.text or "", resp.url or url


def fetch_and_classify(fetch: Fetcher, entry: dict) -> tuple[str, str | None]:
    """Returns (status, error_message)."""
    try:
        status_code, text, final_url = fetch(entry["url"])
    except Exception as e:  # noqa: BLE001
        return ERROR, f"{type(e).__name__}: {e}"[:300]
    if status_code and status_code >= 400:
        return ERROR, f"HTTP {status_code}"
    return classify_document_page(text, entry["docket_number"], final_url), None


def run_witness(fetch: Fetcher, entry: dict) -> dict:
    try:
        status_code, text, _ = fetch(search_url(entry["docket_number"]))
    except Exception as e:  # noqa: BLE001
        return {"verdict": WITNESS_UNKNOWN, "listed_dates": [],
                "error": f"{type(e).__name__}: {e}"[:300]}
    if status_code and status_code >= 400:
        return {"verdict": WITNESS_UNKNOWN, "listed_dates": [], "error": f"HTTP {status_code}"}
    return classify_search_page(text, entry["docket_number"],
                                _parse_date(entry.get("decision_date")))


# ═══════════════════════════════════════════════════════════════════════════
# ntfy
# ═══════════════════════════════════════════════════════════════════════════

def send_ntfy(title: str, message: str, priority: str = "default",
              tags: str = "warning", url: str | None = None) -> bool:
    """urllib POST with Title / Priority / Tags headers (the
    dispatch_health_alerts.send mechanism). Tests replace this function."""
    req = urllib.request.Request(
        url or NTFY_URL, data=message.encode("utf-8", errors="replace"),
        headers={"Title": title.encode("ascii", errors="replace").decode("ascii"),
                 "Priority": priority, "Tags": tags},
    )
    try:
        urllib.request.urlopen(req, timeout=10).read()
        return True
    except Exception as e:  # noqa: BLE001
        print(f"ntfy send failed: {e}", file=sys.stderr)
        return False


def _fmt_ch(iso: str | None) -> str:
    d = _parse_date(iso)
    return f"{d:%d.%m.%Y}" if d else "?"


def candidate_line(e: dict) -> str:
    w = e.get("witness") or {}
    verdict = w.get("verdict") or "not checked"
    extra = ""
    if verdict == WITNESS_REPLACED:
        extra = (f" under {', '.join(w.get('listed_dates') or [])}"
                 f" (relist id {', '.join(w.get('relist_ids') or [])})")
    return (f"{e['docket_number']} ({_fmt_ch(e.get('decision_date'))}, "
            f"listed {_fmt_ch(e.get('publication_date'))}, "
            f"scraped {_fmt_ch(e.get('scraped_at'))}): "
            f"'Dokument nicht gefunden' since {_fmt_ch(e.get('first_not_found'))} "
            f"on {len(e.get('not_found_days') or [])} days; AZA index: {verdict}{extra}. "
            f"Served at {SERVED_URL.format(decision_id=e['decision_id'])}")


def notify_candidates(new: list[dict], now: datetime, sender=send_ntfy) -> bool:
    if not new:
        return False
    title = f"BGer withdrawn-decision check: {len(new)} new removal candidate" + \
        ("s" if len(new) != 1 else "")
    body = "\n".join(candidate_line(e) for e in new)
    body += ("\nNothing was deleted. Removal / de-listing / re-sync is a maintainer "
             "decision: runbooks/bger_withdrawn_decisions.md, "
             "docs/governance-and-removal-policy.md.")
    ok = sender(title, body, priority="high", tags="warning,scales")
    if ok:
        for e in new:
            e["alerted_at"] = now.isoformat()
    return ok


def notify_relists(relisted: list[dict], now: datetime, sender=send_ntfy) -> bool:
    if not relisted:
        return False
    lines = [f"{e['docket_number']} ({_fmt_ch(e.get('decision_date'))}) is back on "
             f"search.bger.ch; the removal candidate is withdrawn, nothing to do "
             f"unless the text changed (re-sync per policy)." for e in relisted]
    ok = sender("BGer withdrawn-decision check: relisted", "\n".join(lines),
                priority="default", tags="white_check_mark")
    if ok:
        for e in relisted:
            e["relist_alerted_at"] = now.isoformat()
            e["alerted_at"] = None          # a later withdrawal alerts afresh
    return ok


# ═══════════════════════════════════════════════════════════════════════════
# Run
# ═══════════════════════════════════════════════════════════════════════════

def _entry_summary(e: dict) -> dict:
    keys = ("decision_id", "docket_number", "decision_date", "publication_date",
            "language", "scraped_at", "url", "checks", "last_checked", "last_status",
            "first_not_found", "last_not_found", "not_found_days", "last_present",
            "alerted_at", "witness", "relisted_at")
    out = {k: e.get(k) for k in keys}
    out["served_url"] = SERVED_URL.format(decision_id=e["decision_id"])
    return out


def build_report(ledger: dict, now: datetime, args) -> dict:
    entries = ledger["entries"].values()
    candidates = [e for e in entries if is_candidate(e, now, args.grace_days)]
    watch = [e for e in entries
             if e.get("first_not_found") and e.get("last_status") == NOT_FOUND
             and not is_candidate(e, now, args.grace_days)]
    return {
        "generated_at": now.isoformat(),
        "window_days": args.window_days,
        "grace_days": args.grace_days,
        "policy": "docs/governance-and-removal-policy.md",
        "runbook": "runbooks/bger_withdrawn_decisions.md",
        "counts": {
            "tracked": len(ledger["entries"]),
            "candidates": len(candidates),
            "watch": len(watch),
        },
        "candidates": sorted((_entry_summary(e) for e in candidates),
                             key=lambda x: x["first_not_found"] or ""),
        "watch": sorted((_entry_summary(e) for e in watch),
                        key=lambda x: x["first_not_found"] or ""),
    }


def print_table(rows: list[dict], now: datetime, grace_days: int) -> None:
    if not rows:
        return
    print(f"{'docket':<14} {'decided':<11} {'listed':<11} {'status':<14} "
          f"{'nf-since':<11} {'nf-days':>7}  {'witness':<10} verdict")
    for e in rows:
        w = (e.get("witness") or {}).get("verdict") or "-"
        verdict = "CANDIDATE" if is_candidate(e, now, grace_days) else (
            "watch" if e.get("first_not_found") else "")
        print(f"{e['docket_number']:<14} {e.get('decision_date') or '?':<11} "
              f"{e.get('publication_date') or '-':<11} {e.get('last_status') or '-':<14} "
              f"{(e.get('first_not_found') or '-')[:10]:<11} "
              f"{len(e.get('not_found_days') or []):>7}  {w:<10} {verdict}")


def run(args, *, now: datetime | None = None, fetch: Fetcher | None = None,
        sender=send_ntfy) -> dict:
    """Execute one check. Returns the report dict (also written unless dry)."""
    now = now or datetime.now(timezone.utc)
    jsonl = Path(args.jsonl)
    ledger_path = Path(args.ledger)
    report_path = Path(args.report)
    adhoc = bool(args.decision_id or args.docket)
    dry = bool(args.dry_run or adhoc)

    ledger = load_ledger(ledger_path)
    entries: dict[str, dict] = ledger["entries"]

    # ── candidate rows ────────────────────────────────────────────
    if adhoc:
        rows: list[dict] = []
        if args.docket:
            d = _parse_date(args.decision_date)
            if d is None:
                raise SystemExit("--docket needs --decision-date YYYY-MM-DD")
            did = "bger_" + re.sub(r"[^0-9A-Za-z]+", "_", args.docket).strip("_")
            rows.append({"decision_id": did, "docket_number": args.docket,
                         "decision_date": d.isoformat(), "publication_date": None,
                         "language": None, "scraped_at": now.isoformat(),
                         "url": document_url(d, args.docket)})
        if args.decision_id:
            wanted = set(args.decision_id)
            tail = read_jsonl_tail(jsonl, int(args.tail_mb * 1024 * 1024))
            found = {c["decision_id"]: c for c in (row_to_candidate(r) for r in tail)
                     if c and c["decision_id"] in wanted}
            for did in args.decision_id:
                if did in found:
                    rows.append(found[did])
                elif did in entries:
                    rows.append({k: entries[did][k] for k in (
                        "decision_id", "docket_number", "decision_date",
                        "publication_date", "language", "scraped_at", "url")})
                else:
                    print(f"{did}: not in the last {args.tail_mb} MB of {jsonl} nor in "
                          f"the ledger — pass --docket/--decision-date instead",
                          file=sys.stderr)
        truncated = False
        work_entries = [new_entry(r) for r in rows]      # never touches the ledger
        # ad-hoc mode: report the current classification, no cadence, no maturity
    else:
        rows, truncated = recent_candidates(
            jsonl, now=now, window_days=args.window_days,
            min_age_hours=args.min_age_hours, max_bytes=int(args.tail_mb * 1024 * 1024))
        if truncated:
            print(f"WARN: the {args.tail_mb} MB tail of {jsonl} does not reach back "
                  f"{args.window_days} days — raise --tail-mb", file=sys.stderr)
        for r in rows:
            if r["decision_id"] not in entries:
                entries[r["decision_id"]] = new_entry(r)
        pruned = prune(ledger, now, args.window_days, args.grace_days)
        due = [e for e in entries.values() if is_due(e, now, args.recheck_days)]
        # Least recently ATTEMPTED first (a transient answer counts as an
        # attempt) so the request cap cannot starve the same ids every day;
        # rows never attempted go first.
        due.sort(key=lambda e: (max(filter(None, (e.get("last_checked"),
                                                  e.get("last_transient"))), default=""),
                                e.get("scraped_at") or ""))
        work_entries = due
        print(f"{len(rows)} rows in window, {len(entries)} tracked, {len(due)} due, "
              f"{pruned} pruned")

    # ── fetch + classify ──────────────────────────────────────────
    if fetch is None and work_entries:
        fetch = ScraperFetcher(Path(args.state_dir))
    requests_made = 0
    consecutive_errors = 0
    successes = 0
    counts: dict[str, int] = {}
    aborted = False
    for e in work_entries:
        if requests_made >= args.max_requests:
            print(f"request cap {args.max_requests} reached; "
                  f"{len(work_entries) - requests_made} rows wait for the next run")
            break
        status, err = fetch_and_classify(fetch, e)
        requests_made += 1
        counts[status] = counts.get(status, 0) + 1
        if status == ERROR:
            consecutive_errors += 1
            if successes == 0 and consecutive_errors >= args.abort_after:
                print(f"ABORT: first {consecutive_errors} fetches failed "
                      f"({err}) — tunnel or host down; ledger untouched")
                aborted = True
                break
        else:
            consecutive_errors = 0
            successes += 1
        if adhoc:
            e["last_status"] = status
            e["checks"] = 1
            print(f"{e['docket_number']:<14} {e['decision_date']:<11} {status:<14} "
                  f"{err or ''}  {e['url']}")
            if status == NOT_FOUND and not args.no_witness \
                    and requests_made < args.max_requests:
                e["witness"] = run_witness(fetch, e)
                requests_made += 1
                print(f"{'':<14} witness: {e['witness']}")
            continue
        observe(e, status, now)
        if err:
            e["last_error"] = err

    if aborted:
        return {"aborted": True, "generated_at": now.isoformat(),
                "counts": counts, "candidates": [], "watch": []}

    # ── second witness for matured candidates ─────────────────────
    if not adhoc and not args.no_witness:
        for e in entries.values():
            w = e.get("witness")
            if is_candidate(e, now, args.grace_days) \
                    and (w is None or w.get("verdict") == WITNESS_UNKNOWN) \
                    and requests_made < args.max_requests:
                e["witness"] = run_witness(fetch, e)
                requests_made += 1

    # ── report, ledger, alerts ────────────────────────────────────
    if adhoc:
        report = {"generated_at": now.isoformat(), "adhoc": True,
                  "rows": [_entry_summary(e) for e in work_entries], "counts": counts}
        return report

    report = build_report(ledger, now, args)
    report["counts"].update({"requests": requests_made, **{f"status_{k}": v
                                                            for k, v in counts.items()}})
    print_table([e for e in entries.values() if e.get("first_not_found")], now,
                args.grace_days)
    print(f"requests {requests_made}; statuses {counts}; candidates "
          f"{report['counts']['candidates']}; watch {report['counts']['watch']}")

    if dry:
        print("dry run: ledger, report and ntfy untouched")
        return report

    new_candidates = [e for e in entries.values()
                      if is_candidate(e, now, args.grace_days) and not e.get("alerted_at")]
    relisted = [e for e in entries.values()
                if e.get("relisted_at") and e.get("alerted_at")
                and not e.get("relist_alerted_at")]
    if args.no_ntfy:
        if new_candidates or relisted:
            print(f"ntfy suppressed (--no-ntfy): {len(new_candidates)} new candidate(s), "
                  f"{len(relisted)} relist(s)")
    else:
        if notify_candidates(new_candidates, now, sender):
            print(f"ntfy: {len(new_candidates)} new candidate(s) posted")
        if notify_relists(relisted, now, sender):
            print(f"ntfy: {len(relisted)} relist(s) posted")

    ledger["updated_at"] = now.isoformat()
    atomic_write_json(ledger_path, ledger)
    atomic_write_json(report_path, report)
    return report


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--jsonl", default=str(DEFAULT_JSONL))
    ap.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    ap.add_argument("--report", default=str(DEFAULT_REPORT))
    ap.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR),
                    help="scraper state dir (Incapsula cookie cache, known ids)")
    ap.add_argument("--window-days", type=int, default=WINDOW_DAYS)
    ap.add_argument("--grace-days", type=int, default=GRACE_DAYS)
    ap.add_argument("--min-age-hours", type=float, default=MIN_AGE_HOURS)
    ap.add_argument("--recheck-days", type=int, default=RECHECK_DAYS)
    ap.add_argument("--max-requests", type=int, default=MAX_REQUESTS)
    ap.add_argument("--abort-after", type=int, default=ABORT_AFTER_ERRORS,
                    help="consecutive fetch errors before any success → abort")
    ap.add_argument("--tail-mb", type=float, default=TAIL_MB)
    ap.add_argument("--no-witness", action="store_true",
                    help="skip the AZA docket-search second witness")
    ap.add_argument("--no-ntfy", action="store_true")
    ap.add_argument("--dry-run", action="store_true",
                    help="fetch and classify, write nothing, post nothing")
    ap.add_argument("--decision-id", action="append",
                    help="check one served id (repeatable); implies --dry-run")
    ap.add_argument("--docket", help="ad-hoc docket, with --decision-date; implies --dry-run")
    ap.add_argument("--decision-date")
    ap.add_argument("--fail-on-candidates", action="store_true",
                    help="exit 1 when removal candidates exist (for make / by hand)")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = run(args)
    if args.fail_on_candidates and report.get("candidates"):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
