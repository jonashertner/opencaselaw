"""An argument we do not recognise must not vanish in silence.

Unknown arguments were dropped without a word, so the tool answered a
question the caller had not asked and looked confident doing it. Measured
in a day of live capture: `search_decisions` received `legal_area` twelve
times and `courtLevel` once, `get_decision` received `include_full_text`,
`include_summary`, `highlight` and `format` — every one discarded, every
caller told nothing. A `search_decisions` call whose `legal_area` filter
evaporates comes back unfiltered and indistinguishable from a real answer.

Two responses, both cheap: alias the names that mean something we already
have, and name the rest in the reply so the model can correct itself on
the next call. Aliasing beats rejecting because a rejected call helps
nobody, and because `topic` on find_leading_cases follows our own
description ("for a topic or statute") against a parameter called `query`
— our wording invited it.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import mcp_server as m  # noqa: E402


def test_declared_args_are_read_from_the_real_schemas():
    declared = m._declared_tool_args()
    assert "search_decisions" in declared
    assert {"query", "court", "limit"} <= declared["search_decisions"]
    assert "query" in declared["find_leading_cases"]


def test_topic_is_accepted_as_the_query_it_describes():
    args = {"topic": "Schuldneranweisung", "limit": 3}
    unknown = m._normalise_tool_args("find_leading_cases", args)
    assert args["query"] == "Schuldneranweisung"
    assert "topic" not in args
    assert unknown == [], "an aliased name is understood, not reported"


def test_an_alias_never_overwrites_an_explicit_value():
    args = {"topic": "ignored", "query": "explicit"}
    m._normalise_tool_args("find_leading_cases", args)
    assert args["query"] == "explicit"


def test_a_genuinely_unknown_argument_is_reported():
    """The live case: the filter silently evaporates."""
    args = {"query": "Mietzins", "legal_area": "Mietrecht"}
    assert m._normalise_tool_args("search_decisions", args) == ["legal_area"]


def test_known_arguments_are_never_reported():
    args = {"query": "x", "court": "bger", "limit": 5, "include_pinpoint": True}
    assert m._normalise_tool_args("search_decisions", args) == []


def test_the_warning_says_what_was_ignored_and_what_is_valid():
    from mcp.types import TextContent
    res = [TextContent(type="text", text="# Leading Cases (all, top 3)")]
    out = m._prepend_arg_warning(res, "find_leading_cases", ["legal_area"])
    text = out[0].text
    assert text.startswith("NOTE:")
    assert "legal_area" in text
    assert "did NOT affect" in text
    assert "query" in text, "the valid parameters have to be named"
    assert "# Leading Cases" in text, "the answer itself must survive"


def test_no_unknown_arguments_leaves_the_response_untouched():
    from mcp.types import TextContent
    res = [TextContent(type="text", text="body")]
    assert m._prepend_arg_warning(res, "search_decisions", [])[0].text == "body"


def test_an_unknown_tool_is_not_second_guessed():
    """Schemas we cannot see are not evidence that an argument is wrong."""
    assert m._normalise_tool_args("not_a_tool", {"whatever": 1}) == []


def test_normalisation_survives_odd_input():
    assert m._normalise_tool_args("search_decisions", None) == []
    assert m._prepend_arg_warning([], "search_decisions", ["x"]) == []


@pytest.mark.parametrize("arg", ["law", "law_code", "statute"])
def test_the_act_reaches_get_law_under_any_name_callers_use(arg):
    args = {arg: "ZGB", "article": "28"}
    assert m._normalise_tool_args("get_law", args) == []
    assert args == {"abbreviation": "ZGB", "article": "28"}


def test_a_get_law_alias_never_overwrites_an_explicit_abbreviation():
    args = {"law": "ZGB", "abbreviation": "OR"}
    assert m._normalise_tool_args("get_law", args) == ["law"]
    assert args["abbreviation"] == "OR"


def _outcome_record(tmp_path):
    lines = next(tmp_path.glob("capture_*.jsonl")).read_text().splitlines()
    return next(r for r in map(json.loads, lines) if r["src"] == "outcome")


@pytest.mark.asyncio
async def test_a_get_law_miss_is_labelled_empty_not_answered(tmp_path, monkeypatch):
    """Live: `law_id` was dropped, the act never arrived, and the reply
    "Provide sr_number or abbreviation." was counted as substantive."""
    monkeypatch.setattr(m, "_RESEARCH_LOG_DIR", tmp_path)
    monkeypatch.setattr(m, "_FULL_CAPTURE", True)
    monkeypatch.setattr(m, "get_law",
                        lambda **kw: {"error": "Provide sr_number or abbreviation."})

    result = await m._dispatch_with_timeout("get_law", {"law_id": "ZGB", "article": "28"})

    assert _response_text_starts_with_note(result)
    rec = _outcome_record(tmp_path)
    assert rec["outcome"] == "empty"
    assert rec["unknown_args"] == ["law_id"]


def _response_text_starts_with_note(result):
    return m._response_text(result).startswith("NOTE: ignored unrecognised")


@pytest.mark.asyncio
@pytest.mark.parametrize("payload, outcome, ids", [
    ({"error": "no such filter"}, "empty", []),
    ({"results": [{"decision_id": "bge_140 III 86"}]}, "substantive", ["bge_140 III 86"]),
])
async def test_the_note_cannot_change_how_an_answer_is_recorded(
        tmp_path, monkeypatch, payload, outcome, ids):
    """The note makes a JSON answer unparseable, so it goes on last."""
    from mcp.types import TextContent
    monkeypatch.setattr(m, "_RESEARCH_LOG_DIR", tmp_path)
    monkeypatch.setattr(m, "_FULL_CAPTURE", True)

    async def _answer(name, arguments):
        return [TextContent(type="text", text=json.dumps(payload))]

    monkeypatch.setattr(m, "_handle_call_tool_inner", _answer)
    result = await m._dispatch_with_timeout("search_decisions",
                                            {"query": "x", "legal_area": "Mietrecht"})

    assert _response_text_starts_with_note(result)
    rec = _outcome_record(tmp_path)
    assert rec["outcome"] == outcome
    assert rec["returned_ids"] == ids
