"""Byte-identical repeat searches are served from _SEARCH_RESULT_CACHE.

Devdata 2026-08 measured ~30% of search queries as exact repeats (one
integrator loop alone at ~8,900×/wk), each re-paying FTS5 + hybrid +
Haiku rerank. The cache wraps search_fts5's full body, so these tests pin
the seam's contracts rather than search quality:

- the key is byte-exact over ALL args — FTS5 operators are case-sensitive
  ('Mietzins OR Pachtzins' ≠ 'mietzins or pachtzins', c975ea1a), so no
  normalization may ever fold two queries onto one entry;
- hits and misses are isolated by deep copies in both directions (callers
  mutate returned rows: REST `fields` stripping, handler annotations);
- meta is replayed on hits even when the priming call passed meta=None;
- degraded (deadline_partial) results and bulk calls (limit > gate) are
  never cached;
- _cache_clear() — the db_generation swap hook — empties it.

The inner search is stubbed: what's under test is the cache, not FTS5.
"""
import sqlite3

import pytest

import mcp_server


def _fake_get_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    # No marked_for_publication column → the flags loop no-ops (its own
    # behavior is pinned separately in test_flags_loop_result_is_cached).
    conn.execute("CREATE TABLE decisions (decision_id TEXT)")
    return conn


class _InnerSpy:
    """Stands in for _search_fts5_inner; counts calls, returns canned rows."""

    def __init__(self, rows=None, total=7, meta_extra=None):
        self.calls = 0
        self._rows = rows if rows is not None else [
            {"decision_id": "bger_1", "snippet": "Notwehr", "score": 1.0},
            {"decision_id": "bger_2", "snippet": "Putativnotwehr", "score": 0.9},
        ]
        self._total = total
        self._meta_extra = dict(meta_extra or {})

    def __call__(self, conn, query, court, canton, language, date_from,
                 date_to, chamber, decision_type, legal_area, limit, offset,
                 sort=None, marked_for_publication=None, meta=None,
                 published_from=None, published_to=None):
        self.calls += 1
        if meta is not None:
            meta["total_is_lower_bound"] = False
            meta.update(self._meta_extra)
        # Fresh dicts per call, like the real inner building rows from SQL.
        return [dict(r) for r in self._rows], self._total


@pytest.fixture()
def spy(monkeypatch):
    inner = _InnerSpy()
    monkeypatch.setattr(mcp_server, "get_db", _fake_get_db)
    monkeypatch.setattr(mcp_server, "_search_fts5_inner", inner)
    return inner


def test_identical_call_served_from_cache(spy):
    hits0 = mcp_server._metrics["search_result_cache_hits"]
    r1, t1 = mcp_server.search_fts5("Notwehr", court="bger", limit=50)
    r2, t2 = mcp_server.search_fts5("Notwehr", court="bger", limit=50)
    assert spy.calls == 1
    assert (r1, t1) == (r2, t2)
    assert mcp_server._metrics["search_result_cache_hits"] == hits0 + 1


def test_operator_case_is_a_different_query(spy):
    # 'OR' is an FTS5 operator, 'or' a literal term — folding the key would
    # serve one query's results for the other and partially undo c975ea1a.
    mcp_server.search_fts5("Mietzins OR Pachtzins")
    mcp_server.search_fts5("mietzins or pachtzins")
    assert spy.calls == 2


def test_whitespace_is_significant(spy):
    mcp_server.search_fts5("Notwehr")
    mcp_server.search_fts5(" Notwehr ")
    assert spy.calls == 2


def test_any_filter_change_misses(spy):
    mcp_server.search_fts5("Notwehr", court="bger")
    mcp_server.search_fts5("Notwehr", court="bge")
    mcp_server.search_fts5("Notwehr", court="bger", offset=50)
    mcp_server.search_fts5("Notwehr", court="bger", sort="date_desc")
    mcp_server.search_fts5("Notwehr", court="bger", published_from="2026-10-01")
    assert spy.calls == 5


def test_mutating_returned_rows_does_not_poison_cache(spy):
    r1, _ = mcp_server.search_fts5("Notwehr")
    r1[0]["snippet"] = "MUTATED"
    del r1[1]["decision_id"]
    r1.append({"decision_id": "fake"})
    r2, _ = mcp_server.search_fts5("Notwehr")
    assert spy.calls == 1
    assert len(r2) == 2
    assert r2[0]["snippet"] == "Notwehr"
    assert r2[1]["decision_id"] == "bger_2"


def test_hits_are_isolated_from_each_other(spy):
    ra, _ = mcp_server.search_fts5("Notwehr")
    rb, _ = mcp_server.search_fts5("Notwehr")
    rb[0]["snippet"] = "MUTATED"
    rc, _ = mcp_server.search_fts5("Notwehr")
    assert ra[0]["snippet"] == "Notwehr"
    assert rc[0]["snippet"] == "Notwehr"


def test_meta_replayed_on_hit_even_if_priming_call_passed_none(monkeypatch):
    inner = _InnerSpy(meta_extra={"total_is_lower_bound": True})
    monkeypatch.setattr(mcp_server, "get_db", _fake_get_db)
    monkeypatch.setattr(mcp_server, "_search_fts5_inner", inner)
    mcp_server.search_fts5("Notwehr")            # meta=None primes the cache
    meta: dict = {}
    mcp_server.search_fts5("Notwehr", meta=meta)  # hit must still carry flags
    assert inner.calls == 1
    assert meta["total_is_lower_bound"] is True


def test_limit_above_gate_is_not_cached(spy):
    lim = mcp_server._SEARCH_RESULT_CACHE_LIMIT_GATE + 1
    mcp_server.search_fts5("Notwehr", limit=lim)
    mcp_server.search_fts5("Notwehr", limit=lim)
    assert spy.calls == 2
    assert len(mcp_server._SEARCH_RESULT_CACHE) == 0


def test_deadline_partial_result_is_not_cached(monkeypatch):
    inner = _InnerSpy(meta_extra={"deadline_partial": True})
    monkeypatch.setattr(mcp_server, "get_db", _fake_get_db)
    monkeypatch.setattr(mcp_server, "_search_fts5_inner", inner)
    mcp_server.search_fts5("Notwehr")
    mcp_server.search_fts5("Notwehr")
    assert inner.calls == 2


def test_cache_clear_hook_empties_it(spy):
    mcp_server.search_fts5("Notwehr")
    assert len(mcp_server._SEARCH_RESULT_CACHE) == 1
    mcp_server._cache_clear()  # what get_db() runs on a db_generation swap
    mcp_server.search_fts5("Notwehr")
    assert spy.calls == 2


def test_flags_loop_result_is_cached(monkeypatch):
    """The cache write sits AFTER the marked_for_publication flags loop, so a
    hit must carry the flag without re-querying."""
    def _get_db_with_flag():
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE decisions "
            "(decision_id TEXT, marked_for_publication INTEGER)"
        )
        conn.execute("INSERT INTO decisions VALUES ('bger_1', 1)")
        return conn

    inner = _InnerSpy(rows=[{"decision_id": "bger_1", "snippet": "s"}])
    monkeypatch.setattr(mcp_server, "get_db", _get_db_with_flag)
    monkeypatch.setattr(mcp_server, "_search_fts5_inner", inner)
    r1, _ = mcp_server.search_fts5("Notwehr")
    r2, _ = mcp_server.search_fts5("Notwehr")
    assert inner.calls == 1
    assert r1[0]["marked_for_publication"] is True
    assert r2[0]["marked_for_publication"] is True


def test_metrics_exposed(spy):
    mcp_server.search_fts5("Notwehr")
    mcp_server.search_fts5("Notwehr")
    snap = mcp_server._get_metrics()["search_result_cache"]
    assert snap["hits"] >= 1
    assert snap["misses"] >= 1
    assert snap["entries"] >= 1
