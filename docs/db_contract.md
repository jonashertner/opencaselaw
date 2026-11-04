# DB Generation Contract

How writers of `decisions.db` (the live FTS5 corpus) communicate
swap events to MCP workers without coordination, so workers see
new rows on their next request and stale aggregation caches are
invalidated.

## Mechanism: `PRAGMA user_version`

SQLite stores a 32-bit signed integer per database file accessible
via `PRAGMA user_version`. We use it as `db_generation`: a monotone
ID that changes every time a writer commits a new view of the
corpus.

**Range:** signed 32-bit (`-2^31` to `2^31 - 1` ≈ 2.14 billion).
Unix epoch seconds work as the value until **2038-01-19**. After
2038 we will need to switch to (epoch - 2_000_000_000) or a
similar offset; see `runbooks/db_generation_year_2038.md` (to be
written before 2037). For now: `int(time.time())` is the canonical
value.

## When writers bump

Both writers set `user_version` **after the final durable write
and before the atomic swap**:

- `build_fts5.py`: after `wal_checkpoint(TRUNCATE)` and
  `journal_mode=DELETE`, before `conn.close()` and `os.replace`.
- `scripts/quick_publish.py`: after `conn.commit()`, before
  `conn.close()` and `os.replace`.

Setting it before close ensures the value is persisted to the file
that gets renamed. Setting it after journal-mode switch ensures
it's written in DELETE mode directly to the main file, no WAL
sidecar to lose.

## How readers (MCP workers) consume

MCP workers do **not** cache the SQLite connection across requests.
`mcp_server.get_db()` opens a fresh connection per call with
`?immutable=1` (see `mcp_server.py:1418`). When the path's inode
changes (atomic swap), the next `get_db()` opens the new file.

The thing that *does* need invalidation is the module-level
`_query_cache` dict at `mcp_server.py:1468`. It caches the results
of expensive aggregations (`list_courts`, `get_statistics`,
`get_db_stats`) keyed by `(function_name, args_tuple)`. After a
swap, these aggregations are stale.

**Contract:**

1. Module-level `_last_seen_db_generation: int = 0`.
2. In `get_db()`, after opening the connection, run
   `PRAGMA user_version` once.
3. If the returned generation differs from `_last_seen_db_generation`:
   - Call `_cache_clear()`.
   - Update `_last_seen_db_generation` to the new value.
   - Log a single `INFO` line: `db_generation transitioned X → Y`.
4. Return the connection.

The check is one `SELECT`-style PRAGMA per request — sub-millisecond.

## `/health` exposure

`handle_health` in `mcp_server.py` returns `db_generation` in its
JSON response alongside `decisions` count. Operators and external
monitors can poll `/health` to detect stuck workers (generation
not advancing despite known swap activity).
`data_snapshot` in the same response is `db_generation` rendered as a
UTC timestamp (the time the served file was last written).

## What this does NOT do

- Does **not** coordinate across multiple MCP worker processes.
  Each worker maintains its own `_last_seen_db_generation`. This
  is intentional — every worker reaches consistency on its own
  schedule, no cross-worker locking needed.
- Does **not** verify content integrity. That is the job of
  `PRAGMA integrity_check` post-swap and (later) the Merkle
  manifest published as part of Workstream B.
- Does **not** trigger automatic action on mismatch. The mismatch
  *is* the cache-clear; there is no rollback path here.

## Test

`tests/test_db_generation.py` covers:

- `build_fts5` writes a fresh user_version on each rebuild.
- `quick_publish` writes a fresh user_version after each insert.
- A simulated MCP `get_db()` clears `_query_cache` on transition.
- Generation values are monotone within a single VPS lifetime
  (timestamp-based, so a clock skew would break this — operationally
  we treat NTP drift as out-of-scope).

## Operator runbook

`runbooks/db_generation_mismatch.md` — what to check if /health
reports a generation that's older than expected (e.g. quick_publish
ran but workers still report old generation).
