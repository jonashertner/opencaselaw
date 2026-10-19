"""appeal_refs: the court's appeal note, read from json_data and linked."""
from __future__ import annotations

import json
import sqlite3

import appeal_refs


def _conn(rows):
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE decisions (decision_id TEXT PRIMARY KEY, court TEXT, docket_number TEXT)")
    c.executemany("INSERT INTO decisions VALUES (?,?,?)", rows)
    return c


def test_reads_the_note_from_json_data():
    row = {"json_data": json.dumps({"appeal_info": " Weiterzug ans Bundesgericht, 6B_122/2024 "})}
    assert appeal_refs.appeal_info_of(row) == "Weiterzug ans Bundesgericht, 6B_122/2024"
    assert appeal_refs.appeal_info_of({"json_data": json.dumps({"appeal_info": None})}) is None
    assert appeal_refs.appeal_info_of({"json_data": "not json"}) is None
    assert appeal_refs.appeal_info_of({"decision_id": "x"}) is None


def test_dockets_in_order_without_repeats():
    text = "Berufung Bekl. abgew. (LA240029-O), Weiterzug ans Bundesgericht, 4A_32/2019; vgl. 4A_32/2019"
    assert appeal_refs.dockets_in(text) == [("LA240029", "zh"), ("4A_32/2019", "bger")]


def test_links_only_what_the_corpus_holds():
    c = _conn([("bger_6B_122_2024", "bger", "6B_122/2024"),
               ("zh_obergericht_LA240029", "zh_obergericht", "LA240029")])
    refs = appeal_refs.resolve(c, "Weiterzug ans Bundesgericht, 6B_122/2024 und 6B_999/2024; LA240029",
                               "zh_arbeitsgericht", own_id="zh_arbeitsgericht_AN220047-L")
    assert refs == [{"docket": "6B_122/2024", "decision_id": "bger_6B_122_2024"},
                    {"docket": "LA240029", "decision_id": "zh_obergericht_LA240029"}]


def test_a_docket_held_twice_is_not_guessed():
    c = _conn([("zh_obergericht_PS150113", "zh_obergericht", "PS150113"),
               ("zh_obergericht_PS150113_d20150818", "zh_obergericht", "PS150113")])
    assert appeal_refs.resolve(c, "vgl. PS150113", "zh_obergericht") == []


def test_never_links_a_decision_to_itself_or_across_cantons():
    c = _conn([("zh_obergericht_VB020033", "zh_obergericht", "VB020033"),
               ("bger_4A_1_2020", "bger", "4A_1/2020")])
    assert appeal_refs.resolve(c, "VB020033", "zh_obergericht", own_id="zh_obergericht_VB020033") == []
    assert appeal_refs.resolve(c, "VB020033", "be_obergericht") == []
    assert appeal_refs.resolve(c, "4A_1/2020", "be_obergericht") == [
        {"docket": "4A_1/2020", "decision_id": "bger_4A_1_2020"}]


def test_get_decision_carries_the_note_and_its_links(tmp_path, monkeypatch):
    import json as _json
    from pathlib import Path

    import build_fts5
    import mcp_server as m
    from db_schema import SCHEMA_SQL

    dbp = tmp_path / "decisions.db"
    c = sqlite3.connect(str(dbp))
    c.executescript(SCHEMA_SQL)
    base = {"canton": "ZH", "language": "de", "title": "t", "regeste": None, "source_url": "https://x"}
    assert build_fts5.insert_decision(c, {
        **base, "decision_id": "zh_obergericht_SB230343", "court": "zh_obergericht",
        "docket_number": "SB230343", "decision_date": "2023-11-01", "full_text": "a " * 60,
        "appeal_info": "Weiterzug ans Bundesgericht, 6B_122/2024"})
    assert build_fts5.insert_decision(c, {
        **base, "canton": "CH", "decision_id": "bger_6B_122_2024", "court": "bger",
        "docket_number": "6B_122/2024", "decision_date": "2024-06-01", "full_text": "b " * 60})
    c.commit()
    c.close()

    def _conn():
        k = sqlite3.connect(str(dbp))
        k.row_factory = sqlite3.Row
        return k

    monkeypatch.setattr(m, "get_db", _conn)
    monkeypatch.setattr(m, "_canonical_warned", False, raising=False)
    monkeypatch.setattr(m, "CANONICAL_DB_PATH", Path(tmp_path / "missing.db"))
    r = m.get_decision_by_id("zh_obergericht_SB230343")
    assert r["appeal_info"] == "Weiterzug ans Bundesgericht, 6B_122/2024"
    assert r["appeal_references"] == [{"docket": "6B_122/2024", "decision_id": "bger_6B_122_2024"}]
    assert "json_data" not in r
    plain = m.get_decision_by_id("bger_6B_122_2024")
    assert "appeal_info" not in plain and "appeal_references" not in plain
    assert _json.dumps(r)   # serialisable
