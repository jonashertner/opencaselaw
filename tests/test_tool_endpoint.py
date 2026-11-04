"""POST /api/tool/{name}: the handler's dict for any tool; GET /api/tool lists them."""
import asyncio

import pytest
from mcp.types import TextContent

import mcp_server as m


def test_payload_is_parked_and_read_back(monkeypatch):
    async def fake_inner(name, arguments):
        return m._text_and_payload({"cases": [{"decision_id": "a"}], "count": 1}, "1. BGE 1 I 1")
    monkeypatch.setattr(m, "_handle_call_tool_inner", fake_inner)
    out = asyncio.run(m._call_tool_json("find_leading_cases", {"query": "x"}))
    assert out == {"cases": [{"decision_id": "a"}], "count": 1, "_tool": "find_leading_cases"}
    assert m._ctx_tool_payload.get() is None  # reset after the call

    async def text_only(name, arguments):
        return [TextContent(type="text", text="**Brief**\nplain markdown")]
    monkeypatch.setattr(m, "_handle_call_tool_inner", text_only)
    assert asyncio.run(m._call_tool_json("get_case_brief", {})) == {"text": "**Brief**\nplain markdown", "_tool": "get_case_brief"}

    async def json_text(name, arguments):
        return [TextContent(type="text", text='{"error": "No data", "hint": "x"}')]
    monkeypatch.setattr(m, "_handle_call_tool_inner", json_text)
    out = asyncio.run(m._call_tool_json("get_statistics", {}))
    assert out["_is_error"] is True and out["error"] == "No data"


def test_research_pair_answers_with_its_payload(monkeypatch):
    # cite, get_law and the other research tools return (content, payload);
    # the endpoint answered {"text": ""} for all of them until 2026-11-04.
    async def pair(name, arguments):
        return m._research_tool_result("BGE 140 III 86", {"citation_string_de": "BGE 140 III 86", "decision_id": "bge_140 III 86"})
    monkeypatch.setattr(m, "_handle_call_tool_inner", pair)
    out = asyncio.run(m._call_tool_json("cite", {"reference": "BGE 140 III 86"}))
    assert out == {"citation_string_de": "BGE 140 III 86", "decision_id": "bge_140 III 86", "_tool": "cite"}

    async def pair_without_payload(name, arguments):
        return ([TextContent(type="text", text='{"error": "not found"}')], None)
    monkeypatch.setattr(m, "_handle_call_tool_inner", pair_without_payload)
    out = asyncio.run(m._call_tool_json("get_law", {}))
    assert out["error"] == "not found" and out["_is_error"] is True


def test_every_tool_lists_an_input_schema():
    names = {t.name for t in m._list_tools()}
    assert "find_leading_cases" in names and m._tool_input_schema("find_leading_cases")["type"] == "object"
    assert m._tool_input_schema("no_such_tool") is None


@pytest.fixture(scope="module")
def rest_app():
    import uvicorn
    captured = {}
    real_run = uvicorn.run
    uvicorn.run = lambda app, **kwargs: captured.setdefault("app", app)
    try:
        m.main_remote("127.0.0.1", 0)
    finally:
        uvicorn.run = real_run
    return captured["app"]


def test_tool_routes(rest_app, monkeypatch):
    from starlette.testclient import TestClient
    async def fake_inner(name, arguments):
        return m._text_and_payload({"echo": arguments, "tool": name}, "text")
    monkeypatch.setattr(m, "_handle_call_tool_inner", fake_inner)
    client = TestClient(rest_app)
    listing = client.get("/api/tool").json()
    assert any(t["name"] == "cite" for t in listing["tools"]) and all("inputSchema" in t for t in listing["tools"])
    ok = client.post("/api/tool/find_leading_cases", json={"query": "Rachekündigung", "limit": 3})
    assert ok.status_code == 200 and ok.json()["echo"] == {"query": "Rachekündigung", "limit": 3} and ok.json()["_tool"] == "find_leading_cases"
    assert client.post("/api/tool/no_such_tool", json={}).status_code == 404
    bad = client.post("/api/tool/find_leading_cases", json={"limit": "many"})
    assert bad.status_code == 422 and "Input validation error" in bad.json()["detail"]
    assert client.post("/api/tool/find_leading_cases", json=[1, 2]).status_code == 422
