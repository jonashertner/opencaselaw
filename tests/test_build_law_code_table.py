"""Law-code table corrections from the 2026-10-26 anomaly report.

A downstream graph builder measured, per code, the share of cited article
numbers that do not exist in the act the table resolved the code to. Three
classes came out wrong: Fedlex abbreviations the corpus uses for another
act (OPC), codes merged by upper-casing (KLV / KlV), and codes borne only
by repealed acts, which statutes.db does not hold (SGG, GestG, BEHG). The
fixtures below are those acts with their real SR numbers and dates.
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

IN_FORCE = "https://fedlex.data.admin.ch/vocabulary/enforcement-status/0"
REPEALED = "https://fedlex.data.admin.ch/vocabulary/enforcement-status/3"
ELI = "https://fedlex.data.admin.ch/eli/cc/"

LAWS = [
    ("281.41", "VVAG", "OPC", "ODiC", ELI + "39/77_77_77"),
    ("831.301", "ELV", "OPC-AVS/AI", "OPC-AVS/AI", ELI + "1971/29_29_29"),
    ("832.112.31", "KLV", "OPAS", "OPre", ELI + "1995/4964_4964_4964"),
    ("814.310.1", "KlV", "OCI", "OOCli", ELI + "2024/773"),
    ("641.10", "StG", "LT", "LTB", ELI + "1974/11_11_11"),
    ("272", "ZPO", "CPC", "CPC", ELI + "2010/262"),
    ("831.30", "ELG", "LPC", "LPC", ELI + "2007/804"),
]

WORKS = [
    {"work": ELI + "1995/4964_4964_4964", "srNumber": "832.112.31",
     "inForceStatus": IN_FORCE, "dateEntryInForce": "1996-01-01"},
    {"work": ELI + "2024/773", "srNumber": "814.310.1",
     "inForceStatus": IN_FORCE, "dateEntryInForce": "2025-01-01"},
    # Gerichtsstandsgesetz: repealed, its SR number now the ZPO's.
    {"work": ELI + "2000/465", "srNumber": "272", "inForceStatus": REPEALED,
     "dateEntryInForce": "2001-01-01", "dateNoLongerInForce": "2011-01-01"},
    # ELG 1965: repealed predecessor sharing the code with the 2006 act.
    {"work": ELI + "1966/1_1_1", "srNumber": "831.30", "inForceStatus": REPEALED,
     "dateEntryInForce": "1966-01-01", "dateNoLongerInForce": "2008-01-01"},
    # Flag still "in force", but the end date has passed: repealed.
    {"work": ELI + "2002/280", "srNumber": "173.71", "inForceStatus": IN_FORCE,
     "dateEntryInForce": "2003-08-01", "dateNoLongerInForce": "2011-01-01"},
    # Ambiguous Fedlex record (one row in force, one repealed): left out.
    {"work": ELI + "2010/1", "srNumber": "951.26", "inForceStatus": IN_FORCE},
    {"work": ELI + "2010/1", "srNumber": "951.26", "inForceStatus": REPEALED},
    # Repealed act whose short title collides with usage (TVA = VAT).
    {"work": ELI + "1991/1_1", "srNumber": "814.600", "inForceStatus": REPEALED,
     "dateEntryInForce": "1991-02-01", "dateNoLongerInForce": "2016-01-01"},
]

META = {
    ELI + "2000/465": {"abbr_de": "GestG", "abbr_fr": "LFors", "abbr_it": "LForo",
                       "title_de": "Gerichtsstandsgesetz"},
    ELI + "1966/1_1_1": {"abbr_de": "ELG", "abbr_fr": "LPC", "abbr_it": "LPC",
                         "title_de": "ELG 1965"},
    ELI + "2002/280": {"abbr_de": "SGG", "abbr_fr": "LTPF", "abbr_it": "LTPF",
                       "title_de": "Strafgerichtsgesetz"},
    ELI + "2010/1": {"abbr_de": "XYZ"},
    ELI + "1991/1_1": {"abbr_de": "TVA", "abbr_fr": "OTD", "title_de": "TVA"},
}


def _payload(tmp_path: Path) -> dict:
    db = tmp_path / "statutes.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE laws(sr_number, abbr_de, abbr_fr, abbr_it, "
                "title_de, title_fr, title_it, work_uri)")
    con.executemany("INSERT INTO laws VALUES (?,?,?,?,?,NULL,NULL,?)",
                    [(sr, de, fr, it, f"Act {sr}", w) for sr, de, fr, it, w in LAWS])
    con.commit()
    con.close()
    return blt.build_payload(blt.federal(str(db)), [],
                             {"fetched": "2026-10-26", "works": WORKS,
                              "repealed_meta": META})


def test_opc_resolves_to_the_opc_avs_ai(tmp_path):
    p = _payload(tmp_path)
    assert p["alias_to_sr"]["OPC"] == "831.301"
    assert p["alias_notes"]["OPC"]["usage_override"]["fedlex_sr"] == "281.41"
    # the act's own row still says what Fedlex says
    vvag = next(r for r in p["federal"] if r["sr_number"] == "281.41")
    assert "OPC" in vvag["aliases"]


def test_klv_case_collision_is_documented_and_resolved(tmp_path):
    p = _payload(tmp_path)
    assert p["alias_to_sr"]["KLV"] == "832.112.31"
    cands = p["alias_notes"]["KLV"]["case_collision"]["candidates"]
    assert {(c["sr_number"], c["in_force_from"]) for c in cands} == {
        ("832.112.31", "1996-01-01"), ("814.310.1", "2025-01-01")}


def test_cantonal_homonym_keeps_the_federal_mapping(tmp_path):
    p = _payload(tmp_path)
    assert p["alias_to_sr"]["STG"] == "641.10"
    assert "cantonal_homonym" in p["alias_notes"]["STG"]


def test_repealed_codes_resolve_to_the_act_not_its_sr_number(tmp_path):
    p = _payload(tmp_path)
    # GestG is never routed to 272 (now the ZPO) through alias_to_sr
    assert "GESTG" not in p["alias_to_sr"]
    [gestg] = [r for r in p["repealed"] if r["eli"] in p["alias_to_repealed"]["GESTG"]]
    assert (gestg["sr_number"], gestg["in_force_from"], gestg["in_force_until"]) == (
        "272", "2001-01-01", "2011-01-01")
    assert p["alias_to_repealed"]["LFORS"] == p["alias_to_repealed"]["GESTG"]


def test_passed_end_date_counts_as_repealed(tmp_path):
    p = _payload(tmp_path)
    assert p["alias_to_repealed"]["SGG"] == [ELI + "2002/280"]


def test_code_borne_by_both_editions_is_in_both_indexes(tmp_path):
    p = _payload(tmp_path)
    assert p["alias_to_sr"]["ELG"] == "831.30"
    assert p["alias_to_repealed"]["ELG"] == [ELI + "1966/1_1_1"]
    assert p["counts"]["repealed_aliases_also_in_force"] >= 2  # ELG, LPC


def test_ambiguous_status_and_excluded_titles_are_left_out(tmp_path):
    p = _payload(tmp_path)
    assert "XYZ" not in p["alias_to_repealed"]
    assert "TVA" not in p["alias_to_repealed"]
    assert "OTD" in p["alias_to_repealed"]
    assert "repealed_alias_excluded" in p["alias_notes"]["TVA"]


def test_no_fedlex_still_builds(tmp_path):
    db = tmp_path / "statutes.db"
    _payload(tmp_path)  # writes the fixture db
    p = blt.build_payload(blt.federal(str(db)), [], None)
    assert p["repealed"] == [] and p["alias_to_repealed"] == {}
    assert p["alias_to_sr"]["OPC"] == "831.301"


def test_published_table_carries_the_corrections():
    """Guards against regenerating docs/data/law_codes.json with an older
    builder or a five-law dev slice of statutes.db."""
    d = json.loads((REPO / "docs" / "data" / "law_codes.json").read_text(encoding="utf-8"))
    assert d["alias_to_sr"]["OPC"] == "831.301"
    assert d["alias_to_sr"]["KLV"] == "832.112.31"
    assert d["counts"]["federal_acts"] > 1000
    assert d["counts"]["repealed_acts"] > 500
    for code in ("SGG", "GESTG", "BEHG", "OLE", "VDSG"):
        assert code in d["alias_to_repealed"], code
    for code in blt.REPEALED_ALIAS_EXCLUDED:
        assert code not in d["alias_to_repealed"], code


def test_two_word_codes_get_a_joined_key(tmp_path):
    p = _payload(tmp_path)
    assert p["compound_codes"] == {}  # the fixture acts are all one-word codes
    assert blt.compound_codes({"BVV 2": "831.441.1"}, {"GEBV SCHKG": ["eli:x"]}) == {
        "BVV 2": "BVV2", "GEBV SCHKG": "GEBVSCHKG"}
