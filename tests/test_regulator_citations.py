"""UEK and SIX rulings cite by the body's own label, never 'Gericht CH', and both
the new strings and the 'Gericht CH ...' ones handed out before resolve back to
the ruling, never to another court's ruling under the same docket."""
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mcp_server as m

UEK = {"decision_id": "uek_500_01", "court": "uek", "canton": "CH",
       "docket_number": "500/01", "decision_date": "2011-12-22", "language": "de"}
SIX = {"decision_id": "six_ser_SaKo-IV_2023", "court": "six_ser", "canton": "CH",
       "docket_number": "SaKo-IV/2023", "decision_date": "2023-05-11", "language": "de"}
SIX_ARB = {"decision_id": "six_ser_Schiedsgericht SaKo-IV_2023", "court": "six_ser", "canton": "CH",
           "docket_number": "Schiedsgericht SaKo-IV/2023", "decision_date": "2025-02-27", "language": "de"}
ZH = {"decision_id": "zh_obergericht_500_01", "court": "zh_obergericht", "canton": "ZH",
      "docket_number": "500/01", "decision_date": "2015-03-01", "language": "de"}


def test_uek_cites_as_uek_and_copa():
    c = m._build_citation_strings(UEK, pinpoint="3.2")
    assert c["citation_string_de"] == "UEK 500/01 vom 22. Dezember 2011, E. 3.2"
    assert c["citation_string_fr"] == "COPA 500/01 du 22 décembre 2011, consid. 3.2"
    assert c["citation_string_it"] == "COPA 500/01 del 22 dicembre 2011, consid. 3.2"


def test_six_cites_with_the_instance_the_docket_names():
    assert m._build_citation_strings(SIX)["citation_string_de"] == "SIX SaKo-IV/2023 vom 11. Mai 2023"
    assert (m._build_citation_strings(SIX_ARB)["citation_string_fr"]
            == "SIX Schiedsgericht SaKo-IV/2023 du 27 février 2025")


@pytest.mark.parametrize("rec", [UEK, SIX, SIX_ARB])
def test_no_court_label_for_a_regulator(rec):
    c = m._build_citation_strings(rec)
    assert not any(w in c[k] for k in c if k.startswith("citation")
                   for w in ("Gericht", "Tribunal", "Tribunale"))


@pytest.mark.parametrize("ref, parts", [
    ("UEK 500/01 vom 22. Dezember 2011", (("uek",), "500/01", "2011-12-22")),
    ("COPA 500/01 du 22 décembre 2011, consid. 3", (("uek",), "500/01", "2011-12-22")),
    ("SIX Schiedsgericht SaKo-IV/2023", (("six_ser",), "Schiedsgericht SaKo-IV/2023", None)),
    ("Gericht CH 500/01 vom 22. Dezember 2011", (None, "500/01", "2011-12-22")),
    ("siehe UEK 500/01", None),        # a later mention is a cross-reference
    ("BGer 4A_1/2020", None),
])
def test_regulator_reference_parts(ref, parts):
    assert m._regulator_reference(ref) == parts


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "decisions.db"
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE decisions(decision_id TEXT PRIMARY KEY, docket_number TEXT, court TEXT, "
              "canton TEXT, decision_date TEXT)")
    c.execute("CREATE INDEX idx_decisions_docket ON decisions(docket_number)")
    c.executemany("INSERT INTO decisions VALUES (?,?,?,?,?)", [
        (r["decision_id"], r["docket_number"], r["court"], r["canton"], r["decision_date"])
        for r in (UEK, SIX, SIX_ARB, ZH)])
    c.commit()
    c.close()

    def get_db():
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        return conn

    monkeypatch.setattr(m, "get_db", get_db)


@pytest.mark.parametrize("ref, did", [
    ("UEK 500/01 vom 22. Dezember 2011", "uek_500_01"),
    ("COPA 500/01 del 22 dicembre 2011", "uek_500_01"),
    ("UEK 500/01", "uek_500_01"),                                   # not the ZH row of the same docket
    ("Gericht CH 500/01 vom 22. Dezember 2011", "uek_500_01"),      # string handed out before the fix
    ("SIX SaKo-IV/2023 vom 11. Mai 2023, E. 4", "six_ser_SaKo-IV_2023"),
    ("SIX Schiedsgericht SaKo-IV/2023 vom 27. Februar 2025", "six_ser_Schiedsgericht SaKo-IV_2023"),
    ("Tribunal CH SaKo-IV/2023 du 11 mai 2023", "six_ser_SaKo-IV_2023"),
])
def test_written_forms_resolve_lenient_and_strict(db, ref, did):
    assert m._resolve_decision_id(ref) == did
    assert m._resolve_decision_id_strict(ref) == did


def test_a_wrong_date_or_body_resolves_nothing_strict(db):
    assert m._resolve_decision_id_strict("UEK 500/01 vom 23. Dezember 2011") is None
    assert m._resolve_decision_id_strict("SIX 500/01") is None


def test_cite_identity_accepts_old_and_new_strings():
    assert m._cite_identity("Gericht CH 500/01 vom 22. Dezember 2011", dict(UEK)) == {
        "method": "exact_docket", "label": "500/01"}
    assert m._cite_identity("UEK 500/01 vom 22. Dezember 2011", dict(UEK))["method"] in (
        "exact_server_citation", "exact_docket")
    assert m._cite_identity("UEK 500/01 vom 22. Dezember 2011", dict(ZH)) is None
