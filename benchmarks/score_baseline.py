#!/usr/bin/env python3
"""Score a baseline sweep against the v3 candidate labels (backlog #59).

Inputs:  search_relevance_candidates_v3.json (graded labels, frozen split)
         + a sweep JSONL ({id, returned: [decision_id...], latency_ms, error}).
Outputs: MRR@10 (grade>=2 = relevant), recall@10, nDCG@10 (graded), overall
         and per stratum (split / branch / language / era / query-type), plus
         the latency distribution the sweep measured for free (#42 evidence).

ID matching: exact decision_id primary. A normalized match (separators/case
folded) is reported SEPARATELY as a diagnostic for the known bge id-variant
split ('bge_BGE_140_III_337' vs 'bge_140 III 337') — never silently merged
into the headline number.
"""
from __future__ import annotations

import argparse
import json
import math
import re
from collections import defaultdict
from pathlib import Path


def _norm(did: str) -> str:
    # "bge_BGE_140_III_244" and the direct scraper's "bge_140 III 244" are
    # one BGE (#40): drop the repeated collection prefix before comparing.
    did = re.sub(r"^bge_(?:BGE|ATF|DTF)_", "bge_", did or "", flags=re.IGNORECASE)
    return re.sub(r"[^a-z0-9]+", "", did.lower())


def load(candidates_path: Path, sweep_path: Path):
    cand = json.loads(candidates_path.read_text())
    labels = {q["id"]: q for q in cand["queries"]}
    runs = []
    for ln in sweep_path.read_text().splitlines():
        try:
            r = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if r.get("id") in labels:
            runs.append(r)
    return labels, runs


def score_run(run: dict, q: dict):
    rel = {r["decision_id"]: r["grade"] for r in q["relevant"]}
    rel_norm = {_norm(k): v for k, v in rel.items()}
    returned = run.get("returned") or []
    rr = rr_n = 0.0
    hits = hits_n = 0
    dcg = dcg_n = 0.0
    for i, did in enumerate(returned[:10]):
        g = rel.get(did, 0)
        gn = rel_norm.get(_norm(did), 0)
        if g >= 2 and rr == 0.0:
            rr = 1.0 / (i + 1)
        if gn >= 2 and rr_n == 0.0:
            rr_n = 1.0 / (i + 1)
        hits += 1 if g >= 2 else 0
        hits_n += 1 if gn >= 2 else 0
        dcg += (2 ** g - 1) / math.log2(i + 2)
        dcg_n += (2 ** gn - 1) / math.log2(i + 2)
    ideal = sorted((g for g in rel.values()), reverse=True)[:10]
    idcg = sum((2 ** g - 1) / math.log2(i + 2) for i, g in enumerate(ideal)) or 1.0
    n_rel = sum(1 for g in rel.values() if g >= 2) or 1
    return {"rr": rr, "rr_norm": rr_n, "recall": hits / min(n_rel, 10),
            "ndcg": dcg / idcg, "ndcg_norm": dcg_n / idcg,
            "latency_ms": run.get("latency_ms"), "error": run.get("error")}


def aggregate(labels, runs):
    per_q = {}
    for run in runs:
        per_q[run["id"]] = score_run(run, labels[run["id"]])

    def agg(ids):
        rows = [per_q[i] for i in ids if i in per_q and not per_q[i]["error"]]
        if not rows:
            return None
        lat = sorted(r["latency_ms"] for r in rows if r["latency_ms"] is not None)
        p = lambda q: lat[min(len(lat) - 1, int(len(lat) * q))] if lat else None
        return {"n": len(rows),
                "mrr@10": round(sum(r["rr"] for r in rows) / len(rows), 4),
                "mrr@10_norm": round(sum(r["rr_norm"] for r in rows) / len(rows), 4),
                "ndcg@10": round(sum(r["ndcg"] for r in rows) / len(rows), 4),
                "recall@10": round(sum(r["recall"] for r in rows) / len(rows), 4),
                "lat_p50_ms": p(0.5), "lat_p95_ms": p(0.95)}

    out = {"overall": agg(list(per_q))}
    strata = defaultdict(list)
    for qid in per_q:
        q = labels[qid]
        strata[f"split:{q['split']}"].append(qid)
        tags = q["tags"]
        strata[f"branch:{tags[0]}"].append(qid)
        strata[f"lang:{tags[2]}"].append(qid)
        strata[f"era:{tags[3]}"].append(qid)
        qtype = "citation-form" if "citation-form" in tags else "natural-language"
        strata[f"type:{qtype}"].append(qid)
    for k in sorted(strata):
        a = agg(strata[k])
        if a:
            out[k] = a
    out["errors"] = sum(1 for r in per_q.values() if r["error"])
    out["scored"] = len(per_q)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--candidates", type=Path,
                    default=Path("benchmarks/search_relevance_candidates_v3.json"))
    ap.add_argument("--sweep", type=Path, required=True)
    ap.add_argument("--output", type=Path, default=None)
    args = ap.parse_args()
    labels, runs = load(args.candidates, args.sweep)
    result = aggregate(labels, runs)
    print(json.dumps(result["overall"], indent=1))
    for k in sorted(result):
        if ":" in k:
            r = result[k]
            print(f"{k:24s} n={r['n']:4d} mrr={r['mrr@10']:.3f} "
                  f"ndcg={r['ndcg@10']:.3f} recall={r['recall@10']:.3f} "
                  f"p50={r['lat_p50_ms']}ms")
    print(f"scored={result['scored']} errors={result['errors']}")
    if args.output:
        args.output.write_text(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
