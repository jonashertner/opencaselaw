"""The /mcp-edu endpoint keeps nothing per request past the technical logs.

The Microsoft 365 Copilot agent (tools/copilot-agent) calls /mcp-edu, and
the data-protection fact sheet given to universities states that its users'
queries are not kept. These tests hold the three per-request writers to that:
full capture, search traces and the per-IP cost ledger.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import mcp_server as m  # noqa: E402


@pytest.fixture
def no_retention():
    token = m._ctx_no_retention.set(True)
    yield
    m._ctx_no_retention.reset(token)


@pytest.mark.parametrize("path,expected", [
    ("/mcp-edu", True),
    ("/mcp-edu/", True),
    ("/mcp-copilot", True),   # first name, agent 1.1.0
    ("/mcp-education", False),
    ("/mcp", False),
    ("/", False),
    ("/mcp-copilotx", False),
    ("", False),
])
def test_only_the_copilot_path_is_no_retention(path, expected):
    assert m._is_no_retention_path(path) is expected


def test_mcp_root_app_sets_the_flag_from_the_path():
    src = (REPO / "mcp_server.py").read_text(encoding="utf-8")
    assert "_ctx_no_retention.set(_is_no_retention_path(path))" in src


def test_capture_is_skipped(tmp_path, monkeypatch, no_retention):
    monkeypatch.setattr(m, "_RESEARCH_LOG_DIR", tmp_path)
    monkeypatch.setattr(m, "_FULL_CAPTURE", True)
    m._capture_event({"src": "mcp", "tool": "search_decisions",
                      "params": {"query": "résiliation abusive"}})
    assert not list(tmp_path.iterdir())


def test_capture_still_runs_on_the_normal_path(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "_RESEARCH_LOG_DIR", tmp_path)
    monkeypatch.setattr(m, "_FULL_CAPTURE", True)
    m._capture_event({"src": "mcp", "tool": "search_decisions"})
    assert list(tmp_path.glob("capture_*.jsonl"))


def test_search_trace_is_skipped(tmp_path, monkeypatch, no_retention):
    monkeypatch.setattr(m, "_RESEARCH_LOG_DIR", tmp_path)
    m._log_search_trace({"query": "Mietzins"[:200], "ids": ["a"]})
    assert not list(tmp_path.glob("search_traces_*.jsonl"))


def test_search_trace_is_skipped_inside_worker_threads(tmp_path, monkeypatch, no_retention):
    """search_fts5 runs via asyncio.to_thread, which copies the context."""
    monkeypatch.setattr(m, "_RESEARCH_LOG_DIR", tmp_path)

    async def run():
        await asyncio.to_thread(m._log_search_trace, {"query": "Mietzins"})

    asyncio.run(run())
    assert not list(tmp_path.glob("search_traces_*.jsonl"))


def test_ip_ledger_is_skipped_but_cost_accounting_stays(tmp_path, monkeypatch, no_retention):
    usage = tmp_path / "usage.jsonl"
    ledger = tmp_path / "ledger.jsonl"
    monkeypatch.setattr(m, "LLM_USAGE_LOG_PATH", usage)
    monkeypatch.setattr(m, "LLM_LEDGER_LOG_PATH", ledger)
    ip_token = m._ctx_client_ip.set("20.250.196.30")
    src_token = m._ctx_llm_source.set("mcp")
    try:
        m._llm_usage_log(model="claude-haiku-4-5-20251001", feature="query_parse",
                         response_json={"usage": {"input_tokens": 100, "output_tokens": 10}})
    finally:
        m._ctx_client_ip.reset(ip_token)
        m._ctx_llm_source.reset(src_token)
    assert not ledger.exists()
    row = json.loads(usage.read_text().strip())
    assert not {"ip", "ip_pseudonym"} & set(row)
    assert row["feature"] == "query_parse"
