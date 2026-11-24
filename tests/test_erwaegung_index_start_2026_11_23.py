"""Report 2026-11-23 (Mnemosyne): BGer 5A_297/2012 had E. 1.1 to 3.1 missing
from the structure index, and cite called E. 2.3 nonexistent.

Two faults. The extractor took the reasoning to start at the first opener in
rank order, and "hat erwogen" in running text ("3.1 Das Obergericht hat
erwogen, gemäss ...") outranks the "Erwägungen:" heading line, so everything
before E. 3.1 was cut off. On the serving side the text fallback found the
"2.3" heading but the range rule dropped it (the index began at E. 3), and
cite, attest and get_erwaegung then reported an absence the text contradicts.

Fixtures are cut from the served text of BGer 5A_297/2012. Offline.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "search_stack")]

import pytest  # noqa: E402

from search_stack.extract_decision_structure import extract  # noqa: E402

TEXT = (
    "Urteil vom 10. Juli 2012\nII. zivilrechtliche Abteilung\n"
    "Sachverhalt:\nA.\nIn der Betreibung Nr. xxxx wurde der Konkurs eröffnet.\n"
    "B.\nDas Bundesgericht hat die Vorakten, hingegen keine Vernehmlassungen eingeholt.\n"
    "Erwägungen:\n1.\n"
    "1.1 Gegen den Endentscheid über die Konkurseröffnung ist die Beschwerde in Zivilsachen zulässig.\n"
    "1.2 Die Beschwerde ist zu begründen (\nArt. 42 Abs. 2 BGG\n).\n"
    "2.\n2.1 Nach\nArt. 174 Abs. 2 SchKG\nkann die Rechtsmittelinstanz die Konkurseröffnung aufheben.\n"
    "2.3 Glaubhaft gemacht ist eine Tatsache dann, wenn für deren Vorhandensein gewisse Elemente sprechen.\n"
    "3.\n3.1 Das Obergericht hat erwogen, gemäss der vom Liquidator eingereichten Aufstellung bestünden Schulden.\n"
    "3.2 Die Beschwerdeführerin wendet ein, sie werde ihre Verbindlichkeiten erfüllen können.\n"
    "4.\n4.1 Die Zahlungsfähigkeit ist nicht glaubhaft gemacht (vgl. E. 2.3 oben).\n"
    "Demnach erkennt das Bundesgericht:\n1.\nDie Beschwerde wird abgewiesen.\n"
)


def test_a_heading_line_before_a_quoted_opener_opens_the_reasoning():
    s = extract(TEXT, "de", "bger_5A_297_2012")
    assert s.erwaegungen_method == "ranked_de_header"
    nums = [p["e_number"] for p in s.erwaegungen_paragraphs]
    assert nums[:4] == ["1.1", "1.2", "2.1", "2.3"]
    assert "3.1" in nums and "4.1" in nums
    by = {p["e_number"]: p["text"] for p in s.erwaegungen_paragraphs}
    assert by["2.3"].startswith("Glaubhaft gemacht ist eine Tatsache")
    # The facts end where the reasoning starts, not at E. 3.1.
    assert s.sachverhalt.endswith("keine Vernehmlassungen eingeholt.")


def test_an_old_volume_without_a_heading_still_opens_at_hat_erwogen():
    text = ("Sachverhalt:\nA. Der Kläger verlangte die Ausschlagung.\n"
            "Das Bundesgericht hat in Erwägung gezogen:\n"
            "1. Die Berufung ist zulässig.\n2. Mischt sich ein Erbe ein, so gilt er als annehmender Erbe.\n"
            "3. Die Berufung ist abzuweisen.\n"
            "Demnach erkennt das Bundesgericht:\nDie Berufung wird abgewiesen.\n")
    s = extract(text, "de", "bge_70_II_199")
    assert s.erwaegungen_method == "ranked_de_erwaegung_gezogen"
    assert [p["e_number"] for p in s.erwaegungen_paragraphs] == ["1", "2", "3"]


def test_a_heading_after_the_opener_does_not_move_it():
    # The heading rule looks only before the prose-prone match.
    text = ("Sachverhalt:\nA. Sachverhalt.\n"
            "in Erwägung, dass\n1. die Beschwerde fristgerecht erhoben wurde;\n"
            "2. sie sich als unbegründet erweist;\n3. die Kosten der Beschwerdeführerin aufzuerlegen sind;\n"
            "Erwägung:\nZitat einer Überschrift im Anhang.\n")
    s = extract(text, "de", "zh_x")
    assert s.erwaegungen_method == "ranked_de_in_erwaegung"


# ── Serving side ───────────────────────────────────────────────────────────

import mcp_server as m  # noqa: E402

DID = "bger_5A_297_2012"
# The index as it stood on 2026-11-23: it began at E. 3.2.
PARAS = [
    {"e_number": "3.2", "parent": "3", "depth": 2,
     "text": "Die Beschwerdeführerin wendet ein, sie werde ihre Verbindlichkeiten erfüllen können."},
    {"e_number": "4.1", "parent": "4", "depth": 2,
     "text": "Die Zahlungsfähigkeit ist nicht glaubhaft gemacht (vgl. E. 2.3 oben)."},
]
ROW = {"decision_id": DID, "court": "bger", "language": "de", "decision_date": "2012-07-10",
       "docket_number": "5A_297/2012", "collection": None, "bge_reference": None,
       "regeste": "", "full_text": TEXT}


@pytest.fixture
def corpus(monkeypatch):
    def _resolve(ref):
        return DID if "5A_297" in (ref or "") else ref

    def _get(did):
        return ROW if did == DID else None

    monkeypatch.setattr(m, "_resolve_decision_id", _resolve)
    monkeypatch.setattr(m, "_resolve_decision_id_strict", lambda ref: _resolve(ref) if _get(_resolve(ref)) else None)
    monkeypatch.setattr(m, "get_decision_by_id", _get)
    monkeypatch.setattr(m, "_get_decision_strict", _get)
    monkeypatch.setattr(m, "_fetch_structure_paragraphs", lambda did: list(PARAS) if did == DID else [])
    monkeypatch.setattr(m, "_fetch_structure_row", lambda did: _get(did) or {})
    monkeypatch.setattr(m, "_audit_statutes", lambda *a, **k: [])
    monkeypatch.setattr(m, "_audit_dates", lambda *a, **k: [])
    monkeypatch.setattr(m, "_auto_link_citations", lambda t: t)
    # cite looks up other rulings under a bare docket; there are none here.
    monkeypatch.setattr(m, "get_db", lambda: type("C", (), {"close": lambda self: None})())
    monkeypatch.setattr(m, "_docket_homonyms", lambda *a, **k: [])


def test_a_heading_outside_the_indexed_numbering_is_unverified_not_invalid():
    verdict = m._verify_pinpoint("2.3", paragraphs=PARAS, full_text=TEXT)
    assert verdict["status"] == m.PINPOINT_UNVERIFIED
    assert verdict["method"] == "full_text_heading_outside_index"
    # No heading at all is still an absence finding.
    verdict = m._verify_pinpoint("2.7", paragraphs=PARAS, full_text=TEXT)
    assert verdict["status"] == m.PINPOINT_INVALID


def test_cite_no_longer_calls_the_erwaegung_nonexistent(corpus):
    res = m._handle_cite(reference="5A_297/2012", pinpoint="2.3")
    assert res["pinpoint_status"] == m.PINPOINT_UNVERIFIED
    assert res["pinpoint_valid"] is None
    assert "does not exist" not in res["pinpoint_note"]
    assert "outside the indexed numbering" in res["pinpoint_note"]
    # Like every unverified pinpoint, it stays on the citation (only an
    # invalid one is stripped); the note tells the caller to confirm it.
    assert res["citation_string_de"].endswith("E. 2.3")


def test_get_erwaegung_hint_says_what_the_fallback_found(corpus):
    res = m._handle_get_erwaegung(decision_id="5A_297/2012", e_number="2.3")
    assert res["error_code"] == "pinpoint_not_found"
    assert res["pinpoint_status"] == m.PINPOINT_UNVERIFIED
    assert "Neither the structure index nor a heading" not in res["hint"]
    assert "has a line headed 2.3" in res["hint"]
    absent = m._handle_get_erwaegung(decision_id="5A_297/2012", e_number="2.7")
    assert absent["pinpoint_status"] == m.PINPOINT_INVALID
    assert absent["hint"].startswith("Neither the structure index nor a heading")


def test_attest_warns_instead_of_accusing(corpus):
    res = m._handle_attest_response(draft_text="Vgl. BGer 5A_297/2012 E. 2.3.")
    assert not [i for i in res.get("issues", []) if i.get("problem") == "pinpoint_not_in_decision"]
