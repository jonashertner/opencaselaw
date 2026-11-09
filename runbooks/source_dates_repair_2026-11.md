# Runbook: decision dates from the source (comparison report 2026-11-08, item #5)

**Status 2026-11-08: code on main (6b02f801); the Pass-1 alias line (§5.6) landed
after Jonas's OK at 15:40 UTC ("go ahead with all steps … complete and accurate
corpus"). Repairs run per court after the 11-08 publish exits, each `--apply` under
`flock -w 3600 /var/lock/ocl-shard-repair.lock` (other repairs share the night).**

A decision date is a legal fact. Every date below is read from what the court
publishes (the docket it mints from the date, the ruling's own header or
letterhead, the trailer a yearbook prints under each entry, the official file
name or URL slug, the portal's own date field). entscheidsuche was used only as
the audit list that pointed here; its metadata is never a source.

Rules: `source_dates.py` (shared by the scrapers and the repair). Repair:
`scripts/repair_source_dates.py`. TI evidence: `scripts/fetch_ti_decision_dates.py`.
Tests: `tests/test_source_dates.py`, `tests/test_repair_source_dates.py`,
`tests/test_scraper_source_dates.py`.

## 1. What is wrong, per court

Population measured on the whole court, not the report's sample: served rows from
the HF mirror (`voilaj/swiss-caselaw`, light columns, 2026-11-08) and the raw shards
(read-only copies pulled 2026-11-08 ~13:15 UTC, after the 11-08 build's QC gate).
"Report" = the null / 1 January count of item #5; the real defect is often wider.

| Court | Report | Root cause | Defect found in the shards |
|---|---:|---|---|
| `bge_egmr` | 78 of 79 since 2024 | `bge.py` writes EGMR rows into `bge.jsonl`; the listing prints the date as `05.11.26`, which `parse_date` cannot read, so it fell back to `date(year, 1, 1)` (year = today's). `bge_egmr.py` stored None. The 03-12 text pass then wrote body-text dates (a signoff "Paris, 12 décembre 2015") into some | 74 rows of `bge.jsonl`, 69 of `bge_egmr.jsonl`; 30 `_d` twins |
| `gr_gerichte` | 821 | PKG/PVG/POG Praxis entries: the Tribuna register gives an upload or volume date (875 entries carry the scrape day 2026-02-13/14), sometimes the *previous* entry's trailer (PKG 1999 25 stored 1999-12-14, own trailer 20 January 1999). Federation copies are 470-char stubs (`0000-00-00`). Plus Tribuna rows stored before the 2026-09-27 string-table fix | direct 2,802, es 3,121 changed |
| `ti_gerichte` | 233 | Rows stored before 2af5a6e7 (2026-09-15) when the listing's "data decisione" was not read; for ~100 the ruling's heading has no date either; 163 more carry the February scrape day | direct 440, es 19 |
| `ar_gerichte` | 1,182 | AR GVP yearbook entries in the frozen federation shard carry 2021-01-01 (or a 03-12 body date); the direct scraper takes the portal's `decisionDate`, which for GVP entries is sometimes a date the entry only cites (ARGVP 2002 3410: 1995-04-30, the Landsgemeinde decision it applies; its trailer says "JuAK 14.11.02") | direct 252, es 1,608 |
| `ur_gerichte` | 59 | 1905/1901/1913-01-01 portal sentinels in the federation copies; rows scraped 06-20 before the 07-23 header fix; the 03-12 pass replaced 33 portal dates by statute dates ("1. Januar 2022") | direct 32, es 115 |
| `nw_gerichte` | 15 | The listing's `datum` came back empty on 2026-06-24 for 15 rulings; the scraper had no fallback | direct 16, es 1 |
| `bl_gerichte` | 178 | Federation copies: Steuergericht entries on 2021-01-01, slug-dated stubs undated; the 03-12 pass overwrote 5,815 KG dates with body-text dates (same day as the portal's own date in every one of them: the stamp keeps it) | direct 3,833, es 2,371 |
| `be_zivilstraf` | 53 | Before 2e46549d (2026-09-27) base_tribuna read a row's date from the deduplicated GWT string table: ~3,000 rows carry the *contested* decision's date (BK 25 85 stored 2025-01-28 = the Staatsanwaltschaft's Verfügung; the ruling is "Bern, 1. Dezember 2025", head and signature). `restore_tribuna_dates.py` never fixed BE: its heading rule does not match the BE letterhead | direct 1,805, es 1,257 |

TG is the sibling session's. BE BVD/StRK/Direktionen are the BE session's; only
`be_zivilstraf` dates are touched here.

## 2. The rules (`source_dates.py`) and how each was validated

A rule either **fills** (empty, `0000`, a 1 January, a portal sentinel, the scrape
day) or may also **correct** a stored date. Correct only where the rule reads the
court's own statement unambiguously or the stored date is known damage (the
2026-03-12 `repair_decision_dates.py` pass, which stamps `date_extraction`;
Tribuna rows stored before the fix). Text rules are vetoed when the date is before
the docket's registration year or after a stated publication date.

| Court | Rule | Mode | Validation |
|---|---|---|---|
| bge_egmr | docket `YYYYMMDD_appno_yy` | correct | stated in the judgment's own text for **493 of 493** `bge.jsonl` rows; HUDOC `kpdate` agrees for 53377/11 (2016-08-23), 30995/19 (2024-06-13), 2633/23 (2025-03-27) |
| gr_gerichte | Praxis entry: last `<docket>\n<Urteil…> vom <date>` trailer, in the closing part of the text, not after volume + 1; the federation stub takes its direct twin's (same `ServletDownload/PKG_1999_25_` PDF) | correct | the stored text *is* the official PDF; the previous-entry trap and a lost trailer return None (tests). None of the 3,923 yearbook dates trips the build's GR plausibility NULL |
| gr_gerichte | other rows: "Urteil/Entscheid … vom" in the head, before bench and parties | correct pre-fix rows only | agrees with the portal date on **71 of 72** rows scraped after the Tribuna fix (one by a day) |
| ti_gerichte | portal "Data decisione" of the row's own `source_url` (refetched) | fill | it is the portal field the scraper reads today |
| ar_gerichte | GVP: decision date in the official PDF file name | correct | 40 PDFs fetched (30 random + 10 with dates decades before the volume): where the entry prints a trailer, **26 of 27** agree; the 27th (ARGVP 1988 1155) is a composite entry whose last ruling, RRB 8.12.1981, is the file-name date. ARGVP 1988 really collects rulings from 1953 on |
| ur_gerichte | Rechenschaftsbericht line "Obergericht, <date>, <own docket>", else the letterhead head | fill (+03-12 damage) | 33 of 43 corrections restore the portal's own pre-damage date; the rest replace 1905 sentinels. Live: **8 of 8** sampled re-read identically from the fresh ur.ch PDF |
| nw_gerichte | head "Entscheid/Urteil/Beschluss vom", never the "Urteil BGer … vom" appeal note above it | fill (+03-12 damage) | live **7 of 8** re-read identically; the 8th: nw.ch now serves another ruling (VA 25 6) under the publication id we captured VA 25 26 from (§7) |
| bl_gerichte | baselland.ch slug date (fill only); Steuergericht "Entscheid Nr. 61/2003 vom 4.7.2003" (own number); KG head "Urteil des Kantonsgerichts … vom" | fill (+03-12 damage) | **5,815** corrections equal the portal's own pre-damage date; the slug equals an independent stored date on 6,055 of 6,109 rows (99.1 %). baselland.ch slug pages are 404 now, bl.swisslex.ch is a JS app: no cheap live re-read |
| be_zivilstraf | letterhead "Bern, 5. Februar 2026"; corrects only when the signature line repeats it and only rows stored before the fix | correct pre-fix / fill | agrees with the portal date on **65 of 66** post-fix rows (one by a day). Live **8 of 8** re-read identically from the fresh Tribuna PDF. A letterhead without the signature can carry a wrong year (BK 22 322): fill only |

A federation copy with no source of its own still on its twin's old date moves
with the twin (`twin_follow`); without that, 1,286 GR rulings would stop sharing a
canonical key and be served twice.

## 3. Rehearsal (copies of the 2026-11-08 shards, local)

Dry run + `--simulate` (the build's insert collisions and dedup Pass 1 replayed
for the court, before and after; content-aware Pass 2 and the federation fold run
after this and only remove more). "Served" = rows after Pass 1.

| Court | Shard | Lines | Changed | filled | placeholder | corrected | twin_follow | agrees | left alone* | no source | Served before → after (Pass 1) | Folds | Split |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|
| bge_egmr | bge.jsonl | 21,397 | **74** | 0 | 48 | 26 | 0 | 419 | 0 | 0 | 523 → 493 (94.3 %) | 0 | 0 |
|  | bge_egmr.jsonl | 488 | **69** | 13 | 30 | 26 | 0 | 419 | 0 | 0 |  |  |  |
| gr_gerichte | gr_gerichte.jsonl | 14,984 | **2,802** | 5 | 768 | 2,029 | 0 | 8,844 | 270 | 3,068 | 17,460 → 15,744 (90.2 %) | 1,716 | 0 |
|  | es_gr_gerichte.jsonl | 14,300 | **3,121** | 1,193 | 0 | 642 | 1,286 | 2,061 | 58 | 9,060 |  |  |  |
| ti_gerichte | ti_gerichte.jsonl | 59,693 | **440** | 274 | 163 | 3 | 0 | 8 | 0 | 59,245 | 61,784 → 61,398 (99.4 %) | 0 | 0 |
|  | es_ti_gerichte.jsonl | 58,116 | **19** | 0 | 7 | 12 | 0 | 366 | 0 | 57,731 |  |  |  |
| ar_gerichte | ar_gerichte.jsonl | 1,665 | **252** | 0 | 1 | 251 | 0 | 1,413 | 0 | 0 | 4,631 → 4,476 (96.7 %) | 155 | 0 |
|  | es_ar_gerichte.jsonl | 4,322 | **1,608** | 0 | 1,177 | 431 | 0 | 2,713 | 0 | 1 |  |  |  |
| ur_gerichte | ur_gerichte.jsonl | 440 | **32** | 10 | 2 | 20 | 0 | 381 | 3 | 24 | 1,325 → 1,260 (95.1 %) | 65 | 0 |
|  | es_ur_gerichte.jsonl | 1,104 | **115** | 1 | 90 | 23 | 1 | 317 | 3 | 669 |  |  |  |
| nw_gerichte | nw_gerichte.jsonl | 561 | **16** | 15 | 0 | 1 | 0 | 483 | 60 | 2 | 1,069 → 1,069 (100.0 %) | 0 | 0 |
|  | es_nw_gerichte.jsonl | 508 | **1** | 0 | 0 | 1 | 0 | 492 | 0 | 15 |  |  |  |
| bl_gerichte | bl_gerichte.jsonl | 9,431 | **3,833** | 0 | 2 | 3,831 | 0 | 4,481 | 8 | 1,109 | 19,516 → 17,690 (90.6 %) | 1,826 | 0 |
|  | es_bl_gerichte.jsonl | 16,907 | **2,371** | 163 | 120 | 2,063 | 25 | 9,130 | 23 | 5,383 |  |  |  |
| be_zivilstraf | be_zivilstraf.jsonl | 6,057 | **1,805** | 48 | 6 | 1,751 | 0 | 3,803 | 23 | 426 | 6,448 → 6,124 (95.0 %) | 326 | 2 |
|  | es_be_zivilstraf.jsonl | 5,640 | **1,257** | 0 | 1 | 1,256 | 0 | 3,721 | 17 | 645 |  |  |  |

\* differs_left_alone + implausible_left_alone: the stored date stays.

Apply on the copies: every court applied in turn to APFS clones of the copies
(`scripts/repair_source_dates.py --apply`), then dry-run again:

| Court | Rows changed | Lines before = after | Second run | `.tmp` left | Backups |
|---|---:|---:|---:|---:|---:|
| bge_egmr | 143 | 21,885 | 0 | 0 | 2 |
| gr_gerichte | 5,923 | 29,284 | 0 | 0 | 2 |
| ti_gerichte | 459 | 117,809 | 0 | 0 | 2 |
| ar_gerichte | 1,860 | 5,987 | 0 | 0 | 2 |
| ur_gerichte | 147 | 1,544 | 0 | 0 | 2 |
| nw_gerichte | 17 | 1,069 | 0 | 0 | 2 |
| bl_gerichte | 6,204 | 26,338 | 0 | 0 | 2 |
| be_zivilstraf | 3,062 | 11,697 | 0 | 0 | 2 |
| **total** | **17,815** | | | | |

File modes kept (0600). Rows checked in the applied copies: `bge_egmr_20160823_53377_11`
2026-01-01 → 2016-08-23, `gr_gerichte_PKG 1999 25` 1999-12-14 → 1999-01-20 (its
federation stub `0000-00-00` → the same), `be_zivilstraf_BK 2025 85` 2025-01-28 →
2025-12-01, `ar_gerichte_ARGVP_2002_3410` 2021-01-01 → 2002-11-14,
`bl_gerichte_725 18 300_83` 1996-03-08 → 2019-03-28.

The report's own rows (served null / 1 January on HF), after the repair:

| Court | Report rows | Dated from the source | Left | Why left |
|---|---:|---:|---:|---|
| bge_egmr | 78 (live) | all | 0 | |
| gr_gerichte | 821 | 728 | 93 | entry PDF without its trailer (89), no head (4) |
| ti_gerichte | 233 | 233 | 0 | |
| ar_gerichte | 1,182 | 1,173 | 9 | the official file name itself says 1 January |
| ur_gerichte | 59 | 49 | 10 | stubs / excerpts without a date line |
| nw_gerichte | 15 | 15 | 0 | |
| bl_gerichte | 178 | 150 | 28 | stubs without slug, trailer or head (`copy_of_2011/…`) |
| be_zivilstraf | 53 | 49 | 4 | no letterhead (KES/HG layouts) |

TI evidence: 442 document pages refetched (2026-11-08, one per ~3 s), all HTTP 200
with a "Data decisione"; where the ruling's heading states a date it equals the
portal date in 383 of 383. 163 direct TI rows carried the February scrape day
(2026-02-13/14) as their date: a placeholder the report did not count.

`twins_split` > 0 only for BE (2): direct and federation rows of BK 2025 199 and
SK 2017 460 hold different documents with different letterheads, i.e. two
rulings, correctly no longer folded.

Random samples of changed rows per court are in the `--sample` output; the live
re-reads are in §2.

## 4. Gate arithmetic (swap-gate-rekey-trap)

Every court stays above the per-court 80 % gate (lowest: GR 90.2 %, BL 90.6 %).
`/health` decisions will drop by about the sum of the "served" deltas (~4,500):
federation copies folding into their direct twin once both carry the same date
(BL 1,826, GR 1,716, BE 324, AR 155, UR 65), TI `_d` twins (386) and EGMR `_d`
twins (30).
That is duplicate removal, not loss: each fold pair is one docket and one date.

## 5. Production steps (each needs Jonas's OK)

0. **Before anything:** check no other session or cron is armed for the same
   window (`journalctl` for `source-dates-*` units; `ls` the quarantine dir) —
   the 11-04 TI collision.
1. **Land the code** (commit, push, `git fetch && git merge --ff-only origin/main`
   on the VPS, or let the next build's step 6 pull it). The scraper fixes then
   date new rows from the next 01:00 scrape.
2. **TI evidence.** sentenze.ti.ch refuses Hetzner: run the fetcher on the Mac
   against fresh read-only copies of both TI shards (resumable; only rows still
   undated), then copy the file to the quarantine dir:
   ```bash
   python3 scripts/fetch_ti_decision_dates.py <copy>/ti_gerichte.jsonl <copy>/es_ti_gerichte.jsonl --out ti_decision_dates.jsonl
   ```
   ```bash
   scp -i ~/.ssh/caselaw ti_decision_dates.jsonl root@46.225.212.40:/mnt/HC_Volume_104655575/quarantine/source-dates-YYYYMMDD/
   ```
3. **Window.** Both publish units inactive, no scraper appending: after the
   incremental has exited (weekdays ~22:40) and before the 01:00 scrape, or after
   the 01:00 scrape's `Done.` and before 03:30. `bge.jsonl` is also appended by
   the 09:00 federal and 10:20 late scrapers: never a daytime window for bge_egmr.
   Re-check right before each apply:
   ```bash
   ssh -i ~/.ssh/caselaw root@46.225.212.40 'systemctl is-active opencaselaw-publish.service opencaselaw-publish-incremental.service opencaselaw-scrape.service opencaselaw-scrape-federal.service opencaselaw-late-scrapers.service; tail -1 /opt/caselaw/repo/logs/publish.log'
   ```
4. **Per court: dry run, compare with §3, apply.** Counts drift by the rows
   scraped since 11-08 (new rows are already dated by the fixed scrapers, so
   expect `agrees` to grow, not `rows_changed`). Stop if `rows_changed` differs
   from §3 by more than a few rows or `twins_split` is not 0 (BE: 2).
   ```bash
   ssh -i ~/.ssh/caselaw root@46.225.212.40 'cd /opt/caselaw/repo; D=/mnt/HC_Volume_104655575/output/decisions; python3 scripts/repair_source_dates.py --court nw_gerichte $D/nw_gerichte.jsonl $D/es_nw_gerichte.jsonl --simulate'
   ```
   ```bash
   ssh -i ~/.ssh/caselaw root@46.225.212.40 'cd /opt/caselaw/repo; D=/mnt/HC_Volume_104655575/output/decisions; systemd-run --unit=source-dates-nw --collect --wait --pipe --working-directory=/opt/caselaw/repo python3 scripts/repair_source_dates.py --court nw_gerichte $D/nw_gerichte.jsonl $D/es_nw_gerichte.jsonl --apply'
   ```
   Same for `ur_gerichte`, `ar_gerichte`, `bl_gerichte`, `gr_gerichte`,
   `be_zivilstraf`, `ti_gerichte` (add `--evidence /mnt/HC_Volume_104655575/quarantine/source-dates-YYYYMMDD/ti_decision_dates.jsonl`)
   and `bge_egmr` (shards `$D/bge.jsonl $D/bge_egmr.jsonl`). Each apply hard-links
   `<shard>.bak-dates-<UTC date>` (same inode, no space), writes `<shard>.tmp`
   (neither matches the build's `*.jsonl` glob), checks the line count and swaps.
   A second dry run must report `rows_changed` 0.
5. **The next full build carries it** (step 2 rebuilds from the shards). The
   shards grow (the `date_repair` stamp), so a queued incremental seeks to its old
   offset, drops the torn line and re-reads the tail: known ids, skipped; harmless.
6. **Landed 2026-11-08 (before the first build after the repair):** dedup
   Pass 1 deletes without an alias, so the ~4,100 federation ids that fold in Pass 1 stop
   resolving (`bl_gerichte_100_04_732`, `gr_gerichte_PKG_1999_25` …). Two lines in
   `build_fts5._dedup_decisions` keep them resolving and let the swap gate count
   them, like the federation-twin fold already does:
   ```python
   for row in rows[1:]:
       conn.execute("DELETE FROM decisions WHERE decision_id = ?", (row[0],))
       _record_folded_id(conn, row[0], survivor_id, "dedup_pass1")
       deleted += 1
   ```
   (both branches of Pass 1). Written with Jonas's OK; test in
   `tests/test_decision_id_aliases.py`.
7. **Verify after the swap** (MCP or `get_decision`): `bge_egmr_20160823_53377_11`
   2016-08-23, `bge_egmr_20260709_14652_22` 2026-07-09, `gr_gerichte_PKG 1999 25`
   1999-01-20, `be_zivilstraf_BK 2025 85` 2025-12-01, `nw_gerichte_VA 25 26`
   2026-02-23, `ur_gerichte_OG V 26 8` 2026-04-01, `ti_gerichte_60.2012.58`
   2012-05-07, `ar_gerichte_ARGVP_2002_3410` 2002-11-14. Per-court counts as §4.
   Then the canonical-identity sidecar may be regenerated (outside a build); until
   then serving already ignores a sidecar date that disagrees with a real row date
   (`mcp_server.py`, C-2). Delete the backups after the verification; add a
   `docs/MIGRATIONS.md` entry with Jonas's OK.

Rollback per shard: `mv <shard>.bak-dates-<date> <shard>`. Every changed row also
carries `date_repair {from, to, rule, at, prev}`, so the old value is in the row.
Repaired rows keep their `scraped_at`: they reach the next full parquet, not a
nightly delta.

`--fill-only` restricts any court to empty/placeholder dates (the report's scope)
if Jonas wants the corrections staged separately; the corrections are where most
of the wrong dates are (BE ~3,000, BL ~5,900, GR ~2,700).

### 5.8 Result 2026-11-09 and the BL Wayback shard

The night deploy (runbooks/night_2026-11-08_deploy.md step 1.8) applied all eight
courts on 11-08 before midnight UTC; the backups are `<shard>.bak-dates-20261108`
(16 files). Verified on the live DB after the 11-09 swap (11:37 UTC, per-court
gate OK for 137 courts, no warning for any of the eight):

| court | repaired rows | resolve with the repaired date | served 11-08 → 11-09 |
|---|---|---|---|
| bge_egmr | 143 | 143 | 493 → 493 |
| gr_gerichte | 5,923 | 5,923 | 15,930 → 15,267 |
| ti_gerichte | 459 | 459 | 60,978 → 60,737 |
| ar_gerichte | 1,860 | 1,860 | 4,615 → 4,476 |
| ur_gerichte | 147 | 147 | 1,254 → 1,254 |
| nw_gerichte | 17 | 17 | 1,069 → 1,069 |
| bl_gerichte | 6,204 | 6,191 | 19,485 → 17,661 |
| be_zivilstraf | 3,062 | 3,062 | 6,139 → 6,102 |

Every drop is a fold with an alias (dedup_pass1: GR 13,540, BL 8,450, BE 5,574,
AR 1,511, UR 284; federation_twin: TI 661, BL 42); no repaired id is missing. The
§5.7 ids all carry their source date, `gr_gerichte_PKG 1999 25` and
`be_zivilstraf_BK 2025 85` through their new dedup_pass1 alias.

The 13 BL rows are served from a third shard the repair did not cover:
`bl_wayback.jsonl` (903 rows recovered from the Internet Archive, 2026-04-17,
imported as a direct shard ahead of `es_bl_gerichte`). Its dates were wrong in
the same ways (2015-01-25 for "vom 25. Januar 2018", 2018-01-03 for "vom 1. März
2018", Jan-1 placeholders), and 133 undated rows were filled by the build from a
cited date (`2018-11-02-stger-1` served 1995-02-15, its head says "Entscheid vom
2. November 2018"). Rule `bl_slug_and_head`: the slug's date corrects when the
ruling's own head names the same date (two statements of the court); the stub of
the same id follows (`twin_follow`, BL only, only when its own slug names that
date). Rehearsal on copies of the 11-09 shards: bl_wayback 143 (133 filled, 4
placeholders, 6 corrected), es_bl_gerichte 5, bl_gerichte 0; served 17,701 →
17,690 (11 `_d` twins fold back), twins_split 0, second run 0. One row stays:
`2018-05-18-sv-2` (head 8 May, slug 18 May, stored 2018-02-08).

Production step (same window rules as §5.3, under the shared flock):
```bash
ssh -i ~/.ssh/caselaw root@46.225.212.40 'cd /opt/caselaw/repo; D=/mnt/HC_Volume_104655575/output/decisions; python3 scripts/repair_source_dates.py --court bl_gerichte $D/bl_gerichte.jsonl $D/bl_wayback.jsonl $D/es_bl_gerichte.jsonl --simulate'
```
Expect rows_changed bl_wayback 143, es_bl_gerichte 5, bl_gerichte 0, twins_split 0. Then:
```bash
ssh -i ~/.ssh/caselaw root@46.225.212.40 'cd /opt/caselaw/repo; D=/mnt/HC_Volume_104655575/output/decisions; flock -w 3600 /var/lock/ocl-shard-repair.lock systemd-run --unit=source-dates-bl-wayback --collect --wait --pipe --working-directory=/opt/caselaw/repo python3 scripts/repair_source_dates.py --court bl_gerichte $D/bl_gerichte.jsonl $D/bl_wayback.jsonl $D/es_bl_gerichte.jsonl --apply'
```
The es backup of this run is `es_bl_gerichte.jsonl.bak-dates-<UTC date>`; if it is
still 20261108 (same day as the first run) the script refuses: move the 11-08
backup aside first or run on another UTC day.

## 6. Scraper fixes (new rows)

| Scraper | Change |
|---|---|
| `scrapers/bge.py`, `scrapers/bge_egmr.py` | EGMR date from the docket, else the listing's `dd.mm.yy`; never `date(year, 1, 1)` |
| `scrapers/cantonal/gr_gerichte.py` | Praxis entries dated by their trailer; no trailer → undated, not the register's upload date |
| `scrapers/cantonal/ar_gerichte.py` | GVP entries take the file-name date over `decisionDate` |
| `scrapers/cantonal/ur_gerichte.py` | citation line before the header rule when the portal has no date |
| `scrapers/cantonal/nw_gerichte.py` | head fallback when the listing has no `datum` |
| `scrapers/cantonal/be_zivilstraf.py` | letterhead fallback when the register has no date |
| TI | none: already reads "data decisione" (2af5a6e7), 0 undated rows since 09-15 |

`base_tribuna.py` is untouched (the BE session owns the Tribuna scrapers); GR and
BE override `fetch_decision` in their subclasses only.

## 7. Left alone, and follow-ups

- No source in the stored text and no other evidence: GR 322 yearbook rows
  without a trailer (124 direct + 198 stubs stay undated or on a placeholder),
  UR 13 federation stubs, BL 28 stubs, BE 7, AR 1. Each needs a refetch of its
  own document; most federation stubs are duplicates of a dated direct row.
- `differs_left_alone` / `implausible_left_alone` rows (GR 328, NW 60, BE 40, BL 31, UR 6)
  keep their stored date: the rule's date is not strong enough to overrule it.
- nw.ch reuses publication ids: `nw_gerichte_VA 25 26`'s `source_url`
  (publikation/42163) now serves VA 25 6. Stored text and date are consistent;
  the link is stale. Worth a check of all NW `source_url`s.
- The federation stubs that fold are duplicates; the frozen `es_*` shards for
  these courts could be retired once §6 keeps their ids resolving.
- The serving-side canonical-identity sidecar holds wrong text dates for rows
  that are undated today (TI 72.2007.17 served as 2000-09-01, Zuisens SA as
  2015-02-13); repaired rows no longer use them.
