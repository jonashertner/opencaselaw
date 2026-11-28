"""Re-score finished runs against the current answer key without re-running the agent.

A key correction (for instance after lawyer review) should reach old runs
too. The stored flags are scored again over the same records; the previous
runs.jsonl is kept as runs.jsonl.bak.

Usage:
    python3 -m benchmarks.record_bench.rescore benchmarks/record_bench/results/<dir>
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import fixture
from .replay import _sha, slice_events
from .score import score_run


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Re-score a record bench run directory")
    ap.add_argument("run_dir", type=Path)
    args = ap.parse_args(argv)
    manifest_path = args.run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    want = set(manifest["records"])
    records = [r for r in fixture.load_records() if r["id"] in want]
    events = slice_events(fixture.load_key()["events"], records)
    runs_path = args.run_dir / "runs.jsonl"
    runs = [json.loads(l) for l in runs_path.read_text().splitlines() if l.strip()]
    changed = 0
    for r in runs:
        new = score_run(r["flags"], events, records)
        changed += new != r["score"]
        r["score"] = new
    runs_path.rename(runs_path.with_suffix(".jsonl.bak"))
    runs_path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in runs), encoding="utf-8")
    manifest["rescored_with_key_sha"] = _sha(fixture.KEY_PATH)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"rescored {len(runs)} runs, {changed} changed -> python3 -m benchmarks.record_bench.report {args.run_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
