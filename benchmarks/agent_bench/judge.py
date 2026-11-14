"""Rubric judge: does the answer make each point the question's rubric requires?

One LLM call per run, with no tools. The judge sees the question, the
rubric and the answer, never the gold authorities, so it grades the
legal content only; whether the right authorities were cited is a
deterministic check (checks.py). Its verdicts are a model's judgment,
not legal review: a sample of them goes to the lawyers who review the
question set (README, "Review").
"""
from __future__ import annotations

import json
import re

from .drivers import parse_stream_json, run_claude, Trajectory

JUDGE_SYSTEM = (
    "You grade answers to Swiss legal research questions against a rubric. "
    "You are strict and literal. Reply with JSON only."
)

VERDICTS = ("met", "not_met", "contradicted")

_TEMPLATE = """Question:
<question>
{question}
</question>

Answer to grade:
<answer>
{answer}
</answer>

Rubric points:
{points}

For each rubric point decide:
- "met": the answer states this point (in any wording or language);
- "not_met": the answer does not state it;
- "contradicted": the answer states something incompatible with it.
Judge only what the answer says, not whether its citations are real.

Reply with a JSON array, one object per point, in order:
[{{"index": 0, "verdict": "met|not_met|contradicted", "note": "<one short sentence>"}}]"""


def build_prompt(question: dict, answer: str) -> str:
    points = "\n".join(f"{i}. {p['point']}" for i, p in enumerate(question["rubric"]))
    return _TEMPLATE.format(question=question["question"], answer=answer, points=points)


def parse_verdicts(text: str, n: int) -> list[dict]:
    """Verdicts for n points; a point the judge skipped or garbled is
    recorded as 'unjudged', never silently counted as met."""
    out = [{"index": i, "verdict": "unjudged", "note": ""} for i in range(n)]
    m = re.search(r"\[.*\]", text or "", re.DOTALL)
    if not m:
        return out
    try:
        items = json.loads(m.group(0))
    except json.JSONDecodeError:
        return out
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        i, verdict = item.get("index"), item.get("verdict")
        if isinstance(i, int) and 0 <= i < n and verdict in VERDICTS:
            out[i] = {"index": i, "verdict": verdict, "note": str(item.get("note", ""))[:300]}
    return out


def judge(question: dict, answer: str, model: str = "sonnet",
          timeout: float = 300.0) -> dict:
    n = len(question["rubric"])
    if not answer.strip():
        return {"model": model, "verdicts": parse_verdicts("", n), "error": "empty answer"}
    lines, stderr, _ = run_claude(build_prompt(question, answer), system_prompt=JUDGE_SYSTEM,
                                  model=model, endpoint=None, timeout=timeout)
    t = parse_stream_json(lines, Trajectory("judge", 0, "claude-code", model, JUDGE_SYSTEM))
    return {"model": t.model, "verdicts": parse_verdicts(t.answer, n),
            "cost_usd": t.cost_usd, "error": t.error or ("" if t.answer else stderr[-300:])}
