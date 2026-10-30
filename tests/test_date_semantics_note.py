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
