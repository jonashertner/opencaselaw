# Runbook — db_generation mismatch or stuck workers

## Symptoms

- `/health` on one MCP worker reports a `db_generation` older than
  expected, while another worker reports the newer one.
- `quick_publish.log` shows a successful insert + swap, but
  searches for the newly-inserted decision return zero results on
  some workers.
- Dashboard `freshness_seconds` ticks up monotonically (no new
  data being recognized).

## Quick diagnosis

```bash
# Per-worker generation
for p in {8770..8777}; do
  printf "  %s " $p
  curl -s http://127.0.0.1:$p/health | python3 -c \
    'import sys,json; d=json.load(sys.stdin); print(d.get("db_generation"), d.get("decisions"))'
done

# What the on-disk file actually has
python3 -c "
import sqlite3
c = sqlite3.connect('file:/opt/caselaw/repo/output/decisions.db?immutable=1', uri=True)
print('on-disk user_version:', c.execute('PRAGMA user_version').fetchone()[0])
print('row count:', c.execute('SELECT count(*) FROM decisions').fetchone()[0])
c.close()
"
```

## Interpretation

| /health generation | On-disk generation | Diagnosis |
|---|---|---|
| All workers same, == on-disk | n/a | No mismatch. Look elsewhere. |
| Workers differ from each other | n/a | One worker stuck on stale fd. Restart the lagger. |
| All workers same, < on-disk | newer | Workers haven't served a request since swap. Trigger any tool call (e.g. `get_statistics` via curl). The next `get_db()` will pick up new generation. |
| Workers report 0 | 0 | Writer didn't bump. Check `build_fts5.log` / `quick_publish.log` for the PRAGMA call. |
| On-disk == 0 after a known swap | 0 | Writer bug. Check that the PRAGMA was issued *before* `conn.close()`. |

## Resolution

### Workers differ from each other
```bash
# Restart the lagging worker (other 3 keep serving)
systemctl restart mcp-server@<port>
sleep 3
curl -s http://127.0.0.1:<port>/health
```

### Writer didn't bump
1. Confirm via `grep -n "user_version" build_fts5.py scripts/quick_publish.py` that the patch is present.
2. If patch is present but value is 0: the connection was closed before the PRAGMA committed. File a bug; do **not** auto-fix in production.
3. If patch is missing: deploy the missing patch on a Saturday per the schema-change rule (`docs/decision_rules.md`).

### MCP workers serving stale data after a known good swap
1. Fire a no-op tool call to each worker (the generation check happens in `get_db()`):
   ```bash
   for p in {8770..8777}; do
     curl -s http://127.0.0.1:$p/health > /dev/null
   done
   ```
2. Re-check `/health` — generation should now match on-disk.
3. If it does NOT update: restart the worker. This is a bug in
   the `_last_seen_db_generation` tracking; file an issue with the
   worker's logs.

## What this runbook does **not** authorize

- **Do not** modify `user_version` manually on the live DB. The
  number is meaningful only when set by a writer at swap time;
  manual changes break the cache-invalidation invariant.
- **Do not** clear `_query_cache` by restarting workers on a
  cadence "just in case". The generation check is the contract;
  if it's broken, fix the contract, don't paper over it.

## Escalation

If on-disk `user_version` is changing but workers consistently
fail to pick it up after restart, the bug is in `get_db()` or the
cache-invalidation logic. Open an issue with: per-worker /health
snapshots over time, on-disk generation, and the relevant section
of `mcp_server.py` post-deploy.
