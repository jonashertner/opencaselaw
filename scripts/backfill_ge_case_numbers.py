#!/usr/bin/env python3
"""Store the Geneva case number (C/16444/2023, A/3880/2025, P/…) as
docket_number_2 on the held ge_gerichte rows.

Background (runbooks/tg_ge_sh_repair_2026-11.md): justice.ge.ch prints two
numbers per ruling, the ruling number (ACJC/1978/2026, the title link, our
docket_number and decision_id) and the case number of the procedure (the
result block's flag). The scraper read the flag only for a "Fiche N" the portal
no longer prints, so docket_number_2 is empty on every row (94,492 direct rows
on the 2026-11-07 mirror). The case number is what the Federal Supreme Court
and the other Geneva courts cite and what appeal-chain linking needs. The
scraper now stores it (GEGerichteScraper._parse_block, parse_case_number);
this script backfills the held rows from the same official listing.

Two stages:

  --crawl     page through every section and year of the official listing
              (same queries as the scraper, page_size 500, 1.5 s between
              requests) and write ruling number -> case number lines to --map
              (JSONL: decision_id, docket_number, case_number, section, show_id).
  (default)   stream each --shard and set docket_number_2 on a row whose
              decision_id the map holds, when the row has none. A decision_id
              the listing gives two different case numbers for is left alone
              and counted ("conflict").

Only docket_number_2 changes. ge_gerichte is not in
build_fts5.DECISION_NUMBER_COURTS, so the dedup key (court, docket_number,
date) of every row is unchanged; the script refuses to run otherwise.

DRY RUN by default. --apply hard-links <shard>.bak-ge-case-<UTC date> beside
the shard, writes <shard>.tmp (neither matches the build's *.jsonl glob),
checks the line count and that the shard did not change while it ran, and
swaps the new file in atomically. Run it outside the build window with no
ge_gerichte scrape appending (the shard is 2.7 GB: allow ~10 min of I/O).

Usage:
  python3 scripts/backfill_ge_case_numbers.py --crawl --map /tmp/ge_case_map.jsonl
  python3 scripts/backfill_ge_case_numbers.py --map /tmp/ge_case_map.jsonl
  python3 scripts/backfill_ge_case_numbers.py --map /tmp/ge_case_map.jsonl --apply
"""
from __future__ import annotations

import argparse
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

from build_fts5 import DECISION_NUMBER_COURTS  # noqa: E402
from base_scraper import BaseScraper  # noqa: E402
from scrapers.cantonal.ge_gerichte import (  # noqa: E402
    BASE_URL,
    PAGE_SIZE,
    SECTIONS,
    START_YEAR,
    GEGerichteScraper,
)

COURT = "ge_gerichte"
DEFAULT_SHARD = REPO / "output" / "decisions" / "ge_gerichte.jsonl"
CRAWL_DELAY = 1.5
_SHOW_ID_RE = re.compile(r"/show/(\d+)")


def crawl(map_path: Path, sections: list[str], first_year: int, last_year: int) -> Counter:
    """Write every listed (ruling number, case number) to map_path. Resumable:
    a section-year already in the map is skipped."""
    import requests

    parser = GEGerichteScraper.__new__(GEGerichteScraper)  # _parse_result_page needs no state
    session = requests.Session()
    session.headers["User-Agent"] = BaseScraper.USER_AGENT
    done: set[tuple[str, int]] = set()
    if map_path.exists():
        with open(map_path, encoding="utf-8") as fh:
            for line in fh:
                rec = json.loads(line)
                if rec.get("done"):
                    done.add((rec["section"], rec["year"]))
    stats: Counter = Counter()
    with open(map_path, "a", encoding="utf-8") as out:
        for section in sections:
            for year in range(last_year, first_year - 1, -1):
                if (section, year) in done:
                    stats["section_years_cached"] += 1
                    continue
                url = f"{BASE_URL}/apps/decis/fr/{section}/search"
                params = {"search_meta": f"dt_decision:[01.01.{year} TO 31.12.{year}]",
                          "sort_by": "date", "page_size": str(PAGE_SIZE), "page": "1"}
                page, total, ok = 1, None, True
                while True:
                    params["page"] = str(page)
                    r, err = None, None
                    for attempt in range(4):  # the portal resets a connection now and then
                        time.sleep(CRAWL_DELAY * (1 + 3 * attempt))
                        try:
                            r = session.get(url, params=params, timeout=60)
                            r.raise_for_status()
                            break
                        except Exception as e:
                            r, err = None, e
                    if r is None:
                        print(f"  {section} {year} p{page}: {err}", file=sys.stderr)
                        stats["failed_pages"] += 1
                        ok = False
                        break
                    stats["pages"] += 1
                    if total is None:
                        total = parser._parse_total(r.text) or 0
                    for stub in parser._parse_result_page(r.text, section):
                        m = _SHOW_ID_RE.search(stub.get("detail_url") or "")
                        out.write(json.dumps({
                            "decision_id": stub["decision_id"],
                            "docket_number": stub["docket_number"],
                            "case_number": stub.get("case_number"),
                            "section": section,
                            "year": year,
                            "show_id": m.group(1) if m else None,
                        }, ensure_ascii=False) + "\n")
                        stats["listed"] += 1
                        stats["with_case_number"] += bool(stub.get("case_number"))
                    if page * PAGE_SIZE >= total:
                        break
                    page += 1
                if ok:
                    out.write(json.dumps({"done": True, "section": section, "year": year,
                                          "total": total}) + "\n")
                out.flush()
    return stats


def load_map(map_path: Path) -> tuple[dict[str, str], set[str]]:
    """decision_id -> case number, and the decision_ids listed with two
    different case numbers."""
    seen: dict[str, str] = {}
    conflicts: set[str] = set()
    with open(map_path, encoding="utf-8") as fh:
        for line in fh:
            rec = json.loads(line)
            if rec.get("done") or not rec.get("case_number"):
                continue
            did, case = rec["decision_id"], rec["case_number"]
            if did in seen and seen[did] != case:
                conflicts.add(did)
            seen.setdefault(did, case)
    for did in conflicts:
        seen.pop(did, None)
    return seen, conflicts


def run(shard: Path, mapping: dict[str, str], conflicts: set[str], apply: bool,
        examples: int = 0) -> Counter:
    if COURT in DECISION_NUMBER_COURTS:
        raise SystemExit(f"refusing: {COURT} is in DECISION_NUMBER_COURTS, so docket_number_2 "
                         "is its dedup key and filling it would change canonical keys")
    st = os.stat(shard)
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
                if not isinstance(rec, dict) or rec.get("court") != COURT:
                    stats["passed_through"] += 1
                    if fout:
                        fout.write(raw)
                    continue
                did = rec.get("decision_id")
                if (rec.get("docket_number_2") or "").strip():
                    stats["already_filled"] += 1
                    case = None
                elif did in conflicts:
                    stats["conflict"] += 1
                    case = None
                else:
                    case = mapping.get(did)
                    stats["filled" if case else "not_in_listing"] += 1
                if not case:
                    if fout:
                        fout.write(raw)
                    continue
                new = dict(rec)
                new["docket_number_2"] = case
                for k in rec:
                    if k != "docket_number_2" and new[k] != rec[k]:
                        raise SystemExit(f"refusing: {did} would change {k}")
                year = str(rec.get("decision_date") or "")[:4]
                stats[f"filled_year:{year if year >= '2024' else ('2000-2023' if year >= '2000' else 'pre-2000')}"] += 1
                if shown < examples:
                    shown += 1
                    print(f"  {did}: docket_number_2 = {case!r}", file=sys.stderr)
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
            now = os.stat(shard)
            if (now.st_size, now.st_mtime_ns) != (st.st_size, st.st_mtime_ns):
                raise SystemExit("refusing: the shard changed while the backfill ran (a scrape?)")
            stamp = time.strftime("%Y%m%d", time.gmtime())
            backup = shard.with_name(f"{shard.name}.bak-ge-case-{stamp}")
            if backup.exists():
                raise SystemExit(f"refusing to overwrite existing backup {backup}")
            os.link(shard, backup)
            os.chmod(tmp, st.st_mode & 0o7777)
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
    ap.add_argument("--map", type=Path, required=True, help="ruling -> case number map (JSONL)")
    ap.add_argument("--crawl", action="store_true", help="crawl the listing into --map, then stop")
    ap.add_argument("--sections", default=",".join(SECTIONS))
    ap.add_argument("--first-year", type=int, default=START_YEAR)
    ap.add_argument("--last-year", type=int, default=date.today().year)
    ap.add_argument("--shard", type=Path, action="append",
                    help=f"shard(s) to fill (default {DEFAULT_SHARD})")
    ap.add_argument("--apply", action="store_true", help="write (default: dry run)")
    ap.add_argument("--examples", type=int, default=0)
    args = ap.parse_args(argv)
    if args.crawl:
        stats = crawl(args.map, [s for s in args.sections.split(",") if s],
                      args.first_year, args.last_year)
        print(json.dumps(dict(stats), indent=1))
        return 0
    mapping, conflicts = load_map(args.map)
    print(f"map: {len(mapping)} decision_ids with a case number, {len(conflicts)} conflicting",
          file=sys.stderr)
    for shard in args.shard or [DEFAULT_SHARD]:
        if not shard.exists():
            print(f"shard not found: {shard}", file=sys.stderr)
            return 2
        print(("APPLY" if args.apply else "DRY RUN") + f": {shard}", file=sys.stderr)
        print(json.dumps(dict(sorted(run(shard, mapping, conflicts, args.apply,
                                         args.examples).items())), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
