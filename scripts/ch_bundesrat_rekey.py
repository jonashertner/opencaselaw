#!/usr/bin/env python3
"""Fold the ch_bundesrat duplicates: one row, one stable id per decision.

Background (runbooks/ch_bundesrat_rekey_2026-11.md): bj.admin.ch changed its
URL scheme twice and each scheme minted its own id, so the corpus served the
19 entries of the BJ's listing as 39 rows:

  es shard (entscheidsuche, 2026-02)  ch_bundesrat__bj_de_home_publiservice_..._2025-10-08.html
  direct, old scheme (2026-02-23)     ch_bundesrat_2025-10-08, ch_bundesrat_2025-06-131
  direct, flat slugs (2026-06-19)     ch_bundesrat_2025-10-08-kanton-aargau

and the first two dated several decisions from the first date in their text
(the contested ruling's: 2025-10-08 served as 2025-05-07, a 2024 letter as
2007-12-07).

What identifies a decision across all three is the BJ's PDF file stem
(``2025-10-08-beschwerde-kanton-aargau``; scrapers.ch_bundesrat.pdf_stem).
``runbooks/ch_bundesrat_rekey_2026-11.tsv`` maps each stem to the entry of
the live listing (docket ``YYYY-MM-DD-<descriptor>``, type, detail page, PDF,
keywords, title), read 2026-11-08 by the scraper's own parsers.

Per decision this script
  * keeps ONE row in the direct shard under ``ch_bundesrat_<docket>``: the
    flat-slug row when there is one, else the old-scheme row, re-keyed. Its
    date is the one the decision prints (scrapers.ch_bundesrat.
    decision_date_from_text), else the BJ's listed date; title, type, detail
    page, PDF, keywords and publication date come from the listing entry;
  * gives that row ``previous_decision_id`` = the old-scheme id, else the es
    id, so the old URL resolves (build_fts5 records it in decision_id_aliases);
  * drops the other direct rows of the decision (their id is the alias);
  * re-keys every es row of the decision to the same id, docket and date, with
    its own old id as ``previous_decision_id``. The build imports the direct
    shard first, so the es copy is skipped as the same decision and never
    served twice. The build records the skipped copy's previous_decision_id
    too (commit "build: a skipped duplicate still records its former id"),
    so its old id resolves; without that commit the es ids of the six
    decisions that also had an old-scheme id stop resolving.

Rows whose PDF stem the table does not know are left alone and reported.
DRY RUN by default; --apply rewrites both shards atomically (scripts/
shard_rewrite.py: .tmp, line-count and race checks, hard-linked backup).

Usage:
  python3 scripts/ch_bundesrat_rekey.py --direct output/decisions/ch_bundesrat.jsonl \
      --es output/decisions/es_ch_bundesrat.jsonl [--apply]
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from models import make_decision_id  # noqa: E402
from scrapers.ch_bundesrat import COURT, decision_date_from_text, pdf_stem  # noqa: E402
from scripts.shard_rewrite import read_shard, write_shard  # noqa: E402

DEFAULT_TABLE = REPO / "runbooks" / "ch_bundesrat_rekey_2026-11.tsv"
TAG = "bundesrat-rekey"


def load_table(path: Path) -> dict[str, dict]:
    with open(path, encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh, delimiter="\t"))
    table = {r["pdf_stem"]: r for r in rows}
    if len(table) != len(rows):
        raise SystemExit(f"refusing: duplicate pdf_stem in {path}")
    dockets = Counter(r["docket_number"] for r in rows)
    if any(n > 1 for n in dockets.values()):
        raise SystemExit(f"refusing: duplicate docket in {path}")
    return table


def canonical_row(base: dict, entry: dict) -> dict:
    """The decision's one row: ``base`` (its text) under the listing entry's
    identity and metadata."""
    row = dict(base)
    docket = entry["docket_number"]
    row["decision_id"] = make_decision_id(COURT, docket)
    row["docket_number"] = docket
    listed = entry.get("listed_date") or None
    row["decision_date"] = decision_date_from_text(row.get("full_text") or "", expected=listed) or listed
    row["decision_type"] = entry["decision_type"]
    row["title"] = entry["title"]
    row["source_url"] = entry["detail_url"]
    row["pdf_url"] = entry["pdf_url"]
    if entry.get("publication_date"):
        row["publication_date"] = entry["publication_date"]
    if entry.get("keywords"):
        row["legal_area"] = entry["keywords"]
    return row


def plan(direct: list[dict], es: list[dict], table: dict[str, dict]) -> dict:
    """Compute the new shards. Returns direct/es row lists, the alias map
    (old id -> canonical id, how), per-decision report lines and counts."""
    groups: dict[str, dict[str, list[dict]]] = defaultdict(lambda: {"direct": [], "es": []})
    unmapped_direct, unmapped_es = [], []
    for r in direct:
        stem = pdf_stem(r.get("pdf_url"))
        (groups[stem]["direct"] if stem in table else unmapped_direct).append(r)
    for r in es:
        stem = pdf_stem(r.get("pdf_url"))
        (groups[stem]["es"] if stem in table else unmapped_es).append(r)

    canon_of: dict[str, dict] = {}      # id(base row) -> canonical row
    drop: set[int] = set()              # id(row) of direct rows folded away
    es_new: dict[int, dict] = {}        # id(es row) -> rewritten es row
    aliases: dict[str, tuple[str, str]] = {}
    report = []
    stats: Counter = Counter()
    for stem, g in groups.items():
        entry = table[stem]
        cid = make_decision_id(COURT, entry["docket_number"])
        new_scheme = [r for r in g["direct"] if r["decision_id"] == cid]
        old_scheme = [r for r in g["direct"] if r["decision_id"] != cid]
        if new_scheme:
            base = new_scheme[0]
        elif old_scheme:
            base = max(old_scheme, key=lambda r: len(r.get("full_text") or ""))
        else:
            # Only an es copy holds this decision: leave it to the scraper,
            # which fetches the entry under its id at the next run.
            stats["es_only_decisions"] += 1
            report.append(f"{cid}: es copy only ({len(g['es'])}); not touched")
            continue
        row = canonical_row(base, entry)
        olds = [r["decision_id"] for r in old_scheme if r is not base]
        if base["decision_id"] != cid:
            olds.insert(0, base["decision_id"])
        # An es row re-keyed by an earlier run carries its old id as its alias.
        es_ids = [r.get("previous_decision_id") if r["decision_id"] == cid else r["decision_id"]
                  for r in g["es"]]
        es_ids = [i for i in es_ids if i]
        prev = base.get("previous_decision_id")
        if not prev or prev == cid:
            prev = (olds or es_ids or [None])[0]
        if prev:
            row["previous_decision_id"] = prev
            row["previous_id_source"] = "ch_bundesrat_rekey"
            aliases[prev] = (cid, "previous_decision_id")
        else:
            row.pop("previous_decision_id", None)
        for oid in olds + es_ids:
            if oid not in aliases:
                aliases[oid] = (cid, "es_skipped_duplicate" if oid in es_ids else "LOST")
        canon_of[id(base)] = row
        for r in g["direct"]:
            if r is not base:
                drop.add(id(r))
        for r in g["es"]:
            e = dict(r)
            if r["decision_id"] != cid:
                e["previous_decision_id"] = r["decision_id"]
                e["previous_id_source"] = "ch_bundesrat_rekey"
            e["decision_id"] = cid
            e["docket_number"] = row["docket_number"]
            e["decision_date"] = row["decision_date"]
            if e != r:
                stats["es_rows_rekeyed"] += 1
            es_new[id(r)] = e
        stats["decisions"] += 1
        stats["direct_rows_dropped"] += len(g["direct"]) - 1
        if row != base:
            stats["direct_rows_changed"] += 1
        if base.get("decision_date") != row["decision_date"]:
            stats["date_changed"] += 1
        report.append(
            f"{cid}: date {base.get('decision_date')} -> {row['decision_date']}; "
            f"kept {base['decision_id']}; prev={prev}; "
            f"dropped {[r['decision_id'] for r in g['direct'] if r is not base]}; es {es_ids}")

    new_direct = [canon_of.get(id(r), r) for r in direct if id(r) not in drop]
    new_es = [es_new.get(id(r), r) for r in es]
    ids = Counter(r["decision_id"] for r in new_direct)
    dup = [i for i, n in ids.items() if n > 1]
    if dup:
        raise SystemExit(f"refusing: the new direct shard would hold {dup} twice")
    stats["unmapped_direct"] = len(unmapped_direct)
    stats["unmapped_es"] = len(unmapped_es)
    stats["direct_before"], stats["direct_after"] = len(direct), len(new_direct)
    stats["es_rows"] = len(es)
    stats["aliases_previous_decision_id"] = sum(1 for v in aliases.values() if v[1] == "previous_decision_id")
    stats["aliases_es_skipped_duplicate"] = sum(1 for v in aliases.values() if v[1] == "es_skipped_duplicate")
    stats["aliases_lost"] = sum(1 for v in aliases.values() if v[1] == "LOST")
    for r in unmapped_direct + unmapped_es:
        report.append(f"UNMAPPED {r['decision_id']} (pdf {r.get('pdf_url')}): left alone")
    return {"direct": new_direct, "es": new_es, "aliases": aliases,
            "report": report, "stats": stats}


def served_ids(direct: list[dict], es: list[dict]) -> set[str]:
    """The ids a build serves from these two shards: direct rows, then the es
    rows whose id the direct shard does not already hold."""
    held = {r["decision_id"] for r in direct}
    return held | {r["decision_id"] for r in es if r["decision_id"] not in held}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--direct", type=Path, required=True)
    ap.add_argument("--es", type=Path, required=True)
    ap.add_argument("--table", type=Path, default=DEFAULT_TABLE)
    ap.add_argument("--apply", action="store_true", help="write (default: dry run)")
    args = ap.parse_args(argv)

    table = load_table(args.table)
    direct, st_d = read_shard(args.direct)
    es, st_e = read_shard(args.es)
    out = plan(direct, es, table)
    for line in sorted(out["report"]):
        print(line)
    print()
    print(f"served before: {len(served_ids(direct, es))}  after: {len(served_ids(out['direct'], out['es']))}")
    for k, v in sorted(out["stats"].items()):
        print(f"  {k}: {v}")
    if not args.apply:
        print("DRY RUN: nothing written (--apply to write)")
        return 0
    if out["direct"] != direct:
        write_shard(args.direct, out["direct"], st_d, TAG)
    else:
        print(f"{args.direct}: already folded, not rewritten")
    if out["es"] != es:
        write_shard(args.es, out["es"], st_e, TAG)
    else:
        print(f"{args.es}: already re-keyed, not rewritten")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
