#!/usr/bin/env python3
"""Append today's headline figures from docs/stats.json to docs/stats/history.json.

The public statistics page (docs/stats/index.html, served at /stats/) draws its
growth chart from docs/stats/history.json: one point per day with the record
count, the unique-decision estimate and the other corpus totals. stats.json only
ever holds the current day, so this script keeps the series.

Idempotent per day: a point for the same `generated_at` date replaces the
existing one, so running it twice (Step 6a early push, Step 6 final push) keeps
one point per day carrying the later values. Never raises to the caller on bad
input; exits non-zero with a message, nothing written. The file is written
atomically (.tmp then os.replace).

NOT wired into publish.py yet — see docs/proposals/stats-dashboard.md. The
series was seeded from git history on 2026-09-17.

Usage:
    python3 scripts/append_stats_history.py            # append / replace today's point
    python3 scripts/append_stats_history.py --check    # print what would be written
    python3 scripts/append_stats_history.py --stats docs/stats.json --history docs/stats/history.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCHEMA = "opencaselaw.stats_history.v1"


def point_from_stats(stats: dict) -> dict:
    """One history point from a stats.json document. Unique figures are kept only
    when the representation manifest marked them current; a stale estimate is
    recorded as null rather than as a number."""
    gen = str(stats.get("generated_at") or "")[:10]
    if len(gen) != 10:
        raise ValueError("stats.json has no usable generated_at")
    total = stats.get("total")
    if not isinstance(total, int) or total <= 0:
        raise ValueError("stats.json has no positive integer total")
    current = stats.get("unique_decisions_status") == "current"
    corpus = stats.get("corpus") or {}
    return {
        "date": gen,
        "total": total,
        "unique": stats.get("unique_decisions") if current else None,
        "duplicates": stats.get("duplicate_representations") if current else None,
        "courts": stats.get("court_count"),
        "federal_laws": corpus.get("federal_laws"),
        "cantonal_laws": corpus.get("cantonal_laws"),
        "scholarship": corpus.get("scholarship_publications"),
        "commentaries": corpus.get("commentaries"),
        "citation_edges": corpus.get("citation_edges"),
        "statute_edges": corpus.get("statute_edges"),
    }


def merge(history: dict | None, point: dict) -> dict:
    series = list((history or {}).get("series") or [])
    series = [p for p in series if p.get("date") != point["date"]]
    series.append(point)
    series.sort(key=lambda p: p["date"])
    out = dict(history or {})
    out["schema"] = SCHEMA
    out.setdefault("note", "One point per day, taken from the committed docs/stats.json of that day. null = not computed that day.")
    out["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    out["series"] = series
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--stats", default=str(REPO / "docs" / "stats.json"))
    ap.add_argument("--history", default=str(REPO / "docs" / "stats" / "history.json"))
    ap.add_argument("--check", action="store_true", help="print the point, write nothing")
    args = ap.parse_args(argv)

    try:
        stats = json.loads(Path(args.stats).read_text())
        point = point_from_stats(stats)
    except (OSError, ValueError, json.JSONDecodeError) as e:
        print(f"append_stats_history: not written: {e}", file=sys.stderr)
        return 2

    hist_path = Path(args.history)
    history = None
    if hist_path.exists():
        try:
            history = json.loads(hist_path.read_text())
        except (OSError, json.JSONDecodeError) as e:
            print(f"append_stats_history: not written: history unreadable: {e}", file=sys.stderr)
            return 2

    merged = merge(history, point)
    if args.check:
        print(json.dumps(point, ensure_ascii=False))
        print(f"series would hold {len(merged['series'])} points")
        return 0

    hist_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = hist_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(merged, ensure_ascii=False, indent=0) + "\n")
    os.replace(tmp, hist_path)
    print(f"append_stats_history: {point['date']} total={point['total']} unique={point['unique']} -> {len(merged['series'])} points")
    return 0


if __name__ == "__main__":
    sys.exit(main())
