"""Step 6 extends docs/stats/history.json and commits it; the appender can never fail the publish."""
from __future__ import annotations

import inspect
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import publish  # noqa: E402


def test_step_6_commits_the_growth_series():
    src = inspect.getsource(publish.step_6_git_push)
    assert '"docs/stats/history.json"' in src
    assert "_append_stats_history(dry_run)" in src
    # the appender runs after the homepage sync and before the diff check
    assert src.index("_sync_homepage_fallbacks(dry_run)") < src.index("_append_stats_history(dry_run)") < src.index("\"diff\", \"--quiet\"")


def test_append_helper_is_non_fatal(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("simulated crash")
    monkeypatch.setattr(publish.subprocess, "run", boom)
    assert publish._append_stats_history(dry_run=False) is None      # swallowed, logged

    class Proc:
        returncode = 2; stdout = ""; stderr = "append_stats_history: not written: bad input"
    monkeypatch.setattr(publish.subprocess, "run", lambda *a, **k: Proc())
    assert publish._append_stats_history(dry_run=False) is None      # non-zero exit is a WARN


def test_append_helper_dry_run_runs_nothing(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("must not run in dry-run")
    monkeypatch.setattr(publish.subprocess, "run", boom)
    publish._append_stats_history(dry_run=True)


def test_helper_invokes_the_real_script_path(monkeypatch):
    seen = {}
    class Proc:
        returncode = 0; stdout = "append_stats_history: 2026-09-17 total=1 unique=1 -> 1 points"; stderr = ""
    def fake(cmd, **k):
        seen["cmd"] = cmd; seen["timeout"] = k.get("timeout"); return Proc()
    monkeypatch.setattr(publish.subprocess, "run", fake)
    publish._append_stats_history(dry_run=False)
    assert seen["cmd"][1].endswith("scripts/append_stats_history.py") and seen["timeout"] == 30
