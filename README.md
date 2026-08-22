# OpenCaseLaw

Open Swiss case law and legislation for legal research and AI.

**1M+ decisions · all 26 cantons · 1875–today · citation graph · MCP + REST + Parquet · CC0 data · MIT code**

[Try the search](https://opencaselaw.ch) ·
[Connect an AI client](#connect-in-30-seconds) ·
[Download the dataset](#2-download-the-dataset) ·
[API documentation](https://mcp.opencaselaw.ch/api/docs)

## What you can do

- Find Swiss decisions across federal and cantonal courts, in German, French and Italian.
- Trace citations between cases and identify the leading decisions on a question.
- Query case law and legislation from Claude, ChatGPT-compatible clients, or any MCP client.
- Download the corpus for legal NLP, retrieval and graph research.

## Connect in 30 seconds

```json
{
  "mcpServers": {
    "opencaselaw": {
      "url": "https://mcp.opencaselaw.ch/mcp"
    }
  }
}
```

For a minimal Python client example, see [`examples/python/minimal_mcp_client.py`](examples/python/minimal_mcp_client.py).

No sign-up, no API key, read-only. 42 tools; setup guides for individual
clients are at [mcp.opencaselaw.ch](https://opencaselaw.ch/mcp/).

## A real result

**Question.** When is a dismissal in retaliation for an employee asserting a
claim abusive under Swiss law?

**One call** — `search_decisions("missbräuchliche Kündigung Rachekündigung")`
returns 129 decisions, the leading one first:

> **BGE 136 III 513** (7 October 2010)
>
> *Art. 336 OR; missbräuchliche Kündigung. Der Arbeitnehmer ist vor einer
> Rachekündigung nur geschützt (Art. 336 Abs. 1 lit. d OR), sofern er nach
> Treu und Glauben annehmen kann, dass die von ihm geltend gemachten
> Ansprüche berechtigt sind. Es ist nicht erforderlich, dass sie tatsächlich
> begründet sind.*

The Regeste is returned verbatim from the stored official head-note, and the
citation string comes from a stored field — tools never construct citations.
That contract is what makes the answer checkable rather than plausible.

Reproduce it:

```bash
curl -sG https://mcp.opencaselaw.ch/api/decisions \
  --data-urlencode "q=missbräuchliche Kündigung Rachekündigung" \
  --data-urlencode "limit=3"
```

## Coverage

| Resource | Coverage |
|---|---|
| Court decisions | 1,050,000+ records, 118 court and source collections, 1875–today |
| Languages | German, French, Italian |
| Legislation | 5,525 federal laws, 15,600 cantonal laws (all 26 cantons) |
| Citation graph | ~10M resolved decision-to-decision edges, 12.4M statute references |
| Interpretive layers | Federal Council dispatches, commentaries, open-access scholarship, federal administrative practice |
| Access | Web, MCP, REST, daily CC0 Parquet |
| Licensing | CC0 data, MIT code (ECtHR texts © ECHR-CEDH, see the data card) |

Counts are generated from one source of truth,
[`docs/canonical_numbers.md`](docs/canonical_numbers.md), with the
verification method recorded alongside each figure.

**Limitations, stated plainly.** Coverage is not complete where portals
restrict access; those sources are documented rather than glossed. The
citation graph reports resolution *coverage* (93.8 %), which is not a
precision estimate — a human semantic audit is in preparation. Some cantonal
decisions may be published unredacted by the court; we inherit upstream
pseudonymisation and do not re-pseudonymise. See
[governance and removal policy](docs/governance-and-removal-policy.md).

## Contributing

Data-quality reports are the most valuable contribution — every one so far
has been reproduced, fixed and pinned with a regression test. See
[CONTRIBUTING.md](CONTRIBUTING.md).

---

*The remainder of this page is reference detail.*

## Why this exists

Swiss legal research today is **fragmented across paywalls, inaccessible to language models, and prohibitively expensive** for the people who need it most — law students, independent researchers, and anyone outside the major firms. Commercial databases (Weblaw, Swisslex, Legalis) charge hundreds of francs per month and still don't expose a clean API. LLMs hallucinate statute text because they have no authoritative source. Small cantons publish decisions in PDF archives nobody indexes.

OpenCaseLaw fixes this. **Every published Swiss court decision, every federal and cantonal law, the resolved citation graph between them, and 44 MCP tools (42 remote in public mode + 2 local-only) that let any modern LLM act as a Swiss legal research assistant — all free, all open, all refreshed automatically.**

## What you get

**Case law** — 1,050,000+ decision records (~909,000 unique decisions; cross-identifier duplicates retained and linked, never deleted) from 1875 to today across **118 courts**, full text + structured metadata, covering:
- All 7 federal courts (BGer, BVGer, BStGer, BPatGer, BGE, BGE historical, BGE-EGMR)
- Federal military court (Militärkassationsgericht / MKG, 1,244 decisions 1915–2025)
- All 26 cantonal court systems (first, second, and third instance)
- Regulatory decisions (FINMA, ComCo, FDPIC, IndepBC, ElCom, PostCom, ComCom)
- **ECHR/EGMR**: 841 Swiss-respondent (HUDOC) + general ECtHR Grand Chamber/Chamber/Committee — 2,840 judgments live (481 BGE-published EGMR translations, 841 HUDOC, 1,186 Chamber, 237 Committee, 95 Grand Chamber)
- Three official languages: German 463,012 (46.7%), French 446,869 (45.1%), Italian 81,796 (8.2%); schema reserves `rm` for Romansh
- Deduplicated via docket normalisation + content-length-aware merge
- Updated daily; BGer decisions available within ~15 minutes of court publication

**Legislation** — every Swiss law, federal and cantonal, locally mirrored with article-level indexing:
- 5,525 federal laws / ~133,359 articles in each of DE/FR/IT from the Fedlex SPARQL endpoint
- 15,600 cantonal laws / 361,430 articles, direct-scraped from all 26 cantons (LexWork covers 18, SIL covers 2, ZH OpenData covers ZH, TI~RL covers TI); LexFind PDF fallback supplements 4 cantons for laws missing from their primary portals
- Unified SQLite FTS5 search federates both corpora; sub-millisecond article lookup
- Monthly refresh on the 2nd of each month (the day after laws enter into force)
- 12.4 M resolved links from decisions to individual statute articles

**Citation graph** — the **only public large-scale citation graph of the Swiss legal system**:
- 9.65 M resolved decision-to-decision citation edges (10 M extracted) with confidence scores
- 12.4 M decision-to-statute links resolved against the current consolidated text
- Bidirectional lookup, appeal-chain resolution (Instanzenzug), leading-case ranking by citation authority
- Powers `find_leading_cases`, `find_citations`, `find_appeal_chain`, `analyze_legal_trend` (top: BGE 125 V 351 with 85,108 incoming citations)

**44 MCP tools (42 remote in public mode + 2 local-only)** — specialised research tools that run in your LLM of choice:
- Natural-language decision search (BM25 + synonym expansion + Haiku reranking, **MRR@10 = 0.647** on a 100-query golden set)
- Leading-case discovery, citation networks, appeal chains, jurisprudence evolution
- Federal + cantonal law article lookup, full-text search across both — with **colloquial→legal vocabulary expansion** (searching "Vaterschaftsurlaub" finds the statute even though it says "Urlaub des andern Elternteils") and **cross-language cantonal search** (German query finds French/Italian cantonal laws)
- Doctrine overviews (statute + authority-ranked BGEs + timeline + Botschaft reference)
- **Legislative history (Materialien)** — 83,958 Botschaft amendment references across 9,139 BBl publications and 33,465 distinct (statute, article) pairs; **Phase 2 verbatim Botschaft corpus** (5,900+ documents, ~410K FTS5-indexed paragraphs) accessible via `search_botschaft` (topical FTS5 across the verbatim corpus), `get_article_purpose` (verbatim Botschaft text for a specific article), and `get_article_history` (chronological timeline composing statute + Botschaft + leading cases + commentary); per-article digests (legislative intent, key arguments, design choices, rejected alternatives) for BV and BGFA; parliamentary debate transcripts for the BV. **Full verbatim ingest to ~25K Botschaften via Fedlex SPARQL discovery is scaling.**
- **Decision-structure access** — `get_decision_structure` (Sachverhalt + Erwägungen + Dispositiv + Regeste split), `get_erwaegung` (verbatim Schweizer-citation Einheit, e.g. `get_erwaegung("BGE 140 III 86", "2.3")`), `get_regeste` (official BGer/BVGer/BStGer head-note)
- Scholarly commentary lookup from OnlineKommentar.ch + OpenLegalCommentary.ch (1,131 commentaries)
- **Fallbearbeitung exam questions** generated from real BGE fact patterns, with hidden analysis for practice
- Draft mock decisions from fact patterns (research-only tool for grounding LLM outputs)
- Structured case briefs (regeste, Sachverhalt, Erwägungen, Dispositiv, cited statutes, authority)

**Multiple access paths** — same data, two distinct audiences:

*For LLM users, researchers and developers — full 24-tool surface:*
- Remote MCP server at `mcp.opencaselaw.ch` (SSE + Streamable HTTP) — 30-second setup in any MCP client (Claude, ChatGPT, Cursor, Gemini, Windsurf)
- [OpenAI-compatible tool definitions](docs/openai-tools.json) for Grok/xAI and any function-calling LLM API
- Local MCP server — full offline capability, all 44 tools (the 2 local-only update tools are available offline), ~65 GB disk
- 30-route REST API with [interactive documentation](https://mcp.opencaselaw.ch/api/docs) (Swagger UI + OpenAPI spec)
- Bulk Parquet download via the [HuggingFace dataset](https://huggingface.co/datasets/voilaj/swiss-caselaw) (~7 GB)
- Live dashboard + browsing UI at [opencaselaw.ch](https://opencaselaw.ch)

*For legal practitioners drafting documents — curated practitioner surface:*
- [**Word add-in**](https://word.opencaselaw.ch/install.html) — Search decisions and insert **correctly-formatted Swiss legal citations** directly in Word (no copy-paste). Click an Erwägung to insert it with the correct sub-citation; click a law § to insert that alinea. Free **Audit** scans the whole document for fabricated citations and bad pinpoints (deterministic, no LLM). **Pro tier** (Stripe-billed, CHF 5/month, 25 AI calls/day shared, structural PII redaction) adds four AI tools: **Verify** (single citation vs. full-text decision), **Strengthen** (paragraph x-ray with leading-case suggestions ranked by citation-graph centrality + commentary excerpts), **Find Support** (locate decisions that back an unsupported statement, scored 0–100), and **Reflect** (literary mirror on the whole document — one canonical work that frames the same human dilemma + one reflective question). Exposes ~8 of the 44 MCP tools (42 remote in public mode + 2 local-only) through the REST API, tuned for the write-your-brief workflow.

**Performance you can defend in a paper**:

| Metric | Value |
|---|---|
| Decision search quality | MRR@10 = 0.647 (online) / 0.470 (offline reproducible) · **+102 %** over baseline |
| Article lookup latency | < 1 ms (local FTS5) |
| BGer publication → searchable | ~15 min (was 24 h pre-poller) |
| Daily full-text rebuild | ~5 h, zero downtime (atomic swap) |
| Citation-to-decision resolution | 9.65 M edges resolved (10 M extracted) |

---

## What this is

A structured, searchable archive of Swiss court decisions — from the Federal Supreme Court (BGer) down to cantonal courts in all 26 cantons. Every decision includes the full decision text, docket number, date, language, legal area, judges, cited decisions, and 20+ additional metadata fields.

The dataset is built by direct scraping of official court websites and cantonal court portals. New decisions are scraped, deduplicated, and published every night.

There are eight ways to use it, depending on what you need:

| Method | For whom | What you get |
|--------|----------|-------------|
| [**Search with AI**](#1-search-with-ai) | Everyone | Natural-language search in Claude, ChatGPT, Cursor, or Gemini — instant access, no download, full 24-tool surface |
| [**Citation Analysis**](#citation-graph-tools) | Legal scholars, researchers | Leading cases, citation networks, appeal chains, jurisprudence trends over time |
| [**Statute Lookup**](#statute-lookup-tools) | Legal professionals | Full article text from 5,525 federal laws and 15,600 cantonal laws, federated FTS5, sub-millisecond lookup |
| [**Legislation Search**](#legislation-tools) | Legal professionals | LexFind-backed discovery search with `fetch_top_n_texts` for single-call natural-language workflows |
| [**Education tools**](#education-tools) | Law students, instructors | Structured case briefs, doctrine timelines, real-BGE exam questions with hidden analysis |
| [**Word Add-in**](https://word.opencaselaw.ch/install.html) | Legal practitioners writing briefs | Insert formatted Swiss citations into Word · click Erwägung / § to insert with correct sub-reference · free **Audit** (5-rail citation check) · **Pro** (CHF 5/mo, 25 AI calls/day): **Verify** (citation vs. full text) · **Strengthen** (paragraph x-ray + leading-case suggestions) · **Find Support** (decisions backing a statement) · **Reflect** (literary mirror) — curated ~8-tool subset |
| [**REST API / Download**](#2-download-the-dataset) | Developers, data scientists, NLP researchers | 30-route REST API, bulk Parquet download via HuggingFace (~7 GB) |
| [**Web UI**](https://opencaselaw.ch) | Everyone | Live dashboard with corpus stats, daily delta, top movers, multilingual browsing |

> **Not sure where to start?** Connect to the [remote MCP server](#option-a-remote-server-recommended) — works with Claude, ChatGPT, and Gemini CLI. Instant access to all 1,050,000+ decisions, citation analysis, statute lookup, legislation search, and education tools, no download needed.

---

## 1. Search with AI

The dataset comes with an [MCP server](https://modelcontextprotocol.io) whose exact tool surface is deployment-dependent. Local deployments expose all 44 tools; remote mode omits the 2 local-only update tools (`update_database`, `check_update_status`) for 42 remote tools. You ask a question in natural language; the tool runs a full-text search and returns matching decisions with snippets.

### Remote vs. local

|  | Remote | Local |
|---|---|---|
| **Setup** | 30 seconds | 30–60 minutes |
| **Disk** | None | ~65 GB |
| **Tools** | 38 (no local update tools) | 40, including `update_database` and `check_update_status` |
| **Freshness** | Nightly (automatic) | Manual |
| **Offline** | No | Yes |
| **Requires** | Claude, ChatGPT, or Gemini CLI (see plans below) | Any MCP client |

> Start with the remote server. Switch to local only if you need offline access.

### Option A: Remote server (recommended)

Connect directly to the hosted MCP server — no data download, no local database, instant access to 1,050,000+ decisions.

**Claude.ai / Claude Desktop** (easiest):

1. Open **Settings** → **Connectors**
2. Click **"Add custom connector"**
3. Paste `https://mcp.opencaselaw.ch`
4. Click **Add**

Same steps in the browser (claude.ai) and the desktop app. No Node.js, no config files, no downloads.

> Available on Pro, Max, Team, and Enterprise plans. For the free plan, use Claude Code or the [manual JSON config](#alternative-manual-json-config-if-custom-connectors-arent-available).

**Claude Code:**

```bash
claude mcp add swiss-caselaw --transport sse https://mcp.opencaselaw.ch
```

<details>
<summary>Alternative: manual JSON config (if custom connectors aren't available)</summary>

Add to `claude_desktop_config.json` ([Node.js](https://nodejs.org) required):

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

**ChatGPT:**

1. Open **Settings** → **Apps** → **Advanced settings** → enable **Developer mode**
2. Click **Create app** → name it `Swiss Caselaw`, paste `https://mcp.opencaselaw.ch/sse`, auth: None
3. In any chat: click **+** → **Developer mode** → select **Swiss Caselaw**

> Available on Plus, Pro, Team, Enterprise, and Edu plans. Recommended with GPT-5.3 (GPT-5.4 does not currently support MCP tool invocation).

**Gemini CLI:**

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

Restart Gemini CLI after saving. No account plan required — Gemini CLI is free.

**Google ADK (Agent Development Kit):**

Build a Gemini-powered agent with access to all tools:

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

See the [full MCP setup guide](docs/claude-desktop-setup.md) for Google Gen AI SDK examples and all other platforms.

> See the **[full MCP setup guide](docs/claude-desktop-setup.md)** for detailed instructions for all platforms.

> The `update_database` and `check_update_status` tools are only available on the local server — the remote dataset is updated automatically every night.

### Option B: Local server (offline access)

Run the MCP server locally with your own copy of the database (~65 GB disk). This gives you offline access and full control over the data.

#### Setup with Claude Code

[Claude Code](https://docs.anthropic.com/en/docs/claude-code) is Anthropic's CLI for working with Claude in the terminal.

**Step 1.** Clone this repository:

```bash
git clone https://github.com/jonashertner/opencaselaw.git
cd caselaw-repo-1
```

**Step 2.** Create a virtual environment and install the MCP server dependencies:

```bash
python3 -m venv .venv

# macOS / Linux
source .venv/bin/activate
pip install mcp pydantic huggingface-hub pyarrow fastapi

# Windows (PowerShell)
.venv\Scripts\Activate.ps1
pip install mcp pydantic huggingface-hub pyarrow
```

**Step 3.** Register the MCP server with Claude Code:

```bash
# macOS / Linux
claude mcp add swiss-caselaw -- /path/to/caselaw-repo-1/.venv/bin/python3 /path/to/caselaw-repo-1/mcp_server.py

# Windows
claude mcp add swiss-caselaw -- C:\path\to\caselaw-repo-1\.venv\Scripts\python.exe C:\path\to\caselaw-repo-1\mcp_server.py
```

Use the full absolute path to the Python binary inside `.venv` so that the server always finds its dependencies, regardless of which directory you run Claude Code from.

**Step 4.** Restart Claude Code and run your first search.

On first use, the server automatically:
1. Downloads all Parquet files (~7 GB) from [HuggingFace](https://huggingface.co/datasets/voilaj/swiss-caselaw) to `~/.swiss-caselaw/parquet/`
2. Builds a local SQLite FTS5 full-text search index at `~/.swiss-caselaw/decisions.db` (~58 GB)

This takes 30–60 minutes depending on your machine and connection. It only happens once — after that, searches run instantly against the local database.

**Total disk usage:** ~65 GB in `~/.swiss-caselaw/` (macOS/Linux) or `%USERPROFILE%\.swiss-caselaw\` (Windows).

#### Setup with Claude Desktop

See the **[Claude Desktop setup guide](docs/claude-desktop-setup.md)** for step-by-step instructions (macOS + Windows).

**Quick version** — add this to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "swiss-caselaw": {
      "command": "/path/to/caselaw-repo-1/.venv/bin/python3",
      "args": ["/path/to/caselaw-repo-1/mcp_server.py"]
    }
  }
}
```

Config file location: `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows). On Windows, use `.venv\\Scripts\\python.exe` instead.

Any MCP-compatible client works with the same `command` + `args` pattern.

#### Keeping the data current

The dataset is updated daily. To get the latest decisions, ask Claude to run the `update_database` tool, or call it explicitly. This re-downloads the Parquet files from HuggingFace and rebuilds the local database.

#### SQLite snapshot artifacts

The HuggingFace dataset may also publish a full compressed SQLite base snapshot for bootstrap tools under `artifacts/sqlite/snapshots/`. Its metadata lives in `artifacts/manifest.json` as `snapshot`; if that value is `null`, consumers should fall back to the Parquet rebuild path above.

The snapshot is intended for local MCP/server bootstrapping: download `artifacts/manifest.json`, fetch `snapshot.sqlite_zst.path`, verify `snapshot.sqlite_zst.sha256`, decompress to `decisions.db.tmp`, run a quick SQLite row/schema check, then atomically move it to `decisions.db`. Newer `artifacts/sqlite/deltas/*.sqlite.zst` entries can then be applied by tools that support delta updates.

Manual snapshot bootstrap:

```bash
pip install zstandard

python - <<'PY'
import hashlib
import json
import os
import sqlite3
import urllib.request
from pathlib import Path

import zstandard as zstd

base = "https://huggingface.co/datasets/voilaj/swiss-caselaw/resolve/main"
data_dir = Path(os.environ.get("SWISS_CASELAW_DIR", Path.home() / ".swiss-caselaw")).expanduser()
data_dir.mkdir(parents=True, exist_ok=True)

with urllib.request.urlopen(f"{base}/artifacts/manifest.json") as r:
    manifest = json.load(r)

snapshot = manifest.get("snapshot")
if not snapshot:
    raise SystemExit("No SQLite snapshot is advertised; use update_database instead.")

meta = snapshot["sqlite_zst"]
tmp_db = data_dir / "decisions.db.tmp"
final_db = data_dir / "decisions.db"

class HashingReader:
    def __init__(self, raw, digest):
        self.raw = raw
        self.digest = digest

    def read(self, size=-1):
        data = self.raw.read(size)
        if data:
            self.digest.update(data)
        return data

h = hashlib.sha256()
with urllib.request.urlopen(f"{base}/{meta['path']}") as src, tmp_db.open("wb") as dst:
    zstd.ZstdDecompressor().copy_stream(HashingReader(src, h), dst)
if h.hexdigest() != meta["sha256"]:
    tmp_db.unlink(missing_ok=True)
    raise SystemExit("SHA-256 verification failed")

con = sqlite3.connect(tmp_db)
try:
    rows = con.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
    con.execute("SELECT decision_id FROM decisions LIMIT 1").fetchone()
finally:
    con.close()

expected = snapshot.get("rows")
if expected is not None and rows != expected:
    raise SystemExit(f"Row-count check failed: got {rows}, expected {expected}")

tmp_db.replace(final_db)
print(f"Installed {final_db} with {rows:,} decisions")
PY
```

#### How the local database works

```
~/.swiss-caselaw/
├── parquet/              # Downloaded Parquet files from HuggingFace (~7 GB)
│   └── data/
│       ├── bger.parquet
│       ├── bvger.parquet
│       └── ...           # 100 files, one per court
├── decisions.db          # SQLite FTS5 search index (~58 GB)
├── reference_graph.db    # Case citations + statute links (~3.5 GB)
└── statutes.db           # Federal law articles from Fedlex (~610 MB)
```

All data stays on your machine. No API calls are made during search — the MCP server queries the local SQLite database directly.

**Database structure.** `decisions.db` is a single SQLite file with two tables:

- **`decisions`** — the main table with one row per decision. Holds 24 columns, including the search-facing fields plus `json_data` (full record blob) and `canonical_key` for dedup-aware lookup. Indexed on `court`, `canton`, `decision_date`, `language`, `docket_number`, `chamber`, and `decision_type` for fast filtered queries.

- **`decisions_fts`** — an FTS5 virtual table that mirrors 7 text columns from `decisions`: `court`, `canton`, `docket_number`, `language`, `title`, `regeste`, and `full_text`. FTS5 builds an inverted index over these columns, enabling sub-second full-text search across 1M+ decisions. The tokenizer is `unicode61 remove_diacritics 2`, which handles accented characters across German, French, Italian, and Romansh. Insert/update/delete triggers keep the FTS index in sync with the main table automatically.

**Why ~58 GB.** The full text of 962K+ court decisions averages ~15 KB per decision. The FTS5 inverted index adds overhead for every unique token, its position, and the column it appears in. This is a known trade-off: FTS5 indexes over large text corpora are substantially larger than the source data, but they enable instant ranked search without external infrastructure.

**Search pipeline.** When you search, the server:

1. **Detects query intent** — docket number lookup (`6B_1234/2023`), explicit FTS syntax (`Mietrecht AND Kündigung`), or natural language (`decisions on tenant eviction`).

2. **Runs multiple FTS5 query strategies** — For natural-language queries, the server generates several FTS query variants (AND, OR, phrase, field-focused on regeste/title, with multilingual term expansion) and executes them in sequence. Each strategy produces a ranked candidate set. For explicit syntax (AND/OR/NOT, quoted phrases, column filters), the raw query is tried first.

3. **Fuses candidates via RRF** — Results from all strategies are merged using Reciprocal Rank Fusion: each candidate's score is the weighted sum of `1/(k + rank)` across all strategies that returned it. Decisions found by multiple strategies get a boost.

4. **Reranks with signal scoring** — The top candidates are reranked using a composite score that combines:
   - **BM25 score** (from FTS5, with custom column weights: `full_text` 1.2, `regeste` 5.0, `title` 6.0 — headnotes and titles are weighted heavily over body text)
   - **Term coverage** in title (3.0x), regeste (2.2x), and snippet (0.8x)
   - **Phrase match** in title/regeste (1.8x)
   - **Docket match** — exact (6.0x) or partial (2.0x)
   - **Statute/citation graph signals** — if the query mentions an article (e.g., "Art. 8 BV"), decisions that cite that provision are boosted
   - **Court prior** — e.g., asylum queries boost BVGer results

5. **Selects the best passage** — For each result, the server scans the full text for the most relevant passage and returns it as a snippet.

### Available tools

Available on both remote and local unless noted.

| Tool | Description |
|------|-------------|
| `search_decisions` | Full-text search with filters (court, canton, language, date range, chamber) |
| `get_decision` | Fetch a single decision by docket number or ID. Includes citation graph counts (cited by / cites). |
| `list_courts` | List all courts with decision counts |
| `get_statistics` | Aggregate stats by court, canton, or year |
| `find_citations` | Show what a decision cites and what cites it, with confidence scores |
| `find_appeal_chain` | Trace the appeal chain (Instanzenzug) — prior and subsequent instances |
| `find_leading_cases` | Find the most-cited decisions for a topic or statute |
| `analyze_legal_trend` | Year-by-year decision counts for a statute or topic |
| `draft_mock_decision` | Build a research-only mock decision outline from facts, grounded in caselaw + statute references; asks clarification questions before conclusion |
| `get_case_brief` | Structured brief for any case — regeste, key Erwägungen, cited statutes, and citation authority |
| `get_doctrine` | Statute article or legal concept → ranked leading cases + doctrine timeline |
| `generate_exam_question` | Legal topic → real BGE fact pattern with hidden analysis for Fallbearbeitung practice |
| `get_law` | Look up a federal or cantonal Swiss law by SR number / abbreviation + optional canton. Returns full article text from the local mirror (Fedlex for federal; cantonal_laws.db for cantonal — direct portal scrape for 22 cantons, LexFind PDF extraction for the remaining 4) |
| `search_laws` | Unified federal + cantonal FTS5 search with BM25 rank interleaving. Filter by `canton=` for a specific jurisdiction, or `jurisdiction='federal'` / `'cantonal'` |
| `get_commentary` | Scholarly commentary for a specific federal law article from OnlineKommentar.ch (CC-BY-4.0) |
| `search_commentaries` | Full-text search across 362 legal commentaries from OnlineKommentar.ch across 19 federal laws |
| `search_legislation` | LexFind-backed discovery search across federal + cantonal legislation. `fetch_top_n_texts=N` enriches the top N results with full article text in a single call |
| `get_legislation` | Full metadata + article text for any federal or cantonal law. Local-first (statutes.db → cantonal_laws.db → LexFind API fallback) |
| `browse_legislation_changes` | Recent-changes feed per canton or federal level (live LexFind API, no local mirror) |
| `update_database` | Re-download latest Parquet files from HuggingFace and rebuild the local database *(local only)* |
| `check_update_status` | Check progress of a running database update *(local only)* |

### Citation-integrity toolkit (anti-hallucination)

OpenCaseLaw treats Swiss legal citations as a closed-corpus problem: every reference an LLM writes either resolves to a real entry in the ~1.05M-record corpus + Fedlex statute mirror, or it does not. The MCP server ships a four-tool toolkit that makes this contract enforceable end-to-end.

| Tool | Purpose |
|------|---------|
| `cite` | Build the canonical citation_string + canonical_url for a Swiss reference. Returns `exists=false` plus close_matches when the reference is fabricated — the LLM is contracted to copy the returned string verbatim instead of constructing one itself. |
| `check_claim_support` | Per-claim Sonnet judge: given a (claim, decision_id, optional pinpoint), return `supports: yes / partial / no / contradicts / unrelated` against the verbatim Erwägung text. Different model family (Sonnet) than the one running retrieval (Haiku) so retrieval errors are not re-introduced in verification. |
| `attest_response` | Closing audit, called once before a final answer ships. Runs up to **five** rails over the LLM's draft: <br>① **case** — every BGE/BGer/BVGer/BStGer/BPatGer/MKGE reference exists, every pinpoint resolves; <br>② **statute** — every `Art. X LAW` reference resolves in `statutes.db`; <br>③ **quote** — every `"…"` of ≥30 chars appears verbatim in a cited source (decision or statute); <br>④ **date** — every `vom DD.MM.YYYY` adjacent to a citation matches the stored decision date; <br>⑤ **grounding** *(opt-in via `audit_grounding=true`)* — for each verified citation, an independent Sonnet judge checks whether the cited source actually supports the proposition the LLM attached to it. <br>Returns `linked_text` ready to ship verbatim with every validated citation wrapped in a Markdown link. |
| `get_erwaegung` / `get_regeste` / `get_law` / `get_materialien` / `get_commentary` | The verbatim-text suppliers — the only sources the LLM is permitted to direct-quote. |

The architecture defends against **two empirically-measured legal-LLM failure classes**:

- **Hallucination** (Dahl, Magesh, Suzgun & Ho, *Large Legal Fictions*, Stanford RegLab, 2024 — Journal of Legal Analysis): 58–88 % of legal queries to general-purpose LLMs produced at least one fabricated authority. A follow-up Stanford RegLab study of commercial legal-RAG tools (Magesh et al., *Hallucination-Free?*, 2024) measured 17–33 %. → caught by audits ① ② ③ ④.
- **Reasoning error** (Butler & Butler, Isaacus, *Legal RAG Bench*, Mar 2026): citation is real and source was retrieved, but the proposition attached to it is not actually supported by the cited text. → caught by audit ⑤.

The full server prompt (R1–R8) embeds these rules so any connecting client (Claude, ChatGPT, Cursor, Gemini, Copilot Studio) inherits the contract automatically. See [`mcp_server.py`](mcp_server.py) for the implementation, [`tests/web/test_attest_audits.py`](tests/web/test_attest_audits.py) for the regression suite, and the [verification section on opencaselaw.ch](https://opencaselaw.ch/#verification) for a public explainer.

End-to-end performance against this architecture is measured by **[Swiss Legal RAG Bench](benchmarks/swiss_legal_rag_bench/README.md)** — a benchmark modelled on Butler & Butler's *Legal RAG Bench* methodology, covering DE/FR/IT federal-law questions and decomposing errors into hallucination / retrieval / reasoning components. v0.1 baseline (live OpenCaseLaw + Claude Sonnet 4.6, 10 questions): **100 % correctness, 90 % groundedness, 70 % retrieval accuracy**.

### Example queries

These work on both the remote and local server:

```
> Search for BGer decisions on Mietrecht Kündigung from 2024

> What did the BVGer rule on asylum seekers from Eritrea?

> Show me the full text of 6B_1234/2023

> How many decisions does each court in canton Zürich have?

> Find decisions citing Art. 8 BV

> What are the leading cases on Art. 8 EMRK?

> Show me the citation network for BGE 138 III 374

> How has Mietrecht jurisprudence evolved over time?

> Show me Art. 41 OR (statute lookup)

> Search for statute provisions about Verjährung

> Trace the appeal chain for 5A_234/2026

> Explain BGE 133 III 121 to me

> What are the leading cases on Art. 41 OR and how has the doctrine evolved?

> Give me a practice case on Haftpflichtrecht

> Search for cantonal data protection laws

> Show me the details of SR 220 (Obligationenrecht)

> What legislation changed recently in Zürich?
```

The AI calls the MCP tools automatically — you see the search results inline and can ask follow-up questions about specific decisions.

### Citation graph tools

Four tools expose the **reference graph**: 9.65 million resolved decision-to-decision citation edges (10 million extracted) and 12.4 million statute references. These require the graph database (`output/reference_graph.db`); if it's not available, the tools return a message instead of failing.

**`find_citations`** — Given a decision, show its outgoing citations (what it references) and incoming citations (what references it). Each resolved citation includes the target decision's metadata and a confidence score. Unresolved references (e.g., older decisions not in the dataset) appear with their raw reference text.

```
> Show me the citation network for BGE 138 III 374

## Outgoing (13 — what this decision cites)
1. BGE 136 III 365 (2010-01-01) [bge] conf=0.98 mentions=1
2. BGE 133 III 189 (2007-01-01) [bge] conf=0.93 mentions=1
...

## Incoming (13,621 — what cites this decision)
1. 5A_234/2026 (2026-02-19) [bger] conf=0.92 mentions=2
2. 5A_117/2026 (2026-01-30) [bger] conf=0.88 mentions=1
...
```

Parameters: `decision_id` (required), `direction` (both/outgoing/incoming), `min_confidence` (0–1, default 0.3), `limit` (default 50, max 200).

**`find_appeal_chain`** — Trace the appeal chain (Instanzenzug) for a decision. Shows prior instances (lower courts) and subsequent instances (appeals to higher courts), reconstructing the full procedural path (e.g., Bezirksgericht → Obergericht → Bundesgericht).

Parameters: `decision_id` (required), `min_confidence` (0–1, default 0.3).

**`find_leading_cases`** — Find the most-cited decisions, ranked by how many other decisions reference them. Filter by statute (law code + article), text query, court, or date range.

```
> What are the leading cases on Art. 8 EMRK?

1. BGE 135 I 143 (2009) — 3,840 citations
   Regeste: Anspruch auf Aufenthaltsbewilligung...
2. BGE 130 II 281 (2004) — 3,155 citations
   Regeste: Familiennachzug; gefestigtes Anwesenheitsrecht...
```

Parameters: `query` (optional text), `law_code` + `article` (optional statute), `court`, `date_from`, `date_to`, `limit` (default 20, max 100). At least one of `query` or `law_code` is recommended; without any filter it returns the globally most-cited decisions.

**`analyze_legal_trend`** — Year-by-year decision counts showing how jurisprudence on a topic or statute has evolved over time. Returns a table with counts and a visual bar chart.

```
> How has Art. 29 BV jurisprudence evolved?

**Filter:** Art. 29 BV
**Total:** 34,669 decisions

Year     Count  Bar
2000       275  █████
2005       733  ████████████
2010     1,028  █████████████████
2015     1,536  ██████████████████████████
2020     1,896  ████████████████████████████████
2025     2,400  ████████████████████████████████████████
```

Parameters: `query` (optional text), `law_code` + `article` (optional statute), `court`, `date_from`, `date_to`. At least one of `query` or `law_code` is required.

### Statute lookup tools (local, federal + cantonal)

Two tools provide direct access to **Swiss law text** from the local mirror, covering both federal and cantonal jurisdictions with article-level FTS5 indexing and sub-millisecond lookup:

- **Federal:** 5,525 laws / ~133,359 articles in each of DE/FR/IT from the [Fedlex](https://www.fedlex.admin.ch) SPARQL endpoint, mirrored monthly into `statutes.db`. Covers every consolidated federal act in German, French, and Italian.
- **Cantonal:** 15,600 laws / 361,430 articles in `cantonal_laws.db`, sourced via two layers:
  - **Direct portal scraping (primary)** — 22 cantons whose official Gesetzessammlungen are published via the LexWork, SIL, ZH OpenData, or TI Raccolta delle Leggi platforms (AG, AI, AR, BE, BL, BS, FR, GE, GL, GR, LU, NE, NW, OW, SG, SH, SO, TG, TI, VS, ZG, ZH). The HTML is parsed natively — no PDF extraction, no OCR — yielding clean article-level data.
  - **LexFind PDF fallback** — 4 cantons not yet covered by direct scrapers (JU, SZ, UR, VD). PDFs from [LexFind.ch](https://www.lexfind.ch) are extracted with PyMuPDF and segmented into articles. Bilingual secondary-language passes for BE / FR / VS / GR.
  - Combined and federated via SQLite FTS5 with the federal table.

**`get_law`** — Look up any Swiss law (federal or cantonal) by SR number or abbreviation, optionally fetching a specific article with full text.

```
> Show me Art. 8 BV

# BV — SR 101
**Bundesverfassung der Schweizerischen Eidgenossenschaft vom 18. April 1999**

### Art. 8 — Rechtsgleichheit
1 Alle Menschen sind vor dem Gesetz gleich.
2 Niemand darf diskriminiert werden, namentlich nicht wegen der Herkunft, ...

> Show me Art. 1 of the Zurich Hundegesetz

get_law(canton="ZH", sr_number="554.5", article="1")
```

Parameters: `sr_number` or `abbreviation` (at least one required), `article` (optional — omit to see the full article list), `language` (de/fr/it, default de), `canton` (default `CH`; set to `ZH`, `BE`, etc. for cantonal lookup).

**`search_laws`** — Unified FTS5 search across every Swiss statute article, federal and cantonal. BM25-ranked per corpus, merged by rank interleaving so each response surfaces both jurisdictions.

```
> Search for statute provisions about Verjährung

1. [CH]  Art. 130 OR (SR 220): Die Verjährung beginnt mit der Fälligkeit der Forderung...
2. [ZH]  § 19 Kirchgemeindenreglement (SR 182.60): ... Verjährungsbestimmungen ...
3. [CH]  Art. 132 OR (SR 220): Bei der Berechnung der Frist ist der Tag...
```

Parameters: `query` (required, FTS5 syntax), `sr_number` (optional — one specific federal law, implies federal-only), `canton` (optional — restrict to one canton, or `CH` for federal-only), `jurisdiction` (`all` / `federal` / `cantonal`, default `all`), `language` (de/fr/it), `limit` (1–50).

### Legislation discovery tools (LexFind-backed)

Three additional tools layer the **live LexFind API** on top of the local mirror for cases where the mirror isn't enough — newly published versions between refresh cycles, full version history, recent-changes feeds. These are the broader discovery surface; for plain "give me the current text" queries `get_law` / `search_laws` are faster and more reliable.

**`search_legislation`** — Broader discovery search across Swiss legislation, with optional single-call full-text enrichment. Useful when you don't know whether the law is federal or cantonal, or when you need a one-shot natural-language workflow.

```
> Search for the Bernese dog act with full text

search_legislation(query="Hundegesetz", canton="BE", fetch_top_n_texts=2)
→ returns top 2 matches with full_text_preview + sample_articles inline,
  no follow-up get_legislation call needed.
```

Parameters: `query` (required), `canton` (optional — CH, ZH, BE, etc.), `active_only` (default true), `search_in_content` (default false — searches titles/keywords; set true to search law text), `language` (de/fr/it), `limit` (1–60, default 20), `fetch_top_n_texts` (0–10, default 0 — set to N to enrich top N results with parsed full article text).

**`get_legislation`** — Get details for a specific law including metadata, version history, and links to official sources (Fedlex, cantonal portals).

```
> Show me the Obligationenrecht on LexFind

SR 220 — Bundesgesetz betreffend die Ergänzung des Schweizerischen
Zivilgesetzbuches (Fünfter Teil: Obligationenrecht)
Entity: Bund (CH) | Category: Gesetz | Keywords: OR
In force since: 01.01.2026
Sources:
  DE: https://www.fedlex.admin.ch/eli/cc/27/317_321_377/de
  FR: https://www.fedlex.admin.ch/eli/cc/27/317_321_377/fr
```

Parameters: `lexfind_id` (from search results) or `systematic_number` + `canton` (e.g., "220" + "CH"), `include_versions` (default false), `language` (de/fr/it).

**`browse_legislation_changes`** — Recent legislation changes for a canton or federal level. Shows new laws, amendments, and abrogations with dates and links.

```
> What legislation changed recently in Zürich?

1. [01.03.2026] version — Vollzugsverordnung zur Finanzverordnung (SR 181.131)
2. [01.03.2026] new — Studienordnung für den Masterstudiengang... (SR 414.253.125)
3. [01.03.2026] version — Organisationsverordnung der Finanzdirektion (SR 172.110.3)
```

Parameters: `canton` (default CH), `language` (de/fr/it).

### Education tools

Three tools support **legal study** covering the three core student workflows: understanding a case, understanding a doctrine, and practicing exam subsumption. Tools return rich structured data; Claude acts as the tutor and generates all pedagogy dynamically.

**`get_case_brief`** — Any case reference ("BGE 133 III 121", docket number, or decision_id) → structured brief. Returns regeste, Sachverhalt, key numbered Erwägungen, Dispositiv, cited statutes with text excerpts, citation authority (incoming/outgoing count), and the top cited-by and cites cases.

**`get_doctrine`** — Statute article ("Art. 41 OR") or legal concept ("culpa in contrahendo", "Tierhalterhaftung") → doctrine overview. Returns the leading cases ranked by citation count, a chronological doctrine timeline showing how rules evolved, and the statute text (if applicable).

**`generate_exam_question`** — Legal topic ("Haftpflichtrecht", "Art. 41 OR", "Mietrecht") → real Fallbearbeitung. Picks a leading BGE, returns the anonymized Sachverhalt as fact pattern plus a hidden analysis (applicable statutes, legal test, correct outcome). The student writes a subsumption, then asks Claude to reveal the analysis for comparison and feedback. Pass `exclude_ids` to avoid repeating the same case.

`draft_mock_decision` can use optional Fedlex URLs and caches fetched statute excerpts in
`~/.swiss-caselaw/fedlex_cache.json` (configurable via `SWISS_CASELAW_FEDLEX_CACHE`).

### Search quality benchmark

Use a fixed golden query set to track search relevance over time:

```bash
python3 benchmarks/run_search_benchmark.py \
  --db ~/.swiss-caselaw/decisions.db \
  -k 10 \
  --json-output benchmarks/latest_search_benchmark.json
```

Metrics: `MRR@k`, `Recall@k`, `nDCG@k`, `Hit@1`

The repository also ships a frozen offline baseline at `benchmarks/search_benchmark_2026-03-19_offline_full.json`. On the full 100-query set against a 1,078,177-row local `decisions.db`, that run recorded `MRR@10 = 0.4697`, `Recall@10 = 0.4958`, `nDCG@10 = 0.5250`, and `Hit@1 = 0.33`. Treat it as a reproducible offline baseline rather than a fully provisioned hosted-deployment score.

You can enforce minimum quality gates (non-zero exit on failure):

```bash
python3 benchmarks/run_search_benchmark.py \
  --db ~/.swiss-caselaw/decisions.db \
  -k 10 \
  --min-mrr 0.50 \
  --min-recall 0.75 \
  --min-ndcg 0.85
```

### Build Reference Graph (Optional)

For statute/citation-aware reranking, build the local graph database:

```bash
python3 search_stack/build_reference_graph.py \
  --source-db ~/.swiss-caselaw/decisions.db \
  --courts bger,bge,bvger \
  --db output/reference_graph.db
```

Then point the server to it:

```bash
export SWISS_CASELAW_GRAPH_DB=output/reference_graph.db
```

Graph signals are enabled by default. To disable them, set `SWISS_CASELAW_GRAPH_SIGNALS=0`.

### Build Statutes Database (Optional)

For statute lookup (`get_law`, `search_laws`), build the Fedlex statute database:

```bash
# Download the top 100 most-cited federal laws from Fedlex
python3 -m scrapers.fedlex --top 100

# Build the SQLite FTS5 statutes database
python3 -m search_stack.build_statutes_db
```

Then copy to the data directory:

```bash
cp output/statutes.db ~/.swiss-caselaw/
```

Or set the path explicitly:

```bash
export SWISS_CASELAW_STATUTES_DB=output/statutes.db
```

---

## 2. Download the dataset

The full dataset is on [HuggingFace](https://huggingface.co/datasets/voilaj/swiss-caselaw) as Parquet files — one file per court, 34 fields per decision including complete decision text.

Machine-consumable artifact metadata is published at `artifacts/manifest.json`. Besides daily Parquet/SQLite deltas, the manifest may point to an optional full compressed SQLite snapshot at `artifacts/sqlite/snapshots/<date>.decisions.sqlite.zst` for tools that want to bootstrap a local FTS5 database without rebuilding from Parquet.

### With Python (datasets library)

**Step 1.** Install the library:

```bash
pip install datasets
```

**Step 2.** Load the data:

```python
from datasets import load_dataset

# Load a single court (~170k decisions, ~800 MB)
bger = load_dataset("voilaj/swiss-caselaw", data_files="data/bger.parquet")

# Load all courts (~900K decisions, ~6.5 GB download)
ds = load_dataset("voilaj/swiss-caselaw", data_files="data/*.parquet")
```

**Step 3.** Explore:

```python
# Print a single decision
decision = bger["train"][0]
print(decision["docket_number"])   # "6B_1/2024"
print(decision["decision_date"])   # "2024-03-15"
print(decision["language"])        # "de"
print(decision["regeste"][:200])   # First 200 chars of the headnote
print(decision["full_text"][:500]) # First 500 chars of the full text
```

### With pandas

```python
import pandas as pd

# Load one court
df = pd.read_parquet("hf://datasets/voilaj/swiss-caselaw/data/bger.parquet")

# Filter by date
df_recent = df[df["decision_date"] >= "2024-01-01"]
print(f"{len(df_recent)} decisions since 2024")

# Filter by language
df_french = df[df["language"] == "fr"]

# Group by legal area
df.groupby("legal_area").size().sort_values(ascending=False).head(10)
```

### Direct download

Every court is a single Parquet file. Download directly:

```
https://huggingface.co/datasets/voilaj/swiss-caselaw/resolve/main/data/bger.parquet
https://huggingface.co/datasets/voilaj/swiss-caselaw/resolve/main/data/bvger.parquet
https://huggingface.co/datasets/voilaj/swiss-caselaw/resolve/main/data/zh_gerichte.parquet
...
```

Full list of files: [huggingface.co/datasets/voilaj/swiss-caselaw/tree/main/data](https://huggingface.co/datasets/voilaj/swiss-caselaw/tree/main/data)

---

## 3. REST API

Query the dataset over HTTP without installing anything. This uses the [HuggingFace Datasets Server](https://huggingface.co/docs/datasets-server/).

**Get rows:**

```bash
curl "https://datasets-server.huggingface.co/rows?dataset=voilaj/swiss-caselaw&config=default&split=train&offset=0&length=5"
```

**Get dataset info:**

```bash
curl "https://datasets-server.huggingface.co/info?dataset=voilaj/swiss-caselaw"
```

**Search by SQL** (DuckDB endpoint):

```bash
curl -X POST "https://datasets-server.huggingface.co/search?dataset=voilaj/swiss-caselaw&config=default&split=train" \
  -d '{"query": "SELECT docket_number, decision_date, language FROM data WHERE court = '\''bger'\'' LIMIT 10"}'
```

> Note: The REST API queries the dataset as configured in the HuggingFace repo (per-court Parquet files, full 34-field schema). For bulk access or local analysis, use the [download method](#2-download-the-dataset) above.

---

## 4. Web UI

A local chat interface for searching Swiss court decisions. Ask questions in natural language, and an AI assistant searches the full corpus and answers with cited decisions.

```
Browser (localhost:5173)  →  FastAPI backend  →  MCP server  →  Local SQLite FTS5 DB
```

Everything runs on your machine. No data leaves your computer (except LLM API calls to the provider you choose).

### What you need

| Requirement | How to check | Where to get it |
|-------------|-------------|-----------------|
| **Python 3.10+** | `python3 --version` (macOS/Linux) or `python --version` (Windows) | [python.org/downloads](https://www.python.org/downloads/) |
| **Node.js 18+** | `node --version` | [nodejs.org](https://nodejs.org) — download the LTS version |
| **An LLM provider** | *(see below)* | At least one cloud API key **or** a local model via Ollama |
| **~65 GB free disk** | `df -h .` (macOS/Linux) | For the search index (downloaded on first run) |

> **Windows users:** Install Python from [python.org](https://www.python.org/downloads/) and check "Add Python to PATH" during installation. Node.js installs npm automatically.

#### Cloud providers (choose at least one, or use Ollama below)

| Provider | Env variable | Where to get a key | Cost |
|----------|-------------|---------------------|------|
| **Google Gemini** | `GEMINI_API_KEY` | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) | Free tier available |
| **OpenAI** | `OPENAI_API_KEY` | [platform.openai.com/api-keys](https://platform.openai.com/api-keys) | Free credits for new accounts |
| **Anthropic (Claude)** | `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com/) | Pay-as-you-go |

> **Important:** A Claude Desktop or Claude Pro *subscription* does NOT include an API key. You need a separate developer account at [console.anthropic.com](https://console.anthropic.com/).

#### Local models (no API key needed)

If you prefer not to use cloud APIs, you can run everything locally with [Ollama](https://ollama.com):

| Model | Command to install | Download size | RAM needed |
|-------|-------------------|---------------|------------|
| Qwen 2.5 (14B) | `ollama pull qwen2.5:14b` | ~9 GB | ~16 GB |
| Llama 3.3 (70B) | `ollama pull llama3.3:70b` | ~40 GB | ~48 GB |

Install Ollama from [ollama.com](https://ollama.com) (macOS, Linux, Windows), then:

```bash
ollama serve          # start the Ollama server (leave running)
ollama pull qwen2.5:14b   # download a model (one-time)
```

The Web UI auto-detects Ollama and shows local models as available.

### Step-by-step setup

**Step 1. Clone the repository:**

```bash
git clone https://github.com/jonashertner/opencaselaw.git
cd caselaw-repo-1
```

**Step 2. Create a Python virtual environment:**

```bash
python3 -m venv .venv
```

Activate it:

| OS | Command |
|----|---------|
| **macOS / Linux** | `source .venv/bin/activate` |
| **Windows (PowerShell)** | `.venv\Scripts\Activate.ps1` |
| **Windows (cmd)** | `.venv\Scripts\activate.bat` |

> You'll know it's active when your terminal prompt starts with `(.venv)`.

**Step 3. Install Python dependencies:**

```bash
pip install fastapi uvicorn python-dotenv mcp pyarrow pydantic
```

Then install at least one LLM provider SDK:

```bash
pip install anthropic        # for Claude
pip install openai           # for OpenAI / GPT-4o / local models via Ollama
pip install google-genai     # for Google Gemini
```

> **Tip:** The `openai` package is also used for local Ollama models (Ollama exposes an OpenAI-compatible API). If you only want to use local models, `pip install openai` is sufficient — no cloud API key required.

**Step 4. Install the frontend:**

```bash
cd web_ui && npm install && cd ..
```

**Step 5. Configure your API key:**

```bash
cp .env.example .env
```

Open `.env` in a text editor and paste your API key on the appropriate line. For example, if you have a Gemini key, change `GEMINI_API_KEY=AI...` to your actual key. Leave the other provider lines as-is — they will be ignored if empty.

> You can also skip this step and configure keys from the **Settings** panel inside the UI after starting.

**Step 6. Start the app:**

| OS | Command |
|----|---------|
| **macOS / Linux** | `./scripts/run_web_local.sh` |
| **Windows (PowerShell)** | `.\scripts\run_web_local.ps1` |

Open **http://localhost:5173** in your browser.

**What to expect on first run:** The MCP server will automatically download the dataset (~7 GB) from HuggingFace and build a local search index (~58 GB). This takes **30–60 minutes** depending on your connection and disk speed. You'll see progress in the terminal. After this one-time setup, the app starts instantly.

### Features

- **5 models**: Claude, OpenAI, Gemini (cloud) + Qwen 2.5 and Llama 3.3 via Ollama (local)
- **Local-first option**: Run entirely on your machine with Ollama — no cloud API keys needed
- **Streaming**: Responses appear token-by-token in real time
- **Tool-augmented chat**: The AI calls search, get_decision, list_courts, etc. automatically
- **Decision cards**: Clickable statute references with inline Fedlex article text
- **Filters**: Narrow results by court, canton, language, and date range
- **In-app settings**: Configure API keys and Ollama connection from the UI
- **Export**: Download conversations as Markdown, Word, or PDF

### Troubleshooting

| Problem | Solution |
|---------|----------|
| `python3: command not found` (Windows) | Use `python` instead of `python3`, or reinstall Python with "Add to PATH" checked |
| `npm: command not found` | Install Node.js from [nodejs.org](https://nodejs.org) |
| `ModuleNotFoundError: No module named 'fastapi'` | Activate your venv (`source .venv/bin/activate`) and re-run `pip install ...` |
| "No provider configured" banner | Click the gear icon (Settings) and paste an API key, or start Ollama |
| "Database not found" on first run | Wait for the initial download to finish (check terminal for progress) |
| Port already in use | Edit `BACKEND_PORT` or `FRONTEND_PORT` in `.env` |
| PowerShell script blocked (Windows) | Run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once |

For advanced configuration (custom ports, MCP server path, timeouts), see [`.env.example`](.env.example).

---

## What's in each decision

Every decision has 34 structured fields:

### Core fields

| Field | Type | Example | Description |
|-------|------|---------|-------------|
| `decision_id` | string | `bger_6B_1234_2025` | Unique key: `{court}_{docket_normalized}` |
| `court` | string | `bger` | Court code ([full list](https://opencaselaw.ch)) |
| `canton` | string | `CH` | `CH` for federal, `ZH`/`BE`/`GE`/... for cantonal |
| `docket_number` | string | `6B_1234/2025` | Original case number as published |
| `decision_date` | date | `2025-03-15` | Date the decision was rendered |
| `language` | string | `de` | `de`, `fr`, `it`, or `rm` |
| `full_text` | string | *(complete text)* | Full decision text, typically 5–50 pages |
| `source_url` | string | `https://bger.ch/...` | Permanent link to the original |

### Legal content

| Field | Type | Description |
|-------|------|-------------|
| `regeste` | string | Legal headnote / summary (Regeste) |
| `legal_area` | string | Area of law (Strafrecht, Zivilrecht, ...) |
| `title` | string | Subject line (Gegenstand) |
| `outcome` | string | Result: Gutheissung, Abweisung, Nichteintreten, ... |
| `decision_type` | string | Type: Urteil, Beschluss, Verfügung, ... |
| `cited_decisions` | string | JSON array of cited decision references |
| `bge_reference` | string | BGE collection reference if published |
| `abstract_de` | string | German abstract (primarily BGE) |
| `abstract_fr` | string | French abstract |
| `abstract_it` | string | Italian abstract |

### Court metadata

| Field | Type | Description |
|-------|------|-------------|
| `chamber` | string | Chamber (e.g., "I. zivilrechtliche Abteilung") |
| `judges` | string | Panel composition |
| `clerks` | string | Court clerks (Gerichtsschreiber) |
| `collection` | string | Official collection reference |
| `appeal_info` | string | Appeal status / subsequent proceedings |

### Technical fields

| Field | Type | Description |
|-------|------|-------------|
| `docket_number_2` | string | Secondary docket number |
| `publication_date` | date | Date published online |
| `pdf_url` | string | Direct URL to PDF |
| `external_id` | string | Cross-reference ID |
| `scraped_at` | datetime | When this decision was scraped |
| `source` | string | Data source identifier |
| `source_id` | string | Source-specific ID (e.g. Signatur) |
| `source_spider` | string | Source spider/scraper name |
| `content_hash` | string | MD5 of full_text for deduplication |
| `has_full_text` | bool | Whether `full_text` is non-empty |
| `text_length` | int | Character count of `full_text` |

Full 34-field Parquet export schema: [`export_parquet.py`](export_parquet.py)

---

## Coverage

### Federal courts

| Court | Code | Decisions | Period | Source |
|-------|------|-----------|--------|--------|
| Federal Supreme Court (BGer) | `bger` | ~173,000 | 1996–present | bger.ch |
| BGE Leading Cases | `bge` | ~45,000 | 1954–present | bger.ch CLIR |
| Federal Administrative Court (BVGer) | `bvger` | ~91,000 | 2007–present | bvger.ch |
| Federal Admin. Practice (VPB) | `ch_vb` | ~23,000 | 1982–2016 | admin.ch |
| Federal Criminal Court (BStGer) | `bstger` | ~11,000 | 2004–present | bstger.weblaw.ch |
| EDÖB (Data Protection) | `edoeb` | ~1,200 | 1994–present | edoeb.admin.ch |
| FINMA | `finma` | ~1,200 | 2008–2024 | finma.ch |
| ECHR (Swiss cases, BGer-published) | `bge_egmr` | ~475 | 1974–present | bger.ch CLIR |
| ECHR Switzerland (HUDOC) | `hudoc_ch` | 834 | 1959–present | HUDOC API |
| ECtHR Chamber judgments | `ecthr_chamber` | 193 (growing) | 1959–present (FR; EN v2) | HUDOC API |
| ECtHR Committee judgments | `ecthr_committee` | 30 (growing) | | HUDOC API |
| ECtHR Grand Chamber | `ecthr_grand_chamber` | 13 (growing) | | HUDOC API |
| Militärkassationsgericht (MKG) | `mkg` | 1,244 | 1915–2025 | oa.admin.ch + alexandria.ch |
| Federal Patent Court (BPatGer) | `bpatger` | ~190 | 2012–present | bpatger.ch |
| Competition Commission (WEKO) | `weko` | ~120 | 2009–present | weko.admin.ch |
| Sports Tribunal | `ta_sst` | ~50 | 2024–present | ta-sst.ch |
| Federal Council | `ch_bundesrat` | ~15 | 2012–present | bj.admin.ch |

### Cantonal courts

77 courts across all 26 cantons. The largest cantonal collections:

| Canton | Courts | Decisions | Period |
|--------|--------|-----------|--------|
| Vaud (VD) | 3 | ~155,000 | 1984–present |
| Zürich (ZH) | 20 | ~126,000 | 1980–present |
| Genève (GE) | 1 | ~116,000 | 1993–present |
| Ticino (TI) | 1 | ~58,000 | 1995–present |
| St. Gallen (SG) | 7 | ~35,000 | 2001–present |
| Graubünden (GR) | 1 | ~29,000 | 2002–present |
| Basel-Landschaft (BL) | 1 | ~26,000 | 2000–present |
| Bern (BE) | 6 | ~26,000 | 2002–present |
| Aargau (AG) | 18 | ~21,000 | 1993–present |
| Basel-Stadt (BS) | 3 | ~19,000 | 2001–present |

All 26 cantons covered: AG, AI, AR, BE, BL, BS, FR, GE, GL, GR, JU, LU, NE, NW, OW, SG, SH, SO, SZ, TG, TI, UR, VD, VS, ZG, ZH.

Live per-court statistics: **[Dashboard](https://opencaselaw.ch)**

---

## How it works

```
                        ┌──────────────────────────────────────────────────────┐
                        │                    Daily Pipeline                    │
                        │                                                      │
Court websites ────────►│  Scrapers ──► JSONL ──┬──► Parquet ──► HuggingFace   │
  bger.ch               │  (45 scrapers,        │                              │
  bvger.ch              │   rate-limited,        ├──► FTS5 DB ───┐             │
  cantonal portals      │   resumable)           │               ├► MCP Server │
                        │                        └──► Graph DB ──┘             │
                        │                                          ▲           │
Fedlex (SPARQL) ───────►│  Fedlex scraper ──► XML ──► Statutes DB ─┘           │
                        │                                                      │
                        │  01:00 UTC  scrape        04:00 UTC  publish         │
                        └──────────────────────────────────────────────────────┘
```

### Step by step

1. **Scrape** (01:00 UTC daily) — 45 scrapers run in parallel, each targeting a specific court's website or API. Every scraper is rate-limited and resumable: it tracks which decisions it has already seen and only fetches new ones. Output: one JSONL file per court. A separate Fedlex scraper downloads federal law texts (Akoma Ntoso XML) via SPARQL for the statute database.

2. **Build search index** (04:00 UTC) — JSONL files are ingested into a SQLite FTS5 database for full-text search. On Mon–Sat, this runs in **incremental mode**: a byte-offset checkpoint tracks how far each JSONL file has been read, so only newly appended decisions are processed (typically < 1 minute). On Sundays, a **full rebuild** compacts the FTS5 index and resets the checkpoint (~3 hours). Decisions appearing in multiple sources are deduplicated by `decision_id`, keeping the version with the longest full text. A quality enrichment step fills in missing titles, regestes, and content hashes.

3. **Export** — JSONL files are converted to Parquet (one file per court) with a fixed 34-field schema.

4. **Upload** — Parquet files are pushed to HuggingFace. The MCP server and `datasets` library pick up the new data automatically. Optional artifact publishing can also update `artifacts/manifest.json` with daily deltas and a full compressed SQLite snapshot for external bootstrap tools.

5. **Update dashboard** — `stats.json` is regenerated (including scraper health status from the last run) and pushed to GitHub Pages.

---

## Running locally (developer)

For contributors and developers who want to run scrapers, build the pipeline, or modify the codebase.

### Prerequisites

- Python 3.10+
- pip

### Install

```bash
git clone https://github.com/jonashertner/opencaselaw.git
cd caselaw-repo-1
python3 -m venv .venv
source .venv/bin/activate   # macOS/Linux — on Windows: .venv\Scripts\Activate.ps1
pip install -e ".[all]"
```

This installs all dependencies including PDF parsing, crypto, and the FastAPI server. For a minimal install without optional dependencies, use `pip install -e .` instead.

### Scrape decisions

```bash
# Scrape 5 recent decisions from the Federal Supreme Court
python run_scraper.py bger --max 5 -v

# Scrape BVGer decisions since a specific date
python run_scraper.py bvger --since 2025-01-01 --max 20 -v

# Scrape a cantonal court
python run_scraper.py zh_gerichte --max 10 -v
```

Output is written to `output/decisions/{court}.jsonl` — one JSON object per line, one file per court. The scraper remembers what it has already fetched (state stored in `state/`), so you can run it repeatedly to get only new decisions.

45 court codes are available. Run `python run_scraper.py --list` for the full list, or see the [dashboard](https://opencaselaw.ch) for per-court statistics.

### Build a local search database

```bash
# Full build (reads all JSONL, optimizes FTS index — ~3h for 900K decisions)
python build_fts5.py --output output -v

# Incremental build (reads only new JSONL bytes, skips optimize — seconds)
python build_fts5.py --output output --incremental --no-optimize -v

# Full rebuild (deletes DB + checkpoint, rebuilds from scratch)
python build_fts5.py --output output --full-rebuild -v
```

This reads JSONL files from `output/decisions/` and builds a SQLite FTS5 database at `output/decisions.db`. A full build of 900K decisions takes about 3 hours and produces a ~58 GB database. Incremental mode uses a checkpoint file (`output/.fts5_checkpoint.json`) to skip unchanged files and seek past already-processed bytes, completing in seconds when few new decisions exist.

### Export to Parquet

```bash
python export_parquet.py --input output/decisions --output output/dataset -v
```

Converts JSONL files to Parquet format (one file per court). Output goes to `output/dataset/`.

---

## Data sources

| Source | What | How |
|--------|------|-----|
| **Official court websites** | Federal courts (bger.ch, bvger.ch, bstger.ch, bpatger.ch) | JSON APIs, structured HTML |
| **Federal regulatory bodies** | FINMA, WEKO, EDÖB, VPB | Sitecore/custom APIs |
| **Cantonal court portals** | 26 cantonal platforms (Weblaw, Tribuna, FindInfo, custom portals) | Court-specific scrapers |

Decisions appearing in multiple sources are deduplicated by `decision_id` (a deterministic hash of court code + normalized docket number). The version with the longest full text is kept.

---

## Legal basis

Court decisions are public records under Swiss law. Article 27 BGG requires the Federal Supreme Court to publish its decisions. The Bundesgericht has consistently held that court decisions must be made accessible to the public (BGE 133 I 106, BGE 139 I 129). This project scrapes only publicly available, officially published decisions.

## Governance and removal

Republication changes discoverability, so the project ships a governance policy covering source withdrawals, re-anonymization, and verified correction/removal requests. See [docs/governance-and-removal-policy.md](/Users/jonashertner/opencaselaw/docs/governance-and-removal-policy.md).

---

## Contributors

Maintainer: [Jonas Hertner](https://jonashertner.com). A small group of first users, bug reporters, institutional partners, and consumer-side integrators have shaped the tool; see [CONTRIBUTORS.md](CONTRIBUTORS.md) for the full list and how to add yours.

If you're using OpenCaseLaw in production — as a scraper contributor, dataset consumer, law-firm integrator, or research project — we'd like to know. Email **team@jonashertner.com** or open a [discussion](https://github.com/jonashertner/opencaselaw/discussions).

---

## License

Code: MIT. See [LICENSE](LICENSE).

Dataset packaging and added metadata: CC0-1.0 to the extent rights exist. The underlying decision texts remain official published court decisions sourced from the originating courts and public bodies.

---

## Contact

Questions, feedback, or ideas? Reach out at **team@jonashertner.com**.

You can also [open an issue](https://github.com/jonashertner/opencaselaw/issues) on GitHub.
