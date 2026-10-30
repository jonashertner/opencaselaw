"""Server: passages the structure index lacks, degraded-ranking flag, exact lookup."""
from __future__ import annotations

import asyncio
import copy
import sqlite3

import jsonschema
import pytest
from mcp.types import CallToolRequest, CallToolRequestParams

import mcp_server as m
from research_contracts import output_schema, validate_payload

TEXT = """Sachverhalt

A. Die Beschwerdeführerin ...

Erwägungen

1. Die Beschwerde richtet sich gegen ...

2. Streitig ist die Kündigung.

2.1 Nach Art. 336 OR ist die Kündigung missbräuchlich, wenn ...

2.2 Die Vorinstanz hat festgestellt, dass ...

2.3 Selon l'art. 335 al. 1 CO, le contrat de travail conclu pour une durée indéterminée peut être résilié par chacune des parties. Cette liberté n'est pas absolue.

2.3.1 Une sous-considération qui appartient au 2.3.

2.4 Es bleibt zu prüfen, ob ...

3. Die Beschwerde ist abzuweisen.

Demnach erkennt das Bundesgericht:

1. Die Beschwerde wird abgewiesen.
"""


@pytest.mark.parametrize("label", ["Aus den Erwägungen", "Begründung", "Considérant en droit", "A", "II"])
def test_heading_fallback_answers_none_for_a_label_that_is_no_number(label):
    # 2026-10-30: these reached int() and get_erwaegung raised ValueError.
    text = f"{label}\n1. Die Beschwerde ist zulässig.\n2. Sie ist unbegründet."
    assert m._erwaegung_from_text(text, label) is None


def test_heading_fallback_extracts_the_block_to_the_next_same_level_heading():
    block = m._erwaegung_from_text(TEXT, "2.3")
    assert block["text"].startswith("2.3 Selon l'art. 335 al. 1 CO")
    assert "2.3.1 Une sous-considération" in block["text"] and "2.4 Es bleibt" not in block["text"]
    assert block["depth"] == 2 and block["parent"] == "2"
    parent = m._erwaegung_from_text(TEXT, "2")
    assert parent["text"].startswith("2. Streitig") and "2.4 Es bleibt" in parent["text"] and "3. Die Beschwerde" not in parent["text"]
    last = m._erwaegung_from_text(TEXT, "3")
    assert last["text"] == "3. Die Beschwerde ist abzuweisen."  # stops at the dispositive
    assert m._erwaegung_from_text(TEXT, "9") is None
    assert m._erwaegung_from_text(TEXT, "2.3") is not None and m._erwaegung_from_text("", "2.3") is None
    assert m._erwaegung_from_text("consid. 2.3 Selon la jurisprudence constante, le juge ...", "2.3")["text"].startswith("consid. 2.3")
    # "Art. 2.3" inside a sentence is not a heading
    assert m._erwaegung_from_text("Nach Art. 2.3 des Reglements gilt ...", "2.3") is None


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    for name in ("_capture_event", "_record_tool_call", "_record_tool_outcome", "_record_query"):
        monkeypatch.setattr(m, name, lambda *a, **k: None)
    monkeypatch.setattr(m, "_overlay_enabled", lambda: False)
    monkeypatch.setattr(m, "_auto_link_citations", lambda text: text)
    monkeypatch.setattr(m, "_rule_statement", lambda *a, **k: None)
    monkeypatch.setattr(m, "_build_citation_strings", lambda *a, **k: {
        "citation_string_de": "BGE 1 I 1, E. 2.3", "citation_string_fr": "ATF 1 I 1, consid. 2.3",
        "citation_string_it": "DTF 1 I 1, consid. 2.3", "canonical_url": "https://example.invalid/r"})
    monkeypatch.setattr(m, "_resolve_decision_id", lambda x: x)
    monkeypatch.setattr(m, "_fetch_structure_row", lambda *a: {"court": "bge", "decision_date": "2020-01-01", "language": "de"})
    monkeypatch.setattr(m, "get_decision_by_id", lambda *a: {"decision_id": "test_case", "full_text": TEXT, "court": "bge"})
    token = m._ctx_client_ua.set("")
    yield
    m._ctx_client_ua.reset(token)


async def wire(name, arguments):
    handler = m.server.request_handlers[CallToolRequest]
    return (await handler(CallToolRequest(method="tools/call", params=CallToolRequestParams(name=name, arguments=arguments)))).root


def test_get_erwaegung_falls_back_to_the_text_heading_when_no_structure_exists(monkeypatch):
    monkeypatch.setattr(m, "_fetch_structure_paragraphs", lambda *a: [])
    out = m._handle_get_erwaegung(decision_id="test_case", e_number="2.3")
    assert out["text"].startswith("2.3 Selon") and out["text_source"] == "full_text_heading"
    assert out["verbatim_quotation"] == "text_fallback" and "Check its boundaries" in out["_fallback_note"]
    validate_payload("get_erwaegung", out)
    result = asyncio.run(wire("get_erwaegung", {"decision_id": "test_case", "e_number": "2.3"}))
    assert not result.isError and result.structuredContent["text_source"] == "full_text_heading"
    jsonschema.validate(result.structuredContent, output_schema("get_erwaegung"))
    missing = m._handle_get_erwaegung(decision_id="test_case", e_number="9")
    assert missing["text_source"] == "none" and "has no heading numbered '9'" in missing["error"]


def test_get_erwaegung_prefers_the_index_and_falls_back_only_for_absent_numbers(monkeypatch):
    rows = [{"e_number": "2", "parent": None, "depth": 1, "text": "indexed block for E. 2"},
            {"e_number": "4.1", "parent": "4", "depth": 2, "text": "indexed 4.1"}]
    monkeypatch.setattr(m, "_fetch_structure_paragraphs", lambda *a: rows)
    indexed = m._handle_get_erwaegung(decision_id="test_case", e_number="2")
    assert indexed["text"] == "indexed block for E. 2" and indexed["text_source"] == "structure_index"
    fallback = m._handle_get_erwaegung(decision_id="test_case", e_number="2.3")
    assert fallback["text_source"] == "full_text_heading" and fallback["siblings"] == ["2", "4.1"]
    nothing = m._handle_get_erwaegung(decision_id="test_case", e_number="7")
    assert nothing["available_e_numbers"] == ["2", "4.1"] and nothing["text_source"] == "none" and "hint" in nothing


def test_degraded_ranking_is_visible_on_rest_and_mcp(monkeypatch):
    import uvicorn
    from starlette.testclient import TestClient
    def search(**kwargs):
        kwargs["meta"].update(total_is_lower_bound=True, deadline_partial=True)
        return [{"decision_id": "test_case", "docket_number": "test-docket", "court": "bge", "decision_date": "2020-01-01",
                 "language": "de", "title": "Fixture", "regeste": None, "full_text": "stored text"}], 10
    monkeypatch.setattr(m, "search_fts5", search)
    monkeypatch.setattr(m, "_pinpoint_enrich_results", lambda *a, **k: None)
    monkeypatch.setattr(m, "_representation_info", lambda *a: None)
    wire_result = asyncio.run(wire("search_decisions", {"query": "x", "limit": 1}))
    assert not wire_result.isError and wire_result.structuredContent["degraded"] is True
    assert "degraded ranking" in wire_result.structuredContent["note"] and "degraded ranking" in wire_result.content[0].text
    jsonschema.validate(wire_result.structuredContent, output_schema("search_decisions"))
    captured = {}
    monkeypatch.setattr(m, "_warm_page_cache", lambda: None)
    monkeypatch.setattr(m, "_log_startup", lambda: None)
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: captured.update(app=app))
    m.main_remote("127.0.0.1", 0)
    rest = TestClient(captured["app"]).get("/api/decisions", params={"query": "x", "limit": 1}).json()
    assert rest["degraded"] is True and "degraded ranking" in rest["note"]


def test_exact_lookup_returns_only_decisions_carrying_the_label(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE decisions (decision_id TEXT PRIMARY KEY, docket_number TEXT, court TEXT, canton TEXT, "
                 "decision_date TEXT, title TEXT, collection TEXT, bge_reference TEXT)")
    conn.executemany("INSERT INTO decisions VALUES (?,?,?,?,?,?,?,?)", [
        ("bger_4A_747_2012", "4A_747/2012", "bger", "CH", "2013-04-05", "Mietrecht", None, None),
        ("bger_4A_78_2013", "4A_78/2013", "bger", "CH", "2013-06-01", "related", None, None),
        ("bger_6B_1247_2020", "6B_1247/2020", "bger", "CH", "2021-10-07", "fragment host", None, None),
        ("ag_verwaltungsgericht_WBE.2026.33", "WBE.2026.33", "ag_verwaltungsgericht", "AG", "2026-03-01", None, None, None),
        ("zh_gerichte_WBE.2026.33", "WBE.2026.33", "zh_gerichte", "ZH", "2026-03-02", None, None, None),
        ("bge_BGE_136_III_513", "4A_408/2010", "bge", "CH", "2010-10-07", None, "BGE", "136 III 513"),
    ])
    monkeypatch.setattr(m, "get_db", lambda: conn)
    monkeypatch.setattr(m, "_lookup_docket_alias", lambda c, r: [])
    exact = m._lookup_case_number("4A 747/2012", 25, exact=True)
    assert exact["exact"] is True and [h["decision_id"] for h in exact["results"]] == ["bger_4A_747_2012"]
    reused = m._lookup_case_number("WBE.2026.33", 25, exact=True)
    assert sorted(h["decision_id"] for h in reused["results"]) == ["ag_verwaltungsgericht_WBE.2026.33", "zh_gerichte_WBE.2026.33"]
    assert m._lookup_case_number("247/2020", 25, exact=True)["results"] == []
    bge = m._lookup_case_number("ATF 136 III 513", 25, exact=True)
    assert [h["decision_id"] for h in bge["results"]] == ["bge_BGE_136_III_513"]
    validate_payload("lookup", exact)
    jsonschema.validate(exact, output_schema("lookup"))


WRAPPED = """Erwägungen

1. Die Beschwerde ist zulässig (vgl. BGE 120 Ib 1
E. 2a mit Hinweisen).

2. Beschwerdegegenstand bildet die Verfügung.
a) Das Rekursgericht hat festgestellt, dass die Frist gewahrt war.
b) Dagegen wendet der Beschwerdeführer ein, dass die Zustellung nichtig sei.

3. Die Beschwerde ist abzuweisen.
"""


def test_a_line_wrapped_citation_is_not_a_heading_and_lettered_units_come_from_the_block():
    assert m._erwaegung_from_text(WRAPPED, "2a")["text"].startswith("Das Rekursgericht hat festgestellt")
    assert m._erwaegung_from_text(WRAPPED, "2b")["text"].startswith("Dagegen wendet")
    assert m._erwaegung_from_text(WRAPPED, "2a")["parent"] == "2" and m._erwaegung_from_text(WRAPPED, "2c") is None
    assert m._erwaegung_from_text(WRAPPED, "2")["text"].startswith("2. Beschwerdegegenstand")
    # a numeric pinpoint whose only line-start occurrence is a wrapped citation is not found
    wrapped_numeric = "1. Zulässig (vgl. BGE 131 III 115\nE. 3.1 S. 119).\n\n2. In der Sache ...\n"
    assert m._erwaegung_from_text(wrapped_numeric, "3.1") is None
    # a real prefixed heading followed by a capitalised sentence still counts
    assert m._erwaegung_from_text("E. 2.3 Selon la jurisprudence constante, le juge ...", "2.3")["text"].startswith("E. 2.3")
