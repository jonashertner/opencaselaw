"""The suppression list reaches every serving surface of mcp_server: MCP tool
calls (text and structuredContent), the JSON tool endpoint, REST, and the
decision pages. Offline: search is stubbed, the ids are made up."""
from __future__ import annotations

import asyncio
import json

import pytest
from mcp.types import CallToolRequest, CallToolRequestParams

import decision_suppression as ds
import mcp_server as m

HIDDEN = "bvger_D-1111_2023"
SHOWN = "bvger_D-5_2020"


def _hit(decision_id, docket):
    return {"decision_id": decision_id, "docket_number": docket, "court": "bvger",
            "decision_date": "2024-01-18", "language": "de", "title": docket,
            "regeste": "Asyl", "full_text": f"text of {docket}",
            "snippet": f"snippet of {docket}"}


@pytest.fixture
def suppressed(tmp_path, monkeypatch):
    f = tmp_path / "suppressed.txt"
    f.write_text(HIDDEN + "\n")
    monkeypatch.setenv("OCL_SUPPRESSED_DECISIONS_FILE", str(f))
    ds._reset_for_tests()

    def search(**kwargs):
        return [_hit(HIDDEN, "D-1111/2023"), _hit(SHOWN, "D-5/2020")], 2

    monkeypatch.setattr(m, "search_fts5", search)
    monkeypatch.setattr(m, "_pinpoint_enrich_results", lambda *a, **k: None)
    monkeypatch.setattr(m, "_representation_info", lambda *a: None)
    yield
    ds._reset_for_tests()


async def _wire(name, arguments):
    handler = m.server.request_handlers[CallToolRequest]
    req = CallToolRequest(method="tools/call",
                          params=CallToolRequestParams(name=name, arguments=arguments))
    return (await handler(req)).root


def test_mcp_search_drops_the_suppressed_decision(suppressed):
    result = asyncio.run(_wire("search_decisions", {"query": "Asyl", "limit": 5}))
    blob = json.dumps(result.structuredContent) + "".join(c.text for c in result.content)
    assert SHOWN in blob
    assert HIDDEN not in blob and "D-1111" not in blob


@pytest.mark.parametrize("args", [
    {"decision_id": HIDDEN}, {"decision_id": "D-1111/2023"},
])
def test_mcp_lookup_by_id_or_docket_is_refused(suppressed, args):
    out = asyncio.run(m._handle_call_tool_inner("get_decision", args))
    assert json.loads(out[0].text) == ds.NOT_AVAILABLE


def test_search_by_docket_is_refused(suppressed):
    out = asyncio.run(m._call_tool_json("search_decisions", {"query": "D-1111/2023"}))
    assert out["error"] == "not_available"


@pytest.fixture(scope="module")
def app():
    import uvicorn
    captured = {}
    real_run = uvicorn.run
    uvicorn.run = lambda app, **kwargs: captured.setdefault("app", app)
    try:
        m.main_remote("127.0.0.1", 0)
    finally:
        uvicorn.run = real_run
    return captured["app"]


def test_rest_and_pages(suppressed, app):
    from starlette.testclient import TestClient
    client = TestClient(app)
    listing = client.get("/api/decisions", params={"query": "Asyl", "limit": 5})
    assert listing.status_code == 200
    assert SHOWN in listing.text and HIDDEN not in listing.text
    assert int(listing.headers["content-length"]) == len(listing.content)
    assert client.get(f"/api/decisions/{HIDDEN}").status_code == 410
    assert client.get("/api/decisions", params={"query": "D-1111/2023"}).status_code == 410
    assert client.get(f"/entscheid/{HIDDEN}").status_code == 410
    assert client.get("/entscheid/BVGer%20D-1111%2F2023").status_code == 410
    tool = client.post("/api/tool/search_decisions", json={"query": "Asyl", "limit": 5})
    assert HIDDEN not in tool.text


def test_no_list_changes_nothing(tmp_path, monkeypatch, app):
    from starlette.testclient import TestClient
    monkeypatch.setenv("OCL_SUPPRESSED_DECISIONS_FILE", str(tmp_path / "absent.txt"))
    ds._reset_for_tests()
    monkeypatch.setattr(m, "search_fts5", lambda **k: ([_hit(HIDDEN, "D-1111/2023")], 1))
    monkeypatch.setattr(m, "_pinpoint_enrich_results", lambda *a, **k: None)
    monkeypatch.setattr(m, "_representation_info", lambda *a: None)
    r = TestClient(app).get("/api/decisions", params={"query": "Asyl", "limit": 5})
    assert HIDDEN in r.text
    ds._reset_for_tests()
