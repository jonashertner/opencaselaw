"""Offline tests for the two stats fields added for /stats/ (2026-09-17):
duplicates_by_court (manifest ⋈ decisions) and practice_coverage (practice.db)."""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import generate_stats as gs  # noqa: E402


def _decisions_db(path: Path, rows):
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE decisions (decision_id TEXT PRIMARY KEY, court TEXT, canton TEXT, decision_date TEXT)")
    c.executemany("INSERT INTO decisions VALUES (?,?,?,?)", rows)
    c.commit(); c.close()


def _manifest_db(path: Path, members, total_rows, dup, band=0):
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE decision_representations (canonical_decision_id TEXT, member_decision_id TEXT, canton TEXT, relation_type TEXT, evidence_method TEXT, confidence REAL, date_match INTEGER)")
    c.executemany("INSERT INTO decision_representations VALUES (?,?,?,?,?,?,?)", members)
    c.execute("CREATE TABLE manifest_meta (key TEXT PRIMARY KEY, value TEXT)")
    c.executemany("INSERT INTO manifest_meta VALUES (?,?)", [
        ("algo_version", "test"), ("source_total_rows", str(total_rows)),
        ("duplicate_representations", str(dup)), ("band_unlinked_date_disagree", str(band))])
    c.commit(); c.close()


def test_duplicates_by_court_attributes_members_to_their_court(tmp_path):
    db = tmp_path / "decisions.db"
    _decisions_db(db, [
        ("ge_gerichte_A_1", "ge_gerichte", "GE", "2024-01-01"),
        ("ge_gerichte_B_1", "ge_gerichte", "GE", "2024-01-01"),   # member of A_1
        ("ge_gerichte_C_2", "ge_gerichte", "GE", "2024-02-02"),
        ("ge_gerichte_D_2", "ge_gerichte", "GE", "2024-02-02"),   # member of C_2
        ("ch_vb_X", "ch_vb", "CH", "2001-01-01"),
        ("ch_vb_X_dup", "ch_vb", "CH", "2001-01-01"),             # byte-identical twin
        ("bger_1", "bger", "CH", "2024-03-03"),
    ])
    _manifest_db(tmp_path / "representation_manifest.db", [
        ("ge_gerichte_A_1", "ge_gerichte_A_1", "GE", "judgment", "shared_source_url", 1.0, 1),
        ("ge_gerichte_A_1", "ge_gerichte_B_1", "GE", "publication", "shared_source_url", 1.0, 1),
        ("ge_gerichte_C_2", "ge_gerichte_C_2", "GE", "judgment", "shared_source_url", 1.0, 1),
        ("ge_gerichte_C_2", "ge_gerichte_D_2", "GE", "publication", "shared_source_url", 1.0, 1),
        ("ch_vb_X", "ch_vb_X", "CH", "canonical", "byte_identical", 1.0, 1),
        ("ch_vb_X", "ch_vb_X_dup", "CH", "member", "byte_identical", 1.0, 1),
    ], total_rows=7, dup=3)
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    out = gs._representation_dual_count(db, conn)
    assert out["unique_decisions"] == 4 and out["unique_decisions_status"] == "current"
    assert out["duplicates_by_court"] == {"ge_gerichte": 2, "ch_vb": 1}
    assert sum(out["duplicates_by_court"].values()) == out["duplicate_representations"]
    # the connection is left usable and the manifest detached
    assert conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 7
    assert "repmani" not in {r[1] for r in conn.execute("PRAGMA database_list")}
    json.dumps(out)


def test_dual_count_without_manifest_has_no_by_court(tmp_path):
    db = tmp_path / "decisions.db"
    _decisions_db(db, [("bger_1", "bger", "CH", "2024-01-01")])
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    out = gs._representation_dual_count(db, conn)
    assert "duplicates_by_court" not in out


def test_practice_coverage_counts_and_groups(tmp_path):
    out_dir = tmp_path / "output"; out_dir.mkdir()
    c = sqlite3.connect(out_dir / "practice.db")
    c.execute("CREATE TABLE practice (doc_id TEXT PRIMARY KEY, source TEXT, issuing_authority TEXT, language TEXT, title TEXT)")
    c.executemany("INSERT INTO practice VALUES (?,?,?,?,?)", [
        ("a", "bsv_weisungen", "BSV", "de", "WML"), ("b", "bsv_weisungen", "BSV", "fr", "DAM"),
        ("c", "finma_rs", "FINMA", "de", "RS 2013/3"), ("d", "seco_arg", "SECO", None, "ArG"),
    ])
    c.commit(); c.close()
    pc = gs._practice_coverage(tmp_path)
    assert pc["total_documents"] == 4 and pc["sources"] == 3
    assert pc["by_source"] == {"bsv_weisungen": 2, "finma_rs": 1, "seco_arg": 1}
    assert pc["by_authority"]["BSV"] == 2
    assert pc["by_language"] == {"de": 2, "fr": 1}   # NULL language rows are not a bucket
    json.dumps(pc)


def test_practice_coverage_is_none_when_missing_or_wrong_shape(tmp_path, monkeypatch):
    monkeypatch.delenv("SWISS_CASELAW_PRACTICE_DB", raising=False)
    assert gs._practice_coverage(tmp_path) is None                       # no file
    out_dir = tmp_path / "output"; out_dir.mkdir()
    c = sqlite3.connect(out_dir / "practice.db"); c.execute("CREATE TABLE other (x)"); c.commit(); c.close()
    assert gs._practice_coverage(tmp_path) is None                       # no practice table
    (out_dir / "practice.db").write_bytes(b"not a database at all")
    assert gs._practice_coverage(tmp_path) is None                       # corrupt file, no raise
