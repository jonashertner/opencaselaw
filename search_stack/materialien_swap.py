"""Run a materialien.db writer against a copy, then swap the copy in.

The MCP workers open materialien.db with ``?mode=ro&immutable=1``: no lock,
no change detection. A writer that commits into the live file while a call
reads it can hand that call torn pages. For long or heavy writes — the
historical Botschaft nights (a commit every few seconds for hours), the
volume-key table rebuild — this wrapper gives them a private copy instead:

1. take a consistent copy of the live file with the SQLite backup API, next
   to its REAL path (the swap must be a same-filesystem rename);
2. run the command with ``{db}`` replaced by the copy's path, optionally
   under a deadline (SIGTERM, then SIGKILL after a grace period; the
   writers commit whole documents, so a stopped run leaves a valid copy);
3. check the copy (quick_check; botschaft_documents / botschaft_paragraphs
   never shrink unless ``--allow-shrink``);
4. refuse the swap if the live file changed meanwhile (another writer —
   step 2f, the 04:30 unit — would otherwise lose its work); else
   ``os.replace`` the copy over the live file.

Readers keep the old inode until their next connection (one per call).
One run per host (flock on ``--lock-file``).

Usage::

    python3 -m search_stack.materialien_swap --db output/materialien.db -- \\
        python3 -m search_stack.build_botschaft_corpus --migrate-volume-key --db {db}

Exit codes: the command's own code when the copy was swapped in or the
command failed; 3 another run holds the lock; 5 the copy failed its check;
6 the live file changed during the run (copy discarded).
"""
from __future__ import annotations

import argparse
import fcntl
import logging
import os
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

log = logging.getLogger("materialien_swap")

EXIT_LOCKED = 3
EXIT_CHECK_FAILED = 5
EXIT_LIVE_CHANGED = 6
GUARDED_TABLES = ("botschaft_documents", "botschaft_paragraphs")


def _fingerprint(path: Path) -> tuple[int, int, int]:
    st = os.stat(path)
    return (st.st_ino, st.st_size, st.st_mtime_ns)


def _counts(path: Path) -> dict[str, int]:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        out = {}
        for t in GUARDED_TABLES:
            try:
                out[t] = conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
            except sqlite3.OperationalError:
                out[t] = 0
        return out
    finally:
        conn.close()


def make_copy(live: Path, work: Path) -> None:
    if work.exists():
        work.unlink()
    src = sqlite3.connect(f"file:{live}?mode=ro", uri=True)
    dst = sqlite3.connect(str(work))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def check_copy(work: Path, before: dict[str, int], allow_shrink: bool) -> str | None:
    """None when the copy is fit to serve, else the reason."""
    # Read-write on purpose: a writer stopped by the deadline can leave a hot
    # rollback journal, which only a writable connection rolls back. The copy
    # is then folded into one file (no -journal / -wal beside it) before swap.
    conn = sqlite3.connect(str(work))
    try:
        conn.execute("PRAGMA journal_mode=DELETE").fetchone()
        ok = conn.execute("PRAGMA quick_check").fetchone()[0]
    finally:
        conn.close()
    # The writable connection above rolled back any hot journal; what is left
    # is a stale (never committed) journal that SQLite ignores. Remove it so
    # the swapped file is self-contained. WAL mode was switched off above.
    Path(str(work) + "-journal").unlink(missing_ok=True)
    for suffix in ("-wal", "-shm"):
        if Path(str(work) + suffix).exists():
            return f"leftover {suffix} file beside the copy"
    if ok != "ok":
        return f"quick_check: {ok}"
    after = _counts(work)
    if not allow_shrink:
        for t, n in before.items():
            if after.get(t, 0) < n:
                return f"{t} shrank {n} -> {after.get(t, 0)}"
    return None


def run_command(cmd: list[str], deadline: float | None, grace: float = 120.0) -> int:
    # Own process group: a `bash -c 'a && b'` chain is stopped as a whole
    # (SIGTERM to bash alone would leave the running python behind).
    proc = subprocess.Popen(cmd, start_new_session=True)
    try:
        return proc.wait(timeout=deadline)
    except subprocess.TimeoutExpired:
        log.warning("deadline reached: SIGTERM to process group %d", proc.pid)
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            return proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            return proc.wait()


def swap_run(db: str | Path, command: list[str], *, deadline: float | None = None,
             allow_shrink: bool = False, lock_file: str | Path | None = None,
             ok_codes: tuple[int, ...] = (0,)) -> int:
    live = Path(os.path.realpath(str(db)))
    work = live.with_name(live.name + ".swapwork")
    lock_path = Path(lock_file) if lock_file else live.with_name(live.name + ".swap.lock")
    lock = open(lock_path, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log.error("another materialien_swap run holds %s", lock_path)
        return EXIT_LOCKED
    try:
        fp = _fingerprint(live)
        before = _counts(live)
        t0 = time.time()
        make_copy(live, work)
        log.info("copied %s -> %s in %.1fs (%s)", live, work, time.time() - t0, before)
        cmd = [part.replace("{db}", str(work)) for part in command]
        rc = run_command(cmd, deadline)
        if rc not in ok_codes:
            log.error("command exited %d; copy discarded, live file untouched", rc)
            work.unlink(missing_ok=True)
            return rc
        reason = check_copy(work, before, allow_shrink)
        if reason:
            log.error("copy failed its check (%s); discarded", reason)
            work.unlink(missing_ok=True)
            return EXIT_CHECK_FAILED
        if _fingerprint(live) != fp:
            log.error("live file changed during the run; copy discarded (rerun)")
            work.unlink(missing_ok=True)
            return EXIT_LIVE_CHANGED
        os.replace(work, live)
        log.info("swapped in %s %s", live, _counts(live))
        return rc
    finally:
        work.unlink(missing_ok=True)
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s",
                        datefmt="%H:%M:%S")
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--" not in argv:
        print("usage: materialien_swap --db PATH [options] -- COMMAND ... {db} ...",
              file=sys.stderr)
        return 2
    i = argv.index("--")
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--deadline-seconds", type=float)
    ap.add_argument("--allow-shrink", action="store_true")
    ap.add_argument("--lock-file")
    ap.add_argument("--ok-codes", default="0",
                    help="comma-separated exit codes that still swap (e.g. 0,4)")
    args = ap.parse_args(argv[:i])
    command = argv[i + 1:]
    if not command or not any("{db}" in c for c in command):
        print("the command must reference the copy as {db}", file=sys.stderr)
        return 2
    ok = tuple(int(c) for c in args.ok_codes.split(","))
    # A deadline SIGTERM is a normal end for a budgeted writer.
    if args.deadline_seconds:
        ok = ok + (-signal.SIGTERM,)
    return swap_run(args.db, command, deadline=args.deadline_seconds,
                    allow_shrink=args.allow_shrink, lock_file=args.lock_file, ok_codes=ok)


if __name__ == "__main__":
    sys.exit(main())
