# /coverage/ — source overview and derived freshness (deploy note, 2026-11-08)

Status: built and tested on branch `claude/keen-wilson-a8cf65`, **not committed, not pushed, nothing written on the VPS**. Needs Jonas's OK.

## What changes

| File | Change |
|---|---|
| `docs/coverage/index.html` (+ `en/fr/it/rm` via `make site-languages`) | Rebuilt page: one row per court code (137 today), grouped Bund / authorities / international / 26 cantons; status, intake schedule, holdings, years, newest decision, last row added, cadence, portal count or "not measured"; row opens portal, licence, each feeder's last run, the rule behind the status. Second section: federal and cantonal law, Bundesblatt Materialien, Vernehmlassungen, practice (29 sources), commentaries, scholarship. Hand-written "frozen archives" table removed. Five languages, shared `site.css`/`site.js`, no promo links. |
| `docs/sources/registry.json` (new) | Curated **facts** only: name, official portal, licence key (5-language texts), which scraper writes the code (`fed_by`), retired-feed origin (`feed`), ended series, `superseded_by`. No statuses. |
| `scripts/source_status.py` (new) | Derives one status per code (`fresh / stale / failing / quiet / no_intake / superseded / closed`) from `coverage.json` (rows per year), `stats.json` (newest decision, newest `scraped_at`), the three `scraper_health*.json`, `intake_expected_stale.json` (lag, by-design silences), the scheduler code (`run_scraper.SCRAPERS`, `run_all_scrapers.SKIP_BY_DEFAULT`, `systemd/*.timer`, read with ast/regex, never imported) and `systemctl show` for own-timer units. Collections: counts from `stats.json` + tiny tables of `practice.db`, `materialien.db`, `consultations.db`, `legal_scholarship.db` (`mode=ro&immutable=1`; never `decisions.db`). Reuses `check_intake_rates.evaluate` for the year-rate verdict. |
| `scripts/generate_coverage.py` | After writing `coverage.json`, calls `source_status.generate(...)` → `docs/sources/status.json`. Separate try/except: a failure prints `source_status failed (non-fatal)` and leaves `coverage.json` untouched. No new pipeline step; no extra DB scan. |
| `publish.py` step 6 | **One-line, pipeline-critical:** adds `docs/coverage.json` and `docs/sources/status.json` to the committed paths. `coverage.json` has been regenerated nightly since July but never committed, so the public copy is frozen at 2026-08-19. |
| `docs/sources/status.json` (new) | Initial snapshot built from today's VPS inputs (health 01:52/09:16/10:52, coverage + stats 11:55 UTC) so the page works the moment Pages deploys. Overwritten by step 5g every night. |
| `docs/coverage_notes.json` | `be_steuerrekurs` note corrected: the portal does publish 2026 rulings (100_2025_392 of 2026-08-25); the old "database returns zero rows" text was wrong. |
| `tests/test_source_status.py` (new, 32 tests); `tests/test_publish_step6_rebase.py` | Status rules, schedule parsing, health merge, registry completeness (every corpus code registered, every running scraper feeds a code), wiring, step-6 paths. The step-6 fixture seeds the two new paths, as production has them. |

`make test`: 6,174 passed, 1 failed (`test_consultation_text_gaps::test_real_tesseract_reads_a_rendered_page`: this Mac's Tesseract has no `deu` traineddata; unrelated). `make verify-offline`: green.

## Today's derived picture (2026-11-08)

fresh 104 · quiet 6 · superseded 9 · closed 3 · **stale 7** · **no intake 8**.

- No intake: `vd_omni` (VD CDAP), `sg_gerichte` (SG departments), `be_bvd`, `be_weitere`, `ag_weitere` (retired entscheidsuche feed); `be_steuerrekurs` (scraper registered, not scheduled); `sav_kantone`, `sav_international` (retired import).
- Stale: `ai_gerichte` (newest 2026-05-12, cadence ≈ 16 d), `ar_gerichte`, `gl_gerichte`, `sg_kantonsgericht`, `ne_jurisprudence_adm`, `unwgad` (year-rate detector), `ow_gerichte` (portal frozen since 2022).
- Quiet (not judged): `finma`, `weko`, `lu_gerichte`, `tg_gerichte` (annual batches, `lag_days`), `mkg`, `zg_datenschutzstelle` (by design).

These follow the sibling fixes automatically: when a direct scraper for VD CDAP, SG departments, BE BVD or BE StRK lands, add its key to the code's `fed_by` in the registry. `test_every_running_scraper_feeds_a_registered_code` fails until that is done.

## Deploy (after OK)

1. Commit on a branch from `origin/main`, push, `git merge --ff-only` on the VPS (memory `mcp-deploy-path`). No server restart: nothing in `mcp_server.py` changed.
2. GitHub Pages serves the page with the committed `status.json` immediately.
3. Next publish run: step 5g logs a line `sources/status.json: 137 court codes (...)`; step 6a commits `docs/coverage.json` and `docs/sources/status.json`.

Verify after the first night:

```bash
grep -E "sources/status.json|source_status failed" /opt/caselaw/repo/logs/publish.log | tail -2
python3 -c "import json;d=json.load(open('/opt/caselaw/repo/docs/sources/status.json'));print(d['generated_at'],d['summary'])"
```

Expect `unmapped: []` and `orphan_scrapers: []`; anything listed there is a new court code or scraper missing from the registry.

Rollback: revert the commit; step 6 then stops committing the two files and the page reverts to the old one.

## Follow-ups (not done here)

- `docs/intake_expected_stale.json` still exempts `be_bvd`, `vd_omni`, `sg_gerichte` with reasons the 11-08 audit disproved, so the nightly intake alert stays silent on them. The page no longer shows those reasons, but the alert policy should be corrected by the sessions fixing those scrapers.
- `estv` holds 3 stray Kreisschreiben rows (2026-06-20); ESTV practice lives in `practice.db`. Shown as superseded.
- `legal_scholarship.db` has a record dated 2027; the page caps the year span at the current year.
- BStGer TPF 2022–2025 is a gap inside `bstger`, below court-code granularity, so it is invisible here.
- The year-rate STALE verdicts for AR, GL, SG KG, NE adm. and UNWGAD come from the existing detector and want triage (yearbook dating for AR is a likely false positive).
