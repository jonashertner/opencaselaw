"""Offline checks for the Microsoft 365 Copilot agent package (tools/copilot-agent)."""

import importlib.util
import json
import re
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "copilot_build", ROOT / "tools" / "copilot-agent" / "build_package.py")
build = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build)

# Tools whose per-IP daily quota (web_api/ocl_quota.py) every Copilot user
# would share behind Microsoft's egress IPs.
QUOTA_TOOLS = {"attest_response", "check_claim_support", "draft_mock_decision",
               "generate_exam_question", "analyze_legal_trend", "get_doctrine"}


def _fake_tools(extra=()):
    names = list(build.PINNED_TOOLS) + list(extra)
    return [{"name": n, "title": n, "description": f"{n} tool",
             "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}}},
             "annotations": {"readOnlyHint": True},
             "outputSchema": {"type": "object"}} for n in names]


@pytest.fixture(scope="module")
def files():
    return build.build_files(build.pin_tools(_fake_tools(extra=["search", "attest_response"])))


def test_pinned_tools_exclude_quota_tools():
    assert not QUOTA_TOOLS & set(build.PINNED_TOOLS)
    assert len(build.PINNED_TOOLS) == len(set(build.PINNED_TOOLS))


def test_pin_drops_unpinned_tools_and_extra_keys(files):
    tools = json.loads(files["mcp-tools.json"])["tools"]
    assert [t["name"] for t in tools] == build.PINNED_TOOLS
    assert all(set(t) <= {"name", "title", "description", "inputSchema", "annotations"} for t in tools)
    # Copilot reads readOnlyHint to decide how to confirm a call.
    assert all(t["annotations"]["readOnlyHint"] for t in tools)


def test_missing_pinned_tool_fails():
    with pytest.raises(SystemExit, match="find_leading_cases"):
        build.pin_tools([t for t in _fake_tools() if t["name"] != "find_leading_cases"])


def test_plugin_binds_every_function_to_the_mcp_runtime(files):
    plugin = json.loads(files["ai-plugin.json"])
    names = [f["name"] for f in plugin["functions"]]
    # The Teams admin center rejects functions without a description.
    assert all(f.get("description") for f in plugin["functions"])
    runtime, = plugin["runtimes"]
    assert runtime["type"] == "RemoteMCPServer"
    assert runtime["auth"] == {"type": "None"}
    # The no-retention endpoint; mcp_server._NO_RETENTION_PATHS must list it.
    assert runtime["spec"]["url"] == "https://mcp.opencaselaw.ch/mcp-edu"
    assert runtime["run_for_functions"] == names == build.PINNED_TOOLS
    assert re.fullmatch(r"[A-Za-z0-9]+", plugin["namespace"])
    assert len(plugin["name_for_human"]) <= 20
    assert len(plugin["description_for_human"]) <= 100


def test_agent_limits(files):
    agent = json.loads(files["declarativeAgent.json"])
    assert 0 < len(agent["instructions"]) <= 8000
    assert len(agent["description"]) <= 1000
    assert len(agent["conversation_starters"]) <= 6
    assert len(agent["disclaimer"]["text"]) <= 500
    assert agent["actions"] == [{"id": "opencaselawMcp", "file": "ai-plugin.json"}]


def test_instructions_name_only_pinned_tools(files):
    text = json.loads(files["declarativeAgent.json"])["instructions"]
    mentioned = set(re.findall(r"\b(?:search|get|find)_[a-z_]+|\bcite\b", text))
    assert mentioned <= set(build.PINNED_TOOLS), mentioned - set(build.PINNED_TOOLS)


def test_manifest_references_package_files(files):
    manifest = json.loads(files["manifest.json"])
    assert manifest["id"] == build.APP_ID
    assert len(manifest["name"]["short"]) <= 30
    assert len(manifest["description"]["short"]) <= 80
    assert len(manifest["description"]["full"]) <= 4000
    refs = {manifest["icons"]["color"], manifest["icons"]["outline"],
            manifest["copilotAgents"]["declarativeAgents"][0]["file"]}
    assert refs <= set(files)


def test_icons_have_teams_sizes(files):
    from io import BytesIO
    from PIL import Image

    color = Image.open(BytesIO(files["color.png"]))
    outline = Image.open(BytesIO(files["outline.png"]))
    assert color.size == (192, 192)
    assert outline.size == (32, 32)
    # Outline icons must be white on transparent.
    px = outline.convert("RGBA").load()
    opaque = {px[x, y][:3] for x in range(32) for y in range(32) if px[x, y][3]}
    assert opaque == {(255, 255, 255)}


def test_zip_written(tmp_path, monkeypatch):
    tools_json = tmp_path / "tools.json"
    tools_json.write_text(json.dumps({"tools": _fake_tools()}))
    out = tmp_path / "agent.zip"
    monkeypatch.setattr("sys.argv", ["build", "--tools-json", str(tools_json), "--out", str(out)])
    assert build.main() == 0
    assert set(zipfile.ZipFile(out).namelist()) == {
        "manifest.json", "declarativeAgent.json", "ai-plugin.json",
        "mcp-tools.json", "color.png", "outline.png"}


def test_package_url_is_a_no_retention_path():
    import mcp_server
    from urllib.parse import urlparse
    assert mcp_server._is_no_retention_path(urlparse(build.MCP_URL).path)
