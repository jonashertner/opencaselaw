#!/usr/bin/env python3
"""
run_all_scrapers.py — Daily scraper orchestration
===================================================

Runs all registered scrapers with controlled parallelism, timeouts, and
health checks. Designed to be called by cron before publish.py.

Architecture:
- Scrapers are grouped into batches that run concurrently (max_parallel)
- Each scraper gets a per-scraper timeout (default 2h, configurable)
- Scrapers that complete quickly (few new decisions) free up slots
- All output is logged per-scraper to logs/{court}.log
- A summary is written to logs/daily_scrape.log

Special scrapers:
- ow_gerichte: Needs Playwright (chromium), ~6s/decision, ~3.7h full run
- ju_gerichte: Needs SOCKS proxy on localhost:1080 (SSH tunnel)
- bger, bge: Need Incapsula bypass via Playwright

Cron (run before publish.py):
    0 1 * * * cd /opt/caselaw/repo && python3 run_all_scrapers.py >> logs/daily_scrape.log 2>&1
    15 3 * * * cd /opt/caselaw/repo && python3 publish.py >> logs/publish.log 2>&1

Usage:
    python3 run_all_scrapers.py                    # run all scrapers
    python3 run_all_scrapers.py --courts bger,bge  # run specific scrapers
    python3 run_all_scrapers.py --exclude ow_gerichte  # skip specific scrapers
    python3 run_all_scrapers.py --parallel 4       # max 4 concurrent
    python3 run_all_scrapers.py --timeout 3600     # 1h timeout per scraper
    python3 run_all_scrapers.py --dry-run          # show what would run
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import shutil
import subprocess
import sys
import time
from collections import deque
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("daily_scrape")

REPO_DIR = Path(__file__).parent.resolve()

# Default timeout per scraper (seconds)
DEFAULT_TIMEOUT = 7200  # 2 hours

# Maximum concurrent scrapers. The ccx43 has 16 dedicated CPU + 64 GB RAM,
# and most scrapers are HTTP I/O-bound rather than CPU-bound. Heavy Playwright
# scrapers (~200 MB RSS each: bger, bge, bvger, ow, vd, zh) cap us; at 10
# parallel we use ~2 GB peak for those alongside ~50 MB each for the rest.
DEFAULT_PARALLEL = 10

# Scrapers that need extra time (Playwright-based, large volume)
SLOW_SCRAPERS = {
    "ow_gerichte": 14400,   # 4h — Playwright, ~6s/decision
    "vd_gerichte": 14400,   # 4h — 40k+ decisions, PDF heavy
    "bger": 10800,          # 3h — 90k decisions, Incapsula
    "bge": 10800,           # 3h — Incapsula
    "bvger": 10800,         # 3h — 90k decisions
    "zh_gerichte": 10800,   # 3h — 34k decisions
    "zh_sozialversicherungsgericht": 10800,  # 3h — 34k decisions
    "finma_versicherungsrecht": 14400,  # 4h — 2.6k PDFs, first run downloads all
    # 4h — GitHub #68. The docket-span fix in base_tribuna makes discovery
    # return the rows the old ordinal zip dropped, so the first runs walk
    # ~580 pages and fetch a ~2,159-decision backlog at REQUEST_DELAY=4.0.
    # That is ~3h against the previous 7200s default, which would SIGKILL it
    # mid-catch-up and report red for several nights. Progress survives a
    # kill — run_scraper appends and flushes per decision, marking state only
    # after the durable write — but the cap has to allow the drain.
    "be_verwaltungsgericht": 14400,
}

# Scrapers to skip by default (broken, redundant, or handled separately)
SKIP_BY_DEFAULT: set[str] = {
    "be_steuerrekurs",  # Portal DB disconnected (Feb 2026), returns 0 results
    # ecthr has its own timer (opencaselaw-ecthr.timer, 14:00 UTC) which
    # chains quick_publish so fresh Strasbourg judgments are searchable within
    # minutes. Running it here too meant scraping HUDOC twice a day for the
    # same rows, and — worse — the 01:00 monolithic run is not covered by the
    # backfill guard on opencaselaw-ecthr.service, so it would collide with a
    # long one-shot backfill on state/ecthr.jsonl. Still runnable explicitly:
    # `run_all_scrapers.py --courts ecthr` or `run_scraper.py ecthr`.
    "ecthr",
}

# Scrapers that route through the Mac reverse-SOCKS tunnel (127.0.0.1:1080) via
# BGER_PROXY / JU_PROXY / NE_PROXY because their portals block the Hetzner IP.
# When the tunnel is down (the Mac is asleep at the 01:00 UTC scrape) the portal
# is unreachable through no fault of the scraper, producing a burst of
# discovery-phase connection failures. That must read as "skipped (tunnel down)",
# not a portal-down failure, so a Mac-off night doesn't fire a false alert. The
# bger poller keeps bger current whenever the tunnel is up.
TUNNEL_DEPENDENT: set[str] = {
    "bger", "bge", "ju_gerichte", "ne_gerichte", "ne_jurisprudence_adm",
}


# Scrapers the 10:20 late run (--source manual) only retries: when today's
# 09:00 federal run of the same scraper succeeded there is nothing to retry,
# and running it again only adds load on a portal that has started refusing
# the tunnel address in that window (bge, 2026-09-28/29).
RETRY_ONLY_IF_FEDERAL_FAILED: set[str] = {"bge"}


def _clean_federal_run_today(court: str, logs_dir: Path | None = None,
                             today: str | None = None) -> str | None:
    """The time (HH:MM UTC) of today's successful federal run of `court`, else None."""
    path = (logs_dir or REPO_DIR / "logs") / "scraper_health_federal.json"
    try:
        health = json.loads(path.read_text())
        run_at = datetime.fromisoformat(health["run_at"])
        entry = health["scrapers"][court]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    today = today or datetime.now(timezone.utc).date().isoformat()
    run_at = run_at.astimezone(timezone.utc)
    if run_at.date().isoformat() != today:
        return None
    if not entry.get("success") or entry.get("timed_out"):
        return None
    return run_at.strftime("%H:%M")


def _skipped_result(court: str, note: str) -> dict:
    return {
        "court": court, "success": True, "new_count": 0, "skip_count": 0,
        "error_count": 0, "none_count": 0, "duration": 0.0,
        "error": None, "note": note,
    }


def _socks_tunnel_up(host: str = "127.0.0.1", port: int = 1080) -> bool:
    """True if something is listening on the reverse-SOCKS tunnel port."""
    import socket
    try:
        with socket.create_connection((host, port), timeout=1.0):
            return True
    except OSError:
        return False


# A listing or search page the portal REFUSED (the scraper's own terminal ERROR
# after its retries). requests words these "403 Client Error: Forbidden",
# "503 Server Error: ...", which none of the connection-failure markers match,
# so a run with every listing refused reported "+0 new, Errors: 0" as success:
# bge 2026-09-28 09:00 (356/356 listings 403), bger 10:20 (both hosts 403).
# Replaying every run in logs/*.log on 2026-09-28: 12 runs flip to FAILED, all
# bge/bger, all real refusals (two bger runs predate the "via host" wording). Per-
# document errors are left out on purpose (listing/search lines only), and so
# is bger's first host: "via https://www.bger.ch" falls through to
# search.bger.ch, so a refusal there is only a failure when the fallback fails
# too, which logs its own line.
# 404 joined on 2026-10-02: bger.ch now also refuses with a 404 error page on
# index/listing/search URLs that always exist. No scraper log on the host had
# ever carried a 404 on a listing or search line, so nothing else changes.
# "portal refused" is scrapers.refusal.PortalRefused: a block or challenge page
# served with status 200 where a listing was asked for.
_HTTP_REFUSAL = re.compile(
    r"\b(403|404|429|502|503|504) (Client|Server) Error|portal refused")


def _is_listing_refusal(line: str) -> bool:
    return (
        " ERROR " in line
        and _HTTP_REFUSAL.search(line) is not None
        and ("listing" in line.lower() or " Search " in line)
        and "via https://www.bger.ch" not in line
    )

# Scrapers where a high none_count (>=200) is expected and not a portal failure.
#
# Empty as of 2026-08-26. `ecthr` used to live here: HUDOC lists a row per
# language but only stores the authoritative text, so roughly 1,622 fetches
# a night came back empty and the none_count signal was worthless. Discovery
# now filters those out server-side with `isplaceholder:False`, so a None
# return from ecthr is a real failure again and should alert like any other.
NONE_RETURN_TOLERANT_SCRAPERS: set[str] = set()

# Disk usage thresholds (percent)
DISK_WARN_PERCENT = 85
DISK_CRITICAL_PERCENT = 95


def check_disk_usage() -> dict:
    """Check disk usage for the data volume and repo directory.

    Returns dict with total_gb, used_gb, free_gb, used_percent for each path.
    """
    paths_to_check = {
        "data_volume": Path("/mnt/HC_Volume_104655575"),
        "repo": REPO_DIR,
    }
    result = {}
    for label, path in paths_to_check.items():
        if not path.exists():
            continue
        try:
            usage = shutil.disk_usage(path)
            info = {
                "path": str(path),
                "total_gb": round(usage.total / (1024**3), 1),
                "used_gb": round(usage.used / (1024**3), 1),
                "free_gb": round(usage.free / (1024**3), 1),
                "used_percent": round(usage.used / usage.total * 100, 1),
            }
            result[label] = info

            if info["used_percent"] >= DISK_CRITICAL_PERCENT:
                logger.error(
                    f"CRITICAL: {label} disk at {info['used_percent']}% "
                    f"({info['free_gb']} GB free) — scraping may fail!"
                )
            elif info["used_percent"] >= DISK_WARN_PERCENT:
                logger.warning(
                    f"WARNING: {label} disk at {info['used_percent']}% "
                    f"({info['free_gb']} GB free) — consider cleanup"
                )
            else:
                logger.info(
                    f"Disk {label}: {info['used_percent']}% used "
                    f"({info['free_gb']} GB free)"
                )
        except Exception as e:
            logger.warning(f"Could not check disk for {label}: {e}")
    return result


def get_all_courts() -> list[str]:
    """Get all registered court codes from run_scraper.py."""
    # Import here to avoid circular imports
    sys.path.insert(0, str(REPO_DIR))
    from run_scraper import SCRAPERS
    return sorted(SCRAPERS.keys())


def run_single_scraper(court: str, timeout: int) -> dict:
    """
    Run a single scraper as a subprocess.

    Returns dict with: court, success, new_count, duration, error
    """
    start = time.time()
    log_path = REPO_DIR / "logs" / f"{court}.log"
    log_path.parent.mkdir(exist_ok=True)
    log_start = log_path.stat().st_size if log_path.exists() else 0

    cmd = [sys.executable, str(REPO_DIR / "run_scraper.py"), court]

    try:
        # Capture stderr so we can post-mortem any uncaught exception. Without
        # this, scraper crashes show up as bare "Exit code 1" with no detail
        # because run_scraper.py exceptions go to stderr, not the per-scraper
        # log file.
        result = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=timeout,
            cwd=str(REPO_DIR),
        )
        duration = time.time() - start
        # On failure, append captured stderr to the scraper's log so the
        # diagnosis is preserved alongside its INFO/WARNING records.
        if result.returncode != 0 and result.stderr:
            try:
                with open(log_path, "ab") as f:
                    f.write(b"\n--- subprocess stderr ---\n")
                    f.write(result.stderr)
                    f.write(b"\n--- end stderr ---\n")
            except OSError:
                pass

        # Parse only this run's appended log region:
        # [court] Done. +42 new, 1074/1102 (gap 28), Errors: 3, ...
        new_count = 0
        skip_count = 0
        error_count = 0
        none_count = 0
        our_count = None
        portal_count = None
        error_tail: deque[str] = deque(maxlen=6)
        # Discovery-phase connection failures. The "Done." summary's
        # "Errors: N" field only covers per-decision fetch errors; a scraper
        # whose search/index pages all time out reports Errors: 0 because
        # no fetch was ever attempted. Count connection/timeout errors
        # separately to detect silent total failures.
        discovery_errors = 0
        if log_path.exists():
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                if log_start > 0:
                    f.seek(log_start)
                for line in f:
                    if "Done. +" in line or "Done. New:" in line:
                        # New format: +N new, OUR/PORTAL (gap G), ...
                        # Old format: New: N, Skips: S, ...
                        import re as _re
                        m = _re.search(r'\+(\d+) new', line)
                        if m:
                            new_count = int(m.group(1))
                        elif "New:" in line:
                            try:
                                new_count = int(line.split("New:")[1].split(",")[0].strip())
                            except (ValueError, IndexError):
                                pass
                        # Parse coverage: "OUR/PORTAL" or just "OUR"
                        m = _re.search(r'new, (\d+)/(\d+)', line)
                        if m:
                            our_count = int(m.group(1))
                            portal_count = int(m.group(2))
                        else:
                            m = _re.search(r'new, (\d+),', line)
                            if m:
                                our_count = int(m.group(1))
                        if "Skips:" in line:
                            try:
                                skip_count = int(line.split("Skips:")[1].split(",")[0].strip())
                            except (ValueError, IndexError):
                                pass
                        if "NoneReturns:" in line:
                            try:
                                none_count = int(line.split("NoneReturns:")[1].split(",")[0].strip())
                            except (ValueError, IndexError):
                                pass
                        try:
                            error_count = int(line.split("Errors:")[1].split(",")[0].strip())
                        except (ValueError, IndexError):
                            pass
                    if " ERROR " in line or "Traceback" in line:
                        error_tail.append(line.strip())
                    # Count *terminal* discovery failures only. urllib3 emits
                    # "WARNING Retrying (...) after connection broken by
                    # 'ConnectTimeoutError(...)'" for every transient retry;
                    # if the request then succeeds (as it does for bstger,
                    # which has a flaky upstream but recovers within the
                    # urllib3 retry budget), there is no real failure. Old
                    # logic counted those retries and falsely flagged bstger
                    # as failed nightly. Only count lines that signal an
                    # exhausted retry budget — "Max retries exceeded"
                    # (urllib3's terminal message) or the scraper's own
                    # ERROR-level "Search page X failed" / "... search failed".
                    if "Retrying" not in line and (
                        "Max retries exceeded" in line
                        or ("Search page" in line and "failed" in line)
                        or (
                            " ERROR " in line
                            and "search failed" in line.lower()
                        )
                        or _is_listing_refusal(line)
                    ):
                        discovery_errors += 1

        error = None
        note = None
        failed = result.returncode != 0
        if result.returncode != 0:
            error = " | ".join(error_tail) if error_tail else f"Exit code {result.returncode}"
        elif error_count > 0 and error_count > none_count:
            # Real exceptions (not just NoneReturns)
            real_errors = error_count - none_count
            error = f"{real_errors} scraping errors"
            if real_errors > 20 and real_errors > new_count:
                failed = True

        # Silent total failure: discovery pages all failed and no new decisions
        # were found. Caught JU when its proxy was missing and every request
        # to jurisprudence.jura.ch timed out — the old logic reported
        # success=True because the "Done." summary said Errors: 0.
        if discovery_errors >= 3 and new_count == 0:
            failed = True
            error = (
                f"{discovery_errors} discovery-phase connection failures "
                f"(portal unreachable)"
            )
            # ...unless the scraper depends on the SOCKS tunnel and the tunnel is
            # down: then the portal was unreachable because the Mac was asleep at
            # scrape time, not because the scraper broke. Reclassify as skipped so
            # a Mac-off night doesn't fire a false portal-down alert. A genuine
            # break (tunnel UP but portal failing) still fails.
            if court in TUNNEL_DEPENDENT and not _socks_tunnel_up():
                failed = False
                error = None
                note = (
                    f"tunnel down (:1080) — skipped this run; "
                    f"{discovery_errors} unreachable is expected, not a portal failure"
                )

        # NoneReturns are expected for portals with a few broken entries.
        # Only flag as a note, not an error, unless excessive — and never for
        # scrapers listed in NONE_RETURN_TOLERANT_SCRAPERS where high none_count
        # is normal behaviour (e.g. ecthr / HUDOC, where many judgments lack a
        # downloadable authoritative-language text by design). For those, even
        # large none_counts collapse to an informational note so the daily
        # health dashboard doesn't fire a false-positive "possible portal
        # issue" alert. Real ecthr breakage still surfaces via the Errors:
        # count in the per-run summary, which feeds error_count above.
        if none_count > 0:
            if none_count >= 200 and court not in NONE_RETURN_TOLERANT_SCRAPERS:
                error = f"{none_count} unavailable decisions (possible portal issue)"
                failed = True
            else:
                note = f"{none_count} listed on portal but content not downloadable (empty page or missing PDF)"

        gap = portal_count - our_count if portal_count is not None and our_count is not None else None
        return {
            "court": court,
            "success": not failed,
            "new_count": new_count,
            "skip_count": skip_count,
            "error_count": max(0, error_count - none_count),  # real errors only
            "none_count": none_count,
            "discovery_errors": discovery_errors,
            "our_count": our_count,
            "portal_count": portal_count,
            "gap": gap,
            "duration": duration,
            "error": error,
            "note": note,
        }

    except subprocess.TimeoutExpired:
        duration = time.time() - start
        # Parse any progress from the log before timeout
        new_count = 0
        if log_path.exists():
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                if log_start > 0:
                    f.seek(log_start)
                for line in f:
                    if "Scraped:" in line:
                        new_count += 1
        return {
            "court": court,
            "success": False,
            "timed_out": True,
            "new_count": new_count,
            "skip_count": 0,
            "error_count": 0,
            "none_count": 0,
            "duration": duration,
            # Not None: check_scraper_freshness renders `v.get("error") or
            # "unknown"`, so a timeout used to alert as `FAIL <court>: unknown`
            # (observed 2026-08-23 for ecthr) — the one failure mode whose cause
            # is already known exactly.
            "error": f"timed out after {duration:.0f}s (cap {timeout}s)",
            "note": None,
        }
    except Exception as e:
        duration = time.time() - start
        return {
            "court": court,
            "success": False,
            "new_count": 0,
            "skip_count": 0,
            "error_count": 0,
            "none_count": 0,
            "duration": duration,
            "error": str(e)[:200],
            "note": None,
        }


def main():
    parser = argparse.ArgumentParser(description="Run all scrapers daily")
    parser.add_argument(
        "--courts", type=str, default="",
        help="Comma-separated court codes to run (default: all)",
    )
    parser.add_argument(
        "--exclude", type=str, default="",
        help="Comma-separated court codes to skip",
    )
    parser.add_argument(
        "--parallel", type=int, default=DEFAULT_PARALLEL,
        help=f"Max concurrent scrapers (default: {DEFAULT_PARALLEL})",
    )
    parser.add_argument(
        "--timeout", type=int, default=DEFAULT_TIMEOUT,
        help=f"Default timeout per scraper in seconds (default: {DEFAULT_TIMEOUT})",
    )
    parser.add_argument("--dry-run", action="store_true", help="Show what would run")
    parser.add_argument(
        "--source", type=str, default="cron", choices=["cron", "manual", "federal"],
        help="Run source: 'cron' writes to scraper_health.json, "
             "'manual' writes to scraper_health_manual.json, "
             "'federal' writes to scraper_health_federal.json (used by the "
             "hourly federal poller so it doesn't clobber the daily file).",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    if args.parallel < 1:
        parser.error("--parallel must be at least 1")

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    # Determine which courts to run
    all_courts = get_all_courts()

    if args.courts:
        courts = [c.strip() for c in args.courts.split(",") if c.strip()]
        unknown = set(courts) - set(all_courts)
        if unknown:
            logger.error(f"Unknown courts: {unknown}. Available: {all_courts}")
            sys.exit(1)
    else:
        courts = [c for c in all_courts if c not in SKIP_BY_DEFAULT]

    if args.exclude:
        exclude = {c.strip() for c in args.exclude.split(",")}
        courts = [c for c in courts if c not in exclude]

    # Ensure logs directory exists
    (REPO_DIR / "logs").mkdir(exist_ok=True)

    now = datetime.now(timezone.utc)
    logger.info(f"=== Daily scrape — {now.isoformat()} ===")
    logger.info(f"Courts: {len(courts)}, Parallel: {args.parallel}, Timeout: {args.timeout}s")

    # Pre-flight disk check
    disk_before = check_disk_usage()
    for label, info in disk_before.items():
        if info["used_percent"] >= DISK_CRITICAL_PERCENT:
            logger.error(f"Aborting: {label} disk at {info['used_percent']}% — not enough space to scrape safely")
            sys.exit(2)

    if args.dry_run:
        for court in courts:
            t = SLOW_SCRAPERS.get(court, args.timeout)
            logger.info(f"  [dry-run] Would run: {court} (timeout: {t}s)")
        return

    # Run scrapers with controlled parallelism
    results = []
    total_start = time.time()

    if args.source == "manual":
        for court in [c for c in courts if c in RETRY_ONLY_IF_FEDERAL_FAILED]:
            clean_at = _clean_federal_run_today(court)
            if clean_at:
                courts.remove(court)
                note = f"skipped: today's {clean_at} UTC federal run succeeded"
                logger.info(f"  [SKIP] {court}: {note}")
                results.append(_skipped_result(court, note))

    with ProcessPoolExecutor(max_workers=args.parallel) as executor:
        futures = {}
        for court in courts:
            # SLOW_SCRAPERS sets the default for known-slow scrapers,
            # but --timeout always acts as an upper bound (for health checks)
            default = SLOW_SCRAPERS.get(court, DEFAULT_TIMEOUT)
            timeout = min(default, args.timeout) if args.timeout != DEFAULT_TIMEOUT else default
            future = executor.submit(run_single_scraper, court, timeout)
            futures[future] = court

        for future in as_completed(futures):
            court = futures[future]
            try:
                result = future.result()
                results.append(result)
                if result.get("timed_out"):
                    status = "TIMEOUT"
                elif result["success"]:
                    status = "OK"
                else:
                    status = "FAILED"
                coverage = ""
                if result.get("portal_count") is not None and result.get("our_count") is not None:
                    coverage = f" ({result['our_count']}/{result['portal_count']}"
                    if result.get("gap") and result["gap"] > 0:
                        coverage += f", gap {result['gap']}"
                    coverage += ")"
                elif result.get("our_count") is not None:
                    coverage = f" ({result['our_count']})"
                logger.info(
                    f"  [{status}] {result['court']}: "
                    f"+{result['new_count']} new{coverage}, "
                    f"{result['duration']:.0f}s"
                    f"{' — ' + result['error'] if result['error'] else ''}"
                )
            except Exception as e:
                logger.error(f"  [ERROR] {court}: {e}")
                results.append({
                    "court": court,
                    "success": False,
                    "new_count": 0,
                    "skip_count": 0,
                    "error_count": 0,
                    "duration": 0,
                    "error": str(e)[:200],
                })

    # Summary
    total_elapsed = time.time() - total_start
    succeeded = sum(1 for r in results if r["success"] and not r.get("timed_out"))
    timed_out = sum(1 for r in results if r.get("timed_out"))
    failed = sum(1 for r in results if not r["success"] and not r.get("timed_out"))
    total_new = sum(r["new_count"] for r in results)

    logger.info("\n=== Summary ===")
    logger.info(f"  Succeeded: {succeeded}/{len(results)}")
    if timed_out:
        logger.info(f"  Timed out: {timed_out}")
    logger.info(f"  Failed: {failed}")
    logger.info(f"  Total new decisions: {total_new}")
    logger.info(f"  Total time: {total_elapsed:.0f}s ({total_elapsed/60:.1f} min)")

    if timed_out:
        logger.info("\n  Timed out (hit time cap):")
        for r in results:
            if r.get("timed_out"):
                logger.info(f"    - {r['court']} (+{r['new_count']} new in >{r['duration']:.0f}s)")

    if failed:
        logger.info("\n  Failed scrapers:")
        for r in results:
            if not r["success"]:
                logger.info(f"    - {r['court']}: {r['error']}")

    # Persist health data for dashboard
    health = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "run_duration_s": round(total_elapsed, 1),
        "scrapers": {
            r["court"]: {
                "success": r["success"],
                "timed_out": r.get("timed_out", False),
                "new_count": r["new_count"],
                "skip_count": r["skip_count"],
                "error_count": r["error_count"],
                "none_count": r.get("none_count", 0),
                "our_count": r.get("our_count"),
                "portal_count": r.get("portal_count"),
                "gap": r.get("gap"),
                "duration_s": round(r["duration"], 1),
                "error": r["error"],
                "note": r.get("note"),
            }
            for r in results
        },
    }

    # Post-run disk check
    disk_after = check_disk_usage()
    health["disk"] = disk_after
    logger.info("\n  Disk usage after run:")
    for label, info in disk_after.items():
        logger.info(f"    {label}: {info['used_percent']}% ({info['free_gb']} GB free)")

    if args.source == "manual":
        health_filename = "scraper_health_manual.json"
    elif args.source == "federal":
        health_filename = "scraper_health_federal.json"
    else:
        health_filename = "scraper_health.json"
    health["run_source"] = args.source
    health_path = REPO_DIR / "logs" / health_filename
    health_path.write_text(json.dumps(health, indent=2))
    logger.info(f"Health data written to {health_path}")

    # Soft-fail policy. The daily run aggregates 59 scrapers across federal
    # courts, regulatory bodies, and 26 cantonal portals; a few cantonal
    # portals are chronically flaky (JU/NE blocked from Hetzner IPs and
    # rely on a SOCKS tunnel that drops; ECHR's portal periodically returns
    # partial results). A handful of those failures should not red-light
    # the daily systemd unit if the federal core succeeded, because the
    # operational signal is "is the corpus fresh?", not "is every portal
    # cooperating today?". Two gates:
    #
    #   1. CRITICAL_SCRAPERS — federal courts whose failure IS a real
    #      ops issue. If any of these fail, exit 1.
    #   2. failure_rate — the long-tail flaky cantonal scrapers. If
    #      more than 15 % fail, something systemic is wrong and we
    #      should exit 1; below that, log the failures but exit 0.
    # bstger removed: the Weblaw-backed portal is intermittently unreachable
    # (4 days in May 2026 with "discovery-phase connection failures") and
    # those outages don't reflect a real corpus-freshness regression. Real
    # bstger problems still surface via the failure_rate gate below (any
    # repeated discovery-phase fail count > 15 % of scrapers will still
    # exit 1). bpatger kept — it's hosted by the Federal Patent Court
    # itself, not Weblaw, and its connectivity is reliable.
    CRITICAL_SCRAPERS = {
        "bger", "bvger", "bpatger", "bge",
    }
    critical_failed = [
        r for r in results
        if not r["success"] and r["court"] in CRITICAL_SCRAPERS
    ]
    failure_rate = (failed / len(results)) if results else 0.0
    if critical_failed:
        logger.error(
            f"  Exiting 1: {len(critical_failed)} CRITICAL scraper(s) failed: "
            + ", ".join(sorted(r["court"] for r in critical_failed))
        )
        sys.exit(1)
    if failure_rate > 0.15:
        logger.error(
            f"  Exiting 1: failure rate {100 * failure_rate:.1f}% > 15 % threshold"
        )
        sys.exit(1)
    if failed:
        logger.info(
            f"  {failed} non-critical failure(s) tolerated "
            f"(failure rate {100 * failure_rate:.1f}% ≤ 15 %)"
        )


if __name__ == "__main__":
    main()
