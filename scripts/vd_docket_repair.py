#!/usr/bin/env python3
"""Give held vd_gerichte rows the docket the scraper now writes for new ones.

Background (runbooks/vd_docket_repair_2026-10.md): since 2026-09-04 the listing
of prestations.vd.ch carries no case number, so rows scraped since then (and the
09-04 backfill) hold the per-year ruling number ("344", "111/I", "47bis") or the
pdf uuid as docket_number and cite as "Gericht VD 344 vom 19. April 2021". The
scraper now resolves the docket from the PDF header, else "CREP 2021/344"
(VDGerichteScraper._resolve_docket); this script applies the same rule to the
rows already in the shard, reading the header from the stored full_text.

Only docket_number and docket_number_2 change. docket_number_2 keeps the old
value, and build_fts5.DECISION_NUMBER_COURTS keys vd_gerichte on it, so every
row keeps exactly its canonical key: rewriting dockets alone would let the
build's dedup Pass 1 delete 318 distinct rulings (one case number carries
several rulings). The script refuses to write unless vd_gerichte is in
DECISION_NUMBER_COURTS and, for every line, decision_id, full_text, scraped_at
and the dedup key are unchanged, and the line count is the same.

Streaming, DRY RUN by default. --apply hard-links <shard>.bak-<UTC date>
beside the shard, writes <shard>.tmp (neither matches the build's *.jsonl
glob) and swaps it in atomically. Run it outside the build window, with no
scrape appending to the shard (see the runbook).

Usage:
  python3 scripts/vd_docket_repair.py
  python3 scripts/vd_docket_repair.py --shard output/decisions/vd_gerichte.jsonl --apply
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from build_fts5 import DECISION_NUMBER_COURTS, _dedup_docket  # noqa: E402
from models import make_canonical_key  # noqa: E402
from scrapers.cantonal.vd_gerichte import (  # noqa: E402
    RULING_NO_RE,
    VDGerichteScraper,
    uuid_from_pdf_url,
)

COURT = "vd_gerichte"
DEFAULT_SHARD = REPO / "output" / "decisions" / "vd_gerichte.jsonl"
UNCHANGED = ("decision_id", "full_text", "scraped_at", "decision_date", "court")


def dedup_key(rec: dict) -> str:
    return make_canonical_key(rec.get("court") or "", _dedup_docket(rec), rec.get("decision_date"))


def repair(rec: dict) -> tuple[dict | None, str]:
    """(the repaired record or None, the outcome class) for one shard record."""
    if rec.get("court") != COURT:
        return None, "other_court"
    if (rec.get("docket_number_2") or "").strip():
        return None, "has_docket_2"
    docket = str(rec.get("docket_number") or "").strip()
    uuid = uuid_from_pdf_url(rec.get("pdf_url") or rec.get("source_url") or rec.get("url"))
    if not docket or not (RULING_NO_RE.match(docket) or docket == uuid):
        return None, "real_docket"
    stub = {"ruling_no": docket, "docket_number": docket, "uuid": uuid,
            "autorite": rec.get("chamber")}
    new_docket, docket_2 = VDGerichteScraper._resolve_docket(
        stub, rec.get("full_text") or "", rec.get("decision_date"))
    out = dict(rec)
    out["docket_number"] = new_docket
    out["docket_number_2"] = docket_2
    if new_docket == docket:
        kind = "stays_uuid" if docket == uuid else "stays_number"
    elif new_docket.startswith(f"{(rec.get('chamber') or '').strip()} ") and "/" in new_docket:
        kind = "chamber_year_number"
    else:
        kind = "header"
    return out, kind


def check(before: dict, after: dict) -> None:
    for k in UNCHANGED:
        if before.get(k) != after.get(k):
            raise SystemExit(f"refusing: {before.get('decision_id')} would change {k}")
    if dedup_key(before) != dedup_key(after):
        raise SystemExit(f"refusing: {before.get('decision_id')} would change its dedup key "
                         f"{dedup_key(before)!r} -> {dedup_key(after)!r}")


def run(shard: Path, apply: bool, examples: int = 0) -> Counter:
    if COURT not in DECISION_NUMBER_COURTS:
        raise SystemExit(f"refusing: {COURT} is not in build_fts5.DECISION_NUMBER_COURTS; "
                         "rewritten dockets would let the build's dedup delete rulings")
    stats: Counter = Counter()
    shown = 0
    tmp = shard.with_name(shard.name + ".tmp")
    fout = open(tmp, "wb") if apply else None
    try:
        with open(shard, "rb") as fh:
            for raw in fh:
                stats["lines"] += 1
                line = raw.rstrip(b"\r\n")
                try:
                    rec = json.loads(line) if line.strip() else None
                except ValueError:
                    rec = None
                if not isinstance(rec, dict):
                    stats["unparsed"] += 1
                    if fout:
                        fout.write(raw)
                    continue
                new, kind = repair(rec)
                stats[f"outcome:{kind}"] += 1
                if new is None or new == rec:
                    if fout:
                        fout.write(raw)
                    continue
                check(rec, new)
                stats["rows_changed"] += 1
                if shown < examples:
                    shown += 1
                    print(f"  {rec['decision_id']}  {rec.get('docket_number')!r} -> "
                          f"{new['docket_number']!r}  [{kind}]", file=sys.stderr)
                if fout:
                    fout.write(json.dumps(new, ensure_ascii=False).encode("utf-8") + b"\n")
        if fout:
            fout.flush()
            os.fsync(fout.fileno())
            fout.close()
            fout = None
            with open(tmp, "rb") as fh:
                written = sum(1 for _ in fh)
            if written != stats["lines"]:
                raise SystemExit(f"refusing: wrote {written} lines, read {stats['lines']}")
            stamp = time.strftime("%Y%m%d", time.gmtime())
            backup = shard.with_name(f"{shard.name}.bak-{stamp}")
            if backup.exists():
                raise SystemExit(f"refusing to overwrite existing backup {backup}")
            os.link(shard, backup)  # zero-cost, same inode
            os.chmod(tmp, os.stat(shard).st_mode & 0o7777)
            os.replace(tmp, shard)
            print(f"rewrote {shard}; backup {backup}", file=sys.stderr)
    finally:
        if fout:
            fout.close()
        if tmp.exists():
            tmp.unlink()
    return stats


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--shard", type=Path, default=DEFAULT_SHARD)
    ap.add_argument("--apply", action="store_true", help="write (default: dry run)")
    ap.add_argument("--examples", type=int, default=0, help="print this many changed rows")
    args = ap.parse_args(argv)
    if not args.shard.exists():
        print(f"shard not found: {args.shard}", file=sys.stderr)
        return 2
    print(("APPLY" if args.apply else "DRY RUN") + f": {args.shard}", file=sys.stderr)
    stats = run(args.shard, args.apply, args.examples)
    print(json.dumps(dict(sorted(stats.items())), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
