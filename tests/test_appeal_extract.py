"""P0.2: appealed-decision extraction from the BGer rubrum (the wishlist's
#1 ask). Patterns from real corpus samples; section-clamped so
Rechtsmittelbelehrung text can never poison the match."""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from appeal_extract import extract_appealed  # noqa: E402


def test_german_with_chamber_and_docket():
    t = ("... gegen das Urteil des Sozialversicherungsgerichts des Kantons "
         "Zürich vom 30. September 2021 (UV.2021.00016). Sachverhalt: A. ...")
    r = extract_appealed(t)
    assert r["appealed_date"] == "2021-09-30"
    assert r["appealed_docket"] == "UV.2021.00016"
    assert "Sozialversicherungsgericht" in r["appealed_court_raw"]
    assert r["form"] == "Urteil"


def test_german_beschluss_no_docket():
    t = ("... gegen den Beschluss des Obergerichts des Kantons Zürich, "
         "II. Zivilkammer, vom 26. Januar 2009. Sachverhalt: ...")
    r = extract_appealed(t)
    assert r["appealed_date"] == "2009-01-26"
    assert r["appealed_docket"] is None
    assert "Obergericht" in r["appealed_court_raw"]


def test_french_with_double_docket_parens():
    t = ("... contre l'arrêt de la Cour de justice de la République et "
         "canton de Genève, Chambre des assurances sociales, du 27 mai 2021 "
         "(A/103/2021 - ATAS/506/2021). Vu : la décision ...")
    r = extract_appealed(t)
    assert r["appealed_date"] == "2021-05-27"
    assert r["appealed_docket"] is not None
    assert "Cour de justice" in r["appealed_court_raw"]


def test_french_inverted_rendu_le():
    t = ("... contre l'arrêt rendu le 6 octobre 2020 par le Tribunal "
         "cantonal du canton de Vaud confirmant les décisions rendues le "
         "27 mai 2019 par l'Administration fiscale. Considérant ...")
    r = extract_appealed(t)
    assert r["appealed_date"] == "2020-10-06"
    assert "Tribunal cantonal" in r["appealed_court_raw"]


def test_revision_against_bger_itself():
    t = ("... gegen das Urteil des Schweizerischen Bundesgerichts "
         "4A_353/2020 vom 19. Januar 2021 (Beschluss und Urteil "
         "LB200006-O/U). Sachverhalt: A. ...")
    r = extract_appealed(t)
    assert r["appealed_date"] == "2021-01-19"
    assert "Bundesgericht" in r["appealed_court_raw"]


def test_no_anchor_stays_none():
    # old EVG header format has no 'gegen ...' rubrum
    t = "Bundesgericht Eidgenössisches Versicherungsgericht 20.12.2005 I 527/05 ..."
    assert extract_appealed(t) is None
    assert extract_appealed("") is None
    assert extract_appealed(None) is None


def test_body_rechtsmittelbelehrung_cannot_poison():
    # anchor phrase appears only AFTER the section marker -> clamped away
    t = ("Urteil vom 1. Januar 2024. Sachverhalt: X erhob Beschwerde "
         "gegen das Urteil des Obergerichts des Kantons Bern vom "
         "2. Februar 2020 (ZK 20 99). Erwägungen ...")
    assert extract_appealed(t) is None


def test_a_day_the_calendar_lacks_is_no_date():
    # issue #171: the rubrum's 31 April is a typo, not a date to store
    t = ("... gegen das Urteil des Obergerichts des Kantons Zürich "
         "vom 31. April 2021 (LB200041). Sachverhalt: A. ...")
    r = extract_appealed(t)
    assert r["appealed_date"] is None
    assert r["appealed_docket"] == "LB200041"
    leap = extract_appealed(t.replace("31. April 2021", "29. Februar 2024"))
    assert leap["appealed_date"] == "2024-02-29"
