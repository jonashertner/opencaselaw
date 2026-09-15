#!/usr/bin/env python3
"""Remove zh_baurekursgericht rows that republish a decision the corpus already holds.

On 2026-09-15 (1ff35f07) the scraper ingested 30 docket-less "Zwischenentscheid" PDFs.
20 of them are BEZ republications or second excerpts of decisions held under their BRGE
number; their caption names it ("BRGE II Nrn. 0053/2022 - 0054/2022 vom 15. März 2022 in
BEZ 2023 Nr. 13"), and one is textually identical to the held row. The scraper now skips
such PDFs in fetch_decision; this script removes the rows that were already written.

A row is removed only if all three hold:
  * its id is a docket-less stem id (zh_baurekursgericht_Zwischenentscheid ...),
  * its caption cites a (series, chamber, year, number) held under a numbered docket,
  * one of those held rows has the same decision_date.
A row whose cited number is held under a different date is listed as refused and kept.

state/zh_baurekursgericht.jsonl is not touched, so the nightly scrape keeps the removed ids
known and does not fetch them again. Dry run by default; --apply keeps a backup next to the
shard and replaces the shard atomically. Run it outside the scrape and publish steps
(the 01:00 UTC scrape, the 03:30 UTC build, the 20:00 UTC incremental).

  python3 scripts/zh_brg_remove_republications.py            # list what would be removed
  python3 scripts/zh_brg_remove_republications.py --apply    # remove it
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scrapers.cantonal.zh_baurekursgericht import _brge_keys  # noqa: E402

DEFAULT_SHARD = REPO / "output" / "decisions" / "zh_baurekursgericht.jsonl"
STEM_PREFIX = "zh_baurekursgericht_Zwischenentscheid "
BACKUP_SUFFIX = ".bak-republications"


def plan(records: list[dict]) -> tuple[list[tuple[str, str, str]], list[tuple[str, list[str]]]]:
    """(removals as (id, date, held docket), refusals as (id, held dates))."""
    index: dict[tuple, list[dict]] = {}
    for r in records:
        if r["decision_id"].startswith(STEM_PREFIX):
            continue
        for key in _brge_keys(r.get("docket_number") or ""):
            index.setdefault(key, []).append(r)
    removals, refusals = [], []
    for r in records:
        if not r["decision_id"].startswith(STEM_PREFIX):
            continue
        cited = {
            h["decision_id"]: h
            for key in _brge_keys((r.get("full_text") or "")[:400])
            for h in index.get(key, [])
        }
        if not cited:
            continue
        same_date = [h for h in cited.values() if str(h.get("decision_date")) == str(r.get("decision_date"))]
        if same_date:
            removals.append((r["decision_id"], str(r.get("decision_date")), same_date[0].get("docket_number") or ""))
        else:
            refusals.append((r["decision_id"], sorted(str(h.get("decision_date")) for h in cited.values())))
    return removals, refusals


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--shard", default=str(DEFAULT_SHARD))
    ap.add_argument("--apply", action="store_true", help="write the filtered shard (default: dry run)")
    args = ap.parse_args(argv)

    shard = Path(args.shard)
    lines = [l for l in shard.read_text(encoding="utf-8").splitlines(keepends=True) if l.strip()]
    records = [json.loads(l) for l in lines]
    removals, refusals = plan(records)
    for rid, d, held in removals:
        print(f"remove  {d}  {rid[len(STEM_PREFIX):][:48]:48s} = {held}")
    for rid, held_dates in refusals:
        print(f"KEEP    {rid[len(STEM_PREFIX):][:48]:48s} cited number held under dates {held_dates}")
    drop = {rid for rid, _, _ in removals}
    kept = [l for l, r in zip(lines, records) if r["decision_id"] not in drop]
    print(f"rows {len(lines)} -> {len(kept)}; remove {len(drop)}; refused by the date guard {len(refusals)}")
    if not args.apply:
        print("dry run: nothing written")
        return 0
    if not drop:
        print("nothing to remove")
        return 0
    backup = shard.with_name(shard.name + BACKUP_SUFFIX)
    if not backup.exists():
        shutil.copy2(shard, backup)
    tmp = shard.with_name(shard.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.writelines(kept)
    os.replace(tmp, shard)
    print(f"applied: {shard} now {len(kept)} rows; backup {backup}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
