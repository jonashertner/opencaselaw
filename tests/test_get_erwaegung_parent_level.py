"""GitHub #86: the official Regeste cites Erwägungen at parent level ("Begriff
des Ordre public im Sinne von Art. 190 Abs. 2 lit. e IPRG (E. 2)"), but only leaf
paragraphs are stored, so get_erwaegung(e_number="2") errored with
available_e_numbers=[2.1, 2.2.1, ...]. get_regeste's own note tells callers the
Regeste is the way to locate Erwägungen, so the tool contradicted its own
documentation.

A parent request is now composed from its descendants. `text` is a plain
concatenation with nothing injected, so it stays verbatim and quotable; the
sub-paragraphs stay individually addressable under `parts` for callers that want
to pinpoint one of them.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import mcp_server as m  # noqa: E402

# BGE 132 III 389 as reported: E. 2 and E. 3 exist only as leaves.
PARAS = [
    {"e_number": "2.1", "parent": "2", "depth": 2, "text": "Ordre public erste Stufe."},
    {"e_number": "2.2.1", "parent": "2.2", "depth": 3, "text": "Zweite Stufe A."},
    {"e_number": "2.2.2", "parent": "2.2", "depth": 3, "text": "Zweite Stufe B."},
    {"e_number": "2.3", "parent": "2", "depth": 2, "text": "Dritte Stufe."},
    {"e_number": "3.1", "parent": "3", "depth": 2, "text": "Anderes Thema."},
    # A neighbour that must never be swallowed by a request for E. 2.
    {"e_number": "21.1", "parent": "21", "depth": 2, "text": "Weit spaeter."},
]


def _patch(monkeypatch):
    monkeypatch.setattr(m, "_resolve_decision_id", lambda x: "bge_BGE_132_III_389")
    monkeypatch.setattr(m, "_fetch_structure_paragraphs", lambda _id: PARAS)
    monkeypatch.setattr(m, "_fetch_structure_row", lambda _id: {
        "court": "bge", "language": "de", "decision_date": "2006-05-08",
        "regeste": "Begriff des Ordre public (E. 2).",
    })
    monkeypatch.setattr(m, "get_decision_by_id", lambda _id: {
        "decision_id": "bge_BGE_132_III_389", "docket_number": "132 III 389",
        "collection": "bge", "bge_reference": "BGE 132 III 389",
    })


def test_parent_level_request_is_composed(monkeypatch):
    _patch(monkeypatch)
    r = m._handle_get_erwaegung(decision_id="BGE 132 III 389", e_number="2")
    assert "error" not in r, r
    assert r["composed_of"] == ["2.1", "2.2.1", "2.2.2", "2.3"]
    # Children in document order, verbatim, nothing injected between them.
    assert r["text"] == ("Ordre public erste Stufe.\n\n"
                         "Zweite Stufe A.\n\n"
                         "Zweite Stufe B.\n\n"
                         "Dritte Stufe.")


def test_composed_parent_does_not_swallow_a_higher_number(monkeypatch):
    # "21.1" must not be treated as a child of "2"; the trailing dot is the
    # only thing standing between E. 2 and a citation to a different Erwägung.
    _patch(monkeypatch)
    r = m._handle_get_erwaegung(decision_id="BGE 132 III 389", e_number="2")
    assert "21.1" not in r["composed_of"]
    assert "Weit spaeter" not in r["text"]


def test_parts_stay_addressable(monkeypatch):
    _patch(monkeypatch)
    parts = m._handle_get_erwaegung(decision_id="BGE 132 III 389", e_number="2")["parts"]
    assert [p["e_number"] for p in parts] == ["2.1", "2.2.1", "2.2.2", "2.3"]
    assert parts[1]["text"] == "Zweite Stufe A."


def test_citation_pinpoints_the_parent(monkeypatch):
    # The Regeste cites "(E. 2)", so that is what the caller should cite.
    _patch(monkeypatch)
    r = m._handle_get_erwaegung(decision_id="BGE 132 III 389", e_number="2")
    assert r["e_number"] == "2"
    assert "E. 2" in r["citation_string_de"]
    assert r["_composed_note"]


def test_leaf_request_is_untouched(monkeypatch):
    _patch(monkeypatch)
    r = m._handle_get_erwaegung(decision_id="BGE 132 III 389", e_number="2.1")
    assert r["text"] == "Ordre public erste Stufe."
    assert "composed_of" not in r
    assert r["siblings"] == ["2.1", "2.3"]


def test_genuinely_absent_number_still_errors(monkeypatch):
    # Composition must not invent an Erwägung that has no descendants either.
    _patch(monkeypatch)
    r = m._handle_get_erwaegung(decision_id="BGE 132 III 389", e_number="9")
    assert "error" in r
    assert "2.1" in r["available_e_numbers"]


# Report 2026-10-13, BGE 138 III 67: E. 2.2 is stored as its own introductory
# paragraph AND has stored sub-Erwägungen. A request for E. 2.2 returned only the
# introduction, with no hint that 2.2.1 / 2.2.2 carry the reasoning.
PARAS_WITH_INTRO = [
    {"e_number": "2.2", "parent": "2", "depth": 2, "text": "Einleitung."},
    {"e_number": "2.2.1", "parent": "2.2", "depth": 3, "text": "Begruendung A."},
    {"e_number": "2.2.2", "parent": "2.2", "depth": 3, "text": "Begruendung B."},
    {"e_number": "2.3", "parent": "2", "depth": 2, "text": "Naechste Erwaegung."},
    {"e_number": "2.21", "parent": "2", "depth": 2, "text": "Nicht dazu."},
]


def _patch_intro(monkeypatch):
    _patch(monkeypatch)
    monkeypatch.setattr(m, "_fetch_structure_paragraphs", lambda _id: PARAS_WITH_INTRO)


def test_parent_with_intro_serves_its_subtree(monkeypatch):
    _patch_intro(monkeypatch)
    r = m._handle_get_erwaegung(decision_id="BGE 138 III 67", e_number="2.2")
    assert "error" not in r, r
    assert r["e_number"] == "2.2"
    assert r["composed_of"] == ["2.2", "2.2.1", "2.2.2"]
    assert r["text"] == "Einleitung.\n\nBegruendung A.\n\nBegruendung B."
    assert [p["e_number"] for p in r["parts"]] == ["2.2", "2.2.1", "2.2.2"]
    assert "sub-Erwägungen" in r["_composed_note"]
    assert "E. 2.2" in r["citation_string_de"]


def test_parent_with_intro_keeps_real_siblings(monkeypatch):
    # Siblings come from the stored parent ("2"), not from every number at the
    # same depth, and a trailing-dot prefix keeps 2.21 out of the subtree.
    _patch_intro(monkeypatch)
    r = m._handle_get_erwaegung(decision_id="BGE 138 III 67", e_number="2.2")
    assert r["siblings"] == ["2.2", "2.3", "2.21"]
    assert "Nicht dazu" not in r["text"]


def test_leaf_under_intro_parent_is_untouched(monkeypatch):
    _patch_intro(monkeypatch)
    r = m._handle_get_erwaegung(decision_id="BGE 138 III 67", e_number="2.2.1")
    assert r["text"] == "Begruendung A."
    assert "composed_of" not in r
