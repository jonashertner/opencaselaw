#!/usr/bin/env python3
"""Re-date FINMA circulars already in finma_rs.jsonl by their front
page's last-amendment line (see _in_force_date and _archive_date).

The scraper only applies the rules to documents it fetches, and it does
not re-fetch a document whose pdf_url is unchanged, so the rows already
held need this one pass. No network: the stored body_text is the text the
rules read, and the stored dates stand in for the listed ones the scraper
would see. Appends one corrected record per changed doc_id, which is the
file's own contract (append-only, last line wins in build_practice_db).

    python3 scripts/backfill_finma_front_page_dates.py            # dry run
    python3 scripts/backfill_finma_front_page_dates.py --apply
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scrapers.practice.finma_rundschreiben import (  # noqa: E402
    _archive_date, _in_force_date,
)

DEFAULT = REPO / "output" / "practice" / "finma_rs.jsonl"


def _latest(path: Path) -> dict[str, dict]:
    latest: dict[str, dict] = {}
    with open(path) as f:
        for line in f:
            if line.strip():
                rec = json.loads(line)
                latest[rec["doc_id"]] = rec
    return latest


def corrections(latest: dict[str, dict]) -> list[dict]:
    """Every held record whose date the scraper's rules would now set
    differently, with that date, in doc_id order."""
    in_force: dict[tuple[str, str], str] = {}
    panel: dict[tuple[str, str], set[str]] = defaultdict(set)
    for rec in latest.values():
        key = (rec.get("doc_number", ""), rec.get("language", ""))
        if "_current_" in rec["doc_id"]:
            in_force[key] = rec.get("date", "")
        elif rec.get("date"):
            panel[key].add(rec["date"])
    now = datetime.now(timezone.utc).isoformat()
    out = []
    for rec in latest.values():
        key = (rec.get("doc_number", ""), rec.get("language", ""))
        listed, body = rec.get("date", ""), rec.get("body_text", "")
        if "_current_" in rec["doc_id"]:
            dated = _in_force_date(listed, body)
        else:
            dated = _archive_date(listed, rec.get("pdf_url", ""), body,
                                  sorted(panel[key]), in_force.get(key))
        if dated != listed:
            out.append({**rec, "date": dated, "scraped_at": now})
    return sorted(out, key=lambda r: r["doc_id"])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--jsonl", type=Path, default=DEFAULT)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    latest = _latest(args.jsonl)
    fixes = corrections(latest)
    for rec in fixes:
        print(f"{rec['doc_id']}: {latest[rec['doc_id']]['date']} -> {rec['date']}")
    print(f"{len(fixes)} records to re-date"
          + ("" if args.apply else " (dry run; --apply to append)"))
    if args.apply and fixes:
        with open(args.jsonl, "a") as f:
            for rec in fixes:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
