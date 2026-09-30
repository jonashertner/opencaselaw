"""Admission control: fast / heavy / other execution lanes.

2026-09-30: every blocking call on a worker shared one pool, nothing bounded the
work a worker accepted, and queued work ran long after its caller had gone.
get_law averaged 55 s behind searches and exports. These tests pin the lanes
that replace that pool: cheap key reads never wait behind heavy work, heavy work
is refused quickly once it cannot start in time, and work whose request is
already over is never started.
"""
from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import mcp_server as m  # noqa: E402


def _submit(ex, lane, fn, deadline=None):
    """Submit as a request in `lane` would: the lane and deadline come from
    the caller's context, exactly as asyncio.to_thread hands them over."""
    t1 = m._ctx_lane.set(lane)
    t2 = m._ctx_request_deadline.set(deadline)
    try:
        return ex.submit(fn)
    finally:
        m._ctx_request_deadline.reset(t2)
        m._ctx_lane.reset(t1)


@pytest.fixture
def lanes(monkeypatch):
    """A small lane executor installed as the module's lanes, shut down after."""
    ex = m._LaneExecutor(fast=2, heavy=1, other=2)
    monkeypatch.setattr(m, "_LANES", ex)
    monkeypatch.setattr(m, "OCL_LANES", True)
    monkeypatch.setattr(m, "_heavy_client_inflight", {})
    yield ex
    ex.shutdown(wait=False, cancel_futures=True)


# ── the executor ────────────────────────────────────────────────────────────

def test_executor_installs_on_a_running_loop_and_routes_by_lane():
    # Python 3.12+ set_default_executor rejects anything that is not a
    # ThreadPoolExecutor; asyncio.run shuts the default executor down at exit.
    ex = m._LaneExecutor(fast=1, heavy=1, other=1)

    async def main():
        asyncio.get_running_loop().set_default_executor(ex)
        names = {}
        for lane in ("fast", "heavy", None):
            tok = m._ctx_lane.set(lane)
            try:
                names[lane] = await asyncio.to_thread(lambda: threading.current_thread().name)
            finally:
                m._ctx_lane.reset(tok)
        return names

    names = asyncio.run(main())
    assert names["fast"].startswith("lane-fast")
    assert names["heavy"].startswith("lane-heavy")
    assert names[None].startswith("lane-other")
    assert ex._shutdown and ex._fast._shutdown and ex._heavy._shutdown


def test_fast_lane_does_not_wait_behind_a_saturated_heavy_lane(lanes):
    gate = threading.Event()
    blocker = _submit(lanes, "heavy", gate.wait)
    queued = _submit(lanes, "heavy", lambda: "late")
    try:
        t0 = time.monotonic()
        assert _submit(lanes, "fast", lambda: "quick").result(timeout=2) == "quick"
        assert time.monotonic() - t0 < 0.5
        assert lanes.snapshot()["heavy"]["queued"] == 1
    finally:
        gate.set()
    assert blocker.result(timeout=2) is True
    assert queued.result(timeout=2) == "late"


def test_queued_work_past_its_deadline_never_runs(lanes):
    gate = threading.Event()
    ran = []
    _submit(lanes, "heavy", gate.wait)
    late = _submit(lanes, "heavy", lambda: ran.append(1), deadline=time.monotonic() + 0.05)
    time.sleep(0.15)
    gate.set()
    with pytest.raises(m._LaneDeadlineExpired):
        late.result(timeout=2)
    assert ran == []
    assert lanes.snapshot()["heavy"]["expired"] == 1


def test_cancelled_queued_work_is_dropped_and_not_leaked(lanes):
    # A timed-out MCP call cancels its awaiting coroutine; wrap_future then
    # cancels the queued job, the pool skips it and the job wrapper never runs.
    # The queued count must still come back down, or the lane would refuse
    # every heavy call forever.
    gate = threading.Event()
    ran = []

    async def main():
        asyncio.get_running_loop().set_default_executor(lanes)
        tok = m._ctx_lane.set("heavy")
        try:
            blocker = asyncio.ensure_future(asyncio.to_thread(gate.wait))
            await asyncio.sleep(0.05)
            waiting = asyncio.ensure_future(asyncio.to_thread(lambda: ran.append(1)))
            await asyncio.sleep(0.05)
            assert lanes.snapshot()["heavy"]["queued"] == 1
            waiting.cancel()
            await asyncio.sleep(0.05)
            snap = lanes.snapshot()["heavy"]
            assert snap["queued"] == 0
            assert snap["cancelled"] == 1
            gate.set()
            await blocker
        finally:
            m._ctx_lane.reset(tok)
            gate.set()

    asyncio.run(main())
    assert ran == []


# ── admission ───────────────────────────────────────────────────────────────

def test_admission_refuses_once_the_oldest_queued_job_has_waited_too_long(lanes, monkeypatch):
    monkeypatch.setattr(m, "HEAVY_MAX_QUEUE_WAIT_S", 0.05)
    monkeypatch.setattr(m, "LANE_HEAVY_QUEUE", 64)
    gate = threading.Event()
    try:
        _submit(lanes, "heavy", gate.wait)
        assert m._heavy_admit() is True          # busy but nothing waiting yet
        _submit(lanes, "heavy", lambda: None)
        assert m._heavy_admit() is True          # fresh job in the queue
        time.sleep(0.1)
        assert m._heavy_admit() is False         # it has now waited too long
    finally:
        gate.set()


def test_admission_refuses_at_the_queue_cap(lanes, monkeypatch):
    monkeypatch.setattr(m, "HEAVY_MAX_QUEUE_WAIT_S", 60)
    monkeypatch.setattr(m, "LANE_HEAVY_QUEUE", 1)
    gate = threading.Event()
    try:
        _submit(lanes, "heavy", gate.wait)
        _submit(lanes, "heavy", lambda: None)
        assert m._heavy_admit() is False
    finally:
        gate.set()


def test_per_client_cap_does_not_bite_while_there_is_spare_capacity(lanes, monkeypatch):
    # Work-conserving: an integrator's parallel searches all run on an idle
    # worker; the cap only shares out a lane that is actually full.
    monkeypatch.setattr(m, "HEAVY_PER_CLIENT", 2)
    m._heavy_client_inflight["1.2.3.4"] = 5
    assert m._heavy_admit("1.2.3.4") is True


def test_per_client_cap_bites_once_the_heavy_lane_is_full(lanes, monkeypatch):
    monkeypatch.setattr(m, "HEAVY_PER_CLIENT", 2)
    monkeypatch.setattr(m, "HEAVY_MAX_QUEUE_WAIT_S", 60)
    gate = threading.Event()
    try:
        _submit(lanes, "heavy", gate.wait)        # heavy=1 thread: now full
        m._heavy_client_inflight["1.2.3.4"] = 2
        assert m._heavy_admit("1.2.3.4") is False
        assert m._heavy_admit("5.6.7.8") is True
        assert m._heavy_admit() is True          # MCP: no client key, no per-client cap
    finally:
        gate.set()


def test_admission_is_open_when_lanes_are_off(lanes, monkeypatch):
    monkeypatch.setattr(m, "OCL_LANES", False)
    monkeypatch.setattr(m, "LANE_HEAVY_QUEUE", 0)
    assert m._heavy_admit("1.2.3.4") is True


# ── classification ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("name,args,lane", [
    ("get_law", {"abbreviation": "OR", "article": "41"}, "fast"),
    ("get_law", {"abbreviation": "EG ZGB", "canton": "ZH"}, "heavy"),  # lexfind.ch live
    ("get_law", {"abbreviation": "StG", "canton": "zh"}, "heavy"),
    ("get_law", {"abbreviation": "ZH/StG"}, "heavy"),                  # prefix decides
    ("get_law", {"abbreviation": "OR", "canton": "CH"}, "fast"),       # CH = federal
    ("get_law", {"abbreviation": "OR", "canton": ""}, "fast"),
    ("get_decision", {"decision_id": "x"}, "fast"),
    ("get_erwaegung", {"decision_id": "x"}, "fast"),
    ("get_regeste", {"decision_id": "x"}, "fast"),
    ("get_decision_structure", {"decision_id": "x"}, "fast"),
    ("list_courts", {}, "fast"),
    ("courts", {}, "fast"),                                             # alias
    ("cite", {"reference": "BGE 140 III 86"}, "heavy"),                 # miss path searches
    ("search_decisions", {"query": "x"}, "heavy"),
    ("export_decision", {"decision_id": "x"}, "heavy"),
    ("no_such_tool", {}, "heavy"),
])
def test_tool_lanes(name, args, lane):
    assert m._lane_for_tool(name, args) == lane


@pytest.mark.parametrize("path,query,lane", [
    ("/api/laws/OR", {}, "fast"),
    ("/laws/OR", {}, "fast"),
    ("/api/laws/search", {"q": "x"}, "heavy"),
    ("/api/laws/EG%20ZGB/ZH", {}, "heavy"),
    ("/api/laws/StG", {"canton": "ZH"}, "heavy"),
    ("/api/laws/OR", {"canton": "CH"}, "fast"),
    ("/api/laws/ZH%2FStG", {}, "heavy"),
    ("/api/decisions/bge_BGE_140_III_86", {}, "fast"),
    ("/api/decisions/bge_BGE_140_III_86/export.pdf", {}, "heavy"),
    ("/api/decisions", {"q": "x"}, "heavy"),
    ("/api/erwaegung/bge_BGE_140_III_86/2.1", {}, "fast"),
    ("/api/regeste/bge_BGE_140_III_86", {}, "fast"),
    ("/api/structure/bge_BGE_140_III_86", {}, "fast"),
    ("/api/courts", {}, "fast"),
    ("/api/lookup", {"q": "BGE 140 III 86", "exact": "true"}, "fast"),
    ("/api/lookup", {"q": "BGE 140 III 86", "exact": "1"}, "fast"),
    ("/api/lookup", {"q": "BGE 140 III 86"}, "heavy"),
    ("/api/lookup", {"q": "BGE 140 III 86", "exact": "false"}, "heavy"),
    ("/api/tool/get_law", {}, "fast"),
    ("/api/tool/search_decisions", {}, "heavy"),
    ("/api/leading-cases", {}, "heavy"),
    ("/api/billing/webhook", {}, None),          # Stripe must never get a 503
    ("/api/billing/validate", {}, None),
    ("/api/openapi-v3.json", {}, None),
    ("/api/tool", {}, None),
    ("/api/integrity/bge_BGE_140_III_86", {}, None),
    ("/api/scraper-health", {}, None),
    ("/api/quota/usage", {}, None),
])
def test_rest_lanes(path, query, lane):
    assert m._lane_for_rest(path, query) == lane


# ── MCP dispatch ────────────────────────────────────────────────────────────

def test_mcp_heavy_call_is_refused_fast_when_busy(lanes, monkeypatch):
    monkeypatch.setattr(m, "_heavy_admit", lambda client_key=None: False)

    async def _must_not_run(name, arguments):
        raise AssertionError("a refused call must not be dispatched")

    monkeypatch.setattr(m, "_handle_call_tool_inner", _must_not_run)

    async def _timed():
        # Timed inside the loop: asyncio.run's own setup is not the refusal.
        t0 = time.monotonic()
        res = await m._dispatch_with_timeout("search_decisions", {"query": "Mietrecht"})
        return res, time.monotonic() - t0

    result, elapsed = asyncio.run(_timed())
    assert elapsed < 0.05
    payload = json.loads(result[0].text)
    assert payload["error"] == "server_busy"
    assert payload["retry_after_seconds"] == m.BUSY_RETRY_AFTER_S
    assert "get_decision" in payload["message"]   # tells the model what still works
    assert lanes.snapshot()["heavy"]["rejected"] == 1


def test_mcp_fast_call_is_not_refused_when_heavy_is_busy(lanes, monkeypatch):
    monkeypatch.setattr(m, "_heavy_admit", lambda client_key=None: False)
    sentinel = [m.TextContent(type="text", text="law text")]
    seen = {}

    async def _inner(name, arguments):
        seen["lane"] = m._ctx_lane.get()
        seen["deadline"] = m._ctx_request_deadline.get()
        return sentinel

    monkeypatch.setattr(m, "_handle_call_tool_inner", _inner)
    assert asyncio.run(m._dispatch_with_timeout("get_law", {"abbreviation": "OR"})) is sentinel
    assert seen["lane"] == "fast"
    assert seen["deadline"] is not None


def test_mcp_heavy_timeout_is_the_lower_of_heavy_and_global(lanes, monkeypatch):
    monkeypatch.setattr(m, "HEAVY_TIMEOUT_S", 0.1)
    monkeypatch.setattr(m, "TOOL_DISPATCH_TIMEOUT_S", 5)

    async def _slow(name, arguments):
        await asyncio.sleep(2)

    monkeypatch.setattr(m, "_handle_call_tool_inner", _slow)
    t0 = time.monotonic()
    payload = json.loads(asyncio.run(
        m._dispatch_with_timeout("search_decisions", {"query": "x"}))[0].text)
    assert time.monotonic() - t0 < 1
    assert payload["error"] == "server_timeout"
    assert payload["timeout_seconds"] == 0.1
    assert payload["retry_after_seconds"] == m.BUSY_RETRY_AFTER_S

    monkeypatch.setattr(m, "TOOL_DISPATCH_TIMEOUT_S", 0.05)
    payload = json.loads(asyncio.run(
        m._dispatch_with_timeout("search_decisions", {"query": "x"}))[0].text)
    assert payload["timeout_seconds"] == 0.05


def test_mcp_fast_timeout_stays_the_global_one(lanes, monkeypatch):
    monkeypatch.setattr(m, "HEAVY_TIMEOUT_S", 0.01)
    monkeypatch.setattr(m, "TOOL_DISPATCH_TIMEOUT_S", 0.1)

    async def _slow(name, arguments):
        await asyncio.sleep(2)

    monkeypatch.setattr(m, "_handle_call_tool_inner", _slow)
    payload = json.loads(asyncio.run(
        m._dispatch_with_timeout("get_law", {"abbreviation": "OR"}))[0].text)
    assert payload["timeout_seconds"] == 0.1


def test_expired_lane_work_surfaces_as_timeout(lanes, monkeypatch):
    async def _expired(name, arguments):
        raise m._LaneDeadlineExpired("queued past its deadline")

    monkeypatch.setattr(m, "_handle_call_tool_inner", _expired)
    payload = json.loads(asyncio.run(
        m._dispatch_with_timeout("search_decisions", {"query": "x"}))[0].text)
    assert payload["error"] == "server_timeout"


def test_inner_dispatch_does_not_swallow_expired_lane_work(monkeypatch):
    def _raise(*a, **k):
        raise m._LaneDeadlineExpired("queued past its deadline")

    monkeypatch.setattr(m, "get_decision_by_id", _raise)
    with pytest.raises(m._LaneDeadlineExpired):
        asyncio.run(m._handle_call_tool_inner("get_decision", {"decision_id": "bge_x"}))


def test_lanes_off_restores_the_single_timeout(lanes, monkeypatch):
    monkeypatch.setattr(m, "OCL_LANES", False)
    monkeypatch.setattr(m, "_heavy_admit", lambda client_key=None: False)
    monkeypatch.setattr(m, "HEAVY_TIMEOUT_S", 0.01)
    monkeypatch.setattr(m, "TOOL_DISPATCH_TIMEOUT_S", 0.1)
    seen = {}

    async def _slow(name, arguments):
        seen["lane"] = m._ctx_lane.get()
        seen["deadline"] = m._ctx_request_deadline.get()
        await asyncio.sleep(2)

    monkeypatch.setattr(m, "_handle_call_tool_inner", _slow)
    payload = json.loads(asyncio.run(
        m._dispatch_with_timeout("search_decisions", {"query": "x"}))[0].text)
    assert payload["error"] == "server_timeout"      # not refused
    assert payload["timeout_seconds"] == 0.1         # global, not heavy
    assert seen == {"lane": None, "deadline": None}  # search deadline untouched


# ── search deadline ─────────────────────────────────────────────────────────

def test_search_deadline_keeps_its_own_budget_capped_by_the_request(monkeypatch):
    monkeypatch.setattr(m, "SEARCH_DEADLINE_MS", 20000)
    t0 = 1000.0
    assert m._search_deadline(t0) == 1020.0                      # no request deadline
    tok = m._ctx_request_deadline.set(1005.0)
    try:
        assert m._search_deadline(t0) == 1005.0                  # request ends sooner
    finally:
        m._ctx_request_deadline.reset(tok)
    tok = m._ctx_request_deadline.set(1060.0)
    try:
        assert m._search_deadline(t0) == 1020.0                  # own budget unchanged
    finally:
        m._ctx_request_deadline.reset(tok)


# ── REST ────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def rest_app():
    import uvicorn
    captured = {}
    real_run = uvicorn.run
    uvicorn.run = lambda app, **kwargs: captured.setdefault("app", app)
    try:
        m.main_remote("127.0.0.1", 0)
    finally:
        uvicorn.run = real_run
    return captured["app"]


def test_rest_heavy_request_gets_503_with_retry_after_when_busy(rest_app, lanes, monkeypatch):
    from starlette.testclient import TestClient
    monkeypatch.setattr(m, "_heavy_admit", lambda client_key=None: False)

    def _must_not_run(*a, **k):
        raise AssertionError("a refused request must not search")

    monkeypatch.setattr(m, "search_fts5", _must_not_run)
    r = TestClient(rest_app).get("/api/decisions", params={"query": "Mietrecht"})
    assert r.status_code == 503
    assert r.headers["retry-after"] == str(m.BUSY_RETRY_AFTER_S)
    assert r.json()["error"] == "server_busy"


def test_rest_exempt_route_is_never_refused(rest_app, lanes, monkeypatch):
    from starlette.testclient import TestClient
    monkeypatch.setattr(m, "_heavy_admit", lambda client_key=None: False)
    r = TestClient(rest_app).get("/api/openapi-v3.json")
    assert r.status_code == 200


def test_rest_expired_lane_work_becomes_503(rest_app, lanes, monkeypatch):
    from starlette.testclient import TestClient

    def _expired(*a, **k):
        raise m._LaneDeadlineExpired("queued past its deadline")

    monkeypatch.setattr(m, "search_fts5", _expired)
    r = TestClient(rest_app).get("/api/decisions", params={"query": "Mietrecht"})
    assert r.status_code == 503
    assert r.headers["retry-after"] == str(m.BUSY_RETRY_AFTER_S)


def test_rest_per_client_count_is_released_even_when_the_handler_fails(rest_app, lanes, monkeypatch):
    from starlette.testclient import TestClient
    seen = {}

    def _boom(*a, **k):
        seen["inflight"] = dict(m._heavy_client_inflight)
        seen["lane"] = m._ctx_lane.get()
        raise RuntimeError("boom")

    monkeypatch.setattr(m, "search_fts5", _boom)
    r = TestClient(rest_app, raise_server_exceptions=False).get(
        "/api/decisions", params={"query": "Mietrecht"})
    assert r.status_code == 500
    assert sum(seen["inflight"].values()) == 1       # counted while running
    assert seen["lane"] == "heavy"
    assert sum(m._heavy_client_inflight.values()) == 0


# ── metrics ─────────────────────────────────────────────────────────────────

def test_metrics_expose_the_lanes(lanes):
    snap = m._get_metrics()["lanes"]
    assert snap["enabled"] is True
    assert "installed" in snap
    for lane in ("fast", "heavy", "other"):
        for key in ("threads", "queued", "running", "completed", "expired",
                    "cancelled", "rejected", "oldest_wait_s"):
            assert key in snap[lane], (lane, key)
