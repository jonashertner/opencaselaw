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


# --- 2026-10-22: es rows served with the wrong decision_date (report: issue
# #90 part B). ti_gerichte_11.2016.86 and ti_gerichte_14.2013.162 are both
# already handled correctly by `decide` whenever the script is actually run —
# the gap was that it had never been applied to the es shard in production.
# Trimmed, synthetic versions of the real lines below.
ES_11_2016_86 = (
    "Tessin Tribunale di appello diritto civile La prima Camera civile 24.01.2018 11.2016.86\n"
    "Ticino Tribunale di appello diritto civile La prima Camera civile 24.01.2018 11.2016.86\n\n"
    "Esecutività in Svizzera di una decisione italiana secondo la Convenzione di Lugano\n\n"
    "Incarto n.\n11.2016.86\nLugano\n24 gennaio 2018\n/jh\nIn nome\ndella Repubblica e Cantone\n"
    "Ticino\nLa prima Camera civile del Tribunale d'appello\n"
    "giudicando sul reclamo del 2 settembre 2016 contro la sentenza emessa dal Pretore il 29 luglio 2016;\n"
    "Ritenuto\nin fatto:\nA.\nCon\nsentenza del 14 dicembre 1992 il Tribunale ecclesiastico regionale "
    "emiliano di Modena ha annullato il matrimonio."
)
ES_14_2013_162 = (
    "Ticino Tribunale di appello diritto civile La Camera di esecuzione e fallimenti 24.01.2014 14.2013.162\n\n"
    "Rigetto provvisorio dell'opposizione.\n\n"
    "Incarto n.\n14.2013.162\nLugano\n24 gennaio 2014\nB/ww/fb\nIn nome\ndella Repubblica e Cantone\n"
    "Ticino\nIl vicepresidente della Camera di esecuzione e fallimenti del Tribunale d'appello\n"
    "sulla quale istanza il Pretore del Distretto di Lugano, sezione 5, con sentenza del\n"
    "13 settembre 2013 (inc. n. SO.2013.2467) ha così deciso."
)


def test_reported_row_11_2016_86_is_corrected_once_applied():
    obj = {"decision_id": "ti_gerichte_11.2016.86", "court": "ti_gerichte",
           "decision_date": "1992-12-14", "publication_date": "2018-01-24", "full_text": ES_11_2016_86}
    assert r.decide(obj, TODAY) == ("corrected", "2018-01-24")


def test_reported_row_14_2013_162_is_corrected_once_applied():
    obj = {"decision_id": "ti_gerichte_14.2013.162", "court": "ti_gerichte",
           "decision_date": "2013-09-13", "publication_date": "2014-01-24", "full_text": ES_14_2013_162}
    assert r.decide(obj, TODAY) == ("corrected", "2014-01-24")


# --- the genuine rule gap: an es Kopfzeile carrying the stored (wrong) date
# disagrees with the heading, but the direct scraper's twin row independently
# confirms the heading. Modeled on ti_gerichte_17.2012.144 (real docket).
ES_KOPF_DISAGREES_TWIN_CONFIRMS = (
    "Tessin Corte di appello e di revisione penale 19.07.2013 17.2012.144\n"
    "Ticino Corte di appello e di revisione penale 19.07.2013 17.2012.144\n\n"
    "Deposito incustodito di fucili privi di culatta in abitazione secondaria.\n\n"
    "Incarto n.\n17.2012.144\nLocarno\n22 luglio 2013/mi\nIn nome\ndella Repubblica e Cantone\nTicino\n"
)


def test_kopfzeile_disagreement_overridden_by_direct_twin():
    obj = {"decision_id": "ti_gerichte_17.2012.144", "court": "ti_gerichte",
           "decision_date": "2013-07-19", "full_text": ES_KOPF_DISAGREES_TWIN_CONFIRMS}
    # without a twin, the disagreeing Kopfzeile still blocks the change
    assert r.decide(obj, TODAY) == ("kopfzeile_disagrees", None)
    # the direct shard's own decision_date for the same docket agrees with
    # the heading (2013-07-22), so it is allowed to override the Kopfzeile
    assert r.decide(obj, TODAY, twin_date="2013-07-22") == ("corrected", "2013-07-22")


def test_kopfzeile_disagreement_overridden_by_own_publication_date():
    # no direct twin at all, but this row's own publication_date agrees with
    # the heading — accepted as the fallback corroboration
    obj = {"decision_id": "ti_gerichte_17.2012.144", "court": "ti_gerichte",
           "decision_date": "2013-07-19", "publication_date": "2013-07-22",
           "full_text": ES_KOPF_DISAGREES_TWIN_CONFIRMS}
    assert r.decide(obj, TODAY) == ("corrected", "2013-07-22")


def test_kopfzeile_disagreement_not_overridden_by_a_mismatched_twin():
    # negative case: a twin date exists but points to neither the heading nor
    # agreement — evidence stays ambiguous, so the row is left alone
    obj = {"decision_id": "ti_gerichte_17.2012.144", "court": "ti_gerichte",
           "decision_date": "2013-07-19", "full_text": ES_KOPF_DISAGREES_TWIN_CONFIRMS}
    assert r.decide(obj, TODAY, twin_date="2013-07-19") == ("kopfzeile_disagrees", None)
    assert r.decide(obj, TODAY, twin_date=None) == ("kopfzeile_disagrees", None)


def test_stated_in_text_accepts_numeric_dd_mm_yyyy():
    assert r.stated_in_text("2013-09-13", "con sentenza del 13.09.2013 ha deciso")
    assert r.stated_in_text("2013-09-13", "con sentenza del 13/09/2013 ha deciso")
    assert not r.stated_in_text("2013-09-13", "con sentenza del 14.09.2013 ha deciso")


def test_load_twin_dates_collects_direct_rows_and_flags_conflicts_as_ambiguous(tmp_path):
    direct = tmp_path / "ti_gerichte.jsonl"
    es = tmp_path / "es_ti_gerichte.jsonl"
    direct.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in (
        {"court": "ti_gerichte", "docket_number": "17.2012.144", "decision_date": "2013-07-22"},
        # two direct rows that disagree on the same docket: ambiguous
        {"court": "ti_gerichte", "docket_number": "90.1994.1", "decision_date": "1994-05-01"},
        {"court": "ti_gerichte", "docket_number": "90.1994.1", "decision_date": "1994-05-02"},
        # two direct rows that agree: fine to use
        {"court": "ti_gerichte", "docket_number": "90.1994.2", "decision_date": "1994-06-01"},
        {"court": "ti_gerichte", "docket_number": "90.1994.2", "decision_date": "1994-06-01"},
    )), encoding="utf-8")
    es.write_text(json.dumps(
        # an es row must never be mistaken for a direct twin, even if it
        # happens to carry a clean date for the same docket
        {"court": "ti_gerichte", "docket_number": "17.2012.144", "decision_date": "2013-07-19",
         "source": "entscheidsuche"}, ensure_ascii=False) + "\n", encoding="utf-8")
    twins = r.load_twin_dates([direct, es])
    assert twins["17.2012.144"] == "2013-07-22"
    assert twins["90.1994.1"] is None
    assert twins["90.1994.2"] == "1994-06-01"


def test_run_wires_twin_dates_through_for_both_shards(tmp_path):
    direct = tmp_path / "ti_gerichte.jsonl"
    es = tmp_path / "es_ti_gerichte.jsonl"
    direct.write_text(json.dumps(
        {"decision_id": "ti_gerichte_17.2012.144", "court": "ti_gerichte", "docket_number": "17.2012.144",
         "decision_date": "2013-07-22", "full_text": ES_KOPF_DISAGREES_TWIN_CONFIRMS},
        ensure_ascii=False) + "\n", encoding="utf-8")
    es.write_text(json.dumps(
        {"decision_id": "ti_gerichte_17.2012.144", "court": "ti_gerichte", "docket_number": "17.2012.144",
         "decision_date": "2013-07-19", "source": "entscheidsuche",
         "full_text": ES_KOPF_DISAGREES_TWIN_CONFIRMS},
        ensure_ascii=False) + "\n", encoding="utf-8")
    twins = r.load_twin_dates([direct, es])
    direct_stats = r.run(direct, apply=True, examples=0, today=TODAY, twin_dates=twins)
    es_stats = r.run(es, apply=True, examples=0, today=TODAY, twin_dates=twins)
    assert direct_stats["agrees"] == 1  # the direct row is already correct
    assert es_stats["corrected"] == 1
    rows = json.loads(es.read_text().splitlines()[0])
    assert rows["decision_date"] == "2013-07-22"


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
