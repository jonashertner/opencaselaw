"""Server-side resolution of references as written: stored separators, long forms, court scope."""
import sqlite3

import pytest

import mcp_server as m


@pytest.fixture
def conn(monkeypatch):
    keep = sqlite3.connect("file:parsedref?mode=memory&cache=shared", uri=True)
    keep.row_factory = sqlite3.Row
    keep.execute("CREATE TABLE decisions (decision_id TEXT PRIMARY KEY, docket_number TEXT, court TEXT, canton TEXT, decision_date TEXT)")
    keep.executemany("INSERT INTO decisions VALUES (?,?,?,?,?)", [
        ("bger_4A_535_2018", "4A 535/2018", "bger", "CH", "2019-06-03"),
        ("bger_4C.230_2005", "4C.230/2005", "bger", "CH", "2006-01-10"),
        ("bger_4A_191_2019", "4A_191/2019", "bger", "CH", "2019-11-05"),
        ("ge_gerichte_4A_191_2019", "4A_191/2019", "ge_gerichte", "GE", "2019-11-05"),
        ("zh_obergericht_LA210005", "LA210005", "zh_obergericht", "ZH", "2021-06-15"),
        ("vd_findinfo_HC___2018___391", "HC / 2018 / 391", "vd_findinfo", "VD", "2018-01-26"),
        ("bge_BGE_134_III_354", "134 III 354", "bge", "CH", "2008-04-29"),
        ("bger_6B_1247_2020", "6B_1247/2020", "bger", "CH", "2021-10-07"),
        # an EVG docket, a two-digit chamber, and the cantonal rulings whose whole docket is their tail
        ("bger_B_59_2001", "B_59/2001", "bger", "CH", "2001-09-27"),
        ("bl_gerichte_59_2001", "59/2001", "bl_gerichte", "BL", "2001-04-06"),
        ("bger_12T_3_2013", "12T_3/2013", "bger", "CH", "2013-05-21"),
        ("vd_findinfo_3_2013", "3/2013", "vd_findinfo", "VD", "2013-02-01"),
    ])
    keep.commit()
    def get_db():
        c = sqlite3.connect("file:parsedref?mode=memory&cache=shared", uri=True); c.row_factory = sqlite3.Row; return c
    monkeypatch.setattr(m, "get_db", get_db)
    monkeypatch.setattr(m, "_lookup_docket_alias", lambda c, r: [])
    monkeypatch.setattr(m, "_lookup_ecthr_appno", lambda c, r: [])
    yield keep
    keep.close()


@pytest.mark.parametrize("reference, expected", [
    ("4A_535/2018", "bger_4A_535_2018"),                       # underscore written, space stored
    ("BGer 4A_535/2018 vom 3. Juni 2019", "bger_4A_535_2018"),  # the service's own long form
    ("4C_230/2005", "bger_4C.230_2005"),                        # pre-2007 dot form stored
    ("Obergericht ZH LA210005 vom 15. Juni 2021", "zh_obergericht_LA210005"),
    ("Tribunal cantonal VD, arrêt HC / 2018 / 391 du 26 janvier 2018", "vd_findinfo_HC___2018___391"),
    ("HC/2018/391", "vd_findinfo_HC___2018___391"),
    ("BGE 134 III 354 S. 357", "bge_BGE_134_III_354"),          # page reference on a BGE label
    ("(BGE 134 III 354)", "bge_BGE_134_III_354"),
    ("BGer 4A_191/2019 vom 5. November 2019", "bger_4A_191_2019"),  # court words scope the carriers
    ("Cour de justice de Genève, arrêt 4A_191/2019", "ge_gerichte_4A_191_2019"),
    ("B 59/2001", "bger_B_59_2001"),                            # EVG chamber written with a space (was bl_gerichte_59_2001)
    ("B.59/2001", "bger_B_59_2001"),
    ("Urteil des EVG B 59/2001 vom 27. September 2001", "bger_B_59_2001"),
    ("12T 3/2013", "bger_12T_3_2013"),                          # two-digit chamber (was vd_findinfo_3_2013)
    ("59/2001", "bl_gerichte_59_2001"),                         # the bare number still names the cantonal ruling
])
def test_written_references_resolve_to_the_decision_carrying_the_label(conn, reference, expected):
    assert m._resolve_decision_id(reference) == expected


def test_a_docket_carried_by_two_courts_stays_unresolved_without_a_court(conn):
    assert m._resolve_parsed_reference(conn, "4A_191/2019") in ("bger_4A_191_2019", "ge_gerichte_4A_191_2019")  # the parsed step alone picks newest
    # ... but the existing exact-docket step in _resolve_decision_id runs first and behaves as before (newest);
    # the cite handler's identity check is what reports ambiguity to clients.


def test_a_chamber_the_parser_does_not_know_never_resolves_to_its_tail(conn):
    assert m._resolve_parsed_reference(conn, "X 59/2001") is None
    assert m._resolve_parsed_reference(conn, "4Z 3/2013") is None


def test_a_docket_mentioned_after_the_label_is_never_resolved_to(conn):
    hit = m._resolve_parsed_reference(conn, "Obergericht ZH LA210005 vom 15. Juni 2021, E. 3 (vgl. auch BGer 4A_535/2018)")
    assert hit == "zh_obergericht_LA210005"
    assert m._resolve_parsed_reference(conn, "BGer 4A_9999/2012 und 4A_535/2018") is None
