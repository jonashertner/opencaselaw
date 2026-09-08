# MCP Registry submissions — distribution checklist

Goal: ensure `mcp.opencaselaw.ch` appears in every public MCP server
directory so users discover it natively from inside Claude / ChatGPT /
Cursor / Gemini / etc., without needing to know the URL by heart.

Each file in this directory is a ready-to-paste submission for one
registry.  Order is by ROI (highest visibility / lowest friction first).

| # | Registry | Submission method | Status | File |
|---|----------|--------------------|--------|------|
| 1 | **awesome-mcp-servers** (12k★ GitHub list) | Pull request | ✅ FILED — [PR #5544](https://github.com/punkpeye/awesome-mcp-servers/pull/5544) | [`01-awesome-mcp-servers.md`](01-awesome-mcp-servers.md) |
| 2 | **MCP Registry** (`registry.modelcontextprotocol.io`) | `mcp-publisher publish` (uses `server.json` at repo root) | ✅ PUBLISHED v1.3.0 (2026-07-30) | [`02-mcp-registry.md`](02-mcp-registry.md) |
| 3 | **Smithery** (smithery.ai — read by ChatGPT app catalog) | Web form + GitHub auth | TODO | [`03-smithery.md`](03-smithery.md) |
| 4 | ~~mcp-get.com~~ | — | **DEPRECATED** (repo archived 2026, redirects to Smithery — covered by #3) | [`04-mcp-get.md`](04-mcp-get.md) |
| 5 | **Claude Connectors Directory** (Anthropic) | Portal inside Claude.ai org settings (Team/Enterprise Owner) | ANSWER SHEET READY — needs titles deployed + privacy page pushed, then submit | [`05-anthropic-directory.md`](05-anthropic-directory.md) |

The canonical machine-readable manifest is [`server.json`](../../server.json) at the repo root, conforming to the official MCP server schema (2025-12-11). All registries should be able to ingest it directly via the GitHub raw URL.

## What every reviewer will check

  * **Live endpoint reachable** — `curl -s https://mcp.opencaselaw.ch/health` must return `{"status":"ok",...}`.
  * **No auth required for read-only tools** — confirmed.
  * **Open licence on data + code** — CC0 + MIT, declared in `dataset_card.md` and `LICENSE`.
  * **Active maintenance** — daily publish pipeline + commit history visible on GitHub.
  * **Contact email** — `team@jonashertner.com` (in `server.json`, `README.md`, `dataset_card.md`).

If a reviewer asks for a specific data point we haven't surfaced, add it
to `server.json` first, then to the appropriate submission file.
