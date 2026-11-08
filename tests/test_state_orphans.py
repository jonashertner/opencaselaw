"""scripts/state_orphans.py: state IDs with no shard row (offline)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts import state_orphans as so  # noqa: E402


def _setup(tmp_path, state, rows, es_rows=()):
    st = tmp_path / "state"
    st.mkdir()
    (st / "bstger.jsonl").write_text("".join(s + "\n" for s in state))
    out = tmp_path / "out"
    out.mkdir()
    (out / "bstger.jsonl").write_text(
        "".join(json.dumps({"decision_id": d, "full_text": "x"}) + "\n" for d in rows))
    shards = [out / "bstger.jsonl"]
    if es_rows:
        (out / "es_bstger.jsonl").write_text(
            "".join(json.dumps({"decision_id": d}) + "\n" for d in es_rows))
        shards.append(out / "es_bstger.jsonl")
    args = ["bstger", "--state-dir", str(st)]
    for s in shards:
        args += ["--shard", str(s)]
    return st / "bstger.jsonl", args


def test_report_lists_orphans_and_changes_nothing(tmp_path, capsys):
    state = ["bstger_BB.2004.36", "bstger_BK_B 197_04", "bstger_BB.2004.60"]
    path, args = _setup(tmp_path, state, ["bstger_BB.2004.60"], es_rows=["bstger_BK_B 197_04"])
    before = path.read_text()
    assert so.main(args) == 0
    out = capsys.readouterr().out
    assert "orphans 1" in out and "bstger_BB.2004.36" in out
    assert path.read_text() == before


def test_apply_drops_orphans_with_a_backup(tmp_path, capsys):
    state = ["bstger_BB.2004.36", "bstger_BB.2004.60", "bstger_BB.2004.36"]
    path, args = _setup(tmp_path, state, ["bstger_BB.2004.60"])
    assert so.main(args + ["--apply"]) == 0
    assert path.read_text() == "bstger_BB.2004.60\n"
    backups = list(path.parent.glob("bstger.jsonl.bak-orphans-*"))
    assert len(backups) == 1 and "bstger_BB.2004.36" in backups[0].read_text()


def test_apply_refuses_when_most_ids_look_orphaned(tmp_path):
    state = [f"bstger_X.{i}" for i in range(10)]
    path, args = _setup(tmp_path, state, [])
    assert so.main(args + ["--apply", "--max", "5"]) == 3
    assert len(path.read_text().splitlines()) == 10


def test_missing_shard_is_an_error(tmp_path):
    path, args = _setup(tmp_path, ["bstger_A"], ["bstger_A"])
    assert so.main(["bstger", "--state-dir", str(path.parent),
                    "--shard", str(tmp_path / "nope.jsonl")]) == 2


def test_escaped_ids_are_read():
    assert so.ID_RX.search('{"decision_id": "a\\"b"}').group(1) == 'a\\"b'
