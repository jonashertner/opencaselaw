"""Tests for informative-term selection in _extract_query_terms.

The defect: truncation was strictly positional (first 16 tokens). A pasted
letter's salutation consumed the budget — 'Sehr geehrte Damen und Herren'
burned 4 of 16 slots and the legal content at char 200+ was never searched.

Now: when base tokens exceed `limit`, selection is informativeness-ranked
(_rank_query_tokens) and re-emitted in first-occurrence order. When they fit,
the original loop runs verbatim — asserted here against a reference
implementation of the OLD algorithm over a battery of short queries.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import mcp_server as m  # noqa: E402


def _reference_old_algorithm(query, *, limit, include_variants, include_expansions):
    """The pre-change positional algorithm, verbatim."""
    keep, seen = [], set()
    for tok in re.findall(r"[0-9A-Za-zÀ-ÖØ-öø-ÿ_]+", query.lower()):
        if tok in m.NL_STOPWORDS:
            continue
        normalized = m._normalize_token_for_fts(tok)
        if not normalized:
            continue
        if not normalized.isdigit() and len(normalized) < 3:
            continue
        variants = [normalized]
        if include_variants:
            alt = m._collapse_umlaut_variants(normalized)
            if alt and alt != normalized:
                variants.append(alt)
        if include_expansions:
            for expansion in m._get_query_expansions(normalized):
                if expansion and expansion not in variants:
                    variants.append(expansion)
        if include_variants:
            for part in m._decompose_compound(normalized):
                if part not in variants:
                    variants.append(part)
        for term in variants:
            if term in seen:
                continue
            keep.append(term)
            seen.add(term)
            if len(keep) >= limit:
                return keep
    return keep


SHORT_QUERIES = [
    "Mietrecht Kündigung",
    "missbräuchliche Kündigung Art. 336 OR",
    "asile renvoi Dublin",
    "responsabilité civile art. 41 CO",
    "contratto di locazione disdetta",
    "Werkeigentümerhaftung Strassenzustand",
    "Verjährung Forderung 10 Jahre",
    "BGE 140 III 86 Beschwerdebefugnis",
    "wann verjähren Forderungen aus Vertrag",
    "Chef hat mich gemobbt Entschädigung",
]


def test_short_queries_byte_identical_to_old_algorithm():
    """The ≤limit path must be byte-identical — this is the golden-set safety
    property. All-flag combinations, ten realistic queries."""
    for q in SHORT_QUERIES:
        for iv in (True, False):
            for ie in (True, False):
                new = m._extract_query_terms(q, limit=16, include_variants=iv,
                                             include_expansions=ie)
                old = _reference_old_algorithm(q, limit=16, include_variants=iv,
                                               include_expansions=ie)
                assert new == old, (q, iv, ie, new, old)


LETTER = (
    "Synthetische Testeingabe. Sehr geehrte Damen und Herren, diese fiktive "
    "Korrespondenz betrifft ein Arbeitsverhältnis und eine fristlose "
    "Kündigung. Die Testpartei hält die Kündigungsgründe für missbräuchlich "
    "im Sinne von Art. 336 OR und bittet um Stellungnahme. Mit freundlichen "
    "Grüssen"
)


def test_letter_salutation_and_closing_do_not_consume_slots():
    terms = m._extract_query_terms(LETTER, limit=16, include_variants=False,
                                   include_expansions=False)
    for boilerplate in ("sehr", "geehrte", "damen", "herren", "freundlichen"):
        assert boilerplate not in terms
    # the legal content made it in
    assert any("kundig" in t or "kündig" in t for t in terms), terms
    assert "missbrauchlich" in terms or "missbräuchlich" in terms, terms


def test_informative_term_beyond_position_limit_survives():
    """18 non-stopword filler tokens, then the legal payload. Positional
    truncation dropped the payload; ranked selection must keep it."""
    filler = " ".join(f"wort{i:02d}" for i in range(18))
    q = filler + " fristlose Kündigung Art. 337 OR"
    terms = m._extract_query_terms(q, limit=16, include_variants=False,
                                   include_expansions=False)
    assert "337" in terms, terms
    assert any("kundig" in t or "kündig" in t for t in terms), terms


def test_statute_digits_ranked_above_filler():
    scores = m._rank_query_tokens(["wort01", "336"], "wort01 Art. 336 OR")
    assert scores[1] > scores[0]


def test_fr_letter():
    q = ("Madame, Monsieur, nous vous écrivons au sujet de la résiliation du "
         "bail commercial de notre cliente, fondée sur l'art. 266g CO, que "
         "nous tenons pour injustifiée. Veuillez agréer nos salutations "
         "distinguées")
    terms = m._extract_query_terms(q, limit=16, include_variants=False,
                                   include_expansions=False)
    assert "madame" not in terms and "monsieur" not in terms
    assert "veuillez" not in terms
    assert any("resiliation" in t or "résiliation" in t for t in terms), terms


def test_it_letter():
    q = ("Egregi Signori, in merito alla disdetta del contratto di lavoro "
         "della nostra cliente ai sensi dell'art. 337 CO, riteniamo la "
         "disdetta abusiva. Distinti saluti")
    terms = m._extract_query_terms(q, limit=16, include_variants=False,
                                   include_expansions=False)
    assert "egregi" not in terms and "saluti" not in terms
    assert "disdetta" in terms, terms


def test_ranked_selection_preserves_first_occurrence_order():
    filler = " ".join(f"wort{i:02d}" for i in range(20))
    q = "Werkeigentümerhaftung " + filler + " Verjährung Art. 60 OR"
    terms = m._extract_query_terms(q, limit=8, include_variants=False,
                                   include_expansions=False)
    # the three informative terms all survive the cut...
    assert "werkeigentumerhaftung" in terms
    assert "verjahrung" in terms
    assert "60" in terms
    # ...and appear in original text order: compound first, statute parts last
    assert terms[0] == "werkeigentumerhaftung"
    assert terms.index("verjahrung") < terms.index("60")
    # surviving filler keeps its relative order too
    filler_kept = [t for t in terms if t.startswith("wort")]
    assert filler_kept == sorted(filler_kept)


def test_letter_boilerplate_set_disjoint_from_nl_stopwords():
    overlap = m.LETTER_BOILERPLATE_STOPWORDS & m.NL_STOPWORDS
    assert not overlap, overlap
