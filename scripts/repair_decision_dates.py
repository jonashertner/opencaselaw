#!/usr/bin/env python3
"""
Repair decision dates in JSONL files.

For courts where metadata decision_date actually contains the publication date,
extracts the real decision date from full text and swaps:
  - decision_date → extracted date
  - publication_date → original metadata date (was the publication date)

Uses streaming JSONL pattern: read line-by-line, write to temp file, atomic replace.

Usage:
    python3 scripts/repair_decision_dates.py --dry-run
    python3 scripts/repair_decision_dates.py --court fr_gerichte
    python3 scripts/repair_decision_dates.py --all
    python3 scripts/repair_decision_dates.py --all --min-confidence medium
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tempfile
from collections import Counter
from datetime import date
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.extract_decision_date import (
    extract_decision_date,
    _parse_iso_date,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("repair_dates")

# ============================================================
# Courts to fix and their expected patterns
# ============================================================

# Courts where metadata decision_date is actually the publication date.
# These are safe to fix: move decision_date → publication_date,
# set decision_date to extracted date from full text.
COURTS_PUBLICATION_DATE_AS_DECISION = {
    # Tribuna platform courts (confirmed publication date in metadata)
    "fr_gerichte",
    "vd_gerichte",
    "ti_gerichte",
    "gr_gerichte",
    "be_verwaltungsgericht",
    "be_zivilstraf",
    "be_anwaltsaufsicht",
    "nw_gerichte",
    "so_gerichte",
    "gl_gerichte",           # some have placeholder 2026-02-13
    "tg_gerichte",
    # BGE/ECHR import date issues
    "bge",
    "bge_egmr",
    # Other courts with confirmed issues
    "ow_gerichte",           # all dated 2015-11-26 (import date)
    "lu_gerichte",
    "zh_sozialversicherungsgericht",
    "zh_steuerrekursgericht",
    "zh_verwaltungsgericht",
    "zh_baurekursgericht",
    "zh_gerichte",
    "bl_gerichte",
    "bs_gerichte",
    "ne_gerichte",
    "ur_gerichte",
    "ai_gerichte",
    "ar_gerichte",
    # Regulatory
    "postcom",
    "elcom",
    "comcom",
    "edoeb",
    "finma",
    "weko",
    "ubi",
    # Federal
    "bger",
    "bstger",
    "bvger",
    "bpatger",
    "ch_bundesrat",
    # Other
    "hudoc_ch",
    "emark",
    "bge_historical",
    "ta_sst",
    "vs_gerichte",
    "ju_gerichte",
    "sg_publikationen",
    # Entscheidsuche courts (es_* prefix stripped during processing)
}


# Shards this pass is known to damage: it reads the first "Urteil/Entscheid
# vom <date>" of the text, which in these is a lower court's, an appeal's or a
# neighbouring ruling's date. Their dates were restored from the rulings' own
# headers (restore_bge_dates_from_urteilskopf.py, segment_bge_historical.py,
# restore_sg_dates.py); a rerun would undo that (user report 2026-10-07).
NEVER_REPAIR_SHARDS = frozenset({
    "bge", "es_bge", "bge_historical", "sg_publikationen", "es_sg_gerichte",
})


def repair_jsonl(
    jsonl_path: Path,
    dry_run: bool = False,
    min_confidence: str = "medium",
    max_entries: int = 0,
) -> dict:
    """
    Repair decision dates in a single JSONL file.

    Returns stats dict.
    """
    court_name = jsonl_path.stem
    stats = Counter()
    fixed_examples: list[dict] = []

    # Determine output
    if dry_run:
        fout = None
    else:
        fd, tmp_path = tempfile.mkstemp(
            suffix=".jsonl",
            dir=str(jsonl_path.parent),
        )
        fout = os.fdopen(fd, "w", encoding="utf-8")

    confidence_levels = {"high": 3, "medium": 2, "low": 1, "none": 0}
    min_conf_level = confidence_levels.get(min_confidence, 2)

    count = 0
    try:
        for line in open(jsonl_path, encoding="utf-8"):
            stripped = line.strip()
            if not stripped:
                if fout:
                    fout.write(line)
                continue

            try:
                obj = json.loads(stripped)
            except json.JSONDecodeError:
                if fout:
                    fout.write(line)
                stats["json_error"] += 1
                continue

            full_text = obj.get("full_text", "") or ""
            lang = obj.get("language", "") or ""
            meta_date_str = obj.get("decision_date", "") or ""
            meta_date = _parse_iso_date(meta_date_str)

            # If max_entries reached, just pass through remaining lines
            if max_entries and count >= max_entries:
                if fout:
                    fout.write(line)
                count += 1
                continue

            # Skip entries with very short text (can't extract date)
            if len(full_text) < 100:
                if fout:
                    fout.write(line)
                stats["too_short"] += 1
                continue

            # Extract decision date from full text
            result = extract_decision_date(full_text, language=lang, metadata_date=meta_date)

            if result.category == "MATCH":
                # Date already correct
                if fout:
                    fout.write(line)
                stats["match"] += 1

            elif result.category in ("MISMATCH_FIXABLE", "NULL_METADATA_FIXABLE"):
                conf_level = confidence_levels.get(result.confidence, 0)
                if conf_level < min_conf_level:
                    # Confidence too low, skip
                    if fout:
                        fout.write(line)
                    stats["low_confidence"] += 1
                    continue

                extracted = result.extracted_date

                # Sanity check: extracted date should be reasonable
                if extracted and (extracted.year < 1900 or extracted > date.today()):
                    if fout:
                        fout.write(line)
                    stats["invalid_year"] += 1
                    continue

                # Apply fix
                if not dry_run:
                    # Save original date as publication_date (if it was a pub date)
                    if meta_date and result.category == "MISMATCH_FIXABLE":
                        obj["publication_date"] = meta_date_str
                    # Set decision_date to extracted date
                    obj["decision_date"] = extracted.isoformat() if extracted else None
                    # Add extraction metadata
                    obj["date_extraction"] = result.to_dict()
                    fout.write(json.dumps(obj, ensure_ascii=False) + "\n")

                stats["fixed"] += 1
                if len(fixed_examples) < 5:
                    fixed_examples.append({
                        "id": obj.get("decision_id", ""),
                        "old": meta_date_str,
                        "new": extracted.isoformat() if extracted else None,
                        "method": result.method,
                        "confidence": result.confidence,
                    })

            else:
                # NO_DATE_EXTRACTED, NULL_METADATA_DATE, MISMATCH_AMBIGUOUS
                if fout:
                    fout.write(line)
                stats[result.category.lower()] += 1

            count += 1
            if count % 5000 == 0:
                log.info("  [%s] %d lines, %d fixed...", court_name, count, stats["fixed"])

    except Exception as e:
        log.error("Error processing %s at line %d: %s", jsonl_path, count, e)
        if fout:
            fout.close()
            os.unlink(tmp_path)
        raise

    if fout:
        # Write any remaining lines if we broke out early
        fout.close()

        if stats["fixed"] > 0:
            # Atomic replace
            os.replace(tmp_path, str(jsonl_path))
            log.info("Replaced %s (%d entries repaired)", jsonl_path.name, stats["fixed"])
        else:
            os.unlink(tmp_path)
            log.info("%s: no changes needed", jsonl_path.name)

    return {
        "court": court_name,
        "total": count,
        "stats": dict(stats),
        "examples": fixed_examples,
    }


def main():
    parser = argparse.ArgumentParser(description="Repair decision dates in JSONL files")
    parser.add_argument("--court", type=str, help="Single court to repair")
    parser.add_argument("--all", action="store_true", help="Repair all courts")
    parser.add_argument("--dry-run", action="store_true", help="Count fixes without modifying files")
    parser.add_argument("--min-confidence", choices=["high", "medium", "low"], default="medium",
                        help="Minimum confidence level for fixes (default: medium)")
    parser.add_argument("--max", type=int, default=0, help="Max entries per file (testing)")
    parser.add_argument("--decisions-dir", type=Path,
                        default=Path(os.environ.get("DECISIONS_DIR", "output/decisions")))
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if not args.court and not args.all:
        parser.print_help()
        sys.exit(1)

    results = []

    if args.court and args.court in NEVER_REPAIR_SHARDS:
        log.error("Refusing %s: its dates come from the rulings' own headers; "
                  "this pass would replace them with body-text dates.", args.court)
        sys.exit(1)

    if args.court:
        jsonl = args.decisions_dir / f"{args.court}.jsonl"
        if not jsonl.exists():
            log.error("File not found: %s", jsonl)
            sys.exit(1)
        log.info("Repairing %s (dry_run=%s, min_confidence=%s)...",
                 args.court, args.dry_run, args.min_confidence)
        r = repair_jsonl(jsonl, dry_run=args.dry_run,
                         min_confidence=args.min_confidence, max_entries=args.max)
        results.append(r)
    elif args.all:
        jsonl_files = sorted(args.decisions_dir.glob("*.jsonl"))
        log.info("Repairing %d JSONL files (dry_run=%s, min_confidence=%s)...",
                 len(jsonl_files), args.dry_run, args.min_confidence)
        for jsonl in jsonl_files:
            # Skip temp files from ongoing repairs
            if jsonl.stem.startswith("tmp"):
                continue
            if jsonl.stem in NEVER_REPAIR_SHARDS:
                log.info("  Skipping %s (dates restored from the rulings' own headers)", jsonl.stem)
                continue
            log.info("  Processing %s...", jsonl.stem)
            r = repair_jsonl(jsonl, dry_run=args.dry_run,
                             min_confidence=args.min_confidence, max_entries=args.max)
            results.append(r)

    # Summary
    total_fixed = sum(r["stats"].get("fixed", 0) for r in results)
    total_match = sum(r["stats"].get("match", 0) for r in results)
    total_processed = sum(r["total"] for r in results)

    print(f"\n{'=' * 60}")
    print(f"DATE REPAIR {'(DRY RUN) ' if args.dry_run else ''}SUMMARY")
    print(f"{'=' * 60}")
    print(f"Files processed: {len(results)}")
    print(f"Total entries:   {total_processed}")
    print(f"Already correct: {total_match}")
    print(f"Fixed:           {total_fixed}")

    # Show per-court breakdown for courts with fixes
    for r in results:
        fixed = r["stats"].get("fixed", 0)
        if fixed > 0:
            print(f"\n  {r['court']}: {fixed}/{r['total']} fixed")
            for ex in r["examples"]:
                print(f"    {ex['id']}: {ex['old']} → {ex['new']} [{ex['method']}/{ex['confidence']}]")


if __name__ == "__main__":
    main()
