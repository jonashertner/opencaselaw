"""The query argument must have a length cap on every serving surface.

Document-shaped input once reached TOOL_DISPATCH_TIMEOUT_S. Policy now:
auto-condense above
QUERY_CONDENSE_THRESHOLD (separate change), hard refusal above QUERY_MAX_CHARS
(4,000) with an error that teaches the agent how to retry.

The REST guard is asserted via source AST (the rest_api app is nested and
can't be cheaply instantiated — precedent: tests/test_pro_redaction_guard.py).
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import mcp_server as m  # noqa: E402


# ------------------------------------------------------------------ helper

def test_under_and_at_cap_pass():
    assert m._query_length_error(None) is None
    assert m._query_length_error("") is None
    assert m._query_length_error("x" * 3999) is None
    assert m._query_length_error("x" * 4000) is None


def test_over_cap_refused_with_teaching_text():
    err = m._query_length_error("x" * 4001)
    assert err is not None
    assert "4,001" in err
    # The error must teach the retry, not just refuse.
    assert "3-8" in err
    assert "Art. 336 OR" in err
    assert "legal issue" in err


def test_cap_is_env_tunable():
    # QUERY_MAX_CHARS is read at import; the helper must use the module global
    # so tests (and ops) can adjust it.
    old = m.QUERY_MAX_CHARS
    try:
        m.QUERY_MAX_CHARS = 10
        assert m._query_length_error("x" * 11) is not None
        assert m._query_length_error("x" * 10) is None
    finally:
        m.QUERY_MAX_CHARS = old


# ------------------------------------------------------------------ schema

def test_search_decisions_schema_declares_maxlength():
    schema = m._list_tools()
    [t] = [t for t in schema if t.name == "search_decisions"]
    q = t.inputSchema["properties"]["query"]
    assert q.get("maxLength") == 4000
    assert "auto-condensed" in q["description"]


# ------------------------------------------------------- dispatch + REST

SRC = Path(REPO / "mcp_server.py").read_text(encoding="utf-8")


def _func_source(name: str) -> str:
    tree = ast.parse(SRC)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(SRC, node)
    raise AssertionError(f"function {name} not found")


def test_rest_search_route_guards_length():
    src = _func_source("api_search_decisions")
    assert "_query_length_error" in src
    assert "HTTPException" in src


def test_rest_leading_cases_route_guards_length():
    src = _func_source("api_find_leading_cases")
    assert "_query_length_error" in src


def test_mcp_dispatch_guards_all_three_query_tools():
    # search_decisions, find_leading_cases and the deep-research shim each
    # check the cap before running the search.
    assert SRC.count("_query_length_error(arguments.get(") >= 3
