#!/usr/bin/env python3
"""Reduce the ch_vb shard to the VPB/JAAC series: drop the other portal collections, fold copies.

The entscheidsuche CH_VB spider crawled the whole amtsdruckschriften.bar.admin.ch portal,
so es_ch_vb.jsonl mixes the VPB/JAAC with Bundesblatt, Amtliches Bulletin, Amtliche
Sammlung and smaller collections. The portal document id in viewOrigDoc/<id>.pdf names
the collection by its leading digits; 15 is VPB/JAAC (runbooks/ch_vb_amtsdruckschriften_
contamination.md §1a). Census 2026-10-27: 23,055 rows, 2,632 with prefix 15, over 2,502
distinct PDFs, which are every prefix-15 PDF in the raw feed (8,737 feed entries, each PDF
listed once per language header). Vols. 51-70 (1987-2006) hold every number except 70.32;
2007-2017 every number except 2014.3.

A row is kept only if its portal id starts with 15. Rows sharing a portal id are copies of
one PDF under junk dockets (raw <td> cells, the bare id, text fragments); one row per PDF
is kept, preferring a real "VPB/JAAC x.y" docket, then the bare portal id, then the
longest text. Dry run by default; --apply keeps a backup next to the shard and replaces
the shard atomically. Run it outside the scrape and publish steps (the 01:00 UTC scrape,
the 03:30 UTC build, the 20:00 UTC incremental), and give the next full build
OCL_SWAP_GATE_EXEMPT=ch_vb: the court shrinks by ~89 % on purpose.

  python3 scripts/ch_vb_keep_vpb.py            # count what would be kept and dropped
  python3 scripts/ch_vb_keep_vpb.py --apply    # write it
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
DEFAULT_SHARD = REPO / "output" / "decisions" / "es_ch_vb.jsonl"
BACKUP_SUFFIX = ".bak-pre-vpb-only"
VPB_PREFIX = "15"

_PORTAL_ID = re.compile(r"viewOrigDoc/(\d+)")
_VPB_DOCKET = re.compile(r"^\s*(?:VPB|JAAC|GAAC)\s+(?:\d{2}|(?:19|20)\d{2})\.\d+", re.I)


def portal_id(rec: dict) -> str:
    for key in ("source_url", "pdf_url"):
        m = _PORTAL_ID.search(rec.get(key) or "")
        if m:
            return m.group(1)
    return ""


def _rank(rec: dict, pid: str) -> tuple:
    docket = str(rec.get("docket_number") or "")
    return (
        bool(_VPB_DOCKET.match(docket)),
        docket.strip() == pid,
        len(rec.get("full_text") or ""),
    )


def plan(records: list[dict]) -> tuple[list[int], Counter, int]:
    """(indices to keep in shard order, dropped rows by portal prefix, folded copies)."""
    dropped: Counter = Counter()
    best: dict[str, int] = {}
    for i, r in enumerate(records):
        pid = portal_id(r)
        if not pid.startswith(VPB_PREFIX):
            dropped[pid[:2] or "none"] += 1
            continue
        j = best.get(pid)
        if j is None or _rank(r, pid) > _rank(records[j], pid):
            best[pid] = i
    vpb_rows = sum(1 for r in records if portal_id(r).startswith(VPB_PREFIX))
    return sorted(best.values()), dropped, vpb_rows - len(best)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--shard", default=str(DEFAULT_SHARD))
    ap.add_argument("--apply", action="store_true", help="write the filtered shard (default: dry run)")
    args = ap.parse_args(argv)

    shard = Path(args.shard)
    # Iterate the file, not str.splitlines(): the OCR text carries U+2028 / U+0085,
    # which splitlines() treats as line ends inside a JSON string.
    with open(shard, encoding="utf-8", newline="") as f:
        lines = [l for l in f if l.strip()]
    records = [json.loads(l) for l in lines]
    keep, dropped, folded = plan(records)
    for prefix, n in sorted(dropped.items(), key=lambda kv: -kv[1]):
        print(f"drop    portal prefix {prefix:4s} {n:6d} rows")
    print(f"fold    {folded} copies of a VPB PDF already kept")
    real = sum(1 for i in keep if _VPB_DOCKET.match(str(records[i].get("docket_number") or "")))
    print(f"rows {len(lines)} -> {len(keep)} VPB/JAAC PDFs ({real} with a VPB/JAAC x.y docket)")
    if not args.apply:
        print("dry run: nothing written")
        return 0
    if len(keep) == len(lines):
        print("nothing to drop")
        return 0
    backup = shard.with_name(shard.name + BACKUP_SUFFIX)
    if not backup.exists():
        shutil.copy2(shard, backup)
    tmp = shard.with_name(shard.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.writelines(lines[i] for i in keep)
    os.replace(tmp, shard)
    print(f"applied: {shard} now {len(keep)} rows; backup {backup}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
