#!/usr/bin/env python3
"""
One-off re-key of the Basel-Stadt Gerichte shard to the portal's decision number
(2026-09-17).

Why.  rechtsprechung.gerichte.bs.ch lists several distinct decisions under one
primary docket: SB.2013.5 carries the 2014 judgment (AG.2014.40) *and* a 2020
cost-waiver decision (AG.2020.102).  ``scrapers/cantonal/bs_gerichte.py`` minted
``decision_id`` from the primary docket, so only the first-listed sibling of
every such group was ever stored: 373 of the portal's 11,005 rows were missing
(350 Appellationsgericht, 23 Sozialversicherungsgericht), and in 281 of the 331
groups the stored sibling was the later, usually trivial one.

The unique key is the secondary docket (``docket_number_2``: "AG.2014.40",
"SVG.2018.352"), the court's own decision number.  It is present on every held
row and unique across the whole portal (verified 2026-09-17 against a full
crawl).  The scraper now mints ids from it; this script brings the existing
rows onto the same scheme without re-fetching anything:

  * ``decision_id``  →  ``<court>_<docket_number_2>``
  * ``previous_decision_id`` = the old id (the build turns it into a row of
    ``decision_id_aliases`` so old ids, dashboard URLs and attest ledgers keep
    resolving to exactly the decision they pointed at)
  * ``docket_number`` (the cited case number) and everything else unchanged

The state file gets the new ids appended so the nightly scrape does not
re-fetch the re-keyed rows; the old ids stay (harmless).  The scrape then
fetches only the missing siblings and the three Zivilgericht rows.

Idempotent: a row already keyed on its docket_number_2 is left alone.  Refuses
to write if the new ids are not unique or a row lacks docket_number_2 (report
first, fix the data, rerun).

Usage (on the VPS, between the end of the 01:00 UTC scrape and the 03:30 full
rebuild — the 20:00 incremental seeks into shards by byte offset and must not
see a rewritten file; see runbooks/bs_gerichte_identity_2026-09.md):
    python3 scripts/migrate_bs_gerichte_ids.py --dry-run
    python3 scripts/migrate_bs_gerichte_ids.py
    python3 scripts/migrate_bs_gerichte_ids.py --shard <path> --state <path>
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tempfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from models import make_decision_id  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
log = logging.getLogger("migrate_bs_ids")

# Courts the direct scraper writes.  The two entscheidsuche leftovers under
# court='bs_gerichte' live in another shard and are deliberately untouched.
BS_DIRECT_COURTS = frozenset({
    "bs_appellationsgericht",
    "bs_sozialversicherungsgericht",
    "bs_zivilgericht",
})


def rekey_row(row: dict) -> str | None:
    """Re-key one row in place.  Returns a note when something changed or is
    wrong ("rekey OLD -> NEW", "no_docket2 ID"), None when already on the new
    scheme or not a BS direct-scraper row."""
    court = row.get("court") or ""
    if court not in BS_DIRECT_COURTS:
        return None
    d2 = (row.get("docket_number_2") or "").strip()
    old_id = row.get("decision_id") or ""
    if not d2:
        return f"no_docket2 {old_id}"
    new_id = make_decision_id(court, d2)
    if new_id == old_id:
        return None
    row["previous_decision_id"] = old_id
    row["decision_id"] = new_id
    return f"rekey {old_id} -> {new_id}"


def migrate(rows: list[dict]) -> tuple[Counter, list[str], list[str]]:
    """Pure transformation over the shard rows (mutated in place).

    Returns (stats, notes, errors).  ``errors`` is non-empty when the result
    must not be written: a row without docket_number_2, or new ids that are
    not unique across the shard."""
    stats: Counter = Counter()
    notes: list[str] = []
    errors: list[str] = []
    for row in rows:
        note = rekey_row(row)
        if note is None:
            stats["unchanged"] += 1
            continue
        notes.append(note)
        if note.startswith("no_docket2"):
            stats["no_docket2"] += 1
            errors.append(note)
        else:
            stats["rekeyed"] += 1
            stats[f"rekeyed:{row['court']}"] += 1

    seen: dict[str, int] = {}
    for i, row in enumerate(rows):
        did = row.get("decision_id") or ""
        if did in seen:
            errors.append(f"duplicate id after re-key: {did} (rows {seen[did]} and {i})")
            stats["id_collision"] += 1
        else:
            seen[did] = i
    return stats, notes, errors


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--shard", default="output/decisions/bs_gerichte.jsonl")
    ap.add_argument("--state", default="state/bs_gerichte.jsonl")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verbose", action="store_true", help="print every change")
    args = ap.parse_args()

    shard = Path(args.shard)
    if not shard.exists():
        log.error(f"shard not found: {shard}")
        return 2
    rows: list[dict] = []
    with open(shard, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    log.info(f"{shard}: {len(rows)} rows")

    stats, notes, errors = migrate(rows)
    for k, v in sorted(stats.items()):
        log.info(f"  {k}: {v}")
    if args.verbose:
        for n in notes:
            log.info("  " + n)
    if errors:
        for e in errors[:50]:
            log.error("  " + e)
        log.error(f"{len(errors)} problem(s) — nothing written")
        return 3
    if not stats["rekeyed"]:
        log.info("nothing to do — shard already on the docket_number_2 scheme")
        return 0
    if args.dry_run:
        log.info("dry run — nothing written")
        return 0

    fd, tmp = tempfile.mkstemp(dir=str(shard.parent), prefix=shard.name + ".", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        for r in rows:
            out.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, shard)
    log.info(f"wrote {shard} ({len(rows)} rows)")

    state = Path(args.state)
    known: set[str] = set()
    if state.exists():
        known = {ln.strip() for ln in open(state, encoding="utf-8") if ln.strip()}
    new_ids = [r["decision_id"] for r in rows if r.get("court") in BS_DIRECT_COURTS
               and r["decision_id"] not in known]
    if new_ids:
        with open(state, "a", encoding="utf-8") as f:
            f.writelines(did + "\n" for did in new_ids)
    log.info(f"state {state}: +{len(new_ids)} ids")
    return 0


if __name__ == "__main__":
    sys.exit(main())
