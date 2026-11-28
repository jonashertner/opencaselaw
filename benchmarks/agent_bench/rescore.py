"""Re-apply the current checks to finished runs without re-running the agents.

A check fix should reach old runs too, or two reports of the same
trajectories disagree for reasons that have nothing to do with the agent.
This recomputes `checks` for every record in runs.jsonl from the stored
trajectory, the stored judge verdicts and the stored audit. Records written
before run.py kept the full audit (issues, linked_text) get it re-fetched
with one attest_response call each (deterministic, no grounding); every
other input is reused as recorded. The previous runs.jsonl is kept as
runs.jsonl.bak.

Usage:
    python3 -m benchmarks.agent_bench.rescore benchmarks/agent_bench/results/<dir>
"""
from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path

from . import checks, mcp_http
from .questions import load_questions
from .run import HERE


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Re-apply checks to a run directory")
    ap.add_argument("run_dir", type=Path)
    args = ap.parse_args(argv)
    manifest = json.loads((args.run_dir / "manifest.json").read_text())
    qfile = HERE.parents[1] / manifest["questions_file"]
    questions = {q["id"]: q for q in load_questions(qfile)}
    runs_path = args.run_dir / "runs.jsonl"
    records = [json.loads(l) for l in runs_path.read_text().splitlines() if l.strip()]
    changed = 0
    for r in records:
        with gzip.open(args.run_dir / "trajectories" / f"{r['question_id']}__{r['run_index']}.json.gz", "rt", encoding="utf-8") as fh:
            traj = json.load(fh)
        attest = {k: v for k, v in r["attest"].items() if not (k == "_error" and v is None)} if r.get("attest") else None
        if attest and "_error" not in attest and "linked_text" not in attest and traj["answer"].strip():
            full = mcp_http.call_tool("attest_response", {"draft_text": traj["answer"], "audit_grounding": manifest.get("grounding", False)},
                                      endpoint=manifest["endpoint"])
            attest = {**attest, **{k: full[k] for k in ("issues", "linked_text") if k in full}}
            r["attest"] = attest
        new = checks.check_run(questions[r["question_id"]], traj, attest, r["judge"]["verdicts"])
        changed += new != r["checks"]
        r["checks"] = new
    runs_path.rename(runs_path.with_suffix(".jsonl.bak"))
    runs_path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8")
    print(f"rescored {len(records)} runs, {changed} changed -> python3 -m benchmarks.agent_bench.report {args.run_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
