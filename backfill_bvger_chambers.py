"""Correct the stored `chamber` for court=bvger (Abteilung substring bug).

Wired into build_fts5 as a defensive post-ingest phase next to the bger
chamber correction (approved 2026-09-17): it runs on the .tmp DB during the
full rebuild and can never fail the build. The incremental quick_publish
path copies the previous DB and only inserts, so stored rows are corrected
at the next full rebuild, not on incremental nights.

The scraper-side bug (scrapers/bvger.py `_detect_abteilung`, fixed
2026-09-17): the panel string was matched with a plain substring test, so
"Abt. I" matched Abt. II/III/IV and "Abt. V" matched Abt. VI. Weblaw has been
the primary BVGer mode since Feb 2026 and chamber is written at scrape time,
so the corpus keeps the bad values. Measured on the production DB on
2026-09-16 23:25 UTC (bvger rows by docket letter x stored chamber):

  - 59,124 rows labelled by the bug's two outputs: docket letter B 5,565 /
    C 16,946 / D 29,105 -> "Abteilung I (...)", F 7,508 -> "Abteilung V
    (Asylrecht)". The B bucket includes the 634 "BVGE yyyy/n" collection
    rows labelled I (4,931 are ordinary B-dockets); the 177 BVGE rows
    labelled V had a panel string that genuinely said Abt. V.
  - 14,304 rows with NULL chamber and 520 with the bare docket letter
    (the Feb-2026 jurispub fallback batch and a May-2026 batch)
  - 21 rows storing the verbatim multilingual panel string

Rules, deliberately narrow (mirrors backfill_bger_chambers.py, GitHub #57):

  1. The decision's own header names the Abteilung ("Abteilung IV" /
     "Cour IV" / "Corte IV" within the first HEADER_CHARS characters of
     full_text). Exactly one numeral there -> that label. This is
     period-correct: pre-2016 F-dockets were decided by Abteilung III/IV
     (spot check 2026-09-17: F-3336/2015 -> III, F-7233/2015 -> IV), which
     the docket letter alone would mislabel as VI.
  2. Otherwise the ordinary docket series "A-1234/2025" .. "F-…" gives the
     Abteilung by its letter (A->I … F->VI). "BVGE 2007/10" is not in that
     series and is never mapped.
  3. No derivation -> the stored value is left alone (an unresolved row is
     counted, never guessed).

A row is updated only when the derived label differs from the stored one.
Non-bvger rows are never read. The serving DB is immutable and is never
written here: the intended call site is the .tmp DB during the nightly
rebuild, like the bger phase.

Dry run (read-only, prints what would change):
    python3 backfill_bvger_chambers.py --db output/decisions.db --dry-run
"""
from __future__ import annotations

import argparse
import logging
import re
import sqlite3
from collections import Counter

logger = logging.getLogger(__name__)

HEADER_CHARS = 1500
_HEADER = re.compile(r"\b(?:Abteilung|Cour|Corte)\s+(I{1,3}|IV|VI?)\b")
_DOCKET = re.compile(r"^([A-Fa-f])-\d")


def derive_abteilung(docket: str | None, head: str | None) -> tuple[str | None, str]:
    """Return (label or None, how) with how in {header, prefix, ambiguous, none}."""
    from scrapers.bvger import _PREFIX_MAP, BVGER_ABTEILUNGEN

    found = set(_HEADER.findall((head or "")[:HEADER_CHARS]))
    if len(found) == 1:
        return BVGER_ABTEILUNGEN[found.pop()], "header"
    m = _DOCKET.match(docket or "")
    if m:
        return BVGER_ABTEILUNGEN[_PREFIX_MAP[m.group(1).upper()]], "prefix"
    return None, ("ambiguous" if found else "none")


def apply_to_db(conn: sqlite3.Connection, dry_run: bool = False,
                stats: dict | None = None) -> tuple[int, int, int]:
    """Returns (n_relabelled, n_filled, n_unresolved). Commits unless dry_run.

    n_relabelled: stored value was a canonical Abteilung label, now another.
    n_filled:     stored value was NULL / bare letter / verbatim panel string.
    n_unresolved: nothing derivable and the stored value is not a canonical
                  label (left untouched).
    `stats`, if given, receives per-rule counters, a transition Counter and
    kept_canonical_no_evidence (bug-era labels the repair cannot correct).
    """
    from scrapers.bvger import BVGER_ABTEILUNGEN

    canonical = set(BVGER_ABTEILUNGEN.values())
    how_counter: Counter = Counter()
    transitions: Counter = Counter()
    fixes: list[tuple[str, str]] = []
    n_relabel = n_fill = n_unres = n_kept = 0
    cur = conn.execute(
        "SELECT decision_id, docket_number, chamber, substr(full_text, 1, ?) "
        "FROM decisions WHERE court='bvger'",
        (HEADER_CHARS,),
    )
    for did, docket, chamber, head in cur:
        new, how = derive_abteilung(docket, head)
        how_counter[how] += 1
        if new is None:
            if chamber in canonical:
                n_kept += 1  # possibly a bug-era label, no evidence to change it
            else:
                n_unres += 1
            continue
        if new == chamber:
            continue
        fixes.append((new, did))
        transitions[((chamber or "<NULL>")[:24], new[:13])] += 1
        if chamber in canonical:
            n_relabel += 1
        else:
            n_fill += 1
    if fixes and not dry_run:
        conn.executemany(
            "UPDATE decisions SET chamber=? WHERE decision_id=?", fixes)
    if not dry_run:
        conn.commit()
    if stats is not None:
        stats["how"] = dict(how_counter)
        stats["transitions"] = transitions
        stats["kept_canonical_no_evidence"] = n_kept
    return n_relabel, n_fill, n_unres


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    uri = f"file:{a.db}?mode=ro&immutable=1" if a.dry_run else f"file:{a.db}"
    conn = sqlite3.connect(uri, uri=True)
    stats: dict = {}
    try:
        n1, n2, n3 = apply_to_db(conn, dry_run=a.dry_run, stats=stats)
        mode = "would change" if a.dry_run else "changed"
        print(f"canonical label -> other label:      {mode} {n1}")
        print(f"NULL / bare letter / raw -> label:    {mode} {n2}")
        print(f"unresolved (left untouched):          {n3}")
        print(f"canonical label kept, no evidence:    {stats.get('kept_canonical_no_evidence')}")
        print(f"derivation: {stats.get('how')}")
        for (old, new), n in stats.get("transitions", Counter()).most_common(20):
            print(f"  {n:7d}  {old!r:28} -> {new!r}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
