"""Two-word law codes and the edition prefix (anomaly report of 2026-10-26).

The extractor's law token is one word, so "Art. 32 AsylV 1" was stored as
ART.32.ASYLV (the family of three asylum ordinances, no act) and "GebV
SchKG" as GEBV; upper-casing turned aZG (Zollgesetz 1925) into AZG, the
Arbeitszeitgesetz. Measured on all 109,501 BVGer decisions: 12,253 AsylV 1,
8,255 OA 1, 1,645 BVV 2 and 1,367 aZG references.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO / "scripts"))

import build_law_code_table as blt  # noqa: E402
import mcp_server as m  # noqa: E402
from search_stack import law_code_forms  # noqa: E402
from search_stack.reference_extraction import (  # noqa: E402
    canonical_law_code,
    extract_statute_references,
    graph_law_codes,
)

TABLE = json.loads((REPO / "docs" / "data" / "law_codes.json").read_text(encoding="utf-8"))


def _codes(text: str) -> list[str]:
    return [r.normalized for r in extract_statute_references(text)]


def test_forms_module_matches_the_published_table():
    generated = blt.render_forms_module(TABLE)
    committed = (REPO / "search_stack" / "law_code_forms.py").read_text(encoding="utf-8")
    assert generated == committed, (
        "regenerate with scripts/build_law_code_table.py --forms-out "
        "search_stack/law_code_forms.py")


def test_joined_keys_resolve_like_the_spaced_ones():
    a = TABLE["alias_to_sr"]
    assert a["ASYLV1"] == a["ASYLV 1"] == "142.311"
    assert a["BVV2"] == a["BVV 2"] == "831.441.1"
    assert a["GEBVSCHKG"] == a["GEBV SCHKG"] == "281.35"
    assert "ASYLV" not in a  # the family is no act


def test_joined_form_never_shadows_another_acts_code():
    # OA Fam (836.11) joined would be OAFAM, the code of OAFam (836.21)
    assert TABLE["alias_to_sr"]["OAFAM"] == "836.21"
    assert "OA FAM" not in TABLE["compound_codes"]
    got = blt.compound_codes({"OA FAM": "836.11", "OAFAM": "836.21", "BVV 2": "831.441.1"}, {})
    assert got == {"BVV 2": "BVV2"}


def test_numbered_and_compound_codes_are_joined():
    assert _codes("Art. 32 AsylV 1, Art. 29a Abs. 2 AsylV 1. Art. 1j BVV 2; "
                  "Art. 61 GebV SchKG; art. 8 al. 2 OASI 1") == [
        "ART.32.ASYLV1", "ART.29a.ABS.2.ASYLV1", "ART.1j.BVV2",
        "ART.61.GEBVSCHKG", "ART.8.ABS.2.OASI1"]
    [ref] = extract_statute_references("vgl. Art. 61 GebV SchKG.")
    assert ref.raw == "Art. 61 GebV SchKG"


def test_a_number_that_is_not_the_ordinance_is_left_alone():
    # a decimal or a longer number after the code is not "AsylV 1"
    assert _codes("Art. 5 AsylV 1.5 und Art. 3 BVV 12") == ["ART.5.ASYLV", "ART.3.BVV"]
    # a code without such a title keeps its number out
    assert _codes("Art. 5 OR 1") == ["ART.5.OR"]


def test_edition_prefix_is_kept_before_a_known_code():
    assert _codes("Art. 12 aZG und Art. 12 AZG; Art. 3 Abs. 1 nDSG") == [
        "ART.12.aZG", "ART.12.AZG", "ART.3.ABS.1.nDSG"]
    # lower-case a before an unknown code is not a prefix
    assert "XQZ" not in law_code_forms.KNOWN_CODES
    assert _codes("Art. 4 aXQZ") == ["ART.4.AXQZ"]


def test_canonical_law_code_for_tool_arguments():
    for given, want in [("AsylV 1", "ASYLV1"), ("asylv 1", "ASYLV1"), ("ASYLV1", "ASYLV1"),
                        ("GebV-SchKG", "GEBVSCHKG"), ("aZG", "aZG"), ("OR", "OR"),
                        ("Cst.", "CST."), ("", "")]:
        assert canonical_law_code(given) == want, given


def test_graph_law_codes_family_and_legacy():
    assert graph_law_codes("ASYLV") == (["ASYLV", "ASYLV1", "ASYLV2", "ASYLV3"], [])
    assert graph_law_codes("ASYLV1") == (["ASYLV1"], ["ASYLV", "ASYLV1"])
    assert graph_law_codes("GEBVSCHKG") == (["GEBVSCHKG"], ["GEBV", "GEBVSCHKG"])
    assert graph_law_codes("aZG") == (["aZG"], ["AZG"])
    assert graph_law_codes("aASYLV1") == (["aASYLV1"], ["AASYLV", "AASYLV1"])
    assert graph_law_codes("OR") == (["OR"], [])


def _graph(codes: list[str]) -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE statutes(statute_id TEXT PRIMARY KEY, law_code TEXT, "
              "article TEXT, paragraph TEXT)")
    c.executemany("INSERT INTO statutes VALUES (?,?,?,NULL)",
                  [(f"ART.32.{lc}", lc, "32") for lc in codes])
    return c


def test_server_falls_back_to_legacy_codes_only_on_an_old_graph():
    # the old graph holds joined codes too where the decision wrote "BVV2"
    old = _graph(["ASYLV", "AZG", "OR", "BVV2"])
    new = _graph(["ASYLV1", "ASYLV", "aZG", "AZG", "OR"])
    assert m._graph_statute_codes(old, "AsylV 1") == ["ASYLV", "ASYLV1"]
    assert m._graph_statute_codes(new, "AsylV 1") == ["ASYLV1"]
    assert m._graph_statute_codes(old, "aZG") == ["AZG"]
    assert m._graph_statute_codes(new, "aZG") == ["aZG"]  # never the Arbeitszeitgesetz
    assert m._graph_statute_codes(new, "AsylV") == ["ASYLV", "ASYLV1", "ASYLV2", "ASYLV3"]
    assert m._graph_statute_codes(new, "or") == ["OR"]
    assert m._graph_statute_ids(old, {"ART.32.ASYLV1"}) == ["ART.32.ASYLV", "ART.32.ASYLV1"]
    assert m._graph_statute_codes(old, "BVV 2") == ["BVV", "BVV2"]
    assert m._graph_statute_ids(new, {"ART.32.ASYLV1"}) == ["ART.32.ASYLV1"]


def test_query_refs_and_parser_carry_the_new_codes():
    refs = m._extract_query_statute_refs("Art. 32 AsylV 1 und Art. 12 aZG")
    assert refs == {"ART.32.ASYLV1", "ART.12.aZG"}
    assert m._extract_query_statute_refs("Art. 32 AsylV") == {
        "ART.32.ASYLV", "ART.32.ASYLV1", "ART.32.ASYLV2", "ART.32.ASYLV3"}
    assert m._extract_query_statute_refs("Art. 32 AsylV", expand_families=False) == {"ART.32.ASYLV"}
    assert m._parse_statute_ref("ART.12.aZG") == ("12", None, "aZG")
    assert m._parse_statute_ref("ART.29a.ABS.2.ASYLV1") == ("29a", "2", "ASYLV1")


def test_free_text_statute_takes_the_whole_two_word_code():
    assert m._statute_ref_from_free_text("Art. 32 AsylV 1 Nichteintreten") == (
        "ASYLV1", "32", "Nichteintreten")
    assert m._statute_ref_from_free_text("Art. 12 aZG Zollschuld") == ("aZG", "12", "Zollschuld")
