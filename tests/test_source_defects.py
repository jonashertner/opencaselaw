"""Known source defects (source_defects.py): a reference whose stored text is
another ruling's is served without that text, with a note; its structure and
search hits are withheld; cite says why instead of suggesting a neighbour.
Verified list: runbooks/historical_bge_source_errors_2026-10-07.md / .tsv."""
from __future__ import annotations

import csv
import hashlib
import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import mcp_server as m
import source_defects as sd

FOREIGN = "8\nStaatsrecht.\n3. Urteil vom 28. April 1939\ni. S. Neef gegen Staatsanwaltschaft. " * 20


def test_lookup_accepts_ids_and_references():
    for text in ("bge_52_I_8", "bge_historical_52_I_8", "BGE 52 I 8", "52 I 8", "52_I_8", "ATF 52 I 8"):
        assert sd.lookup(text).reference == "52 I 8", text
    assert sd.lookup("bge_52_I_14") is None           # its genuine neighbour
    assert sd.lookup("bge_65_I_8") is None            # the ruling the scan really is
    assert sd.lookup("BGE 140 III 86") is None
    assert sd.lookup("") is None and sd.lookup(None) is None
    assert sd.lookup_parts("52", "i", 149).reference == "52 I 149"
    assert sd.lookup_parts("x", "I", 1) is None


def test_list_matches_the_verified_runbook_list():
    tsv = REPO / "runbooks" / "historical_bge_source_errors_2026-10-07.tsv"
    with open(tsv, encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh, delimiter="\t"))
    verified = {r["decision_id"] for r in rows if not r["class"].startswith("not_source_error")}
    listed = {"bge_" + d.reference.replace(" ", "_") for d in sd.DEFECTS}
    assert listed == verified


def test_withhold_empties_text_and_falls_back_to_the_volume_placeholder():
    row = {"decision_id": "bge_22_I_12", "docket_number": "22_I_12", "full_text": FOREIGN,
           "regeste": "r", "decision_date": "1896-06-29", "date_provenance": "extracted_from_text"}
    out = sd.apply(row)
    assert out is not row and row["full_text"] == FOREIGN     # the input is not mutated
    assert out["full_text"] == "" and out["regeste"] is None
    assert out["decision_date"] == "1896-01-01" and "date_provenance" not in out
    assert out["text_available"] is False
    assert out["source_defect"]["kind"] == "other_reference"
    assert out["source_defect"]["source_holds"] == "22 I 1012"
    assert "BGE 22 I 1012" in out["source_defect"]["note"]


def test_unlisted_rows_pass_unchanged():
    row = {"decision_id": "bge_52_I_14", "full_text": "x"}
    assert sd.apply(row) is row
    # another court's docket that happens to read like a listed BGE reference
    cantonal = {"decision_id": "zh_obergericht_52_I_8", "court": "zh_obergericht",
                "docket_number": "52 I 8", "full_text": "x"}
    assert sd.apply(cantonal) is cantonal
    # the BGE row found by its docket alone
    by_docket = sd.apply({"decision_id": "x", "court": "bge", "docket_number": "52_I_8", "full_text": "y"})
    assert by_docket["text_available"] is False


def test_truncate_only_while_the_text_is_the_verified_one(monkeypatch):
    own, foreign = "Own ruling. " * 10, "\n154\nOther ruling. " * 10
    text = own + foreign
    entry = sd.SourceDefect(reference="71 II 223", kind="foreign_pages_appended", action="truncate",
                            holds="71 II 223 + 77 II 154", copy_of="bge_77_II_154", note="n",
                            text_sha256=hashlib.sha256(text.encode()).hexdigest(), keep_chars=len(own))
    monkeypatch.setitem(sd._BY_PARTS, (71, "II", 223), entry)
    out = sd.apply({"decision_id": "bge_71_II_223", "full_text": text, "decision_date": "1945-01-01"})
    assert out["full_text"] == own.rstrip() and out["text_available"] is True
    assert out["source_defect"]["chars_withheld"] == len(text) - len(own.rstrip())
    assert out["decision_date"] == "1945-01-01"
    changed = {"decision_id": "bge_71_II_223", "full_text": own}   # e.g. after re-segmentation
    assert sd.apply(changed) is changed
    assert sd.withholds_structure("bge_71_II_223") and not sd.withholds_text("bge_71_II_223")


def test_the_71_II_223_entry_cuts_before_the_appended_pages():
    d = sd.lookup("bge_71_II_223")
    assert d.action == "truncate" and d.keep_chars == 5729 and len(d.text_sha256) == 64


def test_search_hits_on_withheld_references_are_dropped():
    hits = [{"decision_id": "bge_52_I_149"}, {"decision_id": "bge_52_I_154"},
            {"decision_id": "bge_71_II_223"}, {"decision_id": None}]
    kept, dropped = sd.filter_hits(hits)
    assert [h["decision_id"] for h in kept] == ["bge_52_I_154", "bge_71_II_223", None] and dropped == 1


# ── serving (mcp_server) against a throwaway decisions.db ───────────────────

def _db(path: Path) -> str:
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE decisions(decision_id TEXT PRIMARY KEY, court TEXT, canton TEXT, "
              "docket_number TEXT, decision_date TEXT, publication_date TEXT, language TEXT, "
              "title TEXT, regeste TEXT, full_text TEXT, json_data TEXT)")
    c.execute("INSERT INTO decisions VALUES('bge_52_I_8','bge','CH','52_I_8','1926-01-01',NULL,'de',"
              "'BGE 52 I 8',NULL,?,NULL)", (FOREIGN,))
    c.execute("INSERT INTO decisions VALUES('bge_52_I_14','bge','CH','52_I_14','1926-02-26',NULL,'de',"
              "'BGE 52 I 14',NULL,'3. Urteil vom 26. Februar 1926 i. S. Partei.',NULL)")
    c.commit()
    c.close()
    return str(path)


def _conn(p):
    c = sqlite3.connect(p)
    c.row_factory = sqlite3.Row
    return c


@pytest.fixture
def served(tmp_path, monkeypatch):
    dbp = _db(tmp_path / "d.db")
    monkeypatch.setattr(m, "get_db", lambda: _conn(dbp))
    monkeypatch.setattr(m, "CANONICAL_DB_PATH", Path(tmp_path / "missing.db"))
    monkeypatch.setattr(m, "_canonical_warned", False)
    monkeypatch.setattr(m, "_batch_fetch_statutes", lambda ids, limit_per=8: {i: ["Art. 148 StGB"] for i in ids})
    monkeypatch.setattr(m, "_count_citations", lambda did: (0, 0))
    return dbp


def test_get_decision_withholds_the_foreign_text(served):
    out = m.get_decision_by_id("bge_52_I_8")
    assert out["full_text"] == "" and out["text_available"] is False
    assert out["source_defect"]["source_holds"] == "65 I 8"
    assert out["source_defect"]["copy_in_corpus"] == "bge_65_I_8"
    assert out["date_is_estimated"] is True
    assert "statutes" not in out                      # they were extracted from the foreign text
    genuine = m.get_decision_by_id("bge_52_I_14")
    assert "source_defect" not in genuine and genuine["full_text"].startswith("3. Urteil")
    assert genuine["statutes"] == ["Art. 148 StGB"]


def test_strict_fetch_used_by_claim_checks_withholds_too(served):
    assert m._get_decision_strict("bge_52_I_8")["full_text"] == ""
    assert m._get_decision_strict("bge_52_I_14")["full_text"].startswith("3. Urteil")


def test_structure_is_withheld_without_opening_the_sidecar(monkeypatch):
    def boom():
        raise AssertionError("structure DB must not be read for a source defect")
    monkeypatch.setattr(m, "_get_structure_conn", boom)
    assert m._fetch_structure_row("bge_52_I_8") is None
    assert m._fetch_structure_paragraphs("bge_historical_52_I_8") == []
    assert m._fetch_structure_paragraphs("bge_71_II_223") == []


def test_cite_miss_on_a_withheld_reference_explains_and_suggests_nothing(served, monkeypatch):
    for name in ("_capture_event", "_record_tool_call", "_record_tool_outcome", "_record_query"):
        monkeypatch.setattr(m, name, lambda *a, **k: None, raising=False)
    out = m._handle_cite(reference="BGE 39 I 469")      # not in the throwaway db
    assert out["exists"] is False and out["close_matches"] == []
    assert out["not_found_reason"] == "source_defect"
    assert out["source_defect"]["source_holds"] == "39 I 483"


def test_fetch_shim_says_what_is_missing(served, monkeypatch):
    monkeypatch.setattr(m, "_resolve_decision_id", lambda x: x)
    out = m._deep_research_fetch("bge_52_I_8")
    assert out["text"].startswith("[Source defect: ") and "65 I 8" in out["text"]
    assert out["metadata"]["source_defect"]["kind"] == "foreign_volume"
