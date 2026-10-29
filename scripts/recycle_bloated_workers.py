#!/usr/bin/env python3
"""Restart MCP workers whose memory has grown past a threshold, one at a time.

Why (2026-09-30 and 2026-10-04): Python and SQLite do not return freed memory
to the OS, so a few hours of heavy clients (deep pagination, bulk agents) leave
each mcp-server@ worker at 4-13 GB instead of ~2 GB. Eight of them squeeze the
page cache, every search and the nightly build go to disk, and the data volume
sits at its IOPS ceiling: searches took 20-45 s on both nights until a rolling
restart freed the memory (37.7 GB -> 2.1 GB on 10-04).

What it does, every run (timer: every 15 min):
  - reads each active worker's resident memory from /proc;
  - does nothing unless the whole fleet is healthy (a worker already down
    means capacity is short; taking another one out would make it worse);
  - restarts the largest workers above the threshold, at most MAX_PER_RUN,
    each with `systemctl restart` (graceful SIGTERM, the unit's 90 s stop
    timeout) and waits for its /health before touching the next.

A worker that does not come back healthy stops the run and exits 1, so the
unit's OnFailure alert fires. Settings (environment):
  OCL_WORKER_RSS_MAX_MB   restart above this resident size at night (default 3584)
  OCL_WORKER_RSS_DAY_MAX_MB  the same by day (default 5120): a restart cuts the
                          calls running on that worker (one failed call and a
                          few reopened streams per restart, measured 2026-10-06),
                          so by day only a worker well past normal is restarted
  OCL_WORKER_NIGHT_HOURS  UTC hours counted as night, "start-end" (default 0-6)
  OCL_PUBLISH_UNIT        the nightly build's unit (opencaselaw-publish.service):
                          while it is active the night limit applies at any
                          hour, because the build reads the same volume and
                          slows to a crawl once the workers squeeze the page
                          cache (2026-10-23: 29.7 GB of workers at 11:12 UTC,
                          largest 4.0 GB, step 2 1h15m behind)
  OCL_WORKER_RECYCLE_MAX  at most this many restarts per run (default 2)
  OCL_WORKER_HEALTH_WAIT  seconds to wait for /health after a restart (30)
  OCL_WORKER_RECYCLE_DRY  "1": report what would be restarted, change nothing
"""
from __future__ import annotations

import datetime
import os
import subprocess
import sys
import time
import urllib.request

RSS_MAX_MB = int(os.environ.get("OCL_WORKER_RSS_MAX_MB", "3584"))
RSS_DAY_MAX_MB = int(os.environ.get("OCL_WORKER_RSS_DAY_MAX_MB", "5120"))
NIGHT_HOURS = os.environ.get("OCL_WORKER_NIGHT_HOURS", "0-6")
MAX_PER_RUN = int(os.environ.get("OCL_WORKER_RECYCLE_MAX", "2"))
HEALTH_WAIT = int(os.environ.get("OCL_WORKER_HEALTH_WAIT", "30"))
PUBLISH_UNIT = os.environ.get("OCL_PUBLISH_UNIT", "opencaselaw-publish.service")
DRY_RUN = os.environ.get("OCL_WORKER_RECYCLE_DRY") == "1"


def log(msg: str) -> None:
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    print(f"{ts} worker_recycle {msg}", flush=True)


def active_workers() -> list[str]:
    """Unit names of the running mcp-server@ workers (the pool size lives in
    systemd, as in rolling_restart_workers.sh and publish.py)."""
    out = subprocess.run(
        ["systemctl", "list-units", "mcp-server@*.service", "--state=active",
         "--no-legend", "--plain"],
        capture_output=True, text=True, check=True).stdout
    return [line.split()[0] for line in out.splitlines() if line.strip()]


def main_pid(unit: str) -> int:
    out = subprocess.run(["systemctl", "show", "-p", "MainPID", "--value", unit],
                         capture_output=True, text=True, check=True).stdout.strip()
    return int(out or 0)


def rss_mb(pid: int) -> int:
    """Resident set size of one process in MB, 0 if it is gone."""
    try:
        with open(f"/proc/{pid}/status") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) // 1024
    except OSError:
        pass
    return 0


def port_of(unit: str) -> str:
    return unit.split("@", 1)[1].split(".", 1)[0]


def healthy(port: str, timeout: float = 5.0) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=timeout) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001 — any failure means not healthy
        return False


def wait_healthy(port: str, seconds: int) -> bool:
    for _ in range(seconds):
        if healthy(port):
            return True
        time.sleep(1)
    return False


def build_running(unit: str = PUBLISH_UNIT) -> bool:
    """True while the nightly build's unit is active or starting. Any failure
    to ask systemd counts as not running (the day limit, as before)."""
    try:
        out = subprocess.run(["systemctl", "is-active", unit],
                             capture_output=True, text=True, check=False, timeout=10).stdout
    except Exception:  # noqa: BLE001 — no answer means no build known
        return False
    return out.strip() in ("active", "activating")


def utc_hour() -> int:
    return datetime.datetime.now(datetime.timezone.utc).hour


def limit_for(hour_utc: int, night: str = NIGHT_HOURS,
              night_mb: int = RSS_MAX_MB, day_mb: int = RSS_DAY_MAX_MB,
              build_active: bool = False) -> int:
    """The restart threshold for this hour: the low one at night, when a cut
    call hurts least, or while the build runs, when bloated workers starve
    it of page cache; the high one otherwise. "22-6" wraps past midnight."""
    start, end = (int(x) for x in night.split("-", 1))
    is_night = start <= hour_utc < end if start <= end else (hour_utc >= start or hour_utc < end)
    return night_mb if is_night or build_active else day_mb


def plan(sizes: dict[str, int], all_healthy: bool, limit_mb: int, max_per_run: int) -> list[str]:
    """Which workers to restart: the largest above the limit, at most
    max_per_run, and none at all while any worker is unhealthy."""
    if not all_healthy:
        return []
    over = sorted((u for u, mb in sizes.items() if mb > limit_mb), key=lambda u: -sizes[u])
    return over[:max_per_run]


def main() -> int:
    units = active_workers()
    if not units:
        log("no active mcp-server@ units; nothing to do")
        return 0
    sizes = {u: rss_mb(main_pid(u)) for u in units}
    building = build_running()
    limit = limit_for(utc_hour(), build_active=building)
    sick = [u for u in units if not healthy(port_of(u))]
    total = sum(sizes.values())
    log(f"{len(units)} workers, {total} MB resident, largest "
        f"{max(sizes.values())} MB, limit {limit} MB"
        + (" (build running)" if building else "")
        + (f", unhealthy: {', '.join(sick)}" if sick else ""))
    todo = plan(sizes, not sick, limit, MAX_PER_RUN)
    if sick:
        log("a worker is unhealthy; restarting nothing this run")
        return 0
    for unit in todo:
        if DRY_RUN:
            log(f"would restart {unit} ({sizes[unit]} MB)")
            continue
        log(f"restarting {unit} ({sizes[unit]} MB)")
        subprocess.run(["systemctl", "restart", unit], check=False)
        if not wait_healthy(port_of(unit), HEALTH_WAIT):
            log(f"{unit} not healthy {HEALTH_WAIT}s after restart; stopping")
            return 1
        log(f"{unit} healthy, now {rss_mb(main_pid(unit))} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
