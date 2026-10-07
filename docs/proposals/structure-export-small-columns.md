# Proposal: let the `structure.parquet` export read small columns only

**Status:** proposed, not implemented. Changes the `decision_structure.db`
schema (`search_stack/extract_decision_structure*.py` is `proposal_only` in
`ops/autonomy-policy.json`) and the nightly export (`export_parquet.py`, publish
step 3). Needs explicit owner approval.
**Raised:** 2026-10-07.
**Evidence (offline, in this repo):** `scripts/measure_structure_export_cost.py`,
`tests/test_structure_export_id_parity.py`.

## The defect

On Hugging Face (`voilaj/swiss-caselaw`, checked 2026-10-07),
`structure/structure.parquet` last changed in commit `1d37aa20`
(2026-09-10 00:03 UTC). It still keys the 14,578 historical BGE rows as
`bge_historical_*`. `structure/erwaegungen_paragraphs.parquet` (commit
`12c2b8f1`, 2026-10-04) keys the same decisions as `bge_*`. Anyone who joins
the two files loses those rows. The metadata file also lacks every decision
added or re-extracted since 2026-09-10.

The cause is that the export never finishes. Since step 2g builds the sidecar
from served text (2026-09-09), every metadata export projects over its budget.
`export_parquet._bounded_stream` (`export_parquet.py:685`) then keeps the last
good file, and step 4 uploads that file again. The skip is recorded in
`structure/export_status.json`, but nothing reads that file (see open question 3).

## Why the query is expensive (reproduced offline)

The query is `_STRUCTURE_META_COLS` (`export_parquet.py:729`):

```sql
SELECT decision_id, court, language,
       CAST(sachverhalt IS NOT NULL AND sachverhalt != '' AS INTEGER),
       CAST(erwaegungen IS NOT NULL AND erwaegungen != '' AS INTEGER),
       CAST(dispositiv  IS NOT NULL AND dispositiv  != '' AS INTEGER),
       sachverhalt_method, erwaegungen_method, dispositiv_method,
       CAST(erwaegungen_paragraph_count AS INTEGER)
FROM structure
```

SQLite stores a row as one record: a header, then the column bodies in
declaration order. Whatever does not fit on the leaf page goes into a linked
chain of overflow pages. To read a column at byte offset *k* of the record,
SQLite has to walk the chain up to *k*, one page at a time, because each
page only stores the number of the next one. The `structure` columns are
declared in this order (`search_stack/extract_decision_structure.py:558`):

| # | column | size | needed by the export |
|---|---|---|---|
| 1–5 | `decision_id`, `court`, `canton`, `language`, `decision_date` | small | `decision_id`, `court`, `language` |
| 6 | `regeste` | text | — |
| 7 | **`sachverhalt`** | **text** | `has_sachverhalt` (computed from the text) |
| 8 | `sachverhalt_method` | small | yes, but it sits behind #6–7 |
| 9 | **`erwaegungen`** | **text** | `has_erwaegungen` (computed from the text) |
| 10–11 | `erwaegungen_method`, `erwaegungen_paragraph_count` | small | yes, but they sit behind #9 |
| 12 | **`dispositiv`** | **text** | `has_dispositiv` (computed from the text) |
| 13 | `dispositiv_method` | small | yes, but it sits behind #12 |
| 14–15 | `dispositiv_orders`, `extracted_at` | small | — |

`erwaegungen_paragraph_count` is already a stored INTEGER. It is not computed
from the text, yet reading it costs the whole walk because of where it sits.

`scripts/measure_structure_export_cost.py --rows 20000` builds synthetic
sidecars with the production `SCHEMA` (SQLite 3.45.1, 4 KiB pages; the
`structure` b-tree has 10,424 leaf and 80,143 overflow pages). It then
measures the bytes each layout's export query reads on a fresh connection
opened `mode=ro&immutable=1` (Linux `/proc/self/io`):

| layout | bytes read / row | at 1.07M rows | query plan |
|---|---:|---:|---|
| **today**: current schema, current query | 22,010 | 23.6 GB | `SCAN structure` |
| flag columns **appended** to the row, no index | 18,554 | 19.9 GB | `SCAN structure` |
| small columns **moved in front of** the text, no index | 2,139 | 2.3 GB | `SCAN structure` |
| current schema + **expression** index | 70 | 75 MB | `SCAN structure USING INDEX …` |
| **proposed**: flag columns + **covering** index | **78** | **84 MB** | `SCAN structure USING COVERING INDEX idx_structure_export` |
| reference: read only `decision_id, court, language` | 2,140 | 2.3 GB | `SCAN structure` |

The synthetic rows hold ~18 KB of section text on average. Production rows appear
larger: the 2026-09-10 comment in `export_parquet.py` records the walk at
~52 GB and ~27 min. The full-scan rows grow with text size; the index rows do
not.

What the measurements show:

1. **Any column behind the text costs the whole walk.** That includes the
   methods, the count, and even `extracted_at`. `x IS NOT NULL` also loads
   the full value. The current query reads ~19 % more than one pass over
   the table, which fits each text being loaded twice (`IS NOT NULL`, then
   `!= ''`).
2. **Storing the flags as columns is necessary but not enough.** An added
   column (`ALTER TABLE … ADD COLUMN`, or appended in `SCHEMA`) lands at the
   end of the record, behind all the text: 18.6 KB/row, no gain.
3. Moving the small columns in front of the text limits the read to the leaf
   pages (~2.1 KB/row). That is 10× better but still reads every leaf page,
   which holds each text's first part.
4. **A covering index over the small columns is ~280× cheaper**, and the
   query plan states that no table page is read.
5. **The export's probe must change too, or the fix does nothing.**
   `_projected_seconds` (`export_parquet.py:658`) times
   `SELECT … FROM structure WHERE rowid BETWEEN ? AND ?`. That is a rowid
   seek *on the table*, so on the indexed layout it still walks the overflow
   chains: 19.5 KB/row measured over the probe's 800 rows. With only the
   schema and the query changed, the probe would keep projecting the old
   cost and keep skipping the export. Step 2g would bootstrap for 3 h and
   `structure.parquet` would stay frozen.

## Proposed fix

### 1. Schema (`search_stack/extract_decision_structure.py`, `SCHEMA`)

```sql
CREATE TABLE IF NOT EXISTS structure (
    ...                                   -- the 15 existing columns, unchanged
    extracted_at         TEXT,
    has_sachverhalt      INTEGER NOT NULL,   -- 1 iff sachverhalt is non-empty
    has_erwaegungen      INTEGER NOT NULL,
    has_dispositiv       INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_structure_export ON structure(
    decision_id, court, language, has_sachverhalt, has_erwaegungen, has_dispositiv,
    sachverhalt_method, erwaegungen_method, dispositiv_method, erwaegungen_paragraph_count);
```

- The flags are written at extraction time as `int(bool(section))`. That is
  the same predicate as today's `x IS NOT NULL AND x != ''`, since `None` and
  `''` are the only falsy values.
- They are appended on purpose. The export never reads them from the row
  (finding 4), so their position costs nothing. Appending also leaves the
  order of the existing columns unchanged for every reader. The `SELECT *`
  readers (`mcp_server._fetch_structure_row`, `seo_pages._fetch_structure`)
  access columns by name, so three extra keys are harmless.
- `NOT NULL` **without** a default: a writer that forgets the flags fails
  loudly instead of silently recording "no section".
- The index costs ~80 B per decision in the synthetic test. Production
  `decision_id`s are somewhat longer, so expect ~0.1 GB at 1.07M rows,
  against a ~55 GB sidecar. SQLite maintains it on every insert/upsert in
  both writers.

### 2. Writers

- `build_db` (shard fallback): the positional
  `INSERT OR REPLACE INTO structure VALUES (?, … 15)`
  (`extract_decision_structure.py:693`, `:704`) becomes 18 values; named
  columns would be safer.
- `_apply_one` (served-text writer,
  `extract_decision_structure_incremental.py:341`): add the three columns to
  the INSERT and to `ON CONFLICT … DO UPDATE`.

### 3. Export (`export_parquet.py`)

```python
_STRUCTURE_EXPORT_INDEX = "idx_structure_export"
_STRUCTURE_META_COLS_INDEXED = (
    "decision_id, court, language, has_sachverhalt, has_erwaegungen, has_dispositiv, "
    "sachverhalt_method, erwaegungen_method, dispositiv_method, "
    "CAST(erwaegungen_paragraph_count AS INTEGER)")

def _structure_meta_source(conn) -> tuple[str, str | None]:
    """(select columns, covering index or None). The index-only path is taken
    only when the sidecar has the index AND SQLite's plan says it is covering;
    anything else (an older sidecar) keeps today's query and probe."""
    if _STRUCTURE_EXPORT_INDEX not in {r[1] for r in conn.execute("PRAGMA index_list(structure)")}:
        return _STRUCTURE_META_COLS, None
    plan = " ".join(str(r[-1]) for r in conn.execute(
        f"EXPLAIN QUERY PLAN SELECT {_STRUCTURE_META_COLS_INDEXED} "
        f"FROM structure INDEXED BY {_STRUCTURE_EXPORT_INDEX}"))
    if f"COVERING INDEX {_STRUCTURE_EXPORT_INDEX}" not in plan:
        return _STRUCTURE_META_COLS, None
    return _STRUCTURE_META_COLS_INDEXED, _STRUCTURE_EXPORT_INDEX
```

- `_bounded_stream(…, index_only=None)`: when `index_only` is set, the SELECT
  reads `FROM structure INDEXED BY <index>` and **skips
  `_projected_seconds`**. The probe exists because a table scan's cost
  depends on text volume, which nobody knows up front. A covering-index
  scan never touches the table b-tree and costs a bounded amount (the index's
  size), and the plan check above guarantees it. The progress-handler hard
  stop and the wall-clock cap stay unchanged as the safety net. Log the
  path taken (`index-only via idx_structure_export` / `legacy table scan`)
  so the nightly log shows which one ran. To keep a
  projection anyway, use key windows through the index
  (`WHERE decision_id >= ? LIMIT 50`, start keys read by rowid from the
  table's leading column, which is cheap). The rowid windows are what is
  wrong.
- The parquet schema (`STRUCTURE_META_SCHEMA`) is unchanged. Rows come out in
  `decision_id` order (index order) instead of rowid order.
- Update the comment block at `export_parquet.py:600–640`. It already
  anticipated this ("once the sidecar carries a covering index … the nightly
  export resumes by itself"), but because of finding 5 it would not have
  resumed on its own.

### Alternatives considered

- **Expression index, no new columns.** On SQLite 3.45.1 it reads 70 B/row,
  because the planner substitutes the indexed expressions. Not recommended as
  the primary fix:
  - The plan says only `USING INDEX`, not `COVERING`, so the export cannot
    verify the fast path before running.
  - It works only while the export's expression text matches the index DDL. A
    cosmetic edit to either side silently brings back the 52 GB walk, and
    with it the frozen file.
  - It depends on SQLite's indexed-expression substitution, which older
    releases do not do. I have not checked which SQLite production links, or
    the exact release that introduced the substitution.

  Its one advantage is in the migration plan (option B).
- **Small columns first** (reordered table): 2.3 GB at production scale, 27×
  more than the index. It still needs the same rebuild, and the positional
  writers change too.
- **Narrow companion table `structure_meta`.** Same I/O as the index, but it
  adds a second write path and a second cascade delete
  (`_delete_for_decisions`) to keep in step. The index is kept in step by
  SQLite itself.

## Migration plan

Invariants: the live sidecar is never written in place; every reader keeps
opening it `mode=ro&immutable=1`; it is only ever replaced by `os.replace`
onto the resolved data-volume path (`publish.py:805`); and old and new code
can each read the other's sidecar.

**Option A (recommended): through the existing bootstrap.**

1. Land schema, writers, export and the test change in one commit (see
   Acceptance). Changing `extract_decision_structure.py` changes
   `EFFECTIVE_EXTRACTOR_VERSION`: in the prototype it went from
   `3+src.65ad13e00a09` to `3+src.066bfa07a69d`. `_select_diff_base` then
   reports `version_mismatch`, and the next step 2g runs `_bootstrap_via_full`.
   That writes a fresh sidecar to `.decision_structure.db.tmp` beside the
   resolved live file. It takes ~3 h, can resume across nights, and
   `_refuse_without_space` requires 1.2× the sidecar size free.
   `SCHEMA` creates the index before the first insert, so there is no
   separate full-table read to build it.
2. The step 2g coverage gate (`STRUCTURE_COVERAGE_FLOOR = 0.98`) runs, then
   `os.replace` swaps the new file in and the warm-up runs. Workers open the
   new inode on their next request.
3. While the bootstrap is running (1–2 nights), the live sidecar has no index.
   The export takes the legacy path and skips exactly as it does today, so
   nothing gets worse.
4. The first export after the swap takes the index-only path. Its log line
   reads `index-only`, and `export_status.json` shows `counts.structure`.
   Step 4 uploads the new file, and on HF `structure/structure.parquet` gets a
   fresh commit. Then check id parity (no `bge_historical_*` left; every
   paragraph `decision_id` present in `structure.parquet`).
5. Rollback:
   - Revert only `export_parquet.py`: the export goes back to the legacy path,
     and the three columns and the index are inert for every reader.
   - Revert everything: one more bootstrap back to the old schema.

   No data has to move in either direction; the sidecar is derived data.

**Option B (no bootstrap; interim only).** Put the *expression* index DDL in
`INCREMENTAL_SCHEMA` (`extract_decision_structure_incremental.py`). That file
is not part of the extractor hash, so no bootstrap follows: the next nightly
`_ensure_state_tables` builds the index on the tmp copy. That costs one
~27 min full read inside step 2g, which has a 4 h timeout and a 2.5 h stall
watchdog. The export then queries `INDEXED BY idx_structure_export_expr`.
This carries every caveat listed under the expression-index alternative. I
would only use it if a 3 h bootstrap cannot be scheduled soon.

## Acceptance

- `tests/test_structure_export_id_parity.py::test_metadata_export_reads_no_section_text`
  is `xfail(strict=True)` today. It zeroes every overflow page of the fixture's
  `structure` table, so any read that walks section text fails with
  `SQLITE_CORRUPT`, on any OS. The metadata export must still finish with
  identical rows. When the fix lands the test XPASSes, strict mode turns that
  into a failure, and the marker is removed in the same commit.
- I checked this in a throwaway worktree (not committed):

  | prototype stage | result |
  |---|---|
  | schema + writers only | still xfail |
  | + new export query, probe unchanged | still xfail (catches finding 5) |
  | + index-only path skips the table probe | XPASS (strict) |

  With the marker removed, the full suite passes (see Prototype).
- `test_structure_export_finishes_with_the_paragraph_export_ids` must stay
  green. It checks that a stale `bge_historical_*` file is replaced and that
  both exports key the same decisions.
- On the real sidecar, read-only:
  `python3 scripts/measure_structure_export_cost.py --structure-db output/decision_structure.db`
  should report `has_proposed_columns: true`, `has_proposed_index: true`, and
  a `plan_proposed_query` containing `COVERING INDEX`. This mode never scans
  the table; it reads only the probe's 800 sample rows.

## Open questions for the owner

1. The brief mentions "four has-section flags", but `STRUCTURE_META_SCHEMA`
   has three (`has_sachverhalt`, `has_erwaegungen`, `has_dispositiv`). A
   fourth, for example `has_regeste`, would add a column to the public
   dataset. Do you want it?
2. `erwaegungen_paragraph_count` counts the extractor's synthetic depth-0
   paragraph (the fallback in `parse_erwaegungen_paragraphs`). That paragraph
   is never written to `erwaegungen_paragraph`, so some decisions show
   `count = 1` with no rows in `erwaegungen_paragraphs.parquet`. Should the
   count stay as it is (documented), or count the paragraph rows actually
   written? That changes public semantics and is out of scope here.
3. Nothing alerts on a skip streak. `export_status.json` is written but read
   by no check, which is how a frozen file went unnoticed for four weeks.
   Should a skip on N consecutive runs, or a `structure.parquet` older than
   X days, raise a health alert?
4. When should the 3 h bootstrap run? A weekend night avoids competing with
   weekday step-2g wall clock.

## Not verified here

No production or HF access from this environment. Not checked:

- the SQLite version production Python links;
- the real per-row text sizes (the ~52 GB / ~27 min figure comes from the
  2026-09-10 comment in `export_parquet.py`);
- the HF commit facts, which are taken from the brief.

The scaling to 1.07M rows multiplies measured per-row bytes; it does not
measure production.

## Prototype

The fix was prototyped in a throwaway worktree on 2026-10-07 and **not
committed**: about 60 lines across the two extractor files and
`export_parquet.py`, as described above. With the xfail marker removed, the
full suite gave **4,363 passed, 28 skipped**: the 4,360 of the unchanged
baseline plus the three new tests. The existing structure tests
(`test_structure_parquet_export.py`, which uses hand-built legacy-schema
sidecars) pass unchanged on the legacy fallback path.
`test_extract_decision_structure_incremental.py` and
`test_warm_structure_sidecar.py` pass unchanged as well.
