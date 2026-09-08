"""Step 2g builds the structure sidecar from the served text, behind a coverage gate."""
import shutil
import sqlite3
from pathlib import Path

import publish


def _decisions(path, ids):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE decisions (decision_id TEXT PRIMARY KEY, court TEXT)")
    con.executemany("INSERT INTO decisions VALUES (?, 'bger')", [(i,) for i in ids])
    con.commit(); con.close()


def _sidecar(path, ids):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE erwaegungen_paragraph (decision_id TEXT, e_number TEXT, depth INTEGER, parent TEXT, text TEXT)")
    con.executemany("INSERT INTO erwaegungen_paragraph VALUES (?, '1', 1, NULL, 'x')", [(i,) for i in ids])
    con.execute("CREATE INDEX idx_erw_decision ON erwaegungen_paragraph(decision_id)")
    con.commit(); con.close()


def test_structure_coverage_counts_current_decisions_only(tmp_path):
    _decisions(tmp_path / "decisions.db", ["a", "b", "c"])
    _sidecar(tmp_path / "side.db", ["a", "b", "stale_1", "stale_2"])
    assert publish._structure_coverage(tmp_path / "side.db", tmp_path / "decisions.db") == 2
    assert publish._structure_coverage(tmp_path / "missing.db", tmp_path / "decisions.db") is None


def _setup(tmp_path, monkeypatch, old_ids, new_ids, ok=True):
    out = tmp_path / "output"; out.mkdir()
    _decisions(out / "decisions.db", ["a", "b", "c", "d", "e"])
    _sidecar(out / "decision_structure.db", old_ids)
    new = tmp_path / "new.db"; _sidecar(new, new_ids)
    monkeypatch.setattr(publish, "OUTPUT_DIR", out)
    monkeypatch.setattr(publish, "REPO_DIR", Path(publish.__file__).parent)
    calls = []
    def fake_run_cmd(cmd, description, dry_run=False, **kwargs):
        calls.append((cmd, description, kwargs))
        if ok:
            shutil.copy(new, out / "decision_structure.db.tmp")
        return ok
    monkeypatch.setattr(publish, "run_cmd", fake_run_cmd)
    fallback = []
    monkeypatch.setattr(publish, "_step_2g_from_shards", lambda dry_run=False: fallback.append(dry_run) or True)
    return out, calls, fallback


def test_served_text_sidecar_is_swapped_in_when_coverage_holds(tmp_path, monkeypatch):
    out, calls, fallback = _setup(tmp_path, monkeypatch, old_ids=["a", "b", "c"], new_ids=["a", "b", "c", "d"])
    assert publish.step_2g_build_decision_structure() is True
    cmd = calls[0][0]
    assert "extract_decision_structure_incremental.py" in cmd[1] and "--output" in cmd and cmd[-1].endswith("decision_structure.db.tmp")
    assert calls[0][2]["timeout"] == 14400 and not fallback
    assert not (out / "decision_structure.db.tmp").exists()
    assert publish._structure_coverage(out / "decision_structure.db", out / "decisions.db") == 4


def test_coverage_gate_keeps_the_old_sidecar(tmp_path, monkeypatch):
    out, calls, fallback = _setup(tmp_path, monkeypatch, old_ids=["a", "b", "c", "d", "e"], new_ids=["a"])
    assert publish.step_2g_build_decision_structure() is False
    assert not (out / "decision_structure.db.tmp").exists() and not fallback
    assert publish._structure_coverage(out / "decision_structure.db", out / "decisions.db") == 5


def test_failed_extractor_falls_back_to_the_shard_build_only_without_a_sidecar(tmp_path, monkeypatch):
    """Covered in detail below (2026-09-08): a readable sidecar is kept, a
    missing one triggers the shard build."""
    out, calls, fallback = _setup(tmp_path, monkeypatch, old_ids=["a"], new_ids=["a", "b"], ok=False)
    (out / "decision_structure.db").unlink()
    assert publish.step_2g_build_decision_structure() is True and fallback == [False]


def test_full_rebuild_no_longer_forces_a_full_extraction(tmp_path, monkeypatch):
    """2026-09-07: forcing a full re-extraction on every full build (and on
    every `--step 2g`, which counts as one) loaded 1.07M decisions into memory
    and was killed by the stall watchdog. The extractor bootstraps by itself
    when it has no state or a new version; a full build only diffs."""
    monkeypatch.delenv("OCL_STRUCTURE_FORCE_FULL", raising=False)
    out, calls, fallback = _setup(tmp_path, monkeypatch, old_ids=["a"], new_ids=["a", "b"])
    assert publish.step_2g_build_decision_structure(full_rebuild=True) is True
    assert "--force-full" not in calls[0][0]


def test_env_override_still_forces_a_full_extraction(tmp_path, monkeypatch):
    monkeypatch.setenv("OCL_STRUCTURE_FORCE_FULL", "1")
    out, calls, fallback = _setup(tmp_path, monkeypatch, old_ids=["a"], new_ids=["a", "b"])
    assert publish.step_2g_build_decision_structure() is True and "--force-full" in calls[0][0]


# ── 2026-09-08 review fixes: resolved paths, disk space, failure policy ──

def test_failed_extractor_keeps_a_readable_sidecar_instead_of_rebuilding(tmp_path, monkeypatch):
    """The shard build is the safety net for a MISSING sidecar only; rebuilding
    an existing one costs ~4.5 h. A killed bootstrap resumes next run."""
    out, calls, fallback = _setup(tmp_path, monkeypatch, old_ids=["a"], new_ids=["a", "b"], ok=False)
    assert publish.step_2g_build_decision_structure() is False
    assert fallback == []
    assert (out / "decision_structure.db").exists()


def test_failed_extractor_without_any_sidecar_falls_back_to_shards(tmp_path, monkeypatch):
    out, calls, fallback = _setup(tmp_path, monkeypatch, old_ids=["a"], new_ids=["a", "b"], ok=False)
    (out / "decision_structure.db").unlink()
    assert publish.step_2g_build_decision_structure() is True and fallback == [False]


def test_symlinked_sidecar_builds_and_swaps_on_the_data_volume(tmp_path, monkeypatch):
    """Production: output/decision_structure.db -> /mnt/.../decision_structure.db.
    The tmp must live beside the REAL file and the swap must replace the real
    file, never the symlink (2026-09-08 review: the old code would have filled
    the root disk and turned the symlink into a file)."""
    out = tmp_path / "output"; out.mkdir()
    volume = tmp_path / "volume"; volume.mkdir()
    _decisions(out / "decisions.db", ["a", "b", "c"])
    _sidecar(volume / "decision_structure.db", ["a"])
    (out / "decision_structure.db").symlink_to(volume / "decision_structure.db")
    new = tmp_path / "new.db"; _sidecar(new, ["a", "b", "c"])
    monkeypatch.setattr(publish, "OUTPUT_DIR", out)
    monkeypatch.setattr(publish, "REPO_DIR", Path(publish.__file__).parent)
    calls = []
    def fake_run_cmd(cmd, description, dry_run=False, **kwargs):
        calls.append(cmd)
        shutil.copy(new, cmd[cmd.index("--output") + 1])
        return True
    monkeypatch.setattr(publish, "run_cmd", fake_run_cmd)
    assert publish.step_2g_build_decision_structure() is True
    cmd = calls[0]
    assert cmd[cmd.index("--output") + 1] == str(volume / "decision_structure.db.tmp")
    assert cmd[cmd.index("--structure-db") + 1] == str(volume / "decision_structure.db")
    assert (out / "decision_structure.db").is_symlink()                       # symlink untouched
    assert publish._structure_coverage(volume / "decision_structure.db", out / "decisions.db") == 3
    assert not (volume / "decision_structure.db.tmp").exists()
    assert not (out / "decision_structure.db.tmp").exists()


def test_not_enough_free_space_keeps_the_current_sidecar(tmp_path, monkeypatch):
    out, calls, fallback = _setup(tmp_path, monkeypatch, old_ids=["a"], new_ids=["a", "b"])
    import collections
    usage = collections.namedtuple("usage", "total used free")
    monkeypatch.setattr(publish.shutil, "disk_usage", lambda p: usage(100, 100, 0))
    assert publish.step_2g_build_decision_structure() is False
    assert calls == [] and fallback == []
