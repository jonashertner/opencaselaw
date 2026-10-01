"""Identity exposure on the research surface (field test, 2026-09):

1. A consolidated proceeding is filed under its lead docket; a client holding
   a joined docket saw the lead docket and called the decision "unrecognized".
   The record, lookup hits and cite now list `joined_dockets`.
2. The same ruling can be stored under two ids (bge_143 III 38 and
   bge_BGE_143_III_38); the representation manifest names the canonical one.
   Records, lookup hits, cite and search rows (compact included) now carry
   `canonical_decision_id` + `is_canonical` while the manifest is loaded.

Offline: a fixture decisions.db (schema + alias build) and a fixture manifest.
"""
from __future__ import annotations

import asyncio
import sqlite3
import sys
from pathlib import Path

import jsonschema
import pytest
from mcp.types import CallToolRequest, CallToolRequestParams

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import build_fts5  # noqa: E402
import mcp_server as m  # noqa: E402
from db_schema import SCHEMA_SQL  # noqa: E402
from research_contracts import output_schema, validate_payload  # noqa: E402

LEAD = "bger_1B_242_2022"
BGE_CANONICAL = "bge_BGE_143_III_38"
BGE_DUPLICATE = "bge_143 III 38"
PLAIN = "bger_5A_790_2021"


def _decisions(path):
    c = sqlite3.connect(path)
    c.executescript(SCHEMA_SQL)
    # The production table carries these two columns (the exact lookup selects
    # them); the base schema fixture does not, and without them the lookup
    # degrades to a lean path that skips joined-docket aliases.
    c.execute("ALTER TABLE decisions ADD COLUMN collection TEXT")
    c.execute("ALTER TABLE decisions ADD COLUMN bge_reference TEXT")
    rows = [
        # a consolidated decision: lead 1B_242/2022, joined 1B_243 + 1B_244
        (LEAD, "bger", "CH", "1B 242/2022", "2022-05-30", "de", "Strafverfahren",
         "Bundesgericht 30.05.2022 1B 242/2022 (1B_242/2022)\n"
         "1B_242/2022, 1B_243/2022 und 1B_244/2022\nUrteil vom 30. Mai 2022\n",
         "https://www.bger.ch/x"),
        # the same BGE ruling stored twice (direct scrape + entscheidsuche id)
        (BGE_DUPLICATE, "bge", "CH", "143 III 38", "2016-12-14", "de", None, "BGE 143 III 38 text", "https://bge/x"),
        (BGE_CANONICAL, "bge", "CH", "BGE 143 III 38", "2016-12-14", "de", None, "BGE 143 III 38 text", "https://bge/y"),
        # an ordinary decision the manifest does not know
        (PLAIN, "bger", "CH", "5A 790/2021", "2022-02-01", "de", None, "Urteil 5A_790/2021", "https://www.bger.ch/y"),
    ]
    c.executemany(
        "INSERT INTO decisions (decision_id, court, canton, docket_number, decision_date, "
        "language, title, full_text, source_url) VALUES (?,?,?,?,?,?,?,?,?)", rows)
    c.commit()
    build_fts5._build_docket_aliases(c)
    c.commit()
    c.close()
    return path


def _manifest(path):
    c = sqlite3.connect(path)
    c.executescript(
        "CREATE TABLE decision_representations (canonical_decision_id TEXT NOT NULL, "
        "member_decision_id TEXT NOT NULL, canton TEXT NOT NULL, relation_type TEXT NOT NULL, "
        "evidence_method TEXT NOT NULL, confidence REAL NOT NULL, date_match INTEGER NOT NULL DEFAULT 1, "
        "PRIMARY KEY (canonical_decision_id, member_decision_id), UNIQUE (member_decision_id));"
        "CREATE INDEX idx_repr_member ON decision_representations(member_decision_id);")
    c.executemany("INSERT INTO decision_representations VALUES (?,?,?,?,?,?,1)", [
        (BGE_CANONICAL, BGE_CANONICAL, "CH", "canonical", "bge_dual_id", 1.0),
        (BGE_CANONICAL, BGE_DUPLICATE, "CH", "duplicate_representation", "bge_dual_id", 1.0),
    ])
    c.commit()
    c.close()
    return path


def _rconn(p):
    c = sqlite3.connect(p)
    c.row_factory = sqlite3.Row
    return c


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    dbp = str(_decisions(tmp_path / "decisions.db"))
    monkeypatch.setattr(m, "get_db", lambda: _rconn(dbp))
    monkeypatch.setattr(m, "REPRESENTATION_MANIFEST_DB_PATH", Path(_manifest(tmp_path / "manifest.db")))
    monkeypatch.setattr(m, "_manifest_warned", False)
    for name, flag in (("CANONICAL_DB_PATH", "_canonical_warned"), ("GRAPH_DB_PATH", "_graph_warned")):
        monkeypatch.setattr(m, name, Path(tmp_path / f"missing-{name}.db"))
        monkeypatch.setattr(m, flag, False, raising=False)
    for name in ("_capture_event", "_record_tool_call", "_record_tool_outcome", "_record_query"):
        monkeypatch.setattr(m, name, lambda *a, **k: None)
    monkeypatch.setattr(m, "_overlay_enabled", lambda: False)
    monkeypatch.setattr(m, "_auto_link_citations", lambda text: text)
    monkeypatch.setattr(m, "_pinpoint_enrich_results", lambda *a, **k: None)
    token = m._ctx_client_ua.set("")
    yield tmp_path
    m._ctx_client_ua.reset(token)


def no_manifest(monkeypatch, tmp_path):
    monkeypatch.setattr(m, "REPRESENTATION_MANIFEST_DB_PATH", Path(tmp_path / "absent-manifest.db"))
    monkeypatch.setattr(m, "_manifest_warned", False)


# ── 1. joined dockets ────────────────────────────────────────────────────

def test_decision_record_lists_every_joined_docket(corpus):
    lead = m.get_decision_by_id(LEAD)
    assert lead["joined_dockets"] == ["1B_243/2022", "1B_244/2022"]
    via_alias = m.get_decision_by_id("1B_243/2022")
    assert via_alias["decision_id"] == LEAD and via_alias["resolved_via"] == "joined_docket_alias"
    assert via_alias["joined_dockets"] == ["1B_243/2022", "1B_244/2022"]
    assert "joined_dockets" not in m.get_decision_by_id(PLAIN)  # absent, never an empty list
    validate_payload("get_decision", lead)
    jsonschema.validate(lead, output_schema("get_decision"))


def test_lookup_hits_list_joined_dockets_and_canonical_ids(corpus, monkeypatch):
    exact = m._lookup_case_number("1B_243/2022", 25, exact=True)
    assert [h["decision_id"] for h in exact["results"]] == [LEAD]
    hit = exact["results"][0]
    assert hit["docket_number"] == "1B 242/2022" and hit["joined_dockets"] == ["1B_243/2022", "1B_244/2022"]
    assert hit["canonical_decision_id"] == LEAD and hit["is_canonical"] is True
    validate_payload("lookup", exact)
    jsonschema.validate(exact, output_schema("lookup"))
    # the site search box path (search_fts5 hits) carries the same fields; it is
    # reached only when nothing matches exactly, so the exact step is emptied here
    monkeypatch.setattr(m, "_looks_like_docket_query", lambda q: True)
    monkeypatch.setattr(m, "_lookup_exact", lambda qn, limit=25: {"total": 0, "results": []})
    rows = [dict(r) for r in _rconn(str(corpus / "decisions.db")).execute(
        "SELECT * FROM decisions WHERE decision_id IN (?, ?)", (LEAD, BGE_DUPLICATE))]
    monkeypatch.setattr(m, "search_fts5", lambda **k: (rows, len(rows)))
    loose = m._lookup_case_number("1B_242/2022", 25)
    by_id = {h["decision_id"]: h for h in loose["results"]}
    assert by_id[LEAD]["joined_dockets"] == ["1B_243/2022", "1B_244/2022"]
    assert "joined_dockets" not in by_id[BGE_DUPLICATE]
    assert by_id[BGE_DUPLICATE]["canonical_decision_id"] == BGE_CANONICAL and by_id[BGE_DUPLICATE]["is_canonical"] is False
    validate_payload("lookup", loose)


def test_cite_carries_joined_dockets_and_keeps_decision_id(corpus):
    cite = m._handle_cite(reference="1B_243/2022")
    assert cite["exists"] is True and cite["decision_id"] == LEAD
    assert cite["joined_dockets"] == ["1B_243/2022", "1B_244/2022"]
    assert cite["canonical_decision_id"] == LEAD and cite["is_canonical"] is True
    validate_payload("cite", cite)
    jsonschema.validate(cite, output_schema("cite"))
    plain = m._handle_cite(reference=PLAIN)
    assert "joined_dockets" not in plain and plain["canonical_decision_id"] == PLAIN


# ── 2. duplicate representations ─────────────────────────────────────────

def test_decision_record_names_its_canonical_representation(corpus, monkeypatch):
    duplicate = m.get_decision_by_id(BGE_DUPLICATE)
    assert duplicate["decision_id"] == BGE_DUPLICATE  # the requested row is served, never swapped
    assert duplicate["canonical_decision_id"] == BGE_CANONICAL and duplicate["is_canonical"] is False
    canonical = m.get_decision_by_id(BGE_CANONICAL)
    assert canonical["canonical_decision_id"] == BGE_CANONICAL and canonical["is_canonical"] is True
    unknown = m.get_decision_by_id(PLAIN)  # not in the manifest: canonical is itself
    assert unknown["canonical_decision_id"] == PLAIN and unknown["is_canonical"] is True
    validate_payload("get_decision", duplicate)
    cite = m._handle_cite(reference=BGE_DUPLICATE)
    assert cite["decision_id"] == BGE_DUPLICATE and cite["canonical_decision_id"] == BGE_CANONICAL
    assert cite["is_canonical"] is False
    validate_payload("cite", cite)
    # without the manifest the fields are absent, not false
    no_manifest(monkeypatch, corpus)
    assert "canonical_decision_id" not in m.get_decision_by_id(BGE_DUPLICATE)
    assert "is_canonical" not in m._handle_cite(reference=BGE_DUPLICATE)


def test_attach_canonical_ids_is_one_batched_lookup_and_never_raises(corpus, monkeypatch):
    rows = [{"decision_id": BGE_DUPLICATE}, {"decision_id": PLAIN}, {"decision_id": None}, "not a row"]
    opened = []
    real = m._get_manifest_conn

    def counting():
        opened.append(1)
        return real()
    monkeypatch.setattr(m, "_get_manifest_conn", counting)
    m._attach_canonical_ids(rows)
    assert len(opened) == 1
    assert rows[0]["canonical_decision_id"] == BGE_CANONICAL and rows[0]["is_canonical"] is False
    assert rows[1]["canonical_decision_id"] == PLAIN and rows[1]["is_canonical"] is True
    assert rows[2] == {"decision_id": None} and rows[3] == "not a row"
    monkeypatch.setattr(m, "_get_manifest_conn", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    untouched = [{"decision_id": PLAIN}]
    m._attach_canonical_ids(untouched)
    assert untouched == [{"decision_id": PLAIN}]


async def wire(name, arguments):
    handler = m.server.request_handlers[CallToolRequest]
    return (await handler(CallToolRequest(method="tools/call", params=CallToolRequestParams(name=name, arguments=arguments)))).root


def _rest_client(monkeypatch):
    import uvicorn
    from starlette.testclient import TestClient
    captured = {}
    monkeypatch.setattr(m, "_warm_page_cache", lambda: None)
    monkeypatch.setattr(m, "_log_startup", lambda: None)
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: captured.update(app=app))
    m.main_remote("127.0.0.1", 0)
    return TestClient(captured["app"])


def test_search_rows_carry_canonical_ids_on_rest_and_mcp(corpus, monkeypatch):
    rows = [dict(r) for r in _rconn(str(corpus / "decisions.db")).execute(
        "SELECT * FROM decisions WHERE decision_id IN (?, ?, ?) ORDER BY decision_id",
        (BGE_DUPLICATE, BGE_CANONICAL, PLAIN))]
    for r in rows:
        r.pop("json_data", None)
    monkeypatch.setattr(m, "search_fts5", lambda **k: ([dict(r) for r in rows], len(rows)))
    monkeypatch.setattr(m, "_rule_statement", lambda *a, **k: None)
    monkeypatch.setattr(m, "_build_citation_strings", lambda *a, **k: {
        "citation_string_de": "stored-de", "citation_string_fr": "stored-fr",
        "citation_string_it": "stored-it", "canonical_url": "https://example.invalid/record"})
    client = _rest_client(monkeypatch)
    for fields in ("compact", "full"):
        rest = client.get("/api/decisions", params={"query": "x", "limit": 10, "fields": fields}).json()
        by_id = {r["decision_id"]: r for r in rest["results"]}
        assert by_id[BGE_DUPLICATE]["canonical_decision_id"] == BGE_CANONICAL and by_id[BGE_DUPLICATE]["is_canonical"] is False
        assert by_id[BGE_CANONICAL]["is_canonical"] is True and by_id[PLAIN]["canonical_decision_id"] == PLAIN
        if fields == "compact":
            assert set(by_id[PLAIN]) == set(m._RESEARCH_COMPACT_KEYS) | {"canonical_decision_id", "is_canonical"}
        validate_payload("search_decisions", rest)
        jsonschema.validate(rest, output_schema("search_decisions"))
    result = asyncio.run(wire("search_decisions", {"query": "x", "limit": 10, "fields": "compact"}))
    assert not result.isError
    mcp_rows = {r["decision_id"]: r for r in result.structuredContent["results"]}
    # the MCP path already folds the two BGE id forms into one display row
    assert PLAIN in mcp_rows and mcp_rows[PLAIN]["canonical_decision_id"] == PLAIN
    assert all("canonical_decision_id" in r for r in mcp_rows.values())
    jsonschema.validate(result.structuredContent, output_schema("search_decisions"))
    # without the manifest, compact rows are exactly the documented compact keys
    no_manifest(monkeypatch, corpus)
    bare = client.get("/api/decisions", params={"query": "x", "limit": 10, "fields": "compact"}).json()
    assert all(set(r) == set(m._RESEARCH_COMPACT_KEYS) for r in bare["results"])
