"""Replay the answer key's changes on a simulated clock and score a watch/flag agent.

The clock runs one day per tick from the replay start to its end. On each
day the agent sees the records decided on or before that day and the changes
published that day (id, published_at and change only: origin, trigger,
expected, review and injection stay hidden). Calendar triggers (T12, T14)
have no event; the agent raises them from the records' dates.

Usage:
    python3 -m benchmarks.record_bench.replay --agent rule
    python3 -m benchmarks.record_bench.replay --agent oracle --records rec_02,rec_16,rec_23
    python3 -m benchmarks.record_bench.replay --agent command --records rec_23 \\
        --command "codex exec --skip-git-repo-check --ephemeral -s read-only -o {out} -"
    python3 -m benchmarks.record_bench.replay --agent claude --model sonnet --k 3
    python3 -m benchmarks.record_bench.replay --resume benchmarks/record_bench/results/<dir>
    python3 -m benchmarks.record_bench.report benchmarks/record_bench/results/<dir>

Writes into the run directory:
    manifest.json               agent, records, key hash, fixture version
    runs.jsonl                  one scored line per run (all flags included)
    transcripts/run_<i>.jsonl   model answers per event (LLM agents only)
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from collections import defaultdict
from pathlib import Path

from . import fixture
from .agents import RuleAgent, make_agent
from .score import attest_live, score_run, select_events

HERE = Path(__file__).resolve().parent
HARNESS_VERSION = "0.1.0"
VISIBLE = ("id", "published_at", "change")


def resolve_ids(all_records: list[dict], spec: str | None) -> list[dict]:
    if not spec:
        return all_records
    want = [s.strip() for s in spec.split(",") if s.strip()]
    out = [r for r in all_records if any(r["id"] == w or r["id"].startswith(w + "_") for w in want)]
    if len(out) != len(want):
        raise SystemExit(f"--records {spec!r} matched {[r['id'] for r in out]}")
    return out


def slice_events(key_events: list[dict], records: list[dict]) -> list[dict]:
    """Events for the selected records: expected for them, or matched to them by the rule stage."""
    ids = {r["id"] for r in records}
    rule, matched = RuleAgent(), set()
    for e in key_events:
        if e["origin"] == "calendar":
            continue
        vis = [r for r in records if r["decided_at"] <= e["published_at"]]
        if rule.candidates({k: e[k] for k in VISIBLE}, vis):
            matched.add(e["id"])
    return select_events(key_events, ids, matched)


def replay(agent, key_events: list[dict], records: list[dict], start: str, end: str) -> list[dict]:
    by_day = defaultdict(list)
    for e in key_events:
        if e["origin"] != "calendar":
            by_day[e["published_at"]].append({k: e[k] for k in VISIBLE})
    flags = []
    day, last = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    while day <= last:
        now = day.isoformat()
        visible = [r for r in records if r["decided_at"] <= now]
        if visible:
            for f in agent.tick(now, by_day.get(now, []), visible):
                flags.append({**f, "delivered_at": now})
        day += dt.timedelta(days=1)
    return flags


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Replay the record bench")
    ap.add_argument("--agent", choices=["null", "oracle", "rule", "claude", "command"], default="rule")
    ap.add_argument("--records", help="comma-separated record ids or prefixes (rec_02,rec_23); default all")
    ap.add_argument("--k", type=int, default=1, help="independent runs (worst-of-k in the report)")
    ap.add_argument("--model", default="sonnet", help="model for --agent claude")
    ap.add_argument("--command", help="CLI template for --agent command; prompt on stdin, {out}/{cwd} expanded")
    ap.add_argument("--timeout", type=float, default=300.0, help="seconds per model call")
    ap.add_argument("--attest", action="store_true", help="also run attest_response over cited assessments (network)")
    ap.add_argument("--out", type=Path, help="run directory (default results/<timestamp>_<agent>)")
    ap.add_argument("--resume", type=Path, help="continue an unfinished run directory")
    args = ap.parse_args(argv)

    key = fixture.load_key()
    all_records = fixture.load_records()
    if args.resume:
        out = args.resume
        m = json.loads((out / "manifest.json").read_text())
        args.agent, args.records, args.k = m["agent"]["agent"], ",".join(m["records"]), m["k"]
        args.model, args.command, args.timeout = m["agent"].get("model", args.model), m["agent"].get("command"), m["timeout"]
        args.attest = m["attest"]
        if m["answer_key_sha"] != _sha(fixture.KEY_PATH):
            print("note: the answer key changed since this run started; rescore after it finishes")
    else:
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        out = args.out or HERE / "results" / f"{stamp}_{args.agent}"
    records = resolve_ids(all_records, args.records)
    events = slice_events(key["events"], records)
    start, end = key["replay"]["start"], key["replay"]["end"]
    agent0 = make_agent(args.agent, key_events=events, model=args.model, command=args.command, timeout=args.timeout)
    out.mkdir(parents=True, exist_ok=True)
    if not args.resume:
        (out / "manifest.json").write_text(json.dumps({
            "harness_version": HARNESS_VERSION, "started_at": dt.datetime.now().isoformat(timespec="seconds"),
            "agent": agent0.meta(), "records": [r["id"] for r in records], "k": args.k, "timeout": args.timeout,
            "attest": args.attest, "replay": {"start": start, "end": end}, "events": len(events),
            "answer_key_sha": _sha(fixture.KEY_PATH), "fixture_version": fixture.load_company()["fixture_version"],
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    runs_path = out / "runs.jsonl"
    done = {json.loads(l)["run_index"] for l in runs_path.read_text().splitlines() if l.strip()} if runs_path.exists() else set()
    for i in range(args.k):
        if i in done:
            continue
        agent = agent0 if i == 0 else make_agent(args.agent, key_events=events, model=args.model,
                                                 command=args.command, timeout=args.timeout)
        t0 = dt.datetime.now()
        flags = replay(agent, events, records, start, end)
        line = {"run_index": i, "seconds": round((dt.datetime.now() - t0).total_seconds(), 1),
                "calls": getattr(agent, "calls", 0), "errors": getattr(agent, "errors", []),
                "flags": flags, "score": score_run(flags, events, records)}
        if args.attest:
            line["attest"] = attest_live(flags)
        if getattr(agent, "transcript", None):
            (out / "transcripts").mkdir(exist_ok=True)
            (out / "transcripts" / f"run_{i}.jsonl").write_text(
                "".join(json.dumps(t, ensure_ascii=False) + "\n" for t in agent.transcript), encoding="utf-8")
        with runs_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")
        s = line["score"]
        print(f"run {i}: miss {s['missed']}/{s['expected_push']} ({s['miss_rate']}), "
              f"false flags {s['false_flags']}/{s['pushes']}, calls {line['calls']}, errors {len(line['errors'])}")
    print(f"-> python3 -m benchmarks.record_bench.report {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
