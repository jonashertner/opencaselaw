"""Build identity: serverInfo.version and the /health fields that tell a
client which code, data snapshot, models and tool surface answered.

Offline: a tmp decisions.db, fake .git directories, no network.
"""
from __future__ import annotations

import copy
import inspect
import sqlite3
import subprocess
from pathlib import Path

import pytest

import mcp_server as m

SHA_A = "a" * 40
SHA_B = "b" * 40
SHA_C = "c" * 40


# ── serverInfo.version ────────────────────────────────────────

def _no_git_binary(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("git")
    monkeypatch.setattr(subprocess, "run", boom)


def test_env_override_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("OCL_SERVER_VERSION", "  release-7  ")
    assert m._resolve_server_version(tmp_path) == "release-7"


def test_fallback_is_unknown_outside_a_checkout(monkeypatch, tmp_path):
    monkeypatch.delenv("OCL_SERVER_VERSION", raising=False)
    _no_git_binary(monkeypatch)
    assert m._resolve_server_version(tmp_path) == "unknown"


def test_git_refusal_falls_back_to_reading_git_files(monkeypatch, tmp_path):
    """The worker user may not own the checkout: git exits 128 on
    "dubious ownership"; the .git files are still readable."""
    monkeypatch.delenv("OCL_SERVER_VERSION", raising=False)
    gd = tmp_path / ".git"
    (gd / "refs" / "heads").mkdir(parents=True)
    (gd / "HEAD").write_text("ref: refs/heads/main\n")
    (gd / "refs" / "heads" / "main").write_text(SHA_A + "\n")
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 128, "", "fatal: dubious ownership"),
    )
    assert m._resolve_server_version(tmp_path) == SHA_A[:8]


def test_git_rev_parse_used_when_it_works(monkeypatch, tmp_path):
    monkeypatch.delenv("OCL_SERVER_VERSION", raising=False)
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, SHA_C + "\n", ""),
    )
    assert m._resolve_server_version(tmp_path) == SHA_C[:8]


def test_resolver_never_raises_on_garbage(monkeypatch, tmp_path):
    monkeypatch.delenv("OCL_SERVER_VERSION", raising=False)
    _no_git_binary(monkeypatch)
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_bytes(b"\xff\xfe not a ref")
    assert m._resolve_server_version(tmp_path) == "unknown"


def test_read_head_detached(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text(SHA_B + "\n")
    assert m._read_git_head_sha(tmp_path) == SHA_B


def test_read_head_packed_ref(tmp_path):
    gd = tmp_path / ".git"
    gd.mkdir()
    (gd / "HEAD").write_text("ref: refs/heads/main\n")
    (gd / "packed-refs").write_text(
        "# pack-refs with: peeled fully-peeled sorted\n"
        f"{SHA_A} refs/heads/other\n{SHA_B} refs/heads/main\n"
    )
    assert m._read_git_head_sha(tmp_path) == SHA_B


def test_read_head_linked_worktree(tmp_path):
    """A worktree's .git is a file; its branch ref lives in the common dir."""
    common = tmp_path / "main" / ".git"
    wt_gd = common / "worktrees" / "wt"
    wt_gd.mkdir(parents=True)
    (common / "refs" / "heads").mkdir(parents=True)
    (common / "refs" / "heads" / "feature").write_text(SHA_C + "\n")
    (wt_gd / "HEAD").write_text("ref: refs/heads/feature\n")
    (wt_gd / "commondir").write_text("../..\n")
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".git").write_text(f"gitdir: {wt_gd}\n")
    assert m._read_git_head_sha(wt) == SHA_C


def test_server_info_reports_server_version_not_sdk():
    opts = m.server.create_initialization_options()
    assert opts.server_version == m.SERVER_VERSION
    import importlib.metadata
    assert m.SERVER_VERSION != importlib.metadata.version("mcp")


# ── tools_hash ────────────────────────────────────────────────

def test_tools_hash_stable_across_calls():
    h1 = m._tools_hash(m._list_tools())
    h2 = m._tools_hash(m._list_tools())
    assert h1 == h2 and len(h1) == 64
    cached = m._cached_tools_hash()
    assert cached == m._cached_tools_hash()
    assert cached == (h1, len(m._list_tools()))


def test_tools_hash_changes_with_a_description():
    tools = m._list_tools()
    base = m._tools_hash(tools)
    changed = copy.deepcopy(tools)
    changed[0] = changed[0].model_copy(update={"description": changed[0].description + " "})
    assert m._tools_hash(changed) != base


def test_tools_hash_changes_with_a_schema_parameter():
    tools = m._list_tools()
    base = m._tools_hash(tools)
    changed = copy.deepcopy(tools)
    schema = copy.deepcopy(changed[0].inputSchema)
    schema.setdefault("properties", {})["new_optional"] = {"type": "string"}
    changed[0] = changed[0].model_copy(update={"inputSchema": schema})
    assert m._tools_hash(changed) != base


def test_tools_hash_sees_tool_order():
    tools = m._list_tools()
    assert m._tools_hash(list(reversed(tools))) != m._tools_hash(tools)


# ── /health ───────────────────────────────────────────────────

GENERATION = 1_762_300_000  # 2025-11-04T23:46:40Z


@pytest.fixture
def served_db(tmp_path, monkeypatch):
    dbp = tmp_path / "decisions.db"
    c = sqlite3.connect(str(dbp))
    c.execute("CREATE TABLE decisions (decision_id TEXT PRIMARY KEY)")
    c.executemany("INSERT INTO decisions VALUES (?)", [("a",), ("b",), ("c",)])
    c.execute(f"PRAGMA user_version = {GENERATION}")
    c.commit()
    c.close()
    monkeypatch.setattr(m, "DB_PATH", dbp)
    monkeypatch.setattr(m, "_last_seen_db_generation", 0)
    return dbp


def test_health_reports_build_identity(served_db):
    body, status = m._health_payload()
    assert status == 200
    assert body["status"] == "ok"
    assert body["decisions"] == 3
    assert body["db_generation"] == GENERATION
    assert body["data_snapshot"] == "2025-11-04T23:46:40Z"
    assert body["commit"] == m.SERVER_VERSION
    assert body["models"] == {
        "query_llm": m.HAIKU_MODEL,
        "cross_encoder": m.CROSS_ENCODER_MODEL,
        "embedding": m.VECTOR_MODEL_ID,
    }
    assert set(body["search_steps"]) == {
        "llm_expansion", "llm_rerank", "cross_encoder", "vector_search",
    }
    assert all(isinstance(v, bool) for v in body["search_steps"].values())
    assert (body["tools_hash"], body["tool_count"]) == m._cached_tools_hash()


def test_health_llm_steps_off_without_api_key(served_db, monkeypatch):
    monkeypatch.setattr(m, "ANTHROPIC_API_KEY", "")
    steps = m._health_payload()[0]["search_steps"]
    assert steps["llm_expansion"] is False and steps["llm_rerank"] is False


def test_health_error_still_names_the_build(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "DB_PATH", tmp_path / "missing.db")
    monkeypatch.setattr(m, "_last_seen_db_generation", 0)
    body, status = m._health_payload()
    assert status == 503
    assert body["status"] == "error"
    assert body["commit"] == m.SERVER_VERSION
    assert body["data_snapshot"] is None
    assert "tools_hash" in body


def test_data_snapshot_edges():
    assert m._data_snapshot_utc(0) is None
    assert m._data_snapshot_utc(-5) is None
    assert m._data_snapshot_utc(GENERATION) == "2025-11-04T23:46:40Z"


def test_health_route_uses_the_payload():
    """handle_health is a closure inside the HTTP entry point, out of a
    TestClient's reach; pin that it serves _health_payload."""
    src = inspect.getsource(m)
    handler = src[src.index("async def handle_health(request):"):]
    handler = handler[:handler.index("\n\n")]
    assert "_health_payload()" in handler
    assert "status_code=status" in handler
