# Runbook: AG Baugesetzgebung and AG Weitere from the source (2026-11-08)

**Status 2026-11-08: scraper, fold script and tests on branch `claude/frosty-lumiere-1c8c37`,
not pushed. New scraper = pipeline-gated (CLAUDE.md invariant 5): Jonas decides the deploy.
Rehearsed on copies of the production es shards (copied 2026-11-08 18:46 CET) and on a full
live listing + PDF download.** Source of the finding:
`docs/claude/entscheidsuche-parity-addendum-2026-11-08.md` §2/§2b (main checkout): two AG
collections fed only by the frozen es shards (no growth since 2026-03), nine 1991-93 BVU
rulings not held.

## The sources (ag.ch, probed 2026-11-08)

**`ag_baugesetzgebung`**: "Entscheidsammlung Baugesetzgebung" of the BVU Rechtsabteilung,
`https://www.ag.ch/de/themen/planen-bauen/baurecht/entscheidsammlung`. Closed: "Ab Januar
2022 … im Web-Portal AGVE. Die Entscheidsammlung auf dieser Seite wird nicht mehr
weitergeführt" (the page; ag_gerichte holds the later ones under `ag_departement_bvu`).
- The page's JavaScript filelist POSTs a JSON query to
  `https://www.ag.ch/app/search-service/api/v1/doctable` (categories filter, pagination,
  sort; read from `bundle.js` v25). The API refuses answers over 256 KiB, so 100 entries a
  page: 3 requests.
- 255 entries; category = deciding authority: BVU 116, VG 83, RR 44, BGer 11, SKE 1
  (Schätzungskommission nach Baugesetz). 254 link a PDF under
  `/media/kanton-aargau/bvu/bauen/baurecht/entscheidsammlung-rechtsabteilung-bvu/`, 249
  distinct files. 259 downloads, 0 failures; every PDF has a text layer (shortest 712
  characters).
- Portal defects (reported by the scraper as warnings, nothing to fetch):
  - 1 entry without a document: 1993-12-23 VG "Parteientschädigung" (headnote only).
  - 5 entries link another ruling's PDF; their headnote is not in that PDF, so their own
    document is not published: 2012-08-23 VG "Submission" → Stützmauer PDF; 2012-08-23 VG
    "Grössere Parkierungsanlagen" → 2012-08-22 Entzug der aufschiebenden Wirkung; 1994-01-10
    "Kanalisationsanschlussgebühr" → 1994-01-18 PDF; 2012-07-11 "Vereinfachtes
    Baubewilligungsverfahren; Kostenverteilung" and 1994-08-03 "Baubewilligungspflicht" →
    their same-day neighbour's PDF.

**`ag_weitere`**: 24 frozen es rows of two kinds.
- 7 Grundbuch und Notariat decisions of the DVI, from
  `https://www.ag.ch/de/themen/planen-bauen/grundbuch-vermessung/grundbuch/rechtliche-grundlagen/entscheide`
  (static table, 10 PDFs 2001-2015). The es rows are 500-660 character stubs (title only).
- 17 "weitere Entscheide" of the Aargau courts (Handelsgericht, Justizgericht, Obergericht,
  Verwaltungsgericht KESR, Versicherungsgericht; `AG_HG_002`, `AG_OGA_*`, `AG_OG_*`, …),
  also title-only stubs. **All 17 ag.ch PDFs return 404** (old `/media/kanton_aargau/jb/…`
  paths, gone since the 2022 site migration): no source left to read. They stay as they are
  (policy: es rows stay; no feed). The addendum's "one Handelsregister ruling" is
  `ag_weitere_` (AG_HG_002), a Handelsgericht ruling, among these 17.
- Not covered elsewhere: `scrapers/practice/grundbuch_kantone.py` (2e1d2111) reads the AG
  Grundbuch *Weisungen und Kreisschreiben* page, not the *Entscheide* page. Nothing to
  reconcile.

## Root cause of the 51 missing BVU rulings

The entscheidsuche spider keyed each entry on its **title** (`ag_baugesetzgebung_Planbeschwerde`).
Entries sharing a title collapsed into one: the collection has six "Planbeschwerde", fifteen
"Submission…", two "Legitimation eines Nachbarn", three "Landwirtschaftszone", etc. 249 PDFs,
198 held. The nine rulings named in the addendum are among the 51 (+4 more from 1993).

21 of its 198 dates differ from the one the PDF gives the ruling; most are the first date in
the text, often a statute's:
`ag_baugesetzgebung_Umweltschutzrecht` 1983-10-07 (USG) for a 1994-02-21 judgment,
`Wiedererwägungsgesuch` 1968-07-09 (VRPG) for 1994-09-14.

## The scraper (`scrapers/cantonal/ag_entscheidsammlungen.py`)

Two classes, registered in `run_scraper.SCRAPERS` under the existing court codes:

| | `AGBaugesetzgebungScraper` | `AGWeitereScraper` |
|---|---|---|
| listing | doctable API, all 5 categories, paged | Grundbuch table |
| id / docket | `ag_baugesetzgebung_<pdf stem>` | `ag_weitere_<pdf stem>` |
| date | the PDF's own date line (below), else the portal date | the table's date (= the title's) |
| chamber | portal category (deciding authority); "Baudepartement" where the PDF names it (pre-2005) | authority named in title/text (Verwaltungsgericht, Regierungsrat, Departement des Innern / DVI) |
| regeste | the portal headnote(s) that occur in the PDF | none (titles are descriptive) |
| docket_number_2 | case number printed in the header (`WBE.2012.199`, `BVURA.11.722`; 71 of 249) | none |
| legal_area | "Stichwörter:" of the PDF (92 of 249) | none |
| collection | Entscheidsammlung Baugesetzgebung (BVU Rechtsabteilung) | Entscheide Grundbuch und Notariat (DVI) |

- **Docket = PDF stem**, not the case number or title: most excerpts print no number, and the
  build's dedup keys on (court, docket, date). The three excerpts of 1997-03-04
  ("planbeschwerde", "-i", "-ii") or two excerpts of one VG case would otherwise fold.
- **Dates**: older PDFs print a form line of their own ("Entscheid des Verwaltungsgerichts
  (III/7) vom 21.02.1994", often extracted last), newer ones a header before "Aus den
  Erwägungen" ("Aus dem Entscheid des Departements Bau, Verkehr und Umwelt (EBVU) vom 26.
  Oktober 2010 (BVU-RA.10.458)"), with month or "vom" split across lines. A form line naming
  two acts (a citation) is skipped. Over the 249 PDFs: 249 dated from the PDF; 242 equal the
  portal date, 7 equal the file name where the portal date is off (e.g. 2005-09-16 Vorentscheid,
  portal 2005-06-16); 0 contradict both.
- Health (invariant 7): a failed or empty listing, an API answer without `files`, or fewer
  entries than the API's own `totalElements` raises (FAILED in `scraper_health.json`).
  Both collections are closed, so nightly runs normally fetch nothing (3 + 1 requests).
  `portal_count` is the API's entry count (255), so scraper_health shows `249/255 (gap 6)`:
  the six defective entries. Below the freshness check's `GAP_MIN` (10): no alert.
- `scripts/check_scraper_freshness.py`: both codes leave `ENTSCHEIDSUCHE_ONLY`, stay in
  `KNOWN_DEAD_SOURCES` (closed: no new rows expected). `scripts/reprobe_dead_sources.py`:
  they move from `NO_SCRAPER_CLASS` to `CLOSED_COLLECTIONS` (never probed).
- `scrapers/entscheidsuche_ingest.py`: `AG_Baugesetzgebung` and `AG_Weitere` leave
  `SPIDER_MAP`, so a manual es ingest cannot bring the quarantined rows back.

## The fold (`scripts/ag_entscheidsammlungen_es_fold.py`)

Pairs each es row with the direct row holding **the same PDF file name** (exact, 1:1). Per
pair: the direct row gets `previous_decision_id` = the es id (build_fts5 records the alias:
the es URL resolves); the es row moves to `<quarantine-dir>/es_<court>.quarantined.jsonl.txt`.
es rows without a direct twin stay. Refuses on ambiguous PDFs, a conflicting
`previous_decision_id`, a missing direct shard, or a quarantine dir equal to the decisions dir.
Dry run by default; `--apply` appends the quarantine file first (never twice), then rewrites
the es and the direct shard atomically (`*.bak-ag-es-fold-<date>`). Idempotent.

Without the fold, the build would serve each held ruling twice (different id, docket and
often date: no dedup pass pairs them).

## Rehearsal

Direct shards produced offline from the live listing and the 259 downloaded PDFs (the same
`build_decision` path), es shards = production copies. A live `run_scraper.py` run of both
scrapers into scratch output/state dirs (2026-11-08 19:14-19:22 CET) gave the same ids and
dates: ag_weitere `+10 new, 10/10`, ag_baugesetzgebung `+249 new, 249/255 (gap 6)`, 0 errors,
the 6 portal-defect warnings, 6.3 min. Fold dry run → `--apply` → second `--apply` (no-op) →
`build_fts5.py --full-rebuild` on a scratch output dir (`OCL_EARLY_STATS_PUSH=0`, HEAD
unchanged):

| court | served before (es only) | served after | new rulings | es rows quarantined | es stubs replaced by full text | es dates corrected | aliases |
|---|---:|---:|---:|---:|---:|---:|---:|
| `ag_baugesetzgebung` | 198 | **249** | 51 (all 9 named + 42) | 198 (shard left empty) | 109 | 21 | 198 |
| `ag_weitere` | 24 | **27** | 3 (2007-08-23, 2009-02-19 №2, 2009-10-21) | 7 | 7 | 0 | 7 |

205 aliases, 0 pointing at a missing row. The 17 dead-source `ag_weitere` stubs are served
unchanged.

## Production steps (Jonas)

Window: after the nightly publish has exited and before 03:30 UTC, no publish running
(`systemctl is-active opencaselaw-publish.service opencaselaw-publish-incremental.service`
both `inactive`). Do steps 2-4 in **one** window: once the code is merged, the 01:00 nightly
scrape writes the direct rows, and a build before the fold serves both copies (446 rows instead
of 249) for a night.

1. Deploy (push, then on the VPS):

   ```bash
   cd /opt/caselaw/repo && git fetch && git merge --ff-only origin/main
   ```

2. First direct runs (the VPS reaches ag.ch directly; ~7 min, 263 requests):

   ```bash
   cd /opt/caselaw/repo && python3 run_scraper.py ag_weitere && python3 run_scraper.py ag_baugesetzgebung
   ```

   Expect `+10 new` and `+249 new`, 0 errors; 6 warnings for ag_baugesetzgebung (the portal
   defects above). **Stop if** fewer, or any `ERROR`.

3. Fold, dry run:

   ```bash
   D=$(readlink -f output/decisions); Q=$(dirname "$D")/quarantine/ag_es_fold_2026-11
   for c in ag_baugesetzgebung ag_weitere; do python3 scripts/ag_entscheidsammlungen_es_fold.py --court $c --decisions-dir "$D" --quarantine-dir "$Q"; done
   ```

   Expect ag_baugesetzgebung `served before 447, after 249`, `pairs: 198`,
   `es_rows_after: 0`, `direct_without_es_copy: 51`; ag_weitere `served before 34, after 27`,
   `pairs: 7`, `es_kept_no_direct_copy: 17`. **Stop if** different.

4. Apply: the same loop with `--apply`. Check `ls -la "$Q"` (198 + 7 lines) and the
   `*.bak-ag-es-fold-<date>` backups beside the four shards. Rollback: move the four backups
   back (the quarantine file can stay).

5. After the next build: `ag_baugesetzgebung` 249, `ag_weitere` 27;
   `get_decision("ag_baugesetzgebung_Umweltschutzrecht")` resolves to
   `ag_baugesetzgebung_1994-02-21-umweltschutzrecht` dated 1994-02-21. Both courts are below
   the per-court gate's 500-row floor; no override.

## Follow-ups (not in this change)

- The 17 `ag_weitere` court rulings without a source: check whether `ag_gerichte`
  (decwork.ag.ch) holds them under their court codes; if so, fold them like the above.
- `ag_baugesetzgebung` is in no `_COURT_OVERLAP_GROUPS` group: VG/RR rulings of 2009-2021
  can also be in AGVE (`ag_verwaltungsgericht`, `ag_regierungsrat`). The excerpts are not the
  full rulings, so they are not duplicates in content; worth a check before any grouping.
- `es_ag_weitere` holds the Handelsgericht ruling of 2019-02-19 twice (`ag_weitere_`,
  `ag_weitere_unknown_AG_OGA_002`, same PDF): an es-internal duplicate.
- Tell the BVU about the six portal defects (one missing document, five wrong links).
