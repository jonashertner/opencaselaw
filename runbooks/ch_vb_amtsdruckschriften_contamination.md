# ch_vb: keep the VPB/JAAC series, drop the rest of the entscheidsuche CH_VB crawl

Status: 2026-10-27 — REVERSED from "retire the whole shard" to "keep the VPB
rows only". Code and wording on branch `claude/vpb-keep` (off origin/main
4b2a66f2); the shard filter `scripts/ch_vb_keep_vpb.py` dry-run against the
live VPS shard, NOT applied (section 4b, Jonas).

Decision recorded 2026-10-27 (Jonas): the VPB is in the corpus and stays
there; keep the 2,632 VPB rows, drop the Bundesblatt / Amtliches Bulletin /
Amtliche Sammlung rows from `ch_vb`, correct the public wording, take the
shard-archive step out of this runbook. This supersedes the 2026-09-11
decision ("nothing from entscheidsuche for this segment; retire the shard"),
which #79 (580ad932, 7fe4bb4b, merged 2026-10-25) implemented in code and
wording; its data step (moving the shard) was never run, so the VPB rows were
live the whole time while the server and the site said they were absent.

## Bottom line

`court='ch_vb'` is fed by exactly one frozen shard,
`output/decisions/es_ch_vb.jsonl`, which the entscheidsuche `CH_VB` spider
built by crawling the Federal Archives' amtsdruckschriften portal wholesale.
Census 2026-10-27 (VPS shard, read-only):

- 23,055 rows; 2,632 carry a VPB/JAAC portal id (prefix 15), over **2,502
  distinct PDFs**. The raw feed lists 8,737 prefix-15 entries, but those are
  the same 2,502 PDFs, each listed 2–7 times (once per language header):
  **every VPB PDF the feed knew is in the shard**, all with full text.
- Vols. 51–70 (1987–2006): every number from 1 to the volume's highest is
  present except **VPB 70.32**. 2007–2017 (`VPB <year>.<n>`, from 2008 often
  several numbers in one quarterly-issue PDF): every number present except
  **VPB 2014.3**. A number past a year's highest held one would not show in
  this check; the issues' tables of contents are the way to rule that out.
- The other 20,423 rows (Bundesblatt 12,261, Amtliches Bulletin 7,091,
  Amtliche Sammlung 833, small collections 238) are not VPB and are dropped.
  130 further rows are copies of a VPB PDF under junk dockets (raw `<td>`
  cells, the bare portal id, text fragments such as "terne Abklärung") and are
  folded into one row per PDF.

After `scripts/ch_vb_keep_vpb.py --apply`: 2,502 rows, 2,386 of them with a
`VPB/JAAC x.y` docket.

## 1. Sizing as of 2026-09-11 (record; see the bottom line for the 2026-10-27 census)

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
filter drops. The 2,632 VPB rows are the series itself: the "~8,700
documents" read off the feed in September were feed entries, not documents
(2,502 PDFs, see the bottom line).

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
  repeated). Keeping the VPB rows keeps that provenance (see "Open decisions").

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

### 4a. Record: what #79 changed (merged 2026-10-25), and what 2026-10-27 reverses

2026-10-27 reverses: `ch_vb` back in `EXPECTED_FEDERAL`; the intake exemption's reason
(ended series, frozen VPB shard); every "pending an official delivery" text
(server instructions, routing row, `search_practice` tail, README, dataset card,
homepage register row, coverage and decision-browser pages, coverage notes,
dashboard label, directory submission) now names the series and `court='ch_vb'`.
Kept from #79: `CH_VB` stays out of `SPIDER_MAP` and the download lists (the
shard is frozen, a re-ingest would bring the BBl/AB pages back), the
case-insensitive `OCL_SWAP_GATE_EXEMPT`, and the 404 tests for dropped ids.
The #79 record follows unchanged.

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

Run steps 1–3 between the pipeline's exit (~17:00 UTC) and the next 03:30 UTC
full build, not during the 20:00 UTC incremental. Incremental builds never
delete rows, so the drop takes effect at the next full build.

1. Commit + push `claude/vpb-keep`; on the VPS, fast-forward:
   ```
   ssh -i ~/.ssh/caselaw root@46.225.212.40 'cd /opt/caselaw/repo && git fetch -q && git merge --ff-only origin/main && git log -1 --oneline'
   ```
2. Dry run, then apply the filter (keeps a backup `es_ch_vb.jsonl.bak-pre-vpb-only`
   next to the shard, replaces the shard atomically). The dry run must print
   `rows 23055 -> 2502 VPB/JAAC PDFs (2386 with a VPB/JAAC x.y docket)`; stop if not.
   ```
   ssh -i ~/.ssh/caselaw root@46.225.212.40 'cd /opt/caselaw/repo && python3 scripts/ch_vb_keep_vpb.py'
   ```
   ```
   ssh -i ~/.ssh/caselaw root@46.225.212.40 'cd /opt/caselaw/repo && python3 scripts/ch_vb_keep_vpb.py --apply && ls -la output/decisions/es_ch_vb.jsonl*'
   ```
3. One-run gate exemption for the next FULL rebuild (ch_vb shrinks 23,046 →
   2,502, below its 80% floor). Set in `/opt/caselaw/repo/.env.publish` (the
   publish unit's `EnvironmentFile=`), not a systemd drop-in. Look first:
   ```
   ssh -i ~/.ssh/caselaw root@46.225.212.40 'grep -n OCL_SWAP_GATE_EXEMPT /opt/caselaw/repo/.env.publish'
   ```
   No line: add `OCL_SWAP_GATE_EXEMPT=ch_vb`. A line: append `,ch_vb`. The
   global row gate stays armed and passes on its own (−20,553 of ~1.05M rows,
   2%, against a 95% floor). Do NOT use `OCL_SKIP_SWAP_GATE=1`. Remove the
   entry after the build has swapped and the pipeline has exited.
4. Verify after the swap:
   1. Publish log: `pre-swap per-court gate: exempting ch_vb for this run via
      OCL_SWAP_GATE_EXEMPT` and `pre-swap per-court gate: ch_vb 23046 → 2502
      rows, exempt` (±the build's own dedup).
   2. QC gate green, `courts.federal_present` includes `ch_vb`.
   3. `search_decisions(court='ch_vb', query='Verwaltungspraxis')` returns VPB
      rows (e.g. JAAC 70.103); `search_decisions(court='ch_vb', query='"Wortlaut
      der Motion"')` returns none.
   4. A dropped id, e.g. `GET /entscheid/ch_vb_90000341`, returns 404, not 500.
   5. `make verify` headline numbers (floor unaffected, 2% of rows).
   6. Representation manifest, HF parquet and paper tables pick up the new
      count at their next refresh.
5. Rollback of step 2: `mv es_ch_vb.jsonl.bak-pre-vpb-only es_ch_vb.jsonl`;
   the next full rebuild restores the rows (growth never trips a gate).

Accepted window: between the ff-merge (texts live at the next worker
recycle) and the swap, the texts say "2,500 VPB rulings" while `ch_vb` still
also serves the 20,423 non-VPB rows it has served since March.

## 5. Closing the last gaps

- **VPB 70.32 and VPB 2014.3**: not in any shard PDF. Fetch the two by hand
  (a person on amtsdruckschriften.bar.admin.ch or bj.admin.ch; robots.txt
  binds crawlers, not a reader) or ask the Bundeskanzlei KAV for exactly these
  two; ingest as two rows keyed `VPB 70.32` / `VPB 2014.3`.
- **Tail check**: compare each volume's highest held number with the issue's
  table of contents (vols. 51–70: the annual register; 2008–2017: the
  quarterly issue PDFs, which are in the shard). Not done yet.
- The September letters (BAR bulk delivery "~8,700 documents", BK KAV source
  files) were never sent and rest on the feed-entry miscount: do not send them
  as drafted. At most a short request for the two missing numbers.

## Open decisions for Jonas

- Provenance: the VPB PDF texts were fetched in March 2026 from the portal's
  `/viewOrigDoc/` path, which robots.txt disallows (Art. 5 URG: the content
  itself is unprotected). Kept on 2026-10-27; do not fetch from that path
  again. The backup `es_ch_vb.jsonl.bak-pre-vpb-only` (the dropped BBl/AB/AS
  rows) can be deleted once the swap has verified.
- The eight federal `es_*` shards frozen on 2026-04-29 (es_bge, es_bger,
  es_bvger, …) sit under the same "nothing from entscheidsuche" policy; out of
  scope here, flagged only.
- Whether the BJ Gutachten belong in practice.db (proposed) or in
  decisions.db — moot until that scraper lands as its own change (see "Not
  part of this change").
- Whether a separate, rewritten BAR letter is still wanted for the
  historical Botschaften alone (the VPB part is moot, see section 5).
