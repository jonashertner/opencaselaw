"""quick_publish reads only the shard bytes appended since the last full build.

Each full build deletes ~159k shard rows as duplicates or stubs. Their ids stay
in the shards but not in `decisions`, so the old "every id not in the DB" read
would put them back on the first all-courts quick_publish (the Step B blocker
of 2026-10-10). These tests build the checkpoint the way build_fts5 writes it
and check that quick_publish starts at its offsets.

Offline: throwaway DB and shards in tmp_path.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import build_fts5  # noqa: E402
import db_schema  # noqa: E402
import scripts.quick_publish as qp  # noqa: E402


def _row(did: str, court: str = "zh_gerichte") -> dict:
    return {
        "decision_id": did, "court": court, "canton": "ZH",
        "docket_number": did.upper(), "language": "de", "title": "t",
        "regeste": "", "full_text": f"Erwägungen zu {did}, lang genug.",
        "decision_date": "2026-10-01",
    }


def _line(did: str, court: str = "zh_gerichte") -> bytes:
    return (json.dumps(_row(did, court), ensure_ascii=False) + "\n").encode()


def _make_db(path: Path, ids: list[str]) -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript(db_schema.SCHEMA_SQL)
    for did in ids:
        conn.execute(
            "INSERT INTO decisions(decision_id, court, canton, docket_number, "
            "language, title, regeste, full_text) VALUES (?,?,?,?,?,?,?,?)",
            (did, "zh_gerichte", "ZH", did.upper(), "de", "t", "", "body text"))
    conn.commit()
    conn.close()


def _ids(path: Path) -> set[str]:
    conn = sqlite3.connect(str(path))
    try:
        return {r[0] for r in conn.execute("SELECT decision_id FROM decisions")}
    finally:
        conn.close()


@pytest.fixture
def wired(tmp_path, monkeypatch):
    db = tmp_path / "decisions.db"
    jsonl = tmp_path / "decisions"
    jsonl.mkdir()
    checkpoint = tmp_path / ".fts5_checkpoint.json"
    monkeypatch.setattr(qp, "DB_PATH", db)
    monkeypatch.setattr(qp, "JSONL_DIR", jsonl)
    monkeypatch.setattr(qp, "TMP_PATH", Path(str(db) + ".quick"))
    monkeypatch.setattr(qp, "QUICK_PUBLISH_LOCK_PATH", str(tmp_path / "quick.lock"))
    monkeypatch.setattr(qp, "PUBLISH_LOCK_PATH", str(tmp_path / "publish.lock"))
    monkeypatch.setattr(qp, "FULL_BUILD_CHECKPOINT_PATH", checkpoint)
    return db, jsonl, checkpoint


def _full_build(checkpoint: Path, shards: dict[Path, int], tail: bool = True) -> None:
    """Write the checkpoint a full build that read `shards` up to those sizes leaves."""
    files = {}
    for path, size in shards.items():
        entry = {"size": size, "imported": 0}
        if tail:
            entry["tail_sha256"] = build_fts5.shard_tail_sha256(path, size)
        files[path.name] = entry
    checkpoint.write_text(json.dumps(
        {"files": files, "last_full_build": "2026-10-18T11:27:52+00:00"}))


def _new_ids(courts=None) -> list[str]:
    return [r["decision_id"] for r in qp._read_new_jsonl(courts, qp_existing())]


def qp_existing() -> set[str]:
    return _ids(qp.DB_PATH)


def test_rows_the_full_build_removed_are_not_reinserted(wired):
    db, jsonl, checkpoint = wired
    shard = jsonl / "zh_gerichte.jsonl"
    # The full build read a and its twin a_dup, and its dedup kept only a.
    shard.write_bytes(_line("a") + _line("a_dup"))
    _make_db(db, ["a"])
    _full_build(checkpoint, {shard: shard.stat().st_size})
    with open(shard, "ab") as f:          # tonight's scrape
        f.write(_line("b"))

    assert qp.quick_publish() == 1
    assert _ids(db) == {"a", "b"}         # a_dup stays removed


def test_offset_inside_a_line_rereads_that_line(wired):
    db, jsonl, checkpoint = wired
    shard = jsonl / "zh_gerichte.jsonl"
    first, cut = _line("a"), _line("b")
    shard.write_bytes(first + cut + _line("c"))
    _make_db(db, ["a"])
    # The build stat'd the shard while "b" was half written, then hit EOF
    # mid-line, so "b" never reached the DB.
    _full_build(checkpoint, {shard: len(first) + 10})

    assert _new_ids() == ["b", "c"]


def test_offset_on_a_line_boundary_keeps_the_first_new_line(wired):
    db, jsonl, checkpoint = wired
    shard = jsonl / "zh_gerichte.jsonl"
    shard.write_bytes(_line("a"))
    _make_db(db, ["a"])
    _full_build(checkpoint, {shard: shard.stat().st_size})
    with open(shard, "ab") as f:
        f.write(_line("b") + _line("c"))

    assert _new_ids() == ["b", "c"]


def test_rewritten_shard_waits_for_the_full_build(wired, caplog):
    db, jsonl, checkpoint = wired
    rewritten, appended = jsonl / "ti_gerichte.jsonl", jsonl / "zh_gerichte.jsonl"
    rewritten.write_bytes(_line("t1", "ti_gerichte") + _line("t1_dup", "ti_gerichte"))
    appended.write_bytes(_line("a"))
    _make_db(db, ["t1", "a"])
    _full_build(checkpoint, {rewritten: rewritten.stat().st_size,
                             appended: appended.stat().st_size})
    # A re-fetch rewrites ti with longer texts and appends a new ruling.
    longer = _row("t1", "ti_gerichte")
    longer["full_text"] += " Vollständiger Text nach dem Re-fetch."
    rewritten.write_bytes((json.dumps(longer) + "\n").encode()
                          + _line("t1_dup", "ti_gerichte") + _line("t2", "ti_gerichte"))
    with open(appended, "ab") as f:
        f.write(_line("b"))

    with caplog.at_level(logging.WARNING, logger="quick_publish"):
        assert _new_ids() == ["b"]
    assert "ti_gerichte.jsonl" in caplog.text and "rewritten" in caplog.text


def test_shrunk_shard_is_skipped(wired):
    db, jsonl, checkpoint = wired
    shard = jsonl / "zh_gerichte.jsonl"
    shard.write_bytes(_line("a") + _line("removed"))
    _make_db(db, ["a"])
    _full_build(checkpoint, {shard: shard.stat().st_size})
    shard.write_bytes(_line("a"))         # a takedown removed a row

    assert _new_ids() == []


def test_shard_the_full_build_never_saw_is_read_whole(wired):
    db, jsonl, checkpoint = wired
    old, new = jsonl / "zh_gerichte.jsonl", jsonl / "zg_gvp.jsonl"
    old.write_bytes(_line("a"))
    _make_db(db, ["a"])
    _full_build(checkpoint, {old: old.stat().st_size})
    new.write_bytes(_line("g1", "zg_gvp") + _line("g2", "zg_gvp"))

    assert _new_ids() == ["g1", "g2"]


def test_direct_shard_is_read_before_its_es_twin(wired):
    db, jsonl, checkpoint = wired
    direct, es = jsonl / "zh_gerichte.jsonl", jsonl / "es_zh_gerichte.jsonl"
    direct.write_bytes(_line("a"))
    es.write_bytes(_line("a", "es_zh"))
    _make_db(db, ["a"])
    _full_build(checkpoint, {direct: direct.stat().st_size, es: es.stat().st_size})
    with open(es, "ab") as f:
        f.write(_line("b", "es_zh"))
    with open(direct, "ab") as f:
        f.write(_line("b"))

    rows = qp._read_new_jsonl(None, qp_existing())
    assert [(r["decision_id"], r["court"]) for r in rows] == [
        ("b", "zh_gerichte"), ("b", "es_zh")]  # insert_decision keeps the first


def test_checkpoint_without_tail_hash_resumes_at_the_offset(wired):
    db, jsonl, checkpoint = wired
    shard = jsonl / "zh_gerichte.jsonl"
    shard.write_bytes(_line("a") + _line("a_dup"))
    _make_db(db, ["a"])
    _full_build(checkpoint, {shard: shard.stat().st_size}, tail=False)
    with open(shard, "ab") as f:
        f.write(_line("b"))

    assert _new_ids() == ["b"]


def test_all_courts_run_refuses_without_a_checkpoint_before_copying(wired, monkeypatch):
    db, jsonl, _ = wired
    (jsonl / "zh_gerichte.jsonl").write_bytes(_line("a") + _line("a_dup"))
    _make_db(db, ["a"])
    copies = []
    monkeypatch.setattr(qp.shutil, "copy2", lambda *a, **k: copies.append(a))

    with pytest.raises(qp.MissingFullBuildCheckpoint):
        qp.quick_publish()
    assert copies == []
    assert _ids(db) == {"a"}


def test_court_limited_run_reads_whole_shards_without_a_checkpoint(wired):
    db, jsonl, _ = wired
    (jsonl / "bger.jsonl").write_bytes(_line("x", "bger") + _line("y", "bger"))
    _make_db(db, ["x"])

    assert _new_ids(["bger"]) == ["y"]


def test_full_build_checkpoint_records_the_tail_hash(tmp_path):
    jsonl = tmp_path / "decisions"
    jsonl.mkdir()
    shard = jsonl / "zh_gerichte.jsonl"
    shard.write_bytes(b"".join(_line(f"r{i}") for i in range(200)))  # > 4 KB
    conn = sqlite3.connect(str(tmp_path / "decisions.db"))
    conn.executescript(db_schema.SCHEMA_SQL)

    _, _, checkpoint = build_fts5.import_jsonl(conn, jsonl, None)
    conn.close()

    entry = checkpoint["zh_gerichte.jsonl"]
    assert entry["size"] == shard.stat().st_size
    assert entry["tail_sha256"] == build_fts5.shard_tail_sha256(shard, entry["size"])
    with open(shard, "ab") as f:          # appending keeps the hash
        f.write(_line("r200"))
    assert build_fts5.shard_tail_sha256(shard, entry["size"]) == entry["tail_sha256"]
