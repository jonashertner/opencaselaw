"""Tests for _compute_pinpoint and _pinpoint_enrich_results.

These cover the auto-pinpoint enrichment path that wires the existing
find_relevant_erwaegung resolver into search_decisions / find_leading_cases
results. Confidence floors, URL-anchor shape, batch behaviour.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import mcp_server


def _make_structure_db(tmp_path: Path, paragraphs: list[tuple[str, str, str]]) -> sqlite3.Connection:
    """Build a temp decision_structure-shaped DB with FTS5 paragraph index.

    paragraphs is a list of (decision_id, e_number, text).
    Returns an open connection (caller closes).
    """
    db = tmp_path / "structure.db"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE erwaegungen_paragraph (
            decision_id TEXT, e_number TEXT, depth INTEGER, parent TEXT,
            text TEXT,
            PRIMARY KEY (decision_id, e_number)
        );
        CREATE INDEX idx_erw_decision ON erwaegungen_paragraph(decision_id);
        CREATE VIRTUAL TABLE erwaegungen_paragraph_fts USING fts5(
            text,
            content='erwaegungen_paragraph',
            content_rowid='rowid',
            tokenize='unicode61 remove_diacritics 1'
        );
        """
    )
    for did, en, txt in paragraphs:
        depth = en.count(".") + 1
        parent = en.rsplit(".", 1)[0] if "." in en else None
        conn.execute(
            "INSERT INTO erwaegungen_paragraph(decision_id, e_number, depth, parent, text) "
            "VALUES (?, ?, ?, ?, ?)",
            (did, en, depth, parent, txt),
        )
    # Rebuild FTS5 from content
    conn.execute(
        "INSERT INTO erwaegungen_paragraph_fts(erwaegungen_paragraph_fts) "
        "VALUES('rebuild')"
    )
    conn.commit()
    return conn


def test_compute_pinpoint_high_confidence_when_one_clear_match(tmp_path):
    conn = _make_structure_db(tmp_path, [
        ("bge_BGE_140_III_86", "1", "Sachverhalt der Streitsache hier."),
        ("bge_BGE_140_III_86", "2.1", "Allgemeine Erwägungen zur Beschwerdebefugnis nach Art. 76 BGG."),
        ("bge_BGE_140_III_86", "3", "Zum Schadenersatz nach Art. 41 OR siehe BGE 134 III 511."),
    ])
    try:
        pp = mcp_server._compute_pinpoint(
            "bge_BGE_140_III_86",
            "Beschwerdebefugnis Art. 76 BGG",
            conn=conn,
        )
        assert pp is not None
        assert pp["e_number"] == "2.1"
        assert pp["confidence"] in {"high", "medium"}
        assert "Beschwerdebefugnis" in pp["matched_sentence"]
        assert pp["url"].startswith("https://")
        assert "highlight=" in pp["url"]
        assert "e=2.1" in pp["url"]
    finally:
        conn.close()


def test_compute_pinpoint_returns_none_when_claim_too_short(tmp_path):
    conn = _make_structure_db(tmp_path, [
        ("d_1", "1", "Etwas hier zum Mietrecht und der Kündigung der Wohnung."),
    ])
    try:
        assert mcp_server._compute_pinpoint("d_1", "ab", conn=conn) is None
        assert mcp_server._compute_pinpoint("d_1", "  ", conn=conn) is None
        assert mcp_server._compute_pinpoint("d_1", "", conn=conn) is None
    finally:
        conn.close()


def test_compute_pinpoint_returns_none_when_no_match(tmp_path):
    conn = _make_structure_db(tmp_path, [
        ("d_1", "1", "Strafrechtliche Erwägungen zum Diebstahl."),
    ])
    try:
        pp = mcp_server._compute_pinpoint(
            "d_1", "Mietvertrag Kündigung Wohnung", conn=conn
        )
        assert pp is None
    finally:
        conn.close()


def test_compute_pinpoint_returns_none_when_empty_decision_id(tmp_path):
    conn = _make_structure_db(tmp_path, [
        ("d_1", "1", "Anything goes here for the test."),
    ])
    try:
        assert mcp_server._compute_pinpoint("", "anything", conn=conn) is None
    finally:
        conn.close()


def test_compute_pinpoint_returns_none_when_no_fts_index(tmp_path):
    db = tmp_path / "nofts.db"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE erwaegungen_paragraph (
            decision_id TEXT, e_number TEXT, depth INTEGER, parent TEXT,
            text TEXT, PRIMARY KEY (decision_id, e_number)
        );
        """
    )
    conn.execute(
        "INSERT INTO erwaegungen_paragraph VALUES (?, ?, ?, ?, ?)",
        ("d_1", "1", 1, None, "Some text about anything"),
    )
    conn.commit()
    try:
        # No erwaegungen_paragraph_fts table → resolver must return None.
        pp = mcp_server._compute_pinpoint("d_1", "anything", conn=conn)
        assert pp is None
    finally:
        conn.close()


def test_compute_pinpoint_handles_fts_special_chars_via_phrase_quote(tmp_path):
    # Claims with characters FTS5 reserves (parens, asterisks) shouldn't
    # crash; phrase-quoting in the resolver makes them safe.
    conn = _make_structure_db(tmp_path, [
        ("d_1", "1", "Erwägungen zum Schadenersatz nach Art. 41 OR sind hier."),
    ])
    try:
        pp = mcp_server._compute_pinpoint(
            "d_1", "Schadenersatz (Art. 41 OR)", conn=conn
        )
        # May or may not match — but must not raise.
        if pp is not None:
            assert pp["e_number"] == "1"
    finally:
        conn.close()


def test_pinpoint_enrich_results_attaches_to_top_n(tmp_path, monkeypatch):
    conn = _make_structure_db(tmp_path, [
        ("d_1", "1", "Diskussion über Mietrecht und Kündigung der Wohnung."),
        ("d_1", "2.1", "Detailliertes über Mietrecht Kündigung Probezeit."),
        ("d_2", "1", "Allgemeine Mietrecht-Kündigungsfrist Erwägungen."),
        ("d_3", "1", "Mietrecht Kündigung der Familienwohnung."),
        ("d_4", "1", "Anderes Strafrecht Thema hier ohne Match."),
    ])
    # Enrichment now parallelises: each worker opens its OWN connection via
    # _get_structure_conn (sqlite3 conns are thread-affine). A shared-conn
    # lambda would hand workers a closed handle and silently degrade every
    # pinpoint to None — the factory keeps the test exercising real lookups.
    db_file = tmp_path / "structure.db"

    def _fresh_conn():
        c = sqlite3.connect(str(db_file), check_same_thread=False)
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setattr(mcp_server, "_get_structure_conn", _fresh_conn)
    try:
        results = [
            {"decision_id": "d_1", "court": "bger"},
            {"decision_id": "d_2", "court": "bger"},
            {"decision_id": "d_3", "court": "bger"},
            {"decision_id": "d_4", "court": "bger"},
            {"decision_id": "d_5_no_struct", "court": "bger"},
            {"decision_id": "d_6_skipped", "court": "bger"},
        ]
        mcp_server._pinpoint_enrich_results(results, "Mietrecht Kündigung", top_n=4)

        # First 4 entries enriched (presence of pinpoint key — value may be None)
        for r in results[:4]:
            assert "pinpoint" in r
        # Entries beyond top_n are untouched
        for r in results[4:]:
            assert "pinpoint" not in r
        # The parallel path must COMPUTE, not merely decorate: d_1 has two
        # matching paragraphs and must carry a real pinpoint.
        assert results[0]["pinpoint"] is not None
        assert results[0]["pinpoint"]["e_number"] in {"1", "2.1"}
        # d_4 has no good match → pinpoint should be None.
        assert results[3]["pinpoint"] is None
    finally:
        conn.close()


def test_pinpoint_enrich_results_noop_on_empty_claim(tmp_path, monkeypatch):
    conn = _make_structure_db(tmp_path, [("d_1", "1", "Stuff here")])
    monkeypatch.setattr(mcp_server, "_get_structure_conn", lambda: conn)
    try:
        results = [{"decision_id": "d_1"}]
        mcp_server._pinpoint_enrich_results(results, "", top_n=5)
        assert "pinpoint" not in results[0]
        mcp_server._pinpoint_enrich_results(results, "  ", top_n=5)
        assert "pinpoint" not in results[0]
        mcp_server._pinpoint_enrich_results(results, "ab", top_n=5)
        assert "pinpoint" not in results[0]
    finally:
        conn.close()


def test_pinpoint_enrich_results_noop_when_db_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(mcp_server, "_get_structure_conn", lambda: None)
    results = [{"decision_id": "d_1"}]
    mcp_server._pinpoint_enrich_results(results, "anything substantive", top_n=5)
    assert "pinpoint" not in results[0]


def test_url_includes_highlight_and_e_anchor(tmp_path):
    # Need a corpus with multiple paragraphs so BM25 IDF is non-trivial;
    # single-row tables collapse all terms to log(1) ≈ 0 weights.
    conn = _make_structure_db(tmp_path, [
        ("bge_BGE_140_III_86", "1", "Sachverhalt der Streitsache hier."),
        ("bge_BGE_140_III_86", "2.1", "Erwägung über andere Themen."),
        ("bge_BGE_140_III_86", "2.2", "Etwas zum Strafrecht ohne Bezug."),
        ("bge_BGE_140_III_86", "2.3",
         "Die Beschwerdelegitimation nach Art. 76 BGG verlangt ein "
         "schutzwürdiges Interesse."),
        ("bge_BGE_140_III_86", "3", "Schadenersatz nach Art. 41 OR."),
        ("bge_BGE_140_III_86", "4", "Dispositiv des Bundesgerichts."),
    ])
    try:
        pp = mcp_server._compute_pinpoint(
            "bge_BGE_140_III_86",
            "Beschwerdelegitimation Art. 76 BGG",
            conn=conn,
        )
        assert pp is not None
        assert pp["e_number"] == "2.3"
        # URL gets ?highlight=<urlencoded sentence>&e=2.3 — both must be present.
        assert "highlight=" in pp["url"]
        assert "e=2.3" in pp["url"]
        # The matched sentence (used as ?highlight= source) is preserved.
        assert "Beschwerdelegitimation" in pp["matched_sentence"]
    finally:
        conn.close()


# ───────────────────────── False-confidence regression tests ─────────────────


def test_or_fallback_does_not_promote_thin_overlap_to_high(tmp_path):
    """REGRESSION: 3-token claim, paragraph matches only 1 of the 3 terms.

    Previously: phrase pass empty → OR pass returns single row →
    ``gap_ratio = 999.0`` short-circuited the gap-based confidence
    branch → spurious "high" pinpoint emitted to the user. Coverage
    guard now suppresses entirely (only 33% of claim tokens hit).
    """
    conn = _make_structure_db(tmp_path, [
        # Single paragraph contains "Kündigung" but not "Mietrecht" or
        # "Wohnung". The OR fallback ("Mietrecht OR Kündigung OR
        # Wohnung") matches it at very low BM25 (only 1 of 3 tokens).
        ("d_1", "1", "Eine kurze Bemerkung zur Kündigung im allgemeinen."),
    ])
    try:
        pp = mcp_server._compute_pinpoint(
            "d_1", "Mietrecht Kündigung Wohnung", conn=conn
        )
        assert pp is None, (
            f"Expected None for thin OR-coverage match (1 of 3 claim "
            f"tokens), got {pp!r} — false-high-confidence regression."
        )
    finally:
        conn.close()


def test_or_fallback_caps_partial_coverage_at_medium(tmp_path):
    """4-token claim, 2 of 4 in matched paragraph (coverage 50 %).

    Hits the lower coverage bound (≥ 0.5) so the result is *not*
    suppressed, but is capped at ``medium`` — high requires ≥ 70 %
    coverage on multi-token claims. Multi-paragraph corpus so BM25
    IDF doesn't collapse to ~0.
    """
    conn = _make_structure_db(tmp_path, [
        # 2 of 4 claim tokens (Schadenersatz, Verschulden) appear here.
        ("d_1", "1",
         "Der Schadenersatz setzt ein Verschulden voraus — Grundsatz "
         "des ausservertraglichen Haftungsrechts gemäss Art. 41 OR."),
        # Distractors so IDF gives matching tokens meaningful weight.
        ("d_1", "2", "Sachverhalt: Der Beschwerdeführer reichte am 5. Januar."),
        ("d_1", "3", "Verfahren vor Vorinstanz: Berufungsinstanz hat abgewiesen."),
        ("d_1", "4", "Kostenfolgen werden nach Massgabe des Obsiegens verteilt."),
        ("d_1", "5", "Dispositiv: Die Beschwerde wird abgewiesen."),
    ])
    try:
        pp = mcp_server._compute_pinpoint(
            "d_1",
            "Schadenersatz Verschulden Mietrecht Wohnung",
            conn=conn,
        )
        assert pp is not None, "Expected a medium-confidence pinpoint, not None"
        assert pp["confidence"] == "medium", (
            f"Expected 'medium' for 2-of-4 coverage, got "
            f"{pp['confidence']!r}"
        )
    finally:
        conn.close()


def test_full_coverage_keeps_high_confidence(tmp_path):
    """Sanity check: when all multi-token claim words appear in the
    matched paragraph (and only there), the high-confidence path still
    works. Multi-paragraph corpus needed for non-trivial BM25.
    """
    conn = _make_structure_db(tmp_path, [
        ("d_1", "1",
         "Mietrecht Kündigung Wohnung — alle drei Schlüsselwörter kommen vor "
         "und zwar sehr explizit zur Demonstration der vollen Token-Coverage."),
        ("d_1", "2", "Etwas ganz anderes über Strafrecht ohne Bezug."),
        ("d_1", "3", "Sachverhalt: Der Beschwerdeführer reichte am 5. Januar."),
        ("d_1", "4", "Verfahren vor Vorinstanz: Berufung ist abgewiesen."),
        ("d_1", "5", "Dispositiv: Die Beschwerde wird abgewiesen."),
        ("d_1", "6", "Kostenfolgen verteilt nach Massgabe des Obsiegens."),
    ])
    try:
        pp = mcp_server._compute_pinpoint(
            "d_1", "Mietrecht Kündigung Wohnung", conn=conn
        )
        assert pp is not None
        assert pp["e_number"] == "1"
        assert pp["confidence"] == "high"
    finally:
        conn.close()


def test_single_row_with_weak_bm25_returns_none(tmp_path):
    """Direct guard for the original bug: single-row OR match with very
    low absolute BM25 score (e.g. ~ 1e-6) was treated as ``gap_ratio =
    999.0`` and promoted to high. Now the absolute-strength branch
    rejects it correctly.
    """
    conn = _make_structure_db(tmp_path, [
        # Long paragraph diluting BM25 of any single token hit. Only
        # "Kündigung" of the claim's tokens appears.
        ("d_1", "1",
         "Im vorliegenden Fall geht es um zahlreiche Sachverhalte und "
         "Erwägungen zu unterschiedlichen Rechtsfragen, die das Gericht "
         "abschliessend zu beurteilen hatte. Die Kündigung wird hier "
         "nur am Rande erwähnt unter vielen anderen Aspekten des "
         "Verfahrens, das eine umfangreiche Beweisaufnahme verlangte. "
         "Diese Erwägungen sind allgemeiner Natur."),
    ])
    try:
        pp = mcp_server._compute_pinpoint(
            "d_1", "Mietvertrag Wohnungswechsel Vertragsbruch", conn=conn
        )
        # None of the claim's actual tokens appear → OR fallback finds 0 rows
        # → returns None. (If a substring partially matched, coverage check
        # would still suppress.) This documents the strict-by-default policy.
        assert pp is None
    finally:
        conn.close()


def test_score_pinpoint_confidence_directly(tmp_path):
    """Unit-level coverage of the shared scorer (independent of the FTS5
    layer) — fixes regressions in confidence semantics quickly."""
    f = mcp_server._score_pinpoint_confidence

    # Clear gap, full coverage → high
    assert f([-3.0, -1.0], "Mietrecht Kündigung", "Mietrecht Kündigung Wohnung") == "high"
    # Marginal gap, full coverage → medium
    assert f([-1.5, -1.2], "Mietrecht Kündigung", "Mietrecht Kündigung") == "medium"
    # Strong single-row absolute → high (sanity)
    assert f([-3.0], "Mietrecht", "Mietrecht ist hier zentral.") == "high"
    # Weak single-row absolute (the original bug) → None
    assert f([-1e-6], "Mietrecht Kündigung Wohnung", "Eine kurze Bemerkung zur Kündigung.") is None
    # Empty / no scores → None
    assert f([], "anything", "anything") is None


def test_repeated_claim_tokens_are_deduped_before_coverage(tmp_path):
    """Claim 'Mietrecht Mietrecht Mietrecht Kündigung' has 4 raw tokens
    but only 2 distinct ones. Without dedup, a paragraph hitting just
    'Mietrecht' would score 3/4 = 75 % → high. With dedup it's 1/2 =
    50 % → capped at medium. Matters for any client that builds claims
    by concatenating boosted terms.
    """
    matched, total = mcp_server._claim_token_coverage(
        "Mietrecht Mietrecht Mietrecht Kündigung",
        "Hier wird Mietrecht behandelt — das ist der Kern.",
    )
    assert (matched, total) == (1, 2), (
        f"Expected (1, 2) after dedup, got ({matched}, {total})"
    )


def test_phrase_match_with_partial_coverage_capped_at_medium(tmp_path):
    """A phrase match in principle implies all phrase tokens appear, so
    coverage is usually 100 % — but if some claim tokens are filtered
    out (≤ 2 chars), the kept-tokens denominator shrinks and the
    high-confidence path could fire on a thin signal. This test pins
    the cap behaviour for the rare case where 0.5 ≤ coverage < 0.7
    AND match_kind = "phrase" (e.g. 3 of 5 distinct claim tokens hit).
    """
    f = mcp_server._score_pinpoint_confidence
    # 5 distinct claim tokens, 3 in text → coverage 0.6 (in cap range).
    out = f(
        [-3.0],
        "Mietrecht Kündigung Wohnung Schaden Verschulden",
        "Mietrecht Kündigung Wohnung — Sachverhalt.",
        match_kind="phrase",
    )
    assert out == "medium", (
        f"Expected medium for 3/5 coverage phrase match, got {out!r}"
    )


def test_phrase_match_below_coverage_floor_suppressed(tmp_path):
    """Phrase match where coverage drops below 0.5 — must still suppress
    (return None), even though phrase signal is normally trusted.
    """
    f = mcp_server._score_pinpoint_confidence
    # 4 claim words, paragraph text contains just 1 of them. Coverage 0.25.
    out = f([-3.0],
            "Mietrecht Kündigung Wohnung Schaden",
            "Mietrecht behandelt im Rest.",
            match_kind="phrase")
    assert out is None, f"Expected None for 1/4 coverage phrase, got {out!r}"


def test_stopword_heavy_claim_doesnt_inflate_coverage(tmp_path):
    """Claim full of long-but-low-signal words ('Voraussetzungen',
    'Frage', 'Aspekt', etc. — common discursive German nouns). If the
    paragraph contains those generic words but NOT the operative legal
    term, coverage looks high but the result is semantically wrong.
    Pins the boundary: even with 5/6 coverage, the result surfaces —
    we accept this trade-off and document it (token coverage is a
    syntactic guard, not a semantic one).
    """
    matched, total = mcp_server._claim_token_coverage(
        "Voraussetzungen Frage Aspekt zur Mietrecht-Kündigung Wohnungsrecht",
        "Voraussetzungen für die Frage des Aspekts der Wohnungsrecht-Erwägungen.",
    )
    # 4 of 6 distinct claim tokens (>2 chars) appear → 4/6 = 0.67.
    # voraussetzungen, frage, aspekt, wohnungsrecht ✓ (mietrecht-kündigung is hyphenated)
    # Note: \w+ splits on -, so "Mietrecht-Kündigung" → tokens {"Mietrecht", "Kündigung"}.
    assert total >= 4
    assert matched >= 3, f"Expected ≥ 3 tokens to match, got {matched}/{total}"


def test_dedup_prevents_inflation_in_compute_pinpoint(tmp_path):
    """End-to-end: a claim with repeated terms doesn't fool the resolver
    into emitting high confidence for a paragraph hitting only one
    distinct term.
    """
    conn = _make_structure_db(tmp_path, [
        ("d_1", "1", "Eine kurze Bemerkung zur Kündigung im allgemeinen."),
        ("d_1", "2", "Sachverhalt: Der Beschwerdeführer reichte am 5. Januar."),
        ("d_1", "3", "Verfahren vor Vorinstanz: Berufung ist abgewiesen."),
        ("d_1", "4", "Dispositiv: Die Beschwerde wird abgewiesen."),
    ])
    try:
        # Claim with deliberate repetition. Distinct tokens: {Mietrecht,
        # Kündigung, Wohnung}. Paragraph 1 has only 'Kündigung' → coverage
        # 1/3 = 33 % → must be suppressed despite raw-token-list 4/6 = 67 %.
        pp = mcp_server._compute_pinpoint(
            "d_1",
            "Mietrecht Mietrecht Kündigung Kündigung Wohnung",
            conn=conn,
        )
        assert pp is None, (
            f"Repeated-token claim should still be suppressed for 1-of-3 "
            f"distinct coverage; got {pp!r}"
        )
    finally:
        conn.close()


def test_all_stopword_claim_suppressed(tmp_path):
    """Lexical-bait scenario: claim is purely generic Swiss legal
    discourse ("Verfahren Beschwerde Bundesgericht Erwägung"). After
    stopword filtering, total_n == 0 → must suppress entirely. Empirical
    bench against real BGEs showed this case as the sole remaining FP
    pre-fix (50 % coverage, 'medium' confidence on lexical bait).
    """
    matched, total = mcp_server._claim_token_coverage(
        "Verfahren Beschwerde Bundesgericht Erwägung",
        "Im Verfahren vor Bundesgericht ist die Beschwerde abzuweisen.",
    )
    assert (matched, total) == (0, 0), (
        f"Stopword-only claim should drop to (0, 0); got ({matched}, {total})"
    )

    # End-to-end: scorer must return None even with a strong-looking BM25.
    out = mcp_server._score_pinpoint_confidence(
        [-5.33, -3.61],
        "Verfahren Beschwerde Bundesgericht Erwägung",
        "Im Verfahren vor Bundesgericht ist die Beschwerde abzuweisen.",
        match_kind="or",
    )
    assert out is None, f"All-stopword claim must suppress; got {out!r}"


def test_stopword_filter_preserves_substantive_terms(tmp_path):
    """Mixed claim: some stopwords + meaningful legal terms. The
    meaningful terms should drive coverage. E.g. "Verfahren im
    Mietrecht" → after filter: {Mietrecht}. Paragraph hitting
    Mietrecht → 1/1 = 100 % coverage.
    """
    matched, total = mcp_server._claim_token_coverage(
        "Verfahren Beschwerde Bundesgericht im Mietrecht",
        "Mietrecht und ähnliche Themen werden hier behandelt.",
    )
    assert (matched, total) == (1, 1), (
        f"Substantive terms preserved through stopword filter; "
        f"got ({matched}, {total})"
    )


def test_high_coverage_rescue_when_gap_collapses(tmp_path):
    """When multiple paragraphs match the OR query similarly (small BM25
    gap) but rank-1 has high coverage AND meaningful absolute strength,
    the rescue branch surfaces it as medium. Empirically observed in
    BGE 145 IV 50 + "Drogentest Anordnung Polizei SVG" — top 3
    sub-paragraphs all scored ~17–20, gap_ratio ≈ 1.04 → without
    rescue, all suppressed; with rescue, rank-1 surfaces at medium.
    """
    f = mcp_server._score_pinpoint_confidence
    # Two paragraphs scoring similarly (gap_ratio ≈ 1.04, below 1.2),
    # rank-1 coverage 75 %, absolute strength > 5.
    out = f(
        [-20.5, -19.8],  # gap_ratio ≈ 1.035
        "Drogentest Anordnung Polizei SVG",
        "Drogentest wurde durch die Polizei angeordnet aufgrund SVG.",
        match_kind="or",
    )
    assert out == "medium", (
        f"High-coverage rescue should surface medium; got {out!r}"
    )


def test_high_coverage_rescue_does_not_fire_below_threshold(tmp_path):
    """The rescue requires three conditions simultaneously:
    coverage ≥ 0.7, total_n ≥ 2, AND abs(score) > 5.0. If any guard
    fails, suppression stays.
    """
    f = mcp_server._score_pinpoint_confidence
    # Score too weak (below 5.0 floor) — even with high coverage, no rescue.
    out = f(
        [-3.0, -2.9],  # gap 1.03, below 1.2
        "Drogentest Anordnung Polizei",
        "Drogentest Anordnung Polizei sind hier behandelt.",
        match_kind="or",
    )
    assert out is None, (
        f"Rescue should not fire below abs-score 5.0 floor; got {out!r}"
    )


def test_handle_find_relevant_erwaegung_uses_or_fallback_for_recall(tmp_path, monkeypatch):
    """Symmetric fallback: the explicit MCP tool now mirrors
    _compute_pinpoint's two-pass behavior. When the FTS5 phrase pass
    returns no matches (typical for Regeste-style claims that don't
    appear verbatim in any single Erwägung), the OR fallback fires
    so legitimate semantic matches surface. Empirical mini-bench
    (5 BGE Regestes via /api/relevant-erwaegung, 2026-05-10) was
    0/5 before this fix.
    """
    conn = _make_structure_db(tmp_path, [
        # Multi-paragraph corpus so BM25 IDF works. Target paragraph has
        # all 3 claim tokens but in different word order than the phrase.
        ("d_1", "1", "Sachverhalt: Beklagte hat am 5. Januar einen Mietvertrag."),
        ("d_1", "2", "Erwägungen über die Sperrfrist nach Mietrecht-Kündigung "
                     "und die Wohnung der Familie."),
        ("d_1", "3", "Schlussfolgerung: Die Beschwerde wird gutgeheissen."),
        ("d_1", "4", "Dispositiv: Aufhebung des angefochtenen Urteils."),
    ])
    monkeypatch.setattr(mcp_server, "_get_structure_conn", lambda: conn)
    monkeypatch.setattr(mcp_server, "_resolve_decision_id", lambda x: x)
    monkeypatch.setattr(mcp_server, "_fetch_structure_row", lambda x: None)
    monkeypatch.setattr(mcp_server, "get_decision_by_id", lambda x: None)
    try:
        # Phrase "Mietrecht Kündigung Wohnung Familie" doesn't match exactly
        # (paragraph has "Mietrecht-Kündigung" hyphenated and "Wohnung der
        # Familie") — pre-fix this returned no_match. Post-fix the OR
        # fallback fires and finds E.2.
        out = mcp_server._handle_find_relevant_erwaegung(
            decision_id="d_1",
            claim="Mietrecht Kündigung Wohnung Familie",
            top_k=2,
        )
        assert out.get("no_match") is False, (
            f"OR fallback should rescue Regeste-style claims; got {out}"
        )
        assert (out.get("matches") or [{}])[0].get("e_number") == "2", (
            f"Expected rank-1 to be E.2, got {out.get('matches')}"
        )
        assert out.get("confidence") in {"high", "medium"}, (
            f"Confidence should be high/medium, got {out.get('confidence')!r}"
        )
    finally:
        conn.close()


def test_handle_find_relevant_erwaegung_does_not_emit_thin_high(tmp_path, monkeypatch):
    """The same false-confidence bug applied to the explicit MCP tool
    when the OR fallback fired (only on FTS5 OperationalError there,
    but the inflated ``gap_ratio = 999.0`` for single-row matches still
    bit). Verify the shared scorer fixes the explicit handler too.
    """
    conn = _make_structure_db(tmp_path, [
        ("d_1", "1", "Eine kurze Bemerkung zur Kündigung im allgemeinen."),
    ])
    monkeypatch.setattr(mcp_server, "_get_structure_conn", lambda: conn)
    monkeypatch.setattr(
        mcp_server,
        "_resolve_decision_id",
        lambda x: x,
    )
    monkeypatch.setattr(
        mcp_server,
        "_fetch_structure_row",
        lambda x: None,
    )
    monkeypatch.setattr(
        mcp_server,
        "get_decision_by_id",
        lambda x: None,
    )
    try:
        out = mcp_server._handle_find_relevant_erwaegung(
            decision_id="d_1",
            claim="Mietrecht Kündigung Wohnung",
            top_k=3,
        )
        # Either no_match or all matches downgraded — but never a "high"
        # confidence pinpoint surfaced for thin lexical overlap.
        if out.get("no_match") is True:
            return
        assert out.get("confidence") != "high", (
            f"Explicit handler still emits high-confidence thin match: {out}"
        )
    finally:
        conn.close()


def _record_statements(conn) -> list[str]:
    statements: list[str] = []
    conn.set_trace_callback(statements.append)
    return statements


def test_a_span_shared_with_another_decision_keeps_paragraphs_apart(tmp_path):
    """A re-extracted decision can straddle another's rowids. The span only
    narrows the search; decision_id still decides whose paragraph it is."""
    conn = _make_structure_db(tmp_path, [
        ("d_1", "1", "Die Kündigung war missbräuchlich."),
        ("d_2", "1", "Die Kündigung war missbräuchlich und sehr missbräuchlich."),
        ("d_1", "2", "Die Sperrfrist lief noch."),
    ])
    try:
        assert mcp_server._paragraph_rowid_span(conn, "d_1") == (1, 3)
        pp = mcp_server._compute_pinpoint("d_1", "Kündigung missbräuchlich", conn=conn)
        assert pp is None or pp["e_number"] == "1"
        rows = conn.execute(
            "SELECT e_number FROM erwaegungen_paragraph_fts CROSS JOIN erwaegungen_paragraph p "
            "ON p.rowid = erwaegungen_paragraph_fts.rowid "
            "WHERE erwaegungen_paragraph_fts MATCH ? "
            "AND erwaegungen_paragraph_fts.rowid BETWEEN ? AND ? AND p.decision_id = ?",
            ("Sperrfrist OR missbräuchlich", 1, 3, "d_1"),
        ).fetchall()
        assert sorted(r["e_number"] for r in rows) == ["1", "2"]
    finally:
        conn.close()


def test_a_decision_without_paragraphs_never_touches_the_index(tmp_path, monkeypatch):
    conn = _make_structure_db(tmp_path, [("d_1", "1", "Die Kündigung war missbräuchlich.")])
    monkeypatch.setattr(mcp_server, "_compute_pinpoint_semantic_rescue", lambda *a, **k: None)
    statements = _record_statements(conn)
    try:
        assert mcp_server._paragraph_rowid_span(conn, "d_none") is None
        assert mcp_server._compute_pinpoint("d_none", "Kündigung", conn=conn) is None
        assert not [s for s in statements if "MATCH" in s]
    finally:
        conn.set_trace_callback(None)
        conn.close()


def test_the_index_stays_the_outer_table(tmp_path, monkeypatch):
    """The other order restarts FTS5 per paragraph and bm25() recounts its
    corpus statistics each time: 5-10x slower on 11M paragraphs, invisibly."""
    conn = _make_structure_db(tmp_path, [
        ("d_1", "1", "Die Kündigung war missbräuchlich."),
        ("d_1", "2", "Die Sperrfrist lief noch."),
    ])
    monkeypatch.setattr(mcp_server, "_get_structure_conn", lambda: conn)
    monkeypatch.setattr(mcp_server, "_resolve_decision_id", lambda x: x)
    monkeypatch.setattr(mcp_server, "_fetch_structure_row", lambda x: None)
    monkeypatch.setattr(mcp_server, "get_decision_by_id", lambda x: None)
    statements = _record_statements(conn)
    mcp_server._compute_pinpoint("d_1", "Kündigung Sperrfrist", conn=conn)
    mcp_server._handle_find_relevant_erwaegung(decision_id="d_1", claim="Kündigung Sperrfrist")
    queries = [s for s in statements if "MATCH" in s]
    assert len(queries) >= 2

    check = sqlite3.connect(tmp_path / "structure.db")
    try:
        for q in queries:
            plan = [r[-1] for r in check.execute("EXPLAIN QUERY PLAN " + q)]
            assert "erwaegungen_paragraph_fts" in plan[0], plan
    finally:
        check.close()
