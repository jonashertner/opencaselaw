# Incremental nightly runbook (Workstream A v0.1)

## Goal

Replace the 7h+ full-rebuild nightly with a ~10-25 min incremental run
for weekdays, keeping a weekly Sunday full rebuild as safety net.

**Projected impact**: nightly 7h 22min → ~25 min on Mon-Sat (one Sunday
full = 7h). Net: ~51h/week → ~6.5h/week of pipeline time. New decisions
searchable within ~15 min of scrape rather than next morning.

## What this directory adds

| File | Role |
|---|---|
| `scripts/incremental_nightly.py` | Orchestrator. Chains `quick_publish` → `build_reference_graph_incremental` → `extract_decision_structure_incremental` → `generate_stats`. Writes a JSONL summary per run. |
| `systemd/opencaselaw-publish-incremental.service` | systemd unit invoking the orchestrator. **Default mode is `--shadow`** — incremental builders write to sibling .db files and live data is untouched. |
| `systemd/opencaselaw-publish-incremental.timer` | Mon-Sat 03:30 UTC. Sunday remains on legacy `opencaselaw-publish.timer` for the weekly full rebuild. |
| `logs/incremental_nightly.jsonl` | Auto-appended summary of every run (success or failure). Feeds drift validation. |

## Prerequisites that are already met (verified 2026-05-18)

- ✅ D3 cache-invalidation contract (PRAGMA user_version) production-proven
  this morning at 10:52 UTC (Monday gate, commit `1ea5645`).
- ✅ `quick_publish.py` deployed, live, bumps `user_version` on swap.
- ✅ `build_reference_graph_incremental.py` deployed (May 8).
- ✅ `extract_decision_structure_incremental.py` deployed (May 7).

## Shadow → in-place cutover (1-week validation)

### Phase 1 — Enable in shadow mode (low risk)

```bash
ssh -i ~/.ssh/caselaw root@46.225.212.40
cd /opt/caselaw/repo && git pull
sudo cp systemd/opencaselaw-publish-incremental.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now opencaselaw-publish-incremental.timer
systemctl list-timers opencaselaw-publish-incremental --no-pager
```

Mon-Sat **12:00 UTC** the orchestrator fires — well after the legacy
03:30 UTC full rebuild has typically completed. The Phase 1 service
also runs with `--skip-quick-publish` so it doesn't race the legacy
publish's lock on `decisions.db` (quick_publish itself keeps firing
hourly via `bger-poller`). Only the graph + structure incremental
builders run, writing to sibling .db files for drift validation.

**Live data is served from the full rebuild's output as today.**

### Phase 2 — Drift validation (during the shadow week)

After each shadow run, the orchestrator writes a summary to
`logs/incremental_nightly.jsonl`. Compare against expected duration
and exit code. After Sunday's full rebuild, compare the freshly-built
live DBs against the prior Saturday-night incremental sibling DBs:

```bash
# Quick check
tail -7 /opt/caselaw/repo/logs/incremental_nightly.jsonl | jq .

# Row count drift (sibling vs live)
python3 -c "
import sqlite3
live = sqlite3.connect('file:/opt/caselaw/repo/output/reference_graph.db?immutable=1', uri=True)
sib  = sqlite3.connect('file:/opt/caselaw/repo/output/reference_graph_incremental.db?immutable=1', uri=True)
print('live edges:', live.execute('SELECT count(*) FROM citation_targets').fetchone()[0])
print('sib  edges:', sib.execute('SELECT count(*) FROM citation_targets').fetchone()[0])
"
```

Acceptance: row count delta < 0.5 %, top-30 cited decisions identical
in both DBs, no extractor-version mismatch warnings in the logs.

### Phase 3 — Cutover

After 7 consecutive green nights:

```bash
# Edit the service file to add --in-place
sudo systemctl edit --full opencaselaw-publish-incremental.service
# Change ExecStart line to:
#   ExecStart=/usr/bin/python3 /opt/caselaw/repo/scripts/incremental_nightly.py --in-place
sudo systemctl daemon-reload

# Move the legacy publish to Sunday-only
sudo systemctl edit opencaselaw-publish.timer
# Change OnCalendar to: Sun *-*-* 03:30:00 UTC
sudo systemctl daemon-reload
sudo systemctl restart opencaselaw-publish.timer
```

After cutover: Mon-Sat incremental updates live DBs in ~25 min;
Sunday full rebuild remains the safety-net (rebuilds wayback_queue,
FTS5 optimize, full parquet export).

## Rollback

```bash
sudo systemctl disable --now opencaselaw-publish-incremental.timer
sudo systemctl restart opencaselaw-publish.timer
```

The legacy full-rebuild path is untouched throughout Phase 1 and
Phase 2 — rollback is just disabling the new timer. Phase 3 cutover
requires reverting both edits + a successful manual full rebuild.

## Open follow-ups

- `scripts/publish_drift_check.py` — automate the row-count + top-30
  diff so it runs daily and writes pass/fail to alerts.
- Bypass `wayback_queue` provisioning in the Sunday full rebuild
  (1h 21min saved) — needs a separate enqueue path so the archiver
  picks up new rows. Currently the only enqueue path is publish.py.
- Skip FTS5 optimize on incremental days — already implicit since
  incremental never runs build_fts5. Worth confirming the FTS5 index
  doesn't degrade significantly between weekly optimizes.
- Enable `OCL_USE_DAG=1` parallel scheduling for the Sunday full
  rebuild (commits `d779404`+`c6572ee` shipped phase B v0.2/v0.3) —
  could cut 2-3h from the Sunday slot by parallelising Steps 2c+2d+2e.

## Step A (2026-09) — the incremental takes over structure + distribution, full build stays

Decided 2026-09-04. The decision_structure drift residual (3,752 decisions) is a property of
the live builder's inputs, not of the incremental path, so the pair can never go green; the
way out is to rebuild structure from the shards on weeknights exactly as the full build does
(`--structure-from-shards`, commit adae408c) and to run the cheap distribution steps every
night (`--with-distribution`). The daily 03:30 full build remains the safety net until Step B.

### Prerequisites (all shipped, unit install is manual)

- `systemd/opencaselaw-publish-incremental.service`: `ReadWritePaths=/opt/caselaw/repo`
  (the tail commits to `.git`, writes `releases/`, appends `quality/history.db`),
  `EnvironmentFile=-/opt/caselaw/repo/.env.publish` (HF token + delta flag),
  `OnFailure=ntfy-alert@%n.service`, `TimeoutStartSec=10h`, the same CPU/IO/memory fence as
  the full build. ExecStart is unchanged until the flip. Install: copy to
  `/etc/systemd/system/`, delete the now-redundant `override.conf` drop-in, `daemon-reload`.
- `publish.py`: a `--step N` run no longer clears the resume checkpoint, refreshes
  `state/last_publish_success.json` or pushes "Publish OK"; run records carry
  `manual_step`; a run that cannot take the publish flock exits 2 (pages via OnFailure).
- `scripts/incremental_nightly.py`: `--latest-start-utc` (default 22:30) skips the ~2h
  structure rebuild and the distribution tail on a night that started too late to finish
  before 03:30; a `--step 2g` that exits 0 but leaves decision_structure.db untouched is
  treated as failed; the step-7 child never gets the SQLite snapshot flag; exit 2 = QC gate
  withheld publication, exit 3 = a distribution step lagged.

### The flip (one line, one night)

Pick a night whose full build ended before ~19:30 UTC (check `state/publish_runs.jsonl`),
not a Saturday (bger-backfill 20:00 + the practice timer 21:30 share the evening). Install a
drop-in rather than editing the file, so rollback is `rm` + `daemon-reload`:

```bash
ssh -i ~/.ssh/caselaw root@46.225.212.40 'mkdir -p /etc/systemd/system/opencaselaw-publish-incremental.service.d && printf "[Service]\nExecStart=\nExecStart=/usr/bin/python3 /opt/caselaw/repo/scripts/incremental_nightly.py --in-place-graph --structure-from-shards --with-distribution\n" > /etc/systemd/system/opencaselaw-publish-incremental.service.d/stage-a.conf && systemctl daemon-reload && systemctl cat opencaselaw-publish-incremental.service | grep -n "^ExecStart"'
```

### Morning after (all read-only)

```bash
ssh -i ~/.ssh/caselaw root@46.225.212.40 'cd /opt/caselaw/repo && tail -1 logs/incremental_nightly.jsonl | python3 -c "import sys,json; d=json.load(sys.stdin); print(d[\"mode\"], \"ok\", d[\"ok\"], \"exit\", d.get(\"exit_code\"), \"late\", d.get(\"late_start\"), \"dist_ok\", d.get(\"distribution_ok\"), d.get(\"distribution_failed_steps\")); [print(\"  \", s[\"step\"], s[\"exit_code\"], round(s[\"duration_s\"]), \"s\") for s in d[\"steps\"]]"; systemctl is-failed opencaselaw-publish-incremental.service; grep -c "Another publish process is already running" logs/publish.log; git log -1 --format="%h %ci %s" -- docs/stats.json; ls -la --time-style=+%H:%M output/decision_structure.db output/reference_graph.db; grep -E "\"step\": \"(2g|5c)\"" state/publish_runs.jsonl | tail -2'
```

Green means: `ok True exit 0`, no late start, every step exit 0, the unit not failed, no
"already running" line since the flip, a bot commit on stats.json stamped in the night,
decision_structure.db and reference_graph.db with night timestamps, 2g and 5c "ok" with the
gate well under 3,600 s. Two green nights, then Step B: `opencaselaw-publish.timer` to
`Sun *-*-* 03:30:00 UTC` and, optionally, the incremental timer to weekdays 03:30 so the
night's scrape is live by morning.

### Step 2g from served text (2026-09-08 fix, d804e901)

The first production run (2026-09-07) died: `--force-full` on every full build materialised
1.07M decisions in memory, hit the 32 GiB MemoryHigh, printed nothing and was killed by the
stall watchdog; the shard fallback then hid it ("Step 2g: OK"). Since d804e901:

- the extractor streams (progress line every 10,000 rows), commits every 2,000 writes and
  **resumes** a bootstrap that a wall clock killed (the working copy keeps
  `meta.bootstrap_in_progress`); a bootstrap is ~3 h, a normal night ~30-45 min (55 GB base copy
  + one text scan), only new/changed decisions are re-extracted;
- publish.py no longer passes `--force-full`; the extractor bootstraps by itself without state
  or on a version change. One-off full re-extraction: `OCL_STRUCTURE_FORCE_FULL=1` in the
  environment of ONE run (never in `.env.publish` — it would bootstrap every night);
- tmp and swap use the resolved path on the data volume; a free-space check (1.2 x sidecar) in
  publish.py and in the extractor refuses the root disk;
- on an extractor failure a readable current sidecar is KEPT (return False, 2g is non-fatal);
  the shard build only runs when no sidecar is served. Watch `publish_runs.jsonl` for 2g:
  a run of `status: failed` nights means the bootstrap is not converging (check the
  `[extract_structure] scanned …` lines in the publish log for progress per night).

Morning check for the sidecar: `ls -la --time-style=+%H:%M output/decision_structure.db` (night
timestamp) and `python3 -c "import sqlite3;c=sqlite3.connect('file:/mnt/HC_Volume_104655575/output/decision_structure.db?mode=ro&immutable=1',uri=True);print(c.execute('select key,value from meta').fetchall())"`
(extractor_version present, no bootstrap_in_progress).

### Rollback

`rm /etc/systemd/system/opencaselaw-publish-incremental.service.d/stage-a.conf && systemctl daemon-reload`.
Nothing else: every night's output is either atomically swapped or regenerated by the next
full build.
