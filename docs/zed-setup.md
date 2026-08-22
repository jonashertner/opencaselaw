# Swiss Case Law — Zed Setup Guide

Connect OpenCaseLaw's public MCP server to **Zed** editor to query 1,050,000+ Swiss court decisions, statutes, and citation graphs using Zed's built-in Model Context Protocol support.

---

## 1. Quick Setup (Remote Server)

The hosted MCP server is read-only, free, requires no API key, and requires no local database download.

### Supported Transports
- **Streamable HTTP (Recommended):** `https://mcp.opencaselaw.ch/mcp`
- **SSE (Server-Sent Events):** `https://mcp.opencaselaw.ch/sse`

---

## 2. Configuration

### Config File Location

- **macOS:** `~/.config/zed/settings.json` or `~/Library/Application Support/Zed/settings.json` (or press `Cmd + ,`)
- **Linux:** `~/.config/zed/settings.json`
- **Windows:** `%APPDATA%\Zed\settings.json`

### Direct URL Configuration (Recommended)

Add `swiss-caselaw` under `context_servers` in your `settings.json`:

```json
{
  "context_servers": {
    "swiss-caselaw": {
      "url": "https://mcp.opencaselaw.ch/mcp"
    }
  }
}
```

### Alternative: CLI Wrapper (mcp-remote)

If your Zed version uses command-based context server processes:

```json
{
  "context_servers": {
    "swiss-caselaw": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "https://mcp.opencaselaw.ch/mcp"]
    }
  }
}
```

---

## 3. Verify the Connection

1. Save `settings.json`.
2. In Zed, open the Assistant panel (`Cmd + R` on macOS, `Ctrl + R` on Linux/Windows).
3. The `swiss-caselaw` context server will show as connected, providing access to 41+ legal research tools (`search_decisions`, `get_decision`, `get_law`, `find_citations`, etc.).

---

## 4. Example Queries in Zed Assistant

### Example 1: Search Federal Court Decisions
> *"Search for BGer decisions concerning Haftpflichtrecht and Art. 41 OR"*

### Example 2: Inspect Specific BGE Head-note & Citations
> *"Get a case brief for BGE 136 III 513 and trace its incoming citations"*

### Example 3: Legislation Lookup
> *"Retrieve the German and French text of Art. 253a OR"*

---

## 5. Troubleshooting

- **Server status red in Zed:** Verify network access to `https://mcp.opencaselaw.ch/health`.
- **Invalid JSON:** Ensure no missing braces or extra trailing commas in `settings.json`.
