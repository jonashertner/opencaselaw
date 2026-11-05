#!/usr/bin/env python3
"""Per-court census of what dehyphenate.py would change, read-only.

For every input file (JSONL shard or Parquet, e.g. the HuggingFace
mirror's data/<court>.parquet) runs dehyphenate over full_text with the
given lexicon and reports, per court: decisions, decisions changed,
characters removed, counts per action:reason, and two invariants checked on
every decision changed (no letter or digit differs; a second run changes
nothing). Writes a JSON report and a TSV of changes drawn uniformly at random
per court (reservoir sampling) for review.

    python scripts/dehyphen_census.py --parquet hf/data --lexicon lex.db \\
        --workers 6 --sample 40 --out-dir census/

Nothing is written anywhere but --out-dir.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_dehyphen_lexicon import input_paths, iter_rows  # noqa: E402
from dehyphenate import SqliteLexicon, dehyphenate  # noqa: E402


def _alnum(s: str) -> str:
    return "".join(c for c in s if c.isalnum())


def census_file(path: str, lexicon_path: str, sample: int, seed: int) -> dict:
    lex = SqliteLexicon(lexicon_path)
    rng = random.Random(f"{seed}:{Path(path).name}")
    stats: Counter = Counter()
    docs = changed = removed = seen_changes = 0
    letter_diff: list[str] = []
    not_idempotent: list[str] = []
    reservoir: list[tuple] = []
    for row in iter_rows(Path(path), ("decision_id", "language", "full_text")):
        text = row.get("full_text")
        if not isinstance(text, str) or not text:
            continue
        docs += 1
        lang = row.get("language") or "de"
        rec: list = []
        out = dehyphenate(text, lang, lex, stats, rec)
        if out == text:
            continue
        changed += 1
        removed += len(text) - len(out)
        did = row.get("decision_id") or ""
        if _alnum(out) != _alnum(text.replace("‑", "-").replace("‐", "-")):
            letter_diff.append(did)
        if dehyphenate(out, lang, lex) != out:
            not_idempotent.append(did)
        for r in rec:
            seen_changes += 1
            item = (did, lang, *r)
            if len(reservoir) < sample:
                reservoir.append(item)
            else:
                j = rng.randrange(seen_changes)
                if j < sample:
                    reservoir[j] = item
    return {
        "file": Path(path).name, "docs": docs, "docs_changed": changed,
        "chars_removed": removed, "changes": seen_changes, "stats": dict(stats),
        "letter_diff": letter_diff[:50], "letter_diff_n": len(letter_diff),
        "not_idempotent": not_idempotent[:50], "not_idempotent_n": len(not_idempotent),
        "sample": reservoir,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--shards", nargs="+", default=[])
    ap.add_argument("--parquet", nargs="+", default=[])
    ap.add_argument("--lexicon", required=True)
    ap.add_argument("--out-dir", required=True, type=Path)
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--sample", type=int, default=40, help="changes sampled per court")
    ap.add_argument("--seed", type=int, default=20261105)
    args = ap.parse_args()
    inputs = input_paths(args.shards, args.parquet)
    if not inputs:
        print("no inputs found", file=sys.stderr)
        return 1
    args.out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    results = []
    order = sorted(inputs, key=lambda p: -p.stat().st_size)
    with ProcessPoolExecutor(max_workers=max(1, args.workers)) as ex:
        futs = [ex.submit(census_file, str(p), args.lexicon, args.sample, args.seed) for p in order]
        for fut in as_completed(futs):
            r = fut.result()
            results.append(r)
            print(f"  {r['file']}: {r['docs']:,} docs, {r['docs_changed']:,} changed, "
                  f"{r['changes']:,} changes ({time.time() - t0:,.0f}s)", file=sys.stderr)
    results.sort(key=lambda r: -r["changes"])
    with open(args.out_dir / "samples.tsv", "w", encoding="utf-8") as fh:
        fh.write("file\tdecision_id\tlang\taction\treason\tbefore\tafter\n")
        for r in results:
            for did, lang, action, reason, before, after in r["sample"]:
                c = lambda s: s.replace("\t", " ").replace("\r", "").replace("\n", "⏎")  # noqa: E731
                fh.write(f"{r['file']}\t{did}\t{lang}\t{action}\t{reason}\t{c(before)}\t{c(after)}\n")
    total = Counter()
    for r in results:
        total.update(r["stats"])
    report = {
        "inputs": len(results),
        "docs": sum(r["docs"] for r in results),
        "docs_changed": sum(r["docs_changed"] for r in results),
        "changes": sum(r["changes"] for r in results),
        "chars_removed": sum(r["chars_removed"] for r in results),
        "letter_diff_n": sum(r["letter_diff_n"] for r in results),
        "not_idempotent_n": sum(r["not_idempotent_n"] for r in results),
        "stats": dict(total.most_common()),
        "seconds": round(time.time() - t0),
        "per_file": [{k: v for k, v in r.items() if k != "sample"} for r in results],
    }
    (args.out_dir / "report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False))
    print(json.dumps({k: v for k, v in report.items() if k != "per_file"}, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
