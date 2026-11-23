#!/usr/bin/env python3
"""
backfill_dates.py - Extract decision_date from full_text for rows where it's missing.

Patterns matched (first 2000 chars of full_text):
  DE: "Urteil vom 19. Dezember 2013", "Entscheid vom 5. Mai 2014"
  FR: "Arret du 26 janvier 2026", "Jugement du 19 novembre 2010"
  IT: "Sentenza del 15 marzo 2020", "4 dicembre 2013" (after city)

Usage:
  python3 scripts/backfill_dates.py --db output/decisions.db --dry-run
  python3 scripts/backfill_dates.py --db output/decisions.db
"""
from __future__ import annotations

import argparse
import logging
import re
import sqlite3
from datetime import date as _date

logger = logging.getLogger("backfill_dates")

_MONTHS_DE = {
    "januar": 1, "februar": 2, "maerz": 3, "märz": 3, "april": 4,
    "mai": 5, "juni": 6, "juli": 7, "august": 8,
    "september": 9, "oktober": 10, "november": 11, "dezember": 12,
}
_MONTHS_FR = {
    "janvier": 1, "février": 2, "fevrier": 2, "mars": 3, "avril": 4,
    "mai": 5, "juin": 6, "juillet": 7, "août": 8, "aout": 8,
    "septembre": 9, "octobre": 10, "novembre": 11, "décembre": 12, "decembre": 12,
}
_MONTHS_IT = {
    "gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4,
    "maggio": 5, "giugno": 6, "luglio": 7, "agosto": 8,
    "settembre": 9, "ottobre": 10, "novembre": 11, "dicembre": 12,
}
_ALL_MONTHS = {**_MONTHS_DE, **_MONTHS_FR, **_MONTHS_IT}

_MONTH_NAMES = "|".join(sorted(_ALL_MONTHS.keys(), key=len, reverse=True))

_DATE_RE = re.compile(
    rf"(?:(?:vom|du|del|le|dès\s+le)\s+)?(\d{{1,2}})\.?\s+({_MONTH_NAMES})\s+(\d{{4}})",
    re.IGNORECASE,
)
_ISO_RE = re.compile(r"\b((?:19|20)\d{2})-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])\b")
_DMY_RE = re.compile(r"\b(\d{1,2})\.(0[1-9]|1[0-2])\.(\d{4})\b")


def _real(year: int, month: int, day: int) -> bool:
    """A day the calendar has: no 31 April, no 29 February 2021."""
    try:
        _date(year, month, day)
    except ValueError:
        return False
    return True


def _extract_date(text: str) -> str | None:
    snippet = text[:2000]

    for m in _DATE_RE.finditer(snippet):
        day = int(m.group(1))
        month_name = m.group(2).lower()
        year = int(m.group(3))
        month = _ALL_MONTHS.get(month_name)
        if month and 1900 <= year <= 2030 and _real(year, month, day):
            return f"{year:04d}-{month:02d}-{day:02d}"

    for m in _DMY_RE.finditer(snippet):
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1900 <= year <= 2030 and _real(year, month, day):
            return f"{year:04d}-{month:02d}-{day:02d}"

    m = _ISO_RE.search(snippet)
    if m:
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1900 <= year <= 2030 and _real(year, month, day):
            return f"{year:04d}-{month:02d}-{day:02d}"

    return None


def backfill(db_path: str, *, dry_run: bool = False, batch_size: int = 1000) -> dict:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")

    total_missing = conn.execute(
        "SELECT COUNT(*) FROM decisions WHERE decision_date IS NULL OR decision_date = ''"
    ).fetchone()[0]
    logger.info(f"Total decisions with missing date: {total_missing:,}")

    if total_missing == 0:
        conn.close()
        return {"total_missing": 0, "fixed": 0, "unfixable": 0}

    last_id = ""
    fixed = 0
    unfixable = 0
    unfixable_by_court: dict[str, int] = {}
    updates: list[tuple[str, str]] = []

    while True:
        rows = conn.execute(
            """
            SELECT decision_id, court, substr(full_text, 1, 2000)
            FROM decisions
            WHERE (decision_date IS NULL OR decision_date = '')
              AND decision_id > ?
            ORDER BY decision_id
            LIMIT ?
            """,
            (last_id, batch_size),
        ).fetchall()

        if not rows:
            break

        for decision_id, court, text_start in rows:
            last_id = decision_id
            extracted = _extract_date(text_start or "")
            if extracted:
                updates.append((extracted, decision_id))
                fixed += 1
            else:
                unfixable += 1
                unfixable_by_court[court] = unfixable_by_court.get(court, 0) + 1

        if len(updates) >= batch_size and not dry_run:
            conn.executemany(
                "UPDATE decisions SET decision_date = ? WHERE decision_id = ?",
                updates,
            )
            conn.commit()
            logger.info(f"  Updated {fixed:,} / {total_missing:,} so far...")
            updates.clear()

    if updates and not dry_run:
        conn.executemany(
            "UPDATE decisions SET decision_date = ? WHERE decision_id = ?",
            updates,
        )
        conn.commit()

    conn.close()

    logger.info(f"Done: fixed={fixed:,}, unfixable={unfixable:,} out of {total_missing:,}")
    if unfixable_by_court:
        logger.info("Unfixable by court:")
        for court, count in sorted(unfixable_by_court.items(), key=lambda x: -x[1]):
            logger.info(f"  {court}: {count:,}")

    return {
        "total_missing": total_missing,
        "fixed": fixed,
        "unfixable": unfixable,
        "unfixable_by_court": unfixable_by_court,
    }


def main():
    parser = argparse.ArgumentParser(description="Backfill missing decision_date from full_text")
    parser.add_argument("--db", required=True, help="Path to decisions.db")
    parser.add_argument("--dry-run", action="store_true", help="Don't write updates")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    if args.dry_run:
        logger.info("DRY RUN -- no changes will be written")

    result = backfill(args.db, dry_run=args.dry_run)

    print(f"\nResults:")
    print(f"  Total missing: {result['total_missing']:,}")
    print(f"  Fixed:         {result['fixed']:,}")
    print(f"  Unfixable:     {result['unfixable']:,}")
    if result.get("unfixable_by_court"):
        print(f"\n  Unfixable by court:")
        for court, count in sorted(result["unfixable_by_court"].items(), key=lambda x: -x[1]):
            print(f"    {court}: {count:,}")


if __name__ == "__main__":
    main()
