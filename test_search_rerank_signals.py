import sqlite3
from pathlib import Path

import mcp_server


def _row(decision_id: str, *, bm25: float, title: str = "", regeste: str = "", snippet: str = "") -> dict:
    return {
        "decision_id": decision_id,
        "court": "bger",
        "canton": "CH",
        "chamber": None,
        "docket_number": f"X_{decision_id}/2025",
        "decision_date": "2025-01-01",
        "language": "de",
        "title": title,
        "regeste": regeste,
        "snippet": snippet,
        "source_url": None,
        "pdf_url": None,
        "bm25_score": bm25,
    }


def test_query_strategies_include_legal_expansion():
    strategies, _ = mcp_server._build_query_strategies("asile renvoi")
    expanded = next(s for s in strategies if s["name"] == "nl_or_expanded")
    q = expanded["query"]
    assert "asyl" in q
    assert "wegweisung" in q


def test_query_strategies_include_field_focus_queries():
    strategies, _ = mcp_server._build_query_strategies("Asyl und Wegweisung")
    names = {s["name"] for s in strategies}
    assert any(name.startswith("anchor_") for name in names)
    assert "regeste_focus" in names
    assert "title_focus" in names
    ordered_names = [s["name"] for s in strategies]
    assert ordered_names.index("regeste_focus") < ordered_names.index("nl_or")


def test_detect_query_languages_french():
    langs = mcp_server._detect_query_languages(
        "Je cherche un arrêt sur le permis de construire"
    )
    assert "fr" in langs


def test_expand_rank_terms_for_match_uses_legal_expansions():
    expanded = mcp_server._expand_rank_terms_for_match(["beschleunigtes"])
    assert "verkurzte" in expanded


def test_query_strategies_include_language_focus_when_detected():
    strategies, _ = mcp_server._build_query_strategies(
        "Je cherche un arrêt sur le permis de construire"
    )
    names = {s["name"] for s in strategies}
    assert "lang_fr_and" in names


def test_query_strategies_include_anchor_pair_for_windpark_query():
    strategies, _ = mcp_server._build_query_strategies(
        "Je cherche un arrêt sur le permis de construire d'un parc éolien"
    )
    anchor_queries = {
        s["query"]
        for s in strategies
        if s["name"].startswith("anchor_pair_")
    }
    assert "parc AND eolien" in anchor_queries


def test_rerank_uses_fusion_scores_to_break_close_lexical_ties():
    rows = [
        _row("d_fused", bm25=1.25, title="asyl", regeste="wegweisung"),
        _row("d_plain", bm25=1.05, title="asyl", regeste="wegweisung"),
    ]
    results = mcp_server._rerank_rows(
        rows,
        "Asyl Wegweisung",
        limit=2,
        fusion_scores={
            "d_fused": {"rrf_score": 0.05, "strategy_hits": 3},
            "d_plain": {"rrf_score": 0.0, "strategy_hits": 1},
        },
    )
    assert results[0]["decision_id"] == "d_fused"


def test_rerank_boosts_statute_hits_from_graph_signals(tmp_path: Path, monkeypatch):
    graph_db = tmp_path / "reference_graph.db"
    conn = sqlite3.connect(graph_db)
    conn.executescript(
        """
        CREATE TABLE decision_statutes (
            decision_id TEXT NOT NULL,
            statute_id TEXT NOT NULL,
            mention_count INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE decision_citations (
            source_decision_id TEXT NOT NULL,
            target_ref TEXT NOT NULL,
            target_type TEXT NOT NULL,
            target_decision_id TEXT,
            mention_count INTEGER NOT NULL DEFAULT 1
        );
        """
    )
    conn.execute(
        "INSERT INTO decision_statutes(decision_id, statute_id, mention_count) VALUES (?, ?, ?)",
        ("d_hit", "ART.8.EMRK", 3),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(mcp_server, "GRAPH_DB_PATH", graph_db)
    monkeypatch.setattr(mcp_server, "GRAPH_SIGNALS_ENABLED", True)

    rows = [
        _row("d_miss", bm25=1.0),
        _row("d_hit", bm25=1.0),
    ]
    results = mcp_server._rerank_rows(
        rows,
        "Art. 8 EMRK",
        limit=2,
        fusion_scores={
            "d_miss": {"rrf_score": 0.0, "strategy_hits": 1},
            "d_hit": {"rrf_score": 0.0, "strategy_hits": 1},
        },
    )
    assert results[0]["decision_id"] == "d_hit"


def test_passage_snippet_prefers_relevant_paragraph():
    text = (
        "Einleitung ohne Treffer.\n\n"
        "Dieser Absatz enthält Art. 8 EMRK und den relevanten Kern der Begründung.\n\n"
        "Schlussteil."
    )
    snippet = mcp_server._select_best_passage_snippet(
        text,
        rank_terms=["art", "8", "emrk"],
        phrase="art 8 emrk",
        fallback="fallback",
    )
    assert snippet is not None
    # _highlight_terms wraps matches in <mark> tags; strip them for content check
    plain = snippet.replace("<mark>", "").replace("</mark>", "")
    assert "Art. 8 EMRK" in plain


def test_asylum_query_boosts_bvger_court():
    rows = [
        _row("d_bger", bm25=1.0, title="Asyl und Wegweisung", regeste="Asyl und Wegweisung"),
        _row("d_bvger", bm25=1.0, title="Asyl und Wegweisung", regeste="Asyl und Wegweisung"),
    ]
    rows[0]["court"] = "bger"
    rows[1]["court"] = "bvger"
    rows[1]["docket_number"] = "E-7414/2015"

    results = mcp_server._rerank_rows(
        rows,
        "Asyl und Wegweisung",
        limit=2,
        fusion_scores={
            "d_bger": {"rrf_score": 0.0, "strategy_hits": 1},
            "d_bvger": {"rrf_score": 0.0, "strategy_hits": 1},
        },
    )
    assert results[0]["decision_id"] == "d_bvger"


def test_asylum_accelerated_query_boosts_equivalent_term():
    rows = [
        _row("d_plain", bm25=1.0, title="Asyl und Wegweisung", regeste="Asyl und Wegweisung"),
        _row(
            "d_equiv",
            bm25=1.0,
            title="Asyl und Wegweisung",
            regeste="Asyl und Wegweisung (verkuerzte Beschwerdefrist)",
        ),
    ]
    rows[0]["court"] = "bvger"
    rows[1]["court"] = "bvger"

    results = mcp_server._rerank_rows(
        rows,
        "Asyl und Wegweisung beschleunigtes Verfahren",
        limit=2,
        fusion_scores={
            "d_plain": {"rrf_score": 0.0, "strategy_hits": 1},
            "d_equiv": {"rrf_score": 0.0, "strategy_hits": 1},
        },
    )
    assert results[0]["decision_id"] == "d_equiv"


def test_decision_intent_query_prefers_high_court():
    rows = [
        _row("d_bger", bm25=1.0, title="permis de construire", regeste="parc eolien"),
        _row("d_canton", bm25=1.0, title="permis de construire", regeste="parc eolien"),
    ]
    rows[0]["court"] = "bger"
    rows[1]["court"] = "ne_gerichte"

    results = mcp_server._rerank_rows(
        rows,
        "Je cherche un arrêt sur le permis de construire",
        limit=2,
        fusion_scores={
            "d_bger": {"rrf_score": 0.0, "strategy_hits": 1},
            "d_canton": {"rrf_score": 0.0, "strategy_hits": 1},
        },
    )
    assert results[0]["decision_id"] == "d_bger"


def test_query_has_numeric_terms_detection():
    assert mcp_server._query_has_numeric_terms("Art. 8 EMRK")
    assert not mcp_server._query_has_numeric_terms("Asyl und Wegweisung")


def test_query_has_expandable_terms_detection():
    assert mcp_server._query_has_expandable_terms("Asyl und Wegweisung")
    assert not mcp_server._query_has_expandable_terms("Rechtsmittelbelehrung")


def test_extract_query_statute_refs_handles_multilingual_paragraph_marker():
    refs = mcp_server._extract_query_statute_refs("selon art. 8 al. 1 CEDH")
    assert "ART.8.ABS.1.CEDH" in refs
    assert "ART.8.AL" not in refs


def test_extract_query_statute_refs_handles_ordinal_suffixes():
    refs = mcp_server._extract_query_statute_refs(
        "Art. 8bis BV, Art. 34ter BV und Art. 8 Abs. 2bis BV"
    )
    assert "ART.8bis.BV" in refs
    assert "ART.34ter.BV" in refs
    assert "ART.8.ABS.2bis.BV" in refs
    assert "ART.8b.IS" not in refs
    assert "ART.34t.ER" not in refs


def test_parse_statute_ref_handles_ordinal_suffixes():
    article, paragraph, law = mcp_server._parse_statute_ref("ART.8bis.BV")
    assert article == "8bis"
    assert paragraph is None
    assert law == "BV"

    article, paragraph, law = mcp_server._parse_statute_ref("ART.8.ABS.2bis.BV")
    assert article == "8"
    assert paragraph == "2bis"
    assert law == "BV"


def test_looks_like_docket_query_is_strict_for_long_nl_query():
    assert mcp_server._looks_like_docket_query("4A_291/2017")
    assert not mcp_server._looks_like_docket_query(
        "ZB.2016.28 (13.04.2017 / Rückweisung 23.08.2018) – "
        "Herabsetzung des Mietzinses (BGer 4A_291/2017)"
    )


def test_extract_inline_docket_candidates():
    cands = mcp_server._extract_inline_docket_candidates(
        "Rückweisung gemäss BGer 4A_291/2017 und Folgeentscheid ZB.2016.28"
    )
    assert "4A_291/2017" in cands
    assert "ZB.2016.28" in cands


def test_detect_query_preferred_courts_bger():
    prefs = mcp_server._detect_query_preferred_courts("BGer 4A_291/2017")
    assert "bger" in prefs
    assert "bge" in prefs


def test_parse_docket_family_and_serial_extract():
    family = mcp_server._parse_docket_family("1A.122/2005")
    assert family == ("1A", 122, "2005")
    assert mcp_server._parse_docket_family("Asyl und Wegweisung") is None
    assert mcp_server._extract_docket_serial(
        "1A.124/2005",
        prefix="1A",
        year="2005",
    ) == 124
    assert mcp_server._extract_docket_serial(
        "E-7414/2015",
        prefix="1A",
        year="2005",
    ) is None
    variants = mcp_server._build_docket_family_candidates(
        prefix="1A",
        serial=122,
        year="2005",
    )
    assert "1A.124/2005" in variants
    assert "1A.122_2005" in variants


def test_rerank_boosts_language_match():
    rows = [
        _row("d_fr", bm25=1.0, title="permis de construire", regeste="parc eolien"),
        _row("d_de", bm25=1.0, title="baubewilligung", regeste="windpark"),
    ]
    rows[0]["language"] = "fr"
    rows[1]["language"] = "de"

    results = mcp_server._rerank_rows(
        rows,
        "Je cherche un arrêt sur le permis de construire",
        limit=2,
        fusion_scores={
            "d_fr": {"rrf_score": 0.0, "strategy_hits": 1},
            "d_de": {"rrf_score": 0.0, "strategy_hits": 1},
        },
    )
    assert results[0]["decision_id"] == "d_fr"


def test_graph_signal_map_falls_back_to_legacy_target_decision_id(
    tmp_path: Path, monkeypatch
):
    graph_db = tmp_path / "reference_graph.db"
    conn = sqlite3.connect(graph_db)
    conn.executescript(
        """
        CREATE TABLE decision_statutes (
            decision_id TEXT NOT NULL,
            statute_id TEXT NOT NULL,
            mention_count INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE decision_citations (
            source_decision_id TEXT NOT NULL,
            target_ref TEXT NOT NULL,
            target_type TEXT NOT NULL,
            target_decision_id TEXT,
            mention_count INTEGER NOT NULL DEFAULT 1
        );
        INSERT INTO decision_citations(source_decision_id, target_ref, target_type, target_decision_id, mention_count)
        VALUES ('src1', 'VB_2018_00411', 'docket', 'target1', 3);
        """
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(mcp_server, "GRAPH_DB_PATH", graph_db)
    monkeypatch.setattr(mcp_server, "GRAPH_SIGNALS_ENABLED", True)

    signal_map = mcp_server._load_graph_signal_map(
        ["target1"],
        query_statutes=set(),
        query_citations=set(),
    )
    assert signal_map["target1"]["incoming_citations"] == 3


def test_graph_signal_map_preserves_fractional_confidence_weights(
    tmp_path: Path, monkeypatch
):
    graph_db = tmp_path / "reference_graph.db"
    conn = sqlite3.connect(graph_db)
    conn.executescript(
        """
        CREATE TABLE decision_statutes (
            decision_id TEXT NOT NULL,
            statute_id TEXT NOT NULL,
            mention_count INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE decision_citations (
            source_decision_id TEXT NOT NULL,
            target_ref TEXT NOT NULL,
            target_type TEXT NOT NULL,
            mention_count INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE citation_targets (
            source_decision_id TEXT NOT NULL,
            target_ref TEXT NOT NULL,
            target_decision_id TEXT NOT NULL,
            match_type TEXT NOT NULL DEFAULT 'docket_norm',
            confidence_score REAL NOT NULL DEFAULT 1.0
        );
        INSERT INTO decision_citations(source_decision_id, target_ref, target_type, mention_count)
        VALUES ('src1', 'VB_2018_00411', 'docket', 1);
        INSERT INTO citation_targets(source_decision_id, target_ref, target_decision_id, match_type, confidence_score)
        VALUES ('src1', 'VB_2018_00411', 'target1', 'docket_norm', 0.49);
        """
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(mcp_server, "GRAPH_DB_PATH", graph_db)
    monkeypatch.setattr(mcp_server, "GRAPH_SIGNALS_ENABLED", True)

    signal_map = mcp_server._load_graph_signal_map(
        ["target1"],
        query_statutes=set(),
        query_citations=set(),
    )
    assert 0.0 < signal_map["target1"]["incoming_citations"] < 1.0


def test_extract_inline_docket_candidates_text_position_order():
    """Issue 3: docket candidates should follow text position, not regex pattern order."""
    # AB.2024.12345 matches pattern 2, 4A_291/2017 matches pattern 1.
    # Without position sorting, pattern 1 match would come first.
    cands = mcp_server._extract_inline_docket_candidates(
        "comparing AB.2024.12345 with 4A_291/2017"
    )
    assert len(cands) == 2
    assert cands[0] == "AB.2024.12345"
    assert cands[1] == "4A_291/2017"


def test_collapse_spaced_docket_basic():
    # Standard 3-part docket with spaces
    assert mcp_server._collapse_spaced_docket("6B 1234 2025") is not None
    result = mcp_server._collapse_spaced_docket("6B 1234 2025")
    assert "6B" in result and "1234" in result and "2025" in result

    # 2-digit year gets expanded
    result = mcp_server._collapse_spaced_docket("7W 15 25")
    assert result is not None
    assert "2025" in result

    # Not a docket — too many parts
    assert mcp_server._collapse_spaced_docket("one two three four five") is None
    # Not a docket — no letters in first part
    assert mcp_server._collapse_spaced_docket("123 456 7890") is None
    # Empty
    assert mcp_server._collapse_spaced_docket("") is None


def test_looks_like_docket_query_accepts_spaced_docket():
    assert mcp_server._looks_like_docket_query("6B 1234 2025") is True
    assert mcp_server._looks_like_docket_query("7W 15 25") is True


# ── Date-sort rerank skip (integration evaluation latency audit 2026-07) ──────────────
# Under sort=date_desc/date_asc the date re-sort overwrites every relevance
# boost, so the ~3s LLM rerank (and the cross-encoder pass) ran for nothing.

def _count_calls(monkeypatch):
    calls = {"ce": 0, "llm": 0}

    def fake_ce(scored, *a, **k):
        calls["ce"] += 1
        return scored

    def fake_llm(scored, *a, **k):
        calls["llm"] += 1
        return scored

    monkeypatch.setattr(mcp_server, "_apply_cross_encoder_boosts", fake_ce)
    monkeypatch.setattr(mcp_server, "_apply_llm_rerank", fake_llm)
    return calls


def test_rerank_skipped_on_date_sorts(monkeypatch):
    rows = [
        _row("d_new", bm25=1.0) | {"decision_date": "2024-05-01"},
        _row("d_old", bm25=2.0) | {"decision_date": "2019-01-01"},
    ]
    for sort, first in (("date_desc", "d_new"), ("date_asc", "d_old")):
        calls = _count_calls(monkeypatch)
        results = mcp_server._rerank_rows(rows, "Kündigung", limit=2, sort=sort)
        assert calls == {"ce": 0, "llm": 0}, f"rerank ran under sort={sort}"
        # the date order itself must still hold
        assert results[0]["decision_id"] == first


def test_rerank_still_runs_on_relevance_sorts(monkeypatch):
    rows = [_row("d1", bm25=1.0), _row("d2", bm25=2.0)]
    for sort in (None, "relevance"):
        calls = _count_calls(monkeypatch)
        mcp_server._rerank_rows(rows, "Kündigung", limit=2, sort=sort)
        assert calls["ce"] == 1 and calls["llm"] == 1, f"rerank skipped under sort={sort}"
