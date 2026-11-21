"""The agent bench's parsing, checks and aggregation (offline)."""
import json

import pytest

from benchmarks.agent_bench import checks, judge, questions, report
from benchmarks.agent_bench.drivers import Trajectory, parse_stream_json


def _stream(*records):
    return [json.dumps(r) for r in records]


def test_stream_json_pairs_tool_calls_with_results_and_takes_the_final_answer():
    lines = _stream(
        {"type": "system", "subtype": "init", "model": "claude-sonnet-5-5", "claude_code_version": "2.1.296"},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": "mcp__ocl__cite", "input": {"reference": "BGE 132 III 122"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": [{"type": "text", "text": '{"exists": true}'}]}]}},
        {"type": "result", "subtype": "success", "result": "Antwort.", "num_turns": 2, "total_cost_usd": 0.1},
    )
    t = parse_stream_json(lines + ["not json"], Trajectory("q", 0, "claude-code", "sonnet", "sp"))
    assert t.model == "claude-sonnet-5-5" and t.status == "completed" and t.answer == "Antwort."
    assert [(s.name, s.input, s.result) for s in t.steps] == [("cite", {"reference": "BGE 132 III 122"}, '{"exists": true}')]
    assert parse_stream_json([], Trajectory("q", 0, "c", "m", "sp")).status == "error"


def test_judge_verdicts_never_default_to_met():
    out = judge.parse_verdicts('ok: [{"index": 0, "verdict": "met"}, {"index": 5, "verdict": "met"}, {"index": 1, "verdict": "maybe"}]', 3)
    assert [v["verdict"] for v in out] == ["met", "unjudged", "unjudged"]
    assert [v["verdict"] for v in judge.parse_verdicts("no json here", 2)] == ["unjudged", "unjudged"]


Q = {
    "id": "pa-001", "type": "praxisaenderung", "language": "de", "question": "?", "facts_date": None,
    "rubric": [{"point": "current rule", "critical": True}, {"point": "notes the change", "critical": False}],
    "gold": {"decisions": [{"decision_id": "bge_BGE_140_III_1", "role": "overruling", "must_cite": True, "pinpoint": "4.2"}],
             "statutes": [{"law": "OR", "aliases": ["OR", "CO"], "article": "60", "as_of": None}]},
    "forbidden": [{"decision_id": "bge_BGE_120_II_1", "reason": "overruled"}],
    "provenance": "test", "review": {"status": "draft", "prepared_by": "test", "reviewed_by": None, "reviewed_at": None},
}
MET = [{"index": 0, "verdict": "met"}, {"index": 1, "verdict": "met"}]


def _traj(answer, steps=()):
    return {"status": "completed", "answer": answer, "error": "", "steps": list(steps)}


def test_clean_run_passes_and_cites_are_read_from_links():
    answer = "Nach Art. 60 Abs. 1 CO gilt … ([BGE 140 III 1, E. 4.2.1](https://mcp.opencaselaw.ch/entscheid/bge_BGE_140_III_1#e-4-2-1))."
    steps = [{"name": "search_decisions", "input": {"query": "x"}, "result": "bge_BGE_140_III_1"},
             {"name": "attest_response", "input": {}, "result": ""}]
    r = checks.check_run(Q, _traj(answer, steps), {"issues": [], "linked_text": answer}, MET)
    assert r["passed"] and r["score"] == 1.0 and r["findings"] == []
    assert r["cited_decisions"] == {"bge_BGE_140_III_1": ["4.2.1"]}


def test_failures_map_to_severities():
    attest = {"issues": [{"category": "case", "citation": "BGE 299 III 1", "problem": "decision_not_in_corpus"},
                         {"category": "case", "citation": "x", "problem": "stored_date_suspect"}],
              "linked_text": "[BGE 120 II 1](https://mcp.opencaselaw.ch/entscheid/bge_BGE_120_II_1)"}
    verdicts = [{"index": 0, "verdict": "contradicted"}, {"index": 1, "verdict": "not_met"}]
    steps = [{"name": "search_decisions", "input": {"query": "x", "language": "de"}, "result": ""}]
    r = checks.check_run(Q, _traj("Die alte Regel gilt.", steps), attest, verdicts)
    found = {(f["check"], f["severity"]) for f in r["findings"]}
    assert {("fabricated_decision", "S1"), ("wrong_answer", "S1"), ("overruled_authority", "S1"),
            ("missing_authority", "S2"), ("statute_not_named", "S3"), ("no_closing_attest", "S3"),
            ("language_filter", "S3"), ("cited_without_retrieval", "S3")} == found
    assert not r["passed"] and r["score"] == 0.0


def test_unaudited_answer_is_not_a_pass_and_parent_pinpoint_is_imprecise():
    answer = "Art. 60 OR; [BGE](https://mcp.opencaselaw.ch/entscheid/bge_BGE_140_III_1#e-4)"
    r = checks.check_run(Q, _traj(answer, [{"name": "attest_response", "result": "bge_BGE_140_III_1"}]), {"_error": "timeout"}, MET)
    assert not r["passed"]
    assert {f["check"] for f in r["findings"]} >= {"attest_unavailable", "pinpoint_imprecise"}


@pytest.mark.parametrize("text,ok", [("Art. 60 Abs. 1 OR", True), ("art. 60 al. 1 CO", True), ("Art. 600 OR", False),
                                     ("Art. 60 ZGB", False), ("Art. 49 SchlT ZGB", False)])
def test_statute_mention(text, ok):
    assert checks.statute_mentioned(text, "OR", "60", ["OR", "CO"]) is ok


def test_statute_mention_spelled_out_final_title():
    assert checks.statute_mentioned("art. 49 du titre final du CC", "SchlT ZGB", "49", ["SchlT ZGB"])


def _record(q, i, score, s1=0, passed=True, qtype="pinpoint"):
    return {"question_id": q, "question_type": qtype, "run_index": i,
            "trajectory_meta": {"cost_usd": 0.1, "tool_sequence": ["cite"]},
            "checks": {"score": score, "passed": passed, "counts": {"S1": s1, "S2": 0, "S3": 0},
                       "findings": [{"check": "wrong_answer", "severity": "S1", "detail": "d"}] if s1 else []}}


def test_worst_of_k_counts_a_question_once_any_run_fails():
    recs = [_record("a", i, 1.0) for i in range(5)] + [_record("b", i, 1.0) for i in range(4)] + [_record("b", 4, 0.0, s1=1, passed=False)]
    s = report.summarize(recs)["overall"]
    assert s["worst_of_k_s1"] == 0.5 and s["s1_run_rate"] == 0.1 and s["all_k_pass"] == 0.5
    assert s["spread"] == pytest.approx((0 + 0.4) / 2)
    assert report.summarize(recs)["failures"][0] == {"check": "wrong_answer", "severity": "S1", "runs": 1, "questions": 1, "example": "b#4: d"}


def test_question_schema_rules():
    assert questions.validate(Q) == []
    reviewed = {**Q, "review": {"status": "reviewed", "reviewed_by": None, "reviewed_at": None}}
    assert "reviewed without reviewed_by / reviewed_at" in questions.validate(reviewed)
    assert "rubric needs at least one critical point" in questions.validate({**Q, "rubric": [{"point": "x", "critical": False}]})


def test_committed_question_set_is_well_formed():
    rows = questions.load_questions()
    assert len(rows) >= 50 and {q["type"] for q in rows} == questions.TYPES
    assert all(q["review"]["status"] in {"draft", "imported"} for q in rows)  # no lawyer review yet
