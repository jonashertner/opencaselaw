"""Correct the stored `chamber` for court=bger (GitHub #57).

The scraper-side derivation was fixed in 343dd6c (PREFIX_TO_ABTEILUNG maps
7B/7F to the II. strafrechtliche Abteilung; the division-name scan got a
lookbehind so "I. Strafrechtliche Abteilung" can no longer match inside
"II. strafrechtliche Abteilung"; the Bundesstrafgericht entry no longer
exists to be picked up from quoted text). But chamber is written at SCRAPE
time and the build copies it, so the corpus kept the bad values:

  - ~3,420 new-series 7B/7F rows labelled "I. Strafrechtliche Abteilung"
  - ~1,295 rows whose chamber is the literal docket prefix ("7B")
  - 847 rows carrying a Federal Criminal Court body on a Federal Supreme
    Court decision

Rules, deliberately narrow:

  1. New-series 7B/7F (separator `_` or space — the OLD dotted series
     `7B.64/2000` is the Schuldbetreibungs- und Konkurskammer, correct as
     stored, and is never touched): chamber := II. Strafrechtliche
     Abteilung, overriding whatever is there. Within the new series the
     prefix is unambiguous by construction (the 2023 reorganisation created
     it), so this is safe to force.

  2. Every other bger row: touched ONLY if the stored value is provably
     broken — NULL/empty, the bare docket prefix, or a Federal Criminal
     Court body. Replacement comes from the decision's own full_text via
     chamber_from_text (period-correct: a 2010 row re-derives the division
     name of 2010, which today's prefix map cannot supply because the 2023
     reorganisation also renamed divisions). If the text names nothing,
     chamber becomes NULL — an honest gap beats a wrong label.

  3. Every BGG-era row (4A_594/2026, not 4P.253/1999) whose stored value
     names a BGer division OTHER than the one its docket series belongs to,
     and other than the one its own header names: re-derived. Since 2007
     each series belongs to exactly one division, so such a value can only
     have been lifted from the reasons — the pre-2026-11 text scan took the
     longest division name found ANYWHERE in the ruling (bger_4A_594_2026,
     suggestion 0003: header "I Corte di diritto civile", stored "II.
     Zivilrechtliche Abteilung" because the reasons discuss a recusal
     request against the President of the II Corte). A value that matches
     the header is kept even when it is not the series' division: the
     header is the court's own statement ("II. sozialrechtliche Abteilung
     (als strafrechtliche Abteilung)", 6B_819/2010).

Replacement, rules 2 and 3: the header's division (chamber_from_text) when
it is the series' division; for a BGG-era docket otherwise the series'
division under the name it carried on the decision date (division_for_
docket — structural, not a guess); for older dockets NULL.

Wired into build_fts5 as a defensive post-ingest phase (same pattern as the
canonical date correction): it runs on the .tmp DB during the nightly
rebuild and can never fail the build. The serving DB is immutable and is
never written here.

Dry run (read-only, prints what would change):
    python3 backfill_bger_chambers.py --db output/decisions.db --dry-run
"""
from __future__ import annotations

import argparse
import logging
import re
import sqlite3
from datetime import date

logger = logging.getLogger(__name__)

# New-series criminal-procedure dockets: 7B_311/2023, 7B 1008/2023, 7F_9/2024.
# The dot form (7B.64/2000) is the pre-2007 SchKK series — excluded.
_NEW_7BF = re.compile(r"^7[BF][_ ]")
_PREFIX = re.compile(r"^(\d{1,2}[A-Z])[_ ]")

_FOREIGN_MARKERS = (
    "bundesstrafgericht",
    "tribunal pénal fédéral",
    "tribunale penale federale",
    "beschwerdekammer",          # the BStGer body #57 pattern B imported
    "berufungskammer",
)


def _is_broken(chamber: str | None, docket: str) -> bool:
    if not chamber or not chamber.strip():
        return True
    c = chamber.strip()
    m = _PREFIX.match(docket or "")
    if m and c == m.group(1):            # chamber == bare prefix ("7B")
        return True
    cl = c.lower()
    return any(mark in cl for mark in _FOREIGN_MARKERS)


def _parse_date(value) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def _replacement(docket: str, decision_date, full_text: str) -> str | None:
    """Header division if consistent with the docket series, else the
    series' period-correct division (BGG dockets), else the header (older
    dockets, the pre-existing rule-2 behaviour) or None."""
    from scrapers.bger import (
        chamber_from_text,
        division_for_docket,
        division_key,
        docket_division_key,
    )

    header = chamber_from_text(full_text or "")
    series = docket_division_key(docket)
    if series is None:
        return header
    if header and division_key(header) == series:
        return header
    return division_for_docket(docket, _parse_date(decision_date))


def apply_to_db(conn: sqlite3.Connection, dry_run: bool = False):
    """Returns (n_7bf_forced, n_rederived, n_nulled). Commits unless dry_run.

    n_rederived counts rules 2 and 3 together (the build log line reads
    the tuple); the rule-3 share is logged here.
    """
    from scrapers.bger import (
        PREFIX_TO_ABTEILUNG,
        chamber_from_text,
        division_key,
        docket_division_key,
    )

    ii_straf = PREFIX_TO_ABTEILUNG["7B"][1]["de"]

    # Rule 1 — new-series 7B/7F. NB: `LIKE '7B_%'` would be wrong here —
    # `_` is a LIKE wildcard and matches the dot of the historic SchKK
    # series ('7B.64/2000'). Broad SQL prefilter, authoritative regex in
    # Python.
    rows = conn.execute(
        """SELECT decision_id, docket_number, chamber FROM decisions
           WHERE court='bger'
             AND (docket_number LIKE '7B%' OR docket_number LIKE '7F%')""",
    ).fetchall()
    force = [r[0] for r in rows
             if _NEW_7BF.match(r[1] or "") and (r[2] or "") != ii_straf]
    if force and not dry_run:
        conn.executemany(
            "UPDATE decisions SET chamber=? WHERE decision_id=?",
            [(ii_straf, did) for did in force],
        )

    # Rules 2 and 3 — candidates from the cheap leading columns only (no
    # full_text read), the authoritative checks in Python, full_text then
    # fetched per suspect (~50k es rows carrying a docket code, a few hundred
    # contradictions). New-series 7B/7F rows are rule
    # 1's, never re-derived here.
    broken: list[str] = []
    contradicting: list[str] = []
    for did, docket, chamber in conn.execute(
            "SELECT decision_id, docket_number, chamber FROM decisions "
            "WHERE court='bger'"):
        docket = docket or ""
        if _NEW_7BF.match(docket):
            continue
        if _is_broken(chamber, docket):
            broken.append(did)
            continue
        series = docket_division_key(docket)
        stored = division_key(chamber)
        if series and stored and stored != series:
            contradicting.append(did)

    rule3 = set(contradicting)
    n_rederived = n_nulled = n_rule3 = 0
    fixes: list[tuple[str | None, str]] = []
    for did in broken + contradicting:
        docket, chamber, decision_date, full_text = conn.execute(
            "SELECT docket_number, chamber, decision_date, full_text "
            "FROM decisions WHERE decision_id=?", (did,)).fetchone()
        if did in rule3:
            # Rule 3: the court's own header wins over the series.
            header = chamber_from_text(full_text or "")
            if header and division_key(header) == division_key(chamber):
                continue
        new = _replacement(docket or "", decision_date, full_text or "")
        if new == chamber:
            continue
        fixes.append((new, did))
        if new:
            n_rederived += 1
            if did in rule3:
                n_rule3 += 1
        else:
            n_nulled += 1
    if fixes and not dry_run:
        conn.executemany(
            "UPDATE decisions SET chamber=? WHERE decision_id=?", fixes)
    if not dry_run:
        conn.commit()
    logger.info("bger chambers: %d of the re-derived values contradicted "
                "their docket series and their own header (rule 3)", n_rule3)
    return len(force), n_rederived, n_nulled


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    uri = f"file:{a.db}?mode=ro&immutable=1" if a.dry_run else f"file:{a.db}"
    conn = sqlite3.connect(uri, uri=True)
    try:
        n1, n2, n3 = apply_to_db(conn, dry_run=a.dry_run)
        mode = "would change" if a.dry_run else "changed"
        print(f"7B/7F forced to II. Strafrechtliche Abteilung: {mode} {n1}")
        print(f"broken chambers re-derived from full_text:     {mode} {n2}")
        print(f"broken chambers with no derivable value -> NULL: {mode} {n3}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
