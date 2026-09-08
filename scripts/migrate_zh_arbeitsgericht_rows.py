#!/usr/bin/env python3
"""
One-off repair of the gerichte-zh.ch shard for the Arbeitsgericht / Mietgericht
Zürich rows (2026-09-04).

What it fixes, in one streaming pass over output/decisions/zh_gerichte.jsonl:

  1. Court code.  Rows whose Abteilung/Kammer (or Gericht) says
     "Arbeitsgericht" / "Mietgericht" are re-filed from the host
     Bezirksgericht code (or the generic zh_gerichte bucket) to
     zh_arbeitsgericht / zh_mietgericht, with the decision_id re-minted.
     Mirrors the precedence rule now in scrapers/cantonal/zh_gerichte.py.

  2. Duplicates.  The portal re-edits metadata (Geschäftsnummer "AH230041" →
     "AH230041-L", Gericht "Arbeitsgericht Zürich" → "Bezirksgericht Zürich"),
     and every re-edit minted a fresh docket-keyed id for the same document.
     The stable identity is the TYPO3 document id in external_id
     ("zh_gerichte_<doc_id>"); one row per external_id survives — the most
     recently scraped one (ties: longest full_text).

  3. Implausible dates.  ZH dockets encode the registration year
     ("AN230029" → 2023). A decision_date more than a year before that year is
     a text-recovery artefact (AGer-Z 2024 Nr. 6 carried 1937-12-03, the date
     of a treaty quoted in its headnote, after a one-off run of
     scripts/repair_decision_dates.py swapped it in). If publication_date is
     plausible it becomes the decision_date (that is where the portal's
     Entscheiddatum ended up); otherwise the date is NULLed. Never guessed.

The state file (state/zh_gerichte.jsonl) gets the new ids appended so the
nightly scrape does not re-fetch the re-filed rows; the old ids stay (harmless).

Usage (on the VPS, outside the build window, after `git merge --ff-only`):
    python3 scripts/migrate_zh_arbeitsgericht_rows.py --dry-run
    python3 scripts/migrate_zh_arbeitsgericht_rows.py
    python3 scripts/migrate_zh_arbeitsgericht_rows.py --shard <path> --state <path>
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from models import make_decision_id

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
log = logging.getLogger("migrate_zh_ager")

ZH_HOST_COURTS = {
    "zh_gerichte", "zh_bezirksgericht", "zh_bezirksgericht_zuerich",
    "zh_bezirksgericht_winterthur", "zh_bezirksgericht_uster",
    "zh_bezirksgericht_pfaeffikon", "zh_bezirksgericht_meilen",
    "zh_bezirksgericht_horgen", "zh_bezirksgericht_hinwil",
    "zh_bezirksgericht_dietikon", "zh_bezirksgericht_dielsdorf",
    "zh_bezirksgericht_buelach", "zh_bezirksgericht_andelfingen",
    "zh_bezirksgericht_affoltern", "zh_arbeitsgericht", "zh_mietgericht",
}
SPECIALISED = (("arbeitsgericht", "zh_arbeitsgericht"), ("mietgericht", "zh_mietgericht"))
_ZH_DOCKET_RE = re.compile(r"^[A-Z]{2}(\d{2})\d{4}(?:-[A-Z]\d?)?$")


def target_court(row: dict) -> tuple[str, str | None]:
    """(court, chamber) the row should carry under the fixed mapping."""
    court = row.get("court") or ""
    chamber = (row.get("chamber") or "").strip() or None
    if court not in ZH_HOST_COURTS:
        return court, chamber
    for keyword, code in SPECIALISED:
        if keyword in (chamber or "").lower():
            return code, (None if (chamber or "").lower() == keyword else chamber)
    return court, chamber


def docket_registration_year(docket: str | None, today: date | None = None) -> int | None:
    m = _ZH_DOCKET_RE.match((docket or "").strip())
    if not m:
        return None
    yy = int(m.group(1))
    cur = (today or date.today()).year
    year = 2000 + yy
    return year if year <= cur + 1 else 1900 + yy


def _year(iso: str | None) -> int | None:
    try:
        return int(str(iso)[:4]) if iso else None
    except ValueError:
        return None


def fix_dates(row: dict, today: date | None = None) -> str | None:
    """Apply rule 3 in place; return a short reason when something changed."""
    reg = docket_registration_year(row.get("docket_number"), today)
    dy = _year(row.get("decision_date"))
    if reg is None or dy is None or dy >= reg - 1:
        return None
    py = _year(row.get("publication_date"))
    if py is not None and py >= reg - 1:
        row["decision_date"], row["publication_date"] = row["publication_date"], None
        return f"decision_date {dy} < docket year {reg}: took publication_date"
    row["decision_date"] = None
    return f"decision_date {dy} < docket year {reg}: NULLed"


def migrate(rows: list[dict], today: date | None = None) -> tuple[list[dict], Counter, list[str]]:
    """Pure transformation: returns (kept rows, counters, human-readable notes)."""
    stats: Counter = Counter()
    notes: list[str] = []

    # 1. court re-filing
    for row in rows:
        court, chamber = target_court(row)
        if court != row.get("court") or chamber != row.get("chamber"):
            old_id = row["decision_id"]
            row["court"], row["chamber"] = court, chamber
            row["decision_id"] = make_decision_id(court, row.get("docket_number") or "")
            stats[f"refiled→{court}"] += 1
            notes.append(f"refile {old_id} → {row['decision_id']}")

    # 2. one row per TYPO3 document
    by_ext: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        ext = row.get("external_id")
        if ext:
            by_ext[ext].append(row)
    drop: set[int] = set()
    for ext, group in by_ext.items():
        if len(group) < 2:
            continue
        group.sort(key=lambda r: (r.get("scraped_at") or "", len(r.get("full_text") or "")), reverse=True)
        for r in group[1:]:
            drop.add(id(r))
            stats["dropped_duplicate"] += 1
            notes.append(f"drop {r['decision_id']} (dup of {group[0]['decision_id']}, {ext})")
    kept = [r for r in rows if id(r) not in drop]

    # 3. dates
    for row in kept:
        why = fix_dates(row, today)
        if why:
            stats["date_fixed"] += 1
            notes.append(f"date {row['decision_id']}: {why}")

    # decision_id must stay unique after re-filing
    seen: set[str] = set()
    final: list[dict] = []
    for r in kept:
        if r["decision_id"] in seen:
            stats["dropped_id_collision"] += 1
            notes.append(f"drop {r['decision_id']} (id collision after refile)")
            continue
        seen.add(r["decision_id"])
        final.append(r)
    return final, stats, notes


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--shard", default="output/decisions/zh_gerichte.jsonl")
    ap.add_argument("--state", default="state/zh_gerichte.jsonl")
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

    final, stats, notes = migrate(rows)
    for k, v in sorted(stats.items()):
        log.info(f"  {k}: {v}")
    if args.verbose:
        for n in notes:
            log.info("  " + n)
    log.info(f"rows after: {len(final)} (removed {len(rows) - len(final)})")

    if args.dry_run:
        log.info("dry run — nothing written")
        return 0

    fd, tmp = tempfile.mkstemp(dir=str(shard.parent), prefix=shard.name + ".", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        for r in final:
            out.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, shard)
    log.info(f"wrote {shard}")

    state = Path(args.state)
    known: set[str] = set()
    if state.exists():
        known = {ln.strip() for ln in open(state, encoding="utf-8") if ln.strip()}
    new_ids = [r["decision_id"] for r in final if r["decision_id"] not in known]
    if new_ids:
        with open(state, "a", encoding="utf-8") as f:
            f.writelines(did + "\n" for did in new_ids)
    log.info(f"state {state}: +{len(new_ids)} ids")
    return 0


if __name__ == "__main__":
    sys.exit(main())
