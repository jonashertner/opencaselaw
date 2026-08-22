# Swiss Case Law — Open WebUI Setup Guide

Connect OpenCaseLaw's public MCP server to **Open WebUI** to give your self-hosted LLMs direct access to 1,050,000+ Swiss court decisions, federal statutes, cantonal laws, and citation networks.

---

## 1. Quick Setup (Remote Server)

The hosted MCP server is read-only, free, requires no API key, and requires no local database download.

### Supported Transports
- **Streamable HTTP (Recommended):** `https://mcp.opencaselaw.ch/mcp`
- **SSE (Server-Sent Events):** `https://mcp.opencaselaw.ch/sse`

---

## 2. Configuration in Open WebUI

1. Log in to your Open WebUI instance with an administrator account.
2. Navigate to **Admin Panel** → **Settings** → **Tools** / **Connections**.
3. Under MCP / External Tool Connections, click **+ Add Connection**:
   - **Name:** `Swiss Caselaw`
   - **Type:** `Streamable HTTP` (or `SSE`)
   - **Server URL:** `https://mcp.opencaselaw.ch/mcp` (or `https://mcp.opencaselaw.ch/sse`)
   - **Authentication:** `None`
4. Click **Save** / **Connect**.

---

## 3. Verify the Connection

1. In Open WebUI, start a new chat.
2. Enable the **Swiss Caselaw** toolset in the model / chat controls.
3. Ask a Swiss law question:
   > *"Search for Swiss Federal Supreme Court decisions on Arbeitsrecht and fristlose Kündigung from 2023–2025"*
4. Open WebUI will automatically invoke `search_decisions` or `get_decision` and format the response with official citations.
