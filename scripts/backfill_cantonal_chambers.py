#!/usr/bin/env python3
"""Fill the empty `chamber` of held cantonal rows from the court's own text.

Applies chamber_rules (the same rules the scrapers now use for new rows) to the
rows of one shard: the Cour / Abteilung / Kammer the ruling's own header names,
else the court's docket or file-name series where that maps one-to-one onto a
chamber. See chamber_rules' module docstring and
runbooks/cantonal_chambers_2026-11.md.

Changes only `chamber`, and only

  * where it is empty, or
  * where it names only the court the derived chamber belongs to
    ("Kantonsgericht" -> "Kantonsgericht, Abteilung Strafrecht"; ar_gerichte
    "Obergericht" -> "Obergericht, 3. Abteilung") — chamber_rules.refine.

Never changes another field, never drops or adds a row. A row whose header and
docket name different chambers is left as it is and listed in the report
(`conflicts`): on every such row checked the stored text belonged to another
ruling.

DRY RUN by default. --apply streams the shard into <shard>.tmp (which the
build's *.jsonl glob does not match), checks the line count and that the shard
did not change while it ran, hard-links <shard>.bak-chamber-<UTC date> and
swaps the new file in with os.replace. Run it outside the build window with no
scrape of that court running.

Usage:
  python3 scripts/backfill_cantonal_chambers.py --shard output/decisions/fr_gerichte.jsonl
  python3 scripts/backfill_cantonal_chambers.py --shard ... --report /tmp/fr.json
  python3 scripts/backfill_cantonal_chambers.py --shard ... --apply
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import chamber_rules  # noqa: E402

MAX_REPORT_ROWS = 2000  # per list in the report


def _filled(v) -> bool:
    return bool((v or "").strip()) if isinstance(v, str) else bool(v)


def run(shard: Path, courts: set[str], apply: bool, report: Path | None,
        examples: int) -> dict:
    st = os.stat(shard)
    tmp = shard.with_name(shard.name + ".tmp")
    out = open(tmp, "wb") if apply else None
    stats: Counter = Counter()
    per: dict[str, Counter] = defaultdict(Counter)
    values: dict[str, Counter] = defaultdict(Counter)
    conflicts: list[dict] = []
    changes: list[dict] = []
    shown = 0
    try:
        with open(shard, "rb") as fh:
            for raw in fh:
                stats["lines"] += 1
                line = raw.rstrip(b"\r\n")
                rec = None
                if line.strip():
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        rec = None
                court = rec.get("court") if isinstance(rec, dict) else None
                if court not in courts:
                    stats["passed_through"] += 1
                    if out:
                        out.write(raw)
                    continue
                c = per[court]
                c["rows"] += 1
                old = rec.get("chamber")
                if _filled(old):
                    c["filled_before"] += 1
                r = chamber_rules.derive(
                    court, docket=rec.get("docket_number"), text=rec.get("full_text"),
                    language=rec.get("language"), source_url=rec.get("source_url"))
                new = chamber_rules.refine(old, r.chamber)
                if r.how == "conflict":
                    c["conflict"] += 1
                    if len(conflicts) < MAX_REPORT_ROWS:
                        conflicts.append({
                            "decision_id": rec.get("decision_id"), "court": court,
                            "docket_number": rec.get("docket_number"),
                            "header_says": r.header_key, "docket_says": r.docket_key,
                            "source_url": rec.get("source_url")})
                if new == old or (not _filled(new) and not _filled(old)):
                    if _filled(old):
                        c["filled_after"] += 1
                    if out:
                        out.write(raw)
                    continue
                kind = "refined" if _filled(old) else "filled"
                c[kind] += 1
                c[f"{kind}_by:{r.how}"] += 1
                c["filled_after"] += 1
                values[court][new] += 1
                if len(changes) < MAX_REPORT_ROWS:
                    changes.append({"decision_id": rec.get("decision_id"), "court": court,
                                    "docket_number": rec.get("docket_number"),
                                    "chamber_before": old, "chamber_after": new, "how": r.how})
                if shown < examples:
                    shown += 1
                    print(f"  {rec.get('decision_id')}: {old!r} -> {new!r} ({r.how})", file=sys.stderr)
                rec["chamber"] = new
                if out:
                    out.write(json.dumps(rec, ensure_ascii=False).encode("utf-8") + b"\n")
        if out:
            out.flush()
            os.fsync(out.fileno())
            out.close()
            out = None
            with open(tmp, "rb") as fh:
                written = sum(1 for _ in fh)
            if written != stats["lines"]:
                raise SystemExit(f"refusing: wrote {written} lines, read {stats['lines']}")
            now = os.stat(shard)
            if (now.st_size, now.st_mtime_ns) != (st.st_size, st.st_mtime_ns):
                raise SystemExit("refusing: the shard changed while the backfill ran (a scrape?)")
            if not any(per[c]["filled"] + per[c]["refined"] for c in per):
                print("nothing to change; shard left as it is", file=sys.stderr)
            else:
                stamp = time.strftime("%Y%m%d", time.gmtime())
                backup = shard.with_name(f"{shard.name}.bak-chamber-{stamp}")
                if backup.exists():
                    raise SystemExit(f"refusing to overwrite existing backup {backup}")
                os.link(shard, backup)
                os.chmod(tmp, st.st_mode & 0o7777)
                os.replace(tmp, shard)
                print(f"rewrote {shard}; backup {backup}", file=sys.stderr)
    finally:
        if out:
            out.close()
        if tmp.exists():
            tmp.unlink()

    summary = {}
    for court, c in sorted(per.items()):
        n = c["rows"]
        summary[court] = {
            "rows": n,
            "filled_before": c["filled_before"],
            "filled_after": c["filled_after"],
            "fill_rate_before": round(c["filled_before"] / n, 4) if n else None,
            "fill_rate_after": round(c["filled_after"] / n, 4) if n else None,
            "filled": c["filled"], "refined": c["refined"], "conflict": c["conflict"],
            "by": {k.split(":", 1)[1] + ("" if k.startswith("filled") else " (refined)"): v
                   for k, v in sorted(c.items()) if ":" in k},
            "values": dict(values[court].most_common()),
        }
    result = {"shard": str(shard), "apply": apply, "stats": dict(stats), "courts": summary}
    if report:
        report.write_text(json.dumps({**result, "conflicts": conflicts, "changes": changes},
                                     ensure_ascii=False, indent=1))
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--shard", type=Path, required=True)
    ap.add_argument("--court", action="append",
                    help=f"limit to these courts (default: all of {sorted(chamber_rules.RULES)})")
    ap.add_argument("--apply", action="store_true", help="write (default: dry run)")
    ap.add_argument("--report", type=Path, help="write the summary, conflicts and changed rows here")
    ap.add_argument("--examples", type=int, default=0)
    args = ap.parse_args(argv)
    if not args.shard.exists():
        print(f"shard not found: {args.shard}", file=sys.stderr)
        return 2
    courts = set(args.court or chamber_rules.RULES)
    unknown = courts - set(chamber_rules.RULES)
    if unknown:
        ap.error(f"no chamber rule for {sorted(unknown)}")
    print(("APPLY" if args.apply else "DRY RUN") + f": {args.shard}", file=sys.stderr)
    result = run(args.shard, courts, args.apply, args.report, args.examples)
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
