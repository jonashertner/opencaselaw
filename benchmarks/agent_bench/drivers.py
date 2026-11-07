"""Agent drivers: run one research question end to end, return the trajectory.

The default driver is Claude Code in headless mode (`claude -p`). It is a
real MCP client, it receives the server's own instructions (R1-R11) the
way a user's client does, and it needs no API key on a machine where
Claude Code is signed in. Every run is isolated:

  --strict-mcp-config + --mcp-config   only the OpenCaseLaw server
  --tools ""                           no built-in tools (no shell, no web)
  --setting-sources ""                 no user/project settings, hooks, CLAUDE.md
  --system-prompt                      replaces Claude Code's own prompt
  fresh empty cwd                      no project files or memory

so the only things that vary between runs are the model and the server.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass, field

from .mcp_http import DEFAULT_ENDPOINT

MCP_NAME = "ocl"
TOOL_PREFIX = f"mcp__{MCP_NAME}__"

# Deliberately thin: the bench measures the server's tools and
# instructions, so the client adds no research method of its own.
SYSTEM_PROMPT_V1 = (
    "You are a legal research assistant. Answer the user's question about "
    "Swiss law. Use the OpenCaseLaw tools to find and verify the "
    "authorities, follow the server's instructions, and cite every "
    "authority you rely on. Answer in the language of the question."
)


@dataclass
class Step:
    name: str
    input: dict
    result: str = ""
    is_error: bool = False


@dataclass
class Trajectory:
    question_id: str
    run_index: int
    driver: str
    model: str
    system_prompt: str
    steps: list[Step] = field(default_factory=list)
    answer: str = ""
    status: str = "completed"   # completed | error | timeout | no_answer
    turns: int = 0
    cost_usd: float | None = None
    duration_ms: int = 0
    usage: dict = field(default_factory=dict)
    client_version: str = ""
    error: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _result_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(c.get("text", "") for c in content
                         if isinstance(c, dict) and c.get("type") == "text")
    return ""


def parse_stream_json(lines, traj: Trajectory) -> Trajectory:
    """Fill `traj` from Claude Code's --output-format stream-json lines."""
    by_id: dict[str, Step] = {}
    final = None
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = rec.get("type")
        if kind == "system" and rec.get("subtype") == "init":
            traj.model = rec.get("model") or traj.model
            traj.client_version = rec.get("claude_code_version", "")
        elif kind == "assistant":
            for block in rec.get("message", {}).get("content", []):
                if block.get("type") == "tool_use":
                    name = block.get("name", "")
                    step = Step(name=name.removeprefix(TOOL_PREFIX),
                                input=block.get("input") or {})
                    by_id[block.get("id")] = step
                    traj.steps.append(step)
        elif kind == "user":
            content = rec.get("message", {}).get("content", [])
            for block in content if isinstance(content, list) else []:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    step = by_id.get(block.get("tool_use_id"))
                    if step is not None:
                        step.result = _result_text(block.get("content"))
                        step.is_error = bool(block.get("is_error"))
        elif kind == "result":
            final = rec
    if final is None:
        traj.status = "error"
        traj.error = traj.error or "no result record in stream"
        return traj
    traj.answer = final.get("result") or ""
    traj.turns = final.get("num_turns") or 0
    traj.cost_usd = final.get("total_cost_usd")
    traj.usage = final.get("usage") or {}
    if final.get("is_error") or final.get("subtype") != "success":
        traj.status = "error"
        traj.error = str(final.get("subtype") or final.get("api_error_status") or "error")
    elif not traj.answer.strip():
        traj.status = "no_answer"
    return traj


def run_claude(prompt: str, *, system_prompt: str, model: str,
               endpoint: str | None, effort: str | None = None,
               timeout: float = 900.0) -> tuple[list[str], str, int]:
    """Run `claude -p` once. Returns (stdout lines, stderr, elapsed ms).
    endpoint=None runs with no tools at all (used by the rubric judge)."""
    cmd = [
        "claude", "-p", prompt,
        "--output-format", "stream-json", "--verbose",
        "--setting-sources", "", "--tools", "", "--disable-slash-commands",
        "--strict-mcp-config", "--no-session-persistence",
        "--permission-mode", "dontAsk",
        "--model", model, "--system-prompt", system_prompt,
    ]
    if endpoint:
        cfg = {"mcpServers": {MCP_NAME: {"type": "http", "url": endpoint}}}
        cmd += ["--mcp-config", json.dumps(cfg), "--allowedTools", f"mcp__{MCP_NAME}"]
    if effort:
        cmd += ["--effort", effort]
    t0 = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="agentbench-") as cwd:
        proc = subprocess.run(cmd, cwd=cwd, stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=timeout)
    return proc.stdout.splitlines(), proc.stderr, int((time.monotonic() - t0) * 1000)


class ClaudeCodeDriver:
    name = "claude-code"

    def __init__(self, model: str = "sonnet", endpoint: str = DEFAULT_ENDPOINT,
                 system_prompt: str = SYSTEM_PROMPT_V1, effort: str | None = None,
                 timeout: float = 900.0):
        self.model, self.endpoint = model, endpoint
        self.system_prompt, self.effort, self.timeout = system_prompt, effort, timeout

    def run(self, question: dict, run_index: int) -> Trajectory:
        traj = Trajectory(question_id=question["id"], run_index=run_index,
                          driver=self.name, model=self.model,
                          system_prompt=self.system_prompt)
        try:
            lines, stderr, ms = run_claude(
                question["question"], system_prompt=self.system_prompt,
                model=self.model, endpoint=self.endpoint, effort=self.effort,
                timeout=self.timeout)
        except subprocess.TimeoutExpired:
            traj.status, traj.error = "timeout", f"no answer within {self.timeout:.0f}s"
            return traj
        traj.duration_ms = ms
        parse_stream_json(lines, traj)
        if traj.status == "error" and not traj.error:
            traj.error = stderr[-500:]
        return traj
