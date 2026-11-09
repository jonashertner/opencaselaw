"""Pass 2 (date-agnostic dedup) must leave an entscheidsuche date-twin to the
federation fold, and keep every id it drops resolvable.

Build of 2026-11-09: be_bvd and be_steuerrekurs direct rows took the live
entscheidsuche ids (be_bvd_110_2018_117). The es copy of such a ruling, under
another date, was imported as be_bvd_110_2018_117_d20170309. Pass 2 runs before
_fold_federation_date_twins, kept the LONGER row (the es copy, longer by page
furniture) and deleted the direct row with no alias: 407 be_bvd and 10
be_steuerrekurs ids stopped resolving and the es dates were served. Replayed on
the production shards, this fix gives 0 unresolvable ids, 417 folded.
"""
from __future__ import annotations

import sqlite3

import build_fts5
from db_schema import SCHEMA_SQL

TEXT = ("ENTSCHEID DER BAU-, VERKEHRS- UND ENERGIEDIREKTION RA Nr. 110/2018/117 "
        "Bern, 4. Dezember 2018 in der Beschwerdesache zwischen Herrn A. und Herrn B. "
        + "betreffend die Verfügung der Baubewilligungsbehörde. " * 60)


def _row(decision_id, date, text, source=None, docket="110 2018 117"):
    r = {"decision_id": decision_id, "court": "be_bvd", "canton": "BE",
         "docket_number": docket, "decision_date": date, "language": "de",
         "title": "Split-Wärmepumpe", "regeste": None, "full_text": text,
         "source_url": "https://www.bvd-entscheide.apps.be.ch/x"}
    if source:
        r["source"] = source
    return r


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
    return {p: (d, s) for p, d, s in c.execute(
        "SELECT previous_id, decision_id, source FROM decision_id_aliases")}


def test_build_order_keeps_the_direct_row_and_its_date(tmp_path):
    c = _db(tmp_path,
            _row("be_bvd_110_2018_117", "2018-12-04", TEXT),
            _row("be_bvd_110_2018_117", "2017-03-09", TEXT + " Seite 1/9 Seite 2/9",
                 source="entscheidsuche"))
    assert _ids(c) == ["be_bvd_110_2018_117", "be_bvd_110_2018_117_d20170309"]
    assert build_fts5._dedup_decisions(c) == 0          # left to the fold
    assert build_fts5._fold_federation_date_twins(c).get("folded") == 1
    assert _ids(c) == ["be_bvd_110_2018_117"]
    assert c.execute("SELECT decision_date FROM decisions").fetchone()[0] == "2018-12-04"
    assert _aliases(c)["be_bvd_110_2018_117_d20170309"] == ("be_bvd_110_2018_117", "federation_twin")


def test_third_copy_is_aliased_to_a_row_that_survives(tmp_path):
    # A second direct row of the same ruling under another id (a re-keyed
    # docket spelling): Pass 2 folds it into the base row, not into the es
    # twin the fold deletes next.
    c = _db(tmp_path,
            _row("be_bvd_110_2018_117", "2018-12-04", TEXT),
            _row("be_bvd_110_2018_117", "2017-03-09", TEXT + " Seite 1/9", source="entscheidsuche"),
            _row("be_bvd_110_2018_117_alt", "2018-12-05", TEXT[:-200]))
    assert build_fts5._dedup_decisions(c) == 1
    build_fts5._fold_federation_date_twins(c)
    assert _ids(c) == ["be_bvd_110_2018_117"]
    a = _aliases(c)
    assert a["be_bvd_110_2018_117_alt"] == ("be_bvd_110_2018_117", "dedup_pass2")
    assert a["be_bvd_110_2018_117_d20170309"][0] == "be_bvd_110_2018_117"


def test_direct_date_twins_stay_pass2s(tmp_path):
    # Two direct rows under one id with two dates: no es copy for the fold to
    # take, so Pass 2 dedups them as before, and the dropped id resolves.
    c = _db(tmp_path,
            _row("be_bvd_110_2018_117", "2018-12-04", TEXT),
            _row("be_bvd_110_2018_117", "2018-12-07", TEXT + " Nachtrag"))
    assert build_fts5._dedup_decisions(c) == 1
    assert _ids(c) == ["be_bvd_110_2018_117_d20181207"]
    assert _aliases(c)["be_bvd_110_2018_117"] == ("be_bvd_110_2018_117_d20181207", "dedup_pass2")


def test_distinct_rulings_of_a_docket_stay(tmp_path):
    c = _db(tmp_path,
            _row("be_bvd_110_2018_117", "2018-12-04", TEXT),
            _row("be_bvd_110_2018_117", "2018-06-01",
                 "Zwischenverfügung über die aufschiebende Wirkung. " * 40, source="entscheidsuche"))
    assert build_fts5._dedup_decisions(c) == 0
    build_fts5._fold_federation_date_twins(c)
    assert len(_ids(c)) == 2
