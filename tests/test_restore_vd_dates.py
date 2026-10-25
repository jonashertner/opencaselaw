"""scripts/restore_vd_dates.py — Vaud rows (vd_findinfo, vd_gerichte) get their
ruling's heading date and the language of their text (report 2026-10-13)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import restore_vd_dates as r  # noqa: E402

TODAY = "2026-10-14"
SEANCE = ("Vaud Tribunal cantonal Cour d'appel pénale 23.07.2015 Jug / 2015 / 329 LÉSIONS | 52 CP "
          "TRIBUNAL CANTONAL 288 PE12.024402-VFE COUR D’APPEL PENALE ________________ Séance du "
          "23 juillet 2015 ____________ Composition : M. S, président. Statuant sur l'appel "
          "interjeté contre le jugement rendu le 13 mars 2015 par le Tribunal de police")
PLACEHOLDER = ("Waadt Tribunal cantonal Cour administrative 10 RÉCUSATION | 47 CPC TRIBUNAL CANTONAL "
               "10 COUR ADMINISTRATIVE ______ RECUSATION CIVILE Séance du 1 er mars 2023 ______ "
               "Présidence de Mme B, présidente")
KEYWORDS_ONLY = ("Waadt Tribunal cantonal Cour administrative 18 ADMISSION DE LA DEMANDE, AUTORITÉ DE "
                 "CONCILIATION, PARENTÉ, BAIL À LOYER, RÉCUSATION Vaud Tribunal cantonal Cour administrative")


def _row(did, docket, stored, text, lang="fr"):
    return {"decision_id": did, "court": "vd_findinfo", "docket_number": docket,
            "decision_date": stored, "language": lang, "full_text": text, "regeste": None}


def test_heading_of_the_header_block():
    assert r.heading_date(SEANCE, "Jug / 2015 / 329", TODAY) == ("2015-07-23", "heading")


def test_premier_with_space_and_placeholder_row():
    assert r.heading_date(PLACEHOLDER, "10", TODAY)[0] == "2023-03-01"


def test_reviewed_decision_after_the_panel_is_never_taken():
    text = ("TRIBUNAL CANTONAL CHAMBRE DES RECOURS PENALE ______ Composition : M. X. "
            "Statuant sur le recours contre l'ordonnance rendue le 2 septembre 2019, Décision du 5 mai 2019")
    assert r.heading_date(text, "Décision / 2019 / 790", TODAY) == (None, "no_heading")


def test_number_year_bounds_nothing():
    # the findinfo number carries the publication year, which can be years later
    dec = "TRIBUNAL CANTONAL CHAMBRE DES RECOURS CIVILE ______ Arrêt du 22 décembre 2017 ______ Composition"
    assert r.heading_date(dec, "HC / 2021 / 27", TODAY)[0] == "2017-12-22"


def test_dates_in_the_facts_are_never_the_heading():
    # seen in the 2026-10-14 dry run once the year bound was dropped: no
    # "Composition" line, so the first match was an event of the facts
    facts = ("TRIBUNAL CANTONAL COUR D'APPEL PENALE ____ A. Le service a confirmé que, dans une "
             "séance du 21 mai 2014, la délégation a décidé. Par jugement incident du 28 janvier 2011")
    assert r.heading_date(facts, "Jug / 2019 / 403", TODAY) == (None, "no_heading")


def test_kopfzeile_that_disagrees_blocks_the_change():
    text = SEANCE.replace("23.07.2015", "24.07.2015")
    assert r.heading_date(text, "Jug / 2015 / 329", TODAY) == (None, "kopfzeile_disagrees")


def test_future_heading_is_refused():
    text = "TRIBUNAL CANTONAL ______ Arrêt du 5 mai 2027 ______ Composition"
    assert r.heading_date(text, "HC / 2027 / 1", TODAY) == (None, "heading_in_future")


def test_decide_corrects_fills_and_fixes_language():
    assert r.decide(_row("a", "Jug / 2015 / 329", "2015-03-13", SEANCE), TODAY) == (
        ["date_corrected"], {"decision_date": "2015-07-23"})
    assert r.decide(_row("b", "10", "2021-01-01", PLACEHOLDER, lang="de"), TODAY) == (
        ["date_filled", "language_fixed"], {"decision_date": "2023-03-01", "language": "fr"})
    # no body, French keywords only: date unknown, language still French
    assert r.decide(_row("c", "18", "2021-01-01", KEYWORDS_ONLY, lang="de"), TODAY) == (
        ["no_heading", "language_fixed"], {"language": "fr"})


def test_german_text_keeps_its_tag():
    german = "Das Gericht hat die Beschwerde nicht zugelassen und ist auf die Klage nicht eingetreten. " * 3
    assert r.decide(_row("d", "1", None, german, lang="de"), TODAY) == (["no_heading"], {})


def test_apply_stamps_keeps_mode_and_is_idempotent(tmp_path):
    p = tmp_path / "es_vd_findinfo.jsonl"
    other = {"decision_id": "x", "court": "vd_omni", "decision_date": "2020-01-01"}
    p.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in (
        _row("b", "10", "2021-01-01", PLACEHOLDER, lang="de"), other)), encoding="utf-8")
    p.chmod(0o600)
    stats = r.run(p, apply=True, examples=0, today=TODAY)
    assert stats["rows_changed"] == 1 and stats["other_court"] == 1
    rows = [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["decision_date"] == "2023-03-01" and rows[0]["language"] == "fr"
    assert rows[0]["date_restore"]["from"] == "2021-01-01"
    assert rows[0]["language_restore"] == {**rows[0]["language_restore"], "from": "de", "to": "fr"}
    assert rows[1] == other
    assert (p.stat().st_mode & 0o777) == 0o600
    assert r.run(p, apply=True, examples=0, today=TODAY)["rows_changed"] == 0
    assert list(tmp_path.iterdir()) == [p]


def test_vd_gerichte_rows_are_repaired_too(tmp_path):
    p = tmp_path / "vd_gerichte.jsonl"
    row = {**_row("vd_gerichte_41_2017", "41/2017", "2016-03-07",
                  "TRIBUNAL CANTONAL COUR ADMINISTRATIVE ______ Séance du 30 octobre 2017 ______ Présidence de M. X"),
           "court": "vd_gerichte"}
    p.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    assert r.run(p, apply=True, examples=0, today=TODAY)["date_corrected"] == 1
    assert json.loads(p.read_text(encoding="utf-8"))["decision_date"] == "2017-10-30"


def test_one_year_off_on_the_same_day_is_left_alone():
    text = "TRIBUNAL CANTONAL ______ Séance du 7 mars 2023 ______ Composition"
    assert r.decide(_row("e", "1", "2024-03-07", text), TODAY) == (["year_off_by_one"], {})


def test_small_gap_the_text_never_states_is_left_alone():
    text = "TRIBUNAL CANTONAL ______ Séance du 8 juillet 2025 ______ Composition"
    assert r.decide(_row("f", "AM 22/25", "2025-07-05", text), TODAY) == (["small_unexplained_gap"], {})


def test_small_gap_to_a_dated_event_of_the_text_is_corrected():
    # the portal took the date the dispositif was communicated
    text = ("TRIBUNAL CANTONAL ______ Jugement du 24 novembre 2020 ______ Composition ... "
            "Le jugement qui précède, dont le dispositif a été communiqué aux parties le 4 décembre 2020")
    assert r.decide(_row("g", "25/2020/JMN", "2020-12-04", text), TODAY) == (
        ["date_corrected"], {"decision_date": "2020-11-24"})


# Cour d'appel pénale judgments open with "Audience du"; the stored value was the
# first instance's judgment date (PE17.011760: 2022-10-10 vs 17 August 2023).
AUDIENCE = ("TRIBUNAL CANTONAL 237 PE17.011760-SOS COUR D’APPEL PENALE ______________________________ "
            "Audience du 17 août 2023 __________________ Composition : M. W, président. "
            "Statuant sur les appels formés contre le jugement rendu le 10 octobre 2022")


def test_audience_heads_appeal_judgments():
    assert r.heading_date(AUDIENCE, "PE17.011760", TODAY) == ("2023-08-17", "heading")


def test_audience_outside_the_cour_d_appel_penale_is_a_hearing_not_the_ruling():
    # Cour civile: hearing 18.08.2010, written judgment 06.12.2010
    text = ("TRIBUNAL CANTONAL CM10.017229 COUR CIVILE ____________ Audience du "
            "18 août 2010 ____________ Composition : M. X")
    assert r.heading_date(text, "CM10.017229", TODAY) == (None, "no_heading")


def test_an_audience_in_the_facts_is_never_the_heading():
    facts = ("TRIBUNAL CANTONAL COUR D'APPEL PENALE ____ A. Lors de l'audience du 3 mars 2021, "
             "le prévenu a contesté les faits.")
    assert r.heading_date(facts, "PE20.000001", TODAY) == (None, "no_heading")


def test_decide_moves_an_appeal_judgment_to_its_audience_date():
    row = _row("vd_gerichte_PE17.011760", "PE17.011760", "2022-10-10", AUDIENCE)
    row["court"] = "vd_gerichte"
    changed, new = r.decide(row, TODAY)
    assert new["decision_date"] == "2023-08-17"


def test_audience_under_a_letter_split_court_name():
    text = ("TRIBUNAL CANTONAL 77 PE07.023614-ABA JUGEMENT DE LA COUR D’ APP EL PE NAL E ______________ "
            "Audience du 9 mai 2012 __________________ Présidence de M. S")
    assert r.heading_date(text, "PE07.023614", TODAY)[0] == "2012-05-09"


@pytest.mark.parametrize("failing", ["dumps", "replace"])
def test_interrupt_mid_write_leaves_no_stray_file_and_the_shard_untouched(tmp_path, monkeypatch, failing):
    # the build ingests every *.jsonl in the shard dir: a temp file must never outlive the run
    p = tmp_path / "es_vd_findinfo.jsonl"
    p.write_text(json.dumps(_row("b", "10", "2021-01-01", PLACEHOLDER, lang="de"), ensure_ascii=False) + "\n",
                 encoding="utf-8")
    before = p.read_bytes()

    def boom(*a, **k):
        raise KeyboardInterrupt

    monkeypatch.setattr(r.json if failing == "dumps" else r.os, failing, boom)
    with pytest.raises(KeyboardInterrupt):
        r.run(p, apply=True, examples=0, today=TODAY)
    assert list(tmp_path.iterdir()) == [p]
    assert p.read_bytes() == before
