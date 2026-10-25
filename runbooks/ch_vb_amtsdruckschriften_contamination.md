# ch_vb: retire the entscheidsuche CH_VB feed, re-source federal administrative practice from the federal authorities

Status: 2026-10-22 — rebased onto current `origin/main` (f18d6aef) in the
worktree `claude/ch-vb-retire`, uncommitted, offline suite 5,143 passed / 23
skipped. Production side (shard move, gate exemption, deploy) still NOT run:
it is the build window and those steps are Jonas's (section 4b).

The code/doc change set was originally prepared 2026-09-11 on
`claude/elastic-wilson-cbe289` (commits 8eda93bf, d3df43b3) against a much
older main (1eefec72, 309 commits behind). Porting it forward 2026-10-22
found one superseding change already on main — `OCL_SWAP_GATE_EXEMPT`
(f6f02460) replaces the `OCL_SWAP_GATE_EXEMPT_COURTS` variable this runbook
originally introduced; see "4a. What changed since 09-11" below — and dropped
the BJ Berichte-und-Gutachten scraper from this change set entirely (it will
land separately; see "Not part of this change").

Decision recorded 2026-09-11 (Jonas): nothing is taken from entscheidsuche for
this segment; the sources have to be the federal authorities' own. An earlier
draft of this note proposed keeping the VPB subset of the feed; that variant
and its split script were withdrawn.

## Bottom line

`court='ch_vb'` (23,046 rows) is fed by exactly one shard,
`output/decisions/es_ch_vb.jsonl`, which the entscheidsuche `CH_VB` spider built
by crawling the Federal Archives' amtsdruckschriften portal wholesale. In the
raw feed only 27% of the entries are Verwaltungspraxis der Bundesbehörden
(VPB/JAAC), in the live DB only 11.4% (2,632 of 23,046 rows); the rest are Bundesblatt, Amtliches Bulletin, Amtliche Sammlung,
Staatsrechnung, Staatskalender, Geschäftsberichte and similar pages. The
shard is retired as a whole. The federal administrative-practice material is
re-sourced from (a) the Bundeskanzlei / Bundesarchiv for the historical VPB
series, by request, and (b) the offices' own publication pages for the
born-digital era, by direct scrapers.

## 1. What is being dropped (sizing)

### 1a. Source side, exact (raw feed /opt/caselaw/entscheidsuche/CH_VB, 32,705 JSON files, read 2026-09-11)

Every entry points at `www.amtsdruckschriften.bar.admin.ch`; the host cannot
separate collections. The portal document id in `viewOrigDoc/<id>.pdf?ID=<id>`
can: its leading digits map to exactly one collection label in the feed's
`Meta` field, confirmed by reading one PDF per prefix.

| id prefix | collection                                                    | entries | years (feed `Datum`) |
|-----------|----------------------------------------------------------------|--------:|----------------------|
| 10        | Bundesblatt / Feuille fédérale                                 | 12,681  | 1982–2008 |
| 20        | Amtliches Bulletin der Bundesversammlung / Bulletin officiel   | 10,105  | 1982–1999 (600–980 per year 1982–95, ~50/yr 1996–99) |
| 15        | VPB / JAAC (the only decisions)                                | 8,737   | 1982–2016 |
| 30        | Amtliche Sammlung / Recueil officiel                           | 857     | 1982–1998 |
| 90        | Staatsrechnung und Voranschlag                                 | 105     | 1982–2005 |
| 80        | Studien und Quellen                                            | 84      | 1982–2005 |
| 11        | Übersicht über die Verhandlungen der Bundesversammlung         | 58      | 1982–1995 |
| 40        | Eidgenössischer Staatskalender                                 | 56      | 1982–1999 |
| 50        | Geschäftsberichte des Bundesrates                              | 22      | 1982–1995 |

Amtliches Bulletin by year: 1982 641 · 1983 597 · 1984 517 · 1985 617 ·
1986 606 · 1987 652 · 1988 654 · 1989 737 · 1990 667 · 1991 739 · 1992 855 ·
1993 980 · 1994 831 · 1995 802 · 1996 54 · 1997 50 · 1998 56 · 1999 50.

Docket damage: 2,208 feed entries carry raw HTML in `Num[0]`
(`<td class="metadataCell">…</td>`, an upstream entscheidsuche bug that
`build_decision()` passes through); only 8,202 entries, all VPB, have a real
`JAAC x.y` / `VPB x.y` docket. `decision_date` on these rows was re-derived from
text by `scripts/repair_decision_dates.py --all` (2026-03-12), which is why
stats.json shows `ch_vb` "earliest 1900-09-17".

### 1b. DB side, exact (production decisions.db, 73.6 GB, indexed read-only pull after the 2026-09-11 build exit, 20:36 UTC)

23,046 live `ch_vb` rows, all `source='entscheidsuche'`, all on
`www.amtsdruckschriften.bar.admin.ch`. Classified by the portal id in the URL
(same key as 1a); the digitisation footer in the last 350 characters of the
text agrees on every row where it is present (7,088/7,088 AB rows carry the
"Dienst für das Amtliche Bulletin" footer, 2,608/2,632 VPB rows the
"Bundeskanzlei konvertiert" footer, the rest the plain Bundesarchiv footer).

| collection | rows | share |
|---|---:|---:|
| Bundesblatt (prefix 10) | 12,256 | 53.2% |
| Amtliches Bulletin (20) | 7,088 | 30.8% |
| **VPB / JAAC (15)** | **2,632** | **11.4%** |
| Amtliche Sammlung (30) | 833 | 3.6% |
| Studien und Quellen (80) | 78 | 0.3% |
| Staatsrechnung (90) | 76 | 0.3% |
| Staatskalender (40) | 35 | 0.2% |
| Übersicht Verhandlungen (11) | 26 | 0.1% |
| Geschäftsberichte (50) | 22 | 0.1% |

Non-decision rows: **20,414 of 23,046 (88.6%)**. The VPB share is lower than in
the feed (8,737 entries) because the ingest's docket-keyed dedup collapsed the
VPB rows hardest; only 2,632 survived. Of those, 2,400 carry a real
`JAAC x.y` / `VPB x.y` docket, 125 a bare portal id, 101 the raw HTML cell,
6 other fragments. HTML dockets in total: 2,208 (866 AB, 700 BBl, 481 AS,
101 VPB, 60 in the small collections) — the `_clean_docket` R1 defence covered
all of them. No row is under 500 characters of text.

Year axis = feed issue date (`publication_date` where the March date-repair
pass moved it there, else `decision_date`, which is the untouched feed date for
the remaining rows; 14,679 rows have no `publication_date`):

| year | BBl | AB | VPB | AS | other |
|---|---:|---:|---:|---:|---:|
| 1982 | 150 | 445 | 1 | 58 | 24 |
| 1983 | 130 | 402 | 0 | 50 | 13 |
| 1984 | 113 | 337 | 1 | 51 | 9 |
| 1985 | 103 | 419 | 7 | 48 | 16 |
| 1986 | 105 | 401 | 65 | 48 | 11 |
| 1987 | 108 | 454 | 65 | 48 | 13 |
| 1988 | 115 | 508 | 104 | 50 | 10 |
| 1989 | 106 | 516 | 63 | 49 | 10 |
| 1990 | 100 | 515 | 64 | 52 | 6 |
| 1991 | 113 | 529 | 85 | 49 | 13 |
| 1992 | 119 | 564 | 95 | 47 | 12 |
| 1993 | 122 | 731 | 114 | 49 | 20 |
| 1994 | 123 | 535 | 150 | 50 | 6 |
| 1995 | 128 | 522 | 166 | 42 | 8 |
| 1996 | 111 | 54 | 123 | 48 | 6 |
| 1997 | 141 | 50 | 140 | 47 | 7 |
| 1998 | 143 | 56 | 130 | 47 | 5 |
| 1999 | 190 | 50 | 128 | 0 | 5 |
| 2000 | 758 | 0 | 141 | 0 | 2 |
| 2001 | 721 | 0 | 142 | 0 | 3 |
| 2002 | 860 | 0 | 125 | 0 | 6 |
| 2003 | 1,019 | 0 | 159 | 0 | 9 |
| 2004 | 1,096 | 0 | 163 | 0 | 14 |
| 2005 | 1,175 | 0 | 124 | 0 | 9 |
| 2006 | 1,445 | 0 | 79 | 0 | 0 |
| 2007 | 1,411 | 0 | 43 | 0 | 0 |
| 2008 | 1,535 | 0 | 37 | 0 | 0 |
| 2009 | 10 | 0 | 32 | 0 | 0 |
| 2010 | 3 | 0 | 13 | 0 | 0 |
| 2011 | 1 | 0 | 11 | 0 | 0 |
| 2012 | 0 | 0 | 14 | 0 | 0 |
| 2013 | 2 | 0 | 15 | 0 | 0 |
| 2014 | 0 | 0 | 14 | 0 | 0 |
| 2015 | 0 | 0 | 4 | 0 | 0 |
| 2016 | 0 | 0 | 15 | 0 | 0 |
| **total** | **12,256** | **7,088** | **2,632** | **833** | **237** |

The AB rows are the Nationalrat/Ständerat debate pages 1982–1998 (the
"Wortlaut der Motion" hits); the BBl rows are Botschaften and reports
1982–2008, i.e. the pre-1999 Botschaft gap already known from the materialien
work, obtained here through a robots-disallowed path. Both are what the
retirement drops; the 2,632 VPB rows are what the official delivery has to
exceed (the full series is ~8,700 documents).

## 2. Entry path (single, frozen)

- `es_ch_vb.jsonl` (745 MB, mtime 2026-03-16) — written by
  `scrapers/entscheidsuche_ingest.py` (`SPIDER_MAP["CH_VB"]`, line 66), then
  rewritten in place by `scripts/recover_vpb_pdfs.py` (db35af38, 2026-03-02:
  23,031 PDF texts fetched from the portal) and the date-repair pass.
- No direct scraper exists for VPB (`tests/test_reprobe_dead_sources.py:57`).
- `opencaselaw-entscheidsuche.timer` is disabled and inactive on the VPS; the
  CH_VB staging dir was last refreshed 2026-03-09; `publish.py` step 1 (ingest)
  is skipped by default. Nothing refreshes the shard.
- `build_fts5.py` reads every `*.jsonl` under `output/decisions/`; the nightly
  `--full-rebuild` rebuilds decisions.db from the shards. Incremental builds
  never delete rows, so the shard's removal takes effect at the next full build.
- Provenance note: the portal's robots.txt disallows `/viewOrigDoc/*`; the
  March recovery fetched 23,031 PDFs from that path, and this investigation
  fetched 7 sample PDFs from it (one per prefix, to confirm the labels; not
  repeated). Retiring the shard also closes that provenance issue.

## 3. Official sources for the material (verified 2026-09-11)

**A. VPB 1987–2017, the historical series.**
- Producer: Bundeskanzlei, Kompetenzzentrum Amtliche Veröffentlichungen (KAV),
  Feldeggweg 1, 3003 Bern, info@bk.admin.ch, +41 58 462 31 31. The KAV page
  still lists "Veröffentlichung der Verwaltungspraxis der Bundesbehörden (VPB)"
  among its tasks. The 2007–2017 issues were born-digital on admin.ch
  (Bundesrecht → VPB, four issues a year); those pages and the asset URLs
  (`admin.ch/dam/gov/de/Bundesrecht/VBP/<year>/<year>.<n>.pdf…`) now answer
  404/403, so the BK's copy is no longer online but the source files exist.
- Custodian: Schweizerisches Bundesarchiv, amtsdruckschriften.bar.admin.ch,
  series "Verwaltungspraxis der Bundesbehörden (1987–2017)". bar.admin.ch:
  "VPB wurde per 1. Januar 2018 eingestellt." Contact info@bar.admin.ch. No API
  or bulk export on the portal; robots.txt disallows the document paths. But the
  BAR already publishes complete amtsdruckschriften series as open data on
  opendata.swiss (Amtliche Sammlung 1948–2018, Staatsrechnung 1848–2014,
  archive metadata), so a delivery of the VPB series is squarely within their
  practice. This is the "ask, don't crawl" route already chosen for the
  historical Botschaften; one letter to the BAR can cover both series.
- Last resort only, not a federal-authority source: Wayback captures of the old
  vpb.admin.ch (per-decision HTML `…/deutsch|franz|ital/doc/<vol>/<vol>.<n>.html`;
  ~2,400 FR and ~2,350 IT unique URLs captured 2000–2007, DE listing timed out).

**B. Born-digital VPB items on the offices' own sites (direct scrapers, robots allow).**
- Bundesamt für Justiz, "Berichte und Gutachten":
  https://www.bj.admin.ch/de/berichte-und-gutachten — ~90 entries 2001–2026,
  many carrying their VPB number ("VPB 2016.2 (S. 15–31)", "VPB 2013.7a",
  "VPB 2009.10a"), entry URLs like `/de/vpb-2016-2-s-15-31-ausgabe-vom-26-januar-2016`,
  PDFs under `/dam/bj/…`; bj.admin.ch robots.txt allows everything. These are
  Gutachten, not rulings: home is practice.db (`search_practice`, authority BJ,
  kind Gutachten), next to the BJ SchKG Weisungen already there, not `ch_vb`.
  **SEPARATE CHANGE, not part of this one** (2026-10-22): a
  `scrapers/practice/bj_gutachten.py` scraper for this source was drafted
  2026-09-11 and shipped offline-tested (10 tests, 4 fixtures) on
  `claude/elastic-wilson-cbe289`. It is deliberately NOT carried into this
  retirement change set — it is independent of dropping `ch_vb` (the BJ page
  doesn't disappear if the shard retirement is delayed, and the retirement
  doesn't need the BJ scraper to be safe) and should be reviewed and landed on
  its own branch/PR so a problem in one doesn't block or get confused with the
  other. Until then this source is simply not ingested; `search_practice`
  says so (see 4a).
- The Bundesrat's Beschwerdeentscheide on bj.admin.ch are already a direct
  scraper (`scrapers/ch_bundesrat.py`, court `ch_bundesrat`).
- Other offices announced their VPB issues via admin.ch news (e.g. EFD
  Rechtsdienst, Gutachten "cause Champagne", 2017; BJ Gutachten Art. 114 Abs. 5
  BV, 2016); whether those notices still link live PDFs is unchecked (the
  Bundesrecht asset path is dead). Candidate for a later inventory: EDA
  Direktion für Völkerrecht practice.

**C. Post-2017.** No successor series; offices publish on their own portals.
Already direct: edoeb, esbk, eschk, preisueberwacher, rab, finma, weko, elcom,
postcom, comcom, ubi, ch_bundesrat, plus practice.db (BSV, OAK BV, WEKO, SECO,
BAG, ESTV, SEM, BJ, BAFU). Nothing from this thread is missing there.

## 4. Runbook

### 4a. What changed since 09-11, done in the worktree (review with `git diff`, then commit + push)

- `scrapers/entscheidsuche_ingest.py`: `CH_VB` removed from `SPIDER_MAP` (a
  re-enabled weekly job can no longer re-ingest it);
  `scrapers/entscheidsuche_download.py`: `CH_VB` removed from the download list;
  `scripts/source_coverage_audit.py`: comment (classifies as "uncovered").
- `quality/checks/courts.py`: `ch_vb` out of `EXPECTED_FEDERAL` (the presence
  check would otherwise fail CRITICAL on the first build without the shard).
- `docs/intake_expected_stale.json`: `ch_vb` reason updated (kept, so the
  draining rows are not reported STALE in between); `docs/coverage_notes.json`,
  `docs/observability.md`, `README.md` (source table + source list),
  `dataset_card.md` (overview line + source table): VPB marked "pending an
  official delivery". `docs/index.html` (register row, DE/FR/IT/EN) and
  `docs/coverage/index.html` (five languages: DE/FR/IT/RM/EN) likewise; both
  are generator SOURCES for `docs/coverage/{en,fr,it,rm}/index.html` and
  `docs/entscheide/{en,fr,it,rm}/index.html` — after editing the source, run
  `node scripts/build_page_languages.mjs` (not hand-edit the generated
  mirrors) and `--check` to confirm nothing is left out of date.
  `docs/entscheide/index.html` display label ("VPB (1982–2016)" implied the
  series was in the corpus) updated the same way.
- `mcp_server.py`: the "23,000+ VPB/JAAC decisions under court='ch_vb'" claim
  (server instructions), the routing row, and the `search_practice`
  description tail now say the series is not ingested yet (description back
  under the 1,024-char budget); tests `test_search_practice_discoverability.py`,
  `test_tool_description_budget.py`, `test_check_intake_rates.py` updated.
  `COURT_DISPLAY_NAMES["ch_vb"]`, `COURT_LEVELS["ch_vb"]`, `branch_map.py`,
  `proceeding_map.py`, `search_stack/publish_delta.py` FEDERAL_COURTS and the
  `_clean_docket` / `_lookup_padded_id` defensive code left AS IS —
  deliberately: these are inert classification tables and docket-sanitisation
  code that must keep working against any `ch_vb` id or docket an old
  citation, bookmark, or the citation graph still carries, even though no new
  row will ever populate them.
- Old `/entscheid/<ch_vb id>` links and `get_decision`/`get_decisions` calls on
  a retired id: audited, not changed. `get_decision_by_id`'s resolution ladder
  and `seo_pages.render_decision_page` already fail through every fallback
  (alias, padded-id, docket, BGE-candidate, reference-resolver) to a plain
  404 / `{"error": "Decision not found: ..."}` — there is no path that raises
  on a miss. Two regression tests added to confirm this explicitly for the
  `ch_vb` case (a plain retired id and the known raw-HTML docket artifact):
  `tests/test_seo_decision_url_fallback.py::test_retired_ch_vb_id_gives_helpful_404_not_500`
  and `::test_retired_ch_vb_docket_html_artifact_gives_404_not_500`.
- `build_fts5.py`: the branch's `OCL_SWAP_GATE_EXEMPT_COURTS` was DROPPED —
  main already grew the equivalent mechanism independently (`OCL_SWAP_GATE_EXEMPT`,
  f6f02460, comma list unioned into `PER_COURT_GATE_EXEMPT` via
  `_per_court_gate_exempt()`, checked in `_check_swap_per_court_gate` before
  alias folding). Ported forward instead: `_per_court_gate_exempt()` now
  lower-cases the env codes (case-insensitive) and the gate warns once per
  env-added code absent from `live_by_court` ("check the spelling"), the two
  review fixes the original branch made to its own (now-retired) variable.
  Three tests in `tests/test_swap_row_gate.py`
  (`test_exempt_env_is_case_insensitive`,
  `test_exempt_env_warns_on_a_code_not_in_the_live_db`, plus the pre-existing
  `test_env_adds_exemptions_for_a_run` in `tests/test_zh_cross_court_attribution.py`).
  (Invariant 5: pipeline file; this edit is small and additive to an existing
  mechanism, but still review before deploy.)
- Letters to the Bundesarchiv (VPB data delivery) and the Bundeskanzlei KAV
  (VPB source files) drafted 2026-09-11, kept outside this repository.
  Status "draft (awaiting send)" as of this port; addresses to be checked
  before sending; re-verify they still read correctly six weeks on.
- Adversarial review 2026-09-12 (three lenses, ten refuters; 8 confirmed, 2
  refuted) of the original branch: public pages still advertising the VPB,
  the gate exemption made case-insensitive with a warning for unknown codes,
  the `ch_vb` intake exemption kept (reason updated). All of that is
  reflected above (via main's own `OCL_SWAP_GATE_EXEMPT` for the gate point).
  Refuted: the representation manifest drift claim (publish step 5a rebuilds
  the manifest first) and the "exemption consumed by a no-op build" claim
  (accepted instead: remove the .env.publish exemption only after the build that actually
  swaps without the shard — unchanged in 4b below).
- Not done, by design: nothing pushed; nothing changed on the VPS; no pipeline
  step run against real data.

### Not part of this change

- **BJ Berichte-und-Gutachten scraper** (`scrapers/practice/bj_gutachten.py` +
  tests + fixtures, drafted 2026-09-11 on the original branch): deliberately
  left out. It ingests a different, independent source and should land on its
  own branch/PR, reviewed and timed on its own — not bundled with a pipeline
  file change (build_fts5.py) and a QC-gate change (quality/checks/courts.py).
  Until it lands, `search_practice`'s description correctly does not mention
  it (it never did; the original branch's §4a step 5 to "promote after first
  run" is also dropped here along with the scraper).

### 4b. Production steps, in this order (Jonas; each one-liner is safe to run alone)

Ordering matters: the code must be live on the VPS BEFORE the full build that
misses the shard, otherwise `courts.federal_present.ch_vb` fails the QC gate and,
without the gate exemption, the per-court gate aborts the swap (fail-safe:
live DB untouched, build red, HF upload and pushes skipped for that night).

1. Commit + push the change set; on the VPS, outside the build window:
   ```
   ssh -i ~/.ssh/caselaw root@46.225.212.40 'cd /opt/caselaw/repo && git fetch -q && git merge --ff-only origin/main && git log -1 --oneline'
   ```
2. Retire the shard (same window; `output/archive/` is read by nothing):
   ```
   ssh -i ~/.ssh/caselaw root@46.225.212.40 'mkdir -p /opt/caselaw/repo/output/archive && mv /opt/caselaw/repo/output/decisions/es_ch_vb.jsonl /opt/caselaw/repo/output/archive/es_ch_vb.jsonl.retired-2026-10 && ls -la /opt/caselaw/repo/output/archive/'
   ```
   (`rm` instead of `mv` if the bytes should go; see "Open decisions".)
3. One-run gate exemption for the next FULL rebuild, set in
   `/opt/caselaw/repo/.env.publish` (the publish unit's `EnvironmentFile=`; the
   same place `bge_es_retirement.md` and `bge_twin_dates_2026-10-14.md` use).
   Not a systemd drop-in: a drop-in `Environment=` silently overrides whatever
   `.env.publish` already sets. With the new build_fts5.py live, first look for
   an existing line:
   ```
   ssh -i ~/.ssh/caselaw root@46.225.212.40 'grep -n OCL_SWAP_GATE_EXEMPT /opt/caselaw/repo/.env.publish'
   ```
   If there is none, add `OCL_SWAP_GATE_EXEMPT=ch_vb` (operator edit). If the
   variable already exists, append `,ch_vb` to its value
   (`OCL_SWAP_GATE_EXEMPT=bge,ch_vb`); `build_fts5._per_court_gate_exempt()`
   parses a comma-separated, case-insensitive list. (`zh_gerichte` is permanent
   and needs no flag.) It exempts this one court; the global row gate stays on.
   Hard prerequisite: step 1 is live (new build_fts5.py AND new
   quality/checks/courts.py travel in one ff-merge). Do NOT fall back to
   `OCL_SKIP_SWAP_GATE=1`: with the old code it would swap under a blanket
   override and then fail the QC gate on `courts.federal_present.ch_vb`,
   withholding the night's distribution after the data had already changed.
   After that build has swapped and the pipeline has exited, remove `ch_vb`
   from `.env.publish` again (drop the whole line if it was the only entry;
   otherwise remove just `,ch_vb`) and re-check with the same `grep`. A
   leftover entry is harmless but logs a "check the spelling" warning each night.
4. Verify after the swap, in this order:
   1. Publish log shows the two lines `build_fts5.py` now emits:
      `pre-swap per-court gate: exempting ch_vb for this run via OCL_SWAP_GATE_EXEMPT`
      and (since ch_vb collapses below its 80% floor)
      `pre-swap per-court gate: ch_vb 23046 → 0 rows, exempt`. Absence of the
      second line with a nonzero post-swap `ch_vb` count would mean the shard
      move (step 2) did not take effect — check it ran before the build.
   2. QC gate green (`courts.federal_present` no longer names `ch_vb`;
      confirm via the publish log's quality-gate summary or
      `quality/checks/courts.py` run standalone against the new DB).
   3. `list_courts` (MCP tool or `GET /api/courts`) has no `ch_vb` entry, or
      lists it with count 0 — either is correct; a nonzero count is not.
   4. `search_decisions(court='ch_vb')` / `search_decisions("Wortlaut der Motion")`
      returns zero rows, not an error.
   5. A known retired id, e.g. `GET /entscheid/ch_vb_90000341` or
      `GET /api/decisions/ch_vb_90000341`, returns 404 with a helpful body —
      not a 500. (Regression-tested offline; this is the live-corpus check.)
   6. `make verify` / `make verify-offline` headline numbers (950k floor
      unaffected — ch_vb was 23,046 of ~1.08M rows, 2.1%).
   7. Regenerate the representation manifest (ch_vb's byte-identical-duplicate
      rule in `scripts/build_representation_manifest.py` becomes a no-op,
      0 members — not an error); HF parquet at the next upload (no per-court
      logic there to update); paper tables
      (`docs/paper/*/tables/corpus_graph_stats.json`) at the next refresh.
5. Send the two letters (addresses to verify first). The re-sourced VPB then
   arrives as an offline ingest `scrapers/ch_vb.py` → direct `ch_vb.jsonl` keyed
   on `VPB <vol>.<n>`, and `ch_vb` goes back into `EXPECTED_FEDERAL` and the
   intake policy's `expected_stale` entry comes out.
6. Separately, once reviewed: land the BJ Berichte-und-Gutachten scraper (see
   "Not part of this change" above) on its own branch/PR.
7. Rollback of step 2: move the file back; the next full rebuild (or the next
   quick-publish, which globs the shard directory) restores the rows; growth
   never trips a gate.

Known, accepted windows (review 2026-09-12, still true): (a) between the
ff-merge and the swap the MCP texts already say "VPB/JAAC: not ingested yet"
while 23,046 ch_vb rows are still served (texts go live at the next worker
recycle, e.g. the Saturday 21:30 UTC practice run's restart); (b)
`scripts/check_intake_rates.py` keeps the `ch_vb` exemption so the draining
rows are not reported STALE; (c) the docs/index.html, docs/coverage/*/index.html
and docs/entscheide/*/index.html register rows are updated in the same change
set and regenerated via `build_page_languages.mjs`, so there is no dual-write
lag for those pages; `docs/stats/*` is a nightly-generated snapshot (asof
whatever the last full rebuild was) that recomputes its own `ch_vb` figure
client-side from `docs/stats.json` at generation time — it is not hand-edited
here and will show `ch_vb: 0` once the retiring rebuild runs.

## Open decisions for Jonas

- Delete the retired shard bytes rather than archive them (they were obtained
  via a robots-disallowed path).
- The eight federal `es_*` shards frozen on 2026-04-29 (es_bge, es_bger,
  es_bvger, …) sit under the same "nothing from entscheidsuche" policy; out of
  scope here, flagged only.
- Whether the BJ Gutachten belong in practice.db (proposed) or in
  decisions.db — moot until that scraper lands as its own change (see "Not
  part of this change").
- Send the BAR letter together with the historical-Botschaften request.
