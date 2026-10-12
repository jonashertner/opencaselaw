"""Haiku 5.5 request fields and response reading (2026-10-07 model switch).

Haiku 5.5 thinks by default, which would eat the 2-3 s budgets of the query
parse, expansion and rerank calls, so those calls turn thinking off. Haiku 4.5
rejects `effort`, so the OCL_HAIKU_MODEL rollback must send none of the new
fields. A 5.5 response can lead with a thinking block, so the text is read by
block type, not position. Offline: no API call.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import mcp_server as m  # noqa: E402


def test_haiku_5_5_turns_thinking_off(monkeypatch):
    monkeypatch.setattr(m, "HAIKU_MODEL", "claude-haiku-5-5")
    assert m._haiku_request_fields() == {
        "thinking": {"type": "disabled"}, "output_config": {"effort": "low"}}


def test_the_rollback_to_haiku_4_5_sends_no_new_fields(monkeypatch):
    monkeypatch.setattr(m, "HAIKU_MODEL", "claude-haiku-4-5-20251001")
    assert m._haiku_request_fields() == {}


def test_the_default_model_is_priced():
    assert m.HAIKU_MODEL in m._LLM_PRICING


def test_text_is_read_by_block_type_not_position():
    resp = {"content": [{"type": "thinking", "thinking": "..."},
                        {"type": "text", "text": '{"intent": "search"}'}]}
    assert m._messages_text(resp) == '{"intent": "search"}'


def test_a_response_without_text_yields_empty_string():
    assert m._messages_text({"content": [], "stop_reason": "refusal"}) == ""
    assert m._messages_text({}) == ""
    assert m._messages_text({"content": None}) == ""
