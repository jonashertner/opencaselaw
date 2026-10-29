"""Quality-check runner — discovers, executes, aggregates.

Discovery: every module in `quality.checks.*` is imported. Every
top-level function whose name starts with `check_` is treated as a
check. Each check accepts `(conn: sqlite3.Connection, **ctx)` and
returns a `CheckResult` (or a list of `CheckResult`s for per-court
fan-out).

Concurrency: runs checks in a thread pool (max 4 workers since the
SQLite read-only WAL is the bottleneck). Each check opens its own
read-only connection via `_open_db()`; they don't share a cursor.

Output: writes `quality/reports/latest.json` + dated archive,
optionally appends to `quality/history.db` for drift detection.
"""
from __future__ import annotations

import importlib
import json
import logging
import pkgutil
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

from quality.types import CheckResult, CheckRunReport, Severity

logger = logging.getLogger(__name__)

DEFAULT_DB = Path("output/decisions.db")
DEFAULT_REPORT_DIR = Path("quality/reports")
MAX_WORKERS = 4

# Per-check progress trail, written as the run proceeds.
#
# This exists because a timed-out gate previously left NOTHING behind. The
# publish gate runs `python -m quality.cli run --critical-only --gate` with a
# 3600 s cap and no --verbose, so cli.main configures logging at WARNING and
# any INFO line is discarded; when the cap fires, the subprocess is killed
# before `run()` returns, so no report is written, no history row is appended,
# and docs/quality.json still holds the PREVIOUS day's result. Working out
# which check was in flight then requires an investigation rather than a query
# (2026-08-26).
#
# One `start` line and one `done` line per check, flushed immediately. After a
# kill, any check with a `start` and no `done` was in flight. Best-effort
# throughout: a failure to write progress must never affect a verdict.
PROGRESS_FILENAME = "gate-progress.jsonl"
_progress_lock = threading.Lock()
_progress_path: Path | None = None


def _progress_begin(report_dir: Path | None) -> None:
    """Truncate the progress trail at the start of a run, or disable it.

    ``None`` disables. Writing unconditionally would give every library and
    test caller of ``run()`` a filesystem side effect in the repo, which is
    how the first version of this leaked a 17 KB gate-progress.jsonl into a
    working tree during a test run.
    """
    global _progress_path
    _progress_path = None
    if report_dir is None:
        return
    try:
        report_dir.mkdir(parents=True, exist_ok=True)
        path = report_dir / PROGRESS_FILENAME
        path.write_text("", encoding="utf-8")
        _progress_path = path
    except Exception:
        logger.debug("progress trail unavailable (non-fatal)", exc_info=True)


def _progress(event: str, name: str, **fields) -> None:
    if _progress_path is None:
        return
    rec = {"event": event, "check": name,
           "ts": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    rec.update(fields)
    try:
        with _progress_lock:
            with open(_progress_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                fh.flush()
    except Exception:
        logger.debug("progress write failed (non-fatal)", exc_info=True)


def _open_db(db_path: Path) -> sqlite3.Connection:
    """Open the corpus DB read-only, immutable=1 for safe concurrent reads."""
    conn = sqlite3.connect(
        f"file:{db_path}?mode=ro&immutable=1", uri=True,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=120000")
    return conn


def discover_checks(critical_only: bool = False) -> list[Callable]:
    """Find every check_* function in quality.checks.*.

    Order is deterministic: alphabetical by module, then function name.
    A check function may return either a single CheckResult or an
    iterable of them (for per-court fan-out).

    When ``critical_only`` is True, modules that declare
    ``MODULE_NEVER_CRITICAL = True`` at module scope are skipped
    entirely (they only emit WARNING/INFO results, so running them
    inside the publish gate just burns time).
    """
    import quality.checks as checks_pkg
    found: list[tuple[str, Callable]] = []
    for _, name, _ in pkgutil.iter_modules(checks_pkg.__path__):
        mod = importlib.import_module(f"quality.checks.{name}")
        if critical_only and getattr(mod, "MODULE_NEVER_CRITICAL", False):
            continue
        for attr_name in sorted(dir(mod)):
            if not attr_name.startswith("check_"):
                continue
            fn = getattr(mod, attr_name)
            if callable(fn) and getattr(fn, "_qc_check", True):
                found.append((f"{name}.{attr_name[len('check_'):]}", fn))
    found.sort(key=lambda t: t[0])
    return [fn for _, fn in found]


def _check_name(fn: Callable) -> str:
    return f"{fn.__module__.split('.')[-1]}.{fn.__name__[len('check_'):]}"


def _run_one(fn: Callable, db_path: Path, ctx: dict) -> list[CheckResult]:
    """Execute a single check. Wrap any exception as a CRITICAL failure
    so a buggy check never silently disappears from the report."""
    name = _check_name(fn)
    conn = None
    results: list[CheckResult] = []
    started = time.monotonic()
    _progress("start", name)
    try:
        try:
            conn = _open_db(db_path)
            out = fn(conn, **ctx)
            if isinstance(out, CheckResult):
                results = [out]
            elif out is None:
                results = []
            else:
                results = list(out)
                if not all(isinstance(r, CheckResult) for r in results):
                    raise TypeError(f"check {name} returned non-CheckResult")
        except Exception as e:
            logger.exception("check %s raised", name)
            results = [CheckResult(
                name=name,
                severity=Severity.CRITICAL,
                passed=False,
                metric_value=-1,
                threshold=None,
                message=f"check raised exception: {type(e).__name__}: {e}",
                fix_advice="investigate the check itself; bug in the QC code",
            )]
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
        return results
    finally:
        elapsed = round(time.monotonic() - started, 3)
        # Stamped here rather than at each return so the per-court fan-out
        # behaves like the single-result case: every result carries the cost
        # of the call that produced it, so a 118-result fan-out reports the
        # same elapsed 118 times.
        for r in results:
            r.elapsed_s = elapsed
        _progress("done", name, elapsed_s=elapsed, n_results=len(results))
        logger.info("check %s finished in %.2fs", name, elapsed)


def gate_visible_results(results: list[CheckResult]) -> list[CheckResult]:
    """Filter for --critical-only (gate) runs.

    Keep CRITICAL (gating) and QUARANTINE (count-bounded, non-blocking
    but must still alert + render on the dashboard during the gate run).
    Keep WARNING results ONLY when they FAILED: publish.py Step 6c fires
    ntfy via severity.alerting_results, and dropping failing WARNINGs
    here made that alert path unreachable from the nightly (dark from
    2026-05-02 to 2026-07-01). Passing WARNINGs and INFO belong to the
    full QC run. Gate semantics are unchanged: report.passed and
    exit_code_for stay CRITICAL-only, so nothing kept here can block.
    """
    return [
        r for r in results
        if r.severity in (Severity.CRITICAL, Severity.QUARANTINE)
        or (r.severity is Severity.WARNING and not r.passed)
    ]


def run(
    db_path: Path | str = DEFAULT_DB,
    only: Iterable[str] | None = None,
    critical_only: bool = False,
    record_history: bool = True,
    parallel: bool = True,
    progress_dir: Path | str | None = None,
) -> CheckRunReport:
    """Run all (or filtered) checks and return aggregate report.

    Args:
        db_path: SQLite corpus DB. Read-only.
        only: If set, only run checks whose dotted name matches one
              of these prefixes. e.g. ["dates", "schema.not_null"].
        critical_only: Skip WARNING + INFO checks. Used by the publish
              gate to keep gate runtime tight.
        record_history: Append measurements to quality/history.db for
              drift detection. Disable in tests / dry runs.
        parallel: Run checks in a thread pool. Disable for debugging.
        progress_dir: Where to write the per-check progress trail. Opt-in:
              when None, nothing is written. The publish gate passes it (via
              quality.cli) because that is the run that gets killed at a
              wall-clock cap and needs to leave a trail; library and test
              callers get no filesystem side effect.
    """
    db_path = Path(db_path)
    if not db_path.exists():
        raise FileNotFoundError(f"corpus DB not found: {db_path}")

    checks = discover_checks(critical_only=critical_only)
    if only:
        prefixes = list(only)

        def matches(check_fn: Callable) -> bool:
            n = _check_name(check_fn)
            return any(n == p or n.startswith(p + ".") or n.split(".")[0] == p
                       for p in prefixes)

        checks = [c for c in checks if matches(c)]

    ctx: dict = {"critical_only": critical_only, "db_path": str(db_path)}
    _progress_begin(Path(progress_dir) if progress_dir else None)
    started = time.monotonic()
    results: list[CheckResult] = []
    timings: dict[str, float] = {}

    def run_timed(fn: Callable) -> tuple[str, float, list[CheckResult]]:
        t0 = time.monotonic()
        out = _run_one(fn, db_path, ctx)
        return _check_name(fn), round(time.monotonic() - t0, 3), out

    if parallel and len(checks) > 1:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            futures = [ex.submit(run_timed, fn) for fn in checks]
            for fut in as_completed(futures):
                name, elapsed, out = fut.result()
                timings[name] = elapsed
                results.extend(out)
    else:
        for fn in checks:
            name, elapsed, out = run_timed(fn)
            timings[name] = elapsed
            results.extend(out)

    # Timings are taken before the gate filter below, so a passing WARNING
    # that costs half the run is still on record.
    if critical_only:
        results = gate_visible_results(results)

    duration = time.monotonic() - started
    report = CheckRunReport(
        run_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        db_path=str(db_path),
        duration_seconds=round(duration, 2),
        results=sorted(results, key=lambda r: (r.severity, r.name), reverse=True),
        scope=("critical_only" if critical_only
               else "subset" if only else "full"),
        check_timings=timings,
    )

    if record_history:
        try:
            from quality.baseline import append_measurements
            append_measurements(report)
        except Exception:
            logger.exception("history append failed (non-fatal)")

    return report


def write_report(report: CheckRunReport, out_dir: Path | str = DEFAULT_REPORT_DIR) -> Path:
    """Persist `latest.json` + per-run archive. Returns the archive path.

    The archive is keyed on the full run timestamp, not the day. Keying
    on the day meant a later run replaced an earlier one under the same
    name — and because the publish gate runs --critical-only, a FILTERED
    report silently replaced the day's complete one. Non-full runs also
    carry their scope in the filename so a partial report can never be
    mistaken for a full archive in a directory listing.
    """
    import json

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = report.run_at[:19].replace(":", "")     # 2026-08-19T142345
    suffix = "" if report.scope == "full" else f"-{report.scope}"
    dated = out_dir / f"{stamp}Z{suffix}.json"
    latest = out_dir / "latest.json"
    payload = report.to_dict()
    for p in (dated, latest):
        with open(p, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
    return dated
