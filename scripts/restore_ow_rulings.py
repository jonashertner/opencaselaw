#!/usr/bin/env python3
"""
restore_ow_rulings.py — give ow_gerichte rows the date their own ruling line
states, and the Regeste the portal cut short.

Why (external report 2026-10-26; audit of the HF mirror the same day):
  * Dates. The portal dates every entry imported in 2015 with 26 November
    2015. `scripts/repair_decision_dates.py --all` (2026-03-12) replaced that
    with the first "Beschluss/Entscheid vom <date>" of the text, which in a
    yearbook entry is often a date from the Sachverhalt: AbR 2000/01 Nr. 4,
    a ruling of 18 July 2001, was cited as of 8 February 1999 (a Gemeinderat
    resolution it recounts), because "Entscheid der Obergerichtskommisson
    vom" does not match the pass's "Entscheid vom". Of 2,182 directly
    scraped rows, 793 disagreed with their own ruling line (646 moved to a
    wrong date, 147 still on the import day).
  * Regeste. The scraper stored the portal's abstract, which stops at ~240
    characters ("... vom 18. Juli 2"); the text opens with the same block in
    full. 2,180 of the 2,182 regestes are a prefix of their text.

The entry's own line is the one between the Regeste and the reasons
(scrapers.cantonal.ow_gerichte.ruling_date). Rows without one, with a split
date ("vom 25. November/22. Dezember 2003") or with a year outside the
yearbook keep their date. Where the date moves to an earlier day and
publication_date is empty, the old date goes there: it is the portal's
import day (26 November 2015 for AbR, 1 July 2016 for VVGE) or a later
upload date, not a ruling's.

Only directly scraped rows are dated. The entscheidsuche stubs in
es_ow_gerichte.jsonl (1,288: a header and the same cut Regeste, dated with
the portal's import day) carry no text to read a date from, and cite()
resolved "AbR 2000/01 Nr. 4" to the stub, as of 26 November 2015.
`--drop-es-twins ES_SHARD` removes the 1,264 stubs whose docket the direct
shard holds, writing them to ES_SHARD.dropped-<date> beside it (not
*.jsonl, which the build would ingest), and gives each direct twin the
stub's id as previous_decision_id (old links resolve; the per-court swap
gate counts the stub as answered); the 24
without a direct row (appendices, entries the portal search misses) stay.

Every changed row gets a `date_restore` / `regeste_restore` stamp
{from, to, method, at} (a previous stamp is kept under "prev"), so the change
is exactly reversible.

Streaming, temp file + atomic replace, DRY RUN by default, idempotent. Apply
only while no process reads or appends the shard: after that night's scrape
has logged Done. and before the 03:30 full build.

Usage:
  python3 scripts/restore_ow_rulings.py output/decisions/ow_gerichte.jsonl
  python3 scripts/restore_ow_rulings.py output/decisions/ow_gerichte.jsonl --apply
  python3 scripts/restore_ow_rulings.py output/decisions/ow_gerichte.jsonl \
      --drop-es-twins output/decisions/es_ow_gerichte.jsonl [--apply]
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
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scrapers.cantonal.ow_gerichte import full_regeste, ruling_date  # noqa: E402

def decide(obj: dict) -> tuple[str, str | None, str | None]:
    """(date outcome, new decision_date or None, new regeste or None)."""
    text = obj.get("full_text")
    outcome, own = ruling_date(text, obj.get("docket_number"))
    new_date = None
    if own:
        cur = str(obj.get("decision_date") or "")[:10]
        if own.isoformat() == cur:
            outcome = "agrees"
        else:
            outcome = "filled" if not cur else "corrected"
            new_date = own.isoformat()
    return outcome, new_date, full_regeste(text, obj.get("regeste"))


def _stamp(obj: dict, key: str, stamp: dict) -> None:
    if obj.get(key):
        stamp["prev"] = obj[key]
    obj[key] = stamp


def apply_change(obj: dict, new_date: str | None, new_regeste: str | None, now: str) -> dict:
    if new_date:
        old = str(obj.get("decision_date") or "")[:10]
        stamp = {"from": obj.get("decision_date"), "to": new_date,
                 "method": "ow_ruling_line", "at": now}
        if old and old > new_date and not obj.get("publication_date"):
            obj["publication_date"] = old
            stamp["publication_date"] = {"from": None, "to": old}
        _stamp(obj, "date_restore", stamp)
        obj["decision_date"] = new_date
    if new_regeste:
        _stamp(obj, "regeste_restore", {"from_len": len(obj.get("regeste") or ""),
                                        "to_len": len(new_regeste),
                                        "method": "ow_text_block", "at": now})
        obj["regeste"] = new_regeste
    return obj


def run(path: Path, apply: bool, examples: int) -> Counter:
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
                if obj.get("court") != "ow_gerichte" or obj.get("source") == "entscheidsuche":
                    stats["skipped_other"] += 1
                    if fout:
                        fout.write(line)
                    continue
                stats["rows"] += 1
                outcome, new_date, new_regeste = decide(obj)
                stats[f"date_{outcome}"] += 1
                if new_regeste:
                    stats["regeste_completed"] += 1
                if new_date and shown < examples:
                    shown += 1
                    print(f"  {obj.get('decision_id'):<40} {obj.get('decision_date')} -> "
                          f"{new_date}  [{outcome}]", file=sys.stderr)
                if not new_date and not new_regeste:
                    if fout:
                        fout.write(line)
                    continue
                stats["rows_changed"] += 1
                if not fout:
                    continue
                obj = apply_change(obj, new_date, new_regeste, now)
                fout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    except Exception:
        if fout:
            fout.close()
            os.unlink(tmp_path)
        raise
    if fout:
        fout.close()
        if stats["rows_changed"]:
            # mkstemp creates 0600; keep the shard's own mode.
            os.chmod(tmp_path, stat.S_IMODE(os.stat(path).st_mode))
            os.replace(tmp_path, str(path))
        else:
            os.unlink(tmp_path)
    return stats


def _docket_key(docket: str | None) -> str:
    """"AbR 2000/01 Nr. 4" and the stub's "AbR_2000_01_Nr._4" alike."""
    return re.sub(r"[\s._/\-]", "", docket or "").upper()


def _rewrite(path: Path, lines: list[str]) -> None:
    fd, tmp_path = tempfile.mkstemp(suffix=".jsonl", dir=str(path.parent))
    with os.fdopen(fd, "w", encoding="utf-8") as fout:
        fout.writelines(lines)
    # mkstemp creates 0600; keep the shard's own mode.
    os.chmod(tmp_path, stat.S_IMODE(os.stat(path).st_mode))
    os.replace(tmp_path, str(path))


def drop_es_twins(direct: Path, es: Path, apply: bool) -> Counter:
    """Remove the entscheidsuche stubs whose docket the direct shard holds.

    The direct twin takes the stub's id as `previous_decision_id`, so the build
    records it in decision_id_aliases: a link to the stub's id keeps resolving,
    and the per-court swap gate counts the stub as answered, not lost. A twin
    that already carries a previous id keeps it, and its stub stays."""
    stats: Counter = Counter()
    direct_lines: list[str] = []
    twin_at: dict[str, int] = {}
    with open(direct, encoding="utf-8") as fh:
        for line in fh:
            direct_lines.append(line)
            if not line.strip():
                continue
            obj = json.loads(line)
            key = _docket_key(obj.get("docket_number"))
            if (obj.get("source") != "entscheidsuche" and key
                    and not obj.get("previous_decision_id")):
                twin_at.setdefault(key, len(direct_lines) - 1)
    kept: list[str] = []
    dropped: list[str] = []
    for line in open(es, encoding="utf-8"):
        if not line.strip():
            continue
        obj = json.loads(line)
        key = _docket_key(obj.get("docket_number"))
        i = twin_at.pop(key, None) if obj.get("court") == "ow_gerichte" and key else None
        if i is None:
            kept.append(line)
            stats["es_kept"] += 1
            continue
        twin = json.loads(direct_lines[i])
        twin["previous_decision_id"] = obj["decision_id"]
        twin["previous_id_source"] = "es_twin_dropped"
        direct_lines[i] = json.dumps(twin, ensure_ascii=False) + "\n"
        dropped.append(line)
        stats["es_dropped_twin"] += 1
    if apply and dropped:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
        # Not *.jsonl: the build ingests every output/decisions/*.jsonl.
        side = es.with_name(f"{es.name}.dropped-{stamp}")
        with open(side, "a", encoding="utf-8") as fh:
            fh.writelines(dropped)
        _rewrite(direct, direct_lines)
        _rewrite(es, kept)
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("shards", nargs="+", type=Path)
    ap.add_argument("--apply", action="store_true", help="rewrite the shard (default: dry run)")
    ap.add_argument("--examples", type=int, default=15)
    ap.add_argument("--drop-es-twins", type=Path, metavar="ES_SHARD",
                    help="instead of repairing, drop the stubs of ES_SHARD the (one) direct shard holds")
    a = ap.parse_args()
    if a.drop_es_twins:
        if len(a.shards) != 1:
            ap.error("--drop-es-twins takes exactly one direct shard")
        print(f"== {a.drop_es_twins} ({'APPLY' if a.apply else 'dry run'})", file=sys.stderr)
        stats = drop_es_twins(a.shards[0], a.drop_es_twins, a.apply)
        print(json.dumps({"shard": str(a.drop_es_twins), **dict(sorted(stats.items()))}))
        return 0
    for shard in a.shards:
        print(f"== {shard} ({'APPLY' if a.apply else 'dry run'})", file=sys.stderr)
        stats = run(shard, a.apply, a.examples)
        print(json.dumps({"shard": str(shard), **dict(sorted(stats.items()))}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
