"""Command-line entry: `python -m quality.cli run [options]`.

Subcommands:
  run            run all (or filtered) checks and write a report
  list           list all discovered checks
  history        print last N runs from quality/history.db
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from quality import runner, severity, types


def _cmd_run(args: argparse.Namespace) -> int:
    report = runner.run(
        db_path=args.db,
        only=args.check or None,
        critical_only=args.critical_only,
        record_history=not args.no_history,
        parallel=not args.serial,
        # Always the audit-trail directory, never Path(args.output).parent:
        # the publish gate passes --output docs/quality.json, and the trail
        # is operational debris that has no business in the published docs
        # tree. quality/reports/ is where write_report already archives.
        progress_dir=runner.DEFAULT_REPORT_DIR,
    )
    out_path = runner.write_report(
        report,
        out_dir=Path(args.output).parent if args.output else runner.DEFAULT_REPORT_DIR,
    )
    if args.output:
        # Custom output path: atomic write (.tmp + os.replace) so the
        # public dashboard never reads a partially-written file if the
        # gate is killed mid-write.
        import os, tempfile
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", delete=False,
            dir=str(out.parent), prefix=out.name + ".", suffix=".tmp",
        ) as f:
            json.dump(report.to_dict(), f, indent=2, ensure_ascii=False)
            tmp_path = f.name
        os.replace(tmp_path, str(out))

    print(f"\nReport: {out_path}")
    print(f"Duration: {report.duration_seconds:.1f}s  "
          f"({len(report.results)} checks, "
          f"{sum(1 for r in report.results if r.passed)} passed, "
          f"{len(report.critical_failures)} CRIT, "
          f"{len(report.quarantine_failures)} QUAR, "
          f"{len(report.warning_failures)} WARN)")
    slowest = sorted(report.check_timings.items(), key=lambda kv: -kv[1])[:5]
    if slowest:
        print("Slowest: " + ", ".join(f"{n} {s:.1f}s" for n, s in slowest))

    if report.critical_failures:
        print("\n  Critical failures:")
        for r in report.critical_failures:
            print(f"    ✗ {r.name}: {r.message}")
    if report.quarantine_failures:
        print("\n  Quarantined (auto-NULLed at build, NOT blocking):")
        for r in report.quarantine_failures:
            print(f"    ⚑ {r.name}: {r.message}")
    if report.warning_failures:
        print("\n  Warnings:")
        for r in report.warning_failures:
            print(f"    ⚠ {r.name}: {r.message}")
    if report.passed and not report.warning_failures:
        print("\n  All clear ✓")

    if args.gate:
        return severity.exit_code_for(report)
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    checks = runner.discover_checks()
    print(f"Discovered {len(checks)} checks:\n")
    for fn in checks:
        mod = fn.__module__.split(".")[-1]
        name = fn.__name__[len("check_"):]
        doc = (fn.__doc__ or "").strip().split("\n", 1)[0]
        print(f"  {mod:>20s}.{name:<35s} {doc[:60]}")
    return 0


def _cmd_history(args: argparse.Namespace) -> int:
    from quality.baseline import HISTORY_DB, _connect
    if not Path(HISTORY_DB).exists():
        print("(no history yet)")
        return 0
    conn = _connect(HISTORY_DB)
    try:
        rows = conn.execute(
            "SELECT run_at, total, passed, critical_failures, warning_failures, "
            "duration_seconds FROM run_log ORDER BY run_at DESC LIMIT ?",
            (args.limit,),
        ).fetchall()
    finally:
        conn.close()
    print(f"  {'run_at':25s} {'total':>5s} {'pass':>5s} {'CRIT':>5s} {'WARN':>5s} {'dur':>6s}")
    for r in rows:
        print(f"  {r[0]:25s} {r[1]:>5d} {r[2]:>5d} {r[3]:>5d} {r[4]:>5d} {r[5]:>6.1f}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser("quality", description=__doc__)
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    # run
    pr = sub.add_parser("run", help="run checks and produce a report")
    pr.add_argument("--db", default="output/decisions.db",
                    help="Corpus DB path (default output/decisions.db)")
    pr.add_argument("--check", action="append",
                    help="Run only checks matching this prefix; repeatable.")
    pr.add_argument("--critical-only", action="store_true",
                    help="Skip WARNING + INFO checks (publish-gate path)")
    pr.add_argument("--gate", action="store_true",
                    help="Exit 1 if any CRITICAL check failed")
    pr.add_argument("--no-history", action="store_true",
                    help="Skip writing to quality/history.db (test runs)")
    pr.add_argument("--serial", action="store_true",
                    help="Run checks serially (debugging)")
    pr.add_argument("--output", help="Custom JSON output path")
    pr.set_defaults(func=_cmd_run)

    # list
    pl = sub.add_parser("list", help="list all discovered check_* functions")
    pl.set_defaults(func=_cmd_list)

    # history
    ph = sub.add_parser("history", help="print recent runs from history.db")
    ph.add_argument("--limit", type=int, default=10)
    ph.set_defaults(func=_cmd_history)

    args = p.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
