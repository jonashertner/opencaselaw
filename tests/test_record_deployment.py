"""Deployment changelog: the restart-log helper and the published files.

scripts/record_deployment.py appends one record per rolling restart and
renders docs/deployments.{json,md}. All offline: git runs on temp repos (or
read-only on this checkout), /health probes hit nothing, systemctl and curl
are shell stubs.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "rolling_restart_workers.sh"
HELPER = REPO / "scripts" / "record_deployment.py"
DOC_JSON = REPO / "docs" / "deployments.json"
DOC_MD = REPO / "docs" / "deployments.md"


def _load():
    spec = importlib.util.spec_from_file_location("record_deployment", HELPER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rd = _load()

SOURCE_55 = '''
HAIKU_MODEL = os.environ.get("OCL_HAIKU_MODEL", "claude-haiku-5-5")
CROSS_ENCODER_MODEL = os.environ.get(
    "SWISS_CASELAW_CROSS_ENCODER_MODEL",
    "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1",
)
PINPOINT_SEMANTIC_MODEL = os.environ.get(
    "PINPOINT_SEMANTIC_MODEL",
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
)
def _get_vector_model():
    model_id = "BAAI/bge-m3"
'''


def _git(cwd: Path, *args: str) -> str:
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.org",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.org")
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True,
                          text=True, env=env).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    (r / "mcp_server.py").write_text(SOURCE_55)
    _git(r, "add", ".")
    _git(r, "commit", "-q", "-m", "code")
    (r / "docs").mkdir()
    (r / "docs" / "stats.json").write_text("{}")
    _git(r, "add", ".")
    _git(r, "commit", "-q", "-m", "Update stats.json + feeds")
    return r


# ── model parsing ─────────────────────────────────────────────────

def test_models_from_source_reads_code_defaults():
    m = rd.models_from_source(SOURCE_55)
    assert m == {
        "query_llm": "claude-haiku-5-5",
        "cross_encoder": "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1",
        "embedding": "BAAI/bge-m3",
        "pinpoint_semantic": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
    }


def test_models_from_source_matches_the_live_server_module():
    m = rd.models_from_source((REPO / "mcp_server.py").read_text(encoding="utf-8"))
    assert m["query_llm"] and m["query_llm"].startswith("claude-haiku-")
    assert m["cross_encoder"] and m["pinpoint_semantic"] and m["embedding"]


def test_models_from_source_missing_constant_is_none():
    assert rd.models_from_source("x = 1")["query_llm"] is None


# ── build_record ──────────────────────────────────────────────────

def _record(**kw):
    base = dict(started="2026-11-05T10:00:00Z", finished="2026-11-05T10:00:30Z",
                ok_ports=["8771", "8770"], failed_ports=[], cause="test", head="a" * 40,
                commit="b" * 40, source=SOURCE_55, landed="2026-11-05T09:59:00Z", health={})
    base.update(kw)
    return rd.build_record(**base)


def test_record_from_source_when_health_has_no_identity():
    rec = _record(health={"8770": {"status": "ok", "decisions": 5}, "8771": None})
    assert rec["commit"] == "b" * 8 and rec["head"] == "a" * 8
    assert rec["ports_ok"] == ["8770", "8771"] and rec["complete"] is True
    assert rec["models"]["query_llm"] == "claude-haiku-5-5"
    assert rec["models_source"].startswith("mcp_server.py")
    assert "tools_hash" not in rec


def test_record_prefers_worker_health_identity():
    ident = {"status": "ok", "commit": "bbbbbbbb", "tools_hash": "f" * 64, "tool_count": 58,
             "models": {"query_llm": "claude-haiku-4-5-20251001", "cross_encoder": "x",
                        "embedding": "y"},
             "search_steps": {"cross_encoder": False},
             "model_state": {"cross_encoder": "missing", "embedding": "off"}}
    rec = _record(health={"8770": ident, "8771": dict(ident, commit="cccccccc")})
    assert rec["models_source"] == "worker /health"
    assert rec["models"]["query_llm"] == "claude-haiku-4-5-20251001"
    assert rec["tools_hash"] == "f" * 64 and rec["tool_count"] == 58
    assert rec["worker_commits"] == ["bbbbbbbb", "cccccccc"]
    assert rec["model_state"] == {"cross_encoder": "missing", "embedding": "off"}


def test_record_with_a_failed_port_is_incomplete():
    rec = _record(ok_ports=["8770"], failed_ports=["8771"])
    assert rec["complete"] is False and rec["ports_failed"] == ["8771"]
    assert _record(ok_ports=[], failed_ports=[])["complete"] is False


# ── git helpers on a temp repo ────────────────────────────────────

def test_code_commit_skips_docs_only_head(repo: Path):
    head, code = rd.code_commit(repo)
    assert head == _git(repo, "rev-parse", "HEAD")
    assert code == _git(repo, "rev-parse", "HEAD~1")


def test_landed_on_disk_reads_the_reflog(repo: Path):
    code = _git(repo, "rev-parse", "HEAD~1")
    landed = rd.landed_on_disk(repo, code)
    assert landed and re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", landed)


def test_cmd_record_appends_one_line(repo: Path, tmp_path: Path):
    log = tmp_path / "logs" / "deployments.jsonl"
    for _ in range(2):
        assert rd.main(["record", "--repo", str(repo), "--log", str(log), "--no-health",
                        "--ok", "8770 8771", "--failed", "", "--cause", "test"]) == 0
    lines = log.read_text().splitlines()
    assert len(lines) == 2
    rec = json.loads(lines[0])
    assert rec["commit_full"] == _git(repo, "rev-parse", "HEAD~1")
    assert rec["models"]["query_llm"] == "claude-haiku-5-5"
    assert rec["cause"] == "test" and rec["complete"] is True


def test_cmd_record_never_fails(tmp_path: Path, capsys):
    blocker = tmp_path / "file"
    blocker.write_text("")
    # log path under a regular file cannot be created; repo does not exist
    rc = rd.main(["record", "--repo", str(tmp_path / "nope"), "--log", str(blocker / "x.jsonl"),
                  "--no-health", "--ok", "8770"])
    assert rc == 0
    assert "WARNING" in capsys.readouterr().err


# ── merge + render ────────────────────────────────────────────────

def _doc():
    return {"entries": [{"commit": "aaaaaaaa", "live_from_utc": "2026-11-04T14:27:00Z",
                         "live_complete_utc": "2026-11-04T14:27:30Z", "commits": [],
                         "models": {"query_llm": "claude-haiku-5-5"}, "precision": ["exact"]}]}


def test_merge_records_adds_new_commit_once():
    rec = _record(commit="c" * 40)
    doc = rd.merge_records(_doc(), [rec, dict(rec)])
    assert [e["commit"] for e in doc["entries"]] == ["aaaaaaaa", "cccccccc"]
    new = doc["entries"][-1]
    assert new["commit_range"] == "aaaaaaaa..cccccccc"
    assert new["live_from_utc"] == "2026-11-05T10:00:00Z"
    assert new["live_complete_utc"] == "2026-11-05T10:00:30Z"
    assert doc["entries"][0]["retired_utc"] == "2026-11-05T10:00:30Z"
    assert len(doc["restart_log"]) == 1


def test_merge_records_same_commit_is_a_restart_not_a_version():
    rec = _record(commit="a" * 8 + "0" * 32)
    doc = rd.merge_records(_doc(), [rec])
    assert len(doc["entries"]) == 1 and len(doc["restart_log"]) == 1


def test_merge_records_incomplete_restart_has_no_complete_time():
    rec = _record(commit="d" * 40, ok_ports=["8770"], failed_ports=["8771"])
    new = rd.merge_records(_doc(), [rec])["entries"][-1]
    assert new["live_complete_utc"] is None and "8771" in new["notes"][0]


def test_read_records_skips_garbage(tmp_path: Path):
    p = tmp_path / "d.jsonl"
    p.write_text('{"commit": "aaaaaaaa"}\nnot json\n\n{"no": "commit"}\n')
    assert [r["commit"] for r in rd.read_records(p)] == ["aaaaaaaa"]
    assert rd.read_records(tmp_path / "missing.jsonl") == []


# ── the published files ───────────────────────────────────────────

def test_published_markdown_is_rendered_from_the_json():
    doc = json.loads(DOC_JSON.read_text(encoding="utf-8"))
    assert rd.render_markdown(doc) == DOC_MD.read_text(encoding="utf-8")


def test_published_json_is_consistent():
    doc = json.loads(DOC_JSON.read_text(encoding="utf-8"))
    entries = doc["entries"]
    assert len(entries) >= 50
    lives = [e["live_from_utc"] for e in entries]
    assert lives == sorted(lives)
    for e in entries:
        assert re.fullmatch(r"[0-9a-f]{8}", e["commit"])
        assert e["landed_on_disk_utc"] <= e["live_from_utc"]
        if e.get("live_complete_utc"):
            assert e["live_from_utc"] <= e["live_complete_utc"]
        for k in ("commit_range", "models", "notes", "live_complete_utc"):
            assert k in e
    by = {e["commit"]: e for e in entries}
    switch = by["2a77d717"]
    assert switch["models"]["query_llm"] == "claude-haiku-5-5"
    before = [e for e in entries if e["live_from_utc"] < switch["live_from_utc"]]
    assert before and all(e["models"]["query_llm"] == "claude-haiku-4-5-20251001" for e in before)
    after = [e for e in entries if e["live_from_utc"] >= switch["live_from_utc"]]
    assert all(e["models"]["query_llm"] == "claude-haiku-5-5" for e in after)
    assert doc["model_changes"][0]["commit"] == "2a77d717"


# ── the restart script calls the helper and survives its failure ─────

def _stub(path: Path, body: str) -> None:
    path.write_text("#!/usr/bin/env bash\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _run_script(tmp_path: Path, extra_env: dict, python_stub: str | None = None):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    _stub(bin_dir / "systemctl", """
if [ "$1" = list-units ]; then
  echo "mcp-server@59991.service loaded active running x"
  echo "mcp-server@59992.service loaded active running x"
fi
exit 0
""")
    _stub(bin_dir / "curl", 'case "$*" in *59991*) exit 0;; *) exit 7;; esac\n')
    if python_stub is not None:
        _stub(bin_dir / "python3", python_stub)
    else:
        _stub(bin_dir / "python3", f'exec "{sys.executable}" "$@"\n')
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}", HEALTH_TRIES="1", **extra_env)
    return subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True, env=env,
                          timeout=120)


def test_restart_script_appends_a_record(tmp_path: Path):
    log = tmp_path / "deployments.jsonl"
    out = _run_script(tmp_path, {"OCL_DEPLOY_LOG_PATH": str(log), "DEPLOY_CAUSE": "pytest"})
    assert out.returncode == 0, out.stderr
    rec = json.loads(log.read_text().splitlines()[-1])
    assert rec["ports_ok"] == ["59991"] and rec["ports_failed"] == ["59992"]
    assert rec["cause"] == "pytest" and rec["complete"] is False
    assert re.fullmatch(r"[0-9a-f]{8}", rec["commit"])
    assert rec["restart_started_utc"] <= rec["restart_finished_utc"]


def test_restart_script_survives_a_failing_logger(tmp_path: Path):
    out = _run_script(tmp_path, {}, python_stub="exit 3\n")
    assert out.returncode == 0
    assert "deployment log not written" in out.stderr


def test_restart_script_logging_can_be_switched_off(tmp_path: Path):
    log = tmp_path / "deployments.jsonl"
    out = _run_script(tmp_path, {"OCL_DEPLOY_LOG": "0", "OCL_DEPLOY_LOG_PATH": str(log)})
    assert out.returncode == 0 and not log.exists()
