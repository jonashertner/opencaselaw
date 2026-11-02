"""Issue #27: get_law must serve cantonal laws LexFind-first (always current +
complete), with the local mirror as a resilience fallback only.

Regression guard: ZH 550.1 (PolG) / 554.5 (HuG) are absent from the incomplete
cantonal_laws.db mirror but present in LexFind; get_law previously returned a
false "No law found". These tests are offline (LexFind + mirror are mocked).
"""
import json

import pytest

import mcp_server


# ── get_law routing → LexFind-first legislation path ──────────────────────────

def test_get_law_cantonal_returns_lexfind_law(monkeypatch):
    """get_law maps a LexFind-served cantonal law and extracts the article."""
    leg = {
        "lexfind_id": 22871,
        "systematic_number": "554.5",
        "title": "Hundegesetz (HuG)",
        "current_version": {"category": "Tiere", "active_since": "2025-06-01"},
        "source": "lexfind",
        "articles": [
            {"article_num": "1", "heading": "Zweck", "text": "Dieses Gesetz ..."},
            {"article_num": "2", "heading": "Geltungsbereich", "text": "..."},
        ],
    }
    monkeypatch.setattr(mcp_server, "_get_legislation", lambda **kw: leg)

    res = mcp_server._get_law_cantonal(
        sr_number="554.5", abbreviation=None, article="1",
        language="de", canton="ZH",
    )
    assert "error" not in res
    assert res["sr_number"] == "554.5"
    assert res["canton"] == "ZH"
    assert res["lexfind_id"] == 22871
    assert res["version_active_since"] == "2025-06-01"
    assert [a["article_num"] for a in res["articles"]] == ["1"]
    assert res["articles"][0]["text"].startswith("Dieses Gesetz")


def test_get_law_cantonal_passes_canton_and_sr_to_legislation(monkeypatch):
    """The delegated call carries canton + systematic_number through."""
    seen = {}
    def fake(**kw):
        seen.update(kw)
        return {"systematic_number": "550.1", "title": "PolG",
                "articles": [{"article_num": "33", "heading": "Wegweisung", "text": "x"}]}
    monkeypatch.setattr(mcp_server, "_get_legislation", fake)

    res = mcp_server._get_law_cantonal(
        sr_number="550.1", abbreviation=None, article="33",
        language="de", canton="zh",
    )
    assert seen["systematic_number"] == "550.1"
    assert seen["canton"] == "ZH"
    assert res["articles"][0]["heading"] == "Wegweisung"


def test_get_law_cantonal_no_law_when_legislation_errors(monkeypatch):
    """A genuine LexFind miss surfaces as 'No law found' (not a crash)."""
    monkeypatch.setattr(mcp_server, "_get_legislation",
                        lambda **kw: {"error": "No legislation found"})
    res = mcp_server._get_law_cantonal(
        sr_number="999.99", abbreviation=None, article="1",
        language="de", canton="ZH",
    )
    assert "error" in res
    assert "No law found" in res["error"]


def test_get_law_cantonal_handles_legislation_exception(monkeypatch):
    """A malformed LexFind response (exception inside _get_legislation) must
    degrade to a graceful error, not an unhandled 500."""
    def boom(**kw):
        raise KeyError("families")
    monkeypatch.setattr(mcp_server, "_get_legislation", boom)
    res = mcp_server._get_law_cantonal(
        sr_number="554.5", abbreviation=None, article="1",
        language="de", canton="ZH",
    )
    assert "error" in res


def test_get_law_cantonal_digit_abbreviation_used_as_sr(monkeypatch):
    """A digit-form abbreviation is used directly as the SR/LS number — no
    mirror lookup needed (REST /api/laws/{n}?canton=ZH path)."""
    seen = {}
    def fake(**kw):
        seen.update(kw)
        return {"systematic_number": "131.1", "title": "GG", "articles": []}
    monkeypatch.setattr(mcp_server, "_get_legislation", fake)
    res = mcp_server._get_law_cantonal(
        sr_number=None, abbreviation="131.1", article=None,
        language="de", canton="ZH",
    )
    assert seen["systematic_number"] == "131.1"
    assert "error" not in res


# ── _get_legislation cantonal: LexFind-first + mirror resilience fallback ──────

def _mock_lexfind_success(monkeypatch, *, sr, canton, lexfind_id, title):
    """Wire _lexfind_request + _fetch_lexfind_law_text to emulate a live hit."""
    def fake_request(method, endpoint, language, json_body=None, timeout=None):
        if endpoint == "systematic-search":
            return {"id": "S1", "session_id": "SID"}
        if endpoint.startswith("systematic-search/"):
            return {"texts_of_law_with_latest_version": [
                {"id": lexfind_id, "entity": {"abbreviation": canton},
                 "systematic_number": sr}], "number_of_pages": 1}
        if endpoint.startswith("texts-of-law/"):
            return {"id": lexfind_id, "systematic_number": sr,
                    "entity": {"abbreviation": canton, "name": canton},
                    "is_active": True, "dta_urls": [],
                    "families": [[[{"id": 1, "title": title,
                                    "info_badge": "current",
                                    "version_active_since": "2025-06-01",
                                    "is_active": True,
                                    "category": {"name": "X"}}]]]}
        return None
    monkeypatch.setattr(mcp_server, "_lexfind_request", fake_request)
    monkeypatch.setattr(mcp_server, "_fetch_lexfind_law_text",
                        lambda lid, language="de": {
                            "articles": [{"article_num": "1", "heading": "h", "text": "live"}],
                            "full_text": "live", "text_source": "lexfind_pdf"})
    monkeypatch.setattr(mcp_server, "_lexfind_cache_get", lambda k: None)
    monkeypatch.setattr(mcp_server, "_lexfind_cache_set", lambda k, v: None)
    monkeypatch.setattr(mcp_server, "LEXFIND_ENABLED", True)


def test_get_legislation_cantonal_prefers_lexfind_over_mirror(monkeypatch):
    """LexFind-first: a present-but-stale mirror must NOT short-circuit the
    live LexFind result."""
    _mock_lexfind_success(monkeypatch, sr="554.5", canton="ZH",
                          lexfind_id=22871, title="Hundegesetz")
    # Mirror would return a STALE copy — it must be ignored when LexFind works.
    monkeypatch.setattr(mcp_server, "_get_cantonal_local",
                        lambda **kw: {"title": "STALE", "source": "cantonal_local",
                                      "articles": [{"article_num": "1", "heading": "old", "text": "STALE"}]})
    res = mcp_server._get_legislation(systematic_number="554.5", canton="ZH", language="de")
    assert res["source"] == "lexfind"
    assert res["title"] == "Hundegesetz"
    assert res["articles"][0]["text"] == "live"


def test_get_legislation_cantonal_falls_back_to_mirror_when_lexfind_down(monkeypatch):
    """Resilience: LexFind unreachable → serve the local mirror, not an error."""
    monkeypatch.setattr(mcp_server, "LEXFIND_ENABLED", True)
    monkeypatch.setattr(mcp_server, "_lexfind_request",
                        lambda *a, **k: None)  # every LexFind call fails
    monkeypatch.setattr(mcp_server, "_lexfind_cache_get", lambda k: None)
    monkeypatch.setattr(mcp_server, "_get_cantonal_local",
                        lambda **kw: {"title": "PolG", "source": "cantonal_local",
                                      "articles": [{"article_num": "1", "heading": "h", "text": "mirror"}]})
    res = mcp_server._get_legislation(systematic_number="550.1", canton="ZH", language="de")
    assert res["source"] == "cantonal_local"
    assert res["title"] == "PolG"


def test_get_legislation_cantonal_error_when_lexfind_down_and_no_mirror(monkeypatch):
    """LexFind down AND mirror miss → a real error, not a crash."""
    monkeypatch.setattr(mcp_server, "LEXFIND_ENABLED", True)
    monkeypatch.setattr(mcp_server, "_lexfind_request", lambda *a, **k: None)
    monkeypatch.setattr(mcp_server, "_lexfind_cache_get", lambda k: None)
    monkeypatch.setattr(mcp_server, "_get_cantonal_local", lambda **kw: None)
    res = mcp_server._get_legislation(systematic_number="550.1", canton="ZH", language="de")
    assert "error" in res


# ── search: cantonal LexFind-first (issue #27, Option A) ──────────────────────

def _mock_lexfind_search(monkeypatch, *, sr, canton, title, snippet):
    def fake_request(method, endpoint, language, json_body=None, timeout=None):
        if endpoint == "fulltext-search":
            return {"id": "S1", "session_id": "SID"}
        if endpoint.startswith("fulltext-search/"):
            return {"texts_of_law_with_matches": [
                {"entity": {"abbreviation": canton, "name": canton},
                 "systematic_number": sr, "id": 23646, "is_active": True,
                 "dta_urls": [],
                 "matches": [{"title": title, "snippet": snippet,
                              "category": {"name": "x"}, "is_active": True}]}],
                "results": [{"number_of_results": 1}]}
        return None
    monkeypatch.setattr(mcp_server, "_lexfind_request", fake_request)
    monkeypatch.setattr(mcp_server, "_lexfind_cache_get", lambda k: None)
    monkeypatch.setattr(mcp_server, "_lexfind_cache_set", lambda k, v: None)
    monkeypatch.setattr(mcp_server, "LEXFIND_ENABLED", True)


def test_search_legislation_cantonal_lexfind_first(monkeypatch):
    """search_legislation finds a law present in LexFind but absent from the
    incomplete local mirror (ZH PolG / 'Wegweisung')."""
    _mock_lexfind_search(monkeypatch, sr="550.1", canton="ZH",
                         title="Polizeigesetz (PolG)", snippet="...Wegweisung...")
    res = mcp_server._search_legislation(query="Wegweisung", canton="ZH", language="de")
    assert res["laws"], "expected a LexFind hit"
    assert res["laws"][0]["systematic_number"] == "550.1"


def test_search_legislation_cantonal_falls_back_to_mirror(monkeypatch):
    """LexFind unreachable → serve local mirror search, not an error/empty."""
    monkeypatch.setattr(mcp_server, "LEXFIND_ENABLED", True)
    monkeypatch.setattr(mcp_server, "_lexfind_request", lambda *a, **k: None)
    monkeypatch.setattr(mcp_server, "_lexfind_cache_get", lambda k: None)
    monkeypatch.setattr(mcp_server, "_search_cantonal_local",
                        lambda **kw: {"laws": [{"systematic_number": "550.1",
                                                "title": "PolG", "entity": "ZH"}]})
    res = mcp_server._search_legislation(query="Wegweisung", canton="ZH", language="de")
    assert res["laws"][0]["systematic_number"] == "550.1"


def test_search_laws_cantonal_routes_to_lexfind(monkeypatch):
    """search_laws cantonal delegates to the LexFind path and reformats to the
    article-level shape, using the RAW query (not the FTS5-expanded one)."""
    seen = {}
    def fake_leg(**kw):
        seen.update(kw)
        return {"laws": [{"systematic_number": "550.1", "title": "PolG",
                          "entity": "ZH", "snippet": "...Wegweisung...",
                          "lexfind_id": 23646}]}
    monkeypatch.setattr(mcp_server, "_search_legislation", fake_leg)
    out = mcp_server._search_laws_cantonal(
        "(wegweisung OR ...) AND x", "ZH", "de", 10, raw_query="Wegweisung")
    assert seen["query"] == "Wegweisung"      # raw query, not FTS5-expanded
    assert seen["canton"] == "ZH"
    assert out[0]["sr_number"] == "550.1"
    assert out[0]["level"] == "cantonal"
    assert out[0]["title"] == "PolG"


# ── When LexFind does not answer ──────────────────────────────────────────────

def _lexfind_down(monkeypatch):
    monkeypatch.setattr(mcp_server, "LEXFIND_ENABLED", True)
    monkeypatch.setattr(mcp_server, "_lexfind_request", lambda *a, **k: None)
    monkeypatch.setattr(mcp_server, "_lexfind_cache_get", lambda k: None)
    monkeypatch.setattr(mcp_server, "_lexfind_cache_set", lambda k, v: None)


@pytest.fixture
def mirror(tmp_path, monkeypatch):
    """A real cantonal_laws.db: VD 100 holds a repealed act and its successor."""
    from search_stack.build_cantonal_laws_db import build

    def law(lid, canton, sr, title, active, text):
        return {"lexfind_id": lid, "canton": canton, "sr_number": sr, "title": title,
                "language": "de", "is_active": active, "text_source": "lexfind_pdf",
                "version_active_since": "26.02.2025", "full_text": text,
                "fetched_at": "2026-04-11T00:00:00+00:00",
                "articles": [{"article_num": "1", "heading": None, "text": text}]}

    lexfind = tmp_path / "lexfind"
    lexfind.mkdir()
    rows = [law(1, "VD", "100", "Ancienne", False, "Hundesteuer aufgehoben"),
            law(9, "VD", "100", "Nouvelle", True, "Hundesteuer geltend"),
            law(2, "VD", "200", "Abrogée", False, "Hundesteuer weg")]
    (lexfind / "VD.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    db = tmp_path / "cantonal_laws.db"
    build(tmp_path / "direct", lexfind, db)
    monkeypatch.setattr(mcp_server, "CANTONAL_LAWS_DB_PATH", db)
    return db


def _get(sr, canton="VD"):
    return mcp_server._get_law_cantonal(
        sr_number=sr, abbreviation=None, article="1", language="de", canton=canton)


def test_a_failed_lookup_is_not_reported_as_a_missing_law(monkeypatch):
    """The Polizeigesetz exists. 'No law found' told the reader it did not."""
    _lexfind_down(monkeypatch)
    monkeypatch.setattr(mcp_server, "_get_cantonal_local", lambda **kw: None)
    res = _get("550.1", "ZH")
    assert res["lookup_failed"] is True
    assert "No law found" not in res["error"]
    assert "says nothing about whether the law exists" in res["error"]


@pytest.mark.parametrize("fails", [
    "systematic-search/", "fulltext-search", "fulltext-search/", "texts-of-law/"])
def test_every_unanswered_request_is_a_failed_lookup(monkeypatch, fails):
    def request(method, endpoint, language, json_body=None, timeout=None):
        if endpoint.startswith(fails) and (fails.endswith("/") or endpoint == fails):
            return None
        if endpoint in ("systematic-search", "fulltext-search"):
            return {"id": "S", "session_id": "X"}
        if endpoint.startswith("systematic-search/"):
            hit = fails == "texts-of-law/"
            return {"texts_of_law_with_latest_version": [
                {"id": 7, "entity": {"abbreviation": "ZH"},
                 "systematic_number": "550.1"}] if hit else [], "number_of_pages": 1}
        return {"texts_of_law_with_matches": []}

    monkeypatch.setattr(mcp_server, "LEXFIND_ENABLED", True)
    monkeypatch.setattr(mcp_server, "_lexfind_request", request)
    monkeypatch.setattr(mcp_server, "_lexfind_cache_get", lambda k: None)
    monkeypatch.setattr(mcp_server, "_lexfind_cache_set", lambda k, v: None)
    monkeypatch.setattr(mcp_server, "_get_cantonal_local", lambda **kw: None)
    assert _get("550.1", "ZH").get("lookup_failed") is True


def test_a_lexfind_answer_of_no_such_law_stays_a_miss(monkeypatch):
    def request(method, endpoint, language, json_body=None, timeout=None):
        if endpoint in ("systematic-search", "fulltext-search"):
            return {"id": "S", "session_id": "X"}
        return {"texts_of_law_with_latest_version": [], "number_of_pages": 1,
                "texts_of_law_with_matches": []}

    monkeypatch.setattr(mcp_server, "LEXFIND_ENABLED", True)
    monkeypatch.setattr(mcp_server, "_lexfind_request", request)
    monkeypatch.setattr(mcp_server, "_lexfind_cache_get", lambda k: None)
    monkeypatch.setattr(mcp_server, "_get_cantonal_local", lambda **kw: None)
    res = _get("999.9", "ZH")
    assert "No law found" in res["error"]
    assert "lookup_failed" not in res


def test_a_wrong_id_from_the_caller_is_not_a_failed_lookup(monkeypatch):
    _lexfind_down(monkeypatch)
    monkeypatch.setattr(mcp_server, "_get_cantonal_local", lambda **kw: None)
    assert "lookup_failed" not in mcp_server._get_legislation(lexfind_id=999999)


def test_the_mirror_answers_with_the_act_in_force(monkeypatch, mirror):
    _lexfind_down(monkeypatch)
    res = _get("100")
    assert res["title"] == "Nouvelle"
    assert res["is_active"] is True
    assert res["articles"][0]["text"] == "Hundesteuer geltend"


def test_a_mirror_answer_says_what_it_is_and_from_when(monkeypatch, mirror):
    _lexfind_down(monkeypatch)
    res = _get("100")
    assert res["source"] == "cantonal_local"
    assert res["source_label"] == "Lokale Kopie vom 2026-04-11"
    assert "not live from LexFind" in res["mirror_notice"]
    assert "2026-04-11" in res["mirror_notice"]
    assert res["mirror_notice"] in mcp_server._format_get_law_response(res)


def test_a_repealed_act_is_named_as_repealed(monkeypatch, mirror):
    _lexfind_down(monkeypatch)
    res = _get("200")
    assert res["is_active"] is False
    assert "no longer in force" in res["mirror_notice"]


def test_a_live_answer_carries_no_mirror_notice(monkeypatch):
    _mock_lexfind_success(monkeypatch, sr="554.5", canton="ZH",
                          lexfind_id=22871, title="Hundegesetz")
    res = _get("554.5", "ZH")
    assert "mirror_notice" not in res
    assert res["source_label"] == "LexFind"


def test_search_leaves_repealed_acts_out_of_the_mirror(monkeypatch, mirror):
    _lexfind_down(monkeypatch)
    hits = mcp_server._search_laws_cantonal(
        "Hundesteuer", "VD", "de", 10, raw_query="Hundesteuer")
    assert [h["title"] for h in hits] == ["Nouvelle"]


def test_search_legislation_can_still_ask_for_repealed_acts(monkeypatch, mirror):
    _lexfind_down(monkeypatch)
    res = mcp_server._search_legislation(
        query="Hundesteuer", canton="VD", language="de", active_only=False)
    assert len(res["laws"]) == 3
    assert "mirror_notice" in res


def test_search_laws_marks_mirror_hits(monkeypatch, mirror):
    _lexfind_down(monkeypatch)
    res = mcp_server.search_laws(query="Hundesteuer", canton="VD", language="de")
    assert res["mirror_notice"]
    payload = mcp_server._law_hits_structured(res, "de")
    assert payload["mirror_notice"] == res["mirror_notice"]
    assert payload["hits"][0]["source_label"] == "Lokale Kopie"
    assert res["mirror_notice"] in mcp_server._format_search_laws_response(res)


def test_an_empty_search_says_when_it_could_not_run(monkeypatch, mirror):
    _lexfind_down(monkeypatch)
    res = mcp_server.search_laws(query="Quantenschaum", canton="VD", language="de")
    assert res["count"] == 0
    assert res["lookup_failed"] is True
    assert "could not run" in mcp_server._format_search_laws_response(res)


def test_an_empty_live_search_is_just_empty(monkeypatch, mirror):
    monkeypatch.setattr(mcp_server, "_search_legislation",
                        lambda **kw: {"laws": [], "total": 0})
    res = mcp_server.search_laws(query="Quantenschaum", canton="VD", language="de")
    assert res["count"] == 0
    assert "lookup_failed" not in res
