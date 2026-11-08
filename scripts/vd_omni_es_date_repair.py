#!/usr/bin/env python3
"""Give frozen es_vd_omni rows the decision date the court's portal gives.

Background (runbooks/vd_omni_cdap_2026-11.md): 825 of the 28,033 rows in
es_vd_omni.jsonl (the frozen entscheidsuche copy of the Vaud CDAP / Tribunal
administratif) carry a wrong decision_date, often the date of the decision
under appeal (AC.1990.6992: 1990-08-06; the ruling is of 1991-07-10). The
vd_omni scraper recognises them (same docket; the row's own header date or its
résumé matches a portal ruling of another date), does not fetch them again,
and lists them in state/vd_omni.es_date_mismatches.tsv. The list measured
2026-11-08 is runbooks/vd_omni_es_date_repair_2026-11.tsv.

Only decision_date changes, and only for a listed row whose current date is
the listed es_date. Every new date must be confirmed by the row itself: the
date its entscheidsuche header gives for the docket ("Vaud Tribunal cantonal
... 10.07.1991 AC.1990.6992"), or the ruling's own date in its text ("Arrêt du
11 juillet 2023", "Lausanne, le 11 juillet 2023"), or a date checked by hand
on the portal (CONFIRMED_ON_PORTAL, one row). An unconfirmed row is left as
it is and counted. The script refuses to write unless every changed line
keeps all other fields, the line count is the same, and no corrected date
gives two rows of the shard the same court|docket|date key (the build's
dedup Pass 1 would delete one without reading the text). With --direct-shard
it also counts keys shared with the direct vd_omni shard (the same ruling held
twice; the build then keeps one, which is the point).

Streaming, DRY RUN by default. --apply hard-links <shard>.bak-<UTC date>
beside the shard, writes <shard>.tmp (neither matches the build's *.jsonl
glob) and swaps it in atomically. Run it outside the build window, under the
shared shard-repair lock (see the runbook).

Usage:
  python3 scripts/vd_omni_es_date_repair.py
  python3 scripts/vd_omni_es_date_repair.py --apply
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from collections import Counter
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from models import make_canonical_key  # noqa: E402
from scrapers.cantonal.vd_omni import es_header_date  # noqa: E402

COURT = "vd_omni"
DEFAULT_SHARD = REPO / "output" / "decisions" / "es_vd_omni.jsonl"
DEFAULT_DIRECT = REPO / "output" / "decisions" / "vd_omni.jsonl"
DEFAULT_LIST = REPO / "runbooks" / "vd_omni_es_date_repair_2026-11.tsv"

# Dates checked by hand on the portal where the row itself cannot confirm
# them. FO.2023.0004 (checked 2026-11-08): the es copy is the court's earlier,
# unsigned version ("Arrêt du 27 juin 2023", "Lausanne, le" left blank); the
# portal holds the re-issued ruling, same text, "Arrêt du 11 juillet 2023 ...
# Lausanne, le 11 juillet 2023". The backfill fetches that final text under
# the same id (VD_OMNI_REFETCH_IDS, runbook).
CONFIRMED_ON_PORTAL = {"vd_omni_FO.2023.0004": "2023-07-11"}

_MONTHS = {m: i for i, m in enumerate(
    ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
     "septembre", "octobre", "novembre", "décembre"], start=1)}
_OWN_DATE_RE = re.compile(
    r"(?:Arr[êe]t|Jugement|D[ée]cision|Ordonnance)\s+du\s+(\d{1,2})(?:\s*er)?\s+("
    + "|".join(_MONTHS) + r")\s+(\d{4})|Lausanne,\s+le\s+(\d{1,2})(?:\s*er)?\s+("
    + "|".join(_MONTHS) + r")\s+(\d{4})", re.IGNORECASE)


def text_own_dates(text: str | None) -> set[str]:
    """Dates a ruling gives itself: its "Arrêt du …" opening and its
    "Lausanne, le …" closing line."""
    out = set()
    for m in _OWN_DATE_RE.finditer(text or ""):
        d, mon, y = (m.group(1), m.group(2), m.group(3)) if m.group(1) else (m.group(4), m.group(5), m.group(6))
        try:
            out.add(date(int(y), _MONTHS[mon.lower()], int(d)).isoformat())
        except (ValueError, KeyError):
            pass
    return out


def load_list(path: Path) -> dict[str, tuple[str, str]]:
    """decision id -> (es date, portal date)."""
    out = {}
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            out[row["es_decision_id"]] = (row["es_date"], row["portal_date"])
    return out


def key(rec: dict) -> str:
    return make_canonical_key(rec.get("court") or "", rec.get("docket_number") or "", rec.get("decision_date"))


def repair(rec: dict, wanted: dict[str, tuple[str, str]]) -> tuple[dict | None, str]:
    did = rec.get("decision_id") or ""
    if did not in wanted:
        return None, "not_listed"
    es_date, portal_date = wanted[did]
    if str(rec.get("decision_date") or "") != es_date:
        return None, "date_already_differs"
    docket = rec.get("docket_number") or ""
    if es_header_date(rec.get("full_text"), docket) == portal_date:
        how = "confirmed_by_header"
    elif portal_date in text_own_dates(rec.get("full_text")):
        how = "confirmed_by_text"
    elif CONFIRMED_ON_PORTAL.get(did) == portal_date:
        how = "confirmed_on_portal"
    else:
        return None, "unconfirmed"
    out = dict(rec)
    out["decision_date"] = portal_date
    return out, how


def _iter(shard: Path):
    with open(shard, "rb") as fh:
        for raw in fh:
            line = raw.rstrip(b"\r\n")
            try:
                rec = json.loads(line) if line.strip() else None
            except ValueError:
                rec = None
            yield raw, rec if isinstance(rec, dict) else None


def run(shard: Path, wanted: dict, apply: bool, direct: Path | None = None, examples: int = 0) -> Counter:
    stats: Counter = Counter()
    # Pass 1 (read only): every key after the repair, to refuse collisions.
    after_keys: Counter = Counter()
    before_keys: Counter = Counter()
    for _, rec in _iter(shard):
        if rec is None or rec.get("court") != COURT or not (rec.get("docket_number") or "").strip():
            continue
        new, _ = repair(rec, wanted)
        before_keys[key(rec)] += 1
        after_keys[key(new or rec)] += 1
    new_dupes = [k for k, n in after_keys.items() if n > 1 and n > before_keys.get(k, 0)]
    if new_dupes:
        raise SystemExit(f"refusing: {len(new_dupes)} corrected dates collide with another row of "
                         f"the shard (court|docket|date), e.g. {new_dupes[:3]}")
    stats["dup_keys_before"] = sum(1 for n in before_keys.values() if n > 1)
    if direct is not None and direct.is_file():
        direct_keys = {key(r) for _, r in _iter(direct) if r}
        stats["keys_shared_with_direct_before"] = sum(1 for k in before_keys if k in direct_keys)
        stats["keys_shared_with_direct_after"] = sum(1 for k in after_keys if k in direct_keys)

    # Pass 2: rewrite.
    shown = 0
    seen: set[str] = set()
    present: set[str] = set()
    tmp = shard.with_name(shard.name + ".tmp")
    fout = open(tmp, "wb") if apply else None
    try:
        for raw, rec in _iter(shard):
            stats["lines"] += 1
            if rec is None:
                stats["unparsed"] += 1
                if fout:
                    fout.write(raw)
                continue
            new, how = repair(rec, wanted)
            stats[f"outcome:{how}"] += 1
            if rec.get("decision_id") in wanted:
                present.add(rec["decision_id"])
            if new is None:
                if fout:
                    fout.write(raw)
                continue
            for k in set(rec) | set(new):
                if k != "decision_date" and rec.get(k) != new.get(k):
                    raise SystemExit(f"refusing: {rec.get('decision_id')} would change {k}")
            seen.add(rec["decision_id"])
            stats["rows_changed"] += 1
            if shown < examples:
                shown += 1
                print(f"  {rec['decision_id']}  {rec.get('decision_date')} -> {new['decision_date']}  [{how}]",
                      file=sys.stderr)
            if fout:
                fout.write(json.dumps(new, ensure_ascii=False).encode("utf-8") + b"\n")
        stats["listed_not_in_shard"] = len(set(wanted) - present)
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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--shard", type=Path, default=DEFAULT_SHARD)
    ap.add_argument("--list", type=Path, default=DEFAULT_LIST, help="es_date_mismatches TSV")
    ap.add_argument("--direct-shard", type=Path, default=DEFAULT_DIRECT)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--examples", type=int, default=5)
    a = ap.parse_args()
    wanted = load_list(a.list)
    stats = run(a.shard, wanted, a.apply, a.direct_shard, a.examples)
    print(json.dumps({"apply": a.apply, "listed": len(wanted), **dict(sorted(stats.items()))}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
