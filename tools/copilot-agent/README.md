# OpenCaseLaw agent for Microsoft 365 Copilot

A declarative agent that gives Microsoft 365 Copilot Chat access to OpenCaseLaw through its public MCP server (`https://mcp.opencaselaw.ch/mcp`). A tenant admin installs it once; every assigned user then finds **OpenCaseLaw** in the agent list of Copilot Chat.

## Licensing

No Copilot Studio licence, no Copilot credits, no Microsoft 365 Copilot add-on licence. According to Microsoft's capability table, declarative agents with custom actions are available in Copilot Chat without usage-based billing ([Agent capabilities and licensing models](https://learn.microsoft.com/en-us/microsoft-365/copilot/extensibility/prerequisites#agent-capabilities-and-licensing-models)). Microsoft's usual Copilot Chat usage limits apply. The agent uses no tenant data (no SharePoint, Graph or Copilot connectors), which is what would trigger metered billing.

## What is in the package

| File | Content |
|---|---|
| `manifest.json` | App manifest 1.30 |
| `declarativeAgent.json` | Agent manifest 1.8: instructions, citation rules, conversation starters |
| `ai-plugin.json` | Plugin manifest 2.4, one `RemoteMCPServer` runtime, authentication `None` |
| `mcp-tools.json` | The 24 pinned tool definitions, copied from the server's `tools/list` |
| `color.png`, `outline.png` | Icons |

The tools are pinned: the agent can call only these 24 read-only research tools, and they change only with a new package version. OpenCaseLaw's AI-backed tools (audits, mock decisions, exam questions, trend analysis) are left out.

## Data flow

When a user asks the agent something, Copilot sends the tool call (search terms, article numbers, decision ids) from Microsoft's cloud to `mcp.opencaselaw.ch`. The runtime is configured without authentication, so no user token is passed, and OpenCaseLaw sees Microsoft's IP addresses, not the users'. The agent is instructed to send legal search terms only and to abstract names and personal details before calling a tool.

On OpenCaseLaw's side the agent calls its own endpoint, `/mcp-copilot` (`_NO_RETENTION_PATHS` in `mcp_server.py`). For requests there:

- Not kept: query text, session id, full capture, search-quality traces, per-IP cost ledger. No research archive, no model training.
- Kept: the web server's access log (Microsoft's IP address, user agent, URL; 72 hours), the application log line (tool name and structural parameters such as article numbers, no free text), tier-2/3 logs without personal data, and cost accounting without IP or query text.
- Sent at request time: to Anthropic's API (Claude), the search text and short excerpts of candidate decisions for query analysis and re-ranking, without IP or identifier; to LexFind (Switzerland), the search text for cantonal legislation.

Full detail: <https://opencaselaw.ch/datenschutz/> (section on the Copilot agent) and the French fact sheet (`make_dpo_sheet_pdf.py`).

Users should therefore not enter personal data of third parties in their questions.

## Installation (tenant admin)

Tested 2026-09-25 in a tenant with Microsoft 365 Copilot Chat (Basic) only.

1. Teams admin center > **Teams apps > Manage apps > Actions > Upload new app**, and select `opencaselaw-copilot-agent.zip`. The first time this page is used in a tenant, Microsoft can take up to 30 minutes to enable it.
2. Set **Available to** (default: the whole organisation). An admin cannot pre-install this app type; users add it themselves.
3. Users open Copilot Chat (<https://m365.cloud.microsoft/chat>) > **Agents > more agents**, search **OpenCaseLaw** ("Built by your org") and click **Add**. It appears a few minutes after the upload.
4. On the first call Copilot asks once for permission to connect to OpenCaseLaw. After that, calls run without confirmation, because every pinned tool carries `readOnlyHint: true`.

To update, upload the new zip under **New version > Upload file** on the app page. Users who already added the agent get the new version with a delay; removing and re-adding the agent picks it up at once.

## Building

`python tools/copilot-agent/make_guide_pdf.py` writes the French installation guide for admins; `make_dpo_sheet_pdf.py` the French data-protection fact sheet (keep it in line with /datenschutz/).

```bash
python tools/copilot-agent/build_package.py                    # tool definitions from the live server
python tools/copilot-agent/build_package.py --tools-json t.json # offline, from a saved tools/list result
python tools/copilot-agent/build_package.py --schemas DIR       # also validate against Microsoft's JSON schemas
```

The zip lands in `tools/copilot-agent/dist/`. Bump `APP_VERSION` for every release; never change `APP_ID`.

Release: copy the zip and the PDF to `docs/copilot/` and update the version in `docs/copilot/index.html` (all four languages). They are served at <https://opencaselaw.ch/copilot/>.
