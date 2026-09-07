"""Mid-query deadline abort via sqlite3 progress handler (integration evaluation 2026-07).

SEARCH_DEADLINE_MS was a cooperative soft deadline checked only BETWEEN
operations; a pathological in-flight FTS5 MATCH ran to the 120 s dispatch
timeout with nothing able to stop it. _deadline_abort installs a progress
handler for the duration of one statement and always clears it on exit.
"""
from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import mcp_server as m  # noqa: E402

# A recursive CTE that takes long enough for the handler to fire.
SLOW_SQL = """
WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM c WHERE x < 3000000)
SELECT count(*) FROM c
"""


def test_expired_deadline_aborts_in_flight_query():
    conn = sqlite3.connect(":memory:")
    deadline = time.monotonic() - 1  # already passed
    try:
        with m._deadline_abort(conn, deadline, n_opcodes=1000):
            try:
                conn.execute(SLOW_SQL).fetchall()
                raise AssertionError("query was not interrupted")
            except sqlite3.OperationalError as e:
                assert "interrupt" in str(e).lower()
    finally:
        conn.close()


def test_handler_cleared_after_exit():
    conn = sqlite3.connect(":memory:")
    deadline = time.monotonic() - 1
    try:
        with m._deadline_abort(conn, deadline, n_opcodes=1000):
            try:
                conn.execute(SLOW_SQL).fetchall()
            except sqlite3.OperationalError:
                pass
        # same connection, handler gone → the query completes
        (n,) = conn.execute(
            "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM c WHERE x < 200000) "
            "SELECT count(*) FROM c").fetchone()
        assert n == 200000
    finally:
        conn.close()


def test_none_deadline_never_installs_handler():
    calls = []

    class Recorder:
        def set_progress_handler(self, *a):
            calls.append(a)

    with m._deadline_abort(Recorder(), None):
        pass
    assert calls == []


def test_future_deadline_lets_query_finish():
    conn = sqlite3.connect(":memory:")
    deadline = time.monotonic() + 30
    try:
        with m._deadline_abort(conn, deadline, n_opcodes=1000):
            (n,) = conn.execute(
                "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM c WHERE x < 100000) "
                "SELECT count(*) FROM c").fetchone()
        assert n == 100000
    finally:
        conn.close()


def test_strategy_loop_wires_the_abort():
    src = Path(REPO / "mcp_server.py").read_text(encoding="utf-8")
    assert "with _deadline_abort(conn, _deadline):" in src
    # partial-result disclosure, never an error
    assert '"deadline_partial"' in src or "deadline_partial" in src
