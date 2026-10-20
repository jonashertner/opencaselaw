"""Report 2026-10-20: the structure parser read running text as Erwägung
numbers and dropped sub-numbers below a missing intermediate level.

Fixtures are cut from the served texts of BGE 140 III 312, 146 III 290,
142 III 521 and 99 III 71, plus the cantonal heading forms the change keeps."""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "search_stack")]

from search_stack.extract_decision_structure import extract, parse_erwaegungen_paragraphs  # noqa: E402


def numbers(text):
    return [p["e_number"] for p in parse_erwaegungen_paragraphs(text)]


def by_number(text):
    return {p["e_number"]: p["text"] for p in parse_erwaegungen_paragraphs(text)}


def test_a_missing_intermediate_level_opens_under_the_current_top_number():
    # BGE 140 III 312 prints "6." then "6.3.2"; the Regeste cites E. 6.3.2
    text = ("6.\n6.3.2\nOb die Eingabe verspätet erfolgte, ist nach Art. 229 ZPO zu beurteilen.\n"
            "6.3.2.1\nDie Eingabe vom 29. Oktober 2012 erfolgte nach dem zweiten Schriftenwechsel.\n"
            "6.3.2.3\nDass eine unbeschränkte Ergänzung nicht zulässig ist, folgt aus der Eventualmaxime.\n")
    assert numbers(text) == ["6.3.2", "6.3.2.1", "6.3.2.3"]


def test_a_page_turn_mid_sentence_is_no_erwaegung():
    # BGE 142 III 521: "et\nBGE 142 III 521 S. 532\n6 par. 1 CEDH" was an "E. 6"
    text = ("1.\nLa demande de révision est recevable.\n"
            "2.\n2.3.4\nLes parties ont droit à un tribunal indépendant, telle qu'elle résulte des\n"
            "art. 30 al. 1 Cst.\net\nBGE 142 III 521 S. 532\n6 par. 1 CEDH, car cette garantie est un pilier.\n"
            "C'est ainsi que l'art. 51 al. 3 CPC reprend l'art. 38 al. 3 LTF.\n"
            "2.3.5\nL'examen qui précède tendrait à admettre une lacune.\n"
            "3.\nLe recours est rejeté.\n")
    got = by_number(text)
    assert "6" not in got
    assert "l'art. 51 al. 3 CPC" in got["2.3.4"]


def test_table_cells_and_quoted_statute_paragraphs_are_no_erwaegungen():
    # BGE 99 III 71: the statute's "2 Die Gebühr ..." became E. 2, the table's "10" an E. 10
    text = ("1.\nArt. 28 GebT lautet:\n1 Für die Verwahrung wird eine Gebühr erhoben.\n"
            "2 Die Gebühr beträgt monatlich je Stück einen Franken.\n"
            "2.\nDie Rekurrentinnen sind freilich anderer Meinung.\n"
            "3.\nDen Beschwerdeantrag hat die Aufsichtsbehörde abgewiesen.\n"
            "4.\nRechnerisch wird die Gebühr wie folgt ermittelt.\n"
            "5.\nDie Rekurrentinnen erläutern dies mit folgenden Beispielen:\nFr.\n10.–\n10000\n100000.–\n"
            "10\n100000.–\na) Art. 16 SchKG ermächtigt den Bundesrat, den Gebührentarif zu erlassen.\n")
    got = by_number(text)
    assert [n for n in got if n.isdigit()] == ["1", "2", "3", "4", "5"]
    assert got["2"].startswith("Die Rekurrentinnen sind freilich")
    assert "ermächtigt den Bundesrat" in got["5"]


def test_cantonal_heading_forms_stay_headings():
    # Vaud writes "2\n2.1 ..." without a dot; Ticino puts the dot on the next line
    vaud = ("1.\n1.1 Les parties peuvent attaquer l'ordonnance.\n1.2 Le recours est recevable.\n"
            "2\n2.1 Il convient ensuite d'examiner la qualité pour recourir.\n2.2 Aux termes de l'art. 105 CPP.\n"
            "3. Il résulte de ce qui précède que le recours est rejeté.\n")
    assert "2.1" in numbers(vaud) and "3" in numbers(vaud)
    ticino = ("1\n.   Il ricorrente contesta la decisione.\n2\n.   Nelle proprie osservazioni.\n"
              "3\n.   L'art. 27 CPP in vigore.\nIntimazione:\n1. PI 1\n2. PI 2\n")
    assert [n for n in numbers(ticino) if n.isdigit()][:3] == ["1", "2", "3"]


def test_a_two_part_ruling_without_markers_is_still_segmented():
    # a Vaud ruling with E. 1 and 2 only passed the fallback while a stray number made a third
    text = ("Le Tribunal cantonal statue.\n"
            "1.\n1.1 Les parties peuvent attaquer l'ordonnance de non-entrée en matière.\n"
            "1.2 Le recours a été déposé en temps utile.\n"
            "2.\n2.1 Il convient d'examiner si le recourant a qualité de partie plaignante.\n"
            "2.2 Tel n'est pas le cas en l'espèce, de sorte que le recours est irrecevable.\n")
    s = extract(text, "fr", "vd_gerichte_test")
    assert s.erwaegungen_method == "fallback_numerical_headers"
    assert [p["e_number"] for p in s.erwaegungen_paragraphs] == ["1.1", "1.2", "2.1", "2.2"]
