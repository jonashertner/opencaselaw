"""BStGer official collection (TPF) <-> the ruling it publishes.

A TPF entry ("TPF 2024 194") holds the underlying ruling's docket in
docket_number_2 (BG.2024.60); the ruling's own row did not name its TPF
entry, nor the entry its ruling. Served the way published_as_bge /
full_decision are for BGE, with every lookup on the docket index
(bge-reverse-link incident, 2026-10-16)."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import mcp_server as m  # noqa: E402

ROWS = [
    # the ruling and its TPF entry
    ("bstger_BG.2024.60", "bstger", "CH", "BG.2024.60", None, "2024-12-11", None, "de", "Beschluss", None),
    ("bstger_TPF_2024_194", "bstger", "CH", "TPF 2024 194", "BG.2024.60", "2024-12-11", None, "de",
     "29. Auszug aus dem Beschluss der Beschwerdekammer", None),
    # an interim order under the same docket, another date: not published
    ("bstger_BG.2024.60_order", "bstger", "CH", "BG.2024.60", None, "2024-10-01", None, "de", "Verfügung", None),
    # joined rulings, the heading prints the first date only (TPF 2005 123)
    ("bstger_SK.2004.14", "bstger", "CH", "SK.2004.14", None, "2005-06-02", None, "de", "Entscheid", None),
    ("bstger_SK.2004.15", "bstger", "CH", "SK.2004.15", None, "2005-06-14", None, "de", "Entscheid", None),
    ("bstger_TPF_2005_123", "bstger", "CH", "TPF 2005 123", "SK.2004.14 und SK.2004.15", "2005-06-02", None,
     "de", "33. Auszug aus den Entscheiden der Strafkammer", None),
    # an old-style docket with a slash
    ("bstger_BK_B_023/04", "bstger", "CH", "BK_B 023/04", None, "2004-05-27", None, "it", "Sentenza", None),
    ("bstger_TPF_2004_16", "bstger", "CH", "TPF 2004 16", "BK_B 023/04", "2004-05-27", None, "it",
     "4. Estratto della Sentenza", None),
    # a TPF entry whose ruling is not held, and a ruling never published
    ("bstger_TPF_2010_9", "bstger", "CH", "TPF 2010 9", "BB.2010.3", "2010-05-03", None, "de", "x", None),
    ("bstger_RR.2020.1", "bstger", "CH", "RR.2020.1", None, "2020-02-02", None, "fr", "arrêt", None),
    # another court with a TPF-looking docket is ignored
    ("zh_TPF_1", "zh_obergericht", "ZH", "TPF 2024 194", "BG.2024.60", "2024-12-11", None, "de", "x", None),
]


def _db(path):
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE decisions(decision_id TEXT PRIMARY KEY, court TEXT, canton TEXT, "
              "docket_number TEXT, docket_number_2 TEXT, decision_date TEXT, publication_date TEXT, "
              "language TEXT, full_text TEXT, json_data TEXT)")
    c.executemany("INSERT INTO decisions VALUES(?,?,?,?,?,?,?,?,?,?)", ROWS)
    c.execute("CREATE INDEX idx_decisions_court ON decisions(court)")      # as in production
    c.execute("CREATE INDEX idx_decisions_docket ON decisions(docket_number)")
    c.commit(); c.close()
    return path


def _rconn(p):
    c = sqlite3.connect(p); c.row_factory = sqlite3.Row
    return c


def _wire(monkeypatch, tmp_path):
    dbp = _db(tmp_path / "d.db")
    monkeypatch.setattr(m, "get_db", lambda: _rconn(dbp))
    monkeypatch.setattr(m, "DB_PATH", Path(dbp))
    monkeypatch.setattr(m, "CANONICAL_DB_PATH", tmp_path / "missing.db")
    monkeypatch.setattr(m, "_tpf_by_docket_sig", None)
    monkeypatch.setattr(m, "_tpf_by_docket", {})
    return dbp


def test_ruling_names_its_tpf_entry(tmp_path, monkeypatch):
    _wire(monkeypatch, tmp_path)
    out = m.get_decision_by_id("bstger_BG.2024.60")
    assert [t["decision_id"] for t in out["published_as_tpf"]] == ["bstger_TPF_2024_194"]
    t = out["published_as_tpf"][0]
    assert t["citation_string_de"] == t["citation_string_fr"] == "TPF 2024 194"
    assert "TPF 2024 194" in out["published_as_tpf_note"]


def test_tpf_entry_names_its_full_ruling(tmp_path, monkeypatch):
    _wire(monkeypatch, tmp_path)
    out = m.get_decision_by_id("bstger_TPF_2024_194")
    assert out["full_decision"]["decision_id"] == "bstger_BG.2024.60"
    assert "published_as_tpf" not in out


def test_interim_order_under_the_same_docket_is_not_linked(tmp_path, monkeypatch):
    _wire(monkeypatch, tmp_path)
    assert "published_as_tpf" not in m.get_decision_by_id("bstger_BG.2024.60_order")


def test_joined_rulings_link_both_ways(tmp_path, monkeypatch):
    _wire(monkeypatch, tmp_path)
    for rid in ("bstger_SK.2004.14", "bstger_SK.2004.15"):
        out = m.get_decision_by_id(rid)
        assert [t["decision_id"] for t in out["published_as_tpf"]] == ["bstger_TPF_2005_123"], rid
    # back: the first docket with the heading's date
    assert m.get_decision_by_id("bstger_TPF_2005_123")["full_decision"]["decision_id"] == "bstger_SK.2004.14"


def test_slash_docket_and_unheld_ruling(tmp_path, monkeypatch):
    _wire(monkeypatch, tmp_path)
    assert m.get_decision_by_id("bstger_TPF_2004_16")["full_decision"]["decision_id"] == "bstger_BK_B_023/04"
    assert m.get_decision_by_id("bstger_BK_B_023/04")["published_as_tpf"][0]["decision_id"] == "bstger_TPF_2004_16"
    assert "full_decision" not in m.get_decision_by_id("bstger_TPF_2010_9")
    assert "published_as_tpf" not in m.get_decision_by_id("bstger_RR.2020.1")


def test_cite_carries_the_tpf_link(tmp_path, monkeypatch):
    _wire(monkeypatch, tmp_path)
    out = m._handle_cite(reference="bstger_BG.2024.60")
    assert out["published_as_tpf"][0]["citation_string_de"] == "TPF 2024 194"
    out = m._handle_cite(reference="TPF 2024 194")
    assert out["full_decision"]["decision_id"] == "bstger_BG.2024.60"


def test_index_rebuilt_when_the_db_file_changes(tmp_path, monkeypatch):
    dbp = _wire(monkeypatch, tmp_path)
    assert m._tpf_index()
    sig = m._tpf_by_docket_sig
    c = sqlite3.connect(dbp)
    c.execute("INSERT INTO decisions VALUES('bstger_TPF_2020_1','bstger','CH','TPF 2020 1','RR.2020.1',"
              "'2020-02-02',NULL,'fr','x',NULL)")
    c.commit(); c.close()
    assert m._published_as_tpf(m.get_decision_by_id("bstger_RR.2020.1"))[0]["decision_id"] == "bstger_TPF_2020_1"
    assert m._tpf_by_docket_sig != sig


def test_underlying_docket_split():
    assert m._tpf_underlying_dockets("SK.2004.14 und SK.2004.15") == ["SK.2004.14", "SK.2004.15"]
    assert m._tpf_underlying_dockets("BK_B 023/04") == ["BK_B 023/04"]
    assert m._tpf_underlying_dockets("RR.2008.1, RR.2008.2") == ["RR.2008.1", "RR.2008.2"]
    assert m._tpf_underlying_dockets("BB.2005.1 et BB.2005.2") == ["BB.2005.1", "BB.2005.2"]
    assert m._tpf_underlying_dockets(None) == []


def test_lookups_use_the_docket_index_only(tmp_path, monkeypatch):
    dbp = _wire(monkeypatch, tmp_path)
    seen: list[str] = []

    def traced():
        c = _rconn(dbp)
        c.set_trace_callback(seen.append)
        return c
    monkeypatch.setattr(m, "get_db", traced)
    m.get_decision_by_id("bstger_TPF_2024_194")
    m.get_decision_by_id("bstger_BG.2024.60")
    m.get_decision_by_id("bstger_TPF_2005_123")
    plan_conn = sqlite3.connect(dbp)
    tpf_sql = [q for q in seen if "TPF" in q and q.lstrip().upper().startswith("SELECT")]
    assert tpf_sql, seen
    for sql in {q for q in seen if q.lstrip().upper().startswith("SELECT")}:
        plan = " ".join(str(r[-1]) for r in plan_conn.execute("EXPLAIN QUERY PLAN " + sql))
        assert "idx_decisions_court" not in plan and "SCAN decisions" not in plan, (sql, plan)
    for sql in (m._TPF_SQL, m._TPF_RULING_SQL.format("?,?")):
        plan = " ".join(str(r[-1]) for r in plan_conn.execute(
            "EXPLAIN QUERY PLAN " + sql, () if "?" not in sql else ("a", "b")))
        assert "USING INDEX idx_decisions_docket" in plan, plan


def test_get_decision_text_names_both_directions(tmp_path, monkeypatch):
    import asyncio
    _wire(monkeypatch, tmp_path)
    def _text(res):
        if isinstance(res, tuple):
            res = res[0]
        return "".join(getattr(c, "text", "") for c in res)
    text = _text(asyncio.run(m._handle_call_tool_inner("get_decision", {"decision_id": "bstger_BG.2024.60"})))
    assert "`TPF 2024 194` — cite the TPF reference" in text
    text = _text(asyncio.run(m._handle_call_tool_inner("get_decision", {"decision_id": "bstger_TPF_2024_194"})))
    assert "**Full ruling:**" in text and "BG.2024.60" in text
