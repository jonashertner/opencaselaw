# Canonical numbers — single source of truth

This document fixes the canonical values for every headline statistic that
appears in any externally-visible OpenCaseLaw surface (paper, dashboards,
README, HuggingFace dataset card, methodology page). When you update any
of these, update the source of truth listed under "Where the value lives"
and every consumer should follow.

When values appear in code, prefer to read from the source-of-truth file
at runtime rather than hard-coding. When that's not possible (HTML, .tex),
update all listed consumers in the same commit.

## Snapshot vs. live distinction

| Concept | Use when | Examples |
|---|---|---|
| **Paper snapshot** | Anything in the v3 paper or `docs/paper/v3/tables/*.json` | "971,992 decisions, snapshot 2026-05-13" |
| **Live** | Dashboards / README / dataset card | "1,040,000+ records (900,000+ unique)" (rounded down; grows daily) |

## The numbers

### Corpus scale

| Quantity | Snapshot value (paper) | Live value | Source of truth | Consumers |
|---|---:|---|---|---|
| Decision records | **971,992** | **~1.05M** (1,047,586 on 2026-11-07) | `docs/paper/v3/tables/corpus_graph_stats.json::total_decisions` (live: `decisions.db` COUNT) | paper §3 abstract, dataset_card.md, README.md, methodology.html, index.html |
| **Unique decisions** (live only) | — | **900k+** (906,811 on 2026-11-07) | `output/representation_manifest.db` (live: `stats.json::unique_decisions`) | dataset_card.md, README.md, index.html, llms.txt |
| Courts | 108 | 137 | same JSON (`stats.json::court_count`) (live: `decisions.db`) | all |
| Cantons | 26 | 26 | constant | all |
| Languages | DE 449,575 (46.3 %), FR 441,158 (45.4 %), IT 80,704 (8.3 %) | same | corpus_overview.tex | paper §3, methodology.html |
| Date range | 1875–2026 | 1875–2026 | constant | all |
| `ch_vb` rows | — | **22,916** = 2,502 VPB/JAAC + 20,414 restored pages | `stats.json::by_court` (split: see below) | README.md, dataset_card.md, coverage_notes.json, coverage page, stats page, mcp_server.py instructions |
| Snapshot date (paper) | **2026-05-13** | — | `corpus_graph_stats.json::snapshot_date` | paper, methodology.html date references |

**Record vs. unique (cross-identifier dual-count):** the live corpus is a RECORD
count (`decisions.db` rows). Romandie portals (GE, VD) and a few smaller sources
(ch_vb, nw, edoeb, ur) carry one document under two identifiers, so ~141k rows are
duplicate representations that we retain and link (never delete). Live figures:
**1,040,000+ records → 900,000+ unique decisions** (`stats.json::unique_decisions`,
generation-tracked via `representation_manifest.db`). Public surfaces state both;
the paper snapshot predates this and reports the record count only. See
`docs/superpowers/specs/2026-07-15-cross-identifier-representations-design.md`.

**`ch_vb` is two sets, always named apart.** The VPB/JAAC series (2,502 rulings and
legal opinions, 1987–2017, cited as `VPB <number>`) and the Bundesblatt, Amtliches
Bulletin and Amtliche Sammlung pages restored on 2026-10-31 (shard census 12,261
BBl, 7,091 AB, 833 AS, 238 small series = 20,423 rows; 20,414 after the build).
The restored pages are not rulings: dates unreliable, all tagged `language=de` though
many are French or Italian, cited as `Bundesverwaltung <docket>`. Never write ~23k
VPB, and never say this material is not in the corpus. The swap took the total
from 1,053,732 to 1,074,228, and these pages count in records and unique decisions
alike. Live 2026-11-01: 1,074,262 records, 933,478 unique. The 2026-11-07 build folded
22,618 entscheidsuche date twins and 517 cantonal copies of BGer rulings into their
base rows (3b67068b; old ids answer through `decision_id_aliases`): 1,047,586 records,
906,811 unique.

### Citation graph

| Quantity | Value | Source | Consumers |
|---|---:|---|---|
| Citation edges extracted (paper snapshot) | 8,649,879 | `corpus_graph_stats.json::rg_citation_edges` | paper §4 |
| Resolved edges (paper snapshot) | 8,089,112 (93.5 %) | same | paper |
| **Resolved citation edges (live)** | **~9.84M** | `reference_graph.db::decision_citations` (= `stats.json::citation_edges`) | README, dataset_card, dashboards |
| **Extracted citations (live)** | **~10M** | `reference_graph.db::citation_targets` | README, dataset_card, dashboards |
| Cited ≥ 100 times | 10,874 | `in_degree_buckets` | paper Table 2 |
| Cited ≥ 1,000 times | 999 | same | paper |
| Cited ≥ 10,000 times | **47** | same | paper §4, citation_graph.tex |
| Top-cited | BGE 125 V 351, 85,108 incoming | `top30_cited[0]` | paper, methodology |
| Cross-lingual share | 34.0 % | computed from cross_lang_matrix | paper §4 |

### Statute graph

| Quantity | Value | Source | Consumers |
|---|---:|---|---|
| Decision–statute edges (paper snapshot) | 11,261,717 | `corpus_graph_stats.json::rg_statute_edges` | paper |
| **Decision–statute edges (live)** | **~12.4M** | `reference_graph.db::decision_statutes` | README, dataset_card, dashboards |
| Distinct provisions | 283,119 | `rg_distinct_statutes` | all |
| Federal SR laws | 5,546 | live (Fedlex; grows) | all |
| Federal articles | 403,181 (DE 134,188 / FR 134,826 / IT 134,167) | live (Fedlex; grows) | all |
| Cantonal laws | 16,400 | `cantonal_laws.db` | all |
| Cantonal articles | 381,725 | same | all |
| Direct portal coverage | **23 cantons** (LexWork 19 + SIL 2 + ZH 1 + TI 1) | `cantonal_laws.db.laws.text_source` | paper §3, README, methodology |
| LexFind PDF supplements | **4 cantons**: JU, SZ, VD whole, ZH for the acts its portal lacks | same | same |
| Top provision | BGG/LTF Art. 100, 205,725 decisions | `top_statutes_canonical.json` | paper §4 |

### Materialien

| Quantity | Value | Source | Consumers |
|---|---:|---|---|
| Botschaft documents (paper snapshot) | 5,292 | `corpus_graph_stats.json` | paper §4 |
| Bundesblatt documents (live, DE/FR/IT language versions) | **28,100+** (16,800+ works; 14,600+ Botschaften, the rest Berichte and Stellungnahmen des Bundesrates, Kommissionsberichte, Erlassentwürfe, Vernehmlassungs-Eröffnungen) | `materialien.db::botschaft_documents` (`stats.json::materialien_coverage`) | dataset_card, README, index.html register, stats page, mcp_server.py instructions |
| Bundesblatt coverage (live) | typed 1999– complete against Fedlex; 1850–1999 being added nightly (DE back to 1935 on 2026-10-21) | `logs/materialien_completeness.json` | dataset_card, index.html register |
| Paragraphs (paper snapshot) | 381,711 | same | paper |
| Paragraphs (live) | **~2.88M** | `materialien.db::botschaft_paragraphs` | dataset_card, README |
| Article links (live) | **28,100+** across 785 acts | `materialien.db::article_botschaft_links` | dataset_card, README |
| Vernehmlassungen (live) | **2,694 procedures** (2,582 = every Fedlex consultation since 1993, + 112 from the FOJ legislative projects and the Chancellery's 1995–2004 index) | `consultations.db::consultation_procedures` (`stats.json::consultation_coverage`) | dataset_card, README, index.html register, stats page, mcp_server.py instructions |
| Vernehmlassung documents searchable (live) | **10,296** (2,630 Erläuternde Berichte, 3,340 Vorlagen, 1,995 Ergebnisberichte, 1,331 Stellungnahmen collections, 243 synoptische Tabellen, 757 other) / ~10.8M paragraphs | same | dataset_card, README, index.html register |
| Vernehmlassungseingaben (caveat) | on Fedlex systematically only since 2016; earlier ones only where the FOJ published them | census 2026-10-21 | dataset_card, README |
| Article-anchored links | 8,124 | same | paper |

### Commentaries

| Quantity | Value | Source |
|---|---:|---|
| Total commentaries (live) | 1,100+ (~1,131; OnlineKommentar + OpenLegalCommentary) | `ok_commentaries.db` |
| Paper citation | 362 (OnlineKommentar only) | paper §4 simplifies to the CC-BY count |

### MCP tool counts

**These are the most-drifted numbers historically. Pick the right one for context.**

| Mode | Tools | Where used |
|---|---:|---|
| **Public / remote mode** (deployed at `mcp.opencaselaw.ch`) | **42** | public-facing dashboards, live API |
| **Local mode** (running on operator's machine with `update_database` + `check_update_status`) | **44** | README internal docs, local dev guides |
| **Local-only delta** | 2 (`update_database`, `check_update_status`) | technical references |

Standard phrasing: *"44 MCP tools (42 remote in public mode + 2 local-only)"* or simply *"42 tools"* when only the public surface matters.

**Verified 2026-08-10** by two independent methods that must agree before this
number is changed: (a) `grep -c "Tool(" mcp_server.py` = 44 registrations;
(b) a live `tools/list` against https://mcp.opencaselaw.ch/mcp = 42 remote
tools. The difference is exactly `update_database` + `check_update_status`,
hidden under REMOTE_MODE. Any file quoting a tool count must cite this
section; an external review on 2026-08-10 found 24/38/40/41/43/44 in
circulation simultaneously, which is why the two-method check exists.

**Paper note**: paper v3 (snapshot 2026-05-13) cites 33 tools; that figure is frozen for the snapshot. Public surface has since grown by 6 scholarship tools (`search_scholarship`, `get_scholarship`, `find_scholarship_citing_statute`, `find_scholarship_citing_decision`, `list_scholarship_sources`, `get_scholarship_full_text`) plus the 2 ChatGPT Deep-Research tools (`search`, `fetch`) — bringing the live public count to 41.

### Search-quality numbers

**Distinguish two different benchmarks** — these are easy to confuse.

| Benchmark | Snapshot date | MRR@10 | Hit@10 | n queries | Where reported |
|---|---|---:|---:|---:|---|
| Online golden-set (with Haiku rerank) | 2026-03-19 | 0.647 | 0.570 (Hit@1) | 100 | methodology.html |
| Offline golden-set (no LLM) | 2026-03-19 | 0.470 | 0.330 (Hit@1) | 100 | baseline_by_language.tex |
| **Cross-lingual diagnostic (regeste-derived)** | 2026-05-13 | **0.630** | **0.833** | 150 (50 × 3 langs) | paper §1 §7, abstract |

The paper's headline is the cross-lingual 0.630/0.833 number. The methodology page's 0.647 is the older online golden-set bench (different design).

### Audit / hallucination bench (v1.1)

| Condition | n | Correctness | Groundedness | Retrieval acc. | correct/hallucination/retrieval/reasoning |
|---|---:|---:|---:|---:|---|
| Prior-only | 30 | 86.7 % | — | — | 26 / 4 / — / — |
| Retrieval-augmented | 30 | 63.3 % | 93.3 % | 53.3 % | 17 / 2 / 9 / 2 |

Source: `benchmarks/swiss_legal_rag_bench/results/v1_1_*.json`. Reported in paper §8 and the new `tables/audit_bench_results.tex`.

### Precision proxies (citation graph)

| Stratum | n | Date-sanity pass | Self-cite | Confidence p50 |
|---|---:|---:|---:|---:|
| docket_norm | 3,286,234 | 97.25 % | 0 | 0.99 |
| bge_bare | 2,943,028 | 99.75 % | 0 | 0.85 |
| bge_norm | 1,215,768 | 99.26 % | 0 | 0.75 |
| bge_pincite | 652,508 | 97.35 % | 0 | 0.75 |
| **Overall** | **8,097,538** | **98.47 %** | 0 | — |

Source: `benchmarks/citation_precision_proxies.json`. Reported in paper §4 and `tables/precision_proxies.tex`.

### Literature comparators (citing other people's numbers — be exact)

| Paper | Range we cite | Verified against |
|---|---|---|
| Dahl et al. 2024 | **58–88 %** (ChatGPT-4 floor to Llama-2 ceiling) | arXiv:2401.01301 abstract |
| Magesh et al. 2024/2025 | **17–33 %** (Lexis+ AI, Westlaw AI, Ask Practical Law) | JELS 22 (2025) abstract |

Common error to avoid: "58–82 %" for Dahl is WRONG. Was in v1.0 paper and dataset_card; fixed 2026-05-18.

### LLM cost (30-day, indicative)

| Period | Total | Notes |
|---|---:|---|
| Last 30 days | ~$87 | Haiku 4.5: ~$76 (search_rerank + query_parse + query_expansion); Sonnet 4.6: ~$11 (audit + reflect) |
| Daily typical | $4–8 | Bursts to $30+ on high-traffic days |

Source: `logs/llm_usage.jsonl`. Don't quote these numbers in the paper — they're operational, not scientific.

### Active cohorts (privacy-preserving, weekly)

| Channel | Weekly cohorts (max-daily HLL, last full week) | Notes |
|---|---:|---|
| browser_chrome | ~14,000 | dominated by SEO traffic |
| chatgpt | ~500 | |
| python_script | ~80 | |
| claude_other (CLI + desktop) | ~75 | |
| claude_desktop (explicit) | ~20 | |
| cursor | ~5 | |
| word_addin | ~5 | matches ~10 Pro licenses |

Source: `analytics.db::weekly_reach` (after 2026-05-18 cohort-derivation upgrade). Updated weekly.

## Process: how to update a canonical number

1. Update the source of truth (the JSON file or DB).
2. Re-run `make paper-tables` so `tables/*.tex` regenerate.
3. Grep this doc + every consumer for the old number; update them.
4. Bump the "Last verified" line below.

**Last verified:** 2026-11-07 (headline sweep after the duplicate fold, vs stats.json: 1,047,586 records -> "1,040,000+", 906,811 unique -> "900,000+", courts 137, languages DE 482,737 / FR 474,882 / IT 83,757 / EN 6,210; consumers: README, dataset_card, methodology.html, index.html, entscheide/ mcp/ standards/ ueber/ word/ pages + language copies, mcp_server.py, llms.txt, claude-desktop-setup.md, word-addin AppSource texts, Anthropic directory submission). Previous: 2026-07-24 (live-figure sweep vs stats.json + reference_graph.db: **~1.05M records / ~909k unique decisions** (cross-identifier dual-count now live), courts 118, citation edges 9.65M resolved / ~10M extracted, statute 12.4M, federal laws 5,525, cantonal 15,600, Botschaft 5,900+/~410K, commentaries ~1,131, tools 43/41/2; paper-snapshot rows unchanged at 971,992). Consumers synced this sweep: README, dataset_card, dataset_card_legislation, methodology.html (6 langs), index.html, mcp_server.py (instructions + tool descriptions), llms.txt, claude-desktop-setup.md, entscheide/ + ueber/ pages, word-addin AppSource listing. Paper (docs/paper/*) intentionally left at its frozen snapshot.

**Materialien re-verified 2026-10-21** against production materialien.db / consultations.db: Bundesblatt 19,842 rows (9,727 works), 1,650,592 paragraphs, 26,587 article links / 770 acts; Vernehmlassungen 2,694 procedures, 10,296 searchable documents, 10,841,357 paragraphs. Consumers synced: README, dataset_card, index.html register (DE/FR/IT/EN), stats page, mcp/api pages, mcp_server.py instructions + tool descriptions.

**Materialien re-verified 2026-11-02** after the XML re-parse, against production materialien.db: Bundesblatt 28,104 rows (16,842 works, 14,644 Botschaften), 2,880,484 paragraphs, 28,180 article links / 785 acts. Most of the rise in rows and paragraphs since 10-21 is the historical backfill, which still runs nightly; the re-parse added 62,024 paragraphs and 1,154 links. Consumers synced the same day: README, dataset_card, llms.txt, mcp_server.py instructions + tool descriptions.

**Cantonal laws re-verified 2026-11-02** against production cantonal_laws.db after the monthly LexFind harvest: 16,442 laws (15,556 active, 886 repealed), 381,725 articles; lexwork 19 cantons, sil 2, ti_rl 1, zhlex_pdf 1 (ZH), lexfind_pdf JU, SZ, VD and 809 ZH acts. index.html's 515,000+ articles = ~134,400 federal per language + 381,725 cantonal. Consumers synced: README, dataset_card, index.html, courts/local, court/angebot, Word add-in listing, mcp_server.py.

**Federal laws and scholarship re-verified 2026-11-03** against production statutes.db and legal_scholarship.db: 5,546 federal laws (4,859 with article text), 403,181 articles (DE 134,188 / FR 134,826 / IT 134,167); 51,892 scholarship records from 27 sources (the scholarship page counts the keys of stats.json's scholarship_by_source), 8,619 with full text. Consumers synced: README, dataset_card, index.html, scholarship/ and search/ pages (all languages), mcp_server.py, Word add-in listing, Connectors Directory sheet. stats.json still carried 15,618 cantonal laws / 362,465 articles on 2026-11-03, behind cantonal_laws.db.
