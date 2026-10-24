# Agent bench

End-to-end evaluation of AI research agents that use the OpenCaseLaw MCP
server. The other benchmarks here test one layer each: search relevance
(`run_search_benchmark.py`), the server's anti-hallucination tools
(`run_hallucination_probe.py`), or a fixed one-search RAG pipeline
(`swiss_legal_rag_bench/`). This one runs a real MCP client over a whole
research question, records every tool call and the final answer, and
scores the answer, the authorities and the method. It is the "re-run the
evals" step of the improvement loop: a change to tools, descriptions,
ranking or server instructions is measured here before it ships.

## What a run is

`run.py` starts Claude Code headless (`claude -p`) as the agent, isolated
so that only the model and the server vary (no built-in tools, no user or
project settings, no CLAUDE.md, a fresh empty working directory, a thin
fixed system prompt: `drivers.SYSTEM_PROMPT_V1`). The agent receives the
server's own instructions (R1–R11) as any client does. Traffic goes to
the no-retention path `/mcp-edu`, so bench runs never enter the
production capture stream or search traces.

Each run is then scored three ways:

1. **Server audit.** The final answer goes through `attest_response`
   (citations exist, pinpoints exist, quotes verbatim, statutes exist,
   dates match; `--grounding` adds the LLM grounding rail).
2. **Rubric judge.** One LLM call (`judge.py`, no tools) decides for each
   rubric point whether the answer states it, omits it, or contradicts
   it. The judge never sees the gold authorities.
3. **Deterministic checks** (`checks.py`): cited authorities against the
   gold set and the forbidden (overruled) set, gold pinpoints, statutes
   named, and the method read off the trajectory (closing attest called,
   no language filter, every cited decision traceable to a tool result).

## Severity

| | meaning | checks |
|---|---|---|
| S1 | misleads | fabricated_decision, invalid_pinpoint, unverified_quote, unknown_statute(_article), wrong_answer (critical rubric point contradicted), overruled_authority (cited while a critical point is missed) |
| S2 | materially incomplete | missing_authority (must-cite gold), incomplete_answer (critical point not met), wrong_date, unsupported_proposition, no_answer |
| S3 | method | pinpoint_missing / pinpoint_imprecise, statute_not_named, no_closing_attest, language_filter, cited_without_retrieval, attest_unavailable |

A run **passes** when the server could audit it and it has no S1 or S2.

## Numbers reported (`report.py`)

- **mean_score**: rubric score (critical points weigh 2), all runs.
- **spread**: standard deviation of the score across the k runs of a
  question, averaged over questions. A change smaller than this is noise.
- **worst_of_k_s1**: share of questions where at least one of the k runs
  has an S1. This is the tail a user eventually meets; releases should be
  decided on it, not on the mean.
- **pass_rate**, **s1_run_rate**, **all_k_pass**, per question type,
  plus failures grouped by check with an example each, and cost.

## Questions (`questions_v0.jsonl`)

| type | what it tests | source |
|---|---|---|
| praxisaenderung | the old leading case is the trap; `forbidden` lists it | drafted, gold verified with the tools |
| temporal | facts predate a revision; the answer must apply the version then in force | drafted, verified with `get_law(as_of)` |
| cross_lingual | leading case written in another language than the question | drafted from `swiss_legal_rag_bench/cross_lingual_v1.jsonl` targets |
| pinpoint | answer sits in one Erwägung; the Regeste is broader or silent | drafted, verified with `get_erwaegung` + `check_claim_support` |
| doctrine | general doctrine questions | imported from `swiss_legal_rag_bench/questions.jsonl` |

Every gold and forbidden `decision_id` was copied from tool output and
must resolve through `cite`; `python3 -m benchmarks.agent_bench.questions
--verify` re-checks that against the live corpus.

## Review

**No question in v0 has been reviewed by a lawyer.** Drafted questions
carry `review.status: "draft"` (prepared by Claude via the OpenCaseLaw
tools); imported ones carry `"imported"`. The judge's verdicts are a
model's judgment. Until a named reviewer has checked a question
(`review.status: "reviewed"` requires `reviewed_by` and `reviewed_at`; an
organisation label is not a reviewer), its results show where to look, not
what is legally right. Before the numbers are published or used as a
release gate:

1. two reviewers check each question, rubric and gold set, independently
   on a sample, so agreement can be reported;
2. a sample of judge verdicts is checked by hand;
3. the set is split into a development part (used when working on fixes)
   and a frozen test part (never used for a fix).

## Running

```sh
python3 -m benchmarks.agent_bench.questions --check     # offline schema check
python3 -m benchmarks.agent_bench.questions --verify    # live: every gold id resolves
python3 -m benchmarks.agent_bench.run --k 5 --only pa-001,tv-001 --concurrency 2
python3 -m benchmarks.agent_bench.rescore benchmarks/agent_bench/results/<dir>  # after a check changes
python3 -m benchmarks.agent_bench.report benchmarks/agent_bench/results/<dir>
```

Needs a signed-in `claude` CLI on PATH; stdlib Python only. A run costs
about what a user's research question costs (see `report.md` for the
measured list price per run); `--resume <dir>` continues an interrupted
batch.

## Limits of v0

- One client (Claude Code). Other clients, and the open model swisslaw.io
  ships, need their own driver in `drivers.py`.
- Temporal questions are scored by the rubric only: no check yet reads
  which statute version an answer applied.
- The overruled-precedent check needs the `forbidden` list per question;
  there is no corpus-wide Praxisänderung table yet.
- Cantonal and commentary citations are outside `attest_response`'s
  scope, so they are not audited here either.
