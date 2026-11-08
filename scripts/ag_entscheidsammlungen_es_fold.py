#!/usr/bin/env python3
"""Fold the frozen entscheidsuche rows of ag_baugesetzgebung / ag_weitere into
the direct rows of scrapers/cantonal/ag_entscheidsammlungen.py.

Background (runbooks/ag_entscheidsammlungen_2026-11.md): both codes were fed
only by the entscheidsuche spiders AG_Baugesetzgebung and AG_Weitere (frozen
since 2026-03). Their ids are built from the entry's title
(``ag_baugesetzgebung__sthetische_Generalklausel``), the direct scraper's from
the PDF's file stem (``ag_baugesetzgebung_1998-12-21-aesthetische-
generalklausel``), and 18 of the es dates are a statute's date. Left side by
side, the build would serve every ruling twice: different ids, dockets and
often dates, so no dedup pass pairs them.

Both copies link the same ag.ch PDF, so the PDF file name pairs them exactly.
For every pair this script
  * gives the direct row ``previous_decision_id`` = the es id (build_fts5
    records it in decision_id_aliases: the es URL keeps resolving);
  * moves the es row out of the es shard into the quarantine file
    ``<quarantine-dir>/es_<court>.quarantined.jsonl.txt`` (kept, not
    deleted; no build glob reads it).
es rows whose PDF no direct row holds stay where they are (ag_weitere: the 17
court rulings whose ag.ch PDFs are gone). It refuses when a PDF pairs more
than one row on either side, or when a direct row already carries another
previous_decision_id.

DRY RUN by default. --apply appends to the quarantine file first (rows already
in it are not written twice), then rewrites the es shard and the direct shard
atomically (scripts/shard_rewrite.py). Re-running after --apply is a no-op.

Usage:
  python3 scripts/ag_entscheidsammlungen_es_fold.py --court ag_baugesetzgebung \
      --decisions-dir output/decisions --quarantine-dir <dir> [--apply]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scripts.shard_rewrite import append_quarantine, read_shard, write_shard  # noqa: E402

COURTS = ("ag_baugesetzgebung", "ag_weitere")
TAG = "ag-es-fold"
ALIAS_SOURCE = "ag_es_fold"


def pdf_name(url: str | None) -> str:
    """The PDF's file name, the key both copies share."""
    return (url or "").split("?", 1)[0].rstrip("/").split("/")[-1].lower()


def plan(direct: list[dict], es: list[dict]) -> dict:
    by_pdf_direct: dict[str, list[dict]] = defaultdict(list)
    for r in direct:
        by_pdf_direct[pdf_name(r.get("pdf_url") or r.get("source_url"))].append(r)
    by_pdf_es: dict[str, list[dict]] = defaultdict(list)
    for r in es:
        by_pdf_es[pdf_name(r.get("pdf_url") or r.get("source_url"))].append(r)
    problems = []
    for name, rows in by_pdf_direct.items():
        if name and len(rows) > 1:
            problems.append(f"{len(rows)} direct rows hold {name}")
    for name, rows in by_pdf_es.items():
        if name and len(rows) > 1 and name in by_pdf_direct:
            problems.append(f"{len(rows)} es rows hold {name}")
    if problems:
        raise SystemExit("refusing:\n  " + "\n  ".join(problems))

    alias_of: dict[str, str] = {}      # direct id -> es id
    quarantine: list[dict] = []
    keep_es: list[dict] = []
    report: list[str] = []
    stats: Counter = Counter()
    for r in es:
        name = pdf_name(r.get("pdf_url") or r.get("source_url"))
        twin = by_pdf_direct.get(name, [None])[0] if name else None
        if twin is None:
            keep_es.append(r)
            stats["es_kept_no_direct_copy"] += 1
            continue
        prev = twin.get("previous_decision_id")
        if prev and prev != r["decision_id"]:
            raise SystemExit(f"refusing: {twin['decision_id']} already carries "
                             f"previous_decision_id {prev}, not {r['decision_id']}")
        alias_of[twin["decision_id"]] = r["decision_id"]
        quarantine.append(r)
        stats["pairs"] += 1
        if (r.get("decision_date") or None) != (twin.get("decision_date") or None):
            stats["es_date_differs"] += 1
            report.append(f"date {r.get('decision_date')} (es) -> {twin.get('decision_date')}: "
                          f"{r['decision_id']} -> {twin['decision_id']}")
        if len(r.get("full_text") or "") < 1000 <= len(twin.get("full_text") or ""):
            stats["es_stub_replaced_by_full_text"] += 1

    new_direct = []
    for r in direct:
        es_id = alias_of.get(r["decision_id"])
        if es_id and r.get("previous_decision_id") != es_id:
            r = dict(r)
            r["previous_decision_id"] = es_id
            r["previous_id_source"] = ALIAS_SOURCE
            stats["direct_aliased"] += 1
        new_direct.append(r)
    stats["direct_rows"] = len(direct)
    stats["direct_without_es_copy"] = len(direct) - stats["pairs"]
    stats["es_rows_before"] = len(es)
    stats["es_rows_after"] = len(keep_es)
    return {"direct": new_direct, "es": keep_es, "quarantine": quarantine,
            "report": report, "stats": stats}


def served(direct: list[dict], es: list[dict]) -> int:
    ids = {r["decision_id"] for r in direct}
    return len(ids | {r["decision_id"] for r in es})


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--court", choices=COURTS, required=True)
    ap.add_argument("--decisions-dir", type=Path, default=REPO / "output" / "decisions")
    ap.add_argument("--quarantine-dir", type=Path, required=True,
                    help="outside the decisions directory")
    ap.add_argument("--apply", action="store_true", help="write (default: dry run)")
    args = ap.parse_args(argv)

    d_path = args.decisions_dir / f"{args.court}.jsonl"
    e_path = args.decisions_dir / f"es_{args.court}.jsonl"
    q_path = args.quarantine_dir / f"es_{args.court}.quarantined.jsonl.txt"
    if args.quarantine_dir.resolve() == args.decisions_dir.resolve():
        raise SystemExit("refusing: the quarantine directory must not be the decisions directory")
    if not d_path.exists():
        raise SystemExit(f"refusing: {d_path} missing (run the direct scraper first)")
    direct, st_d = read_shard(d_path)
    es, st_e = read_shard(e_path) if e_path.exists() else ([], None)
    out = plan(direct, es)
    for line in out["report"]:
        print(line)
    print(f"\n{args.court}: served before {served(direct, es)}, after {served(out['direct'], out['es'])}")
    for k, v in sorted(out["stats"].items()):
        print(f"  {k}: {v}")
    if not args.apply:
        print("DRY RUN: nothing written (--apply to write)")
        return 0
    if out["quarantine"]:
        held = set()
        if q_path.exists():
            with open(q_path, encoding="utf-8") as fh:
                held = {json.loads(line)["decision_id"] for line in fh if line.strip()}
        new_q = [r for r in out["quarantine"] if r["decision_id"] not in held]
        append_quarantine(q_path, new_q)
        print(f"quarantined {len(new_q)} rows to {q_path}")
        write_shard(e_path, out["es"], st_e, TAG)
    if out["direct"] != direct:
        write_shard(d_path, out["direct"], st_d, TAG)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
