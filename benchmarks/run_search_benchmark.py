#!/usr/bin/env python3
"""
Evaluate mcp_server search relevance using a fixed golden query set.

Metrics:
- MRR@k
- Recall@k
- nDCG@k
- Hit@1
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
import statistics
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Swiss caselaw search relevance benchmark")
    parser.add_argument(
        "--golden",
        type=Path,
        action="append",
        help=(
            "Path to golden relevance JSON (repeatable). "
            "Default: benchmarks/search_relevance_golden.json"
        ),
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=Path.home() / ".swiss-caselaw" / "decisions.db",
        help="Path to SQLite decisions.db",
    )
    parser.add_argument(
        "-k",
        type=int,
        default=10,
        help="Top-k cutoff for metrics (default: 10)",
    )
    parser.add_argument(
        "--json-output",
        type=Path,
        help="Optional path to write machine-readable benchmark report JSON",
    )
    parser.add_argument(
        "--min-mrr",
        type=float,
        help="Fail (exit 1) if MRR@k is below this threshold",
    )
    parser.add_argument(
        "--min-recall",
        type=float,
        help="Fail (exit 1) if Recall@k is below this threshold",
    )
    parser.add_argument(
        "--min-ndcg",
        type=float,
        help="Fail (exit 1) if nDCG@k is below this threshold",
    )
    parser.add_argument(
        "--show-misses",
        action="store_true",
        help="Print per-query misses where no relevant judgment appears in top-k",
    )
    parser.add_argument(
        "--min-evaluated",
        type=int,
        help="Fail if evaluated queries are below this count",
    )
    parser.add_argument(
        "--require-tag",
        action="append",
        default=[],
        help="Require evaluated count per tag, format TAG:MIN (repeatable)",
    )
    return parser.parse_args()


def _dcg(grades: list[int]) -> float:
    score = 0.0
    for idx, grade in enumerate(grades, start=1):
        gain = (2 ** grade) - 1
        score += gain / math.log2(idx + 1)
    return score


def _load_golden(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        payload = json.load(f)
    if "queries" not in payload or not isinstance(payload["queries"], list):
        raise ValueError(f"Invalid golden file format: {path}")
    return payload


def _load_goldens(paths: list[Path]) -> tuple[list[dict], list[str]]:
    merged: list[dict] = []
    seen_ids: set[str] = set()
    sources: list[str] = []
    for path in paths:
        payload = _load_golden(path)
        sources.append(str(path))
        for query in payload["queries"]:
            q = dict(query)
            qid = str(q.get("id") or "")
            if qid and qid in seen_ids:
                suffix = 2
                while f"{qid}__{suffix}" in seen_ids:
                    suffix += 1
                q["id"] = f"{qid}__{suffix}"
            qid_final = str(q.get("id") or f"auto_{len(merged)+1}")
            q["id"] = qid_final
            seen_ids.add(qid_final)
            merged.append(q)
    return merged, sources


def _parse_tag_requirements(items: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for item in items:
        text = (item or "").strip()
        if not text:
            continue
        if ":" not in text:
            raise ValueError(f"Invalid --require-tag value '{item}', expected TAG:MIN")
        tag, minimum = text.split(":", 1)
        tag = tag.strip()
        if not tag:
            raise ValueError(f"Invalid --require-tag value '{item}', empty tag")
        try:
            min_count = int(minimum.strip())
        except ValueError as e:
            raise ValueError(f"Invalid --require-tag value '{item}', MIN must be integer") from e
        if min_count < 0:
            raise ValueError(f"Invalid --require-tag value '{item}', MIN must be >= 0")
        out[tag] = min_count
    return out


def _existing_ids(conn: sqlite3.Connection, ids: set[str]) -> set[str]:
    if not ids:
        return set()
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"SELECT decision_id FROM decisions WHERE decision_id IN ({placeholders})",
        tuple(ids),
    ).fetchall()
    return {r[0] for r in rows}


def _normalize_docket(value: str | None) -> str:
    return re.sub(r"[^0-9a-z]+", "", (value or "").lower())


def _candidate_dockets_from_relevant_id(relevant_id: str) -> list[str]:
    """
    Derive likely docket strings from a canonical-looking decision_id.

    Examples:
    - bger_8C_47_2011 -> 8C_47/2011
    - bger_1A.122_2005 -> 1A.122/2005
    - bvger_E-7414_2015 -> E-7414/2015
    - bstger_RR.2012.25 -> RR.2012.25
    """
    if not relevant_id:
        return []

    parts = relevant_id.split("_")
    tail = parts[-1] if len(parts) == 1 else "_".join(parts[1:])
    candidates: list[str] = [tail]

    if re.search(r"_\d{4}$", tail):
        stem, year = tail.rsplit("_", 1)
        candidates.append(f"{stem}/{year}")

    if len(parts) >= 3 and re.fullmatch(r"\d{4}", parts[-1]):
        stem = "_".join(parts[1:-1]) if len(parts) > 2 else parts[0]
        if stem:
            candidates.append(f"{stem}/{parts[-1]}")

    seen: set[str] = set()
    out: list[str] = []
    for c in candidates:
        c = c.strip()
        if c and c not in seen:
            out.append(c)
            seen.add(c)
    return out


def _lookup_decision_id_by_docket(conn: sqlite3.Connection, docket: str) -> str | None:
    row = conn.execute(
        "SELECT decision_id FROM decisions WHERE docket_number = ? LIMIT 1",
        (docket,),
    ).fetchone()
    if row:
        return row[0]

    norm = _normalize_docket(docket)
    if not norm:
        return None

    row = conn.execute(
        """
        SELECT decision_id
        FROM decisions
        WHERE replace(replace(replace(replace(lower(docket_number),'.',''),'/',''),'_',''),'-','') = ?
        LIMIT 1
        """,
        (norm,),
    ).fetchone()
    if row:
        return row[0]
    return None


def _resolve_relevant_id(
    conn: sqlite3.Connection,
    relevant_id: str,
    *,
    existing_ids: set[str],
    cache: dict[str, str | None],
) -> str | None:
    if relevant_id in existing_ids:
        return relevant_id
    if relevant_id in cache:
        return cache[relevant_id]

    # A BGE golden id in the other stored form ("bge_BGE_140_III_244" vs the
    # direct scraper's "bge_140 III 244", #40): exact candidates first.
    import decision_ref
    for cand in decision_ref.bge_id_candidates(relevant_id):
        if conn.execute("SELECT 1 FROM decisions WHERE decision_id = ?", (cand,)).fetchone():
            cache[relevant_id] = cand
            return cand

    for docket in _candidate_dockets_from_relevant_id(relevant_id):
        resolved = _lookup_decision_id_by_docket(conn, docket)
        if resolved:
            cache[relevant_id] = resolved
            return resolved

    cache[relevant_id] = None
    return None


def _configure_search_db(db_path: Path):
    import mcp_server

    mcp_server.DB_PATH = db_path
    mcp_server.DATA_DIR = db_path.parent
    mcp_server.PARQUET_DIR = db_path.parent / "parquet"
    mcp_server.GRAPH_DB_PATH = db_path.parent / "reference_graph.db"
    mcp_server.VECTOR_DB_PATH = db_path.parent / "vectors.db"
    return mcp_server


def main() -> int:
    args = parse_args()
    k = max(1, args.k)
    db_path = args.db.expanduser().resolve()
    golden_paths = args.golden or [Path("benchmarks/search_relevance_golden.json")]
    golden_paths = [p.expanduser().resolve() for p in golden_paths]

    if not db_path.exists():
        print(f"Database not found: {db_path}", file=sys.stderr)
        return 1
    for golden_path in golden_paths:
        if not golden_path.exists():
            print(f"Golden file not found: {golden_path}", file=sys.stderr)
            return 1

    try:
        required_tags = _parse_tag_requirements(args.require_tag)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 1

    queries, golden_sources = _load_goldens(golden_paths)
    if not queries:
        print("Golden inputs contain no queries.", file=sys.stderr)
        return 1

    all_relevant_ids = {
        rel["decision_id"]
        for q in queries
        for rel in q.get("relevant", [])
        if isinstance(rel, dict) and rel.get("decision_id")
    }

    with sqlite3.connect(str(db_path)) as conn:
        existing_relevant = _existing_ids(conn, all_relevant_ids)
        total_rows = conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
    resolve_conn = sqlite3.connect(str(db_path))

    mcp_server = _configure_search_db(db_path)

    per_query = []
    rr_scores = []
    recall_scores = []
    ndcg_scores = []
    hit1_scores = []
    latencies_ms = []
    resolved_alias_count = 0
    unresolved_relevant_count = 0
    skipped = 0
    resolution_cache: dict[str, str | None] = {}

    for q in queries:
        qid = q.get("id", "")
        query = q.get("query", "")
        relevant_items = q.get("relevant", [])
        tags = [t for t in q.get("tags", []) if isinstance(t, str)]

        rel_grades = {}
        for rel in relevant_items:
            if not isinstance(rel, dict):
                continue
            rid = rel.get("decision_id")
            if not rid:
                continue
            resolved = _resolve_relevant_id(
                resolve_conn,
                rid,
                existing_ids=existing_relevant,
                cache=resolution_cache,
            )
            if not resolved:
                unresolved_relevant_count += 1
                continue
            if resolved != rid:
                resolved_alias_count += 1
            rel_grades[resolved] = max(rel_grades.get(resolved, 0), int(rel.get("grade", 1)))

        if not rel_grades:
            skipped += 1
            per_query.append(
                {
                    "id": qid,
                    "query": query,
                    "tags": tags,
                    "status": "skipped_no_relevant_docs_in_db",
                    "rr": None,
                    "recall": None,
                    "ndcg": None,
                    "hit1": None,
                    "latency_ms": None,
                    "topk_ids": [],
                    "matched_ranks": {},
                }
            )
            continue

        start = time.perf_counter()
        results, _total = mcp_server.search_fts5(query=query, limit=k)
        latency_ms = (time.perf_counter() - start) * 1000.0
        latencies_ms.append(latency_ms)

        topk_ids = [r.get("decision_id") for r in results if r.get("decision_id")]
        matched_ranks = {
            rid: (topk_ids.index(rid) + 1)
            for rid in rel_grades
            if rid in topk_ids
        }

        if matched_ranks:
            best_rank = min(matched_ranks.values())
            rr = 1.0 / best_rank
        else:
            rr = 0.0

        recall = len(matched_ranks) / len(rel_grades)

        graded_found = [
            rel_grades[rid]
            for rid, _rank in sorted(matched_ranks.items(), key=lambda x: x[1])
        ]
        dcg = _dcg(graded_found)
        ideal_grades = sorted(rel_grades.values(), reverse=True)[:k]
        idcg = _dcg(ideal_grades)
        ndcg = (dcg / idcg) if idcg > 0 else 0.0

        hit1 = 1.0 if topk_ids and topk_ids[0] in rel_grades else 0.0

        rr_scores.append(rr)
        recall_scores.append(recall)
        ndcg_scores.append(ndcg)
        hit1_scores.append(hit1)

        per_query.append(
            {
                "id": qid,
                "query": query,
                "tags": tags,
                "status": "ok",
                "rr": rr,
                "recall": recall,
                "ndcg": ndcg,
                "hit1": hit1,
                "latency_ms": latency_ms,
                "topk_ids": topk_ids,
                "matched_ranks": matched_ranks,
            }
        )

    evaluated = len(rr_scores)
    if evaluated == 0:
        resolve_conn.close()
        print("No benchmark queries could be evaluated (all skipped).", file=sys.stderr)
        return 1

    summary = {
        "k": k,
        "db_path": str(db_path),
        "db_rows": total_rows,
        "golden_path": golden_sources[0] if golden_sources else "",
        "golden_paths": golden_sources,
        "queries_total": len(queries),
        "queries_evaluated": evaluated,
        "queries_skipped": skipped,
        "relevant_aliases_resolved": resolved_alias_count,
        "relevant_unresolved": unresolved_relevant_count,
        "mrr_at_k": statistics.mean(rr_scores),
        "recall_at_k": statistics.mean(recall_scores),
        "ndcg_at_k": statistics.mean(ndcg_scores),
        "hit_at_1": statistics.mean(hit1_scores),
        "latency_ms_avg": statistics.mean(latencies_ms),
        "latency_ms_p95": sorted(latencies_ms)[max(0, int(0.95 * len(latencies_ms)) - 1)],
    }

    tag_groups: dict[str, dict[str, list[float]]] = {}
    for row in per_query:
        if row["status"] != "ok":
            continue
        for tag in row.get("tags", []):
            bucket = tag_groups.setdefault(
                tag,
                {"rr": [], "recall": [], "ndcg": [], "hit1": [], "latency_ms": []},
            )
            bucket["rr"].append(row["rr"])
            bucket["recall"].append(row["recall"])
            bucket["ndcg"].append(row["ndcg"])
            bucket["hit1"].append(row["hit1"])
            bucket["latency_ms"].append(row["latency_ms"])

    by_tag = {}
    for tag, vals in sorted(tag_groups.items()):
        by_tag[tag] = {
            "count": len(vals["rr"]),
            "mrr_at_k": statistics.mean(vals["rr"]),
            "recall_at_k": statistics.mean(vals["recall"]),
            "ndcg_at_k": statistics.mean(vals["ndcg"]),
            "hit_at_1": statistics.mean(vals["hit1"]),
            "latency_ms_avg": statistics.mean(vals["latency_ms"]),
        }
    summary["by_tag"] = by_tag

    print(f"Search Benchmark @ {k}")
    print(f"- DB: {db_path} ({total_rows} decisions)")
    print(f"- Golden files: {len(golden_sources)}")
    for source in golden_sources:
        print(f"  - {source}")
    print(
        f"- Evaluated: {evaluated}/{len(queries)} "
        f"(skipped {skipped} missing relevance docs)"
    )
    print(f"- MRR@{k}:    {summary['mrr_at_k']:.4f}")
    print(f"- Recall@{k}: {summary['recall_at_k']:.4f}")
    print(f"- nDCG@{k}:   {summary['ndcg_at_k']:.4f}")
    print(f"- Hit@1:      {summary['hit_at_1']:.4f}")
    print(f"- Latency:    avg {summary['latency_ms_avg']:.2f} ms, p95 {summary['latency_ms_p95']:.2f} ms")

    if by_tag:
        print("\nBy tag:")
        for tag, stats in by_tag.items():
            print(
                f"- {tag}: n={stats['count']}, "
                f"MRR@{k}={stats['mrr_at_k']:.3f}, "
                f"Recall@{k}={stats['recall_at_k']:.3f}, "
                f"nDCG@{k}={stats['ndcg_at_k']:.3f}, "
                f"Hit@1={stats['hit_at_1']:.3f}, "
                f"lat={stats['latency_ms_avg']:.1f}ms"
            )

    if args.show_misses:
        misses = [q for q in per_query if q["status"] == "ok" and q["rr"] == 0.0]
        if misses:
            print("\nMisses (no relevant result in top-k):")
            for m in misses:
                print(f"- {m['id']}: {m['query']}")
                if m["topk_ids"]:
                    print(f"  topk: {', '.join(m['topk_ids'][:5])}")
                else:
                    print("  topk: <empty>")

    if args.json_output:
        payload = {
            "summary": summary,
            "per_query": per_query,
        }
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        with open(args.json_output, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        print(f"\nWrote JSON report: {args.json_output}")

    failed = False
    if args.min_evaluated is not None and summary["queries_evaluated"] < args.min_evaluated:
        print(
            f"Threshold fail: evaluated {summary['queries_evaluated']} < {args.min_evaluated}",
            file=sys.stderr,
        )
        failed = True
    if args.min_mrr is not None and summary["mrr_at_k"] < args.min_mrr:
        print(
            f"Threshold fail: MRR@{k} {summary['mrr_at_k']:.4f} < {args.min_mrr:.4f}",
            file=sys.stderr,
        )
        failed = True
    if args.min_recall is not None and summary["recall_at_k"] < args.min_recall:
        print(
            f"Threshold fail: Recall@{k} {summary['recall_at_k']:.4f} < {args.min_recall:.4f}",
            file=sys.stderr,
        )
        failed = True
    if args.min_ndcg is not None and summary["ndcg_at_k"] < args.min_ndcg:
        print(
            f"Threshold fail: nDCG@{k} {summary['ndcg_at_k']:.4f} < {args.min_ndcg:.4f}",
            file=sys.stderr,
        )
        failed = True
    if required_tags:
        for tag, min_count in required_tags.items():
            got = int(summary["by_tag"].get(tag, {}).get("count", 0))
            if got < min_count:
                print(
                    f"Threshold fail: tag '{tag}' evaluated count {got} < {min_count}",
                    file=sys.stderr,
                )
                failed = True

    resolve_conn.close()
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
