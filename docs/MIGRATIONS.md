# OpenCaseLaw — Data Migrations & Compatibility Notes

This document records breaking changes and historical data-quality issues in the
public data products at `voilaj/swiss-caselaw` (HuggingFace) so consumers can
apply remedial migrations without re-deriving them.

---

## 2026-09-04 — Arbeitsgericht Zürich: court-code repair + yearbooks 2003–2023

**Affected courts:** `zh_arbeitsgericht`, `zh_bezirksgericht_zuerich`, `zh_mietgericht`.

- Rulings of the Arbeitsgericht and Mietgericht Zürich that the gerichte-zh.ch
  portal files under Gericht="Bezirksgericht Zürich" with the specialised court
  in the Kammer field were served under `zh_bezirksgericht_zuerich` (67 rows,
  2006–2026). They move to `zh_arbeitsgericht` / `zh_mietgericht`; their
  `decision_id` changes from `zh_bezirksgericht_zuerich_<docket>` to
  `zh_arbeitsgericht_<docket>`. The chamber field no longer repeats the court
  name. Consumers keyed on `decision_id` should re-key by `external_id`
  (`zh_gerichte_<TYPO3 doc id>`), which is stable.
- 11 rulings existed twice (`AH230041` and `AH230041-L`, the portal re-edited
  the Geschäftsnummer); the older docket-shape row is dropped, the `-L` form
  survives. `scripts/migrate_zh_arbeitsgericht_rows.py`.
- `zh_bezirksgericht_zuerich_AN230029-L` (AGer-Z 2024 Nr. 6) carried
  `decision_date` 1937-12-03 — the date of a treaty quoted in its headnote,
  written by a one-off text-date repair. Restored to the portal's 2024-11-25.
- New: the court's annual yearbooks "Entscheide des Arbeitsgerichtes Zürich"
  2003–2023 (21 PDFs, 530 edited excerpts) under `court=zh_arbeitsgericht`,
  `docket_number` = the court's citation form `AGer-Z 2023 Nr. 7`,
  `docket_number_2` = the Geschäftsnummer from the excerpt's trailer (511 of
  530), `decision_date` from the trailer (507 of 530; the rest NULL, floor 40
  in `quality/checks/dates.py`), `collection` = `AGer-Z 2023`,
  `external_id` = `ager_z_2023_7`. Excerpts are the court's redaction: facts
  summarised, Erwägungen quoted «verbatim», editorial omissions in brackets.
  Source: `scrapers/cantonal/zh_arbeitsgericht_sammlung.py`; the operator
  runbook is kept in the private maintenance workspace.

---

## 2026-05-01 — QC system + 5 codified auto-correction passes

A 4-layer quality-control system was deployed end-to-end. Going forward, every
nightly publish runs **61 integrity checks** before the `git push` step;
a CRITICAL regression blocks publication and users keep yesterday's data.
Live dashboard: <https://opencaselaw.ch/quality.html>.

### Auto-corrections now codified in `build_fts5.py` (run every nightly)

| Pass | Effect | Removes data-quality class |
|---|---|---|
| `_normalize_dockets()` | trim + strip internal `\n`/`\r`/`\t` + collapse spaces | König P5 (whitespace dockets, ~21,000) + 5,441 newline dockets |
| `_normalize_dates()` | clear `'0000-…'` markers + dates `> today+365` to NULL | König P6 (year-0000, ~796) |
| `_recover_decision_dates()` | multilingual anchor extraction for 5 safe courts | König P4 (5,258 NULL dates recovered) |
| `_normalize_source_urls()` | host-prefix bs/gl Tribuna `/cgi-bin/...` paths | König P2 (694 relative URLs in BS/GL) |
| `_truncate_oversized_regestes()` | head-note only when regeste > 8 KB and ≈ duplicates `full_text` | HUDOC bge_egmr leakage (462 rows, max 875 KB) |
| `_dedup_egmr_in_bge()` | DELETE `bge` rows with cedh URLs | König #1 (474 EGMR duplicates) |
| `_migrate_short_text_to_regeste()` | 10-99 char `full_text` → `regeste` when regeste empty | König P7 (248 rows, sh_gerichte) |

Source-side fix in `scrapers/entscheidsuche_ingest.py` complements the regeste
truncation by truncating `Abstract > 8000` chars at ingest, so future-imported
HUDOC rows are clean from the start.

### Step 5c QC gate (semantics)

`publish.py` step 5c runs `python -m quality.cli run --critical-only --gate`
between RSS feed generation (5b) and the early git push (6a). Severity matrix:

- **CRITICAL** → blocks Step 6a and 6 (git push). 8 classes: schema integrity,
  total-count cliff > 1%, EGMR re-regression, court not in registry, whitespace
  dockets > 0, year-0000 dates > 0, future dates > 50, cross-DB row mismatch
  > 0.5%.
- **WARNING** → ntfy alert only. Examples: per-court drift > 5×MAD over 7-day
  median, OCR-quality regression, citation-graph edge drop > 5%, floor residuals
  growing.
- **INFO** → recorded only.

When the gate blocks: `docs/quality.json` records the failing checks; an
ntfy.sh alert fires with the names; users on opencaselaw.ch keep yesterday's
data until the regression is investigated and resolved.

### Production-uptime smoke (L4)

`opencaselaw-smoke.timer` fires `python -m quality.smoke` every 5 min on the VPS.
Six probes (health, /entscheid HTML, four export formats). Logs at
`/var/log/opencaselaw-smoke/latest.json`. On any non-200 response, systemd
`OnFailure` triggers an ntfy alert (topic `opencaselaw-smoke`).

### Consumer-facing impact

The HuggingFace dataset gets cleaner data going forward — the 7,000+ rows
across the issue classes above are corrected automatically at the next
publish. **No consumer-side migration required**: rows are mutated in place,
preserving `decision_id`. If a consumer was doing string equality on the
old (whitespace / newline / relative-URL / oversized-regeste) values to
detect them, that detection becomes a no-op (which is correct).

---

## 2026-04-30 — Historical Delta Schema Drift (2026-02-16 to 2026-04-22)

**Status**: upstream fixed 2026-04-23. **Legacy artifacts still on HF.** Consumers
applying the 65 affected daily deltas in cumulative mode will end up with
non-canonical court codes, NULL `decision_date`, NULL `chamber`, and UUID-format
`id` values. This document describes the fix.

### Symptom

Daily-delta files at `artifacts/sqlite/deltas/YYYY-MM-DD.sqlite.zst` for any of
the 65 dates from `2026-02-16` through `2026-04-22` (inclusive) have schema
divergence from the per-court Parquet files at `data/<court>.parquet`:

| Field | Canonical (Parquet) | Drift (these deltas) |
|---|---|---|
| `court` | `zh_obergericht` (court_code) | `Obergericht` (display name) |
| `source_id` | `null` | `zh` |
| `source_name` | `null` | `Zürich` |
| `decision_date` | `2026-02-11` | `null` (often) |
| `chamber` | `II. Zivilkammer` | `null` (often) |
| `title` | `Konkurseröffnung` | `PS260036-O2` (docket + file suffix) |
| `docket_number` | `PS260036` | `docket: PS260036-O2` (with file suffix) |
| `id` | `zh_obergericht_PS260036` | UUID string |

Furthermore: the drift court codes are sometimes sachlich incorrect.
Bezirksgericht decisions sometimes appear under `court='Obergericht'`,
Verwaltungskommission rulings under `court='Strafkammer'`. Determining the true
court code requires inspecting `content_text` headers (e.g. "Bezirksgericht
Hinwil — Zwangsmassnahmengericht" → `zh_bezirksgericht_hinwil`).

### From 2026-04-23 onward

All daily deltas are canonical. The cutover was the switch from a private
`daily_update.yml` GitHub Action to the in-tree `search_stack/publish_delta.py`
pipeline.

### Migration SQL

For consumers who applied the 65 corrupted deltas to a local SQLite database,
the following two-step migration restores canonical state.

#### Step 1 — Reclassify drift rows by canonical court_code

```sql
-- Drift rows are identified by:
--  (a) court name not matching any Parquet court_code
--  (b) source_id not being NULL
-- Build a lookup table that maps (drift_court, source_id, content_text_clue)
-- → canonical_court_code via inspection of the per-court Parquets.

CREATE TEMP TABLE court_remap AS
SELECT * FROM (
  -- ZH Obergericht decisions — rows where header confirms OG ZH
  SELECT 'Obergericht' AS drift_court, 'zh' AS source_id,
         'Obergericht des Kantons Z' AS header_starts_with,
         'zh_obergericht' AS canonical_court
  UNION ALL
  -- ZH Bezirksgerichte under court='Obergericht' (incorrect classification)
  SELECT 'Obergericht', 'zh', 'Bezirksgericht Hinwil',  'zh_bezirksgericht_hinwil'
  UNION ALL SELECT 'Obergericht', 'zh', 'Bezirksgericht Z',     'zh_bezirksgericht_zuerich'
  UNION ALL SELECT 'Obergericht', 'zh', 'Bezirksgericht B',     'zh_bezirksgericht_buelach'
  UNION ALL SELECT 'Obergericht', 'zh', 'Bezirksgericht D',     'zh_bezirksgericht_dielsdorf'
  UNION ALL SELECT 'Obergericht', 'zh', 'Bezirksgericht Mei',   'zh_bezirksgericht_meilen'
  UNION ALL SELECT 'Obergericht', 'zh', 'Bezirksgericht U',     'zh_bezirksgericht_uster'
  UNION ALL SELECT 'Obergericht', 'zh', 'Bezirksgericht W',     'zh_bezirksgericht_winterthur'
  UNION ALL SELECT 'Obergericht', 'zh', 'Bezirksgericht Hor',   'zh_bezirksgericht_horgen'
  UNION ALL SELECT 'Obergericht', 'zh', 'Bezirksgericht A',     'zh_bezirksgericht_affoltern'
  UNION ALL SELECT 'Obergericht', 'zh', 'Bezirksgericht P',     'zh_bezirksgericht_pfaeffikon'
  UNION ALL SELECT 'Obergericht', 'zh', 'Mietgericht',          'zh_mietgericht'
  UNION ALL SELECT 'Obergericht', 'zh', 'Handelsgericht',       'zh_handelsgericht'
  -- Other cantons
  UNION ALL SELECT 'Obergericht', 'zg', '',                     'zg_obergericht'
  UNION ALL SELECT 'Obergericht', 'ag', '',                     'ag_gerichte'
  UNION ALL SELECT 'Obergericht', 'so', '',                     'so_gerichte'
  UNION ALL SELECT 'Obergericht', 'ar', '',                     'ar_gerichte'
  UNION ALL SELECT 'Obergericht', 'nw', '',                     'nw_gerichte'
  UNION ALL SELECT 'Obergericht', 'ur', '',                     'ur_gerichte'
  UNION ALL SELECT 'Strafkammer',  'zh', '',                    'zh_obergericht'
  UNION ALL SELECT 'Zivilkammer',  'zh', '',                    'zh_obergericht'
  UNION ALL SELECT 'Mietgericht',  'zh', '',                    'zh_mietgericht'
  UNION ALL SELECT 'Verwaltungsgericht', 'zg', '',              'zg_verwaltungsgericht'
  UNION ALL SELECT 'Verwaltungsgericht', 'nw', '',              'nw_verwaltungsgericht'
  UNION ALL SELECT 'Verwaltungsgericht', 'gl', '',              'gl_gerichte'
  UNION ALL SELECT 'Kantonsgericht', 'nw', '',                  'nw_kantonsgericht'
  UNION ALL SELECT 'Kantonsgericht', 'lu', '',                  'lu_gerichte'
  UNION ALL SELECT 'Kantonsgericht', 'sz', '',                  'sz_gerichte'
  UNION ALL SELECT 'Tribunal cantonal',                'ju', '', 'ju_gerichte'
  UNION ALL SELECT 'Tribunale cantonale',              'ti', '', 'ti_gerichte'
  UNION ALL SELECT 'Cour de droit administratif et public', 'vd', '', 'vd_gerichte'
  UNION ALL SELECT 'Wettbewerbskommission',            'weko', '', 'weko'
  UNION ALL SELECT 'Eidgenössischer Datenschutz- und Öffentlichkeitsbeauftragter', 'edoeb', '', 'edoeb'
  UNION ALL SELECT 'Bundesverwaltungsgericht', 'bvger', '', 'bvger'
  UNION ALL SELECT 'Bundesgericht',            'bger',  '', 'bger'
);

-- Apply the remap. For ambiguous (drift_court, source_id) combinations the
-- header_starts_with predicate disambiguates via SUBSTR(content_text, 1, 200).
UPDATE decisions
SET court = (
  SELECT canonical_court FROM court_remap
   WHERE court_remap.drift_court = decisions.court
     AND (court_remap.source_id = '' OR court_remap.source_id = decisions.source_id)
     AND (court_remap.header_starts_with = ''
          OR SUBSTR(COALESCE(content_text, ''), 1, 200) LIKE '%' || court_remap.header_starts_with || '%')
   ORDER BY LENGTH(court_remap.header_starts_with) DESC
   LIMIT 1
)
WHERE court IN (SELECT DISTINCT drift_court FROM court_remap)
  AND EXISTS (
    SELECT 1 FROM court_remap
    WHERE court_remap.drift_court = decisions.court
      AND (court_remap.source_id = '' OR court_remap.source_id = decisions.source_id)
  );
```

#### Step 2 — Strip file suffix from `docket` and dedup against canonical Parquet rows

```sql
-- File suffix pattern: '-O2', '-E1', etc. — strip them
UPDATE decisions
SET docket = REGEXP_REPLACE(docket, '-[A-Z][0-9]+$', '')
WHERE docket REGEXP '-[A-Z][0-9]+$';

-- For consumers who track an explicit decision_id alongside the UUID `id`,
-- regenerate the slug ID:
UPDATE decisions
SET decision_id = court || '_' || REPLACE(REPLACE(REPLACE(docket, '/', '_'), ' ', '_'), '.', '_')
WHERE id LIKE '________-____-____-____-____________'  -- UUID pattern
  AND court IN (SELECT canonical_court FROM court_remap);

-- Dedup: where a Parquet-imported slug-row and a delta-imported UUID-row
-- describe the same decision (same court + canonical_key), prefer the slug
-- row (richer metadata: regeste, judges, clerks, cited_decisions etc.).
DELETE FROM decisions
WHERE id LIKE '________-____-____-____-____________'  -- UUID
  AND EXISTS (
    SELECT 1 FROM decisions s
    WHERE s.court = decisions.court
      AND s.canonical_key = decisions.canonical_key
      AND s.id NOT LIKE '________-____-____-____-____________'
  );
```

#### Step 3 — Recover NULL decision_date from content_text where possible

```sql
-- Multilingual anchor-driven recovery; only safe for courts with high-precision
-- patterns. König (2026-04-30 audit) verified zh_obergericht, zh_bezirksgericht_*,
-- zg_obergericht, gr_gerichte, bl_gerichte have clean "Urteil/Entscheid vom DD.
-- Monat YYYY" patterns → 90-100% precision.

-- Recommend doing this in Python with proper anchor-driven logic rather than
-- pure SQL. See build_fts5.py:_recover_decision_dates() in the OpenCaseLaw
-- repo for a reference implementation that targets only safe courts.
```

### Acknowledgments

This migration document was derived from the 2026-04-30 audit by Adrian König
(consumer of `voilaj/swiss-caselaw`). His local-DB cleanup script
`delta_normalization.py` and accompanying 45-test suite informed the SQL above.
Thanks Adrian.

### Reference: which dates are affected?

```
2026-02-16, 2026-02-17, 2026-02-18, 2026-02-19, 2026-02-20,
2026-02-23, 2026-02-24, 2026-02-25, 2026-02-26, 2026-02-27,
2026-03-02, 2026-03-03, 2026-03-04, 2026-03-05, 2026-03-06,
2026-03-09, 2026-03-10, 2026-03-11, 2026-03-12, 2026-03-13,
2026-03-16, 2026-03-17, 2026-03-18, 2026-03-19, 2026-03-20,
2026-03-23, 2026-03-24, 2026-03-25, 2026-03-26, 2026-03-27,
2026-03-30, 2026-03-31, 2026-04-01, 2026-04-02, 2026-04-03,
2026-04-07, 2026-04-08, 2026-04-09, 2026-04-10, 2026-04-13,
2026-04-14, 2026-04-15, 2026-04-16, 2026-04-17, 2026-04-20,
2026-04-21, 2026-04-22
```

(Some weekend/holiday gaps; total: 65 daily delta files in scope.)

---

## 2026-04-30 — Production-side data quality fixes

For production OpenCaseLaw users (mcp.opencaselaw.ch and `data/<court>.parquet`),
the following data-quality issues were fixed on 2026-04-30 and codified in the
nightly rebuild pipeline so future scraper regressions auto-correct:

- **Whitespace docket numbers**: 20,864 rows had leading whitespace in
  `docket_number` (zh_verwaltungsgericht 11,359; ch_vb 6,721; vd_findinfo 2,705
  dominate). All trimmed.
- **Year-0000 date markers**: 796 rows had `decision_date LIKE '0000%'` (mostly
  gr_gerichte default when extraction fails). All converted to NULL.
- **Far-future date typos**: 0 currently (audit floor: > today + 365d).
- **NULL decision_date recovery**: 5,193 rows recovered from full_text via
  anchor-driven extraction in zh_verwaltungsgericht / gr_gerichte / bl_gerichte
  (98.5% recovery rate).
- **EGMR duplicates** (König 2026-04-29 #1): 474 bge-with-cedh-URL duplicates
  removed; canonical entries preserved in bge_egmr.

Each codified as a post-import pass in `build_fts5.py`. See [post_publish_health_check.py](https://github.com/jonashertner/opencaselaw/blob/main/scripts/post_publish_health_check.py)
for the 8-assertion health check that fails the pipeline + alerts on regression.

---

## 2026-04-29 — König audit #1 (EGMR / GL+BS host / http→https)

The 2026-04-29 audit (also by Adrian König) flagged:
1. EGMR cases double-counted under `bge` and `bge_egmr` (474 pairs)
2. GL/BS cantonal scrapers writing relative URLs without host prefix (694 rows)
3. Mixed `http://` / `https://` source URLs

All addressed in commits between 2026-04-29 and 2026-04-30. See git log for
specifics. Production state at 2026-04-30 18:00 UTC: 0 EGMR duplicates, 100%
host-prefixed URLs, 779,037 https URLs.

---

---

## 2026-04-30 — Known per-court data-quality limitations (post-recovery floor)

After today's normalisations + recovery passes, the following per-court NULL
`decision_date` and short `full_text` populations reflect **source-data limits**
(not bugs we can fix without external data). Consumers should be aware:

### NULL decision_date (1,542 of 970,649 rows = 0.16%)

| Court | NULL count | % of court | Cause |
|---|---:|---:|---|
| `ti_gerichte` | 549 | 0.9% | source PDFs are header-only stubs (~1.5K chars typical); decision body absent. **Needs re-scrape from sentenze.ti.ch with deeper extraction.** |
| `mkg` | 542 | 43.6% | Militärkassationsgerichtsentscheidungen Bd 1-15 (1914-2010) historical archive; PDFs don't expose decision dates in machine-readable form. **Needs external academic cross-reference (e.g. ETHZ legal-history database).** |
| `hudoc_ch` | 246 | 29.5% | ECHR mixed-language documents; decision dates often only in metadata, not body. **Could be filled via HUDOC API by case-ID lookup** (e.g. `001-180707`). |
| `gr_gerichte` | 80 | 0.6% | Residual after 90% recovery; remaining 10% have non-standard date formats. |
| `fr_gerichte`, `be_verwaltungsgericht` | 33 each | 0.2% | Per-chamber variation. |
| `sav_kantone`, `sav_international`, `tg_anwaltskommission` | ~36 | various | Aufsichtsbehörden — no PDFs published, only docket+title. |

### Short full_text after migration (64 rows)

| Court | Count | Cause |
|---|---:|---|
| `so_gerichte` | 59 | Has both regeste AND short body — truncated PDF extraction. **Needs scraper PDF re-fetch.** |
| Others | 5 | Distributed; per-row PDF debugging needed. |

### What we did NOT do (and why)

- **Synthetic "YYYY-01-01" placeholder dates**: would mislead consumers using
  `WHERE decision_date BETWEEN ...` filters. NULL is honest about uncertainty.
- **Set decision_date from publication_date when present**: production audit
  showed 0 mkg/ti/hudoc rows have publication_date populated — no fallback
  source available.
- **Tail-text mining for mkg**: dry-run recovered 203 of 542 (37.5%) but
  spot-check showed **~60% of those were cited Bundesratsbeschluss dates,
  event dates, or different cited cases — NOT the decision date**. Wrong
  dates are worse than NULL for date-range filters. Skipped.
- **Force-extract from full_text for ti_gerichte**: text genuinely truncated
  (median 1,271 chars). The decision body — and its date — never made it
  through the scraper's PDF extraction. Solved instead by re-fetching
  source URLs from sentenze.ti.ch (98.5% recovery).

These residuals are tracked in `pending_backlog_2026_04_30.md` for future
sessions targeting per-court scraper PDF re-extraction.

---

## License

This document, like all OpenCaseLaw documentation, is published under **CC0 1.0**.
You may copy, adapt, and redistribute without restriction.
