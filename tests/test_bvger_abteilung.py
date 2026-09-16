"""BVGer Abteilung detection must read the Roman numeral as a whole word.

`_detect_abteilung` iterated BVGER_ABTEILUNGEN in order I..VI and returned on
a plain substring test, `f"Abt. {k}" in raw`. "Abt. I" is a substring of
"Abt. II", "Abt. III" and "Abt. IV", and "Abt. V" of "Abt. VI", so every
Weblaw-sourced decision of Abteilung II/III/IV was labelled Abteilung I and
every Abteilung VI decision was labelled Abteilung V. Weblaw has been the
primary BVGer mode since Feb 2026; measured on the production DB on
2026-09-17: 59,124 rows carried the wrong label (B 5,565 / C 16,946 /
D 29,105 -> "Abteilung I", F 7,508 -> "Abteilung V").

Rules pinned here:
  - the raw panel string wins when it names exactly one Abteilung, even if
    the docket letter says otherwise (portal metadata over our prefix map);
  - the docket prefix (A->I ... F->VI) is the tie-breaker when the raw
    string names none or several;
  - neither -> None, never a guess.

Pure function, no network.
"""
from __future__ import annotations

import pytest

from scrapers.bvger import BVGER_ABTEILUNGEN, _detect_abteilung

ABT = BVGER_ABTEILUNGEN

# The Weblaw `panel` field as stored verbatim by an early scraper version
# (production rows scraped 2026-02-20): German;;French;;Italian, sometimes
# with the German segment repeated at the end.
PANEL_STRINGS = {
    "I": (
        "Abt. I (Infrastruktur, Umwelt, Abgaben, Personal);;"
        "Cour I (infrastructure, environnement, finances, personnel);;"
        "Corte I (infrastruttura, ambiente, tributi, personale)"
    ),
    "II": (
        "Abt. II (Wirtschaft, Wettbewerb, Bildung);;"
        "Cour II (économie, concurrence, formation);;"
        "Corte II (economia, concorrenza, formazione)"
    ),
    "III": (
        "Abt. III (Sozialversicherungen, Gesundheit);;"
        "Cour III (assurances sociales, santé);;"
        "Corte III (assicurazioni sociali, sanità)"
    ),
    "IV": (
        "Abt. IV (Asylrecht);;Cour IV (droit d'asile);;"
        "Corte IV (diritto di asilo);;Abt. IV (Asylrecht)"
    ),
    "V": (
        "Abt. V (Asylrecht);;Cour V (droit d'asile);;"
        "Corte V (diritto di asilo);;Abt. V (Asylrecht)"
    ),
    "VI": (
        "Abt. VI (Ausländer- und Bürgerrecht);;"
        "Cour VI (droit des étrangers, droit de cité);;"
        "Corte VI (diritto degli stranieri e cittadinanza)"
    ),
}

# A docket whose letter maps to a DIFFERENT Abteilung than the panel string,
# so a passing test proves the panel string was read, not the prefix.
MISMATCHED_DOCKET = {
    "I": "B-100/2026",
    "II": "A-100/2026",
    "III": "A-100/2026",
    "IV": "E-100/2026",
    "V": "F-100/2026",
    "VI": "E-100/2026",
}


@pytest.mark.parametrize("numeral", list(PANEL_STRINGS))
def test_panel_string_is_read_as_a_whole_numeral(numeral):
    raw = PANEL_STRINGS[numeral]
    assert _detect_abteilung(MISMATCHED_DOCKET[numeral], raw) == ABT[numeral]


def test_reproduction_b_5477_2025():
    # Production row bvger_B-5477_2025 was stored as Abteilung I; the portal
    # panel is Abt. II and jurispub lists Abteilung II.
    raw = (
        "Abt. II (Wirtschaft, Wettbewerb, Bildung);;"
        "Cour II (économie, concurrence, formation)"
    )
    assert _detect_abteilung("B-5477/2025", raw) == ABT["II"]
    assert _detect_abteilung("B-5477/2025", raw) != ABT["I"]


@pytest.mark.parametrize(
    "docket, numeral",
    [("A-1/2026", "I"), ("B-1/2026", "II"), ("C-1/2026", "III"),
     ("D-1/2026", "IV"), ("E-1/2026", "V"), ("F-1/2026", "VI"),
     ("f-1/2026", "VI")],
)
def test_docket_prefix_only(docket, numeral):
    assert _detect_abteilung(docket, None) == ABT[numeral]
    assert _detect_abteilung(docket, "") == ABT[numeral]


def test_raw_without_a_panel_falls_back_to_docket():
    assert _detect_abteilung("C-1/2026", "no panel here") == ABT["III"]


def test_ambiguous_raw_falls_back_to_docket():
    raw = "Abt. IV (Asylrecht);;Abt. V (Asylrecht)"
    assert _detect_abteilung("D-1/2026", raw) == ABT["IV"]
    assert _detect_abteilung("E-1/2026", raw) == ABT["V"]
    assert _detect_abteilung("", raw) is None
    assert _detect_abteilung("X-1/2026", raw) is None


def test_full_and_short_label_forms():
    # Canonical label (unique via its parenthetical) and the jurispub-style
    # short form both resolve directly, and win over the docket letter.
    assert _detect_abteilung("A-1/2026", ABT["VI"]) == ABT["VI"]
    assert _detect_abteilung("A-1/2026", "Abteilung III") == ABT["III"]
    assert _detect_abteilung("A-1/2026", "Cour II (économie)") == ABT["II"]


def test_bvge_collection_dockets_never_use_the_prefix_map():
    # "BVGE 2007/10" starts with B but is the published collection, not
    # Abteilung II. Production held 811 such rows on 2026-09-17.
    assert _detect_abteilung("BVGE 2007/10", None) is None
    assert _detect_abteilung("BVGE 2014 IV/3", "") is None
    assert _detect_abteilung("BVGE 2007/10", PANEL_STRINGS["V"]) == ABT["V"]


def test_nothing_known_is_none():
    assert _detect_abteilung("", None) is None
    assert _detect_abteilung("X-1/2026", None) is None
    assert _detect_abteilung("X-1/2026", "Kammer 3") is None
