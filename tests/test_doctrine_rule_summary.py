"""get_doctrine's rule_summary: the clause that states the rule (2026-10-10).

Until then the summary was the regeste up to its first full stop: "Art. 135
Ziff. 2 und Art. 138 Abs. 1 OR, Unterbrechung der Verjährung; ..." gave "Art",
and "Streitwert (Art. 46 OG). Täuschung ..." gave "Streitwert (Art". Regestes
below are the real ones (BGE 133 III 675, 116 II 431, 144 III 93, 132 III 268).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mcp_server as m  # noqa: E402

R_133_III_675 = ("Regeste\n Art. 135 Ziff. 2 und Art. 138 Abs. 1 OR, Unterbrechung der Verjährung; "
                 "Art. 33 VVG, Auslegung allgemeiner Versicherungsbedingungen nach dem Vertrauensprinzip. "
                 "Soweit die Verjährung erst nach Klageanhebung zu laufen beginnt, wird sie durch jede "
                 "folgende Prozesshandlung gemäss Art. 138 Abs. 1 OR unterbrochen (E. 2.4).")
R_116_II_431 = ("Regeste\n Streitwert (Art. 46 OG). Täuschung (Art. 28 OR). Kaufvertrag; Auslegung nach "
                "dem Vertrauensgrundsatz. 1. Streitwert der Wandelungsklage (Art. 46 OG) (E. 1).")
R_144_III_93 = ("Regeste\n Darlehensvertrag (Art. 312 OR) oder Schenkung (Art. 239 Abs. 1 OR). Anwendung der "
                "Prinzipien zur Auslegung des Parteiwillens (Art. 18 Abs. 1 OR und Vertrauensprinzip). Kann ein "
                "tatsächlich übereinstimmender Wille der Parteien nicht festgestellt werden")
R_132_III_268 = ("Regeste\n Art. 9 Abs. 1 und Art. 22 Abs. 2 GestG; Begriff des Konsumentenvertrages im Sinne von "
                 "Art. 22 Abs. 2 GestG; Zweck der Bestimmung; Auslegung einer in den AGB einer Bank enthaltenen "
                 "Gerichtsstandsklausel nach dem Vertrauensprinzip.")
TERMS = m._leading_topic_terms("Vertrauensprinzip")


def test_an_abbreviation_full_stop_does_not_end_the_rule():
    # A heading of one word ("Streitwert") is joined with the clause after it.
    assert m._doctrine_rule_summary(R_116_II_431) == "Streitwert (Art. 46 OG): Täuschung (Art. 28 OR)"


def test_a_clause_opening_with_its_statute_keeps_the_rule_after_the_comma():
    assert m._doctrine_rule_summary(R_133_III_675) == "Unterbrechung der Verjährung"


def test_a_concept_query_takes_the_clause_about_that_concept():
    assert m._doctrine_rule_summary(R_133_III_675, TERMS) == (
        "Auslegung allgemeiner Versicherungsbedingungen nach dem Vertrauensprinzip")
    assert m._doctrine_rule_summary(R_144_III_93, TERMS).startswith(
        "Anwendung der Prinzipien zur Auslegung des Parteiwillens")


def test_a_clause_of_citations_only_is_skipped():
    assert m._doctrine_rule_summary(R_132_III_268) == "Begriff des Konsumentenvertrages im Sinne von Art. 22 Abs. 2 GestG"


def test_without_a_matching_clause_the_first_rule_stands():
    assert m._doctrine_rule_summary(R_116_II_431, TERMS) == "Streitwert (Art. 46 OG): Täuschung (Art. 28 OR)"


R_143_IV_500 = ("Regeste a\n Art. 36 Abs. 2 und 27 Abs. 1 SVG, Art. 36 Abs. 2 SSV; Vortrittsrecht an einer mit "
                "dem Signal \"kein Vortritt\" versehenen Kreuzung; Verkehrsspiegel.\nRegeste b\n Art. 26 SVG; "
                "Vertrauensprinzip. Voraussetzungen, unter welchen sich der Lenker eines vortrittsbelasteten "
                "Fahrzeugs auf das Vertrauensprinzip berufen kann (E. 1.2.4).")
R_146_IV_211 = ("Regeste\n Art. 122 Abs. 1, 126 Abs. 1 lit. a StPO; Art. 305 bis Ziff. 2 StGB; Art. 41, 50 Abs. 3 OR; "
                "Zivilklage, Schadenersatzforderung aus Geldwäscherei. Soweit das Gericht die beschuldigte Person "
                "schuldig spricht, ist der Entscheid zwingend (E. 3).")
R_131_III_115 = ("Regeste\n Art. 56 Abs. 1 OR; Tierhalterhaftung. Haftungsvoraussetzungen und Befreiungsbeweis des "
                 "Tierhalters; Anforderungen an die Umzäunung einer Pferdeweide (E. 2 und 3).")


def test_the_concept_is_found_in_a_later_part_of_the_regeste():
    assert m._doctrine_rule_summary(R_143_IV_500, TERMS) == (
        "Vertrauensprinzip: Voraussetzungen, unter welchen sich der Lenker eines vortrittsbelasteten "
        "Fahrzeugs auf das Vertrauensprinzip berufen kann")


def test_statute_lists_with_mixed_case_acts_are_citations():
    # "126 Abs. 1 lit. a StPO", "Art. 305 bis Ziff. 2 StGB", "Art. 41, 50 Abs. 3 OR" are not rules.
    assert m._doctrine_rule_summary(R_146_IV_211) == "Zivilklage, Schadenersatzforderung aus Geldwäscherei"


def test_a_one_word_heading_is_joined_with_its_rule():
    assert m._doctrine_rule_summary(R_131_III_115) == (
        "Tierhalterhaftung: Haftungsvoraussetzungen und Befreiungsbeweis des Tierhalters")


def test_a_long_clause_is_cut_at_a_word_and_marked():
    s = m._doctrine_rule_summary("Regeste\n " + "Vertragsauslegung " * 30, max_len=60)
    assert len(s) <= 62 and s.endswith(" …") and "Vertragsausl …" not in s


def test_empty_and_header_only_regestes_give_nothing():
    assert m._doctrine_rule_summary("") == ""
    assert m._doctrine_rule_summary("Regeste\n") == ""
