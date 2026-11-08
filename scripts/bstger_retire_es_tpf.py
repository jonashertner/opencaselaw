#!/usr/bin/env python3
"""Drop the entscheidsuche copies of TPF entries the direct bstger scraper now holds.

All 583 TPF rows of es_bstger.jsonl (2004-2021) came from entscheidsuche. Once the
direct scraper has written the whole collection to bstger.jsonl (scrapers/bstger.py,
`OCL_BSTGER_TPF_ONLY=1 run_scraper.py bstger --since 2004-01-01`), each es row has a
direct row under the same id (bstger_TPF_2004_12). The build keeps the direct row,
but where the dates differ (339 of 583: 244 es rows carry a 1 January placeholder) it
re-ids the es copy as <id>_dYYYYMMDD, and the federation fold matches only 183 of
those: the es text spans the neighbouring entries and was extracted by another PDF
library, so 156 rulings would be served twice. Dry run 2026-11-08.

A row is dropped only if it is a TPF entry ("TPF 2004 12", also the malformed
"TPF201126") whose reference the direct shard holds. Nothing else is touched. If any
es TPF row has no direct counterpart the script refuses to write, so no entry can be
lost. Dry run by default; --apply keeps a backup next to the shard and replaces it
atomically. Run it after the backfill and outside the scrape and publish steps.

  python3 scripts/bstger_retire_es_tpf.py            # count what would be dropped
  python3 scripts/bstger_retire_es_tpf.py --apply    # write it
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_DIR = REPO / "output" / "decisions"
BACKUP_SUFFIX = ".bak-pre-tpf-direct"

# "TPF 2004 12", "TPF201126" (year 4 digits, then the page)
_TPF_KEY = re.compile(r"^TPF(\d{4})(\d{1,4})$")


def tpf_key(docket: str | None) -> str | None:
    """'TPF 2004 12' / 'TPF201126' → 'TPF 2004 12' / 'TPF 2011 26'; None otherwise."""
    m = _TPF_KEY.match(re.sub(r"[^A-Z0-9]", "", str(docket or "").upper()))
    return f"TPF {m.group(1)} {int(m.group(2))}" if m else None


def _read(path: Path) -> list[str]:
    # Iterate the file, not str.splitlines(): texts may carry U+2028 / U+0085.
    with open(path, encoding="utf-8", newline="") as f:
        return [l for l in f if l.strip()]


def plan(direct_lines: list[str], es_lines: list[str]) -> tuple[list[int], Counter, list[str]]:
    """(es indices to keep, dropped rows by volume year, es TPF references with no
    direct row)."""
    held = set()
    for line in direct_lines:
        rec = json.loads(line)
        if rec.get("court") == "bstger":
            key = tpf_key(rec.get("docket_number"))
            if key:
                held.add(key)
    keep, dropped, uncovered = [], Counter(), []
    for i, line in enumerate(es_lines):
        key = tpf_key(json.loads(line).get("docket_number"))
        if key is None:
            keep.append(i)
        elif key in held:
            dropped[key.split()[1]] += 1
        else:
            keep.append(i)
            uncovered.append(key)
    return keep, dropped, uncovered


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", default=str(DEFAULT_DIR), help="the decisions shard directory")
    ap.add_argument("--apply", action="store_true", help="write the filtered shard (default: dry run)")
    args = ap.parse_args(argv)

    d = Path(args.dir)
    es = d / "es_bstger.jsonl"
    es_lines = _read(es)
    keep, dropped, uncovered = plan(_read(d / "bstger.jsonl"), es_lines)
    for year, n in sorted(dropped.items()):
        print(f"drop    TPF {year}: {n}")
    print(f"rows {len(es_lines)} -> {len(keep)} ({sum(dropped.values())} TPF copies dropped)")
    if uncovered:
        print(f"REFUSING: {len(uncovered)} es TPF rows have no direct row: {sorted(set(uncovered))[:20]}")
        return 1
    if not args.apply:
        print("dry run: nothing written")
        return 0
    if len(keep) == len(es_lines):
        print("nothing to drop")
        return 0
    backup = es.with_name(es.name + BACKUP_SUFFIX)
    if not backup.exists():
        shutil.copy2(es, backup)
    tmp = es.with_name(es.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.writelines(es_lines[i] for i in keep)
    os.replace(tmp, es)
    print(f"applied: {es} now {len(keep)} rows; backup {backup}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
