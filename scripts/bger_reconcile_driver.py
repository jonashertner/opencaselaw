#!/usr/bin/env python3
"""Run the BGer count-reconciliation sweep year by year, unattended, only
inside the windows where no other writer of output/decisions/bger.jsonl runs.

Runbook: runbooks/bger_reconcile_2026-11.md. Each year is the same command the
template unit opencaselaw-bger-reconcile@.service runs
(`run_scraper.py bger --reconcile --since Y-01-01 --until Y-12-31`, 2024 ends
2024-06-30); this driver only decides WHEN, and stops on the runbook's stop
conditions.

Other bger writers (UTC): nightly scrape 01:00 (to ~03:15), late-scrapers
10:20 (~35 min), bger-poller 05:00 and 16:00, weekly backfill Sat 20:00 (~1 h).
A run starts only inside WINDOWS, only when none of those units is active and
no other `run_scraper.py bger` process exists, and is stopped (SIGTERM) at the
window's end. The sweep is idempotent (rows dedup by decision_id; the held
index skips what is held), so a stopped year simply runs again in the next
window.

The held index (state/bger.held.tsv) is rebuilt before a run when older than
HELD_MAX_AGE_H, so rulings the nightly scrape added count as held. Jonas
approved building it while a publish build runs (2026-11-09, "do all"); it
reads only the bger shards, at idle I/O priority.

Stop conditions (runbook step 3/4):
  * a finished year fetched more than MAX_CANDIDATES rulings → stop, ntfy
    (held index missing a shard, or docket normalisation disagrees);
  * `stopped_refused` (bger.ch refused 3 windows in a row) → wait
    REFUSED_BACKOFF_H, retry that year; after MAX_REFUSALS → stop, ntfy;
  * held index below MIN_HELD rulings → stop.

Progress: state/bger.reconcile.done (one finished year per line), log
logs/bger_reconcile_driver.log, per-run output logs/bger_reconcile.log.

Usage (VPS, as a transient unit so it survives the SSH session):
  systemd-run --unit=bger-reconcile-driver --working-directory=/opt/caselaw/repo \
    --property=Nice=10 --property=IOSchedulingClass=idle \
    /usr/bin/python3 /opt/caselaw/tmp/bger_reconcile_driver.py
  # optional: --years 2018,2010 to run only those, --dry-run to print the plan
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

# The repo is the working directory (the script may live outside it, e.g.
# /opt/caselaw/tmp, so a later ff-merge never meets an untracked copy).
REPO = Path.cwd()
STATE = REPO / "state"
HELD = STATE / "bger.held.tsv"
REPORT = STATE / "bger.reconcile.jsonl"
DONE = STATE / "bger.reconcile.done"
LOG = REPO / "logs" / "bger_reconcile_driver.log"
RUN_LOG = REPO / "logs" / "bger_reconcile.log"

# Default order (runbook step 4): validation years first, then 2023 → 2000,
# then 2024 (ends 2024-06-30).
DEFAULT_YEARS = [2018, 2010] + [y for y in range(2023, 1999, -1) if y not in (2018, 2010)] + [2024]

# (start, end) in UTC minutes of the day; end may pass midnight (> 1440).
WINDOWS = [
    (3 * 60 + 20, 4 * 60 + 55),     # after the 01:00 scrape, before the 05:00 poller
    (5 * 60 + 5, 10 * 60 + 15),     # poller → late-scrapers 10:20
    (11 * 60 + 5, 15 * 60 + 55),    # after late-scrapers → 16:00 poller
    (16 * 60 + 5, 24 * 60 + 45),    # → 00:45, before the 01:00 nightly scrape
]
SATURDAY_BLACKOUT = (19 * 60 + 45, 21 * 60 + 45)   # weekly backfill Sat 20:00
MIN_RUN_MIN = 25          # don't start a year with less than this left in the window
WRITER_UNITS = ["opencaselaw-scrape.service", "opencaselaw-late-scrapers.service",
                "bger-poller.service", "opencaselaw-bger-backfill.service"]
HELD_MAX_AGE_H = 6
MIN_HELD = 190_000
MAX_CANDIDATES = 300
REFUSED_BACKOFF_H = 2
MAX_REFUSALS = 3

# Rulings entscheidsuche's index named that bger.ch serves and we lacked
# (parity addendum 2026-11-08): the validation years must fetch them.
VALIDATION = {2018: ["2C_1023/2018", "9C_806/2018"], 2010: ["1B_232/2010"],
              2009: ["2C_170/2009"], 2012: ["8C_243/2012"]}


def shard_has(docket: str) -> bool:
    r = subprocess.run(["grep", "-c", "-F", f'"docket_number": "{docket}"',
                        str(REPO / "output" / "decisions" / "bger.jsonl")],
                       capture_output=True, text=True)
    return r.stdout.strip() not in ("", "0")


RUN_ENV = {
    "REQUESTS_CA_BUNDLE": "/opt/caselaw/certs/ca-bundle.pem",
    "SSL_CERT_FILE": "/opt/caselaw/certs/ca-bundle.pem",
}

_stop = False


def log(msg: str) -> None:
    line = f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S} {msg}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as f:
        f.write(line + "\n")


def ntfy(msg: str) -> None:
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        env = Path("/opt/caselaw/ops.env")
        if env.exists():
            for ln in env.read_text().splitlines():
                if ln.startswith("NTFY_TOPIC="):
                    topic = ln.split("=", 1)[1].strip().strip('"')
    if not topic:
        return
    url = topic if topic.startswith("http") else f"https://ntfy.sh/{topic}"
    try:
        urllib.request.urlopen(urllib.request.Request(
            url, data=("[bger reconcile] " + msg).encode(), method="POST"), timeout=15)
    except Exception as e:  # alert is best effort
        log(f"ntfy failed: {e}")


def window_end(now: datetime) -> datetime | None:
    """End of the window `now` lies in, or None when outside every window."""
    day0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    for base in (day0, day0 - timedelta(days=1)):     # windows past midnight
        for s, e in WINDOWS:
            ws, we = base + timedelta(minutes=s), base + timedelta(minutes=e)
            if ws <= now < we:
                # Saturday blackout cuts the window short
                for b in (base, base + timedelta(days=1)):
                    if b.weekday() == 5:
                        bs = b + timedelta(minutes=SATURDAY_BLACKOUT[0])
                        be = b + timedelta(minutes=SATURDAY_BLACKOUT[1])
                        if bs <= now < be:
                            return None
                        if now < bs < we:
                            we = bs
                return we
    return None


def other_writer() -> str | None:
    for u in WRITER_UNITS:
        r = subprocess.run(["systemctl", "is-active", u], capture_output=True, text=True)
        if r.stdout.strip() in ("active", "activating", "reloading", "deactivating"):
            return u
    r = subprocess.run(["pgrep", "-af", "run_scraper.py bger"], capture_output=True, text=True)
    lines = [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]
    return lines[0] if lines else None


def done_years() -> set[int]:
    if not DONE.exists():
        return set()
    return {int(x) for x in DONE.read_text().split() if x.strip().isdigit()}


def ensure_held_index() -> bool:
    if HELD.exists() and time.time() - HELD.stat().st_mtime < HELD_MAX_AGE_H * 3600:
        return True
    log("rebuilding held index")
    r = subprocess.run(["ionice", "-c", "3", "nice", "-n", "19", sys.executable,
                        "scripts/build_bger_held_index.py"],
                       cwd=REPO, capture_output=True, text=True)
    if r.returncode != 0:
        log(f"held index build failed: {r.stderr[-500:]}")
        return False
    try:
        info = json.loads(r.stdout)          # the script prints indented JSON
    except Exception:
        log(f"held index: unreadable output {r.stdout[-300:]}")
        return False
    n = info.get("rulings", 0)
    by_year = info.get("by_year", {})
    thin = [y for y in range(2000, 2024) if by_year.get(str(y), 0) < 5000]
    log(f"held index: {n} rulings")
    if n < MIN_HELD or thin:
        log(f"STOP: held index has {n} rulings (min {MIN_HELD}); years under 5,000: {thin}")
        ntfy(f"stopped: held index {n} rulings, thin years {thin}")
        return False
    return True


def last_summary(year: int, since_offset: int) -> list[dict]:
    """Report events this run appended (from byte offset since_offset)."""
    if not REPORT.exists():
        return []
    out = []
    with REPORT.open("rb") as f:
        f.seek(since_offset)
        for ln in f:
            try:
                out.append(json.loads(ln))
            except Exception:
                pass
    return out


def run_year(year: int, deadline: datetime) -> str:
    """'done' | 'stopped' (window end) | 'refused' | 'failed' | 'too_many:<n>'"""
    until = "2024-06-30" if year == 2024 else f"{year}-12-31"
    cmd = [sys.executable, "run_scraper.py", "bger", "--reconcile",
           "--since", f"{year}-01-01", "--until", until]
    offset = REPORT.stat().st_size if REPORT.exists() else 0
    log(f"{year}: start, window ends {deadline:%H:%M} UTC")
    env = {**os.environ, **RUN_ENV}
    with RUN_LOG.open("a") as out:
        p = subprocess.Popen(cmd, cwd=REPO, env=env, stdout=out, stderr=subprocess.STDOUT)
        while p.poll() is None:
            if _stop or datetime.now(timezone.utc) >= deadline:
                log(f"{year}: window end, stopping (SIGTERM)")
                p.send_signal(signal.SIGTERM)
                try:
                    p.wait(timeout=120)
                except subprocess.TimeoutExpired:
                    p.kill()
                    p.wait()
                return "stopped"
            time.sleep(15)
    events = last_summary(year, offset)
    if any(e.get("event") == "stopped_refused" for e in events):
        return "refused"
    summ = [e for e in events if e.get("event") == "summary" and e.get("since") == f"{year}-01-01"]
    if p.returncode != 0 or not summ:
        log(f"{year}: exit {p.returncode}, summary {'yes' if summ else 'missing'}")
        return "failed"
    s = summ[-1]
    log(f"{year}: summary {json.dumps(s)}")
    n = int(s.get("candidates", 0))
    if n > MAX_CANDIDATES:
        return f"too_many:{n}"
    return "done"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--years", help="comma list, default: all of 2000..2024 in runbook order")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if not (REPO / "run_scraper.py").exists():
        print(f"run from the repo root (no run_scraper.py in {REPO})", file=sys.stderr)
        return 1
    years = [int(y) for y in args.years.split(",")] if args.years else DEFAULT_YEARS

    def _term(*_):
        global _stop
        _stop = True
    signal.signal(signal.SIGTERM, _term)

    todo = [y for y in years if y not in done_years()]
    log(f"driver start: {len(todo)} years to do: {todo}")
    if args.dry_run:
        now = datetime.now(timezone.utc)
        log(f"now in window until: {window_end(now)}; other writer: {other_writer()}")
        return 0
    refusals: dict[int, int] = {}
    subprocess.run(["/opt/caselaw/certs/build-ca-bundle.sh"], capture_output=True)
    while todo and not _stop:
        now = datetime.now(timezone.utc)
        we = window_end(now)
        if we is None or (we - now) < timedelta(minutes=MIN_RUN_MIN):
            time.sleep(60)
            continue
        w = other_writer()
        if w:
            log(f"waiting: other bger writer active ({w[:80]})")
            time.sleep(120)
            continue
        if not ensure_held_index():
            return 2
        y = todo[0]
        res = run_year(y, we)
        if res == "done":
            with DONE.open("a") as f:
                f.write(f"{y}\n")
            missing = [d for d in VALIDATION.get(y, []) if not shard_has(d)]
            if missing:
                log(f"STOP: validation year {y} ran but {missing} not in bger.jsonl")
                ntfy(f"stopped: {y} ran but {missing} were not fetched; check before continuing")
                return 6
            todo.pop(0)
            log(f"{y}: done; {len(todo)} left")
        elif res == "stopped":
            pass                                  # same year, next window
        elif res == "refused":
            refusals[y] = refusals.get(y, 0) + 1
            log(f"{y}: bger.ch refused (#{refusals[y]})")
            if refusals[y] >= MAX_REFUSALS:
                ntfy(f"stopped: {y} refused {MAX_REFUSALS} times")
                return 3
            time.sleep(REFUSED_BACKOFF_H * 3600)
        elif res.startswith("too_many"):
            log(f"STOP: {y} fetched {res.split(':')[1]} > {MAX_CANDIDATES}")
            ntfy(f"stopped: {y} fetched {res.split(':')[1]} rulings (> {MAX_CANDIDATES}); check the held index")
            with DONE.open("a") as f:      # the year ran; don't repeat it blindly
                f.write(f"{y}\n")
            return 4
        else:
            ntfy(f"stopped: {y} failed (see logs/bger_reconcile.log)")
            return 5
    if not todo:
        log("all years done")
        ntfy("all years done; date conflicts: grep date_conflict state/bger.reconcile.jsonl")
    return 0


if __name__ == "__main__":
    sys.exit(main())
