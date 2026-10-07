#!/usr/bin/env python3
"""
repair_bge_historical_sources.py — take the other ruling's text out of the shard
rows that source_defects.py lists (historical BGE rows whose DFR document holds
another reference's ruling; runbooks/historical_bge_source_errors_2026-10-07.md).

The serving list (source_defects.py) already keeps that text away from readers.
This repair removes it from the build's input, so it also leaves decisions.db,
the dataset export, the citation graph and the structure DB:

  withhold   the row is removed from the shard. Its id stays in the scraper
             state (state/bge_historical.jsonl), so the next scrape does not
             fetch the same defective document again. Without a row the build
             has no reference; source_defects keeps answering for it.
  truncate   the row's full_text is cut to the ruling's own pages, only while it
             is the text the cut was verified on (SHA-256); cited_decisions is
             recomputed from the cut text; a `source_repair` stamp records the
             cut. A row already cut (e.g. by the segmentation repair) is left.

Run it on output/decisions/bge_historical.jsonl and on every es_*.jsonl shard
that carries one of the listed rows (the build's text-upgrade can bring an
entscheidsuche copy of a row back in; see the runbook). Rows of other courts are
never touched, whatever their docket looks like.

Streaming, temp file + atomic replace, DRY RUN by default, idempotent (a second
run changes nothing). --apply writes every removed or changed row, as it was, to
<shard>.source-repair-<UTC date>.jsonl beside the shard (undo file). Run outside
the build window (the shard is the build's input); the next full build serves
the result.

Usage:
  python3 scripts/repair_bge_historical_sources.py output/decisions/bge_historical.jsonl
  python3 scripts/repair_bge_historical_sources.py output/decisions/bge_historical.jsonl --apply
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import source_defects

COURTS = ("bge", "bge_historical")


def defect_for(obj: dict) -> source_defects.SourceDefect | None:
    """The listed defect a shard row belongs to; None for every other row."""
    if obj.get("court") not in COURTS:
        return None
    return (source_defects.lookup(obj.get("decision_id"))
            or source_defects.lookup(obj.get("docket_number")))


def decide(obj: dict) -> tuple[str, dict | None]:
    """(outcome, the row to write or None to drop it)."""
    d = defect_for(obj)
    if d is None:
        return "untouched", obj
    if d.action == "withhold":
        return f"removed:{d.kind}", None
    text = obj.get("full_text") or ""
    own = source_defects.own_text(d, text)
    if own is None:
        return "truncate:not_the_verified_text", obj
    from models import extract_citations

    out = dict(obj)
    out["full_text"] = own
    out["cited_decisions"] = extract_citations(own) if len(own) > 200 else []
    out["source_repair"] = {
        "kind": d.kind, "holds": d.holds, "chars_before": len(text),
        "sha256_before": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }
    return f"cut:{d.kind}", out


def run(path: Path, apply: bool) -> Counter:
    stats: Counter = Counter()
    now = datetime.now(timezone.utc)
    stamp = now.isoformat(timespec="seconds")
    undo_path = path.with_name(f"{path.name}.source-repair-{now.date().isoformat()}.jsonl")
    fout = undo = None
    tmp_path = None
    if apply:
        fd, tmp_path = tempfile.mkstemp(suffix=".jsonl", dir=str(path.parent))
        fout = os.fdopen(fd, "w", encoding="utf-8")
        undo = open(undo_path, "a", encoding="utf-8")  # noqa: SIM115 (closed on every path below)
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                stripped = line.strip()
                if not stripped:
                    if fout:
                        fout.write(line)
                    continue
                try:
                    obj = json.loads(stripped)
                except ValueError:
                    stats["json_error"] += 1
                    if fout:
                        fout.write(line)
                    continue
                stats["rows"] += 1
                outcome, new = decide(obj)
                stats[outcome] += 1
                if outcome != "untouched":
                    print(f"  {obj.get('decision_id')}: {outcome}", file=sys.stderr)
                if new is obj:
                    if fout:
                        fout.write(line)
                    continue
                stats["rows_changed"] += 1
                if not fout:
                    continue
                undo.write(stripped + "\n")
                if new is not None:
                    new["source_repair"]["at"] = stamp
                    fout.write(json.dumps(new, ensure_ascii=False) + "\n")
    except Exception:
        if fout:
            fout.close()
            os.unlink(tmp_path)
        if undo:
            undo.close()
        raise
    if fout:
        fout.close()
        undo.close()
        if stats["rows_changed"]:
            os.replace(tmp_path, str(path))
        else:
            os.unlink(tmp_path)
        if not undo_path.stat().st_size:
            undo_path.unlink()
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("shard", type=Path, help="output/decisions/bge_historical.jsonl or an es_*.jsonl shard")
    ap.add_argument("--apply", action="store_true", help="rewrite the shard (default: dry run)")
    args = ap.parse_args()
    print(f"{'APPLY' if args.apply else 'DRY RUN'}: {args.shard}", file=sys.stderr)
    stats = run(args.shard, args.apply)
    for k, v in sorted(stats.items()):
        print(f"{v:>10}  {k}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
