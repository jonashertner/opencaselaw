"""be_weitere (frozen entscheidsuche bucket) folds into be_direktionen (direct).

Both courts hold the Bernese directorates' rulings under the same docket
(2025.GSI.2700, 2023.DIJ.92, 300.01-19). On 2026-11-08, 182 were served twice,
and the BKD/DIJ sources added to be_direktionen bring 309 more. be_weitere is a
collection bucket (it names no authority), so the attributed row is kept, and
the dropped id stays resolvable as an alias in both passes.
"""
from __future__ import annotations

import sqlite3

import build_fts5
from db_schema import SCHEMA_SQL

TEXT = ("Die Direktion für Inneres und Justiz zieht in Erwägung, dass die "
        "Handänderungssteuer auf dem Kaufpreis des Grundstücks zu erheben ist. ") * 30


def _row(decision_id, court, text, docket="2023.DIJ.92", date="2025-05-07"):
    return {
        "decision_id": decision_id, "court": court, "canton": "BE",
        "docket_number": docket, "decision_date": date, "language": "de",
        "title": "Handänderungssteuer", "regeste": None, "full_text": text,
        "source_url": "https://www.gba.dij.be.ch/x.pdf",
    }


def _db(tmp_path, *rows):
    c = sqlite3.connect(str(tmp_path / "d.db"))
    c.executescript(SCHEMA_SQL)
    for r in rows:
        assert build_fts5.insert_decision(c, r)
    c.commit()
    return c


def _ids(c):
    return sorted(r[0] for r in c.execute("SELECT decision_id FROM decisions"))


def _aliases(c):
    return {p: d for p, d in c.execute("SELECT previous_id, decision_id FROM decision_id_aliases")}


def test_same_date_keeps_the_direct_row(tmp_path):
    c = _db(tmp_path,
            _row("be_direktionen_2023.DIJ.92", "be_direktionen", TEXT[:-40]),
            _row("be_weitere_2023.DIJ.92", "be_weitere", TEXT))
    assert build_fts5._cross_court_dedup(c) == 1
    assert _ids(c) == ["be_direktionen_2023.DIJ.92"]
    assert _aliases(c)["be_weitere_2023.DIJ.92"] == "be_direktionen_2023.DIJ.92"


def test_other_date_same_text_folds_with_alias(tmp_path):
    # es dated the old BKD rulings differently (300.01-19: 2018-12-17 vs the
    # table's 03.05.2019); the text decides.
    c = _db(tmp_path,
            _row("be_direktionen_300.01-19", "be_direktionen", TEXT, docket="300.01-19", date="2019-05-03"),
            _row("be_weitere_300.01-19", "be_weitere", TEXT, docket="300.01-19", date="2018-12-17"))
    assert build_fts5._cross_court_dedup(c) == 1
    assert _ids(c) == ["be_direktionen_300.01-19"]
    assert _aliases(c)["be_weitere_300.01-19"] == "be_direktionen_300.01-19"


def test_other_ruling_of_the_docket_stays(tmp_path):
    c = _db(tmp_path,
            _row("be_direktionen_2023.DIJ.92", "be_direktionen", TEXT),
            _row("be_weitere_2023.DIJ.92", "be_weitere",
                 "Zwischenverfügung über die Akteneinsicht und die Kosten. " * 40, date="2024-01-10"))
    assert build_fts5._cross_court_dedup(c) == 0
    assert len(_ids(c)) == 2


def test_bucket_rows_without_a_direct_twin_stay(tmp_path):
    c = _db(tmp_path,
            _row("be_weitere_01.43", "be_weitere", TEXT, docket="01.43", date="2003-05-14"),
            _row("be_direktionen_2023.DIJ.92", "be_direktionen", TEXT))
    assert build_fts5._cross_court_dedup(c) == 0
    assert len(_ids(c)) == 2


def test_bvd_es_copy_with_wrong_date_folds_into_the_direct_row(tmp_path):
    """be_bvd direct rows take the live es id (be_bvd_110_2018_117). The es copy
    of that ruling, dated 2017-03-09 where the ruling says 4 December 2018,
    becomes <id>_d20170309 on import and the federation fold merges it."""
    text = ("ENTSCHEID DER BAU-, VERKEHRS- UND ENERGIEDIREKTION RA Nr. 110/2018/117 "
            "Bern, 4. Dezember 2018 in der Beschwerdesache zwischen Herrn A. und Herrn B. "
            + "betreffend die Verfügung der Baubewilligungsbehörde. " * 60)
    direct = _row("be_bvd_110_2018_117", "be_bvd", text, docket="110 2018 117", date="2018-12-04")
    es = dict(_row("be_bvd_110_2018_117", "be_bvd", text + " Seite 1/9", docket="110 2018 117",
                   date="2017-03-09"), source="entscheidsuche")
    c = _db(tmp_path, direct, es)                     # direct-first, as import_jsonl orders shards
    assert _ids(c) == ["be_bvd_110_2018_117", "be_bvd_110_2018_117_d20170309"]
    counts = build_fts5._fold_federation_date_twins(c)
    assert counts.get("folded") == 1
    assert _ids(c) == ["be_bvd_110_2018_117"]
    assert c.execute("SELECT decision_date FROM decisions").fetchone()[0] == "2018-12-04"
    assert _aliases(c)["be_bvd_110_2018_117_d20170309"] == "be_bvd_110_2018_117"
