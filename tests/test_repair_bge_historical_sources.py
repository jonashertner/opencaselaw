"""scripts/repair_bge_historical_sources.py takes the other ruling's text out of
the shard rows that source_defects.py lists: withheld rows leave the shard, the
truncated one is cut while its text is the verified one. Dry run by default,
undo file, idempotent, other courts never touched."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO))
import repair_bge_historical_sources as rep

import source_defects as sd

OWN = "49. Auszug aus dem Urteil vom 25. September 1945 i. S. Habegger. Erwägung. " * 6
FOREIGN = "\n154\n32. Auszug aus dem Urteil vom 20. Februar 1951 i. S. Frank A.-G. gegen Frigaliment. " * 6
TEXT = OWN + FOREIGN


@pytest.fixture
def cut_entry(monkeypatch):
    entry = sd.SourceDefect(reference="71 II 223", kind="foreign_pages_appended", action="truncate",
                            holds="71 II 223 + 77 II 154", copy_of="bge_77_II_154", note="n",
                            text_sha256=hashlib.sha256(TEXT.encode()).hexdigest(), keep_chars=len(OWN))
    monkeypatch.setitem(sd._BY_PARTS, (71, "II", 223), entry)
    return entry


def _row(did, court="bge_historical", docket=None, text="x" * 300):
    return {"decision_id": did, "court": court, "docket_number": docket or did.split("_", 2)[-1],
            "full_text": text}


def _shard(tmp_path, rows):
    p = tmp_path / "bge_historical.jsonl"
    p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    return p


def _ids(path):
    return [json.loads(l)["decision_id"] for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def test_dry_run_reports_and_writes_nothing(tmp_path, cut_entry):
    p = _shard(tmp_path, [_row("bge_historical_52_I_8"), _row("bge_historical_71_II_223", text=TEXT),
                          _row("bge_historical_52_I_14")])
    before = p.read_bytes()
    stats = rep.run(p, apply=False)
    assert stats["removed:foreign_volume"] == 1 and stats["cut:foreign_pages_appended"] == 1
    assert stats["untouched"] == 1 and stats["rows_changed"] == 2
    assert p.read_bytes() == before and not list(tmp_path.glob("*.undo-source-repair-*"))


def test_apply_removes_cuts_keeps_an_undo_file_and_is_idempotent(tmp_path, cut_entry):
    rows = [_row("bge_historical_52_I_8"), _row("bge_historical_39_I_469"),
            _row("bge_historical_71_II_223", text=TEXT), _row("bge_historical_52_I_14"),
            # another court whose docket reads like a listed BGE reference
            _row("zh_obergericht_52_I_8", court="zh_obergericht", docket="52_I_8")]
    p = _shard(tmp_path, rows)
    rep.run(p, apply=True)
    assert _ids(p) == ["bge_historical_71_II_223", "bge_historical_52_I_14", "zh_obergericht_52_I_8"]
    cut = json.loads(p.read_text(encoding="utf-8").splitlines()[0])
    assert cut["full_text"] == OWN.rstrip() and "Frigaliment" not in cut["full_text"]
    assert cut["source_repair"]["chars_before"] == len(TEXT)
    assert cut["source_repair"]["sha256_before"] == hashlib.sha256(TEXT.encode()).hexdigest()
    undo = next(tmp_path.glob("bge_historical.jsonl.undo-source-repair-*.jsonl.bak"))
    assert list(tmp_path.glob("*.jsonl")) == [p]   # the build ingests every *.jsonl beside the shard
    assert sorted(json.loads(l)["decision_id"] for l in undo.read_text(encoding="utf-8").splitlines()) == [
        "bge_historical_39_I_469", "bge_historical_52_I_8", "bge_historical_71_II_223"]
    assert json.loads(undo.read_text(encoding="utf-8").splitlines()[2])["full_text"] == TEXT
    again = rep.run(p, apply=True)
    assert again["rows_changed"] == 0 and again["truncate:not_the_verified_text"] == 1


def test_a_row_cut_elsewhere_is_left_alone(tmp_path, cut_entry):
    p = _shard(tmp_path, [_row("bge_historical_71_II_223", text=OWN[:200] + " (segmented)")])
    stats = rep.run(p, apply=False)
    assert stats["truncate:not_the_verified_text"] == 1 and stats["rows_changed"] == 0


def test_cli_dry_run(tmp_path):
    p = _shard(tmp_path, [_row("bge_52_I_149", court="bge"), _row("bge_52_I_154", court="bge")])
    proc = subprocess.run([sys.executable, str(REPO / "scripts" / "repair_bge_historical_sources.py"), str(p)],
                          capture_output=True, text=True, check=True)
    assert "DRY RUN" in proc.stderr and "bge_52_I_149: removed:foreign_volume" in proc.stderr
    assert "removed:foreign_volume" in proc.stdout and _ids(p) == ["bge_52_I_149", "bge_52_I_154"]
