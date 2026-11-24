"""Structured-extraction sidecar checks (decision_structure.db).

The structure sidecar holds Sachverhalt + Erwägungen-paragraphs +
Dispositiv + Regeste for ~700k decisions (federal + cantonal as of
2026-04-29). These checks verify:

- Sidecar table exists and has reasonable count
- E-numbers parse as a sortable hierarchy (no garbage like "abc.def")
- Federal decisions have ≥1 Erwägung paragraph (BGer/BGE always do)
- Cantonal coverage hasn't regressed below the post-2026-04-29 baseline
"""
from __future__ import annotations

import os
import sqlite3
from datetime import date
from pathlib import Path

from quality.types import CheckResult, Severity


def _structure_db_path() -> Path:
    base = Path(os.environ.get(
        "SWISS_CASELAW_STRUCTURE_DB",
        "output/decision_structure.db",
    ))
    return base


def _open_structure() -> sqlite3.Connection | None:
    p = _structure_db_path()
    if not p.exists():
        return None
    conn = sqlite3.connect(f"file:{p}?mode=ro&immutable=1", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def check_sidecar_exists(conn: sqlite3.Connection, **_) -> CheckResult:
    """The structure sidecar must exist for /erwaegung and /regeste
    endpoints to function."""
    p = _structure_db_path()
    return CheckResult(
        name="structure.sidecar_exists",
        severity=Severity.CRITICAL,
        passed=p.exists(),
        metric_value=1 if p.exists() else 0,
        threshold=1,
        message=f"sidecar at {p}" + (
            " (present)" if p.exists() else " (MISSING — /erwaegung broken)"),
        fix_advice="run search_stack/extract_decision_structure.py",
    )


def check_sidecar_population(conn: sqlite3.Connection, **_):
    """Total counts in structure + erwaegungen_paragraph tables."""
    sconn = _open_structure()
    if sconn is None:
        return
    try:
        n_struct = sconn.execute("SELECT COUNT(*) FROM structure").fetchone()[0]
        n_para = sconn.execute(
            "SELECT COUNT(*) FROM erwaegungen_paragraph"
        ).fetchone()[0]
    finally:
        sconn.close()

    yield CheckResult(
        name="structure.row_count",
        severity=Severity.WARNING,
        passed=(n_struct >= 700_000),
        metric_value=n_struct,
        threshold=700_000,
        message=f"structure rows: {n_struct:,}",
        fix_advice="if dropped, decision_structure rebuild may have failed; "
                   "check publish.py Step 2g logs",
    )
    yield CheckResult(
        name="structure.paragraph_count",
        severity=Severity.WARNING,
        passed=(n_para >= 5_000_000),
        metric_value=n_para,
        threshold=5_000_000,
        message=f"erwaegungen_paragraph rows: {n_para:,}",
    )


def check_e_number_sortable(conn: sqlite3.Connection, **_) -> CheckResult:
    """Every e_number should be a dotted numeric like '1', '2.3',
    '4.1.2', optionally with an old-style letter run on the last level
    ('2a', '2aa', '3c/aa', '3.3.3bb' — since 2026-09-06 the extractor
    keeps lettered sub-Erwägungen). Anything else means the heading
    parser produced garbage."""
    sconn = _open_structure()
    if sconn is None:
        return CheckResult(
            name="structure.e_number_sortable",
            severity=Severity.INFO,
            passed=True,
            metric_value=0,
            threshold=None,
            message="sidecar absent — skipped",
        )
    try:
        # Sample 10k random rows for cost
        rows = sconn.execute(
            "SELECT decision_id, e_number FROM erwaegungen_paragraph "
            "ORDER BY random() LIMIT 10000"
        ).fetchall()
    finally:
        sconn.close()

    import re
    pat = re.compile(r"^\d+(?:\.\d+)*[a-z]{0,2}(?:/[a-z]{1,2})?$")
    bad = [r for r in rows if not pat.match(r["e_number"] or "")]
    pct = round(100 * len(bad) / max(1, len(rows)), 3)
    return CheckResult(
        name="structure.e_number_sortable",
        severity=Severity.WARNING,
        passed=(pct <= 1.0),
        metric_value=pct,
        threshold=1.0,
        message=f"{len(bad)}/{len(rows):,} sample paragraphs "
                f"({pct:.2f}%) have non-numeric e_number",
        sample_rows=[
            {"decision_id": r["decision_id"], "e_number": r["e_number"]}
            for r in bad[:5]
        ],
        fix_advice="check extract_decision_structure.py heading parser",
    )


def _years_ago(n: int, today: date | None = None) -> str:
    """ISO date n years before today (29 Feb falls back to 28 Feb). The
    cutoff used to be the literal '2021-01-01', which stopped meaning
    "older than five years" once 2026 began."""
    today = today or date.today()
    try:
        return today.replace(year=today.year - n).isoformat()
    except ValueError:
        return today.replace(year=today.year - n, day=28).isoformat()


def check_federal_have_erwaegungen(conn: sqlite3.Connection, **_) -> CheckResult:
    """Every BGer decision >5 years old has at least one Erwägung in
    the sidecar (newer ones may still be in extraction queue)."""
    sconn = _open_structure()
    if sconn is None:
        return CheckResult(
            name="structure.federal_have_erwaegungen",
            severity=Severity.INFO,
            passed=True,
            metric_value=0,
            threshold=None,
            message="sidecar absent — skipped",
        )
    try:
        # Sample 1k random old BGer decisions and check sidecar coverage
        bger_ids = [r[0] for r in conn.execute(
            "SELECT decision_id FROM decisions "
            "WHERE court='bger' AND decision_date < ? "
            "ORDER BY random() LIMIT 1000",
            (_years_ago(5),),
        ).fetchall()]
        if not bger_ids:
            return CheckResult(
                name="structure.federal_have_erwaegungen",
                severity=Severity.INFO,
                passed=True,
                metric_value=0,
                threshold=None,
                message="no old BGer decisions in DB — skipped",
            )
        placeholders = ",".join("?" * len(bger_ids))
        with_para = sconn.execute(
            f"SELECT COUNT(DISTINCT decision_id) "
            f"FROM erwaegungen_paragraph WHERE decision_id IN ({placeholders})",
            bger_ids,
        ).fetchone()[0]
    finally:
        sconn.close()
    coverage = round(100 * with_para / len(bger_ids), 1)
    return CheckResult(
        name="structure.federal_have_erwaegungen",
        severity=Severity.WARNING,
        passed=(coverage >= 95.0),
        metric_value=coverage,
        threshold=95.0,
        message=f"BGer (>5y old): {with_para}/{len(bger_ids)} "
                f"sample have sidecar paragraphs ({coverage}%)",
    )
