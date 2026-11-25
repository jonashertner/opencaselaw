#!/usr/bin/env python3
"""
restore_sz_dates.py — give SZ rows (Kantonsgericht, Verwaltungsgericht) the
date their ruling's own heading states.

Why (classification of both portals, 2026-11-25; first seen 2026-09-27):
  * The SZ Tribuna listing gives one date per dossier, not per document. A
    held record can therefore carry another document's date: BEK 2023 94 is
    stored 2024-04-08 but heads "Beschluss vom 14. Dezember 2023" (2024-04-08
    is the date of the second document of that dossier); ZK2 2019 47 holds the
    Berichtigung of 15 January 2020 under the judgment's date 2019-11-22.
  * Rows from the entscheidsuche import (es_sz_*.jsonl) carry dates from the
    contested act or the filing: STK_2021_17 stored 2015-10-19, heads
    "Urteil vom 8. Februar 2022"; II_2021_36 stored 2020-07-30, the date of
    the "Zahlungsverfügung" under review.
  Across every held SZ text the heading agrees with the stored date for 99.3%
  of rows; each of the 28 that disagree was read: the heading is right.

  It matters beyond the date shown: the doc-id seeds (same change) make the
  scraper fetch the further documents of held dossiers with the date they
  state. build_fts5 folds rows with equal (court, docket, date), so a held
  record carrying the other document's date would swallow that document.

The court's own statement: the first ruling heading in the document head
(header_date_de, shared with the scraper): "Urteil vom 3. März 2026",
"III 2025 82Entscheid vom 26. September 2025", "Berichtigung vom …". Contested
acts quoted further down ("Einspracheentscheid vom …") never count. It is used
only when its year is not before the docket's year and it is not after today.

Per row whose court is sz_gerichte or sz_verwaltungsgericht:
  heading date found and differs from (or fills) decision_date -> heading date
  otherwise                                                    -> unchanged
Every changed row gets a `date_restore` stamp {from, to, method, at} (a
previous stamp is kept under "prev"), so the change is exactly reversible.

Streaming, temp file + atomic replace, DRY RUN by default, idempotent. Apply
only while no process reads or appends the shard: after that night's scrape
has logged Done. and before the 03:30 full build. Run it on every shard that
holds SZ rows (sz_*.jsonl and es_sz_*.jsonl).

Usage:
  python3 scripts/restore_sz_dates.py output/decisions/sz_gerichte.jsonl \\
      output/decisions/es_sz_gerichte.jsonl output/decisions/sz_verwaltungsgericht.jsonl \\
      output/decisions/es_sz_verwaltungsgericht.jsonl            # dry run
  ... --apply
"""
from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
import tempfile
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scrapers.cantonal.sz_gerichte import header_date_de  # noqa: E402

SZ_COURTS = ("sz_gerichte", "sz_verwaltungsgericht")


def docket_year(docket: str | None) -> int | None:
    """'BEK 2023 94', 'III_2021_196' -> 2023, 2021."""
    for p in re.split(r"[\s_]+", (docket or "").strip()):
        if re.fullmatch(r"(?:19|20)\d{2}", p):
            return int(p)
    return None


def decide(obj: dict, today: str | None = None) -> tuple[str, str | None]:
    """(outcome, new decision_date or None)."""
    own_d = header_date_de(obj.get("full_text") or "")
    if own_d is None:
        return "no_heading", None
    own = own_d.isoformat()
    if own > (today or date.today().isoformat()):
        return "heading_in_future", None
    dy = docket_year(obj.get("docket_number"))
    if dy is not None and own_d.year < dy:
        return "before_docket_year", None
    cur = str(obj.get("decision_date") or "")[:10]
    if cur == own:
        return "agrees", None
    return ("filled" if not cur else "corrected"), own


def run(path: Path, apply: bool, examples: int, today: str | None = None) -> Counter:
    stats: Counter = Counter()
    shown = 0
    fout = None
    tmp_path = None
    if apply:
        fd, tmp_path = tempfile.mkstemp(suffix=".jsonl", dir=str(path.parent))
        fout = os.fdopen(fd, "w", encoding="utf-8")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                stripped = line.strip()
                obj = None
                if stripped:
                    try:
                        obj = json.loads(stripped)
                    except ValueError:
                        stats["json_error"] += 1
                if obj is None or str(obj.get("court") or "") not in SZ_COURTS:
                    if stripped and obj is not None:
                        stats["other_court"] += 1
                    if fout:
                        fout.write(line)
                    continue
                stats["rows"] += 1
                outcome, new = decide(obj, today)
                stats[outcome] += 1
                if new and shown < examples:
                    shown += 1
                    print(f"  {obj.get('decision_id'):<44} {obj.get('decision_date')} -> {new}"
                          f"  [{outcome}]", file=sys.stderr)
                if new is None:
                    if fout:
                        fout.write(line)
                    continue
                stats["rows_changed"] += 1
                if not fout:
                    continue
                restore = {"from": obj.get("decision_date"), "to": new,
                           "method": "sz_heading", "at": now}
                if obj.get("date_restore"):
                    restore["prev"] = obj["date_restore"]
                obj["date_restore"] = restore
                obj["decision_date"] = new
                fout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    except Exception:
        if fout:
            fout.close()
            os.unlink(tmp_path)
        raise
    if fout:
        fout.close()
        if stats["rows_changed"]:
            os.chmod(tmp_path, stat.S_IMODE(os.stat(path).st_mode))
            os.replace(tmp_path, str(path))
        else:
            os.unlink(tmp_path)
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("shards", nargs="+", type=Path)
    ap.add_argument("--apply", action="store_true", help="rewrite the shard (default: dry run)")
    ap.add_argument("--examples", type=int, default=40)
    a = ap.parse_args()
    for shard in a.shards:
        print(f"== {shard} ({'APPLY' if a.apply else 'dry run'})", file=sys.stderr)
        stats = run(shard, a.apply, a.examples)
        print(json.dumps({"shard": str(shard), **dict(sorted(stats.items()))}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
