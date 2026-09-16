from __future__ import annotations

import sys

import publish
import pytest


@pytest.fixture(autouse=True)
def _no_real_ntfy(monkeypatch):
    """Never post to the real ntfy topic from the test suite.

    Three tests in this file drive publish.main() through the success branch
    with fake STEPS. Until 2026-09-16 that reached the real _notify() and
    pushed "Publish OK / ? decisions, 0 min" to ntfy.sh/opencaselaw-publish on
    every local and CI run - operator-alert noise on the same channel that
    carries "Publish FAILED", observed as three messages per suite run.
    Tests that assert on notifications patch _notify again themselves; their
    patch is applied later and still wins.
    """
    monkeypatch.setattr(publish, "_notify", lambda *a, **k: None)


def test_publish_manual_weekly_step_forces_execution(monkeypatch):
    called: dict[str, object] = {}

    def _fake_weekly_step(*, dry_run: bool = False, full_rebuild: bool = False) -> bool:
        called["dry_run"] = dry_run
        called["full_rebuild"] = full_rebuild
        return True

    monkeypatch.setattr(publish, "STEPS", [("2d", "Quality Enrichment", _fake_weekly_step)])
    monkeypatch.setattr(sys, "argv", ["publish.py", "--step", "2d", "--dry-run"])

    publish.main()

    assert called["dry_run"] is True
    # Manual step mode should force weekly-gated step execution.
    assert called["full_rebuild"] is True


def test_publish_full_rebuild_flag_reaches_weekly_steps(monkeypatch):
    called: dict[str, object] = {}

    def _fake_weekly_step(*, dry_run: bool = False, full_rebuild: bool = False) -> bool:
        called["dry_run"] = dry_run
        called["full_rebuild"] = full_rebuild
        return True

    monkeypatch.setattr(publish, "STEPS", [("2c", "Reference Graph", _fake_weekly_step)])
    monkeypatch.setattr(sys, "argv", ["publish.py", "--dry-run", "--full-rebuild"])

    publish.main()

    assert called["dry_run"] is True
    assert called["full_rebuild"] is True


def test_publish_skips_ingest_by_default(monkeypatch):
    """Step 1 (ingest) should be skipped unless --ingest is passed."""
    called = {"ingest": False}

    def _fake_ingest(dry_run: bool = False) -> bool:
        called["ingest"] = True
        return True

    monkeypatch.setattr(publish, "STEPS", [(1, "Ingest", _fake_ingest)])
    monkeypatch.setattr(sys, "argv", ["publish.py", "--dry-run"])

    publish.main()

    assert called["ingest"] is False, "Ingest should not run without --ingest flag"


def test_publish_runs_ingest_with_flag(monkeypatch):
    """Step 1 runs when --ingest is passed."""
    called = {"ingest": False}

    def _fake_ingest(dry_run: bool = False) -> bool:
        called["ingest"] = True
        return True

    monkeypatch.setattr(publish, "STEPS", [(1, "Ingest", _fake_ingest)])
    monkeypatch.setattr(sys, "argv", ["publish.py", "--dry-run", "--ingest"])

    publish.main()

    assert called["ingest"] is True


def test_publish_sqlite_snapshot_can_run_without_delta_state(monkeypatch):
    """A one-off SQLite snapshot should not require hf_delta_snapshot.json."""
    called: dict[str, object] = {}

    def _fake_run_cmd(cmd, desc, dry_run=False, **kwargs):
        called["cmd"] = cmd
        called["desc"] = desc
        called["dry_run"] = dry_run
        return True

    monkeypatch.delenv("OCL_PUBLISH_DELTA", raising=False)
    monkeypatch.setenv("OCL_PUBLISH_SQLITE_SNAPSHOT", "1")
    # Force the snapshot path regardless of weekday — this test exercises
    # dispatch, not the Sunday cadence gate. Without this it silently passes
    # only on Sundays (and never ran in CI while the file sat at repo root).
    monkeypatch.setenv("OCL_PUBLISH_SQLITE_SNAPSHOT_WEEKDAY", "-1")
    monkeypatch.setattr(publish, "run_cmd", _fake_run_cmd)

    assert publish.step_7_publish_delta(dry_run=True) is True

    cmd = called["cmd"]
    assert "--snapshot-only" in cmd
    assert "--publish-snapshot" in cmd
    assert "--dry-run" in cmd
    assert called["desc"] == "Publish artifacts"


def test_append_run_record_is_durable_and_never_raises(tmp_path, monkeypatch):
    """publish_runs.jsonl is the pipeline's only durable structured
    record — one line per step and per run, written on success AND
    failure. Until 2026-08-19 a failed run left nothing behind (the
    failure branch exited before any marker), so build creep and gate
    timeouts were invisible until they hurt. Telemetry must also never
    be able to break the pipeline: a write failure is swallowed."""
    import json as _json
    import publish as p
    monkeypatch.setattr(p, "REPO_DIR", tmp_path)
    p._append_run_record({"type": "run_start", "run_id": "r1"})
    p._append_run_record({"type": "step", "run_id": "r1", "step": "2",
                          "status": "failed", "elapsed_s": 1.5})
    p._append_run_record({"type": "run_summary", "run_id": "r1",
                          "outcome": "failed", "failed_steps": ["2 (Build FTS5)"]})
    lines = [_json.loads(l) for l in
             (tmp_path / "state" / "publish_runs.jsonl").read_text().splitlines()]
    assert [l["type"] for l in lines] == ["run_start", "step", "run_summary"]
    assert lines[2]["outcome"] == "failed", "failed runs must leave a record"

    # A broken filesystem must not become a broken pipeline.
    monkeypatch.setattr(p, "REPO_DIR", tmp_path / "nope" / "\0bad")
    p._append_run_record({"type": "step"})   # must not raise


def test_step_2c_uses_the_incremental_builder_and_seeds_state(monkeypatch, tmp_path):
    """Step 2c must leave `meta` + `processed_decisions` on the live graph.

    The full builder (build_reference_graph.py) never writes those tables,
    so a subsequent --in-place incremental run finds no diff base and
    bootstraps the whole graph (~3h22m on production) instead of applying
    a delta. That is the concrete blocker for the weekday-incremental
    cutover, so pin the invocation.
    """
    import publish

    captured = {}

    def _fake_run_cmd(cmd, desc, dry_run=False, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return True

    db = tmp_path / "decisions.db"
    db.write_bytes(b"")
    script = tmp_path / "search_stack" / "build_reference_graph_incremental.py"
    script.parent.mkdir(parents=True)
    script.write_text("# stub\n")

    monkeypatch.setattr(publish, "run_cmd", _fake_run_cmd)
    monkeypatch.setattr(publish, "REPO_DIR", tmp_path)
    monkeypatch.setattr(publish, "DB_PATH", db)
    monkeypatch.setattr(publish, "OUTPUT_DIR", tmp_path)

    assert publish.step_2c_build_reference_graph(dry_run=False) is True

    cmd = captured["cmd"]
    assert str(script) in cmd, f"2c did not call the incremental builder: {cmd}"
    assert "--force-full" in cmd, "2c must force a full rebuild, not a delta"
    assert "--in-place" in cmd, "2c must publish onto the live graph"
    assert "--decisions-db" in cmd and "--graph-db" in cmd
    # The old full-builder flags must be gone — they are not accepted by
    # the incremental builder's argparse and would fail at runtime.
    assert "--source-db" not in cmd and "--db" not in cmd
    # Keep the measured headroom: bootstrap was 12,140s on production.
    assert captured["kwargs"].get("timeout", 0) >= 18000


# ── manual single-step runs must not impersonate a full publish ───────────
# (2026-09-04 Stage A review: the incremental orchestrator drives 2g, 5b, 5c,
# 5d, 7, 6, 6b as `publish.py --step X`; each used to clear the resume
# checkpoint, refresh state/last_publish_success.json and push "Publish OK".)

def _fake_ok_step(dry_run: bool = False, full_rebuild: bool = False) -> bool:
    return True


def _capture(monkeypatch, tmp_path):
    notes: list[tuple] = []
    events: dict[str, int] = {"clear": 0, "save": 0}
    monkeypatch.setattr(publish, "REPO_DIR", tmp_path)
    monkeypatch.setattr(publish, "_notify", lambda *a, **k: notes.append(a))
    monkeypatch.setattr(publish, "_clear_checkpoint", lambda: events.__setitem__("clear", events["clear"] + 1))
    monkeypatch.setattr(publish, "_save_checkpoint", lambda *a, **k: events.__setitem__("save", events["save"] + 1))
    monkeypatch.setattr(publish, "_load_checkpoint", lambda: None)
    monkeypatch.setattr(publish, "_append_run_record", lambda rec: None)
    monkeypatch.setattr(publish, "STEPS", [("5b", "Generate RSS Feeds", _fake_ok_step)])
    return notes, events


def test_manual_step_leaves_marker_checkpoint_and_notify_alone(monkeypatch, tmp_path):
    notes, events = _capture(monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "argv", ["publish.py", "--step", "5b", "--dry-run"])
    publish.main()
    assert not (tmp_path / "state" / "last_publish_success.json").exists()
    assert events == {"clear": 0, "save": 0}
    assert not any(a and a[0] == "Publish OK" for a in notes)


def test_full_run_still_writes_marker_and_notifies(monkeypatch, tmp_path):
    notes, events = _capture(monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "argv", ["publish.py", "--dry-run"])
    publish.main()
    assert (tmp_path / "state" / "last_publish_success.json").exists()
    assert events["clear"] == 1 and events["save"] == 1
    assert any(a and a[0] == "Publish OK" for a in notes)


def test_lock_refusal_exits_non_zero(monkeypatch, tmp_path):
    """A run that cannot take the publish flock is a missed publish; with
    exit 0 the unit stayed green and nobody was paged."""
    import fcntl
    _capture(monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "argv", ["publish.py", "--dry-run"])

    def _busy(*a, **k):
        raise BlockingIOError

    monkeypatch.setattr(fcntl, "flock", _busy)
    import pytest
    with pytest.raises(SystemExit) as e:
        publish.main()
    assert e.value.code == 2
