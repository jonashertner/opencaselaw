"""scripts/apply_bge_historical_recoveries.py writes the rulings recovered from
neighbouring DFR scans (runbooks/historical_bge_recovered_2026-10-08/) into the
bge_historical shard; the segmentation and the source repair leave those rows
alone. Offline: the texts are files in the repo."""
from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO))
import apply_bge_historical_recoveries as rec
import repair_bge_historical_sources as rep
import segment_bge_historical as segment_script

import source_defects as sd


@pytest.fixture(scope="module")
def entries():
    return rec.load()


def _row(did, court="bge_historical", text="x" * 300, **extra):
    return {"decision_id": did, "court": court, "docket_number": did.split("_", 2)[-1],
            "full_text": text, "decision_date": "1926-01-01", **extra}


def _shard(tmp_path, rows):
    p = tmp_path / "bge_historical.jsonl"
    p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    return p


def _rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_manifest_texts_are_verified_and_listed(entries):
    assert sorted(entries) == ["bge_historical_22_I_12", "bge_historical_39_I_469",
                               "bge_historical_52_I_149", "bge_historical_52_I_23",
                               "bge_historical_52_I_39"]
    for did, e in entries.items():
        d = sd.lookup(did)
        assert d is not None and d.action == "withhold"
        serial = re.match(r"No (\d+),", e["ruling"]).group(1)
        assert re.match(rf"{serial}\.\s", e["text"]), did        # starts at its own header
        volume = int(e["docket_number"].split("_")[0])
        assert int(e["decision_date"][:4]) in (volume + 1873, volume + 1874)
        markers = re.findall(r"\[Pages? (\d+)(?:-(\d+))? (?:is|are) missing from the source\.\]", e["text"])
        missing = [p for a, b in markers for p in range(int(a), int(b or a) + 1)]
        assert missing == e["pages_missing"], did
        assert (e["completeness"] == "complete") == (not missing)


def test_a_changed_text_is_refused(tmp_path):
    d = tmp_path / "rec"
    shutil.copytree(rec.RECOVERY_DIR, d)
    f = d / "39_I_469.txt"
    f.write_text(f.read_text(encoding="utf-8").replace("1943", "1913"), encoding="utf-8")
    with pytest.raises(ValueError, match="39_I_469.txt"):
        rec.load(d)


def test_dry_run_writes_nothing(tmp_path, entries):
    p = _shard(tmp_path, [_row("bge_historical_52_I_23"), _row("bge_historical_52_I_14")])
    before = p.read_bytes()
    stats = rec.run(p, apply=False, entries=entries)
    assert stats["replaced:partial"] == 1 and stats["rows_changed"] == 5   # four rows added
    assert p.read_bytes() == before and not list(tmp_path.glob("*.undo-recovery-*"))


def test_apply_replaces_adds_keeps_an_undo_file_and_is_idempotent(tmp_path, entries):
    old = _row("bge_historical_39_I_469", text="87. Entscheid ... Bachmann " * 20,
               regeste="foreign", text_segment={"x": 1})
    rows = [_row("bge_historical_52_I_14"), old,
            _row("zh_obergericht_52_I_23", court="zh_obergericht"),   # another court, same docket
            _row("bge_historical_22_I_12")]
    p = _shard(tmp_path, rows)
    stats = rec.run(p, apply=True, entries=entries)
    assert stats["replaced:complete"] == 2 and stats["added:partial"] == 3
    out = {r["decision_id"]: r for r in _rows(p)}
    assert out["bge_historical_52_I_14"] == rows[0]
    assert out["zh_obergericht_52_I_23"] == rows[2]
    new = out["bge_historical_39_I_469"]
    e = entries["bge_historical_39_I_469"]
    assert new["full_text"] == e["text"] and new["decision_date"] == "1913-09-11"
    assert new["language"] == "fr" and new["source_url"] == "https://www.fallrecht.ch/c1039465.pdf"
    assert new["title"] == "BGE 39 I 469" and new["court"] == "bge_historical"
    assert "regeste" not in new or new["regeste"] is None          # the foreign ruling's fields are gone
    assert "text_segment" not in new
    assert new["source_recovery"]["completeness"] == "complete"
    assert new["source_recovery"]["text_sha256"] == e["text_sha256"]
    assert out["bge_historical_52_I_23"]["source_recovery"]["pages_missing"] == [24, 25]
    assert out["bge_historical_52_I_23"]["decision_date"] == "1926-02-26"
    undo = next(tmp_path.glob("bge_historical.jsonl.undo-recovery-*.jsonl.bak"))
    assert list(tmp_path.glob("*.jsonl")) == [p]   # the build ingests every *.jsonl beside the shard
    assert sorted(json.loads(x)["decision_id"] for x in undo.read_text(encoding="utf-8").splitlines()) == [
        "bge_historical_22_I_12", "bge_historical_39_I_469"]
    again = rec.run(p, apply=True, entries=entries)
    assert again["rows_changed"] == 0 and again["already_recovered"] == 5


def test_the_other_repairs_leave_a_recovered_row_alone(tmp_path, entries):
    p = _shard(tmp_path, [])
    rec.run(p, apply=True, entries=entries)
    for row in _rows(p):
        assert rep.decide(row) == ("kept:recovered", row)
        assert segment_script.decide(row) == {"_why": "recovered"}
    # an unrecovered listed row is still removed by the repair
    assert rep.decide(_row("bge_historical_52_I_23"))[1] is None
