#!/usr/bin/env python3
"""Date the undated ti_gerichte rows from the court's own document pages, in two phases.

Why not from the text we hold: measured 2026-09-16 against 57,422 TI rows with a known
date, backfill_dates._extract_date reproduces it exactly only 85.6% of the time (9.2% a
different year, 5.2% the same year on a different day). On the undated rows 111 of its
654 answers predate the docket's registration year, i.e. they are the dates of cited
rulings, police reports and complaints. A date is a legal fact; one wrong in seven is
not acceptable, and a year guard does not catch same-year wrong days (72.2013.80: text
says 2013-07-09, the page says 2013-11-05).

Why the page: the 831 undated rows are the ones whose document page split "Data
decisione" across two cells, which the old fetch path missed. TIGerichteScraper.
fetch_decision now reads that structured cell, so re-fetching each stored source_url
through the scraper's own routine recovers the authoritative date - including for the
182 rows whose text carries no decision date at all (60.2012.250 -> 2012-10-11).

Why two phases: a fetch takes 15-20 s at the scraper's 2 s delay, so 831 rows is
several hours. `harvest` is resumable and writes only a small sidecar; it can run from
any machine and touches neither the shard nor state/. `apply` then rewrites the shard
from the sidecar in about a minute, in a window when nothing else writes it (not during
the 01:00 scrape, not during the build).

Why the shard and not decisions.db: the served databases are opened immutable, and the
nightly rebuild regenerates decisions.db from the shards - a date written to the DB is
erased at the next build.

Usage:
  python3 scripts/backfill_ti_dates.py harvest --targets targets.jsonl --out ti_dates.jsonl
  python3 scripts/backfill_ti_dates.py harvest --shard output/decisions/ti_gerichte.jsonl --out ti_dates.jsonl
  python3 scripts/backfill_ti_dates.py apply --dates ti_dates.jsonl            # dry run
  python3 scripts/backfill_ti_dates.py apply --dates ti_dates.jsonl --apply    # write, with backup
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_SHARD = REPO / "output" / "decisions" / "ti_gerichte.jsonl"
BACKUP_SUFFIX = ".bak-ti-dates"
RE_DOCKET_YEAR = re.compile(r"^\d+\.((?:19|20)\d{2})\.\d+")


def docket_year(docket: str | None) -> int | None:
    m = RE_DOCKET_YEAR.match(docket or "")
    return int(m.group(1)) if m else None


def date_ok(iso: str, docket: str | None, *, today: str | None = None) -> bool:
    """A decision cannot predate its own registration year or lie in the future."""
    if iso > (today or date.today().isoformat()):
        return False
    dy = docket_year(docket)
    if dy is None:
        return True
    try:
        return int(iso[:4]) >= dy
    except ValueError:
        return False


def _iso(value) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()[:10]
    s = str(value).strip()
    return s[:10] if s else None


# -- harvest ---------------------------------------------------------------------------

def undated_targets_from_shard(shard: Path) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    with open(shard, encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            d = r.get("decision_date")
            if d and str(d).strip():
                continue
            did = r.get("decision_id") or ""
            if did in seen:
                continue
            seen.add(did)
            out.append({"decision_id": did, "docket_number": r.get("docket_number"),
                        "source_url": r.get("source_url")})
    return out


def load_targets(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        return [json.loads(l) for l in fh if l.strip()]


def load_done(out_path: Path) -> set[str]:
    if not out_path.exists():
        return set()
    with open(out_path, encoding="utf-8") as fh:
        return {json.loads(l)["decision_id"] for l in fh if l.strip()}


def real_fetch():
    """The scraper's own fetch_decision, with a throwaway state dir (it marks gaps)."""
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    from scrapers.cantonal.ti_gerichte import TIGerichteScraper
    scraper = TIGerichteScraper(state_dir=Path(tempfile.mkdtemp(prefix="ti_backfill_state_")))
    return scraper.fetch_decision


def harvest(targets: list[dict], out_path: Path, fetch, *, limit: int | None = None,
            log=print) -> dict:
    """Fetch each target's page and append {decision_id, decision_date, chamber} to out_path.

    Only successes are written, so a rerun retries every failure and skips every success.
    Dates that predate the docket year are refused and logged, never written.
    """
    done = load_done(out_path)
    stats = {"targets": len(targets), "already_done": 0, "dated": 0, "no_date": 0,
             "fetch_failed": 0, "rejected": 0}
    failed_path = out_path.with_name(out_path.name + ".failed")
    pending = [t for t in targets if t["decision_id"] not in done]
    stats["already_done"] = len(targets) - len(pending)
    if limit is not None:
        pending = pending[:limit]
    t0 = time.time()
    with open(out_path, "a", encoding="utf-8") as out, \
            open(failed_path, "a", encoding="utf-8") as failed:
        for i, t in enumerate(pending, 1):
            stub = {"url": t.get("source_url"), "docket_number": t.get("docket_number"),
                    "decision_date": None, "autorita": None, "title": None,
                    "publication_date": None}
            try:
                d = fetch(stub)
            except Exception as exc:  # network or parse error: retry on the next run
                d = None
                reason = f"exception {type(exc).__name__}"
            else:
                reason = "fetch returned None" if d is None else ""
            iso = _iso(getattr(d, "decision_date", None)) if d is not None else None
            if d is None:
                stats["fetch_failed"] += 1
            elif not iso:
                stats["no_date"] += 1
                reason = "page has no decision date"
            elif not date_ok(iso, t.get("docket_number")):
                stats["rejected"] += 1
                reason = f"date {iso} predates docket year"
            else:
                stats["dated"] += 1
                out.write(json.dumps({"decision_id": t["decision_id"],
                                      "docket_number": t.get("docket_number"),
                                      "decision_date": iso,
                                      "chamber": getattr(d, "chamber", None)},
                                     ensure_ascii=False) + "\n")
                out.flush()
            if reason:
                failed.write(json.dumps({"decision_id": t["decision_id"],
                                         "docket_number": t.get("docket_number"),
                                         "reason": reason}) + "\n")
                failed.flush()
            if i % 25 == 0 or i == len(pending):
                el = time.time() - t0
                log(f"  {i}/{len(pending)} fetched, {stats['dated']} dated, "
                    f"{stats['no_date']} no date, {stats['fetch_failed']} failed, "
                    f"{stats['rejected']} rejected, {el/60:.1f} min", flush=True)
    return stats


# -- apply -----------------------------------------------------------------------------

def load_sidecar(path: Path) -> tuple[dict[str, str], dict[str, str]]:
    """Return ({decision_id: iso date}, {decision_id: chamber}); dates failing date_ok are dropped."""
    dates: dict[str, str] = {}
    chambers: dict[str, str] = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            r = json.loads(line)
            iso = _iso(r.get("decision_date"))
            if iso and date_ok(iso, r.get("docket_number")):
                dates[r["decision_id"]] = iso
            ch = (r.get("chamber") or "").strip()
            if ch and not ch.isdigit():
                chambers[r["decision_id"]] = ch
    return dates, chambers


def load_dates(path: Path) -> dict[str, str]:
    return load_sidecar(path)[0]


def plan_apply(shard: Path, dates: dict[str, str], *, overwrite: bool = False,
               chambers: dict[str, str] | None = None) -> tuple[list[str], dict]:
    """Output lines + summary.

    Default: fill EMPTY dates only. --overwrite: also REPLACE a stored date when the sidecar's
    differs (the Feb-Mar 2026 portal index gave ~22% of TI rows a wrong date; the page cell is
    authoritative). chambers: set the chamber when the stored one is empty or a bare number
    (the old parser stored the day of the month there).
    Patches EVERY row of an id (1,302 ids are doubled in the direct shard and the build keeps
    one arbitrarily). Untouched lines are copied verbatim.
    """
    chambers = chambers or {}
    out: list[str] = []
    stats = {"rows": 0, "undated": 0, "filled": 0, "replaced": 0, "unchanged": 0,
             "still_undated": 0, "chamber_set": 0, "bad_json": 0}
    ids: set[str] = set()
    date_ids: set[str] = set()
    with open(shard, encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                out.append(line)
                continue
            stats["rows"] += 1
            try:
                row = json.loads(line)
            except ValueError:
                stats["bad_json"] += 1
                out.append(line)
                continue
            did = row.get("decision_id") or ""
            cur = row.get("decision_date")
            cur = str(cur)[:10] if cur and str(cur).strip() else None
            iso = dates.get(did)
            changed = False
            if cur is None:
                stats["undated"] += 1
                if iso:
                    row["decision_date"] = iso
                    stats["filled"] += 1
                    date_ids.add(did)
                    changed = True
                else:
                    stats["still_undated"] += 1
            elif overwrite and iso and iso != cur:
                row["decision_date"] = iso
                stats["replaced"] += 1
                date_ids.add(did)
                changed = True
            elif iso:
                stats["unchanged"] += 1
            ch = chambers.get(did)
            if ch:
                have = str(row.get("chamber") or "").strip()
                if not have or have.isdigit():
                    row["chamber"] = ch
                    stats["chamber_set"] += 1
                    changed = True
            if changed:
                ids.add(did)
                out.append(json.dumps(row, ensure_ascii=False) + "\n")
            else:
                out.append(line)
    stats["patched"] = stats["filled"] + stats["replaced"]
    stats["ids_patched"] = len(ids)
    stats["duplicate_rows_patched"] = stats["patched"] - len(date_ids)
    return out, stats


def write_shard(shard: Path, out: list[str], expected_rows: int) -> Path:
    if len(out) < expected_rows:
        raise RuntimeError("output has fewer lines than input; refusing to write")
    backup = shard.with_name(shard.name + BACKUP_SUFFIX)
    if not backup.exists():
        shutil.copy2(shard, backup)
    tmp = shard.with_name(shard.name + ".tmp-ti-dates")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.writelines(out)
    os.replace(tmp, shard)
    return backup


# -- cli -------------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    h = sub.add_parser("harvest", help="fetch dates from the document pages into a sidecar")
    src = h.add_mutually_exclusive_group(required=True)
    src.add_argument("--targets", help="JSONL of decision_id, docket_number, source_url")
    src.add_argument("--shard", help="derive the targets from the shard's undated rows")
    h.add_argument("--out", required=True, help="sidecar JSONL to append to (resumable)")
    h.add_argument("--limit", type=int, default=None, help="fetch at most N (for a trial run)")

    a = sub.add_parser("apply", help="write the sidecar dates into the shard")
    a.add_argument("--dates", required=True, help="sidecar JSONL from harvest")
    a.add_argument("--shard", default=str(DEFAULT_SHARD))
    a.add_argument("--overwrite", action="store_true",
                   help="also replace stored dates that differ from the sidecar (default: fill empty only)")
    a.add_argument("--chambers", action="store_true",
                   help="also set the chamber where the stored one is empty or numeric")
    a.add_argument("--apply", action="store_true", help="write (default: dry run)")

    args = ap.parse_args(argv)

    if args.cmd == "harvest":
        targets = (load_targets(Path(args.targets)) if args.targets
                   else undated_targets_from_shard(Path(args.shard)))
        stats = harvest(targets, Path(args.out), real_fetch(), limit=args.limit)
        print("harvest:", json.dumps(stats))
        return 0

    shard = Path(args.shard)
    if not shard.exists():
        print(f"shard not found: {shard}")
        return 2
    dates, chambers = load_sidecar(Path(args.dates))
    out, stats = plan_apply(shard, dates, overwrite=args.overwrite,
                            chambers=chambers if args.chambers else None)
    print(f"shard        : {shard}")
    print(f"sidecar      : {len(dates)} dates, {len(chambers)} chambers"
          f"{' (chambers applied)' if args.chambers else ''}")
    print(f"rows         : {stats['rows']}")
    print(f"undated      : {stats['undated']}  -> filled {stats['filled']}, still undated {stats['still_undated']}")
    print(f"dated        : replaced {stats['replaced']}"
          f"{'' if args.overwrite else ' (overwrite off)'}, unchanged {stats['unchanged']}")
    print(f"chambers set : {stats['chamber_set']}")
    print(f"rows patched : {stats['patched']} dates ({stats['ids_patched']} ids touched incl. chambers)")
    if stats["bad_json"]:
        print(f"unparseable  : {stats['bad_json']} (copied through untouched)")
    if not args.apply:
        print("dry run: nothing written")
        return 0
    if not stats["ids_patched"]:
        print("nothing to write")
        return 0
    backup = write_shard(shard, out, stats["rows"])
    print(f"applied: {stats['patched']} dates, {stats['chamber_set']} chambers; backup {backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
