# Runbook: Vaud CDAP / Tribunal administratif direct scraper (`vd_omni`)

**Status 2026-11-08: approved by Jonas (relayed by the coordinating session,
~15:40 UTC: "You go ahead will all steps as required with the goal to have a
complete and accurate corpus"). Decisions taken there: repair the wrong es
dates, resolve the 5 placeholder rows by hand, do not put Jurivoc keywords
into `regeste`.** Source of the gap:
`docs/claude/entscheidsuche-comparison-2026-11-08.md` item #1 (main checkout,
untracked). Reference for every count: the court portal, never entscheidsuche.

## What was wrong

`vd_omni` holds the rulings of the Tribunal cantonal's Cour de droit
administratif et public (CDAP), of its predecessor until 2007, the Tribunal
administratif (TA), and of the Cour constitutionnelle (CCST). Its only intake
was the entscheidsuche VD_Omni spider: `es_vd_omni.jsonl` is frozen since
2026-03-12 (newest ruling 2026-02-09). `vd_gerichte` (prestations.vd.ch) does
not carry these courts, so the exemption in `docs/intake_expected_stale.json`
("superseded by vd_gerichte") was wrong.

**The gap is ~10,500 rulings, not ~785.** The portal lists 38,501 cantonal
rulings; the corpus holds 28,028 of them (825 under a wrong date), plus 4
frozen rows whose dockets the portal no longer lists (below). Two thirds of the TA years 1991-2001 were never ingested: in
1992, 302 of 1,314 rulings are held. The report's "~785" is the 2026 share,
and it matches: 783 rulings of 2026 are missing. The report compared against
entscheidsuche's own listing, which lacks the same history.

## The portal (jurisprudence.vd.ch, probed 2026-11-08)

Omnis JURISWEB, schema `VD_TA_WEB`, same family as BS/GL/NE/TI. 50,399 fiches
(the per-year counters sum to 50,398; one fiche has no docket):

| Authority | Fiches | Taken |
|---|---:|---|
| CDAP | 21,913 | yes |
| TA (Tribunal administratif, to 2007) | 21,887 | yes |
| CCST (Cour constitutionnelle) | 272 | yes |
| JI 91, TN 11, CF 9, DF 7, TC 4, CE 1, XX 1, blank 1 | 125 | yes |
| TF (Federal Supreme Court, filed under the cantonal docket) | 6,186 | **no**: held under `bger` |
| TAF (Federal Administrative Court) | 16 | **no**: held under `bvger` |

- **One ruling, several fiches.** The portal files a ruling once per
  headnote: 3,433 docket+date groups hold 2 to 11 fiches (9,127 fiches) with
  the same document (identical text hash, checked on 4 groups) and a different
  résumé each. 44,196 cantonal fiches = **38,501 rulings**.
- No PDF exists: rulings are HTML (`pdf_url` stays null). The document URL
  works without the session key, so `source_url` is a stable link to the
  ruling (the frozen rows carry a generic search URL).
- The listing's résumé equals the document's (200 of 200 compared).
- The portal fails intermittently: a `302` with
  `Location: http://jurisprudence.vd.chAn error occurred while executing a method`,
  or a body cut short; the same request succeeds seconds later.
- The VPS reaches the portal directly (HTTP 200 in 1.8 s): no tunnel.

## The scraper (`scrapers/cantonal/vd_omni.py`)

- Court code `vd_omni`, shard `output/decisions/vd_omni.jsonl` (direct, so the
  build imports it before `es_vd_omni.jsonl`). Registered in `run_scraper.py`,
  so the 01:00 nightly runs it.
- Fields: decision date, publication date ("publié le"), docket, chamber =
  the portal's authority (CDAP / TA / CCST / ...; CDAP derives branch
  `oeffentlich`, proceeding `vwv_beschwerde`/`vrg_vd`), language (detected),
  title = parties, regeste = every headnote of the ruling (the registry's
  résumés, blank-line separated), judges/clerks (abbreviations),
  collection = "Publication (revue juridique)", appeal_info = "Ref. TF",
  source_url. Text = every `WordSection*` / `Section*` div of the document.
- Windows: one listing request per month by decision date (one window for
  < 1991). Nightly default: the last 24 months (2026 publication lag: median
  20 days, p90 78, max 280). `--since` walks from that date. All windows are
  listed before anything is resolved or fetched (see identity).
- Identity, in three passes over the whole walk:
  1. a ruling the sidecar knows (by nF30_KEY), or a frozen es row of the docket
     with the same decision date: held, not fetched. Ids are `vd_omni_<docket>`,
     the shape of the frozen rows, and an es row binds to one ruling only. A
     frozen row's date is the one its own entscheidsuche header gives
     ("Vaud Tribunal cantonal ... 10.07.1991 AC.1990.6992"), else its date
     field: 825 fields are wrong (often the date of the decision under
     appeal), and 4 of them equal another ruling of the same docket. A row
     matched by its header but not its field is held as "wrong date" and
     reported in `state/vd_omni.es_date_mismatches.tsv`;
  2. a frozen es row of the docket with the same résumé under another date
     (FO.2023.0004): held, not fetched, reported the same way. A direct copy under the right
     date would be served next to the es copy: the build's date-agnostic
     pass folds only prefix-identical texts, and the es copy opens with its
     own header. Résumés match when identical and ≥ 20 letters (never the
     bare placeholder "Résumé") or one is a prefix of the other and ≥ 80;
  3. otherwise new: the docket's first ruling takes `vd_omni_<docket>`, a
     ruling on another date `_dYYYYMMDD` (the suffix the build itself uses), a
     second one on that date `_n<nF30_KEY>`.

  Pass 2 runs only after every date match of the walk is bound, because an
  exact date must win (3 rulings went wrong in a one-pass version).
  Sidecar `state/vd_omni.rulings.tsv` (base id, date, nF30 keys, id, origin,
  résumé key) is seeded once from `es_vd_omni.jsonl` + the own shard and
  appended after every durable write.
- `VD_OMNI_REFETCH_HELD=1` fetches the date-matched es rows under their own
  ids, so the direct copy replaces the es copy at the next build (same id,
  same date, direct shard first); `VD_OMNI_REFETCH_IDS=<id>,...` does that for
  named rows only. Wrong-date rows are never re-fetched before their date is
  repaired (the copies would be served side by side).
- Retries: listing pages and documents are tried 3 times (pauses 10 s, 30 s),
  redirects are never followed, and a listing window that still fails is
  split in halves (3 tries each).
- Health (invariant 7): a window that cannot be listed completely (failure,
  no hit counter, fewer rows than the counter) logs `ERROR ... search failed`,
  which `run_all_scrapers` counts as a discovery error; after 3 such windows in
  a row the run stops listing. A dead portal therefore gives ≥ 3 discovery
  errors and 0 new = FAILED (pinned by
  `test_failed_listing_counts_as_a_discovery_error_in_scraper_health`, which
  feeds the scraper's own log lines through `run_single_scraper`). Retry
  warnings never contain "Max retries exceeded", which the health parser
  counts at any log level.

Other changes: `scripts/check_scraper_freshness.py` drops `vd_omni` from
`ENTSCHEIDSUCHE_ONLY` (it must now appear in `scraper_health.json`, and the
registry check warns if it does not); `docs/intake_expected_stale.json` drops
its wrong exemption.

## Dry run (2026-11-08, live portal, REQUEST_DELAY 2 s, scratch state)

1. **Full listing walk**, all 432 windows since 1800 through the scraper's own
   `_search`: 50,398 fiches listed, equal to the portal's own per-year
   counters in every year; 55 min; 2 intermittent errors, both recovered on
   the 2nd try; 0 failed windows.
2. **Resolution** of that listing against the frozen shard (28,033 rows), by
   the scraper's own passes (offline replay of the recorded listing):

| Year | Portal fiches | TF/TAF | Cantonal fiches | Listed | Cantonal rulings | Held (es, same date) | Held (es, wrong date) | To fetch | Held + to fetch | es rows |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| ≤1990 | 17 | 5 | 12 | 17 | 10 | 2 | 0 | 8 | 10 | 22 |
| 1991 | 299 | 31 | 268 | 299 | 265 | 69 | 24 | 172 | 265 | 98 |
| 1992 | 1,367 | 49 | 1,318 | 1,367 | 1,314 | 272 | 30 | 1,012 | 1,314 | 318 |
| 1993 | 1,409 | 86 | 1,323 | 1,409 | 1,320 | 302 | 50 | 968 | 1,320 | 332 |
| 1994 | 1,270 | 75 | 1,195 | 1,270 | 1,192 | 238 | 17 | 937 | 1,192 | 249 |
| 1995 | 1,404 | 74 | 1,330 | 1,404 | 1,302 | 276 | 17 | 1,009 | 1,302 | 291 |
| 1996 | 1,859 | 154 | 1,705 | 1,859 | 1,509 | 292 | 12 | 1,205 | 1,509 | 305 |
| 1997 | 1,736 | 163 | 1,573 | 1,736 | 1,360 | 271 | 12 | 1,077 | 1,360 | 288 |
| 1998 | 1,442 | 174 | 1,268 | 1,442 | 1,104 | 274 | 15 | 815 | 1,104 | 289 |
| 1999 | 1,410 | 171 | 1,239 | 1,410 | 1,061 | 289 | 16 | 756 | 1,061 | 299 |
| 2000 | 1,300 | 111 | 1,189 | 1,300 | 974 | 314 | 10 | 650 | 974 | 332 |
| 2001 | 1,244 | 130 | 1,114 | 1,244 | 936 | 267 | 19 | 650 | 936 | 288 |
| 2002 | 1,095 | 123 | 972 | 1,095 | 820 | 772 | 26 | 22 | 820 | 806 |
| 2003 | 1,146 | 103 | 1,043 | 1,146 | 887 | 846 | 35 | 6 | 887 | 874 |
| 2004 | 1,587 | 124 | 1,463 | 1,587 | 1,179 | 1,116 | 50 | 13 | 1,179 | 1,156 |
| 2005 | 1,695 | 199 | 1,496 | 1,695 | 1,254 | 1,208 | 32 | 14 | 1,254 | 1,236 |
| 2006 | 2,165 | 254 | 1,911 | 2,165 | 1,619 | 1,577 | 27 | 15 | 1,619 | 1,603 |
| 2007 | 1,925 | 320 | 1,605 | 1,925 | 1,297 | 1,270 | 17 | 10 | 1,297 | 1,283 |
| 2008 | 1,565 | 210 | 1,355 | 1,565 | 997 | 985 | 8 | 4 | 997 | 996 |
| 2009 | 1,623 | 240 | 1,383 | 1,623 | 998 | 986 | 7 | 5 | 998 | 996 |
| 2010 | 1,663 | 286 | 1,377 | 1,663 | 960 | 936 | 12 | 12 | 960 | 944 |
| 2011 | 1,471 | 206 | 1,265 | 1,471 | 917 | 881 | 26 | 10 | 917 | 906 |
| 2012 | 1,550 | 227 | 1,323 | 1,550 | 1,050 | 992 | 48 | 10 | 1,050 | 1,037 |
| 2013 | 1,745 | 340 | 1,405 | 1,745 | 1,164 | 1,102 | 45 | 17 | 1,164 | 1,142 |
| 2014 | 1,431 | 209 | 1,222 | 1,431 | 991 | 958 | 19 | 14 | 991 | 987 |
| 2015 | 1,620 | 233 | 1,387 | 1,620 | 1,202 | 1,140 | 46 | 16 | 1,202 | 1,173 |
| 2016 | 1,403 | 187 | 1,216 | 1,403 | 1,085 | 1,047 | 24 | 14 | 1,085 | 1,073 |
| 2017 | 1,298 | 160 | 1,138 | 1,298 | 1,006 | 959 | 28 | 19 | 1,006 | 990 |
| 2018 | 1,456 | 198 | 1,258 | 1,456 | 1,118 | 1,076 | 27 | 15 | 1,118 | 1,105 |
| 2019 | 1,413 | 194 | 1,219 | 1,413 | 1,123 | 1,084 | 20 | 19 | 1,123 | 1,107 |
| 2020 | 1,275 | 220 | 1,055 | 1,275 | 997 | 962 | 16 | 19 | 997 | 976 |
| 2021 | 1,099 | 180 | 919 | 1,099 | 887 | 852 | 16 | 19 | 887 | 868 |
| 2022 | 971 | 145 | 826 | 971 | 811 | 783 | 14 | 14 | 811 | 795 |
| 2023 | 1,108 | 147 | 961 | 1,108 | 945 | 907 | 15 | 23 | 945 | 924 |
| 2024 | 1,176 | 157 | 1,019 | 1,176 | 1,012 | 937 | 26 | 49 | 1,012 | 965 |
| 2025 | 1,201 | 195 | 1,006 | 1,201 | 998 | 908 | 18 | 72 | 998 | 927 |
| 2026 | 960 | 122 | 838 | 960 | 837 | 53 | 1 | 783 | 837 | 53 |
| **Total** | **50,398** | **6,202** | **44,196** | **50,398** | **38,501** | **27,203** | **825** | **10,473** | **38,501** | **28,033** |

   "Held + to fetch" equals the portal's cantonal rulings in every year: after
   the backfill the corpus holds every ruling the portal lists. Of the 28,033
   frozen rows, 28,028 match a ruling (27,203 by date, 825 under a wrong
   date); the 5 others are 1 row without a docket and 4 whose dockets the
   portal no longer lists (AC.2023.0305, GE.2014.0095, PE.2017.0501,
   PE.2018.0121). They stay as they are: a ruling the portal dropped is still
   a ruling.
   The 5 TA rows with the placeholder résumé "Résumé" (AC.1990.6992,
   AC.1990.7059, AC.1990.7437, FI.1992.0132, FO.1990.1395) were checked by
   hand: each is the portal's ruling (98-100 % of the text in common both
   ways), and each es header prints the portal's date while the field holds
   the date of the decision under appeal. They are held, not fetched, and
   their dates are in the repair.
3. **Fetch** through the real `run_scraper.py` path with the final code
   (`--since 2026-01-01 --max 200`, scratch output/state): 200 of 200
   written, 7.7 min (2.3 s per ruling), 0 errors, 0 empty fetches, 0
   warnings. All 200 have decision and publication date, résumé, parties and
   judge; text median 20,821 characters (min 2,640, max 157,886); language fr;
   chambers CDAP 197, CCST 3; one ruling carries two headnotes; GE.2025.0191
   (the wrong-date es row) was held, not fetched. The report's evidence
   ruling, fetched alone: `vd_omni_AC.2026.0235`, CDAP, decided 2026-09-01,
   published 2026-09-02, résumé and 8,933 characters of text. (An earlier run
   on a pre-grouping revision wrote the two headnotes of AC.2024.0277 as two
   rows and fetched GE.2025.0191 a second time; both are fixed and tested.)
4. **Ground truth sample:** 12 random frozen rows looked up on the portal by
   docket: all 12 have a listed ruling of the same date, and 100% of the
   portal text's 60-character windows appear verbatim in the es copy (the es
   copy adds its header and résumé). The same-docket, same-date identity
   holds.

## Repair of the 825 wrong es dates (`scripts/vd_omni_es_date_repair.py`)

Input `runbooks/vd_omni_es_date_repair_2026-11.tsv` (es id, es date, portal
date, nF30_KEY; from the dry run). Only `decision_date` changes, only on a
listed row whose current date is the listed es date, and only when the new
date is confirmed by the row itself: its header (824 rows) or the ruling's own
text; FO.2023.0004 is the one exception, confirmed by hand on the portal (the
es copy is the court's earlier, unsigned version dated 27 juin; the portal
holds the re-issued ruling of 11 juillet 2023, same text), and its final text
comes in with the backfill (`VD_OMNI_REFETCH_IDS`). Refuses on a line-count
change, on any other field changing, and on a corrected date giving two rows
the same court|docket|date key. Dry run default; `--apply` hard-links
`es_vd_omni.jsonl.bak-<date>`, writes `.tmp`, swaps atomically.

Rehearsed 2026-11-08 on a byte-identical copy of the live shard (md5
d45d1f8a4090…): dry run 825 changes (824 by header, 1 on portal), 0
collisions, 0 listed rows missing; apply: 28,033 lines in and out, backup on
the original inode; rerun 0 changes. Then a real `build_fts5.build_database`
of a scratch directory with the original vs the repaired shard (plus 200 direct
rows): both serve the same 28,233 `vd_omni` rows with the same ids, no `_d`
rows, and exactly the 825 listed rows change date, each to the portal's. The
per-court swap gate sees an identical count.

## Deploy

Files: `scrapers/cantonal/vd_omni.py` (new), `run_scraper.py` (+3 lines),
`scripts/check_scraper_freshness.py` (1 line), `docs/intake_expected_stale.json`
(−4 lines), `tests/test_vd_omni_scraper.py` (29 tests), `scripts/vd_omni_es_date_repair.py` + `tests/test_vd_omni_es_date_repair.py` (7 tests), `runbooks/vd_omni_es_date_repair_2026-11.tsv`,
`tests/test_vd_gerichte_resume.py` (2 tests), 8 fixtures (`tests/fixtures/vd_omni_*`,
`tests/fixtures/vd_gerichte_search_2026_10_20261108.json`), this runbook.

1. **Commit + push** (noreply identity, memory `git_identity`) **after the
   day's build and the 20:00 incremental have exited**, so no step 6 pull
   lands it mid-run. Then on the VPS, as for every deploy:
   ```
   cd /opt/caselaw/repo && git fetch origin && git merge --ff-only origin/main
   python3 -c "import scrapers.cantonal.vd_omni"
   ```
2. **Repair the es dates, before the first nightly run** (so the scraper
   seeds its sidecar from corrected dates), in a window after the build and
   the 20:00 incremental have exited and before 00:40 UTC, under the shared
   lock:
   ```
   cd /opt/caselaw/repo
   python3 scripts/vd_omni_es_date_repair.py            # dry run: rows_changed 825, unconfirmed 0
   flock -w 3600 /var/lock/ocl-shard-repair.lock \
     python3 scripts/vd_omni_es_date_repair.py --apply
   python3 scripts/vd_omni_es_date_repair.py            # rerun: rows_changed 0
   wc -l output/decisions/es_vd_omni.jsonl              # 28033
   ```
   Stop if the dry run shows anything but 825 changed / 0 unconfirmed / 0
   listed_not_in_shard, or refuses. Rollback:
   `mv output/decisions/es_vd_omni.jsonl.bak-<date> output/decisions/es_vd_omni.jsonl`.
   If the nightly already ran before the repair, delete
   `state/vd_omni.rulings.tsv` afterwards (it reseeds from both shards).
3. **First nightly run happens by itself** at 01:00 UTC. It seeds
   `state/vd_omni.rulings.tsv` from `output/decisions/es_vd_omni.jsonl`
   (28,033 rows, seconds), lists the last 24 months (25 requests, ~3 min) and
   fetches the **861** rulings decided since 2024-11-01 that the corpus lacks
   (~30 min at 2 s), inside the default 2 h cap. Next morning:
   ```
   python3 -c "import json;print(json.load(open('logs/scraper_health.json'))['scrapers']['vd_omni'])"
   wc -l output/decisions/vd_omni.jsonl state/vd_omni.rulings.tsv state/vd_omni.es_date_mismatches.tsv
   grep -c 'search failed' logs/vd_omni.log
   ```
   Expected: success true, new_count ≈ 861, discovery_errors 0.
4. **Full-history backfill**, the remaining **9,612** rulings (1991-2024):
   ~55 min of listing, then ~5.6 h of documents at 2 s. One transient unit,
   started after the 01:00 scrape has finished and bounded so it can never
   overlap the next 01:00 run (two writers on one shard and one state file):
   ```
   systemd-run --unit=vd-omni-backfill --working-directory=/opt/caselaw/repo \
     --property=RuntimeMaxSec=$(( $(date -d 'tomorrow 00:40' +%s) - $(date +%s) )) \
     --property=CPUWeight=20 --property=IOWeight=20 \
     --setenv=VD_OMNI_REFETCH_IDS=vd_omni_FO.2023.0004 \
     /usr/bin/python3 run_scraper.py vd_omni --since 1800-01-01
   journalctl -u vd-omni-backfill -f
   ```
   Progress is durable per ruling (append + flush, then state, then sidecar),
   so a stopped unit resumes with the same command and fetches only what is
   still missing (the listing pass repeats, 55 min). Appending while the 03:30
   build reads the shard is harmless: the build is a full rebuild from the
   start of every shard, so a row it misses comes in the next night.
5. **After the first build that carries the rows** (judge by `vd_omni` alone):
   `vd_omni` ≈ 28,033 + fetched (+861 after step 3, +10,473 after step 4);
   the swap gates only refuse drops. Spot-check `get_decision` for
   `vd_omni_AC.2026.0235` and one TA ruling of 1992.
   `check_intake_rates` / `check_scraper_freshness` now grade `vd_omni` like
   a direct court; before the first run it reports STALE, which is true.
6. **Optional, later:** `VD_OMNI_REFETCH_HELD=1` with the backfill command
   re-fetches the 27,203 date-matched es rows under their own ids (~16 h at
   2 s): working source links, chamber, publication date, every headnote.
   Not needed for completeness.

Rollback: remove the `vd_omni` line from `run_scraper.py` (stops intake),
`mv output/decisions/vd_omni.jsonl{,.off}` before 03:30 (the next build
serves the es rows alone, as today), delete `state/vd_omni.*`.

## vd_gerichte résumé (report item #10): nothing to map, nothing to backfill

The premise of report item #10 does not hold. Measured 2026-11-08 on
prestations.vd.ch/pub/101623:

- `decisionHit.resume` is `null` on **all 3,841 hits of 2025**, and on every
  hit of the months sampled in 2015, 2021, 2024 and 2026. The detail endpoint
  `api/decision/{uuid}` returns the same object, `resume: null`.
- `queryTarget: RESUME` exists in the API but matches nothing: "art", "le",
  "bail" over 2000-2026 all return 0 hits (the same words against `ALL` /
  `DECISION` return hundreds).
- `vd_gerichte.py` already maps the field (`hit["resume"]` → stub → `regeste`).
  `tests/test_vd_gerichte_resume.py` pins that on the golden response, so a
  résumé the portal starts publishing lands in `regeste` with no code change.
- The "99 % regeste" of the frozen `es_vd_findinfo` copy is not a résumé: it
  is the Jurivoc keywords and article list joined with " | " ("LOYER INDEXÉ,
  BAIL À LOYER, … | 269b CO, 270 CO"). The live API returns the same two
  fields (`conceptsJurivoc`, `articlesDeLoi`) on ~95 % of hits.

So there is nothing to backfill and no repair script was written (it would
rewrite 0 rows). **Decided 2026-11-08:** the Jurivoc keywords and article
lists do not go into `regeste` (a verbatim headnote surface); the field stays
empty, as on the portal.
