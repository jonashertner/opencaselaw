"""Date-quality checks (König P4 + P6 + plausibility).

Catches:
- NULL/empty dates outside known floor
- Year-0000 placeholder dates (build_fts5 _normalize_dates auto-fixes;
  this check ensures it actually ran)
- Future dates beyond the next 30 days (typo / parser bug)
- Pre-1700 dates (parser bug — earliest legitimate decision is BGE 1875)
- Far-future dates beyond next year (catastrophic typo)
"""
from __future__ import annotations

import re
import sqlite3
from datetime import date, timedelta

from quality.types import CheckResult, Severity


# Known "source-data floor" — these residual NULL counts are not bugs;
# they reflect court archives that genuinely lack machine-readable dates.
# Floors codified in docs/MIGRATIONS.md (2026-04-30).
KNOWN_NULL_DATE_FLOORS = {
    "mkg": 542,            # 1914-2010 archive; dates only in scanned images
    "ti_gerichte": 549,    # PDFs truncated at ~1.5K chars (no body)
    "hudoc_ch": 246,       # ECHR metadata-only docs
    "sav_kantone": 36,     # Aufsichtsbehörden — no PDF, only metadata
    "fr_gerichte": 80,     # post-recovery residual
    # post-recovery residual (80) + up to ~188 Praxis-digest rows whose
    # text-recovered dates were junk and are NULLed by
    # build_fts5._null_implausible_gr_dates since 2026-07-02 (backlog L2);
    # the guarded recovery may refill some from deeper true-date anchors.
    "gr_gerichte": 270,
    # Arbeitsgericht Zürich yearbooks 2003–2023 (scrapers/cantonal/
    # zh_arbeitsgericht_sammlung.py): the judgment date comes from each
    # excerpt's closing trailer; ~25 of 530 excerpts have none and are left
    # NULL by design (the volume year is in the docket "AGer-Z 2011 Nr. 8").
    "zh_arbeitsgericht": 40,
}


def check_year_0000_dates(conn: sqlite3.Connection, **_) -> CheckResult:
    """Build_fts5._normalize_dates() converts year-0000 placeholders to
    NULL. Any row with `decision_date LIKE '0000%'` means the auto-fix
    didn't run."""
    n = conn.execute(
        "SELECT COUNT(*) FROM decisions WHERE decision_date LIKE '0000%'"
    ).fetchone()[0]
    return CheckResult(
        name="dates.year_0000",
        severity=Severity.QUARANTINE,  # auto-NULLed by _normalize_dates; warn-not-block
        passed=(n == 0),
        metric_value=n,
        threshold=0,
        message=f"{n} rows with year-0000 dates" if n else
                "no year-0000 placeholder dates",
        fix_advice="build_fts5._normalize_dates() auto-converts to NULL; "
                   "verify it ran on this build",
    )


def check_far_future_dates(conn: sqlite3.Connection, **_) -> CheckResult:
    """Dates beyond today+365 are catastrophic typos — never legitimate."""
    cutoff = (date.today() + timedelta(days=365)).isoformat()
    n = conn.execute(
        "SELECT COUNT(*) FROM decisions WHERE decision_date > ?", (cutoff,)
    ).fetchone()[0]
    return CheckResult(
        name="dates.far_future",
        severity=Severity.QUARANTINE,  # auto-NULLed by _normalize_dates; warn-not-block
        passed=(n == 0),
        metric_value=n,
        threshold=0,
        message=f"{n} rows dated > today+365d" if n else
                "no far-future dates",
        fix_advice="build_fts5._normalize_dates() auto-NULLs these; "
                   "check scraper date-parsing for German/French month confusion",
    )


def check_future_dates_window(conn: sqlite3.Connection, **_) -> CheckResult:
    """Dates within today+30 ≤ d ≤ today+365 are sometimes legit (pending
    publication), but >50 means parser bug. König 2026-04-30 measured
    12 such rows in production (fr_gerichte+sz_gerichte month-name parse
    bug)."""
    cutoff = (date.today() + timedelta(days=30)).isoformat()
    n = conn.execute(
        "SELECT COUNT(*) FROM decisions WHERE decision_date > ?", (cutoff,)
    ).fetchone()[0]
    sample = [
        dict(r) for r in conn.execute(
            "SELECT decision_id, court, decision_date FROM decisions "
            "WHERE decision_date > ? ORDER BY decision_date DESC LIMIT 5",
            (cutoff,),
        ).fetchall()
    ] if n else []
    return CheckResult(
        name="dates.future_window",
        severity=Severity.QUARANTINE,  # count-bounded (>50 = parser regression); warn-not-block
        passed=(n <= 50),
        metric_value=n,
        threshold=50,
        message=f"{n} rows dated > today+30d (threshold 50)" if n > 50 else
                f"{n} future-dated rows (within tolerance)",
        sample_rows=sample,
        fix_advice="if >50, expect a scraper date-parsing regression",
    )


def check_pre_1700_dates(conn: sqlite3.Connection, **_) -> CheckResult:
    """No legitimate Swiss court decision predates 1700. The earliest
    indexed BGE is 1875. Anything older is a parser bug."""
    n = conn.execute(
        "SELECT COUNT(*) FROM decisions WHERE decision_date IS NOT NULL "
        "AND decision_date != '' AND decision_date < '1700-01-01'"
    ).fetchone()[0]
    sample = [
        dict(r) for r in conn.execute(
            "SELECT decision_id, court, decision_date FROM decisions "
            "WHERE decision_date IS NOT NULL AND decision_date != '' "
            "AND decision_date < '1700-01-01' LIMIT 5"
        ).fetchall()
    ] if n else []
    return CheckResult(
        name="dates.pre_1700",
        severity=Severity.QUARANTINE,  # auto-NULLed by _normalize_dates; warn-not-block (froze publish 2026-06-03..06)
        passed=(n == 0),
        metric_value=n,
        threshold=0,
        message=f"{n} rows dated before 1700-01-01" if n else
                "no pre-1700 dates",
        sample_rows=sample,
    )


def check_invalid_date_format(conn: sqlite3.Connection, **_) -> CheckResult:
    """Decision dates must be ISO 8601 (YYYY-MM-DD). Anything shorter is
    truncated; anything longer has trailing junk."""
    n = conn.execute(
        "SELECT COUNT(*) FROM decisions WHERE decision_date IS NOT NULL "
        "AND decision_date != '' AND decision_date != 'None' "
        "AND length(decision_date) != 10"
    ).fetchone()[0]
    return CheckResult(
        name="dates.invalid_format",
        severity=Severity.CRITICAL,
        passed=(n == 0),
        metric_value=n,
        threshold=0,
        message=f"{n} rows with non-ISO-8601 decision_date" if n else
                "all dates are ISO 8601 format",
    )


def check_null_dates_floor(conn: sqlite3.Connection, **_):
    """For every court with a known NULL-date floor (mkg, ti_gerichte,
    hudoc_ch, …), assert the count hasn't grown materially. Floors are
    documented in docs/MIGRATIONS.md.

    Per-court WARNING: drift below the floor is fine (more recovery!),
    drift above by >10% means a regression."""
    for court, floor in KNOWN_NULL_DATE_FLOORS.items():
        n = conn.execute(
            "SELECT COUNT(*) FROM decisions WHERE court=? AND "
            "(decision_date IS NULL OR decision_date='')", (court,),
        ).fetchone()[0]
        upper = max(int(floor * 1.10), floor + 10)
        yield CheckResult(
            name=f"dates.null_date_floor.{court}",
            severity=Severity.WARNING,
            passed=(n <= upper),
            metric_value=n,
            threshold=upper,
            message=f"{court}: {n} NULL decision_dates (floor {floor})",
            court=court,
            fix_advice="if growing, check the court-specific recovery script in "
                       "build_fts5._recover_decision_dates / scripts/",
        )


# Baseline for publication_date < decision_date inversions. A court CANNOT
# publish before it rules, so every one is a mislabel/parse error
# (decision_date — the header ruling date — is trusted; publication_date is
# optional and the suspect field). As of 2026-06-16 build_fts5 has a GROSS
# forward guard (_date_inversion_guard_inline) that NULLs any pub_date >31
# days before the ruling — it removes the ~21,603 gross inversions on every
# full rebuild, leaving only the ~24,587 small-band (0-3 day / days-to-1mo,
# possible dispatch dates) which are intentionally NOT corrected. Baseline
# set with headroom above that; a scraper regression that re-introduces
# gross inversions trips it. WARNING (alerts, doesn't block).
PUB_BEFORE_DEC_BASELINE = 28000


def check_publication_before_decision(conn: sqlite3.Connection, **_) -> CheckResult:
    """publication_date earlier than decision_date is impossible if the two
    are labeled correctly — a court does not publish before it rules. Counts
    inversions, with a gross (>1 month) subset and per-court breakdown so the
    'document-vs-ruling date' band (0-3 days) is distinguished from outright
    mislabels (months/years)."""
    n = conn.execute(
        "SELECT COUNT(*) FROM decisions "
        "WHERE publication_date IS NOT NULL AND publication_date != '' "
        "AND decision_date IS NOT NULL AND decision_date != '' "
        "AND publication_date < decision_date"
    ).fetchone()[0]
    gross = conn.execute(
        "SELECT COUNT(*) FROM decisions "
        "WHERE publication_date IS NOT NULL AND publication_date != '' "
        "AND decision_date IS NOT NULL AND decision_date != '' "
        "AND publication_date < date(decision_date, '-31 days')"
    ).fetchone()[0]
    by_court = {
        r["court"]: r["n"] for r in conn.execute(
            "SELECT court, COUNT(*) AS n FROM decisions "
            "WHERE publication_date IS NOT NULL AND publication_date != '' "
            "AND decision_date IS NOT NULL AND decision_date != '' "
            "AND publication_date < decision_date "
            "GROUP BY court ORDER BY n DESC LIMIT 10"
        ).fetchall()
    }
    sample = [
        dict(r) for r in conn.execute(
            "SELECT decision_id, court, decision_date, publication_date "
            "FROM decisions WHERE publication_date IS NOT NULL "
            "AND publication_date != '' AND decision_date IS NOT NULL "
            "AND decision_date != '' "
            "AND publication_date < date(decision_date, '-31 days') LIMIT 5"
        ).fetchall()
    ] if gross else []
    return CheckResult(
        name="dates.publication_before_decision",
        severity=Severity.WARNING,
        passed=(n <= PUB_BEFORE_DEC_BASELINE),
        metric_value=n,
        threshold=PUB_BEFORE_DEC_BASELINE,
        message=(
            f"{n} rows with publication_date < decision_date "
            f"({gross} gross >1mo) — baseline {PUB_BEFORE_DEC_BASELINE}"
        ),
        sample_rows=sample,
        extra={"gross_over_1mo": gross, "by_court": by_court},
        fix_advice="decision_date (header ruling date) is trusted; run "
                   "scripts/fix_date_inversions.py to swap-or-NULL the "
                   "suspect publication_date + add a build_fts5 forward "
                   "guard. Growth here = a scraper labeling the dates "
                   "backwards.",
    )


# ── Docket-year plausibility (LegalStats wishlist P0.3 / backlog L2) ──
#
# A decision cannot predate its docket's registration year by more than a
# year-boundary edge case. Measured 2026-07-02 on the served DB: 9,761 rows
# violate this under STRICT extraction (627,265 rows have an unambiguous
# registration year in the docket), led by ge_gerichte 3,829 /
# zh_verwaltungsgericht 1,188 / ne_gerichte 885 — a pre-existing stock
# tracked as backlog L2/P0.3b cleanup. This check BASELINES the stock and
# alerts on GROWTH (a scraper date regression), mirroring the
# publication_before_decision pattern. Strictness matters: a naive
# "any year in the docket" extractor inflates the count to ~15k with
# cause-year / cited-docket noise (GE), so only two unambiguous
# registration-year positions are trusted.
DOCKET_YEAR_IMPOSSIBLE_BASELINE = 10_800  # 9,761 measured + headroom

_YEAR = r"(18[7-9]\d|19\d\d|20[0-3]\d)"
_TRAILING_YEAR = re.compile(r"[/_.]" + _YEAR + r"\s*$")
_MIDDLE_YEAR = re.compile(
    r"^[A-Za-z][A-Za-z0-9]{0,14}[ ./-]" + _YEAR + r"[ ./-]\d")


def docket_registration_year(docket: str) -> int | None:
    """Registration year from a docket string, STRICT positions only.

    Trusted: trailing separator-year ("5A_1008/2025", "HC/2024.15") and
    code-year-number ("VSKLA.2024.5", "SR2 2025 84"). Anything else
    (cause years, cited foreign dockets, bare numbers) returns None —
    NULL over guess."""
    m = _TRAILING_YEAR.search(docket)
    if m:
        return int(m.group(1))
    m = _MIDDLE_YEAR.match(docket)
    if m:
        return int(m.group(1))
    return None


def check_docket_year_plausibility(conn: sqlite3.Connection, **_) -> CheckResult:
    """decision_date more than 1 year BEFORE the docket registration year
    is impossible (courts do not rule before a case exists). Counts the
    corpus-wide stock and alerts on growth over the recorded baseline."""
    from collections import Counter

    n = 0
    by_court: Counter = Counter()
    sample = []
    for docket, d, court, did in conn.execute(
        "SELECT docket_number, decision_date, court, decision_id "
        "FROM decisions WHERE docket_number IS NOT NULL "
        "AND decision_date IS NOT NULL AND decision_date != '' "
        "AND length(decision_date) = 10"
    ):
        year = docket_registration_year(docket or "")
        if year is None:
            continue
        try:
            delta = int(d[:4]) - year
        except ValueError:
            continue
        if delta < -1:
            n += 1
            by_court[court] += 1
            if len(sample) < 5 and delta <= -3:
                sample.append({"decision_id": did, "docket_number": docket,
                               "decision_date": d})
    return CheckResult(
        name="dates.docket_year_plausibility",
        severity=Severity.WARNING,  # pre-existing stock; alerts on growth, never blocks
        passed=(n <= DOCKET_YEAR_IMPOSSIBLE_BASELINE),
        metric_value=n,
        threshold=DOCKET_YEAR_IMPOSSIBLE_BASELINE,
        message=(
            f"{n} rows dated >1y before their docket registration year "
            f"(baseline {DOCKET_YEAR_IMPOSSIBLE_BASELINE})"
        ),
        sample_rows=sample,
        extra={"by_court": dict(by_court.most_common(10))},
        fix_advice="growth = a scraper date-parsing regression (check the top "
                   "court in by_court); the pre-existing stock is backlog "
                   "L2/P0.3b (ge, zh_vwg, ne, sg, bs, gr cleanup)",
    )


def check_total_null_dates(conn: sqlite3.Connection, **_) -> CheckResult:
    """Aggregate: total rows with NULL decision_date should not exceed
    the sum of known floors + 200 absolute slack."""
    n = conn.execute(
        "SELECT COUNT(*) FROM decisions WHERE "
        "(decision_date IS NULL OR decision_date='')"
    ).fetchone()[0]
    expected_max = sum(KNOWN_NULL_DATE_FLOORS.values()) + 200
    return CheckResult(
        name="dates.total_null",
        severity=Severity.WARNING,
        passed=(n <= expected_max),
        metric_value=n,
        threshold=expected_max,
        message=f"{n} total NULL decision_dates (expected ≤ {expected_max})",
        fix_advice="if growing, identify the new court contributing NULL dates "
                   "via per-court breakdown",
    )
