"""publish.py step 2g honours OCL_SERVED_TEXT_STRUCTURE=0 by building the
sidecar from shards instead of running the served-text extractor."""
from __future__ import annotations

import publish


def test_step_2g_switch_off_uses_shard_build(monkeypatch):
    calls: list = []
    monkeypatch.setattr(publish, "_step_2g_from_shards",
                        lambda dry_run=False: calls.append(dry_run) or True)

    def _no_run(*a, **k):
        raise AssertionError("run_cmd must not be called when the switch is off")
    monkeypatch.setattr(publish, "run_cmd", _no_run)
    monkeypatch.setenv("OCL_SERVED_TEXT_STRUCTURE", "0")
    assert publish.step_2g_build_decision_structure(dry_run=True) is True
    assert calls == [True]


def test_step_2g_default_runs_served_text_extractor(monkeypatch, tmp_path):
    (tmp_path / "decisions.db").write_bytes(b"")
    monkeypatch.setattr(publish, "OUTPUT_DIR", tmp_path)
    monkeypatch.delenv("OCL_SERVED_TEXT_STRUCTURE", raising=False)
    seen: list = []
    monkeypatch.setattr(publish, "run_cmd", lambda cmd, *a, **k: seen.append(cmd) or True)

    def _no_shards(dry_run=False):
        raise AssertionError("shard fallback must not run when the switch is on")
    monkeypatch.setattr(publish, "_step_2g_from_shards", _no_shards)
    assert publish.step_2g_build_decision_structure(dry_run=True) is True
    assert seen and "extract_decision_structure_incremental.py" in " ".join(seen[0])
