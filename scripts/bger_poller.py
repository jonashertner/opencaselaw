#!/usr/bin/env python3
"""
BGer Neuheiten Poller
======================
Lightweight poller that checks the BGer Eurospider "Neuheiten" page
for new decisions. If new decisions are detected, triggers the BGer scraper.

Designed to run every 15 min via systemd timer during business hours.
Single HTTP request per run (~1s), minimal load on BGer servers.

Architecture:
  1. Fetch index_aza.php?date=YYYYMMDD&mode=news (today's date)
  2. Extract docket numbers from page
  3. Compare against last known count (stored in state file)
  4. If new decisions found → run bger scraper
  5. Update state file

State: /tmp/bger_poller_state.json
  {"date": "2026-04-07", "count": 22, "dockets": ["5A_24/2024", ...]}

Usage:
  python3 scripts/bger_poller.py          # check and trigger if needed
  python3 scripts/bger_poller.py --dry-run # check only, don't trigger
  python3 scripts/bger_poller.py --force   # trigger scraper regardless
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parents[1]
STATE_FILE = Path("/tmp/bger_poller_state.json")
LOG_FILE = REPO_DIR / "logs" / "bger_poller.log"

NEUHEITEN_URL = (
    # search.bger.ch — confirmed working 2026-05-06.
    # www.bger.ch silently returned an empty page on this date which made the
    # poller log "0 decisions" all morning while 29 fresh dockets were live
    # at search.bger.ch (the host the rest of the scraper already uses).
    "https://search.bger.ch/ext/eurospider/live/de/php/aza/http/index_aza.php"
    "?date={date}&lang=de&mode=news"
)

DOCKET_RE = re.compile(r"\b\d[A-Z]_\d+/\d{4}\b")

# The aza:// document ids on the Neuheiten page are the authoritative list of
# decisions actually PUBLISHED that day (one id per linked document):
# aza://DD-MM-YYYY-<docket-stem>-<year>, e.g. aza://02-07-2026-1C_146-2025.
AZA_ID_RE = re.compile(r"aza://\d{2}-\d{2}-\d{4}-([0-9A-Za-z_]+)-(\d{4})")


def _extract_feed_dockets(text: str) -> set[str]:
    """Dockets of the decisions actually LINKED on the Neuheiten page.

    Extracts from the aza:// document ids, NOT from a docket regex over the
    whole page: Revisions-/Erläuterungsgesuch entries carry the ATTACKED
    judgment's docket in their title ("Revisionsgesuch gegen das Urteil ...
    5A_402/2026 vom 15. Mai 2026"), and the broad regex swept those up as
    phantom "new decisions" that can never be fetched (2026-07-01: x7
    doc-service-failure alarms for 5A_402/2026; 2026-07-02: 3 more phantoms
    from one Erläuterungsgesuch title — feed had 42 real aza ids but 45
    regex-extracted dockets). Falls back to the broad regex only if the page
    has dockets but no aza ids at all (markup-change safety net: over-
    extraction beats silent blindness)."""
    dockets = {f"{stem}/{year}" for stem, year in AZA_ID_RE.findall(text)}
    if not dockets and DOCKET_RE.search(text):
        logger.warning(
            "Neuheiten page has docket strings but no aza:// ids — markup "
            "change? Falling back to broad docket extraction")
        return set(DOCKET_RE.findall(text))
    return dockets

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _proxy() -> str | None:
    """Residential reverse-SOCKS tunnel for BGer egress, if configured.

    search.bger.ch (Incapsula) hard-blocks the Hetzner datacenter IP, so the
    poller egresses via the Mac's residential tunnel (same as NE/JU). A residential
    IP is NOT challenged, so the proxy path skips the cookie/PoW dance entirely.
    """
    return os.environ.get("BGER_PROXY") or os.environ.get("SCRAPER_PROXY") or None

# quick_publish copies the full decisions.db before inserting (64 GB on
# 2026-06-11, growing daily); that copy+insert took 10m19s under
# publish-tail I/O contention. The old 900 s subprocess.run timeout left
# no headroom — and on expiry subprocess.run SIGKILLs the child,
# bypassing quick_publish's SIGTERM cleanup handler and orphaning the
# multi-GB .quick copy (the 2026-05-04 ENOSPC pattern).
QUICK_PUBLISH_TIMEOUT_S = 2700
QUICK_PUBLISH_GRACE_S = 60

# Escalate when the same docket fails this many consecutive RUNS.
# Denominated in poller runs, not fetch attempts: since 2026-08-31 each
# run retries unfetched documents up to BURST_RETRIES times internally
# (the doc service lags the Neuheiten listing by 30-90 min), so one
# failed run already represents ~40 min of retrying. Two failed runs
# ≈ 2 h of persistent failure — ntfy-alerted, deduped once per day.
FAILING_STREAK_ALERT = 2

# In-run document retries. BGer lists a batch on the Neuheiten page
# ~10:00 UTC but its doc service returns HTTP-200 "nicht gefunden"
# pages for those dockets for 30-90 min (verified 2026-08-31). The
# 15-min TIMER cadence that would absorb this was deliberately
# reverted 2026-05-13 (Incapsula challenge load) — so instead the
# hourly run retries internally: 3 slots x 12 min covers the observed
# lag, costs 3 extra fetches on batch days only, and a still-running
# oneshot simply absorbs the next timer fire (no pile-up).
BURST_RETRIES = 3
BURST_INTERVAL_S = 720
# In-run wall-clock budget: bail out of further burst slots beyond this
# so the run always finishes (and saves state) well inside the unit's
# TimeoutStartSec (raised to 14400 s alongside this change).
BURST_DEADLINE_S = 7200

# Same bundle the scrape/late-scrapers units carry as a systemd drop-in
# (www.bger.ch serves a leaf-only TLS chain since 2026-08-24). Injected
# into child env as defense-in-depth: a unit missing its drop-in fails
# every fetch with CERTIFICATE_VERIFY_FAILED, which surfaced as a week
# of silent "0 new decisions" (found 2026-08-31). Remove with the
# drop-ins when upstream fixes its chain.
CA_BUNDLE = Path("/opt/caselaw/certs/ca-bundle.pem")

logger = logging.getLogger("bger_poller")


def _get_pow_cookies() -> dict:
    """Generate PoW cookies for BGer Eurospider."""
    sys.path.insert(0, str(REPO_DIR))
    from base_scraper import make_pow_cookies
    return make_pow_cookies(16)


def _fetch_neuheiten(date_str: str) -> set[str]:
    """Fetch today's Neuheiten page and extract docket numbers.

    Prefers the residential tunnel (BGER_PROXY/SCRAPER_PROXY): Incapsula does NOT
    challenge a residential IP, so we skip the cookie/PoW dance entirely. Falls
    back to the direct Incapsula-bypass path only when no proxy is set, or the
    proxied fetch errors (the Hetzner IP is hard-blocked as of 2026-06-29, so the
    direct path currently fails — the fallback is there for if/when it recovers).
    """
    url = NEUHEITEN_URL.format(date=date_str)
    proxy = _proxy()
    if proxy:
        try:
            return _fetch_neuheiten_via_proxy(url, proxy)
        except Exception as e:  # connection/tunnel error — try direct as a backstop
            logger.warning("Neuheiten proxy fetch failed (%s) — falling back to direct", e)
    return _fetch_neuheiten_direct(url)


def _fetch_neuheiten_via_proxy(url: str, proxy: str) -> set[str]:
    """Residential egress (reverse-SOCKS tunnel), plain GET, no cookies or PoW.

    The residential IP is USUALLY not challenged — but on 2026-09-28 Incapsula
    refused it from ~05:00 to ~10:30 UTC (403/502/503 on BGE, BGer and here), so
    the body is classified rather than trusted:

    - whitespace only: BGer's genuine answer for a date with nothing listed yet
      (a single "\\n", measured for a Sunday and for tomorrow on 2026-09-28);
    - the list itself: aza:// ids / dockets, or the "neu aufgenommenen
      Entscheide" header;
    - anything else is not a Neuheiten page (challenge/block page) and raises,
      so it is reported as a failed fetch instead of a quiet "0 decisions".
    """
    import requests
    session = requests.Session()
    session.headers["User-Agent"] = _UA
    session.proxies = {"http": proxy, "https": proxy}
    r = session.get(url, timeout=45)
    r.raise_for_status()
    return _classify_neuheiten_page(r.text)


NEUHEITEN_LIST_MARKER = "neu aufgenommenen Entscheide"


def _classify_neuheiten_page(text: str) -> set[str]:
    """Dockets of a real Neuheiten answer; raises on a page that is not one."""
    if not text.strip():
        return set()
    dockets = _extract_feed_dockets(text)
    if dockets or NEUHEITEN_LIST_MARKER in text:
        return dockets
    raise RuntimeError(
        f"not a Neuheiten page ({len(text)} bytes, no list, no "
        f"'{NEUHEITEN_LIST_MARKER}' header) — challenge or block page")


def _fetch_neuheiten_direct(url: str) -> set[str]:
    """Direct (datacenter IP): harvest Incapsula cookies + PoW. Works only while
    the IP is not hard-blocked; raises loudly on a persistent block."""
    import requests
    sys.path.insert(0, str(REPO_DIR))
    from incapsula_bypass import IncapsulaCookieManager

    session = requests.Session()
    session.headers["User-Agent"] = _UA
    incap_mgr = IncapsulaCookieManager(cache_dir=REPO_DIR / "state")
    session.cookies.update(incap_mgr.get_cookies("search.bger.ch"))
    session.cookies.update(_get_pow_cookies())

    r = session.get(url, timeout=30)
    r.raise_for_status()
    if IncapsulaCookieManager.is_incapsula_blocked_response(r):
        logger.warning("Incapsula block detected — refreshing cookies and retrying")
        session.cookies.clear()
        session.cookies.update(incap_mgr.refresh_cookies("search.bger.ch"))
        session.cookies.update(_get_pow_cookies())
        r = session.get(url, timeout=30)
        r.raise_for_status()
        if IncapsulaCookieManager.is_incapsula_blocked_response(r):
            raise RuntimeError(
                "Incapsula still blocking after cookie refresh — manual "
                "intervention needed (browser automation not bypassing)"
            )
    return _extract_feed_dockets(r.text)


def _is_workday(d=None) -> bool:
    """Mon-Fri. BGer does not publish on weekends, so an empty Neuheiten feed is
    only suspicious on a workday."""
    from datetime import date as _date
    return (d or _date.today()).weekday() < 5


# BGer publishes its Neuheiten around 10:00 UTC; before that an empty feed is
# normal (not yet published), not a blocked fetch. Alarm only once this window
# has passed (with an hour of margin).
BGER_PUBLICATION_HOUR_UTC = 11


def _empty_feed_is_anomalous(now=None) -> bool:
    """True when an empty Neuheiten feed signals a blocked fetch: a workday AND
    past BGer's ~10:00 UTC publication window. Before ~11:00 UTC an empty feed is
    the normal pre-publication state — alarming there produced ~5 false ERROR
    alerts every workday morning (05:00-09:00 UTC)."""
    now = now or datetime.now(timezone.utc)
    return _is_workday(now.date()) and now.hour >= BGER_PUBLICATION_HOUR_UTC


def _alert_ntfy(title: str, msg: str, tags: str = "warning") -> None:
    """Fire-and-forget ntfy push; failures are logged, never raised."""
    try:
        import requests
        topic = os.environ.get("NTFY_TOPIC", "opencaselaw-prod")
        base = os.environ.get("NTFY_URL", "https://ntfy.sh")
        requests.post(f"{base}/{topic}", data=msg.encode("utf-8"),
                      headers={"Title": title, "Priority": "high",
                               "Tags": tags},
                      timeout=10)
    except Exception as e:
        logger.warning("ntfy alert failed: %s", e)


def _alert_empty_neuheiten(today_iso: str) -> None:
    """Workday + zero dockets ⇒ the fetch is almost certainly blocked (the feed is
    never empty on a business day). Surface it loudly — the silent count:0 path is
    exactly what hid a multi-week BGer gap in 2026-06. Caller dedups per day."""
    msg = (f"BGer Neuheiten returned 0 dockets on a workday ({today_iso}) — fetch "
           f"likely Incapsula-blocked. Check the reverse-SOCKS tunnel / BGER_PROXY.")
    logger.error(msg)
    _alert_ntfy("BGer poller: empty Neuheiten on a workday", msg,
                tags="warning,rotating_light")


def _alert_fetch_failure(today_iso: str, err: Exception, now=None) -> bool:
    """ntfy once per day when the Neuheiten fetch itself fails after BGer's
    publication window (workday, >= 11:00 UTC).

    The empty-feed alarm covers a fetch that returns nothing; a fetch that
    raises used to be logged only. That was already true for a 503, and since
    block pages are now raised rather than read as "0 decisions", staying quiet
    here would have made the blocked case LESS visible. Before the window a
    failure only logs, like the empty feed (morning noise). Returns True when
    an alert was sent. The state is re-saved unchanged except for the marker.
    """
    if not _empty_feed_is_anomalous(now):
        return False
    state = _load_state()
    alerts = dict(state.get("alerts", {}))
    if alerts.get("fetch") == today_iso:
        return False
    msg = (f"BGer Neuheiten fetch failed on a workday ({today_iso}): {err}. "
           f"Check the reverse-SOCKS tunnel / BGER_PROXY; the 01:00 scrape is the backstop.")
    _alert_ntfy("BGer poller: Neuheiten fetch failing", msg,
                tags="warning,rotating_light")
    alerts["fetch"] = today_iso
    _save_state(state.get("date") or today_iso, set(state.get("dockets", [])),
                dict(state.get("failing", {})), bool(state.get("pending_publish", False)),
                alerts)
    return True


def _load_state() -> dict:
    """Load last known state."""
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    # pending_publish defaults False on a missing state file (/tmp dies on
    # reboot): a reboot can strand scraped-but-unpublished JSONL rows until
    # the 03:30 nightly rebuild sweeps them — accepted, the alternative
    # (always-publish) re-copies the 60+ GB decisions.db every poll.
    return {"date": "", "count": 0, "dockets": []}


def _save_state(today: str, dockets: set[str],
                failing: dict[str, int] | None = None,
                pending: bool = False,
                alerts: dict | None = None):
    """Save current state.

    ``failing`` maps docket → consecutive runs it has failed ingestion
    (doc-service error pages, or a same-docket second ruling the scraper
    could not identify — see _held_under_docket_id). Dockets recover by
    dropping out of the dict.
    ``pending`` records scraped-but-unpublished JSONL rows (quick_publish
    failed or was lock-skipped) so the next run sweeps them even when the
    Neuheiten page shows nothing new. ``alerts`` holds per-day ntfy dedup
    markers ({"empty": iso-date, "streak": iso-date}).
    """
    STATE_FILE.write_text(json.dumps({
        "date": today,
        "count": len(dockets),
        "dockets": sorted(dockets),
        "failing": failing or {},
        "pending_publish": bool(pending),
        "alerts": alerts or {},
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }))


def _ingestion_window_seconds(now: datetime | None = None,
                              floor: int = 10800) -> int:
    """Lookback for the JSONL truth check: back to the start of the UTC
    day, never less than ``floor``.

    The poller's unit of accounting is the day (a Neuheiten page lists one
    day's publications, state is kept per day), and quick_publish is
    routinely deferred behind the full build's lock for three to four
    hours. With a fixed 3 h lookback, rows the scraper fetched in the
    morning had aged out by the time the publish went through, so the
    poller reported them "NOT in JSONL" every hour and raised a false
    ingestion-failure streak (2026-09-04: 26 dockets fetched 10:43,
    published 14:21, flagged 14:59, 15:39 and 16:39).
    """
    now = now or datetime.now(timezone.utc)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return max(floor, int((now - midnight).total_seconds()) + 3600)


def _recently_ingested_dockets(window_seconds: int = 3600) -> set[str]:
    """Return docket_numbers in bger.jsonl whose ``scraped_at`` is within
    the last ``window_seconds`` seconds.

    The poller uses this to confirm that a triggered scraper actually
    saved its targets to JSONL. Dockets that BGer's document-service
    returns as error pages (the chronic failure mode 2026-05-08 onward)
    never make it into JSONL — those should NOT be marked "seen" so
    the next poll retries them. Naturally bounded: BGer rotates older
    decisions off the Neuheiten feed, so a permanent doc-service
    failure stops being retried within ~24h.

    Reads the last ``tail_bytes`` of the file (default 8 MB). A typical
    BGer decision JSONL row is 30–80 KB (full_text + metadata), so 8 MB
    holds ~100–200 rows — comfortable for any plausible poller batch
    (Neuheiten exposes ~30 dockets / day at peak). Earlier 256 KB cap
    silently truncated batches with substantial text bodies and made
    the success/fail accounting wrong (2026-05-08: 14 of 27 ingested
    rows were misreported as 'not in JSONL').
    """
    p = REPO_DIR / "output" / "decisions" / "bger.jsonl"
    if not p.exists():
        return set()
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=window_seconds)
    tail_bytes = 8 * 1024 * 1024  # 8 MB
    out: set[str] = set()
    try:
        with open(p, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            seek_back = min(tail_bytes, size)
            f.seek(size - seek_back, 0)
            chunk = f.read().decode("utf-8", errors="replace")
        # Drop first partial line if we seeked into the middle of one.
        if seek_back == tail_bytes and "\n" in chunk:
            chunk = chunk.split("\n", 1)[1]
        for line in chunk.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            sa = d.get("scraped_at")
            dn = d.get("docket_number")
            if not sa or not dn:
                continue
            try:
                sa_dt = datetime.fromisoformat(sa.replace("Z", "+00:00"))
            except Exception:
                continue
            if sa_dt >= cutoff:
                out.add(dn)
    except Exception as e:
        logger.warning("could not scan bger.jsonl tail: %s", e)
    return out


def _parse_scraper_new_count(output: str) -> int:
    """Extract N from run_scraper's ``[bger] Done. +N new, ...`` summary.

    run_scraper logs via a bare logging.StreamHandler, which writes to
    STDERR — callers must pass stdout and stderr combined. Scanning
    stdout alone made the poller log "0 new decisions" on every run
    while the scraper was genuinely ingesting (second regression of
    this kind: the 2026-05-07 refactor broke the regex; the fix
    corrected the regex but kept reading the wrong stream).
    """
    m = re.search(r"Done\.\s*\+(\d+)\s+new\b", output)
    return int(m.group(1)) if m else 0


def _parse_quick_publish_output(output: str) -> tuple[int, bool]:
    """Return (inserted_count, skipped_due_to_lock) from quick_publish
    output (stdout + stderr combined — quick_publish also logs via
    StreamHandler → stderr)."""
    inserted = 0
    m = re.search(r"Inserted (\d+)/", output)
    if m:
        inserted = int(m.group(1))
    # quick_publish exits rc=0 with this exact phrase when publish.py
    # holds the exclusive lock.
    skipped = ("skipping quick_publish" in output
               or "Full publish.py is running" in output)
    return inserted, skipped


def _held_under_docket_id(dockets: set[str]) -> set[str]:
    """Subset of ``dockets`` whose docket-keyed id is already in the
    scraper state (state/bger.jsonl, one id per line; ~1.6 MB).

    A docket that is listed on Neuheiten, NOT in JSONL after the scraper
    ran, but already held is not a doc-service failure: BGer published a
    second ruling under a docket we hold (recusal ruling → final judgment,
    2C_532/2025 on 2026-09-03). The scraper fetches those under a -D<date>
    id once its date sidecar is seeded (backfill_bger_docket_collisions.py);
    in legacy mode they are skipped as "known" and every burst retry is
    wasted. Distinguishing the two keeps the alert honest.
    """
    if not dockets:
        return set()
    p = REPO_DIR / "state" / "bger.jsonl"
    try:
        ids = set(p.read_text().split())
    except OSError:
        return set()
    return {d for d in dockets if "bger_" + d.replace("/", "_") in ids}


def _update_failing_streaks(prev_failing: dict[str, int],
                            unsaved: set[str]) -> dict[str, int]:
    """Consecutive-poll ingestion-failure count per docket.

    Dockets still failing increment; dockets that recovered (or rotated
    off the Neuheiten page) drop out.
    """
    return {d: int(prev_failing.get(d, 0)) + 1 for d in sorted(unsaved)}


def _communicate_graceful(proc: subprocess.Popen, timeout_s: float,
                          grace_s: float = 60.0):
    """communicate() with SIGTERM-then-SIGKILL on timeout.

    Returns (out, err, returncode, timed_out). SIGTERM first so the
    child's cleanup handlers run — quick_publish removes its 60+ GB
    .quick copy on SIGTERM; a bare SIGKILL (what subprocess.run's
    timeout does) orphans it on disk.
    """
    try:
        out, err = proc.communicate(timeout=timeout_s)
        return out, err, proc.returncode, False
    except subprocess.TimeoutExpired:
        proc.terminate()
        try:
            out, err = proc.communicate(timeout=grace_s)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, err = proc.communicate()
        return out, err, proc.returncode, True


def _qp_needed(scrape_rc_ok: bool, new_count: int, force: bool) -> bool:
    """quick_publish is skippable ONLY when the scrape succeeded cleanly
    and landed nothing new and no earlier rows await publishing. On a
    failed/timed-out scrape JSONL may hold rows worth publishing — the
    2026-05-04 stall lesson — so those always publish."""
    return force or new_count > 0 or not scrape_rc_ok


def _next_pending(prev: bool, qp_ran: bool, qp_ok: bool) -> bool:
    """pending_publish transition: a completed quick_publish clears it, a
    failed one sets it; when qp was skipped (only possible after a clean
    +0-new unforced scrape — _qp_needed) the flag carries forward."""
    if qp_ran:
        return not qp_ok
    return prev


def _late_scrapers_running() -> bool:
    """True while opencaselaw-late-scrapers.service is active: it drives
    its own bger stream through the same residential tunnel, and two
    concurrent bger streams grind both to a halt (2026-07-02: the
    poller's scrape hit its 600 s cap mid-batch). Burst slots skip
    while it runs. Fail-open on probe errors."""
    try:
        r = subprocess.run(
            ["systemctl", "is-active", "--quiet",
             "opencaselaw-late-scrapers.service"],
            timeout=10,
        )
        return r.returncode == 0
    except Exception:
        return False


def _run_quick_publish() -> tuple[bool, int]:
    """Run quick_publish.py; return (qp_ok, inserted_count).

    qp_ok is False on timeout, non-zero exit, or lock-skip — the caller
    must not mark dockets "seen" then, or scraped-but-unpublished rows
    sit in JSONL until the nightly rebuild with nothing retrying them.
    """
    quick_pub = REPO_DIR / "scripts" / "quick_publish.py"
    if not quick_pub.exists():
        return False, 0
    cmd = [sys.executable, str(quick_pub), "--courts", "bger"]
    logger.info("Quick-publishing: %s", " ".join(cmd))
    qp_proc = subprocess.Popen(
        cmd,
        cwd=str(REPO_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    qp_out, qp_err, qp_rc, qp_timed_out = _communicate_graceful(
        qp_proc, QUICK_PUBLISH_TIMEOUT_S, QUICK_PUBLISH_GRACE_S)
    combined = (qp_out or "") + "\n" + (qp_err or "")
    if qp_timed_out:
        logger.error(
            "Quick-publish exceeded %ds — sent SIGTERM (its cleanup "
            "handler removes the .quick copy); dockets stay unmarked "
            "and the next poll retries. Last output: %s",
            QUICK_PUBLISH_TIMEOUT_S, combined[-300:].strip(),
        )
        return False, 0
    if qp_rc != 0:
        logger.error("Quick-publish failed (exit %d): %s",
                     qp_rc, (qp_err or "")[-300:])
        return False, 0
    inserted_count, skipped_due_to_lock = _parse_quick_publish_output(combined)
    if skipped_due_to_lock:
        # Dockets stay unmarked (qp_ok=False), so the next run
        # re-detects them, re-runs the scraper (idempotent append:
        # +0 new) and retries quick_publish.
        logger.warning(
            "Quick-publish SKIPPED: full publish.py or another "
            "quick_publish holds a lock — freshly-scraped BGer dockets "
            "remain in bger.jsonl; the next run retries once the lock "
            "releases"
        )
        return False, inserted_count
    logger.info("Quick-publish: inserted %d new decisions", inserted_count)
    return True, inserted_count


def _trigger_scraper(force_qp: bool = True):
    """Run the BGer scraper, then quick-publish new decisions into FTS5.

    The scraper is allowed up to 10 minutes to run. If it stalls past
    that (the typical failure mode is camoufox returning partial
    Incapsula cookies under heavy challenge state, which then causes
    bger.py's retry loop to grind without progress), we kill it and
    still run quick_publish on whatever decisions it managed to write
    to JSONL before stalling. This is essential because today's stall
    showed the previous code path was: timeout → exception → poller
    crashes → quick_publish never fires → decisions sit unpublished.
    """
    # Step 1: Scrape — capped at 10 min via Popen + process-group kill
    # (subprocess.run with timeout doesn't reliably kill grandchildren
    # like camoufox's headless browser).
    import os
    import signal as _sig
    # --neuheiten-only: skip the AZA search backfill step, which was
    # the stall site under heavy Imperva challenge today (3 sockets in
    # CLOSE_WAIT, 16 min stuck). Neuheiten alone covers the daily delta
    # the poller is responsible for; manual `run_scraper.py bger` (no
    # flag) remains the path for backfill + nightly publish.
    cmd = [
        sys.executable, str(REPO_DIR / "run_scraper.py"),
        "bger", "--neuheiten-only",
    ]
    logger.info("Triggering BGer scraper: %s", " ".join(cmd))
    SCRAPER_TIMEOUT_S = 600
    # Route the scraper's egress through the same residential tunnel (the direct
    # datacenter IP is Incapsula-blocked). base_scraper reads SCRAPER_PROXY.
    scraper_env = dict(os.environ)
    _p = _proxy()
    if _p:
        scraper_env["SCRAPER_PROXY"] = _p
    # Defense-in-depth for the leaf-only bger.ch TLS chain: even if this
    # unit loses its ca-bundle drop-in again, the child still verifies.
    if "REQUESTS_CA_BUNDLE" not in scraper_env and CA_BUNDLE.exists():
        scraper_env["REQUESTS_CA_BUNDLE"] = str(CA_BUNDLE)
    proc = subprocess.Popen(
        cmd,
        cwd=str(REPO_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
        env=scraper_env,
    )
    try:
        out, err = proc.communicate(timeout=SCRAPER_TIMEOUT_S)
        rc = proc.returncode
        timed_out = False
    except subprocess.TimeoutExpired:
        logger.warning(
            "BGer scraper exceeded %ds — process-group SIGKILL",
            SCRAPER_TIMEOUT_S,
        )
        try:
            os.killpg(os.getpgid(proc.pid), _sig.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            out, err = proc.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            out, err = "", ""
        rc = -1
        timed_out = True

    new_count = 0
    if rc == 0:
        # Combined streams: run_scraper's "Done. +N new" line goes to
        # stderr (bare StreamHandler), not stdout — see
        # _parse_scraper_new_count.
        new_count = _parse_scraper_new_count(
            (out or "") + "\n" + (err or ""))
        logger.info("BGer scraper completed: %d new decisions", new_count)
    elif timed_out:
        logger.warning(
            "BGer scraper killed after timeout — proceeding to quick-publish "
            "on whatever data is in JSONL"
        )
    else:
        logger.error(
            "BGer scraper failed (exit %d): %s", rc, (err or "")[-500:],
        )
        # No `return` here — even on non-timeout failures, JSONL may
        # contain decisions worth publishing.

    # Step 2: Quick-publish — skippable only when the scrape succeeded
    # cleanly with nothing new and nothing pending from earlier runs
    # (burst retries would otherwise re-copy the 60+ GB decisions.db
    # every 12 minutes for nothing).
    scrape_rc_ok = (rc == 0)
    new_count = new_count if scrape_rc_ok else 0
    inserted_count = 0
    qp_ran = False
    qp_ok = False
    if _qp_needed(scrape_rc_ok, new_count, force_qp):
        qp_ran = True
        qp_ok, inserted_count = _run_quick_publish()
    else:
        logger.info("Quick-publish skipped: clean scrape, 0 new, none pending")

    return qp_ok, new_count, qp_ran, inserted_count


PUBLISH_UNIT = "opencaselaw-publish.service"


def _full_publish_running() -> bool:
    """True while the nightly full publish is active OR in its post-swap
    I/O-heavy tail (parquet / HuggingFace upload / reference-graph / stats).

    Why ActiveState and NOT ``systemctl is-active --quiet``: opencaselaw-publish
    is a *oneshot* unit, so for the entire ~6-15 h run its ActiveState is
    "activating", which ``is-active --quiet`` reports as NOT active (exit 3) —
    a false negative that would silently defeat this guard. Why not the publish
    *lock*: publish.py releases it at OCL_SWAP_DONE, before the I/O-heavy tail.
    Reading ActiveState directly covers the whole pipeline incl. the tail.
    Fail-open (return False) on any probe error: never block the dashboard
    refresh on an absent systemd or a probe timeout."""
    try:
        r = subprocess.run(
            ["systemctl", "show", "-p", "ActiveState", "--value", PUBLISH_UNIT],
            capture_output=True, text=True, timeout=10,
        )
    except Exception as e:  # FileNotFoundError (no systemd), timeout, etc.
        logger.warning("could not probe %s state: %s", PUBLISH_UNIT, e)
        return False
    return r.stdout.strip() in {"active", "activating", "reloading", "deactivating"}


def _maybe_update_stats(new_decisions: int) -> None:
    """Regenerate stats.json + git push when quick_publish inserted rows."""
    if new_decisions <= 0:
        logger.info("No new decisions inserted — skipping stats.json refresh")
        return

    # Don't fire the ~22-min generate_stats while the nightly full publish is
    # running: its parquet/HF/graph/stats tail saturates the data volume and
    # our scan over the ~69 GB decisions.db goes D-state (the build/serve
    # I/O-coupling stall the post-mortems warn about). The publish regenerates
    # stats.json itself (Step 5), and the +N rows are already searchable via
    # quick_publish, so skipping here loses only a little dashboard freshness
    # on the rare publish-overlap polls.
    if _full_publish_running():
        logger.info(
            "Full publish (%s) is running — skipping stats.json refresh "
            "(+%d new rows already searchable via quick_publish; the nightly "
            "Step 5 will regenerate stats.json)",
            PUBLISH_UNIT, new_decisions,
        )
        return

    # Single-flight lock: a 22-min generate_stats can outlive the 15-min
    # poll interval. The next poll's stats step would race with this
    # one; flock makes it skip cleanly.
    import fcntl
    lock_path = Path("/tmp/opencaselaw-stats.lock")
    lock_fd = open(lock_path, "w")
    try:
        try:
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            logger.warning(
                "Another stats.json refresh is in progress (lock held) — "
                "skipping; the running instance will catch up our %d new rows",
                new_decisions,
            )
            return

        # Step 3a: regenerate stats.json. --no-interesting-stats keeps
        # the heavy weekly block from the on-disk file (the weekly timer
        # owns it) and re-runs only the daily/recent blocks.
        gs = REPO_DIR / "generate_stats.py"
        if not gs.exists():
            logger.warning("generate_stats.py not found — cannot refresh stats.json")
            return
        gs_cmd = [sys.executable, str(gs), "--no-interesting-stats"]
        logger.info("Regenerating stats.json (~22 min): %s", " ".join(gs_cmd))
        gs_t0 = time.time()
        gs_res = subprocess.run(
            gs_cmd,
            cwd=str(REPO_DIR),
            capture_output=True,
            text=True,
            timeout=2700,  # 45 min cap (steady-state ~22 min)
        )
        if gs_res.returncode != 0:
            logger.error(
                "generate_stats failed (exit %d): %s",
                gs_res.returncode, (gs_res.stderr or "")[-500:],
            )
            return
        logger.info(
            "stats.json regenerated in %.0fs",
            time.time() - gs_t0,
        )

        # Step 3b: only push if there's actually a diff (the generator
        # rewrites generated_at every run, so this is essentially always
        # true after a content change — cheap to verify).
        diff_check = subprocess.run(
            ["git", "diff", "--quiet", "docs/stats.json"],
            cwd=str(REPO_DIR), capture_output=True, text=True, timeout=30,
        )
        if diff_check.returncode == 0:
            logger.info("No diff in docs/stats.json — nothing to push")
            return

        # Step 3c: commit + push to GitHub Pages.
        for cmd in (
            ["git", "add", "docs/stats.json"],
            [
                "git",
                "-c", "user.name=opencaselaw-bot",
                "-c", "user.email=bot@opencaselaw.ch",
                "commit", "-m",
                f"Update stats.json — BGer poller +{new_decisions} new decisions",
            ],
            ["git", "push", "origin", "main"],
        ):
            r = subprocess.run(
                cmd,
                cwd=str(REPO_DIR),
                capture_output=True,
                text=True,
                timeout=300,
            )
            if r.returncode != 0:
                logger.error(
                    "git step failed (%s): %s",
                    " ".join(cmd[:3]), (r.stderr or "")[-300:],
                )
                return
        logger.info(
            "stats.json pushed (+%d decisions) — opencaselaw.ch refreshes in 1-2 min",
            new_decisions,
        )
    finally:
        try:
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        lock_fd.close()


def main():
    parser = argparse.ArgumentParser(description="BGer Neuheiten poller")
    parser.add_argument("--dry-run", action="store_true", help="Check only, don't trigger scraper")
    parser.add_argument("--force", action="store_true", help="Trigger scraper regardless")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        # Only StreamHandler — systemd captures stdout to the log file.
        # Adding FileHandler would cause duplicate lines.
    )

    today = date.today().strftime("%Y%m%d")
    today_iso = date.today().isoformat()
    run_start = time.monotonic()

    def _stats_budgeted(inserted: int) -> None:
        """Stats regen, last and optional: never let it crash the run
        (its subprocess.run timeouts are uncaught) and never let it push
        the run past TimeoutStartSec (elapsed guard)."""
        elapsed = time.monotonic() - run_start
        if elapsed > 10800:
            logger.warning(
                "skipping stats regen — %.0f s elapsed, too close to the "
                "unit timeout; the nightly rebuild refreshes stats anyway",
                elapsed)
            return
        try:
            _maybe_update_stats(inserted)
        except Exception as e:
            logger.warning("stats regen failed (non-fatal): %s", e)

    if args.force:
        logger.info("Force mode — triggering scraper")
        f_qp_ok, _n, _ran, f_inserted = _trigger_scraper(force_qp=True)
        if not f_qp_ok:
            # Persist the debt UNDER TODAY'S DATE so the next timer
            # run's rollover cannot drop it (stale-date saves were
            # silently discarded — reverify 2026-08-31). Carried
            # fields reset when the state was from another day.
            st = _load_state()
            stale = st.get("date") != today_iso
            _save_state(today_iso,
                        set() if stale else set(st.get("dockets", [])),
                        failing={} if stale else dict(st.get("failing", {})),
                        pending=True,
                        alerts=dict(st.get("alerts", {})))
        _stats_budgeted(f_inserted)
        return

    # Fetch current Neuheiten
    try:
        current_dockets = _fetch_neuheiten(today)
    except Exception as e:
        logger.error("Failed to fetch Neuheiten: %s", e)
        _alert_fetch_failure(today_iso, e)
        return

    logger.info("Neuheiten %s: %d decisions", today_iso, len(current_dockets))

    # State FIRST — the empty-feed dedup below reads ``alerts`` (review
    # 2026-08-31: reading it before this load was an UnboundLocalError
    # that crashed the run in exactly the blocked-fetch scenario the
    # alert exists for, and the reload here clobbered the dedup marker).
    state = _load_state()
    prev_dockets = set(state.get("dockets", []))
    prev_failing: dict[str, int] = dict(state.get("failing", {}))
    pending_publish = bool(state.get("pending_publish", False))
    alerts: dict = dict(state.get("alerts", {}))

    # Silent-failure guard: the feed is never empty on a business day AFTER BGer's
    # ~10:00 UTC publication, so 0 dockets then means the fetch is blocked (not "no
    # new decisions"). Alert instead of quietly recording count:0 — that path hid a
    # multi-week gap. The publication-window guard drops the daily pre-10:00 noise.
    if not current_dockets and _empty_feed_is_anomalous():
        if alerts.get("empty") != today_iso:
            _alert_empty_neuheiten(today_iso)
            alerts["empty"] = today_iso
        else:
            logger.error("Neuheiten still empty on a workday (%s) — "
                         "ntfy already sent today", today_iso)

    if state.get("date") != today_iso:
        # New day — start fresh (don't carry over yesterday's dockets;
        # failing dockets rotate off the polled Neuheiten page with the
        # date, so their streaks are stale too — the nightly full scrape
        # is the backstop that still retries them via older date pages)
        prev_dockets = set()
        prev_failing = {}
        # The 03:30 nightly full rebuild published every JSONL row; a
        # stale flag would fire a pointless 60+GB sweep into the build
        # window's I/O.
        pending_publish = False

    new_dockets = current_dockets - prev_dockets

    if new_dockets:
        logger.info(
            "NEW decisions detected: %d (%s)",
            len(new_dockets), ", ".join(sorted(new_dockets)[:5]),
        )
        if args.dry_run:
            logger.info("[dry-run] Would trigger BGer scraper")
            # Save prev (NOT current): a dry-run observes without
            # consuming the day's detection — saving current made every
            # later real run see nothing new (pre-existing bug).
            _save_state(today_iso, prev_dockets, failing=prev_failing,
                        pending=pending_publish, alerts=alerts)
        else:
            total_inserted = 0

            def _attempt() -> tuple[bool, bool, set[str]]:
                """One scrape+maybe-publish attempt. Returns
                (qp_ok, db_current, ingested_now).

                ingested_now is JSONL truth, computed unconditionally
                (cheap tail scan — it does not read the DB).
                db_current means the live DB reflects JSONL: either
                quick_publish completed, or it was legitimately skipped
                (clean scrape, nothing new, nothing pending) — a skip is
                NOT a failure (review 2026-08-31: conflating them wiped
                the seen-cache, froze failing streaks and killed the
                streak alert in its primary scenario).
                """
                nonlocal pending_publish, total_inserted, published
                nonlocal ingested_all
                qp_ok, _sc_new, qp_ran, inserted = _trigger_scraper(
                    force_qp=pending_publish)
                total_inserted += inserted
                pending_publish = _next_pending(
                    pending_publish, qp_ran, qp_ok)
                ingested_now = _recently_ingested_dockets(
                    window_seconds=_ingestion_window_seconds())
                # Accumulate: a fixed lookback window must not age
                # attempt-1 rows out of the accounting late in a long run.
                ingested_all.update(ingested_now)
                if qp_ok:
                    # Published accumulates ACROSS attempts: a later
                    # skip slot must not un-see these.
                    published |= current_dockets & ingested_now
                # Rows in JSONL that no completed publish covered — ours
                # or another writer's (late-scrapers shares bger.jsonl;
                # this also backstops a regressed new-count regex, which
                # has broken twice): force the next publish.
                if ((ingested_now & new_dockets) - published) and not qp_ok:
                    pending_publish = True
                db_current = qp_ok or not qp_ran
                return qp_ok, db_current, set(ingested_all)

            published: set[str] = set()
            ingested_all: set[str] = set()
            qp_ok, db_current, ingested_recent = _attempt()
            unsaved = new_dockets - ingested_recent

            # Burst retries: the doc service lags its own Neuheiten
            # listing (see BURST_RETRIES above). Retry the unfetched
            # documents within this run instead of waiting an hour —
            # skipping any slot while late-scrapers drives its own
            # bger stream through the shared tunnel, and bailing out
            # before the systemd TimeoutStartSec budget (state is
            # saved after every attempt so a kill loses one slot, not
            # the run).
            attempt = 0
            while unsaved and attempt < BURST_RETRIES:
                if (time.monotonic() - run_start) > BURST_DEADLINE_S:
                    logger.warning(
                        "burst budget exhausted (%.0f s) — leaving %d "
                        "dockets for the next hourly run",
                        time.monotonic() - run_start, len(unsaved))
                    break
                attempt += 1
                _save_state(today_iso, prev_dockets | published,
                            failing=prev_failing,
                            pending=pending_publish, alerts=alerts)
                logger.info(
                    "burst %d/%d: %d dockets pending, retrying in %d s",
                    attempt, BURST_RETRIES, len(unsaved), BURST_INTERVAL_S)
                time.sleep(BURST_INTERVAL_S)
                if _late_scrapers_running():
                    # Start-of-slot check only: a collision where
                    # late-scrapers starts mid-slot is bounded to
                    # minutes and both sides tolerate a slow stream.
                    logger.info(
                        "late-scrapers active — skipping burst slot %d",
                        attempt)
                    continue
                qp_ok, db_current, ingested_recent = _attempt()
                unsaved = new_dockets - ingested_recent

            seen_state = prev_dockets | published
            failing = (_update_failing_streaks(prev_failing, unsaved)
                       if db_current else dict(prev_failing))
            _save_state(today_iso, seen_state, failing=failing,
                        pending=pending_publish, alerts=alerts)
            if unsaved and db_current:
                held = _held_under_docket_id(unsaved)
                logger.warning(
                    "%d/%d new dockets NOT in JSONL — %d already held under "
                    "a docket-keyed id (a second ruling under a known "
                    "docket; the scraper needs its date sidecar seeded, see "
                    "backfill_bger_docket_collisions.py), %d likely BGer "
                    "doc-service lag/error pages; will retry next poll. "
                    "First few: %s",
                    len(unsaved), len(new_dockets), len(held),
                    len(unsaved) - len(held),
                    ", ".join(sorted(unsaved)[:5]),
                )
                persistent = {d: n for d, n in failing.items()
                              if n >= FAILING_STREAK_ALERT}
                if persistent:
                    def _tag(d: str, n: int) -> str:
                        return (f"{d} (x{n}, held under docket id)"
                                if d in held else f"{d} (x{n})")
                    msg = (
                        "BGer ingestion failure persisting >=%d "
                        "consecutive runs (each run bursts %d retries): "
                        "%s — listed on Neuheiten but not ingested. 'held "
                        "under docket id' = a second ruling under a docket "
                        "we already hold (not an error page; seed the "
                        "scraper's date sidecar). Otherwise BGer's doc "
                        "service keeps returning error pages" % (
                            FAILING_STREAK_ALERT, BURST_RETRIES,
                            ", ".join(_tag(d, n)
                                      for d, n in sorted(persistent.items())),
                        ))
                    logger.error(msg)
                    if alerts.get("streak") != today_iso:
                        _alert_ntfy(
                            "BGer poller: dockets stuck for hours", msg)
                        alerts["streak"] = today_iso
                        _save_state(today_iso, seen_state, failing=failing,
                                    pending=pending_publish, alerts=alerts)
            elif unsaved:
                logger.warning(
                    "quick_publish FAILED (not skipped) — %d dockets "
                    "left unmarked with pending_publish=%s; the next run "
                    "re-runs the scraper (idempotent) and sweeps. "
                    "First few: %s",
                    len(unsaved), pending_publish,
                    ", ".join(sorted(unsaved)[:5]),
                )
            _stats_budgeted(total_inserted)
    else:
        if pending_publish and not args.dry_run:
            # Rows scraped by an earlier run whose quick_publish failed
            # or was lock-skipped: sweep them now even though the
            # Neuheiten page shows nothing new.
            logger.info("pending publish from an earlier run — sweeping")
            qp_ok, inserted = _run_quick_publish()
            if qp_ok:
                pending_publish = False
            # Save FIRST: a stats-regen crash/timeout must not undo a
            # completed 60+GB sweep (or the alert dedup markers).
            _save_state(today_iso, current_dockets, failing=prev_failing,
                        pending=pending_publish, alerts=alerts)
            _stats_budgeted(inserted)
        else:
            logger.info("No new decisions since last check")
            _save_state(today_iso, current_dockets, failing=prev_failing,
                        pending=pending_publish, alerts=alerts)


if __name__ == "__main__":
    main()
