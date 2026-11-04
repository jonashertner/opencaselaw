"""get_regeste and cite flag a placeholder decision date the way get_decision
does (Mnemosyne report, 2026-11-04): BGE 40 II 559 came back from both with
decision_date 1914-01-01 and nothing saying it is the volume year, while
get_decision on the same id said date_is_estimated true, date_provenance
volume_synthetic. decision_date itself is left as it was; the citation
strings are not touched.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import mcp_server as m  # noqa: E402

REGESTE = "Art. 41 OR. Haftung des Werkeigentümers."
ROWS = [
    # (decision_id, court, docket, date, language, full_text, regeste)
    ("bge_40_II_559", "bge", "40_II_559", "1914-01-01", "de", "Urteilskopf 40 II 559 ...", REGESTE),
    ("bge_152_II_1", "bge", "152_II_1", "2026-01-01", "de", "Urteilskopf 152 II 1 ...", REGESTE),
    ("bger_5A_1_2024", "bger", "5A_1/2024", "2024-05-06", "de", "Urteil vom 6. Mai 2024", REGESTE),
]
SIDECAR = [
    # (decision_id, decision_date, provenance, publication_date, pub_prov, ecli, key)
    ("bge_40_II_559", None, "volume_synthetic", "1914-01-01", "volume_year", None, None),
    ("bge_152_II_1", "2025-09-27", "extracted_from_text", "2026-01-01", "volume_year", None, None),
]


def _rconn(p):
    c = sqlite3.connect(f"file:{p}?mode=ro&immutable=1", uri=True)
    c.row_factory = sqlite3.Row
    return c


@pytest.fixture
def wired(tmp_path, monkeypatch):
    dbp = tmp_path / "d.db"
    c = sqlite3.connect(dbp)
    c.execute("CREATE TABLE decisions(decision_id TEXT PRIMARY KEY, court TEXT, canton TEXT, "
              "docket_number TEXT, decision_date TEXT, publication_date TEXT, language TEXT, "
              "full_text TEXT, regeste TEXT, json_data TEXT)")
    c.executemany("INSERT INTO decisions VALUES(?,?,'CH',?,?,NULL,?,?,?,NULL)", ROWS)
    c.execute("CREATE INDEX idx_decisions_docket ON decisions(docket_number)")
    c.commit(); c.close()
    scp = tmp_path / "ci.db"
    c = sqlite3.connect(scp)
    c.execute("CREATE TABLE canonical_identity(decision_id TEXT PRIMARY KEY, decision_date TEXT, "
              "decision_date_provenance TEXT, publication_date TEXT, publication_date_provenance TEXT, "
              "ecli TEXT, canonical_key TEXT)")
    c.executemany("INSERT INTO canonical_identity VALUES(?,?,?,?,?,?,?)", SIDECAR)
    c.commit(); c.close()
    monkeypatch.setattr(m, "get_db", lambda: _rconn(dbp))
    monkeypatch.setattr(m, "CANONICAL_DB_PATH", scp)
    monkeypatch.setattr(m, "_canonical_warned", False)
    monkeypatch.setattr(m, "_bge_by_key_sig", None)
    monkeypatch.setattr(m, "_bge_by_key", {})
    monkeypatch.setattr(m, "_fetch_structure_row", lambda did: None)
    monkeypatch.setattr(m, "_fetch_structure_paragraphs", lambda did: [])
    return dbp


def _structure_rows(monkeypatch):
    """The decision_structure path: the row carries its own (raw) date."""
    by_id = {r[0]: {"decision_id": r[0], "court": r[1], "canton": "CH", "decision_date": r[3],
                    "language": r[4], "regeste": r[6]} for r in ROWS}
    monkeypatch.setattr(m, "_fetch_structure_row", lambda did: by_id.get(did))


# ── get_decision is the reference behaviour ──────────────────

def test_get_decision_flags_the_volume_placeholder(wired):
    out = m.get_decision_by_id("bge_40_II_559")
    assert out["decision_date"] == "1914-01-01"
    assert out["date_is_estimated"] is True
    assert out["date_provenance"] == "volume_synthetic"


# ── cite ──────────────────────────────────────────────────────

def test_cite_flags_a_volume_synthetic_bge(wired):
    out = m._handle_cite(reference="BGE 40 II 559")
    assert out["exists"] is True
    assert out["decision_date"] == "1914-01-01"            # kept, not nulled
    assert out["date_is_estimated"] is True
    assert out["date_provenance"] == "volume_synthetic"
    assert "placeholder, not the real Urteilsdatum" in out["date_estimate_note"]
    assert out["citation_string_de"] == "BGE 40 II 559"     # citation untouched


def test_cite_real_date_is_not_estimated(wired):
    out = m._handle_cite(reference="5A_1/2024")
    assert out["exists"] is True
    assert out["decision_date"] == "2024-05-06"
    assert out["date_is_estimated"] is False
    assert "date_estimate_note" not in out


def test_cite_text_verified_date_is_not_estimated(wired):
    out = m._handle_cite(reference="BGE 152 II 1")
    assert out["decision_date"] == "2025-09-27"
    assert out["date_is_estimated"] is False
    assert out["date_provenance"] == "extracted_from_text"


def test_cite_citation_strings_unchanged_by_the_flag(wired, monkeypatch):
    flagged = m._handle_cite(reference="BGE 40 II 559")
    monkeypatch.setattr(m, "_date_estimate_fields", lambda shown, rec: {})
    plain = m._handle_cite(reference="BGE 40 II 559")
    for k in ("citation_string", "citation_string_de", "citation_string_fr",
              "citation_string_it", "canonical_url", "markdown_link", "decision_date"):
        assert flagged[k] == plain[k], k


# ── get_regeste ───────────────────────────────────────────────

def test_regeste_main_db_path_flags_the_placeholder(wired):
    out = m._handle_get_regeste(decision_id="bge_40_II_559")
    assert out["regeste"]
    assert out["decision_date"] == "1914-01-01"
    assert out["date_is_estimated"] is True
    assert out["date_provenance"] == "volume_synthetic"
    assert "placeholder" in out["date_estimate_note"]


def test_regeste_structure_path_flags_the_placeholder(wired, monkeypatch):
    _structure_rows(monkeypatch)
    out = m._handle_get_regeste(decision_id="bge_40_II_559")
    assert out["decision_date"] == "1914-01-01"
    assert out["date_is_estimated"] is True
    assert out["date_provenance"] == "volume_synthetic"


def test_regeste_real_date_is_not_estimated(wired, monkeypatch):
    for structured in (False, True):
        if structured:
            _structure_rows(monkeypatch)
        out = m._handle_get_regeste(decision_id="bger_5A_1_2024")
        assert out["decision_date"] == "2024-05-06"
        assert out["date_is_estimated"] is False
        assert "date_estimate_note" not in out


def test_regeste_structure_placeholder_not_cleared_by_the_records_verified_date(wired, monkeypatch):
    """The structure row still shows 2026-01-01 while get_decision serves the
    text-verified 2025-09-27: the provenance of the record's date must not
    vouch for the placeholder the tool actually shows."""
    _structure_rows(monkeypatch)
    out = m._handle_get_regeste(decision_id="bge_152_II_1")
    assert out["decision_date"] == "2026-01-01"
    assert out["date_is_estimated"] is True
    assert "date_provenance" not in out


# ── the MCP text and the REST tool endpoint carry the same fields ──

@pytest.mark.parametrize("tool,args", [
    ("cite", {"reference": "BGE 40 II 559"}),
    ("get_regeste", {"decision_id": "bge_40_II_559"}),
])
def test_tool_text_and_rest_payload_carry_the_flag(wired, tool, args):
    import asyncio
    content = asyncio.run(m._handle_call_tool_inner(tool, args))
    if isinstance(content, tuple):
        content = content[0]
    text = "\n".join(c.text for c in content if getattr(c, "type", "") == "text")
    assert "placeholder, not the real Urteilsdatum" in text
    assert json.loads(text)["date_is_estimated"] is True
    rest = asyncio.run(m._call_tool_json(tool, args))
    assert rest["date_is_estimated"] is True
    assert rest["date_provenance"] == "volume_synthetic"
