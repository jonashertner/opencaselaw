# Export harvesting: per-network budget + time-limited auto-ban (2026-10-23)

## Why

Every nginx limit on the export renderers (`/api/decisions/<id>/export*`) was
per address. On 2026-10-23 a fleet of 50 addresses in one cloud provider's
range pulled ~12,900 export renders per 15 minutes from 11:19 to 17:56 UTC.
Each address stayed inside its own budget, so nothing fired. The renders bloated
the MCP workers (back to ~23 GB within 15 minutes of a restart), the page cache
shrank, the data volume sat at its IOPS ceiling, step 5 of the nightly build
timed out and the QC gate timed out without a verdict. The range was denied by
hand at 21:37 UTC, after the fleet had stopped.

## What it does

| Piece | Effect |
|---|---|
| `deploy/nginx/ocl-harvest-guard.conf` → `/etc/nginx/conf.d/` | `ocl_export_net`: an export budget per network (IPv4 /24, IPv6 /48), 30 r/m, burst 60. `$ocl_harvest_ban`: a `geo` map over the generated ban list. |
| export location in `/etc/nginx/sites-available/mcp-server` | applies `ocl_export_net` and answers banned networks with 429 (the bulk-access notice). Nothing else: search, the JSON API, `/entscheid/` and MCP stay open to a banned network. |
| `scripts/autoban_harvesters.py` | every run: counts export renders and `/bulk-access.txt` fetches per network in the last 15 min of `tier1.log`; bans a network with ≥ 600 from ≥ 3 addresses, and the member /24s (≥ 50 each) of an IPv4 /16 with ≥ 2,000 from ≥ 10 addresses; ban lasts 24 h after the last offence; writes the list, `nginx -t`, reload (puts the old list back if the test fails); one ntfy per run that adds a ban. |
| `systemd/opencaselaw-autoban.{service,timer}` | every 5 minutes; output in the journal (it names client networks, so not in `logs/`); OnFailure → ntfy. |

Host-local, never committed (they hold client networks):
`/etc/nginx/snippets/ocl-autoban.geo` (generated), `/var/lib/opencaselaw/autoban.json`
(state), `/etc/opencaselaw/autoban-allow.txt` (networks never banned, one CIDR per
line, `#` comments), `/etc/opencaselaw/autoban.env` (optional settings, e.g.
`OCL_AUTOBAN_DRY=1`). Ban entries expire with their 24 h, inside tier1.log's
72 h retention. Thresholds and other settings: see the script's docstring.

## Calibration (tier1.log 2026-10-20 to 23)

Export + notice requests per /24 per 15 min: median 1, 99th percentile 6,
99.9th 24. Replayed over the four days, the defaults ban exactly two things:
the 10-23 fleet at 11:25 (six minutes after it started; 8 /24s, two of them
through the /16 rule), and one large crawler's range that fetches exports
despite robots.txt. Five single addresses at 600–1,175 per window (one a Swiss
line) are left alone: the per-address budget already holds them to 10 a minute.
If a partner is ever caught, add its range to the allowlist and delete its
line from the state file (next run lifts it).

## Install (VPS, after `git merge --ff-only origin/main`)

1. Files nginx needs before it sees the new config (the `geo` include fails on
   a missing file):

```bash
install -d /etc/opencaselaw /var/lib/opencaselaw
touch /etc/nginx/snippets/ocl-autoban.geo /etc/opencaselaw/autoban-allow.txt
install -m 644 /opt/caselaw/repo/deploy/nginx/ocl-harvest-guard.conf /etc/nginx/conf.d/
```

2. The export location of the live site (it differs from the repo snapshot, so
   patch it rather than copying the file): two directives after the per-address
   budget, with a backup and a restore if the test fails:

```bash
f=/etc/nginx/sites-available/mcp-server; cp $f $f.bak-autoban && awk '{print} /^        limit_req zone=ocl_export burst=30 nodelay;$/{print "        # Per-network budget and auto-ban (conf.d/ocl-harvest-guard.conf,"; print "        # scripts/autoban_harvesters.py): a fleet spread over many addresses"; print "        # in one range shares one budget instead of getting one each."; print "        limit_req zone=ocl_export_net burst=60 nodelay;"; print "        if ($ocl_harvest_ban) { return 429; }"}' $f.bak-autoban > $f && (nginx -t && systemctl reload nginx && echo RELOADED || (cp $f.bak-autoban $f; rm /etc/nginx/conf.d/ocl-harvest-guard.conf; echo RESTORED))
```

3. A dry run against the live log (changes nothing), then the timer:

```bash
cd /opt/caselaw/repo && OCL_AUTOBAN_DRY=1 python3 scripts/autoban_harvesters.py
install -m 644 systemd/opencaselaw-autoban.service systemd/opencaselaw-autoban.timer /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now opencaselaw-autoban.timer
```

Check: `journalctl -u opencaselaw-autoban -n 20`, `cat /etc/nginx/snippets/ocl-autoban.geo`.

## Lift a ban by hand

Delete the network's entry from `/var/lib/opencaselaw/autoban.json` and add it
to the allowlist if it should never come back; the next run rewrites the list
and reloads nginx. To stop everything: `systemctl disable --now
opencaselaw-autoban.timer`, then `: > /etc/nginx/snippets/ocl-autoban.geo &&
nginx -t && systemctl reload nginx`.

## Rollback

```bash
systemctl disable --now opencaselaw-autoban.timer
f=/etc/nginx/sites-available/mcp-server; cp $f.bak-autoban $f
rm /etc/nginx/conf.d/ocl-harvest-guard.conf
nginx -t && systemctl reload nginx
```

## Not covered

- The JSON API (`/api/decisions/<id>`) is not auto-banned: legitimate
  integrators reach 6,000–7,000 requests per /24 per 15 minutes there, so
  volume alone cannot tell them from a harvester. A fleet that moves there
  meets only the per-address limits.
- Renders are not cached: a repeated export costs a worker the same as the
  first. Caching rendered files, or capping concurrent renders, is the next
  layer if a fleet gets through below the thresholds.
