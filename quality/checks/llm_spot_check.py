"""LLM 1%-sample spot-check — catches the unknown-unknown class.

The deterministic checks (schema, dates, dockets, urls, etc.) catch
*known* failure modes. The LLM spot-check covers the residual: a small
random sample of decisions handed to a fast LLM judge with the prompt
"is the regeste a plausible summary of the full_text? Are the statute
references real?". When the judge flags >5% of samples as suspicious,
we emit a WARNING for human review.

Cost model: 50 samples × ~$0.001/call ≈ $0.05/nightly run. Bounded by
``MAX_SAMPLES`` and short prompt windows. Disabled at module level if
``ANTHROPIC_API_KEY`` is unset (e.g. CI runners) — falls through to
WARNING-skipped so the gate never blocks on a missing key.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3

from quality.types import CheckResult, Severity

MODULE_NEVER_CRITICAL = True  # WARNING-only — never blocks publish

MAX_SAMPLES = 50
SUSPICIOUS_FRACTION_WARN = 0.05  # > 5% suspicious → fire WARNING
MODEL = os.environ.get("OCL_QC_LLM_MODEL", "claude-haiku-5-5")

PROMPT_TEMPLATE = """You are auditing a Swiss court decision for ingestion quality.

REGESTE (court-written headnote):
{regeste}

FULL TEXT (first 6000 chars):
{full_text}

Answer JSON only with these keys:
  regeste_summary_match: "yes" | "no" — is the regeste a plausible
      summary of the full text? "no" if they are about different cases,
      or the regeste is gibberish, or the text is empty/garbage.
  statute_refs_plausible: "yes" | "no" | "n/a" — do all statute
      references in the regeste exist in real Swiss law? "n/a" if no
      refs.
  notes: short string explaining any "no" verdicts (≤200 chars).
"""


def _sample_recent(conn: sqlite3.Connection, n: int) -> list[dict]:
    rows = conn.execute(
        "SELECT decision_id, court, regeste, "
        "       substr(full_text, 1, 6000) AS full_text "
        "FROM decisions "
        "WHERE length(full_text) > 1000 AND length(regeste) > 30 "
        "ORDER BY random() LIMIT ?",
        (n,),
    ).fetchall()
    return [dict(r) for r in rows]


def _ask_judge(decision: dict) -> dict | None:
    """Best-effort call. Returns None on any failure (rate limit, parse,
    network) so the spot-check degrades gracefully rather than tripping
    a CRITICAL gate failure on infra hiccup."""
    try:
        from anthropic import Anthropic
    except ImportError:
        return None
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    client = Anthropic()
    try:
        # Haiku 4.5 rejects `effort`; Haiku 5.5 would think by default.
        extra = ({} if MODEL.startswith("claude-haiku-4") else
                 {"thinking": {"type": "disabled"},
                  "output_config": {"effort": "low"}})
        msg = client.messages.create(
            model=MODEL,
            max_tokens=400,
            **extra,
            messages=[{
                "role": "user",
                "content": PROMPT_TEMPLATE.format(
                    regeste=(decision.get("regeste") or "")[:2000],
                    full_text=(decision.get("full_text") or "")[:6000],
                ),
            }],
        )
        body = next((b.text for b in msg.content if b.type == "text"), "")
    except Exception:
        return None
    m = re.search(r"\{[^{}]*\}", body, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def check_llm_spot_audit(conn: sqlite3.Connection, **_) -> CheckResult:
    """1%-sample LLM audit of regeste vs full_text + statute plausibility."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return CheckResult(
            name="llm_spot_check.regeste_vs_text",
            severity=Severity.WARNING,
            passed=True,
            metric_value=0,
            threshold=None,
            message="ANTHROPIC_API_KEY not set — skipped (run on production)",
        )

    sample = _sample_recent(conn, MAX_SAMPLES)
    if not sample:
        return CheckResult(
            name="llm_spot_check.regeste_vs_text",
            severity=Severity.WARNING,
            passed=True,
            metric_value=0,
            threshold=None,
            message="empty corpus — no decisions to sample",
        )

    suspicious: list[dict] = []
    judged = 0
    for d in sample:
        verdict = _ask_judge(d)
        if verdict is None:
            continue
        judged += 1
        if (
            verdict.get("regeste_summary_match") == "no"
            or verdict.get("statute_refs_plausible") == "no"
        ):
            suspicious.append({
                "decision_id": d["decision_id"],
                "court": d["court"],
                "verdict": verdict,
            })

    if judged == 0:
        return CheckResult(
            name="llm_spot_check.regeste_vs_text",
            severity=Severity.WARNING,
            passed=True,
            metric_value=0,
            threshold=None,
            message="judge unreachable on all samples — skipped",
        )

    suspicious_frac = len(suspicious) / judged
    is_warn = suspicious_frac > SUSPICIOUS_FRACTION_WARN
    return CheckResult(
        name="llm_spot_check.regeste_vs_text",
        severity=Severity.WARNING,
        passed=(not is_warn),
        metric_value=round(suspicious_frac, 4),
        threshold=SUSPICIOUS_FRACTION_WARN,
        message=(
            f"{len(suspicious)}/{judged} sampled decisions flagged "
            f"({suspicious_frac:.1%}); threshold "
            f"{SUSPICIOUS_FRACTION_WARN:.1%}"
        ),
        sample_rows=suspicious[:5],
        fix_advice=(
            "investigate the flagged decisions; common causes are "
            "PDF-extraction merging two cases, regeste-from-wrong-source, "
            "or OCR garbage. Spot-check then fix the scraper if a court "
            "is over-represented in fails."
        ),
    )
