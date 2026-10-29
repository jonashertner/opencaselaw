"""Core types for the quality-control framework."""
from __future__ import annotations

import enum
from dataclasses import dataclass, field, asdict
from typing import Any


class Severity(enum.Enum):
    """A check's severity tier.

    CRITICAL   → blocks publish.py Step 6 (git push) on failure.
    QUARANTINE → count-bounded data defects auto-neutralised at build time
                 (build_fts5._normalize_dates); fires an ntfy alert but does
                 NOT block the publish. A single cosmetic source typo must not
                 freeze HF upload + git push (the 2026-06-03..06 lesson).
    WARNING    → fires ntfy.sh alert, pipeline continues.
    INFO       → recorded in report, no alerting.
    """
    CRITICAL = "critical"
    QUARANTINE = "quarantine"
    WARNING = "warning"
    INFO = "info"

    def __lt__(self, other: "Severity") -> bool:
        order = {Severity.INFO: 0, Severity.WARNING: 1,
                 Severity.QUARANTINE: 2, Severity.CRITICAL: 3}
        return order[self] < order[other]


@dataclass
class CheckResult:
    """The structured result of a single quality check.

    name           Stable identifier, e.g. "dates.future_dates" or
                   "per_court_drift.zh_obergericht.row_count".
    severity       Tier this metric defends; controls block-vs-alert.
    passed         True if the check is within its tolerance.
    metric_value   The measured value (e.g. 471 NULL dates).
    threshold      The configured threshold; None for descriptive checks.
    message        One-line human summary.
    sample_rows    Up to 5 offending rows, for triage. Each row is a
                   dict of column → value to keep the report compact.
    fix_advice     How to remediate. Either a codified remedy reference
                   ("auto-corrected by build_fts5._normalize_dates()")
                   or a manual instruction.
    court          Optional per-court scoping for drift / per-court checks.
    extra          Additional structured detail (e.g. per-bucket counts).
    elapsed_s      Wall time of the check that produced this result, set by
                   runner._run_one. Note the runner is parallel
                   (MAX_WORKERS=4), so these overlap and do NOT sum to the
                   run duration — use `quality.cli run --serial` when
                   you need attributable per-check cost. None when the
                   result was built outside the runner (tests, ad-hoc use).
    """
    name: str
    severity: Severity
    passed: bool
    metric_value: float | int
    threshold: float | int | None
    message: str
    sample_rows: list[dict[str, Any]] = field(default_factory=list)
    fix_advice: str | None = None
    court: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    elapsed_s: float | None = None

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable form. Severity rendered as its string value."""
        d = asdict(self)
        d["severity"] = self.severity.value
        return d

    @property
    def is_blocking_failure(self) -> bool:
        """True iff this result would block publish.py Step 6 (git push)."""
        return (not self.passed) and self.severity is Severity.CRITICAL

    @property
    def is_alerting_failure(self) -> bool:
        """True iff this result fires an ntfy alert (CRITICAL or WARNING failure)."""
        return (not self.passed) and self.severity is not Severity.INFO


@dataclass
class CheckRunReport:
    """Aggregate of one QC run.

    Written to quality/reports/<run_at>[-gate].json + reports/latest.json
    + appended to quality/history.db for drift detection.
    """
    run_at: str                       # ISO 8601 UTC timestamp
    db_path: str
    duration_seconds: float
    results: list[CheckResult]
    # Which checks this run actually executed: 'full', 'critical_only'
    # (the publish gate) or 'subset' (--only). A filtered report must say
    # so — until 2026-08-19 a gate run archived under the same
    # YYYY-MM-DD.json name as the day's full run and silently replaced a
    # complete report with a partial one.
    scope: str = "full"
    # Wall time of every check that ran, keyed by check name ("urls.http_count"
    # style, one entry per check function, not per fanned-out result). Kept
    # separately from the results because a --critical-only run drops passing
    # WARNING/INFO results from `results`, and their timings went with them:
    # the gate's slowest check could be one the report never showed.
    check_timings: dict[str, float] = field(default_factory=dict)

    @property
    def critical_failures(self) -> list[CheckResult]:
        return [r for r in self.results if r.is_blocking_failure]

    @property
    def warning_failures(self) -> list[CheckResult]:
        return [
            r for r in self.results
            if (not r.passed) and r.severity is Severity.WARNING
        ]

    @property
    def quarantine_failures(self) -> list[CheckResult]:
        """Count-bounded data defects that alert but do NOT block the publish."""
        return [
            r for r in self.results
            if (not r.passed) and r.severity is Severity.QUARANTINE
        ]

    @property
    def passed(self) -> bool:
        """True iff no CRITICAL check failed (= safe to git push).

        QUARANTINE/WARNING failures alert but never block — only CRITICAL
        cascades to skip HF upload + git push."""
        return not self.critical_failures

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_at": self.run_at,
            "scope": self.scope,
            "db_path": self.db_path,
            "duration_seconds": self.duration_seconds,
            "summary": {
                "total": len(self.results),
                "passed": sum(1 for r in self.results if r.passed),
                "critical_failures": len(self.critical_failures),
                "quarantine_failures": len(self.quarantine_failures),
                "warning_failures": len(self.warning_failures),
                "publish_safe": self.passed,
            },
            "check_timings": dict(sorted(
                self.check_timings.items(), key=lambda kv: (-kv[1], kv[0]))),
            "results": [r.to_dict() for r in self.results],
        }
