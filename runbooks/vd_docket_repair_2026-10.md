# Runbook: Vaud dockets that are ruling numbers or uuids

**Status: scraper fix, build line and chamber form approved by Jonas 2026-10-30.
Not deployed. `scripts/vd_docket_repair.py` written and rehearsed 2026-11-04 on a
copy of the live shard (below); applying it to the production shard needs Jonas to
name that step.** Counts in "Size" are from the public HF data of 2026-10-29,
read-only.

## Root cause

prestations.vd.ch returns `affaireHit.numero = null` and `cause = null` on every
hit since 2026-09-04. `_parse_search_item` then takes `decisionHit.numero`, which
is the per-year ruling number ("344"), or the uuid when that is empty. f11421f4
fixed the **id** for this case and deliberately left the **docket** as it was
("display value stays real", pinned in test_vd_uuid_identity.py). The follow-up in
`runbooks/vd_uuid_identity_2026-09.md` (read the case number from the PDF header)
was never done.

The listing has no other field with the number (one live search of 2026-10-30).
The number is printed in the PDF header, which `pdfplumber` returns intact
(checked on both evidence PDFs).

**It is getting worse in a way the header cannot fix.** New rulings print the
number masked (`PE25.***-***`): under 1 % of rulings dated 2025-08, 18 % in 2025-11,
79 % in 2025-12, 98 % in 2026-06. With the listing gone too, the court no longer publishes
the case number of new rulings, which looks deliberate. Reading the header repairs
the past; new rulings need another form.

## Size

`vd_gerichte` on HF: 66,313 rows, **12,778 affected (19.3 %)**, not 177.

| Scraped | Rows |
|---|---:|
| Feb 2026 | 793 |
| 09-04 to 09-06 (backfill) | 11,254 |
| other nights | 585 |
| 10-28 | 146 |

Affected = docket is a bare number (12,431), a uuid (183), or a sectioned number
like `111/I` / `47bis` (164). The other 31 of the 177 rows of 10-28 have real
dockets and are untouched.

These are served rows. The raw shard holds more (66,844 lines vs 65,841 served on
10-01), so the dry run will report more than 12,778. That is expected.

## The fix

Ids do not move. Only `docket_number` and `docket_number_2` change.

| Case | docket_number | docket_number_2 | Rows |
|---|---|---|---:|
| Header prints the number | `DA21.000826` | `344` | 9,354 (73.2 %) |
| Masked or absent | `CREP 2021/344` | `344` | 3,413 (26.7 %) |
| No number, no dossier | uuid (unchanged) | uuid | 11 |

Of the 177 rows of 10-28: 49 / 94 / 3, and 31 untouched.

Header accuracy, measured on the 53,103 rows whose docket came from the listing:
of 44,872 headers read, 44,017 (98.1 %) give the same number, 717 (1.6 %) the same
serial under another register prefix (`JS16.041255` / `JI16.041255`), 138 (0.3 %)
a different one. Slash references (`RPE/01/14/0001385/ds`) and 5- or 7-digit
numbers are not read: on those same rows they never matched the court's number.

`CREP 2021/344` is the chamber, year and number, the parts of the form the courts
cite these rulings by (`CREP 7 février 2022/107`: 8,904 such citations in a sample
of 8,000 rulings). It is an identifier we build, approved by Jonas 2026-10-30.

### Why `docket_number_2` and the build_fts5 line are not optional

`_dedup_decisions` Pass 1 deletes all but the longest row of a canonical key
**without reading the text**. One case number carries several rulings:
PE24.013673 has four of 2025-12-01. Ran the real dedup code over all 66,313 rows:

| Scenario | Rows deleted |
|---|---:|
| Today | 0 |
| Dockets rewritten, nothing else | **318 distinct rulings** |
| This proposal | 0 |

So vd_gerichte keys on `docket_number_2`, as BS does. Every repaired row keeps
exactly today's canonical key, which also leaves the cross-feed dedup with
vd_findinfo as it is (it matches on number + date). No held row has a
`docket_number_2` today (0 of 66,313), so the build line alone changes nothing
until rows carry one. The two must never be separated:
`tests/test_vd_docket_header.py::test_two_rulings_of_one_case_on_one_day_keep_apart_keys`
pins it.

## Repair of existing rows (after approval, nothing below has run)

Shard rewrite only. Never `decisions.db`, never `state/` (the uuid sidecar is
unaffected: ids do not change).

1. Land the code. Confirm on the VPS that `vd_gerichte` is in
   `DECISION_NUMBER_COURTS` **before** step 3.
2. `scripts/vd_docket_repair.py` (on the model of `vd_uuid_sidecar.py --dedupe`):
   dry run by default, `--apply` hard-links `<shard>.bak-<date>`, writes
   `<shard>.tmp` (neither matches the build's `*.jsonl` glob) and swaps atomically.
   Selects lines whose docket matches `RULING_NO_RE` or equals the pdf uuid and
   resolves them through `_resolve_docket` with the row's `chamber`. Refuses to
   run unless `vd_gerichte` is in `DECISION_NUMBER_COURTS`, and refuses to write
   unless every line keeps `decision_id`, `full_text`, `scraped_at`, `decision_date`
   and its dedup key, and the line count is unchanged.

   Rehearsed 2026-11-04 on a copy of the live shard (67,529 lines, 01:50 UTC),
   branch code shipped to a quarantine dir:

   | Outcome | Rows |
   |---|---:|
   | `header` (case number from the PDF) | 9,556 |
   | `chamber_year_number` (`CREP 2021/344`) | 3,550 |
   | `stays_uuid` | 11 |
   | **rows_changed** | **13,117** |
   | `real_docket` (untouched) | 54,412 |

   Apply on the copy: line count 67,529 before and after, no `.tmp` left, second
   run 0 changed; `02949903-…` → `DA21.000826` / `344`, `ffa44e63-…` →
   `TF23.022423` / uuid. Derived `branch` and `proceeding_type` identical for all
   13,117 rows.

   ```bash
   ssh -i ~/.ssh/caselaw root@46.225.212.40 'cd /opt/caselaw/repo; python3 -c "import build_fts5; assert \"vd_gerichte\" in build_fts5.DECISION_NUMBER_COURTS; print(\"build line ok\")"; D=/mnt/HC_Volume_104655575/output/decisions; python3 scripts/vd_docket_repair.py --shard $D/vd_gerichte.jsonl --examples 5'
   ssh -i ~/.ssh/caselaw root@46.225.212.40 'cd /opt/caselaw/repo; D=/mnt/HC_Volume_104655575/output/decisions; wc -l < $D/vd_gerichte.jsonl; systemd-run --unit=vd-docket-repair --collect --wait --pipe python3 scripts/vd_docket_repair.py --shard $D/vd_gerichte.jsonl --apply; wc -l < $D/vd_gerichte.jsonl; ls $D/*.tmp 2>/dev/null'
   ```
3. Window: both publish units inactive, no scrape appending. After the 01:00
   scrape's `Done.` and before 03:30, or ~11:00 UTC to the incremental start.
   Dry run, compare with the table above, then apply.
4. The next full build carries it (step 2 always rebuilds from the shards). The
   shard grows, so the queued incremental seeks to its old offset, drops the torn
   line and reads the tail: known ids with unchanged keys, skipped.
5. Verify after the swap: vd_gerichte row count unchanged apart from new rulings,
   per-court gate OK, `vd_gerichte_02949903-ae38-4ad9-833b-7dce612af97d` docket
   `DA21.000826`, `vd_gerichte_ffa44e63-115b-472f-b69a-12896bbe141e` docket
   `TF23.022423`. Delete the backup after that. Add a `docs/MIGRATIONS.md` entry.

Rollback: `mv output/decisions/vd_gerichte.jsonl.bak-<date> output/decisions/vd_gerichte.jsonl`.
Repaired rows keep their `scraped_at`, so they appear in the next full parquet but
in no nightly delta. Integrators on deltas will not see the docket change.

## Known limits

- **591 rows in 273 groups share docket and date with another ruling**, so their
  citation strings become identical and a docket lookup returns one of them
  (checked: `PE24.013673` returns one). Today those strings differ only by a bare
  number. Fixing it means `_build_citation_strings` printing the ruling number for
  these rows: a separate change, not in this diff.
- A full citation string does not resolve back through `get_decision`
  (`Gericht VD DA21.000826 vom 19. April 2021` gives nothing; the bare docket does).
- 11 rows stay on a uuid docket.
- `vd_findinfo` holds 943 bare-number dockets. Same symptom, another feed, not touched.
- 23 rows from deltas since 09-05 are not served. 9 match a vd_findinfo row on
  number and date, so probably folded there. 2 match a same-number, same-date
  ruling of another chamber, so probably lost to Pass 1, which this proposal does
  not cure. 12 are unexplained (11 of them CCIV). Inferred from HF, not from the
  build's logs.
- Why the portal offered 177 rulings of 2009-2026 on one night is not established.
  170 of them have no publication date.
