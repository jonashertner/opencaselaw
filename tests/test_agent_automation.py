from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

import scripts.agent_assess as agent_assess
import scripts.agent_record as agent_record
import scripts.agent_safe_deploy as agent_safe_deploy


REPO = Path(__file__).resolve().parents[1]


def test_git_status_parser_preserves_first_path_character():
    changed, untracked = agent_assess.parse_git_status(
        " M docs/example.md\n"
        "?? .agents/\n"
    )

    assert changed == [".agents/", "docs/example.md"]
    assert untracked == 1


def test_quality_summary_flags_critical_and_staleness():
    now = datetime(2026, 6, 8, 12, tzinfo=timezone.utc)
    summary = agent_assess.summarize_quality(
        {
            "available": True,
            "payload": {
                "run_at": "2026-06-06T00:00:00+00:00",
                "critical_failures": 1,
                "quarantine_failures": 2,
                "warning_failures": 3,
                "publish_safe": False,
            },
        },
        now,
    )

    assert summary["ok"] is False
    assert summary["age_hours"] == 60.0
    assert summary["critical_failures"] == 1
    assert summary["quarantine_failures"] == 2
    assert summary["publish_safe"] is False


def test_scraper_summary_flags_explicit_and_silent_failures():
    now = datetime(2026, 6, 8, 1, tzinfo=timezone.utc)
    summary = agent_assess.summarize_scraper_health(
        {
            "available": True,
            "payload": {
                "run_at": "2026-06-08T00:00:00+00:00",
                "scrapers": {
                    "ok": {"success": True, "new_count": 0, "discovery_errors": 0},
                    "bad": {"success": False, "new_count": 0, "discovery_errors": 0},
                    "silent": {"success": True, "new_count": 0, "discovery_errors": 3},
                    "timeout": {"success": True, "new_count": 1, "timed_out": True},
                },
            },
        },
        now,
    )

    assert summary["ok"] is False
    assert summary["age_hours"] == 1.0
    assert summary["total"] == 4
    assert summary["failures"] == ["bad"]
    assert summary["silent_failures"] == ["silent"]
    assert summary["timed_out"] == ["timeout"]


def test_safe_deploy_allows_only_safe_candidate_paths():
    policy = agent_safe_deploy.load_policy()
    result = agent_safe_deploy.evaluate(
        [
            "tests/test_agent_automation.py",
        ],
        policy,
    )

    assert result["allowed"] is True
    assert result["requires_restart"] is False
    assert result["classifications"]["proposal_only"] == []
    assert result["classifications"]["always_human"] == []
    assert result["classifications"]["unknown"] == []


def test_safe_deploy_blocks_pipeline_state_unknown_and_control_plane_paths():
    policy = agent_safe_deploy.load_policy()
    result = agent_safe_deploy.evaluate(
        [
            "publish.py",
            "state/coverage.db",
            "random/new_file.py",
            "AGENTS.md",
            "ops/autonomy-policy.json",
            "scripts/agent_safe_deploy.py",
        ],
        policy,
    )

    assert result["allowed"] is False
    assert [x["path"] for x in result["classifications"]["proposal_only"]] == [
        "AGENTS.md",
        "ops/autonomy-policy.json",
        "publish.py",
        "scripts/agent_safe_deploy.py",
    ]
    assert [x["path"] for x in result["classifications"]["always_human"]] == ["state/coverage.db"]
    assert [x["path"] for x in result["classifications"]["unknown"]] == ["random/new_file.py"]


def test_hook_blocks_obvious_destructive_commands():
    hook = REPO / ".codex" / "hooks" / "pre_tool_use_policy.py"
    proc = subprocess.run(
        [sys.executable, str(hook)],
        input=json.dumps({"tool_input": {"command": "git reset --hard HEAD"}}),
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 2
    assert "destructive git reset" in proc.stderr


def test_hook_allows_unknown_or_safe_commands():
    hook = REPO / ".codex" / "hooks" / "pre_tool_use_policy.py"
    proc = subprocess.run(
        [sys.executable, str(hook)],
        input=json.dumps({"tool_input": {"command": "git status --short"}}),
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0
    assert proc.stderr == ""


def test_agent_record_formats_log_entry():
    entry = agent_record.format_entry(
        timestamp="2026-06-08 12:00 UTC",
        situation="Assessment green",
        action="Added automation policy",
        evidence="pytest tests/test_agent_automation.py passed",
        outcome="Ready for review",
    )

    assert "## 2026-06-08 12:00 UTC" in entry
    assert "Situation report:\n- Assessment green" in entry
    assert "Action:\n- Added automation policy" in entry
    assert "Evidence:\n- pytest tests/test_agent_automation.py passed" in entry
    assert "Outcome:\n- Ready for review" in entry


def test_agent_record_requires_existing_private_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCASELAW_PRIVATE_ROOT", str(tmp_path / "missing"))

    with pytest.raises(FileNotFoundError, match="private maintenance workspace"):
        agent_record.resolve_log_path(None)


def test_agent_record_rejects_path_outside_private_workspace(tmp_path, monkeypatch):
    private_root = tmp_path / "private"
    private_root.mkdir()
    monkeypatch.setenv("OPENCASELAW_PRIVATE_ROOT", str(private_root))

    with pytest.raises(ValueError, match="inside the private workspace"):
        agent_record.resolve_log_path(str(REPO / "docs" / "maintenance-log.md"))


def test_private_root_discovery_uses_primary_checkout_for_worktrees(monkeypatch):
    monkeypatch.delenv("OPENCASELAW_PRIVATE_ROOT", raising=False)
    common_dir = subprocess.run(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    )
    expected = Path(common_dir.stdout.strip()).resolve().parent.parent / "opencaselaw-internal"

    assert agent_record.default_private_root() == expected
    assert agent_assess.private_root(REPO) == expected
