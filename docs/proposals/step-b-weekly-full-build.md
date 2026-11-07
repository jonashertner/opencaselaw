# Step B: the full build on Sundays only, the incremental build on weekdays

Draft, 2026-10-08. Nothing is deployed. Every step below needs the owner's OK (pipeline gate).

## Why now

The daily full build no longer fits in a day.

| Run | Start (UTC) | End (UTC) | Length |
|---|---|---|---|
| 2026-08-23 | 03:30 | 18:03 | 14 h 33 |
| 2026-10-06 | 03:30 | 23:41 | 20 h 11 |
| 2026-10-07 | 06:20 (checkpoint bug) | 02:29 the next day | 20 h 09 |
| 2026-10-08 | 03:30 | ~02:15 the next day (estimate) | ~22 h 45 (incl. a one-off 3 h 40 structure re-extraction) |

What this costs every day:

- **The quality-control gate times out.** It hit its 3,600 s cap on 10-06 and 10-08, because it scans
  `decisions.db` during the workday while the same build is also running. The gate's
  verdict is lost, and distribution goes ahead with the corpus unverified. On the Stage A nights (09-28 and 10-01..10-03) the
  same gate passed at night in 31-50 min.
- **The build window covers the whole day.** CLAUDE.md invariant #9 forbids corpus-wide scans
  for its whole length. It also blocks the BGer poller's `quick_publish` (skipped on lock all
  day on 10-07 and 10-08) and leaves no maintenance window. The planned "after the publish, ~18:30"
  window no longer exists.
- **The incremental build gets squeezed out.** It is queued behind the full build (`After=`). Since 10-05 every night has started after
  the 21:30 cut-off, so it ran only the graph and the stats. The structure and distribution steps were skipped.
- **Builds can collide.** A run that passes 03:30 swallows the next day's timer, because the timer fires into an active unit.

Step 2 alone (the search-database rebuild) grew from 5 h 36 to 9 h 21 in six weeks: ingest 2 h 38, dedup 1 h 30,
search-index optimize 1 h 14, normalise 56 min, content hashes 50 min, stub removal 44 min, chamber
corrections 37 min. Nothing in it is incremental. The corpus gains about 120-400 rows a day, yet a million are
rebuilt.

## What changes

| | Today | Step B |
|---|---|---|
| Full build (`opencaselaw-publish.timer`) | daily 03:30 | **Sun 03:30** |
| Incremental (`opencaselaw-publish-incremental.timer`) | Mon-Sat 20:00, queued behind the full | **Mon-Sat 03:30**, held by the 01:00 scrape (`After=`) |
| Incremental ingests the night's scrape | no (`--skip-quick-publish`) | **yes** (`quick_publish`) |
| Incremental's structure and distribution steps | skipped when it starts after 21:30 | run every weekday night (starts ~03:30) |

The weekday night then runs: quick_publish (~18 min, mostly the 73 GB copy) → graph in place (~50 min)
→ structure `publish.py --step 2g` (~55 min) → stats (~45 min) → feeds, gate (~35 min),
manifest, Hugging Face delta, git push, health check. That is about 3.5 h, done by about 07:00-07:30 UTC, before
the Swiss workday. On weekdays the build window shrinks from about 20 h to about 3.5 h, and the gate runs at night.

### Unit changes (drop-ins, so rollback is `rm` + `daemon-reload`)

```ini
# /etc/systemd/system/opencaselaw-publish.timer.d/step-b.conf
[Timer]
OnCalendar=
OnCalendar=Sun *-*-* 03:30:00 UTC
```

```ini
# /etc/systemd/system/opencaselaw-publish-incremental.timer.d/step-b.conf
[Timer]
OnCalendar=
OnCalendar=Mon..Sat *-*-* 03:30:00 UTC
```

```ini
# /etc/systemd/system/opencaselaw-publish-incremental.service.d/stage-a.conf  (replaces the live one)
[Service]
ExecStart=
ExecStart=/usr/bin/python3 /opt/caselaw/repo/scripts/incremental_nightly.py --in-place-graph --structure-from-shards --with-distribution --latest-start-utc 21:30
```

These stay as they are: `no-overlap.conf` (a Monday incremental waits for an overrunning Sunday full build), the
late-start guard (a Saturday run that starts after 21:30 skips the long steps before Sunday's full build),
`oom-policy.conf`, the 10 h timeout and the resource fence.

## Code changes needed first

1. **Recycle the workers after a `quick_publish` swap.** This is required.
   Pooled worker connections pin the old `decisions.db` inode (73 GB) until the process restarts.
   The full build already does a rolling restart after its swap for exactly this reason
   (`publish._recycle_mcp_workers`, after the 2026-07-08 "database or disk is full" incident: 130 GB
   pinned). `quick_publish` has no such step.
   - Under Step B it swaps every weekday night, and so can the BGer poller, up to about 3 times a day, now that no
     build holds the lock.
   - Disk on the data volume: about 126 GB free at rest. One pinned copy leaves 53 GB. The structure rebuild needs 1.2 x 52.8 =
     63 GB, so 2g would refuse on the same night.
   - **Implemented on this branch:** after a successful swap, `quick_publish` runs
     `scripts/rolling_restart_workers.sh`, the same health-gated rolling restart (one worker at a time, about 30 s,
     zero downtime). It is non-fatal: a failed restart leaves the swap in place. `OCL_QUICK_PUBLISH_RECYCLE=0`
     turns it off. Tests (`tests/test_quick_publish_recycle.py`): a swap restarts once; no new rows, a dry run
     or the switch restart never; a failed restart keeps the swap. The first test fails without the call.
   - Takes effect as soon as the server pulls it, also for today's BGer poller, whose rare
     post-build swaps then stop pinning a copy as well.
   - Verify on the first Step B night that the incremental unit's sandbox (`ProtectSystem=strict`,
     `NoNewPrivileges=yes`; the publish and poller units are not sandboxed) lets `systemctl restart` through.
     It should: the unit runs as root, and its `no-overlap.conf` ExecCondition already reaches systemd
     through `systemctl show` inside the same sandbox. Green means the log line `Rolling restart of the
     workers …` followed by eight `healthy` lines. If it fails, the swap still stands, and the fallback is the
     memory-recycle timer or a `+` prefix on that one command.
2. **The BGer poller's daytime `quick_publish`: owner's choice.** Today it is effectively off, because the
   full build holds the lock all day. Under Step B it would copy 73 GB during working hours each time BGer publishes (~3
   batches a day). `get_decision` already serves new BGer rulings from `recent_overlay.db` within the poll interval;
   only search needs the swap.
   - **(a) Keep it.** New BGer rulings become searchable within hours, at the cost of about 3 x 18 min of 73 GB copies by day.
   - **(b) Turn it off on weekdays** (a poller env flag). They become searchable the next morning. That is today's behaviour, and I recommend it for the first week.
3. **Optional, cheap weekday additions:**
   - `--step 2e` (Anwaltsrecht tags, ~1 min) and `--step 2f` (`materialien.db`, <1 min). Without them, Botschaften
     ingested daily at 04:34 are served only weekly.
   - `2d` (quality enrichment, ~34 min) and `2b` (quality report, ~38 min) can stay weekly.

No change to `publish.py`, the schemas, `base_scraper.py` or `state/`. The step-2c idea (an incremental graph inside
the full build, saving ~2.5 h) is no longer worth it once the full build runs only on Sundays.

## Trade-offs the owner accepts

- **Corrections to existing rows reach users only on Sunday.** That covers shard repairs like tonight's,
  re-scrapes that change text or a date, migrations, and removals: up to about 8 days instead of the next
  afternoon. `quick_publish` is INSERT OR IGNORE: it adds new ids and never updates or deletes.
  The governance policy already says removal "may require a rebuild cycle". An urgent case can still start
  `systemctl start opencaselaw-publish.service` by hand (about 20 h), or be withheld at serve time, as `source_defects` does.
- **New rows skip the whole-corpus passes until Sunday:** cross-court dedup, docket and date
  normalisation, chamber corrections, docket aliases, canonical date correction. Inline at insert:
  the content hash, stub drop and date recovery. This affects about 100-400 rows a day for up to 6 days, the same state the
  hourly BGer path already produces.
- **Weekly instead of daily:** search-index optimize, the full Parquet export and Hugging Face upload (the nightly delta continues),
  materialien and scholarship rebuilds (unless point 3 is taken), the Anwaltsrecht tags, the
  wayback queue, the interesting-stats block and the integrity root (already promised weekly).
- **Known gap, unchanged:** `publish.py --step 5c` exits 0 on a gate timeout, so a timed-out gate does not
  block the weekday push. At night it hasn't timed out yet; worth fixing separately.

## Rollout

1. Owner approves points 1-3. Implement 1 (and 2b and 3 if chosen) with offline tests, `make test`, PR, merge.
   The server pulls at the end of a publish run.
2. Flip on a weekday evening after that day's full build has exited and before 03:30: install the three
   drop-ins, `systemctl daemon-reload`, then check `systemctl list-timers` for the full timer (next Sun 03:30) and
   the incremental timer (next 03:30).
3. Morning check (read-only), as in the Step A runbook. Expect `ok True exit 0`, no late start,
   quick_publish `Inserted N/N`, no `decisions.db (deleted)` handles left in the worker processes, structure and
   graph with night timestamps, the gate well under 3,600 s, and a bot commit on `stats.json`. Also `df` on
   the data volume.
4. The first Sunday: the full build runs as today. Monday 03:30: the incremental runs after it.

**Rollback:** remove the two timer drop-ins and restore the old ExecStart (`--skip-quick-publish …`), then `daemon-reload`.
The next 03:30 is a full build again. Every artefact is atomically swapped or regenerated by the next full build.
