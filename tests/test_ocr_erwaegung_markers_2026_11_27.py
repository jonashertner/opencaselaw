"""Report 2026-11-27 (Mnemosyne): in BGE 55 II 235 the OCR read the heading
of E. 4 as "4 .. -". ERW_PARA_RE takes one dot and one dash, so E. 4 ran on
inside E. 3, siblings listed only 2 and 3, and cite called E. 4 invalid.
119 BGE rulings (volumes 20-95) carry such markers ("2, -", "3: -", "4.. Die").

An OCR-damaged top-level marker is admitted only where the sequence already
found expects it: the marker before it belongs to E. N-1 and the next one to
a higher number. Fixtures are cut from the served texts. Offline.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "search_stack")]

from search_stack.extract_decision_structure import (  # noqa: E402
    extract,
    parse_erwaegungen_paragraphs,
)


def _nums(text: str) -> list[str]:
    return [p["e_number"] for p in parse_erwaegungen_paragraphs(text)]


BGE_55_II_235 = (
    "Aus den Erwägungen:\n"
    "2. -\nDass der Urkundsbeamte das öffentliche Testament mit Schreibmaschinenschrift "
    "hergestellt hat, macht es nicht ungültig.\n"
    "3. -\nDie die Errichtung eines öffentlichen Testamentes ausmachenden Handlungen sind "
    "in Art. 499 ff. ZGB aufgezählt. Wieso das Datieren die ihm zugedachte Funktion \n"
    "etwa nicht erfüllen könnte, wenn es dem Unterzeichnen \n"
    "durch den Erblasser vorausgeht, ist nicht erfiridlich. \n"
    "4 .. -\n"
    "Die Fassung des Art. 500 ZGB steht auch der \nAuSlegung nicht entgegen, wobei nach dem "
    "in Erwägung 3 Ausgeführten gleichgültig ist, ob das Datum richtig angegeben sei.\n"
)


def test_ocr_marker_opens_the_next_erwaegung():
    paras = parse_erwaegungen_paragraphs(BGE_55_II_235)
    assert [p["e_number"] for p in paras] == ["2", "3", "4"]
    e3, e4 = paras[1]["text"], paras[2]["text"]
    assert e3.endswith("ist nicht erfiridlich.")
    assert e4.startswith("Die Fassung des Art. 500 ZGB")
    assert "4 .. -" not in e3


def test_marker_forms_seen_in_the_volumes():
    for marker in ("2, -", "2: -", "2; -", "2 .. -", "2.' -", "2_ -", "2 . .-:", "2.-."):
        text = f"1. -\nErste Erwägung.\n{marker}\nZweite Erwägung.\n3. -\nDritte.\n"
        assert _nums(text) == ["1", "2", "3"], marker
    inline = "1. -\nErste Erwägung.\n2.. Die Kläger selbst können sich darauf berufen.\n3. -\nDritte.\n"
    assert _nums(inline) == ["1", "2", "3"]


def test_only_where_the_sequence_expects_it():
    # not the next number: a quoted list item or a page artefact stays text
    assert _nums("1. -\nErste.\n5 .. -\nText.\n2. -\nZweite.\n") == ["1", "2"]
    # an E. 2 already exists: a second "2, -" is no heading
    assert _nums("1. -\nErste.\n2. -\nZweite.\n2, -\nnoch Text.\n3. -\nDritte.\n") == ["1", "2", "3"]
    # the next marker is not higher (a sub-number of the open E. 2 follows)
    assert _nums("1. -\nA.\n2. -\nB.\n2.1 Erstens.\n3 .. -\nText.\n2.2 Zweitens.\n4. -\nD.\n") \
        == ["1", "2", "2.1", "2.2", "4"]
    # one punctuation mark is the normal form or nothing ("1," in French prose)
    assert _nums("1. -\nErste.\n2,\non ne se trouve en présence d'aucune contestation.\n3. -\nDritte.\n") \
        == ["1", "3"]


def test_false_friends_from_the_regression_run():
    # Shapes the first draft admitted on 450,000 non-BGE rulings (2026-11-27).
    head = "1. -\nErste Erwägung, die lang genug ist.\n"
    tail = "3. -\nDritte.\n"
    # a citation broken across lines (TI 38.2024.14)
    assert _nums(head + "STF 8C_347/2019 del 17 agosto 2020 consid.\n2.; STF 8C_510/2018\n" + tail) == ["1", "3"]
    # an amount broken across lines (TI 33.2009.8, 10.2003.689)
    assert _nums(head + "importo di Fr. 4 '17\n2.-. Al totale di Fr. 10 '638.-\n" + tail) == ["1", "3"]
    assert _nums(head + "spese giudiziarie di fr.\n2.--.\nInoltre la condanna.\n" + tail) == ["1", "3"]
    # a quoted list of motions or questions (ZH PP250013, TI 38.2006.27)
    assert _nums(head + "mit den Anträgen:\n\"1 - Aufschiebende Wirkung sei zu erteilen.\n"
                 "2 -. Betreibung 1 sei einzustellen.\n" + tail) == ["1", "3"]


def test_printed_omission_mark_adds_no_paragraph():
    # "3 ..." above "4. -": the volume omits E. 3; nothing is served under it
    text = "1. -\nErste.\n2. -\nZweite.\n3 ...\n4. -\nVierte Erwägung.\n"
    assert _nums(text) == ["1", "2", "4"]
    # the last Erwägung omitted, then the dispositive (BGE 44 I 152) or only
    # the printer's line (BGE 78 I 474)
    assert _nums("3. -\nDritte.\n4. -\nVierte.\n5 ...............\nDemnach erkennt das Bundesgericht:\n") \
        == ["3", "4"]
    assert _nums("3. -\nDritte.\n4. -\nVierte.\n5 ....\nIMPRIMERIES REUNIES S. A., LAUSANNE\n") == ["3", "4"]
    # a dotted marker with a real Erwägung inline is a heading (BGE 70 I 216)
    assert _nums("3. -\nDritte.\n4. -\nVierte.\n5 . ......:.. Der angefochtene Entscheid ist aufzuheben.\n") \
        == ["3", "4", "5"]


def test_clean_rulings_are_unchanged():
    text = ("Erwägungen:\n1.\nDie Beschwerde ist zulässig.\n1.1 Sie richtet sich gegen einen Endentscheid.\n"
            "1.2 Sie ist begründet.\n2.\nDie Vorinstanz hat Art. 8 ZGB verletzt.\n"
            "3.\nDie Kosten trägt der Kanton.\n"
            "Demnach erkennt das Bundesgericht:\n1.\nDie Beschwerde wird gutgeheissen.\n")
    s = extract(text, "de", "bger_1C_1_2020")
    assert [p["e_number"] for p in s.erwaegungen_paragraphs] == ["1", "1.1", "1.2", "2", "3"]
