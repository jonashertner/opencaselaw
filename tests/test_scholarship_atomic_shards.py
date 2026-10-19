"""Custom scholarship harvesters never truncate a live shard (offline).

2026-09-13: the weekly harvest hit its shared 3,600 s cap while anci_ch was
streaming into its live shard with open("w"); the shard went from 100 records
to 0 bytes and two DB builds served none of them.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from scrapers.scholarship import _atomic, anci_ch
from scrapers.scholarship._atomic import atomic_jsonl

REPO = Path(__file__).resolve().parents[1]
CUSTOM_MODULES = ["anci_ch", "leges", "thegoodboard", "repositorium_ch", "wordpress_rest"]


def _write(path: Path, n: int, tag: str = "old") -> None:
    path.write_text("".join(json.dumps({"i": i, "v": tag}) + "\n" for i in range(n)),
                    encoding="utf-8")


def _lines(path: Path) -> int:
    return sum(1 for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip())


def test_normal_completion_replaces_the_shard(tmp_path):
    shard = tmp_path / "x.jsonl"
    _write(shard, 10)
    with atomic_jsonl(shard) as fh:
        for i in range(12):
            fh.write(json.dumps({"i": i, "v": "new"}) + "\n")
    assert _lines(shard) == 12
    assert '"new"' in shard.read_text()
    assert not (tmp_path / "x.jsonl.tmp").exists()


def test_interrupted_harvest_leaves_the_live_shard_intact(tmp_path):
    shard = tmp_path / "x.jsonl"
    _write(shard, 100)
    before = shard.read_bytes()
    with pytest.raises(KeyboardInterrupt):
        with atomic_jsonl(shard) as fh:
            fh.write(json.dumps({"i": 0}) + "\n")
            raise KeyboardInterrupt
    assert shard.read_bytes() == before
    assert (tmp_path / "x.jsonl.tmp").exists()


def test_a_kill_mid_write_never_touches_the_live_shard(tmp_path):
    # SIGTERM/SIGKILL end the process inside the block: nothing after the
    # yield runs. Simulate by abandoning the generator mid-block.
    shard = tmp_path / "x.jsonl"
    _write(shard, 100)
    before = shard.read_bytes()
    cm = atomic_jsonl(shard)
    fh = cm.__enter__()
    fh.write("partial\n")
    fh.flush()
    assert shard.read_bytes() == before


def test_large_shrink_keeps_the_previous_shard(tmp_path):
    shard = tmp_path / "x.jsonl"
    _write(shard, 100)
    with atomic_jsonl(shard) as fh:
        for i in range(60):
            fh.write(json.dumps({"i": i, "v": "new"}) + "\n")
    assert _lines(shard) == 100 and '"old"' in shard.read_text()
    assert _lines(tmp_path / "x.jsonl.shrunk") == 60


def test_small_shrink_is_accepted(tmp_path):
    shard = tmp_path / "x.jsonl"
    _write(shard, 100)
    with atomic_jsonl(shard) as fh:
        for i in range(95):
            fh.write(json.dumps({"i": i, "v": "new"}) + "\n")
    assert _lines(shard) == 95


def test_first_harvest_and_empty_to_empty(tmp_path):
    shard = tmp_path / "new.jsonl"
    with atomic_jsonl(shard) as fh:
        fh.write(json.dumps({"i": 0}) + "\n")
    assert _lines(shard) == 1
    empty = tmp_path / "empty.jsonl"
    empty.write_text("")
    with atomic_jsonl(empty):
        pass
    assert empty.read_text() == ""


def test_upstream_outage_does_not_wipe_anci_ch(tmp_path, monkeypatch):
    shard = tmp_path / "anci_ch.jsonl"
    _write(shard, 100)
    before = shard.read_bytes()
    home = "".join(f'<a href="https://anci.ch/articles/{i}">x</a>' for i in range(100))

    def fetch(url, timeout=15):
        if url == anci_ch.HOMEPAGE:
            return home
        raise OSError("upstream down")

    monkeypatch.setattr(anci_ch, "_fetch", fetch)
    monkeypatch.setattr(anci_ch.time, "sleep", lambda s: None)
    anci_ch.harvest(output_dir=tmp_path)
    assert shard.read_bytes() == before


@pytest.mark.parametrize("module", CUSTOM_MODULES)
def test_no_custom_harvester_writes_its_live_shard_directly(module):
    src = (REPO / "scrapers" / "scholarship" / f"{module}.py").read_text(encoding="utf-8")
    assert not re.search(r"out_path\.open\(\s*[\"']w", src)
    assert "atomic_jsonl(out_path)" in src
