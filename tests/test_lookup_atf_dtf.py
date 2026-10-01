"""/api/lookup must resolve ATF and DTF — the official FR/IT citation forms.

Found 2026-08-23 while verifying the /search/ capabilities panel before
publishing it: the panel's French text promised "ATF 140 III 86" lookup,
and the live probe returned is_case_number=false with zero results — the
docket gate only knew the German prefix. A francophone user typing their
own language's official citation form got told it isn't a case number.
GitHub #43's REST slice.

Offline: the exact match and search_fts5 are stubbed to capture what the
lookup actually asks the index for.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import mcp_server as m  # noqa: E402


@pytest.fixture
def captured(monkeypatch):
    calls: list[str] = []

    def fake_search(query, limit=8, **kw):
        calls.append(query)
        return ([{"decision_id": "bge_BGE_140_III_86",
                  "docket_number": "BGE 140 III 86", "court": "bge",
                  "canton": "CH", "decision_date": "2014-01-23",
                  "title": None}], 1)

    def fake_exact(qn, limit=25):
        # The lookup asks the exact match first; it must get the normalised
        # form too. Empty here, so the stubbed search answers.
        calls.append(qn)
        return {"total": 0, "results": []}

    monkeypatch.setattr(m, "search_fts5", fake_search)
    monkeypatch.setattr(m, "_lookup_exact", fake_exact)
    return calls


@pytest.mark.parametrize("form", ["ATF 140 III 86", "DTF 140 III 86",
                                  "atf 140 III 86", "BGE 140 III 86"])
def test_all_three_language_forms_resolve(captured, form):
    out = m._lookup_case_number(form)
    assert out["is_case_number"] is True
    assert out["results"], form
    assert len(captured) == 2                       # exact match, then search
    assert all(q.startswith("BGE ") for q in captured)   # normalised for both


def test_atf_inside_text_is_not_mangled(captured):
    """Only a leading collection prefix is normalised — a topical query
    mentioning ATF stays a topical query (and correctly not a docket)."""
    out = m._lookup_case_number("la jurisprudence ATF récente")
    assert out["is_case_number"] is False


def test_atf_without_a_number_is_not_a_docket(captured):
    assert m._lookup_case_number("ATF")["is_case_number"] is False
