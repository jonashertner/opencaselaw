"""One docket -> decision_id resolver (decision_ref) for the identifiers users type.

Every typed shape below is one measured on the production capture journal
(2026-09-06..09) as a miss of get_decision / cite / check_claim_support /
find_citations, and every stored id is a shape observed in the corpus or, for
the AGer-Z yearbook, the id the scraper in the tree mints (pinned below
against models.make_decision_id and the scraper's COURT):

  bare cantonal dockets      UH220412, ACPR/635/2024, VB.2025.0683, IV.2006.00678
  dotted pre-2007 BGer       1A.235/2000 -> bger_1A.235_2000
  EVG two-digit years        I 123/04 -> bger_I_123_04
  federal collections        BVGer A-5274/2023, SK.2023.12, O2023_001
  ZH yearbook                AGer-Z 2023 Nr. 7
  ECtHR application number   12345/09, n° 22060/20
  percent-encoded ids        bge_127%20I%2038
  ZH VGr double underscore   zh_verwaltungsgericht__VB.2025.00683
  GitHub #76                 the citation string '4A_123/2024' for a row whose
                             docket is stored '4A 123/2024' round-trips

Plus the honest not-found outcomes: a pre-2000 BGer docket shape is reported
as never_published_online (docs/coverage_notes.json), not as a bare miss —
conditionally ('looks like ...; no decision with this number is in the
corpus'), never asserting that the decision exists — and a shape that is also
a cantonal number ('P 123 1999': EVG, Geneva, Vaud) is reported as ambiguous
with every candidate, never resolved to one court.
"""
from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import decision_ref as d  # noqa: E402
import mcp_server as m  # noqa: E402

ROWS = [
    # decision_id, docket_number, court, canton, date
    ("zh_obergericht_UH220412", "UH220412", "zh_obergericht", "ZH", "2023-01-10"),
    ("ge_gerichte_ACPR_635_2024", "ACPR/635/2024", "ge_gerichte", "GE", "2024-09-02"),
    ("zh_verwaltungsgericht__VB.2025.00683", "VB.2025.00683", "zh_verwaltungsgericht", "ZH", "2026-01-15"),
    ("zh_sozialversicherungsgericht_IV.2006.00678", "IV.2006.00678", "zh_sozialversicherungsgericht", "ZH", "2007-03-01"),
    ("bger_1A.235_2000", "1A.235/2000", "bger", "CH", "2001-02-14"),
    ("bger_2P.123_2004", "2P 123/2004", "bger", "CH", "2004-11-30"),
    ("bger_I_123_04", "I 123/04", "bger", "CH", "2005-06-01"),
    ("bger_4A_123_2024", "4A 123/2024", "bger", "CH", "2024-08-20"),
    ("bvger_A-5274_2023", "A-5274/2023", "bvger", "CH", "2024-03-05"),
    ("bstger_SK.2023.12", "SK.2023.12", "bstger", "CH", "2023-10-10"),
    ("bpatger_O2023_001", "O2023_001", "bpatger", "CH", "2023-05-05"),
    ("zh_arbeitsgericht_AGer-Z 2023 Nr. 7", "AGer-Z 2023 Nr. 7", "zh_arbeitsgericht", "ZH", "2023-04-04"),
    ("hudoc_ch_12345_09", "12345/09", "hudoc_ch", "CH", "2015-07-07"),
    ("ecthr_chamber_22060_20_20230613", "22060/20_20230613", "ecthr_chamber", None, "2023-06-13"),
    ("bge_BGE_127_I_38", "127 I 38", "bge", "CH", "2000-12-01"),
    ("bge_BGE_131_III_121", "131 III 121", "bge", "CH", "2004-12-10"),
]


def _fixture(path: Path) -> str:
    c = sqlite3.connect(path)
    c.execute(
        "CREATE TABLE decisions(decision_id TEXT PRIMARY KEY, docket_number TEXT, court TEXT, "
        "canton TEXT, decision_date TEXT, language TEXT, title TEXT, full_text TEXT, regeste TEXT, "
        "source_url TEXT, pdf_url TEXT, json_data TEXT)"
    )
    c.execute("CREATE INDEX idx_decisions_docket ON decisions(docket_number)")
    for did, docket, court, canton, date in ROWS:
        c.execute(
            "INSERT INTO decisions VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (did, docket, court, canton, date, "de", "t", f"Text of {did}. " * 20, "", "https://x", None, None),
        )
    c.commit()
    c.close()
    return str(path)


@pytest.fixture
def db(tmp_path, monkeypatch):
    dbp = _fixture(tmp_path / "decisions.db")

    def get_db():
        c = sqlite3.connect(dbp)
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setattr(m, "get_db", get_db)
    monkeypatch.setattr(m, "CANONICAL_DB_PATH", tmp_path / "missing.db")
    monkeypatch.setattr(m, "_canonical_warned", False, raising=False)
    for name in ("_capture_event", "_record_tool_call", "_record_tool_outcome", "_record_query"):
        if hasattr(m, name):
            monkeypatch.setattr(m, name, lambda *a, **k: None)
    monkeypatch.setattr(m, "_fetch_structure_paragraphs", lambda *a, **k: [])
    monkeypatch.setattr(m, "_rule_statement", lambda *a, **k: None)
    monkeypatch.setattr(m, "_docket_close_matches", lambda *a, **k: [])
    monkeypatch.setattr(m, "search_fts5", lambda **k: ([], None))
    monkeypatch.setattr(m, "_overlay_enabled", lambda: False)
    return dbp


# ── the pure resolver: court inference from docket grammar ───────────────

@pytest.mark.parametrize("typed, first", [
    ("UH220412", "zh_obergericht_UH220412"),
    ("UH 220412", "zh_obergericht_UH220412"),
    ("HG120273", "zh_handelsgericht_HG120273"),
    ("ACPR/635/2024", "ge_gerichte_ACPR_635_2024"),
    ("ATA/508/2014", "ge_gerichte_ATA_508_2014"),
    ("HC/2018/391", "vd_findinfo_HC___2018___391"),
    ("VB.2025.0683", "zh_verwaltungsgericht_VB.2025.00683"),
    ("VB.2025.00683", "zh_verwaltungsgericht_VB.2025.00683"),
    ("IV.2006.00678", "zh_sozialversicherungsgericht_IV.2006.00678"),
    ("SK.2023.12", "bstger_SK.2023.12"),
    ("BB.2023.1", "bstger_BB.2023.1"),
    ("CA.2023.1", "bstger_CA.2023.1"),
    ("A-1234/2023", "bvger_A-1234_2023"),
    ("BVGer A-5274/2023", "bvger_A-5274_2023"),
    ("F-5046/2026", "bvger_F-5046_2026"),
    ("O2023_001", "bpatger_O2023_001"),
    ("S2023_001", "bpatger_S2023_001"),
    ("1A.235/2000", "bger_1A_235_2000"),
    ("4A_123/2024", "bger_4A_123_2024"),
    ("4A 123/2024", "bger_4A_123_2024"),
    ("I 123/04", "bger_I_123_04"),
    ("U 12/03", "bger_U_12_03"),
    ("H 45/02", "bger_H_45_02"),
    ("K 9/01", "bger_K_9_01"),
    ("C 3/00", "bger_C_3_00"),
    ("B 1/00", "bger_B_1_00"),
    ("AGer-Z 2023 Nr. 7", "zh_arbeitsgericht_AGer-Z 2023 Nr. 7"),
    ("12345/09", "hudoc_ch_12345_09"),
    ("BGE 148 IV 11", "bge_BGE_148_IV_11"),
    ("148 IV 11", "bge_BGE_148_IV_11"),
    ("zh_NG230021", "zh_obergericht_NG230021"),
    ("BGer 6B 1518/2021 vom 31. Januar 2022", "bger_6B_1518_2021"),
])
def test_court_inferred_from_docket_grammar(typed, first):
    cands = d.resolve_decision_ref(typed)
    assert cands and cands[0] == first, cands[:5]


def test_ager_z_id_is_the_one_the_yearbook_scraper_mints():
    """The space-containing id is not a guess: scrapers/cantonal/
    zh_arbeitsgericht_sammlung.py mints make_decision_id(COURT, 'AGer-Z <year>
    Nr. <n>') for every yearbook ruling (c58bc85a)."""
    from models import make_decision_id
    from scrapers.cantonal import zh_arbeitsgericht_sammlung as yearbook

    assert yearbook.COURT == "zh_arbeitsgericht"
    assert d.resolve_decision_ref("AGer-Z 2023 Nr. 7")[0] == make_decision_id(yearbook.COURT, "AGer-Z 2023 Nr. 7")
    assert d.resolve_decision_ref("AGer-Z 2023 Nr. 07")[0] == make_decision_id(yearbook.COURT, "AGer-Z 2023 Nr. 7")


def test_dotted_bger_docket_yields_both_stored_spellings():
    assert d.resolve_decision_ref("1A.235/2000") == ["bger_1A_235_2000", "bger_1A.235_2000"]
    # and the canonical id typed with the modern underscore reaches the dotted row
    assert "bger_4C.194_2006" in d.resolve_decision_ref("bger_4C_194_2006")


def test_evg_two_digit_year_is_stored_as_printed():
    assert d.resolve_decision_ref("I 123/04")[:2] == ["bger_I_123_04", "bger_I_123_2004"]
    assert d.resolve_decision_ref("I_123/2004")[0] == "bger_I_123_04"


def test_percent_encoded_and_separator_variants_of_canonical_ids():
    assert d.resolve_decision_ref("bge_127%20I%2038") == ["bge_127 I 38", "bge_127_I_38", "bge_BGE_127_I_38"]
    # decoded, pinpoint stripped: the exact id leads, then its spellings
    assert d.resolve_decision_ref("bge_BGE_127_I_38%2C%20E.%202")[0] == "bge_BGE_127_I_38"
    assert d.resolve_decision_ref("bger_4A 123/2024")[-2:] == ["bger_4A_123_2024", "bger_4A.123_2024"]
    assert d.resolve_decision_ref("bger 4A_123/2024")[0] == "bger_4A_123_2024"


def test_zh_verwaltungsgericht_double_underscore_both_ways():
    a = d.resolve_decision_ref("zh_verwaltungsgericht__VB.2022.00753")
    b = d.resolve_decision_ref("zh_verwaltungsgericht_VB.2022.00753")
    assert a[0] == "zh_verwaltungsgericht_VB.2022.00753" and b[0] == "zh_verwaltungsgericht__VB.2022.00753"
    # the canton fallback stays inside ZH (the ids were re-homed between ZH courts before)
    assert all(c.startswith("zh_") for c in a + b)


def test_a_named_court_or_canton_restricts_the_candidates():
    assert all(c.startswith("zh_") for c in d.resolve_decision_ref("Obergericht ZH LA210005 vom 15. Juni 2021"))
    assert d.resolve_decision_ref("Cour de justice de Genève, arrêt ACPR/635/2024") == ["ge_gerichte_ACPR_635_2024"]
    assert all(c.startswith("bger_") for c in d.resolve_decision_ref("BGer 4A_123/2024"))


def test_a_lowercase_prose_word_is_not_read_as_a_court_prefix():
    """'urteil 4A_123/2024' must reach the docket grammar, not probe
    'urteil_4A_123_2024'; only heads that are real court codes are prefixes."""
    assert d.resolve_decision_ref("urteil 4A_123/2024")[0] == "bger_4A_123_2024"
    assert not any(c.startswith("urteil") for c in d.resolve_decision_ref("urteil 4A_123/2024"))
    assert d.resolve_decision_ref("art 5 EMRK") == []
    assert d._court_of("urteil_4A_123_2024") == ""
    for head in ("bger", "zh", "ch", "hudoc", "ecthr", "weko", "finma"):
        assert head in d.ID_HEADS


def test_nothing_is_guessed_for_text_that_is_not_a_reference():
    for text in ("INVALID", "Zubac v. Croatia", "SO StPG § 27bis", "", None, "x" * 300, "1 %"):
        assert d.resolve_decision_ref(text) == []
    # a longer BGE page is never a candidate of a shorter one (bug C-1 property)
    assert "bge_BGE_131_III_121" not in d.resolve_decision_ref("131 III 12")


def test_application_number_from_prose_only_when_labelled():
    assert d.application_number("CourEDH SPERISEN c. SUISSE, n° 22060/20") == "22060/20"
    assert d.application_number("application no. 34812/15") == "34812/15"
    assert d.application_number("17153/11") == "17153/11"
    assert d.application_number("A/279/2011") is None  # a Geneva case number is not an application


def test_every_corpus_court_code_parses_as_an_id_prefix():
    courts = [c["court"] for c in json.loads((REPO / "docs" / "coverage.json").read_text())["courts"]]
    assert courts
    for court in courts:
        assert d._court_of(f"{court}_X1") == court, court


# ── wired into the server's lookup ladder (PK lookups, no LIKE scan) ─────

@pytest.mark.parametrize("typed, expected", [
    ("UH220412", "zh_obergericht_UH220412"),
    ("ACPR/635/2024", "ge_gerichte_ACPR_635_2024"),
    ("VB.2025.0683", "zh_verwaltungsgericht__VB.2025.00683"),
    ("zh_verwaltungsgericht_VB.2025.00683", "zh_verwaltungsgericht__VB.2025.00683"),
    ("IV.2006.00678", "zh_sozialversicherungsgericht_IV.2006.00678"),
    ("1A.235/2000", "bger_1A.235_2000"),
    ("1A_235/2000", "bger_1A.235_2000"),
    ("bger_1A_235_2000", "bger_1A.235_2000"),
    ("2P.123/2004", "bger_2P.123_2004"),
    ("I 123/04", "bger_I_123_04"),
    ("I_123/2004", "bger_I_123_04"),
    ("4A_123/2024", "bger_4A_123_2024"),
    ("4A 123/2024", "bger_4A_123_2024"),
    ("BVGer A-5274/2023", "bvger_A-5274_2023"),
    ("A-5274/2023", "bvger_A-5274_2023"),
    ("SK.2023.12", "bstger_SK.2023.12"),
    ("O2023_001", "bpatger_O2023_001"),
    ("AGer-Z 2023 Nr. 7", "zh_arbeitsgericht_AGer-Z 2023 Nr. 7"),
    ("12345/09", "hudoc_ch_12345_09"),
    ("22060/20", "ecthr_chamber_22060_20_20230613"),
    ("CourEDH SPERISEN c. SUISSE, n° 22060/20", "ecthr_chamber_22060_20_20230613"),
    ("bge_127%20I%2038", "bge_BGE_127_I_38"),
    ("bge_BGE_127_I_38", "bge_BGE_127_I_38"),
])
def test_resolve_decision_id_finds_the_typed_identifier(db, typed, expected):
    assert m._resolve_decision_id(typed) == expected
    assert m.get_decision_by_id(typed)["decision_id"] == expected


def test_no_like_scan_is_needed_for_the_typed_shapes(db, monkeypatch):
    """The LIKE fallback is what cost ~2 s per miss; the resolver must hit
    before it. Traced at the SQL level (every statement the lookup runs), so
    the check holds for canonical-prefixed inputs too, which the LIKE gate
    short-circuits before _input_is_docket_like is ever consulted."""
    statements: list[str] = []
    real_get_db = m.get_db

    def traced_get_db():
        conn = real_get_db()
        conn.set_trace_callback(statements.append)
        return conn

    monkeypatch.setattr(m, "get_db", traced_get_db)
    for typed in ("UH220412", "VB.2025.0683", "1A_235/2000", "bge_127%20I%2038", "I_123/2004"):
        statements.clear()
        assert m._resolve_decision_id(typed) != typed, typed
        assert statements, typed
        assert not any(" like " in sql.lower() for sql in statements), (typed, statements)


def test_get_decision_reports_what_it_resolved_from(db):
    out = m.get_decision_by_id("UH220412")
    assert out["decision_id"] == "zh_obergericht_UH220412"
    assert out["resolved_from"] == "UH220412"
    assert "resolved_from" not in m.get_decision_by_id("zh_obergericht_UH220412")


def test_strict_resolver_accepts_typed_identifiers_without_a_scan(db):
    assert m._resolve_decision_id_strict("1A.235/2000") == "bger_1A.235_2000"
    assert m._resolve_decision_id_strict("UH220412") == "zh_obergericht_UH220412"
    assert m._resolve_decision_id_strict("VB.2025.0683") == "zh_verwaltungsgericht__VB.2025.00683"
    assert m._resolve_decision_id_strict("UH999999") is None


def test_unknown_inputs_still_fall_through_unchanged(db):
    assert m._resolve_decision_id("INVALID") == "INVALID"
    assert m._resolve_decision_id("UH999999") == "UH999999"
    assert m.get_decision_by_id("Zubac v. Croatia") is None


# ── GitHub #76: the space form of our own BGer citation string round-trips ──

def test_issue_76_citation_string_round_trips_to_the_space_stored_row(db):
    row = m.get_decision_by_id("bger_4A_123_2024")
    strings = m._build_citation_strings(row)
    assert "4A_123/2024" in strings["citation_string_de"] and "4A 123/2024" not in strings["citation_string_de"]
    for s in (strings["citation_string_de"], strings["citation_string_fr"], strings["citation_string_it"],
              "4A_123/2024", "4A 123/2024", "bger_4A_123_2024", "bger_4A 123/2024"):
        assert m._resolve_decision_id(s) == "bger_4A_123_2024", s
    out = m._handle_cite(reference=strings["citation_string_de"])
    assert out["exists"] is True and out["decision_id"] == "bger_4A_123_2024"
    assert out["citation_string_de"] == strings["citation_string_de"]
    assert out["identity"]["method"] in ("exact_server_citation", "exact_docket")
    assert out["resolved_from"] == strings["citation_string_de"]


# ── cite / check_claim_support / find_citations carry resolved_from ──────

def test_cite_resolves_typed_dockets_and_says_so(db):
    for typed, expected in [("UH220412", "zh_obergericht_UH220412"),
                            ("1A.235/2000", "bger_1A.235_2000"),
                            ("ACPR/635/2024", "ge_gerichte_ACPR_635_2024"),
                            ("bge_127%20I%2038", "bge_BGE_127_I_38")]:
        out = m._handle_cite(reference=typed)
        assert out["exists"] is True and out["decision_id"] == expected, (typed, out)
        assert out["resolved_from"] == d.percent_decode(typed)
    assert "resolved_from" not in m._handle_cite(reference="bger_1A.235_2000")


def test_cite_accepts_an_ecthr_application_number_as_the_carried_label(db):
    out = m._handle_cite(reference="22060/20")
    assert out["exists"] is True and out["decision_id"] == "ecthr_chamber_22060_20_20230613"
    assert out["identity"]["method"] == "exact_application_number"
    out = m._handle_cite(reference="CourEDH SPERISEN c. SUISSE, n° 22060/20")
    assert out["exists"] is True and out["identity"]["label"] == "22060/20"


def test_check_claim_support_and_find_citations_carry_resolved_from(db, monkeypatch):
    monkeypatch.setattr(m, "ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(m, "_get_graph_conn", lambda: None)
    out = m.find_citations(decision_id="UH220412")
    assert out["decision_id"] == "zh_obergericht_UH220412" and out["resolved_from"] == "UH220412"
    assert "resolved_from" not in m.find_citations(decision_id="zh_obergericht_UH220412")
    # check_claim_support: the resolver runs before the judge; a miss is a miss.
    out = m._handle_check_claim_support(claim="x", decision_id="UH999999")
    assert out["error"].startswith("Decision not found") and "error_code" not in out


# ── honest not-found: pre-2000 BGer judgments were never put online ──────

@pytest.mark.parametrize("typed, code, year", [
    ("4C.24/1992", "never_published_online", 1992),
    ("5P.218/1997", "never_published_online", 1997),
    ("BGer 1P.530/1994", "never_published_online", 1994),
    ("bger_2A_4_1998", "never_published_online", 1998),
    ("H_302/03", "not_yet_ingested", 2003),
    ("P 33/96", "not_yet_ingested", 1996),
])
def test_unavailable_reason_from_the_coverage_notes(typed, code, year):
    reason = d.unavailable_reason(typed)
    assert reason["error_code"] == code and reason["year"] == year and reason["court"] == "bger"
    assert reason["coverage_note"] == d.BGER_COVERAGE_NOTE
    if code == "never_published_online":
        assert "did not publish its ordinary judgments online before 2000" in reason["reason"]
    else:
        assert "EVG backlog is only partly ingested" in reason["reason"]
    assert "no decision with this number is in the corpus" in reason["reason"]


_EXISTENCE_CLAIMS = re.compile(
    r"\bis an? (?:Federal Supreme Court|EVG|Eidgen)|\bexists? solely\b|\bthis one is not indexed\b",
    re.IGNORECASE,
)


@pytest.mark.parametrize("typed", ["4C.9999/1995", "5P.218/1997", "bger_2A_4_1998", "H_302/03", "P 33/96",
                                   "P 123 1999", "H 302 2003"])
def test_not_found_prose_never_asserts_that_the_decision_exists(typed):
    """A hallucinated '4C.9999/1995' must not come back confirmed as a real
    1995 judgment: every reason is conditional on the SHAPE of the text."""
    reason = d.unavailable_reason(typed)
    assert reason is not None
    for field in ("reason", "hint"):
        assert not _EXISTENCE_CLAIMS.search(reason[field]), (typed, field, reason[field])
    assert "no decision with this number is in the corpus" in reason["reason"].lower()
    assert reason["error_code"] == "ambiguous_reference" or "looks like" in reason["reason"]
    assert "verified" in reason["hint"]


# ── ambiguous shapes: every candidate, never one court ───────────────────

@pytest.mark.parametrize("typed", ["P 123 1999", "P_123_1999", "H 302 2003"])
def test_an_ambiguous_shape_is_reported_with_all_candidates_never_one_court(typed):
    """'P 123 1999' is an EVG docket (P = EL chamber, 1999) AND a Geneva /
    Vaud case number; the resolver tries all of them, so the miss must say so
    instead of declaring it an EVG decision (reviewer finding)."""
    reason = d.unavailable_reason(typed)
    assert reason["error_code"] == "ambiguous_reference"
    assert "court" not in reason  # no single court is chosen
    assert reason["candidates"] == d.resolve_decision_ref(typed)
    assert {"bger", "ge_gerichte", "vd_findinfo", "vd_gerichte"} <= set(reason["courts"])
    assert reason["courts"] == list(dict.fromkeys(d._court_of(c) for c in reason["candidates"]))
    assert "more than one docket shape" in reason["reason"] and "Geneva" in reason["reason"]
    assert "Name the court" in reason["hint"]


def test_naming_the_court_disambiguates():
    for typed in ("EVG P 123 1999", "BGer P 123 1999", "bger_P_123_1999", "P 123/1999", "P 33/96"):
        reason = d.unavailable_reason(typed)
        assert reason["error_code"] == "not_yet_ingested" and reason["court"] == "bger", typed
        assert all(c.startswith("bger_") for c in d.resolve_decision_ref(typed)), typed
    # a named canton is a restriction: the miss is then a plain cantonal miss
    assert d.resolve_decision_ref("Cour de justice GE P 123 1999") == ["ge_gerichte_P_123_1999"]
    assert d.unavailable_reason("Cour de justice GE P 123 1999") is None
    assert d.unavailable_reason("BVGer P 123 1999") is None


def test_ambiguous_shape_reaches_every_tool_with_the_candidates(db, monkeypatch):
    out = m._handle_cite(reference="P 123 1999")
    assert out["exists"] is False and out["not_found_reason"] == "ambiguous_reference"
    assert "coverage_note" not in out and out["candidates"] == d.resolve_decision_ref("P 123 1999")
    assert "ge_gerichte" in out["courts"] and "bger" in out["courts"]
    content, payload = asyncio.run(m._handle_call_tool_inner("get_decision", {"decision_id": "P 123 1999"}))
    assert payload["error_code"] == "ambiguous_reference" and "court" not in payload
    assert "more than one docket shape" in content[0].text
    monkeypatch.setattr(m, "ANTHROPIC_API_KEY", "test-key")
    out = m._handle_check_claim_support(claim="x", decision_id="P 123 1999")
    assert out["error_code"] == "ambiguous_reference" and len(out["candidates"]) >= 3
    monkeypatch.setattr(m, "_get_graph_conn", lambda: None)
    out = m.find_citations(decision_id="P 123 1999")
    assert out["error_code"] == "ambiguous_reference" and "outgoing" not in out


def test_no_reason_is_invented_for_covered_years_or_other_courts():
    for typed in ("1A.235/2000", "4A_123/2024", "UH220412", "I 123/2007", "BGE 80 IV 33", "INVALID"):
        assert d.unavailable_reason(typed) is None


def test_a_stored_pre_2000_row_still_resolves(db, tmp_path):
    """The reason is only for a MISS: the corpus holds a few 1986-1999 rulings."""
    c = sqlite3.connect(db)
    c.execute("INSERT INTO decisions(decision_id, docket_number, court, canton, decision_date, language, full_text)"
              " VALUES ('bger_4C.24_1992','4C.24/1992','bger','CH','1992-05-05','de','t')")
    c.commit()
    c.close()
    assert m._resolve_decision_id("4C.24/1992") == "bger_4C.24_1992"
    assert m._handle_cite(reference="4C.24/1992")["exists"] is True


def test_get_decision_tool_returns_the_structured_reason(db):
    content, payload = asyncio.run(m._handle_call_tool_inner("get_decision", {"decision_id": "4C.24/1992"}))
    assert payload["error"].startswith("Decision not found")
    assert payload["error_code"] == "never_published_online" and payload["year"] == 1992
    assert "did not publish its ordinary judgments online before 2000" in content[0].text
    assert "looks like a Federal Supreme Court docket from 1992" in content[0].text
    content, payload = asyncio.run(m._handle_call_tool_inner("get_decision", {"decision_id": "UH999999"}))
    assert payload == {"error": "Decision not found: UH999999"}


def test_cite_check_claim_and_find_citations_return_the_reason(db, monkeypatch):
    out = m._handle_cite(reference="5P.218/1997")
    assert out["exists"] is False and out["not_found_reason"] == "never_published_online"
    assert "citation_string" not in out and "1997" in out["reason"]
    monkeypatch.setattr(m, "ANTHROPIC_API_KEY", "test-key")
    out = m._handle_check_claim_support(claim="x", decision_id="4C.24/1992")
    assert out["error_code"] == "never_published_online" and out["docket"] == "4C.24/1992"
    monkeypatch.setattr(m, "_get_graph_conn", lambda: None)
    probes: list[str] = []
    real_exists = m._decision_exists
    monkeypatch.setattr(m, "_decision_exists", lambda did: probes.append(did) or real_exists(did))
    out = m.find_citations(decision_id="P 33/96")
    assert out["error_code"] == "not_yet_ingested" and out["error"].startswith("Decision not found")
    assert "outgoing" not in out and probes == ["P 33/96"]
    # the common path — a stored canonical id — pays for no extra PK probe
    probes.clear()
    out = m.find_citations(decision_id="bger_4A_123_2024")
    assert probes == [] and "error_code" not in out
    # a stored pre-2000 row is served, not explained away
    m.get_db().execute("INSERT INTO decisions(decision_id, docket_number, court, canton, decision_date, language, full_text)"
                       " VALUES ('bger_4C.24_1992','4C.24/1992','bger','CH','1992-05-05','de','t')").connection.commit()
    out = m.find_citations(decision_id="bger_4C.24_1992")
    assert "error_code" not in out and out["decision_id"] == "bger_4C.24_1992"
