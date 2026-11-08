#!/usr/bin/env python3
"""source_status.py — one derived status per source for the public /coverage/ page.

Why: "the scraper exited 0" is not health. ai_gerichte reported success with
new_count 0 for months while the court published five rulings; VD CDAP, BE BVD,
BE Steuerrekurs and the SG departments stopped growing in March 2026 when the
entscheidsuche feed was retired, and the public page showed nothing. This
module joins what the nightly already knows into one status per court code:

  docs/sources/registry.json      curated FACTS: name, portal, licence, which
                                  scraper writes the code, retired-feed origin,
                                  ended series. Never a status.
  docs/coverage.json (step 5g)    rows per decision year (rate, cadence)
  docs/stats.json    (step 5a)    newest decision date, newest row scraped_at
  logs/scraper_health*.json       the latest run of every scraper
  docs/intake_expected_stale.json lag_days + by-design silences
  run_scraper.py / run_all_scrapers.py / systemd/*.timer
                                  which scraper runs when (read with ast/regex,
                                  never imported, so this stays side-effect free)
  systemctl show (VPS only)       last successful run of the own-timer units
  practice.db / materialien.db / consultations.db / legal_scholarship.db
                                  counts and year spans of the non-decision
                                  collections (tiny tables, mode=ro&immutable=1;
                                  never decisions.db)

and writes docs/sources/status.json. Every status is recomputed nightly, so the
page follows the scraper fixes as they land instead of a hand-written table.

Status per court code (exactly one):
  fresh       live intake, and new decisions arrive at the expected cadence
  stale       live intake, but nothing new for longer than the cadence allows
              (or the year-rate detector check_intake_rates.py says STALE)
  failing     the latest run of a scraper writing this code failed
  quiet       live intake, not judged by cadence: the source has stopped
              publishing by design (intake_expected_stale.json) or publishes
              in annual batches whose window has not opened (lag_days)
  no_intake   no scraper runs for this code (retired feed, not scheduled)
  superseded  frozen rows of an earlier feed; current rulings arrive under
              other codes, named in superseded_by
  closed      the court or series no longer exists

Pure functions + thin IO; tests feed dicts. Never raises out of main().
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sqlite3
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_intake_rates import evaluate as _rate_evaluate  # noqa: E402
from check_intake_rates import load_policy as _load_policy  # noqa: E402

SCHEMA = "source-status/v1"

# Cadence → staleness window. A source is stale when its newest decision is
# older than STALE_FACTOR typical gaps between decisions (plus the series'
# publication lag), AND no row at all was added for STALE_FACTOR gaps. The
# floors absorb ordinary publication delay and batch uploads (ZH Obergericht
# publishes 6-8 weeks after deciding; NW uploads a month at a time); the
# ceiling keeps a once-a-year publisher judgeable. Series the year-rate
# detector cannot judge yet (WAIT: annual batches such as WEKO, FINMA, LGVE)
# are 'quiet', not stale.
STALE_FACTOR = 4
MIN_WINDOW_NEWEST = 60
MIN_WINDOW_ADDED = 30
MAX_WINDOW = 548

STATUSES = ("failing", "stale", "no_intake", "fresh", "quiet", "superseded", "closed")

# systemd units whose last run we report, keyed to what they feed.
SCRAPE_UNITS = {
    "nightly": "opencaselaw-scrape",
    "federal": "opencaselaw-scrape-federal",
    "late": "opencaselaw-late-scrapers",
    "poller": "bger-poller",
}
POLLER_SCRAPER = {"bger-poller": "bger"}


# ─── small helpers ──────────────────────────────────────────────────────────

def _parse_date(s) -> date | None:
    if not s or not isinstance(s, str):
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def _days(a: date | None, b: date) -> int | None:
    return (b - a).days if a else None


def _clamp(x: float, lo: int, hi: int) -> int:
    return int(max(lo, min(hi, round(x))))


def _literal(path: Path, name: str):
    """A module-level literal (dict/set/list) by name, read with ast — the
    module is never imported, so no network or DB setup can run."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return None
    for node in tree.body:
        target = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
        elif isinstance(node, ast.AnnAssign):
            target = node.target
        if isinstance(target, ast.Name) and target.id == name and node.value is not None:
            try:
                return ast.literal_eval(node.value)
            except ValueError:
                return None
    return None


# ─── schedules (code + systemd files in the repo) ───────────────────────────

_DOW = r"(Mon|Tue|Wed|Thu|Fri|Sat|Sun)"


def parse_oncalendar(spec: str) -> dict | None:
    """Structured form of the OnCalendar expressions this repo uses.
    '*-*-* 01:00:00 UTC'              -> daily 01:00
    'Sat 21:30 UTC' / 'Mon *-*-* 13:00:00 UTC' -> weekly
    '*-*-02 04:00:00 UTC'             -> monthly, day 2
    'Mon..Fri *-*-* 05..16:00:00 UTC' -> hourly, Mon-Fri 05-16
    Unknown shapes return {'raw': spec} so nothing is silently dropped."""
    s = spec.split("#", 1)[0].strip()
    if not s:
        return None
    m = re.fullmatch(rf"(?:{_DOW}\.\.{_DOW}\s+)?\*-\*-\*\s+(\d\d)\.\.(\d\d):00(?::00)?(?:\s+UTC)?", s)
    if m:
        days = f"{m.group(1)}-{m.group(2)}" if m.group(1) else None
        return {"cadence": "hourly", "from": m.group(3), "to": m.group(4), "days": days}
    m = re.fullmatch(rf"{_DOW}(?:\s+\*-\*-\*)?\s+(\d\d:\d\d)(?::00)?(?:\s+UTC)?", s)
    if m:
        return {"cadence": "weekly", "day": m.group(1), "time": m.group(2)}
    m = re.fullmatch(r"\*-\*-(\d\d)\s+(\d\d:\d\d)(?::00)?(?:\s+UTC)?", s)
    if m:
        return {"cadence": "monthly", "day": int(m.group(1)), "time": m.group(2)}
    m = re.fullmatch(r"\*-\*-\*\s+(\d\d:\d\d)(?::00)?(?:\s+UTC)?", s)
    if m:
        return {"cadence": "daily", "time": m.group(1)}
    return {"raw": s}


def read_timer(systemd_dir: Path, unit: str) -> list[dict]:
    path = systemd_dir / f"{unit}.timer"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    out = []
    for line in text.splitlines():
        if line.strip().startswith("OnCalendar="):
            p = parse_oncalendar(line.split("=", 1)[1])
            if p:
                out.append(p)
    return out


def _courts_flag(systemd_dir: Path, unit: str) -> list[str]:
    try:
        text = (systemd_dir / f"{unit}.service").read_text(encoding="utf-8")
    except OSError:
        return []
    m = re.search(r"--courts\s+([\w,]+)", text)
    return m.group(1).split(",") if m else []


def load_code_facts(repo: Path) -> dict:
    """Which scraper runs when, from the code that decides it."""
    scrapers = _literal(repo / "run_scraper.py", "SCRAPERS") or {}
    skip = set(_literal(repo / "run_all_scrapers.py", "SKIP_BY_DEFAULT") or ())
    own = _literal(repo / "scripts" / "check_scraper_freshness.py",
                   "INDEPENDENTLY_SCHEDULED_SOURCES") or {}
    sd = repo / "systemd"
    federal = _courts_flag(sd, SCRAPE_UNITS["federal"])
    late = _courts_flag(sd, SCRAPE_UNITS["late"])
    timers = {u: read_timer(sd, u) for u in SCRAPE_UNITS.values()}
    sched: dict[str, list[dict]] = {}
    for key in sorted(scrapers):
        runs = []
        if key in own:
            unit = own[key].removesuffix(".timer")
            runs.append({"kind": "own_timer", "unit": unit, "when": read_timer(sd, unit)})
        elif key not in skip:
            runs.append({"kind": "nightly", "unit": SCRAPE_UNITS["nightly"],
                         "when": timers[SCRAPE_UNITS["nightly"]]})
        if key in federal:
            runs.append({"kind": "federal", "unit": SCRAPE_UNITS["federal"],
                         "when": timers[SCRAPE_UNITS["federal"]]})
        if key in late:
            runs.append({"kind": "late", "unit": SCRAPE_UNITS["late"],
                         "when": timers[SCRAPE_UNITS["late"]]})
        for unit, target in POLLER_SCRAPER.items():
            if target == key:
                runs.append({"kind": "poller", "unit": unit, "when": timers.get(unit) or read_timer(sd, unit)})
        sched[key] = runs
    return {"scrapers": sorted(scrapers), "schedule": sched}


# ─── health (latest real run per scraper) ───────────────────────────────────

def merge_health(health_files: list[dict | None]) -> dict[str, dict]:
    """Latest real run per scraper across the nightly, federal and late (manual)
    health files. A 'skipped: ...' entry (late run declining to repeat a clean
    earlier run) is not a run and never shadows the run it points at."""
    best: dict[str, dict] = {}
    for h in health_files:
        if not h:
            continue
        run_at = h.get("run_at")
        source = h.get("run_source")
        for key, e in (h.get("scrapers") or {}).items():
            if not isinstance(e, dict):
                continue
            if str(e.get("note") or "").startswith("skipped"):
                continue
            cur = best.get(key)
            if cur and (cur.get("run_at") or "") >= (run_at or ""):
                continue
            best[key] = {**e, "run_at": run_at, "run_source": source}
    return best


def run_ok(e: dict | None) -> bool | None:
    if not e:
        return None
    return bool(e.get("success")) and not e.get("timed_out") and not e.get("error")


def _run_view(e: dict | None) -> dict | None:
    if not e:
        return None
    return {
        "at": e.get("run_at"), "run": e.get("run_source"), "ok": run_ok(e),
        "new": e.get("new_count"), "errors": e.get("error_count"),
        "timed_out": bool(e.get("timed_out")),
        "error": (str(e["error"])[:240] if e.get("error") else None),
        "note": (str(e["note"])[:240] if e.get("note") else None),
        "our_count": e.get("our_count"),
        "portal_count": e.get("portal_count"), "gap": e.get("gap"),
    }


# ─── systemd runtime (VPS only; empty elsewhere) ────────────────────────────

def _systemd_ts(s: str) -> str | None:
    s = (s or "").strip()
    if not s or s == "n/a":
        return None
    for fmt in ("%a %Y-%m-%d %H:%M:%S %Z", "%a %Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            continue
    return None


def read_units(units: list[str]) -> dict[str, dict]:
    """Last run of each unit: {'result', 'finished', 'active'}. Read-only
    `systemctl show`; returns {} where systemd is absent (dev, CI)."""
    out: dict[str, dict] = {}
    for unit in units:
        try:
            p = subprocess.run(
                ["systemctl", "show", f"{unit}.service", "-p", "Result",
                 "-p", "ExecMainExitTimestamp", "-p", "ActiveState"],
                capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            return {}
        if p.returncode != 0:
            continue
        kv = dict(line.split("=", 1) for line in p.stdout.splitlines() if "=" in line)
        out[unit] = {"result": kv.get("Result") or None,
                     "finished": _systemd_ts(kv.get("ExecMainExitTimestamp", "")),
                     "active": kv.get("ActiveState") or None}
    return out


# ─── courts ─────────────────────────────────────────────────────────────────

def _stats_by_court(stats: dict) -> dict[str, dict]:
    """stats.json groups by (court, canton); fold to court."""
    out: dict[str, dict] = {}
    for r in stats.get("by_court") or []:
        c = r.get("court")
        if not c:
            continue
        o = out.setdefault(c, {"count": 0, "latest": None, "last_scraped": None, "earliest": None})
        o["count"] += int(r.get("count") or 0)
        for k, pick in (("latest", max), ("last_scraped", max), ("earliest", min)):
            v = r.get(k)
            if v:
                o[k] = pick(o[k], v) if o[k] else v
    return out


def _superseded_list(spec, code: str, all_codes: list[str]) -> list[str]:
    if isinstance(spec, list):
        return spec
    if isinstance(spec, dict) and spec.get("prefix"):
        p = spec["prefix"]
        return [c for c in all_codes if c.startswith(p) and c != code]
    return []


def court_entry(code: str, reg: dict | None, cov: dict | None, st: dict | None,
                rate: dict | None, facts: dict, health: dict[str, dict],
                units: dict[str, dict], policy: dict, notes: dict,
                all_codes: list[str], today: date) -> dict:
    reg = reg or {}
    cov = cov or {}
    st = st or {}
    by_year = {str(k): int(v) for k, v in (cov.get("by_year") or {}).items()}
    scrapers = set(facts["scrapers"])

    # Feeders: explicit in the registry, else the scraper of the same key.
    if "fed_by" in reg:
        feeders = list(reg["fed_by"])
    else:
        feeders = [code] if code in scrapers else []
    feeder_views, scheduled, any_failed = [], False, False
    for f in feeders:
        runs = facts["schedule"].get(f, [])
        last = health.get(f)
        if last is None and runs and runs[0]["kind"] == "own_timer":
            u = units.get(runs[0]["unit"]) or {}
            if u.get("finished"):
                last = {"run_at": u["finished"], "run_source": "own_timer",
                        "success": u.get("result") == "success", "timed_out": False,
                        "error": None if u.get("result") == "success" else u.get("result")}
        if runs:
            scheduled = True
            if run_ok(last) is False:
                any_failed = True
        feeder_views.append({
            "scraper": f, "registered": f in scrapers,
            "runs": [{"kind": r["kind"], "unit": r["unit"], "when": r["when"]} for r in runs],
            "last_run": _run_view(last),
        })

    newest = _parse_date(st.get("latest"))
    added = _parse_date(st.get("last_scraped"))
    if newest and newest > today:
        newest = None
    y = today.year
    p1, p2 = by_year.get(str(y - 1), 0), by_year.get(str(y - 2), 0)
    per_year = (p1 + p2) / 2.0
    if per_year == 0:
        tail = [n for yy, n in sorted(by_year.items()) if n > 0][-2:]
        per_year_hist = sum(tail) / len(tail) if tail else 0.0
    else:
        per_year_hist = per_year
    lag = int(policy.get("lag_days", {}).get(code, 0))
    every = (365.0 / per_year) if per_year > 0 else None
    win_newest = (_clamp(STALE_FACTOR * every, MIN_WINDOW_NEWEST, MAX_WINDOW) + lag) if every else MAX_WINDOW + lag
    win_added = _clamp(STALE_FACTOR * every, MIN_WINDOW_ADDED, MAX_WINDOW) if every else MAX_WINDOW
    d_newest, d_added = _days(newest, today), _days(added, today)

    superseded_by = _superseded_list(reg.get("superseded_by"), code, all_codes)
    if isinstance(reg.get("superseded_by"), dict) and reg["superseded_by"].get("collection"):
        superseded_collection = reg["superseded_by"]["collection"]
    else:
        superseded_collection = None
    rate_status = (rate or {}).get("status")

    if reg.get("ended"):
        status, why = "closed", "ended"
    elif superseded_by or superseded_collection:
        status, why = "superseded", "superseded"
    elif not scheduled:
        status = "no_intake"
        if any(fv["registered"] for fv in feeder_views):
            why = "scraper_not_scheduled"
        elif reg.get("feed") == "entscheidsuche":
            why = "feed_retired"
        elif reg.get("feed") == "import":
            why = "import_retired"
        else:
            why = "no_scraper"
    elif any_failed:
        status, why = "failing", "run_failed"
    elif rate_status == "EXPECTED":
        status, why = "quiet", "silent_by_design"
    elif rate_status == "WAIT":
        status, why = "quiet", "publication_lag"
    elif rate_status == "STALE":
        status, why = "stale", "rate_stale"
    elif (d_newest is None or d_newest > win_newest) and (d_added is None or d_added > win_added):
        status, why = "stale", "no_new_decision"
    else:
        status, why = "fresh", None
        if every is None:
            why = "rare"

    # Public context: the curated coverage note; the alert policy's reason only
    # where it explains a by-design silence. Its reasons for retired-feed codes
    # were written before the 2026-11-08 audit and are partly wrong (be_bvd
    # "portal answered 0 rows", vd_omni "at parity"); the status says it plainly.
    note = notes.get(code)
    if not note and status in ("quiet", "closed"):
        note = policy.get("expected_stale", {}).get(code)
    years = sorted(yy for yy, n in by_year.items() if n > 0)
    return {
        "code": code,
        "name": reg.get("name") or code,
        "group": reg.get("group") or "unmapped",
        "canton": reg.get("canton"),
        "portal": reg.get("portal"),
        "licence": reg.get("licence"),
        "count": int(cov.get("total") or st.get("count") or 0),
        "first_year": years[0] if years else None,
        "last_year": years[-1] if years else None,
        "undated": int(cov.get("undated") or 0),
        "newest_decision": newest.isoformat() if newest else None,
        "days_since_newest": d_newest,
        "last_added": added.isoformat() if added else None,
        "days_since_added": d_added,
        "cadence": {
            "per_year": round(per_year, 1),
            "per_year_last_active": round(per_year_hist, 1),
            "every_days": max(1, round(every)) if every else None,
            "lag_days": lag,
            "stale_after_days": win_newest if scheduled and status in ("fresh", "stale") else None,
        },
        # The detector's reason is published only when it is its own arithmetic
        # ("3 of 69 expected"); for EXPECTED it echoes the alert-policy text.
        "rate_check": ({"status": rate_status,
                        "reason": None if rate_status == "EXPECTED" else ((rate or {}).get("reason") or None)}
                       if rate_status else None),
        "intake": {
            "live": scheduled,
            "feed": reg.get("feed"),
            "feeders": feeder_views,
        },
        "status": status,
        "why": why,
        "ended": reg.get("ended"),
        "superseded_by": superseded_by or None,
        "superseded_by_collection": superseded_collection,
        "note": note,
        "unmapped": not reg,
    }


# ─── collections (laws, Materialien, practice, commentaries, scholarship) ───

def _q(db: Path, sql: str, params=()):
    if not db.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro&immutable=1", uri=True)
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return None


def collect_collection_facts(out_dir: Path) -> dict:
    """Counts and year spans from the non-decision databases. Small tables or
    columns ahead of the text blobs only; never decisions.db."""
    f: dict = {}
    r = _q(out_dir / "materialien.db",
           "SELECT COUNT(*), MIN(bbl_year), MAX(bbl_year) FROM botschaft_documents")
    if r:
        f["materialien"] = {"count": r[0][0], "first_year": r[0][1], "last_year": r[0][2]}
    r = _q(out_dir / "consultations.db",
           "SELECT COUNT(*), MIN(year), MAX(year) FROM consultation_procedures")
    d = _q(out_dir / "consultations.db", "SELECT COUNT(*) FROM consultation_documents")
    if r:
        f["vernehmlassungen"] = {"count": r[0][0], "first_year": r[0][1], "last_year": r[0][2],
                                 "documents": d[0][0] if d else None}
    r = _q(out_dir / "practice.db",
           "SELECT s.source, s.issuing_authority, s.doc_count, s.last_scraped_at, "
           "MIN(substr(p.date,1,4)), MAX(substr(p.date,1,4)) "
           "FROM sources s LEFT JOIN practice p ON p.source = s.source "
           "GROUP BY s.source ORDER BY s.doc_count DESC")
    if r:
        f["practice"] = {"count": sum(x[2] or 0 for x in r), "sources": [
            {"source": x[0], "authority": x[1], "count": x[2], "last_scraped": x[3],
             "first_year": x[4] if x[4] and x[4].isdigit() else None,
             "last_year": x[5] if x[5] and x[5].isdigit() else None} for x in r]}
    r = _q(out_dir / "legal_scholarship.db",
           "SELECT COUNT(*), MIN(NULLIF(year,0)), "
           "MAX(CASE WHEN year <= CAST(strftime('%Y','now') AS INTEGER) THEN year END) "
           "FROM publications")
    if r:
        f["scholarship"] = {"count": r[0][0], "first_year": r[0][1], "last_year": r[0][2]}
    return f


def _db_mtime(out_dir: Path | None, name: str | None) -> date | None:
    if not out_dir or not name:
        return None
    try:
        return datetime.fromtimestamp((out_dir / name).stat().st_mtime, timezone.utc).date()
    except OSError:
        return None


def collections(registry: dict, stats: dict, facts: dict, units: dict[str, dict],
                systemd_dir: Path, today: date, out_dir: Path | None = None) -> list[dict]:
    corpus = stats.get("corpus") or {}
    out = []
    for c in registry.get("collections") or []:
        cid = c["id"]
        row = {k: c.get(k) for k in ("id", "kind", "name", "authority", "portal", "licence", "sources_note")}
        count_key = c.get("count_from_stats")
        row["count"] = corpus.get(count_key) if count_key else None
        fx = facts.get(c.get("facts_key") or cid) or {}
        if row["count"] is None:
            row["count"] = fx.get("count")
        for k in ("first_year", "last_year", "documents"):
            if fx.get(k) is not None:
                row[k] = fx[k]
        if cid == "practice" and fx.get("sources"):
            row["sources"] = fx["sources"]
        if c.get("breakdown_from_stats"):
            row["breakdown"] = corpus.get(c["breakdown_from_stats"])
        runs = []
        for unit in c.get("units") or []:
            u = units.get(unit) or {}
            runs.append({"unit": unit, "when": read_timer(systemd_dir, unit),
                         "result": u.get("result"), "finished": u.get("finished"),
                         "active": u.get("active")})
        row["runs"] = runs
        finished = [_parse_date(r["finished"]) for r in runs if r.get("finished") and r.get("result") == "success"]
        # The database file is swapped atomically at the end of a successful
        # build, so its mtime is a second witness (the publish unit is still
        # running when this script runs inside it).
        built = _db_mtime(out_dir, c.get("db"))
        row["db_updated"] = built.isoformat() if built else None
        cands = [d for d in finished + [built] if d]
        last_ok = max(cands) if cands else None
        row["last_success"] = last_ok.isoformat() if last_ok else None
        failed = any(r.get("result") not in (None, "success") for r in runs)
        expected = c.get("expected_every_days")
        if failed:
            row["status"] = "failing"
        elif last_ok is None:
            row["status"] = "unknown"
        elif expected and (today - last_ok).days > 2 * expected + 1:
            row["status"] = "stale"
        else:
            row["status"] = "fresh"
        out.append(row)
    return out


# ─── assembly ───────────────────────────────────────────────────────────────

def build(registry: dict, coverage: dict, stats: dict, health_files: list[dict | None],
          policy: dict, notes: dict, facts: dict, units: dict[str, dict],
          collection_facts: dict, systemd_dir: Path, today: date,
          now_iso: str | None = None, out_dir: Path | None = None) -> dict:
    reg_courts = registry.get("courts") or {}
    cov_rows = {r["court"]: r for r in coverage.get("courts") or [] if r.get("court")}
    st_rows = _stats_by_court(stats)
    health = merge_health(health_files)
    rate_rows = {r.court: {"status": r.status, "reason": r.reason}
                 for r in _rate_evaluate(coverage, policy, today)} if coverage.get("courts") else {}

    # Every code the corpus holds, exactly once. A registered code without a
    # single row (folded away, or not yet scraped) is listed in the summary.
    codes = sorted(set(cov_rows) | set(st_rows))
    empty_registered = sorted(set(reg_courts) - set(codes))
    courts = []
    for code in codes:
        e = court_entry(code, reg_courts.get(code), cov_rows.get(code), st_rows.get(code),
                        rate_rows.get(code), facts, health, units, policy, notes, codes, today)
        e["unmapped"] = code not in reg_courts
        courts.append(e)

    # Scrapers that run but feed no listed code would be invisible; surface them.
    fed = {f["scraper"] for c in courts for f in c["intake"]["feeders"]}
    orphans = sorted(k for k, runs in facts["schedule"].items() if runs and k not in fed)

    by_status = {s: 0 for s in STATUSES}
    for c in courts:
        by_status[c["status"]] = by_status.get(c["status"], 0) + 1

    run_meta = {}
    for h in health_files:
        if h and h.get("run_source"):
            run_meta[h["run_source"]] = h.get("run_at")

    return {
        "schema": SCHEMA,
        "generated_at": now_iso or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "as_of": today.isoformat(),
        "inputs": {
            "coverage_generated_at": coverage.get("generated_at"),
            "stats_generated_at": stats.get("generated_at"),
            "health_runs": run_meta,
        },
        "method": {
            "stale_factor": STALE_FACTOR, "min_window_newest_days": MIN_WINDOW_NEWEST,
            "min_window_added_days": MIN_WINDOW_ADDED, "max_window_days": MAX_WINDOW,
            "rate_detector": "scripts/check_intake_rates.py",
        },
        "summary": {
            "courts": len(courts),
            "decisions": sum(c["count"] for c in courts),
            "by_status": by_status,
            "portal_measured": sum(1 for c in courts for f in c["intake"]["feeders"]
                                   if f["last_run"] and f["last_run"].get("portal_count") is not None),
            "unmapped": [c["code"] for c in courts if c["unmapped"]],
            "registered_without_rows": empty_registered,
            "orphan_scrapers": orphans,
        },
        "licences": registry.get("licences") or {},
        "courts": courts,
        "collections": collections(registry, stats, collection_facts, units, systemd_dir, today, out_dir),
    }


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def generate(repo: Path, coverage: dict | None, stats_path: Path, logs_dir: Path,
             registry_path: Path, policy_path: Path, notes_path: Path, out_path: Path,
             out_dir: Path, today: date | None = None, use_systemd: bool = True) -> dict:
    registry = _read_json(registry_path) or {}
    stats = _read_json(stats_path) or {}
    coverage = coverage or {}
    health_files = [_read_json(logs_dir / n) for n in
                    ("scraper_health.json", "scraper_health_federal.json", "scraper_health_manual.json")]
    policy = _load_policy(policy_path)
    notes = _read_json(notes_path) or {}
    facts = load_code_facts(repo)
    unit_names = set(SCRAPE_UNITS.values())
    for runs in facts["schedule"].values():
        unit_names.update(r["unit"] for r in runs)
    for c in registry.get("collections") or []:
        unit_names.update(c.get("units") or [])
    units = read_units(sorted(unit_names)) if use_systemd else {}
    payload = build(registry, coverage, stats, health_files, policy, notes, facts, units,
                    collect_collection_facts(out_dir), repo / "systemd",
                    today or datetime.now(timezone.utc).date(), out_dir=out_dir)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    tmp.replace(out_path)
    return payload


def summary_line(p: dict) -> str:
    s = p["summary"]
    parts = ", ".join(f"{k} {v}" for k, v in s["by_status"].items() if v)
    extra = ""
    if s["unmapped"]:
        extra += f"; UNMAPPED {','.join(s['unmapped'])}"
    if s["orphan_scrapers"]:
        extra += f"; ORPHAN scrapers {','.join(s['orphan_scrapers'])}"
    return f"sources/status.json: {s['courts']} court codes ({parts}){extra}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--coverage", default=str(REPO / "docs" / "coverage.json"))
    ap.add_argument("--stats", default=str(REPO / "docs" / "stats.json"))
    ap.add_argument("--logs", default=str(REPO / "logs"))
    ap.add_argument("--registry", default=str(REPO / "docs" / "sources" / "registry.json"))
    ap.add_argument("--policy", default=str(REPO / "docs" / "intake_expected_stale.json"))
    ap.add_argument("--notes", default=str(REPO / "docs" / "coverage_notes.json"))
    ap.add_argument("--db-dir", default=os.environ.get("SWISS_CASELAW_DIR", str(REPO / "output")))
    ap.add_argument("--output", default=str(REPO / "docs" / "sources" / "status.json"))
    ap.add_argument("--as-of", help="YYYY-MM-DD (default: today UTC)")
    ap.add_argument("--no-systemd", action="store_true")
    a = ap.parse_args(argv)
    try:
        p = generate(REPO, _read_json(Path(a.coverage)), Path(a.stats), Path(a.logs),
                     Path(a.registry), Path(a.policy), Path(a.notes), Path(a.output),
                     Path(a.db_dir), date.fromisoformat(a.as_of) if a.as_of else None,
                     use_systemd=not a.no_systemd)
        print(summary_line(p))
    except Exception as e:  # noqa: BLE001 — never fail a publish over the status page
        print(f"source_status failed (non-fatal): {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
