---
license: cc0-1.0
language:
  - de
  - fr
  - it
tags:
  - legal
  - swiss-law
  - case-law
  - court-decisions
  - nlp
  - full-text
  - citation-graph
  - mcp
  - multilingual
pretty_name: Swiss Case Law
authors:
  - Jonas Hertner
size_categories:
  - 1M<n<10M
task_categories:
  - text-classification
  - summarization
  - question-answering
configs:
  - config_name: default
    data_files:
      - split: train
        path: data/*.parquet
---

# Swiss Case Law Dataset

**1,050,000+ published decision records (~909,000 unique decisions) from Swiss federal, cantonal, and regulatory bodies.**

*Figures as of 2026-07-24 — refreshed daily; live counts at [opencaselaw.ch](https://opencaselaw.ch).*

Full text, structured metadata, extracted case-citation references, and daily updates. The dataset contains German, French, and Italian decisions; the export schema also reserves `rm` for Romansh.

[![Dashboard](https://img.shields.io/badge/Dashboard-live-d1242f)](https://opencaselaw.ch)
[![GitHub](https://img.shields.io/badge/GitHub-source-black)](https://github.com/jonashertner/opencaselaw)
[![LinkedIn](https://img.shields.io/badge/LinkedIn-opencaselaw--ch-0A66C2)](https://www.linkedin.com/company/opencaselaw-ch/)
[![MCP Server](https://img.shields.io/badge/MCP-live-blue)](https://mcp.opencaselaw.ch/health)
[![Data License: CC0--1.0](https://img.shields.io/badge/Data_License-CC0--1.0-blue.svg)](https://creativecommons.org/publicdomain/zero/1.0/)
[![Code License: MIT](https://img.shields.io/badge/Code_License-MIT-green.svg)](https://github.com/jonashertner/opencaselaw/blob/main/LICENSE)

## Dataset Summary

The largest open collection of Swiss court decisions: 1,050,000+ decision records from 118 federal, cantonal, regulatory, and international courts, scraped from official publication channels. These records represent ~909,000 unique decisions: the Romandie portals (GE, VD) and a few others publish one ruling under two identifiers (procedure number + decision number), so ~141,000 rows are cross-identifier duplicate representations, which we retain and link rather than delete. New decisions are added every night.

- **20+ federal courts and bodies**: BGer, BVGer, BStGer, BPatGer, BGE, FINMA, WEKO, EDÖB, MKG (Militärkassationsgericht), VPB, Sports Tribunal, and more
- **80+ cantonal courts** across all 26 cantons
- **ECHR/EGMR**: 847 Swiss-respondent judgments (HUDOC) + every ECtHR Chamber and Grand Chamber judgment of importance 1–3 against any respondent state (8,275 judgments, French or English). Not part of this CC0 dedication — see the note below.
- **Current decision languages**: German (463,012; 46.7%), French (446,869; 45.1%), Italian (81,796; 8.2%); the export schema also reserves `rm`, and `en` for the ECtHR judgments whose only authoritative text is English (those rows are excluded from this export)
- **Temporal range**: 1875–present (BGE historical vol. 1 from 1875)
- **10 million extracted case-citation references** (9.65 million resolved, with confidence scores)
- **12.4 million statute-decision links** in `graph/statute_references.parquet` (e.g., which decisions cite Art. 41 OR)
- **34 structured fields** per decision in Parquet; 27 in the FTS5 search index

**What this Parquet dataset contains:** the decisions corpus (`data/`), the citation and statute-reference graph (`graph/`), and structured decision sections (`structure/`).

**Not included as bulk Parquet — served live via the [MCP API](https://mcp.opencaselaw.ch) and [dashboard](https://opencaselaw.ch):** the full **law texts** and **legislative materials**. These are queried in real time (`get_law`, `search_laws`, `get_legislation`, `get_materialien`) and sourced from Fedlex SPARQL (5,525 federal laws / 132,586 articles in DE/FR/IT), direct cantonal-portal scraping with LexFind fallback (15,600 cantonal laws / 353,464 articles), and a verbatim Botschaft/Materialien corpus (references for ~33,000 statute articles, digests for BV and BGFA, and Amtliches-Bulletin debate transcripts for the BV). The statute-decision *links* above are included in the download; the article *texts* are not.

## Quick Start

### Load with HuggingFace datasets

```python
from datasets import load_dataset

# Load all courts
ds = load_dataset("voilaj/swiss-caselaw")

# Load a single court
bger = load_dataset("voilaj/swiss-caselaw", data_files="data/bger.parquet")
```

### Load with pandas

```python
import pandas as pd

df = pd.read_parquet("hf://datasets/voilaj/swiss-caselaw/data/bger.parquet")
df_recent = df[df["decision_date"] >= "2024-01-01"]
print(f"{len(df_recent)} decisions since 2024")

# Filter by language
df_french = df[df["language"] == "fr"]

# Group by legal area
df.groupby("legal_area").size().sort_values(ascending=False).head(10)
```

### Direct download

Every court is a single Parquet file:

```
https://huggingface.co/datasets/voilaj/swiss-caselaw/resolve/main/data/bger.parquet
https://huggingface.co/datasets/voilaj/swiss-caselaw/resolve/main/data/bvger.parquet
https://huggingface.co/datasets/voilaj/swiss-caselaw/resolve/main/data/zh_gerichte.parquet
```

Full list: [huggingface.co/datasets/voilaj/swiss-caselaw/tree/main/data](https://huggingface.co/datasets/voilaj/swiss-caselaw/tree/main/data)

### REST API (no setup)

Query via the HuggingFace Datasets Server — no installation required:

```bash
# Get rows
curl "https://datasets-server.huggingface.co/rows?dataset=voilaj/swiss-caselaw&config=default&split=train&offset=0&length=5"

# Dataset info
curl "https://datasets-server.huggingface.co/info?dataset=voilaj/swiss-caselaw"
```

### Command-line client (scripts and agents)

`ocl` is a dependency-free Python client over the same public API, made for two jobs: checking the citations in a draft (each reference comes back resolved, missing or ambiguous, with pinpoints verified against the structure index) and saving the evidence behind a memo into a folder with the full texts, the requested Erwägungen, statute articles, a plain-language `INDEX.md` and a hashed `manifest.json`. Install with `pipx install opencaselaw-cli` (or `uv tool install opencaselaw-cli`); guide: [docs/research-cli.md](https://github.com/jonashertner/opencaselaw/blob/main/docs/research-cli.md).

### Full-text search via MCP

Connect the dataset to Claude, ChatGPT, Cursor, Gemini, Grok, or any MCP client for natural-language search over all 1,050,000+ decisions, statute lookup, citation graph traversal, legislative history, and more. The MCP server exposes **44 tools total — 42 in remote (public) mode**; the 2 local-only `update_database` / `check_update_status` tools are hidden when REMOTE_MODE=True. Tools include verbatim head-note retrieval (`get_regeste`), structured Erwägung-paragraph access (`get_erwaegung`), full decision-structure decomposition (`get_decision_structure`), and a closed-corpus citation-integrity toolkit (`cite`, `check_claim_support`, `attest_response`) that audits every reference, statute, quotation, decision date, and — opt-in — proposition-grounding before an answer ships. The architecture defends against the two empirically-measured legal-LLM failure classes: **hallucination** (Dahl, Magesh, Suzgun & Ho, *Large Legal Fictions*, Journal of Legal Analysis 16(1) 2024 — 58–88 % of legal queries to general-purpose LLMs produced fabricated authority, range across ChatGPT-4 to Llama-2; the follow-up Magesh et al. *Hallucination-Free?* study, Journal of Empirical Legal Studies 22 (2025), measured 17–33 % on commercial legal-RAG tools) and **reasoning error** (Butler & Butler, *Legal RAG Bench*, arXiv:2603.01710, 2026 — citation real, source retrieved, proposition unsupported).

**Remote (no download needed):**

```bash
# Claude Code
claude mcp add swiss-caselaw --transport sse https://mcp.opencaselaw.ch

# Claude Desktop: Settings → Connectors → Add custom connector → https://mcp.opencaselaw.ch

# ChatGPT: Settings → Apps → Developer mode → Create app → https://mcp.opencaselaw.ch/sse (auth: None)
# Recommended with GPT-5.3

# Gemini CLI: add to ~/.gemini/settings.json
# { "mcpServers": { "swiss-caselaw": { "url": "https://mcp.opencaselaw.ch" } } }
```

Search results include enriched metadata: court name (human-readable), court level, legal area, statute articles cited, citation count, and leading-case flag.

**Local (offline access, ~65 GB disk):**

```bash
git clone https://github.com/jonashertner/opencaselaw.git
cd caselaw-repo-1
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\Activate.ps1
pip install mcp pydantic huggingface-hub pyarrow
claude mcp add swiss-caselaw -- /path/to/.venv/bin/python3 /path/to/mcp_server.py
# Windows: use .venv\Scripts\python.exe instead
```

On first search, the server downloads the Parquet files (~7 GB) from this dataset and builds a local SQLite FTS5 index (~58 GB). This takes 30–60 minutes and only happens once. After that, searches are instant.

### Artifact manifest and SQLite snapshot

The dataset may include machine-readable artifact metadata at `artifacts/manifest.json`. The manifest lists daily delta artifacts and may point to an optional full compressed SQLite base snapshot under `snapshot.sqlite_zst`, for example `artifacts/sqlite/snapshots/<date>.decisions.sqlite.zst`.

This snapshot is intended for local MCP/server bootstrap tools that want to avoid rebuilding the SQLite FTS5 database from Parquet. Consumers should verify the advertised SHA-256, decompress to a temporary database path, run a quick SQLite row/schema check, then atomically move it into place. If `snapshot` is `null`, use the Parquet rebuild path.

## Dataset Statistics

| Metric | Value |
|--------|-------|
| Total decision records | 1,050,000+ (live count, updated daily) |
| Unique decisions | ~909,000 (after cross-identifier linkage; ~141,000 records are duplicate representations, retained and linked) |
| Courts | 118 |
| Temporal range | 1875–present |
| Average decision length | 22,775 characters |
| Full text coverage | 100% |
| Regeste (headnote) coverage | 38.7% |
| Case-citation references | 9.65 million resolved (10 million extracted) |
| Statute-decision links | 12.4 million |
| Federal laws indexed | 5,525 (400,405 articles total / ~133,359 per DE/FR/IT) |
| Cantonal laws indexed | 15,600 (361,430 articles, direct-scraped + LexFind) |
| Laws with Botschaft refs | 2,615 (33,465 articles) |
| Verbatim Botschaft corpus | 5,900+ documents / ~410K FTS5-indexed paragraphs (Phase 2, scaling) |
| Legislation texts searchable | 33,000+ (federal + cantonal + intercantonal) |
| Scholarly commentaries | 1,100+ (OnlineKommentar.ch + OpenLegalCommentary.ch) |
| MCP tools | 44 total (42 remote in public mode + 2 local-only) |

> Rows from *Federal laws indexed* downward (laws, Botschaft/Materialien, legislation texts, commentaries) are **platform figures served live via the MCP API**, not contents of this Parquet download. The Parquet dataset comprises `data/` (decisions), `graph/` (citations + statute-decision links), and `structure/`.

**Language distribution:**

| Language | Count | Share |
|----------|-------|-------|
| German (de) | 463,012 | 46.7% |
| French (fr) | 446,869 | 45.1% |
| Italian (it) | 81,796 | 8.2% |

**Reference graph:** 9.65 million resolved citation edges (10 million extracted) and 12.4 million statute-to-decision links. The most-cited decision is BGE 125 V 351 with 85,108 incoming citations.

**Search benchmark (frozen offline baseline):** `benchmarks/search_benchmark_2026-03-19_offline_full.json` records a 100-query run against a 1,078,177-row local `decisions.db`, with MRR@10 = 0.4697, Recall@10 = 0.4958, nDCG@10 = 0.5250, and Hit@1 = 0.33. This is a reproducible offline baseline, not a fully provisioned hosted-system score.

## Intended Uses

- **Legal research and case law analysis**: full-text search and citation network analysis across the Swiss court system
- **NLP research on multilingual legal text**: classification, summarization, named entity recognition, and cross-lingual tasks on German/French/Italian legal corpora
- **Legal tech development**: building search engines, citation analysis tools, and document drafting assistants grounded in Swiss jurisprudence
- **Academic study of Swiss jurisprudence**: tracking doctrinal evolution, identifying leading cases, analyzing court output over time

**Not intended for**: automated legal advice or replacing professional legal counsel. This dataset is a research and analysis resource, not a substitute for qualified legal representation.

## Limitations

- **Temporal coverage varies by court**: federal courts from 1996, some cantonal courts from 2000+; historical BGE volumes from 1875
- **Historical OCR artifacts**: BGE decisions from volumes 1–79 (1875–1953) were digitized from print and may contain OCR errors
- **Publication delays**: some cantonal courts have irregular publication schedules; decisions may appear weeks after being rendered
- **Language distribution is unbalanced by design**: it reflects actual court output (German and French cantons are larger), not balanced sampling
- **Anonymization varies by court**: most federal decisions are anonymized; some cantonal decisions may contain personal names or details
- **~1.9% short-text decisions**: some decisions are PDF-only publications where text extraction produced fewer than 500 characters; full text may be available at the source URL

## Dataset Creation

**Collection**: 59 automated scrapers target official court websites, APIs, and publication portals (Weblaw, Tribuna, FindInfo, Omnis, and direct court APIs). Each scraper is rate-limited and resumable — it tracks already-seen decisions and fetches only new ones.

**Deduplication**: `decision_id` is a deterministic hash of court code + normalized docket number. Decisions appearing across multiple sources are grouped and the version with the longest full text is kept. Cross-court overlap groups cover courts whose decisions are published on multiple portals (ZH: 17 sub-courts, AG: 18, VD: 3, BS: 3, BE: 2).

**Quality control**: content hashing (MD5 of full text) detects duplicate text; stub removal discards entries with fewer than 10 characters in both full text and regeste; text length validation flags suspicious entries.

**Pipeline**: scrapers run daily at 01:00 UTC; the publish pipeline starts at 03:30 UTC and rebuilds the FTS5 index, the reference graph, the Parquet export, and uploads to HuggingFace. Every run is a full rebuild with atomic swap (zero-downtime); a typical run takes 4–6 h. The "Notable" landing-page factoids refresh weekly on Sunday at 04:30 UTC; tunnel-dependent cantonal scrapers (JU, NE) retry at 10:00 UTC after the local SOCKS reverse-tunnel comes back up.

## Schema

The Parquet files use a 34-field schema. The 24 columns available in the FTS5 search index are listed below.

| # | Field | Type | Description |
|---|-------|------|-------------|
| 1 | `decision_id` | string | Unique ID: `{court}_{docket_normalized}` |
| 2 | `court` | string | Court code (e.g., `bger`, `zh_obergericht`) |
| 3 | `canton` | string | `CH` for federal, two-letter canton code otherwise |
| 4 | `chamber` | string | Chamber / Abteilung |
| 5 | `docket_number` | string | Original docket number (e.g., `6B_1234/2025`) |
| 6 | `decision_date` | string | ISO date of decision |
| 7 | `publication_date` | string | Date published online |
| 8 | `language` | string | Language code: `de`, `fr`, `it`, `rm` |
| 9 | `title` | string | Subject / Gegenstand |
| 10 | `legal_area` | string | Rechtsgebiet / Domaine juridique |
| 11 | `regeste` | string | Headnote / Regeste (present in 54.3% of decisions) |
| 12 | `full_text` | string | Complete decision text |
| 13 | `decision_type` | string | Urteil, Beschluss, Verfügung, etc. |
| 14 | `outcome` | string | Decision outcome (Gutheissung, Abweisung, ...) |
| 15 | `source_url` | string | Permanent URL to the original |
| 16 | `pdf_url` | string | Direct PDF link |
| 17 | `cited_decisions` | string | JSON array of cited decision references |
| 18 | `scraped_at` | string | Scrape timestamp |
| 19 | `source` | string | Data source identifier |
| 20 | `source_id` | string | Source-specific ID (e.g., Signatur) |
| 21 | `source_spider` | string | Name of the scraper that collected this decision |
| 22 | `content_hash` | string | MD5 hash of full_text for deduplication |
| 23 | `json_data` | string | Complete 34-field record as JSON |
| 24 | `canonical_key` | string | Normalized key for cross-source deduplication |

Full 34-field Parquet export schema: [`export_parquet.py`](https://github.com/jonashertner/opencaselaw/blob/main/export_parquet.py)

## Court Coverage

### Federal Courts (20)

| Court | Code | Decisions | Period |
|-------|------|-----------|--------|
| Federal Supreme Court (BGer) | `bger` | ~174,000 | 1996–present |
| Federal Administrative Court (BVGer) | `bvger` | ~91,500 | 2007–present |
| BGE Leading Cases | `bge` | ~21,200 | 1954–present |
| BGE Historical (vol. 1–79) | `bge_historical` | ~14,600 | 1875–1953 |
| Federal Admin. Practice (VPB) | `ch_vb` | ~22,900 | 1982–2016 |
| Federal Criminal Court (BStGer) | `bstger` | ~11,400 | 2004–present |
| EDÖB (Data Protection) | `edoeb` | ~1,800 | 1994–present |
| FINMA | `finma` | ~405 | 2008–present |
| Militärkassationsgericht (MKG) | `mkg` | 1,244 | 1915–2025 |
| Federal Patent Court (BPatGer) | `bpatger` | ~189 | 2012–present |
| Competition Commission (WEKO) | `weko` | ~256 | 2009–present |
| Sports Tribunal | `ta_sst` | ~49 | 2024–present |

> **Not included: European Court of Human Rights decisions.** The corpus behind
> `mcp.opencaselaw.ch` also carries ~9,600 ECtHR decisions (`ecthr_chamber`,
> `ecthr_committee`, `ecthr_grand_chamber`, `hudoc_ch`, `bge_egmr`). That text is
> © ECHR-CEDH, reproduced from HUDOC under the Court's own reuse terms
> (<https://www.echr.coe.int/copyright-and-disclaimer>), and is therefore **not
> part of this CC0 dedication** — CC0 is a rights waiver, and these are not our
> rights to waive. The material remains freely searchable and retrievable through
> the public API and MCP server, with attribution.

### Cantonal Courts (26 cantons, 110 courts)

| Canton | Courts | Decisions | Period |
|--------|--------|-----------|--------|
| Genève (GE) | 1 | ~167,000 | 1993–present |
| Vaud (VD) | 3 | ~155,000 | 1984–present |
| Zürich (ZH) | 21 | ~81,000 | 1980–present |
| Ticino (TI) | 1 | ~59,000 | 1995–present |
| Bern (BE) | 6 | ~20,000 | 2002–present |
| Basel-Landschaft (BL) | 1 | ~17,000 | 2000–present |
| Graubünden (GR) | 1 | ~14,400 | 2002–present |
| Fribourg (FR) | 1 | ~14,100 | 2007–present |
| St. Gallen (SG) | 7 | ~13,100 | 2001–present |
| Aargau (AG) | 17 | ~11,800 | 1993–present |
| Basel-Stadt (BS) | 3 | ~10,100 | 2001–present |

All 26 cantons covered: AG, AI, AR, BE, BL, BS, FR, GE, GL, GR, JU, LU, NE, NW, OW, SG, SH, SO, SZ, TG, TI, UR, VD, VS, ZG, ZH.

Live per-court statistics: **[Dashboard](https://opencaselaw.ch)**

## Data Sources

**Court decisions** — source-specific scrapers targeting official court platforms directly (federal court APIs, Weblaw, Tribuna, FindInfo, Omnis, plus custom portals for the smaller cantons). Federation includes federal courts (BGer, BVGer, BStGer, BPatGer), every Swiss cantonal court system, the Swiss-relevant European Court of Human Rights subset, and the federal regulators (FINMA, WEKO, EDÖB, UBI, ELCom, PostCom, ComCom). No third-party aggregator in the case-law pipeline; we go to the source.

**Federal legislation** — Fedlex SPARQL endpoint (Bundeskanzlei). Mirrored monthly into `statutes.db`; covers every consolidated federal act in DE/FR/IT.

**Cantonal legislation** — dual-source pipeline. **19 cantons** are scraped directly from their official Gesetzessammlungen (LexWork + SIL platforms — the same publishing systems the cantons themselves operate), parsed natively as HTML for clean article-level data. The **remaining 7 cantons** fall back to PDF extraction via [LexFind.ch](https://www.lexfind.ch). Combined into `cantonal_laws.db` (15,600 laws / 353,464 articles) and federated with `statutes.db` via SQLite FTS5. The live LexFind API also serves as a real-time fallback for SR numbers not yet in the local mirror, and as the discovery catalog for the broader `search_legislation` tool which spans 33,000+ legislation texts including ordinances and intercantonal agreements.

Decisions appearing in multiple sources are deduplicated by `decision_id` (a deterministic hash of court code + normalized docket number). The version with the longest full text is kept.

## Update Frequency

The dataset is updated daily via automated pipeline. New decisions are scraped, deduplicated, exported to Parquet, and uploaded to HuggingFace.

## Legal Basis

This dataset contains only publicly available, officially published decisions. Under Swiss law, published judicial decisions are official works; OpenCaseLaw republishes those source texts and links every record back to the originating court or public body.

## License

The **code** for OpenCaseLaw is released under the MIT license.

The **dataset packaging and added metadata** are dedicated under **CC0-1.0**, to the extent any copyright or database rights exist in those additions. The underlying decision texts remain official published court decisions sourced from the originating courts or public bodies.

See the governance policy for source withdrawals, re-anonymization, and verified correction/removal requests: [`docs/governance-and-removal-policy.md`](https://github.com/jonashertner/opencaselaw/blob/main/docs/governance-and-removal-policy.md).

## Citation

```bibtex
@dataset{swiss_caselaw_2026,
  title={Swiss Case Law Dataset: 1,050,000+ Court Decision Records (~909,000 Unique) with Reference Graph and ECtHR Coverage},
  author={Jonas Hertner},
  year={2026},
  url={https://huggingface.co/datasets/voilaj/swiss-caselaw},
  note={1,050,000+ Swiss federal, cantonal, and regulatory decision records (~909,000 unique) with full text,
        structured metadata, 9.65M resolved citation edges, 12.4M statute links,
        5,525 federal laws, 15,600 cantonal laws,
        and legislative history (83,958 Botschaft amendment references; 459
        verbatim Botschaften, scaling).
        Searchable via 44 MCP tools (42 remote in public mode + 2 local-only) from Claude, ChatGPT, Cursor, Gemini, Grok, or any MCP/function-calling client. Updated daily.}
}
```

## Links

- **Website**: [opencaselaw.ch](https://opencaselaw.ch) — live coverage statistics and dashboard
- **GitHub**: [github.com/jonashertner/opencaselaw](https://github.com/jonashertner/opencaselaw) — source code, scrapers, pipeline
- **MCP Server**: [setup guide](https://github.com/jonashertner/opencaselaw#1-search-with-ai) — full-text search for Claude Code, Claude Desktop, ChatGPT, and Gemini
