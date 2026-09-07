#!/usr/bin/env python3
"""Machine-readable OpenCaseLaw maintenance assessment.

This script is deliberately read-only. It gathers local repo state, optional
public endpoint health, and local log/state summaries into one JSON payload for
Codex or another runner to reason over. It does not read secrets and it does
not write under state/.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
HEALTH_URL = "https://mcp.opencaselaw.ch/health"
QUALITY_URL = "https://opencaselaw.ch/quality.json"


def default_private_root(repo: Path) -> Path:
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


def private_root(repo: Path) -> Path:
    configured = os.environ.get("OPENCASELAW_PRIVATE_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    return default_private_root(repo)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def age_hours(value: Any, now: datetime) -> float | None:
    parsed = parse_dt(value)
    if parsed is None:
        return None
    return round((now - parsed).total_seconds() / 3600.0, 2)


def run_cmd(args: list[str], *, cwd: Path = REPO, timeout: float = 10.0) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            args,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001 - diagnostic surface
        return {
            "ok": False,
            "returncode": None,
            "stdout": "",
            "stderr": f"{type(exc).__name__}: {exc}",
        }
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": proc.stdout.rstrip("\n"),
        "stderr": proc.stderr.rstrip("\n"),
    }


def parse_git_status(stdout: str) -> tuple[list[str], int]:
    changed_files: list[str] = []
    untracked_count = 0
    for line in stdout.splitlines():
        if not line:
            continue
        if line.startswith("?? "):
            untracked_count += 1
            changed_files.append(line[3:])
        else:
            changed_files.append(line[3:].strip())
    return sorted(changed_files), untracked_count


def fetch_json(url: str, *, timeout: float = 10.0) -> dict[str, Any]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "opencaselaw-agent-assess/1"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
        return {"available": True, "ok": True, "url": url, "payload": payload}
    except Exception as exc:  # noqa: BLE001 - best effort
        return {
            "available": False,
            "ok": False,
            "url": url,
            "error": f"{type(exc).__name__}: {exc}",
        }


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"available": False, "path": str(path)}
    try:
        return {"available": True, "path": str(path), "payload": json.loads(path.read_text())}
    except Exception as exc:  # noqa: BLE001 - diagnostic surface
        return {
            "available": False,
            "path": str(path),
            "error": f"{type(exc).__name__}: {exc}",
        }


def repo_summary(repo: Path) -> dict[str, Any]:
    branch = run_cmd(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo)
    head = run_cmd(["git", "rev-parse", "--short", "HEAD"], cwd=repo)
    status = run_cmd(["git", "status", "--porcelain"], cwd=repo)

    changed_files: list[str] = []
    untracked_count = 0
    if status["ok"]:
        changed_files, untracked_count = parse_git_status(status["stdout"])

    return {
        "root": str(repo),
        "branch": branch["stdout"] if branch["ok"] else "unknown",
        "head": head["stdout"] if head["ok"] else "unknown",
        "dirty": bool(changed_files),
        "changed_files": changed_files,
        "untracked_count": untracked_count,
    }


def summarize_health(fetch_result: dict[str, Any]) -> dict[str, Any]:
    if not fetch_result.get("available"):
        return fetch_result
    payload = fetch_result.get("payload") or {}
    decisions = payload.get("decisions")
    ok = payload.get("status") == "ok" and isinstance(decisions, int) and decisions > 950_000
    return {
        "available": True,
        "ok": ok,
        "status": payload.get("status"),
        "decisions": decisions,
        "db_generation": payload.get("db_generation"),
    }


def _int_field(payload: dict[str, Any], *names: str) -> int | None:
    for name in names:
        value: Any = payload
        for part in name.split("."):
            if not isinstance(value, dict) or part not in value:
                value = None
                break
            value = value[part]
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def summarize_quality(fetch_result: dict[str, Any], now: datetime) -> dict[str, Any]:
    if not fetch_result.get("available"):
        return fetch_result
    payload = fetch_result.get("payload") or {}
    run_at = payload.get("run_at") or payload.get("generated_at") or payload.get("summary", {}).get("run_at")
    critical = _int_field(payload, "critical_failures", "summary.critical_failures")
    quarantine = _int_field(payload, "quarantine_failures", "summary.quarantine_failures")
    warnings = _int_field(payload, "warning_failures", "summary.warning_failures")
    publish_safe = payload.get("publish_safe")
    if publish_safe is None and isinstance(payload.get("summary"), dict):
        publish_safe = payload["summary"].get("publish_safe")
    passed = _int_field(payload, "passed", "summary.passed")
    total = _int_field(payload, "total", "summary.total")
    age = age_hours(run_at, now)
    ok = (critical in (0, None)) and publish_safe is not False
    return {
        "available": True,
        "ok": ok,
        "run_at": run_at,
        "age_hours": age,
        "critical_failures": critical,
        "quarantine_failures": quarantine,
        "warning_failures": warnings,
        "publish_safe": publish_safe,
        "passed": passed,
        "total": total,
    }


def _scraper_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw = (
        payload.get("scrapers")
        or payload.get("results")
        or payload.get("courts")
        or payload.get("health")
        or []
    )
    if isinstance(raw, dict):
        items = []
        for key, value in raw.items():
            if isinstance(value, dict):
                row = dict(value)
                row.setdefault("source", key)
                row.setdefault("court", key)
                items.append(row)
        return items
    if isinstance(raw, list):
        return [dict(row) for row in raw if isinstance(row, dict)]
    return []


def summarize_scraper_health(file_result: dict[str, Any], now: datetime) -> dict[str, Any]:
    if not file_result.get("available"):
        return file_result
    payload = file_result.get("payload") or {}
    items = _scraper_items(payload)
    failures: list[str] = []
    silent: list[str] = []
    timed_out: list[str] = []
    for row in items:
        key = str(row.get("source") or row.get("court") or row.get("name") or "unknown")
        if row.get("success") is False:
            failures.append(key)
        try:
            discovery_errors = int(row.get("discovery_errors") or 0)
            new_count = int(row.get("new_count") or 0)
        except (TypeError, ValueError):
            discovery_errors = 0
            new_count = 0
        if discovery_errors >= 3 and new_count == 0:
            silent.append(key)
        if row.get("timed_out"):
            timed_out.append(key)

    run_at = payload.get("run_at") or payload.get("generated_at")
    return {
        "available": True,
        "ok": not failures and not silent,
        "run_at": run_at,
        "age_hours": age_hours(run_at, now),
        "total": len(items),
        "failures": sorted(failures),
        "silent_failures": sorted(set(silent)),
        "timed_out": sorted(timed_out),
    }


def agent_loop_log_summary(path: Path, now: datetime) -> dict[str, Any]:
    if not path.exists():
        return {"available": False, "path": str(path)}
    stat = path.stat()
    dt = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
    return {
        "available": True,
        "path": str(path),
        "modified_at": _iso(dt),
        "age_hours": round((now - dt).total_seconds() / 3600.0, 2),
        "bytes": stat.st_size,
    }


def open_proposals(private_workspace: Path) -> list[str]:
    proposal_dir = private_workspace / "docs" / "agent-loop" / "proposals"
    if not proposal_dir.exists():
        return []
    return sorted(str(path.relative_to(private_workspace)) for path in proposal_dir.glob("*.md"))


def build_risk(assessment: dict[str, Any]) -> dict[str, list[str]]:
    blocking: list[str] = []
    warnings: list[str] = []

    repo = assessment["repo"]
    if repo.get("dirty"):
        warnings.append(f"working tree has {len(repo.get('changed_files', []))} changed/untracked path(s)")

    health = assessment["public"].get("health", {})
    if health.get("available") and not health.get("ok"):
        blocking.append("public health endpoint is not OK or corpus count is below floor")
    elif not health.get("available"):
        warnings.append("public health endpoint unavailable in this assessment")

    quality = assessment["public"].get("quality", {})
    if quality.get("available"):
        if quality.get("critical_failures") not in (0, None):
            blocking.append(f"public quality has {quality.get('critical_failures')} critical failure(s)")
        if quality.get("publish_safe") is False:
            blocking.append("public quality publish_safe is false")
        if quality.get("age_hours") is not None and quality["age_hours"] > 36:
            warnings.append(f"public quality.json is stale at {quality['age_hours']}h")
    else:
        warnings.append("public quality.json unavailable in this assessment")

    scraper = assessment["local"].get("scraper_health", {})
    if scraper.get("available"):
        failures = scraper.get("failures") or []
        silent = scraper.get("silent_failures") or []
        if failures:
            blocking.append(f"scraper_health has {len(failures)} explicit failure(s)")
        if silent:
            blocking.append(f"scraper_health has {len(silent)} silent-failure pattern(s)")
        if scraper.get("age_hours") is not None and scraper["age_hours"] > 36:
            warnings.append(f"local scraper_health.json is stale at {scraper['age_hours']}h")
    else:
        warnings.append("local scraper_health.json not available")

    proposals = assessment["local"].get("open_proposals") or []
    if any("backup" in item for item in proposals):
        warnings.append("offsite backup proposal is open and needs owner decision")

    return {"blocking": blocking, "warnings": warnings}


def recommended_actions(assessment: dict[str, Any]) -> list[str]:
    risk = assessment["risk"]
    if risk["blocking"]:
        return [f"Investigate first blocking condition: {risk['blocking'][0]}"]
    proposals = assessment["local"].get("open_proposals") or []
    if any("backup" in item for item in proposals):
        return ["Resolve backup destination and secret handling proposal before deeper automation"]
    if assessment["repo"].get("dirty"):
        return ["Classify current diff with scripts/agent_safe_deploy.py before commit or deploy"]
    return ["Run a focused completeness or reliability probe, then record evidence"]


def assess(repo: Path, *, network: bool, timeout: float) -> dict[str, Any]:
    now = _now()
    private_workspace = private_root(repo)
    health = {"available": False, "ok": False, "skipped": True}
    quality = {"available": False, "ok": False, "skipped": True}
    if network:
        health = summarize_health(fetch_json(HEALTH_URL, timeout=timeout))
        quality = summarize_quality(fetch_json(QUALITY_URL, timeout=timeout), now)

    assessment = {
        "schema_version": 1,
        "generated_at": _iso(now),
        "repo": repo_summary(repo),
        "public": {
            "health": health,
            "quality": quality,
        },
        "local": {
            "scraper_health": summarize_scraper_health(read_json(repo / "logs" / "scraper_health.json"), now),
            "last_publish_success": read_json(repo / "state" / "last_publish_success.json"),
            "agent_loop_log": agent_loop_log_summary(
                private_workspace / "docs" / "agent-loop" / "LOG.md", now
            ),
            "open_proposals": open_proposals(private_workspace),
        },
        "risk": {"blocking": [], "warnings": []},
        "recommended_next_actions": [],
    }
    assessment["risk"] = build_risk(assessment)
    assessment["recommended_next_actions"] = recommended_actions(assessment)
    return assessment


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=str(REPO), help="repository root")
    parser.add_argument("--no-network", action="store_true", help="skip public endpoint probes")
    parser.add_argument("--timeout", type=float, default=10.0, help="network/command timeout seconds")
    parser.add_argument("--json", action="store_true", help="emit JSON (default; kept for runner clarity)")
    args = parser.parse_args(argv)

    payload = assess(Path(args.repo).resolve(), network=not args.no_network, timeout=args.timeout)
    json.dump(payload, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
