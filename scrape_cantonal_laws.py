#!/usr/bin/env python3
"""
scrape_cantonal_laws.py — Scrape cantonal laws from official portals.

Usage:
    python3 scrape_cantonal_laws.py                     # all implemented cantons
    python3 scrape_cantonal_laws.py --canton ZH          # single canton
    python3 scrape_cantonal_laws.py --canton ZH --max 5  # pilot: never promoted
    python3 scrape_cantonal_laws.py --list               # show available cantons
    python3 scrape_cantonal_laws.py --check-report DIR   # exit 1 if the last run
                                                         # left a canton un-refreshed

Each canton is written to {canton}.jsonl.part and promoted to {canton}.jsonl
only when the run finished with no errors and did not shrink the shard. A
portal outage therefore leaves the previous shard in place instead of
overwriting it with a truncated one, so the DB build can always run on what
is there: fresh shards for the cantons that succeeded, the prior snapshot
for the rest. The failure is still reported (exit status 1, the summary,
and _scrape_report.json), it just no longer costs the other cantons their
monthly refresh.
"""
from __future__ import annotations

import argparse
import importlib
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("scrape_cantonal_laws")

OUTPUT_DIR = Path("output/cantonal_laws_direct")
REPORT_NAME = "_scrape_report.json"
# A run with zero errors that still yields fewer laws than this share of the
# shard already on disk is not promoted: a portal that silently returns a
# short index looks like success to the fetch loop, and the build's override
# is wholesale (2026-08-19: a 3-law ZH shard displaced 1,374 laws).
MIN_KEEP_RATIO = 0.9
# --check-report treats a report older than this as "the scrape did not
# finish": a crashed or timed-out run leaves last month's report behind.
REPORT_MAX_AGE_H = 24


def _count_lines(path: Path) -> int:
    with path.open("rb") as f:
        return sum(1 for _ in f)


def scrape_canton(
    canton: str,
    output_dir: Path = OUTPUT_DIR,
    max_laws: int | None = None,
    delay: float = 0.5,
) -> dict:
    """Scrape a single canton and write JSONL.

    Writes {canton}.jsonl.part and promotes it to {canton}.jsonl with an
    atomic rename when the run is clean. Otherwise {canton}.jsonl is left
    exactly as it was and the partial output is parked as
    {canton}.jsonl.failed for inspection. Pilot runs (--max) are parked as
    {canton}.jsonl.pilot and never touch the shard. None of the parked
    names end in .jsonl, so the DB build never picks them up.
    """
    from scrapers.cantonal_laws import CANTONAL_LAW_SCRAPERS

    if canton not in CANTONAL_LAW_SCRAPERS:
        return {"canton": canton, "ok": False, "error": f"No scraper for {canton}"}

    module_name, class_name = CANTONAL_LAW_SCRAPERS[canton]
    mod = importlib.import_module(module_name)
    scraper_class = getattr(mod, class_name)

    # Instantiate scraper (LexWork takes canton as param)
    scraper = scraper_class(canton=canton)
    scraper.REQUEST_DELAY = delay

    output_dir.mkdir(parents=True, exist_ok=True)
    final_path = output_dir / f"{canton}.jsonl"
    part_path = output_dir / f"{canton}.jsonl.part"
    previous = _count_lines(final_path) if final_path.exists() else None

    start = time.time()
    fetched = 0
    skipped = 0
    errors = 0

    with open(part_path, "w", encoding="utf-8") as f:
        for i, stub in enumerate(scraper.enumerate_laws()):
            if max_laws and fetched >= max_laws:
                logger.info(f"{canton}: reached --max {max_laws}")
                break

            if stub.get("abrogated"):
                skipped += 1
                continue

            # LexWork: skip laws without structured content (concordats etc.)
            if "structured_document_id" in stub and not stub["structured_document_id"]:
                skipped += 1
                continue

            try:
                law = scraper.fetch_law(stub)
                if law and law.get("full_text"):
                    law["fetched_at"] = datetime.now(timezone.utc).isoformat()
                    f.write(json.dumps(law, ensure_ascii=False, default=str) + "\n")
                    fetched += 1
                    if fetched % 50 == 0:
                        elapsed = time.time() - start
                        logger.info(
                            f"{canton}: {fetched} laws fetched, "
                            f"{elapsed:.0f}s, {fetched / elapsed * 3600:.0f}/hour"
                        )
                else:
                    skipped += 1
            except Exception as e:
                errors += 1
                logger.error(f"{canton} {stub.get('sr_number', '?')}: {e}")
                if errors > 20:
                    logger.error(f"{canton}: too many errors, stopping")
                    break

    elapsed = time.time() - start
    portal_count = getattr(scraper, "portal_count", None)
    file_size = part_path.stat().st_size / 1024 / 1024

    coverage = f"{fetched}"
    if portal_count:
        coverage = f"{fetched}/{portal_count}"

    logger.info(
        f"{canton}: done. {coverage} laws, {skipped} skipped, "
        f"{errors} errors, {elapsed:.0f}s, {file_size:.1f} MB"
    )

    # Promotion. `previous` is the shard the build is currently using; it is
    # only replaced by a run that is at least as good.
    floor = int(previous * MIN_KEEP_RATIO) if previous else 1
    promoted = False
    kept_previous = False
    if max_laws:
        parked = output_dir / f"{canton}.jsonl.pilot"
        os.replace(part_path, parked)
        ok = errors == 0
        logger.info(f"{canton}: pilot run parked at {parked}; shard untouched")
    elif errors == 0 and fetched >= floor:
        os.replace(part_path, final_path)
        promoted = ok = True
    else:
        parked = output_dir / f"{canton}.jsonl.failed"
        os.replace(part_path, parked)
        ok = False
        if previous is not None:
            kept_previous = True
            prev_date = datetime.fromtimestamp(
                final_path.stat().st_mtime, timezone.utc).strftime("%Y-%m-%d")
            logger.error(
                f"{canton}: NOT promoted ({errors} errors, {fetched} fetched, "
                f"floor {floor}); previous shard kept ({previous} laws, "
                f"{prev_date}); partial output parked at {parked}"
            )
        else:
            logger.error(
                f"{canton}: NOT promoted ({errors} errors, {fetched} fetched) "
                f"and no previous shard; the build will use the LexFind fallback"
            )

    return {
        "canton": canton,
        "ok": ok,
        "promoted": promoted,
        "kept_previous": kept_previous,
        "fetched": fetched,
        "skipped": skipped,
        "errors": errors,
        "previous": previous,
        "portal_count": portal_count,
        "duration": elapsed,
        "file_size_mb": round(file_size, 1),
    }


def write_report(output_dir: Path, results: list[dict]) -> Path:
    path = output_dir / REPORT_NAME
    report = {
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "output_dir": str(output_dir),
        "cantons": {r["canton"]: r for r in results},
    }
    tmp = path.with_suffix(".json.part")
    tmp.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    os.replace(tmp, path)
    return path


def check_report(output_dir: Path) -> int:
    """Exit status for the last run: 0 all refreshed, 1 a canton was not,
    2 no fresh report (the scrape never finished)."""
    path = output_dir / REPORT_NAME
    if not path.exists():
        print(f"no scrape report at {path}")
        return 2
    report = json.loads(path.read_text(encoding="utf-8"))
    finished = datetime.fromisoformat(report["finished_at"])
    age_h = (datetime.now(timezone.utc) - finished).total_seconds() / 3600
    if age_h > REPORT_MAX_AGE_H:
        print(f"scrape report is {age_h:.0f}h old ({report['finished_at']}): "
              f"the last scrape did not finish")
        return 2
    failed = []
    for canton, r in sorted(report["cantons"].items()):
        if r.get("ok"):
            print(f"  [OK] {canton}: {r.get('fetched', 0)} laws")
            continue
        failed.append(canton)
        if r.get("kept_previous"):
            print(f"  [FAIL] {canton}: {r.get('fetched', 0)} fetched, "
                  f"{r.get('errors', 0)} errors; previous shard kept "
                  f"({r.get('previous')} laws)")
        else:
            print(f"  [FAIL] {canton}: {r.get('fetched', 0)} fetched, "
                  f"{r.get('errors', 0)} errors; no shard")
    if failed:
        print(f"{len(failed)} canton(s) not refreshed: {', '.join(failed)}")
        return 1
    print(f"all {len(report['cantons'])} cantons refreshed at {report['finished_at']}")
    return 0


def main():
    from scrapers.cantonal_laws import CANTONAL_LAW_SCRAPERS

    parser = argparse.ArgumentParser(description="Scrape cantonal laws from official portals")
    parser.add_argument("--canton", type=str, help="Single canton to scrape (e.g., ZH)")
    parser.add_argument("--max", type=int, help="Max laws per canton (pilot; shard is not replaced)")
    parser.add_argument("--delay", type=float, default=0.5, help="Request delay in seconds")
    parser.add_argument("--list", action="store_true", help="List available cantons")
    parser.add_argument("--output", type=str, default=str(OUTPUT_DIR))
    parser.add_argument("--check-report", metavar="DIR",
                        help="Do not scrape; exit 1 if the last run in DIR left a "
                             "canton un-refreshed, 2 if there is no fresh report")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    for noisy in ("urllib3", "requests"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    if args.check_report:
        sys.exit(check_report(Path(args.check_report)))

    if args.list:
        for canton in sorted(CANTONAL_LAW_SCRAPERS.keys()):
            mod, cls = CANTONAL_LAW_SCRAPERS[canton]
            print(f"  {canton}: {mod}.{cls}")
        return

    output_dir = Path(args.output)

    if args.canton:
        cantons = [args.canton.upper()]
        if cantons[0] not in CANTONAL_LAW_SCRAPERS:
            print(f"No scraper for {cantons[0]}. Available: {sorted(CANTONAL_LAW_SCRAPERS.keys())}")
            sys.exit(1)
    else:
        cantons = sorted(CANTONAL_LAW_SCRAPERS.keys())

    results = []
    for canton in cantons:
        result = scrape_canton(canton, output_dir, args.max, args.delay)
        results.append(result)

    # Summary
    ok = sum(1 for r in results if r["ok"])
    total_laws = sum(r.get("fetched", 0) for r in results)
    print(f"\nDone: {ok}/{len(results)} cantons, {total_laws} laws total")
    for r in results:
        status = "OK" if r["ok"] else "FAIL"
        note = ""
        if r.get("kept_previous"):
            note = f" — previous shard kept ({r['previous']} laws)"
        elif not r["ok"] and not r.get("promoted") and not args.max:
            note = " — no shard, LexFind fallback"
        print(f"  [{status}] {r['canton']}: {r.get('fetched', 0)} laws, "
              f"{r.get('duration', 0):.0f}s{note}")

    if not args.max:
        print(f"report: {write_report(output_dir, results)}")

    if any(not r["ok"] for r in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
