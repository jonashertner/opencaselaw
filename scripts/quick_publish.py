#!/usr/bin/env python3
"""
Quick Publish — insert new JSONL decisions into FTS5 DB without full rebuild.

Designed to run after the BGer poller triggers a scrape, so new decisions
are searchable within minutes instead of waiting for the nightly full rebuild.

Architecture:
  1. Copy decisions.db to decisions.db.quick (if not exists or stale)
  2. Read JSONL files, find entries not yet in the DB
  3. INSERT OR IGNORE new rows (FTS5 triggers handle index sync)
  4. Atomic os.replace() swap
  5. MCP workers pick up new file on next connection

This is safe to run concurrently with MCP workers because:
  - Workers use immutable=1 on the current file
  - We write to a temp copy, then atomic swap
  - Workers reconnect and see the new file

Usage:
  python3 scripts/quick_publish.py                    # insert all new
  python3 scripts/quick_publish.py --courts bger      # only specific courts
  python3 scripts/quick_publish.py --dry-run          # count only
"""
from __future__ import annotations

import argparse
import atexit
import fcntl
import json
import logging
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parents[1]
if str(REPO_DIR) not in sys.path:
    sys.path.insert(0, str(REPO_DIR))

from build_fts5 import insert_decision  # noqa: F401 (ensure_derived_columns imported at use site)

logger = logging.getLogger("quick_publish")

OUTPUT_DIR = REPO_DIR / "output"
DB_PATH = OUTPUT_DIR / "decisions.db"
JSONL_DIR = OUTPUT_DIR / "decisions"
TMP_PATH = Path(str(DB_PATH) + ".quick")
PUBLISH_LOCK_PATH = "/tmp/opencaselaw-publish.lock"
# Dedicated mutex so two quick_publish runs (bger-poller hourly + ecthr daily)
# never share the single decisions.db.quick working copy — the concurrency bug
# that corrupted the copy ("database disk image is malformed") and failed the
# ECtHR daily. Bounded wait, then skip (JSONL rows are re-ingested by the next
# quick/full publish, so a skip is zero-loss).
QUICK_PUBLISH_LOCK_PATH = "/tmp/opencaselaw-quick-publish.lock"
QUICK_PUBLISH_LOCK_TIMEOUT_S = 15 * 60

# Tracked so atexit / SIGTERM handlers can unlink the .quick file even if
# the normal try/finally block doesn't run (e.g. parent kills us with SIGTERM
# on subprocess timeout — what burned 2026-05-04 with a 55 GB orphan).
_active_tmp: Path | None = None


def _publish_in_progress() -> bool:
    """Probe publish.py's exclusive lock. Returns True if a full publish holds it.

    publish.py acquires fcntl.LOCK_EX on PUBLISH_LOCK_PATH at startup. A shared
    lock probe (LOCK_SH | LOCK_NB) succeeds iff no exclusive holder exists.
    """
    try:
        with open(PUBLISH_LOCK_PATH, "r") as lf:
            try:
                fcntl.flock(lf.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
                fcntl.flock(lf.fileno(), fcntl.LOCK_UN)
                return False
            except BlockingIOError:
                return True
    except FileNotFoundError:
        return False


def _acquire_quick_publish_lock():
    """Acquire the process-wide quick_publish mutex with a bounded wait.

    Returns the held lock file object, or None if the timeout elapsed while
    another quick_publish held it. The lock file is deliberately PERSISTENT and
    never unlinked: unlinking an flock'd file lets two processes lock different
    inodes at the same pathname. flock is released automatically when the fd is
    closed — including on SIGKILL — so a crashed holder never wedges future runs.
    """
    lock_file = open(QUICK_PUBLISH_LOCK_PATH, "a+")
    deadline = time.monotonic() + QUICK_PUBLISH_LOCK_TIMEOUT_S
    announced_wait = False
    try:
        while True:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return lock_file
            except BlockingIOError:
                if not announced_wait:
                    logger.info(
                        "Another quick_publish holds %s; waiting up to %ds",
                        QUICK_PUBLISH_LOCK_PATH, QUICK_PUBLISH_LOCK_TIMEOUT_S,
                    )
                    announced_wait = True
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    lock_file.close()
                    return None
                time.sleep(min(1.0, remaining))
    except BaseException:
        lock_file.close()
        raise


def _cleanup_active_tmp() -> None:
    """Unlink the in-flight .quick file if a swap never completed."""
    global _active_tmp
    p = _active_tmp
    _active_tmp = None
    if p is None:
        return
    try:
        if p.exists():
            size_gb = p.stat().st_size / 1e9
            p.unlink()
            logger.warning("Cleanup: removed orphan %s (%.1f GB)", p, size_gb)
    except OSError as e:
        logger.warning("Cleanup failed for %s: %s", p, e)


def _signal_handler(signum, frame):  # noqa: ARG001
    _cleanup_active_tmp()
    sys.exit(128 + signum)


def _resolve_real_path(p: Path) -> Path:
    """Resolve symlinks to get the actual file path (for atomic replace on same fs)."""
    return p.resolve()


ROLLING_RESTART_SCRIPT = REPO_DIR / "scripts" / "rolling_restart_workers.sh"
RECYCLE_TIMEOUT_S = 600


def _recycle_workers_after_swap() -> None:
    """Roll-restart the mcp-server@ workers so they let go of the old
    decisions.db inode, as publish.py's post-swap recycle does after Step 2.

    Pooled worker connections keep the swapped-out file (~73 GB) allocated
    until the process restarts; on 2026-07-08 pinned inodes filled the data
    volume. Until Step B a quick_publish swap was rare (the daily full build
    held the lock all day); with the incremental build ingesting every weekday
    night, a pinned copy would leave too little room for the structure rebuild
    that follows it (1.2 x the sidecar).

    The script restarts one worker at a time, each gated on /health, so
    serving never drops. Non-fatal: a recycle problem is logged and the swap
    stands. OCL_QUICK_PUBLISH_RECYCLE=0 turns it off.
    """
    if os.environ.get("OCL_QUICK_PUBLISH_RECYCLE", "1").strip().lower() in {
            "0", "false", "off", "no"}:
        logger.info("Worker recycle after swap disabled (OCL_QUICK_PUBLISH_RECYCLE=0)")
        return
    if not ROLLING_RESTART_SCRIPT.exists():
        logger.warning("Worker recycle skipped: %s not found", ROLLING_RESTART_SCRIPT)
        return
    logger.info("Rolling restart of the workers to free the old decisions.db inode")
    try:
        r = subprocess.run(
            ["bash", str(ROLLING_RESTART_SCRIPT)],
            capture_output=True, text=True, timeout=RECYCLE_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning("Worker recycle failed (%s); the swap stands", e)
        return
    for line in (r.stdout or "").splitlines():
        logger.info("  %s", line)
    for line in (r.stderr or "").splitlines():
        logger.warning("  %s", line)


def _get_existing_ids(conn: sqlite3.Connection) -> set[str]:
    """Get all decision_ids currently in the DB."""
    return {row[0] for row in conn.execute("SELECT decision_id FROM decisions")}


def _read_new_jsonl(courts: list[str] | None, existing_ids: set[str]) -> list[dict]:
    """Read JSONL files and return rows not yet in the DB."""
    new_rows = []
    jsonl_files = sorted(JSONL_DIR.glob("*.jsonl"))

    for jf in jsonl_files:
        court = jf.stem
        if courts and court not in courts:
            continue
        try:
            with open(jf) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    row = json.loads(line)
                    did = row.get("decision_id", "")
                    if did and did not in existing_ids:
                        new_rows.append(row)
        except Exception as e:
            logger.warning("Failed to read %s: %s", jf, e)

    return new_rows


def _quick_publish_locked(courts: list[str] | None = None, dry_run: bool = False) -> int:
    """Insert new JSONL decisions into FTS5 DB. Returns count of inserted rows.

    The temp .quick copy is always cleaned up on exit (success, error, or
    early return). Without this, a crash mid-run leaves a 60+ GB stale
    file on disk — that filled the data volume to 100% on 2026-04-26 and
    blocked the publish pipeline.
    """
    real_db = _resolve_real_path(DB_PATH)
    real_tmp = real_db.parent / (real_db.name + ".quick")

    if not real_db.exists():
        logger.error("FTS5 DB not found at %s", real_db)
        return 0

    # Defensive: clean any .quick leftover from a hard-killed prior run
    # (OOM, SIGKILL, reboot — cases where the finally block didn't run).
    # Without this, quick_publish accumulates 60+ GB of dead files until
    # the data volume fills up and the nightly publish blocks at
    # pre-flight. This burned the 2026-05-02 nightly.
    if not dry_run and real_tmp.exists():
        try:
            stale_stat = real_tmp.stat()  # stat BEFORE unlink (was read-after-unlink)
            stale_age_h = (time.time() - stale_stat.st_mtime) / 3600
            stale_size_gb = stale_stat.st_size / 1e9
            real_tmp.unlink()
            logger.warning(
                "Cleaned up stale .quick from prior crashed run "
                "(age %.1fh, freed %.1f GB)",
                stale_age_h, stale_size_gb,
            )
        except OSError as e:
            logger.error("Failed to clean stale .quick: %s", e)

    t0 = time.time()
    swap_done = False
    conn = None
    global _active_tmp

    try:
        # Step 1: Copy current DB to work on
        logger.info("Copying %s to %s (%s)", real_db, real_tmp,
                    "{:.0f} MB".format(real_db.stat().st_size / 1e6))
        if not dry_run:
            _active_tmp = real_tmp
            shutil.copy2(str(real_db), str(real_tmp))

        # Step 2: Find new rows (dry-run reads the LIVE db read-only + immutable)
        read_uri = f"file:{real_db}?mode=ro&immutable=1"
        conn = sqlite3.connect(
            str(real_tmp) if not dry_run else read_uri,
            uri=dry_run,
        )
        existing_ids = _get_existing_ids(conn)
        logger.info("Existing decisions: %d", len(existing_ids))

        new_rows = _read_new_jsonl(courts, existing_ids)
        logger.info("New decisions to insert: %d", len(new_rows))

        if not new_rows:
            logger.info("Nothing to insert")
            return 0

        if dry_run:
            for r in new_rows[:10]:
                logger.info("  Would insert: %s (%s)", r.get("decision_id"), r.get("court"))
            return len(new_rows)

        # Step 3: Ensure WAL mode is off (for immutable=1 compat after swap)
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("PRAGMA busy_timeout=5000")

        # Step 3b: the working copy may predate the derived-classification
        # columns insert_decision now writes (metadata-only ALTERs).
        from build_fts5 import ensure_derived_columns
        added = ensure_derived_columns(conn)
        if added:
            logger.info("  Added %d derived-classification columns to the copy", added)

        # Step 4: Insert new rows
        inserted = 0
        for row in new_rows:
            if insert_decision(conn, row):
                inserted += 1
        conn.commit()

        total_after = conn.execute("SELECT count(*) FROM decisions").fetchone()[0]

        # Bump db_generation so MCP workers' _query_cache invalidates on
        # next get_db call. See docs/db_contract.md. Runs AFTER commit
        # and BEFORE close + os.replace, so the new value is persisted
        # into the file that gets atomically swapped into the live path.
        # Value is unix epoch seconds.
        _db_generation = int(time.time())
        conn.execute(f"PRAGMA user_version = {_db_generation}")
        logger.info("db_generation set to %d", _db_generation)

        conn.close()
        conn = None

        elapsed = time.time() - t0
        logger.info("Inserted %d/%d new decisions (%d total, %.1fs)",
                    inserted, len(new_rows), total_after, elapsed)

        # Step 5: Atomic swap (consumes real_tmp — no cleanup needed below)
        os.replace(str(real_tmp), str(real_db))
        swap_done = True
        _active_tmp = None
        logger.info("Atomic swap complete — new decisions are live")

        _recycle_workers_after_swap()

        return inserted
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        # dry_run never creates/owns .quick (no copy, no mutex) — it must NOT
        # unlink a concurrent real run's working file (Codex review).
        if not dry_run and not swap_done and real_tmp.exists():
            try:
                real_tmp.unlink()
                logger.info("Cleaned up temp file %s", real_tmp)
            except OSError as e:
                logger.warning("Failed to clean up %s: %s", real_tmp, e)
        _active_tmp = None


def quick_publish(courts: list[str] | None = None, dry_run: bool = False) -> int | None:
    """Run quick publish under the dedicated process-wide mutex.

    Returns the inserted-row count, or None if the run was deliberately SKIPPED
    because another publisher held a lock (JSONL input is retained for the next
    quick/full publish — zero loss). Dry-run needs no mutex (read-only).
    """
    if dry_run:
        return _quick_publish_locked(courts=courts, dry_run=True)

    lock_file = _acquire_quick_publish_lock()
    if lock_file is None:
        logger.info(
            "Another quick_publish still holds %s after %ds; skipping "
            "quick_publish. No JSONL rows were discarded; the next quick or "
            "full publish will retry them.",
            QUICK_PUBLISH_LOCK_PATH, QUICK_PUBLISH_LOCK_TIMEOUT_S,
        )
        return None

    try:
        # Recheck AFTER acquiring the mutex: a full publish may have started
        # while this process was queued behind another quick_publish.
        if _publish_in_progress():
            logger.info(
                "Full publish.py is running (holds %s); skipping quick_publish.",
                PUBLISH_LOCK_PATH,
            )
            return None
        return _quick_publish_locked(courts=courts, dry_run=False)
    finally:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        except OSError as e:
            logger.warning("Failed to explicitly release quick_publish lock: %s", e)
        finally:
            lock_file.close()


def main():
    parser = argparse.ArgumentParser(description="Quick publish — insert new decisions into FTS5")
    parser.add_argument("--courts", type=str, help="Comma-separated court codes (default: all)")
    parser.add_argument("--dry-run", action="store_true", help="Count only, don't modify DB")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    # Defense-in-depth cleanup of the .quick orphan: SIGTERM (parent timeout)
    # bypasses the try/finally below; atexit covers normal-exit paths the
    # finally also covers, but is harmless redundancy.
    atexit.register(_cleanup_active_tmp)
    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    # Skip if a full publish holds the exclusive lock — quick_publish copies
    # the 60 GB decisions.db, which collides with build_fts5's atomic-swap
    # disk budget. The 2026-05-04 incident: BGer poller fired this mid-publish,
    # subprocess timeout killed it at 600 s, left 55 GB orphan, ENOSPC followed.
    if not args.dry_run and _publish_in_progress():
        logger.info("Full publish.py is running (holds %s); skipping quick_publish.",
                    PUBLISH_LOCK_PATH)
        return

    courts = args.courts.split(",") if args.courts else None
    inserted = quick_publish(courts=courts, dry_run=args.dry_run)
    if inserted is None:
        # Deliberately skipped (a lock was held); wrapper already logged why.
        return
    if inserted:
        logger.info("Done — %d new decisions published", inserted)
    else:
        logger.info("Done — no new decisions")


if __name__ == "__main__":
    main()
