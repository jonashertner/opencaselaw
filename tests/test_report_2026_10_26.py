"""External report 2026-10-26 (Adrian König's AI): Gericht OW AbR 2000/01 Nr. 4,
a ruling of 18 July 2001, was cited as of 8 February 1999, the day of a
Gemeinderat resolution its Sachverhalt recounts; its Regeste stopped after
239 characters at "vom 18. Juli 2".

The 2026-03-12 date pass read "Beschluss vom 8. Februar 1999" because the
entry's own line, "Entscheid der Obergerichtskommisson vom 18. Juli 2001",
is not "Entscheid vom"; the scraper stores the portal's abstract, cut at
~240 characters, as the Regeste. scripts/restore_ow_rulings.py repairs the
shard, the OW scraper reads the entry's own line and Regeste block.

The texts below are excerpts of the served full texts.
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scrapers.cantonal.ow_gerichte import full_regeste, ruling_date  # noqa: E402
from scripts import restore_ow_rulings as row  # noqa: E402

ABR_TEXT = (
    "AbR 2000/01 Nr. 4, S. 49:\n"
    "Art. 35 Abs. 1 ZGB\n"
    "Einleitung des Verschollenheitsverfahrens bei einer Person, die ursprünglich still "
    "ausgewandert und seit 18 Jahren nachrichtenlos abwesend ist.\n"
    "Entscheid der Obergerichtskommisson vom 18. Juli 2001\n"
    "Sachverhalt:\n"
    "Im August 1983 reiste M. angeblich für zwei Wochen nach New York.\n"
    "Am 3. November 1987 errichtete der Einwohnergemeinderat Alpnach über M. eine "
    "Beistandschaft. Mit Beschluss vom 8. Februar 1999 nahm der Einwohnergemeinderat "
    "Alpnach Kenntnis vom Bericht des Beistandes.\n"
    "Aus den Erwägungen:\n"
    "3. Ist der Tod einer Person höchstwahrscheinlich, so kann sie der Richter für "
    "verschollen erklären (Art. 35 Abs. 1 ZGB).\n"
)
CUT_REGESTE = (
    "AbR 2000/01 Nr. 4, S. 49: Art. 35 Abs. 1 ZGB Einleitung des Verschollenheitsverfahrens "
    "bei einer Person, die ursprünglich still ausgewandert und seit 18 Jahren nachrichtenlos "
    "abwesend ist. Entscheid der Obergerichtskommisson vom 18. Juli 2"
)
ROW = {
    "decision_id": "ow_gerichte_AbR 2000_01 Nr. 4", "court": "ow_gerichte",
    "docket_number": "AbR 2000/01 Nr. 4", "decision_date": "1999-02-08",
    "publication_date": "2015-11-26", "regeste": CUT_REGESTE, "full_text": ABR_TEXT,
}


def test_the_entry_s_own_line_dates_it_not_the_sachverhalt():
    assert ruling_date(ABR_TEXT, "AbR 2000/01 Nr. 4") == ("ok", date(2001, 7, 18))


def test_ruling_line_forms():
    head = "VVGE 1987/88 Nr. 7, S. 20:\nArt. 1 BauG.\nRegeste.\n"
    for line, want in [
        ("Entscheid des Regierungsrates vom 26. Januar 1988 (Nr. 1159).", date(1988, 1, 26)),
        ("Urteil der Rekurskommission für Sozialversicherung vom 7. Juni 1987", date(1987, 6, 7)),
        ("Präsidialverfügung vom 5. Oktober 1987", date(1987, 10, 5)),
        ("Entscheid vom 17.12.1987 i.S. Rekurs A. A. gegen Gemeinderat Engelberg.",
         date(1987, 12, 17)),
    ]:
        assert ruling_date(head + line + "\nAus den Erwägungen:\n1. …", "VVGE 1987/88 Nr. 7") \
            == ("ok", want), line


def test_dates_the_ruling_line_cannot_settle_are_left_alone():
    head = "AbR 2002/03 Nr. 4, S. 49:\nArt. 176 ZGB\nRegeste.\n"
    split = head + "Entscheid der Obergerichtskommission vom 25. November/22. Dezember 2003\n"
    assert ruling_date(split, "AbR 2002/03 Nr. 4") == ("split_date", None)
    days = head + "Entscheid des Obergerichts vom 15./23. Dezember 2003\n"
    assert ruling_date(days, "AbR 2002/03 Nr. 4") == ("split_date", None)
    # The printed "VVGE 1987/88 Nr. 30 ... vom 2. März 1887" is a misprint.
    misprint = "VVGE 1987/88 Nr. 30, S. 41:\nArt. 16 SVG.\nEntscheid des Regierungsrates vom 2. März 1887 (Nr. 1318).\n"
    assert ruling_date(misprint, "VVGE 1987/88 Nr. 30") == ("implausible_year", None)
    # A ruling reported a year after its volume closes is not one.
    late = "AbR 2000/01 Nr. 2, S. 5:\nArt. 1 ZPO\nEntscheid des Obergerichts vom 8. November 2002\n"
    assert ruling_date(late, "AbR 2000/01 Nr. 2") == ("ok", date(2002, 11, 8))
    # A sentence of the facts or a note on the appeal is no ruling line.
    note = (head + "Das Bundesgericht hat die Beschwerde abgewiesen (Urteil vom 22. September 2003).\n"
            "Mit Beschluss vom 8. Februar 2003 wies der Gemeinderat …\nAus den Erwägungen:\n")
    assert ruling_date(note, "AbR 2002/03 Nr. 4") == ("no_line", None)


def test_the_cut_regeste_is_completed_from_the_text():
    full = full_regeste(ABR_TEXT, CUT_REGESTE)
    assert full.endswith("Entscheid der Obergerichtskommisson vom 18. Juli 2001")
    assert full.startswith(CUT_REGESTE)
    assert full_regeste(ABR_TEXT, full) is None          # complete already
    assert full_regeste(ABR_TEXT, "Ein anderer Text") is None


def test_a_regeste_block_without_a_dating_line_is_not_guessed():
    # Older VVGE run Regeste and facts together: no end of the block to find.
    old = ("VVGE 1966/70 Nr. 13, S. 40:\nVerweigerung der Baubewilligung.\n"
           "A. Mit Beschluss hat der Gemeinderat das Gesuch abgewiesen. " + "x" * 300 + "\n"
           "G. beantragt in seiner Vernehmlassung vom 18. Juni 1970, es sei abzuweisen.\n"
           "In Erwägung:\n1. …\n")
    assert full_regeste(old, old[:240].replace("\n", " ")) is None
    # A non-ruling line dating the entry does close it.
    opinion = ("VVGE 1993/94 Nr. 28, S. 92:\nArt. 10 VV zum BauG.\nRegeste.\n"
               "Stellungnahme des Rechtsdienstes vom 3. Juni 1993.\n1. Gemäss Art. 10 …\n")
    assert full_regeste(opinion, "VVGE 1993/94 Nr. 28, S. 92: Art. 10").endswith(
        "Stellungnahme des Rechtsdienstes vom 3. Juni 1993.")


def test_restore_rewrites_the_shard_once_and_stamps_the_change(tmp_path):
    imported = dict(ROW, decision_id="ow_gerichte_AbR 2000_01 Nr. 6", decision_date="2015-11-26",
                    publication_date=None, regeste=None)
    stub = dict(ROW, decision_id="ow_gerichte_AbR_2000_01_Nr._4", source="entscheidsuche",
                decision_date="2015-11-26")
    other = {"decision_id": "x", "court": "nw_gerichte", "decision_date": "2020-01-01"}
    shard = tmp_path / "ow_gerichte.jsonl"
    shard.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n"
                             for r in (ROW, imported, stub, other)), encoding="utf-8")
    stats = row.run(shard, apply=True, examples=0)
    assert stats["rows_changed"] == 2 and stats["skipped_other"] == 2
    rows = {r["decision_id"]: r for r in map(json.loads, shard.read_text().splitlines())}
    fixed = rows["ow_gerichte_AbR 2000_01 Nr. 4"]
    assert fixed["decision_date"] == "2001-07-18"
    assert fixed["date_restore"]["from"] == "1999-02-08"
    assert fixed["publication_date"] == "2015-11-26"
    assert fixed["regeste"].endswith("vom 18. Juli 2001")
    assert fixed["regeste_restore"]["from_len"] == len(CUT_REGESTE)
    # The 2015 import day moves to publication_date when that is empty.
    assert rows["ow_gerichte_AbR 2000_01 Nr. 6"]["decision_date"] == "2001-07-18"
    assert rows["ow_gerichte_AbR 2000_01 Nr. 6"]["publication_date"] == "2015-11-26"
    assert rows["ow_gerichte_AbR_2000_01_Nr._4"] == stub
    assert rows["x"] == other
    again = row.run(shard, apply=True, examples=0)
    assert again["rows_changed"] == 0 and again["date_agrees"] == 2


def test_the_03_12_date_pass_refuses_ow():
    from scripts import repair_decision_dates as rdd
    assert {"ow_gerichte", "es_ow_gerichte"} <= rdd.NEVER_REPAIR_SHARDS


def test_stubs_with_a_direct_twin_are_dropped_and_kept_aside(tmp_path):
    direct = tmp_path / "ow_gerichte.jsonl"
    direct.write_text(json.dumps(ROW, ensure_ascii=False) + "\n", encoding="utf-8")
    twin = {"decision_id": "ow_gerichte_AbR_2000_01_Nr._4", "court": "ow_gerichte",
            "docket_number": "AbR_2000_01_Nr._4", "source": "entscheidsuche"}
    orphan = dict(twin, decision_id="ow_gerichte_VVGE_1971_75_Nr._12", docket_number="VVGE_1971_75_Nr._12")
    es = tmp_path / "es_ow_gerichte.jsonl"
    es.write_text("".join(json.dumps(r) + "\n" for r in (twin, orphan)), encoding="utf-8")
    assert row.drop_es_twins(direct, es, apply=False) == {"es_dropped_twin": 1, "es_kept": 1}
    assert len(es.read_text().splitlines()) == 2          # dry run writes nothing
    row.drop_es_twins(direct, es, apply=True)
    assert [json.loads(x)["decision_id"] for x in es.read_text().splitlines()] == [orphan["decision_id"]]
    assert sorted(p.name for p in tmp_path.glob("*.jsonl")) == ["es_ow_gerichte.jsonl", "ow_gerichte.jsonl"]
    side = next(tmp_path.glob("es_ow_gerichte.jsonl.dropped-*"))
    assert json.loads(side.read_text())["decision_id"] == twin["decision_id"]
    # The stub's id stays answerable through the direct row.
    kept = json.loads(direct.read_text())
    assert kept["previous_decision_id"] == twin["decision_id"]
    assert kept["previous_id_source"] == "es_twin_dropped"
    assert row.drop_es_twins(direct, es, apply=True) == {"es_kept": 1}
