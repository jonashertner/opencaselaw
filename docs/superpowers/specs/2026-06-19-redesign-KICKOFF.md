# KICKOFF — opencaselaw.ch world-class redesign (run this in a fresh session)

Paste-ready brief for a new Claude Code session. **Read the full design first:**
`docs/superpowers/specs/2026-06-19-dashboard-worldclass-redesign-design.md` — the
**"v2 — RADICAL whole-site rethink"** section is authoritative.

## Mission
Rebuild **opencaselaw.ch** to an *absolute world-class, distinctive, CONSISTENT, de-crowded*
standard. The substance is best-in-class (complete CC0 Swiss legal corpus + cryptographic
provenance + AI-verifiable citations); the presentation must finally match it.

## Locked decisions (do NOT re-litigate)
- **Central message = completeness + daily updates.** Positioning: *"The complete record of
  Swiss law — rebuilt every day."* Giant number = proof of completeness; a prominent daily-fresh
  signal proves it's living. This is the throughline on every page.
- **Keep on homepage:** live corpus stats + a radically-simplified Connect flow. **Demote**
  verifiable/provenance + scholarship to secondary links (not homepage real estate). Move the
  4-tab live demo to its own page or cut.
- **Rethink navigation entirely** — kill the flat 10-item engineering list. Minimal, purposeful
  nav (corpus browse + a Connect CTA + language); meta/trust pages (API, Standards, Integrity,
  Quality, Methodology, Word) live in the footer / a "Docs" group.
- **Aesthetic = Refined Swiss Modernist.** Restrained, generous whitespace; strong display type +
  JetBrains Mono "data-terminal" labels; surgical Swiss-red accent (`#b91c24` for text/links — AA;
  `#da291c` decorative/fills/live-pulse only, ≤5% of surface); hairline Swiss-grid as structure;
  ONE orchestrated motion pass per page (all `prefers-reduced-motion`-safe); signature moment =
  a **Switzerland cantonal-coverage map** (visual proof of completeness).
- **Execution = system + all pages in parallel, against ONE locked contract.**

## The hard rule: consistency comes from ONE source of truth
Today there are **4 divergent i18n systems** and **~15 hand-copied nav/footers**, and the homepage
is a **6,750-line inline-styled island**. You cannot patch your way to "consistent." Build:
- ONE rebuilt `docs/static/css/design-system.css` (v3),
- ONE page shell `tools/layout.html` (new nav + footer),
- ONE locale source (collapse the 4 i18n systems),
- finish `tools/build_docs.py` so **every** page is generated from the shell (delete the inline
  homepage island; rebuild it as a `src/pages/index/` fragment like every other page; have
  `seo_pages.py` consume the same chrome).
`build_docs.py --check` must gate drift in CI / `make test`.

## Execution steps
1. **Lock the contract:** design-system.css v3 + `tools/layout.html` (new nav/footer) + one locale
   source + the **canonical homepage fragment** (completeness-centered, ~6 sections, de-crowded).
   Get the homepage right first as the reference; show the owner before deploy.
2. **Roll out in parallel** (Workflow / parallel agents) — rebuild every page as `src/pages/*`
   fragments against the locked shell: hubs (search/courts/laws/coverage), integrity/standards/
   quality **reimagined as flagship editorial** (calm, big, authoritative — esp. Integrity: a
   beautiful "prove any decision" Merkle page), + detail pages via `seo_pages.py`.
3. **Verify** each: serve `docs/` locally (`cd docs && python3 -m http.server 8899`) + Playwright
   across **5 langs × light/dark × mobile** — assert: no console errors, no raw-key leaks, exactly
   one `<h1>`/page, AA contrast (fix `--text-3` → `#6b7280`), count-up + reveals respect
   reduced-motion. Keep `make test` green; extend `tests/web/test_dashboard_i18n_parity.py` to glob
   ALL pages.
4. **Deploy:** bump the CSS cache-bust `?v=`, commit ONLY redesign files under `docs/` + `tools/` +
   `seo_pages.py`, push → GitHub Pages auto-deploys. Show the owner before each deploy.

## Fold in the audit (Tier-0 must-fixes, all in the main spec)
Missing homepage `<h1>`; the **decision-count drift** (single-source from `stats.json`/`/health` at
build time — live number is **991,298**, never hardcode an exact figure, use a "990,000+" band);
**delete the dead Chart.js** (70 KB, renders nothing, blanks the KPI grid on CDN failure); the
**broken Connect tablist** (role=tablist w/ no tab/keyboard); contrast/focus/target-size; render
`governance-and-removal-policy.md` → `docs/governance/index.html` (footer links 404 to raw .md);
branded **404.html**; `theme-color` + a manual light/dark toggle; **SEO host-split** (detail pages
on `mcp.opencaselaw.ch` unlinked from the brand host — unify + add `Sitemap:` to robots.txt +
richer `Dataset` JSON-LD + BreadcrumbList); self-host fonts.

## Constraints / facts
- Live site = static `docs/` on GitHub Pages (`docs/CNAME`); push to `main` auto-deploys.
- 5 languages de/fr/it/rm/en via `?lang=`. **Romansh is machine-translated** — flag new RM strings
  for native review in `i18n-romansh-review.md`.
- **DON'T commit the pre-existing non-redesign changes** (publish.py, scrapers/cantonal/be_bvd.py,
  run_scraper.py, scrapers/cantonal/registry.py, the private maintenance log, .agents/, .codex/, etc.) —
  they predate the redesign.
- Production access details are private. Don't run heavy ops during a nightly publish.

## Starting state
Working tree reverted to the clean live baseline (the redesign first-draft was reverted; its
tokens — `--text-3` #6b7280, accent `--accent-bright` #da291c, heading weights 600/700, motion
primitives `.reveal`/`.live-dot`/`.grid-rule` — are documented in the main spec; re-apply + expand
in v3). Nothing redesign-related is deployed.

## Unrelated (not part of this)
Scraper post-publish ops (ch_bundesrat supersede + be_verwaltungsgericht backfill) are gated on the
running nightly publish and tracked separately.
