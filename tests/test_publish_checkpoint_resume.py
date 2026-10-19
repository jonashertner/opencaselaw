"""A scheduled run must not resume past a completed database rebuild (2026-10-07).

Tuesday's full build ended at 23:41 UTC with only the early stats step red. The
03:30 timer run found the checkpoint ten minutes before its 4 h TTL ran out,
"resumed", reran that one step and skipped step 2: a day of scraped decisions,
the ZH migration and two de-listings did not reach the site. Now a checkpoint
whose step 2 completed is honoured only with --resume; one whose rebuild failed
is resumed as before.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import publish


def _write(tmp_path, monkeypatch, results, age_h=0.2):
    path = tmp_path / "checkpoint.json"
    path.write_text(json.dumps({
        "last_completed_step": "6b",
        "results": results,
        "timestamp": (datetime.now(timezone.utc) - timedelta(hours=age_h)).isoformat(),
    }))
    monkeypatch.setattr(publish, "CHECKPOINT_PATH", path)


def test_a_completed_rebuild_is_not_resumed_by_a_scheduled_run(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, {"2": True, "5a": False, "5b": True, "6b": True})
    assert publish._load_checkpoint() is None


def test_resume_flag_still_continues_it(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, {"2": True, "5a": False, "5b": True})
    cp = publish._load_checkpoint(resume_requested=True)
    assert cp is not None and cp["results"]["5a"] is False


def test_a_failed_rebuild_is_resumed(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, {"1": True, "2": False, "5b": True})
    assert publish._load_checkpoint() is not None


def test_a_crash_before_the_rebuild_finished_is_resumed(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, {"1": True})
    assert publish._load_checkpoint() is not None


def test_the_ttl_still_applies(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, {"2": False}, age_h=5)
    assert publish._load_checkpoint() is None
    assert publish._load_checkpoint(resume_requested=True) is None


def test_cli_accepts_resume():
    assert "--resume" in Path(publish.__file__).read_text()
