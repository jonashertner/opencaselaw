"""2026-10-30: a model called Zurich "schlecht abgedeckt" because two courts'
newest decision dates were weeks old, though their portals held nothing newer.
Answers about recency now say what a decision date is and is not."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import jsonschema
import pytest

import mcp_server as m
from tests.test_passage_fallback_and_lookup import output_schema, wire


def _ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()


@pytest.mark.parametrize("sort,date_from,published_from", [
    ("date_desc", None, None),
    ("published_desc", None, None),
    ("relevance", _ago(30), None),
    (None, None, _ago(30)),
])
def test_a_question_about_recency_gets_the_note(sort, date_from, published_from):
    assert m._date_semantics_note(sort, date_from, published_from) == m._DATE_SEMANTICS_NOTE


@pytest.mark.parametrize("sort,date_from,published_from", [
    (None, None, None),
    ("relevance", None, None),
    ("date_asc", None, None),
    (None, "2010-01-01", None),
])
def test_other_searches_stay_uncluttered(sort, date_from, published_from):
    assert m._date_semantics_note(sort, date_from, published_from) == ""


def test_the_note_keeps_the_two_dates_apart():
    note = m._DATE_SEMANTICS_NOTE
    assert "decision_date is the day the court decided" in note
    assert "publication_date is the day it published" in note
    assert "not a measure of how current" in note
    assert "null where the court states none" in note


def test_the_note_reaches_mcp_and_rest(monkeypatch):
    import uvicorn
    from starlette.testclient import TestClient

    def search(**kwargs):
        return [{"decision_id": "test_case", "docket_number": "test-docket", "court": "zh_handelsgericht",
                 "decision_date": "2026-09-16", "language": "de", "title": "Fixture", "regeste": None,
                 "full_text": "stored text"}], 1
    monkeypatch.setattr(m, "search_fts5", search)
    monkeypatch.setattr(m, "_pinpoint_enrich_results", lambda *a, **k: None)
    monkeypatch.setattr(m, "_representation_info", lambda *a: None)
    args = {"court": "zh_handelsgericht", "sort": "date_desc", "limit": 1}
    res = asyncio.run(wire("search_decisions", args))
    assert not res.isError and "not a measure of how current" in res.structuredContent["note"]
    assert "not a measure of how current" in res.content[0].text
    jsonschema.validate(res.structuredContent, output_schema("search_decisions"))

    plain = asyncio.run(wire("search_decisions", {"query": "x", "limit": 1}))
    assert "not a measure of how current" not in (plain.structuredContent.get("note") or "")

    captured = {}
    monkeypatch.setattr(m, "_warm_page_cache", lambda: None)
    monkeypatch.setattr(m, "_log_startup", lambda: None)
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: captured.update(app=app))
    m.main_remote("127.0.0.1", 0)
    rest = TestClient(captured["app"]).get("/api/decisions", params=args).json()
    assert "not a measure of how current" in rest["note"]


def test_list_courts_says_its_dates_are_decision_dates(monkeypatch):
    monkeypatch.setattr(m, "list_courts", lambda: [
        {"court": "zh_handelsgericht", "canton": "ZH", "decision_count": 4717, "languages": "de",
         "earliest": "2011-01-01", "latest": "2026-09-16"}])
    res = asyncio.run(wire("list_courts", {}))
    text = res.content[0].text
    assert "Earliest and Latest are decision dates" in text
    assert "not a measure of how current" in text


def test_r12_rejects_the_claim_in_the_system_prompt():
    src = open(m.__file__, encoding="utf-8").read()
    assert "NEVER call a court poorly covered" in src


# 2026-11-03, Jonas: no model connected here should tell a user the corpus is
# incomplete or not up to date because the newest decisions are older than today.

def test_the_note_tells_the_model_how_to_answer_a_request_for_the_latest():
    note = m._DATE_SEMANTICS_NOTE
    assert "updated daily" in note
    assert "courts publish weeks to months after deciding" in note
    assert "Do not call this corpus, or a court in it, incomplete, outdated" in note


def test_r12_rules_out_calling_the_corpus_outdated():
    src = open(m.__file__, encoding="utf-8").read()
    assert "NEVER describe opencaselaw as incomplete, outdated" in src
    assert "When a user asks for the latest or most recent decisions" in src


def test_recent_year_counts_get_the_lag_note():
    this_year = datetime.now(timezone.utc).year
    assert m._recent_years_note({this_year: 3, this_year - 5: 40}) == m._RECENT_YEARS_NOTE
    assert m._recent_years_note({str(this_year - 1): 7, None: 1}) == m._RECENT_YEARS_NOTE
    assert m._recent_years_note({"2001": 9, "2002": 8}) == ""
    assert "not decisions missing from this corpus" in m._RECENT_YEARS_NOTE


def test_get_statistics_carries_the_note_to_mcp(monkeypatch):
    import sqlite3
    this_year = datetime.now(timezone.utc).year

    def db():
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE decisions (court TEXT, canton TEXT, language TEXT, decision_date TEXT)")
        conn.executemany("INSERT INTO decisions VALUES (?,?,?,?)", [
            ("lagtest_court", "ZH", "de", f"{this_year}-03-01"),
            ("lagtest_court", "ZH", "de", "2015-03-01")])
        return conn
    monkeypatch.setattr(m, "get_db", db)
    stats = m.get_statistics(court="lagtest_court")
    assert stats["note"] == m._RECENT_YEARS_NOTE
    res = asyncio.run(wire("get_statistics", {"court": "lagtest_court"}))
    assert m._RECENT_YEARS_NOTE in res.content[0].text


def test_trend_text_ends_with_the_note():
    this_year = datetime.now(timezone.utc).year
    result = {"years": [{"year": this_year - 1, "count": 30}, {"year": this_year, "count": 4}],
              "total": 34, "law_code": None, "article": None, "query": "x",
              "note": m._recent_years_note({this_year: 4})}
    assert m._RECENT_YEARS_NOTE in m._format_trend_response(result)


def test_tool_descriptions_frame_recency_as_publication_lag():
    tools = {t.name: t.description for t in m._list_tools()}
    assert "courts publish after they" in tools["search_decisions"]
    assert "courts publish weeks to months" in tools["search"]
    assert "updated daily" in tools["list_courts"]
    assert "publication lag, not missing data" in tools["get_statistics"]
    assert "recent decisions rank low" in tools["find_leading_cases"]
    assert "publication lag" in tools["analyze_legal_trend"]
