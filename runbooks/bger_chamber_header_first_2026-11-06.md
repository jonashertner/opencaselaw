# BGer chamber: read the ruling's own header (suggestion 0003, 2026-11-06)

Status: code + tests on branch `claude/fervent-rubin-239dd2`, NOT pushed or
deployed. Needs Jonas's OK: `backfill_bger_chambers.py` runs inside
`build_fts5` (step 2, "bger chamber correction" phase).

## Report

A UNIL user's AI compared `bger_4A_594_2026` with entscheidsuche: we had the
ruling sooner but filed it under the "II. Zivilrechtliche Abteilung". The
ruling's header says "I Corte di diritto civile" (4A belongs to the I.
zivilrechtliche Abteilung). The user is right.

## Cause

`scrapers/bger.py::chamber_from_text` tested division names **longest first
across the whole document** and returned the first name found anywhere. The
4A_594 reasons discuss a recusal request against "il Presidente della II Corte
di diritto civile" and a related case pending there. "II Corte di diritto
civile" is longer than "I Corte di diritto civile", so the mention in the
reasons beat the header. The scan also ran on the whole page text, AZA
navigation included.

The same audit found three more defects in the same derivation:

1. **8C/9C names swapped in `ABTEILUNG_MAP`.** Since 2023-01-01, 8C belongs to
   the *IV.* öffentlich-rechtliche Abteilung and 9C to the *III.* (the numerals
   cross: former I./II. sozialrechtliche Abteilung). The headers say so on
   every 8C/9C ruling since (8C_467/2022 of 2023-01-03: "IV."; 9C_595/2022 of
   2023-01-04: "III."). The map had it the other way round. entscheidsuche makes
   the same error in its Kopfzeile, which is probably where it came from.
2. **The entscheidsuche Kopfzeile was read as the header.** Every `es_bger`
   text starts with synthetic lines ("Bundesgericht III. Öffentlich-rechtliche
   Abteilung 12.08.2020 8C 38/2019"). Their division names are
   entscheidsuche's and are wrong for whole series. The build re-derives about
   50k es rows from text each night (#57 backstop), so those wrong names
   reached serving.
3. **Prefix fallback ignored the era and the old docket series.** It stamped
   today's names on pre-2023 rows, and today's divisions on dotted pre-2007
   dockets (7B.64/2000 is the SchKK, not the II. strafrechtliche Abteilung).

## Population (all served BGer rows, 2026-11-06)

Method: the shards on the VPS were streamed read-only (ionice/nice, page
cache dropped behind the reader). No decisions.db access: the incremental was
running. The build's own rules were applied (direct shards first, first copy
wins, docket-code fill at ingest, then the #57 backstop) to simulate the
served value. Spot check: 10/10 simulated values equal the live API. The
simulation predates 3b67068b, whose new twin fold in `build_fts5` may drop
some es duplicates, so the post-build audit is the authoritative count.

| | rows |
|---|---:|
| served bger rows | 194,389 |
| BGG-era dockets (4A_…/2026 shape) | 141,478 |
| dotted pre-2007 dockets | 22,896 (out of scope, unchanged) |
| EVG letter dockets / other | 30,015 (out of scope; 3 rows change) |

Among the BGG-era rows, "wrong" means the row names a different BGer division
than both its docket series and its own header. When the header names another
division, the court said so and the row is not counted ("II. sozialrechtliche
Abteilung (als strafrechtliche Abteilung)", 6B_819/2010).

| BGG-era rows | today | after fix |
|---|---:|---:|
| wrong division | **6,259** | 0 |
| NULL chamber | 5,964 | 0 |
| total off | 12,223 (8.6 %) | 0 |

The 6,259 wrong rows break down as follows:
- **6,052 entscheidsuche-sourced rows**, re-derived each build from text by the
  old scan. Almost all are pre-2023 8C/9C rows that carry the Kopfzeile's
  "III./IV. öffentlich-rechtliche" instead of "I./II. sozialrechtliche":
  2,770 9C, 2,622 8C, plus 432 post-2023 8C rows labelled "III." instead of
  "IV.", and 8F/8D/9F.
- **207 directly scraped rows**, the 4A_594 class: a division mentioned in the
  reasons (often the lower instance's "Verwaltungskommission"). They are spread
  over 2007–2026, 26 of them from 2026. 19 are 4A rows filed under "II.
  Zivilrechtliche". This is why sampling found them isolated: about 0.2 % of
  direct rows.

The 5,964 NULLs are es rows (8C/9C/6B, pre-2023) whose header name the old
scan did not know. After the fix they get the period name.

Other changes the fix makes:
- 4,785 es 6B rows from before 2023-07 are relabelled from "I. Strafrechtliche
  Abteilung" to "Strafrechtliche Abteilung". This is the name in their German
  header. Same division, no change in meaning. Direct-scraped 6B rows were
  already mixed (6,922 vs 10,685).
- 0 dotted pre-2007 rows change, and 3 EVG rows change.
- The swap gate is not affected: no row changes court or decision_id.

## Fix (this branch)

- `chamber_from_text`: skips any entscheidsuche Kopfzeile, then reads only the
  first 1,500 characters of the decision text and returns the division named
  **earliest**. It knows the pre-2023 names (I./II. sozialrechtliche
  Abteilung, "Strafrechtliche Abteilung") and tolerates header spellings such
  as "II.Strafrechtliche" and "II zivilrechtliche". With no division in the
  window it returns None. The scraper then reads the decision text, not the
  page.
- `ABTEILUNG_MAP`: 8C → IV., 9C → III. (signatures unchanged).
- `division_for_docket(docket, date, lang)`: the prefix fallback. It covers
  BGG-era dockets only and uses the name in force on the decision date (8C/9C
  before 2023-01-01, 6B before 2023-07-01).
- Scraper: logs a warning when the header names a division outside the docket
  series. That warning would have caught 4A_594 before the user did.
- `backfill_bger_chambers.py` (build phase), new rule 3: a BGG-era row whose
  stored division contradicts both its series and its own header is
  re-derived. Rules 2 and 3 now take the header when it agrees with the
  series, and otherwise the series' period-correct division. This is
  structural, not a guess; older dockets keep the header-or-NULL rule. The
  return tuple is unchanged, so `build_fts5.py` needs no edit; the rule-3 count
  is logged separately.
- `audit_bger_chambers.py`: read-only census for verification. It refuses to
  run during a build.
- Tests: `tests/test_bger_chamber_header_first.py` (fixtures only, including
  the served 4A_594/2026 text). Also updated:
  `tests/test_backfill_bger_chambers.py` (a silent text on a BGG docket now
  gets the series division, not NULL) and `test_parsing.py` (8C/9C names).
- `make test`: 5,873 passed, 1 failed. The failure is
  `test_real_tesseract_reads_a_rendered_page` (`pytesseract` is not installed
  on this Mac) and is unrelated. `make verify-offline` passes.

## Repair plan

No shard rewrite is needed. Chamber is re-derived from the stored text by the
"bger chamber correction" phase on `decisions.db.tmp` at every full rebuild.
The shards are inputs only, and HF/parquet exports come from the DB. The 207
wrong direct rows stay wrong inside `bger.jsonl`, but every build corrects them
on the way in. A hygiene rewrite of a 1.6 GB shard is not worth its risk.

1. Jonas reviews and merges to main. Then fast-forward the VPS
   (`git fetch && git merge --ff-only origin/main`, py_compile) **before 03:30
   UTC**. The build imports the backfill module at step 2, and the hourly
   poller and late scrapers import the scraper.
2. Swap gate: nothing to do. Chamber-only change; per-court counts
   unchanged; no `OCL_SKIP_SWAP_GATE`.
3. Incremental nights (`quick_publish`) insert new rows without the phase.
   Those rows come from the fixed scraper, so they arrive correct.
4. Verify after the first full build that serves it:
   - `publish.log`: "bger chambers: … re-derived" should read about 50k
     (the es rows, as every night; "cleared to NULL" should fall from about
     5,974 to near 0), plus the rule-3 line at about 199. The other 8 of the
     207 direct rows go through rule 2.
   - API probes:
     - `bger_4A_594_2026` → I. Zivilrechtliche Abteilung
     - `bger_8C_881_2015` → I. sozialrechtliche Abteilung (was III.)
     - `bger_9C_421_2017` → II. sozialrechtliche Abteilung (was IV.)
     - `bger_8C_271_2023` → IV. Öffentlich-rechtliche Abteilung (was III.)
     - `bger_6B_140_2009` → Strafrechtliche Abteilung (was NULL)
     - `bger_9C_377_2011` → II. sozialrechtliche Abteilung (was Verwaltungskommission)
   - After the pipeline AND the incremental have exited:
     `python3 audit_bger_chambers.py --db output/decisions.db`. Expected: wrong
     0, NULL 0. Before the fix the same census reads 6,259 / 5,964.

## Follow-ups (not in this branch)

- **7B transition rows:** a few 7B_…/2022 rulings of July 2023 were decided by
  the I. öffentlich-rechtliche Abteilung (7B_91/2022, header). Rule 1 of #57
  forces every new-series 7B/7F row to the II. strafrechtliche Abteilung.
  About 2 rows.
- **Label normalisation:** stored chambers mix languages ("Ire Cour de droit
  civil", 853 rows) and, for pre-2023 6B, two German names. A canonical
  per-division, per-era label (or a separate `division` column) is a product
  decision.
- **Pre-2007 dotted dockets:** these keep the docket code or the old labels.
  Headers like "I. OEFFENTLICHRECHTLICHE ABTEILUNG" and "Kassationshof" are not
  parsed. This was out of scope.

## Note for the suggester (suggestions_admin.py, post after the first build serving the fix)

> Merci pour ce signalement, vous aviez raison : l'arrêt 4A_594/2026 émane de la Ire Cour de droit civil. Notre extraction retenait la IIe Cour, citée dans les considérants. C'est corrigé, ainsi que d'autres arrêts touchés par la même erreur.
