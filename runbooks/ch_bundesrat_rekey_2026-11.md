# Runbook: `ch_bundesrat`, one row and one stable id per decision (2026-11-08)

**Status 2026-11-08: code and repair script on branch `claude/frosty-lumiere-1c8c37`, not
pushed. Rehearsed on copies of the production shards (copied 2026-11-08 18:46 CET). The
production shard repair is Jonas's step (VPS data mutation).** Source of the finding:
`docs/claude/entscheidsuche-parity-addendum-2026-11-08.md` §2 and §3 (main checkout).

## What was wrong

bj.admin.ch lists 19 entries under "Beschwerdeentscheide des Bundesrates" (15 Federal
Council decisions, 2012 to 2025-10-08, plus four EJPD letters filed on the same page). The
corpus served them as **39 rows**, one per URL generation of the BJ's site:

| Generation | Rows | Id shape | Source |
|---|---:|---|---|
| es shard (entscheidsuche, 2026-02-13) | 14 | `ch_bundesrat__bj_de_home_publiservice_publikationen_beschwerdeentscheide_2025-10-08.html` | `es_ch_bundesrat.jsonl`, frozen |
| direct, old URL scheme (2026-02-23) | 10 | `ch_bundesrat_2025-10-08`, `ch_bundesrat_2025-06-131` | `ch_bundesrat.jsonl` |
| direct, flat slugs (2026-06-19) | 15 | `ch_bundesrat_2025-10-08-kanton-aargau` | `ch_bundesrat.jsonl` |

- The old scheme's dockets were the URL file names (`2025-06-131`, `2025-10-080`: the CMS
  appended digits to same-day pages), the es dockets the URL paths.
- The scraper's slug matcher took only `entscheid-des-bundesrates-vom-…`, so the four EJPD
  letters (`schreiben-des-ejpd-vom-…`, `ueberweisung-ejpd-an-eda-vom-…` ×2,
  `zwischenverfuegung-des-ejpd-vom-…`) were held only in the two older generations.
- Wrong dates: the old generations took the first date in the text, usually the contested
  ruling's. The 2025-10-08 decision was dated 2025-05-07 (Regierungsratsbeschluss), the
  2025-10-08 PCHR decision 2021-07-06, 2025-06-13 (EDA) 2024-12-20, the 2024-10-04 letter
  2007-12-07, the 2024-09-20 letter 2024-01-15, the 2020-03-06 decision (es) 2017-10-17.
- Titles of the flat-slug rows carried the link label: `Mehr über «Entscheid …»`.

## The fix

**Scraper (`scrapers/ch_bundesrat.py`).**
- Takes every slug family on the page (`_KIND_TYPES`; decision_type Beschwerdeentscheid /
  Zwischenverfügung / Überweisung / Schreiben).
- Id and docket `YYYY-MM-DD-<descriptor>` from the slug; a slug without a parseable date
  is logged as `ERROR` and skipped. The raw-slug fallback that produced URL dockets is gone.
  Two entries mapping to one docket: `ERROR`, the second is not taken. An empty listing
  raises.
- Date: the decision's own place-date line ("Bern, 13. Juni 2025", "Berne, le 7 novembre
  2018", the letterhead of an EJPD letter); when the PDF has none (stamped date, OCR), the
  detail page's download entry ("Bundesrat, Bern, 8. Oktober 2025"), then the slug. A
  disagreement between text and listing is logged.
- Title from the card heading / the detail page's `h1`; publication date ("Veröffentlicht
  am"), keywords (the "Stichwörter" section) into `legal_area`.
- `pdf_stem()`: the BJ's PDF file stem, identical under all three URL schemes. This is
  what pairs the 39 rows.

**Repair (`scripts/ch_bundesrat_rekey.py`)**, table `runbooks/ch_bundesrat_rekey_2026-11.tsv`
(the 19 listing entries with stem, docket, type, listed date, detail page, PDF, keywords,
title, read 2026-11-08 with the scraper's own parsers). Per decision:
- keeps one row in `ch_bundesrat.jsonl` under `ch_bundesrat_<docket>`: the flat-slug row,
  else the old-scheme row re-keyed (the four letters). Date from the text, else listed;
  metadata from the table;
- `previous_decision_id` = the old-scheme id, else the es id;
- drops the other direct rows of the decision;
- re-keys every es row onto the same id/docket/date, with its own old id as
  `previous_decision_id`. The build imports direct shards first, so the es copy is skipped
  as the same decision (same canonical key).
- dry run by default; `--apply` rewrites both shards atomically (`scripts/shard_rewrite.py`:
  `.tmp`, line count, refuses if the shard changed meanwhile, hard-linked
  `*.bak-bundesrat-rekey-<date>`). Idempotent: a second run changes nothing.

## Rehearsal (copies of `ch_bundesrat.jsonl` 25 rows, `es_ch_bundesrat.jsonl` 14 rows)

Dry run, then `--apply`, then a second `--apply` (no-op), then `build_fts5.py --full-rebuild`
on a scratch output dir (`OCL_EARLY_STATS_PUSH=0`, HEAD unchanged afterwards):

| | before | after |
|---|---:|---|
| rows served (`ch_bundesrat`) | 39 | **19** |
| direct shard rows | 25 | 19 (6 dropped, 4 re-keyed, all 19 refreshed) |
| es shard rows | 14 | 14 (all re-keyed, skipped by the build) |
| dates changed on kept rows | | 2 (2024-09-20 letter: 2024-01-15 → 2024-09-20; 2024-10-04 letter: 2007-12-07 → 2024-10-04); the other wrong dates sat on dropped rows |
| `decision_id_aliases` | 0 | **24** with the build commit (18 without it), source `ch_bundesrat_rekey`, 0 dangling |
| old ids not resolving | | 0 (6 es ids without the build commit, below) |

A fresh live `run_scraper.py ch_bundesrat` into scratch output/state dirs (2026-11-08 19:15
CET, `+19 new, 19/19`, 0 errors, 0 warnings, 1.3 min) yields exactly the 19 ids of the repaired
shard, with the same docket, date, type, title, keywords, publication date and PDF URL.

All 19 kept rows: date = the decision's place-date line where OCR kept it (12), else the BJ's
listed date (7), which equals the slug date in every case.

**The 6 ids, and the build change (commit "build: a skipped duplicate still records its former id" on this
branch, pipeline-gated: Jonas takes or drops it).** 24 old ids exist
(10 old-scheme + 14 es) and a row carries one `previous_decision_id`. Six decisions have both
an old-scheme and an es id (2025-02-12, 2025-06-13 EDA, 2025-10-08 Aargau, and the letters
2024-07-09, 2024-09-20, 2024-10-04 DFAE); their es ids ride on the re-keyed es copies, which
the build skips without recording the alias. `insert_decision` records a row's
`previous_decision_id` only when the row is inserted. Proposal: record it too when an
incoming row is skipped as a true duplicate (same id, same canonical key), test in
`tests/test_decision_id_aliases.py`:

```diff
--- build_fts5.py
+++ build_fts5.py
@@ -2583,6 +2583,9 @@
                     existing_len, len(incoming_ft),
                 )
                 return True
+            # The copy's own former id (previous_decision_id) still names
+            # this decision: keep it resolving (ch_bundesrat re-key, 2026-11).
+            _record_previous_id(conn, row)
             return False  # true duplicate, no richer text — skip as before
```

Rehearsed with the branch's `build_fts5.py`: 19 rows, **24 aliases**, 0 dangling (AG fold
rehearsal unchanged: 205). It is generic: any skipped duplicate that names a former id keeps
it resolving; `INSERT OR IGNORE` on the alias table, so a repeat is harmless. Without it, the
repair is still correct; only those 6 es URLs stop resolving.

## Production steps (Jonas)

Window: after the nightly publish has exited, no publish and no `ch_bundesrat` scrape
running (`systemctl is-active opencaselaw-publish.service opencaselaw-publish-incremental.service`
both `inactive`), before 03:30 UTC.

1. Deploy the code (push, then on the VPS):

   ```bash
   cd /opt/caselaw/repo && git fetch && git merge --ff-only origin/main
   ```

2. Dry run on the live shards (small files, seconds):

   ```bash
   cd /opt/caselaw/repo && D=$(readlink -f output/decisions)
   python3 scripts/ch_bundesrat_rekey.py --direct "$D"/ch_bundesrat.jsonl --es "$D"/es_ch_bundesrat.jsonl
   ```

   Expect (the script counts aliases by mechanism; with the build commit deployed the 6
   `es_skipped_duplicate` ones resolve too): `served before: 39 after: 19`, `decisions: 19`, `direct_rows_dropped: 6`,
   `es_rows_rekeyed: 14`, `aliases_previous_decision_id: 18`, `aliases_es_skipped_duplicate: 6`,
   `aliases_lost: 0`, `unmapped_direct: 0`, `unmapped_es: 0`.
   **Stop if** anything is unmapped, or the counts differ. (If the nightly scrape with the new
   code ran first, the shard holds 4 more rows, the letters under their new ids: then expect
   `direct_before: 29`, `direct_rows_dropped: 10`, same 19 decisions and aliases.)

3. Apply:

   ```bash
   python3 scripts/ch_bundesrat_rekey.py --direct "$D"/ch_bundesrat.jsonl --es "$D"/es_ch_bundesrat.jsonl --apply
   ```

   Backups: `"$D"/ch_bundesrat.jsonl.bak-bundesrat-rekey-<date>`,
   `"$D"/es_ch_bundesrat.jsonl.bak-bundesrat-rekey-<date>`. Rollback = move them back.

4. After the next build:
   - `/api/decisions?court=ch_bundesrat` (or `get_statistics`) shows 19.
   - `get_decision("ch_bundesrat_2025-10-08")` resolves to `ch_bundesrat_2025-10-08-kanton-aargau`
     (`resolved_via: previous_decision_id`), dated 2025-10-08.
   - Gates: 39 → 19 rows is below the per-court gate's 500-row floor; no override needed.

5. The next `ch_bundesrat` scrape fetches the four letter entries once (8 requests): their
   ids are in the shard, so nothing is appended, and state records them.

## Not in this change

- Deploying the build commit is Jonas's call (it touches `build_fts5.py`); it must reach the VPS no
  later than the build that first reads the re-keyed shards, or the 6 es ids miss one night.
- Archiving the source PDFs (parity addendum §3: links rot, the BJ changed schemes twice).
