"""Minimal JSON-RPC client for one OpenCaseLaw tool call over Streamable HTTP.

Stdlib only, so the bench runs on a machine without the server's
dependencies. The server runs the transport statelessly, so a bare
`tools/call` POST needs no `initialize` handshake (the same shortcut
benchmarks/run_hallucination_probe.py takes).
"""
from __future__ import annotations

import json
import re
import urllib.request

# No-retention path: bench traffic must not land in the production
# capture stream or search traces (mcp_server.py, _NO_RETENTION_PATHS).
DEFAULT_ENDPOINT = "https://mcp.opencaselaw.ch/mcp-edu"

_FOOTER = "\n\n---\n"


def parse_envelope(raw: str) -> dict:
    """The JSON-RPC envelope from an SSE (`data: …`) or plain JSON body."""
    m = re.search(r"^data: (.+)$", raw, re.MULTILINE)
    return json.loads(m.group(1) if m else raw)


def tool_payload(envelope: dict) -> dict:
    """The tool's own JSON, or {"_markdown": text} when it returned prose."""
    if "error" in envelope:
        return {"_error": envelope["error"]}
    content = (envelope.get("result") or {}).get("content") or []
    text = "\n".join(c.get("text", "") for c in content if c.get("type") == "text")
    for candidate in (text, text.rsplit(_FOOTER, 1)[0]):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    return {"_markdown": text}


def call_tool(name: str, arguments: dict, endpoint: str = DEFAULT_ENDPOINT,
              timeout: float = 180.0) -> dict:
    body = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }).encode()
    req = urllib.request.Request(endpoint, data=body, headers={
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "User-Agent": "opencaselaw-agent-bench/0.1",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    return tool_payload(parse_envelope(raw))
