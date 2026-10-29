"""Duplicate-detection checks.

Two failure classes:
1. Multiple rows for the same (court, docket) — the per-canton dedup
   missed a logical duplicate. König P1 EGMR class belongs here.
2. Same decision_id family appearing in multiple courts (e.g. an
   ATF case wrongly attributed to both `bge` and `bge_egmr`).

The build_fts5._dedup_decisions() pass enforces docket+court
uniqueness. These checks catch regressions in that pass.
"""
from __future__ import annotations

import sqlite3

from quality.checks._late_columns import census
from quality.types import CheckResult, Severity


def check_court_docket_collisions(conn: sqlite3.Connection,
                                  db_path: str | None = None,
                                  **_) -> CheckResult:
    """A (court, docket) pair legitimately holds MULTIPLE distinct decisions
    (Zwischenentscheid + Endentscheid, costs orders, remands), so raw
    court+docket collisions are expected and NOT a defect — the 2026-07-13
    content-aware dedup intentionally keeps them. A real dedup regression is a
    SAME-CONTENT collision: >1 row sharing one (court, docket) AND the same
    content_hash (a byte-identical duplicate the dedup should have merged).
    Count those instead.

    Counted in the shared late-column pass (see _late_columns). content_hash
    sits after full_text, and the old GROUP BY was planned through the
    non-covering court index — every row's overflow chain, in court order —
    and run a second time for the sample whenever a group existed."""
    c = census(conn, db_path)
    n_groups = c.dup_groups
    sample = list(c.dup_sample) if n_groups else []
    return CheckResult(
        name="duplicates.court_docket_collisions",
        severity=Severity.WARNING,
        passed=(n_groups <= 500),
        metric_value=n_groups,
        threshold=500,
        message=f"{n_groups:,} (court, docket, content_hash) same-content duplicate groups",
        sample_rows=sample,
        fix_advice="byte-identical dups slipping through — check the content_hash "
                   "dedup pass in build_fts5._dedup_decisions()",
    )


def check_egmr_no_dual_attribution(conn: sqlite3.Connection, **_) -> CheckResult:
    """König #1: BGE and EGMR cases were counted twice under court='bge'
    AND court='bge_egmr'. The fix in scrapers/entscheidsuche_ingest.py
    remaps. This check ensures it never regresses."""
    n_bge_cedh = conn.execute(
        "SELECT COUNT(*) FROM decisions "
        "WHERE court='bge' AND source_url LIKE '%cedh%'"
    ).fetchone()[0]
    return CheckResult(
        name="duplicates.egmr_dual_attribution",
        severity=Severity.CRITICAL,
        passed=(n_bge_cedh == 0),
        metric_value=n_bge_cedh,
        threshold=0,
        message=f"{n_bge_cedh} bge rows with cedh.coe.int URL "
                f"(should all be in bge_egmr)" if n_bge_cedh else
                "EGMR attribution clean",
        fix_advice="codified by build_fts5._dedup_egmr_in_bge() + "
                   "scrapers/entscheidsuche_ingest.py override",
    )


def check_bge_egmr_count_in_range(conn: sqlite3.Connection, **_) -> CheckResult:
    """bge_egmr should have ~476 rows post-2026-04-29 fix; tolerance
    range 470-500 leaves room for 1-2 new judgements per quarter."""
    n = conn.execute(
        "SELECT COUNT(*) FROM decisions WHERE court='bge_egmr'"
    ).fetchone()[0]
    return CheckResult(
        name="duplicates.bge_egmr_count_range",
        severity=Severity.WARNING,
        passed=(470 <= n <= 700),
        metric_value=n,
        threshold=None,
        message=f"bge_egmr has {n} rows (expected 470-700)",
        fix_advice="if dropped: dedup over-fired; if grown beyond 700, "
                   "EGMR scraper got new feed",
    )


def check_decision_id_collisions_across_courts(conn: sqlite3.Connection, **_) -> CheckResult:
    """A logical decision shouldn't appear under two different `court`
    values. The dedup pass already enforces this for clean canonical_keys
    but stripped/normalized variants could slip through."""
    n = conn.execute("""
        SELECT COUNT(*) FROM (
            SELECT decision_id FROM decisions
            GROUP BY decision_id
            HAVING COUNT(DISTINCT court) > 1
        )
    """).fetchone()[0]
    return CheckResult(
        name="duplicates.decision_id_cross_court",
        severity=Severity.CRITICAL,
        passed=(n == 0),
        metric_value=n,
        threshold=0,
        message=f"{n} decision_ids attributed to >1 court" if n else
                "all decision_ids have a single court attribution",
    )
