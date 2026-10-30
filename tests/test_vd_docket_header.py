"""Vaud docket from the ruling's own header (since 2026-09-04 the listing gives
only the per-year ruling number, or nothing, so citations read "Gericht VD 344
vom 19. April 2021"). Offline. Header lines copied from the corpus."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from models import make_canonical_key, make_decision_id  # noqa: E402
from scrapers.cantonal import vd_gerichte as vd  # noqa: E402
from scrapers.cantonal.vd_gerichte import (  # noqa: E402
    VDGerichteScraper,
    dossier_from_header,
)

U1 = "02949903-ae38-4ad9-833b-7dce612af97d"
RULE = "__________________________________________"


def header(*lines):
    return "\n".join(lines) + "\nArrêt du 19 avril 2021\n__________\nComposition : M. X, président\n"


# ── reading the header ────────────────────────────────────────────────────

@pytest.mark.parametrize("text, expected", [
    # the number above or below the case number, with or without a suffix
    (header("TRIBUNAL CANTONAL", "344", "DA21.000826-LAS", "CHAMBRE DE S RECO URS PEN ALE"), "DA21.000826"),
    (header("TRIBUNAL CANTONAL", "PE13.010886-MMR", "357", "CHAMBRE DE S RECO URS PEN ALE"), "PE13.010886"),
    (header("TRIBUNAL CANTONAL", "JS12.005418-130223", "182", "CO UR D’APPEL CIVI L E"), "JS12.005418"),
    (header("TRIBUNAL CANTONAL", "135", "PE13.026459/PBR/Jdt/lpv", "CO UR D’APPEL PENALE"), "PE13.026459"),
    (header("TRIBUNAL CANTONAL", "PE09.023630-CMI/YBL/TDE", "47", "CO UR DE CAS SATION P EN ALE"), "PE09.023630"),
    # second character a digit, or a leading digit
    (header("TRIBUNAL CANTONAL", "P315.012345-161234", "12", "CO UR D’APPEL CIVI L E"), "P315.012345"),
    (header("TRIBUNAL CANTONAL", "7F21.040320", "12", "CO UR D’APPEL CIVI L E"), "7F21.040320"),
    # printed separators
    (header("TRIBUNAL CANTONAL", "171", "PE.06.006465-AME/VFV/PGO", "CO UR DE CAS SATION P EN ALE"), "PE06.006465"),
    (header("TRIBUNAL CANTONAL", "LY 13.008197-141730", "238", "CHAMBRE DE S CURATELLE S"), "LY13.008197"),
    (header("TRIBUNAL CANTONAL", "72", "PM10. 025410-MRE", "CO UR DE CAS SATION P EN ALE"), "PM10.025410"),
    (header("TRIBUNAL CANTONAL", "TD19-007793-210984", "450", "CO UR D'AP PEL CI VI LE"), "TD19.007793"),
    (header("TRIBUNAL CANTONAL", "JJ12034055-122109", "424", "CHAMBRE D E S RECOURS CIVIL E"), "JJ12.034055"),
    # the capital I comes out of pdfplumber as a lower-case l
    (header("TRIBUNAL CANTONAL", "Jl19.044023-210902", "12", "CO UR D’APPEL CIVI L E"), "JI19.044023"),
    # the prud'hommes header has no ruling number
    (header("TRIBUNAL DE PRUD'HOMMES", "DE L'ADMINISTRATION CANTONALE", "Palais de justice de Montbenon",
            "1014 Lausanne", "TF23.022423", "JUGEMENT"), "TF23.022423"),
])
def test_dossier_is_read_from_the_header(text, expected):
    assert dossier_from_header(text) == expected


@pytest.mark.parametrize("line", [
    "PE25.***-***", "TD***-***", "PE24***-***", "TF25.***",
    "AA99.***-***", "TRIBUNAL CANTONAL ZD25.***",
])
def test_a_masked_number_is_not_a_docket(line):
    assert dossier_from_header(header("TRIBUNAL CANTONAL", "315", line, "CHAMBRE DES RECOURS PENALE")) is None
    assert dossier_from_header(header(line, "315", "CHAMBRE DES RECOURS PENALE")) is None


@pytest.mark.parametrize("line", [
    "RPE/01/14/0001385/ds", "SPEN/2309/SBA/mbr", "OEP/MES/149883/AVI/CJR",   # prosecutor's reference
    "PE13.0022035-LGN", "JJ16.53106-170215",                                 # misprints
])
def test_what_is_not_the_courts_case_number_is_not_read(line):
    assert dossier_from_header(header("TRIBUNAL CANTONAL", "545", line, "CHAMBRE DES RECOURS PENALE")) is None


def test_no_number_in_the_header():
    assert dossier_from_header(header("TRIBUNAL CANTONAL", "344", "TRIBUNAL D ’ACCUS ATION")) is None
    assert dossier_from_header("") is None
    assert dossier_from_header(None) is None


def test_a_case_number_in_the_body_is_not_read():
    text = header("TRIBUNAL CANTONAL", "344", "TRIBUNAL D ’ACCUS ATION")
    assert dossier_from_header(text + "vu l'enquête PE21.000826-LAS\n") is None
    # also when the date line is what ends a short header
    assert dossier_from_header("TRIBUNAL CANTONAL\nArrêt du 2 mai 2011\nPE21.000826-LAS\n") is None


def test_the_ruling_number_and_a_date_are_not_read_as_a_case_number():
    text = "TRIBUNAL CANTONAL\n2880315\n19.04.2021\n1014 Lausanne\nCHAMBRE\n"
    assert dossier_from_header(text) is None


# ── resolving the docket ──────────────────────────────────────────────────

DOSSIER_TEXT = header("TRIBUNAL CANTONAL", "344", "DA21.000826-LAS", "CHAMBRE")
MASKED_TEXT = header("TRIBUNAL CANTONAL", "344", "PE25.***-***", "CHAMBRE")
BARE_TEXT = header("TRIBUNAL CANTONAL", "344", "CHAMBRE")


def stub(number, chamber="CREP", affaire=None):
    sc = VDGerichteScraper.__new__(VDGerichteScraper)
    return sc._parse_search_item({"decisionHit": {
        "id": U1, "numero": number, "dateDecision": "2021-04-19",
        "affaireHit": {"numero": affaire, "autoriteDirectrice": chamber},
    }})


def resolve(s, text, d=date(2021, 4, 19)):
    return VDGerichteScraper._resolve_docket(s, text, d)


def test_header_number_becomes_the_docket_and_the_ruling_number_is_kept():
    assert resolve(stub("344"), DOSSIER_TEXT) == ("DA21.000826", "344")


def test_masked_or_missing_number_falls_back_to_chamber_year_number():
    assert resolve(stub("344"), MASKED_TEXT) == ("CREP 2021/344", "344")
    assert resolve(stub("344"), BARE_TEXT) == ("CREP 2021/344", "344")
    assert resolve(stub("111/I", chamber="CREC"), BARE_TEXT) == ("CREC 2021/111/I", "111/I")


def test_without_a_chamber_or_date_the_number_stands():
    assert resolve(stub("344", chamber=None), BARE_TEXT) == ("344", "344")
    assert resolve(stub("344"), BARE_TEXT, d=None) == ("344", "344")


def test_listing_without_a_number_keeps_the_uuid_as_dedup_key():
    assert resolve(stub(None), DOSSIER_TEXT) == ("DA21.000826", U1)
    assert resolve(stub(None), BARE_TEXT) == (U1, U1)


def test_a_docket_from_the_listing_is_left_alone():
    assert resolve(stub("249", affaire="ZD17.028583"), MASKED_TEXT) == ("ZD17.028583", None)
    assert resolve(stub("AI 210/17 - 249/2017"), DOSSIER_TEXT) == ("AI 210/17 - 249/2017", None)


# ── ids do not move ───────────────────────────────────────────────────────

def test_the_id_stays_keyed_on_the_uuid():
    assert stub("344")["decision_id"] == make_decision_id("vd_gerichte", U1)
    assert stub(None)["decision_id"] == make_decision_id("vd_gerichte", U1)


def test_a_sectioned_ruling_number_is_keyed_on_the_uuid():
    """'111/I' repeats every year, exactly like '111'."""
    s = stub("111/I", chamber="CREC")
    assert s["decision_id"] == make_decision_id("vd_gerichte", U1)
    assert s["docket_number"] == "111/I"
    assert s["ruling_no"] == "111/I"


# ── through fetch_decision ────────────────────────────────────────────────

class FakeResponse:
    headers = {"Content-Type": "application/pdf"}
    content = b"%PDF-1.4 fixture"

    def raise_for_status(self):
        pass


class FakeSession:
    def get(self, url, timeout=None):
        return FakeResponse()


def fetched(monkeypatch, s, text):
    sc = VDGerichteScraper.__new__(VDGerichteScraper)
    sc.session = FakeSession()
    sc._rate_limit = lambda: None
    monkeypatch.setattr(VDGerichteScraper, "_extract_pdf_text", staticmethod(lambda _: text))
    return sc.fetch_decision(s)


def test_fetch_decision_writes_both_numbers(monkeypatch):
    d = fetched(monkeypatch, stub("344"), DOSSIER_TEXT + "x" * 300)
    assert d.decision_id == make_decision_id("vd_gerichte", U1)
    assert d.docket_number == "DA21.000826"
    assert d.docket_number_2 == "344"
    assert d.pdf_url == f"{vd.API_URL}/decision/download/{U1}"


def test_fetch_decision_on_a_masked_ruling(monkeypatch):
    d = fetched(monkeypatch, stub("344"), MASKED_TEXT + "x" * 300)
    assert d.docket_number == "CREP 2021/344"
    assert d.docket_number_2 == "344"


# ── the build's dedup key must not follow the docket ──────────────────────
# Pass 1 of build_fts5._dedup_decisions deletes all but the longest row of a
# canonical key without reading the text. PE24.013673 alone has four rulings of
# 2025-12-01 (876, 877, 878 and the held vd_gerichte_PE24.013673).

def test_two_rulings_of_one_case_on_one_day_keep_apart_keys():
    import build_fts5

    def key(row):
        return make_canonical_key(row["court"], build_fts5._dedup_docket(row), row["decision_date"])

    rows = [{"court": "vd_gerichte", "docket_number": "PE24.013673",
             "docket_number_2": n, "decision_date": "2025-12-01"} for n in ("876", "877")]
    assert key(rows[0]) != key(rows[1])
    # the key a row has today, so a repaired row dedups exactly as it does now
    assert key(rows[0]) == make_canonical_key("vd_gerichte", "876", "2025-12-01")
