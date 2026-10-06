# MCP worker memory: recycle above 3.5 GB, cap at 8 GB (2026-10-05)

## Why

Python and SQLite keep memory they have freed, so heavy clients (deep
pagination, bulk agents) leave each `mcp-server@` worker at 4–13 GB instead of
about 2 GB. Eight of them squeeze the page cache; searches and the nightly
build then read from disk and the data volume sits at its IOPS ceiling.

- 2026-09-30: workers 9–13 GB, step 2 of the build hit its 12 h cap, smoke
  failed 66 times.
- 2026-10-04: workers 37.7 GB together, page cache 22 GB, searches 20–45 s or
  timing out, step 5e timed out. A rolling restart at 00:28 UTC brought the
  workers to 2.1 GB and searches back to a few seconds.

## What is installed

| File | Effect |
|---|---|
| `scripts/recycle_bloated_workers.py` | Every run: if the whole fleet is healthy, restart the largest workers above 3.5 GB (at most two), one at a time, each gated on `/health`. Exit 1 (alert) if one does not come back. |
| `systemd/opencaselaw-worker-recycle.{service,timer}` | Runs the script every 15 minutes; log in `logs/worker_recycle.log`; OnFailure → ntfy. |
| `systemd/mcp-server@.service.d/memory-cap.conf` | `MemoryMax=8G` per worker: the kernel kills a worker that outruns the timer, `Restart=always` brings it back in 5 s. No `MemoryHigh` (it throttles instead of freeing). |

Settings (environment of the service): `OCL_WORKER_RSS_MAX_MB` (3584),
`OCL_WORKER_RECYCLE_MAX` (2), `OCL_WORKER_HEALTH_WAIT` (30),
`OCL_WORKER_RECYCLE_DRY=1` (report only).

## Install (VPS, after `git merge --ff-only origin/main`)

Dry run first (changes nothing):

```bash
cd /opt/caselaw/repo && OCL_WORKER_RECYCLE_DRY=1 python3 scripts/recycle_bloated_workers.py
```

Then:

```bash
cd /opt/caselaw/repo
install -m 644 systemd/opencaselaw-worker-recycle.service systemd/opencaselaw-worker-recycle.timer /etc/systemd/system/
install -d /etc/systemd/system/mcp-server@.service.d
install -m 644 systemd/mcp-server@.service.d/memory-cap.conf /etc/systemd/system/mcp-server@.service.d/
systemctl daemon-reload
systemctl enable --now opencaselaw-worker-recycle.timer
```

The memory cap applies to running workers at `daemon-reload` (cgroup limits
are live); no restart is needed. Check:

```bash
systemctl show mcp-server@8770.service -p MemoryMax
systemctl list-timers opencaselaw-worker-recycle.timer
```

## Rollback

```bash
systemctl disable --now opencaselaw-worker-recycle.timer
rm /etc/systemd/system/mcp-server@.service.d/memory-cap.conf
systemctl daemon-reload
```

## Known limit

The script checks fleet health when it starts. If the post-swap recycle in
`publish.py` or `rolling_restart_workers.sh` runs at the same moment, two
workers can be down together for a few seconds. Both are rare and brief; a
shared lock is the fix if it ever matters.
