"""Citation-graph integrity checks (output/reference_graph.db).

- Edge counts in the expected range (catches a failed graph build)
- Top-cited decisions remain top-cited (sanity check on ranking)
- Resolution rate (raw → resolved) above the historical 73%
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from quality.checks._common import count_statute_edges
from quality.types import CheckResult, Severity

MODULE_NEVER_CRITICAL = True  # all checks here return WARNING/INFO; runner skips in --critical-only


def _rg_path() -> Path:
    return Path(os.environ.get(
        "SWISS_CASELAW_REFERENCE_GRAPH", "output/reference_graph.db",
    ))


def _open_rg() -> sqlite3.Connection | None:
    p = _rg_path()
    if not p.exists():
        return None
    rg = sqlite3.connect(f"file:{p}?mode=ro&immutable=1", uri=True)
    rg.row_factory = sqlite3.Row
    rg.execute("PRAGMA busy_timeout=30000")
    return rg


def check_top_cited_known_leader(conn: sqlite3.Connection, **_) -> CheckResult:
    """BGE 125 V 351 (Mahnke) is the most-cited Swiss decision —
    >50,000 incoming citations as of Mar 2026. If it drops below
    40,000, citation extraction has regressed."""
    rg = _open_rg()
    if rg is None:
        return CheckResult(
            name="citation_graph.top_cited_known_leader",
            severity=Severity.INFO,
            passed=True,
            metric_value=0,
            threshold=None,
            message="reference_graph.db absent — skipped",
        )
    try:
        # Look for any of the known canonical id forms for BGE 125 V 351: the
        # entscheidsuche id and the direct scraper's id, which is the only
        # one left once es_bge.jsonl is retired (#40)
        candidates = ("bge_BGE_125_V_351", "bge_125 V 351", "BGE 125 V 351", "BGE_125_V_351")
        n_max = 0
        for cid in candidates:
            try:
                n = rg.execute(
                    "SELECT COUNT(*) FROM citation_targets WHERE target_decision_id=?",
                    (cid,),
                ).fetchone()[0]
            except sqlite3.OperationalError:
                continue
            if n > n_max:
                n_max = n
    finally:
        rg.close()
    return CheckResult(
        name="citation_graph.top_cited_known_leader",
        severity=Severity.WARNING,
        passed=(n_max >= 40_000),
        metric_value=n_max,
        threshold=40_000,
        message=f"BGE 125 V 351 has {n_max:,} incoming citations "
                f"(expected >40,000)",
        fix_advice="if dropped, citation extraction regression in "
                   "search_stack/build_reference_graph.py — likely "
                   "_docket_norm() or _resolve_citation_targets()",
    )


def check_total_edge_count(conn: sqlite3.Connection, **_):
    """Total citation edges and statute edges — measured at 9.98M /
    12.41M on 2026-07-26. The floors below catch a broken graph
    rebuild, not ordinary drift."""
    rg = _open_rg()
    if rg is None:
        return
    try:
        tables = {r[0] for r in rg.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        cit = (
            rg.execute("SELECT COUNT(*) FROM citation_targets").fetchone()[0]
            if "citation_targets" in tables else 0
        )
        stat = count_statute_edges(rg)
    finally:
        rg.close()

    yield CheckResult(
        name="citation_graph.citation_targets_count",
        severity=Severity.WARNING,
        passed=(cit >= 5_500_000),
        metric_value=cit,
        threshold=5_500_000,
        message=f"citation_targets: {cit:,} (5% floor: 5.5M)",
    )
    yield CheckResult(
        name="citation_graph.statute_edges_count",
        severity=Severity.WARNING,
        passed=(stat >= 9_000_000),
        metric_value=stat,
        threshold=9_000_000,
        message=f"decision_statutes: {stat:,} (floor: 9M)",
    )


def check_resolution_rate(conn: sqlite3.Connection, **_) -> CheckResult:
    """citation_references is the raw extraction; citation_targets is
    the resolved subset. Historical resolution rate is ~73.8%. <70% is
    a regression."""
    rg = _open_rg()
    if rg is None:
        return CheckResult(
            name="citation_graph.resolution_rate",
            severity=Severity.INFO,
            passed=True,
            metric_value=0,
            threshold=None,
            message="reference_graph.db absent — skipped",
        )
    try:
        tables = {r[0] for r in rg.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        if "citation_references" not in tables or "citation_targets" not in tables:
            return CheckResult(
                name="citation_graph.resolution_rate",
                severity=Severity.INFO,
                passed=True,
                metric_value=0,
                threshold=None,
                message="citation_references/citation_targets table missing",
            )
        n_raw = rg.execute("SELECT COUNT(*) FROM citation_references").fetchone()[0]
        n_resolved = rg.execute("SELECT COUNT(*) FROM citation_targets").fetchone()[0]
    finally:
        rg.close()
    rate = round(100 * n_resolved / max(1, n_raw), 2)
    return CheckResult(
        name="citation_graph.resolution_rate",
        severity=Severity.WARNING,
        passed=(rate >= 70.0),
        metric_value=rate,
        threshold=70.0,
        message=f"resolution rate: {rate:.2f}% "
                f"({n_resolved:,}/{n_raw:,})",
        fix_advice="if dropped, _resolve_citation_targets() may have lost "
                   "a docket-format variant; check _docket_norm",
    )
