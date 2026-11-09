"""Offline research contract parity and MCP wire-error regression checks."""
from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
from urllib.parse import urlsplit

import jsonschema
import pytest
from mcp.types import CallToolRequest, CallToolRequestParams
from openapi_spec_validator import validate as validate_openapi
from starlette.testclient import TestClient

import mcp_server as m
from research_contracts import (
    CONTRACT_VERSION,
    RESEARCH_MODELS,
    RESEARCH_PATHS,
    output_schema,
    validate_payload,
)

_REAL_RULE_STATEMENT = m._rule_statement


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    for name in ("_capture_event", "_record_tool_call", "_record_tool_outcome", "_record_query"):
        monkeypatch.setattr(m, name, lambda *a, **k: None)
    monkeypatch.setattr(m, "_overlay_enabled", lambda: False)
    monkeypatch.setattr(m, "_representation_info", lambda *a: None)
    # No representation manifest here: compact rows then carry exactly the
    # documented compact keys (tests/test_identity_exposure.py covers the
    # canonical_decision_id / is_canonical keys the manifest adds).
    monkeypatch.setattr(m, "REPRESENTATION_MANIFEST_DB_PATH", Path("/nonexistent/representation_manifest.db"))
    monkeypatch.setattr(m, "_manifest_warned", True)
    monkeypatch.setattr(m, "_count_citations", lambda *a: (0, 0))
    monkeypatch.setattr(m, "_materials_for_decision", lambda *a: None)
    monkeypatch.setattr(m, "_pinpoint_enrich_results", lambda *a, **k: None)
    monkeypatch.setattr(m, "_auto_link_citations", lambda text: text)
    monkeypatch.setattr(m, "_rule_statement", lambda *a, **k: "stored excerpt")
    monkeypatch.setattr(m, "_build_citation_strings", lambda *a, **k: {
        "citation_string_de": "stored-de", "citation_string_fr": "stored-fr",
        "citation_string_it": "stored-it", "canonical_url": "https://example.invalid/record",
    })
    token = m._ctx_client_ua.set("")
    yield
    m._ctx_client_ua.reset(token)


@pytest.fixture
def client(monkeypatch):
    import uvicorn
    captured = {}
    monkeypatch.setattr(m, "_warm_page_cache", lambda: None)
    monkeypatch.setattr(m, "_log_startup", lambda: None)
    monkeypatch.setattr(m, "REMOTE_MODE", m.REMOTE_MODE)
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: captured.update(app=app))
    m.main_remote("127.0.0.1", 0)
    # Do not run the application's lifespan, which warms real databases.
    return TestClient(captured["app"])


def decision():
    return {
        "decision_id": "test_record", "docket_number": "test-docket",
        "court": "bger", "decision_date": "2020-01-01", "language": "de",
        "full_text": "stored decision text", "source_url": "https://example.invalid/source",
        "content_hash": "fixture-hash", "unrecognised_metadata": {"preserve": True},
    }


async def wire_call(name, arguments):
    handler = m.server.request_handlers[CallToolRequest]
    result = await handler(CallToolRequest(
        method="tools/call", params=CallToolRequestParams(name=name, arguments=arguments)))
    return result.root


@pytest.mark.parametrize("name,handler,args,path,payload", [
    ("get_erwaegung", "_handle_get_erwaegung", {"decision_id": "test_record", "e_number": "2"},
     "/api/erwaegung/test_record/2", {
         "decision_id": "test_record", "e_number": "2", "text": "stored paragraph",
         "composed_of": ["2.1"], "parts": [{"e_number": "2.1", "text": "stored paragraph", "extra": 1}],
         "extra": {"source": True},
     }),
    ("get_law", "get_law", {"abbreviation": "FIXTURE"}, "/api/laws/FIXTURE", {
        "sr_number": "fixture", "title": "Stored title", "abbreviation": "FIXTURE",
        "articles": [{"article_num": "1", "heading": None, "text": "stored article", "xml": "<article/>", "extra": 1}],
        "snapshot_date": "2020-01-01", "pending_changes": [{"date": "2030-01-01"}], "extra": True,
    }),
    ("find_citations", "find_citations", {"decision_id": "test_record"}, "/api/citations/test_record", {
        "decision_id": "test_record", "direction": "both", "limit": 50, "offset": 0,
        "incoming": [], "outgoing": [], "incoming_total": 0, "outgoing_total": 0,
        "incoming_has_more": False, "outgoing_has_more": False, "extra": True,
    }),
    ("cite", "_handle_cite", {"reference": "test_record"}, "/api/cite?reference=test_record", {
        "exists": True, "decision_id": "test_record", "citation_string": "stored citation",
        "citation_string_de": "stored citation", "extra": True,
    }),
])
def test_rest_mcp_payload_parity(client, monkeypatch, name, handler, args, path, payload):
    monkeypatch.setattr(m, handler, lambda *a, **k: copy.deepcopy(payload))
    response = client.get(path)
    assert response.status_code == 200
    assert response.json() == payload
    wire = asyncio.run(wire_call(name, args))
    assert not wire.isError
    assert wire.structuredContent == payload
    jsonschema.validate(payload, output_schema(name))
    validate_payload(name, payload)
    assert wire.content and wire.content[0].text


def test_decision_preserves_evidence_and_full_text_false(client, monkeypatch):
    monkeypatch.setattr(m, "get_decision_by_id", lambda *a: decision())
    for include in (True, False):
        rest = client.get("/api/decisions/test_record", params={"full_text": include}).json()
        wire = asyncio.run(wire_call("get_decision", {"decision_id": "test_record", "full_text": include}))
        assert not wire.isError
        assert wire.structuredContent == rest
        assert ("full_text" in rest) is include
        assert rest["unrecognised_metadata"] == {"preserve": True}
        assert "stored-de" in wire.content[0].text


def test_long_decision_is_bounded_with_explicit_full_text_recovery(client, monkeypatch):
    original = {**decision(), "full_text": "x" * 300_001}
    snapshot = copy.deepcopy(original)
    monkeypatch.setattr(m, "get_decision_by_id", lambda *a: original)
    wire = asyncio.run(wire_call("get_decision", {"decision_id": "test_record"}))
    assert not wire.isError
    payload = wire.structuredContent
    assert len(payload["full_text"]) == 200_000
    assert payload["full_text_total_chars"] == 300_001
    assert payload["full_text_returned_chars"] == 200_000
    assert payload["full_text_truncated"] is True
    assert "Truncated: first 200,000 of 300,001" in wire.content[0].text
    assert payload["content_hash"] == original["content_hash"]
    url = urlsplit(payload["full_text_url"])
    assert url.path == "/api/decisions/test_record"
    assert url.query == "full_text=true"
    restored = client.get(url.path + "?" + url.query).json()
    assert restored["full_text"] == original["full_text"]
    assert original == snapshot
    jsonschema.validate(payload, output_schema("get_decision"))


def test_metadata_only_rule_matches_rest_without_mutating_source(client, monkeypatch):
    original = {**decision(), "full_text": "Stored body text for the fixture. " * 100, "regeste": None}
    snapshot = copy.deepcopy(original)
    monkeypatch.setattr(m, "get_decision_by_id", lambda *a: original)
    monkeypatch.setattr(m, "_rule_statement", _REAL_RULE_STATEMENT)
    assert _REAL_RULE_STATEMENT(original) is not None
    wire = asyncio.run(wire_call("get_decision", {"decision_id": "test_record", "full_text": False}))
    rest = client.get("/api/decisions/test_record?full_text=false").json()
    assert wire.structuredContent == rest
    assert rest["rule_statement"] is None
    assert "full_text" not in rest
    assert "## Full Text" not in wire.content[0].text
    assert original == snapshot


@pytest.mark.parametrize("widget", [False, True])
def test_search_has_structured_records_with_and_without_widgets(client, monkeypatch, widget):
    def search(**kwargs):
        kwargs["meta"].update(total_is_lower_bound=True, result_set_id="fixture-set")
        return [decision()], 1001
    monkeypatch.setattr(m, "search_fts5", search)
    monkeypatch.setattr(m, "_DECISION_TOOL_META", {"ui": {}} if widget else None)
    rest = client.get("/api/decisions", params={"query": "fixture", "limit": 1}).json()
    wire = asyncio.run(wire_call("search_decisions", {"query": "fixture", "limit": 1}))
    assert not wire.isError
    payload = wire.structuredContent
    for key in rest:
        assert payload[key] == rest[key]
    assert ("decisions" in payload) is widget
    if widget:
        expected = m._decision_hits_structured([decision()], "fixture", total=1001, total_is_lower_bound=True)
        for key, value in expected.items():
            assert payload[key] == value
    assert payload["has_more"] is True
    assert payload["total_is_lower_bound"] is True
    assert payload["next_offset"] == 1
    assert "Found 1001+" in wire.content[0].text
    jsonschema.validate(payload, output_schema("search_decisions"))


def test_search_empty_page_is_valid_success(monkeypatch):
    monkeypatch.setattr(m, "search_fts5", lambda **k: ([], 0))
    wire = asyncio.run(wire_call("search_decisions", {"query": "fixture"}))
    assert not wire.isError
    assert wire.structuredContent["results"] == []
    assert wire.structuredContent["has_more"] is False


@pytest.mark.parametrize("fields,count,widget", [("compact", 805, True), ("full", 51, False)])
def test_search_compact_projection_and_page_bounds(client, monkeypatch, fields, count, widget):
    large_text = "oversized-source-field " * 1000
    rows = [{**decision(), "decision_id": f"record_{i}", "docket_number": f"fixture_{i}",
             "full_text": large_text, "regeste": large_text, "title": large_text,
             "snippet": "<mark>stored</mark> snippet"} for i in range(count)]
    snapshot = copy.deepcopy(rows)
    def search(**kwargs):
        offset = kwargs.get("offset", 0)
        return rows[offset:offset + kwargs["limit"]], len(rows)
    monkeypatch.setattr(m, "search_fts5", search)
    monkeypatch.setattr(m, "_DECISION_TOOL_META", {"ui": {}} if widget else None)
    args = {"query": "fixture", "limit": 1000, "fields": fields}
    wire = asyncio.run(wire_call("search_decisions", args))
    rest = client.get("/api/decisions", params=args).json()
    assert not wire.isError
    payload = wire.structuredContent
    assert payload["results"] == rest["results"]
    assert payload["returned"] == min(count, 800)
    assert payload["has_more"] is (count > 800)
    assert payload["next_offset"] == (800 if count > 800 else None)
    assert "oversized-source-field" not in json.dumps(payload)
    assert "oversized-source-field" not in wire.content[0].text
    assert all(set(row) == set(m._RESEARCH_COMPACT_KEYS) for row in payload["results"])
    if widget:
        assert len(payload["decisions"]) == 800
        assert "snippet_html" in payload["decisions"][0]
        assert payload["decisions"][0]["title"] is None
        next_page = asyncio.run(wire_call("search_decisions", {**args, "offset": payload["next_offset"]}))
        assert next_page.structuredContent["returned"] == 5
        assert next_page.structuredContent["has_more"] is False
        assert next_page.structuredContent["results"][0]["decision_id"] == "record_800"
    assert rows == snapshot


def test_search_pagination_consumes_duplicate_display_representations(monkeypatch):
    row = {**decision(), "court": "bge"}
    monkeypatch.setattr(m, "search_fts5", lambda **k: ([row.copy(), row.copy()], 10))
    wire = asyncio.run(wire_call("search_decisions", {"query": "fixture", "limit": 2}))
    assert not wire.isError
    assert wire.structuredContent["returned"] == 1
    assert wire.structuredContent["has_more"] is True
    assert wire.structuredContent["next_offset"] == 2


def test_lookup_uses_the_same_additive_schema(client, monkeypatch):
    payload = {"query": "fixture", "is_case_number": True, "total": 2,
               "results": [decision(), {**decision(), "decision_id": "other_record"}], "extra": True}
    monkeypatch.setattr(m, "_lookup_case_number", lambda *a: copy.deepcopy(payload))
    response = client.get("/api/lookup", params={"q": "fixture", "limit": 25})
    assert response.json() == payload
    validate_payload("lookup", payload)
    jsonschema.validate(payload, output_schema("lookup"))


@pytest.mark.parametrize("name,args,patch", [
    ("search_decisions", {"query": "x", "date_from": "invalid-date"}, None),
    ("get_decision", {"decision_id": "missing"}, "get_decision_by_id"),
    ("get_erwaegung", {"decision_id": "missing", "e_number": "2"}, "_handle_get_erwaegung"),
    ("get_law", {"abbreviation": "missing"}, "get_law"),
    ("find_citations", {"decision_id": "missing"}, "find_citations"),
    ("cite", {"reference": ""}, "_handle_cite"),
])
def test_handled_errors_set_wire_iserror(monkeypatch, name, args, patch):
    if patch:
        value = None if name == "get_decision" else {"error": "Fixture backend unavailable", "extra": True}
        monkeypatch.setattr(m, patch, lambda *a, **k: copy.deepcopy(value))
    wire = asyncio.run(wire_call(name, args))
    assert wire.isError
    assert wire.structuredContent["error"]
    jsonschema.validate(wire.structuredContent, output_schema(name))


def test_cite_unresolved_reference_is_completed_lookup(monkeypatch):
    payload = {"exists": False, "queried": "fixture", "close_matches": [], "_note": "stored note"}
    monkeypatch.setattr(m, "_handle_cite", lambda **k: payload)
    wire = asyncio.run(wire_call("cite", {"reference": "fixture"}))
    assert not wire.isError
    assert wire.structuredContent == payload


def test_timeout_is_a_wire_error(monkeypatch):
    async def slow(*a, **k):
        await asyncio.sleep(1)
    monkeypatch.setattr(m, "_handle_call_tool_inner", slow)
    monkeypatch.setattr(m, "TOOL_DISPATCH_TIMEOUT_S", 0.001)
    wire = asyncio.run(wire_call("cite", {"reference": "fixture"}))
    assert wire.isError
    assert wire.structuredContent["error"] == "server_timeout"


def test_unexpected_backend_exception_is_a_wire_error(monkeypatch):
    def broken(**kwargs):
        raise RuntimeError("fixture backend failure")
    monkeypatch.setattr(m, "_handle_cite", broken)
    wire = asyncio.run(wire_call("cite", {"reference": "fixture"}))
    assert wire.isError
    assert "fixture backend failure" in wire.structuredContent["error"]


def test_ignored_arguments_warning_survives_structured_output(monkeypatch):
    monkeypatch.setattr(m, "_handle_cite", lambda **kw: {"exists": False, "close_matches": []})
    wire = asyncio.run(wire_call("cite", {"reference": "fixture", "unknown_argument": True}))
    assert not wire.isError
    assert "ignored unrecognised parameter(s) unknown_argument" in wire.content[0].text
    assert wire.structuredContent == {
        "exists": False, "close_matches": [], "ignored_arguments": ["unknown_argument"],
    }


def test_commercial_note_is_preserved_once(monkeypatch):
    monkeypatch.setattr(m, "_handle_cite", lambda **kw: {"exists": False, "close_matches": []})
    token = m._ctx_client_ua.set("FixtureCommercialClient/1.0")
    try:
        wire = asyncio.run(wire_call("cite", {"reference": "fixture"}))
    finally:
        m._ctx_client_ua.reset(token)
    assert wire.content[0].text.count(m._OPEN_ACCESS_NOTE) == 1


def test_schema_violation_cannot_be_success(monkeypatch):
    monkeypatch.setattr(m, "_handle_cite", lambda **k: {"exists": "definitely"})
    wire = asyncio.run(wire_call("cite", {"reference": "fixture"}))
    assert wire.isError
    assert wire.structuredContent["error"] == "response_contract_error"


def test_curated_openapi_contains_only_research_and_closed_refs(client):
    full = client.get("/api/openapi.json").json()
    response = client.get("/api/research/openapi.json")
    assert response.status_code == 200
    spec = response.json()
    validate_openapi(spec)
    validate_openapi(full)
    assert spec["openapi"] == "3.0.3"
    assert spec["x-opencaselaw-contract-version"] == CONTRACT_VERSION
    assert set(spec["paths"]) == set(RESEARCH_PATHS.values())
    assert set(spec["x-opencaselaw-capabilities"]) == set(RESEARCH_MODELS)
    assert not any(part in json.dumps(spec) for part in ("/billing/", "/quota/", "StrengthenRequest", "license_key"))
    for name, path in RESEARCH_PATHS.items():
        operation = spec["paths"][path]["get"]
        assert operation["x-opencaselaw-operation"] == name
        schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
        refs = {branch["$ref"] for branch in schema["anyOf"]}
        assert refs == {f"#/components/schemas/{RESEARCH_MODELS[name].__name__}",
                        "#/components/schemas/ResearchError"}
        assert operation["responses"]["422"]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/HTTPValidationError"}
        assert ("404" in operation["responses"]) is (name in ("get_decision", "get_law"))
        assert operation["operationId"] == full["paths"][path]["get"]["operationId"]
    assert spec["paths"]["/laws/{abbreviation}"]["get"]["responses"]["200"]["content"]["application/xml"]
    # OpenAPI 3.0.3 has no null type; optional fields must be `nullable`.
    assert '"null"' not in json.dumps(spec["components"]).replace('"default": null', "")
    assert spec["components"]["schemas"]["DecisionRecord"]["additionalProperties"] is True
    assert spec["components"]["schemas"]["LookupHit"]["properties"]["citation"]["nullable"] is True

    def check_refs(node):
        if isinstance(node, dict):
            if "$ref" in node:
                assert node["$ref"].startswith("#/")
                target = spec
                for part in node["$ref"][2:].split("/"):
                    target = target[part]
                assert isinstance(target, dict)
            for child in node.values():
                check_refs(child)
        elif isinstance(node, list):
            for child in node:
                check_refs(child)
    check_refs(spec)


_RESEARCH_ROUTES = set(RESEARCH_PATHS.values())


def test_live_openapi_documents_keep_their_existing_response_schemas(client):
    """The typed contracts live only in the curated document. The application
    document and the Copilot Studio subset (hand-typed 200 schemas, plain
    HTTPValidationError 422) must read exactly as before: Copilot Studio
    binds output variables to those schemas and rejects anyOf unions."""
    full = client.get("/api/openapi.json").json()
    for path in _RESEARCH_ROUTES:
        responses = full["paths"][path]["get"]["responses"]
        assert responses["200"]["content"]["application/json"]["schema"] == {}, path
        assert responses["422"]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/HTTPValidationError"}, path
        assert "404" not in responses, path
    for name in ("ResearchError", "ResearchHTTPError", *(m.__name__ for m in RESEARCH_MODELS.values())):
        assert name not in full["components"]["schemas"], name
    copilot = client.get("/api/openapi.copilot.json").json()
    assert set(copilot["components"]["schemas"]) <= {"HTTPValidationError", "ValidationError"}
    for path, item in copilot["paths"].items():
        for method, operation in item.items():
            schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
            assert schema.get("type") == "object" and schema.get("properties"), (method, path)
            assert not any(key in schema for key in ("$ref", "anyOf", "oneOf", "allOf")), (method, path)
            if "422" in operation["responses"]:
                assert operation["responses"]["422"]["content"]["application/json"]["schema"] == {
                    "$ref": "#/components/schemas/HTTPValidationError"}, (method, path)


def test_rest_error_status_and_payload_are_unchanged(client, monkeypatch):
    monkeypatch.setattr(m, "get_decision_by_id", lambda *a: None)
    response = client.get("/api/decisions/missing")
    assert response.status_code == 404
    assert response.json() == {"detail": "Decision not found: missing"}
    response = client.get("/api/decisions", params={"query": "fixture", "date_from": "invalid"})
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], str)


def test_all_core_tool_schemas_are_valid_json_schema():
    named = {tool.name: tool for tool in m._list_tools()}
    for name in RESEARCH_MODELS.keys() - {"lookup"}:
        assert named[name].outputSchema == output_schema(name)
        jsonschema.Draft202012Validator.check_schema(named[name].outputSchema)


def test_table_of_contents_and_historical_statute_metadata_are_preserved():
    payload = {"sr_number": "fixture", "articles": [{"article_num": "1", "heading": None}],
               "verbatim_quotation": "not_guaranteed", "snapshot_date": "2020-01-01",
               "unknown_provenance": {"keep": True}}
    validate_payload("get_law", payload)
    jsonschema.validate(payload, output_schema("get_law"))
    assert payload["unknown_provenance"] == {"keep": True}


@pytest.mark.parametrize("is_canonical", [True, False])
def test_decision_with_representations_matches_contract(monkeypatch, is_canonical):
    """A record stored under two ids (GE DAS/C pairs) must not turn into
    response_contract_error: `representations` is a list, the canonical pair
    has its own fields (Mnemosyne report 2026-11-09, ge_gerichte_DAS_192_2020)."""
    import sqlite3
    monkeypatch.setattr(m, "get_decision_by_id", lambda *a: decision())
    other = "ge_member" if is_canonical else "ge_canonical"
    monkeypatch.setattr(m, "_representation_info", lambda *a: {
        "canonical_decision_id": "test_record" if is_canonical else other,
        "is_canonical": is_canonical,
        "members": ([{"decision_id": other, "relation_type": "alt_representation",
                      "evidence_method": "shared_source_url", "confidence": 1.0}]
                    if is_canonical else []),
    })

    def fake_db():
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE decisions (decision_id TEXT, docket_number TEXT, source_url TEXT)")
        conn.execute("INSERT INTO decisions VALUES (?, 'C/1/2020', 'https://example.invalid/m')", (other,))
        return conn
    monkeypatch.setattr(m, "get_db", fake_db)

    wire = asyncio.run(wire_call("get_decision", {"decision_id": "test_record", "full_text": False}))
    assert not wire.isError, wire.content[-1].text
    payload = wire.structuredContent
    jsonschema.validate(payload, output_schema("get_decision"))
    assert "## Representations" in wire.content[0].text
    assert payload["is_canonical"] is is_canonical
    if is_canonical:
        assert payload["canonical_decision_id"] == "test_record"
        assert payload["representations"] == [{
            "decision_id": "ge_member", "relation_type": "alt_representation",
            "evidence_method": "shared_source_url", "confidence": 1.0,
            "source_url": "https://example.invalid/m"}]
    else:
        assert payload["canonical_decision_id"] == "ge_canonical"
        assert "representations" not in payload
