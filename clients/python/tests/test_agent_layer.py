"""The agent layer: tool calls over the MCP endpoint, the cache, doctor, skills, the library facade."""
import io
import json
from pathlib import Path

import pytest
from opencaselaw_cli import api, cli
from opencaselaw_cli.client import APIError, Client

TOOLS = [{"name": "cite", "description": "Canonical citation. More text.", "inputSchema": {"type": "object", "required": ["reference"], "properties": {"reference": {"type": "string"}, "pinpoint": {"type": "string"}}}, "outputSchema": {"type": "object"}},
         {"name": "find_leading_cases", "description": "Leading cases for a query.", "inputSchema": {"type": "object", "required": ["query"], "properties": {"query": {}, "limit": {}}}}]


class FakeMcp:
    base_url = "https://example.test"
    cache_dir = None
    requests = 0

    def __init__(self):
        self.calls = []

    def mcp_tools(self):
        return TOOLS

    def tool_json(self, name, arguments=None):
        result = self.mcp_call(name, arguments)
        structured = result.get("structuredContent")
        value = dict(structured) if isinstance(structured, dict) else {"text": "\n".join(c.get("text", "") for c in result.get("content", []))}
        value["_tool"] = name
        if result.get("isError"):
            value["_is_error"] = True; value.setdefault("error", value.get("text"))
        return value

    def mcp_call(self, name, arguments=None):
        self.calls.append((name, dict(arguments or {})))
        if name == "cite":
            if arguments.get("reference") == "BGE 999 III 1":
                return {"isError": True, "content": [{"type": "text", "text": "not found"}], "structuredContent": {"error": "Reference not found", "exists": False}}
            return {"isError": False, "content": [{"type": "text", "text": "..."}], "structuredContent": {"exists": True, "decision_id": "bge_BGE_136_III_513", "citation_string": "BGE 136 III 513"}}
        if name == "find_leading_cases":
            return {"isError": False, "content": [{"type": "text", "text": "1. BGE 1 I 1\n2. BGE 2 II 2"}]}
        raise APIError(400, f"{name}: unknown tool")

    def get(self, path, params=None):
        if path == "/health":
            return {"status": "ok", "decisions": 25, "db_generation": 7}
        if path == "/api/cite":
            return {"exists": True, "decision_id": "bge_BGE_136_III_513"}
        raise AssertionError(path)


def invoke(monkeypatch, capsys, argv, client=None, stdin=""):
    client = client or FakeMcp()
    monkeypatch.setattr(cli, "create_client", lambda args: client)
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(stdin))
    monkeypatch.setenv("OCL_CONFIG", "/nonexistent/ocl-config")
    code = cli.main(argv)
    return client, code, capsys.readouterr()


def test_tool_list_schema_and_typed_call(monkeypatch, capsys):
    _, code, out = invoke(monkeypatch, capsys, ["tool", "list", "--format", "json"])
    listing = json.loads(out.out)
    assert code == 0 and listing["count"] == 2 and listing["tools"][0]["required"] == ["reference"] and listing["tools"][0]["structured_output"] is True
    _, code, out = invoke(monkeypatch, capsys, ["tool", "schema", "find_leading_cases", "--format", "json"])
    assert code == 0 and json.loads(out.out)["inputSchema"]["required"] == ["query"]
    client, code, out = invoke(monkeypatch, capsys, ["tool", "call", "cite", "reference=BGE 136 III 513", "pinpoint=2.3", "--format", "json"])
    assert code == 0 and json.loads(out.out)["decision_id"] == "bge_BGE_136_III_513" and json.loads(out.out)["_tool"] == "cite"
    client, code, out = invoke(monkeypatch, capsys, ["tool", "call", "find_leading_cases", "query=Rachekündigung", "limit=5", "flag=true", "ids=[\"a\",1]", "--format", "json"])
    assert client.calls[0][1] == {"query": "Rachekündigung", "limit": 5, "flag": True, "ids": ["a", 1]}
    assert code == 0 and json.loads(out.out)["text"].startswith("1. BGE 1 I 1")
    # a tool-reported error is exit 4 with the structured error, never a crash
    _, code, out = invoke(monkeypatch, capsys, ["tool", "call", "cite", "reference=BGE 999 III 1", "--format", "json"])
    assert code == 4 and json.loads(out.out)["_is_error"] is True and json.loads(out.out)["error"] == "Reference not found"
    # --args object plus pairs; unknown tool is exit 4 with a message
    client, code, out = invoke(monkeypatch, capsys, ["tool", "call", "cite", "--args", '{"reference": "BGE 136 III 513"}', "pinpoint=1", "--format", "json"])
    assert code == 0 and client.calls[0][1] == {"reference": "BGE 136 III 513", "pinpoint": "1"}
    # pairs after options, as a shell user types them, on every Python version
    client, code, out = invoke(monkeypatch, capsys, ["tool", "call", "cite", "--format", "json", "reference=BGE 136 III 513", "--timeout", "5", "pinpoint=2"])
    assert code == 0 and client.calls[0][1] == {"reference": "BGE 136 III 513", "pinpoint": "2"}
    assert cli._reorder_tool_call(["tool", "call", "x", "--args", "a=b", "k=v"]) == ["tool", "call", "x", "k=v", "--args", "a=b"]
    _, code, out = invoke(monkeypatch, capsys, ["tool", "call", "nope", "--format", "json"])
    assert code == 4 and json.loads(out.out)["errors"][0]["status"] == 400
    # one call per JSONL row
    client, code, out = invoke(monkeypatch, capsys, ["tool", "call", "cite", "--stdin", "--format", "json"], stdin='{"reference": "BGE 136 III 513"}\n{"arguments": {"reference": "BGE 999 III 1"}}\n')
    payload = json.loads(out.out)
    assert code == 4 and payload["requested"] == 2 and payload["results"][1]["_is_error"] is True


def test_text_rendering_of_tools(monkeypatch, capsys):
    _, code, out = invoke(monkeypatch, capsys, ["tool", "list", "--format", "text", "--color", "never"])
    assert code == 0 and out.out.startswith("cite  Canonical citation") and "2 tools" in out.out
    _, code, out = invoke(monkeypatch, capsys, ["tool", "call", "find_leading_cases", "query=x", "--format", "text", "--color", "never"])
    assert code == 0 and "1. BGE 1 I 1" in out.out


def test_doctor_reports_and_exits_by_reachability(monkeypatch, capsys):
    _, code, out = invoke(monkeypatch, capsys, ["doctor", "--format", "json"])
    report = json.loads(out.out)
    assert code == 0 and report["ok"] and report["tools"]["count"] == 2 and report["cite_ok"] and report["health"]["db_generation"] == 7
    class Down(FakeMcp):
        def get(self, path, params=None):
            raise APIError(None, "Request failed: refused")
    _, code, out = invoke(monkeypatch, capsys, ["doctor", "--format", "json"], client=Down())
    assert code == 3 and json.loads(out.out)["ok"] is False
    _, code, out = invoke(monkeypatch, capsys, ["doctor", "--format", "text", "--color", "never"])
    assert code == 0 and out.out.startswith("ok  https://example.test")


def test_skills_are_bundled_and_installable(monkeypatch, capsys, tmp_path):
    _, code, out = invoke(monkeypatch, capsys, ["skills", "list", "--format", "json"])
    names = [s["name"] for s in json.loads(out.out)["skills"]]
    assert code == 0 and names == ["citation-check", "evidence-bundle", "research"]
    _, code, out = invoke(monkeypatch, capsys, ["skills", "show", "citation-check"])
    assert code == 0 and out.out.startswith("---\nname: citation-check")
    _, code, out = invoke(monkeypatch, capsys, ["skills", "install", "--dir", str(tmp_path), "--format", "json"])
    report = json.loads(out.out)
    assert code == 0 and len(report["installed"]) == 3 and (tmp_path / "research" / "SKILL.md").exists()
    _, code, out = invoke(monkeypatch, capsys, ["skills", "install", "--dir", str(tmp_path), "--format", "json"])
    assert len(json.loads(out.out)["skipped_existing"]) == 3
    _, code, out = invoke(monkeypatch, capsys, ["agent-guide"])
    assert code == 0 and out.out.startswith("# ocl for agents") and "ocl tool call" in out.out


def test_client_cache_is_keyed_by_the_server_generation(tmp_path):
    served = {"generation": 7}
    def opener(request, timeout):
        url = request.full_url
        if url.endswith("/health"):
            body = json.dumps({"status": "ok", "db_generation": served["generation"]})
        elif request.data:
            body = 'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"tools":[{"name":"cite"}]}}\n\n'
        else:
            body = json.dumps({"decision_id": "a", "n": served["generation"]})
        class R(io.BytesIO):
            headers = {"Content-Type": "text/event-stream" if request.data else "application/json"}
        return R(body.encode())
    client = Client(opener=opener, sleep=lambda s: None, cache_dir=tmp_path)
    assert client.get("/api/decisions/a")["n"] == 7 and client.get("/api/decisions/a")["n"] == 7
    assert client.cache_hits == 1 and client.requests == 2  # /health for the generation + one fetch; the repeat is served from disk
    assert client.mcp_tools() == [{"name": "cite"}] and client.mcp_tools() == [{"name": "cite"}] and client.cache_hits == 2
    served["generation"] = 8
    fresh = Client(opener=opener, sleep=lambda s: None, cache_dir=tmp_path)
    assert fresh.get("/api/decisions/a")["n"] == 8 and fresh.cache_hits == 0  # a new generation invalidates the cache


def test_library_facade_uses_the_same_rows():
    class Fake(FakeMcp):
        def get(self, path, params=None):
            if path == "/api/cite":
                return {"exists": True, "decision_id": "bge_BGE_136_III_513", "citation_string_de": "BGE 136 III 513"}
            if path == "/api/decisions/bge_BGE_136_III_513":
                return {"decision_id": "bge_BGE_136_III_513", "citation_string_de": "BGE 136 III 513", "court": "bge"}
            if path == "/api/erwaegung/bge_BGE_136_III_513/2.3":
                return {"decision_id": "bge_BGE_136_III_513", "e_number": "2.3", "text": "served"}
            return super().get(path, params)
    client = Fake()
    rows = api.resolve([{"reference": "BGE 136 III 513 E. 2.3"}], client=client)
    assert rows[0]["status"] == "resolved" and rows[0]["pinpoint_status"] == "retrieved"
    assert api.passage("bge_BGE_136_III_513", "2.3", client=client)["text_plain"] == "served"
    assert api.tool("cite", client=client, reference="BGE 136 III 513")["decision_id"] == "bge_BGE_136_III_513"
    with pytest.raises(APIError):
        api.tool("cite", client=client, reference="BGE 999 III 1")
    assert [t["name"] for t in api.tools(client=client)] == ["cite", "find_leading_cases"]


def test_tool_json_prefers_the_rest_endpoint_and_falls_back_to_mcp():
    seen = []
    def opener(request, timeout):
        seen.append((request.get_method(), request.full_url))
        if request.full_url.endswith("/api/tool/find_leading_cases"):
            body = json.dumps({"cases": [{"decision_id": "a"}], "_tool": "find_leading_cases"})
        elif request.full_url.endswith("/api/tool/old_tool"):
            from urllib.error import HTTPError
            raise HTTPError(request.full_url, 404, "Not Found", {}, io.BytesIO(b'{"detail": "no route"}'))
        elif request.data:
            body = 'data: {"jsonrpc":"2.0","id":1,"result":{"content":[{"type":"text","text":"markdown"}],"isError":false}}\n\n'
        else:
            body = "{}"
        class R(io.BytesIO):
            headers = {"Content-Type": "text/event-stream" if request.data and "tool/" not in request.full_url else "application/json"}
        return R(body.encode())
    client = Client(opener=opener, sleep=lambda s: None)
    assert client.tool_json("find_leading_cases", {"query": "x"})["cases"] == [{"decision_id": "a"}]
    fallback = client.tool_json("old_tool", {})
    assert fallback == {"text": "markdown", "_tool": "old_tool"} and seen[-1][1].endswith("/")


def test_doctor_cite_check_accepts_the_direct_bge_id():
    # the service drops the entscheidsuche BGE ids (#40): the direct
    # scraper's id is an equally good answer to the reference check
    from opencaselaw_cli import cli
    assert "bge_136 III 513" in cli._CITE_CHECK_IDS
    assert "bge_BGE_136_III_513" in cli._CITE_CHECK_IDS
