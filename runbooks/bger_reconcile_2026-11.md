# Runbook: BGer completeness sweep, judgment years 2000 to mid-2024 (2026-11)

## Why

Until 5f10adfe (2026-06-08), `_follow_pagination` fetched pages 2+ of a 4-day AZA window's *first day*, not of the whole window. Every AZA backfill before that lost most results on a window's later days. The fixed code re-swept only 730 days: the one-off run of 2026-06-09 began at 2024-06-10, and the weekly Saturday backfill has covered every later judgment date since.

For judgment years 2000 to mid-2024, the corpus rests on that buggy backfill plus the frozen entscheidsuche intake. es rows are 35–55% of BGer rows per year (2018: 2,592 of 7,768). Rulings both sources missed are missing. On 2026-11-08 the entscheidsuche index named three that bger.ch serves: **2C_1023/2018, 9C_806/2018, 1B_232/2010**. Two more need checking: 6B_371/2015 (bger.ch now answers "Error retrieving URL") and 8C_7/2008 (their date is a Sunday). Details: `docs/claude/entscheidsuche-parity-addendum-2026-11-08.md`.

## What the code does

`run_scraper.py bger --reconcile --since Y-01-01 --until Y-12-31`:

- reads `state/bger.held.tsv`, every held ruling from **all** bger shards (`bger.jsonl`, `bger_evg.jsonl`, `es_bger.jsonl`). Scraper state knows only ~100k of ~194k held rulings, so it can't serve as the reference. The run refuses to start without the index;
- per 4-day window, compares the AZA hit count with held rulings for those dates. If we hold at least as many, page 1 is the only request. Short windows are paged through (split into days above 100 hits, where complete days are skipped);
- yields only rulings whose (docket, date) no shard holds. **A docket held under another date is never fetched**: that is a date defect on our side or a same-docket second ruling, and fetching it could serve one ruling twice. It goes to the report instead;
- writes every short window, date conflict and run summary to `state/bger.reconcile.jsonl`, and logs to `logs/bger_reconcile.log`.

## Expected load

The weekly backfill sweeps 183 windows in ~58 min, about 19 s per window (PoW + Incapsula). The sweep covers 2,231 windows (2000-01-01 → 2024-06-30). A dry estimate on 11-08 data used entscheidsuche's per-day counts as a stand-in for the AZA. It put **~13% of windows short** (283), mostly date disagreements that become date-conflict report lines, not fetches. That's about 4,500 requests in total, against ~25,000 for a plain re-sweep. Per judgment year: ~90 windows, **~30–60 min**.

## Steps (Jonas; the VPS, outside the build window)

### 0. Deploy

Merge the branch to main and push. Then `git fetch && git merge --ff-only origin/main` on the VPS. Copy the unit (unit installs are a hand-over; see memory `vps-ops-permission-boundary`):

```bash
install -m 644 /opt/caselaw/repo/systemd/opencaselaw-bger-reconcile@.service /etc/systemd/system/ && systemctl daemon-reload
```

### 1. Build the held index (once, then before each evening's runs)

Only when the publish pipeline is not running (invariant 9: it reads ~3 GB of shards):

```bash
systemctl is-active opencaselaw-publish.service   # must print: inactive
```

```bash
cd /opt/caselaw/repo && nice -n 19 ionice -c 3 python3 scripts/build_bger_held_index.py
```

**Stop** unless the printed `rulings` is ≥ 190,000 and `by_year` shows 5,000+ for each year 2000–2023. Rebuild it before each evening's batch, so rulings the nightly scrape added since count as held.

### 2. Pre-checks before each run

```bash
systemctl is-active opencaselaw-publish.service bger-poller.service opencaselaw-bger-backfill.service; pgrep -af "run_scraper.py bger" || echo "no bger writer"
```

All three must print `inactive`, followed by `no bger writer`. Don't start after 23:30 UTC: a run must end before the 01:00 nightly scrape. Don't start on Saturday between 19:00 and 22:00 (weekly backfill at 20:00).

### 3. First two runs: the years with known misses (validation)

```bash
systemctl start opencaselaw-bger-reconcile@2018.service
```

**Check** (`tail -50 logs/bger_reconcile.log`, `tail -3 state/bger.reconcile.jsonl`):
- the summary line shows `windows` ≈ 92, with `complete` the large majority;
- **2C_1023/2018 and 9C_806/2018 are fetched**: `grep -c -e '2C_1023/2018' -e '9C_806/2018' output/decisions/bger.jsonl` prints 2;
- `Errors: 0` in the `Done.` line, and no `stopped_refused` event.

Then:

```bash
systemctl start opencaselaw-bger-reconcile@2010.service
```

**Check:** 1B_232/2010 fetched, same checks as above.

**Stop and report** if a run fetches more than ~300 rulings for one year. That would mean the held index is missing a shard or the docket normalisation disagrees with the AZA listing; a real gap is expected in the tens.

### 4. The remaining years, 2–3 per evening

Order: 2023 → 2000, then 2024 (ends 2024-06-30). Use one `systemctl start` per year, in sequence, never in parallel. For example:

```bash
for y in 2023 2022 2021; do systemctl start opencaselaw-bger-reconcile@$y.service || break; done
```

Run the pre-checks (step 2) before each evening's batch. If a run logs `stopped_refused` (bger.ch refused 3 windows in a row), stop for the evening and repeat that year next time. The run is idempotent.

### 5. After the sweep

- **Fetched rulings** reach production with the next nightly publish (no extra step).
- **Date conflicts:** `grep '"date_conflict"' state/bger.reconcile.jsonl`. Each line names a docket the AZA lists under another date than ours. Triage separately: they feed the dates work (memory `source-dates-repair-2026-11`) and same-docket second rulings (`_stub_filter`). Nothing is changed automatically.
- **6B_371/2015 and 8C_7/2008:** if the sweep fetched neither, check them with the withdrawn check (`scripts/check_bger_withdrawn.py`) rather than by hand from the scraping address.
- Remove the unit when done: `rm /etc/systemd/system/opencaselaw-bger-reconcile@.service && systemctl daemon-reload`.

## Rollback

The sweep only appends new rulings to `bger.jsonl`, through the normal `run_scraper` path, deduplicated by `decision_id`. To drop them, filter the appended lines by `scraped_at` of the sweep window, as in the other shard repairs (hard-link backup, atomic swap). The held index and the report are scratch state and can be deleted.

## Files

`scrapers/bger.py` ("Count reconciliation"), `run_scraper.py` (`--reconcile`), `scripts/build_bger_held_index.py`, `systemd/opencaselaw-bger-reconcile@.service`, `tests/test_bger_reconcile.py` (12 tests).
