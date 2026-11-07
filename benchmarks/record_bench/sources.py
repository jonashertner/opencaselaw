"""Snapshots of OpenCaseLaw tool output, the only place legal authorities come from.

Every legal authority in a record or in the answer key is copied from a
tool result stored here (AGENTS.md: "Do not construct legal citation
strings"). A snapshot is the tool name, its arguments, the endpoint, the
time of the call and the raw result. The offline check
(`fixture.py --check`) verifies that each copied field appears verbatim
in the snapshot it names, so a typed citation fails the test suite.

Fetching is live and only happens in `build.py`; tests and the replay
read the stored snapshots.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCES = HERE / "fixture" / "sources"


def key(tool: str, args: dict) -> str:
    blob = json.dumps({"tool": tool, "args": args}, sort_keys=True, ensure_ascii=False)
    slug = "_".join(str(v) for v in args.values())[:48]
    slug = "".join(c if c.isalnum() else "-" for c in slug).strip("-")
    return f"{tool}__{slug}__{hashlib.sha256(blob.encode()).hexdigest()[:10]}"


def load(snapshot: str) -> dict:
    return json.loads((SOURCES / f"{snapshot}.json").read_text(encoding="utf-8"))


def text_of(snapshot: dict) -> str:
    """The tool result as one searchable string (markdown or JSON)."""
    r = snapshot["result"]
    return r["_markdown"] if isinstance(r, dict) and set(r) == {"_markdown"} else json.dumps(r, ensure_ascii=False)


def fetch(tool: str, args: dict, *, refresh: bool = False, endpoint: str | None = None) -> tuple[str, dict]:
    """(snapshot key, snapshot) for one tool call; cached on disk."""
    from .mcp_http import DEFAULT_ENDPOINT, call_tool
    k = key(tool, args)
    path = SOURCES / f"{k}.json"
    if path.exists() and not refresh:
        return k, json.loads(path.read_text(encoding="utf-8"))
    result = call_tool(tool, args, endpoint=endpoint or DEFAULT_ENDPOINT)
    snap = {"tool": tool, "args": args, "endpoint": endpoint or DEFAULT_ENDPOINT,
            "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "result": result}
    SOURCES.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snap, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return k, snap
