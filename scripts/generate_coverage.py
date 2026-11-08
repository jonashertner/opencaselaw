#!/usr/bin/env python3
"""generate_coverage.py — the denominators table (backlog P3.1).

Selection bias is the #1 credibility attack on decision-derived statistics:
"how many decisions do you have" means nothing without "out of how many".
This publishes, per court: our corpus counts (total + by decision year),
the portal's own total where the daily scrape can see one
(scraper_health.json carries our_count/portal_count/gap for all scrapers),
and a curated NOTE explaining every known structural gap — distinguishing
"not published by the court" from "not captured by us".

Output: docs/coverage.json (committed by the nightly like stats.json).
Never raises out of main(); a coverage hiccup must not fail a publish.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))


def build_coverage(db_path: Path, health_path: Path, notes_path: Path) -> dict:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True)

    by_court: dict[str, dict] = {}
    for court, year, n in conn.execute(
        "SELECT court, substr(decision_date, 1, 4), COUNT(*) FROM decisions "
        "GROUP BY court, substr(decision_date, 1, 4)"
    ):
        c = by_court.setdefault(court, {"total": 0, "by_year": {}, "undated": 0})
        c["total"] += n
        if year and year.isdigit():
            c["by_year"][year] = c["by_year"].get(year, 0) + n
        else:
            c["undated"] += n

    health = {}
    checked_at = None
    if health_path.exists():
        h = json.loads(health_path.read_text())
        checked_at = h.get("run_at")
        health = h.get("scrapers", {}) or {}

    notes = {}
    if notes_path.exists():
        notes = json.loads(notes_path.read_text())

    courts = []
    for court in sorted(by_court):
        c = by_court[court]
        years = sorted(c["by_year"])
        s = health.get(court) or {}
        courts.append({
            "court": court,
            "total": c["total"],
            "first_year": years[0] if years else None,
            "last_year": years[-1] if years else None,
            "undated": c["undated"],
            "by_year": c["by_year"],
            "portal_total": s.get("portal_count"),
            "our_count_at_check": s.get("our_count"),
            "gap": s.get("gap"),
            "portal_checked_at": checked_at if s else None,
            "note": notes.get(court),
        })

    return {
        "schema": "coverage/v1",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "methodology": (
            "total/by_year = decisions in the served corpus by decision year. "
            "portal_total = the source portal's own result count where the "
            "daily scrape can observe one (not every portal exposes totals). "
            "gap = portal_total - our_count at check time; a gap is NOT "
            "necessarily missing data — see the per-court note. Publication "
            "practice varies enormously between courts: absence of a decision "
            "here usually means the court never published it."
        ),
        "totals": {
            "decisions": sum(c["total"] for c in by_court.values()),
            "courts": len(by_court),
            "courts_with_portal_total": sum(1 for x in courts if x["portal_total"]),
            "courts_with_note": sum(1 for x in courts if x["note"]),
        },
        "courts": courts,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(REPO / "output" / "decisions.db"))
    ap.add_argument("--health", default=str(REPO / "logs" / "scraper_health.json"))
    ap.add_argument("--notes", default=str(REPO / "docs" / "coverage_notes.json"))
    ap.add_argument("--output", default=str(REPO / "docs" / "coverage.json"))
    ap.add_argument("--stats", default=str(REPO / "docs" / "stats.json"))
    ap.add_argument("--status-output", default=str(REPO / "docs" / "sources" / "status.json"),
                    help="per-source status for /coverage/ ('' to skip)")
    args = ap.parse_args()
    try:
        payload = build_coverage(Path(args.db), Path(args.health), Path(args.notes))
        out = Path(args.output)
        tmp = out.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1))
        tmp.replace(out)
        print(f"coverage.json: {payload['totals']['courts']} courts, "
              f"{payload['totals']['courts_with_portal_total']} with portal totals, "
              f"{payload['totals']['courts_with_note']} with notes")
    except Exception as e:  # noqa: BLE001 — never fail a publish over coverage
        print(f"generate_coverage failed (non-fatal): {e}", file=sys.stderr)
        return 0
    if args.status_output:
        # Per-source status for the public /coverage/ page (scripts/source_status.py):
        # freshness, cadence and intake per court code, derived from this payload,
        # docs/stats.json (step 5a) and the scraper health files. Reads no
        # decisions.db. Isolated: its failure leaves coverage.json untouched.
        try:
            import source_status
            sp = source_status.generate(
                REPO, payload, Path(args.stats), Path(args.health).parent,
                REPO / "docs" / "sources" / "registry.json",
                REPO / "docs" / "intake_expected_stale.json",
                Path(args.notes), Path(args.status_output), Path(args.db).parent)
            print(source_status.summary_line(sp))
        except Exception as e:  # noqa: BLE001
            print(f"source_status failed (non-fatal): {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
