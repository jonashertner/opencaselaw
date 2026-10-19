"""GitHub #83: `cite` resolved BGE 76 II 346 while `attest_response` reported the
same reference as absent from the corpus, i.e. a correct citation flagged as
fabricated — the worst thing an attestation tool can do.

Cause: attest_response does not pass the reference to the resolver. It builds a
decision_id from the citation first ("bge_BGE_76_II_346", hardcoded prefixed
form) and resolves that. `_bge_ref_candidates`, which is what lets one BGE
reference match either stored id shape, only parsed the human reference form, so
on an id-shaped input it returned no candidates at all. A decision stored only as
"bge_76_II_346" therefore never matched.

The fix widens that one shared helper to accept both stored id shapes, rather
than adding a third normaliser beside the two that exist. Note this is NOT the
same defect as a decision being stored under two ids at once: BGE 76 II 346
exists under exactly one id, and the resolver was looking for the other one.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import mcp_server as m  # noqa: E402


def _db(path):
    c = sqlite3.connect(path)
    c.execute(
        "CREATE TABLE decisions(decision_id TEXT PRIMARY KEY, court TEXT, "
        "docket_number TEXT, decision_date TEXT, language TEXT, full_text TEXT)"
    )
    # Stored under the BARE form only — the shape attest_response never guessed.
    c.execute("INSERT INTO decisions VALUES('bge_76_II_346','bge','76 II 346','1950-06-01','de','x')")
    # Stored under the PREFIXED form only — the mirror image of the same bug.
    c.execute("INSERT INTO decisions VALUES('bge_BGE_129_III_320','bge','129 III 320','2003-01-01','de','y')")
    c.execute("INSERT INTO decisions VALUES('bger_4A_231_2014','bger','4A_231/2014','2014-09-23','de','z')")
    c.commit()
    c.close()
    return path


def _rconn(p):
    c = sqlite3.connect(p)
    c.row_factory = sqlite3.Row
    return c


def _patched(tmp_path, monkeypatch):
    # Build the fixture ONCE; get_db is called per lookup, so creating the
    # schema inside the lambda would re-run CREATE TABLE on the second call.
    dbp = _db(tmp_path / "d.db")
    monkeypatch.setattr(m, "get_db", lambda: _rconn(dbp))


def test_prefixed_guess_resolves_a_bare_stored_id(tmp_path, monkeypatch):
    # The exact #83 reproduction: attest_response's guess is the prefixed form.
    _patched(tmp_path, monkeypatch)
    assert m._resolve_decision_id_strict("bge_BGE_76_II_346") == "bge_76_II_346"


def test_bare_guess_resolves_a_prefixed_stored_id(tmp_path, monkeypatch):
    _patched(tmp_path, monkeypatch)
    assert m._resolve_decision_id_strict("bge_129_III_320") == "bge_BGE_129_III_320"


def test_reference_form_still_resolves(tmp_path, monkeypatch):
    _patched(tmp_path, monkeypatch)
    assert m._resolve_decision_id_strict("BGE 76 II 346") == "bge_76_II_346"
    assert m._resolve_decision_id_strict("ATF 76 II 346") == "bge_76_II_346"


# ── the candidate helper itself (pure, no DB) ────────────────────────────────

def test_both_id_shapes_expand_to_every_candidate():
    for probe in ("bge_BGE_76_II_346", "bge_76_II_346", "BGE 76 II 346"):
        got = m._bge_ref_candidates(probe)
        assert got == ["bge_BGE_76_II_346", "bge_76_II_346", "bge_76 II 346"], probe


def test_division_letter_suffix_survives_the_id_form():
    assert m._bge_ref_candidates("bge_BGE_116_Ia_28")[0] == "bge_BGE_116_Ia_28"


def test_pinpoint_suffix_still_stripped():
    assert m._bge_ref_candidates("BGE 131 III 12, E. 2.3")[1] == "bge_131_III_12"


def test_non_bge_ids_yield_no_candidates():
    # Underscore-to-space normalisation must not turn other courts' ids into
    # something the BGE tuple regex will accept. A false candidate here would
    # resolve a citation to an unrelated decision, which is worse than a miss.
    for probe in ("bger_4A_231_2014", "mkg_MKGE_16_Nr_1", "6B_1518_2021",
                  "zh_gerichte_131_III_12", "", "bger_"):
        assert m._bge_ref_candidates(probe) == [], probe


# ── the scraper's id for volumes 1-79 (user report 2026-10-07) ───────────────
# The DFR scraper mints "bge_historical_78_IV_83"; the build serves the row as
# "bge_78_IV_83" (build_fts5.ID_PREFIX_REMAP). Anyone holding the scraper form
# (dataset mirror, structure parquet) got "Decision not found".

def test_historical_scraper_id_expands_to_the_served_id():
    got = m._bge_ref_candidates("bge_historical_78_IV_83")
    assert "bge_78_IV_83" in got
    assert got == m._bge_ref_candidates("BGE 78 IV 83")


def test_historical_scraper_id_resolves(tmp_path, monkeypatch):
    _patched(tmp_path, monkeypatch)
    assert m._resolve_decision_id_strict("bge_historical_76_II_346") == "bge_76_II_346"
    assert m._resolve_decision_id("bge_historical_76_II_346") == "bge_76_II_346"
