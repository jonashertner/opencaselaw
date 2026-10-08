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


# ── review fixes (2026-10-07): citations, snippets, paging, other paths ─────

def test_withheld_and_truncated_rows_carry_no_outgoing_citations(monkeypatch):
    out = sd.apply({"decision_id": "bge_52_I_8", "full_text": FOREIGN, "cited_decisions": '["RO 59 I 179"]'})
    assert out["cited_decisions"] is None
    text = "Own ruling text. " * 30 + "\n154\n" + "Other ruling. " * 30
    entry = sd.SourceDefect(reference="71 II 223", kind="foreign_pages_appended", action="truncate",
                            holds="71 II 223 + 77 II 154", copy_of="bge_77_II_154", note="n",
                            text_sha256=hashlib.sha256(text.encode()).hexdigest(),
                            keep_chars=text.index("\n154\n"))
    monkeypatch.setitem(sd._BY_PARTS, (71, "II", 223), entry)
    cut = sd.apply({"decision_id": "bge_71_II_223", "full_text": text, "cited_decisions": '["BGE 77 II 1"]'})
    assert cut["cited_decisions"] is None and "Other ruling" not in cut["full_text"]


def test_snippet_in():
    own = "Das Stillschweigen der Beklagten auf das Schreiben begründet die Vermutung."
    assert sd.snippet_in("...das <mark>Stillschweigen</mark> der Beklagten auf das Schreiben...", own)
    assert not sd.snippet_in("...Haftung für <mark>Zwischenspediteure</mark> nach Art. 101 OR...", own)
    assert sd.snippet_in("", own) and sd.snippet_in(None, own) and sd.snippet_in("<mark>kurz</mark>", own)


def test_a_truncated_row_is_found_only_on_its_own_text(monkeypatch):
    own = "Das Stillschweigen der Beklagten auf das Schreiben begründet die Vermutung. " * 3
    text = own + "\n154\nHaftung für Zwischenspediteure nach Art. 101 OR, Frigaliment. " * 3
    entry = sd.SourceDefect(reference="71 II 223", kind="foreign_pages_appended", action="truncate",
                            holds="71 II 223 + 77 II 154", copy_of="bge_77_II_154", note="n",
                            text_sha256=hashlib.sha256(text.encode()).hexdigest(), keep_chars=len(own))
    monkeypatch.setitem(sd._BY_PARTS, (71, "II", 223), entry)
    hits = [{"decision_id": "bge_71_II_223", "snippet": "...Haftung für <mark>Zwischenspediteure</mark> nach Art. 101 OR..."},
            {"decision_id": "bge_71_II_223", "snippet": "...<mark>Stillschweigen</mark> der Beklagten auf das Schreiben..."}]
    kept, dropped = sd.filter_hits(hits, lambda did: text)
    assert dropped == 1 and kept == [hits[1]]
    # once the stored text changed (re-segmented), nothing is dropped any more
    assert sd.filter_hits(hits, lambda did: own)[1] == 0


def test_search_refills_a_page_that_lost_a_withheld_hit(served, monkeypatch):
    pool = [{"decision_id": d, "snippet": ""} for d in
            ("bge_52_I_14", "bge_52_I_8", "bge_52_I_27", "bge_52_I_34", "bge_52_I_44")]
    calls = []

    def _inner(conn, query, court, canton, language, date_from, date_to, chamber,
               decision_type, legal_area, limit, offset, **kw):
        calls.append((limit, offset))
        return [dict(r) for r in pool[offset:offset + limit]], 50

    monkeypatch.setattr(m, "_search_fts5_inner", _inner)
    monkeypatch.setattr(m, "_SEARCH_RESULT_CACHE_LIMIT_GATE", 0)
    rows, total = m.search_fts5(query="Registersachen", limit=3)
    assert [r["decision_id"] for r in rows] == ["bge_52_I_14", "bge_52_I_27", "bge_52_I_34"]
    assert total == 50 and calls == [(3, 0), (4, 0)]   # a full page, the same total on every page


def test_pinpoint_and_relevant_erwaegung_never_read_a_defect_structure(monkeypatch):
    def boom():
        raise AssertionError("structure DB must not be read for a source defect")
    monkeypatch.setattr(m, "_get_structure_conn", boom)
    assert m._compute_pinpoint("bge_52_I_8", "nulla poena sine lege") is None
    monkeypatch.setattr(m, "_resolve_decision_id", lambda x: x)
    out = m._handle_find_relevant_erwaegung(decision_id="bge_71_II_223", claim="Bestätigungsschreiben")
    assert out["no_match"] is True and out["matches"] == []
    assert out["source_defect"]["kind"] == "foreign_pages_appended"


def test_find_citations_keeps_incoming_and_drops_outgoing(monkeypatch):
    class _Conn:
        def close(self):
            pass
    monkeypatch.setattr(m, "_resolve_decision_id", lambda x: x)
    monkeypatch.setattr(m, "_get_graph_conn", lambda: _Conn())
    monkeypatch.setattr(m, "_count_citations_filtered", lambda did, min_confidence: (3, 5))

    def no_outgoing(*a, **k):
        raise AssertionError("outgoing edges of a source defect must not be read")
    monkeypatch.setattr(m, "_find_outgoing_citations", no_outgoing)
    monkeypatch.setattr(m, "_find_incoming_citations", lambda did, **k: [{"source_decision_id": "bger_1P.152_2002"}])
    out = m.find_citations(decision_id="bge_52_I_149")
    assert out["outgoing"] == [] and out["outgoing_total"] == 0
    assert out["incoming_total"] == 3 and out["incoming"][0]["source_decision_id"] == "bger_1P.152_2002"
    assert out["source_defect"]["source_holds"] == "65 I 149"


def test_leading_case_fallback_drops_withheld_rows(monkeypatch):
    assert sd.filter_hits([{"decision_id": "bge_52_I_149"}, {"decision_id": "bge_95_I_366"}])[0] == [
        {"decision_id": "bge_95_I_366"}]


# ── recovered rulings (2026-10-08): served once the stored text is the recovered one ─

RECOVERED = REPO / "runbooks" / "historical_bge_recovered_2026-10-08"


def _recovered_text(name: str) -> str:
    return (RECOVERED / name).read_text(encoding="utf-8").removesuffix("\n")


def test_recovery_hashes_are_the_manifest_texts():
    import json

    manifest = json.loads((RECOVERED / "manifest.json").read_text(encoding="utf-8"))
    by_ref = {e["reference"]: e for e in manifest["recoveries"]}
    recovered = {d.reference: d for d in sd.DEFECTS if d.recovered}
    assert sorted(recovered) == sorted(by_ref)
    for ref, d in recovered.items():
        e = by_ref[ref]
        assert d.action == "withhold" and d.recovered == e["completeness"]
        assert d.recovered_sha256 == e["text_sha256"] == hashlib.sha256(
            _recovered_text(e["file"]).encode("utf-8")).hexdigest()
        if e["pages_missing"]:
            assert f"Pages {e['pages_missing'][0]}-{e['pages_missing'][-1]} are missing" in d.recovered_note


def test_a_recovered_text_is_served_with_its_note():
    text = _recovered_text("52_I_23.txt")
    row = {"decision_id": "bge_52_I_23", "court": "bge", "docket_number": "52_I_23",
           "full_text": text, "decision_date": "1926-02-26", "regeste": "r"}
    out = sd.apply(row)
    assert out is not row and out["full_text"] == text
    assert out["decision_date"] == "1926-02-26" and out["regeste"] == "r"
    assert out["text_available"] is True
    assert out["source_defect"]["recovered"] == "partial"
    assert out["source_defect"]["source_holds"] == "65 I 23"
    assert "Pages 24-25 are missing" in out["source_defect"]["note"]
    # any other text under the reference is still withheld, and so is the structure
    still = sd.apply({**row, "full_text": text + " "})
    assert still["full_text"] == "" and still["text_available"] is False
    assert sd.withholds_structure("bge_52_I_23")


def test_the_ocr_recovery_says_so():
    out = sd.apply({"decision_id": "bge_39_I_469", "court": "bge",
                    "full_text": _recovered_text("39_I_469.txt")})
    assert out["text_available"] is True and out["source_defect"]["recovered"] == "complete"
    note = out["source_defect"]["note"]
    assert "OCR" in note and "not proofread" in note and "c1039465.pdf" in note


def test_search_hits_on_a_recovered_reference_need_its_recovered_text():
    text = _recovered_text("22_I_12.txt")
    hits = [{"decision_id": "bge_22_I_12", "snippet": ""}]
    assert sd.filter_hits(hits) == ([], 1)                         # no stored text to check
    assert sd.filter_hits(hits, lambda did: FOREIGN) == ([], 1)    # still the other ruling's
    assert sd.filter_hits(hits, lambda did: text) == (hits, 0)


def test_get_decision_and_cite_serve_a_recovered_row(served, monkeypatch):
    text = _recovered_text("52_I_39.txt")
    c = sqlite3.connect(served)
    c.execute("INSERT INTO decisions VALUES('bge_52_I_39','bge','CH','52_I_39','1926-03-26',NULL,'de',"
              "'BGE 52 I 39',NULL,?,NULL)", (text,))
    c.commit()
    c.close()
    out = m.get_decision_by_id("bge_52_I_39")
    assert out["full_text"] == text and out["text_available"] is True
    assert out["decision_date"] == "1926-03-26"
    assert out["source_defect"]["recovered"] == "partial"
    for name in ("_capture_event", "_record_tool_call", "_record_tool_outcome", "_record_query"):
        monkeypatch.setattr(m, name, lambda *a, **k: None, raising=False)
    cited = m._handle_cite(reference="BGE 52 I 39")
    assert cited["exists"] is True and cited["text_available"] is True
    assert cited["source_defect"]["recovered"] == "partial"


def test_find_citations_gives_a_recovered_reference_its_recovery_note(served, monkeypatch):
    text = _recovered_text("52_I_149.txt")
    c = sqlite3.connect(served)
    c.execute("INSERT INTO decisions VALUES('bge_52_I_149','bge','CH','52_I_149','1926-03-05',NULL,'de',"
              "'BGE 52 I 149',NULL,?,NULL)", (text,))
    c.commit()
    c.close()

    class _Conn:
        def close(self):
            pass
    monkeypatch.setattr(m, "_resolve_decision_id", lambda x: x)
    monkeypatch.setattr(m, "_get_graph_conn", lambda: _Conn())
    monkeypatch.setattr(m, "_count_citations_filtered", lambda did, min_confidence: (3, 5))

    def no_outgoing(*a, **k):
        raise AssertionError("outgoing edges of a source defect must not be read")
    monkeypatch.setattr(m, "_find_outgoing_citations", no_outgoing)
    monkeypatch.setattr(m, "_find_incoming_citations", lambda did, **k: [])
    out = m.find_citations(decision_id="bge_52_I_149")
    assert out["outgoing"] == [] and out["source_defect"]["recovered"] == "partial"
    assert "Pages 150-153 are missing" in out["source_defect"]["note"]
