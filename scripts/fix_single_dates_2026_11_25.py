#!/usr/bin/env python3
"""Correct three decision dates the quality review of 2026-11-25 found.

1. St. Gallen Versicherungsgericht, IV 2012/450 (publikationen.sg.ch
   publication 8395). Two entscheidsuche copies, in es_sg_gerichte.jsonl and
   es_sg_publikationen.jsonl, carry 2015-12-15, copied from a typo in the
   court's own summary ("vom 15. Dezember 2015"). Each copy's own header line
   reads "St.Gallen ... 15.12.2014", which is the date of the full-text row
   (sg_versicherungsgericht, sg_publikationen.jsonl), and the Federal Supreme
   Court decided the appeal as 8C_92/2015, filed in 2015. With the date
   corrected, the build's SG overlap fold (court group + docket + date) folds
   both short copies into the full-text row, and their ids stay resolvable as
   aliases.

2. fr_gerichte 605 2025 140. The shard has no date; the build recovered
   2027-07-27 from the ruling, which prints it twice. The appeal was filed on
   2025-09-04 and the ruling scraped on 2026-09-15, so 27 July 2026 is the only
   27 July in between.

Each row gets a `date_restore` stamp (a previous one is kept under "prev").
A row is changed only if it still carries the date the review found, so a
second run changes nothing. Before rewriting a shard the script copies it to
<shard>.bak-dates-20261125. The rewrite goes through a temporary file and
os.replace, keeping the shard's permissions; run it when no scraper is
appending to the shard.

    python3 scripts/fix_single_dates_2026_11_25.py output/decisions        # dry run
    python3 scripts/fix_single_dates_2026_11_25.py output/decisions --apply
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

BACKUP_SUFFIX = ".bak-dates-20261125"
SG_PUBLICATION = "publication%5D=8395"

FIXES = [
    {
        "shard": "es_sg_gerichte.jsonl",
        "match": lambda o: (o.get("court") == "sg_gerichte"
                            and SG_PUBLICATION in (o.get("source_url") or "")),
        "old": "2015-12-15",
        "new": "2014-12-15",
        "method": "own_header_line",
        "evidence": "header 'St.Gallen Versicherungsgericht 15.12.2014'; "
                    "full-text twin sg_versicherungsgericht 2014-12-15; "
                    "appeal 8C_92/2015",
    },
    {
        "shard": "es_sg_publikationen.jsonl",
        "match": lambda o: (o.get("court") == "sg_publikationen"
                            and SG_PUBLICATION in (o.get("source_url") or "")),
        "old": "2015-12-15",
        "new": "2014-12-15",
        "method": "own_header_line",
        "evidence": "header 'St.Gallen Sonstiges 15.12.2014'; "
                    "full-text twin sg_versicherungsgericht 2014-12-15; "
                    "appeal 8C_92/2015",
    },
    {
        "shard": "fr_gerichte.jsonl",
        "match": lambda o: o.get("decision_id") == "fr_gerichte_605 2025 140",
        "old": None,
        "new": "2026-07-27",
        "method": "bounded_by_filing_and_scrape",
        "evidence": "ruling prints 27.07.2027; appeal filed 2025-09-04, "
                    "scraped 2026-09-15: the only 27 July between is 2026",
    },
]


def run(shard: Path, fixes: list[dict], apply: bool, now: str) -> dict:
    stats = {"rows": 0, "matched": 0, "changed": 0, "already_or_other_date": 0}
    fout = None
    tmp_path = None
    if apply:
        fd, tmp_path = tempfile.mkstemp(prefix=shard.name + ".", suffix=".tmp",
                                        dir=str(shard.parent))
        fout = os.fdopen(fd, "w", encoding="utf-8")
    try:
        with open(shard, encoding="utf-8") as fh:
            for line in fh:
                stats["rows"] += 1
                fix = None
                stripped = line.strip()
                if stripped:
                    try:
                        obj = json.loads(stripped)
                    except ValueError:
                        obj = None
                    if obj is not None:
                        fix = next((f for f in fixes if f["match"](obj)), None)
                if fix is None:
                    if fout:
                        fout.write(line)
                    continue
                stats["matched"] += 1
                if obj.get("decision_date") != fix["old"]:
                    stats["already_or_other_date"] += 1
                    print(f"  {obj['decision_id'][:70]}: date "
                          f"{obj.get('decision_date')!r}, expected {fix['old']!r}: kept",
                          file=sys.stderr)
                    if fout:
                        fout.write(line)
                    continue
                stats["changed"] += 1
                print(f"  {obj['decision_id'][:70]}: {fix['old']} -> {fix['new']}",
                      file=sys.stderr)
                if not fout:
                    continue
                restore = {"from": fix["old"], "to": fix["new"],
                           "method": fix["method"], "evidence": fix["evidence"],
                           "at": now}
                if obj.get("date_restore"):
                    restore["prev"] = obj["date_restore"]
                obj["date_restore"] = restore
                obj["decision_date"] = fix["new"]
                fout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    except Exception:
        if fout:
            fout.close()
            os.unlink(tmp_path)
        raise
    if fout:
        fout.close()
        if stats["changed"]:
            shutil.copymode(shard, tmp_path)
            os.replace(tmp_path, shard)
        else:
            os.unlink(tmp_path)
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("shard_dir", type=Path, help="output/decisions")
    ap.add_argument("--apply", action="store_true", help="rewrite (default: dry run)")
    args = ap.parse_args()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    by_shard: dict[str, list[dict]] = {}
    for f in FIXES:
        by_shard.setdefault(f["shard"], []).append(f)
    print(f"{'APPLY' if args.apply else 'DRY RUN'}: {args.shard_dir}", file=sys.stderr)
    total = 0
    for name, fixes in by_shard.items():
        shard = args.shard_dir / name
        if not shard.exists():
            print(f"{name}: missing", file=sys.stderr)
            return 1
        stats = run(shard, fixes, apply=False, now=now)
        if args.apply and stats["changed"]:
            backup = shard.with_name(shard.name + BACKUP_SUFFIX)
            if not backup.exists():
                shutil.copy2(shard, backup)
            stats = run(shard, fixes, apply=True, now=now)
        total += stats["changed"]
        print(f"{name}: {stats}")
    print(f"rows {'changed' if args.apply else 'to change'}: {total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
