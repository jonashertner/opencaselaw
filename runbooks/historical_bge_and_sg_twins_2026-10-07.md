# Historical BGE page ranges and St. Gallen twins (user report 2026-10-07)

**Report.** An external user checked the Hugging Face dataset (`voilaj/swiss-caselaw`
commit `f7d5c459`, 2026-10-06) and the API, and found six things. All six were confirmed on
2026-10-07 through the MCP tools (`get_decision`, `get_decision_structure`):

1. Historical BGE (volumes 1-79) rows overlap their neighbours: BGE 78 IV 83 (Fyg
   gegen Born) opens with the last page of No 21 and ends with the head of No 23
   (Friedlin). 1,336 of 14,578 rows carry two or more ruling headers (user's count).
2. `get_decision_structure` gives BGE 78 IV 83 one "Erwägung 23", which is No 23's
   serial number and text. The Sachverhalt is No 23's as well, and so are `statutes`
   (Art. 148/306 StGB).
3. The API dates BGE 78 IV 83 to 1951-07-17 (`extracted_from_text`), a letter quoted
   in No 21. Its header reads "vom 3. Juni 1952". The ECLI key carries 1951.
4. Dataset ids `bge_historical_78_IV_83` return "Decision not found" from the API, which
   serves the row as `bge_78_IV_83`.
5. St. Gallen rulings are held twice (`sg_gerichte_*` from entscheidsuche, plus the direct
   `sg_publikationen_*`), with two dates. VZ.2004.35 is dated 2004-08-10, the lower
   court's ruling; BZ.2006.83 is dated 2008-07-04, the Kassationsgericht's dismissal.
6. The dataset and the API hold different rows and courts for the same SG ids.

## Root causes

| # | cause | where |
|---|---|---|
| 1 | DFR serves each ruling as its scanned page range in double-page spreads; the scraper stored it verbatim | `scrapers/bge_historical.py` |
| 2 | the extractor read "23. Auszug aus dem Urteil ..." as an Erwägung marker; the unnumbered "Aus den Erwägungen" body precedes it and is dropped; depth-0 paragraphs are never written | `search_stack/extract_decision_structure.py` |
| 3 | `derive_from_text.extract_text_date` takes the first date of the head; the in-build pass `apply_to_db` gates by volume (±1 y lets 1951 through for 1952); the sidecar writer `run_write` had **no gate at all**; the API applied the sidecar date even over a real row date | `backfill_canonical_identity.py`, `mcp_server.py` |
| 4 | `build_fts5.ID_PREFIX_REMAP` serves `bge_historical_*` as `bge_*`; the resolver did not know the scraper form | `mcp_server._bge_ref_candidates` |
| 5 | `scripts/repair_decision_dates.py --all` (2026-03-12) took the first "Urteil/Entscheid vom" date of SG texts; `_cross_court_dedup` keys on docket **and date**, so a misdated twin survives | shards `sg_publikationen.jsonl`, `es_sg_gerichte.jsonl` |
| 6 | see "Dataset" below: the HF files do not come from the current export path | `export_parquet.py`, publish step 3/4 |

## Code changes (this branch)

- `bge_historical_segment.py` (new, pure): places the ruling's own header on its
  reference page (page-number lines; OCR-tolerant) and cuts the text there and at the next
  ruling's header; dates the ruling from that header only. If it cannot place the header,
  it leaves the text alone, so the 1875-1880 Fraktur OCR is never cut. Calibrated on 15
  served rows from 1875 to 1952 (DE/FR/IT, including garbled headers such as "47. A.rtet du
  3 Juin 1882" and "32. Sll1teDl& 24 ottobre 1919"), then validated on all 14,578 published
  rows and 48 rows read against the DFR scans (see "Validation" below).
- `scrapers/bge_historical.py`: new and re-fetched rows are stored cut, with the
  header date.
- `scripts/segment_bge_historical.py` (new): repairs the existing shard (see Steps).
- `backfill_canonical_identity.gate_bge_text_date`: one gate for both the in-build pass and
  the sidecar writer. In volumes 1-79 only the own header's date counts. Later volumes keep
  the existing volume window, which the sidecar writer now gets too (it had none).
- `mcp_server.py`: a sidecar date that disagrees with a real row date is stale and is no
  longer served (nor is its date-bearing key). `bge_historical_*` ids resolve.
- `search_stack/extract_decision_structure.py`: volumes 1-79 are cut to the own ruling
  before extraction; a ruling heading is never an Erwägung and ends the paragraph before
  it. `bge_historical_segment.py` joins the extractor version hash, **so the next
  incremental structure run bootstraps (full re-extraction)**. Every change to the
  extractor already triggers this; plan for the run time.
- `scripts/restore_sg_dates.py` (new): restores SG dates from the court's own citation
  trailer ("(Kantonsgericht, ..., 14. Februar 2005, VZ.2004.35)") or the Kopfzeile, only
  where they name the row's docket.
- `build_fts5._cross_court_dedup`: the id of a folded twin is recorded in
  `decision_id_aliases` (`source='cross_court_dedup'`), so `sg_gerichte_VZ.2004.35` keeps
  resolving to the copy that is kept. This applies to every overlap group (ZH, AG, SG, ...).
- `scripts/repair_decision_dates.py`: refuses or skips `bge`, `es_bge`, `bge_historical`,
  `sg_publikationen` and `es_sg_gerichte`, the shards it is known to damage.

## Steps on the VPS (after review and merge; never during the build window)

Prerequisite: `make test` and `make verify-offline` pass, and the deploy has gone through
`scripts/agent_safe_deploy.py` / the normal deploy. Before each `--apply`, take a copy of
the shard (the BGE script also writes an undo file).

```bash
# 1. dry runs: read the counts and the examples before applying
python3 scripts/segment_bge_historical.py output/decisions/bge_historical.jsonl --examples 40
python3 scripts/restore_sg_dates.py output/decisions/sg_publikationen.jsonl --examples 40
python3 scripts/restore_sg_dates.py output/decisions/es_sg_gerichte.jsonl --examples 40
```

Expected from the dry runs (measured 2026-10-07 on shards rebuilt from the published
parquet files; see "Validation"; the server shard should land between the two columns):

| `segment_bge_historical.py` | March shard export (`bge_historical.parquet`, dates as stored then) | current text (`data/bge.parquet`, vol 1-79) |
|---|---:|---:|
| rows | 14,578 | 14,578 |
| `outcome:placed` | 10,671 (73.2 %) | 10,926 (74.9 %) |
| `outcome:own_header_not_placed` | 3,907 | 3,652 |
| `text_cut` (`cut_before` / `cut_after`) | 6,359 (6,331 / 3,822) | 6,609 (6,581 / 4,030) |
| `chars_removed` | 15.8 M of 195.9 M | 17.1 M of 206.1 M |
| `date:own_header` | 8,093 | 8,269 |
| `date:placeholder` | 2,578 | 2,649 |
| `date:keep` | 0 | 8 |
| `date_changed` | 5,967 | 3,317 |
| `rows_changed` | 9,085 | 7,280 |
| second run after `--apply`: `rows_changed` | 1 (BGE 79 III 159, see below) | 0 |

Stop and look before `--apply` if `outcome:placed` is far outside 10,600-11,000 or `date:keep`
exceeds a few dozen. Every `date:placeholder` row had an unreadable header date (OCR noise in the
day, month or year); spot-check five. `outcome:own_header_not_placed` covers the Fraktur pages,
headers OCR destroyed, pages without a ruling (Kreisschreiben) and the refusals listed in
"Validation".

SG (shards rebuilt from `data/sg_gerichte.parquet` and `data/sg_publikationen.parquet` +
`data/sg_kantonsgericht.parquet`; the parquet files carry no `date_extraction` column, so here
every corrected row counts as unstamped; on the server the 03-12 rows carry the stamp and move
from `unstamped_disagree` to `own_date:*`):

| `restore_sg_dates.py` | `sg_publikationen.jsonl` | `es_sg_gerichte.jsonl` |
|---|---:|---:|
| `sg_rows` | 1,713 | 3,141 |
| `agrees` | 514 | 1,313 |
| `no_own_date` | 750 | 1,774 |
| `conflict` | 0 | 11 |
| `unstamped_disagree` (default) | 449 | 43 |
| of which `publication_date` = own date (the 03-12 trace) | 430 | 11 |
| with `--include-unstamped`: `own_date:regeste_trailer` / `:kopfzeile` | 449 / 0 | 32 / 11 |

Expected on the server without `--include-unstamped`: about 430 `own_date:regeste_trailer` in
`sg_publikationen` (the rows with the 03-12 trace) and an `unstamped_disagree` remainder of
about 19 + 32 + 11 rows. Those and the 11 conflicts are listed in
`historical_bge_and_sg_twins_2026-10-07.sg_cases.tsv`; do not use `--include-unstamped` before
someone has read them (see "Validation", SG).

```bash
# 2. apply (shards are the build's input)
cp output/decisions/bge_historical.jsonl /tmp/bge_historical.jsonl.bak-20261007
python3 scripts/segment_bge_historical.py output/decisions/bge_historical.jsonl --apply
python3 scripts/restore_sg_dates.py output/decisions/sg_publikationen.jsonl --apply
python3 scripts/restore_sg_dates.py output/decisions/es_sg_gerichte.jsonl --apply
# 3. next full build picks them up (quick_publish does not re-read changed rows)
# 4. regenerate the canonical-identity sidecar from the new decisions.db, at the path
#    the MCP server reads (CANONICAL_DB_PATH = $SWISS_CASELAW_CANONICAL_DB or
#    $SWISS_CASELAW_DIR/canonical_identity.db); write a temp file, then rename
D="$SWISS_CASELAW_DIR"
python3 backfill_canonical_identity.py --db "$D/decisions.db" --write "$D/canonical_identity.db.tmp" \
  && mv "$D/canonical_identity.db.tmp" "$D/canonical_identity.db"
```

Step 4 is still needed after step 3, for two reasons. The API now ignores a stale sidecar
date only where the row carries a real date. And the sidecar's ECLI keys keep the old year
until they are rebuilt.

## Verification after the build

- `get_decision('bge_78_IV_83')`: text starts "22. Auszug aus dem Urteil des
  Kassationshofes vom 3. Juni 1952", contains no "Friedlin", date 1952-06-03, ECLI year 1952.
- `get_decision_structure('bge_78_IV_83')`: no Erwägung "23"; the Sachverhalt is not
  No 23's.
- `get_decision('bge_historical_78_IV_83')` resolves.
- `cite('VZ.2004.35')` and `cite('BZ.2006.83')`: one row each, dated 14. Februar 2005 and
  16. Oktober 2007. `get_decision('sg_gerichte_VZ.2004.35')` resolves to the kept copy.
- Search `"Prozessbetrug" court:bge date 1952`: BGE 78 IV 84 only, not 78 IV 83.

## Dataset (findings 4 and 6)

Read 2026-10-07 from the Hugging Face repo itself (`HfApi.get_paths_info(..., expand=True)`,
last commit per path; repo head `f7d5c459`, 2026-10-06 22:54 UTC, the commit the user named):

| path | last commit (UTC) | content |
|---|---|---|
| `bge_historical.parquet` (repo **root**) | `b4746827` 2026-03-14 06:41 "Update dataset (full rebuild 2026-03-14)" | 14,578 rows, court `bge_historical`, ids `bge_historical_*`, scraped 2026-03-03, BGE 78 IV 83 dated 1952-01-01 (shard placeholder) |
| `data/bge_historical.parquet` | does not exist (404) | — |
| `data/bge.parquet` | `72a3f4a6` 2026-09-28 19:16 | 49,254 rows, all `bge`; the 14,578 historical rows as `bge_*`; `bge_78_IV_83` present, dated 1951-07-17, text uncut (7,468 chars) |
| `bge.parquet` (root) | `b4746827` 2026-03-14 | 21,228 rows (20,718 `bge_BGE_*` from entscheidsuche, 510 `bge_*`), no historical row |
| `data/sg_gerichte.parquet` | `2b3ed48f` 2026-09-03 | 3,141 rows |
| `data/sg_publikationen.parquet` | `fc363653` 2026-08-26 | 630 rows |
| `data/sg_kantonsgericht.parquet` | `6aa10826` 2026-10-01 | 1,083 rows, all `sg_publikationen_*` ids |
| `sg_gerichte.parquet`, `sg_publikationen.parquet`, `sg_kantonsgericht.parquet` (root) | `b4746827` 2026-03-14 | 3,795 / 658 / 114 rows |
| `structure/structure.parquet` | `1d37aa20` 2026-09-10 00:03 | 1,131,622 rows; the historical rows as `bge_historical_*` (14,578) |
| `structure/erwaegungen_paragraphs.parquet` | `12c2b8f1` 2026-10-04 22:25 | 10,889,828 rows; historical rows as `bge_*`; `bge_78_IV_83` has one paragraph, `e_number` "23", which is the heading of No 23 |

Findings:

- **The user's files are stale root-level leftovers, not the current export.** The repo root holds
  99 parquet files last written on 2026-03-03 (5) and 2026-03-14 (94) by a manual upload
  (commit titles "Update dataset (...)", not produced by `publish.py`). Since 2026-02-14
  (`0c97e054`) `publish.py` uploads to `data/` and prunes only `data/*.parquet`
  (`delete_patterns` relative to `path_in_repo="data"`), so the root files are never replaced
  or removed. The dataset card's config reads `data/*.parquet`, so `load_dataset` does not
  see them, but a direct download of `bge_historical.parquet` gets the March shard export.
  `data/bge_historical.parquet` is therefore not stale and not written by the JSONL fallback:
  it does not exist. The suspected JSONL-fallback run is not needed to explain finding 4.
- The 106/105 St. Gallen count reproduces exactly from the stale root files
  (`sg_gerichte.parquet` against `sg_publikationen.parquet`, exact docket: 106 shared, 105
  dated differently). The current `data/` files are worse: 580 shared dockets (581
  normalised), 579 dated differently (see "Validation", SG).
- `bge_78_IV_83` is in `data/bge.parquet` (the current export), with the bad date and the
  uncut text, as served by the API.
- `structure/structure.parquet` is stale since the 2026-09-09/10 build, as described below:
  its last change is 2026-09-10 00:03 UTC, and it still carries the shard-era
  `bge_historical_*` ids while the 2026-10-04 paragraph export carries `bge_*`.

`structure/structure.parquet` is a separate cause, and it is known. Since the sidecar has
been built from served text (2026-09-09), the metadata export projects over its budget and
keeps the last good file (`export_parquet._bounded_stream`). The published structure.parquet
is therefore the shard-era one, with `bge_historical_*` ids, while the Sunday paragraphs
export is current, with `bge_*` ids. This is the id mismatch in finding 2. The proposed fix
(not in this branch, schema change to the sidecar): store the four has-section flags as
small columns at extraction time, or index them as expressions, so the export no longer
walks the text overflow pages.

Proposed (needs approval, writes to the public HF repo): delete the 99 stale root-level
parquet files in one commit (`HfApi.delete_files(..., delete_patterns=["*.parquet"])` scoped
to the root, never `data/`, `graph/`, `structure/` or `artifacts/`), and say so in the dataset
card's changelog. Until then every root file is a March 2026 snapshot.

## Validation 2026-10-07 (published data, DFR scans, live API)

Method: the published parquet files (above) were downloaded into an empty directory and
rebuilt as JSONL shards (`bge_historical.parquet` as is; the volume 1-79 rows of
`data/bge.parquet` with court and id mapped back to `bge_historical`). The two differ: 7,267
of 14,578 texts changed since March (5,790 only in whitespace, 1,477 longer), and 6,190 dates
differ. Both shards were run through `segment_bge_historical.py` (dry run) and a per-row
analysis; 48 rows, stratified by outcome, were read against the live API and the DFR PDFs
(`https://www.fallrecht.ch/c<S><VVV><PPP>.pdf`), header crops of the scan image where the
text layer was ambiguous. Row-by-row verdicts: `historical_bge_and_sg_twins_2026-10-07.bge_sample.tsv`.

The user's "1,336 of 14,578 rows with two or more ruling headers" depends on the header
pattern used and could not be reproduced exactly: plain regexes give 898-2,354 rows; the
confirmed headers of `bge_historical_segment.ruling_headers` (rules of `d364e0b`) give 3,358
(March text) and 3,536 (current text). With the final rules 6,359-6,609 rows (44-45 %) carry a neighbour's text
before or after their own ruling and are cut.

**Defects found in the branch as received (`d364e0b`) and fixed** (each with an offline test
in `tests/test_bge_historical_segment.py`; eleven tests fail on `d364e0b`: ten new ones and
the extended `keep` test; one new test pins the limit of rule 1 and passes on both). In the
48-row sample, 7 rows got a wrong date or a neighbour's ruling from `d364e0b`; with the final
rules none does (they are correct, or left whole / on the placeholder):

1. Right-hand pages read header first. The text layer can put the page number after the
   header ("73. Arret du 21 Septembre 1888 ... / contre Hotfmann. / 479"), so the own header
   counted as standing on the page before and the next ruling was taken (BGE 14 I 479 → No 74
   and 31 Aug 1888; 70 II 75 → No 13; 50 I 134 → No 28). 333 rows show it, all on odd pages,
   the number within five lines of the header. A header followed that closely by the reference
   page's own odd number now stands on it. This reading is not extended to other pages: a
   header can end a page (BGE 4 I 60).
2. No page bound without a later page number. Any header up to the end of the text counted as
   on the reference page (BGE 44 III 163 took No 45 from 7,267 characters on). The own header
   must now lie within one page (3,000 characters; median page 1,970-2,190, 99th percentile
   2,727) of where the reference page starts; a next ruling beyond that ends the own one even
   when the page numbers between are lost (BGE 40 III 332).
3. Two candidate headers and no reference-page number: the first was taken, which can be a
   short previous ruling (4 of 13 such rows). Now refused.
4. A heading of the own ruling that OCR destroyed ("35. Auszug aus dem Besohluss vom aa.
   September 1Sa1 / i. S. Bürer." on BGE 47 III 116; "7. Auszug sous dem Urteil" on 45 I 54;
   Fraktur "141. ... 1875" on 1 I 520) let the next ruling be taken, with its date, and the own
   ruling's text be cut away. A heading numbered one less than the chosen header on the
   reference page now refuses the placement (16 rows; consecutive rows sharing a serial fell
   from 33 to 13 pairs, the rest mostly source duplicates or OCR'd serials).
5. Day numbers with OCR noise glued on were read without their lost digit: "22. Sentenza !3
   aprile 1914" is 2 April (scan), "64. Sentenza. a2 ottobre 1914" is 22 October (scan),
   "1.7 décembre" is the 17th. A one-digit day glued to a non-space character (except "/" of a
   hearing range "1./2. Dezember" and the Italian elision "dell'8") now gives no date (104
   rows go to the placeholder instead of a wrong day).
6. `keep` checked only day and year: BGE 54 II 464 ("vom 12~ Dezember 1928") would have kept
   1928-10-12. A readable month must now match, and a stored date outside the volume window
   is never kept (46 March-shard rows stored OCR years such as "16. Juli 1991" in volume 47).
7. OCR spellings that left whole volumes unplaced: the volume 28-39 OCR opens each text with
   "N. Arteil vom ..." ("Arleil", "Arkeil", "Eutscheid", "Urheil", ...; 1,248 rows, their stored
   dates often wrong: BGE 38 II 745 stored 1912-07-10, scan 18. Oktober 1912; 28 II 1 stored
   1901-10-04, scan 22. Januar 1902); "'Urteil", "T1rteilvom", "Orteil", "Arrit"/"Arrät" in
   volumes 31-64; "dans la causa / cattse / canse / eause" as parties.
8. The next-ruling line search read the own header's date line as the next ruling: "9.
   Auszug aus dem Urteil des Kassationshofes vom / 10. März 1926 i. S. ..." (BGE 52 I 54)
   was cut after its first line whenever the reference page's number was missing, and BGE
   55 I 79 lost the end of its Dispositiv at "14. Dezember 1928 richtet, ...". The search now
   starts after the own header's lines and skips a number followed by a month. Found by
   running the repair twice: the second run cut 52 I 54 again (the first had the page
   number); after the fix a second run changes nothing on the current text.
9. Header years one after the volume year or three before it are OCR: 8 of 8 and 4 of 4
   checked on the scans were misread ("21. Dezember 1915" read as 1916, BGE 41 II 739;
   1928 as 1925, 54 III 268; 1908 as 1905, 34 I 334), while 4 of 4 dates of the year before
   were genuine late publications (22 February 1877 in BGE 4 I 147). Own-header dates of
   volumes 1-79 are now accepted only in the volume year and the year before
   (`historical_year_plausible`); 91 dates go to the placeholder. Two of them print the
   later year on the scan too (BGE 1 I 13: "19. Februar 1876"; 16 I 838: "12. Dezember
   1891") and look like further DFR links to the next volume's page.

**Checks over the whole set (current text).** End cuts: of 4,030 rows cut at their end, 3,125
can be checked against the next row of the same volume part; 3,111 end exactly at that row's
serial, 6 at a ruling without a row of its own, 8 differ because the next row's serial is
OCR'd or because the row keeps an extra unrecognised ruling (cut too little); none cuts into
its own ruling. Dates: 24 randomly drawn own-header dates and 19 off-year ones were compared
with the header crop of the scan: after rule 9, no wrong day, month or year among them.
Idempotency: `--apply` then a second dry run changes 0 rows on the current text; on the March
text it changes 1 (BGE 79 III 159, whose March text lacks two pages, so No 37 looked like a
second ruling of page 159 on the first run; the second run cuts it, which is right: No 37 is
BGE 79 III 162).

**Final dry-run figures** are the table under "Steps on the VPS". Placement by volume band
(current text): 1-9 71 %, 10-29 87 %, 30-39 91 %, 40-64 67 %, 65-79 66 %. Outliers (current
text): own text under 30 % of the row 22 rows (all short rulings in the sample), own header in
the second half of the row 12, more than 6,000 characters in 0. Serial-sequence check (within
a volume part, rows ordered by page must carry increasing serials): 10,828 of 10,926 placed
rows consistent, 8,345 exactly one above the previous row, 98 breaks (0.9 %), mostly OCR'd
serials ("07." for 57) and DFR duplicates.

**Residual risk.** The rules cannot see a destroyed own heading when no trace of its number
survives; then the next ruling can still be taken. The serial check bounds this: of the 13
consecutive-row pairs still sharing a serial, about half are DFR duplicates or OCR'd serials;
a few rows may carry the next ruling. Dates of such rows come from that ruling's header.
**Source errors found in passing:** the DFR PDFs for six rows of BGE 52 I (52 I 1, 8, 23, 39,
149, 230) hold rulings of BGE 65 I (1939); the volume gate gives them the placeholder, but their
text is another ruling's. Volume 4 I 370/371, 46 II 76/77 and 62 II 193/194 hold the same text
twice.

**SG.** `date_extraction` is not a column of any published parquet file, so the 03-12 stamp
cannot be checked from the dataset. Analysis with `--include-unstamped`: of the 581 dockets
in both collections (normalised docket), 1 carries the same date today, 461 would afterwards.
The remaining 120: 112 have no own date (trailer or Kopfzeile) on at least one side, 1 is a
conflict, and in 7 both copies have an own date but the two differ. The entscheidsuche Kopfzeile agrees with the citation trailer
in 1,145 of 1,156 rows; the 11 disagreements (3 to 365 days) show that the Kopfzeile date is
sometimes a publication date (AK.2017.192: trailer 13 July 2017, Kopfzeile 21.12.2017). So the
11 Kopfzeile-only corrections carry that risk, and rows where a second source contradicts the
trailer (BES.2019.118: trailer 2021-01-04, portal id and Kopfzeile 2022-01-04) need a person.
All 503 conflict and unstamped rows: `historical_bge_and_sg_twins_2026-10-07.sg_cases.tsv`.

**Live API before deploy** (`https://mcp.opencaselaw.ch/api/decisions/<id>`, 2026-10-07):

| id | state |
|---|---|
| `bge_78_IV_83` | 200; `decision_date` 1951-07-17, `date_provenance` extracted_from_text, `canonical_key` ECLI:CH:BGER:1951:78_IV_83; text 7,468 chars from page 82, contains No 23 (Friedlin) and the 17 July 1951 letter; `statutes` include Art. 148 and 306 StGB (No 23's) |
| `bge_historical_78_IV_83` | 404 "Decision not found" |
| `sg_gerichte_VZ.2004.35` | 200; 2005-02-14, court `sg_gerichte`, entscheidsuche, 701 chars |
| `sg_publikationen_VZ.2004.35` | 200; 2004-08-10, `publication_date` 2005-02-14, court `sg_kantonsgericht`, 17,956 chars |
| `sg_gerichte_BZ.2006.83` | 200; 2008-07-04 (Kopfzeile and trailer: 16.10.2007), 1,310 chars |
| `sg_publikationen_BZ.2006.83` | 200; 2007-10-16, court `sg_kantonsgericht`, 79,491 chars |

Both SG pairs are served twice (`is_canonical` true on all four), each with one wrong date.

## Not done here (proposals)

- An unnumbered Erwägung ("Aus den Erwägungen :" with no numbers, as in BGE 78 IV 83) is
  parsed as paragraph "0" (depth 0), and both structure builders skip depth 0. Serving it
  would make `get_erwaegung` reach such rulings, but `find_relevant_erwaegung` / `cite` would
  then need a rule for pinpointing "E. 0", across all courts. Decide before building.
- `statutes` and the citation graph for the cut rows follow from the next full build, from
  the cut text. No separate step is needed.
