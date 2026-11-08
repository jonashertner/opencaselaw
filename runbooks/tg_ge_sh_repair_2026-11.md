# Runbook: TG yearbooks, GE case numbers, SH markup and twins, TA_SST scans (2026-11-08)

**Status: code and offline tests written in worktree `beautiful-ellis-d39612`, nothing
committed, pushed or run on the VPS. Every repair below was rehearsed on copies in the
session scratchpad. Applying any of them to production shards needs Jonas to name that
step.** Source: items #6, #7, #10, #12 of
`docs/claude/entscheidsuche-comparison-2026-11-08.md` (untracked in the main checkout).

## What changes

| Area | File | Kind |
|---|---|---|
| TG fold on (series, volume, number) | `build_fts5.py` `_fold_tg_yearbook_twins`, `_fold_dockets` | pipeline-critical |
| SH fold ignores a leading "Nr." | `build_fts5.py` `_cross_court_dedup`, `_fold_dockets` | pipeline-critical |
| TG scraper: UTF-8, head block, signature | `scrapers/cantonal/tg_gerichte.py` | scraper behaviour |
| GE scraper: case number as `docket_number_2` | `scrapers/cantonal/ge_gerichte.py` | scraper behaviour |
| SH scraper: strip label/headline markup | `scrapers/cantonal/sh_gerichte.py` | scraper behaviour |
| TA_SST scraper: OCR scans before caching a gap | `scrapers/ta_sst.py` | scraper behaviour |
| Shard repairs (dry run by default) | `scripts/repair_tg_gerichte.py`, `scripts/backfill_ge_case_numbers.py`, `scripts/repair_sh_markup.py` | data |
| Tests | `tests/test_tg_gerichte_entries.py`, `tests/test_ge_sh_tasst_identity.py` | offline |

## 1. Thurgau (RBOG / TVR)

### Root causes (measured on the live shard, copied 2026-11-08 13:12 CET)

1. **Encoding.** rechtsprechung.tg.ch sends `Content-Type: text/html` with no charset;
   `requests` decodes as ISO-8859-1. **2,285 of 2,304** `tg_gerichte` rows hold
   UTF-8-read-as-Latin-1 text ("EntschÃ¤digung", "Â§ 88 KESV"). Search for any word
   with an umlaut missed these rows. The fix parses `r.content` (BeautifulSoup honours
   `<meta charset="UTF-8">`).
2. **Dates.** "MÃ¤rz" never matched the month table, the fallback took the last date
   in the text, and the final fallback was 1 January of the volume: **960 rows on
   1 January**, 17 before 1990 (TVR 2008 Nr. 19 = the taxpayer's birthday 1928-06-14).
   Every entry ends with its own signature line, which the scraper now reads:
   `Obergericht, 1. Abteilung, 13. Juni 2016, KES.2016.32` or
   `Entscheid des Verwaltungsgerichts VG.2024.42/E vom 11. Juni 2025`. A yearbook
   entry without one (it ends with the BGer judgment, or the source truncates the
   year: "vom 25. März 202") stays **undated** instead of 1 January. A PDF ruling is
   dated by its caption ("hat am 14. September 2022 … entschieden"; VG.2022.61 carried
   the BGer's 2023-11-09).
3. **Regeste.** The scraper never stored one. The court's head block (bold subject line,
   provisions, italic Leitsatz where present) is now the regeste, verbatim, one part per
   line. It is the court's own text in the BGE Regeste layout (Stichwort, Normen,
   Leitsatz).
4. **Underlying docket.** Not stored. The signature's docket (`KES.2016.32`,
   `VG.2024.42/E`, `STRE.2021.117 und STRE.2021.118`) is now `docket_number_2`, as printed.
5. **Overwritten dockets.** The scraper replaced `docket_number` with the first case
   number in the text's first 1,000 characters: **25 yearbook rows** carry a ruling they
   cite (`tg_gerichte_RBOG-2017-15` → "ZBR.2010.70"). The yearbook docket is kept now.
6. **Twins.** `tg_gerichte` "RBOG-2016-8" and the frozen es `tg_obergericht` "RBOG 2016
   Nr. 8" carry different dates, so `_cross_court_dedup` (keyed on docket + date) kept
   both. `_fold_tg_yearbook_twins` folds on (series, volume, number), keeps the direct
   row unless its text is a failed extraction (< 300 chars) and the es copy's is not,
   and aliases the dropped id (`tg_yearbook_twin`).

The es side is no ground truth for dates: where both carry one, 378 of 1,831 differ,
and in the samples checked the es date was the decision under appeal or the BGer's
(TVR 2025 Nr. 12: es 2026-03-11 = 9C_442/2025; signature 11. Juni 2025).

### Rehearsal (copies of `tg_gerichte.jsonl`, `es_tg_obergericht.jsonl`, `sh_*.jsonl`)

Build replica, `OCL_EARLY_STATS_PUSH=0`, 4 shards. The HEAD build reproduces the
production counts exactly (tg_gerichte 1,162, tg_obergericht 2,443, sh 717/718).

| | HEAD | new code |
|---|---:|---:|
| tg_gerichte served | 1,162 | 2,279 |
| tg_obergericht served | 2,443 | 32 |
| distinct yearbook entries served | 2,260 | 2,260 (0 lost) |
| entries served more than once | 1,238 | 5 → 0 after the shard repair (see below) |
| per-court swap gate | | **passes**: tg_obergericht 2,443 → 32, 2,411 answered through an alias (100 %) |

The 5 left before the repair are es-only twins (`RBOG 2000 Nr. 05` and `RBOG 2000 Nr.
5`) whose direct copies carried an overwritten docket (point 5).

Shard repair (`scripts/repair_tg_gerichte.py`; 2,285 rows rebuilt from re-fetched
pages, 19 PDF rulings from their stored text with the encoding undone; 1 page 404:
`og/zr-2025-16-zur-publikation-vorgesehen`, kept from its stored text):

| | before | after |
|---|---:|---:|
| mojibake rows | 2,285 | 0 |
| regeste filled | 0 | 2,282 |
| docket_number_2 filled | 0 | 1,736 |
| dates on 1 January | 960 | 0 |
| dates before 1990 | 17 | 0 |
| undated (no signature of its own) | 0 | 26 |
| yearbook rows whose docket is not their entry's | 50 | 0 |

The 50 rows: 25 under their yearbook id whose docket the old scraper replaced with a
case number it cites, and 25 more whose id was minted from that cited number
(`tg_gerichte_ZR.2010.10` next to `tg_gerichte_RBOG-2010-8`, same entry page). The
repair gives both the entry's docket; the fold serves the yearbook-id row and aliases
the other (without the second half the repair would have split 25 entries into two
served rows: today the build's same-docket dedup folds them).

The re-read pages equal the stored texts with the encoding undone except for a stray
"×" glyph and page chrome the old extraction captured (median length ratio 0.9999); one
row grows (RBOG 2016 Nr. 13: stored 1,324 chars, page 24,564). Decision dates from the
page and from the repaired stored text agree on 2,284 of 2,285 rows (the other is that
truncated one).

**Final TG rehearsal** (repaired `tg_gerichte` + frozen `es_tg_obergericht` + repaired
SH shards, build replica):

| | HEAD | final |
|---|---:|---:|
| tg_gerichte / tg_obergericht served | 1,162 / 2,443 | 2,279 / 2 |
| yearbook entries served | 2,260 | 2,260 (0 lost) |
| entries served more than once | 1,238 | 0 |
| entries on the portal (all volumes 1994–2025, crawled 2026-11-08) / served | 2,260 / 2,260 | 2,260 / 2,260, all under `tg_gerichte` |
| served yearbook rows with regeste / docket_number_2 | 0 / 0 | 2,257 / 1,711 |
| served yearbook rows on 1 January / undated | | 0 / 26 |
| per-court gate | | **passes**: tg_obergericht 2,443 → 2, 2,441 answered through an alias (100 %); sh_gerichte 717 → 105 (98.2 %) |

Sample (each value read from the entry page; dates match its signature line):
`tg_gerichte_RBOG-2024-3` 2024-01-31, KES.2023.41, Obergericht, 1. Abteilung;
`tg_gerichte_TVR-2016-23` 2016-08-24, VG.2016.63/E; `tg_gerichte_RBOG-2022-32`
2021-12-16, SW.2021.129; `tg_gerichte_RBOG-1996-7` 1995-10-17, ZB 95 14;
`tg_gerichte_TVR-2005-18` 2005-07-13 ("Entscheid vom …", no docket printed);
`tg_gerichte_TVR-2025-12` 2025-06-11, VG.2024.42/E (es: 2026-03-11, the BGer's date).

### Deploy (Jonas)

Order matters: the scraper fix before the shard repair (a nightly TG scrape after the
repair would otherwise append mojibake again only for new entries; held ones are
skipped via state, so either order is safe, but deploy the code first).

1. Merge + VPS ff-merge as usual; `make test` green first.
2. Outside the build window, with no `tg_gerichte` scrape running:
   ```
   cd /opt/caselaw/repo
   python3 scripts/repair_tg_gerichte.py --fetch --cache /mnt/HC_Volume_104655575/tmp/tg-cache \
       --shard /mnt/HC_Volume_104655575/output/decisions/tg_gerichte.jsonl
   python3 scripts/repair_tg_gerichte.py --cache /mnt/HC_Volume_104655575/tmp/tg-cache \
       --shard /mnt/HC_Volume_104655575/output/decisions/tg_gerichte.jsonl \
       --report /mnt/HC_Volume_104655575/tmp/tg-repair-report.json --examples 10
   ```
   **Stop** unless the dry run shows `mojibake_rows` 0, `yearbook_date_jan1` 0,
   `yearbook_docket_not_own` 0, `changed:docket_number` 50, `rows` 2,304 (or the current count) and no
   `refusing`. Then the same command with `--apply`.
3. `ls /mnt/HC_Volume_104655575/output/decisions/*.jsonl | grep -E '\..+\.jsonl$'`
   must list nothing new (backup is `tg_gerichte.jsonl.bak-tg-YYYYMMDD`, `.tmp` is
   removed).
4. Gate: no override needed (rehearsed: alias credit covers tg_obergericht).
5. After the next build: `tg_gerichte` ≈ 2,280, `tg_obergericht` ≈ 30; spot-check
   `get_decision tg_gerichte_TVR-2008-19` (2008-12-17), `tg_gerichte_RBOG-2017-15`
   (docket RBOG-2017-15, docket_number_2 ZBR.2016.66), and that
   `tg_obergericht_RBOG_2016_Nr._8` resolves to `tg_gerichte_RBOG-2016-8`.

Rollback: `mv tg_gerichte.jsonl.bak-tg-YYYYMMDD tg_gerichte.jsonl` before the build;
the fold alone (code) is safe on the old shard (rehearsed: HEAD shards + new code).

## 2. Geneva case numbers

**Root cause.** justice.ge.ch prints the case number (`C/16444/2023`, `A/3880/2025`,
`P/…`, `AC/…`, `CAPJ/…`) in each result block's flag `<div class="decis-block__flag">`.
The scraper read that div only for a "Fiche N" it no longer carries. `docket_number_2`
is empty on **all 94,492 direct rows** (all years, not only 2024–26) and on the 76,903 es
rows.

**Scraper fix.** `parse_case_number(flag_text, ruling_number)` → `docket_number_2`.
Checked on 13 listing samples across 11 sections, 2003–2026: 13/13 carry a case number.

**Backfill.** `scripts/backfill_ge_case_numbers.py`: `--crawl` pages through the
official listing (19 sections × 1995–2026, page_size 500; 705 pages, ~20 min at 1.5 s),
then fills `docket_number_2` by `decision_id` where it is empty. Only that field
changes; ge_gerichte is not in `DECISION_NUMBER_COURTS`, so no dedup key moves (the
script refuses otherwise).

Rehearsal (crawl 2026-11-08, map 94,087 ruling numbers, 0 conflicts; dry run against
the HF mirror's ids of 2026-11-07):

| | rows |
|---|---:|
| direct rows filled | **94,076 of 94,492 (99.6 %)** — 2026: 4,757, 2025: 6,295, 2024: 6,507, 2000–2023: 74,666, pre-2000: 1,851 |
| not in today's listing | 416 |
| es rows (`es_ge_gerichte.jsonl`) | 0 (their ids use other docket forms; left alone) |

Source check: 8 random fills against the ruling's own detail page
(`/apps/decis/fr/<section>/show/<id>`): 8/8 print the same case number.

Deploy (Jonas), outside the build window, no GE scrape running (2.7 GB shard):
```
python3 scripts/backfill_ge_case_numbers.py --crawl --map /mnt/HC_Volume_104655575/tmp/ge_case_map.jsonl
python3 scripts/backfill_ge_case_numbers.py --map /mnt/HC_Volume_104655575/tmp/ge_case_map.jsonl \
    --shard /mnt/HC_Volume_104655575/output/decisions/ge_gerichte.jsonl --examples 5
```
Stop unless `filled` ≈ 94,000 and `conflict` 0. Then `--apply` (backup
`ge_gerichte.jsonl.bak-ge-case-YYYYMMDD`, a hard link). No gate effect (no row moves).
The local map from the rehearsal can be copied up instead of re-crawling.

## 3. Schaffhausen

**Markup.** The CMS label and headline sometimes carry editor HTML. In the shards:
`sh_gerichte` 18 dockets (7 tags + 11 `&nbsp;`), the same markup in their
decision_ids, 220 titles (`… – <br>Wahl …`); es `sh_obergericht` 16 dockets, 30
regestes. The scraper now cleans label and headline (`clean_html_text`) and treats the
old markup id as known (`legacy_decision_id`), so nothing is fetched twice.
`scripts/repair_sh_markup.py` cleans the held rows; the 18 direct rows get the clean id
with `previous_decision_id` (aliased by the build). es ids stay (three polluted es rows
already have a clean twin in the same shard).

**Twins (new finding).** 613 SH rulings were served more than once (618 surplus rows): direct `Nr. 60/2018/18` and
es `60/2018/18`, same date. `_cross_court_dedup` stripped "Nr." only for TG (scoped
there in 6f7b2867 to stop `ANR.2024.1`/`A.2024.1` in SG). SH now drops a **leading**
"Nr." only (SH dockets are numeric). The fuller row is kept, which is the es copy in
all 618 folded pairs: **396 direct rows hold only their headline** because their stored
PDF URLs (`/CMS/get/file/<uuid>`) now return 404 (two checked). Follow-up below.

Rehearsal (markup repair applied to copies, then build replica):

| | HEAD | after |
|---|---:|---:|
| sh_gerichte / sh_obergericht served | 717 / 718 | 105 / 712 |
| distinct (docket, date) served | 817 | 817 (0 lost) |
| keys served more than once | 613 (618 surplus rows) | 0 |
| rows with markup in docket/title | 31 | 0 |
| served SH rows with < 300 chars of text | 431 | 46 |
| per-court swap gate | | **passes**: sh_gerichte 717 → 105, 599 answered through an alias (98.2 %) |

Deploy (Jonas), outside the build window:
```
python3 scripts/repair_sh_markup.py \
    --shard /mnt/HC_Volume_104655575/output/decisions/sh_gerichte.jsonl \
    --shard /mnt/HC_Volume_104655575/output/decisions/es_sh_obergericht.jsonl --examples 5
```
Stop unless `changed:decision_id` 18 (direct) and 0 (es) with no `refusing`; then
`--apply`. The SH fold is code; the gate passes without an override (rehearsed).

## 4. Regeste placement (ZH SVG, BS, ZH OG): evaluated, not done

- **ZH OG**: `title` is the proceeding's subject (`Erbteilung`, `Ehescheidung`,
  `Organisationsmangel`); the real Leitsätze are already extracted into `regeste`
  (1,064 rows, `_extract_headnote`).
- **BS**: `title` is the Gegenstand plus status (`vorzeitigen Strafvollzug`, `IVG`,
  `(nicht rechtskräftig)`, `Vollzugsbefehl`).
- **ZH SVG**: `title` is the portal's `betreff`, a subject line, plus `(BGE …)` and the
  Weiterzug note that the scraper appends.

`get_regeste` is documented and used as the court's head-note and is a verbatim
quotation source (R1–R3). Copying these titles would present subjects, status notes
and our own appendices as a Regeste. `title:` is already an FTS column filter
(`FTS_COLUMNS`), so `title:Kündigung` finds them today. Not copied.

## 5. Freshness and leftovers (verified against the sources)

| Item | Finding | Gap? |
|---|---|---|
| OW "stops 2022-12-19" | The newest published volume is OGVE 2018/19 (biennial: 2014/15 out 01-2017, 2016/17 out 12-2018). 2022-12-19 is the portal's load day, which 4 rows (OGVE 2018/19 Nr. 45, Nr. 73, Anhang, +1) still carry as their date; real ruling dates end 2019/2020. No OGVE 2020/21 on rechtsprechung.ow.ch or ow.ch. | no (court lag); 4 date defects |
| LU "stops 2026-06-08" | gerichte.lu.ch is current: the newest valid id is EnId 11117 (2C 25 21, 2026-06-08), held; 11118–11170 are empty. 11116 (7H 24 61) is held live (mirror lag). 10960/10968 are held in shard/state but folded at build by their same-docket, same-date siblings. | no |
| LU dates (new finding) | ~1,191 direct LU rows carry a date taken from the text in `decision_date`, while the portal's Entscheiddatum sits in `publication_date` (992 differ by a year or more). Cause: `scripts/repair_decision_dates.py` (2026-03-12) treated LU's date as a publication date. Checked on the portal: EnId 11092 7H 24 52 portal 14.01.2025, ours 2024-02-22; 11096 08.07.2025 vs 2020-07-01; 11079 19.05.2025 vs 2016-06-29; 10969 03.06.2022 vs 2007-03-06. | yes, metadata (follow-up) |
| ZH Baurekurs ~245 | All 1,230 residuals are held: 1,175 by PDF file name, 20 by (series, chamber, number, year), 28 by the docket in their audit record (a second PDF, e.g. `2018-38.pdf` = BRGE IV Nr. 0097/2017), 7 "Zwischenentscheid" entries = held multi-number rulings (BRGE I Nrn. 0041-0042/2021 …). | no |
| SZ VG II. Kammer 4 | II 2023 13, II 2021 53, II 2021 13, II 2020 62 are on gerichte.sz.ch/vg (full walk of the Tribuna listing, 2,255 entries, 2026-11-08: positions 689, 1,170, 1,269, 1,453) and in neither our state nor the shard. Discovery stops after 200 consecutive known entries (`CONSECUTIVE_KNOWN_LIMIT`) and skips the scan when portal total ≤ state count, so entries deep in the listing are never reached. Fix: one run with the existing switch, `OCL_SCRAPER_RESCAN_ALL=1 python3 run_scraper.py sz_verwaltungsgericht` (outside the build window; ~2,255 requests). | yes → one full walk (ops) |
| TA_SST disciplinary 7 | Worse than reported: **29** DK/CD rulings (2016–2024) listed on sportstribunal.ch/rechtsprechung are missing. All are scans with no text layer (5 checked: 0 chars); the scraper cached each as a gap, re-probed weekly, never stored (145 lines in `state/ta_sst.gaps.jsonl`). Fix: OCR fallback via `scrapers/pdf_ocr.py` (Tesseract 5 deu/fra/ita is on the VPS). The gaps expire after 7 days, so the next probe ingests them; no state edit. | yes → fixed in code |
| VPB 2009–13 18 | All 18 held as `ch_vb_<id>` (e.g. `ch_vb_150000158` = JAAC 2009.10a); keyed by id on our side. | no |
| BS 3 | VD.2026.3, SB.2022.72, KE.2026.28 are live (published 11-07, scraped 11-08). | no |
| TG 61 | All held; their ids zero-pad the number (`RBOG-2025-Nr--01`). | no |

## Follow-ups (not done here)

1. **LU dates**: restore `decision_date` from the portal's Entscheiddatum for direct rows
   (the value is in `publication_date` for rows scraped before 2026-03-12), with a shard
   repair like `scripts/restore_ow_rulings.py`.
2. **SH text**: 396 direct rows hold only a headline; their PDF links 404. Re-resolve the
   file via the CMS item (`sliderguid`) and re-fetch.
3. **GE appeal link**: the listing prints the decision under appeal ("sur
   JTBL/1257/2025"); the scraper does not store it (`appealed_docket` exists).
4. **OW**: 4 rows dated with the portal load day 2022-12-19.
5. **Tribuna early stop**: the `total <= known` shortcut and the 200-known early stop
   hide entries the portal adds deep in its listing (SZ above). A periodic full walk
   (e.g. weekly `OCL_SCRAPER_RESCAN_ALL=1` for the Tribuna courts) would catch them.
6. **TG**: the 26 undated yearbook entries print no date of their own (they end with
   the BGer judgment, or the source truncates the year); their date could only come from
   another document.

## Tests

`make test`: 6,224 passed, 22 skipped, 1 failed: `test_real_tesseract_reads_a_rendered_page`
(local environment: `tesseract` binary present, `pytesseract` missing from the venv;
unrelated to this change). `make verify-offline`: green. New: 82 tests in the two files above.
