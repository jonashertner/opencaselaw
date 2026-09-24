#!/usr/bin/env python3
"""Build the OpenCaseLaw declarative agent for Microsoft 365 Copilot.

The package is a zip that a tenant admin uploads once (Microsoft 365 admin
center > Agents / Integrated apps). The agent calls the public MCP server
(RemoteMCPServer runtime, no auth) with a pinned set of tools, so it works
in the Copilot Chat that comes with Microsoft 365 - no Copilot Studio licence
and no metered billing (custom actions in declarative agents are included).

Tool definitions are pinned from the live server's tools/list, or from a
saved tools/list response (--tools-json) so the build runs offline.

    python tools/copilot-agent/build_package.py                   # live
    python tools/copilot-agent/build_package.py --tools-json t.json
    python tools/copilot-agent/build_package.py --schemas DIR     # + validate
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
MCP_URL = "https://mcp.opencaselaw.ch/mcp"

# Stable forever: Microsoft identifies the app by this id across versions.
APP_ID = "943f49dd-4fad-4d2f-8e32-dbe4bc778631"
APP_VERSION = "1.0.0"

# Tools the agent may call. Excluded on purpose:
# - the LLM-backed tools (attest_response, check_claim_support,
#   draft_mock_decision, generate_exam_question, analyze_legal_trend,
#   get_doctrine): their per-IP daily quotas (web_api/ocl_quota.py) would be
#   shared by every Copilot user behind Microsoft's egress IPs;
# - search / fetch (deep-research duplicates of search_decisions /
#   get_decision) and bulk or statistics tools students do not need.
PINNED_TOOLS = [
    # case law
    "search_decisions",
    "find_leading_cases",
    "get_regeste",
    "get_erwaegung",
    "find_relevant_erwaegung",
    "get_case_brief",
    "get_decision",
    "find_citations",
    "find_appeal_chain",
    "cite",
    # legislation
    "search_laws",
    "get_law",
    "get_article_history",
    "search_legislation",
    "get_legislation",
    # materials
    "get_article_purpose",
    "search_botschaft",
    "get_materialien",
    # doctrine
    "search_commentaries",
    "get_commentary",
    "search_scholarship",
    "get_scholarship",
    # administrative practice
    "search_practice",
    "get_practice",
]

CONVERSATION_STARTERS = [
    {"title": "Jurisprudence", "text": "Quels sont les arrêts de principe du Tribunal fédéral sur la résiliation abusive du bail (art. 271 CO) ?"},
    {"title": "Loi", "text": "Que prévoit l'art. 8 CC et comment le Tribunal fédéral l'interprète-t-il ?"},
    {"title": "Message du Conseil fédéral", "text": "Que dit le Message du Conseil fédéral sur le but de l'art. 336 CO ?"},
    {"title": "Rechtsprechung", "text": "Welche Leitentscheide gibt es zur Haftung des Werkeigentümers nach Art. 58 OR?"},
    {"title": "Doctrine", "text": "Quelle doctrine en libre accès traite de la proportionnalité (art. 36 Cst.) ?"},
    {"title": "Cantonal", "text": "Quelles décisions du Tribunal cantonal vaudois portent sur la LAT depuis 2020 ?"},
]

DESCRIPTION = (
    "Swiss case law, legislation, Botschaften, commentaries and open-access "
    "scholarship from OpenCaseLaw, with verbatim citations and links to the source."
)

# Keys the tools/list format allows inside mcp_tool_description.
_TOOL_KEYS = ("name", "title", "description", "inputSchema")


def fetch_tools(url: str = MCP_URL) -> list[dict]:
    """tools/list from a stateless Streamable HTTP MCP server."""
    headers = {"Content-Type": "application/json",
               "Accept": "application/json, text/event-stream"}

    def rpc(payload: dict, session: str | None) -> tuple[dict | None, str | None]:
        h = dict(headers)
        if session:
            h["Mcp-Session-Id"] = session
        req = urllib.request.Request(url, json.dumps(payload).encode(), h)
        with urllib.request.urlopen(req, timeout=60) as resp:
            sid = resp.headers.get("Mcp-Session-Id") or session
            body = resp.read().decode()
        for line in body.splitlines():
            if line.startswith("data: "):
                return json.loads(line[6:]), sid
        return (json.loads(body) if body.strip() else None), sid

    _, sid = rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {},
        "clientInfo": {"name": "copilot-agent-build", "version": APP_VERSION}}}, None)
    rpc({"jsonrpc": "2.0", "method": "notifications/initialized"}, sid)
    msg, _ = rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}, sid)
    return msg["result"]["tools"]


def pin_tools(tools: list[dict]) -> list[dict]:
    by_name = {t["name"]: t for t in tools}
    missing = [n for n in PINNED_TOOLS if n not in by_name]
    if missing:
        raise SystemExit(f"tools/list lacks pinned tools: {', '.join(missing)}")
    return [{k: by_name[n][k] for k in _TOOL_KEYS if k in by_name[n]}
            for n in PINNED_TOOLS]


def build_files(tools: list[dict]) -> dict[str, bytes]:
    instructions = (HERE / "instructions.txt").read_text(encoding="utf-8").strip()
    if len(instructions) > 8000:
        raise SystemExit(f"instructions.txt is {len(instructions)} chars; the limit is 8000")

    manifest = {
        "$schema": "https://developer.microsoft.com/json-schemas/teams/v1.30/MicrosoftTeams.schema.json",
        "manifestVersion": "1.30",
        "version": APP_VERSION,
        "id": APP_ID,
        "developer": {
            "name": "OpenCaseLaw",
            "websiteUrl": "https://opencaselaw.ch",
            "privacyUrl": "https://opencaselaw.ch/datenschutz/",
            "termsOfUseUrl": "https://opencaselaw.ch/fair-use.html",
        },
        "icons": {"color": "color.png", "outline": "outline.png"},
        "name": {"short": "OpenCaseLaw", "full": "OpenCaseLaw - Swiss law research"},
        "description": {
            "short": "Swiss case law and legislation with verbatim citations",
            "full": DESCRIPTION + " Free and open (CC0 data), no account needed.",
        },
        "accentColor": "#D42020",
        "copilotAgents": {"declarativeAgents": [
            {"id": "opencaselawAgent", "file": "declarativeAgent.json"}]},
        "permissions": ["identity"],
        "validDomains": [],
    }
    agent = {
        "$schema": "https://developer.microsoft.com/json-schemas/copilot/declarative-agent/v1.8/schema.json",
        "version": "v1.8",
        "name": "OpenCaseLaw",
        "description": DESCRIPTION,
        "instructions": instructions,
        "conversation_starters": CONVERSATION_STARTERS,
        "actions": [{"id": "opencaselawMcp", "file": "ai-plugin.json"}],
        "disclaimer": {"text": (
            "Research aid, not legal advice. Check every source before you rely on it. "
            "Aide à la recherche, pas un conseil juridique. Vérifiez chaque source.")},
    }
    names = [t["name"] for t in tools]
    plugin = {
        "$schema": "https://developer.microsoft.com/json-schemas/copilot/plugin/v2.4/schema.json",
        "schema_version": "v2.4",
        "name_for_human": "OpenCaseLaw",
        "namespace": "opencaselaw",
        "description_for_human": "Swiss case law, legislation and legal materials",
        "description_for_model": (
            "Swiss legal research: court decisions, federal and cantonal statutes, "
            "Federal Council messages, commentaries, scholarship and administrative "
            "practice. Use it for every question about Swiss law. Citation strings "
            "and quotations must be copied verbatim from its results."),
        "contact_email": "team@jonashertner.com",
        "privacy_policy_url": "https://opencaselaw.ch/datenschutz/",
        "legal_info_url": "https://opencaselaw.ch/fair-use.html",
        "functions": [{"name": n} for n in names],
        "runtimes": [{
            "type": "RemoteMCPServer",
            "auth": {"type": "None"},
            "spec": {"url": MCP_URL, "mcp_tool_description": {"file": "mcp-tools.json"}},
            "run_for_functions": names,
        }],
    }
    files = {
        "manifest.json": manifest,
        "declarativeAgent.json": agent,
        "ai-plugin.json": plugin,
        "mcp-tools.json": {"tools": tools},
    }
    out = {name: (json.dumps(obj, ensure_ascii=False, indent=2) + "\n").encode()
           for name, obj in files.items()}
    out["color.png"], out["outline.png"] = _icons()
    return out


def _icons() -> tuple[bytes, bytes]:
    """The site favicon (white cross on red) at the sizes Teams requires."""
    from PIL import Image, ImageDraw

    def cross(size: int, bg, fg) -> bytes:
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        s = size / 32
        if bg:
            d.rounded_rectangle([0, 0, size - 1, size - 1], radius=round(6 * s), fill=bg)
        d.rectangle([14 * s, 8 * s, 18 * s - 1, 24 * s - 1], fill=fg)
        d.rectangle([8 * s, 14 * s, 24 * s - 1, 18 * s - 1], fill=fg)
        buf = io.BytesIO()
        img.save(buf, "PNG")
        return buf.getvalue()

    # color: 192x192 full colour; outline: 32x32 white on transparent.
    return cross(192, "#D42020", "#FFFFFF"), cross(32, None, "#FFFFFF")


def validate(files: dict[str, bytes], schema_dir: Path) -> None:
    """Validate against Microsoft's published JSON schemas (downloaded by the caller)."""
    import jsonschema

    pairs = {"manifest.json": "MicrosoftTeams.schema.json",
             "declarativeAgent.json": "declarative-agent.schema.json",
             "ai-plugin.json": "plugin.schema.json"}
    for fname, sname in pairs.items():
        schema = json.loads((schema_dir / sname).read_text(encoding="utf-8"))
        doc = json.loads(files[fname])
        # Draft 7 even for the draft-04 files: Microsoft relies on propertyNames.
        errors = sorted(jsonschema.Draft7Validator(schema).iter_errors(doc), key=str)
        if errors:
            raise SystemExit(f"{fname}: " + "; ".join(e.message for e in errors[:5]))
        print(f"valid: {fname}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tools-json", type=Path,
                    help="saved tools/list result ({'tools': [...]}) instead of the live server")
    ap.add_argument("--schemas", type=Path,
                    help="dir with MicrosoftTeams.schema.json, declarative-agent.schema.json, plugin.schema.json")
    ap.add_argument("--out", type=Path, default=HERE / "dist" / "opencaselaw-copilot-agent.zip")
    args = ap.parse_args()

    raw = json.loads(args.tools_json.read_text()) if args.tools_json else {"tools": fetch_tools()}
    files = build_files(pin_tools(raw["tools"]))
    if args.schemas:
        validate(files, args.schemas)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.out, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in sorted(files):
            zf.writestr(name, files[name])
    print(f"wrote {args.out} ({len(PINNED_TOOLS)} tools)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
