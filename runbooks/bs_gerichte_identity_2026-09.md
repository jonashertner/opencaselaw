# Runbook: BS Gerichte identity (case number → decision number) and text re-fetch

**What was wrong (found 2026-09-17).** rechtsprechung.gerichte.bs.ch showed 11,005
rows; we served 10,628 under the two portal court codes (plus 37 + 15 Rekurskommission
rows and 2 entscheidsuche leftovers that are not on that portal, which is how "10,682"
arises). The 377 missing rows are:

| Cause | Rows |
|---|---|
| Several decisions listed under one case number; `decision_id` was court + case number, so only the first-listed sibling was ever stored (331 groups: 293 pairs, 35 triples, 2 of four, 1 of five). In 281 groups the stored one was the later, usually trivial decision (Kostenerlass) and the judgment was lost. | 350 AG + 23 SVG |
| Zivilgericht instance not in the scraper's sources | 3 |
| `bs_appellationsgericht_AK.2012.25` in state but dropped from the DB by the cross-court dedup against the es row of the same decision; never retried | 1 |

Not a cause: the 500-row page cap. AG 2014–2025 each exceed 500 hits and every
nightly run parsed only 500, but every unique case number was already in state after
months of runs. The cap does delay new decisions for those years and would have hidden
the missing siblings, so it goes too.

**Found on the way: the served text is truncated.** The document extractor kept only
`<p class="MsoNormal">`; the Word export files most body text under other styles
(aaText, Entscheidtext, aaDispositiv, MsoBodyText …) and whole paragraphs under `<h2>`.
Sozialversicherungsgericht rows: median 1,742 characters, 2,104 of 2,198 under 2,000
(one checked ruling: 1,695 characters stored, 26,233 with the fixed extractor);
Appellationsgericht: 270 rows under 2,000 and 922 under 5,000 characters. Fixed in the
scraper for new rows; existing rows need one polite re-fetch (phase B below).

**The fix (this change set).**

- `scrapers/cantonal/bs_gerichte.py`: `decision_id` = court + the court's decision
  number (`docket_number_2`, "AG.2014.40"), which is unique across the portal; the cited
  case number stays in `docket_number`. 2,000-row pages (largest year is 793; the server
  honours it), a per-Geschäftsart split for a year that still overflows, "search failed"
  ERROR lines where the health check counts them, the Zivilgericht as third source
  (`bs_zivilgericht`), `portal_count` on the health line, every block element in the
  document text, and a startup guard that refuses to run against a state file still on
  the case-number scheme.
- `scripts/migrate_bs_gerichte_ids.py`: re-keys the 10,629 shard rows offline (no
  re-fetch), records the old id in `previous_decision_id`, appends the new ids to the
  state file (old ids stay, harmless). Idempotent; refuses to write on any collision.
- `scripts/refetch_bs_gerichte_text.py`: phase `fetch` walks the shard and stores each
  document's text in a sidecar (hours, no shard writes, resumable, keyed on the decision
  number so it works before or after the re-key); phase `apply` rewrites the shard once
  and only ever lengthens a row's text.
- `db_schema.py` / `build_fts5.py`: `decision_id_aliases` (previous_id → decision_id),
  filled at insert time; dedup keys BS rows on the decision number so two decisions
  under one case number on the same day are not a "duplicate".
- `mcp_server.py` / `seo_pages.py`: an old id resolves (get_decision, both resolvers,
  attest) to exactly the decision it pointed at, `resolved_via = previous_decision_id`;
  `/entscheid/<old id>` is a 301 to the new id.
- `decision_ref.py` (bare "AG.2014.40" / "SVG.…" / "ZG.…" mint the BS candidate),
  `branch_map.py`, add-in i18n: the new court code. `proceeding_map` is left unmapped
  for `bs_zivilgericht` on purpose (no clean key for a first-instance civil court).

Kept on purpose: the 2 entscheidsuche rows under `bs_gerichte` (Jonas, 2026-09-17: keep
existing rows even if they came from entscheidsuche.ch). The cross-court dedup still
collapses an es row and its direct twin to one decision (longest text wins).

## Deploy

Two rules. The new scraper refuses to run before the migration (guard: the run logs
`Unhandled exception … run scripts/migrate_bs_gerichte_ids.py before scraping`, exits 1,
and `scraper_health.json` shows bs_gerichte FAILED for that night, which is the
intended signal). And every shard rewrite (migration `apply`) must happen while no
process reads or appends the shard: after the bs_gerichte scrape has logged `Done.`
(~01:10 UTC) and after the 20:00 incremental build has exited (usually 01:20–01:30 UTC;
`pgrep -af build_fts5` empty), and before the 03:30 full rebuild starts.

### Phase A (identity) — night 1

1. Merge on the VPS (any time):

```bash
ssh -i ~/.ssh/caselaw root@46.225.212.40 'cd /opt/caselaw/repo && git fetch -q && git merge --ff-only origin/main && python3 -m py_compile scrapers/cantonal/bs_gerichte.py build_fts5.py mcp_server.py seo_pages.py db_schema.py && echo OK'
```

2. In the window, confirm the shard is idle (only a `run_scraper.py bs_gerichte`,
`build_fts5` or `publish.py` process matters; other scrapers may still run):

```bash
ssh -i ~/.ssh/caselaw root@46.225.212.40 'cd /opt/caselaw/repo && date -u; pgrep -af "run_scraper.py bs_gerichte|build_fts5|publish.py" || echo idle; grep -c "Done\." logs/bs_gerichte.log'
```

3. Dry-run, then run the migration:

```bash
ssh -i ~/.ssh/caselaw root@46.225.212.40 'cd /opt/caselaw/repo && python3 scripts/migrate_bs_gerichte_ids.py --dry-run'
```

Expected: `rows 10629`, `rekeyed: 10629` (8431 + 2198), no ERROR lines.

```bash
ssh -i ~/.ssh/caselaw root@46.225.212.40 'cd /opt/caselaw/repo && python3 scripts/migrate_bs_gerichte_ids.py && wc -l state/bs_gerichte.jsonl output/decisions/bs_gerichte.jsonl'
```

Expected: `state … +10629 ids` → state 21,258 lines; shard still 10,629 rows.

### Phase B (text) — start any time after the merge, apply in a later window

4. Start the fetch as a background job (≈ 6–7 h for 10,629 documents at 2 s; it only
appends to `output/decisions/bs_gerichte.refetch.jsonl`, never to the shard, and can
be stopped and restarted). Start with the SVG rows if the full walk should wait:

```bash
ssh -i ~/.ssh/caselaw root@46.225.212.40 'cd /opt/caselaw/repo && nohup python3 scripts/refetch_bs_gerichte_text.py fetch >> logs/bs_refetch.log 2>&1 & sleep 2; tail -2 logs/bs_refetch.log'
```

Progress: `grep -c . output/decisions/bs_gerichte.refetch.jsonl` and
`grep -E "fetched|failed" logs/bs_refetch.log | tail -2`. Expected at the end:
`fetched: 10629` (a handful of `failed` is acceptable; rerun `fetch` to retry them —
they are the only rows it will request).

5. In the next idle window (same rules as step 2), apply:

```bash
ssh -i ~/.ssh/caselaw root@46.225.212.40 'cd /opt/caselaw/repo && python3 scripts/refetch_bs_gerichte_text.py apply --dry-run'
```

Expected: `updated` ≈ 2,200 SVG + several hundred to a few thousand AG, `kept` for
rows the old extractor already got in full, `chars_gained` in the tens of millions.

```bash
ssh -i ~/.ssh/caselaw root@46.225.212.40 'cd /opt/caselaw/repo && python3 scripts/refetch_bs_gerichte_text.py apply && ls -la output/decisions/bs_gerichte.jsonl'
```

The 377 new rows the scraper fetches after phase A already use the fixed extractor and
need nothing.

## Verify

After the first 01:00 scrape following phase A:

```bash
ssh -i ~/.ssh/caselaw root@46.225.212.40 'cd /opt/caselaw/repo && grep -E "Done\.|search failed|Unhandled" logs/bs_gerichte.log | tail -3 && wc -l output/decisions/bs_gerichte.jsonl'
```

Expected: `+377 new, 11006/11005` (the shard has one row the portal no longer lists:
BEZ.2025.40 was re-issued as AG.2026.93 after our March copy of AG.2026.75 "nicht
rechtskräftig"; both versions are kept), no "search failed" lines, no gap.

After the 03:30 full rebuild that follows:

```bash
ssh -i ~/.ssh/caselaw root@46.225.212.40 'cd /opt/caselaw/repo && python3 -c "
import sqlite3
c=sqlite3.connect(\"file:output/decisions.db?mode=ro&immutable=1\",uri=True)
for r in c.execute(\"select court,count(*),sum(length(full_text)<2000) from decisions where court like \x27bs_%\x27 group by court\"): print(r)
print(\"aliases\", c.execute(\"select count(*) from decision_id_aliases\").fetchone())
print(\"old id ->\", c.execute(\"select decision_id from decision_id_aliases where previous_id=\x27bs_appellationsgericht_SB.2013.5\x27\").fetchone())
"'
```

Expected: `bs_appellationsgericht` 8,780–8,782, `bs_sozialversicherungsgericht` 2,220–2,221,
`bs_zivilgericht` 3, `bs_gerichte` 2 (the es twins win or lose the dedup by text length;
the sum over the four codes is 11,005 or 11,006), `aliases` 10,629, the old id of
SB.2013.5 mapping to `bs_appellationsgericht_AG.2020.102`, and after phase B the
under-2,000-character count for SVG down from 2,104 to a few dozen.

Swap gate: every BS court's row count grows or stays, so the per-court 80% gate does
not fire; no `OCL_SKIP_SWAP_GATE` override.

Then, via the served API: `get_decision("bs_appellationsgericht_SB.2013.5")` returns
AG.2020.102 with `resolved_via: previous_decision_id`; `get_decision("SB.2013.5")`
returns the newest of the two siblings; `https://opencaselaw.ch/entscheid/bs_appellationsgericht_SB.2013.5`
answers 301.

## Externally visible

10,629 BS ids change. HuggingFace parquet, research-CLI and API users holding old ids,
and indexed decision URLs are covered by the alias table and the 301; the dataset card
should mention the re-key in its next revision.

## Known follow-ups (not in this change)

- Structure sidecar (`decision_structure.db`) lags for the re-keyed and re-fetched rows
  until step 2g processes them; the pinpoint tools fall back to full text meanwhile.
- The date extractor takes the appealed decision's date for some rows (VD.2013.58 is
  stored as 2010-10-21 for a 2015 ruling); the listing's `Entscheiddatum` is now parsed
  for new rows, and `apply` fills only missing dates, never overwrites one.
- The 30 Aufsichtskommission über die Anwältinnen und Anwälte rows stay under
  `bs_appellationsgericht`, as the portal itself files them.
- `decision_ref` cannot mint candidates for the Zivilgericht case numbers ("K3."/"K5."
  carry a digit); exact id, decision number and case number still resolve.
