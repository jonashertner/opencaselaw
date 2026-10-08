"""import_jsonl reads shards only: <name>.jsonl with no other dot in the name.

Repair scripts used to write their undo files beside the shard as
<shard>.<kind>-<date>.jsonl (bge_historical.jsonl.source-repair-2026-10-08.jsonl),
and the glob in import_jsonl took them for shards: INSERT OR IGNORE put the
removed rows back, and the text-upgrade path (same canonical_key, incoming text
>2x longer and +1000 chars) put the old long text back over the repaired one.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import build_fts5  # noqa: E402
from db_schema import SCHEMA_SQL  # noqa: E402

OWN = "Auszug aus dem Urteil der I. Zivilabteilung. " * 40          # the repaired text
FOREIGN = OWN + "Fremde Seiten eines anderen Urteils. " * 200     # the text before the cut


def _row(decision_id, text, docket):
    return {"decision_id": decision_id, "court": "bge_historical", "canton": "CH",
            "docket_number": docket, "decision_date": "1926-02-26", "language": "de",
            "full_text": text}


def _write(path, rows):
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def _served(c):
    """{decision_id: full_text} as stored (bge_historical rows are served as bge)."""
    return dict(c.execute("SELECT decision_id, full_text FROM decisions"))


def _conn():
    c = sqlite3.connect(":memory:")
    c.executescript(SCHEMA_SQL)
    return c


def _dir(tmp_path):
    _write(tmp_path / "bge_historical.jsonl", [_row("bge_historical_52_I_23", OWN, "52 I 23")])
    # undo file under the scripts' old name: the cut row as it was, and a removed row
    _write(tmp_path / "bge_historical.jsonl.source-repair-2026-10-08.jsonl",
           [_row("bge_historical_52_I_23", FOREIGN, "52 I 23"),
            _row("bge_historical_39_I_469", OWN, "39 I 469")])
    _write(tmp_path / "bs_gerichte.refetch.jsonl",
           [{"decision_id": "bs_appellationsgericht_AG.2025.449", "court": "bs_appellationsgericht",
             "docket_number_2": "AG.2025.449", "full_text": OWN}])
    return tmp_path


def test_files_with_a_dot_before_jsonl_are_not_read_as_shards(tmp_path, caplog):
    d = _dir(tmp_path)
    c = _conn()
    with caplog.at_level(logging.WARNING, logger=build_fts5.logger.name):
        imported, _skipped, checkpoint = build_fts5.import_jsonl(c, d)
    # no removed row back, no old text over the repaired one
    assert _served(c) == {"bge_52_I_23": OWN.strip()}
    assert imported == 1
    assert sorted(checkpoint) == ["bge_historical.jsonl"]
    warned = " ".join(r.getMessage() for r in caplog.records if r.levelno == logging.WARNING)
    assert "bge_historical.jsonl.source-repair-2026-10-08.jsonl" in warned
    assert "bs_gerichte.refetch.jsonl" in warned


def test_the_guard_holds_in_incremental_mode_and_plain_order(tmp_path, monkeypatch):
    d = _dir(tmp_path)
    monkeypatch.setenv("BUILD_FTS5_DIRECT_FIRST", "0")
    c = _conn()
    _, _, checkpoint = build_fts5.import_jsonl(c, d, checkpoint={})
    assert _served(c) == {"bge_52_I_23": OWN.strip()}
    assert sorted(checkpoint) == ["bge_historical.jsonl"]


def test_ordinary_shard_names_are_all_read(tmp_path):
    _write(tmp_path / "bge_historical.jsonl", [_row("bge_historical_52_I_23", OWN, "52 I 23")])
    _write(tmp_path / "es_bge.jsonl", [_row("bge_historical_39_I_469", OWN, "39 I 469")])
    _write(tmp_path / "zh_gerichte.jsonl.bak-portalmeta-20261006", [_row("x_1", OWN, "1")])  # not *.jsonl
    c = _conn()
    imported, _, checkpoint = build_fts5.import_jsonl(c, tmp_path)
    assert imported == 2
    assert sorted(checkpoint) == ["bge_historical.jsonl", "es_bge.jsonl"]
