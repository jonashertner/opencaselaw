#!/usr/bin/env python3
"""Minimal MCP client example for OpenCaseLaw.

Usage:
    python examples/python/minimal_mcp_client.py "missbräuchliche Kündigung"
"""

from __future__ import annotations

import asyncio
import json
import sys

from mcp import ClientSession
from mcp.client.sse import sse_client


SERVER_URL = "https://mcp.opencaselaw.ch"
TOOL_NAME = "search_decisions"


def _extract_content_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        for key in ("text", "content", "value"):
            value = content.get(key)
            if isinstance(value, str):
                return value
    return ""


def _extract_results(payload):
    if isinstance(payload, dict):
        for key in ("results", "items", "decisions", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    if isinstance(payload, list):
        return payload
    return []


async def main() -> int:
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} \"query\"", file=sys.stderr)
        return 2

    query = sys.argv[1]

    async with sse_client(SERVER_URL) as streams:
        async with ClientSession(*streams) as session:
            await session.initialize()
            response = await session.call_tool(TOOL_NAME, {"query": query, "limit": 3})

    text = "".join(_extract_content_text(part) for part in getattr(response, "content", []))
    if not text and hasattr(response, "model_dump"):
        text = json.dumps(response.model_dump(), ensure_ascii=False)

    payload = json.loads(text) if text.strip().startswith("{") or text.strip().startswith("[") else text
    results = _extract_results(payload)

    for result in results[:3]:
        citation = result.get("citation_string_de") if isinstance(result, dict) else None
        if citation:
            print(citation)

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
