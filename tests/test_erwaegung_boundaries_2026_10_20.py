"""Report 2026-10-20: lines of running text read as Erwägung boundaries.

The served BGE text breaks a line at every linked reference and carries the
printed page heads, so a cited pinpoint ("BGE 130 III 213\\nE. 2.1 S. 216 f."),
a page turn mid-sentence ("et\\nBGE 142 III 521 S. 532\\n6 par. 1 CEDH") and
the cells of a table ("10.–\\n10000") all start lines. The full-text heading
fallback took them for headings, `cite` refused sub-numbers `get_erwaegung`
served, and every BGE's own page-head reference became a graph edge to its
neighbour in the volume. The fixture is modelled on BGE 146 III 290, 142 III
521, 99 III 71, 140 III 409 and 144 III 298 as served.
"""
from __future__ import annotations

import sqlite3

import pytest

import mcp_server as m
from search_stack.build_reference_graph import SCHEMA_SQL

TEXT = """Aus den Erwägungen:
4.
(...)
4.3.1
Die streitverkündende Partei muss ein beziffertes Rechtsbegehren stellen (
BGE 142 III 102
E. 6 S. 110; Urteil 4A_134/2016 vom 11. Juli 2016 E. 1.1). Gemäss einer geplanten Gesetzesnovelle soll dieses Erfordernis entfallen.
4.3.2
Der Beschwerdeführer beruft sich auf die Garantie, telle qu'elle résulte des
art. 30 al. 1 Cst.
et
BGE 146 III 290 S. 296
6 par. 1 CEDH. Das trifft nicht zu.
4.3.3
Die Bezifferung prüft das Gericht von Amtes wegen (
Art. 60 ZPO
;
BGE 142 III 102
E. 3 S. 104). Ein Verzicht ist auch im Rahmen der erwähnten Gesetzesnovelle nicht vorgesehen.
5.
Die Rekurrentinnen erläutern die Gebühr mit folgenden Beispielen:
Fr.
10.–
10000
100000.–
10
100000.–
a) Art. 16 SchKG ermächtigt den Bundesrat, den Gebührentarif zu erlassen.
6.
Die organschaftliche Treuepflicht gilt auch hier (
BGE 130 III 213
E. 2.1 S. 216 f. [zur AG]; WATTER/ROTH PELLANDA, in: Basler Kommentar, N. 9 zu
Art. 809 OR
). Ebenso wenig steht die Scheidung im Weg (
BGE 81 II 395
E. 3 S. 399;
BGE 113 II 97
E. 2 S. 99). Die Beschwerde ist abzuweisen.
"""


def block(e_number):
    return m._erwaegung_from_text(TEXT, e_number)


def test_a_cited_pinpoint_on_its_own_line_does_not_end_the_erwaegung():
    # BGE 146 III 290 E. 4.3.3 was cut after "BGE 142 III 102"
    e431 = block("4.3.1")
    assert "Gemäss einer geplanten Gesetzesnovelle" in e431["text"]
    assert "4.3.2" not in e431["text"]
    e433 = block("4.3.3")
    assert e433["text"].endswith("erwähnten Gesetzesnovelle nicht vorgesehen.")


def test_a_bare_number_line_is_the_next_heading():
    # "4.3.3\nDass ..." is how BGE print a heading; the end scanner skipped it
    assert block("4.3.2")["text"].startswith("4.3.2\nDer Beschwerdeführer")
    assert "4.3.3" not in block("4.3.2")["text"]
    assert "5.\nDie Rekurrentinnen" not in block("4.3.3")["text"]


def test_a_page_turn_mid_sentence_is_not_a_heading():
    # BGE 142 III 521 E. 2.3.4 ended at the page head "S. 532"; "6 par." is no E. 6
    assert "6 par. 1 CEDH. Das trifft nicht zu." in block("4.3.2")["text"]
    assert block("6")["text"].startswith("6.\nDie organschaftliche")


def test_table_cells_are_not_headings():
    # BGE 99 III 71: E. 5 stopped in the table and its a) to e) sat under an "E. 10"
    e5 = block("5")
    assert "a) Art. 16 SchKG ermächtigt den Bundesrat" in e5["text"]
    assert "6.\nDie organschaftliche" not in e5["text"]
    assert block("10") is None
    assert block("10000") is None


def test_a_citation_line_is_not_an_erwaegung_of_this_decision():
    # BGE 140 III 409 "E. 2.1" and BGE 144 III 298 "E. 3" were citations
    assert block("2.1") is None
    assert block("3") is None
    assert block("2") is None


def test_a_real_prefixed_heading_still_counts():
    text = "Erwägungen:\n\nE. 2.3 Selon la jurisprudence constante, le juge applique le droit.\n"
    assert m._erwaegung_from_text(text, "2.3")["text"].startswith("E. 2.3")


PARAS = [{"e_number": n, "parent": None, "depth": 1, "text": f"indexed E. {n}"} for n in ("4", "5", "6")]


def test_cite_verifies_a_heading_the_index_lacks():
    # BGE 140 III 312 E. 6.3.2: the Regeste's own pinpoint, served by
    # get_erwaegung, called invalid by cite
    verdict = m._verify_pinpoint("4.3.3", paragraphs=PARAS, full_text=TEXT)
    assert verdict["status"] == m.PINPOINT_VERIFIED
    assert verdict["method"] == "full_text_heading"
    assert verdict["text"].startswith("4.3.3\nDie Bezifferung")
    for absent in ("2.1", "3", "10"):
        assert m._verify_pinpoint(absent, paragraphs=PARAS, full_text=TEXT)["status"] == m.PINPOINT_INVALID
    # without the text, structure alone decides as before
    assert m._verify_pinpoint("4.3.3", paragraphs=PARAS)["status"] == m.PINPOINT_INVALID


def test_a_text_heading_outside_the_index_numbering_is_not_taken():
    # an excerpt that starts at E. 5 has no E. 4.3.1, whatever the text says
    late = [p for p in PARAS if p["e_number"] != "4"]
    assert m._verify_pinpoint("4.3.1", paragraphs=late, full_text=TEXT)["status"] == m.PINPOINT_INVALID
    assert m._text_heading_fits_structure("4.3.1", PARAS)
    assert m._text_heading_fits_structure("7", PARAS)
    assert not m._text_heading_fits_structure("8", PARAS)
    assert m._text_heading_fits_structure("3", [{"e_number": "0"}])


# ── find_citations: a BGE's own page-head reference is not a citation ─────


@pytest.fixture
def graph(tmp_path, monkeypatch):
    db = tmp_path / "reference_graph.db"
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA_SQL)
    for did, dn, date in (("bge_139 III 110", "139 III 110", "2013-02-05"),
                          ("bge_139 III 120", "139 III 120", "2013-02-26"),
                          ("bge_138 III 130", "138 III 130", "2012-01-01")):
        conn.execute("INSERT INTO decisions (decision_id, court, docket_norm, decision_date) VALUES (?, 'bge', ?, ?)",
                     (did, dn, date))
    for ref, target, kind in (("139 III 120", "bge_139 III 110", "bge_pincite"),
                              ("BGE 139 III 120", "bge_139 III 110", "bge_pincite"),
                              ("BGE 138 III 130", "bge_138 III 130", "bge_norm")):
        conn.execute("INSERT INTO decision_citations (source_decision_id, target_ref, target_type, mention_count)"
                     " VALUES ('bge_139 III 120', ?, 'bge', 1)", (ref,))
        conn.execute("INSERT INTO citation_targets VALUES ('bge_139 III 120', ?, ?, ?, 0.9)", (ref, target, kind))
    conn.commit()
    conn.close()
    monkeypatch.setattr(m, "GRAPH_DB_PATH", db)
    monkeypatch.setattr(m, "_resolve_decision_id", lambda x: x)
    return db


def test_find_citations_drops_a_bge_s_own_reference(graph):
    out = m.find_citations(decision_id="bge_139 III 120", direction="both")
    assert [o["target_ref"] for o in out["outgoing"]] == ["BGE 138 III 130"]
    assert out["outgoing_total"] == 1
    neighbour = m.find_citations(decision_id="bge_139 III 110", direction="incoming")
    assert neighbour["incoming"] == [] and neighbour["incoming_total"] == 0
    cited = m.find_citations(decision_id="bge_138 III 130", direction="incoming")
    assert [i["source_decision_id"] for i in cited["incoming"]] == ["bge_139 III 120"]
