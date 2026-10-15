"""scripts/restore_ti_dates.py — Ticino rows get the date of their ruling's own
heading, only where the stored date is provably another event (2026-10-15)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import restore_ti_dates as r  # noqa: E402

TODAY = "2026-10-15"
HEAD = ("Incarto n.32.2023.125 FC Lugano 11 aprile 2024 In nome della Repubblica e Cantone Ticino "
        "Il vicepresidente del Tribunale cantonale delle assicurazioni statuendo sul ricorso "
        "dell'8 novembre 2023 contro la decisione del 10 ottobre 2023 emanata da Ufficio")


def _row(stored, text=HEAD):
    return {"decision_id": "ti_gerichte_32.2023.125", "court": "ti_gerichte",
            "decision_date": stored, "full_text": text}


def test_heading_date_with_initials_and_glued_words():
    assert r.heading_date(HEAD, TODAY) == ("2024-04-11", "heading")
    glued = "Incarto n.32.2023.144 JV/sc Lugano 8 ottobre 2024 In nomedella Repubblica e Cantone"
    assert r.heading_date(glued, TODAY)[0] == "2024-10-08"


def test_contested_decision_date_is_corrected():
    assert r.decide(_row("2023-10-10"), TODAY) == ("corrected", "2024-04-11")


def test_empty_date_is_filled():
    assert r.decide(_row(None), TODAY) == ("filled", "2024-04-11")


def test_stored_later_or_unstated_is_left_alone():
    assert r.decide(_row("2024-05-02"), TODAY) == ("stored_later_left_alone", None)
    assert r.decide(_row("2023-09-01"), TODAY) == ("stored_not_in_text_left_alone", None)


def test_no_heading_without_the_header_block():
    # a date in the body never counts; nor a heading not followed by the formula
    assert r.heading_date("Ritenuto in fatto che il 5 marzo 2024 il Consiglio di Stato", TODAY)[0] is None
    assert r.heading_date("Incarto n. 1 Lugano 5 marzo 2024 Ritenuto in fatto", TODAY)[0] is None


def test_heading_after_a_prefix_or_the_es_kopfzeile():
    # the re-fetched 32.2023.125 opens with "Raccomandata"
    assert r.heading_date("Raccomandata " + HEAD, TODAY)[0] == "2024-04-11"
    es = ("Ticino Tribunale di appello diritto civile La Camera di esecuzione e fallimenti 02.12.2016 "
          "15.2016.109 Ricorso contro una convenzione 1 Incarto n. 15.2016.109 Lugano 2 dicembre 2016 "
          "In nome della Repubblica e Cantone Ticino")
    assert r.heading_date(es, TODAY) == ("2016-12-02", "heading")


def test_kopfzeile_that_disagrees_blocks_the_change():
    es = ("Tessin Tribunale cantonale 06.08.2008 32.2007.258 Incarto n. 32.2007.258 Lugano 7 agosto 2008 "
          "In nome della Repubblica e Cantone Ticino")
    assert r.heading_date(es, TODAY) == (None, "kopfzeile_disagrees")


def test_first_of_month_written_1_degree():
    t = "Incarto n. 52.2025.75 Lugano 1° aprile 2025 In nome della Repubblica e Cantone Ticino"
    assert r.heading_date(t, TODAY)[0] == "2025-04-01"
    assert r.stated_in_text("2025-04-01", t)


def test_apply_stamps_keeps_mode_and_is_idempotent(tmp_path):
    p = tmp_path / "ti_gerichte.jsonl"
    other = {"decision_id": "x", "court": "vd_gerichte", "decision_date": "2020-01-01"}
    p.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in (_row("2023-10-10"), other)))
    p.chmod(0o644)
    stats = r.run(p, apply=True, examples=0, today=TODAY)
    assert stats["rows_changed"] == 1 and stats["other_court"] == 1
    rows = [json.loads(ln) for ln in p.read_text().splitlines()]
    assert rows[0]["decision_date"] == "2024-04-11"
    assert rows[0]["date_restore"] == {**rows[0]["date_restore"], "from": "2023-10-10", "method": "ti_heading"}
    assert rows[1] == other
    assert (p.stat().st_mode & 0o777) == 0o644
    assert r.run(p, apply=True, examples=0, today=TODAY)["rows_changed"] == 0
    assert list(tmp_path.iterdir()) == [p]
