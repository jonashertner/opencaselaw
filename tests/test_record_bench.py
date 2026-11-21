"""The record bench fixture, answer key, replay and scoring (offline)."""
import copy

from benchmarks.record_bench import fixture, lawtext, sources
from benchmarks.record_bench.agents import NullAgent, OracleAgent, RuleAgent, parse_flags
from benchmarks.record_bench.replay import replay, resolve_ids, slice_events
from benchmarks.record_bench.score import business_days, score_run

THREE = "rec_02,rec_16,rec_23"


def _slice(spec=THREE):
    key = fixture.load_key()
    records = resolve_ids(fixture.load_records(), spec)
    return key, records, slice_events(key["events"], records)


def test_fixture_and_answer_key_pass_the_offline_check():
    assert fixture.check(verbose=False) == []


def test_check_catches_typed_citations_altered_copies_and_unnamed_reviewers():
    people = {p["id"] for p in fixture.load_company()["people"]}
    rec = next(r for r in fixture.load_records() if r["id"].startswith("rec_23"))
    ok: list[str] = []
    fixture.check_record(rec, people, ok)
    assert ok == []

    typed = copy.deepcopy(rec)
    typed["basis"]["law"][2]["proposition"] = "As BGE 140 III 533 holds, distributions can be reclaimed."
    altered = copy.deepcopy(rec)
    altered["basis"]["law"][0]["article"] = "675a"
    unknown_owner = copy.deepcopy(rec)
    unknown_owner["owner"]["person"] = "nobody"
    for bad in (typed, altered, unknown_owner):
        errors: list[str] = []
        fixture.check_record(bad, people, errors)
        assert errors, bad["basis"]["law"][0]

    errors = []
    fixture.check_review({"status": "reviewed", "reviewed_by": None, "reviewed_at": None}, "ev", errors)
    fixture.check_review({"status": "draft", "reviewed_by": "someone", "reviewed_at": None}, "ev", errors)
    assert len(errors) == 2


def test_every_answer_key_entry_is_a_draft_and_planted_text_is_marked():
    key = fixture.load_key()
    assert all(e["review"]["status"] == "draft" and e["review"]["reviewed_by"] is None for e in key["events"])
    for e in key["events"]:
        if e["origin"] == "planted":
            for f in ("new_text", "summary"):
                if e["change"].get(f):
                    assert e["change"][f].startswith("[SYNTHETIC — NOT LAW]"), e["id"]


def test_lawtext_parses_a_cached_get_law_snapshot():
    snap = sources.load("get_law__OR-675-2026-03-12__fdf4863880")
    p = lawtext.parse(sources.text_of(snap))
    assert (p["law"], p["article"], p["structured"]) == ("OR", "675", True)
    assert p["paragraphs"]["2"].startswith("2 ")
    rec = next(r for r in fixture.load_records() if r["id"].startswith("rec_23"))
    assert p["paragraphs"]["2"] == rec["basis"]["law"][0]["text"]


def test_oracle_scores_perfect_and_null_misses_every_push():
    key, records, events = _slice()
    start, end = key["replay"]["start"], key["replay"]["end"]
    oracle = score_run(replay(OracleAgent(events), events, records, start, end), events, records)
    assert oracle["expected_push"] > 0 and oracle["missed"] == 0 and oracle["false_flags"] == 0
    assert oracle["injection"]["followed"] == 0 and oracle["injection"]["suppressed"] == 0
    null = score_run(replay(NullAgent(), events, records, start, end), events, records)
    assert null["missed"] == null["expected_push"] and null["pushes"] == 0


def test_misrouted_push_is_a_miss_and_unlisted_push_is_a_false_flag():
    _, records, events = _slice("rec_23")
    rec = records[0]
    e = next(e for e in events if e["origin"] == "planted"
             and any(x["record_id"] == rec["id"] and x["action"] == "push" for x in e["expected"]))
    flag = {"record_id": rec["id"], "change_id": e["id"], "action": "push", "severity": "high",
            "basis_ids": [], "assessment": "", "delivered_at": e["published_at"]}
    s = score_run([{**flag, "to": "us_counsel"}], events, records)
    assert s["by_origin"]["planted"]["misrouted"] == 1 and s["hits"] == 0
    s = score_run([{**flag, "to": rec["owner"]["person"], "change_id": "ev_none"}], events, records)
    assert s["false_flag_detail"] == [{"change_id": "ev_none", "record_id": rec["id"], "expected": "unlisted"}]


def test_injection_canary_in_a_flag_counts_as_followed():
    _, records, events = _slice("rec_23")
    e = next(e for e in events if (e.get("injection") or {}).get("canary"))
    rec = records[0]
    flag = {"record_id": rec["id"], "change_id": e["id"], "action": "push", "severity": "low",
            "to": rec["owner"]["person"], "basis_ids": ["f2"], "delivered_at": e["published_at"],
            "assessment": f"Reference {e['injection']['canary']}."}
    assert score_run([flag], events, records)["injection"]["followed"] == 1


def test_rule_agent_is_deterministic_and_logs_unchanged_paragraphs():
    key, records, events = _slice()
    start, end = key["replay"]["start"], key["replay"]["end"]
    a = replay(RuleAgent(), events, records, start, end)
    assert a == replay(RuleAgent(), events, records, start, end)
    rec = next(r for r in records if r["id"].startswith("rec_23"))
    l1 = rec["basis"]["law"][0]
    ev = {"id": "x", "published_at": "2026-05-01", "change": {
        "kind": "statute_amendment", "stage": "in_force", "law": l1["law"], "article": l1["article"],
        "current_paragraphs": {e["paragraph"]: e["text"] for e in rec["basis"]["law"][:2]}}}
    [(_, action, _, basis, _)] = RuleAgent().candidates(ev, [rec])
    assert (action, basis) == ("log", ["l1", "l2"])


def test_model_answers_are_parsed_defensively_and_business_days_skip_weekends():
    assert parse_flags('Sure.\n{"flags": [{"record_id": "r", "action": "push"}]}') == [{"record_id": "r", "action": "push"}]
    assert parse_flags("no json") == [] and parse_flags('{"flags": "x"}') == []
    assert business_days("2026-10-02", "2026-10-05") == 1  # Friday to Monday
