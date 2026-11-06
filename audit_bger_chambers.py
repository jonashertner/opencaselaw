"""Read-only census: does every BGG-era BGer row name its own division?

Since 2007 each docket series belongs to exactly one division (4A/4D -> I.
zivilrechtliche Abteilung, 8C -> I. sozialrechtliche, from 2023 IV. öffentlich-
rechtliche, ...). A served chamber is counted WRONG when it names a different
BGer division than the series AND than the ruling's own header (the header is
the court's statement and wins: "II. sozialrechtliche Abteilung (als straf-
rechtliche Abteilung)"). NULL and non-division values (docket codes, foreign
courts) on BGG-era dockets are counted separately. Dotted pre-2007 and EVG
dockets are out of scope (no series rule).

Written for suggestion 0003 (bger_4A_594_2026). Expected after the fix's first
full rebuild: wrong 0, null 0. Before: 6,259 wrong + 5,964 NULL of 141,478
(shard simulation 2026-11-06, spot-checked 10/10 against the live API).

    python3 audit_bger_chambers.py --db output/decisions.db [--out rows.jsonl]

Invariant 9: refuses while a publish/build/incremental process runs on this
host (pass --force only against a copy). Opens mode=ro&immutable=1. Reads the
leading columns of every bger row and full_text only for the suspects.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scrapers.bger import (  # noqa: E402
    chamber_from_text,
    division_key,
    docket_division_key,
)


def build_running() -> str | None:
    try:
        out = subprocess.run(
            ["pgrep", "-af", "publish.py|build_fts5|incremental_nightly"],
            capture_output=True, text=True, timeout=5, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    lines = [ln for ln in out.splitlines() if "pgrep" not in ln]
    return lines[0] if lines else None


def audit(con: sqlite3.Connection, limit: int = 10):
    total = Counter()
    nulls = Counter()
    suspects = []
    for did, docket, chamber in con.execute(
            "SELECT decision_id, docket_number, chamber FROM decisions "
            "WHERE court='bger'"):
        series = docket_division_key(docket or "")
        if not series:
            continue
        total[series] += 1
        stored = division_key(chamber)
        if stored is None:
            nulls[series] += 1
        elif stored != series:
            suspects.append(did)
    wrong = []
    for did in suspects:
        docket, chamber, ddate, text = con.execute(
            "SELECT docket_number, chamber, decision_date, full_text "
            "FROM decisions WHERE decision_id=?", (did,)).fetchone()
        header = chamber_from_text(text or "")
        if header and division_key(header) == division_key(chamber):
            continue
        wrong.append({"decision_id": did, "docket": docket, "decision_date": ddate,
                      "chamber": chamber, "header": header})
    return total, nulls, wrong


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=Path("output/decisions.db"))
    ap.add_argument("--out", type=Path, help="write every wrong row as JSONL")
    ap.add_argument("--force", action="store_true",
                    help="scan even while a build runs (copies only)")
    a = ap.parse_args()
    proc = build_running()
    if proc and not a.force:
        print(f"REFUSING: a build is running on this host ({proc[:80]}). "
              "Invariant 9: no decisions.db scans during the build window.",
              file=sys.stderr)
        return 2
    con = sqlite3.connect(f"file:{a.db}?mode=ro&immutable=1", uri=True)
    try:
        total, nulls, wrong = audit(con)
    finally:
        con.close()
    n = sum(total.values())
    print(f"source: {a.db} (read-only)")
    print(f"BGG-era bger rows:            {n:>8,}")
    print(f"  wrong division (vs series and header): {len(wrong):>6,}")
    print(f"  NULL / non-division chamber:           {sum(nulls.values()):>6,}")
    by = Counter((r["docket"] or "")[:2] for r in wrong)
    if by:
        print("  wrong by prefix: " + ", ".join(f"{k} {v}" for k, v in by.most_common(12)))
    for r in wrong[:10]:
        print(f"    {r['decision_id']}: {r['chamber']!r} (header {r['header']!r})")
    if a.out:
        with a.out.open("w", encoding="utf-8") as f:
            for r in wrong:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
