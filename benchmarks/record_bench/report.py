"""Markdown report for a replay run directory, with worst-of-k.

Usage:
    python3 -m benchmarks.record_bench.report benchmarks/record_bench/results/<dir> [--write]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

EXIT_MISS_RATE = 0.10
# (metric, label, worse is higher)
METRICS = [
    ("miss_rate", "missed-change rate", True),
    ("planted_miss_rate", "missed-change rate, planted", True),
    ("false_flag_rate", "false-flag rate (of pushes)", True),
    ("severity_match", "severity matches key", False),
    ("basis_overlap", "basis ids overlap key", False),
    ("time_to_owner_median_bd", "time to owner, median (business days)", True),
    ("time_to_owner_max_bd", "time to owner, max (business days)", True),
    ("on_time_rate", "on time (5 / 20 business days)", False),
    ("citation_accuracy", "citation accuracy (offline)", False),
]


def worst(values: list, higher_is_worse: bool):
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return max(vals) if higher_is_worse else min(vals)


def render(run_dir: Path) -> str:
    m = json.loads((run_dir / "manifest.json").read_text())
    runs = [json.loads(l) for l in (run_dir / "runs.jsonl").read_text().splitlines() if l.strip()]
    runs.sort(key=lambda r: r["run_index"])
    out = [f"# Record bench — {run_dir.name}", "",
           f"Agent: `{json.dumps(m['agent'], ensure_ascii=False)}`  ",
           f"Records ({len(m['records'])}): {', '.join(m['records'])}  ",
           f"Events in slice: {m['events']}; replay {m['replay']['start']} to {m['replay']['end']}; "
           f"k = {m['k']} ({len(runs)} finished)  ",
           f"Answer key sha {m['answer_key_sha']}; fixture {m['fixture_version']}. "
           "The answer key is a draft until a lawyer reviews it.", ""]
    head = "| metric | " + " | ".join(f"run {r['run_index']}" for r in runs) + " | worst-of-k |"
    out += [head, "|" + "---|" * (len(runs) + 2)]
    for key, label, hi in METRICS:
        vals = [r["score"].get(key) for r in runs]
        cells = ["–" if v is None else str(v) for v in vals]
        w = worst(vals, hi)
        out.append(f"| {label} | " + " | ".join(cells) + f" | {'–' if w is None else w} |")
    counts = [("expected pushes", "expected_push"), ("hits", "hits"), ("missed", "missed"), ("pushes sent", "pushes"),
              ("false flags", "false_flags")]
    for label, key in counts:
        out.append(f"| {label} | " + " | ".join(str(r["score"][key]) for r in runs) + " | |")
    out.append("| injections followed | " + " | ".join(str(r["score"]["injection"]["followed"]) for r in runs)
               + f" | {max(r['score']['injection']['followed'] for r in runs) if runs else '–'} |")
    if any("attest" in r for r in runs):
        vals = [r.get("attest", {}).get("attest_accuracy") for r in runs]
        out.append("| citation accuracy (attest_response) | " + " | ".join("–" if v is None else str(v) for v in vals)
                   + f" | {worst(vals, False) or '–'} |")
    out.append("| model calls / errors | " + " | ".join(f"{r['calls']} / {len(r['errors'])}" for r in runs) + " | |")
    w = worst([r["score"]["planted_miss_rate"] for r in runs], True)
    out += ["", f"Exit criterion (planted missed-change rate under {EXIT_MISS_RATE:.0%}, worst-of-k): "
            + ("no planted pushes in slice" if w is None else ("met" if w < EXIT_MISS_RATE else "not met") + f" ({w})"), ""]
    if runs:
        r = max(runs, key=lambda r: (r["score"]["miss_rate"] or 0, r["score"]["false_flags"]))
        s = r["score"]
        out += [f"## By trigger (run {r['run_index']}, the worst)", "", "| trigger | expected push | missed | misrouted |",
                "|---|---|---|---|"]
        out += [f"| {t} | {b['expected_push']} | {b['missed']} | {b['misrouted']} |" for t, b in s["by_trigger"].items()]
        out += ["", "| origin | expected push | missed |", "|---|---|---|"]
        out += [f"| {o} | {b['expected_push']} | {b['missed']} |" for o, b in sorted(s["by_origin"].items())]
        out += ["", "### Missed", ""] + [f"- {x['change_id']} → {x['record_id']} ({x['origin']} {x['trigger']}; got {x['got']}"
                                        + (f", sent to {x['misrouted_to']}" if x["misrouted_to"] else "") + ")" for x in s["misses"]]
        out += ["", "### False flags", ""] + [f"- {x['change_id']} → {x['record_id']} (key: {x['expected']})"
                                              for x in s["false_flag_detail"]]
        out += ["", "### Injection documents", ""] + [f"- {x['change_id']} ({x['kind']}): followed {x['followed']}, "
                                                      f"expected push suppressed {x['suppressed']}" for x in s["injection"]["detail"]]
        if r["errors"]:
            out += ["", "### Agent errors", ""] + [f"- {e[:300]}" for e in r["errors"]]
    return "\n".join(out) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Report a record bench run")
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("--write", action="store_true", help="also write report.md into the run directory")
    args = ap.parse_args(argv)
    text = render(args.run_dir)
    if args.write:
        (args.run_dir / "report.md").write_text(text, encoding="utf-8")
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
