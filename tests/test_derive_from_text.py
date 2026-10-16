"""derive_from_text: re-derive authoritative fields from the decision's own text.

Fixtures are real Swiss decision headers (the BGE 152 II 1 Urteilskopf is verbatim
from the corpus). The contract: a date verified from text overrides a synthetic
volume-year placeholder, and provenance is reported honestly.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import derive_from_text as d  # noqa: E402

# Verbatim head of bge_152 II 1 from the live corpus.
BGE_152_II_1 = (
    "Urteilskopf 152 II 1 1. Auszug aus dem Urteil der III. öffentlich-rechtlichen "
    "Abteilung i.S. A.A. gegen Einwohnergemeinde U. (Beschwerde in "
    "öffentlich-rechtlichen Angelegenheiten) 9C_113/2025 vom 27. September 2025 "
    "Regeste Art. 1 Abs. 1, Art. 6 Abs. 1, Art. 457-459 ZGB"
)
BGE_FR = ("Arrêt de la Ire Cour de droit civil dans la cause X. contre Y. "
          "4A_123/2024 du 5 mars 2024 Regeste ...")
BGE_IT = ("Sentenza della II Corte di diritto pubblico nella causa A. "
          "2C_456/2023 del 14 marzo 2023 Regesto ...")


def test_extract_real_date_from_bge_header_de():
    iso, raw = d.extract_text_date(BGE_152_II_1)
    assert iso == "2025-09-27"
    assert "27. September 2025" in raw  # raw includes the docket+marker anchor


def test_docket_year_validates_date():
    # a ruling cannot predate its own docket: a docket-adjacent date whose year
    # is before the docket (filing) year is an extraction/OCR error -> rejected.
    assert d.extract_urteilskopf("9C_113/2025 vom 27. September 1964").get("date") is None
    # the clean case extracts cleanly
    assert d.extract_urteilskopf("9C_113/2025 vom 27. September 2025")["date"] == "2025-09-27"
    assert d.docket_year("9C_113/2025") == 2025


def test_build_ecli_couples_verified_fields():
    # ECLI uses the VERIFIED year; BGE excerpt + its docket -> same ECLI
    assert d.build_ecli("bge", "2025-09-27", "9C_113/2025") == "ECLI:CH:BGER:2025:9C_113.2025"
    assert d.build_ecli("bger", "2025-09-27", "9C_113/2025") == "ECLI:CH:BGER:2025:9C_113.2025"
    # a synthetic date would have produced a wrong-year ECLI — guarded upstream
    assert d.build_ecli("bge", "2026-01-01", "9C_113/2025")[:16] == "ECLI:CH:BGER:202"
    assert d.build_ecli("zz_unknown", "2025-01-01", "x") is None


def test_extract_real_date_fr_it():
    assert d.extract_text_date(BGE_FR)[0] == "2024-03-05"
    assert d.extract_text_date(BGE_IT)[0] == "2023-03-14"


def test_numeric_date_fallback():
    assert d.extract_text_date("Urteil 27.09.2025 i.S. ...")[0] == "2025-09-27"


def test_anchored_date_beats_earlier_cited_date():
    # a cited case's date (1. Januar 2010) appears BEFORE the real 'vom' date —
    # the marker-anchored extractor must pick the ruling's own date.
    txt = ("... bestätigt den Entscheid vom 1. Januar 2010 ... 9C_113/2025 "
           "vom 27. September 2025 Regeste ...")
    assert d.extract_text_date(txt)[0] == "2025-09-27"
    # French + Italian markers
    assert d.extract_text_date("X. 4A_1/2024 du 5 mars 2024")[0] == "2024-03-05"
    assert d.extract_text_date("A. 2C_4/2023 del 14 marzo 2023")[0] == "2023-03-14"


def test_future_and_impossible_dates_rejected():
    # 2026 future-dated cantonal bug class: rejected when max_year caps it
    assert d.extract_text_date("entschieden am 17. August 2026", max_year=2025)[0] is None
    # impossible calendar date
    assert d.extract_text_date("vom 31. Februar 2024")[0] is None


def test_extract_and_normalize_docket():
    assert d.extract_docket(BGE_152_II_1) == "9C_113/2025"
    assert d.normalize_docket("2P.139/2004") == "2P_139/2004"
    assert d.normalize_docket("2C 838/2018") == "2C_838/2018"
    assert d.normalize_docket("2C_838/2018") == "2C_838/2018"  # idempotent


def test_urteilskopf_gives_docket_and_date_together():
    uk = d.extract_urteilskopf(BGE_152_II_1)
    assert uk["docket"] == "9C_113/2025"   # the BGE->docket link
    assert uk["date"] == "2025-09-27"      # the real date


def test_derive_date_provenance():
    # synthetic placeholder + text has real date -> text wins, flagged
    best, prov = d.derive_date("2026-01-01", BGE_152_II_1)
    assert best == "2025-09-27" and prov == "extracted_from_text"
    # real stored date is trusted, text not second-guessed
    best, prov = d.derive_date("2025-04-29", "irrelevant body")
    assert best == "2025-04-29" and prov == "source_metadata"
    # synthetic and no recoverable text date -> kept but flagged unverified
    best, prov = d.derive_date("2026-01-01", "no date here")
    assert best == "2026-01-01" and prov == "volume_synthetic"
    # nothing at all
    assert d.derive_date(None, "no date here") == (None, "null")


def test_derive_dates_demuxes_decision_and_publication():
    # BGE: synthetic decision_date is the VOLUME (publication) year; the real
    # decision date comes from text. They are distinct and both recovered.
    dd, dprov, pd, pprov = d.derive_dates("2026-01-01", None, BGE_152_II_1)
    assert dd == "2025-09-27" and dprov == "extracted_from_text"      # Urteilsdatum
    assert pd == "2026-01-01" and pprov == "volume_year"             # AS volume year

    # real stored dates on both axes are trusted, not overwritten
    dd, dprov, pd, pprov = d.derive_dates("2025-04-29", "2025-06-01", "body")
    assert (dd, dprov) == ("2025-04-29", "source_metadata")
    assert (pd, pprov) == ("2025-06-01", "source_metadata")

    # synthetic decision date, no recoverable text date -> decision unknown,
    # but the volume year is preserved as the publication signal (not a fake date)
    dd, dprov, pd, pprov = d.derive_dates("2026-01-01", None, "no date here")
    assert (dd, dprov) == (None, "null")
    assert (pd, pprov) == ("2026-01-01", "volume_year")


def test_derive_date_rejects_synthetic_and_future_extractions():
    # a text date that is itself YYYY-01-01 is an artifact (no court rules Jan 1) ->
    # must NOT replace the synthetic stored date as 'extracted'
    best, prov = d.derive_date("2026-01-01", "Vereinbarung vom 1. Januar 2009 ...")
    assert prov != "extracted_from_text"
    # a future extracted date is rejected when max_date caps it
    best, prov = d.derive_date("2026-01-01", "9C_9/2026 vom 17. August 2026", max_date="2026-06-28")
    assert prov != "extracted_from_text"
    # the clean case still works
    best, prov = d.derive_date("2026-01-01", BGE_152_II_1, max_date="2026-06-28")
    assert (best, prov) == ("2025-09-27", "extracted_from_text")


def test_is_synthetic_date():
    assert d.is_synthetic_date("2026-01-01") is True
    assert d.is_synthetic_date("") is True
    assert d.is_synthetic_date(None) is True
    assert d.is_synthetic_date("2025-09-27") is False


# Heads the parser missed (2026-10-16): no date was read, so a 1-January
# placeholder stayed, and EVG-era BGEs got no federal key at all.
import pytest  # noqa: E402


@pytest.mark.parametrize("head,docket,iso", [
    ("(ricorso in materia penale) 6B_1379/2023 dell'11 settembre 2024 Regeste", "6B_1379/2023", "2024-09-11"),
    ("(ricorso per cassazione) 6S.110/2005 del 1° settembre 2005 Regeste", "6S_110/2005", "2005-09-01"),
    ("(recours en réforme) 5C.29/2005 des 14 juillet et 1er septembre 2005 Regeste", "5C_29/2005", "2005-07-14"),
    ("Sozialversicherungsgericht des Kantons Zürich P 71/01 vom 5. März 2002 Regeste", "P71/01", "2002-03-05"),
    ("Tribunal des assurances du canton de Vaud I 189/02 du 15 janvier 2003 Regeste", "I189/02", "2003-01-15"),
])
def test_italian_apostrophe_first_of_month_two_sessions_and_evg_dockets(head, docket, iso):
    u = d.extract_urteilskopf(head)
    assert (u.get("docket"), u.get("date")) == (docket, iso)


def test_evg_shape_is_never_taken_without_its_date():
    # "A 12/34" in running text is not a docket
    assert d.extract_urteilskopf("gemäss Ziff. A 12/34 der Weisung, Regeste").get("docket") is None


def test_joined_dockets_give_the_lead_docket():
    # BGE 136 III 502 and 151 I 3: the judgment is filed under the first docket
    u = d.extract_urteilskopf("(recours en matière civile) 4A_210/2010 / 4A_214/2010 / 4A_216/2010 "
                              "du 1er octobre 2010 Regeste")
    assert (u.get("docket"), u.get("date")) == ("4A_210/2010", "2010-10-01")
    u = d.extract_urteilskopf("(recours en matière de droit public) 2C_36/2023 / 2C_38/2023 du 5 juin 2024 Regeste")
    assert (u.get("docket"), u.get("date")) == ("2C_36/2023", "2024-06-05")
