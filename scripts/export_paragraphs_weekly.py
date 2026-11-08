#!/usr/bin/env python3
"""Weekly structure/erwaegungen_paragraphs.parquet export, outside the build.

Until 2026-11 the 4.9 GB paragraphs export ran inside publish step 3 on
Sundays, gated by a probe that projects the run time. That projection
over-estimates (1.6-2x in 2026-09/10, ~5x since the 2026-11-05 structure
re-extract left the paragraph table's rowids sparse), and inside the build
the export only gets what is left of step 3's window. It skipped on 11-01
("projected 119.9 min > budget 45 min") and 11-08 (252.4 > 45); the same
call run after the build on 11-08 finished in 48 min.

This job runs after the publish service exits (opencaselaw-paragraphs-export
.service, started by OnSuccess= on opencaselaw-publish.service). Each run:

  1. refuses while opencaselaw-publish.service is active: no full scan of a
     production DB inside the build window;
  2. decides whether an export is due: the build that just ended started
     on a Sunday (UTC), or the file is older than --max-age-days (a
     catch-up after a failed or missed Sunday);
  3. sets a hard deadline --margin-min before the next --build-start-utc
     (default 03:00 UTC for a 03:30 build) and calls
     export_parquet.export_decision_structure() with that deadline as
     both budget and hard stop and the probe logging only, so the
     deadline, not the projection, bounds the run.

The file is written atomically (.tmp + os.replace); the last good file is
kept on any skip or stop. Publish step 4 uploads structure/*.parquet on
every run, so the next build ships the new file to HuggingFace.

Exit codes: 0 = written, or not due; 1 = due but not written (skipped,
hard stop, no sidecar), which fails the unit and fires its OnFailure alert;
2 = refused because the publish service is active.

Usage (on the VPS, from the repo root):
    python3 scripts/export_paragraphs_weekly.py            # what the unit runs
    python3 scripts/export_paragraphs_weekly.py --force    # ignore "due"
    python3 scripts/export_paragraphs_weekly.py --dry-run  # decide, export nothing
"""
from __future__ import annotations

import argparse
import logging
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

logger = logging.getLogger("export_paragraphs_weekly")

PUBLISH_UNIT = "opencaselaw-publish.service"
PARAGRAPHS_FILE = "erwaegungen_paragraphs.parquet"
# A oneshot that is still running reports ActiveState=activating.
BUSY_STATES = {"active", "activating", "reloading", "deactivating"}

EXIT_OK = 0
EXIT_NOT_WRITTEN = 1
EXIT_REFUSED = 2


def _hhmm(raw: str) -> tuple[int, int]:
    h, _, m = raw.partition(":")
    hh, mm = int(h), int(m or 0)
    if not (0 <= hh < 24 and 0 <= mm < 60):
        raise argparse.ArgumentTypeError(f"not a time of day: {raw!r}")
    return hh, mm


def publish_state(unit: str = PUBLISH_UNIT) -> str | None:
    """ActiveState of the publish unit, or None where systemd is absent."""
    try:
        r = subprocess.run(["systemctl", "show", "-p", "ActiveState", "--value", unit],
                           capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() or None


def build_started_on(now: datetime, build_start: tuple[int, int]) -> datetime:
    """Start time of the most recent build slot at or before ``now``."""
    start = now.replace(hour=build_start[0], minute=build_start[1], second=0, microsecond=0)
    return start if start <= now else start - timedelta(days=1)


def deadline_for(now: datetime, build_start: tuple[int, int], margin: timedelta) -> datetime:
    """``margin`` before the next build start. Between that point and the
    build start this is in the past: no time left tonight."""
    nxt = build_started_on(now, build_start) + timedelta(days=1)
    return nxt - margin


def file_age(path: Path, now: datetime) -> timedelta | None:
    try:
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except FileNotFoundError:
        return None
    return now - mtime


def due_reason(now: datetime, build_start: tuple[int, int], age: timedelta | None,
               max_age: timedelta) -> str | None:
    """Why an export is due now, or None if it is not."""
    if age is None:
        return "no paragraphs file yet"
    if build_started_on(now, build_start).weekday() == 6:
        return "Sunday build"
    if age >= max_age:
        return (f"file is {age.total_seconds() / 86400:.1f} days old "
                f"(catch-up, max {max_age.total_seconds() / 86400:.1f})")
    return None


def run(args, now: datetime | None = None, state_fn=publish_state, export_fn=None) -> int:
    now = now or datetime.now(timezone.utc)
    dataset_dir = Path(args.dataset_dir)
    structure_db = Path(args.structure_db)
    target = dataset_dir / "structure" / PARAGRAPHS_FILE

    state = state_fn()
    if state in BUSY_STATES:
        logger.error(f"{PUBLISH_UNIT} is {state}: refusing to scan the sidecar "
                     f"inside the build window")
        return EXIT_REFUSED
    if state is None:
        logger.info(f"systemd state of {PUBLISH_UNIT} unknown (no systemctl); continuing")

    age = file_age(target, now)
    why = "forced" if args.force else due_reason(
        now, args.build_start_utc, age, timedelta(days=args.max_age_days))
    if why is None:
        logger.info(f"not due: build slot {build_started_on(now, args.build_start_utc):%a %F}, "
                    f"file {age.total_seconds() / 86400:.1f} days old")
        return EXIT_OK

    deadline = deadline_for(now, args.build_start_utc, timedelta(minutes=args.margin_min))
    left_s = (deadline - now).total_seconds()
    logger.info(f"due ({why}); deadline {deadline:%F %H:%M} UTC, "
                f"{left_s / 60:.0f} min left")
    if left_s < args.min_window_min * 60:
        logger.error(f"only {max(left_s, 0) / 60:.0f} min before the deadline "
                     f"(minimum {args.min_window_min}); last good file kept, "
                     f"the next post-build run catches up")
        return EXIT_NOT_WRITTEN
    if args.dry_run:
        logger.info("dry run: no export")
        return EXIT_OK

    if export_fn is None:
        from export_parquet import export_decision_structure as export_fn
    counts = export_fn(structure_db, dataset_dir,
                       include_paragraphs=True, include_meta=False,
                       paragraphs_budget_s=left_s,
                       deadline_monotonic=time.monotonic() + left_s,
                       paragraphs_probe_gates=False)
    if "erwaegungen_paragraphs" in counts:
        logger.info(f"wrote {target}: {counts['erwaegungen_paragraphs']} paragraphs; "
                    f"the next publish step 4 uploads it")
        return EXIT_OK
    logger.error(f"{PARAGRAPHS_FILE} NOT written: "
                 f"{counts.get('erwaegungen_paragraphs_skipped') or 'no sidecar at ' + str(structure_db)}")
    return EXIT_NOT_WRITTEN


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dataset-dir", default=str(REPO / "output" / "dataset"))
    ap.add_argument("--structure-db", default=str(REPO / "output" / "decision_structure.db"))
    ap.add_argument("--build-start-utc", type=_hhmm, default=(3, 30),
                    help="daily build start, HH:MM UTC (default 03:30)")
    ap.add_argument("--margin-min", type=float, default=30,
                    help="finish this many minutes before the build start (default 30)")
    ap.add_argument("--min-window-min", type=float, default=15,
                    help="do not start with less time than this before the deadline")
    ap.add_argument("--max-age-days", type=float, default=7.5,
                    help="catch up on a non-Sunday when the file is older than this")
    ap.add_argument("--force", action="store_true", help="export even if not due")
    ap.add_argument("--dry-run", action="store_true", help="decide only, export nothing")
    return ap.parse_args(argv)


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    return run(parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
