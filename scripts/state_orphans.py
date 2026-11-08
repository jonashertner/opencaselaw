#!/usr/bin/env python3
"""
state_orphans.py — scraper state IDs that no shard holds a row for.

A scraper skips every decision_id listed in state/<court>.jsonl. When the state
names an ID whose row never reached (or was later lost from) the court's shard,
the ruling is skipped forever and is missing from the corpus. Found 2026-11-08
by the entscheidsuche parity audit: the first 35 lines of state/bstger.jsonl
(BStGer rulings of January to early March 2005, the first window of the
February 2026 backfill) have no row in output/decisions/bstger.jsonl.

Dry run by default: prints the orphans. With --apply it rewrites the state file
without them (backup next to it, atomic replace), so the next run with --since
fetches them again. Reads only the shard JSONL files, never decisions.db.

Do not --apply while that court's scraper runs: it appends to the state file.

Usage:
    python3 scripts/state_orphans.py bstger                 # report
    python3 scripts/state_orphans.py bstger --apply         # drop them (Jonas)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ID_RX = re.compile(r'"decision_id":\s*"((?:[^"\\]|\\.)*)"')


def shard_ids(paths: list[Path]) -> set[str]:
    ids: set[str] = set()
    for p in paths:
        with open(p, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                m = ID_RX.search(line)
                if m:
                    ids.add(json.loads(f'"{m.group(1)}"'))
    return ids


def state_lines(path: Path) -> list[str]:
    return [l.strip() for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def find_orphans(state: list[str], held: set[str]) -> list[str]:
    seen, out = set(), []
    for did in state:
        if did not in held and did not in seen:
            seen.add(did)
            out.append(did)
    return out


def apply(state_path: Path, orphans: list[str]) -> Path:
    drop = set(orphans)
    keep = [l for l in state_lines(state_path) if l not in drop]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = state_path.with_name(f"{state_path.name}.bak-orphans-{stamp}")
    shutil.copy2(state_path, backup)
    tmp = state_path.with_name(state_path.name + ".tmp")
    tmp.write_text("".join(l + "\n" for l in keep), encoding="utf-8")
    os.replace(tmp, state_path)
    return backup


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("court", help="scraper key, e.g. bstger")
    ap.add_argument("--state-dir", default=str(REPO / "state"))
    ap.add_argument("--shard", action="append",
                    help="shard file(s); default output/decisions/<court>.jsonl "
                         "and es_<court>.jsonl when present")
    ap.add_argument("--apply", action="store_true", help="rewrite the state file")
    ap.add_argument("--max", type=int, default=200,
                    help="refuse --apply above this many orphans (a missing shard "
                         "would make every ID an orphan)")
    args = ap.parse_args(argv)

    state_path = Path(args.state_dir) / f"{args.court}.jsonl"
    if not state_path.exists():
        print(f"no state file {state_path}", file=sys.stderr)
        return 2
    if args.shard:
        shards = [Path(s) for s in args.shard]
    else:
        base = REPO / "output" / "decisions"
        shards = [p for p in (base / f"{args.court}.jsonl", base / f"es_{args.court}.jsonl")
                  if p.exists()]
    missing = [str(p) for p in shards if not p.exists()]
    if not shards or missing:
        print(f"shard(s) missing: {missing or 'none given'}", file=sys.stderr)
        return 2

    state = state_lines(state_path)
    held = shard_ids(shards)
    orphans = find_orphans(state, held)
    print(f"{args.court}: state {len(set(state))} IDs, shards {len(held)} IDs "
          f"({', '.join(p.name for p in shards)}), orphans {len(orphans)}")
    for did in orphans:
        print(f"  {did}")
    if not args.apply or not orphans:
        return 0
    if len(orphans) > args.max:
        print(f"refusing --apply: {len(orphans)} orphans > --max {args.max}", file=sys.stderr)
        return 3
    backup = apply(state_path, orphans)
    print(f"dropped {len(orphans)} IDs from {state_path} (backup {backup.name})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
