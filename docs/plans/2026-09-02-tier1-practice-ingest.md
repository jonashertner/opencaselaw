# Plan: Tier 1 practice sources for the Caritas demo (Approach A)

Date 2026-09-02. The source memo is archived in the private maintenance workspace.
Scope approved: federal PDF sources only. SKOS deferred (licence). Cantonal HTML handbooks deferred.

## Acceptance criteria
1. `search_practice` returns ranked hits for all four zero-result probes:
   "Ergänzungsleistungen Vermögensverzicht" (BSV WEL), "Prämienverbilligung" (BAG KS 5.x / KVV context),
   "Arbeitslosenentschädigung Einstellung Anspruchsberechtigung" (SECO AVIG-Praxis ALE),
   "Existenzminimum Lohnpfändung" (BJ SchKG / cantonal Kreisschreiben) — verified against a locally built practice.db.
2. `make test` passes; new tests are offline (golden fixtures); no live network in tests.
3. Every new scraper runs with `--max-new 3` live without error and yields records with non-empty body, date, language, pdf_url.
4. BSV keeps one row per (document, version, language); superseded versions are retained and distinguishable by `date` and a topic tag.
5. `search_practice` tool description/enums reflect the new sources; the discoverability test is updated, not deleted.
6. Nothing in `publish.py`, `base_scraper.py`, `state/`, `decisions.db` paths is touched. No commit, no deploy.

## Files
Create: `scrapers/practice/bsv_weisungen.py`, `seco_alv.py`, `bag_kvg.py`, `sem_handbuch_asyl.py`, `bj_schkg.py`;
tests `tests/test_bsv_weisungen.py`, `test_seco_alv.py`, `test_bag_kvg.py`, `test_sem_handbuch_asyl.py`, `test_bj_schkg.py`;
fixtures under `tests/fixtures/practice/` (trimmed excerpts of pages captured 2026-09-02).
Modify: `scrapers/practice/runner.py` (register), `search_stack/build_practice_db.py` (upsert SET list),
`mcp_server.py` (search_practice description + enums, get_practice docstring, server instructions count),
`tests/test_search_practice_discoverability.py`, `tests/test_practice_reissue_detection.py` (pin new REVISION_FIELDs).
Read-only reference: `scrapers/practice/base.py`, `finma_rundschreiben.py`, `seco_arg.py`, `estv_kreisschreiben.py`.

## Design per scraper

### bsv_weisungen (BSV, sozialversicherungen.admin.ch)
- Discovery: GET `/de/home`; parse `nav#secondary-navigation` nested `<ul>/<li>` to get every folder `/de/f/<id>` with its section path.
  Include sections: Alle Sozialversicherungen, ATSG, AHV, IV, EL, ÜL, BV (2. Säule), KV, UV, EO, FamZ.
  Exclude section International entirely (EESSI/BUC/SED tooling, treaty texts are in Fedlex).
  Exclude folder labels matching /Links|Statistiken|Formulare|Verträge|Tarife|Spitex|Support|Standards|Vorlagen|Adresslisten|Anträge/.
- Per folder: GET `/de/f/<id>`; `table.bsv-documents` → parent rows (abbreviation in `<strong>`, title in following div, doc id from `/de/d/<id>`),
  inline versions table (`tr` with "Version N", Geändert date, language spans) and details rows (`tr.bsv-version-details[data-version]`:
  `<p>` full title, `dl` with Versionsnummer / Dokumentennummer).
- One stub per (doc id, version, language): pdf_url `https://sozialversicherungen.admin.ch/{lang}/d/{id}/download?version={n}`,
  url `https://sozialversicherungen.admin.ch/{lang}/d/{id}`. Verify at implementation that `/fr/d/<id>/download?version=N` returns the French file.
- doc_id `bsv_weisungen_{id}_v{n}_{lang}` (FINMA model; upsert trap avoided). doc_number = Dokumentennummer if present else abbreviation.
  date = version "Geändert" (ISO). doc_type from title keyword: Wegleitung→wegleitung, Kreisschreiben→kreisschreiben, Rundschreiben→rundschreiben,
  Mitteilung→mitteilung, Nachtrag→nachtrag, else weisung; folder "Rechtsprechung"/"AHI-Praxis" → rechtsprechung.
  topics: [abbreviation, section (AHV/IV/EL…), folder label, "aktuelle Version" | "frühere Version"].
- No REVISION_FIELD needed (version in id). Override `fetch_pdf_text` to fetch WITHOUT the /tmp cache (base caches every PDF forever;
  BSV is ~2,600 docs × versions × languages, cache would grow to many GB). REQUEST_DELAY 1.0 s; honour robots.txt (check at implementation).
- Class attr `FOLDER_ALLOWLIST: set[str] | None` for scoped runs; `--max-new` for smoke.
- First full run is long (hours). Run manually with nohup; the systemd unit's TimeoutStartSec=3600 would kill it. Document in plan §Ops.

### seco_alv (SECO, arbeit.swiss)
- Pages: de `/de/informationszentrum/publikationen`, fr `/fr/publications-fr`, it `/it/pubblicazioni` (old secoalv URLs 301 here).
- Section: the `<h2>` whose text matches /AVIG-Praxis|LACI|LADI/ (DE "Weisungen / AVIG-Praxis / Richtlinien"); collect `fileservice` anchors
  from following siblings until the next `<h2>`.
- title = anchor text minus trailing "PDF | dd.mm.yyyy"; date = "gültig ab|valable dès|valida dal d.m.yyyy" if present else trailing date;
  doc_number = "AVIG ALE" style code or "2026/01" via regex, else slug(title). doc_id `seco_alv_{slug(doc_number)}_{lang}`.
- REVISION_FIELD "pdf_url" (UUID media URL changes on re-issue). topics ["AVIG"/"LACI"/"LADI", code]. doc_type weisung (Leitfaden→weisung too).

### bag_kvg (BAG)
- Pages: de `/de/krankenversicherung-kreisschreiben-schweiz`, fr `/fr/assurance-maladie-circulaires-suisse` (IT page has no PDFs).
- Anchors with `/dam/` + `.pdf`; doc_number "KS 5.1" from /(\d+\.\d+)/; date from DE/FR month-name text; doc_id `bag_kvg_{num}_{lang}`;
  REVISION_FIELD "pdf_url"; doc_type kreisschreiben; topics ["KVG", number].

### sem_handbuch_asyl (SEM)
- Pages: de/fr `/sem/{lang}/home/asyl/asylverfahren/nationale-verfahren/handbuch-asyl-rueckkehr.html` (46 articles each; IT has none — tolerate 0).
- Anchors `hb-art-*.pdf`; doc_number = article code from "Artikel|Article C6.1"; date from "(PDF, …, dd.mm.yyyy)"; doc_id `sem_handbuch_asyl_{code}_{lang}`.
- pdf_url is STABLE across re-issues → REVISION_FIELD "date" AND override `fetch_pdf_text` without cache (otherwise a re-issue re-appends cached old text).
- doc_type handbuch; topics ["Handbuch Asyl und Rückkehr", chapter letter + chapter heading from page].

### bj_schkg (BJ, Oberaufsicht SchKG)
- Pages: Weisungen de `/de/weisungen-schkg`, fr `/fr/instructions-lp`, it `/it/istruzioni-lef`; Kreisschreiben de `/de/kreisschreiben-schkg`,
  fr `/fr/circulaires-lp`, it `/it/circolari-lef` (0 PDFs, tolerate). Skip `.zip` annexes (log count).
- doc_number: "Weisung Nr. N" → "Weisung N"; cantonal Kreisschreiben from filename `01-zh-ks-d.pdf` → "ZH KS 01"; doc_id from filename stem.
- date from anchor text (DE/FR/IT month names); REVISION_FIELD "pdf_url"; doc_type weisung | kreisschreiben; topics ["SchKG", canton or "CH",
  "Existenzminimum" when title matches /Existenzminimum|minimum vital|minimo vitale/].

## Shared changes
- `runner.py`: add all five to ENABLED_SCRAPERS.
- `build_practice_db.py`: ON CONFLICT SET also date, pdf_url, doc_number, doc_type, url, language, topics_json (re-issue must refresh them).
- `mcp_server.py`: search_practice description — replace "NOT covered: BSV/AHV-IV, BAG…" with the new coverage; add sources/authorities/doc_types
  to enums; counts filled from the local build. get_practice docstring example. Server instructions line mentioning practice counts.
- Tests: discoverability test tuples + window; reissue test pins for the five REVISION_FIELD values.

## Test strategy
Parser tests per scraper on golden fixtures via `Scraper.__new__` (no __init__, no network), asserting: count, doc_number/date/language/pdf_url
present, doc_id uniqueness, language-scoped ids, REVISION_FIELD value. One `run()` test for BSV using the `_Stub` pattern with a stubbed
`fetch_pdf_text` (per-version rows, superseded retained). Live smoke: `python -m scrapers.practice.runner --only <key> --max-new 3 -v` (manual, not pytest).

## Risk class: low (new files + additive shared edits). Rollback: delete the five modules + remove from runner; practice.db rebuild without their JSONL.

## Ops notes (for the deploy step, not this change)
- Move `opencaselaw-practice.timer` off Sat 06:00 UTC (inside Saturday full build) before enabling in prod.
- First BSV run manually: `nohup python3 -m scrapers.practice.runner --only bsv_weisungen` (hours; 3600 s unit timeout would kill it).
- Rebuild practice.db and rolling-restart workers as the unit already does.

## Amendments after the adversarial plan check (2026-09-02, 4 lenses, 30 findings)
- Description budget: `search_practice` description is at 1,018/1,024 chars (M365 truncation test). Top-level description keeps
  authority names + total; per-source counts move into the enum `description` strings. `tests/test_tool_description_budget.py` pins updated.
- Enum edit is load-bearing (mcp 1.26 validates inputSchema before dispatch): enums ship with the scrapers; enum values == SOURCE_KEY.
- Version crowding: `_search_practice` collapses IN SQL (window function, before LIMIT) to the newest row per document unless
  `include_superseded=true`; document identity = doc_number, except BSV where series share an abbreviation → the per-document url;
  the "aktuelle/frühere Version" topic tag is dropped (it would decay); currency = `date` + version number in doc_id + `Version N` topic.
- Doc ids: every new scraper overrides `_make_doc_id` to include the language. SECO strips the validity clause before slugging and uses a
  stable code regex. BSV doc_number = the abbreviation (WEL/KSRP); Dokumentennummer goes to topics. BJ doc_type/doc_number come from the
  anchor text (Weisung/Richtlinien/Kreisschreiben/Konkordat/Erlass), first date in text = issuance date; federal SchKK circulars page added.
  SEM Handbuch article code from the filename segment (FR labels I2 what DE calls I1); chapter headings hard-coded.
- Safety: BSV registered in EXPERIMENTAL_SCRAPERS (never picked up by the Saturday unit) until the unit gets TimeoutStartSec=infinity and a
  lock; `CACHE_PDFS=False` on BSV and SEM Handbuch (30 GB cache / stale-cache re-issue); upsert uses COALESCE(NULLIF()) so a blank field
  never overwrites a good one; sources.doc_count counts rows; empty stub date never counts as a re-issue.
- Acceptance criteria corrected: probe "Prämienverbilligung" is answered only in its EL-calculation sense (WEL), IPV stays cantonal and
  NOT covered; probe "Sozialhilfe Grundbedarf Kürzung" stays unanswered (SKOS/cantonal handbooks deferred) and is disclosed as such in the
  tool description; substitute probe "Existenzminimum" verified against a cantonal Kreisschreiben. Two SECO Richtlinien carry no date (allowed).
- BSV scope: sections AHV, IV, EL, ÜL, BV, KV, UV, EO, FamZ, ATSG, Alle Sozialversicherungen; International, eGov, Altersfragen excluded;
  label regex extended with Verzeichnisse. 41 folders in scope today.
- Licence: admin.ch "schriftliche Zustimmung" boilerplate seen on all hosts; overridden by Art. 5 URG exactly as for ESTV/SEM/FINMA.
  Follow-up for Jonas (not this change): one sentence in README/dataset_card extending the official-texts statement to Verwaltungsverordnungen.
- Ops follow-ups (deploy step): TimeoutStartSec=infinity or per-source flock; move timer off Sat 06:00; size practice.db after first BSV run
  and move it to the data volume if > 500 MB; stage the first BSV crawl per section over off-peak evenings.

## Implementation status (2026-09-02, working tree, uncommitted)
Done: five scrapers + fixtures + 29 tests (`tests/test_tier1_practice_scrapers.py`), runner registration (BSV experimental),
practice base (`CACHE_PDFS`, `NO_TEXT_LAYER_BODY`, `first_date_iso`), null-safe upsert + row counts in `build_practice_db.py`,
`search_practice` version collapsing + `include_superseded`, description (1,023 chars) + enums, routing rows, updated budget /
discoverability tests. `make test`: 2,502 passed; the single failure (`test_be_bvd_not_registered_until_productive`) is the
pre-existing uncommitted `run_scraper.py` change, not this work.
Live smoke: all five scrapers run clean with `--max-new 3`; full runs of seco_alv / bag_kvg / sem_handbuch_asyl / bj_schkg and a
scoped BSV run (folder 5638, 92 records) verified locally — see the acceptance section below once filled.
Known data facts: 4 BAG Kreisschreiben (1.1, 1.2, 2.2, 5.2) are image-only scans → indexed by title/number with the placeholder body.
The IT BSV folder page renders de/fr-only documents without an Italian file (fixed: no fallback stub for a language the row lacks).

## Deploy sequence (for Jonas — nothing here is done yet)
1. Commit + push; on the VPS `git merge --ff-only` (never scp into the tree).
2. `systemd/opencaselaw-practice.service`: `TimeoutStartSec=infinity` (or a per-source flock in runner.py) and a Description that
   names all sources; move `opencaselaw-practice.timer` off `Sat 06:00 UTC` (collides with the Saturday full build), e.g. `Sun 06:00 UTC`.
3. First ingest by hand, off-peak, after the nightly build has exited (invariant 9):
   `cd /opt/caselaw/repo && nohup python3 -m scrapers.practice.runner --only seco_alv,bag_kvg,sem_handbuch_asyl,bj_schkg &`
   then `nohup python3 -m scrapers.practice.runner --only bsv_weisungen &` (hours; ~10k+ PDFs at 1 req/s; stage per section with
   `FOLDER_ALLOWLIST` if the host or disk complains). `output/practice/bsv_weisungen.jsonl` will be several GB of text.
4. `python3 -m search_stack.build_practice_db --jsonl-dir output/practice --db output/practice.db`; size it — if practice.db > 500 MB
   move it to `/mnt/HC_Volume_*/output/` and symlink like the other DBs. Then `scripts/rolling_restart_workers.sh`.
5. Promote `bsv_weisungen` from EXPERIMENTAL_SCRAPERS to ENABLED_SCRAPERS (one-line change) once step 3 has completed once.
6. Refresh the counts in the `search_practice` enum descriptions and the "3,400+" total from `SELECT source, COUNT(*) FROM practice`.
7. `make smoke` + `scripts/tool_surface_check.py`; re-run the four memo probes against production.
8. README / dataset_card: one sentence extending the official-texts (Art. 5 URG) statement to Verwaltungsverordnungen; practice.db
   stays out of the CC0 Parquet mirror as today.

**Sequencing rule (load-bearing):** BSV rows only arrive with the manual ingest. Until `bsv_weisungen` is promoted in runner.py the
shipped description lists BSV under NOT covered as "ingest in progress" (decided 2026-09-03: four small sources go live first). Do NOT roll workers between step 1 and step 4, and mask the practice timer
(`systemctl mask opencaselaw-practice.timer`) until BSV is indexed — otherwise a Saturday run rebuilds practice.db without BSV and
rolling-restarts workers that then promise a corpus with zero rows. Land code and ingest in the same off-peak window.

Review follow-ups folded into the sequence (2026-09-02 code review):
- Step 1 is a SCOPED commit (`git add -p`): mcp_server.py carries ~57 unrelated hunks; `run_scraper.py` is uncommitted and fails
  `test_be_bvd_not_registered_until_productive` on its own.
- Step 4: `build()` now resolves a symlinked `--db` path itself, so the `.tmp` and the replace land on the volume. Still point the
  unit's `--db` at the real path once moved.
- Step 6 wording: the description is at 1,023/1,024 chars — a five-digit total must be written as "14k+", or the total moves into an
  enum description; `tests/test_tool_description_budget.py` accepts either "\d,\d{3}\+? documents" form, adjust the regex if needed.
- Step 7, before the rolling restart: `python3 -c "import sqlite3; print(sqlite3.sqlite_version)"` must be ≥ 3.25 — the collapse
  uses window functions (the only `OVER (` in mcp_server.py). Below that, every default search_practice call errors.
- A manual `--only bsv_weisungen` run overwrites `logs/practice_health.json` with BSV's summary alone (read by
  `scripts/collect_dev_data.py`); run the four small sources again afterwards or accept one distorted rollup.
- Non-PDF HTTP 200 responses (login bounce) are now failures, never placeholder records; BAG scans confirmed: 1.1, 1.2, 2.2, 5.2,
  6.1, 7.3 (6 DE + 6 FR = 12 placeholder rows).

## Acceptance results (local practice.db built 2026-09-02 12:23 from output/practice; 1,589 rows incl. local estv/finma)
- "Ergänzungsleistungen Vermögensverzicht" → WEL (BSV) top; 19 superseded WEL editions collapsed; include_superseded lists them.
- "Arbeitslosenentschädigung Einstellung Anspruchsberechtigung" → AVIG ALE, RVEI, AMM (seco_alv).
- "Existenzminimum Notbedarf" / "Existenzminimum Lohnpfändung" → cantonal Richtlinien OW/GL/SZ/NW/BL, LU KS, BS Weisung (bj_schkg).
- "Dublin-Verfahren Zuständigkeit" (SEM) → Handbuch Art. C3/C4/E5/F7/C5.
- "Prämienverbilligung" → WEL only (EL-calculation sense, as amended); IPV remains cantonal/uncovered.
- "Sozialhilfe Grundbedarf Kürzung" → WEL only (EL context) — disclosed as NOT covered in the tool description.
- Scanned PDFs indexed with placeholder body: bag_kvg 12 of 38, bj_schkg 83 of 174 (historical SchKK circulars 1892–2004 and
  2000-era cantonal KS). Findable by title/number only. Follow-up: OCR pass (no tesseract on the dev machine; the repo already
  has ~5k scanned decisions in the same state, see TECHNICAL_OVERVIEW §12).
- Row counts: seco_alv 54, bag_kvg 38, sem_handbuch_asyl 92, bj_schkg 174, bsv_weisungen 95 (folder 5638 only). Local DB 129 MB
  (dominated by finma_rs).
- Note for the reviewer: `mcp_server.py` carried ~57 unrelated uncommitted hunks before this session (get_erwaegung / structure
  edits); this change is the 18 hunks touching search_practice, the tool schema and the server instructions.
