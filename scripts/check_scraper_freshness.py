#!/usr/bin/env python3
"""
check_scraper_freshness.py — Alert on stale or failed scrapers.

Reads scraper_health.json and the per-court content-change snapshots from
coverage.db, and writes warnings to logs/scraper_alerts.log
when:
  - any scraper failed in today's run
  - any court lacks recent evidence of a successful scrape
  - the entscheidsuche cron timer has been failing for >2 weeks

Designed to run after the daily scrape (e.g. 03:00 UTC).

**Notification semantics** (changed 2026-05-08):

  - Always exits 0 on a successful run. Alerts are surfaced via
    (a) the per-day log line at ``logs/scraper_alerts.log`` and
    (b) ntfy.sh push at ``ntfy.sh/opencaselaw-scrapers`` when the
    alert SET changes vs. the previous run.
  - The previous behaviour (exit 1 on alerts, no notify) caused this
    service to be journalled as ``failed`` daily for weeks while the
    actual alerts rotted in a logfile nobody read. The dispatch is
    now active.

Usage:
    python3 scripts/check_scraper_freshness.py
    python3 scripts/check_scraper_freshness.py --max-stale-days 14
    python3 scripts/check_scraper_freshness.py --no-ntfy   # local debug
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ── ntfy push channel ─────────────────────────────────────────────
# Env-driven since 2026-08-24: the operator subscribes to ONE topic
# (NTFY_TOPIC, set via /opt/caselaw/ops.env in the service drop-ins) —
# the previous per-purpose topic had no subscribers, ever. The legacy
# topic stays as fallback so an unconfigured run keeps old behavior.
NTFY_URL = (
    os.environ.get("NTFY_URL", "https://ntfy.sh").rstrip("/")
    + "/"
    + os.environ.get("NTFY_TOPIC", "opencaselaw-scrapers")
)

REPO = Path(__file__).resolve().parent.parent

# Coverage snapshot store. Lives in state/coverage.db since the
# 2026-04-20 migration; output/decisions.db's coverage tables have been
# EMPTY since, so the old read here silently disabled per-court staleness
# detection (false "all clear"). Override with OCL_COVERAGE_DB for tests.
COVERAGE_DB = Path(os.environ.get("OCL_COVERAGE_DB", str(REPO / "state" / "coverage.db")))

# Courts that are KNOWN-DEAD upstream — don't alert on them
KNOWN_DEAD_SOURCES = {
    "ch_vb",                 # source dead since 2021
    "ag_baugesetzgebung",    # source stagnant since Nov 2025
    "ag_weitere",            # source dead since 2023
    "sg_publikationen",      # entscheidsuche.ch path stale (we have direct scraper anyway)
    "be_steuerrekurs",       # portal DB disconnected (Feb 2026)
    "ow_gerichte",           # portal offline since Dec 2022
    "ta_sst",                # rare publication
    "ch_bundesrat",          # rare publication
    "comcom",                # rare publication
}

# Courts that ONLY come from entscheidsuche.ch — use ES last-modified to grade them
ENTSCHEIDSUCHE_ONLY = {
    "vd_findinfo", "vd_omni", "ch_vb", "sg_gerichte", "tg_obergericht",
    "be_bvd", "be_weitere", "sh_obergericht", "be_steuerrekurs",
    "ag_baugesetzgebung", "ag_weitere",
}

# Courts where partial-fetch failures are expected (upstream limitation,
# not a scraper bug) — downgrade FAIL → WARN. ECtHR's HUDOC HTML
# converter returns empty bodies for many pre-2018 judgments
# (PDF-only); CACHE_NONE_AS_GAP=True caches the gaps but the cumulative
# count keeps creeping up. Treat as informational, not an outage.
TOLERATED_PARTIAL_SOURCES = {
    "ecthr",
}
# These scrapers egress through the MacBook reverse-SOCKS tunnel, which
# sleeps when the laptop sleeps (JU/NE portals block Hetzner at TCP;
# search.bger.ch Incapsula-blocks the Hetzner IP since 2026-06-29, so
# BGer/BGE discovery is proxied too). Failures during the 01:00 UTC
# window are normal; the late-scrapers timer at 10:00 UTC retries once
# the tunnel is back. Keep in sync with run_all_scrapers.TUNNEL_DEPENDENT.
TUNNEL_DEPENDENT_SOURCES = {
    "bger",
    "bge",
    "ju_gerichte",
    "ne_gerichte",
    "ne_jurisprudence_adm",
}

# Scrapers that run on their OWN systemd timer rather than through
# run_all_scrapers.py. They are correctly absent from a full
# scraper_health.json run, so the registry reconciliation below must not
# report them as "silently skipped or never ran".
#
# 2026-08-27: ecthr fired that WARN while it had in fact just completed a
# 216-minute full-corpus backfill — +8,270 judgments, 0 errors — under
# opencaselaw-ecthr-backfill.service. A daily top-off lives in
# opencaselaw-ecthr.timer. The alert was not merely noise: it asserted the
# opposite of the truth, which is the fastest way to train an operator to
# ignore a channel.
#
# They still get a freshness check of their own below, keyed on the state
# file, so a genuinely dead independent scraper is not silently exempt.
# Value is the owning unit, for the alert text.
INDEPENDENTLY_SCHEDULED_SOURCES = {
    "ecthr": "opencaselaw-ecthr.timer",
}

# How stale an independently-scheduled scraper's state file may get before
# we alert. ECtHR tops off daily; three days absorbs a weekend plus one
# missed run without crying wolf.
INDEPENDENT_STALE_DAYS = 3

# Courts that legitimately publish rarely (small chamber, archival
# series, historical-only). The "<30s + 0 new" heuristic is a
# false-positive here — a clean caught-up exit looks identical to a
# silent failure. Verified manually 2026-05-08: each is reachable and
# the historical row count is current.
SILENT_SKIP_EXEMPT_SOURCES = {
    # ~1,244-row archival corpus (1915–2025); MKG publishes Bd. 1–16
    # via alexandria.ch, no new volumes expected.
    "mkg",
    # Small civil/criminal-chamber series; portal occasionally returns
    # 0 results when no new publications are pending (legitimate empty).
    "be_zivilstraf",
    # BL portal pre-poll bails out fast when we already have the last
    # batch; large catch-up only in long-poll runs.
    "bl_gerichte",
    # Single-request discovery: one search call returns the entire
    # listing (~19s), then each new decision is fetched under the rate
    # limit (~60s). Duration is therefore 20s + 60s x new_count, so a
    # zero-new day is necessarily a ~30s day and carries no information
    # about portal health. Verified 2026-08-06 from the scraper log: on
    # 2026-08-04, the run this heuristic flagged, discovery returned
    # 34,243 rows against 34,245 known IDs — the portal was fully
    # reachable and there was simply nothing new. Six false alarms
    # (14/16/19/20 Jul, 2/4 Aug 2026). Outages are caught instead by
    # check_stalled_corpus below.
    "zh_sozialversicherungsgericht",
}


def get_last_scraped(court: str) -> str | None:
    """Return ISO date of the last content snapshot from the coverage store.

    Reads source_snapshots from state/coverage.db (the live coverage DB
    since 2026-04-20). Opened mode=ro (NOT immutable=1) because coverage.db
    is written by scrapers concurrently — immutable would risk stale/torn
    reads. Override the path with OCL_COVERAGE_DB.

    Note: run_scraper.py writes source_snapshots only for changed years (or
    initial backfill), so this is not by itself proof of the last successful
    scrape attempt. Fresh scraper_health success takes precedence below.
    """
    if not COVERAGE_DB.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{COVERAGE_DB}?mode=ro", uri=True, timeout=5)
        row = conn.execute(
            "SELECT MAX(snapshot_date) FROM source_snapshots WHERE source_key = ?",
            (court,),
        ).fetchone()
        conn.close()
        return row[0] if row and row[0] else None
    except Exception:
        return None


def selftest() -> int:
    """Assert the freshness monitor is wired to LIVE coverage data.

    Guards against the 2026-04-20-class regression where get_last_scraped
    silently read an empty DB and returned None for every court (a false
    "all clear" — worse than no monitor). Exits 0 if the coverage store has
    snapshots AND get_last_scraped returns a date for the most-recently
    snapshotted source; non-zero otherwise.
    """
    if not COVERAGE_DB.exists():
        print(f"SELF-TEST FAIL: coverage DB not found at {COVERAGE_DB}", file=sys.stderr)
        return 1
    try:
        conn = sqlite3.connect(f"file:{COVERAGE_DB}?mode=ro", uri=True, timeout=5)
        n = conn.execute("SELECT COUNT(*) FROM source_snapshots").fetchone()[0]
        row = conn.execute(
            "SELECT source_key, MAX(snapshot_date) AS d FROM source_snapshots "
            "GROUP BY source_key ORDER BY d DESC LIMIT 1"
        ).fetchone()
        conn.close()
    except Exception as e:
        print(f"SELF-TEST FAIL: cannot read source_snapshots in {COVERAGE_DB}: {e}", file=sys.stderr)
        return 1
    if n == 0 or not row or not row[1]:
        print(f"SELF-TEST FAIL: source_snapshots has {n} rows in {COVERAGE_DB} — "
              f"freshness monitor is reading an empty/dead DB", file=sys.stderr)
        return 1
    sample = get_last_scraped(row[0])  # exercise the real code path
    if sample is None:
        print(f"SELF-TEST FAIL: get_last_scraped({row[0]!r}) returned None despite "
              f"{n} snapshot rows", file=sys.stderr)
        return 1
    print(f"SELF-TEST PASS: {n} snapshot rows in {COVERAGE_DB}; "
          f"get_last_scraped({row[0]!r})={sample}")
    return 0


def check_es_cron_health() -> tuple[bool, str]:
    """Check if entscheidsuche timer has been failing.

    Returns (ok, message).
    """
    try:
        result = subprocess.run(
            ["systemctl", "show", "opencaselaw-entscheidsuche.service",
             "-p", "Result", "-p", "ActiveExitTimestamp"],
            capture_output=True, text=True, timeout=10,
        )
        props = dict(line.split("=", 1) for line in result.stdout.strip().split("\n") if "=" in line)
        last_result = props.get("Result", "unknown")
        if last_result not in ("success", "unknown", ""):
            return False, f"ES service last result: {last_result}"
        return True, f"ES service ok (last={last_result})"
    except Exception as e:
        return True, f"ES check skipped: {e}"


GAP_STATE_PATH = Path("logs/scraper_gap_state.json")
GAP_MIN = 10          # smaller shortfalls are usually counting differences
GAP_PERSIST_DAYS = 3  # a gap must survive this long before it is worth waking

# Structural offsets confirmed by a full rescan: the portal's row count
# exceeds the number of distinct decisions it will actually hand out, so
# the difference is not a backlog and must not alarm. Each entry records
# the date the full scan was run and what it returned, so the claim can be
# re-tested rather than taken on trust.
KNOWN_GAP_OFFSETS: dict[str, dict] = {
    "sz_gerichte": {
        "gap": 51,
        "verified": "2026-09-15",
        "evidence": "OCL_SCRAPER_RESCAN_ALL=1 walked all 3450 portal pages "
                    "in 86.2 min with 0 errors, 0 NoneReturns and +0 new "
                    "against 3399 known ids (re-verified 2026-09-15; first "
                    "verified 2026-08-04 over 3415 pages). The offset held at "
                    "exactly 51 while the corpus grew by 35+ decisions, which "
                    "a real backlog would not do",
    },
    "gr_gerichte": {
        "gap": 50,
        "verified": "2026-09-15",
        "evidence": "Scratch-state listing probe 2026-09-15 (31 min): 14903 "
                    "portal rows -> 14893 parsed -> 14852 distinct dockets, "
                    "41 duplicate rows, 10 rows without a recognisable docket, "
                    "0 unknown ids against 14853 known. The 2026-08-25 "
                    "RESCAN_ALL over 14856 rows had already returned +0. Since "
                    "1b8415cd base_tribuna drops duplicate rows from "
                    "portal_count, so the nightly gap falls to the ~10 "
                    "unparsed rows",
    },
    "vs_gerichte": {
        "gap": 399,
        "verified": "2026-08-25",
        "evidence": "OCL_SCRAPER_RESCAN_ALL=1 walked every offset to 4995 in "
                    "batches of 500 ('discovered 0 new' at each), 2.5 min, "
                    "0 errors, +0 new against 4596 known ids — same duplicate-"
                    "listing signature as gr_gerichte, on a different scraper "
                    "implementation",
    },
    "fr_gerichte": {
        "gap": 11,
        "verified": "2026-09-15",
        "evidence": "2026-09-15 RESCAN_ALL after the base64-path fix "
                    "(9f5bf8e9): 14708 rows, 31.8 min, 0 errors, 0 NoneReturns, "
                    "+25 new, gap 9 = duplicate listing rows (the gr_gerichte "
                    "signature). CORRECTION of the 2026-09-05 reading: the 2 "
                    "'rows without a download link' (102 2026 223, 601 2026 45) "
                    "were the first rows carrying the new base64 document path, "
                    "i.e. a scraper defect, and had grown to 25 real decisions "
                    "by 09-15. A NoneReturn on a Tribuna row is not evidence of "
                    "a structural offset",
    },
    "be_verwaltungsgericht": {
        "gap": 15,
        "verified": "2026-09-15",
        "evidence": "OCL_SCRAPER_RESCAN_ALL=1 with the docket-anchored parser "
                    "(#68 fixed 2026-08-27): 40.2 min, 0 errors, 0 NoneReturns, "
                    "+0 new against 11618 known ids; scratch-state listing "
                    "probe the same day: 11633 portal rows -> 11600 distinct "
                    "dockets, 0 unknown ids. The 15 are duplicate or "
                    "under-filled rows of the date-windowed listing, not the "
                    "#68 backlog",
    },
    # NOT added, deliberately: ju_gerichte (29), ne_gerichte (45) and
    # ne_jurisprudence_adm (37) also returned +0 new on 2026-08-25, but their
    # logs show urllib3 retries EXHAUSTING against the portals through the
    # SOCKS tunnel. A +0 that might mean "could not fetch" is not evidence of
    # a structural offset, and suppressing an alert on that basis would hide a
    # real shortfall. Re-test when the tunnel is quiet.
}

# Gaps that are REAL but that a rescan provably cannot close. Without this the
# alert keeps prescribing a remedy that has already been measured not to work,
# which is how an operator learns to ignore the whole channel.
KNOWN_GAP_REMEDIES: dict[str, str] = {
    "be_verwaltungsgericht": (
        "kein Nachlauf: der #68-Rueckstand (base_tribuna zippte doc_id und "
        "Geschaeftsnummer nach Position, die Trefferquote folgte der doc_id-"
        "Kurve, 2013: 82/674; behoben 2026-08-27) ist abgebaut — voller "
        "RESCAN_ALL 2026-09-15 (40.2 min, 0 Fehler, +0 neu) und eine "
        "Listing-Probe mit leerem State (11633 Zeilen -> 11600 Dossiers, 0 "
        "unbekannt). Ein Gap ueber den bekannten 15 waere neu: Duplikat- oder "
        "Unterfuellungszeilen des datumsgefensterten Listings pruefen (seit "
        "1b8415cd zieht base_tribuna beobachtete Duplikatzeilen vom "
        "portal_count ab)."
    ),
    # Forensik 2026-09-02 (3 Agenten + Cross-Check, nach sauberem RESCAN_ALL
    # aller drei Portale mit +0 neu / 0 Fehlern): dieselbe Identitaetsfalle
    # wie #68, diesmal per Design — decision_id ist docket-basiert, die
    # NE-Portale listen aber MEHRERE Fiches pro Aktenzeichen (Zwischen- und
    # Endentscheid je eigene Fiche/nF30_KEY). Discovery behaelt die neueste
    # und unterdrueckt aeltere dauerhaft; ein Rescan kann das nie schliessen.
    "ne_gerichte": (
        "REAL, aber kein Nachlauf: docket-basierte decision_id kollabiert "
        "mehrere Fiches pro Aktenzeichen (INT- vs. CDP-Entscheide); 35 der "
        "45 via entscheidsuche.ch-Kreuzabgleich benannt. Fix = per-Fiche-"
        "Identitaet (nF30_KEY) + gezielter Backfill, pipeline-gated. "
        "RESCAN_ALL 2026-09-02 (387 Seiten, 37.6 min, 0 Fehler): +0 — "
        "erwartungsgemaess wirkungslos."
    ),
    "ne_jurisprudence_adm": (
        "REAL, aber kein Nachlauf: gleiche Ursache wie ne_gerichte "
        "(Massnahmen- und Endentscheid teilen das Aktenzeichen, je eigene "
        "Fiche). 37 Kollisions-Fiches, in einem 17-Seiten-Walk enumerierbar. "
        "Fix = per-Fiche-Identitaet + Backfill, pipeline-gated. RESCAN_ALL "
        "2026-09-02 (83 Seiten, 5.4 min, 0 Fehler): +0."
    ),
    "ju_gerichte": (
        "GEMISCHT, kein Nachlauf: 4 von 29 sind real, aber upstream defekt — "
        "das Portal liefert fuer ADM 2011 85, ADM 2015 126, ADM 2015 140 und "
        "ADM 2016 11 Null-Byte-PDFs (HTTP 200, Content-Length: 0, verifiziert "
        "2026-09-02; einzige Abhilfe: Meldung ans Tribunal cantonal JU). Die "
        "uebrigen 25 sind vermutlich Duplikat-Zeilen der Portalliste, aber "
        "unklassifiziert. RESCAN_ALL 2026-09-02 (alle 1170 Seiten, 29.4 min, "
        "0 Fehler): +0 neu, 4 NoneReturns — die vier genannten."
    ),
}


def check_persistent_gaps(health: dict, today: str,
                          state_path: Path | None = None) -> list[str]:
    """Alert on coverage shortfalls that successful runs keep reporting.

    run_all_scrapers.py records `gap` (portal count minus ours) per court,
    but nothing watched it: sz_gerichte reported "[OK] … gap 51" every
    night for three weeks while 51 decisions stayed uncollected, because
    the newest-first scan stops after 200 consecutive known decisions and
    never reaches them (2026-08-03).

    A single day's gap is not alarmed — portals double-count, withdraw
    decisions, or publish placeholders. A gap that persists across
    GAP_PERSIST_DAYS distinct days is a real shortfall and gets one alert
    naming the catch-up switch.
    """
    path = state_path or GAP_STATE_PATH
    try:
        prev = json.loads(path.read_text()) if path.exists() else {}
    except Exception:
        prev = {}

    scrapers = health.get("scrapers", health) or {}
    cur: dict[str, dict] = {}
    alerts: list[str] = []
    for court, row in scrapers.items():
        if not isinstance(row, dict):
            continue
        gap = row.get("gap")
        if not isinstance(gap, int) or gap < GAP_MIN:
            continue                      # closed or never measured: forget it
        known = KNOWN_GAP_OFFSETS.get(court)
        if known and gap <= known["gap"]:
            continue                      # structural offset, not a shortfall
        seen = prev.get(court, {})
        # d[:10] collapses legacy minute-resolution keys on first load.
        days = sorted({d[:10] for d in seen.get("days", [])} | {today})
        cur[court] = {"gap": gap, "days": days[-400:]}
        if len(days) >= GAP_PERSIST_DAYS:
            ours = row.get("our_count")
            portal = row.get("portal_count")
            remedy = KNOWN_GAP_REMEDIES.get(court)
            if remedy is None:
                remedy = ("einmaliger Nachlauf mit OCL_SCRAPER_RESCAN_ALL=1 "
                          f"python3 run_scraper.py {court}")
            alerts.append(
                f"GAP {court}: {gap} Entscheide fehlen seit {len(days)} Tagen "
                f"(portal={portal}, ours={ours}) — {remedy}")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cur, indent=1, sort_keys=True))
    except Exception:
        pass                              # state is an optimisation, not a gate
    return alerts


STALL_STATE_PATH = Path("logs/scraper_stall_state.json")
STALL_MIN_CORPUS = 1000    # small series publish too rarely to judge this way

# Calibrated 2026-08-06 against 113 nightly runs per court (2026-04-14 to
# 2026-08-06, parsed from the "Done. +N new, <total>" lines in logs/*.log).
# Publication rhythms span two orders of magnitude, so one threshold cannot
# serve everyone: bger/bvger/ge_gerichte never stayed flat for more than 3
# consecutive runs, while be_zivilstraf — a real, growing court — stayed
# flat for 64 and then published a batch. The default therefore sits above
# the longest streak any growing court actually showed (64), and courts
# with a dense rhythm get a tighter bound where one is informative.
STALL_DEFAULT_DAYS = 90
STALL_TIGHT_DAYS = {
    # Longest zero-growth streak observed in 113 runs: 3 (corpus +480 over
    # the period). 14 days is nearly five times that, and this court needs
    # the cover: it is exempt from the duration heuristic above.
    "zh_sozialversicherungsgericht": 14,
}


def check_stalled_corpus(health: dict, today: str,
                         state_path: Path | None = None) -> list[str]:
    """Alert when a court that was growing stops growing.

    The silent-skip heuristic infers an outage from a fast run, which only
    holds for scrapers whose runtime scales with the corpus. Scrapers that
    discover the whole listing in one request finish fast whenever there is
    nothing new, so their duration says nothing (2026-08-06:
    zh_sozialversicherungsgericht produced six false alarms that way).

    Corpus growth is architecture-independent: whatever the portal or the
    scan strategy, a live court adds decisions. Alert when our_count has not
    moved across the court's threshold in distinct days.

    Growth must have been observed at least once before a court can alert.
    Without that, every archival feed that is meant to sit still (mkg, weko,
    the anwaltsaufsicht series — 112 flat runs and no growth at all) would
    alarm forever, and "stopped growing" would be claimed about something
    that was never seen growing.
    """
    path = state_path or STALL_STATE_PATH
    try:
        prev = json.loads(path.read_text()) if path.exists() else {}
    except Exception:
        prev = {}

    scrapers = health.get("scrapers", health) or {}
    cur: dict[str, dict] = {}
    alerts: list[str] = []
    for court, row in scrapers.items():
        if not isinstance(row, dict) or not row.get("success"):
            continue
        if court in KNOWN_DEAD_SOURCES or court in TOLERATED_PARTIAL_SOURCES:
            continue
        count = row.get("our_count")
        if not isinstance(count, int) or count < STALL_MIN_CORPUS:
            continue
        seen = prev.get(court, {})
        grew = bool(seen.get("grew"))
        if seen.get("count") != count:
            # grew (or shrank): clock restarts, and the court has now
            # proved it is live — from here a flat stretch means something
            cur[court] = {"count": count, "days": [today],
                          "grew": grew or "count" in seen}
            continue
        days = sorted({d[:10] for d in seen.get("days", [])} | {today})
        cur[court] = {"count": count, "days": days[-120:], "grew": grew}
        limit = STALL_TIGHT_DAYS.get(court, STALL_DEFAULT_DAYS)
        if grew and len(days) >= limit:
            alerts.append(
                f"STALL {court}: Bestand seit {len(days)} Tagen unveraendert "
                f"bei {count} Entscheiden trotz erfolgreicher Laeufe — "
                f"Portal oder Discovery pruefen")
    # A failed or absent run carries no growth evidence either way, so it must
    # not reset the clock. Without this, one tunnel-down night wiped bger's
    # entire history (observed 2026-08-25), and the five TUNNEL_DEPENDENT
    # sources could never reach the 90-entry threshold at all.
    # check_persistent_gaps deliberately does NOT do this: there, dropping a
    # court whose gap closed is the correct behaviour.
    for _k, _v in prev.items():
        cur.setdefault(_k, _v)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cur, indent=1, sort_keys=True))
    except Exception:
        pass                              # state is an optimisation, not a gate
    return alerts


def _alert_set_digest(alerts: list[str]) -> str:
    """Stable hash of the alert SET (order-independent). Used to skip
    ntfy when today's alerts are byte-for-byte the same as yesterday's
    — recurring portal warnings would otherwise spam the topic daily.
    """
    blob = "\n".join(sorted(alerts))
    return hashlib.sha256(blob.encode("utf-8", errors="replace")).hexdigest()


def _ascii_safe(s: str) -> str:
    """ntfy.sh forwards Title/Tags as raw HTTP headers, which urllib
    encodes as latin-1 by default. Strip non-latin-1 chars so an em-dash
    in a translated court name doesn't raise UnicodeEncodeError."""
    return s.encode("ascii", errors="replace").decode("ascii")


def _portal_count_confirms_caught_up(portal_n: object, our_n: int) -> bool:
    """Return True when the portal count rules out a silent zero-new skip.

    Some portals report a total that is a few rows below our corpus because
    decisions were withdrawn, de-duplicated, or historically recovered. Treat
    only small negative drift as caught-up; if the portal is ahead, missing, or
    far below us, keep the warning.
    """
    if portal_n is None:
        return False
    try:
        portal_i = int(portal_n)
    except (TypeError, ValueError):
        return False
    if portal_i <= 0 or our_n <= 0 or portal_i > our_n:
        return False
    tolerated_negative_drift = max(5, int(our_n * 0.001))
    return (our_n - portal_i) <= tolerated_negative_drift


def post_ntfy(alerts: list[str], today: str, *, priority: str, title: str) -> bool:
    """Best-effort ntfy push. Never raises (mirrors
    scripts/publish_failure_alert.py — alert path must not generate
    further failure noise). Returns True on HTTP 2xx, False on any
    failure; the caller uses this to decide whether to persist the
    dedupe state file (a failed post should not look 'already
    delivered' on the next run)."""
    body = f"{today}  {len(alerts)} alert(s)\n\n" + "\n".join(alerts[:30])
    if len(alerts) > 30:
        body += f"\n\n...({len(alerts) - 30} more in scraper_alerts.log)"
    try:
        req = urllib.request.Request(
            NTFY_URL,
            data=body.encode("utf-8", errors="replace"),
            headers={
                "Title": _ascii_safe(title),
                "Priority": priority,
                "Tags": "warning",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return 200 <= resp.status < 300
    except Exception as e:  # noqa: BLE001 — best-effort
        print(f"ntfy post failed: {e}", file=sys.stderr)
        return False


def maybe_dispatch_ntfy(
    alerts: list[str],
    today: str,
    state_dir: Path,
) -> str:
    """Dispatch via ntfy if today's alert set differs from the last
    dispatched set.

    Returns one of: ``posted``, ``unchanged``, ``skipped`` (no alerts).
    """
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "scraper_alerts_last_dispatched.json"

    if not alerts:
        # On clear-up, post one all-clear if we previously dispatched.
        # Drop the state file so the next regression re-notifies.
        if state_file.exists():
            try:
                last = json.loads(state_file.read_text())
                if last.get("digest"):
                    post_ntfy(
                        ["All previously-flagged scraper alerts have cleared."],
                        today,
                        priority="default",
                        title="opencaselaw scrapers all clear",
                    )
            except Exception:
                pass
            try:
                state_file.unlink()
            except OSError:
                pass
        return "skipped"

    digest = _alert_set_digest(alerts)
    last_digest = None
    if state_file.exists():
        try:
            last_digest = json.loads(state_file.read_text()).get("digest")
        except Exception:
            last_digest = None
    if digest == last_digest:
        return "unchanged"

    has_fail_or_critical = any(
        a.startswith(("FAIL ", "CRITICAL", "STALE "))
        for a in alerts
    )
    priority = "high" if has_fail_or_critical else "default"
    title = (
        f"opencaselaw scrapers - {len(alerts)} alert(s)"
        if not has_fail_or_critical
        else f"opencaselaw scrapers - {len(alerts)} alert(s), action needed"
    )
    posted = post_ntfy(alerts, today, priority=priority, title=title)
    if not posted:
        # Don't persist dedupe state on a failed post — the next run
        # should retry instead of declaring 'already delivered'.
        return "post_failed"

    try:
        state_file.write_text(json.dumps({
            "digest": digest,
            "dispatched_at": datetime.now(timezone.utc).isoformat(),
            "alert_count": len(alerts),
        }, indent=2))
    except OSError as e:
        print(f"state write failed: {e}", file=sys.stderr)
    return "posted"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-stale-days", type=int, default=14,
                        help="Alert if a court lacks recent scrape evidence for this many days")
    parser.add_argument("--max-es-stale-days", type=int, default=21,
                        help="Alert if an ES-only court is older than this")
    parser.add_argument("--health-file", type=str,
                        default=str(REPO / "logs" / "scraper_health.json"))
    parser.add_argument("--alert-log", type=str,
                        default=str(REPO / "logs" / "scraper_alerts.log"))
    parser.add_argument("--state-dir", type=str,
                        default=str(REPO / "state"),
                        help="Where to remember the last-dispatched alert set "
                             "(used to dedupe recurring ntfy posts).")
    parser.add_argument("--no-ntfy", action="store_true",
                        help="Skip ntfy dispatch (local debug).")
    parser.add_argument("--quiet", action="store_true",
                        help="Don't print to stdout, only write log")
    parser.add_argument("--self-test", action="store_true",
                        help="Assert the monitor reads live coverage data; exit non-zero if dead.")
    args = parser.parse_args()

    if args.self_test:
        sys.exit(selftest())

    alerts: list[str] = []
    now = datetime.now(timezone.utc)
    # Date-only. Minute resolution made every RUN a distinct "day": three runs
    # on 2026-08-25 counted as three, and "seit 11 Tagen" printed on 14
    # consecutive alerts while the true age was 21. Both counters now run
    # slower, so this can never manufacture an alert that was not already due.
    today = now.strftime("%Y-%m-%d")
    # Display/log stamp keeps minute resolution so scraper_alerts.log still
    # distinguishes the 03:00, 07:55 and 08:36 runs of the same day. Only the
    # day COUNTERS use the date-only `today` — that was the bug.
    stamp = now.strftime("%Y-%m-%d %H:%M UTC")

    # ── 1. Read scraper_health.json ──
    health_path = Path(args.health_file)
    scrapers: dict[str, dict] = {}
    health_run_dt: datetime | None = None
    if not health_path.exists():
        alerts.append(f"CRITICAL: scraper_health.json missing at {health_path}")
    else:
        try:
            health = json.loads(health_path.read_text())
            scrapers = health.get("scrapers", {})

            # Registry-vs-health reconciliation (2026-06-16). The health dict is
            # built only from scrapers that actually RAN, so a scraper that
            # silently stops appearing (breaks, is dropped, or is skipped) just
            # vanishes from every check below instead of failing one — the exact
            # class that hid be_steuerrekurs for ~4 months before it was finally
            # classified in KNOWN_DEAD_SOURCES. Surface any REGISTERED scraper
            # missing from a FULL run, except those we intentionally don't expect
            # (KNOWN_DEAD_SOURCES) or that never run as direct scrapers
            # (ENTSCHEIDSUCHE_ONLY). The len>=20 guard skips partial/manual runs
            # (e.g. `run_all_scrapers --only bger`), which legitimately contain
            # few scrapers and must not raise dozens of false WARNs.
            if len(scrapers) >= 20:
                try:
                    # run_scraper.py lives at the repo root; when this script is
                    # invoked as `python3 scripts/check_scraper_freshness.py`,
                    # sys.path[0] is scripts/, not REPO — put REPO on the path.
                    if str(REPO) not in sys.path:
                        sys.path.insert(0, str(REPO))
                    from run_scraper import SCRAPERS as _REGISTERED
                    missing = sorted(
                        set(_REGISTERED) - set(scrapers)
                        - KNOWN_DEAD_SOURCES - ENTSCHEIDSUCHE_ONLY
                        - set(INDEPENDENTLY_SCHEDULED_SOURCES)
                    )
                    for k in missing:
                        alerts.append(
                            f"WARN {k}: registered scraper absent from a full "
                            f"scraper_health.json run (silently skipped or "
                            f"never ran)"
                        )
                    # Independently-scheduled scrapers are excluded above,
                    # so check them on their own terms: the state file is
                    # touched on every run that writes anything.
                    for k, unit in sorted(INDEPENDENTLY_SCHEDULED_SOURCES.items()):
                        sf = REPO / "state" / f"{k}.jsonl"
                        if not sf.exists():
                            alerts.append(
                                f"WARN {k}: independently scheduled ({unit}) but "
                                f"no state file at {sf}")
                            continue
                        age_d = (now - datetime.fromtimestamp(
                            sf.stat().st_mtime, tz=timezone.utc)).days
                        if age_d > INDEPENDENT_STALE_DAYS:
                            alerts.append(
                                f"WARN {k}: independently scheduled ({unit}), "
                                f"no write for {age_d}d — check that timer")
                except Exception as e:
                    alerts.append(f"WARN registry reconciliation failed: {e}")

            # Failed scrapers
            failed = [(k, v) for k, v in scrapers.items() if not v.get("success")]
            for k, v in failed:
                if k in KNOWN_DEAD_SOURCES:
                    continue
                err = v.get("error") or "unknown"
                if k in TOLERATED_PARTIAL_SOURCES:
                    alerts.append(f"WARN {k}: {err} (tolerated upstream limitation)")
                elif k in TUNNEL_DEPENDENT_SOURCES:
                    alerts.append(f"WARN {k}: {err} (MacBook SOCKS tunnel — late-scrapers retry at 10:00 UTC)")
                else:
                    alerts.append(f"FAIL {k}: {err}")

            # Real silent-failure signal: success=true AND finished
            # suspiciously fast for an active court (under 30s) AND new=0.
            # Most legitimate zero-new scrapes take >60s because the portal
            # must be traversed.
            #
            # Exemptions:
            #   - portal_count confirms caught-up or near caught-up:
            #     genuine empty, not a silent skip.
            #   - SILENT_SKIP_EXEMPT_SOURCES: archival/small-chamber feeds
            #     where a fast no-new exit is the normal weekday outcome.
            for k, v in scrapers.items():
                if k in KNOWN_DEAD_SOURCES or k in SILENT_SKIP_EXEMPT_SOURCES:
                    continue
                # isinstance, NOT `(... or 0)`: our_count/duration_s are stored
                # as null for a failed discovery, so .get(k, 0) returns None and
                # `None > 1000` raises TypeError. That aborted the whole health
                # block before health_run_dt was assigned, un-gating the STALE
                # loop into a 35-line storm — six nights in July 2026. The alert
                # body also formats duration_s with :.0f, which `or 0` would not
                # save. Matches the file's own idiom at :304.
                if (v.get("success")
                        and v.get("new_count", 0) == 0
                        and isinstance(v.get("our_count"), int)
                        and v["our_count"] > 1000  # large active corpus
                        and isinstance(v.get("duration_s"), (int, float))
                        and v["duration_s"] < 30):
                    portal_n = v.get("portal_count")
                    our_n = v.get("our_count", 0)
                    if _portal_count_confirms_caught_up(portal_n, our_n):
                        # Caught-up confirmation; not a silent skip.
                        continue
                    alerts.append(
                        f"WARN {k}: scraped in {v.get('duration_s'):.0f}s with 0 new "
                        f"(corpus={v.get('our_count')}) — possible API outage with silent skip"
                    )

            # Run age
            run_at = health.get("run_at")
            if run_at:
                try:
                    run_dt = datetime.fromisoformat(run_at)
                    if run_dt.tzinfo is None:
                        run_dt = run_dt.replace(tzinfo=timezone.utc)
                    else:
                        run_dt = run_dt.astimezone(timezone.utc)
                    health_run_dt = run_dt
                    age_h = (now - run_dt).total_seconds() / 3600
                    if age_h > 36:
                        alerts.append(f"WARN scrape_health.json is {age_h:.0f}h old (cron may be down)")
                except Exception:
                    pass
        except Exception as e:
            alerts.append(f"CRITICAL: cannot parse scraper_health.json: {e}")

    # ── 2. Per-court freshness via health + coverage snapshots ──
    cutoff_normal = now - timedelta(days=args.max_stale_days)
    cutoff_es = now - timedelta(days=args.max_es_stale_days)

    if scrapers:
        for court, scraper_state in scrapers.items():
            if court in KNOWN_DEAD_SOURCES:
                continue
            cutoff = cutoff_es if court in ENTSCHEIDSUCHE_ONLY else cutoff_normal
            if (
                health_run_dt is not None
                and health_run_dt >= cutoff
                and scraper_state.get("success")
            ):
                # A fresh successful run is scrape-attempt evidence. Coverage
                # snapshots lag legitimately when the portal published nothing
                # new, because run_scraper.py only snapshots changed years.
                continue
            last = get_last_scraped(court)
            if not last:
                continue
            try:
                last_dt = datetime.fromisoformat(last).replace(tzinfo=timezone.utc)
            except Exception:
                continue
            if last_dt < cutoff:
                age_d = (now - last_dt).days
                tag = "ES-only" if court in ENTSCHEIDSUCHE_ONLY else "direct"
                # get_last_scraped() reads MAX(source_snapshots.snapshot_date),
                # which only advances when a year's CONTENT changed — not when
                # a run succeeded. Naming it "last_scraped" reported fr_gerichte
                # as 33 days stale for a breakage that began that morning.
                alerts.append(
                    f"STALE {court} ({tag}): last content change {last} ({age_d}d ago)")

    # ── 3. ES cron health ──
    es_ok, es_msg = check_es_cron_health()
    if not es_ok:
        alerts.append(f"FAIL entscheidsuche cron: {es_msg}")

    # ── 4. persistent coverage gaps ──
    try:
        if isinstance(health, dict):
            alerts.extend(check_persistent_gaps(health, today))
    except Exception as e:                                  # never gate on this
        alerts.append(f"WARN gap check failed: {e}")

    # ── 5. corpora that stopped growing ──
    try:
        if isinstance(health, dict):
            alerts.extend(check_stalled_corpus(health, today))
    except Exception as e:                                  # never gate on this
        alerts.append(f"WARN stall check failed: {e}")

    # ── Output ──
    log_path = Path(args.alert_log)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if alerts:
        line_set = [f"{stamp}  {alert}" for alert in alerts]
        with log_path.open("a") as f:
            for line in line_set:
                f.write(line + "\n")
        if not args.quiet:
            print(f"=== {len(alerts)} alert(s) at {stamp} ===")
            for line in line_set:
                print(line)

    if not args.no_ntfy:
        verdict = maybe_dispatch_ntfy(alerts, stamp, Path(args.state_dir))
        if not args.quiet:
            print(f"ntfy: {verdict}")
    elif not alerts and not args.quiet:
        print(f"All checks passed at {stamp}")

    # Always exit 0 on a successful run. The exit-1-on-alert semantics
    # were never wired to a notification path; the dispatch is now
    # active via ntfy + state file. systemd no longer reports this
    # service as failed daily.
    sys.exit(0)


if __name__ == "__main__":
    main()
