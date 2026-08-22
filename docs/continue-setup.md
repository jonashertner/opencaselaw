# Swiss Case Law — Continue Setup Guide

Connect OpenCaseLaw's public MCP server to **Continue** (in VS Code or JetBrains IDEs) to query 1,050,000+ Swiss court decisions, federal statutes, cantonal laws, and citation networks directly within your editor.

---

## 1. Quick Setup (Remote Server)

The hosted MCP server is read-only, free, requires no API key, and requires no local database download.

### Supported Transports
- **Streamable HTTP (Recommended):** `https://mcp.opencaselaw.ch/mcp`
- **SSE (Server-Sent Events):** `https://mcp.opencaselaw.ch/sse`

---

## 2. Configuration

### Config File Location

- **macOS / Linux:** `~/.continue/config.json`
- **Windows:** `%USERPROFILE%\.continue\config.json` (e.g. `C:\Users\YOUR_USERNAME\.continue\config.json`)
- **Workspace-specific (optional):** `.continue/config.json` in your workspace root.

### Standard Configuration

Add the `swiss-caselaw` entry to your `~/.continue/config.json`:

```json
{
  "mcpServers": {
    "swiss-caselaw": {
      "url": "https://mcp.opencaselaw.ch/mcp"
    }
  }
}
```

### Transport-Specific Configuration (Alternative format)

If your Continue extension uses explicit transport definitions or you prefer SSE:

#### Option A: Streamable HTTP (Recommended)
```json
{
  "experimental": {
    "modelContextProtocolServers": [
      {
        "name": "swiss-caselaw",
        "transport": {
          "type": "streamable-http",
          "url": "https://mcp.opencaselaw.ch/mcp"
        }
      }
    ]
  }
}
```

#### Option B: Server-Sent Events (SSE)
```json
{
  "experimental": {
    "modelContextProtocolServers": [
      {
        "name": "swiss-caselaw",
        "transport": {
          "type": "sse",
          "url": "https://mcp.opencaselaw.ch/sse"
        }
      }
    ]
  }
}
```

---

## 3. Verify the Connection

1. Save `~/.continue/config.json`.
2. Reload your editor or click the **refresh** / reload button in Continue's sidebar.
3. Open the Continue chat panel and check the available MCP tools list. You should see 41+ specialized Swiss law tools:
   - `search_decisions`
   - `get_decision`
   - `get_law`
   - `search_laws`
   - `find_citations`
   - `find_leading_cases`
   - `get_case_brief`
   - `get_doctrine`

---

## 4. Example Queries

Once connected, ask legal research questions directly in Continue chat:

### Example 1: Search Decisions on Tenancy Law
> *"Find BGer decisions on Mietrecht Kündigung from 2024"*

**Tool Invocation:**
```json
search_decisions({
  "query": "Mietrecht Kündigung",
  "limit": 3
})
```

**Output:**
```
Found 1082+ decisions (showing 1–3):
1. BGE 125 III 231 (1999-04-13) [bge] [de]
   Regeste: Mietrecht; Kündigungsschutz für einen zusammen mit einer Wohnung vermieteten Autoabstellplatz...
```

### Example 2: Statute Lookup
> *"Look up Art. 8 BV in German and French"*

**Tool Invocation:**
```json
get_law({
  "law": "BV",
  "article": "8",
  "language": "de"
})
```

### Example 3: Leading Case & Citation Analysis
> *"Find leading cases on Art. 41 OR (Tierhalterhaftung)"*

**Tool Invocation:**
```json
find_leading_cases({
  "statute": "OR 41"
})
```

---

## 5. Troubleshooting

- **Server unreachable or timeout:** Ensure your machine can reach `https://mcp.opencaselaw.ch/health`.
- **Tools not appearing in Continue:** 
  - Verify JSON syntax in `~/.continue/config.json` (no trailing commas).
  - Try toggling between `https://mcp.opencaselaw.ch/mcp` (Streamable HTTP) and `https://mcp.opencaselaw.ch/sse` (SSE).
  - Restart the editor (VS Code / JetBrains).
