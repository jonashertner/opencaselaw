"""Aggregate scored runs into the three numbers the loop decides on.

  mean        rubric score over all runs
  spread      run-to-run standard deviation per question, averaged: a
              change smaller than this is noise, not an improvement
  worst-of-k  share of questions where at least one of the k runs has an
              S1 finding: the tail a user meets eventually

Usage:
    python3 -m benchmarks.agent_bench.report benchmarks/agent_bench/results/<run>/
"""
from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path


def load_records(run_dir: Path) -> list[dict]:
    path = run_dir / "runs.jsonl"
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def _share(xs) -> float | None:
    xs = list(xs)
    return round(sum(xs) / len(xs), 4) if xs else None


def summarize(records: list[dict]) -> dict:
    by_q: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        by_q[r["question_id"]].append(r)

    def block(qids: list[str]) -> dict:
        runs = [r for q in qids for r in by_q[q]]
        scores = [r["checks"]["score"] for r in runs if r["checks"]["score"] is not None]
        spreads = [statistics.pstdev([r["checks"]["score"] for r in by_q[q]])
                   for q in qids if len(by_q[q]) > 1]
        return {
            "questions": len(qids),
            "runs": len(runs),
            "mean_score": round(statistics.fmean(scores), 4) if scores else None,
            "spread": round(statistics.fmean(spreads), 4) if spreads else None,
            "pass_rate": _share(r["checks"]["passed"] for r in runs),
            "s1_run_rate": _share(r["checks"]["counts"]["S1"] > 0 for r in runs),
            "worst_of_k_s1": _share(any(r["checks"]["counts"]["S1"] > 0 for r in by_q[q]) for q in qids),
            "all_k_pass": _share(all(r["checks"]["passed"] for r in by_q[q]) for q in qids),
        }

    qids = sorted(by_q)
    types = sorted({by_q[q][0]["question_type"] for q in qids})
    failures: dict[str, dict] = {}
    for r in records:
        for name in sorted({f["check"] for f in r["checks"]["findings"]}):
            f = next(x for x in r["checks"]["findings"] if x["check"] == name)
            entry = failures.setdefault(name, {"severity": f["severity"], "runs": 0, "questions": set(), "example": None})
            entry["runs"] += 1
            entry["questions"].add(r["question_id"])
            entry["example"] = entry["example"] or f"{r['question_id']}#{r['run_index']}: {f['detail']}"
    sev_rank = {"S1": 0, "S2": 1, "S3": 2}
    failure_rows = sorted(
        ({"check": k, "severity": v["severity"], "runs": v["runs"], "questions": len(v["questions"]), "example": v["example"]}
         for k, v in failures.items()),
        key=lambda x: (sev_rank.get(x["severity"], 9), -x["runs"]))
    costs = [r["trajectory_meta"]["cost_usd"] or 0 for r in records]
    tools = Counter(name for r in records for name in r["trajectory_meta"]["tool_sequence"])
    return {
        "overall": block(qids),
        "by_type": {t: block([q for q in qids if by_q[q][0]["question_type"] == t]) for t in types},
        "per_question": {
            q: {"type": by_q[q][0]["question_type"],
                "scores": [r["checks"]["score"] for r in sorted(by_q[q], key=lambda r: r["run_index"])],
                "s1_runs": sum(r["checks"]["counts"]["S1"] > 0 for r in by_q[q]),
                "passed_runs": sum(r["checks"]["passed"] for r in by_q[q])}
            for q in qids},
        "failures": failure_rows,
        "cost": {"total_usd": round(sum(costs), 2), "mean_usd_per_run": round(statistics.fmean(costs), 3) if costs else None,
                 "mean_tool_calls": round(statistics.fmean(len(r["trajectory_meta"]["tool_sequence"]) for r in records), 1) if records else None,
                 "tool_calls": dict(tools.most_common())},
    }


def _fmt(x) -> str:
    return "–" if x is None else (f"{x:.2f}" if isinstance(x, float) else str(x))


def to_markdown(summary: dict, manifest: dict | None = None) -> str:
    cols = ["questions", "runs", "mean_score", "spread", "pass_rate", "s1_run_rate", "worst_of_k_s1", "all_k_pass"]
    lines = ["# Agent bench report", ""]
    if manifest:
        lines += [f"Model `{manifest.get('model')}` via {manifest.get('driver')} {manifest.get('client_version', '')}, "
                  f"k={manifest.get('k')}, endpoint `{manifest.get('endpoint')}`, questions `{manifest.get('questions_file')}` "
                  f"(sha256 {manifest.get('questions_sha256', '')[:12]}), started {manifest.get('started')}.", ""]
    lines += ["| slice | " + " | ".join(cols) + " |", "|---" * (len(cols) + 1) + "|"]
    rows = [("all", summary["overall"])] + list(summary["by_type"].items())
    for name, b in rows:
        lines.append(f"| {name} | " + " | ".join(_fmt(b[c]) for c in cols) + " |")
    lines += ["", "## Failures", "", "| check | severity | runs | questions | example |", "|---|---|---|---|---|"]
    for f in summary["failures"]:
        lines.append(f"| {f['check']} | {f['severity']} | {f['runs']} | {f['questions']} | {f['example'][:140].replace('|', '/')} |")
    lines += ["", "## Per question", "", "| question | type | scores | S1 runs | passed runs |", "|---|---|---|---|---|"]
    for q, v in summary["per_question"].items():
        lines.append(f"| {q} | {v['type']} | {' '.join(_fmt(s) for s in v['scores'])} | {v['s1_runs']} | {v['passed_runs']} |")
    c = summary["cost"]
    lines += ["", f"Cost: {c['total_usd']} USD list price ({c['mean_usd_per_run']} per run); "
              f"{c['mean_tool_calls']} tool calls per run on average.", ""]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("run_dir", type=Path)
    args = ap.parse_args(argv)
    summary = summarize(load_records(args.run_dir))
    manifest_path = args.run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else None
    (args.run_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    md = to_markdown(summary, manifest)
    (args.run_dir / "report.md").write_text(md)
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
