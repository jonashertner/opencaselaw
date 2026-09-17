# Public statistics page (`/stats/`) — what shipped, what needs approval

Status: page built 2026-09-17, untracked, not committed. Pipeline-side items below are proposals only.

## What the page is

`docs/stats/index.html`, served by the existing GitHub Pages site at `https://opencaselaw.ch/stats/`.
It hydrates from `/stats.json`, which the nightly rebuild already regenerates (publish.py Step 5 / 5e)
and commits (Step 6). So every figure on the page updates daily with **no pipeline change**.

Elements covered, all from fields that already exist in `stats.json`:

| Section | Source fields |
|---|---|
| Hero + anatomy (records → unique → decisions of Swiss courts and authorities, shown ≈) | `total`, `unique_decisions*`, `duplicate_representations`, `by_court` (ECtHR prefix sum, `ch_vb`) |
| Decisions | `federal_vs_cantonal`, `by_language`, `by_year` (clipped to the current year), `by_canton` (26 cantons), `top_courts`, `delta.by_court`, `interesting_stats.echr_switzerland` |
| Growth | `docs/stats/history.json` (see below) |
| Legislation | `corpus.federal_laws`, `corpus.*_articles`, `corpus.cantonal_laws_by_canton`, `upcoming_amendments` |
| Legislative materials | `interesting_stats.materialien_coverage` |
| Commentaries & scholarship | `corpus.commentaries`, `corpus.scholarship_by_source`, `corpus.scholarship_by_type` |
| Citation graph | `corpus.citation_edges`, `corpus.statute_edges`, `interesting_stats.most_cited_*`, `top_5_*` |
| Quality | `interesting_stats.regeste_coverage`, duplicate share, `date_range`, `oldest_decision` |
| Freshness | `generated_at`, `scraper_health.run_at_daily`, `run_at_federal`, `delta.total` |
| Usage | `traffic.total_calls`, `by_client`, `status_hist`, `privacy` — aggregate only; per-endpoint error rates, latencies, `reach`, and anything query-level are deliberately not shown (Jonas, 2026-09-17: keep the block, publish nothing about user queries) |

Every `interesting_stats` card is guarded: Step 5 runs with `--no-interesting-stats` and pushes at 6a,
so during the build window those fields can be absent. The page renders “—” and a “not computed yet”
note rather than failing. `unique_decisions_status !== "current"` hides the unique figures the same way
the homepage does. `traffic` absent hides the Usage section.

Shared chrome: `site.css v3` + `site.js` (`OCL.init`), five languages (de/fr/it/rm/en), dark mode via
the existing tokens, keyboard-visible focus, reduced-motion respected, phone width verified at 400px.

Also changed (unstaged): a **Statistics** link under *Browse* in the shared footer
(`docs/static/js/site.js`, five strings) and in the homepage’s own inline footer (`docs/index.html`).

## The growth series: `docs/stats/history.json`

`stats.json` holds only the current day. The growth chart needs one point per day, so
`docs/stats/history.json` was seeded from the git history of `docs/stats.json` (137 days, 2026-05-01 →
2026-09-16). `unique` is `null` on days the manifest was stale (as on 2026-09-10) rather than derived.

Until the appender below is wired, the file is **static** and the page labels the chart “as of <date>”.

`scripts/append_stats_history.py` is written and tested (`tests/test_append_stats_history.py`, offline):
idempotent per day, atomic write, refuses bad input without writing.

## Proposals — need explicit approval (invariant 5: pipeline gate)

### P1. Wire the appender and commit the file (publish.py Step 6)

Two lines in `publish.py`:

1. In `step_6_git_push`, before the diff check, call the appender the same way `_sync_homepage_fallbacks`
   is called: `subprocess.run([sys.executable, "scripts/append_stats_history.py"], timeout=30)`,
   logging a WARN on non-zero, never failing the publish.
2. Add `"docs/stats/history.json"` to the `paths` list so Step 6 commits it.

Alternative with no publish.py edit: call it from inside `scripts/sync_homepage_fallbacks.py`, which
Step 6 already runs under a WARN-only wrapper. But `paths` still needs the new file, so publish.py is
touched either way. Risk: low (30 s timeout, WARN-only, 33 KB file). Blast radius if the script
misbehaves: the growth chart stops advancing; nothing else.

### P2. Three missing fields in `generate_stats.py`

Not in `stats.json` today:

- **`duplicates_by_court`** from `representation_manifest.db` (member rows per court). The hero's third
  tier subtracts all `ch_vb` rows from the unique count, but the manifest already collapses byte-identical
  VPB rows inside `unique_decisions`, so the figure is shown as ≈ and described as a lower bound. With the
  per-court duplicate count the page can subtract only the *unique* VPB rows and drop the ≈. Cheap: the
  manifest is small and already opened by `_representation_dual_count`.

- **Administrative practice (Verwaltungspraxis)**: ~9,900 documents in `practice.db`. Add
  `interesting_stats.practice_coverage = {total_documents, by_source: {source: n}, by_authority}`
  mirroring the `materialien_coverage` block (exists-check, table check, `except sqlite3.Error`).
  The page already renders `k-practice` when `interesting_stats.practice_coverage.total_documents`
  is present. **Open question**: `generate_stats.py` resolves databases under `repo_dir/output/`,
  while `mcp_server.py` reads `practice.db` from `DATA_DIR` (`SWISS_CASELAW_PRACTICE_DB`). Confirm
  where the VPS copy lives before wiring; a wrong path silently yields nothing.
- **Structure coverage**: share of decisions with an indexed Erwägung (72.5 % on 2026-09-06 per the
  structure-coverage memo). Only if it can be answered from a small table or the daily rollup; a
  full scan of the 52 GB sidecar is out of the question during the build window (invariant 9).

Both run inside Step 5e (interesting_stats). Risk: medium only because of *where* they run; the code
pattern itself is the guarded one already used for materialien.

### P3. Static fallbacks for the stats page

The page carries hardcoded fallback numbers (2026-09-16 values) for crawlers and no-JS readers, like
the homepage. `scripts/sync_homepage_fallbacks.py` could rewrite them nightly too. It must not let a
stats-page failure abort the homepage rewrite (the script is strict / all-or-nothing by design), so
implement as a separate function with its own try/except, or a sibling script. Until then the
fallbacks drift by design and the hydrated values win for everyone with JS.

### P4. Deploy

Nothing here is committed. Deploy path is the usual: review → commit → push → the site is static so
GitHub Pages publishes on push; `site.js` changes are cache-busted by `?v=2` today, so bump to `?v=3`
across sub-pages if the footer link must appear immediately for returning visitors.

## Verification done

- Desktop 1380 px and phone 400 px (iframe probe): no horizontal overflow, all figures hydrated,
  zero skeletons left, zero page errors.
- Degraded `stats.json` (no `interesting_stats`, no `traffic`, `unique_decisions_status: stale`,
  empty delta): renders cleanly with “—” and notes.
- All five `?lang=` variants; 154 keys per language, every `data-t` key present in all five.
- Dark tokens applied: charts, hatch, tooltips legible.
- Hover tooltips on both charts; Chart/Table toggles; growth-chart step labels computed from data.
- `node --check` on the inline script; CSS braces balanced; `tests/test_append_stats_history.py` 6/6.
