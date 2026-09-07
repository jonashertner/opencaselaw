"""Critical invariant #3: every FTS5 query passes _sanitize_fts5, and its output
must be executable as an FTS5 MATCH without raising. Golden cases = the documented
historical production failures + the bread-and-butter Swiss legal query shapes that
search_botschaft / find_leading_cases now route through the sanitizer.

Until now the sanitizer (a 22k-line-file hotspot) had ZERO tests, so any refactor
could silently re-introduce the exact crashes it exists to prevent.
"""
import sqlite3

import pytest

import mcp_server


# Inputs that historically raised fts5 syntax errors when passed raw.
DOCUMENTED = [
    "Art. 64 StGB Verwahrung",            # trailing dot after Art.
    "öffentlich-rechtliche Körperschaft",  # hyphen -> "no such column" error
    "l'abus de droit",                    # French apostrophe
    'unbalanced " quote',                 # unterminated string
    "Obligationenrecht OR",               # OR as abbreviation, not operator
    "trailing OR",                        # bare trailing operator
    "Rechtsmissbrauch Art. 2 ZGB",
    "",                                    # empty
    '""',                                  # empty quoted phrase
    "NEAR(",                               # bare operator fragment
    "a AND b NOT c",                       # operator tokens mid-query
]


@pytest.fixture(scope="module")
def fts():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE VIRTUAL TABLE t USING fts5(body)")
    conn.execute(
        "INSERT INTO t(body) VALUES "
        "('Art 64 StGB Verwahrung öffentlich rechtliche OR Obligationenrecht abus droit Rechtsmissbrauch ZGB')"
    )
    conn.commit()
    yield conn
    conn.close()


@pytest.mark.parametrize("raw", DOCUMENTED)
def test_sanitized_query_is_fts5_executable(fts, raw):
    """The sanitizer's output must never raise an FTS5 syntax error. (Empty is
    allowed — callers guard on it; any non-empty output must be MATCH-safe.)"""
    safe = mcp_server._sanitize_fts5(raw)
    if safe:
        fts.execute("SELECT 1 FROM t WHERE t MATCH ? LIMIT 1", (safe,)).fetchall()


def test_sanitizer_is_load_bearing(fts):
    """Prove the raw inputs really do raise — otherwise the sanitizer (and the
    search_botschaft / find_leading_cases fixes) would be pointless."""
    raised = 0
    for raw in ["Art. 64 StGB", "öffentlich-rechtliche", "l'abus", 'x " y']:
        try:
            fts.execute("SELECT 1 FROM t WHERE t MATCH ? LIMIT 1", (raw,)).fetchall()
        except sqlite3.OperationalError:
            raised += 1
    assert raised >= 3, "raw inputs did not raise — sanitizer is not load-bearing"


def test_trailing_dot_stripped():
    assert mcp_server._sanitize_fts5("Art. 64 StGB Verwahrung") == "Art 64 StGB Verwahrung"


def test_hyphen_neutralized():
    assert "-" not in mcp_server._sanitize_fts5("öffentlich-rechtliche Körperschaft")


def test_bare_operator_tokens_quoted():
    assert mcp_server._sanitize_fts5("Obligationenrecht OR") == 'Obligationenrecht "OR"'
    assert mcp_server._sanitize_fts5("trailing OR") == 'trailing "OR"'


def test_empty_quoted_phrase_collapses():
    assert mcp_server._sanitize_fts5('""').strip() == ""


# ── Explicit-syntax detection on the ORIGINAL query (integration evaluation 2026-07) ──
# _has_explicit_fts_syntax was evaluated on _sanitize_fts5 output. The
# sanitizer strips the dot from 'Art.' and quotes bare 'OR' as '"OR"', which
# defeated the statute mask twice AND tripped the quote-count branch — so
# every query citing '… OR' was misclassified as operator syntax, which
# disabled the vector/semantic rescue for exactly the archetypal lawyer query.

class TestExplicitSyntaxOnOriginal:
    def test_statute_citation_is_not_operator_syntax(self):
        assert mcp_server._has_explicit_fts_syntax("Kündigung Art. 335 OR") is False

    def test_sanitized_form_documents_the_old_trap(self):
        # what the sanitizer produces for the query above — the old call order
        # fed THIS to the detector and got True
        assert mcp_server._has_explicit_fts_syntax('Kündigung Art 335 "OR"') is True

    def test_bare_trailing_or_is_the_abbreviation(self):
        # documented sanitizer input: 'Obligationenrecht OR' — no right operand
        assert mcp_server._has_explicit_fts_syntax("Obligationenrecht OR") is False

    def test_infix_or_with_operands_is_syntax(self):
        assert mcp_server._has_explicit_fts_syntax("Miete OR Pacht") is True

    def test_lowercase_or_is_not_syntax(self):
        # FTS5 operators are uppercase-only; 'or' mid-sentence is English filler
        assert mcp_server._has_explicit_fts_syntax("landlord or tenant duties") is False

    def test_quoted_phrase_still_detected(self):
        assert mcp_server._has_explicit_fts_syntax('"Treu und Glauben"') is True

    def test_and_not_near_still_loose(self):
        assert mcp_server._has_explicit_fts_syntax("Arbeitsrecht AND Kündigung") is True
        assert mcp_server._has_explicit_fts_syntax("Miete NOT Pacht") is True
