#!/usr/bin/env python3
"""Seed the date sidecar and recover same-docket second rulings (BVGer, BStGer, BPatGer).

The three scrapers keyed decision_id on the docket and skipped every held
docket, so a later ruling under it (interim → final ruling, referral → merits
judgment) was never fetched. Audit 2026-11-16
(docs/claude/same-docket-gap-2026-11-16/): BVGer 186, BStGer 16, BPatGer 36.
The scrapers are now date-aware (scrapers/_date_identity.py) but run in
LEGACY mode until the sidecar state/<court>.dates.txt exists. Same mechanism
and same guards as backfill_bger_docket_collisions.py (BGer, 2026-09-03).

This script
  1. seeds the sidecar from the corpus shards' (docket_number, decision_date)
     pairs (only rows whose id is the docket id or its -D variant: TPF
     collection entries and other id schemes are left out), then
  2. finds the suppressed rulings on the COURT PORTALS and appends them:
     - bvger, bstger: a one-day ruling-date window on each date listed in the
       audit TSV (the TSV only says where to look; the rulings come from the
       court's own Weblaw search);
     - bpatger: every detail page of the portal's database search.
     Only same-docket second rulings (-D ids) are appended; dockets that are
     not held at all are counted and left to the regular scraper.

Run ON THE VPS, OUTSIDE the nightly build window (the seed streams the
court's corpus shards; fetch mode appends to the JSONL the build reads):
  cd /opt/caselaw/repo
  nice -n 19 ionice -c 3 python3 backfill_docket_collisions.py --court bvger --seed-only
  python3 backfill_docket_collisions.py --court bvger --dry-run
  python3 backfill_docket_collisions.py --court bvger            # fetch + append

Idempotent: an existing non-empty sidecar is kept (--reseed rebuilds it);
fetched rulings enter the sidecar and state and are skipped next time.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))

from models import make_decision_id  # noqa: E402

COURTS = ("bvger", "bstger", "bpatger")
AUDIT_DIR = REPO / "docs" / "claude" / "same-docket-gap-2026-11-16"

RE_ID = re.compile(r'"decision_id"\s*:\s*"([^"]+)"')
RE_COURT = re.compile(r'"court"\s*:\s*"([^"]+)"')
RE_DOCKET = re.compile(r'"docket_number"\s*:\s*"([^"]+)"')
RE_DATE = re.compile(r'"decision_date"\s*:\s*"(\d{4}-\d{2}-\d{2})')
HEAD = 8192   # the fields sit at the start of every row


def _fields(line: str):
    head = line[:HEAD]
    out = []
    for rx in (RE_ID, RE_COURT, RE_DOCKET, RE_DATE):
        m = rx.search(head) or rx.search(line)
        out.append(m.group(1) if m else None)
    return tuple(out)


def make_scraper(court: str, state_dir: Path):
    if court == "bvger":
        from scrapers.bvger import BVGerScraper as cls
    elif court == "bstger":
        from scrapers.bstger import BStGerScraper as cls
    else:
        from scrapers.bpatger import BPatGerScraper as cls
    return cls(state_dir=state_dir)


def seed_pairs(court: str, shards: list[Path]) -> tuple[list[str], int, int]:
    """(sidecar lines, undated rows, rows of other id schemes)."""
    pairs: list[str] = []
    seen: set[str] = set()
    undated = foreign = 0
    for src in shards:
        with open(src, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                did, row_court, docket, d_iso = _fields(line)
                if not did or not docket or (row_court and row_court != court):
                    continue
                base = make_decision_id(court, docket)
                if did != base and not did.startswith(base + "-D"):
                    foreign += 1          # e.g. bstger TPF collection entries
                    continue
                if not d_iso:
                    undated += 1
                    continue
                key = f"{base}|{d_iso}"
                if key in seen:
                    continue
                seen.add(key)
                pairs.append(f"{base}\t{d_iso}\t{did}")
    return pairs, undated, foreign


def seed_sidecar(scraper, court: str, shards: list[Path], reseed: bool) -> int:
    side = scraper._dates_sidecar()
    if side.exists() and side.stat().st_size > 0 and not reseed:
        n = len([ln for ln in side.read_text().splitlines() if ln.strip()])
        print(f"[{court}] sidecar already seeded ({n} pairs) — kept")
        return n
    missing = [s for s in shards if not s.exists()]
    if missing:
        raise SystemExit(f"[{court}] corpus shard(s) missing: {missing}")
    pairs, undated, foreign = seed_pairs(court, shards)
    if not pairs:
        raise SystemExit(
            f"[{court}] corpus yielded 0 (docket, date) pairs — refusing to "
            "write an empty sidecar")
    tmp = side.with_suffix(".tmp")
    tmp.write_text("\n".join(pairs) + "\n")
    tmp.replace(side)      # atomic — a crash mid-seed leaves no half file
    print(f"[{court}] sidecar seeded with {len(pairs)} pairs from "
          f"{len(shards)} shard(s) ({undated} undated rows, {foreign} rows "
          "of another id scheme skipped)")
    return len(pairs)


def audit_dates(court: str, tsv: Path, include_disagreements: bool) -> list[date]:
    kinds = {"second_ruling"} | ({"date_disagreement"} if include_disagreements else set())
    out: set[date] = set()
    with open(tsv, newline="") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            if row["kind"] in kinds and len(row["es_date"]) == 10:
                out.add(date.fromisoformat(row["es_date"]))
    return sorted(out)


def candidates(scraper, court: str, days: list[date]):
    """Yield stubs that passed the date-aware filter."""
    if court == "bvger":
        for d in days:
            yield from scraper._yield_window(d, d, 0, 0)
    elif court == "bstger":
        from scrapers.bstger import _random_user_id
        for d in days:
            offset, more = 0, True
            while more:
                docs, total, more, _ = scraper._fetch_window(d, 0, offset)
                uid = _random_user_id()
                for doc in docs:
                    stub = scraper._parse_document(doc, uid)
                    stub = scraper._stub_filter(stub) if stub else None
                    if stub:
                        yield stub
                offset += len(docs)
                if not docs or offset >= total:
                    break
    else:
        yield from scraper.discover_database()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--court", required=True, choices=COURTS)
    ap.add_argument("--dry-run", action="store_true",
                    help="seed (if needed) and list candidates; fetch nothing")
    ap.add_argument("--seed-only", action="store_true")
    ap.add_argument("--reseed", action="store_true")
    ap.add_argument("--max", type=int, default=300, help="safety cap on appended rulings")
    ap.add_argument("--state", default=str(REPO / "state"))
    ap.add_argument("--jsonl", action="append", default=None,
                    help="corpus shard(s) holding the court; the first one is "
                         "appended to (default output/decisions/<court>.jsonl)")
    ap.add_argument("--audit-tsv", default=None,
                    help="dates to probe for bvger/bstger (default: the 2026-11-16 audit)")
    ap.add_argument("--include-date-disagreements", action="store_true",
                    help="also probe dates where our held row's date matches none "
                         "of the listed ones (review those by hand first)")
    args = ap.parse_args()
    court = args.court

    scraper = make_scraper(court, Path(args.state))
    shards = [Path(p) for p in (args.jsonl or [REPO / "output" / "decisions" / f"{court}.jsonl"])]
    print(f"[{court}] state:   {scraper.state.state_file}")
    print(f"[{court}] sidecar: {scraper._dates_sidecar()}")
    print(f"[{court}] shards:  {', '.join(map(str, shards))}")
    known_n = len(getattr(scraper.state, "_seen", []) or [])
    if known_n == 0 and shards[0].exists() and shards[0].stat().st_size > 0:
        raise SystemExit(
            f"[{court}] state knows 0 ids but the corpus is non-empty — wrong "
            "state path; refusing (second rulings would be written under plain "
            "held ids)")
    seed_sidecar(scraper, court, shards, args.reseed)
    if args.seed_only:
        return 0

    scraper._load_known_dates()
    if scraper._known_dates is None:
        print(f"[{court}] sidecar not accepted (see log: half-seed guard / "
              "unreadable) — LEGACY mode, nothing to do")
        return 2

    days: list[date] = []
    if court in ("bvger", "bstger"):
        tsv = Path(args.audit_tsv) if args.audit_tsv else AUDIT_DIR / f"{court}.tsv"
        days = audit_dates(court, tsv, args.include_date_disagreements)
        print(f"[{court}] probing {len(days)} ruling dates from {tsv}")

    seconds, fresh = [], 0
    for stub in candidates(scraper, court, days):
        if court == "bpatger" or "-D" in stub["decision_id"]:
            seconds.append(stub)
        else:
            fresh += 1
    label = "detail pages to classify" if court == "bpatger" else "same-docket second rulings"
    print(f"[{court}] {len(seconds)} {label}; {fresh} unheld dockets left to "
          "the regular scraper")
    for s in seconds:
        print(f"    {s.get('docket_number', s.get('listing_text', ''))[:40]:40s} "
              f"{s.get('decision_date', '')}  → {s.get('decision_id', s.get('url'))}")
    if args.dry_run or not seconds:
        return 0

    from run_scraper import serialize_decision
    appended = skipped = 0
    with open(shards[0], "a", encoding="utf-8") as out:
        for s in seconds:
            if appended >= args.max:
                print(f"[{court}] --max {args.max} reached — rerun for the rest")
                break
            dec = scraper.fetch_decision(s)
            if dec is None or "-D" not in dec.decision_id:
                # bpatger: the detail page classified it as held (None) or as
                # an unheld docket (plain id) — not this script's job.
                skipped += 1
                continue
            out.write(serialize_decision(dec) + "\n")
            out.flush()
            scraper.state.mark_scraped(dec.decision_id)   # after durable write
            appended += 1
            print(f"    {dec.docket_number} {dec.decision_date} → {dec.decision_id} "
                  f"({len(dec.full_text or '')} chars)")
    print(f"[{court}] appended {appended} rulings to {shards[0]} ({skipped} skipped); "
          "they reach decisions.db with the next nightly build")
    return 0


if __name__ == "__main__":
    sys.exit(main())
