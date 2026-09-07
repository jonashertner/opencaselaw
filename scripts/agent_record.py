#!/usr/bin/env python3
"""Append an evidence record to an existing private maintenance workspace."""
from __future__ import annotations

import argparse
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def default_private_root(repo: Path = REPO) -> Path:
    """Find the private sibling beside the primary checkout, not a worktree."""
    proc = subprocess.run(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode == 0 and proc.stdout.strip():
        common_dir = Path(proc.stdout.strip()).resolve()
        if common_dir.name == ".git":
            return common_dir.parent.parent / "opencaselaw-internal"
    return repo.resolve().parent / "opencaselaw-internal"


def private_workspace_root() -> Path:
    configured = os.environ.get("OPENCASELAW_PRIVATE_ROOT")
    root = (
        Path(configured).expanduser().resolve()
        if configured
        else default_private_root().resolve()
    )
    if not root.is_dir():
        raise FileNotFoundError(
            "private maintenance workspace is unavailable; set "
            "OPENCASELAW_PRIVATE_ROOT to an existing owner-only directory"
        )
    return root


def resolve_log_path(log_path: str | None) -> Path:
    """Resolve a log path contained by the configured private workspace."""
    private_root = private_workspace_root()
    resolved = (
        Path(log_path).expanduser().resolve()
        if log_path
        else private_root / "docs" / "agent-loop" / "LOG.md"
    )
    if resolved != private_root and private_root not in resolved.parents:
        raise ValueError("maintenance records must stay inside the private workspace")
    return resolved


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _section(title: str, body: str | None) -> str:
    lines = [f"{title}:"]
    if not body:
        lines.append("- Not provided.")
        return "\n".join(lines)
    for line in body.splitlines():
        stripped = line.strip()
        if stripped:
            lines.append(f"- {stripped}")
    if len(lines) == 1:
        lines.append("- Not provided.")
    return "\n".join(lines)


def format_entry(*, timestamp: str, situation: str | None, action: str, evidence: str, outcome: str) -> str:
    return (
        f"\n## {timestamp}\n\n"
        f"{_section('Situation report', situation)}\n\n"
        f"{_section('Action', action)}\n\n"
        f"{_section('Evidence', evidence)}\n\n"
        f"{_section('Outcome', outcome)}\n"
    )


def append_entry(
    *,
    log_path: Path,
    situation: str | None,
    action: str,
    evidence: str,
    outcome: str,
    timestamp: str | None = None,
) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if not log_path.exists():
        log_path.write_text("# OpenCaseLaw Agent Loop Log\n", encoding="utf-8")
    entry = format_entry(
        timestamp=timestamp or _timestamp(),
        situation=situation,
        action=action,
        evidence=evidence,
        outcome=outcome,
    )
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(entry)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--log",
        help="log path inside OPENCASELAW_PRIVATE_ROOT (defaults to its maintenance log)",
    )
    parser.add_argument("--timestamp", help="override UTC timestamp")
    parser.add_argument("--situation", help="situation report text")
    parser.add_argument("--action", required=True, help="action taken")
    parser.add_argument("--evidence", required=True, help="verification evidence")
    parser.add_argument("--outcome", required=True, help="outcome")
    args = parser.parse_args()

    try:
        log_path = resolve_log_path(args.log)
    except (FileNotFoundError, ValueError) as exc:
        parser.error(str(exc))

    append_entry(
        log_path=log_path,
        timestamp=args.timestamp,
        situation=args.situation,
        action=args.action,
        evidence=args.evidence,
        outcome=args.outcome,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
