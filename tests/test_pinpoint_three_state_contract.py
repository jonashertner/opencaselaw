"""External audit P1.1 — /attest certified considerations that do not exist.

A decision's body quotes other decisions constantly, and those quotes carry
pinpoints of their own. `_pinpoint_in_text` accepted an "E. X.Y" marker
anywhere in the full text, so "vgl. BGE 133 III 393 E. 7.1" inside BGE
140 III 86 proved that BGE 140 III 86 had an E. 7.1. attest_response passed
it, `cite` handed back a pinpointed citation_string plus a dead #e-7-1
anchor, and only get_erwaegung — which reads the structure sidecar — said no.

The fix is a three-state contract shared by all three surfaces
(`_verify_pinpoint`), NOT a structure-only strict resolver: structured
considerations cover ~86 % of the corpus, so structure-only would report
"this Erwägung does not exist" on ~14 % of perfectly valid pinpoints. That
accusation is worse than the missing check it replaces.

  verified    — structured heading, or the parent prefix of one; or, with no
                structure at all, the tightened text check found the
                decision's own heading.
  invalid     — the decision HAS structure and the pinpoint is not in it.
                The only state that may be reported as an error.
  unverified  — no structure, no heading. A warning, never an accusation.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import mcp_server as m  # noqa: E402


# ── Fixtures modelled on the reported case ─────────────────────────────────
#
# STRUCTURED: BGE 140 III 86 really does stop at E. 2 / 4.1 / 4.2, and its
# E. 4.1 really does quote "ATF 135 III 410 consid. 3.2". The draft cites a
# 7.1 that exists only inside a quotation of another case.

STRUCTURED_ID = "bge_BGE_140_III_86"
STRUCTURED_PARAS = [
    {"e_number": "2", "parent": None, "depth": 1,
     "text": "Le Tribunal fédéral applique le droit d'office."},
    {"e_number": "4.1", "parent": "4", "depth": 2,
     "text": "Saisi d'un litige sur l'interprétation d'un contrat, le juge "
             "doit rechercher la réelle et commune intention des parties "
             "(ATF 135 III 410 consid. 3.2)."},
    {"e_number": "4.2", "parent": "4", "depth": 2,
     "text": "Subsidiairement, le juge interprète selon la bonne foi."},
]
STRUCTURED_FULLTEXT = (
    "Considérants\n\n"
    "2. Le Tribunal fédéral applique le droit d'office.\n\n"
    "4.1 Saisi d'un litige, le juge recherche la réelle intention des "
    "parties; dazu vgl. BGE 133 III 393 E. 7.1 sowie ATF 135 III 410 "
    "consid. 3.2.\n\n"
    "4.2 Subsidiairement, le juge interprète selon la bonne foi.\n"
)
STRUCTURED_ROW = {
    "decision_id": STRUCTURED_ID,
    "court": "bge",
    "language": "fr",
    "decision_date": "2014-04-15",
    "docket_number": "140 III 86",
    "collection": "bge",
    "bge_reference": "BGE 140 III 86",
    "regeste": "Interprétation du contrat.",
    "full_text": STRUCTURED_FULLTEXT,
}

# STRUCTURE-LESS: a cantonal-shaped record the sidecar never covered. One
# copy carries its own "7.1." heading, one does not.
BARE_ID = "bger_4A_747_2012"
BARE_FULLTEXT_WITH_HEADING = (
    "Erwägungen\n\n"
    "7.1. Das Bundesgericht prüft die Rüge frei.\n\n"
    "7.2. Die Beschwerde erweist sich als unbegründet.\n"
)
BARE_FULLTEXT_WITHOUT_HEADING = (
    "Erwägungen\n\n"
    "Die Beschwerde erweist sich als unbegründet; vgl. BGE 133 III 393 "
    "E. 7.1.\n"
)


def _row(full_text: str) -> dict:
    return {
        "decision_id": BARE_ID,
        "court": "bger",
        "language": "de",
        "decision_date": "2013-04-08",
        "docket_number": "4A_747/2012",
        "collection": None,
        "bge_reference": None,
        "regeste": "",
        "full_text": full_text,
    }


@pytest.fixture
def corpus(monkeypatch):
    """Wire the strict + lenient resolvers and the structure sidecar to the
    two fixture decisions. `structure` selects which rows BARE_ID gets."""
    state = {"bare_full_text": BARE_FULLTEXT_WITH_HEADING}
    rows = {STRUCTURED_ID: STRUCTURED_ROW}

    def _resolve(ref):
        ref = (ref or "").strip()
        if "133 III 393" in ref or "133_III_393" in ref:
            return "bge_BGE_133_III_393"
        if "140 III 86" in ref or "140_III_86" in ref:
            return STRUCTURED_ID
        if "4A_747" in ref:
            return BARE_ID
        return ref

    def _get(did):
        if did == BARE_ID:
            return _row(state["bare_full_text"])
        return rows.get(did)

    def _paras(did):
        return list(STRUCTURED_PARAS) if did == STRUCTURED_ID else []

    monkeypatch.setattr(m, "_resolve_decision_id", _resolve)
    monkeypatch.setattr(m, "_resolve_decision_id_strict",
                        lambda ref: _resolve(ref) if _get(_resolve(ref)) else None)
    monkeypatch.setattr(m, "get_decision_by_id", _get)
    monkeypatch.setattr(m, "_get_decision_strict", _get)
    monkeypatch.setattr(m, "_fetch_structure_paragraphs", _paras)
    monkeypatch.setattr(m, "_fetch_structure_row",
                        lambda did: _get(did) or {})
    # Keep the audit offline and focused on the case-citation rail.
    monkeypatch.setattr(m, "_audit_statutes", lambda *a, **k: [])
    monkeypatch.setattr(m, "_audit_dates", lambda *a, **k: [])
    monkeypatch.setattr(m, "_auto_link_citations", lambda t: t)
    return state


def _case_issues(res):
    return [i for i in res["issues"] if i.get("category") == "case"]


# ── (a) cross-citation inside a structured decision ────────────────────────

def test_cross_citation_does_not_prove_a_pinpoint():
    """The reported mechanism, isolated: the marker belongs to the quoted
    case, so it must not count as this decision's own heading."""
    assert m._pinpoint_in_text(STRUCTURED_FULLTEXT, "7.1") is False
    # …while the decision's own headings still resolve.
    assert m._pinpoint_in_text(STRUCTURED_FULLTEXT, "4.1") is True


def test_attest_flags_cross_citation_pinpoint(corpus):
    res = m._handle_attest_response(
        draft_text="Das Bundesgericht hielt in BGE 140 III 86 E. 7.1 fest, dass ...")
    issues = _case_issues(res)
    assert res["ok"] is False
    assert len(issues) == 1
    assert issues[0]["problem"] == "pinpoint_not_in_decision"
    assert issues[0]["valid_pinpoints"] == ["2", "4.1", "4.2"]
    assert "⚠️[PINPOINT_INVALID]" in res["annotated_text"]


def test_cite_refuses_to_pinpoint_a_nonexistent_consideration(corpus):
    res = m._handle_cite(reference="BGE 140 III 86", pinpoint="7.1")
    assert res["exists"] is True
    assert res["pinpoint_valid"] is False
    assert res["pinpoint_status"] == m.PINPOINT_INVALID
    assert res["valid_pinpoints"] == ["2", "4.1", "4.2"]
    # The pinpoint must survive nowhere in the copy-verbatim channel: not in
    # the citation strings, not as a dead #e-7-1 anchor.
    for key in ("citation_string", "citation_string_de",
                "citation_string_fr", "citation_string_it",
                "canonical_url", "markdown_link"):
        assert "7.1" not in res[key] and "e-7-1" not in res[key], key


def test_erwaegung_reports_a_nonexistent_consideration_as_such(corpus):
    res = m._handle_get_erwaegung(decision_id="BGE 140 III 86", e_number="7.1")
    assert res["error_code"] == "pinpoint_not_found"
    assert m.ERWAEGUNG_ERROR_STATUS[res["error_code"]] == 404
    assert res["available_e_numbers"] == ["2", "4.1", "4.2"]


# ── (b) structure-less decision WITH a genuine heading ─────────────────────

def test_structureless_with_own_heading_is_verified_by_text(corpus):
    corpus["bare_full_text"] = BARE_FULLTEXT_WITH_HEADING
    verdict = m._verify_pinpoint("7.1", paragraphs=[],
                                 full_text=BARE_FULLTEXT_WITH_HEADING)
    assert verdict["status"] == m.PINPOINT_VERIFIED
    assert verdict["method"] == "text"

    res = m._handle_attest_response(draft_text="Vgl. BGer 4A_747/2012 E. 7.1.")
    assert res["ok"] is True
    assert _case_issues(res) == []
    assert res["warnings"] == []
    assert res["citations_ok"] == 1

    cited = m._handle_cite(reference="4A_747/2012", pinpoint="7.1")
    assert cited["pinpoint_valid"] is True
    assert cited["pinpoint_status"] == m.PINPOINT_VERIFIED


# ── (c) structure-less decision WITHOUT the heading ────────────────────────

def test_structureless_without_heading_is_unverified_not_fabricated(corpus):
    corpus["bare_full_text"] = BARE_FULLTEXT_WITHOUT_HEADING
    res = m._handle_attest_response(draft_text="Vgl. BGer 4A_747/2012 E. 7.1.")
    # A warning, not an issue: `ok` stays true and the citation still counts.
    assert res["ok"] is True
    assert _case_issues(res) == []
    assert res["warnings_count"] == 1
    assert res["warnings"][0]["problem"] == "pinpoint_unverified"
    assert "✓?" in res["annotated_text"]
    # The citation keeps its Markdown link — an unverifiable pinpoint must not
    # cost the reader one-click verification.
    assert "](" in res["linked_text"]

    cited = m._handle_cite(reference="4A_747/2012", pinpoint="7.1")
    assert cited["pinpoint_status"] == m.PINPOINT_UNVERIFIED
    assert cited["pinpoint_valid"] is None
    assert "pinpoint_note" in cited

    erw = m._handle_get_erwaegung(decision_id="4A_747/2012", e_number="7.1")
    assert erw["error_code"] == "no_structure"
    assert erw["pinpoint_status"] == m.PINPOINT_UNVERIFIED
    # Never phrased as an absence finding.
    assert "not found" not in erw["error"]


# ── (d) valid pinpoints behave exactly as before ───────────────────────────

def test_exact_structured_pinpoint_still_passes(corpus):
    res = m._handle_attest_response(draft_text="BGE 140 III 86 E. 4.1 ist einschlägig.")
    assert res["ok"] is True
    assert res["citations_ok"] == 1
    assert res["warnings"] == []
    assert "✓" in res["annotated_text"] and "✓?" not in res["annotated_text"]

    cited = m._handle_cite(reference="BGE 140 III 86", pinpoint="4.1")
    assert cited["pinpoint_valid"] is True
    assert cited["citation_string_de"].endswith("E. 4.1")
    assert cited["canonical_url"].endswith("#e-4-1")

    erw = m._handle_get_erwaegung(decision_id="BGE 140 III 86", e_number="4.1")
    assert "error" not in erw
    assert erw["e_number"] == "4.1"


def test_parent_prefix_counts_as_verified(corpus):
    """The official Regeste cites at parent level and get_erwaegung composes
    those parents from their leaves, so E. 4 must be valid when 4.1/4.2 are
    stored. E. 2 must not swallow a hypothetical 21.x — the trailing dot."""
    verdict = m._verify_pinpoint("4", paragraphs=STRUCTURED_PARAS)
    assert verdict["status"] == m.PINPOINT_VERIFIED
    assert verdict["method"] == "structure_parent"

    res = m._handle_attest_response(draft_text="BGE 140 III 86 E. 4 sagt es.")
    assert res["ok"] is True
    cited = m._handle_cite(reference="BGE 140 III 86", pinpoint="4")
    assert cited["pinpoint_valid"] is True

    near_miss = m._verify_pinpoint("21", paragraphs=[
        {"e_number": "21.1", "parent": "21", "depth": 2, "text": "x"}])
    assert near_miss["status"] == m.PINPOINT_VERIFIED
    assert m._verify_pinpoint("2", paragraphs=[
        {"e_number": "21.1", "parent": "21", "depth": 2, "text": "x"},
    ])["status"] == m.PINPOINT_INVALID


def test_citation_without_pinpoint_is_untouched(corpus):
    res = m._handle_attest_response(draft_text="Siehe BGE 140 III 86.")
    assert res["ok"] is True
    assert res["citations_ok"] == 1
    cited = m._handle_cite(reference="BGE 140 III 86")
    assert "pinpoint_valid" not in cited
    assert "#e-" not in cited["canonical_url"]


# ── Accepted / rejected text forms, pinned ─────────────────────────────────

@pytest.mark.parametrize("body,pinpoint", [
    # Historical heading forms the fallback has always accepted.
    ("Erwägungen: ... E. 2.3. Wie das BGer hielt fest", "2.3"),
    ("siehe consid. 4.1 hierzu", "4.1"),
    ("siehe Erw. 5.2 hierzu", "5.2"),
    ("(E. 6) ist klar", "6"),
    ("Wie in E. 3.2 hiervor dargelegt", "3.2"),
    # Numbered headings at line start, with and without the printed dot.
    ("Erwägungen\n\n7.1. Das Bundesgericht prüft von Amtes wegen.\n", "7.1"),
    ("Erwägungen\n\n7.1 Das Bundesgericht prüft von Amtes wegen.\n", "7.1"),
    ("2. Le Tribunal fédéral examine d'office.\n", "2"),
    # A cross-citation elsewhere must not suppress a genuine own heading.
    ("vgl. BGE 133 III 393 E. 7.1\n\n7.1 Eigene Erwägung hier.\n", "7.1"),
])
def test_pinpoint_text_forms_accepted(body, pinpoint):
    assert m._pinpoint_in_text(body, pinpoint) is True


@pytest.mark.parametrize("body,pinpoint", [
    ("nichts Erwägendes hier", "2.3"),
    ("Erwägung 99.99 wird nicht erwähnt", "99.99"),
    # Every shape of "the marker belongs to another case".
    ("Dazu vgl. BGE 133 III 393 E. 7.1 sowie weiteres.", "7.1"),
    ("vgl. ATF 135 III 410 consid. 3.2", "3.2"),
    ("wie in 133 III 393 E. 7.1 entschieden", "7.1"),
    ("Urteil des Bundesgerichts 1B_243/2022 vom 3. Mai 2022 E. 2.4", "2.4"),
    ("Urteil 1B_243/2022 E. 3.1", "3.1"),
    ("BGer 9C 850/2009 E. 4.2", "4.2"),
    ("vgl. 5C.123/2003 E. 1.2", "1.2"),
    ("BVGer A-1234/2015 E. 6.1", "6.1"),
    ("ECLI:CH:BGER:2014:4A_1.2014 E. 5.5", "5.5"),
    ("Urteil vom 03.04.2024 E. 3.3", "3.3"),
    ("arrêt du 23 septembre 2014 consid. 5.1", "5.1"),
])
def test_pinpoint_text_forms_rejected(body, pinpoint):
    assert m._pinpoint_in_text(body, pinpoint) is False


# ── /api/erwaegung must answer 4xx, not 200-with-an-error-body ─────────────
#
# The REST app is nested inside main_remote() and cannot be cheaply
# instantiated, so — following tests/test_pro_redaction_guard.py — the wiring
# is asserted against the source and the behaviour against a mirror app that
# runs the real handler through the real status map.

def test_error_status_map_is_4xx_only():
    assert set(m.ERWAEGUNG_ERROR_STATUS.values()) <= {400, 404}
    assert m.ERWAEGUNG_ERROR_STATUS["invalid_request"] == 400
    for code in ("decision_not_found", "no_structure", "pinpoint_not_found"):
        assert m.ERWAEGUNG_ERROR_STATUS[code] == 404


def test_rest_route_is_wired_to_the_status_map():
    import ast
    src = (REPO / "mcp_server.py").read_text(encoding="utf-8")
    body = next(
        (ast.get_source_segment(src, n) or "")
        for n in ast.walk(ast.parse(src))
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.name == "api_get_erwaegung"
    )
    assert "ERWAEGUNG_ERROR_STATUS" in body
    assert "JSONResponse" in body


@pytest.mark.parametrize("decision_id,e_number,expected", [
    ("BGE 140 III 86", "4.1", 200),
    ("BGE 140 III 86", "7.1", 404),      # structured, consideration absent
    ("4A_747/2012", "7.1", 404),         # no structured Erwägungen
    ("BGE 999 IX 999", "1", 404),        # decision unknown
    ("BGE 140 III 86", "", 400),         # malformed
])
def test_rest_status_codes(corpus, decision_id, e_number, expected):
    from fastapi import FastAPI
    from starlette.responses import JSONResponse
    from starlette.testclient import TestClient

    corpus["bare_full_text"] = BARE_FULLTEXT_WITHOUT_HEADING
    app = FastAPI()

    @app.get("/api/erwaegung/{decision_id}/{e_number:path}")
    def _route(decision_id: str, e_number: str = ""):
        result = m._handle_get_erwaegung(decision_id=decision_id,
                                         e_number=e_number)
        if isinstance(result, dict) and result.get("error"):
            return JSONResponse(result, status_code=m.ERWAEGUNG_ERROR_STATUS.get(
                result.get("error_code"), 404))
        return result

    resp = TestClient(app).get(f"/api/erwaegung/{decision_id}/{e_number}")
    assert resp.status_code == expected
    if expected != 200:
        # Body shape is unchanged — existing MCP-side readers keep working.
        assert "error" in resp.json()


# ── The documented example must actually resolve ───────────────────────────

def test_no_surface_still_advertises_the_invalid_example():
    """BGE 140 III 86 has E. 2 / 4.1 / 4.2 and never had an E. 2.3. The
    instructions and the Standards page both taught it as the worked
    example, so an agent following the docs produced a dead pinpoint."""
    prompt = m.server.instructions
    assert "BGE 140 III 86 E. 2.3" not in prompt
    assert 'reference="BGE 140 III 86", pinpoint="2.3"' not in prompt
    # Two fixes landed for the same bug on 2026-09-05: this branch moved the
    # example to BGE 140 III 86 E. 4.1, commit 5ecead69 (passage fallback)
    # moved it to BGE 136 III 513 E. 2.3 and verifies every description
    # example live (scripts/check_description_examples.py). Either resolves.
    assert ("BGE 140 III 86 E. 4.1" in prompt) or ("BGE 136 III 513 E. 2.3" in prompt)

    standards = (REPO / "docs" / "standards" / "index.html").read_text(encoding="utf-8")
    assert "bge_BGE_140_III_86#e-4-1" in standards
    # The page only ever illustrated pinpoints on this one decision, so any
    # surviving "2.3" is the invalid example coming back.
    assert "2.3" not in standards
    assert "e-2-3" not in standards
