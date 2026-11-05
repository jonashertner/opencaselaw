#!/usr/bin/env python3
"""Deployment changelog for the hosted MCP server.

Two jobs, stdlib only (the VPS runs it with system python3):

``record``  Called by scripts/rolling_restart_workers.sh after a restart.
            Appends one JSON line to logs/deployments.jsonl (gitignored, so
            the VPS checkout stays clean for `merge --ff-only` and the
            nightly `pull --rebase`). Never exits non-zero and never raises:
            a logging failure must not fail a restart.

``render``  Folds logs/deployments.jsonl into docs/deployments.json and
            rewrites docs/deployments.md from the JSON. Run by hand (or a
            later bot step) where the result is committed; never on a
            dirty production tree.

What a record holds: the code commit the workers were started from, the UTC
window of the restart, which ports came back healthy, and the search models.
Models come from each worker's /health when it reports a build identity
(the ``commit``/``models``/``tools_hash`` fields added on branch
claude/musing-leakey-4732db, bcdc29f5); until that lands they are read
statically from mcp_server.py at the deployed commit, which gives the code
defaults, not environment overrides.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_LOG = Path(os.environ.get("OCL_DEPLOY_LOG_PATH") or REPO / "logs" / "deployments.jsonl")
DEFAULT_JSON = REPO / "docs" / "deployments.json"
DEFAULT_MD = REPO / "docs" / "deployments.md"
SCHEMA_VERSION = 1

# Code defaults in mcp_server.py. Keys match the /health "models" block of
# bcdc29f5 (query_llm, cross_encoder, embedding) plus two more search models.
MODEL_PATTERNS = {
    "query_llm": r'HAIKU_MODEL\s*=\s*os\.environ\.get\(\s*"OCL_HAIKU_MODEL",\s*"([^"]+)"',
    "cross_encoder": (
        r'CROSS_ENCODER_MODEL\s*=\s*os\.environ\.get\(\s*'
        r'"SWISS_CASELAW_CROSS_ENCODER_MODEL",\s*"([^"]+)"'
    ),
    "embedding": r'(?:VECTOR_MODEL_ID\s*=|model_id\s*=)\s*"([^"]+)"',
    "pinpoint_semantic": (
        r'PINPOINT_SEMANTIC_MODEL\s*=\s*os\.environ\.get\(\s*'
        r'"PINPOINT_SEMANTIC_MODEL",\s*"([^"]+)"'
    ),
}
# Identity fields /health may carry (bcdc29f5); copied when present.
HEALTH_IDENTITY_KEYS = ("commit", "data_snapshot", "models", "search_steps",
                        "model_state", "tools_hash", "tool_count")


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _git(repo: Path, *args: str, timeout: float = 10) -> str | None:
    try:
        out = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                             text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def models_from_source(src: str) -> dict:
    """Search-model ids as written in mcp_server.py (code defaults)."""
    out = {}
    for key, pat in MODEL_PATTERNS.items():
        m = re.search(pat, src)
        out[key] = m.group(1) if m else None
    return out


def code_commit(repo: Path) -> tuple[str | None, str | None]:
    """(HEAD, newest commit that touches anything outside docs/).

    On the VPS, HEAD is usually the nightly stats/feeds commit (docs/ only),
    so HEAD alone changes every day without a code change. The code commit is
    the identity that matters for "which build answered".
    """
    head = _git(repo, "rev-parse", "HEAD")
    code = _git(repo, "log", "-1", "--format=%H", "HEAD", "--", ".", ":(exclude)docs")
    return head, code or head


def landed_on_disk(repo: Path, commit: str, limit: int = 300) -> str | None:
    """UTC time the checkout first held ``commit`` (walks the HEAD reflog)."""
    out = _git(repo, "reflog", "--date=iso-strict", f"-n{limit}", "--format=%H %gd")
    if not out:
        return None
    first = None
    for line in out.splitlines():
        sha, _, ref = line.partition(" ")
        m = re.search(r"\{(.+)\}", ref)
        if not m:
            continue
        if sha == commit or _git(repo, "merge-base", "--is-ancestor", commit, sha) is not None:
            first = m.group(1)
        else:
            break
    if not first:
        return None
    try:
        return datetime.fromisoformat(first).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None


def fetch_health(port: str, timeout: float = 3.0) -> dict | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:  # noqa: BLE001 - informative only
        return None


def build_record(*, started: str, finished: str, ok_ports: list[str], failed_ports: list[str],
                 cause: str, head: str | None, commit: str | None, source: str | None,
                 landed: str | None, health: dict[str, dict | None]) -> dict:
    """One restart as a JSON-serialisable record (pure; no I/O)."""
    identities = {}
    for port, body in health.items():
        if isinstance(body, dict):
            ident = {k: body[k] for k in HEALTH_IDENTITY_KEYS if k in body}
            if ident:
                identities[port] = ident
    rec = {
        "schema_version": SCHEMA_VERSION,
        "kind": "rolling_restart",
        "cause": cause,
        "commit": commit[:8] if commit else None,
        "commit_full": commit,
        "head": head[:8] if head else None,
        "landed_on_disk_utc": landed,
        "restart_started_utc": started,
        "restart_finished_utc": finished,
        "ports_ok": sorted(ok_ports),
        "ports_failed": sorted(failed_ports),
        "complete": not failed_ports and bool(ok_ports),
        "models": models_from_source(source) if source else None,
        "models_source": "mcp_server.py code defaults (environment overrides not read)",
    }
    if identities:
        first = identities[sorted(identities)[0]]
        if isinstance(first.get("models"), dict):
            rec["models"] = first["models"]
            rec["models_source"] = "worker /health"
        for k in ("tools_hash", "tool_count", "search_steps", "model_state", "data_snapshot"):
            if k in first:
                rec[k] = first[k]
        commits = sorted({str(v.get("commit")) for v in identities.values() if v.get("commit")})
        if commits:
            rec["worker_commits"] = commits
    return rec


def append_record(rec: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")


def _split_ports(s: str | None) -> list[str]:
    return [p for p in re.split(r"[,\s]+", s or "") if p]


def cmd_record(args: argparse.Namespace) -> int:
    try:
        repo = Path(args.repo)
        head, commit = code_commit(repo)
        source = _git(repo, "show", f"{commit}:mcp_server.py") if commit else None
        if source is None and (repo / "mcp_server.py").is_file():
            source = (repo / "mcp_server.py").read_text(encoding="utf-8", errors="replace")
        ok, failed = _split_ports(args.ok), _split_ports(args.failed)
        health = {} if args.no_health else {p: fetch_health(p) for p in ok}
        rec = build_record(
            started=args.started or utc_now(), finished=args.finished or utc_now(),
            ok_ports=ok, failed_ports=failed, cause=args.cause, head=head, commit=commit,
            source=source, landed=landed_on_disk(repo, commit) if commit else None,
            health=health,
        )
        append_record(rec, Path(args.log))
        print(f"record_deployment: {rec['commit']} {len(ok)} ok / {len(failed)} failed -> {args.log}")
    except Exception as e:  # noqa: BLE001 - never fail the restart over the log
        print(f"record_deployment: WARNING could not write the deployment log: {e}", file=sys.stderr)
    return 0


# ── render ────────────────────────────────────────────────────────

def read_records(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict) and rec.get("commit"):
            out.append(rec)
    return out


def merge_records(doc: dict, records: list[dict]) -> dict:
    """Add logged restarts to ``doc``: each to ``restart_log`` (deduplicated)
    and, when it brought a new commit, as a version entry."""
    log = doc.setdefault("restart_log", [])
    seen = {(r.get("restart_started_utc"), r.get("commit")) for r in log}
    entries = doc.setdefault("entries", [])
    for rec in sorted(records, key=lambda r: r.get("restart_started_utc") or ""):
        key = (rec.get("restart_started_utc"), rec.get("commit"))
        if key in seen:
            continue
        seen.add(key)
        log.append(rec)
        prev = entries[-1] if entries else None
        if prev and prev.get("commit") == rec["commit"]:
            if rec.get("complete") and not prev.get("live_complete_utc"):
                prev["live_complete_utc"] = rec.get("restart_finished_utc")
            continue
        if prev and not prev.get("retired_utc") and rec.get("complete"):
            prev["retired_utc"] = rec.get("restart_finished_utc")
        notes = []
        if not rec.get("complete"):
            notes.append("restart incomplete: ports " + ", ".join(rec.get("ports_failed") or []) +
                         " did not come back healthy")
        entries.append({
            "commit": rec["commit"],
            "commit_full": rec.get("commit_full"),
            "commit_range": f"{prev['commit']}..{rec['commit']}" if prev else None,
            "landed_on_disk_utc": rec.get("landed_on_disk_utc"),
            "landed_via": None,
            "live_from_utc": rec.get("restart_started_utc"),
            "live_from_cause": rec.get("cause"),
            "live_complete_utc": rec.get("restart_finished_utc") if rec.get("complete") else None,
            "live_complete_cause": rec.get("cause") if rec.get("complete") else None,
            "retired_utc": None,
            "precision": ["exact"],
            "serving_code_changed": None,
            "commits": [],
            "models": rec.get("models"),
            "tools_hash": rec.get("tools_hash"),
            "notes": notes,
            "source": "restart log",
        })
    return doc


def _short(ts: str | None) -> str:
    return ts.replace("T", " ").replace("Z", "")[:16] if ts else "—"


def _md_escape(s: str) -> str:
    return s.replace("|", "\\|").replace("\n", " ")


def _model_cell(models: dict | None, prev: dict | None) -> str:
    if not models:
        return "—"
    if prev and models.get("query_llm") == prev.get("query_llm"):
        return models.get("query_llm") or "—"
    return f"**{models.get('query_llm')}**"


def render_markdown(doc: dict) -> str:
    out: list[str] = []
    w = out.append
    w("# Hosted MCP server: deployment changelog")
    w("")
    w(doc.get("intro", "").strip())
    w("")
    w("Machine-readable: [`deployments.json`](deployments.json).")
    w("")
    w("## How to read it")
    w("")
    for line in doc.get("definitions_md", []):
        w(f"- {line}")
    w("")
    w("## Search models")
    w("")
    w("| Step | Model | Status, 2026-09-25 to 2026-11-04 |")
    w("|---|---|---|")
    for m in doc.get("search_models", []):
        w(f"| {_md_escape(m['step'])} | `{m['model']}` | {_md_escape(m['status'])} |")
    w("")
    for c in doc.get("model_changes", []):
        w(f"**Model change:** {_md_escape(c['summary'])}")
        w("")
    w("## Versions served")
    w("")
    w("One row per code commit that workers were started from. *First worker* is when the first of the "
      "8 workers began serving it; *all 8* is when every worker served it or a later build. Times UTC.")
    w("")
    w("| First worker | All 8 | Commit | Range | On disk | Serving code | Query LLM | Evidence | Changes |")
    w("|---|---|---|---|---|---|---|---|---|")
    prev_models = None
    for e in doc.get("entries", []):
        subjects = [c["subject"] for c in e.get("commits", []) if c.get("serving")]
        others = len(e.get("commits", [])) - len(subjects)
        change = "; ".join(_md_escape(s) for s in subjects[:4])
        if len(subjects) > 4:
            change += f"; +{len(subjects) - 4} more"
        if others > 0:
            change += f"{' ' if change else ''}(+{others} non-serving)"
        if e.get("baseline"):
            change = "baseline: served when the window opens"
        sc = e.get("serving_code_changed")
        sc_cell = "yes" if sc else ("no" if sc is False else "?")
        w(f"| {_short(e.get('live_from_utc'))} | {_short(e.get('live_complete_utc'))} | "
          f"`{e['commit']}` | {('`' + e['commit_range'] + '`') if e.get('commit_range') else '—'} | "
          f"{_short(e.get('landed_on_disk_utc'))} | {sc_cell} | "
          f"{_model_cell(e.get('models'), prev_models)} | {_md_escape(', '.join(e.get('precision') or []))} | "
          f"{change or '—'} |")
        prev_models = e.get("models")
    w("")
    w("## Mixed-fleet windows")
    w("")
    w("Periods longer than five minutes in which workers served different builds. A client pinned to one "
      "worker saw one build; a run spread over several workers could see both.")
    w("")
    w("| From | To | Builds served (workers) | Cause |")
    w("|---|---|---|---|")
    for f in doc.get("fleet_states", []):
        if len(f.get("serving", {})) < 2:
            continue
        a, b = f.get("from_utc"), f.get("to_utc")
        if a and b:
            mins = (datetime.fromisoformat(b.replace("Z", "+00:00"))
                    - datetime.fromisoformat(a.replace("Z", "+00:00"))).total_seconds() / 60
            if mins <= 5:
                continue
        serving = ", ".join(f"`{k}` ({v})" for k, v in f["serving"].items())
        w(f"| {_short(a)} | {_short(b)} | {serving} | {_md_escape(f.get('cause', ''))} |")
    w("")
    w("## Windows that cannot be dated exactly")
    w("")
    for g in doc.get("undated_windows", []):
        w(f"- {_md_escape(g)}")
    w("")
    w("## Sources and method")
    w("")
    for line in doc.get("method_md", []):
        w(f"- {line}")
    w("")
    return "\n".join(out)


def cmd_render(args: argparse.Namespace) -> int:
    jpath, mpath = Path(args.json), Path(args.md)
    doc = json.loads(jpath.read_text(encoding="utf-8"))
    doc = merge_records(doc, read_records(Path(args.log)))
    jpath.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    mpath.write_text(render_markdown(doc), encoding="utf-8")
    print(f"render: {len(doc.get('entries', []))} entries -> {jpath}, {mpath}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("record", help="append one restart to the deployment log")
    r.add_argument("--started", help="UTC ISO time the restart began (default: now)")
    r.add_argument("--finished", help="UTC ISO time it ended (default: now)")
    r.add_argument("--ok", default="", help="ports that came back healthy (comma/space separated)")
    r.add_argument("--failed", default="", help="ports that did not")
    r.add_argument("--cause", default="rolling_restart_workers.sh")
    r.add_argument("--repo", default=str(REPO))
    r.add_argument("--log", default=str(DEFAULT_LOG))
    r.add_argument("--no-health", action="store_true", help="skip the /health identity probe")
    r.set_defaults(func=cmd_record)
    d = sub.add_parser("render", help="fold the log into docs/deployments.{json,md}")
    d.add_argument("--log", default=str(DEFAULT_LOG))
    d.add_argument("--json", default=str(DEFAULT_JSON))
    d.add_argument("--md", default=str(DEFAULT_MD))
    d.set_defaults(func=cmd_render)
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        print(f"record_deployment: WARNING {e}", file=sys.stderr)
        sys.exit(0 if len(sys.argv) > 1 and sys.argv[1] == "record" else 1)
