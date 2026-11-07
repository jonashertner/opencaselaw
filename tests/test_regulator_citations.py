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


# --- the other federal authorities ---------------------------------------------------

def _rec(court, docket, date="2024-05-21", did=None):
    return {"decision_id": did or f"{court}_x", "court": court, "canton": "CH",
            "docket_number": docket, "decision_date": date, "language": "de"}


@pytest.mark.parametrize("court, docket, de, fr", [
    ("weko", "Baustoffe und Deponien Bern", "WEKO Baustoffe und Deponien Bern vom 21. Mai 2024",
     "COMCO Baustoffe und Deponien Bern du 21 mai 2024"),
    ("finma", "2024-26", "FINMA 2024-26 vom 21. Mai 2024", "FINMA 2024-26 du 21 mai 2024"),
    ("ubi", "b.1057", "UBI b.1057 vom 21. Mai 2024", "AIEP b.1057 du 21 mai 2024"),
    ("postcom", "VFG-21-2025", "PostCom VFG-21-2025 vom 21. Mai 2024", "PostCom VFG-21-2025 du 21 mai 2024"),
    ("esbk", "62-2021-021-01", "ESBK 62-2021-021-01 vom 21. Mai 2024", "CFMJ 62-2021-021-01 du 21 mai 2024"),
    ("rab", "2020-01", "RAB 2020-01 vom 21. Mai 2024", "ASR 2020-01 du 21 mai 2024"),
    ("ch_bundesrat", "2025-06-131", "Bundesrat 2025-06-131 vom 21. Mai 2024",
     "Conseil fédéral 2025-06-131 du 21 mai 2024"),
    ("preisueberwacher", "bookingcom-bv-verfuegung-vom-20-mai-2025",
     "Preisüberwachung bookingcom-bv-verfuegung-vom-20-mai-2025 vom 21. Mai 2024",
     "Surveillance des prix bookingcom-bv-verfuegung-vom-20-mai-2025 du 21 mai 2024"),
])
def test_authorities_cite_by_their_own_label(court, docket, de, fr):
    c = m._build_citation_strings(_rec(court, docket))
    assert c["citation_string_de"] == de
    assert c["citation_string_fr"] == fr


def test_every_regulator_has_a_label_and_no_court_word():
    for court in m._REGULATOR_COURTS:
        c = m._build_citation_strings(_rec(court, "1/2024"))
        for k in ("citation_string_de", "citation_string_fr", "citation_string_it"):
            assert not c[k].startswith(("Gericht", "Tribunal", "Tribunale")), (court, c[k])


def test_emark_cites_by_series_year_and_number():
    c = m._build_citation_strings(_rec("emark", "EMARK-2006-33", "2006-11-03"), pinpoint="4.1")
    assert c["citation_string_de"] == "EMARK 2006 Nr. 33 vom 3. November 2006, E. 4.1"
    assert c["citation_string_fr"] == "JICRA 2006 n° 33 du 3 novembre 2006, consid. 4.1"
    assert c["citation_string_it"] == "GICRA 2006 n. 33 del 3 novembre 2006, consid. 4.1"


@pytest.mark.parametrize("ref, parts", [
    ("EMARK 2006 Nr. 33", (("emark",), "EMARK-2006-33", None)),
    ("JICRA 2006 n° 33 du 3 novembre 2006", (("emark",), "EMARK-2006-33", "2006-11-03")),
    ("GICRA 1995/7", (("emark",), "EMARK-1995-7", None)),
    ("Gericht CH EMARK-2006-33 vom 3. November 2006", (("emark",), "EMARK-2006-33", "2006-11-03")),
    ("WEKO Swisscom WAN-Anbindung II vom 25. August 2025", (("weko",), "Swisscom WAN-Anbindung II", "2025-08-25")),
    ("Conseil fédéral 2025-06-131", (("ch_bundesrat",), "2025-06-131", None)),
])
def test_authority_reference_parts(ref, parts):
    assert m._regulator_reference(ref) == parts


def test_emark_and_authority_references_resolve(tmp_path, monkeypatch):
    path = tmp_path / "decisions.db"
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE decisions(decision_id TEXT PRIMARY KEY, docket_number TEXT, court TEXT, "
              "canton TEXT, decision_date TEXT)")
    c.execute("CREATE INDEX idx_decisions_docket ON decisions(docket_number)")
    c.executemany("INSERT INTO decisions VALUES (?,?,?,?,?)", [
        ("emark_EMARK-2006-33", "EMARK-2006-33", "emark", "CH", "2006-11-03"),
        ("rab_2020-01", "2020-01", "rab", "CH", "2020-03-01"),
        ("finma_2020-01", "2020-01", "finma", "CH", "2020-06-01"),
    ])
    c.commit()
    c.close()

    def get_db():
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        return conn

    monkeypatch.setattr(m, "get_db", get_db)
    assert m._resolve_decision_id_strict("EMARK 2006 Nr. 33") == "emark_EMARK-2006-33"
    assert m._resolve_decision_id_strict("JICRA 2006 n° 33 du 3 novembre 2006") == "emark_EMARK-2006-33"
    assert m._resolve_decision_id_strict("RAB 2020-01") == "rab_2020-01"
    assert m._resolve_decision_id_strict("FINMA 2020-01") == "finma_2020-01"
    assert m._resolve_decision_id_strict("ASR 2020-01 du 1 mars 2020") == "rab_2020-01"


# --- rule_statement ------------------------------------------------------------------

@pytest.mark.parametrize("text, rule", [
    ("Sachverhalt:\n\n\n\nA.\nDie Bank ist eine AG. Mehr.", "Die Bank ist eine AG. Mehr."),
    ("En fait :\nA. Le recourant a recouru.", "A. Le recourant a recouru."),
    ("Das Bundesgericht zieht in Erwägung: X. Y.", "Das Bundesgericht zieht in Erwägung: X. Y."),
    ("Sachverhalt:\n\nErwägungen:", None),
])
def test_rule_statement_skips_bare_headings(text, rule):
    assert m._rule_statement({"full_text": text, "language": "de"}) == rule
