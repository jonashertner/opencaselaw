"""Regression tests for the 2026-07 MCP tool-correctness batch (GitHub #47/#49/#50/#51).

Offline: exercises importable helpers/formatters only, no live DB or network.
"""
import ast

import mcp_server


# ---- #47: get_erwaegung / cite pinpoint prefix normalisation --------------
def test_strip_erw_prefix_handles_de_fr_it_and_bare_numbers():
    f = mcp_server._strip_erw_prefix
    # bare pinpoints pass through untouched
    assert f("2") == "2"
    assert f("2.3") == "2.3"
    # the old lstrip("E.") bug mangled these; now they resolve to the number
    assert f("E. 2") == "2"
    assert f("E.2") == "2"
    assert f("Erw. 2") == "2"        # was "rw. 2" under lstrip("E.")
    assert f("Erwägung 2") == "2"
    assert f("consid. 2") == "2"     # FR, previously unrecognised
    assert f("considérant 2") == "2"
    assert f("cons. 2") == "2"
    assert f("considerando 2") == "2"  # IT
    assert f(None) == ""
    assert f("") == ""


def test_strip_erw_prefix_does_not_eat_a_leading_digit_section():
    # "4.1" must not lose its leading 4 (no marker present)
    assert mcp_server._strip_erw_prefix("4.1") == "4.1"


# ---- #51: LAW_SEARCH_EXPANSIONS has no silently-dropped duplicate keys -----
def test_law_search_expansions_no_duplicate_keys():
    # Duplicate literal keys collapse in the live dict, so re-parse the source
    # AST (which preserves every key) to prove there are none.
    tree = ast.parse(open(mcp_server.__file__, encoding="utf-8").read())
    dict_node = None
    for node in ast.walk(tree):
        if (isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
                and node.target.id == "LAW_SEARCH_EXPANSIONS"
                and isinstance(node.value, ast.Dict)):
            dict_node = node.value
            break
    assert dict_node is not None, "LAW_SEARCH_EXPANSIONS dict literal not found"
    keys = [k.value for k in dict_node.keys if isinstance(k, ast.Constant)]
    assert len(keys) == len(set(keys)), \
        "duplicate keys: " + ", ".join(sorted({k for k in keys if keys.count(k) > 1}))


def test_law_search_expansions_merged_terms_preserved():
    exp = mcp_server.LAW_SEARCH_EXPANSIONS
    # the previously-dropped topical synonyms must survive the merge
    assert "beistandschaft" in exp["kindesschutz"]      # was dropped by the dup
    assert "protection enfant" in exp["kindesschutz"]   # cross-lang side kept too
    assert "tierschutz" in exp["hund"] and "detentore" in exp["hund"]


# ---- #49: find_citations truncation / pagination surfaced in the response --
def test_citations_formatter_shows_total_and_next_offset_when_truncated():
    result = {
        "decision_id": "bge_133_III_393",
        "direction": "incoming",
        "limit": 200, "offset": 0, "next_offset": 200,
        "incoming": [],
        "incoming_total": 947,
        "incoming_returned": 200,
        "incoming_has_more": True,
    }
    text = mcp_server._format_citations_response(result)
    assert "947" in text                 # true total, not just the returned page
    assert "truncated" in text
    assert "next_offset=200" in text


def test_citations_formatter_complete_page_not_marked_truncated():
    result = {
        "decision_id": "bge_140_III_86",
        "direction": "incoming",
        "limit": 200, "offset": 0,
        "incoming": [],
        "incoming_total": 163,
        "incoming_returned": 163,
        "incoming_has_more": False,
    }
    text = mcp_server._format_citations_response(result)
    assert "163" in text
    assert "truncated" not in text


# ---- #42: pure quoted phrase stays an exact phrase MATCH (no OR-alternation) -
def test_is_pure_phrase_query():
    f = mcp_server._is_pure_phrase_query
    assert f('"Treu und Glauben"')
    assert f('  "ne bis in idem"  ')
    assert not f('Treu und Glauben')            # unquoted -> normal NL path
    assert not f('gamma NOT "alpha beta"')      # boolean -> must NOT restrict (keeps NOT working)
    assert not f('"alpha" "beta"')              # two phrases -> not a single pure phrase
    assert not f('')


def test_pure_phrase_strategy_drops_or_alternation():
    # A pure phrase must produce ONLY the phrase MATCH strategy — no nl_or/
    # expansion that would match a single token of the phrase (issue #42).
    strategies, llm_terms = mcp_server._build_query_strategies('"Treu und Glauben"')
    names = {s["name"] for s in strategies}
    assert names == {"raw"}, names
    assert strategies[0]["query"] == '"Treu und Glauben"'
    assert llm_terms == []
    # a boolean query with a quoted phrase keeps the full strategy set (NOT works)
    bool_strats, _ = mcp_server._build_query_strategies('gamma NOT "alpha beta"')
    assert len(bool_strats) > 1


def test_analyze_query_skips_haiku_parse_for_pure_phrase():
    # For a pure phrase the structured (Haiku) parse is skipped, so no doctrine/
    # synonym strategies get injected downstream to broaden past the phrase (#42).
    strategies, llm_terms, parsed, outcome = mcp_server._analyze_query('"Treu und Glauben"', False)
    assert parsed == {}
    assert outcome == "skipped"      # deliberate skip, not a failure
    assert [s["name"] for s in strategies] == ["raw"]
    assert llm_terms == []


def test_bge_lettered_division_citation_is_correct():
    # #48/R1: an old lettered-division BGE row ("116 IA 28" in storage) must cite
    # as "BGE 116 Ia 28", not fall through to a generic "Gericht CH ..." string.
    assert mcp_server._norm_bge_division("IA") == "Ia"
    assert mcp_server._norm_bge_division("ib") == "Ib"
    assert mcp_server._norm_bge_division("III") == "III"
    r = {"court": "bge", "docket_number": "116 IA 28",
         "decision_id": "bge_116 IA 28", "decision_date": "1990-01-01"}
    assert mcp_server._parse_bge_ref(r) == {"volume": 116, "division": "Ia", "page": 28}
    cs = mcp_server._build_citation_strings(r)
    assert cs["citation_string_de"] == "BGE 116 Ia 28"
    assert cs["citation_string_fr"] == "ATF 116 Ia 28"
    # unlettered divisions unchanged
    r2 = {"court": "bge", "docket_number": "140 III 86",
          "decision_id": "bge_140 III 86", "decision_date": "2014-01-01"}
    assert mcp_server._build_citation_strings(r2)["citation_string_de"] == "BGE 140 III 86"


def test_citations_formatter_offset_beyond_end():
    # #49: offset past the end must not render a nonsensical "201-200 of 163".
    result = {"decision_id": "bge_x", "direction": "incoming",
              "limit": 200, "offset": 200, "incoming": [],
              "incoming_total": 163, "incoming_returned": 0, "incoming_has_more": False}
    text = mcp_server._format_citations_response(result)
    assert "0 of 163" in text
    assert "201-200" not in text
    assert "beyond end" in text  # paged past the end -> flagged
    # but a legitimate empty first page (offset 0, no citations) is NOT "beyond end"
    empty = {"decision_id": "bge_y", "direction": "incoming", "limit": 200,
             "offset": 0, "incoming": [], "incoming_total": 0,
             "incoming_returned": 0, "incoming_has_more": False}
    etext = mcp_server._format_citations_response(empty)
    assert "0 of 0" in etext and "beyond end" not in etext


def test_quoted_statute_ref_preserves_article_search():
    # Both a quoted doctrine phrase and a quoted statute ref drop the nl_or noise
    # (pure-phrase strategy short-circuit). But a quoted statute ref must STILL
    # extract a statute so statute-graph article search runs (the advertised
    # `"Art. 8 BV"` syntax reaches ~2x more decisions than a literal phrase). A
    # doctrine phrase extracts none, so it stays an exact literal match (#42).
    assert mcp_server._is_pure_phrase_query('"Art. 8 BV"')
    assert mcp_server._is_pure_phrase_query('"Treu und Glauben"')
    assert mcp_server._extract_query_statute_refs('"Art. 8 BV"')          # -> statute-graph
    assert not mcp_server._extract_query_statute_refs('"Treu und Glauben"')  # -> exact only


# ---- #48: BGE pinpoint page reference parsing ------------------------------
def test_parse_bge_ref_text_shapes():
    f = mcp_server._parse_bge_ref_text
    assert f("BGE 129 I 236") == ("129", "I", 236)
    assert f("ATF 129 I 236") == ("129", "I", 236)
    assert f("BGE 116 Ia 30") == ("116", "IA", 30)     # division upper-cased to storage form
    assert f("BGE 129 I 236, consid. 4") == ("129", "I", 236)  # pinpoint suffix stripped
    assert f("bger_6B_1_2025") is None                 # docket, not BGE
    assert f("4A_1/2020") is None
    assert f("") is None


# ---- #55: deep-research `fetch` discloses the 200k truncation -------------
def _fetch_with_full_text(monkeypatch, n_chars):
    """Run _deep_research_fetch over a decision whose full_text is n_chars long."""
    dec = {
        "decision_id": "bger_6B_973_2023",
        "court": "bger",
        "decision_date": "2025-12-04",
        "docket_number": "6B_973/2023",
        "language": "fr",
        "title": "Faux dans les titres",
        "full_text": "A" * n_chars,
    }
    monkeypatch.setattr(mcp_server, "_resolve_decision_id", lambda i: i)
    monkeypatch.setattr(mcp_server, "get_decision_by_id", lambda i: dec)
    monkeypatch.setattr(mcp_server, "_build_citation_strings", lambda d: {
        "citation_string_de": "BGer 6B_973/2023",
        "canonical_url": "https://opencaselaw.ch/d/bger_6B_973_2023",
    })
    return mcp_server._deep_research_fetch("bger_6B_973_2023")


def test_fetch_marks_truncated_text_and_reports_char_counts(monkeypatch):
    """Pre-fix the text was cut at exactly 200,000 chars mid-sentence with no
    marker in the text and no flag in the JSON, so a deep-research client read
    51% of a judgment as the whole thing."""
    n = 392_688  # real length of 6B_973/2023
    out = _fetch_with_full_text(monkeypatch, n)

    md = out["metadata"]
    assert md["truncated"] is True
    assert md["total_chars"] == n
    assert md["returned_chars"] == mcp_server._FETCH_TEXT_CAP

    # The text is self-describing too, the way get_decision already is.
    assert "Truncated" in out["text"]
    assert f"{n:,}" in out["text"]
    # Document content is still capped; only the notice is appended.
    assert out["text"].count("A") == mcp_server._FETCH_TEXT_CAP


def test_fetch_does_not_mark_untruncated_text(monkeypatch):
    n = 1_000
    out = _fetch_with_full_text(monkeypatch, n)

    md = out["metadata"]
    assert md["truncated"] is False
    assert md["total_chars"] == n and md["returned_chars"] == n
    assert "Truncated" not in out["text"]
    assert out["text"] == "A" * n


def test_fetch_truncation_notice_carries_the_canonical_url(monkeypatch):
    """The notice has to tell the client where the rest actually is."""
    out = _fetch_with_full_text(monkeypatch, 500_000)
    assert out["url"] in out["text"]


# ── original_query / force_natural_language plumbing (integration evaluation 2026-07) ──

def test_strategy_set_is_nl_when_original_is_statute_citation():
    """Sanitized text carries the injected '"OR"'; the ORIGINAL statute
    citation must select the NL strategy ordering (with nl_or_expanded /
    raw_fallback), not the explicit ordering."""
    sanitized = 'Kündigung Art 335 "OR"'
    strategies, _ = mcp_server._build_query_strategies(
        sanitized, original_query="Kündigung Art. 335 OR")
    names = {s["name"] for s in strategies}
    assert "nl_or_expanded" in names or "raw_fallback" in names, names


def test_strategy_set_is_explicit_without_original():
    """Backward-compat: no original_query supplied → sanitized text decides
    (the pre-change behavior for internal callers)."""
    strategies, _ = mcp_server._build_query_strategies('Kündigung Art 335 "OR"')
    names = {s["name"] for s in strategies}
    assert "nl_or_expanded" not in names


def test_force_natural_language_overrides_real_operator_syntax():
    strategies, _ = mcp_server._build_query_strategies(
        "Miete OR Pacht", force_natural_language=True)
    names = {s["name"] for s in strategies}
    assert "nl_or_expanded" in names or "raw_fallback" in names, names


def test_analyze_query_passes_kwargs_through(monkeypatch):
    captured = {}

    def fake_build(raw_query, *, original_query=None, force_natural_language=False):
        captured["original"] = original_query
        captured["force"] = force_natural_language
        return ([{"name": "raw", "query": raw_query, "weight": 1.0}], [])

    monkeypatch.setattr(mcp_server, "_build_query_strategies", fake_build)
    mcp_server._analyze_query(
        "sanitized text", False,
        original_query="Original Art. 41 OR", force_natural_language=True)
    assert captured == {"original": "Original Art. 41 OR", "force": True}
