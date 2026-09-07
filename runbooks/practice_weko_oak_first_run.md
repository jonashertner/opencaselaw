# Runbook: first run of the WEKO Bekanntmachungen and OAK BV practice sources

Added 2026-09-07 on a user's request (together with the BSV "Mitteilungen über die
berufliche Vorsorge", which the 2026-09-04 BSV crawl already holds). Code:
`scrapers/practice/weko_bekanntmachungen.py`, `scrapers/practice/oak_bv.py`, the
Binnenmarkt add-on in `scrapers/weko.py`. Everything below runs on the VPS as root.

## Windows
- Never inside the full build (03:30 → ~17:00 UTC) or the 20:00 UTC incremental. The two
  practice scrapers are network + JSONL only (minutes), the practice.db rebuild is a disk
  write on the data volume: use 17:15–19:45 UTC or after the incremental exits (~20:20 UTC).
- The WEKO decision add-on needs nothing: the nightly 01:00 UTC scrape runs `weko` and picks
  up the three Binnenmarkt pages (~40 PDFs the first night, then one fetch per page).

## Steps (after the code is on the VPS: `git merge --ff-only origin/main`)
1. Run the two sources (expect ~58 + ~250 new rows, 0 failed, a few minutes):
   ```bash
   cd /opt/caselaw/repo && PYTHONPATH=. python3 -m scrapers.practice.runner --only weko_bekanntmachungen,oak_bv 2>&1 | tail -6
   ```
   The runner overwrites `logs/practice_health.json` with these two sources' summary;
   the weekly run (Sat 21:30 UTC) restores the full picture.
2. Rebuild practice.db (writes `<real path>.tmp` beside the resolved symlink target, then
   `os.replace`; workers keep the old inode until their next connection):
   ```bash
   cd /opt/caselaw/repo && PYTHONPATH=. python3 -m search_stack.build_practice_db --jsonl-dir output/practice --db output/practice.db | tail -20
   ```
   Check the sources table lists `weko_bekanntmachungen`, `oak_bv` (and `bsv_weisungen`).
3. Rolling restart so the new tool description and enums are served:
   ```bash
   bash /opt/caselaw/repo/scripts/rolling_restart_workers.sh
   ```
4. Verify from a client: `search_practice(query="Vertikalbekanntmachung", source="weko_bekanntmachungen")`
   (2022 row first; `include_superseded=true` adds 2010 and 2017),
   `search_practice(query="Vermögensverwaltungskosten", issuing_authority="OAK BV")` (W – 02/2013),
   `search_practice(query="berufliche Vorsorge Mitteilungen", source="bsv_weisungen")` (Nr. 168, 2026-07-01).

## What the sources are
- WEKO: one page per language with ~20 PDFs; the "Archiv" section holds superseded versions
  ("Nicht aktuell: …"). One row per (document, version, language); status in topics with the
  FINMA wording; doc_number is the title without dates ("Vertikalbekanntmachung").
- OAK BV: listing pages link one subpage per Weisung/Mitteilung in force (versions + Zusatz-
  dokumente) and list the repealed ones directly; Anhörungen are drafts (doc_type `entwurf`).
  doc_number is the German form for every language ("W – 01/2014"; FR "D –", "C –").
  The IT site borrows FR files ("disponibile in francese"): those rows are dropped and come
  in from the FR page.
