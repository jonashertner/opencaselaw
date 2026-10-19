"""C1 — instant case-number lookup helper (_lookup_case_number).

The public site's case-number box resolves a docket straight to the decision via
search_fts5's exact-docket fast-path (no Haiku/rerank). Verifies the docket guard
(non-dockets never hit the slow path), R1-safe citation/URL extraction, and the lean shape.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import mcp_server  # noqa: E402


def test_resolves_case_number(monkeypatch):
    monkeypatch.setattr(mcp_server, "_looks_like_docket_query", lambda q: True)
    row = {
        "decision_id": "ag_verwaltungsgericht_WBE.2026.33", "docket_number": "WBE.2026.33",
        "court": "ag_verwaltungsgericht", "canton": "AG", "decision_date": "2026-01-30",
        "title": "Beschwerdeverfahren",
    }
    monkeypatch.setattr(mcp_server, "search_fts5", lambda **k: ([row], 1))
    monkeypatch.setattr(
        mcp_server, "_build_citation_strings",
        lambda r: {"citation_string_de": "WBE.2026.33",
                   "canonical_url": "https://mcp.opencaselaw.ch/entscheid/ag_verwaltungsgericht_WBE.2026.33"},
    )
    res = mcp_server._lookup_case_number("WBE.2026.33")
    assert res["is_case_number"] is True
    assert res["total"] == 1
    h = res["results"][0]
    assert h["decision_id"] == "ag_verwaltungsgericht_WBE.2026.33"
    assert h["citation"] == "WBE.2026.33"          # R1: from the pipeline, never constructed
    assert "entscheid" in h["url"]
    assert h["court"] == "ag_verwaltungsgericht"


def test_rejects_non_case_number(monkeypatch):
    monkeypatch.setattr(mcp_server, "_looks_like_docket_query", lambda q: False)

    def _boom(**k):
        raise AssertionError("search_fts5 must not run for a non-docket input")

    monkeypatch.setattr(mcp_server, "search_fts5", _boom)
    res = mcp_server._lookup_case_number("Verjaehrung im Mietrecht")
    assert res["is_case_number"] is False
    assert res["total"] == 0
    assert "hint" in res


def test_empty_input():
    res = mcp_server._lookup_case_number("")
    assert res["is_case_number"] is False
    assert res["total"] == 0
    assert res["results"] == []


# ── exact-first (2026-10-01) ────────────────────────────────────────────────
# The search fast path misses BGE citations (it strips the spaces the stored
# docket keeps) and slash dockets (the sanitiser removes the slash first), so a
# plain lookup ran the full search: 5-10 s, citing decisions mixed in, and
# "0 results" when the search deadline cut it short under load. The lookup now
# asks the indexed exact match first.

_HIT = {"decision_id": "bge_BGE_140_III_86", "docket_number": "BGE 140 III 86", "court": "bge",
        "canton": "CH", "decision_date": "2014-01-23", "title": None,
        "citation": "BGE 140 III 86", "url": "https://mcp.opencaselaw.ch/entscheid/bge_BGE_140_III_86"}


def _exact_result(hits):
    return {"query": "BGE 140 III 86", "is_case_number": True, "exact": True,
            "total": len(hits), "results": list(hits)}


def test_plain_lookup_answers_from_the_exact_match_without_searching(monkeypatch):
    monkeypatch.setattr(mcp_server, "_lookup_exact", lambda qn, limit=25: _exact_result([_HIT]))

    def _boom(**k):
        raise AssertionError("an exact hit must not fall through to search_fts5")

    monkeypatch.setattr(mcp_server, "search_fts5", _boom)
    res = mcp_server._lookup_case_number("BGE 140 III 86", limit=1)
    assert res["is_case_number"] is True
    assert res["total"] == 1
    assert res["results"][0]["decision_id"] == "bge_BGE_140_III_86"
    assert res["match"] == "exact"
    assert "exact" not in res            # that key reports the caller's exact=true


def test_plain_lookup_falls_back_to_search_when_nothing_is_exact(monkeypatch):
    monkeypatch.setattr(mcp_server, "_looks_like_docket_query", lambda q: True)  # a partial number
    monkeypatch.setattr(mcp_server, "_lookup_exact", lambda qn, limit=25: _exact_result([]))
    row = {"decision_id": "bger_4A_82_2024", "docket_number": "4A_82/2024", "court": "bger",
           "canton": "CH", "decision_date": "2024-08-19", "title": None}
    monkeypatch.setattr(mcp_server, "search_fts5", lambda **k: ([row], 1))
    monkeypatch.setattr(mcp_server, "_build_citation_strings", lambda r: {})
    res = mcp_server._lookup_case_number("4A_82/20")
    assert res["total"] == 1
    assert res["match"] == "search"
    assert "hint" not in res and "partial" not in res


def test_plain_lookup_survives_a_failing_exact_match(monkeypatch):
    def _broken(qn, limit=25):
        raise FileNotFoundError("no database")

    monkeypatch.setattr(mcp_server, "_lookup_exact", _broken)
    monkeypatch.setattr(mcp_server, "search_fts5", lambda **k: ([], 0))
    res = mcp_server._lookup_case_number("4A_82/2024")
    assert res["is_case_number"] is True and res["total"] == 0
    assert "partial" not in res


def test_a_search_cut_short_is_reported_as_incomplete_not_as_not_found(monkeypatch):
    monkeypatch.setattr(mcp_server, "_looks_like_docket_query", lambda q: True)  # a partial number
    monkeypatch.setattr(mcp_server, "_lookup_exact", lambda qn, limit=25: _exact_result([]))

    def _cut(**k):
        k["meta"]["deadline_partial"] = True
        return [], 0

    monkeypatch.setattr(mcp_server, "search_fts5", _cut)
    res = mcp_server._lookup_case_number("4A_82/20")
    assert res["total"] == 0
    assert res["partial"] is True
    assert "exact=true" in res["hint"]


def test_explicit_exact_mode_is_unchanged(monkeypatch):
    monkeypatch.setattr(mcp_server, "_lookup_exact", lambda qn, limit=25: _exact_result([_HIT]))
    res = mcp_server._lookup_case_number("BGE 140 III 86", exact=True)
    assert res["exact"] is True
    assert "match" not in res


import pytest  # noqa: E402


@pytest.fixture(scope="module")
def rest_app():
    import uvicorn
    captured = {}
    real_run = uvicorn.run
    uvicorn.run = lambda app, **kwargs: captured.setdefault("app", app)
    try:
        mcp_server.main_remote("127.0.0.1", 0)
    finally:
        uvicorn.run = real_run
    return captured["app"]


def test_rest_lookup_cut_short_and_empty_is_a_503_not_an_empty_200(rest_app, monkeypatch):
    from starlette.testclient import TestClient
    monkeypatch.setattr(mcp_server, "_looks_like_docket_query", lambda q: True)  # a partial number
    monkeypatch.setattr(mcp_server, "_lookup_exact", lambda qn, limit=25: _exact_result([]))

    def _cut(**k):
        k["meta"]["deadline_partial"] = True
        return [], 0

    monkeypatch.setattr(mcp_server, "search_fts5", _cut)
    r = TestClient(rest_app).get("/api/lookup", params={"q": "4A_82/20"})
    assert r.status_code == 503
    assert r.headers["retry-after"] == str(mcp_server.BUSY_RETRY_AFTER_S)
    assert r.json()["error"] == "search_incomplete"


def test_rest_lookup_exact_hit_is_a_plain_200(rest_app, monkeypatch):
    from starlette.testclient import TestClient
    monkeypatch.setattr(mcp_server, "_lookup_exact", lambda qn, limit=25: _exact_result([_HIT]))
    r = TestClient(rest_app).get("/api/lookup", params={"q": "BGE 140 III 86", "limit": 1})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1 and body["match"] == "exact"
