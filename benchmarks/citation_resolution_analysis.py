#!/usr/bin/env python3
"""
Empirical decomposition of unresolved citations in OpenCaseLaw's reference graph.

Reproduces the §4 analysis in the OpenCaseLaw paper (docs/paper/v3/paper.tex).
Reports a four-bucket breakdown of why 24.8 % of raw citation mentions fail to
resolve under the deployed exact-match resolver:

  1. docket-normalization drift   (BGer 2007-2009 space-vs-underscore variants)
  2. pin-cite failures            (e.g. BGE 125 V 352 pinpoints into 125 V 351)
  3. genuinely outside corpus     (cantonal / lower-court refs not yet covered)
  4. residual BGE-not-in-corpus + malformed-extraction artefacts

Run against `output/reference_graph.db`; takes ~30 s.

Usage::

    python3 -m benchmarks.citation_resolution_analysis [--db PATH]

Prints a table like::

    bucket                     mentions   %unres   %total_raw
    docketnorm_BGer             978,413   44.58%       11.32%
    pincite_BGE                 602,803   27.47%        6.97%
    docket_other                553,064   25.20%        6.40%
    BGE_no_match                 59,163    2.70%        0.68%
    other                         1,097    0.05%        0.01%
"""
from __future__ import annotations

import argparse
import os
import re
import sqlite3
from typing import Iterable

DEFAULT_DB = os.environ.get(
    "SWISS_CASELAW_GRAPH_DB",
    "output/reference_graph.db",
)
PIN_DISTANCE = 30  # max page-offset for pin-cite lookup

_RE_BGE_FORM = re.compile(r"^(?:BGE\s+)?(\d{2,4})\s+([IVX]+)\s+(\d{1,4})$")
_RE_BGER_FORM = re.compile(r"^[1-9][A-Z]_\d+_\d{4}$")
_RE_DECISION_BGE = re.compile(r"(\d{2,4})[\s_]+([IVX]+)[\s_]+(\d{1,4})")


def _build_bge_index(cur: sqlite3.Cursor) -> tuple[set, dict]:
    """Index BGE first pages by (vol, div) -> sorted list of pages."""
    keys: set = set()
    by_voldiv: dict[tuple[int, str], list[int]] = {}
    cur.execute(
        "SELECT decision_id FROM decisions WHERE court IN ('bge','bger') "
        "OR decision_id LIKE 'bge%'"
    )
    for (did,) in cur.fetchall():
        m = _RE_DECISION_BGE.search(did)
        if m:
            vol, div, page = int(m.group(1)), m.group(2), int(m.group(3))
            keys.add((vol, div, page))
            by_voldiv.setdefault((vol, div), []).append(page)
    for k in by_voldiv:
        by_voldiv[k].sort()
    return keys, by_voldiv


def _build_bger_space_norms(cur: sqlite3.Cursor) -> set:
    """BGer docket_norm rows that contain a space, converted to underscore form."""
    cur.execute(
        "SELECT docket_norm FROM decisions "
        "WHERE court='bger' AND docket_norm LIKE '% %'"
    )
    return {dn.replace(" ", "_") for (dn,) in cur.fetchall()}


def analyse(db_path: str = DEFAULT_DB) -> dict:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM decision_citations")
    total_raw = int(cur.fetchone()[0])
    cur.execute("SELECT COUNT(*) FROM citation_targets")
    n_resolved = int(cur.fetchone()[0])

    bge_keys, bge_by_voldiv = _build_bge_index(cur)
    bger_space = _build_bger_space_norms(cur)

    cur.execute(
        """
        SELECT dc.target_ref, dc.target_type, 1
        FROM decision_citations dc
        LEFT JOIN citation_targets ct
          ON dc.source_decision_id = ct.source_decision_id
         AND dc.target_ref = ct.target_ref
        WHERE ct.target_decision_id IS NULL
        """
    )

    buckets = {
        "docketnorm_BGer": 0,
        "pincite_BGE": 0,
        "BGE_no_match": 0,
        "docket_other": 0,
        "other": 0,
    }
    for ref, ttype, m in cur.fetchall():
        bge = _RE_BGE_FORM.match(ref)
        if bge:
            vol, div, page = int(bge.group(1)), bge.group(2), int(bge.group(3))
            if (vol, div, page) in bge_keys:
                buckets["BGE_no_match"] += m
                continue
            pages = bge_by_voldiv.get((vol, div), [])
            best = None
            for p in pages:
                if p <= page and (page - p) <= PIN_DISTANCE:
                    best = p
                if p > page:
                    break
            if best is not None:
                buckets["pincite_BGE"] += m
            else:
                buckets["BGE_no_match"] += m
            continue
        if _RE_BGER_FORM.match(ref):
            if ref in bger_space:
                buckets["docketnorm_BGer"] += m
            else:
                buckets["docket_other"] += m
            continue
        if ttype in ("docket", "bger"):
            buckets["docket_other"] += m
        else:
            buckets["other"] += m

    total_unres = sum(buckets.values())
    recoverable = buckets["pincite_BGE"] + buckets["docketnorm_BGer"]
    return {
        "total_raw": total_raw,
        "n_resolved": n_resolved,
        "total_unresolved": total_unres,
        "buckets": buckets,
        "recoverable": recoverable,
        "uplift_pct": 100.0 * (n_resolved + recoverable) / total_raw,
    }


def _print_report(r: dict) -> None:
    total_raw = r["total_raw"]
    total_unres = r["total_unresolved"]
    print(f"raw mentions:        {total_raw:>14,}")
    print(f"resolved mentions:   {r['n_resolved']:>14,}  ({100*r['n_resolved']/total_raw:.2f} %)")
    print(f"unresolved mentions: {total_unres:>14,}  ({100*total_unres/total_raw:.2f} %)")
    print()
    print(f"{'bucket':<20} {'mentions':>14} {'%unres':>8} {'%total_raw':>12}")
    for name, n in sorted(r["buckets"].items(), key=lambda x: -x[1]):
        pu = 100.0 * n / total_unres
        pt = 100.0 * n / total_raw
        print(f"{name:<20} {n:>14,} {pu:>7.2f}% {pt:>11.2f}%")
    print()
    print(
        f"recoverable (pincite + docketnorm): {r['recoverable']:,} "
        f"-> projected resolution {r['uplift_pct']:.2f} %"
    )


def main(argv: Iterable[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=DEFAULT_DB, help=f"path to reference_graph.db (default: {DEFAULT_DB})")
    args = ap.parse_args(argv)
    if not os.path.exists(args.db):
        raise SystemExit(f"DB not found: {args.db}")
    _print_report(analyse(args.db))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
