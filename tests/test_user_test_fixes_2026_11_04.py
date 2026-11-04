"""Fixes from the user-perspective test of 2026-11-04.

1. get_decision structuredContent carried the whole ECtHR judgment three times
   (abstract_de/fr/it), 2.67M characters for KlimaSeniorinnen; get_case_brief
   served an 866K "dispositiv" for the same judgment.
2. search_decisions(query="OR") answered "1,074,490 decisions": the sanitizer
   empties a bare operator and an empty query lists the whole corpus.
   An inverted date range silently returned zero.
3. draft_mock_decision scraped live Fedlex for statute text and always got
   "fetch_failed"; the local mirror (get_law) has the text.
4. get_practice returned a 244K-character circular in one answer.
5. get_materialien(OR, 271a) listed all 30 OR consultations with their
   documents (272K characters) although `article` was given.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import mcp_server as m  # noqa: E402

SRC = (REPO / "mcp_server.py").read_text(encoding="utf-8")


# ------------------------------------------------ 1. oversized abstracts

def test_abstracts_beyond_cap_are_cut_to_a_prefix_and_flagged():
    judgment = "Urteilskopf\n<br/>Regeste " + "x" * (m._ABSTRACT_MAX_CHARS * 3)
    row = {"abstract_de": judgment, "abstract_fr": judgment,
           "abstract_it": judgment, "regeste": "kurz"}
    m._bound_abstracts(row)
    for col in ("abstract_de", "abstract_fr", "abstract_it"):
        assert len(row[col]) == m._ABSTRACT_MAX_CHARS
        assert judgment.startswith(row[col])          # verbatim prefix (R2)
    assert row["abstract_truncated"] == ["abstract_de", "abstract_fr", "abstract_it"]
    assert row["regeste"] == "kurz"


def test_normal_abstracts_are_untouched():
    row = {"abstract_de": "Art. 75 ATSG; Regress.", "abstract_fr": None,
           "abstract_it": "x" * m._ABSTRACT_MAX_CHARS}
    before = dict(row)
    m._bound_abstracts(row)
    assert row == before and "abstract_truncated" not in row


def test_get_decision_by_id_bounds_abstracts_for_every_caller():
    # get_decision, get_case_brief, fetch and REST all read rows through it.
    src = ast_func("get_decision_by_id")
    assert "_bound_abstracts(result)" in src


def test_long_sections_are_bounded():
    text = "Par ces motifs, la Cour " + "y" * (m._SECTION_MAX_CHARS * 2)
    cut, was_cut = m._bounded_section(text)
    assert was_cut and len(cut) == m._SECTION_MAX_CHARS and text.startswith(cut)
    assert m._bounded_section("Die Beschwerde wird abgewiesen.") == (
        "Die Beschwerde wird abgewiesen.", False)
    assert m._bounded_section(None) == (None, False)


def test_case_brief_and_structure_use_the_section_bound():
    assert "_bounded_section(dispositiv)" in ast_func("_handle_get_case_brief")
    assert '_bounded_section(row.get("dispositiv"))' in ast_func(
        "_handle_get_decision_structure")


# ------------------------------------------- 2. unsearchable queries, dates

@pytest.mark.parametrize("q", ["OR", "or", " OR ", "Or"])
def test_bare_or_is_refused_with_a_statute_hint(q):
    err = m._unsearchable_query_error(q)
    assert err and "Art. 41 OR" in err and "search_laws" in err


@pytest.mark.parametrize("q", ['""', "?", "AND NOT", "( )", "OR AND"])
def test_query_without_terms_is_refused(q):
    err = m._unsearchable_query_error(q)
    assert err and ("no searchable terms" in err or "nothing to search" in err)


@pytest.mark.parametrize("q", [None, "", "   ", "Haftung OR", "Art. 41 OR",
                               "Mietzinserhöhung", "*", '"OR"'])
def test_real_or_empty_queries_pass(q):
    # Empty stays the documented "list recent decisions" browse; a quoted
    # "OR" is an explicit phrase search and is the caller's choice.
    assert m._unsearchable_query_error(q) is None


def test_long_queries_are_left_to_the_condenser():
    assert m._unsearchable_query_error("?" * (m.QUERY_CONDENSE_THRESHOLD + 1)) is None


def test_inverted_date_range_is_refused():
    err = m._date_arg_error("2024-12-31", "2015-01-01")
    assert err and "Swap" in err and "2024-12-31" in err
    assert m._date_arg_error("31.12.2024", "01.01.2015")
    assert m._date_arg_error("2015-01-01", "2024-12-31") is None
    assert m._date_arg_error("2020-05-05", "2020-05-05") is None
    assert m._date_arg_error("2020-05-05", None) is None


def test_mcp_and_rest_search_check_unsearchable_queries():
    assert '_unsearchable_query_error(arguments.get("query"))' in SRC
    assert "_unsearchable_query_error(query)" in ast_func("api_search_decisions")


# --------------------------------------- 3. mock decision statute text

def test_local_statute_excerpt_reads_the_mirror(monkeypatch):
    calls = []

    def fake_get_law(**kw):
        calls.append(kw)
        return {"canton": "CH", "source_url": "https://www.fedlex.admin.ch/eli/cc/27/317_321_377/de#art_337",
                "articles": [{"article_num": "337", "text": "1 Aus wichtigen Gründen …"}]}

    monkeypatch.setattr(m, "get_law", fake_get_law)
    got = m._local_statute_excerpt("OR", "337", "rm")
    assert got == {"fedlex_url": "https://www.fedlex.admin.ch/eli/cc/27/317_321_377/de#art_337",
                   "text_excerpt": "1 Aus wichtigen Gründen …"}
    assert calls[-1] == {"abbreviation": "OR", "article": "337", "language": "de"}
    m._local_statute_excerpt("220", "337", "fr")
    assert calls[-1] == {"sr_number": "220", "article": "337", "language": "fr"}


def test_local_statute_excerpt_misses_cleanly(monkeypatch):
    monkeypatch.setattr(m, "get_law", lambda **kw: {"canton": "CH", "articles": []})
    assert m._local_statute_excerpt("OR", "9999", "de") is None
    monkeypatch.setattr(m, "get_law", lambda **kw: {"error": "No law found"})
    assert m._local_statute_excerpt("XYZ", "1", "de") is None
    monkeypatch.setattr(m, "get_law", lambda **kw: 1 / 0)
    assert m._local_statute_excerpt("OR", "1", "de") is None


def test_statute_resolution_prefers_the_mirror_over_fedlex(monkeypatch):
    monkeypatch.setattr(m, "_local_statute_excerpt", lambda *a: {
        "fedlex_url": "u#art_337", "text_excerpt": "1 Aus wichtigen Gründen"})

    def no_network(**kw):
        raise AssertionError("Fedlex fetched although the mirror answered")

    monkeypatch.setattr(m, "_fetch_fedlex_article_text", no_network)
    out = m._resolve_fedlex_statute_article(
        law_code="OR", article="337", paragraph=None, preferred_language="de",
        fedlex_urls=[], cache={})
    assert out["status"] == "local_mirror" and out["text_excerpt"].startswith("1 Aus")


def test_explicit_fedlex_urls_still_win(monkeypatch):
    monkeypatch.setattr(m, "_local_statute_excerpt",
                        lambda *a: pytest.fail("mirror consulted despite explicit URL"))
    monkeypatch.setattr(m, "_fetch_fedlex_article_text",
                        lambda **kw: {"fedlex_url": kw["url"], "text_excerpt": "Art. 1 x"})
    out = m._resolve_fedlex_statute_article(
        law_code="OR", article="1", paragraph=None, preferred_language="de",
        fedlex_urls=["https://www.fedlex.admin.ch/eli/cc/27/317_321_377/de"], cache={})
    assert out["status"] == "fetched"


# --------------------------------------------------- 4. get_practice paging

@pytest.fixture
def practice_db(monkeypatch):
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE practice (doc_id TEXT, source TEXT, issuing_authority TEXT,
        doc_type TEXT, doc_number TEXT, title TEXT, date TEXT, language TEXT, url TEXT,
        pdf_url TEXT, body_text TEXT, topics_json TEXT, scraped_at TEXT)""")
    body = "".join(f"Rz {i:05d} Die Invalidität wird nach Art. 16 ATSG bemessen.\n"
                   for i in range(4000))
    conn.execute("INSERT INTO practice VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 ("ksir", "bsv_weisungen", "BSV", "Kreisschreiben", "KSIR",
                  "Kreisschreiben über Invalidität", "2026-01-01", "de", None,
                  "https://example.invalid/ksir.pdf", body, "[]", None))

    class _NoClose:
        def __init__(self, c):
            self._c = c

        def __getattr__(self, name):
            return getattr(self._c, name)

        def close(self):
            pass

    monkeypatch.setattr(m, "_open_practice_db", lambda: _NoClose(conn))
    return body


def test_practice_pages_join_up_to_the_whole_body(practice_db):
    body, parts, offset = practice_db, [], 0
    while True:
        r = m._get_practice(doc_id="ksir", offset=offset, max_chars=50_000)
        assert r["body_total_chars"] == len(body)
        assert r["body_returned_chars"] <= 50_000
        parts.append(r["body_text"])
        if not r["has_more"]:
            assert r["next_offset"] is None
            break
        assert r["body_text"].endswith("\n")           # ends on a line break
        offset = r["next_offset"]
    assert "".join(parts) == body and len(parts) > 1


def test_practice_formatter_names_the_next_call(practice_db):
    r = m._get_practice(doc_id="ksir")
    text = m._format_get_practice_response(r)
    assert f'get_practice(doc_id="ksir", offset={r["next_offset"]})' in text
    assert f"of {len(practice_db):,}" in text
    last = m._get_practice(doc_id="ksir", offset=len(practice_db) - 10)
    assert last["has_more"] is False
    assert "Continued" not in m._format_get_practice_response(last)


def test_practice_paging_clamps_bad_arguments(practice_db):
    r = m._get_practice(doc_id="ksir", offset=-5, max_chars=10**9)
    assert r["body_offset"] == 0 and r["body_returned_chars"] <= m._PRACTICE_PAGE_MAX
    r = m._get_practice(doc_id="ksir", offset="x", max_chars="y")
    assert r["body_offset"] == 0 and r["body_returned_chars"] <= m._PRACTICE_PAGE_DEFAULT


def test_short_practice_document_is_one_page():
    page, start, nxt = m._page_text("kurz\n", 0, 50_000)
    assert (page, start, nxt) == ("kurz\n", 0, None)


def test_get_practice_schema_declares_paging():
    [t] = [t for t in m._list_tools() if t.name == "get_practice"]
    props = t.inputSchema["properties"]
    assert {"offset", "max_chars"} <= set(props)


# ---------------------------------------- 5. consultations per article

def test_consultations_split_by_article():
    procs = [
        {"procedure_title": "Mietrecht: Anfechtung des Anfangsmietzinses",
         "documents": [{"doc_type": "erlaeuternder_bericht"}],
         "erlaeuternder_bericht_passages": [{"text": "Art. 271a"}]},
        {"procedure_title": "PACS", "documents": [{"doc_type": "vorlage"}] * 40},
    ]
    on_article, others = m._split_consultations_by_article(procs, "271a")
    assert [p["procedure_title"] for p in on_article] == [
        "Mietrecht: Anfechtung des Anfangsmietzinses"]
    assert on_article[0]["documents"]                    # kept in full
    assert others == [{"procedure_title": "PACS"}]        # title/date only
    assert m._split_consultations_by_article(procs, None) == (procs, [])
    assert m._split_consultations_by_article([], "1") == ([], [])


def test_get_materialien_uses_the_split():
    assert "_split_consultations_by_article(" in SRC
    assert '"vernehmlassungen_on_act"' in SRC


# ------------------------------------------------------------------ helper

def ast_func(name: str) -> str:
    import ast
    tree = ast.parse(SRC)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(SRC, node)
    raise AssertionError(f"function {name} not found")
