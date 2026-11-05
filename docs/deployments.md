# Hosted MCP server: deployment changelog

Each build of the hosted MCP server at mcp.opencaselaw.ch between 2026-09-25 and 2026-11-04: the commit, when it reached the production checkout, when it went live on the first and on all eight worker processes, and the models behind search. Reconstructed from the production server's records. Use it to date benchmark runs: a run at time T was answered by the build(s) live at T.

Machine-readable: [`deployments.json`](deployments.json).

## How to read it

- A commit is *on disk* when the production checkout reached it, by a manual `git merge --ff-only` or the nightly build's `git pull --rebase`. It is not live yet: each of the 8 workers (ports 8770-8777) serves the code it loaded at its last start.
- *First worker* is the start of the first worker restart that loaded the commit (or a later one); *all 8* is the end of the restart that brought the last worker onto it. In between the fleet is mixed or may be (see Mixed-fleet windows).
- Workers restart four ways: the nightly build's rolling restart after the corpus swap (usually 10:30-13:45 UTC), a manual rolling restart after a deploy, the memory recycle of single workers above 3.5 GB (from 2026-10-06), and the monthly Fedlex sync.
- The nightly build pulls new code at its end, after its own restart, so code that arrives that way usually goes live at the next day's post-swap restart unless someone restarts by hand.
- *Evidence*: `exact` = systemd journal, or the per-call model log for the 2026-10-12 switch; `log` = build or recycle log line (within about 20 s); `note (...)` = a dated operator note, with its stated precision. Before 2026-10-27 22:44 UTC the journal has rotated away: an unlogged manual restart may have made a build live earlier than shown, never before *on disk*.
- *Serving code* `yes` = the range changes a Python module the server imports (the import closure of `mcp_server.py`). `no` = scrapers, build pipeline, scripts, tests or docs only: the commit id changes but tool behaviour does not, until data those parts produce is published. Nightly data refreshes are not versions here.
- Bot commits that only refresh `docs/` stats and feeds are left out of the ranges.

## Search models

| Step | Model | Status, 2026-09-25 to 2026-11-04 |
|---|---|---|
| Query parse, query expansion, LLM rerank | `claude-haiku-4-5-20251001 -> claude-haiku-5-5` | Changed once, by 2a77d717 (see below). Confirmed by the model recorded for every call in the server's LLM usage log; no rollback. |
| Cross-encoder rerank | `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` | Switched on in the server configuration but never ran: the model is not in the workers' offline model cache, so it fails to load and ranking skips the step. Unchanged. |
| Dense vector retrieval | `BAAI/bge-m3` | Switched off in the server configuration, and the server holds no vector index, so the step never ran. The model is not in the workers' offline model cache either. Unchanged. |
| Paragraph-level semantic rescue (pinpoints) | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` | In the cache and in use; the cache has not changed since 2026-09-03. Unchanged. |
| Claim check (not search; for completeness) | `claude-sonnet-4-6` | Unchanged (LLM usage log). |

**Model change:** `2a77d717` moved query parse, expansion and rerank from `claude-haiku-4-5-20251001` to `claude-haiku-5-5`. It reached disk with the nightly pull at 2026-10-12 23:17:24 UTC and went live with a manual rolling restart: the last Haiku 4.5 call was at 23:23:38 UTC and the first Haiku 5.5 call at 23:24:00 UTC, with no Haiku 4.5 call since. It is the only search-model change in the window.

## Versions served

One row per code commit that workers were started from. *First worker* is when the first of the 8 workers began serving it; *all 8* is when every worker served it or a later build. Times UTC.

| First worker | All 8 | Commit | Range | On disk | Serving code | Query LLM | Evidence | Changes |
|---|---|---|---|---|---|---|---|---|
| 2026-09-24 12:09 | 2026-09-24 12:10 | `4c044b2e` | `3e3e286d..4c044b2e` | 2026-09-24 06:51 | no | **claude-haiku-4-5-20251001** | log | baseline: served when the window opens |
| 2026-09-25 11:40 | 2026-09-25 11:44 | `6241b42c` | `4c044b2e..6241b42c` | 2026-09-25 11:31 | yes | claude-haiku-4-5-20251001 | note (+/- 1 min) | find_leading_cases: rank free-text topics by regeste match, not citations alone; Schaffhausen commentary: reachable from get_commentary, get_law, cite and the decisions it cites; mcp: /mcp-copilot, a no-retention endpoint for the Copilot agent (+7 non-serving) |
| 2026-09-25 11:48 | 2026-09-25 11:49 | `92cd6417` | `6241b42c..92cd6417` | 2026-09-25 11:48 | yes | claude-haiku-4-5-20251001 | log | get_doctrine: a cantonal query keeps only that canton's leading cases (+1 non-serving) |
| 2026-09-25 11:56 | 2026-09-25 12:15 | `290abc07` | `92cd6417..290abc07` | 2026-09-25 11:55 | yes | claude-haiku-4-5-20251001 | note (approx.) | mcp: no-retention endpoint renamed /mcp-edu; /mcp-copilot kept as alias for agent 1.1.0 |
| 2026-09-26 12:05 | 2026-09-26 12:05 | `860931c0` | `290abc07..860931c0` | 2026-09-25 21:55 | yes | claude-haiku-4-5-20251001 | log | OGE citations: link only the ruling of the written date; OGE links: the snippet shows the occurrence that matched (+2 non-serving) |
| 2026-09-27 11:33 | 2026-09-27 11:33 | `43194cb3` | `860931c0..43194cb3` | 2026-09-27 04:20 | no | claude-haiku-4-5-20251001 | log | (+2 non-serving) |
| 2026-09-27 23:35 | 2026-09-27 23:37 | `175ffe4a` | `43194cb3..175ffe4a` | 2026-09-27 23:24 | yes | claude-haiku-4-5-20251001 | note (+/- 2 min) | leading cases + search: fold the ids a BGE carries into one entry (#40) (+1 non-serving) |
| 2026-09-28 11:16 | 2026-09-28 11:16 | `597a2679` | `175ffe4a..597a2679` | 2026-09-27 23:52 | no | claude-haiku-4-5-20251001 | log | (+1 non-serving) |
| 2026-09-29 09:50 | 2026-09-29 10:00 | `d35111d3` | `597a2679..d35111d3` | 2026-09-29 09:50 | yes | claude-haiku-4-5-20251001 | note (window) | bge ids: resolve the direct scraper's id everywhere (#40, before es_bge retires); mcp: drop the 'no query logging' claim from the server instructions (+2 non-serving) |
| 2026-09-30 13:38 | 2026-09-30 13:50 | `570cad37` | `d35111d3..570cad37` | 2026-09-30 07:00 | no | claude-haiku-4-5-20251001 | note (window) | (+2 non-serving) |
| 2026-09-30 14:13 | 2026-09-30 14:15 | `7eb999dd` | `570cad37..7eb999dd` | 2026-09-30 14:12 | yes | claude-haiku-4-5-20251001 | note (+/- 1 min) | serving: execution lanes with admission control for tool and REST work |
| 2026-10-01 05:15 | 2026-10-01 05:22 | `853fd204` | `7eb999dd..853fd204` | 2026-10-01 05:14 | yes | claude-haiku-4-5-20251001 | note (window) | lookup: resolve a full case number by exact match before searching |
| 2026-10-02 11:27 | 2026-10-02 11:27 | `9d79661e` | `853fd204..9d79661e` | 2026-10-02 10:53 | yes | claude-haiku-4-5-20251001 | log | zh: file rulings by the court that decided, keep the portal's headnote and appeal references (+1 non-serving) |
| 2026-10-02 15:39 | 2026-10-02 15:41 | `9b97efb2` | `9d79661e..9b97efb2` | 2026-10-02 15:39 | yes | claude-haiku-4-5-20251001 | note (+/- 1 min) | zh: show the court's appeal note, serve each ruling once, key undated entries; search: a BGE reference pins the ruling first; /entscheid resolves written references (+17 non-serving) |
| 2026-10-03 10:47 | 2026-10-03 10:47 | `b065c33c` | `9b97efb2..b065c33c` | 2026-10-02 23:22 | yes | claude-haiku-4-5-20251001 | log | ta_sst: read the Swiss Sports Tribunal's own site; retire the SAV/FSA sources; search_commentaries: search the Schaffhausen commentary too (+2 non-serving) |
| 2026-10-04 12:02 | 2026-10-04 12:02 | `167a5415` | `b065c33c..167a5415` | 2026-10-03 12:03 | no | claude-haiku-4-5-20251001 | log | (+1 non-serving) |
| 2026-10-05 11:31 | 2026-10-05 11:32 | `2eac9b02` | `167a5415..2eac9b02` | 2026-10-04 23:25 | no | claude-haiku-4-5-20251001 | log | (+1 non-serving) |
| 2026-10-06 07:02 | 2026-10-06 13:36 | `c1a53f4a` | `2eac9b02..c1a53f4a` | 2026-10-06 07:02 | no | claude-haiku-4-5-20251001 | log, log (restart slow; systemd finished it) | (+1 non-serving) |
| 2026-10-06 13:29 | 2026-10-06 13:36 | `477bbd05` | `c1a53f4a..477bbd05` | 2026-10-06 07:57 | no | claude-haiku-4-5-20251001 | log (restart slow; systemd finished it) | (+1 non-serving) |
| 2026-10-07 17:15 | 2026-10-07 17:15 | `4f9438e5` | `477bbd05..4f9438e5` | 2026-10-07 06:20 | no | claude-haiku-4-5-20251001 | log | (+1 non-serving) |
| 2026-10-11 01:11 | 2026-10-11 01:12 | `24d7a44a` | `4f9438e5..24d7a44a` | 2026-10-10 15:24 | yes | claude-haiku-4-5-20251001 | log | Historical BGE: serve references whose DFR source holds another ruling without that ruling's text (#129); Historical BGE: cut rulings to their own pages and header date; St. Gallen twin dates (#130); Serve the five recovered historical BGE rulings once the build stores them (#135); get_doctrine: rule summaries state the rule; tool check fails error answers (#144) (+17 non-serving) |
| 2026-10-12 12:50 | 2026-10-12 12:52 | `e50abd93` | `24d7a44a..e50abd93` | 2026-10-11 17:49 | yes | claude-haiku-4-5-20251001 | log | search: federal dockets answered by the docket fast path, the ruling first (#145); search: a BGE reference lists its citing decisions from the citation graph (#146); scholarship: incremental, budgeted, resumable harvest — no source starves the rest (#150) (+2 non-serving) |
| 2026-10-12 23:23 | 2026-10-12 23:24 | `2a77d717` | `e50abd93..2a77d717` | 2026-10-12 23:17 | yes | **claude-haiku-5-5** | exact (llm_usage) | ZH appeal chains: caption docket aliases, header and line-wrap fixes, date guard (#151); Haiku 5.5 for the query parse, expansion, rerank and QC spot-check (#139) (+1 non-serving) |
| 2026-10-14 00:05 | 2026-10-14 00:10 | `f607e75f` | `2a77d717..f607e75f` | 2026-10-14 00:01 | yes | claude-haiku-5-5 | note (+/- 5 min) | mcp: serve E. subtrees, resolve padded ids, flag shared dockets and future dates (+3 non-serving) |
| 2026-10-14 11:39 | 2026-10-14 11:40 | `76244a5f` | `f607e75f..76244a5f` | 2026-10-14 06:36 | yes | claude-haiku-5-5 | log | mcp: state the true count of decisions sharing a docket; restore keeps file mode (+4 non-serving) |
| 2026-10-14 13:25 | 2026-10-14 13:35 | `8a53e93a` | `76244a5f..8a53e93a` | 2026-10-14 12:07 | yes | claude-haiku-5-5 | note (approx.) | serving: host-local decision suppression for privacy takedowns (+2 non-serving) |
| 2026-10-15 12:00 | 2026-10-15 12:01 | `7cdc4fc9` | `8a53e93a..7cdc4fc9` | 2026-10-14 19:38 | no | claude-haiku-5-5 | log | (+1 non-serving) |
| 2026-10-16 11:43 | 2026-10-16 11:44 | `a557c0a0` | `7cdc4fc9..a557c0a0` | 2026-10-15 16:42 | no | claude-haiku-5-5 | log | (+1 non-serving) |
| 2026-10-16 12:12 | 2026-10-16 12:14 | `d3251301` | `a557c0a0..d3251301` | 2026-10-16 12:12 | yes | claude-haiku-5-5 | note (+/- 1 min) | get_decision/cite: a BGer judgment names the BGE that publishes it |
| 2026-10-16 12:28 | 2026-10-16 12:31 | `6a59110c` | `d3251301..6a59110c` | 2026-10-16 12:28 | yes | claude-haiku-5-5 | note (+/- 2 min) | get_decision: BGE→judgment lookup uses the docket index, not the court index |
| 2026-10-17 11:49 | 2026-10-17 11:50 | `2bef178e` | `6a59110c..2bef178e` | 2026-10-17 00:28 | yes | claude-haiku-5-5 | log | BGE headers: Italian "dell'11", "1°", "des ... et ...", EVG dockets, lead docket (+3 non-serving) |
| 2026-10-17 20:05 | 2026-10-17 20:08 | `bc31e02a` | `2bef178e..bc31e02a` | 2026-10-17 20:05 | yes | claude-haiku-5-5 | note (+/- 3 min) | search, feeds: find decisions by the date they were published; vorschlag: suggestion page with the whole corpus answering live, and its inbox (+2 non-serving) |
| 2026-10-17 23:50 | 2026-10-17 23:55 | `4a3d8523` | `bc31e02a..4a3d8523` | 2026-10-17 23:47 | no | claude-haiku-5-5 | note (+/- 5 min) | (+2 non-serving) |
| 2026-10-19 12:36 | 2026-10-19 12:37 | `ea12be50` | `4a3d8523..ea12be50` | 2026-10-19 12:31 | no | claude-haiku-5-5 | log | (+1 non-serving) |
| 2026-10-20 11:42 | 2026-10-20 11:44 | `3a2df7cd` | `ea12be50..3a2df7cd` | 2026-10-20 03:00 | yes | claude-haiku-5-5 | log | materialien: title and publication_date on every Botschaft document; materialien: ingest the other Bundesblatt types from Fedlex's file store (Berichte, Stellungnahmen, Kommissionsberichte, Erlassentwürfe, Vernehmlassungs-Eröffnungen), tagged doc_type; materialien: the ingest loop stops on a refusal — 429/403 at once, five consecutive failures otherwise, exponential backoff, --max-per-run; materialien: the nightly pass heals old rows; publication_date says where it came from; +5 more (+9 non-serving) |
| 2026-10-20 22:50 | 2026-10-20 22:59 | `6ab3dcc1` | `3a2df7cd..6ab3dcc1` | 2026-10-20 20:55 | yes | claude-haiku-5-5 | note (10-min window) | materialien: Vernehmlassung documents Fedlex does not hold (Chancellery 1995-2004 index, BJ projects); materialien: Chancellery index port + footer fixes; two hand-checked pre-2005 consultations; Erwägung boundaries: citation lines, page turns and table cells are not headings; a BGE's own reference is not a citation (+1 non-serving) |
| 2026-10-22 12:09 | 2026-10-22 12:10 | `f18d6aef` | `6ab3dcc1..f18d6aef` | 2026-10-22 00:50 | yes | claude-haiku-5-5 | log | get_materialien: Erläuternder-Bericht passages only for the act asked about; docs: Materialien figures everywhere — Bundesblatt documents, Vernehmlassungen; Vernehmlassungen: text for .doc/.xlsx/.zip, oversize PDFs, OCR flagged as OCR; materialien: the 1850-1999 Bundesblatt text is flagged as OCR in every tool (+6 non-serving) |
| 2026-10-23 11:24 | 2026-10-23 11:27 | `e212dcd8` | `f18d6aef..e212dcd8` | 2026-10-22 13:44 | yes | claude-haiku-5-5 | note (+/- 3 min) | health alerts: stop reporting live workers as unreachable |
| 2026-10-24 12:02 | 2026-10-24 12:03 | `70149661` | `e212dcd8..70149661` | 2026-10-23 16:02 | no | claude-haiku-5-5 | log | (+2 non-serving) |
| 2026-10-25 12:06 | 2026-10-25 12:07 | `666c7acd` | `70149661..666c7acd` | 2026-10-24 20:46 | yes | claude-haiku-5-5 | log | feat: UN human-rights bodies — treaty-body decisions (untb_*) and UN recommendations to Switzerland (un_uhri); untb: Working Group on Arbitrary Detention opinions (unwgad) (+1 non-serving) |
| 2026-10-25 13:30 | 2026-10-25 13:39 | `b841ef13` | `666c7acd..b841ef13` | 2026-10-25 13:21 | yes | claude-haiku-5-5 | note (10-min window) | search: fold manifest-linked representations into one hit; harvest a missing Regeste (#90); search collapse: full harvested Regeste on get_decision/get_regeste; kill switch; provenance (#90 review); ch_vb: retire the entscheidsuche CH_VB feed (#79); practice: UN treaty-body General Comments and General Recommendations (un_gc); +4 more (+5 non-serving) |
| 2026-10-26 01:55 | 2026-10-26 01:59 | `a2859c5f` | `b841ef13..a2859c5f` | 2026-10-26 01:53 | yes | claude-haiku-5-5 | note (+/- 5 min) | cjeu: Court of Justice of the EU judgments on the EU acts Swiss law follows; icj: International Court of Justice judgments, advisory opinions and orders (+1 non-serving) |
| 2026-10-26 23:45 | 2026-10-26 23:50 | `245536e4` | `a2859c5f..245536e4` | 2026-10-26 22:07 | yes | claude-haiku-5-5 | note (+/- 5 min) | graph counts: count rows, not SUM(mention_count) (+3 non-serving) |
| 2026-10-27 11:53 | 2026-10-27 11:54 | `88ac6087` | `245536e4..88ac6087` | 2026-10-27 07:58 | yes | claude-haiku-5-5 | log | ch_vb: keep the VPB/JAAC series, drop the rest of the CH_VB crawl (+1 non-serving) |
| 2026-10-28 13:23 | 2026-10-28 13:25 | `99d33cc4` | `88ac6087..99d33cc4` | 2026-10-27 14:09 | no | claude-haiku-5-5 | exact | (+1 non-serving) |
| 2026-10-29 00:37 | 2026-10-29 00:37 | `ae79a7f4` | `99d33cc4..ae79a7f4` | 2026-10-28 23:39 | yes | claude-haiku-5-5 | exact | ch_vb: cite as VPB/JAAC/GAAC, resolve every title; repair 2007+ dockets and languages |
| 2026-10-29 21:56 | 2026-10-29 21:56 | `6c95d562` | `ae79a7f4..6c95d562` | 2026-10-29 21:54 | yes | claude-haiku-5-5 | exact | ntfy: take the operator topic from the environment, never from the code; commentaries: ingest OpenLegalCommentary.ch BV into ok_commentaries.db; reference graph: record real mention counts; law codes: join two-word short titles, keep the edition prefix; +1 more (+3 non-serving) |
| 2026-10-31 11:59 | 2026-10-31 12:00 | `a006fecc` | `6c95d562..a006fecc` | 2026-10-30 23:57 | yes | claude-haiku-5-5 | exact | get_erwaegung: a heading instead of a number no longer raises; exports: the filename header survives ids outside latin-1; search, list_courts: keep decision dates and publication dates apart; pinpoint: search only the decision's own paragraphs (+4 non-serving) |
| 2026-11-02 10:22 | 2026-11-02 10:22 | `cf364f95` | `a006fecc..cf364f95` | 2026-11-02 10:14 | yes | claude-haiku-5-5 | exact | metrics: a URL segment can no longer stop the dev-data export; get_law: accept the act under the names callers use, label misses as misses (+2 non-serving) |
| 2026-11-02 12:47 | 2026-11-02 19:14 | `12b5930b` | `cf364f95..12b5930b` | 2026-11-02 11:06 | yes | claude-haiku-5-5 | exact | ch_vb: name both parts, the VPB series and the restored pages (+2 non-serving) |
| 2026-11-02 19:14 | 2026-11-02 19:14 | `7639b43d` | `12b5930b..7639b43d` | 2026-11-02 19:11 | yes | claude-haiku-5-5 | exact | materialien: stop labelling draft text as Botschaft, add printed pages; materialien: read Botschaft structure, place comments by enacted article; materialien: place language editions together, keep full section headings; cantonal laws: say when an answer is a local copy or a lookup failed (+4 non-serving) |
| 2026-11-03 10:34 | 2026-11-03 10:35 | `4c014216` | `7639b43d..4c014216` | 2026-11-03 08:01 | yes | claude-haiku-5-5 | exact | numbers: public text catches up with Materialien and the cantonal mirror; numbers: federal laws 5,546 and scholarship 51,000+ from 27 sources (+1 non-serving) |
| 2026-11-04 10:50 | 2026-11-04 10:50 | `58c59832` | `4c014216..58c59832` | 2026-11-03 22:58 | yes | claude-haiku-5-5 | exact | mcp: the newest decisions are never dated today, and that is no gap (+3 non-serving) |
| 2026-11-04 11:19 | 2026-11-04 11:20 | `8c5ed985` | `58c59832..8c5ed985` | 2026-11-04 11:19 | yes | claude-haiku-5-5 | exact | mcp: bounded ECtHR payloads, no whole-corpus answer for "OR", statute text for mock decisions, paged practice |
| 2026-11-04 14:27 | 2026-11-04 14:27 | `f78ad410` | `8c5ed985..f78ad410` | 2026-11-04 14:27 | yes | claude-haiku-5-5 | exact | rest: research tools answer with their payload on /api/tool (+3 non-serving) |

## Mixed-fleet windows

Periods longer than five minutes in which workers served different builds. A client pinned to one worker saw one build; a run spread over several workers could see both.

| From | To | Builds served (workers) | Cause |
|---|---|---|---|
| 2026-10-06 07:02 | 2026-10-06 13:30 | `2eac9b02` (7), `c1a53f4a` (1) | worker_recycle (memory) |
| 2026-11-02 12:47 | 2026-11-02 15:17 | `12b5930b` (1), `cf364f95` (7) | systemd journal |
| 2026-11-02 15:17 | 2026-11-02 17:32 | `12b5930b` (2), `cf364f95` (6) | systemd journal |
| 2026-11-02 17:32 | 2026-11-02 17:47 | `12b5930b` (4), `cf364f95` (4) | systemd journal |
| 2026-11-02 17:47 | 2026-11-02 19:14 | `12b5930b` (5), `cf364f95` (3) | systemd journal |

## Windows that cannot be dated exactly

- Before 2026-10-27 22:44 UTC the systemd journal has rotated away, so manual restarts are known only where an operator note dates them. Any build listed before then may have gone live earlier than shown, never before it reached disk. An unlogged restart could also have served a commit that the table folds into the next row.
- 2026-09-25 11:56-12:15: the workers were restarted twice after 290abc07 reached disk at 11:55; the note says about 12:00. The table takes 12:15 as the end.
- 2026-09-25 21:48 to 2026-09-26 12:05: e41559e9 (citation dates checked) and 860931c0 (snippet fix). A note calls e41559e9 live at 21:53 through a data rebuild; no worker restart is recorded before the 2026-09-26 post-swap restart, which the table uses.
- 2026-10-14 13:25-13:35: restart for e5cb325c, dated about 13:30 in a note.
- 2026-10-20 22:50-23:00: rolling restart for 6ab3dcc1, dated 22:5x in a note.
- 2026-10-21 15:06 to 2026-10-22 12:09: notes record a manual restart after f294e188 (get_materialien passage fix) and after the text-gaps commits, without a time. The table uses the 2026-10-22 post-swap restart, an upper bound for f294e188, 03a94f0f, 886de55e and d1dd2b5d.
- 2026-10-25 13:30-13:40: restart after b841ef13, dated 13:3x in a note.
- No post-swap restart on 2026-09-30 (the build hit its time cap), 2026-10-09 or 2026-10-10 (step 2 failed): code that arrived then waited for a later restart.
- On 2026-10-06, 2026-10-12, 2026-10-13, 2026-10-21, 2026-10-26 and 2026-11-01 some workers' restarts outlasted the build's 60 s wait; systemd completed them, so those workers came up to about a minute later than logged.
- 2026-10-02 04:46-04:48: the Fedlex monthly sync's rolling restart; its log has times but no dates, so the date comes from the file it wrote.

## Sources and method

- On-disk times: `git reflog` of the production checkout, which records every `merge --ff-only` and every nightly `pull --rebase`.
- Worker starts: the nightly build log's post-swap restart lines (per worker), `logs/worker_recycle.log` (single workers, from 2026-10-06), the Fedlex sync log, the systemd journal (from 2026-10-27 22:44 UTC) and dated operator notes for manual restarts before that.
- Each worker serves the commit that was on disk when it started; *first worker*, *all 8* and the mixed-fleet windows follow from that per-worker timeline (`fleet_states` in the JSON).
- Search models: the code defaults at each commit, the model recorded for every LLM call, and the contents of the workers' offline model cache.
- From the next deploy on, `scripts/rolling_restart_workers.sh` appends each rolling restart (commit, UTC window, healthy ports, models) to a log on the server, and `scripts/record_deployment.py render` folds that log into these two files. Restarts by the nightly build itself and by the memory recycle are not logged that way yet.
