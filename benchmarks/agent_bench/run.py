"""Run research agents over the question set k times and score every run.

Live: each run is a full agent session against the OpenCaseLaw MCP
endpoint (default the no-retention /mcp-edu path), plus one
attest_response call and one judge call. A run costs roughly what a
user's research question costs; check --k and --only before a full run.

Usage:
    python3 -m benchmarks.agent_bench.run --k 5 --model sonnet
    python3 -m benchmarks.agent_bench.run --only pa-001,tv-002 --k 2
    python3 -m benchmarks.agent_bench.run --resume benchmarks/agent_bench/results/<dir>
    python3 -m benchmarks.agent_bench.report benchmarks/agent_bench/results/<dir>

Writes into the run directory:
    manifest.json             model, client version, endpoint, prompt, question-set hash
    runs.jsonl                one scored record per (question, run)
    trajectories/<q>__<i>.json.gz   full trajectory: every tool call and result, the answer
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import json
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import checks, judge, mcp_http
from .drivers import SYSTEM_PROMPT_V1, ClaudeCodeDriver
from .questions import load_questions

HERE = Path(__file__).resolve().parent
DEFAULT_QUESTIONS = HERE / "questions_v0.jsonl"
HARNESS_VERSION = "0.1.0"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _client_version() -> str:
    try:
        return subprocess.run(["claude", "--version"], capture_output=True, text=True, timeout=30).stdout.strip()
    except Exception:  # noqa: BLE001
        return ""


def score_run(question: dict, traj: dict, endpoint: str, judge_model: str, grounding: bool) -> dict:
    attest = None
    if traj["answer"].strip():
        try:
            attest = mcp_http.call_tool("attest_response", {"draft_text": traj["answer"], "audit_grounding": grounding},
                                        endpoint=endpoint)
        except Exception as exc:  # noqa: BLE001 — recorded, never counted as a pass
            attest = {"_error": f"{type(exc).__name__}: {exc}"[:300]}
    verdict = judge.judge(question, traj["answer"], model=judge_model)
    result = checks.check_run(question, traj, attest, verdict["verdicts"])
    # issues and linked_text are kept so that `rescore` can re-apply changed checks offline.
    keep = ("ok", "issues_count", "issues_by_category", "issues", "linked_text", "ledger", "_error")
    return {"attest": {k: attest[k] for k in keep if k in attest} if attest else None,
            "judge": verdict, "checks": result}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Agent bench runner")
    ap.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS)
    ap.add_argument("--only", default="", help="comma-separated question ids")
    ap.add_argument("--types", default="", help="comma-separated question types")
    ap.add_argument("--k", type=int, default=5, help="runs per question")
    ap.add_argument("--model", default="sonnet")
    ap.add_argument("--effort", default=None)
    ap.add_argument("--judge-model", default="sonnet")
    ap.add_argument("--endpoint", default=mcp_http.DEFAULT_ENDPOINT)
    ap.add_argument("--grounding", action="store_true", help="attest with audit_grounding (one server-side LLM call per run)")
    ap.add_argument("--concurrency", type=int, default=2)
    ap.add_argument("--timeout", type=float, default=900.0, help="seconds per agent run")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--resume", type=Path, default=None, help="existing run directory; finished runs are skipped")
    args = ap.parse_args(argv)

    questions = load_questions(args.questions)
    only = {x for x in args.only.split(",") if x}
    types = {x for x in args.types.split(",") if x}
    questions = [q for q in questions if (not only or q["id"] in only) and (not types or q["type"] in types)]
    if not questions:
        print("no questions selected", file=sys.stderr)
        return 2

    if args.resume:
        out = args.resume
        manifest = json.loads((out / "manifest.json").read_text())
        args.model, args.k, args.endpoint = manifest["model"], manifest["k"], manifest["endpoint"]
    else:
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        out = args.out or HERE / "results" / f"{stamp}_{args.model}_k{args.k}"
        out.mkdir(parents=True, exist_ok=False)
        manifest = {
            "harness_version": HARNESS_VERSION, "driver": ClaudeCodeDriver.name, "client_version": _client_version(),
            "model": args.model, "effort": args.effort, "judge_model": args.judge_model, "k": args.k,
            "endpoint": args.endpoint, "grounding": args.grounding, "system_prompt": SYSTEM_PROMPT_V1,
            "questions_file": str(args.questions.relative_to(HERE.parents[1]) if args.questions.is_relative_to(HERE.parents[1]) else args.questions),
            "questions_sha256": _sha256(args.questions), "question_ids": [q["id"] for q in questions],
            "started": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        }
        (out / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    (out / "trajectories").mkdir(exist_ok=True)

    runs_path = out / "runs.jsonl"
    done = set()
    if runs_path.exists():
        for line in runs_path.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                done.add((r["question_id"], r["run_index"]))
    jobs = [(q, i) for q in questions for i in range(args.k) if (q["id"], i) not in done]
    print(f"{len(jobs)} runs to do ({len(done)} already done) -> {out}", file=sys.stderr)

    driver = ClaudeCodeDriver(model=args.model, endpoint=args.endpoint, effort=args.effort, timeout=args.timeout)
    lock = threading.Lock()

    def one(q: dict, i: int) -> str:
        traj = driver.run(q, i).to_dict()
        with gzip.open(out / "trajectories" / f"{q['id']}__{i}.json.gz", "wt", encoding="utf-8") as fh:
            json.dump(traj, fh, ensure_ascii=False)
        scored = score_run(q, traj, args.endpoint, args.judge_model, args.grounding)
        record = {
            "question_id": q["id"], "question_type": q["type"], "run_index": i,
            "trajectory_meta": {"status": traj["status"], "model": traj["model"], "turns": traj["turns"],
                                "cost_usd": traj["cost_usd"], "duration_ms": traj["duration_ms"],
                                "tool_sequence": [s["name"] for s in traj["steps"]], "error": traj["error"]},
            **scored,
        }
        with lock, runs_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        c = scored["checks"]
        return f"{q['id']}#{i}: score {c['score']} passed {c['passed']} {c['counts']}"

    with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
        futures = [pool.submit(one, q, i) for q, i in jobs]
        for fut in as_completed(futures):
            try:
                print(fut.result(), file=sys.stderr)
            except Exception as exc:  # noqa: BLE001 — one failed run must not stop the batch
                print(f"run failed: {type(exc).__name__}: {exc}", file=sys.stderr)
    print(f"done -> python3 -m benchmarks.agent_bench.report {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
