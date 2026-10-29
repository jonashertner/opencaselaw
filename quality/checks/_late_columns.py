"""One shared pass over the `decisions` columns stored after `full_text`.

`full_text` is ordinal 22 of 36 in the record, and almost every row's
payload spills into an overflow-page chain. SQLite stores a record's columns
in order, so reading any column after `full_text` — `source_url` (25),
`content_hash` (32) — means following that row's whole chain: on production a
full scan that touches one of them reads ~70 GB, against a few GB for a scan
of the early columns.

Three gate checks need such a column for every row, and each used to make its
own pass:

  urls.no_relative_source_urls    COUNT(*) WHERE source_url NOT LIKE 'http%',
                                  then the same scan again for a 5-row sample
  urls.http_when_https_available  GROUP BY scheme over source_url
  duplicates.court_docket_collisions
                                  GROUP BY court, docket_number, content_hash,
                                  then the same scan again, ORDER BY n DESC,
                                  for the sample

Without `sqlite_stat1` (the build never runs ANALYZE) the planner answers the
GROUP BY on (court, docket_number, content_hash) by walking
`idx_decisions_court`, which is not covering: it visits every row in court
order rather than rowid order and walks each row's overflow chain to reach
`content_hash` — and when any duplicate group exists it does all of that a
second time for the sample. Because the check is a passing WARNING the gate
report filters it out, so its cost never appeared next to the URL checks'. In
the production
gate reports from 2026-09 and 2026-10 the run either ends ~20 s after the two
URL checks (~1,600-2,100 s each) or runs on for another several hundred to
1,700 s with no visible check to account for it; this is the only other check
in the gate that reads a column past `full_text` for every row.

`census()` replaces all of that with one sequential scan (`NOT INDEXED`, so
the planner cannot pick the court index), grouped once by
(court, docket_number, content_hash), carrying the URL counters as aggregates.
Every number the three checks report comes from the same predicates they used
before, so the verdicts are unchanged. The result is memoised per database
file and shared under a lock, as in exports._draw_pool: the first check to
arrive pays for the pass, the others wait for it.
"""
from __future__ import annotations

import heapq
import os
import sqlite3
import threading
import time
from dataclasses import dataclass, field

SAMPLE_LIMIT = 5

_URL_PRESENT = "source_url IS NOT NULL AND source_url != ''"
_URL_RELATIVE = f"{_URL_PRESENT} AND source_url NOT LIKE 'http%'"
_URL_HTTP = f"{_URL_PRESENT} AND source_url LIKE 'http://%'"

PASS_SQL = f"""
    SELECT court, docket_number, content_hash,
           COUNT(*) AS n,
           SUM({_URL_PRESENT}) AS n_url,
           SUM({_URL_RELATIVE}) AS n_relative,
           SUM({_URL_HTTP}) AS n_http,
           MIN(CASE WHEN {_URL_RELATIVE} THEN rowid END) AS relative_rowid
    FROM decisions NOT INDEXED
    GROUP BY court, docket_number, content_hash
"""


@dataclass
class LateColumnCensus:
    """Everything the three checks need, from one pass."""
    n_url: int = 0                 # rows with a non-empty source_url
    n_relative: int = 0            # ... that do not start with http (any case)
    n_http: int = 0                # ... that start with http://
    relative_rowids: list[int] = field(default_factory=list)
    dup_groups: int = 0            # (court, docket, content_hash) groups > 1 row
    dup_sample: list[dict] = field(default_factory=list)
    elapsed_s: float = 0.0


_lock = threading.Lock()
_cache: dict[tuple, LateColumnCensus] = {}


def _scan(conn: sqlite3.Connection) -> LateColumnCensus:
    started = time.monotonic()
    out = LateColumnCensus()
    relative: list[int] = []
    dups: list[tuple[int, int, tuple]] = []
    cur = conn.cursor()
    cur.row_factory = None          # plain tuples: ~1M groups on production
    for i, (court, docket, chash, n, n_url, n_rel, n_http, rel_rowid) in \
            enumerate(cur.execute(PASS_SQL)):
        out.n_url += n_url
        out.n_relative += n_rel
        out.n_http += n_http
        if rel_rowid is not None:
            relative.append(rel_rowid)
        if n > 1 and docket and chash:
            out.dup_groups += 1
            item = (n, -i, (court, docket, chash))
            if len(dups) < SAMPLE_LIMIT:
                heapq.heappush(dups, item)
            else:
                heapq.heappushpop(dups, item)
    out.relative_rowids = heapq.nsmallest(SAMPLE_LIMIT, relative)
    out.dup_sample = [
        {"court": c, "docket_number": d, "content_hash": h, "n": n}
        for n, _, (c, d, h) in sorted(dups, reverse=True)
    ]
    out.elapsed_s = round(time.monotonic() - started, 3)
    return out


def _cache_key(db_path: str | None) -> tuple | None:
    if not db_path:
        return None
    try:
        st = os.stat(db_path)
    except OSError:
        return None
    return (os.path.realpath(db_path), st.st_size, st.st_mtime_ns)


def census(conn: sqlite3.Connection,
           db_path: str | None = None) -> LateColumnCensus:
    """The shared pass, computed once per database file per process.

    Without a ``db_path`` (a direct call on a bare connection) nothing is
    cached and every call scans.
    """
    key = _cache_key(db_path)
    if key is None:
        return _scan(conn)
    with _lock:
        hit = _cache.get(key)
        if hit is None:
            hit = _scan(conn)
            _cache[key] = hit
    return hit


def _reset_cache() -> None:
    """Drop the memoised census. For tests — the gate is a fresh process."""
    with _lock:
        _cache.clear()
