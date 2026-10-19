#!/usr/bin/env python3
"""Warm the decision_structure.db sidecar's covering indexes after a swap.

Why: every step-2g swap (and every worker restart on a cold host) leaves the
52 GB served-text sidecar without page cache. The pinpoint attach in
search_decisions does an index seek plus row reads per top result, and on a
cold file every seek is a random read on the volume: fresh searches took
80-96 s on 2026-09-10 00:20 UTC while FTS itself answered in 10 ms. Warming
the indexes alone (~100 s of sequential-ish reads, a few GB) brought that
down to 8-30 s and improving; the 73 GB decisions.db and the sidecar cannot
both fit the cache, the indexes can.

What it does: three index-only queries, each under a wall-clock cap, with the
query plan printed so a future schema change that turns one of them into a
table scan is visible. Read-only (mode=ro&immutable=1), never fails the
caller (exit 0 unless --strict), safe to run at any time. Run it with
`ionice -c 2 -n 7` when serving traffic matters.

    python3 scripts/warm_structure_sidecar.py [--structure-db PATH] [--budget-s 900]
"""
from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
import time
from pathlib import Path

logger = logging.getLogger("warm_structure_sidecar")

# (label, sql, substring the query plan must contain to count as index-only)
WARM_QUERIES: tuple[tuple[str, str, str], ...] = (
    ("erwaegungen_paragraph decision_id index",
     "SELECT count(DISTINCT decision_id) FROM erwaegungen_paragraph",
     "INDEX"),
    ("structure primary key",
     "SELECT count(*) FROM structure WHERE decision_id > ''",
     "INDEX"),
    ("structure row count (smallest index)",
     "SELECT count(*) FROM structure",
     "INDEX"),
)

_PROGRESS_EVERY_OPCODES = 100_000


def _plan(conn: sqlite3.Connection, sql: str) -> str:
    try:
        rows = conn.execute("EXPLAIN QUERY PLAN " + sql).fetchall()
    except sqlite3.Error as e:
        return f"(plan unavailable: {e})"
    return " | ".join(str(r[-1]) for r in rows)


def warm(structure_db: Path, budget_s: float = 900.0,
         queries=WARM_QUERIES) -> dict:
    """Run the warm queries under one shared wall-clock budget.

    Returns {"ran": [...], "skipped": [...], "elapsed_s": float, "path": str}.
    A missing or unreadable sidecar is a skip, not an error."""
    result: dict = {"ran": [], "skipped": [], "elapsed_s": 0.0,
                    "path": str(structure_db)}
    if budget_s <= 0:
        result["skipped"].append(("all", "budget 0"))
        return result
    real = Path(os.path.realpath(structure_db))
    result["path"] = str(real)
    if not real.exists():
        logger.warning(f"sidecar not found at {real} — nothing to warm")
        result["skipped"].append(("all", "sidecar missing"))
        return result
    t_all = time.monotonic()
    deadline = t_all + budget_s
    try:
        conn = sqlite3.connect(f"file:{real}?mode=ro&immutable=1", uri=True)
    except sqlite3.Error as e:
        logger.warning(f"cannot open {real}: {e}")
        result["skipped"].append(("all", f"open failed: {e}"))
        return result
    try:
        conn.set_progress_handler(
            lambda: 1 if time.monotonic() > deadline else 0, _PROGRESS_EVERY_OPCODES)
        for label, sql, must_contain in queries:
            left = deadline - time.monotonic()
            if left <= 0:
                result["skipped"].append((label, "budget exhausted"))
                logger.warning(f"  {label}: SKIPPED (budget exhausted)")
                continue
            plan = _plan(conn, sql)
            if "interrupted" in plan:
                # the budget ran out while planning: same outcome as below
                result["skipped"].append((label, "budget exhausted"))
                logger.warning(f"  {label}: SKIPPED (budget exhausted)")
                continue
            if must_contain and must_contain not in plan.upper():
                result["skipped"].append((label, f"not index-only: {plan}"))
                logger.warning(f"  {label}: SKIPPED — plan is not index-only ({plan})")
                continue
            t0 = time.monotonic()
            try:
                value = conn.execute(sql).fetchone()[0]
            except sqlite3.OperationalError as e:
                if "interrupt" in str(e).lower():
                    result["skipped"].append((label, "hard stop at budget"))
                    logger.warning(f"  {label}: hard stop after {time.monotonic() - t0:.0f} s")
                    continue
                raise
            dt = time.monotonic() - t0
            result["ran"].append((label, value, round(dt, 1)))
            logger.info(f"  {label}: {value:,} in {dt:.1f} s  [{plan}]")
    finally:
        conn.set_progress_handler(None, 0)
        conn.close()
    result["elapsed_s"] = round(time.monotonic() - t_all, 1)
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--structure-db", type=Path,
                    default=Path(os.environ.get("SWISS_CASELAW_DIR", "output")) / "decision_structure.db")
    ap.add_argument("--budget-s", type=float,
                    default=float(os.environ.get("OCL_SIDECAR_WARM_BUDGET_S", "900")))
    ap.add_argument("--strict", action="store_true",
                    help="exit 1 if any query was skipped (default: always exit 0)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logger.info(f"warming {args.structure_db} (budget {args.budget_s:.0f} s)")
    try:
        res = warm(args.structure_db, args.budget_s)
    except Exception as e:  # noqa: BLE001 — a warm-up must never fail its caller
        logger.error(f"warm-up failed (non-fatal): {type(e).__name__}: {e}")
        return 1 if args.strict else 0
    logger.info(f"done in {res['elapsed_s']} s: {len(res['ran'])} warmed, "
                f"{len(res['skipped'])} skipped")
    return 1 if (args.strict and res["skipped"]) else 0


if __name__ == "__main__":
    sys.exit(main())
