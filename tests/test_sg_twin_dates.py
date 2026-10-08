"""St. Gallen rulings held twice with two dates (user report 2026-10-07).

The 2026-03-12 date pass (scripts/repair_decision_dates.py --all) rewrote SG
rows with the first "Urteil/Entscheid vom <date>" of their text: VZ.2004.35
took the lower court's 10 August 2004, BZ.2006.83 the appeal's 4 July 2008.
Each ruling is also held twice (sg_gerichte from entscheidsuche and the direct
sg_publikationen row), and the cross-court dedup keys on docket + date, so the
misdated copy survived beside the right one and `cite` gave two dates.

Fixtures are abridged from the served rows.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
for p in (REPO, REPO / "scripts"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import build_fts5  # noqa: E402
import restore_sg_dates as r  # noqa: E402
from db_schema import SCHEMA_SQL  # noqa: E402

VZ_REGESTE = (
    "Art. 9 BV (SR 101); Art. 8 ZGB (SR 210); Art. 257g OR (SR 220). Beweislastverteilung in der "
    "Frage der Rechtzeitigkeit der Meldung eines Mangels ... (Kantonsgericht, Präsidentin der "
    "III. Zivilkammer, 14. Februar 2005, VZ.2004.35)."
)
BZ_REGESTE = (
    "Vertragsqualifikation: Heimarbeitsvertrag oder selbständige Erwerbstätigkeit ... Abweisung der "
    "Berufung (Kantonsgericht St. Gallen, III. Zivilkammer, 16. Oktober 2007, BZ.2006.83).Das "
    "Kassationsgericht hat eine gegen diesen Entscheid erhobene Nichtigkeitsbeschwerde mit Entscheid "
    "vom 4. Juli 2008 abgewiesen, soweit es darauf eintrat. Das Bundesgericht hat eine gegen diese "
    "beiden Entscheide erhobene Beschwerde abgewiesen (Urteil 4A_553 neues Fenster vom 9. Februar 2009)."
)

DIRECT_VZ = {
    "decision_id": "sg_publikationen_VZ.2004.35", "court": "sg_kantonsgericht", "canton": "SG",
    "docket_number": "VZ.2004.35", "decision_date": "2004-08-10", "publication_date": "2005-02-14",
    "title": "Entscheid Kantonsgericht, 14.02.2005", "regeste": VZ_REGESTE, "language": "de",
    "full_text": ("Präsidentin der III. Zivilkammer, 14. Februar 2005\n Erwägungen: ... Das angerufene "
                  "Gericht schützte die Klage mit Urteil vom 10. August 2004 im Betrag ... ") * 20,
    "source_url": "https://publikationen.sg.ch/rechtsprechung-gerichte-detail/4405/",
    "date_extraction": {"method": "header_de", "raw_match": "Urteil vom 10. August 2004",
                        "metadata_date": "2005-02-14"},
}
ES_VZ = {
    "decision_id": "sg_gerichte_VZ.2004.35", "court": "sg_gerichte", "canton": "SG",
    "docket_number": "VZ.2004.35", "decision_date": "2005-02-14", "publication_date": None,
    "regeste": VZ_REGESTE, "language": "de", "source": "entscheidsuche",
    "full_text": ("St.Gallen Kantonsgericht Zivilkammern (inkl. Einzelrichter) 14.02.2005 VZ.2004.35\n\n"
                  + VZ_REGESTE),
    "source_url": "https://publikationen.sg.ch/rechtsprechung-gerichte?publication=4405",
}
DIRECT_BZ = {
    "decision_id": "sg_publikationen_BZ.2006.83", "court": "sg_kantonsgericht", "canton": "SG",
    "docket_number": "BZ.2006.83", "decision_date": "2007-10-16", "publication_date": "2007-10-16",
    "regeste": BZ_REGESTE, "language": "de", "full_text": "Erwägungen ... " * 400,
    "source_url": "https://publikationen.sg.ch/rechtsprechung-gerichte-detail/4099/",
}
ES_BZ = {
    "decision_id": "sg_gerichte_BZ.2006.83", "court": "sg_gerichte", "canton": "SG",
    "docket_number": "BZ.2006.83", "decision_date": "2008-07-04", "publication_date": "2007-10-16",
    "regeste": BZ_REGESTE, "language": "de", "source": "entscheidsuche",
    "full_text": ("St.Gallen Kantonsgericht Zivilkammern (inkl. Einzelrichter) 16.10.2007 BZ.2006.83\n\n"
                  + BZ_REGESTE),
    "source_url": "https://publikationen.sg.ch/rechtsprechung-gerichte?publication=4099",
    "date_extraction": {"method": "header_de", "raw_match": "Entscheid vom 4. Juli 2008",
                        "metadata_date": "2007-10-16"},
}


def test_own_date_comes_from_the_trailer_or_the_kopfzeile_naming_the_docket():
    assert r.own_date(DIRECT_VZ) == ("2005-02-14", "regeste_trailer")
    assert r.own_date(ES_BZ) == ("2007-10-16", "regeste_trailer")
    assert r.kopfzeile_date(ES_BZ["full_text"], "BZ.2006.83") == "2007-10-16"
    # a trailer for another docket is not this ruling's date
    assert r.trailer_date(VZ_REGESTE, "VZ.2004.36") is None
    # the appeal's date in the same Regeste is never taken
    assert r.trailer_date(BZ_REGESTE, "BZ.2006.83") == "2007-10-16"


def test_disagreeing_sources_leave_the_row_alone():
    row = dict(ES_BZ, full_text=ES_BZ["full_text"].replace("16.10.2007", "17.10.2007", 1))
    assert r.own_date(row) == (None, "conflict")
    assert r.decide(row, include_unstamped=True) == ("conflict", None)


def test_only_rows_the_0312_pass_rewrote_change_by_default():
    unstamped = dict(DIRECT_VZ)
    del unstamped["date_extraction"]
    assert r.decide(unstamped, include_unstamped=False) == ("unstamped_disagree", None)
    assert r.decide(unstamped, include_unstamped=True)[1] == "2005-02-14"
    assert r.decide(ES_VZ, include_unstamped=False) == ("agrees", None)


def test_shard_restore_rewrites_and_is_idempotent(tmp_path):
    shard = tmp_path / "sg_publikationen.jsonl"
    other = {"decision_id": "zh_x", "court": "zh_obergericht", "decision_date": "2004-08-10"}
    rows = [DIRECT_VZ, DIRECT_BZ, other]
    shard.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in rows))
    stats = r.run(shard, apply=True, examples=0)
    assert stats["sg_rows"] == 2 and stats["other_court"] == 1
    assert stats["rows_changed"] == 1 and stats["agrees"] == 1
    out = [json.loads(line) for line in shard.read_text().splitlines()]
    assert out[0]["decision_date"] == "2005-02-14"
    assert out[0]["publication_date"] is None          # the 03-12 pass had planted it
    assert out[0]["date_restore"]["from"] == "2004-08-10"
    assert out[1] == DIRECT_BZ and out[2] == other
    assert r.run(shard, apply=True, examples=0)["rows_changed"] == 0


def _db(tmp_path, *rows):
    c = sqlite3.connect(str(tmp_path / "d.db"))
    c.executescript(SCHEMA_SQL)
    for row in rows:
        row = {k: v for k, v in row.items() if k not in ("date_extraction", "source")}
        assert build_fts5.insert_decision(c, row)
    c.commit()
    return c


def test_once_dated_alike_the_twins_fold_into_the_full_text_and_the_old_id_resolves(tmp_path):
    fixed_es_bz = dict(ES_BZ, decision_date="2007-10-16")
    c = _db(tmp_path, DIRECT_BZ, fixed_es_bz)
    assert build_fts5._cross_court_dedup(c) == 1
    assert [x[0] for x in c.execute("SELECT decision_id FROM decisions")] == ["sg_publikationen_BZ.2006.83"]
    alias = c.execute("SELECT decision_id, source FROM decision_id_aliases "
                      "WHERE previous_id='sg_gerichte_BZ.2006.83'").fetchone()
    assert alias == ("sg_publikationen_BZ.2006.83", "cross_court_dedup")

    import mcp_server as m
    c.row_factory = sqlite3.Row
    assert m._lookup_previous_id(c, "sg_gerichte_BZ.2006.83") == "sg_publikationen_BZ.2006.83"


def test_misdated_twins_are_not_folded(tmp_path):
    # the state the report found: the dedup key holds the date, so both survive
    c = _db(tmp_path, DIRECT_BZ, ES_BZ)
    assert build_fts5._cross_court_dedup(c) == 0
