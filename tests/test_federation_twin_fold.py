"""Federation twins and cantonal copies of BGer rulings (2026-11-04).

The entscheidsuche shards hold second copies of rulings the direct scrapers
hold, under another date (BVGer: the date of the SEM decision under appeal;
NE: years before the docket; SO: the right date where the direct crawl of
2026-02 took the lower instance's). insert_decision keeps such a copy as
<id>_dYYYYMMDD, a "second ruling" under the docket. 29,170 rows on the HF
mirror. _fold_federation_date_twins folds the copy into its base row when the
two are one ruling, dates the base from the copy's header and keeps the copy's
id as an alias; another ruling of the docket stays.

justice.ge.ch lists BGer rulings under their BGer docket; ge_gerichte stored
522 of them as Geneva rulings ("Gericht GE 4A_645/2011").
_fold_cantonal_copies_of_federal_rulings folds them into the bger rows.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import build_fts5 as b  # noqa: E402
from db_schema import SCHEMA_SQL  # noqa: E402
from scrapers.cantonal.so_gerichte import RE_LONG_DATE  # noqa: E402

RULING = (
    "Es wirken mit: Präsidentin Scherrer Reber, Oberrichter Stöckli, Oberrichter "
    "Müller, Gerichtsschreiberin Kaufmann. In Sachen A.___, vertreten durch Advokat "
    "Peter Bürkli, Beschwerdeführerin, gegen KESB Thal-Gäu, Beschwerdegegnerin, "
    "betreffend Anordnung eines Gutachtens zieht das Verwaltungsgericht in Erwägung: "
)


def _body(seed: str, n: int = 60) -> str:
    return " ".join(f"{seed} Erwägung {i}: die Vorinstanz hat den Sachverhalt "
                    f"im Punkt {i * 7 % 13} richtig festgestellt." for i in range(n))


def _row(did, court, docket, date, text, source=None, regeste=None):
    return {"decision_id": did, "court": court, "canton": "CH", "docket_number": docket,
            "decision_date": date, "language": "de", "title": None, "regeste": regeste,
            "full_text": text, "source_url": "https://example.invalid/" + did,
            "source": source}


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.executescript(SCHEMA_SQL)
    yield c
    c.close()


def _ids(c):
    return {r[0] for r in c.execute("SELECT decision_id FROM decisions")}


def _date(c, did):
    return c.execute("SELECT decision_date FROM decisions WHERE decision_id = ?", (did,)).fetchone()[0]


def _alias(c, prev):
    return c.execute("SELECT decision_id, source FROM decision_id_aliases WHERE previous_id = ?",
                     (prev,)).fetchone()


def _insert_pair(c, court, docket, base_date, base_text, twin_date, twin_text, twin_regeste=None):
    did = f"{court}_{docket.replace('/', '_')}"
    assert b.insert_decision(c, _row(did, court, docket, base_date, base_text))
    assert b.insert_decision(c, _row(did, court, docket, twin_date, twin_text,
                                     source="entscheidsuche", regeste=twin_regeste))
    twin = f"{did}_d{twin_date.replace('-', '')}"
    assert twin in _ids(c)          # the import made it a "second ruling"
    return did, twin


# ------------------------------------------------------------ helpers

def test_header_date_needs_the_rows_own_docket():
    t = "Solothurn Versicherungsgericht 25.07.2018 VSBES.2018.30\nSoleure ...\n\nUrteil"
    assert b._federation_header_date(t, "VSBES.2018.30") == "2018-07-25"
    assert b._federation_header_date(t, "VSBES.2018.3") is None
    assert b._federation_header_date(
        "Bundesgericht II. Abteilung 31.05.2022 2C 219/2022 (2C_219/2022)", "2C_219/2022") == "2022-05-31"
    assert b._federation_header_date("Bundesverwal tungsgeri cht\nAbteilung V\nE-4196/2023", "E-4196/2023") is None
    assert b._federation_header_date("X 31.02.2022 A.1", "A.1") is None   # no such day


@pytest.mark.parametrize("text,want", [
    ("Urteilvom25. Mai 2020 Es wirken mit", "2020-05-25"),
    ("Obergericht Zivilkammer Beschluss vom 15. Oktober 2024", "2024-10-15"),
    ("Tribunal cantonal, arrêt du 9 mai 2023, Cour civile", "2023-05-09"),
    ("Tribunale d'appello, sentenza del 3 maggio 2017", "2017-05-03"),
    ("IV-Rente (Einspracheentscheid vom 21. Dezember 2017) zieht das", None),
    ("Mit Verfügung vom 7. Juni 2024 verweigerte das SEM", None),
    ("x" * 500 + " Urteil vom 4. Juli 2022", None),                     # not the opening
])
def test_opening_ruling_date(text, want):
    assert b._opening_ruling_date(text) == want


# ---------------------------------------------- federation twin fold

def test_bvger_copy_dated_with_the_appealed_decision_is_folded(conn):
    text = RULING + _body("Asyl")
    base, twin = _insert_pair(
        conn, "bvger", "D-1245/2022", "2022-03-18", text,
        "2022-03-11", "Bundesverwaltungsgericht 18.03.2022 D-1245/2022\n\n" + text.replace(",", " ,"))
    counts = b._fold_federation_date_twins(conn)
    assert counts["folded"] == 1 and counts["folded_same_date"] == 1
    assert twin not in _ids(conn) and _date(conn, base) == "2022-03-18"
    assert _alias(conn, twin) == (base, "federation_twin")


def test_wrong_base_date_is_corrected_from_the_header(conn):
    # so_gerichte: the 2026-02 crawl stored the Einspracheentscheid date.
    text = "Urteil vom 25. Juli 2018 " + RULING + _body("IV-Rente")
    base, twin = _insert_pair(
        conn, "so_gerichte", "VSBES.2018.30", "2017-12-21", text,
        "2018-07-25", "Solothurn Versicherungsgericht 25.07.2018 VSBES.2018.30\n\n" + text)
    counts = b._fold_federation_date_twins(conn)
    assert counts["folded_same_text"] == 1 and counts["redated"] == 1
    assert _date(conn, base) == "2018-07-25"
    key = conn.execute("SELECT canonical_key FROM decisions WHERE decision_id = ?", (base,)).fetchone()[0]
    assert key.endswith("20180725")


def test_differently_rendered_copy_is_folded_on_the_opening_date(conn):
    # Same ruling, texts too different to match (rendering), base date wrong.
    base_text = "Urteilvom3. Dezember 2018 " + RULING + _body("Gutachten")
    twin_text = ("Verwaltungsgericht 03.12.2018 VWBES.2018.337\n\nAnordnung eines Gutachtens "
                 "Verwaltungsgericht Urteil vom 3. Dezember 2018 " + _body("Familienbegleitung"))
    base, twin = _insert_pair(conn, "so_gerichte", "VWBES.2018.337", "2018-05-11", base_text,
                              "2018-12-03", twin_text)
    counts = b._fold_federation_date_twins(conn)
    assert counts["folded_opening_date"] == 1 and _date(conn, base) == "2018-12-03"


def test_truncated_base_gets_the_full_text(conn):
    # ti_gerichte: the direct row stops after ~1,000 characters.
    full = "Sentenza del 16 giugno 2021 " + RULING + _body("Ricorso", 200)
    base, twin = _insert_pair(conn, "ti_gerichte", "35.2021.11", "2021-06-16", full[:900],
                              "2020-07-13", "Tessin Tribunale 16.06.2021 35.2021.11\n" + full,
                              twin_regeste="Regesto")
    counts = b._fold_federation_date_twins(conn)
    assert counts["folded"] == 1 and counts["text_upgraded"] == 1
    text, regeste = conn.execute("SELECT full_text, regeste FROM decisions WHERE decision_id = ?",
                                 (base,)).fetchone()
    assert len(text) > 5000 and regeste == "Regesto"
    # the FTS index follows the upgraded text
    assert conn.execute("SELECT COUNT(*) FROM decisions_fts WHERE decisions_fts MATCH 'Ricorso'").fetchone()[0] == 1


def test_a_wrong_header_date_does_not_move_a_confirmed_base_date(conn):
    # ti 11.1999.150: header 2001-02-19, text "Lugano, 19 febbraio 2000".
    text = "Incarto n. 11.1999.00150 Lugano, 19 febbraio 2000/fb " + RULING + _body("Appello")
    base, twin = _insert_pair(conn, "ti_gerichte", "11.1999.150", "2000-02-19", text,
                              "2001-01-05", "Tessin Tribunale 19.02.2001 11.1999.150\n" + text)
    counts = b._fold_federation_date_twins(conn)
    assert counts["folded"] == 1 and counts["header_date_unconfirmed"] == 1
    assert _date(conn, base) == "2000-02-19"


@pytest.mark.parametrize("text,want", [
    ("Incarto n. 16.2010.47 Lugano 19 luglio 201010/fb In nome", "2010-07-19"),
    ("Urteilvom4. November 2019 Es wirken mit", "2019-11-04"),
    ("Incarto n. 11.1999.00150 Lugano, 19 febbraio 2000/fb", "2000-02-19"),
    # the Einspracheentscheid further down is not the ruling's own date
    ("Es wirken mit: Präsident Flückiger " + "x " * 150 + "(Einspracheentscheid vom 21. Dezember 2017)", None),
    ("Bellinzona 12.03.2004 Sentenza", "2004-03-12"),
])
def test_own_date(text, want):
    assert b._own_date(text) == want


def test_solothurn_wrong_date_in_the_opening_still_yields_to_the_header(conn):
    # The wrong date (the Einspracheentscheid) is itself in the first lines.
    text = ("Urteil vom 25. Juli 2018 Es wirken mit: Präsident X. In Sachen A.___ "
            "betreffend IV-Rente (Einspracheentscheid vom 21. Dezember 2017) " + _body("IV"))
    base, twin = _insert_pair(conn, "so_gerichte", "VSBES.2018.31", "2017-12-21", text,
                              "2018-07-25", "Solothurn Versicherungsgericht 25.07.2018 VSBES.2018.31\n" + text)
    b._fold_federation_date_twins(conn)
    assert _date(conn, base) == "2018-07-25"


def test_another_ruling_of_the_docket_stays_with_its_own_date(conn):
    base_text = ("Nachträglicher Beschluss vom 12. September 2023 zum Beschluss vom 16. August "
                 "2022 " + _body("Kosten"))
    twin_text = ("Solothurn Obergericht Beschwerdekammer 16.08.2022 BKBES.2022.87\n\n"
                 "Beschluss vom 16. August 2022 " + RULING + _body("Massnahme", 120))
    base, twin = _insert_pair(conn, "so_gerichte", "BKBES.2022.87", "2023-09-12", base_text,
                              "2022-06-13", twin_text)
    counts = b._fold_federation_date_twins(conn)
    assert counts.get("folded", 0) == 0 and counts["kept_distinct_redated"] == 1
    assert {base, twin} <= _ids(conn)
    assert _date(conn, base) == "2023-09-12" and _date(conn, twin) == "2022-08-16"


def test_stubs(conn):
    text = RULING + _body("Asyl")
    # header-less OCR stub: the base row's ruling
    meta = (" Abteilung V, Besetzung: Einzelrichterin, Gerichtsschreiber; Parteien: A.___, "
            "Beschwerdeführer, gegen Staatssekretariat für Migration, Vorinstanz; Gegenstand: "
            "Asyl und Wegweisung; Verfügung des SEM. Volltext als PDF beim Gericht abrufbar.")
    base, twin = _insert_pair(conn, "bvger", "E-4196/2023", "2023-09-01", text,
                              "2023-06-29", "Bundesverwal tungsgeri cht E-4196/2023" + meta)
    # header stub naming another date, base opening silent: may be another ruling
    base2, twin2 = _insert_pair(conn, "bvger", "E-1/2023", "2023-09-01", text,
                                "2023-05-01", "Bundesverwaltungsgericht 02.05.2023 E-1/2023\n" + meta)
    counts = b._fold_federation_date_twins(conn)
    assert counts["folded_stub"] == 1 and twin not in _ids(conn)
    assert twin2 in _ids(conn)


def test_direct_second_rulings_are_not_touched(conn):
    # B-1092/2009: two real rulings from the direct scraper, no federation copy.
    assert b.insert_decision(conn, _row("bvger_B-1092_2009", "bvger", "B-1092/2009", "2009-02-20",
                                        "Zwischenverfügung " + _body("A", 20)))
    assert b.insert_decision(conn, _row("bvger_B-1092_2009", "bvger", "B-1092/2009", "2010-01-05",
                                        "Urteil " + _body("B", 80)))
    assert b._fold_federation_date_twins(conn) == {}
    assert {"bvger_B-1092_2009", "bvger_B-1092_2009_d20100105"} <= _ids(conn)


def test_fold_is_idempotent(conn):
    text = RULING + _body("Asyl")
    _insert_pair(conn, "bvger", "D-2/2022", "2022-03-18", text, "2022-03-11",
                 "Bundesverwaltungsgericht 18.03.2022 D-2/2022\n" + text)
    assert b._fold_federation_date_twins(conn)["folded"] == 1
    assert b._fold_federation_date_twins(conn) == {}


def test_swap_gate_counts_the_folded_rows(tmp_path):
    live, new = tmp_path / "live.db", tmp_path / "new.db"
    text = RULING + _body("Asyl")
    for path, fold in ((live, False), (new, True)):
        c = sqlite3.connect(str(path))
        c.executescript(SCHEMA_SQL)
        _insert_pair(c, "ne_gerichte", "CC.2008.93", "2009-08-24", text, "1993-07-09",
                     "Neuchâtel Tribunal Cantonal 24.08.2009 CC.2008.93\n" + text)
        if fold:
            b._fold_federation_date_twins(c)
        c.commit()
        c.close()
    assert b._rows_answered_by_alias(new, live, "ne_gerichte") == 1


# ------------------------------------- BGer rulings in cantonal feeds

def test_geneva_copy_of_a_bger_ruling_is_folded(conn):
    assert b.insert_decision(conn, _row("bger_4A_645_2011", "bger", "4A_645/2011", "2012-01-27", "Urteil " + _body("Miete")))
    assert b.insert_decision(conn, _row("ge_gerichte_4A_645_2011", "ge_gerichte", "4A_645/2011",
                                        "2012-01-27", "Résumé " + _body("Bail")))
    # a pre-2007 ruling the corpus does not hold otherwise stays
    assert b.insert_decision(conn, _row("ge_gerichte_4P.151_2002", "ge_gerichte", "4P.151/2002",
                                        "2002-10-22", "Résumé " + _body("Recours")))
    # a Geneva docket that merely contains a slash is not federal
    assert b.insert_decision(conn, _row("ge_gerichte_ACJC_1_2012", "ge_gerichte", "ACJC/1/2012",
                                        "2012-01-03", "Arrêt " + _body("Cour")))
    counts = b._fold_cantonal_copies_of_federal_rulings(conn)
    assert counts == {"folded": 1, "folded_ge_gerichte": 1, "no_bger_row": 1}
    assert "ge_gerichte_4A_645_2011" not in _ids(conn)
    assert _alias(conn, "ge_gerichte_4A_645_2011") == ("bger_4A_645_2011", "federal_ruling_in_cantonal_feed")
    assert {"ge_gerichte_4P.151_2002", "ge_gerichte_ACJC_1_2012"} <= _ids(conn)


def test_geneva_copy_goes_to_the_bger_ruling_of_its_date(conn):
    # BGer issues several rulings under one docket (bger_<docket>-D<date> ids).
    for did, d in (("bger_4A_346_2024", "2024-09-02"), ("bger_4A_346_2024-D20251127", "2025-11-27")):
        assert b.insert_decision(conn, _row(did, "bger", "4A_346/2024", d, "Urteil " + _body(d)))
    assert b.insert_decision(conn, _row("ge_gerichte_4A_346_2024", "ge_gerichte", "4A 346/2024",
                                        "2025-11-27", "Résumé " + _body("x")))
    b._fold_cantonal_copies_of_federal_rulings(conn)
    assert _alias(conn, "ge_gerichte_4A_346_2024")[0] == "bger_4A_346_2024-D20251127"


@pytest.mark.parametrize("docket,key", [
    ("4A_645/2011", "4A_645/2011"), ("4A 645/2011", "4A_645/2011"),
    ("4C.95/2004", "4C_95/2004"), ("4P.54/99", "4P_54/1999"), ("12T 3/2024", "12T_3/2024"),
    ("ACJC/1/2012", None), ("C/1234/2020", None), ("A/12/2019-LOI", None),
])
def test_federal_docket_key(docket, key):
    assert b._federal_docket_key(docket) == key


def test_build_runs_both_passes():
    src = (REPO / "build_fts5.py").read_text(encoding="utf-8")
    assert "_fold_federation_date_twins(conn)" in src
    assert "_fold_cantonal_copies_of_federal_rulings(conn)" in src


# ---------------------------------------------------- so_gerichte scraper

@pytest.mark.parametrize("text,want", [
    ("Urteilvom10. Juli 2025 Es wirken mit", ("10", "Juli", "2025")),
    ("Obergericht Zivilkammer Urteil vom 4. Juli 2022", ("4", "Juli", "2022")),
    ("IV-Rente (Einspracheentscheid vom 21. Dezember 2017) zieht", None),
    ("Mit Verfügung vom 7. Juni 2024 verweigerte", None),
])
def test_so_fallback_reads_only_the_rulings_own_date(text, want):
    m = RE_LONG_DATE.search(text)
    assert (m.groups() if m else None) == want
