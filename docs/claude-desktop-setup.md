# Swiss Case Law — MCP Setup Guide

Search 1,050,000+ published Swiss decisions directly inside Claude, ChatGPT, or Gemini.

There are two options: **remote** (no download, instant access) or **local** (offline access, 65 GB disk). The hosted deployment exposes the tool surface available on that server; local installs can expose additional local-update tools.

---

## Option A: Remote server (recommended)

Connect to the hosted server. No data download, no Python, no 65 GB disk usage.

### Claude Desktop / claude.ai

**Plan required:** Pro, Max, Team, or Enterprise.

1. Open **Settings** → **Connectors**
2. Click **"Add custom connector"**
3. Paste `https://mcp.opencaselaw.ch`
4. Click **Add**

Same steps in the browser (claude.ai) and the desktop app. No Node.js, no config files.

### Claude Code

Any plan. One command:

```bash
claude mcp add swiss-caselaw --transport sse https://mcp.opencaselaw.ch
```

### ChatGPT

Custom remote MCP servers connect through ChatGPT's **Developer mode** (full MCP connector support, currently in beta). This exposes opencaselaw's full tool surface (`search_decisions`, `get_decision`, `get_law`, …) in a normal chat.

**Plan required:** Plus, Pro, Business, Enterprise, or Education (web only). On Enterprise/Education an admin/owner must turn Developer mode on for the workspace first.

**One-time setup:**

1. Open **Settings** → **Apps & Connectors** → **Advanced settings** → enable **Developer mode**
2. Back on **Apps & Connectors**, click **Create** (next to Advanced settings)
3. Enter name: `Swiss Caselaw`
4. Paste MCP server URL: `https://mcp.opencaselaw.ch`
5. Set authentication to **No authentication** → click **Create**

The new connector appears under **Drafts**; open it to see the tools pulled from the server, and use **Refresh** there if the tool list ever changes.

**To use in a chat:** click the **+** button → **Developer mode** → select **Swiss Caselaw**. ChatGPT shows a confirmation prompt before any write action — opencaselaw is read-only, so all of its tools are safe to allow.

> **Which ChatGPT models can use it:** custom connectors work with the **Instant** and **Thinking** models (incl. Thinking High / "extended"). They do **not** work with **Pro** models — OpenAI does not pass custom connectors/actions to the Pro models (or the o-series). So if the connector "disappears" or is never called, check the model picker: switch from **Pro** to **Thinking** for heavy reasoning over Swiss law with the connector attached. This is an OpenAI platform limitation, not an opencaselaw one.

> **Transport:** ChatGPT supports both SSE and Streamable HTTP; the URL above serves SSE and connects directly. No API key — the server is public.

> **ChatGPT Deep Research is supported.** The server exposes the two read-only tools deep research requires — `search` (query → ranked `{id, title, url}` results) and `fetch` (id → full decision text) — alongside the richer `search_decisions` / `get_decision` that regular chats use. Add the connector the same way (Developer mode, above), then select it as a source in a Deep Research prompt. (Deep research only calls `search`/`fetch`; in a normal chat ChatGPT uses the full tool set.)

### Gemini CLI

Free, no account plan required.

Add to `~/.gemini/settings.json`:

```json
{
  "mcpServers": {
    "swiss-caselaw": {
      "url": "https://mcp.opencaselaw.ch"
    }
  }
}
```

Restart Gemini CLI after saving.

### Google ADK (Agent Development Kit)

Build a Gemini-powered agent against the hosted MCP server:

```bash
pip install google-adk mcp
```

```python
from google.adk.agents import LlmAgent
from google.adk.tools import MCPToolset
from mcp.client.sse import SseConnectionParams

agent = LlmAgent(
    model="gemini-3.1-pro",
    name="swiss_law_agent",
    instruction="You are a Swiss legal research assistant.",
    tools=[
        MCPToolset(
            connection_params=SseConnectionParams(
                url="https://mcp.opencaselaw.ch/sse",
            ),
        ),
    ],
)
```

The agent auto-discovers the tools exposed by that deployment (`search_decisions`, `find_citations`, `get_law`, etc.) and can call them during conversations. See the [ADK MCP docs](https://google.github.io/adk-docs/tools-custom/mcp-tools/) for authentication options and advanced configuration.

### Google Gen AI SDK

Use MCP tools directly with the Gemini API without the full ADK framework:

```bash
pip install google-genai mcp
```

```python
from google import genai
from google.genai import types
from mcp.client.sse import SseClientTransport

client = genai.Client()
tools = types.Tool(mcp_tool=types.McpTool(
    server=types.McpToolServer(
        url="https://mcp.opencaselaw.ch/sse",
    ),
))

response = client.models.generate_content(
    model="gemini-3.1-pro",
    contents="What are the leading cases on Art. 8 EMRK?",
    config=types.GenerateContentConfig(tools=[tools]),
)
```

The SDK handles tool discovery and execution automatically. See the [Gen AI SDK docs](https://googleapis.github.io/python-genai/) for details.

### Continue (VS Code & JetBrains)

Add to `~/.continue/config.json`:

```json
{
  "mcpServers": {
    "swiss-caselaw": {
      "url": "https://mcp.opencaselaw.ch/mcp"
    }
  }
}
```

See the full [Continue setup guide](continue-setup.md) for details on Streamable HTTP and SSE configuration.

### Zed

Add to `~/.config/zed/settings.json` (or `~/Library/Application Support/Zed/settings.json` on macOS):

```json
{
  "context_servers": {
    "swiss-caselaw": {
      "url": "https://mcp.opencaselaw.ch/mcp"
    }
  }
}
```

See the full [Zed setup guide](zed-setup.md).

### Open WebUI

Navigate to **Admin Panel** → **Settings** → **Tools** → **Valve / MCP Connections** → **+ Add Connection**:
- **Type:** Streamable HTTP (or SSE)
- **URL:** `https://mcp.opencaselaw.ch/mcp`
- **Auth:** None

See the full [Open WebUI setup guide](open-webui-setup.md).

### Other MCP clients

The server uses the SSE (Server-Sent Events) transport at `https://mcp.opencaselaw.ch`. Any MCP client that supports remote SSE servers can connect using this URL. No authentication required.

### Verify it works

After connecting, try asking:

> Search for BGer decisions on Mietrecht Kündigung from 2024

You should see search results with matched decisions, snippets, and metadata.

> The `update_database` and `check_update_status` tools are not available on the remote server — the dataset is updated automatically every night.

<details>
<summary>Alternative: manual JSON config for Claude Desktop (if custom connectors aren't available on your plan)</summary>

Requires [Node.js 18+](https://nodejs.org).

Add to your `claude_desktop_config.json`:

- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "swiss-caselaw": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "https://mcp.opencaselaw.ch"]
    }
  }
}
```

Restart Claude Desktop after saving.

</details>

---

## Option B: Local server (offline access)

Run the MCP server locally with your own copy of the database. Requires 65 GB free disk and a one-time 30–60 minute setup.

### What you need

1. **Claude Desktop** — [claude.ai/download](https://claude.ai/download)
2. **Python 3.10 or newer** — `python3 --version` (macOS/Linux) or `python --version` (Windows)
3. **Git** — `git --version`
4. **65 GB of free disk space**

---

## macOS Setup

### Step 1 — Download the code

Open **Terminal** (press `Cmd + Space`, type "Terminal", press Enter). Then run:

```bash
cd ~
git clone https://github.com/jonashertner/opencaselaw.git
```

This creates a folder at `/Users/YOUR_USERNAME/caselaw-repo-1`.

### Step 2 — Install Python dependencies

Still in Terminal, run these three commands one by one:

```bash
cd ~/caselaw-repo-1
python3 -m venv .venv
.venv/bin/pip install mcp pydantic huggingface-hub pyarrow
```

Wait for the installation to finish. You should see "Successfully installed ..." at the end.

### Step 3 — Find your exact paths

Run this command. It prints the two paths you'll need in the next step:

```bash
echo "command: $(cd ~/caselaw-repo-1 && pwd)/.venv/bin/python3"
echo "    arg: $(cd ~/caselaw-repo-1 && pwd)/mcp_server.py"
```

It will print something like:

```
command: /Users/anna/caselaw-repo-1/.venv/bin/python3
    arg: /Users/anna/caselaw-repo-1/mcp_server.py
```

Keep these — you'll paste them into the config file next.

### Step 4 — Add the MCP server to Claude Desktop

You have two options. **Option A** uses the Claude Desktop settings UI. **Option B** edits the config file directly — use this if Option A doesn't work or if your version of Claude Desktop doesn't have the settings UI.

#### Option A: Through the Claude Desktop settings (easiest)

1. Open **Claude Desktop**
2. Open Settings:
   - Click the **Claude** menu in the menu bar (top-left) → **Settings...**
   - Or press `Cmd + ,`
3. Click **Developer** in the left sidebar
4. Click **Edit Config**. This opens `claude_desktop_config.json` in your default text editor.
5. **Replace the entire file contents** with the following. Use the paths from Step 3:

```json
{
  "mcpServers": {
    "swiss-caselaw": {
      "command": "/Users/YOUR_USERNAME/caselaw-repo-1/.venv/bin/python3",
      "args": ["/Users/YOUR_USERNAME/caselaw-repo-1/mcp_server.py"]
    }
  }
}
```

6. Save the file (`Cmd + S`) and close the text editor.
7. Go back to Claude Desktop Settings → Developer. You should now see **swiss-caselaw** listed under MCP Servers.

> **Already have other MCP servers?** Don't overwrite the file. Add the `"swiss-caselaw": { ... }` block inside your existing `"mcpServers"` object, separated by a comma.

#### Option B: Edit the config file manually (fallback)

If you can't find the Developer settings, edit the config file directly:

```bash
open -a TextEdit ~/Library/Application\ Support/Claude/claude_desktop_config.json
```

If the file doesn't exist, create it first:

```bash
mkdir -p ~/Library/Application\ Support/Claude
echo '{}' > ~/Library/Application\ Support/Claude/claude_desktop_config.json
open -a TextEdit ~/Library/Application\ Support/Claude/claude_desktop_config.json
```

Paste the same JSON from Option A, save, and close.

### Step 5 — Restart Claude Desktop

Quit Claude Desktop completely (`Cmd + Q` — not just close the window). Then reopen it.

**How to verify it worked:** Look at the text input bar at the bottom of the chat. You should see a small hammer icon on the right side. Click it — you should see tools like `search_decisions` and `get_decision` in the list.

You can also verify in Settings → Developer — the swiss-caselaw server should show a green "running" indicator.

If you don't see the hammer icon, see [Troubleshooting](#troubleshooting) below.

### Step 6 — Build the search index (one time, ~30–60 minutes)

In Claude Desktop, send this message:

> **Please run the update_database tool to download the Swiss case law dataset.**

Claude will start downloading ~7 GB of data from HuggingFace and building the local search index. This takes 30–60 minutes depending on your internet connection and disk speed. You can watch the progress in Claude's response.

**You only need to do this once.** After it finishes, searching is instant.

---

## Windows Setup

### Step 1 — Download the code

Open **PowerShell** (press `Win + X`, select "Terminal" or "PowerShell"). Then run:

```powershell
cd $HOME
git clone https://github.com/jonashertner/opencaselaw.git
```

This creates a folder at `C:\Users\YOUR_USERNAME\caselaw-repo-1`.

### Step 2 — Install Python dependencies

Still in PowerShell, run these three commands one by one:

```powershell
cd $HOME\caselaw-repo-1
python -m venv .venv
.venv\Scripts\pip install mcp pydantic huggingface-hub pyarrow
```

Wait for the installation to finish. You should see "Successfully installed ..." at the end.

### Step 3 — Find your exact paths

Run this command to print the paths you need:

```powershell
Write-Host "command: $HOME\caselaw-repo-1\.venv\Scripts\python.exe"
Write-Host "    arg: $HOME\caselaw-repo-1\mcp_server.py"
```

It will print something like:

```
command: C:\Users\Anna\caselaw-repo-1\.venv\Scripts\python.exe
    arg: C:\Users\Anna\caselaw-repo-1\mcp_server.py
```

Keep these paths for the next step. **Important:** when you put them in the JSON config file, you must double every backslash (`\` becomes `\\`).

### Step 4 — Add the MCP server to Claude Desktop

You have two options. **Option A** uses the Claude Desktop settings UI. **Option B** edits the config file directly — use this if Option A doesn't work or if your version of Claude Desktop doesn't have the settings UI.

#### Option A: Through the Claude Desktop settings (easiest)

1. Open **Claude Desktop**
2. Open Settings:
   - Click the **hamburger menu** (three lines, top-left) → **Settings...**
   - Or click **File** → **Settings...**
3. Click **Developer** in the left sidebar
4. Click **Edit Config**. This opens `claude_desktop_config.json` in Notepad.
5. **Replace the entire file contents** with the following. Use the paths from Step 3, with doubled backslashes:

```json
{
  "mcpServers": {
    "swiss-caselaw": {
      "command": "C:\\Users\\YOUR_USERNAME\\caselaw-repo-1\\.venv\\Scripts\\python.exe",
      "args": ["C:\\Users\\YOUR_USERNAME\\caselaw-repo-1\\mcp_server.py"]
    }
  }
}
```

6. Save the file (`Ctrl + S`) and close Notepad.
7. Go back to Claude Desktop Settings → Developer. You should now see **swiss-caselaw** listed under MCP Servers.

> **Already have other MCP servers?** Don't overwrite the file. Add the `"swiss-caselaw": { ... }` block inside your existing `"mcpServers"` object, separated by a comma.

#### Option B: Edit the config file manually (fallback)

If you can't find the Developer settings, edit the config file directly:

```powershell
notepad "$env:APPDATA\Claude\claude_desktop_config.json"
```

If Notepad asks whether to create the file, click **Yes**. Paste the same JSON from Option A, save, and close.

### Step 5 — Restart Claude Desktop

Quit Claude Desktop completely (right-click the system tray icon and choose "Quit"). Then reopen it.

**How to verify it worked:** Look at the text input bar at the bottom of the chat. You should see a small hammer icon on the right side. Click it — you should see tools like `search_decisions` and `get_decision` in the list.

You can also verify in Settings → Developer — the swiss-caselaw server should show a green "running" indicator.

If you don't see the hammer icon, see [Troubleshooting](#troubleshooting) below.

### Step 6 — Build the search index (one time, ~30–60 minutes)

In Claude Desktop, send this message:

> **Please run the update_database tool to download the Swiss case law dataset.**

Claude will start downloading ~7 GB of data from HuggingFace and building the local search index. This takes 30–60 minutes depending on your internet connection and disk speed. You can watch the progress in Claude's response.

**You only need to do this once.** After it finishes, searching is instant.

---

## Using it

Once connected, just ask questions in natural language. Examples:

| What you type | What happens |
|---|---|
| *"Find BGer decisions about Mietrecht from 2024"* | Searches all Federal Supreme Court decisions on tenancy law |
| *"Search for BVGer asylum cases involving Eritrea"* | Searches Federal Administrative Court asylum decisions |
| *"Look up BGE 133 I 106"* | Fetches that specific leading case with full text |
| *"Find decisions citing Art. 8 BV"* | Searches for decisions that reference this constitutional article |
| *"How many decisions does each court have?"* | Shows statistics across all 89 courts |
| *"Draft a legal analysis of whether X constitutes Y"* | Builds a research outline grounded in actual case law |
| *"Get a case brief for BGE 133 III 121"* | Structured brief: Sachverhalt, key Erwägungen, statutes, authority, related cases |
| *"Explain the doctrine of Art. 41 OR"* | Statute text + authority-ranked BGEs + doctrine timeline |
| *"Generate an exam question on Haftpflichtrecht"* | Real BGE fact pattern with hidden analysis for Fallbearbeitung practice |

The AI automatically picks the right search tool, runs the query, and shows you the results. You can then ask follow-up questions like *"Show me the full text of the second result"* or *"Find more recent decisions on the same topic."*

---

## Studying Swiss law

Three tools power legal study and exam prep. No curriculum browsing needed — Claude builds the full session dynamically from real decisions.

**Unpack a decision:**

> *"Get a case brief for BGE 133 III 121"*
> *"Walk me through the key Erwägungen of BGE 120 II 331"*

Returns the Sachverhalt, key Erwägungen (numbered), statutes with article text, citation authority, and related cases. Claude can then generate Socratic questions, hypotheticals, or a reading guide on top of this.

**Explore doctrine:**

> *"Explain the doctrine of Art. 41 OR"*
> *"What are the leading cases on Art. 8 EMRK?"*
> *"How has Tierhalterhaftung evolved in BGer case law?"*

Returns statute text (if applicable), the most-cited BGEs ranked by authority, and a doctrine timeline showing how the rule developed year by year.

**Practice with exam questions:**

> *"Generate an exam question on Haftpflichtrecht"*
> *"Give me a Fallbearbeitung on Vertragsrecht"*

Returns a real BGE fact pattern adapted as a Fallbearbeitung scenario. The applicable statutes, legal test, and correct outcome are hidden — ask Claude to reveal the analysis after you've worked through it.

**Covered areas (100 canonical BGEs):** Vertragsrecht · Haftpflicht · Sachenrecht · Familienrecht · Arbeitsrecht · Mietrecht · Strafrecht AT · Strafrecht BT · Grundrechte · Erbrecht · Gesellschaftsrecht · Zivilprozessrecht · Strafprozessrecht · Öffentliches Prozessrecht

### Available tools

| Tool | What it does |
|------|-------------|
| `search_decisions` | Full-text search with filters (court, canton, language, date range) |
| `get_decision` | Fetch one decision by docket number (e.g., `6B_1234/2025`) or ID |
| `list_courts` | List all 89 courts with decision counts and date ranges |
| `get_statistics` | Aggregate statistics by court, canton, or year |
| `find_citations` | Show what a decision cites and what cites it |
| `find_appeal_chain` | Trace the appeal chain (Instanzenzug) across court levels |
| `find_leading_cases` | Find the most-cited decisions for a topic or statute |
| `analyze_legal_trend` | Year-by-year decision counts for a statute or topic |
| `draft_mock_decision` | Legal research outline grounded in case law and statutes |
| `get_case_brief` | Structured case brief: Sachverhalt, key Erwägungen, statutes, authority, related cases |
| `get_doctrine` | Statute text + authority-ranked BGEs + doctrine timeline for any statute or concept |
| `generate_exam_question` | Real BGE fact pattern as Fallbearbeitung exercise with hidden analysis |
| `get_law` | Look up a Swiss federal law by SR number or abbreviation |
| `search_laws` | Full-text search across Swiss federal law articles |
| `search_legislation` | Search 30,000+ Swiss legislative texts (federal + all 26 cantons) via LexFind.ch |
| `get_legislation` | Get details for a specific law by LexFind ID or SR number |
| `browse_legislation_changes` | Browse recent legislation changes for a canton or federal level |
| `update_database` | Download the latest data from HuggingFace *(local only)* |
| `check_update_status` | Check progress of a running database update *(local only)* |

---

## Keeping the dataset up to date

**Remote server:** The dataset is updated automatically every night. No action needed.

**Local server:** Ask your AI to run the `update_database` tool:

> **Update the Swiss case law database.**

This downloads only the changed files and updates the index. Much faster than the initial build.

Some deployments may also use the HuggingFace `artifacts/manifest.json` file when it advertises a full SQLite snapshot under `snapshot.sqlite_zst`. That snapshot is a bootstrap artifact for external tools; the current local MCP `update_database` tool still uses the Parquet download/rebuild path.

For a manual snapshot bootstrap recipe, see the "SQLite snapshot artifacts" section in the repository README.

---

## Troubleshooting

### No hammer icon after restarting Claude Desktop

This means Claude Desktop didn't load the MCP server. Check these in order:

1. **Is the config file in the right place?**
   - macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
   - Windows: `%APPDATA%\Claude\claude_desktop_config.json`

2. **Is the JSON valid?** Open the file and check for:
   - Missing or extra commas
   - Mismatched braces `{ }`
   - Unescaped backslashes on Windows (must be `\\`, not `\`)

3. **Do the paths actually exist?** Test by running the command directly in your terminal:
   ```bash
   /Users/YOUR_USERNAME/caselaw-repo-1/.venv/bin/python3 --version
   ```
   This should print `Python 3.x.x`. If it says "No such file", the path is wrong.

4. **Did you fully quit Claude Desktop?** On macOS, use `Cmd + Q`. Just closing the window is not enough.

### "Database not found" or empty results

You need to build the search index first. Ask Claude to run the `update_database` tool (Step 6 above).

### First search is slow

The first query after opening Claude Desktop takes 3–5 seconds to load the ~58 GB database into memory. Every subsequent search is fast.

### "Permission denied" errors on macOS

If your terminal says "permission denied" when running Python:

```bash
chmod +x ~/caselaw-repo-1/.venv/bin/python3
```

### Python not found

- **macOS**: Install Python from [python.org](https://www.python.org/downloads/) or via Homebrew: `brew install python`
- **Windows**: Install Python from [python.org](https://www.python.org/downloads/). During installation, check **"Add Python to PATH"**.

### Not enough disk space

The full search index requires ~65 GB. If you don't have enough space, the `update_database` tool will fail partway through. Free up space and run it again — it resumes where it left off.
