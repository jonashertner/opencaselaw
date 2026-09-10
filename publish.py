#!/usr/bin/env python3
"""
publish.py — Daily publishing pipeline for Swiss Case Law
==========================================================

Orchestration script for VPS cron job. Runs the full pipeline:
  1.  Ingest new entscheidsuche.ch downloads (if entscheidsuche_ingest.py exists)
  2.  Build/update FTS5 database
  2d. Quality enrichment (titles, regeste, dates, hashes, dedup)
  2b. Quality report (optional)
  2c. Build reference graph (citations + statutes, ~78 min)
  3.  Export database/JSONL → Parquet
  4.  Upload Parquet + dataset card to HuggingFace
  5.  Generate stats.json
  6.  Git commit + push docs/stats.json

Most steps are wrapped in try/except — failures are logged. Critical steps
(FTS5, Parquet) will skip subsequent guarded steps (HF upload, git push) to
avoid publishing an incomplete dataset.

Cron:
    15 3 * * * cd /opt/caselaw/repo && python3 publish.py >> logs/publish.log 2>&1

Usage:
    python3 publish.py              # run full pipeline
    python3 publish.py --step 3     # run only step 3 (export)
    python3 publish.py --dry-run    # log what would happen
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import shutil
import signal
import urllib.request
import logging
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

LOCK_FILE_PATH = "/tmp/opencaselaw-publish.lock"
CHECKPOINT_PATH = Path("/tmp/opencaselaw-publish-checkpoint.json")
NTFY_TOPIC = "opencaselaw-publish"  # https://ntfy.sh/opencaselaw-publish


def _notify(title: str, message: str, *, priority: str = "default"):
    """Send push notification via ntfy.sh (best-effort, never fails the pipeline)."""
    try:
        req = urllib.request.Request(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=message.encode(),
            headers={"Title": title, "Priority": priority},
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass  # notification failure must never break the pipeline


def _append_run_record(record: dict) -> None:
    """Append one JSON line to state/publish_runs.jsonl.

    The pipeline's only durable structured record. Until 2026-08-19 a run
    left behind 109 overwritten bytes on success and NOTHING on failure
    (the failure branch exited before any marker) — per-step timings were
    computed, logged as text and lost, so a 13h41m → 17h07m build creep
    and a gate timeout were invisible until they hurt. Append-only, one
    line per step and one summary per run, written on success AND failure.

    Telemetry must never break the pipeline: any error here is swallowed.
    """
    try:
        state_dir = REPO_DIR / "state"
        state_dir.mkdir(exist_ok=True)
        with open(state_dir / "publish_runs.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _save_checkpoint(step_num, results: dict):
    """Save completed step so pipeline can resume after crash."""
    CHECKPOINT_PATH.write_text(json.dumps({
        "last_completed_step": str(step_num),
        "results": {str(k): v for k, v in results.items()},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }))


def _load_checkpoint() -> dict | None:
    """Load checkpoint from prior crashed run (if any).

    TTL is 4h (was 12h). The daily timer fires every ~24h, so a 12h TTL
    leaves a wide window where yesterday's checkpoint can be re-used by
    today's timer-triggered run if even one step failed (defeating the
    refresh purpose of the daily publish). 4h is short enough to never
    bleed into the next daily run, yet long enough for a manual resume
    after a crash within the same publish window.
    """
    if CHECKPOINT_PATH.exists():
        try:
            data = json.loads(CHECKPOINT_PATH.read_text())
            age_hours = (datetime.now(timezone.utc) - datetime.fromisoformat(data["timestamp"])).total_seconds() / 3600
            if age_hours < 4:
                return data
        except Exception:
            pass
    return None


def _clear_checkpoint():
    """Remove checkpoint after successful completion."""
    CHECKPOINT_PATH.unlink(missing_ok=True)

logger = logging.getLogger("publish")

REPO_DIR = Path(__file__).parent.resolve()
OUTPUT_DIR = REPO_DIR / "output"
DATASET_DIR = OUTPUT_DIR / "dataset"
DOCS_DIR = REPO_DIR / "docs"
DB_PATH = OUTPUT_DIR / "decisions.db"

HF_REPO_ID = "voilaj/swiss-caselaw"

# Build_fts5 writes decisions.db.tmp (~63 GB) plus a .tmp-wal that peaks
# around 10 GB, then atomically swaps. We keep a 7 GB safety margin so the
# concurrent decision_structure sidecar build (step 2g) doesn't squeeze the
# volume during the brief window before the .tmp is replaced.
DATA_VOLUME = "/mnt/HC_Volume_104655575"
BUILD_DISK_REQUIRED_GB = 80




def run_cmd(
    cmd: list[str],
    description: str,
    dry_run: bool = False,
    timeout: int = 3600,
    stall_timeout: int | None = 5400,
    on_line=None,
    outcome_sink: dict | None = None,
) -> bool:
    """Run a command, return True on success.

    outcome_sink, when given, receives {"timed_out": bool, "stalled": bool,
    "returncode": int|None} so a caller can distinguish "the command was
    killed at its wall-clock cap" from "the command ran and said no". The
    QC gate needs that distinction: a timeout says nothing about corpus
    quality, but until 2026-08-22 it was indistinguishable from a CRITICAL
    verdict and cascade-skipped the HF upload and both git pushes (08-18,
    08-21 — both timeouts, zero regressions).

    Streams stdout/stderr line-by-line to the logger instead of buffering
    the full output in memory (avoids OOM on long-running steps like
    build_fts5 or graph build that can produce hundreds of MB of output).

    on_line: optional callback invoked once per stdout line BEFORE it
    reaches the logger. Used by Step 2 to release the publish lock the
    instant build_fts5 prints its OCL_SWAP_DONE sentinel, without
    waiting for the rest of the integrity-check tail. Callback
    exceptions are caught and logged so a faulty hook can't crash the
    publish.

    Two independent kill-switches:
      - ``timeout``: hard wall-clock cap (default 3600 s). The wall-clock
        bound has to accommodate the longest legitimate step (Step 2c
        reference graph at 10800 s).
      - ``stall_timeout``: kill the process if no output line is received
        for this many seconds (default 5400 s = 90 min). Catches the
        "process is alive but wedged" class — silent OOM, deadlocked DB,
        infinite loop. Bumped from the 30 min initial value on
        2026-05-02 after that watchdog killed a healthy build mid-dedup
        (build_fts5 dedup is silent for ~45 min by design; optimize is
        silent for ~45 min; both are legitimate). 90 min covers the
        longest legitimate silent phase with 2× margin. Set to None to
        disable.
    """
    logger.info(f"  $ {' '.join(cmd)}")
    if dry_run:
        logger.info("  [dry-run] skipped")
        return True
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,  # merge stderr into stdout to avoid pipe deadlock
            text=True,
            cwd=str(REPO_DIR),
            # start_new_session=True makes the child a process-group leader
            # (its PID == its PGID) so we can kill the WHOLE group via
            # os.killpg, which catches grandchildren build_fts5 may spawn.
            # 2026-05-07 incident: Step 2 wall-clock fired at 03:20:05 but
            # build_fts5 kept running, swap happened 3h 20m later — the old
            # proc.kill() only signalled the immediate child, missed the
            # ionice→nice→python chain or a child SQLite worker still in D.
            start_new_session=True,
        )
        # Watchdog timers: kill the process either on wall-clock timeout
        # OR on output-stall timeout. We can't rely on proc.wait(timeout=)
        # because the for-loop over proc.stdout blocks until EOF
        # (i.e. process exit).
        timed_out = threading.Event()
        stalled = threading.Event()
        last_output_at = [time.time()]

        def _kill_pg(reason: str) -> None:
            """Kill the entire process group: SIGTERM, 5s grace, then SIGKILL.
            Belt-and-braces against children that survive a single SIGKILL
            to the leader (the 2026-05-07 incident).
            """
            try:
                pgid = os.getpgid(proc.pid)
            except (ProcessLookupError, OSError):
                # Process already dead.
                return
            try:
                os.killpg(pgid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError):
                pass
            # Give it 5 seconds for atomic-swap / file-handle close to finish.
            for _ in range(50):
                if proc.poll() is not None:
                    return
                time.sleep(0.1)
            try:
                os.killpg(pgid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                pass
            # SIGKILL queued — give kernel up to 30s to deliver
            # (D-state syscalls may delay kill arrival).
            for _ in range(300):
                if proc.poll() is not None:
                    return
                time.sleep(0.1)
            logger.warning(
                f"  process group {pgid} still alive 30s after SIGKILL "
                f"({reason}); subprocess may be wedged in D state"
            )

        def _kill_on_timeout():
            timed_out.set()
            _kill_pg("wall-clock timeout")

        def _kill_on_stall():
            while proc.poll() is None and not timed_out.is_set():
                idle = time.time() - last_output_at[0]
                if stall_timeout is not None and idle > stall_timeout:
                    stalled.set()
                    _kill_pg("stall watchdog")
                    return
                time.sleep(min(60, max(5, (stall_timeout or 60) // 4)))

        wall_timer = threading.Timer(timeout, _kill_on_timeout)
        wall_timer.start()
        stall_thread = None
        if stall_timeout is not None:
            stall_thread = threading.Thread(target=_kill_on_stall, daemon=True)
            stall_thread.start()
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                last_output_at[0] = time.time()
                line = line.rstrip("\n")
                if line:
                    if on_line is not None:
                        try:
                            on_line(line)
                        except Exception as _hook_err:
                            logger.warning(
                                f"  on_line callback raised "
                                f"{type(_hook_err).__name__}: {_hook_err}"
                            )
                    logger.info(f"  | {line}")
            proc.wait()
        finally:
            wall_timer.cancel()
        if outcome_sink is not None:
            outcome_sink["timed_out"] = timed_out.is_set()
            outcome_sink["stalled"] = stalled.is_set()
            outcome_sink["returncode"] = proc.returncode
        if timed_out.is_set():
            logger.error(f"  timed out after {timeout}s (wall-clock)")
            return False
        if stalled.is_set():
            logger.error(
                f"  stalled — no output for {stall_timeout}s; killed by watchdog"
            )
            return False
        if proc.returncode != 0:
            logger.error(f"  exit code {proc.returncode}")
            return False
        return True
    except Exception as e:
        logger.error(f"  failed: {e}")
        if outcome_sink is not None:
            outcome_sink.setdefault("timed_out", False)
            outcome_sink.setdefault("stalled", False)
            outcome_sink.setdefault("returncode", None)
        return False


def step_1_ingest(dry_run: bool = False) -> bool:
    """Step 1: Ingest new entscheidsuche.ch downloads."""
    logger.info("Step 1: Ingest entscheidsuche downloads")

    ingest_script = REPO_DIR / "entscheidsuche_ingest.py"
    if not ingest_script.exists():
        # Try scrapers directory
        ingest_script = REPO_DIR / "scrapers" / "entscheidsuche_ingest.py"
    if not ingest_script.exists():
        logger.info("  No ingest script found, skipping")
        return True

    return run_cmd(
        [sys.executable, str(ingest_script)],
        "Ingest entscheidsuche downloads",
        dry_run,
    )


def _cleanup_stale_build_artifacts() -> None:
    """Remove .tmp/.tmp-wal/.tmp-shm leftover from a crashed prior build.

    Only removes files older than 6 h to avoid clobbering an in-flight build
    invoked outside publish.py (the lockfile already prevents concurrent
    publish runs, so this is defence-in-depth for manual build_fts5 invocations).
    """
    candidates = [
        f"{DATA_VOLUME}/output/decisions.db.tmp",
        f"{DATA_VOLUME}/output/decisions.db.tmp-wal",
        f"{DATA_VOLUME}/output/decisions.db.tmp-shm",
        # `.quick` is a SQLite quickcheck snapshot left behind by a
        # crashed build_fts5 PRAGMA quick_check pass. It can reach 60 GB
        # and burned the 2026-05-02 nightly when the disk filled up.
        f"{DATA_VOLUME}/output/decisions.db.quick",
        f"{DATA_VOLUME}/output/.reference_graph.db.tmp-journal",
        f"{DATA_VOLUME}/output/.decisions.db.tmp-journal",
        # decision_structure rebuild artefacts. The full rebuild writes
        # to a sibling .tmp file that can grow to ~45 GB. A crash mid-
        # build leaves the .tmp orphaned and burns the next nightly
        # at pre-flight. Once decision_structure.db lives on /mnt
        # (post-2026-05-02 symlink) the .tmp lands there too; covering
        # both legacy /opt and current /mnt paths is defence-in-depth.
        f"{DATA_VOLUME}/output/decision_structure.db.tmp",
        f"{DATA_VOLUME}/output/decision_structure.db.tmp-journal",
        "/opt/caselaw/repo/output/decision_structure.db.tmp",
        "/opt/caselaw/repo/output/decision_structure.db.tmp-journal",
        "/opt/caselaw/repo/output/decision_structure.db.partial-2026-04-29",
    ]
    now = time.time()
    for path in candidates:
        try:
            st = os.stat(path)
        except FileNotFoundError:
            continue
        age_h = (now - st.st_mtime) / 3600
        if age_h < 6:
            logger.warning(
                f"  Stale-build cleanup: skipping {path} "
                f"(age {age_h:.1f}h < 6h, possibly active)"
            )
            continue
        try:
            os.unlink(path)
            logger.warning(
                f"  Stale-build cleanup: removed {path} "
                f"({st.st_size / 1e9:.1f} GB freed, age {age_h:.1f}h)"
            )
        except OSError as e:
            logger.error(f"  Stale-build cleanup failed for {path}: {e}")


def _preflight_disk_check() -> bool:
    """Verify /mnt has enough room for build_fts5's transient .tmp + .tmp-wal."""
    import shutil
    if not Path(DATA_VOLUME).exists():
        logger.warning(f"  Pre-flight: {DATA_VOLUME} not present, skipping check")
        return True
    free_gb = shutil.disk_usage(DATA_VOLUME).free / 1e9
    if free_gb < BUILD_DISK_REQUIRED_GB:
        logger.error(
            f"PRE-FLIGHT FAILED: {DATA_VOLUME} has {free_gb:.1f} GB free, "
            f"build needs ~{BUILD_DISK_REQUIRED_GB} GB transient. "
            f"Top consumers in {DATA_VOLUME}/output:"
        )
        out = Path(f"{DATA_VOLUME}/output")
        if out.exists():
            top = sorted(
                ((p, p.stat().st_size) for p in out.iterdir() if p.is_file()),
                key=lambda x: -x[1],
            )[:10]
            for p, sz in top:
                logger.error(f"    {sz / 1e9:>6.1f} GB  {p.name}")
        return False
    logger.info(
        f"  Pre-flight: {DATA_VOLUME} has {free_gb:.1f} GB free "
        f"(need >= {BUILD_DISK_REQUIRED_GB})"
    )
    return True


def _parse_worker_ports(systemctl_list_units_output: str) -> list:
    """Extract worker ports from `systemctl list-units mcp-server@*.service`
    (--plain --no-legend) output. Pure + unit-tested."""
    ports = []
    for line in systemctl_list_units_output.splitlines():
        parts = line.split()
        if parts and parts[0].startswith("mcp-server@") and parts[0].endswith(".service"):
            ports.append(parts[0][len("mcp-server@"):-len(".service")])
    return sorted(set(ports))


def _recycle_mcp_workers(dry_run: bool = False) -> None:
    """Roll-restart the mcp-server@ SSE workers to release handles to the
    just-swapped (now-deleted) decisions.db inode.

    After the Step 2 atomic swap, each serving worker keeps pooled SQLite
    connections open on the OLD decisions.db inode, pinning that ~70 GB file
    (unlinked, not yet freed) until the process recycles. With the data volume
    near capacity this starved the post-build aux tier: on 2026-07-08,
    reference_graph + decision_structure both hit 'database or disk is full'
    because ~130 GB of orphaned inodes were pinned by 106 worker handles. A
    rolling restart (one worker at a time, gated on /health) releases them with
    zero serving downtime.

    Non-fatal: logs and continues on any error so a recycle hiccup never fails
    the build. No-op under dry-run or when the units are absent (dev box).
    """
    if dry_run:
        logger.info("  [dry-run] would roll-restart mcp-server@ workers post-swap")
        return
    try:
        out = subprocess.run(
            ["systemctl", "list-units", "mcp-server@*.service",
             "--state=active", "--no-legend", "--plain", "--no-pager"],
            capture_output=True, text=True, timeout=30,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return  # no systemctl (dev box) — nothing to recycle
    ports = _parse_worker_ports(out.stdout)
    if not ports:
        logger.info("  post-swap recycle: no active mcp-server@ workers; skipping")
        return
    logger.info(
        f"  post-swap recycle: rolling restart of {len(ports)} workers "
        f"({', '.join(ports)}) to free the old decisions.db inode"
    )
    for port in ports:
        try:
            subprocess.run(
                ["systemctl", "restart", f"mcp-server@{port}.service"],
                check=False, timeout=60,
            )
        except subprocess.SubprocessError as e:
            logger.warning(f"    worker {port}: restart error {e}; continuing")
            continue
        healthy = False
        for _ in range(15):
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/health", timeout=3,
                ) as r:
                    if r.status == 200:
                        healthy = True
                        break
            except Exception:  # noqa: BLE001 - keep polling until the deadline
                pass
            time.sleep(1)
        logger.info(f"    worker {port}: {'ok' if healthy else 'TIMEOUT (continuing)'}")
        time.sleep(2)


def step_2_build_fts5(
    dry_run: bool = False,
    full_rebuild: bool = False,
    on_line=None,
) -> bool:
    """Step 2: Build/update FTS5 search database.

    Always uses full rebuild: builds to .db.tmp then atomic os.replace().
    This avoids DB locks with live MCP workers (immutable=1 connections).

    on_line: optional per-stdout-line callback. The publish driver
    passes a hook that releases the publish lock when build_fts5
    prints its ``OCL_SWAP_DONE`` sentinel — so the lock isn't held
    through the post-swap integrity_check tail (1–3 h on the 60 GB
    DB) and quick_publish can fold fresh BGer poller scrapes into
    the live DB during that window.
    """
    script = REPO_DIR / "build_fts5.py"
    if not script.exists():
        logger.error("  build_fts5.py not found")
        return False

    logger.info("Step 2: Full FTS5 rebuild (low I/O priority, zero-downtime swap)")

    # Pre-flight: clean stale .tmp from a prior crashed build, then check we
    # have room for the new build. Fail fast (skip the 90-min crash cycle).
    if not dry_run:
        _cleanup_stale_build_artifacts()
        if not _preflight_disk_check():
            return False

    # Use ionice/nice to prevent I/O starvation of live MCP workers.
    # Best-effort class (-c2), not idle (-c3): under serving load the idle class
    # got fully starved, stalling build_fts5 optimize past the nightly cap.
    # Proven 2026-06-23: optimize 35min (best-effort) vs >4h (idle). See memory
    # incident_2026_06_23_build_starvation_salvage.
    cmd = ["ionice", "-c2", "nice", "-n", "10",
           sys.executable, str(script), "--output", str(OUTPUT_DIR),
           "--full-rebuild"]

    # Wall-clock cap. History:
    #   18000s (5h) — too tight; hit 2026-05-04 03:30 mid-optimize.
    #   25200s (7h) — too tight; hit 2026-05-07 03:20 after 1.46M
    #     wayback_queue + heavy König cleanup pushed total to 10h 20m.
    #   43200s (12h) — current. Today's worst case (10h 20m) + 1h 40m
    #     cushion. The 16h unit-level TimeoutStartSec is the outer cap.
    # Note: with the 2026-05-07 process-group kill fix, even if this
    # cap fires, the entire build_fts5 tree dies within ~6s — no more
    # silent overrun + cascade-skip + post-mortem-class incidents.
    timeout = 43200  # 12h hard cap; legitimate completion ranges 4–10h

    # Stall watchdog: the FTS5 'optimize' phase + post-swap
    # PRAGMA integrity_check both emit NO stdout for ~1–3 h each.
    # History:
    #   5400s (1.5h) — too tight; killed mid-optimize 2026-05-04.
    #   10800s (3h)  — bumped 2026-05-04; tripped today (2026-05-11)
    #                  because integrity_check on the post-swap 60 GB DB
    #                  ran exactly 3h under disk contention. The 13:41
    #                  watchdog kill cascade-skipped Steps 4/6 even
    #                  though the swap had succeeded at 10:40.
    #   14400s (4h)  — current. Empirical post-swap integrity_check
    #                  ranges 2h 55m – 3h 20m on this hardware. 4h
    #                  gives a ~40-min cushion. The new
    #                  OCL_SWAP_DONE handshake (commit b4ba734) means
    #                  quick_publish can run DURING this window, which
    #                  adds disk contention and is the reason the
    #                  previous cap got hit.
    return run_cmd(cmd, "Build FTS5 database", dry_run,
                   timeout=timeout, stall_timeout=14400,
                   on_line=on_line)


def step_2b_quality_report(dry_run: bool = False, full_rebuild: bool = False) -> bool:
    """Step 2b: Generate quality report and check gates."""
    logger.info("Step 2b: Quality report")

    script = REPO_DIR / "quality_report.py"
    if not script.exists():
        logger.info("  quality_report.py not found, skipping")
        return True

    if not DB_PATH.exists():
        logger.info("  Database not found, skipping quality report")
        return True

    return run_cmd(
        [sys.executable, str(script),
         "--db", str(DB_PATH),
         "--output", str(OUTPUT_DIR / "quality_report.json"),
         "--gate"],
        "Quality report",
        dry_run,
        timeout=7200,
    )


def step_2c_build_reference_graph(dry_run: bool = False, full_rebuild: bool = False) -> bool:
    """Step 2c: Build reference graph (citations + statutes)."""
    logger.info("Step 2c: Build reference graph")

    # Use the INCREMENTAL builder in forced-full mode rather than
    # build_reference_graph.py directly. Same inputs (both read
    # decisions.db), same full rebuild — but this one also writes the
    # `meta` and `processed_decisions` state tables.
    #
    # Without that state a subsequent --in-place incremental run finds no
    # diff base (_select_diff_base -> "no_state") and bootstraps the whole
    # graph from scratch, ~3h22m measured on production, instead of
    # applying a delta in ~50min. That is what blocks the weekday-
    # incremental cutover. The step comment below has said "Real fix:
    # build_reference_graph_incremental.py" since 2026-06-03; this is it.
    script = REPO_DIR / "search_stack" / "build_reference_graph_incremental.py"
    if not script.exists():
        logger.info("  build_reference_graph_incremental.py not found, skipping")
        return True

    if not DB_PATH.exists():
        logger.info("  FTS5 database not found, skipping reference graph")
        return True

    graph_db = OUTPUT_DIR / "reference_graph.db"
    return run_cmd(
        [sys.executable, str(script),
         "--decisions-db", str(DB_PATH),
         "--graph-db", str(graph_db),
         "--force-full",
         "--in-place"],
        "Build reference graph",
        dry_run,
        # Bumped 7200→10800 (2026-05-01), then 10800→18000 (2026-06-03 STOPGAP):
        # the full builder is ~78min solo but ran >3h and hit the 10800s cap on
        # the 06-02 nightly under 4-way post-build I/O contention (see
        # PARALLEL_MAX_WORKERS, now 2). 5h leaves margin until 2c moves to the
        # incremental builder. Real fix: build_reference_graph_incremental.py.
        timeout=18000,
    )


def step_2d_enrich_quality(dry_run: bool = False, full_rebuild: bool = False) -> bool:
    """Step 2d: Enrich data quality (titles, regeste, dates, hashes, dedup)."""
    logger.info("Step 2d: Quality enrichment")

    script = REPO_DIR / "scripts" / "enrich_quality.py"
    if not script.exists():
        logger.info("  enrich_quality.py not found, skipping")
        return True

    if not DB_PATH.exists():
        logger.info("  FTS5 database not found, skipping enrichment")
        return True

    cmd = [
        sys.executable, str(script),
        "--db", str(DB_PATH),
        "--output", str(OUTPUT_DIR),
    ]
    if dry_run:
        cmd.append("--dry-run")

    return run_cmd(cmd, "Quality enrichment", dry_run, timeout=7200)


def step_2e_build_anwaltsrecht_tags(dry_run: bool = False, full_rebuild: bool = False) -> bool:
    """Step 2e: Build Anwaltsrecht tags DB from SAV PDFs."""
    logger.info("Step 2e: Build Anwaltsrecht tags")

    script = REPO_DIR / "search_stack" / "build_anwaltsrecht_tags.py"
    if not script.exists():
        logger.info("  build_anwaltsrecht_tags.py not found, skipping")
        return True

    if not DB_PATH.exists():
        logger.info("  FTS5 database not found, skipping Anwaltsrecht tags")
        return True

    tags_db = OUTPUT_DIR / "anwaltsrecht_tags.db"
    return run_cmd(
        [sys.executable, str(script),
         "--fts5-db", str(DB_PATH),
         "--output", str(tags_db)],
        "Build Anwaltsrecht tags",
        dry_run,
        timeout=600,
    )


# Coverage gate for the structure sidecar swap: the new sidecar must index at
# least this share of the current decisions the old one indexed. A broken
# extractor then keeps yesterday's sidecar instead of shipping an empty index.
STRUCTURE_COVERAGE_FLOOR = 0.98


def _structure_coverage(sidecar: Path, decisions_db: Path) -> int | None:
    """Current decisions with at least one indexed Erwägung. Reads only the two
    covering indexes (decision ids), never the 50 GB of paragraph text."""
    import sqlite3
    try:
        dec = sqlite3.connect(f"file:{decisions_db}?mode=ro&immutable=1", uri=True)
        try:
            ids = {row[0] for row in dec.execute("SELECT decision_id FROM decisions")}
        finally:
            dec.close()
        side = sqlite3.connect(f"file:{sidecar}?mode=ro&immutable=1", uri=True)
        try:
            structured = {row[0] for row in side.execute("SELECT DISTINCT decision_id FROM erwaegungen_paragraph")}
        finally:
            side.close()
    except Exception as exc:  # noqa: BLE001 — the gate must never crash the pipeline
        logger.warning(f"  structure coverage of {sidecar.name} could not be measured: {exc}")
        return None
    return len(ids & structured)


def step_2g_build_decision_structure(dry_run: bool = False, full_rebuild: bool = False) -> bool:
    """Step 2g: Rebuild decision_structure.db sidecar from the SERVED text.

    Since 2026-09-06 the sidecar is extracted from decisions.db full_text — the
    text get_decision serves and a reader can check a pinpoint against — by
    search_stack/extract_decision_structure_incremental.py (state-diffed, so a
    normal night re-extracts only new or changed decisions; a bumped
    EXTRACTOR_VERSION re-extracts everything once). The old shard-based build
    read raw JSONL text and, for the historical BGE volumes, indexed nothing;
    the shadow runs of the incremental extractor showed 89% of BGE and 76% of
    the corpus with an indexed Erwägung against 66% / 72.5%.

    The new sidecar is written to decision_structure.db.tmp, passes a coverage
    gate (STRUCTURE_COVERAGE_FLOOR of the old sidecar's indexed current
    decisions) and is then swapped in atomically. If the extractor fails, the
    shard-based build runs as before, so a bad night never leaves the sidecar
    missing.
    """
    logger.info("Step 2g: Build decision_structure sidecar (from served text)")
    # Kill switch (2026-09-08): the served-text extractor loaded every
    # decision's full_text into RAM (35 GB), pinned the publish cgroup at
    # MemoryHigh and starved serving for hours, twice. OCL_SERVED_TEXT_STRUCTURE=0
    # in .env.publish keeps the shard-built sidecar until the extractor streams.
    if os.environ.get("OCL_SERVED_TEXT_STRUCTURE", "1").strip().lower() in {
            "0", "false", "off", "no"}:
        logger.warning("  OCL_SERVED_TEXT_STRUCTURE=0: served-text extractor disabled; "
                       "building the sidecar from shards")
        return _step_2g_from_shards(dry_run)
    script = REPO_DIR / "search_stack" / "extract_decision_structure_incremental.py"
    decisions_db = OUTPUT_DIR / "decisions.db"
    live = OUTPUT_DIR / "decision_structure.db"
    if not script.exists() or not decisions_db.exists():
        logger.warning("  incremental extractor or decisions.db not found; building from shards")
        return _step_2g_from_shards(dry_run)
    # The live sidecar is a symlink to the data volume in production, while
    # OUTPUT_DIR itself is on the 150 GB root disk. The extractor writes its
    # working copy next to --output and this step swaps onto it, so both must
    # be derived from the RESOLVED path: a tmp beside the symlink would fill
    # the root disk (~55 GB sidecar vs ~32 GB free) and the swap would replace
    # the symlink with a file on /. Review finding 2026-09-08.
    live_real = live.resolve() if live.exists() else live
    tmp = live_real.with_name(live_real.name + ".tmp")
    for stale in (tmp, Path(str(tmp) + "-journal")):
        if stale.exists():
            stale.unlink()
    if live_real.exists():
        free = shutil.disk_usage(live_real.parent).free
        need = int(live_real.stat().st_size * 1.2)
        if free < need:
            logger.error(f"  {live_real.parent} has {free / 1e9:.1f} GB free, the sidecar rebuild "
                         f"needs ~{need / 1e9:.1f} GB; keeping the current sidecar")
            return False
    if os.environ.get("OCL_STRUCTURE_FORCE_FULL") == "1":
        logger.warning("  OCL_STRUCTURE_FORCE_FULL=1 is set: full re-extraction of every decision "
                       "(~3 h). Unset it after the one-off, or every night pays this.")
    cmd = [sys.executable, str(script), "--decisions-db", str(decisions_db),
           "--structure-db", str(live_real), "--output", str(tmp)]
    # A full re-extraction is the extractor's own decision: it bootstraps
    # when the sidecar carries no state or a different extractor version.
    # Forcing it on every full build (and on every `--step 2g`, which counts
    # as one) re-extracted all ~1.07M decisions daily; the 2026-09-07 run
    # did that into memory and was killed by the stall watchdog. A normal
    # night now diffs and writes only new or changed decisions (minutes).
    # OCL_STRUCTURE_FORCE_FULL=1 keeps a manual override for a one-off.
    if os.environ.get("OCL_STRUCTURE_FORCE_FULL") == "1":
        cmd.append("--force-full")
    ok = run_cmd(cmd, "Build decision_structure sidecar (served text, incremental)", dry_run,
                 timeout=14400, stall_timeout=9000)
    if dry_run:
        return True
    if not ok or not tmp.exists():
        if tmp.exists():
            tmp.unlink()
        # A first bootstrap can outlast the wall clock; the extractor keeps
        # its partial working copy and resumes next run. The shard build is
        # only the safety net for a MISSING sidecar: rebuilding an existing,
        # readable one costs ~4.5 h and gains nothing.
        if live_real.exists() and _structure_coverage(live_real, decisions_db):
            logger.error("  served-text sidecar build failed; keeping the current sidecar "
                         "(a bootstrap resumes on the next run)")
            return False
        logger.error("  served-text sidecar build failed and no sidecar is served; "
                     "falling back to the shard-based build")
        return _step_2g_from_shards(dry_run)
    old = _structure_coverage(live_real, decisions_db) if live_real.exists() else None
    new = _structure_coverage(tmp, decisions_db)
    if new is None:
        logger.error("  new sidecar unreadable; keeping the current one")
        tmp.unlink()
        return False
    if old and new < old * STRUCTURE_COVERAGE_FLOOR:
        logger.error(f"  coverage gate: new sidecar indexes {new:,} current decisions, "
                     f"old one {old:,} (floor {STRUCTURE_COVERAGE_FLOOR:.0%}); keeping the current sidecar")
        tmp.unlink()
        return False
    logger.info(f"  structure coverage: {old if old is not None else '?'} -> {new:,} current decisions; swapping")
    os.replace(tmp, live_real)
    _warm_structure_sidecar(live_real, dry_run)
    return True


def _warm_structure_sidecar(sidecar: Path, dry_run: bool = False) -> None:
    """After a sidecar swap the new 52 GB file has no page cache; the pinpoint
    attach in search_decisions then does random reads per top result and
    fresh searches took 80-96 s (2026-09-10 00:20 UTC). Reading the covering
    indexes once (index-only queries, ~100 s) brings that back to seconds.
    Never fails the step: the swap already happened and is correct."""
    script = REPO_DIR / "scripts" / "warm_structure_sidecar.py"
    if not script.exists():
        logger.warning("  warm_structure_sidecar.py not found; sidecar stays cold until first use")
        return
    cmd = ["ionice", "-c", "2", "-n", "7", sys.executable, str(script),
           "--structure-db", str(sidecar), "--budget-s", "900"]
    try:
        ok = run_cmd(cmd, "Warm decision_structure sidecar indexes", dry_run, timeout=1200)
    except Exception as e:  # noqa: BLE001 — warm-up is best effort
        ok = False
        logger.warning(f"  sidecar warm-up raised {type(e).__name__}: {e}")
    if not ok:
        logger.warning("  sidecar warm-up did not complete; searches stay slow until the cache fills")


def _step_2g_from_shards(dry_run: bool = False) -> bool:
    """The pre-2026-09-06 build, kept as the fallback: extract from the raw JSONL shards
    (Sachverhalt / Erwägungen-Paragraphs / Dispositiv / Regeste).

    Federal + cantonal + regulatory courts. Reads every JSONL shard
    in OUTPUT_DIR/decisions/ that has the canonical
    `<court>.jsonl` / `es_<court>.jsonl` shape, skipping backups
    (.bak*, .broken) and tmp files (tmp*).  Writes sidecar SQLite
    with atomic swap. Used by get_decision_structure / get_erwaegung /
    get_regeste MCP tools and to enrich get_case_brief responses.

    Auto-glob means new shards (e.g. when a canton's first scraper
    lands) are picked up automatically without a publish.py edit.
    """
    logger.info("Step 2g: Build decision_structure sidecar")

    script = REPO_DIR / "search_stack" / "extract_decision_structure.py"
    if not script.exists():
        logger.info("  extract_decision_structure.py not found, skipping")
        return True

    decisions_dir = OUTPUT_DIR / "decisions"
    if not decisions_dir.exists():
        logger.warning(f"  {decisions_dir} not found, skipping")
        return True

    # Glob every shard, exclude backups / tmp / broken files.
    candidates = sorted(decisions_dir.glob("*.jsonl"))
    skip_patterns = (".bak", ".broken", ".tmp", ".old")
    shard_names = []
    for path in candidates:
        name = path.name
        if any(p in name for p in skip_patterns):
            continue
        # tmp* files (no extension match) — handled by name prefix check
        stem = path.stem  # filename without trailing .jsonl
        if stem.startswith("tmp") and stem[3:4].isalnum():
            continue
        shard_names.append(stem)

    if not shard_names:
        logger.warning("  no shards found, skipping")
        return True

    logger.info(f"  building from {len(shard_names)} shards")
    shards_arg = ",".join(shard_names)

    return run_cmd(
        [sys.executable, str(script), "--build",
         "--shards", shards_arg,
         "--decisions-dir", str(decisions_dir),
         "--output", str(OUTPUT_DIR / "decision_structure.db")],
        f"Build decision_structure sidecar ({len(shard_names)} shards)",
        dry_run,
        timeout=14400,  # 2026-06-03 STOPGAP: full build now runs >2h (outgrew
        # the "~1h" estimate) and hit the old 7200s cap on the 06-02 nightly
        # under 4-way I/O contention. Real fix: extract_decision_structure_incremental.
        # The new FTS5 'rebuild' + 'optimize' phase added in commit
        # b8e4cf3 (find_relevant_erwaegung infra) emits no stdout while
        # SQLite rewrites the index over ~970K paragraph rows. On the
        # 2026-05-04 publish that silent phase ran past the default
        # 5400s stall watchdog and got killed mid-rebuild — leaving the
        # decision_structure.db sidecar (and therefore the FTS5 index
        # find_relevant_erwaegung depends on) un-built. Bumped to 9000s
        # (2.5h) — wide enough for the silent finalisation window
        # observed in production, narrow enough to still catch a
        # genuinely-wedged process within a few hours.
        stall_timeout=9000,
    )


def step_3_export_parquet(dry_run: bool = False) -> bool:
    """Step 3: Export SQLite/JSONL corpus to Parquet."""
    logger.info("Step 3: Export Parquet")

    script = REPO_DIR / "export_parquet.py"
    if not script.exists():
        logger.error("  export_parquet.py not found")
        return False

    # Step 3 is distribution only (nothing users see waits on it), and the
    # decisions + graph exports alone take ~49 min (2026-09-10). The structure
    # add-ons in export_parquet.py budget themselves against
    # OCL_EXPORT_WALLCLOCK_BUDGET_S, kept 5 min under the timeout here: 2 h
    # gives the Sunday paragraphs export (4.8 GB, ~12 min) and the nightly
    # structure metadata (~30-75 min on the served-text sidecar) room without
    # ever letting the step itself time out and cascade-skip the HuggingFace
    # upload and the git pushes (2026-09-09 incident).
    step_timeout_s = 7200
    cmd = ["env", f"OCL_EXPORT_WALLCLOCK_BUDGET_S={step_timeout_s - 300}",
           sys.executable, str(script),
           "--input", str(OUTPUT_DIR / "decisions"),
           "--output", str(DATASET_DIR)]
    # The erwaegungen-paragraphs artifact is 4.8 GB (P1.4) — weekly cadence
    # only (Sunday, aligned with the full-snapshot rhythm); the lean
    # structure.parquet + graph exports ride every run.
    if datetime.now(timezone.utc).weekday() == 6:
        cmd.append("--structure-paragraphs")
    return run_cmd(cmd, "Export Parquet", dry_run, timeout=step_timeout_s)


def step_3b_build_verification_pack(dry_run: bool = False) -> bool:
    """Step 3b: Build the offline verification pack and publish it (weekly).

    scripts/build_verification_pack.py writes one SQLite file — decision
    metadata with the service's own citation strings, docket aliases,
    canonical representations, every indexed Erwägung (zlib per row) — for
    `ocl --local`: citation, pinpoint and quotation checks on a machine
    that never sends a draft anywhere. Reads decisions.db and the fresh
    decision_structure.db sidecar (a full scan of the paragraph table, so
    Sundays only; OCL_PACK=1 forces it). The gzip goes to the HuggingFace
    mirror as artifacts/verification_pack/<date>.sqlite.gz plus
    latest.sqlite.gz. Non-fatal.
    """
    if datetime.now(timezone.utc).weekday() != 6 and os.environ.get("OCL_PACK") != "1":
        logger.info("Step 3b: verification pack is weekly (Sunday); skipping")
        return True
    logger.info("Step 3b: Build verification pack")
    script = REPO_DIR / "scripts" / "build_verification_pack.py"
    decisions_db = OUTPUT_DIR / "decisions.db"
    structure_db = OUTPUT_DIR / "decision_structure.db"
    manifest_db = OUTPUT_DIR / "representation_manifest.db"
    pack_dir = DATASET_DIR / "artifacts" / "verification_pack"
    if not script.exists() or not decisions_db.exists() or not structure_db.exists():
        logger.warning("  builder or inputs missing; skipping")
        return True
    pack_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    output = pack_dir / f"{stamp}.sqlite"
    cmd = [sys.executable, str(script), "--decisions-db", str(decisions_db), "--structure-db", str(structure_db),
           "--output", str(output), "--repo-dir", str(REPO_DIR), "--gzip"]
    if manifest_db.exists():
        cmd += ["--manifest-db", str(manifest_db)]
    ok = run_cmd(cmd, "Build verification pack", dry_run, timeout=7200, stall_timeout=3600)
    if dry_run:
        return True
    gz = output.with_name(output.name + ".gz")
    if not ok or not gz.exists():
        logger.error("  verification pack not built")
        return False
    try:
        output.unlink()  # keep the gzip only; the pack is rebuilt weekly
    except OSError:
        pass
    for stale in sorted(pack_dir.glob("*.sqlite.gz"))[:-2]:
        try:
            stale.unlink()
        except OSError:
            pass
    try:
        from huggingface_hub import HfApi
        api = HfApi()
        for name in (gz.name, "latest.sqlite.gz"):
            api.upload_file(path_or_fileobj=str(gz), path_in_repo=f"artifacts/verification_pack/{name}",
                            repo_id=HF_REPO_ID, repo_type="dataset")
        logger.info(f"  uploaded {gz.name} ({gz.stat().st_size / 1e9:.2f} GB) and latest.sqlite.gz to {HF_REPO_ID}")
    except Exception as exc:  # noqa: BLE001 — the pack file stays on disk for a manual upload
        logger.error(f"  verification pack upload failed: {exc}")
        return False
    return True


def step_4_upload_hf(dry_run: bool = False) -> bool:
    """Step 4: Upload Parquet + dataset card to HuggingFace."""
    logger.info("Step 4: Upload to HuggingFace")

    if dry_run:
        logger.info("  [dry-run] would upload to HuggingFace")
        return True

    try:
        from huggingface_hub import HfApi
    except ImportError:
        logger.error("  huggingface_hub not installed. Run: pip install huggingface_hub")
        return False

    if not DATASET_DIR.exists():
        logger.error(f"  Dataset directory not found: {DATASET_DIR}")
        return False

    parquet_files = list(DATASET_DIR.glob("*.parquet"))
    if not parquet_files:
        logger.error("  No Parquet files to upload")
        return False

    # Defence in depth: DATASET_DIR is never cleaned, so a stale parquet from
    # before a court joined EXCLUDED_COURTS would still be globbed and pushed.
    # FAIL the step rather than skip the file — silently uploading non-CC0
    # material under a CC0 licence tag must not look like a successful publish.
    from export_parquet import EXCLUDED_COURTS
    blocked = sorted(p.stem for p in parquet_files if p.stem in EXCLUDED_COURTS)
    if blocked:
        logger.error(
            "  Refusing to upload non-CC0 court(s) to %s: %s. "
            "Delete the stale file(s) from %s and re-run.",
            HF_REPO_ID, ", ".join(blocked), DATASET_DIR,
        )
        return False

    try:
        api = HfApi()

        # Upload dataset card
        card_path = REPO_DIR / "dataset_card.md"
        if card_path.exists():
            api.upload_file(
                path_or_fileobj=str(card_path),
                path_in_repo="README.md",
                repo_id=HF_REPO_ID,
                repo_type="dataset",
            )
            logger.info("  Uploaded dataset card")

        # Upload Parquet files to data/ directory (batch upload).
        # graph/ is EXCLUDED here: its tables have different schemas and a
        # nested parquet under data/ would break the HF load_dataset config —
        # it gets its own repo path below (P2.4).
        logger.info(f"  Uploading {len(parquet_files)} Parquet files to data/...")
        api.upload_folder(
            folder_path=str(DATASET_DIR),
            path_in_repo="data",
            repo_id=HF_REPO_ID,
            repo_type="dataset",
            allow_patterns="*.parquet",
            ignore_patterns=["graph/*", "structure/*"],
            delete_patterns="*.parquet",  # prune remote parquet not in local folder
        )

        logger.info(f"  Uploaded {len(parquet_files)} files to {HF_REPO_ID}")

        # Aux exports, each to its OWN repo path (a foreign-schema parquet
        # under data/ would break the load_dataset config): graph/ = 8.65M
        # resolved citation edges + 11.86M statute refs (P2.4); structure/ =
        # section metadata + erwaegungen paragraph segmentation (P1.4).
        # Own try/except per dir: an aux hiccup must not fail the main
        # dataset upload that already succeeded.
        for aux in ("graph", "structure"):
            aux_dir = DATASET_DIR / aux
            if aux_dir.exists() and list(aux_dir.glob("*.parquet")):
                try:
                    api.upload_folder(
                        folder_path=str(aux_dir),
                        path_in_repo=aux,
                        repo_id=HF_REPO_ID,
                        repo_type="dataset",
                        allow_patterns="*.parquet",
                        delete_patterns="*.parquet",
                    )
                    logger.info(f"  Uploaded aux parquet to {aux}/")
                except Exception as e:
                    logger.error(f"  {aux}/ upload failed (main dataset upload unaffected): {e}")

        return True

    except Exception as e:
        logger.error(f"  HuggingFace upload failed: {e}")
        return False


def step_2f_build_materialien(dry_run: bool = False, full_rebuild: bool = False) -> bool:
    """Step 2f: Rebuild materialien.db (Botschaft refs + digests + debates)."""
    logger.info("Step 2f: Build materialien.db")

    script = REPO_DIR / "search_stack" / "build_materialien_db.py"
    if not script.exists():
        logger.info("  build_materialien_db.py not found, skipping")
        return True

    materialien_dir = REPO_DIR / "data" / "materialien"
    if not materialien_dir.exists():
        logger.info("  data/materialien/ not found, skipping")
        return True

    return run_cmd(
        [sys.executable, "-m", "search_stack.build_materialien_db",
         "--input-dir", str(materialien_dir)],
        "Build materialien.db",
        dry_run,
        # 600 → 1800 s (2026-09-09): the step re-indexes botschaft_paragraphs_fts
        # every night (38 s at 424k paragraphs, measured on the VPS); the
        # historical Botschaft backfill takes it to ~4M paragraphs, 3-4 min on a
        # quiet night and no margin under 600 s on a loaded weekday afternoon.
        timeout=1800,
    )


def step_2h_build_legal_scholarship(dry_run: bool = False, full_rebuild: bool = False) -> bool:
    """Step 2h: Rebuild legal_scholarship.db (OA Swiss legal publications).

    Runs WEEKLY on Sunday by default — academic publications + commentaries
    don't change at caselaw cadence, and once university IRs + e-periodica
    are activated the OAI-PMH walks will harvest tens of thousands of
    records (multi-hour). Sunday-gating keeps the nightly publish lean
    while still keeping the corpus fresh.

    Override with OCL_PUBLISH_SCHOLARSHIP_WEEKDAY (0=Mon … 6=Sun;
    -1 = any day, for ad-hoc catch-up runs).

    Steps when the gate is open:
      1. Harvest all active OA scholarship sources via OAI-PMH
      2. Build the unified FTS5 DB (atomic swap) re-exporting commentaries
    """
    logger.info("Step 2h: Build legal_scholarship.db")

    try:
        target_weekday = int(
            os.environ.get("OCL_PUBLISH_SCHOLARSHIP_WEEKDAY", "6")
        )
    except ValueError:
        target_weekday = 6
    if target_weekday >= 0:
        today = datetime.now(timezone.utc).weekday()
        if today != target_weekday:
            logger.info(
                "  weekday=%d ≠ target=%d; skipping (scholarship rebuilds weekly on Sunday)",
                today, target_weekday,
            )
            return True

    builder = REPO_DIR / "search_stack" / "build_legal_scholarship.py"
    if not builder.exists():
        logger.info("  build_legal_scholarship.py not found, skipping")
        return True

    harvest_ok = run_cmd(
        [sys.executable, "-m", "scrapers.scholarship.harvest_all"],
        "Harvest OA legal scholarship sources",
        dry_run,
        timeout=3600,
    )
    if not harvest_ok:
        logger.warning("  scholarship harvest failed; building from existing JSONL only")

    return run_cmd(
        [sys.executable, "-m", "search_stack.build_legal_scholarship"],
        "Build legal_scholarship.db",
        dry_run,
        timeout=900,
    )


def _ensure_representation_manifest(dry_run: bool = False) -> None:
    """Rebuild the cross-identifier representation manifest against the freshly
    swapped decisions.db so generate_stats can emit a generation-matched
    unique-decision count.

    Read-only w.r.t. serving: it writes ONLY output/representation_manifest.db (a
    sidecar nothing serves from yet) and never touches decisions.db. Fully
    failure-tolerant: on any failure generate_stats omits the unique block (it
    treats an absent or generation-mismatched sidecar gracefully). ~10 min on the
    full corpus; runs here because step 5a is post-swap (final DB, stable inode)."""
    script = REPO_DIR / "scripts" / "build_representation_manifest.py"
    if not script.exists():
        logger.warning("  build_representation_manifest.py not found; skipping dual-count")
        return
    ok = run_cmd(
        [sys.executable, str(script)],
        "Rebuild representation manifest (cross-identifier dual-count)",
        dry_run,
        timeout=1800,        # 30 min hard cap (build is ~10 min)
        stall_timeout=None,  # one long silent scan phase; the wall-clock cap suffices
    )
    if not ok:
        logger.warning("  representation manifest rebuild failed (non-fatal); "
                       "stats.json will omit or mark-stale the unique count")


def step_5_generate_stats(dry_run: bool = False) -> bool:
    """Step 5: Generate stats.json from database."""
    logger.info("Step 5: Generate stats.json")

    # Build the dual-count sidecar first (non-fatal) so stats.json carries a
    # generation-matched unique-decision estimate alongside the record count.
    _ensure_representation_manifest(dry_run)

    script = REPO_DIR / "generate_stats.py"
    if not script.exists():
        logger.error("  generate_stats.py not found")
        return False

    return run_cmd(
        [sys.executable, str(script),
         "--db", str(DB_PATH),
         "--output", str(DOCS_DIR / "stats.json"),
         # interesting_stats is heavy (full scans on decisions.db +
         # reference_graph.db). The early-tier run skips it; Step 5e
         # below recomputes JUST the interesting_stats block AFTER
         # Step 2c rebuilds reference_graph, so the dashboard isn't
         # showing fresh corpus counts paired with last-week's graph
         # numbers (caught in 2026-05-16 code review).
         "--no-interesting-stats"],
        "Generate stats",
        dry_run,
    )


def step_5e_interesting_stats(dry_run: bool = False) -> bool:
    """Step 5e: Recompute stats.json with FRESH reference_graph counts
    AFTER Step 2c rebuilds reference_graph.db.

    The early Step 5a runs BEFORE reference_graph rebuild and writes
    docs/stats.json with the previous build's citation/statute edges
    in the *corpus* block (collect_corpus_stats reads reference_graph.db).
    The dashboard reads ``stats.corpus.citation_edges`` and
    ``stats.corpus.statute_edges`` for the "Graph & doctrine" card —
    so until this step runs, those numbers can lag a full nightly.

    A FULL re-run (no --interesting-stats-only / --no-interesting-stats
    flags) recomputes BOTH the corpus block (with fresh graph counts)
    AND the interesting_stats block (top-cited / most-cited statute /
    graph_size). The 2026-05-16-evening fix using --interesting-stats-only
    refreshed the wrong block — caught in code review same day.

    Non-fatal: on failure the dashboard keeps whatever Step 5a wrote
    (early-tier counts + previous build's graph). Step 6 commits the
    merged result regardless.
    """
    logger.info("Step 5e: Full stats regenerate (post-graph refresh)")
    script = REPO_DIR / "generate_stats.py"
    if not script.exists():
        logger.warning("  generate_stats.py not found, skipping")
        return True
    graph_db = OUTPUT_DIR / "reference_graph.db"
    if not graph_db.exists():
        logger.warning("  reference_graph.db not found, skipping (Step 2c didn't run)")
        return True
    return run_cmd(
        [sys.executable, str(script),
         "--db", str(DB_PATH),
         "--output", str(DOCS_DIR / "stats.json")],
        "Refresh stats.json with fresh graph counts",
        dry_run,
        timeout=3600,  # full stats incl. interesting_stats: 30-60 min worst case
    )


def step_5b_generate_feeds(dry_run: bool = False) -> bool:
    """Step 5b: Generate RSS feeds for the dashboard.

    Static RSS 2.0 XML files written to docs/feed.xml + docs/feeds/*.xml,
    based on the latest decisions in decisions.db. Reads decisions.db with
    immutable=1 (no lock contention with build_fts5 / 2d / 2e).

    Non-fatal (NON_FATAL_STEPS): the feeds are a convenience artifact. On a
    miss the previous run's XML stays in docs/ and is what 6a/6 push, so a
    feed failure must not turn the unit red, disarm
    state/last_publish_success.json or block the checkpoint clear.
    2026-09-03: the step timed out at its 300 s cap on a weekday (sdb at
    96-100 %util) with every other step OK, and the whole run reported
    FAILED; the cause was the six filtered feeds sorting every row of their
    court/language (~1.4 M random page reads on a 70 GB table). The query now
    walks idx_decisions_date newest-first (generate_feeds.py), so the step
    should take seconds; the 300 s cap is kept as the regression alarm.
    """
    logger.info("Step 5b: Generate RSS feeds")
    script = REPO_DIR / "generate_feeds.py"
    if not script.exists():
        logger.warning("  generate_feeds.py not found, skipping")
        return True
    return run_cmd(
        [sys.executable, str(script),
         "--db", str(DB_PATH),
         "--out", str(DOCS_DIR)],
        "Generate RSS feeds",
        dry_run,
        timeout=300,
    )


def step_7_publish_delta(dry_run: bool = False) -> bool:
    """Step 7: Publish delta artifacts and optional SQLite snapshot to HuggingFace.

    Env-gated by `OCL_PUBLISH_DELTA=1` — off by default until the new
    pipeline has been validated end-to-end. When OFF, the step logs
    "skipped" and returns True (non-fatal).

    A full compressed SQLite base snapshot can be published independently
    with `OCL_PUBLISH_SQLITE_SNAPSHOT=1`. This is intended for occasional
    bootstrap snapshots, not every daily delta run — so even when the env
    var is set, the snapshot path only fires on the configured weekday
    (default Sunday). Override the day via
    `OCL_PUBLISH_SQLITE_SNAPSHOT_WEEKDAY=N` (0=Mon … 6=Sun; -1 = any day,
    for ad-hoc forced runs). The PR-supplied auto-prune-previous default
    keeps HF working-tree storage flat at one snapshot (~14 GB).

    Requires a seeded snapshot at state/hf_delta_snapshot.json. Seed once
    with `python3 -m search_stack.publish_delta --seed` BEFORE enabling.
    Without a seed, the first run would treat the entire corpus as "new".

    Replaces the broken private-repo pipeline
    (`jonashertner/caselaw-repo/daily_update.yml`) that's been publishing
    federal-less deltas for 30+ days. Keep that workflow disabled while
    this is on — two pipelines racing will corrupt artifacts/manifest.json.
    """
    publish_delta_enabled = os.environ.get("OCL_PUBLISH_DELTA") == "1"
    snapshot_env_set = os.environ.get("OCL_PUBLISH_SQLITE_SNAPSHOT") == "1"
    # Cadence gate: default to Sunday (weekday()==6) so we honour the PR's
    # "occasional bootstrap snapshot" intent and HF LFS history doesn't
    # bloat by ~14 GB/day. Override with OCL_PUBLISH_SQLITE_SNAPSHOT_WEEKDAY:
    # set to an int 0-6 to pick a different day, or -1 to force any day
    # (useful for one-off catch-up snapshots).
    try:
        snapshot_weekday = int(
            os.environ.get("OCL_PUBLISH_SQLITE_SNAPSHOT_WEEKDAY", "6")
        )
    except ValueError:
        snapshot_weekday = 6
    today_weekday = datetime.utcnow().weekday()
    snapshot_day_matches = (
        snapshot_weekday == -1 or today_weekday == snapshot_weekday
    )
    publish_snapshot_enabled = snapshot_env_set and snapshot_day_matches
    if snapshot_env_set and not snapshot_day_matches:
        logger.info(
            "Step 7: snapshot env set but today (weekday=%d) != "
            "configured snapshot day (%d) — skipping snapshot, delta only",
            today_weekday, snapshot_weekday,
        )
    if not publish_delta_enabled and not publish_snapshot_enabled:
        logger.info(
            "Step 7: Publish artifacts — DISABLED "
            "(set OCL_PUBLISH_DELTA=1 and/or OCL_PUBLISH_SQLITE_SNAPSHOT=1 to enable)"
        )
        return True

    logger.info("Step 7: Publish artifacts to HuggingFace")
    snapshot_path = REPO_DIR / "state" / "hf_delta_snapshot.json"
    if publish_delta_enabled and not snapshot_path.exists():
        logger.error("  state/hf_delta_snapshot.json missing — run with --seed first")
        return False

    cmd = [
        sys.executable, "-m", "search_stack.publish_delta",
        "--db", str(DB_PATH),
        # Scratch on the data volume (168G+ free), not root /tmp — the
        # unbounded /tmp/caselaw_delta_build growth was the suspected cause of
        # the 2026-06-15 root-fill publish failure. publish_delta also bounds
        # it to the last BUILD_DIR_RETENTION dated dirs.
        "--build-dir", "/mnt/HC_Volume_104655575/caselaw_delta_build",
    ]
    if publish_delta_enabled:
        cmd += ["--snapshot", str(snapshot_path)]
    else:
        cmd += ["--snapshot-only"]
    if publish_snapshot_enabled:
        cmd += ["--publish-snapshot"]
    if dry_run:
        cmd += ["--dry-run"]

    # 60-min cap absorbs the full-SQLite snapshot path: zstd level=10
    # compression of the ~61 GB DB plus HF upload of the ~15-20 GB
    # compressed artifact can easily exceed 30 min on typical bandwidth.
    # Delta-only runs finish in seconds; the timeout only matters when
    # OCL_PUBLISH_SQLITE_SNAPSHOT=1.
    return run_cmd(
        cmd,
        "Publish artifacts",
        dry_run,
        timeout=3600,
    )


def _sync_homepage_fallbacks(dry_run: bool = False) -> None:
    """Rewrite docs/index.html's static numbers from docs/stats.json.

    Never raises and never returns failure — the script itself is strict
    (unknown markup or implausible values exit non-zero with nothing
    written), and this wrapper degrades every failure to a WARN log line.
    Runs at both push points (6a early, 6 final); the script is idempotent,
    so the second invocation is a no-op when nothing changed in between.
    """
    script = REPO_DIR / "scripts" / "sync_homepage_fallbacks.py"
    if not script.exists():
        logger.warning("  sync_homepage_fallbacks.py missing — homepage "
                       "fallbacks not refreshed this run")
        return
    if dry_run:
        logger.info("  [dry-run] would sync docs/index.html from stats.json")
        return
    try:
        proc = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True, text=True, timeout=60, cwd=str(REPO_DIR),
        )
        line = (proc.stdout or proc.stderr or "").strip().splitlines()
        logger.info("  homepage sync: %s", line[-1] if line else f"exit {proc.returncode}")
        if proc.returncode != 0:
            logger.warning("  homepage sync exited %s — docs/index.html "
                           "left as committed (non-fatal)", proc.returncode)
    except Exception as e:
        logger.warning("  homepage sync failed (non-fatal): %s", e)


def step_6_git_push(dry_run: bool = False) -> bool:
    """Step 6: Git commit + push docs/stats.json + docs/feed.xml + docs/feeds/
    + docs/integrity/ (the daily Merkle root from Step 5f) + docs/index.html
    (homepage fallbacks re-derived from stats.json)."""
    logger.info("Step 6: Git commit + push stats.json + feeds + integrity")

    stats_file = DOCS_DIR / "stats.json"
    if not stats_file.exists():
        logger.warning("  docs/stats.json does not exist, skipping")
        return True

    # Homepage static fallbacks: rewrite from the stats.json generated just
    # above, so the numbers non-JS clients see (crawlers, ~76 % of traffic)
    # can never drift again — found 2026-08-22 two months stale (991'298
    # rendered against a live 1'054'206) because nothing regenerated them.
    # Non-fatal by contract: a one-day-stale homepage is cosmetic, a failed
    # publish is not. See docs/proposals/homepage-fallback-regeneration.md.
    _sync_homepage_fallbacks(dry_run)

    # Files we publish on every cycle. The diff check below short-circuits
    # if none of them changed.
    paths = ["docs/stats.json", "docs/feed.xml", "docs/feeds",
             "docs/quality.json", "docs/quality.html", "docs/index.html",
             # docs/integrity/ = the daily RFC-6962 Merkle root (Step 5f). It
             # was omitted here, so the public integrity page froze at the
             # 2026-05-21 commit while the nightly kept regenerating it
             # uncommitted. Including it keeps the provenance root current.
             "docs/integrity"]

    # Check if any of these have unstaged changes
    result = subprocess.run(
        ["git", "diff", "--quiet", "--", *paths],
        capture_output=True, cwd=str(REPO_DIR),
    )
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "--", *paths],
        capture_output=True, text=True, cwd=str(REPO_DIR),
    )
    if result.returncode == 0 and not untracked.stdout.strip():
        logger.info("  No changes to stats.json / feeds, skipping")
        return True

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    if dry_run:
        logger.info(f"  [dry-run] would commit and push stats + feeds ({today})")
        return True

    ok = run_cmd(["git", "add", *paths], "git add", dry_run)
    if not ok:
        return False

    ok = run_cmd(
        ["git", "commit", "-m", f"Update stats.json + feeds ({today})"],
        "git commit",
        dry_run,
    )
    if not ok:
        return False

    # Pull --rebase to avoid conflicts when local commits were pushed
    # from development machines between cron runs.  Stash with --include-untracked
    # to handle temp scripts and other untracked files that block rebase.
    run_cmd(["git", "stash", "--include-untracked"], "git stash --include-untracked", dry_run)
    rebase_ok = run_cmd(["git", "pull", "--rebase", "origin", "main"], "git pull --rebase", dry_run)
    run_cmd(["git", "stash", "pop"], "git stash pop", dry_run)
    if not rebase_ok:
        return False
    return run_cmd(["git", "push"], "git push", dry_run)


def _run_parallel_post_build(deferred: list, args, manual_step_mode: bool) -> dict:
    """Execute deferred parallel-safe steps concurrently.

    All steps in `deferred` are read-only of decisions.db; each opens its own
    SQLite connection and writes to its own output file. (The swapped
    decisions.db is journal_mode=DELETE, not WAL, so concurrent readers contend
    on disk I/O — see PARALLEL_MAX_WORKERS.) Uses a ThreadPoolExecutor — each step internally
    spawns a subprocess via run_cmd, so threads block on subprocess I/O while
    the OS schedules workloads across CPU cores. No pickling, no shared state.

    Returns dict {step_id: bool}. Failures are logged and reported per-step;
    one failure does not abort the others (independent steps continue).
    """
    import concurrent.futures

    logger.info(
        f"=== Parallel batch start: {len(deferred)} steps, "
        f"{PARALLEL_MAX_WORKERS} workers ==="
    )
    for num, name, _ in deferred:
        logger.info(f"  [parallel-queued] Step {num} ({name})")

    def _call(num, name, func):
        t0 = time.time()
        try:
            if num in ("2b", "2c", "2d", "2e", "2f", "2g"):
                ok = func(
                    dry_run=args.dry_run,
                    full_rebuild=(args.full_rebuild or manual_step_mode),
                )
            else:
                ok = func(dry_run=args.dry_run)
            return num, name, ok, None, time.time() - t0
        except Exception as e:
            return num, name, False, str(e), time.time() - t0

    results = {}
    batch_start = time.time()
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=PARALLEL_MAX_WORKERS,
        thread_name_prefix="post-build",
    ) as executor:
        futures = [executor.submit(_call, num, name, func) for num, name, func in deferred]
        for future in concurrent.futures.as_completed(futures):
            num, name, ok, err, elapsed = future.result()
            results[num] = ok
            status = "OK" if ok else "FAILED"
            err_suffix = (" — " + err) if err else ""
            logger.info(
                f"  [parallel-done] Step {num} ({name}): {status} "
                f"({elapsed:.1f}s){err_suffix}"
            )

    total_elapsed = time.time() - batch_start
    n_pass = sum(1 for v in results.values() if v)
    logger.info(
        f"=== Parallel batch done in {total_elapsed:.1f}s "
        f"({n_pass}/{len(deferred)} passed) ==="
    )
    return results


def step_5g_generate_coverage(dry_run: bool = False) -> bool:
    """Step 5g: Generate docs/coverage.json — the per-court denominators
    table (corpus counts by year + portal totals from scraper_health +
    curated gap notes; backlog P3.1). The script itself never exits
    non-zero: a coverage hiccup must not fail a publish."""
    logger.info("Step 5g: Generate coverage.json")
    script = REPO_DIR / "scripts" / "generate_coverage.py"
    if not script.exists():
        logger.warning("  generate_coverage.py not found, skipping")
        return True
    return run_cmd(
        [sys.executable, str(script), "--db", str(DB_PATH)],
        "Generate coverage.json",
        dry_run,
    )


def step_5c_quality_gate(dry_run: bool = False) -> bool | str:
    """Step 5c: Pre-push QC gate.

    Runs `python -m quality.cli run --critical-only --gate` against the
    just-built decisions.db. The CLI exits 1 iff any CRITICAL check
    failed; that propagates here as `return False`, which marks Step 5c
    as failed and (because 5c is in CRITICAL_STEPS) skips the guarded
    Step 6 git pushes — so a regression never reaches users.

    A TIMEOUT is not a verdict. The gate runs AFTER the atomic swap, so
    it guards distribution (HF upload, git pushes), not serving — and a
    scan killed at its wall-clock cap says nothing about corpus quality.
    Both recent gate failures (08-18, 08-21) were exactly-3600s timeouts
    with zero regressions behind them, and each cost a full distribution
    cycle. On timeout/stall this step now returns the WARN string below:
    truthy, so no cascade — distribution proceeds; recorded in the
    summary and last_publish_success.non_fatal_failures; alerted via
    ntfy as DEGRADED so the slow scan still gets investigated. A genuine
    CRITICAL verdict (exit 1) still returns False and still blocks.

    The gate ALSO writes the full report to docs/quality.json (for the
    public dashboard) and to quality/reports/latest.json + dated
    archive (for the per-run audit trail).

    Costs ~3-8 minutes on the production DB (after the 2026-05-02 fix
    that makes ``--critical-only`` actually skip W-only modules).
    Generous 1800s timeout so a slow nightly never re-creates the
    cascade incident we hit on 2026-05-02 (full-corpus run pegged the
    earlier 600s budget). Warnings + drift run separately.
    """
    logger.info("Step 5c: Quality-control gate (block on CRITICAL regression)")
    if dry_run:
        logger.info("  [dry-run] would run quality.cli --critical-only --gate")
        return True

    docs_quality_json = DOCS_DIR / "quality.json"

    cmd = [
        sys.executable, "-m", "quality.cli", "run",
        "--critical-only", "--gate",
        "--db", str(REPO_DIR / "output" / "decisions.db"),
        "--output", str(docs_quality_json),
    ]
    # Bumped 1800→3600 after the 2026-05-28 publish failure where the gate
    # timed out under disk-I/O contention from concurrent build_fts5 + graph
    # rebuild steps. The checks themselves passed (8/8) when given time;
    # the limit was wall-clock, not a real regression. The ceiling is NOT
    # raised further — timeout is now a WARN, so it no longer needs headroom
    # to protect distribution, and keeping it tight preserves the signal
    # that the scan is outgrowing its budget.
    _gate = {}
    ok = run_cmd(cmd, "QC gate", dry_run, timeout=3600, outcome_sink=_gate)
    if not ok and (_gate.get("timed_out") or _gate.get("stalled")):
        how = "timed out" if _gate.get("timed_out") else "stalled"
        logger.warning(
            "  Step 5c DEGRADED: gate %s before reaching a verdict — "
            "distribution proceeds; corpus quality is UNVERIFIED this run. "
            "Investigate the scan duration (qc-gate p90 has been creeping "
            "toward the 3600s ceiling).", how,
        )
        try:
            _notify(
                "Publish DEGRADED — QC gate " + how,
                "The gate hit its wall-clock cap without a verdict. "
                "HF upload and git pushes are proceeding on the previous "
                "night's green plus today's non-gate checks. This alert is "
                "about scan runtime, not corpus quality — but tonight's "
                "corpus is unverified until the next green gate.",
                priority="high",
            )
        except Exception:
            logger.exception("(failed to send gate-timeout alert)")
        return f"WARN (gate {how} — distribution proceeded, verdict unknown)"
    if not ok:
        # Send a focused alert with the failing-check names so the
        # operator knows what triggered the block.
        try:
            import json as _json
            if docs_quality_json.exists():
                payload = _json.loads(docs_quality_json.read_text())
                fails = [
                    r["name"] for r in payload.get("results", [])
                    if r.get("severity") == "critical" and not r.get("passed")
                ]
                _notify(
                    "Publish BLOCKED — QC gate failed",
                    "Critical: " + ", ".join(fails[:5])
                    + (f" (+{len(fails)-5} more)" if len(fails) > 5 else "")
                    + "\nGit push skipped. Investigate then re-run.",
                    priority="urgent",
                )
        except Exception:
            logger.exception("(failed to format QC alert)")
    return ok


def step_5f_integrity_root(dry_run: bool = False) -> bool:
    """Step 5f: Compute the daily Merkle root over the full corpus.

    Bestimmung 06 of the Open Law Standards. RFC-6962 Merkle tree over
    every decision's (decision_id, cli:ch, ECLI, content_hash,
    decision_date). Writes ``docs/integrity/<YYYY-MM-DD>.{root,json}``
    so the daily commit (Step 6) anchors the corpus state to a
    verifiable Merkle root.

    Non-fatal: a build failure does not block git push. The publish
    still ships; only the day's integrity anchor is missing.

    Stamps via OpenTimestamps automatically if the ``ots`` CLI is
    available (pip install opentimestamps-client).
    """
    logger.info("Step 5f: Compute integrity Merkle root (Bestimmung 06)")
    script = REPO_DIR / "scripts" / "build_integrity_root.py"
    if not script.exists():
        logger.warning("  build_integrity_root.py not found, skipping")
        return True
    cmd = [
        sys.executable,
        str(script),
        "--db", str(REPO_DIR / "output" / "decisions.db"),
        "--out-dir", str(DOCS_DIR / "integrity"),
    ]
    # Observed runtimes on production VPS for ~973k decisions:
    # 2026-05-21 → 14 min, 2026-05-22 → 22 min, 2026-05-23 → 30 min,
    # 2026-05-24 → >30 min (hit the prior 1800s cap). Growth of ~7
    # min/day driven by BGer-poller lock contention overlapping the
    # 13:30–14:00 UTC integrity window. 60 min cap absorbs the trend;
    # if 5f starts exceeding this, profile the SQL iteration or move
    # the integrity step outside the poller's business-hours window.
    return run_cmd(cmd, "integrity root", dry_run, timeout=3600)


def step_5d_release_manifest(dry_run: bool = False) -> bool:
    """Step 5d: Append-only release manifest.

    Writes ``releases/<YYYY-MM-DD>/manifest.json`` with corpus counts,
    content-hash totals, gate state, schema version, and license info.
    Committed by Step 6 to GitHub so the repo history is the immutable
    audit trail. Lets anyone reconstruct what was live on a given date
    for citation / reproducibility / forensic use.

    Non-fatal: a manifest-write failure does not block git push (the
    publish itself is still safe; only the audit trail is missing).
    """
    logger.info("Step 5d: Generate release manifest (releases/<date>/manifest.json)")
    if dry_run:
        logger.info("  [dry-run] would run scripts/generate_release_manifest.py")
        return True
    cmd = [
        sys.executable,
        str(REPO_DIR / "scripts" / "generate_release_manifest.py"),
        "--db", str(REPO_DIR / "output" / "decisions.db"),
        "--repo", str(REPO_DIR),
        "--quality-json", str(DOCS_DIR / "quality.json"),
        "--out-root", str(REPO_DIR / "releases"),
    ]
    return run_cmd(cmd, "release manifest", dry_run, timeout=120)


def step_6b_health_check(dry_run: bool = False) -> bool:
    """Step 6b: Auto-validate the publish output.

    Runs scripts/post_publish_health_check.py at the very end of the pipeline.
    The check exercises 7 independent assertions (publish status, total count,
    SG chambers, archive shards, EGMR cleanup, cantonal Erwägungen, top-10
    courts). If any FAIL: returns False → publish.py exits non-zero → systemd
    marks the service failed → OnFailure drop-in fires the alert.

    The König #1 EGMR regression was caught manually today; codifying the check
    means every future nightly self-validates without operator review.

    Threshold tuning: the health check uses conservative bounds (e.g. SG
    sg_kantonsgericht ≥ 1050 vs measured 1074) so normal day-to-day fluctuation
    won't false-positive. See scripts/post_publish_health_check.py for the
    measured 2026-04-30 baselines that informed the thresholds.
    """
    logger.info("Step 6b: Post-publish health check (auto-validate)")
    script = REPO_DIR / "scripts" / "post_publish_health_check.py"
    if not script.exists():
        logger.warning("  scripts/post_publish_health_check.py not found, skipping")
        return True
    return run_cmd(
        [sys.executable, str(script)],
        "Health check",
        dry_run,
        timeout=600,
    )


# Execution order intentionally differs from the step IDs to preserve the
# existing CLI surface (`--step 2b`, `--step 2c`, `--step 2d`) while ensuring
# weekly enrichment happens before quality gating and graph construction.
# Two-tier pipeline:
# - FAST tier (Steps 1→2→5→6): runs daily, completes in ~3.5h.
#   After FTS5 build + atomic swap, immediately regenerate stats.json
#   and push to GitHub so the site shows today's date.
# - SLOW tier (Steps 2d→2e→2b→2c→3→4→5→6): enrichment, graph,
#   Parquet export, HuggingFace upload, final stats refresh + push.
#   Runs after the fast tier. Can be skipped on --fast-only.
#
# The key insight: stats.json + git push happen TWICE — once after
# FTS5 (fast, site-visible) and again after everything else finishes
# (includes updated graph counts, quality report, etc.).

# Steps that READ decisions.db but write to separate output files. Safe to
# run concurrently after Step 2d (Quality Enrichment) — the only step that
# WRITES decisions.db. NOTE: the swapped decisions.db is journal_mode=DELETE
# (build_fts5 switches WAL→DELETE before os.replace), NOT WAL — so concurrent
# readers contend on disk I/O (and a rollback journal); PARALLEL_MAX_WORKERS
# (not a free-lunch fan-out) is therefore the real tuning knob. Each step writes
# its own output (reference_graph.db, decision_structure.db,
# anwaltsrecht_tags.db, materialien.db, dataset/*.parquet, quality_report.json).
PARALLEL_POST_BUILD_STEPS = {"2e", "2b", "2c", "2f", "2g", 3}

# Concurrency cap: disk I/O on /mnt is the binding constraint — 2c (graph) +
# 2g (sidecar) + 3 (parquet) all stream the 60 GB decisions.db at once. Reduced
# 4→2 on 2026-06-03 (STOPGAP): 4-way contention thrashed the volume and pushed
# 2c past its cap and 2g past its on the 06-02 nightly. 2 workers keeps the
# heavy O(corpus) builders from co-saturating the disk. Revisit (→4) once 2c/2g
# move to the incremental builders (minutes, not hours).
PARALLEL_MAX_WORKERS = 2


# ── DAG runner integration (Phase B v0.2) ─────────────────────────────
#
# OCL_USE_DAG=1 hands the pipeline over to publish_dag.run_targets()
# instead of the linear for-loop in main(). Default OFF — every nightly
# and every CI run continues to use the existing path. The opt-in flag
# lets us validate the DAG runner against real workloads before flipping
# the default in a future commit.
#
# Limitations of v0.2 (intentional, to keep the wiring small):
#   • Sequential (no parallel post-build) — slower than today's mode
#   • No checkpoint resume — a mid-run failure restarts from scratch
#   • --fast-only is honoured by passing requested=["git_push_early"]
#   • --step N is honoured by mapping N → DAG target name
#   • --ingest is honoured by force-including the opt-in 'ingest' target
#
# v0.3 will add parallel-wave scheduling, checkpoint integration, and
# (assuming clean A/B against today's pipeline) make DAG mode the default.

# Mapping from publish.py step number/key → publish_dag target name.
# Both directions used so we can translate user --step args INTO the
# DAG, and the DAG result dict BACK into the step-keyed summary log
# operators are familiar with.
STEP_TO_DAG_TARGET: dict[int | str, str] = {
    1: "ingest",
    2: "build_fts5",
    "2b": "quality_report",
    "2c": "reference_graph",
    "2d": "enrich_quality",
    "2e": "anwaltsrecht_tags",
    "2f": "materialien_build",
    "2g": "decision_structure",
    3: "export_parquet",
    "3b": "verification_pack",
    4: "upload_hf",
    "5a": "stats_early",
    "5b": "rss_feeds",
    "5c": "qc_gate",
    "5d": "release_manifest",
    "5e": "stats_interesting",
    "6a": "git_push_early",
    7: "publish_delta",
    6: "git_push_final",
    "6b": "health_check",
}


# Steps whose outright failure is logged FAILED but must not exit 1 / turn the
# systemd unit red. Rationale per step sits with NON_FATAL_STEPS in main().
_NON_FATAL_STEPS = frozenset({"2e", "5d", "5e", "2g", "2c", "5b", "3b"})


STEPS = [
    (1, "Ingest", step_1_ingest),
    (2, "Build FTS5", step_2_build_fts5),
    # ── Fast publish: site shows today's date immediately ──
    ("5a", "Generate Stats (early)", step_5_generate_stats),
    ("5b", "Generate RSS Feeds", step_5b_generate_feeds),
    ("5g", "Coverage Table", step_5g_generate_coverage),
    ("5c", "Quality-Control Gate", step_5c_quality_gate),
    # 5d after gate so the manifest captures the gate's verdict + counts.
    # Non-fatal if it fails — git push (Step 6a/6) still runs; the audit
    # trail just gets a missing entry for that day.
    ("5d", "Release Manifest", step_5d_release_manifest),
    ("6a", "Git Push (early)", step_6_git_push),
    # ── Slow tier: enrichment, graph, materialien, export ──
    ("2d", "Quality Enrichment", step_2d_enrich_quality),
    ("2e", "Anwaltsrecht Tags", step_2e_build_anwaltsrecht_tags),
    ("2b", "Quality Report", step_2b_quality_report),
    ("2c", "Reference Graph", step_2c_build_reference_graph),
    ("2f", "Materialien", step_2f_build_materialien),
    ("2g", "Decision Structure", step_2g_build_decision_structure),
    ("2h", "Legal Scholarship", step_2h_build_legal_scholarship),
    (3, "Export Parquet", step_3_export_parquet),
    ("3b", "Verification pack", step_3b_build_verification_pack),
    (4, "Upload HuggingFace", step_4_upload_hf),
    # ── Integrity Merkle root (Bestimmung 06 — Provenienz).
    #    Runs at the end of the slow tier so content_hash is stable
    #    (computed inside Step 2). The root file ships with the final
    #    git push at Step 6. ~14 min for 972k decisions. ──
    ("5f", "Integrity Root", step_5f_integrity_root),
    # ── Delta publish (env-gated; empty no-op until OCL_PUBLISH_DELTA=1) ──
    (7, "Publish Delta", step_7_publish_delta),
    # ── Refresh interesting_stats (graph + top-cited block of stats.json)
    #    AFTER reference_graph has been rebuilt by Step 2c. Without this,
    #    docs/stats.json combines fresh corpus counts with the previous
    #    build's graph numbers (caught in 2026-05-16 review). Merges into
    #    the existing stats.json so step 6 picks up the change. ──
    ("5e", "Stats Interesting (post-graph)", step_5e_interesting_stats),
    # ── Final git push (catches any docs/ changes from the slow tier:
    #    anwaltsrecht_tags, quality_report, Step 5e interesting_stats,
    #    etc.). Step 5 final (the old duplicate full stats run) was
    #    removed 2026-05-15. The Step 6 diff-check short-circuits
    #    cleanly when nothing changed. ──
    (6, "Git Push (final)", step_6_git_push),
    # ── Auto-validate (FAIL → systemd OnFailure → alert) ──
    ("6b", "Health Check", step_6b_health_check),
]


def _build_dag_builder_map() -> dict:
    """Produce the {target_name → callable} dict the DAG runner expects.

    Wraps each existing step_X function so its signature matches the
    runner's contract ``builder(args, *, dry_run, full_rebuild) -> bool``,
    regardless of whether the original step took only ``dry_run`` or
    also ``full_rebuild``. Keeps the step functions themselves untouched
    so the linear path stays a 1:1 baseline for A/B comparison.
    """
    def _wrap(fn, *, accepts_rebuild: bool):
        def wrapped(_args, *, dry_run=False, full_rebuild=False):
            if accepts_rebuild:
                return fn(dry_run=dry_run, full_rebuild=full_rebuild)
            return fn(dry_run=dry_run)
        return wrapped

    return {
        "ingest":             _wrap(step_1_ingest,                       accepts_rebuild=False),
        "build_fts5":         _wrap(step_2_build_fts5,                   accepts_rebuild=True),
        "enrich_quality":     _wrap(step_2d_enrich_quality,              accepts_rebuild=True),
        "anwaltsrecht_tags":  _wrap(step_2e_build_anwaltsrecht_tags,     accepts_rebuild=True),
        "quality_report":     _wrap(step_2b_quality_report,              accepts_rebuild=True),
        "reference_graph":    _wrap(step_2c_build_reference_graph,       accepts_rebuild=True),
        "materialien_build":  _wrap(step_2f_build_materialien,           accepts_rebuild=True),
        "decision_structure": _wrap(step_2g_build_decision_structure,    accepts_rebuild=True),
        "legal_scholarship":  _wrap(step_2h_build_legal_scholarship,     accepts_rebuild=True),
        "export_parquet":     _wrap(step_3_export_parquet,               accepts_rebuild=False),
        "verification_pack":  _wrap(step_3b_build_verification_pack,     accepts_rebuild=False),
        "upload_hf":          _wrap(step_4_upload_hf,                    accepts_rebuild=False),
        "publish_delta":      _wrap(step_7_publish_delta,                accepts_rebuild=False),
        "stats_early":        _wrap(step_5_generate_stats,               accepts_rebuild=False),
        "rss_feeds":          _wrap(step_5b_generate_feeds,              accepts_rebuild=False),
        "qc_gate":            _wrap(step_5c_quality_gate,                accepts_rebuild=False),
        "release_manifest":   _wrap(step_5d_release_manifest,            accepts_rebuild=False),
        "integrity_root":     _wrap(step_5f_integrity_root,              accepts_rebuild=False),
        "stats_interesting":  _wrap(step_5e_interesting_stats,           accepts_rebuild=False),
        "git_push_early":     _wrap(step_6_git_push,                     accepts_rebuild=False),
        "git_push_final":     _wrap(step_6_git_push,                     accepts_rebuild=False),
        "health_check":       _wrap(step_6b_health_check,                accepts_rebuild=False),
    }


def _run_via_dag(args, manual_step_mode: bool) -> int:
    """Execute the publish pipeline through publish_dag.run_targets().

    Phase B v0.2 — opt-in via OCL_USE_DAG=1. Translates publish.py's
    classic CLI flags (--step / --ingest / --fast-only / --full-rebuild
    / --dry-run) into the DAG runner's `requested` list, then maps the
    runner's result dict back into publish.py's classic OK/FAILED/
    SKIPPED summary log so operator-facing output stays familiar.

    Returns systemd-style exit code (0 = OK, 1 = had fatal failures).
    """
    import publish_dag  # imported lazily so the linear path doesn't pay

    builders = _build_dag_builder_map()

    # Translate flags → requested target list:
    #   • --step N            → run only that target (and its closure)
    #   • --fast-only         → stop after git_push_early
    #   • --ingest            → force-include the opt-in 'ingest' target
    #   • (default)           → full pipeline, ingest auto-skipped
    requested: list[str] | None = None
    if manual_step_mode:
        target = STEP_TO_DAG_TARGET.get(args.step) or STEP_TO_DAG_TARGET.get(str(args.step))
        if target is None:
            logger.error(f"--step {args.step!r} maps to no DAG target")
            return 2
        requested = [target]
    elif args.fast_only:
        requested = ["git_push_early"]
        if args.ingest:
            requested.append("ingest")
    elif args.ingest:
        # Want every target including the opt-in ingest. Listing all
        # target names overrides the opt-in skip for ingest while still
        # running the full pipeline.
        requested = list(publish_dag.REGISTRY.keys())

    # OCL_DAG_WORKERS=N enables parallel scheduling: parallel_safe targets
    # run concurrently up to N workers; non-parallel-safe targets still run
    # exclusively. Default 1 (sequential) — A/B parity with the linear path.
    # Today's pipeline uses 4 workers in its post-build batch; matching
    # that recovers the lost speedup once we mark fast-tier targets as
    # parallel_safe in a future commit.
    try:
        max_workers = max(1, int(os.environ.get("OCL_DAG_WORKERS", "1")))
    except ValueError:
        max_workers = 1

    # Checkpoint hooks bridge to publish.py's existing _save_checkpoint /
    # _load_checkpoint helpers so resume works the same way under DAG mode.
    # The save callback translates the DAG target name back to the classic
    # step number/key so the on-disk checkpoint format stays compatible
    # with the linear path's parser.
    target_to_step = {tgt: step for step, tgt in STEP_TO_DAG_TARGET.items()}
    accumulated_results: dict = {}

    def _ckpt_load() -> dict[str, bool | str] | None:
        # Skip checkpoint resume in manual --step mode (the operator
        # explicitly wants to run that one step regardless).
        if manual_step_mode:
            return None
        cp = _load_checkpoint()
        if not cp:
            return None
        # cp["results"] is keyed by step (str numbers like "2", "5c"); the
        # DAG runner expects target names. Translate via STEP_TO_DAG_TARGET.
        out: dict[str, bool | str] = {}
        for step_key, status in cp["results"].items():
            # Try int-key first (e.g. "2" → 2), fall back to str key.
            try:
                lookup_key: int | str = int(step_key)
            except ValueError:
                lookup_key = step_key
            tgt = STEP_TO_DAG_TARGET.get(lookup_key)
            if tgt is None:
                continue
            out[tgt] = status
        if out:
            logger.info(
                f"  Resuming from checkpoint ({sum(1 for v in out.values() if v is True)}"
                f"/{len(out)} targets already OK)"
            )
        return out

    def _ckpt_save(name: str, status: bool | str) -> None:
        # Translate DAG target name back to step key for checkpoint compat.
        snum = target_to_step.get(name, name)
        accumulated_results[snum] = status
        try:
            _save_checkpoint(snum, accumulated_results)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"checkpoint save failed for {name!r}: {e}")

    logger.info(
        f"OCL_USE_DAG=1 — running via publish_dag "
        f"({len(publish_dag.REGISTRY)} targets, "
        f"{'parallel ' + str(max_workers) + 'w' if max_workers > 1 else 'sequential'}, "
        f"checkpoint=on, opt-in)"
    )
    if requested is not None:
        closure_size = len(publish_dag.closure(publish_dag.REGISTRY, requested))
        logger.info(f"  Requested: {requested} (closure: {closure_size} targets)")

    start = time.time()
    dag_results = publish_dag.run_targets(
        publish_dag.REGISTRY, builders, args,
        requested=requested,
        max_workers=max_workers,
        checkpoint_load=_ckpt_load,
        checkpoint_save=_ckpt_save,
    )
    elapsed = time.time() - start

    # Translate DAG result dict → step-keyed summary, mirroring the
    # operator-facing log format the linear path produces.
    target_to_step = {tgt: step for step, tgt in STEP_TO_DAG_TARGET.items()}
    nice_status = {
        publish_dag.OK:               "OK",
        publish_dag.FAILED:           "FAILED",
        publish_dag.SKIPPED_CASCADE:  "SKIPPED (cascade)",
        publish_dag.SKIPPED_OPTIN:    "SKIPPED (opt-in)",
    }
    # Use the DAG's own non-fatal markers (anwaltsrecht_tags + release_manifest).
    NON_FATAL_TARGETS = {n for n, t in publish_dag.REGISTRY.items() if t.non_fatal}
    fatal_failures: list[str] = []
    for tgt_name, status in dag_results.items():
        snum = target_to_step.get(tgt_name, tgt_name)
        nice = nice_status.get(status, str(status))
        logger.info(f"  Step {snum} ({tgt_name}): {nice}")
        if status is publish_dag.FAILED and tgt_name not in NON_FATAL_TARGETS:
            fatal_failures.append(tgt_name)
    logger.info(f"=== DAG pipeline complete in {elapsed:.1f}s ===")
    if fatal_failures:
        logger.error(f"Fatal failures: {fatal_failures}")
        return 1
    return 0


def main():
    parser = argparse.ArgumentParser(description="Swiss Case Law publishing pipeline")
    parser.add_argument(
        "--step", type=str, default=None,
        help="Run only a specific step (1, 2, 2b, 2c, 2d, 2e, 3, 4, 5, 6)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Log actions without executing")
    parser.add_argument(
        "--full-rebuild", action="store_true",
        help="Force full FTS5 rebuild regardless of day of week"
    )
    parser.add_argument(
        "--ingest", action="store_true",
        help="Run entscheidsuche ingest (step 1); skipped by default"
    )
    parser.add_argument(
        "--fast-only", action="store_true",
        help="Stop after FTS5 build + early stats push (skip graph, Parquet, HF upload). "
             "The site shows today's date in ~3.5h instead of ~6h."
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    logger.info(f"=== Swiss Case Law publish pipeline — {datetime.now(timezone.utc).isoformat()} ===")

    # Prevent concurrent publish runs (cron + manual overlap)
    lock_file = open(LOCK_FILE_PATH, "w")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError):
        # Exit non-zero: a run that could not start is a missed publish.
        # With `return` the unit stayed "success" and nobody was paged when
        # the daily full build lost its slot to a still-running incremental
        # (2026-09-04 Stage A review). OnFailure= on the unit turns this
        # into an alert; the timer simply fires again the next day.
        logger.error("Another publish process is already running. Exiting.")
        sys.exit(2)
    logger.info("Acquired publish lock")

    # Remove the lock file on any normal exit so the systemd-level
    # ConditionPathExists=!/tmp/opencaselaw-publish.lock check in the
    # incremental shadow service (and any other consumer of the
    # *existence* of the file) doesn't keep blocking subsequent runs.
    # Empirically (2026-05-20 → 2026-05-21): the publish flock was
    # correctly released at OCL_SWAP_DONE and again on step 2 return,
    # but the file itself persisted on disk past process exit. That
    # left the 20:00 UTC incremental shadow service in a permanent
    # "skipped" state. atexit fires on any clean exit (normal return,
    # sys.exit, uncaught exception) — only SIGKILL evades it, which
    # is fine because SIGKILL implies an operator intervention that
    # would already touch /tmp by hand.
    import atexit

    def _cleanup_lock_file():
        try:
            os.unlink(LOCK_FILE_PATH)
        except OSError:
            pass

    atexit.register(_cleanup_lock_file)

    if args.dry_run:
        logger.info("DRY RUN — no changes will be made")

    manual_step_mode = args.step is not None

    # Phase B v0.2: opt-in DAG runner. OCL_USE_DAG=1 hands control over
    # to publish_dag.run_targets() and short-circuits the linear path.
    # Default off — every nightly + every CI run uses the established
    # for-loop until v0.3 flips the default after parallel + checkpoint
    # support land in the DAG runner.
    if os.environ.get("OCL_USE_DAG") == "1":
        sys.exit(_run_via_dag(args, manual_step_mode))

    results = {}
    start = time.time()
    # One id per run, shared by every publish_runs.jsonl record it writes.
    run_id = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    _append_run_record({
        "type": "run_start", "run_id": run_id,
        "full_rebuild": bool(args.full_rebuild), "dry_run": bool(args.dry_run),
        "steps_requested": [str(n) for n, _, _ in STEPS],
        "resumed_from_checkpoint": bool(_load_checkpoint()),
        # A `--step N` run is one step, not a publish: consumers of this file
        # (runtime trends, freshness probes) filter on it.
        "manual_step": str(args.step) if manual_step_mode else None,
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })

    # Steps 4 (HF upload) and 6/6a (git push) must not run if critical steps failed,
    # because step 4 prunes remote parquet based on local state. Step 5c (QC gate)
    # blocks 6/6a when a CRITICAL data-quality regression is detected — ensuring
    # broken data never reaches users on opencaselaw.ch.
    CRITICAL_STEPS = {2, 3, "5c"}
    GUARDED_STEPS = {4, 6, "6a"}
    # Non-fatal steps: still logged as FAILED in summary, but don't trigger
    # systemd exit-code=1. These depend on flaky external sources (e.g. sav-fsa.ch
    # PDFs) and their failure doesn't degrade the published dataset.
    # 5e (stats_interesting) is non-fatal: on failure the dashboard
    # keeps Step 5a's already-written stats.json (early-tier counts +
    # previous build's graph block). The DAG marks stats_interesting
    # non_fatal too — keep in sync.
    #
    # 2g (decision_structure) is non-fatal because it's an enrichment
    # sidecar, not part of the served corpus. 2026-05-20 incident: 2g hit
    # its 2h timeout, which left ``failed_steps`` non-empty → the success
    # branch (which calls _clear_checkpoint) was skipped → today's 03:30
    # UTC publish loaded the 9.5h-old checkpoint (within the 12h TTL),
    # saw every step except 2g marked True, and skipped stats regeneration
    # entirely. Marking 2g non-fatal lets the checkpoint clear on
    # completion so the next daily timer starts clean.
    # 2c (reference_graph) + 2g (decision_structure) build DERIVED sidecars; the
    # served decisions.db swap already happened, so a sidecar-build miss must NOT
    # sys.exit(1) and disarm state/last_publish_success.json (the freshness probe
    # keys off it). "2c" added 2026-06-03. 2f (Materialien) stays FATAL until its
    # materialien.db lock fix ships (stale get_materialien is a real degradation
    # worth alarming). TODO: add a separate reference_graph/decision_structure
    # staleness probe so a silently-stale sidecar is still detected.
    # 5b (rss_feeds) added 2026-09-04: convenience artifact, stale feeds keep
    # being served on a miss (see step_5b_generate_feeds). The DAG marks
    # rss_feeds non_fatal too — keep in sync.
    NON_FATAL_STEPS = _NON_FATAL_STEPS
    # Steps after the fast tier — skipped with --fast-only
    SLOW_STEPS = {"2d", "2e", "2b", "2c", "2f", "2g", 3, 4, 5, 6}

    # Parallel mode: run PARALLEL_POST_BUILD_STEPS concurrently after Step 2d.
    # Default ON; set OCL_PARALLEL_POST_BUILD=0 to fall back to sequential.
    # Disabled when running a single --step or --fast-only (those bypass the
    # batch entirely).
    parallel_mode = (
        os.environ.get("OCL_PARALLEL_POST_BUILD", "1") not in ("0", "false", "no")
        and not manual_step_mode
        and not args.fast_only
    )
    parallel_deferred: list = []  # collected (num, name, func) awaiting batch flush
    if parallel_mode:
        logger.info(
            f"Parallel post-build mode: ON ({len(PARALLEL_POST_BUILD_STEPS)} steps "
            f"with {PARALLEL_MAX_WORKERS}-way pool, set OCL_PARALLEL_POST_BUILD=0 to disable)"
        )

    # Resume from checkpoint if prior run crashed
    checkpoint = _load_checkpoint() if not manual_step_mode else None
    if checkpoint:
        logger.info(f"  Resuming from checkpoint (last completed: step {checkpoint['last_completed_step']})")
        for k, v in checkpoint["results"].items():
            # Restore prior results; step keys can be int or str
            for snum, _, _ in STEPS:
                if str(snum) == k:
                    results[snum] = v
                    break

    def _flush_parallel_batch():
        """Flush any deferred parallel-safe steps as one concurrent batch."""
        if not parallel_deferred:
            return
        batch_results = _run_parallel_post_build(parallel_deferred, args, manual_step_mode)
        results.update(batch_results)
        for nkey, nok in batch_results.items():
            if nok:
                _save_checkpoint(nkey, results)
        parallel_deferred.clear()

    for num, name, func in STEPS:
        if args.step is not None and str(args.step) != str(num):
            continue
        # Skip steps already completed in a prior checkpoint. Only treat
        # ``True`` as "done"; a cascade-skip ("skipped_cascade") or False
        # in the checkpoint means the step never actually ran and should
        # be retried.
        if (
            checkpoint
            and str(num) in checkpoint["results"]
            and checkpoint["results"][str(num)] is True
        ):
            logger.info(f"  Step {num} ({name}): SKIPPED (completed in prior run)")
            results[num] = True
            continue
        # Step 1 (ingest) is opt-in: skip unless --ingest or --step 1
        if num == 1 and not args.ingest and not manual_step_mode:
            logger.info(f"  Step {num} ({name}): SKIPPED (use --ingest to enable)")
            results[num] = True
            continue
        # --fast-only: stop after the early stats push (5a + 6a)
        if args.fast_only and num in SLOW_STEPS:
            logger.info(f"  Step {num} ({name}): SKIPPED (--fast-only)")
            results[num] = True
            continue
        # Defer parallel-safe steps; the batch flushes before any subsequent
        # sequential step (so guarded-step checks see their results).
        if parallel_mode and num in PARALLEL_POST_BUILD_STEPS:
            parallel_deferred.append((num, name, func))
            continue
        # Flush parallel batch BEFORE the GUARDED_STEPS check so critical-step
        # results (incl. step 3 from the parallel batch) are populated.
        if parallel_deferred:
            _flush_parallel_batch()
        # Skip guarded steps if a critical step failed (unless running single step)
        if not manual_step_mode and num in GUARDED_STEPS:
            critical_failed = any(
                results.get(s) is False for s in CRITICAL_STEPS
            )
            if critical_failed:
                # Mark as skipped (not failed) so the summary doesn't claim
                # "Step X (Y): FAILED" for a step that was never attempted.
                # Use the dedicated cascade-skip sentinel so the summary can
                # distinguish "we ran it and it failed" from "the gate before
                # it failed and we elided this run".
                results[num] = "skipped_cascade"
                logger.warning(
                    f"  Step {num} ({name}): SKIPPED — critical earlier step failed\n"
                )
                _append_run_record({
                    "type": "step", "run_id": run_id, "step": str(num),
                    "name": name, "status": "skipped_cascade",
                    "elapsed_s": 0.0,
                    "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                })
                continue
        step_start = time.time()
        try:
            if num == 2:
                # Release the publish lock the INSTANT build_fts5
                # prints OCL_SWAP_DONE — not when Step 2 returns. The
                # 2026-05-11 incident: the 10:40 UTC atomic swap landed
                # cleanly, but build_fts5's post-swap PRAGMA integrity_
                # check on the 60 GB DB took ~3 h. During that 3 h the
                # publish lock stayed held, so every bger_poller fire
                # silently skipped quick_publish — 13 fresh BGer dockets
                # were stranded in bger.jsonl all day. The "Neueste
                # Bundesgerichtsentscheide" feed must reflect bger.ch's
                # AZA index ASAP; we cannot afford a 3 h dead zone after
                # every nightly swap. Integrity_check operates on the
                # old inode (held by build_fts5's open fd), so it stays
                # consistent even if quick_publish writes a newer inode
                # to the same path.
                _swap_done = {"released": False}

                def _release_on_swap_done(line: str) -> None:
                    if _swap_done["released"]:
                        return
                    if "OCL_SWAP_DONE" not in line:
                        return
                    try:
                        fcntl.flock(lock_file, fcntl.LOCK_UN)
                        logger.info(
                            "  publish lock released on OCL_SWAP_DONE "
                            "— quick_publish unblocked for the rest of "
                            "the post-build window (integrity_check etc.)"
                        )
                        _swap_done["released"] = True
                    except (OSError, ValueError):
                        pass

                ok = func(
                    dry_run=args.dry_run,
                    full_rebuild=args.full_rebuild,
                    on_line=_release_on_swap_done,
                )
                # Fallback for the (uncommon) path where build_fts5
                # returned ok without ever printing the sentinel — e.g.
                # --incremental mode, manual invocations from a older
                # build_fts5 binary, etc. No-op if already released.
                if ok and not args.dry_run and not _swap_done["released"]:
                    try:
                        fcntl.flock(lock_file, fcntl.LOCK_UN)
                        logger.info(
                            "  publish lock released after Step 2 return "
                            "(OCL_SWAP_DONE sentinel not seen — older "
                            "build_fts5 or --incremental path)"
                        )
                    except (OSError, ValueError):
                        pass
                # Free handles to the old (now-deleted) decisions.db inode
                # before the disk-hungry aux tier runs (2026-07-08 ENOSPC:
                # reference_graph + decision_structure hit 'disk is full' while
                # the workers still pinned the orphaned ~130 GB old DB). Rolling
                # + /health-gated, so serving never drops a worker. Runs before
                # the deferred parallel batch flushes, so the inode is freed
                # before 2c/2g write their .tmp files.
                if ok and not args.dry_run and args.full_rebuild:
                    _recycle_mcp_workers(dry_run=args.dry_run)
            elif num in ("2b", "2c", "2d", "2e", "2f", "2g"):
                ok = func(
                    dry_run=args.dry_run,
                    full_rebuild=(args.full_rebuild or manual_step_mode),
                )
            else:
                # Steps 5, 5a, 6, 6a and all others take only dry_run
                ok = func(dry_run=args.dry_run)
            results[num] = ok
            elapsed = time.time() - step_start
            status = "OK" if ok else "FAILED"
            logger.info(f"  → {status} ({elapsed:.1f}s)\n")
            _append_run_record({
                "type": "step", "run_id": run_id, "step": str(num),
                "name": name, "status": status.lower(),
                "elapsed_s": round(elapsed, 1),
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            })
            # A manual single step must never touch the full build's resume
            # checkpoint (it would replace a crashed run's 4h-TTL checkpoint
            # with a one-step one).
            if ok and not manual_step_mode:
                _save_checkpoint(num, results)
        except Exception as e:
            results[num] = False
            logger.error(f"  → EXCEPTION: {e}\n", exc_info=True)
            _append_run_record({
                "type": "step", "run_id": run_id, "step": str(num),
                "name": name, "status": "exception",
                "elapsed_s": round(time.time() - step_start, 1),
                "error": str(e)[:200],
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            })

    # Defensive: flush any deferred parallel batch in case STEPS ends with
    # parallel-safe entries (current layout has 4, 7, 5, 6, 6b after the batch,
    # so this is a no-op today; future-proofs the code).
    if parallel_deferred:
        _flush_parallel_batch()

    # Summary — distinguish OK / FAILED / SKIPPED so cascade-skipped
    # steps don't masquerade as outright failures.
    #
    # Stale-source caveat: when Step 2 (Build FTS5) fails, the linear
    # path keeps going and Steps 2b-g / 3 / 7 / 5 may still complete
    # successfully — but they ran against the *previous-good*
    # decisions.db (atomic-swap didn't happen). Mark those "OK
    # (stale source)" so the summary doesn't read green for a run
    # whose data layer never refreshed.
    fts5_failed = results.get(2) is False
    STALE_DOWNSTREAM = {"2b", "2c", "2d", "2e", "2f", "2g", 3, 7, 5}

    total_elapsed = time.time() - start
    failed_steps = []
    non_fatal_failures = []
    logger.info("=== Summary ===")
    for num, name, _ in STEPS:
        if num not in results:
            continue
        outcome = results[num]
        if outcome is True:
            if fts5_failed and num in STALE_DOWNSTREAM:
                status = "OK (stale source — Step 2 FAILED)"
            else:
                status = "OK"
        elif outcome == "skipped_cascade":
            status = "SKIPPED (cascade)"
        elif outcome is False:
            status = "FAILED"
        else:
            status = str(outcome)
        logger.info(f"  Step {num} ({name}): {status}")
        if outcome is False:  # only true outright failures count
            if num in NON_FATAL_STEPS:
                non_fatal_failures.append(f"{num} ({name})")
            else:
                failed_steps.append(f"{num} ({name})")
        elif isinstance(outcome, str) and outcome.startswith("WARN"):
            # Degraded-but-not-blocking (today: a QC-gate timeout). Recorded
            # in last_publish_success.non_fatal_failures so a run that shipped
            # with an unverified corpus is visible in the marker, not only in
            # a log line that scrolls away.
            non_fatal_failures.append(f"{num} ({name}): {outcome}")
    logger.info(f"  Total time: {total_elapsed:.1f}s")

    # One summary line per run, success or failure alike. The failure
    # branch exits the process below, which is exactly why the record is
    # written FIRST — failed runs used to leave no structured trace at all.
    _append_run_record({
        "type": "run_summary", "run_id": run_id,
        "outcome": "failed" if failed_steps else "ok",
        "total_s": round(total_elapsed, 1),
        "failed_steps": failed_steps,
        "non_fatal_failures": non_fatal_failures,
        "stale_downstream": fts5_failed,
        "steps": {str(k): (v if isinstance(v, str) else bool(v))
                  for k, v in results.items()},
        "manual_step": str(args.step) if manual_step_mode else None,
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })

    # Notify on completion
    if failed_steps:
        msg = f"Failed steps: {', '.join(failed_steps)}"
        if non_fatal_failures:
            msg += f"\nNon-fatal: {', '.join(non_fatal_failures)}"
        msg += f"\nTotal: {total_elapsed/60:.0f} min"
        _notify("Publish FAILED", msg, priority="high")
        sys.exit(1)
    elif manual_step_mode:
        # A single `--step N` run (the incremental orchestrator drives 2g,
        # 5b, 5c, 5d, 7, 6 and 6b this way) is not a full publish. It must
        # not clear the full build's resume checkpoint, must not refresh the
        # last_publish_success marker that quality.smoke reads as "the whole
        # pipeline succeeded", and must not push a "Publish OK" notification
        # (seven a night would drown the real one). Step-level alerts (the
        # gate's own BLOCKED/DEGRADED, "Publish FAILED" above) still fire.
        logger.info(
            "manual step %s OK — success marker, checkpoint and 'Publish OK' "
            "notification left untouched (not a full publish)", args.step)
    else:
        _clear_checkpoint()
        # Durable full-publish-success marker. The 15-min bger poller keeps
        # db_generation fresh even when the nightly fails, so db_generation is
        # NOT a valid freshness signal — this marker is the authoritative
        # "last fully-successful publish" timestamp that quality.smoke checks
        # to detect a silently-failing pipeline (failures stay green otherwise
        # because the atomic swap keeps serving the previous-good corpus).
        try:
            (REPO_DIR / "state").mkdir(exist_ok=True)
            (REPO_DIR / "state" / "last_publish_success.json").write_text(
                json.dumps({
                    "ts": int(time.time()),
                    "iso": datetime.now(timezone.utc).isoformat(),
                    "total_minutes": round(total_elapsed / 60, 1),
                    "non_fatal_failures": non_fatal_failures,
                })
            )
        except Exception as e:
            logger.warning("could not write last_publish_success marker: %s", e)
        if non_fatal_failures:
            logger.warning(
                f"Publish OK (non-fatal failures: {', '.join(non_fatal_failures)})"
            )
        # Count decisions from stats if available
        try:
            stats = json.loads((DOCS_DIR / "stats.json").read_text())
            n = stats.get("total_decisions", "?")
        except Exception:
            n = "?"
        _notify(
            "Publish OK",
            f"{n} decisions, {total_elapsed/60:.0f} min",
        )


if __name__ == "__main__":
    main()
