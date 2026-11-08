#!/usr/bin/env python3
"""Build state/bger.held.tsv: every BGer ruling the corpus holds, from ALL
bger shards, as "<normalised docket>\t<YYYY-MM-DD>" lines.

Input for `run_scraper.py bger --reconcile` (see "Count reconciliation" in
scrapers/bger.py). Scraper state knows only the rulings the scraper fetched
itself (~100k of ~194k); the entscheidsuche rows (es_bger.jsonl) and the EVG
recovery (bger_evg.jsonl) are not in it. Without this index a completeness
sweep would refetch every one of them.

Reads the shards line by line, pulling only docket_number and decision_date
(regex on the line, json fallback), so it never holds a shard in memory.
Writes atomically (tmp + os.replace). Run it OUTSIDE the publish build window
(CLAUDE.md invariant 9), at idle priority:

    nice -n 19 ionice -c 3 python3 scripts/build_bger_held_index.py

Default shards: output/decisions/{bger,bger_evg,es_bger}.jsonl (missing ones
are skipped with a note).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

DEFAULT_SHARDS = ("bger.jsonl", "bger_evg.jsonl", "es_bger.jsonl")
# Courts a bger shard may carry. Anything else is counted and skipped.
BGER_COURTS = {"bger", "", None}

_STR = r'"((?:[^"\\]|\\.)*)"'
_RX = {k: re.compile(rf'"{k}":\s*(?:{_STR}|null)') for k in ("docket_number", "decision_date", "court")}
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def norm_docket(docket: str | None) -> str:
    """Same key as BgerScraper._norm_docket: 4P.253/1999 → 4P2531999."""
    return re.sub(r"[^0-9A-Z]", "", (docket or "").upper())


def _field(line: str, key: str):
    m = _RX[key].search(line)
    if not m:
        return False                    # key absent → caller falls back to json
    raw = m.group(1)
    if raw is None:
        return None                     # explicit null
    return json.loads(f'"{raw}"')


def row_fields(line: str) -> tuple[str | None, str | None, str | None]:
    docket, d, court = (_field(line, k) for k in ("docket_number", "decision_date", "court"))
    if docket is False or d is False:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            return None, None, None
        return row.get("docket_number"), row.get("decision_date"), row.get("court")
    return docket, d, (None if court is False else court)


def build(shards: list[Path]) -> tuple[set[tuple[str, str]], dict]:
    pairs: set[tuple[str, str]] = set()
    report: dict = {}
    for shard in shards:
        c = Counter()
        if not shard.exists():
            report[str(shard)] = {"missing": True}
            continue
        with open(shard, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if not line.strip():
                    continue
                c["rows"] += 1
                docket, d, court = row_fields(line)
                if court not in BGER_COURTS:
                    c["other_court"] += 1
                    continue
                key = norm_docket(docket)
                d = (d or "")[:10]
                if not key or not _DATE.match(d):
                    c["no_docket_or_date"] += 1
                    continue
                if (key, d) in pairs:
                    c["already_held"] += 1
                    continue
                pairs.add((key, d))
                c["added"] += 1
        report[str(shard)] = dict(c)
    return pairs, report


def write_atomic(pairs: set[tuple[str, str]], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        for key, d in sorted(pairs, key=lambda p: (p[1], p[0])):
            fh.write(f"{key}\t{d}\n")
    os.replace(tmp, out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--decisions-dir", default="output/decisions")
    ap.add_argument("--shard", action="append", default=None,
                    help="shard path (repeatable); default: bger, bger_evg, es_bger")
    ap.add_argument("--out", default="state/bger.held.tsv")
    args = ap.parse_args(argv)

    shards = ([Path(s) for s in args.shard] if args.shard
              else [Path(args.decisions_dir) / n for n in DEFAULT_SHARDS])
    pairs, report = build(shards)
    if not pairs:
        print("no rulings found — refusing to write an empty index", file=sys.stderr)
        return 1
    write_atomic(pairs, Path(args.out))
    years = Counter(d[:4] for _, d in pairs)
    print(json.dumps({"out": args.out, "rulings": len(pairs), "shards": report,
                      "by_year": dict(sorted(years.items()))}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
