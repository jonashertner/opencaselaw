"""REST outcomes must be declared by the route, not guessed from status.

Measured 2026-08-18: the global answered rate read 99.4%, but REST is
~84% of calls and was labelled purely by HTTP status. A 200 carrying an
empty list is indistinguishable from a 200 carrying results, so every
empty search scored as answered. An instrument that flatters you is
worse than no instrument, and the search-quality work cannot be judged
without fixing it first.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import mcp_server as m  # noqa: E402


class _Resp:
    def __init__(self, headers=None):
        self.headers = dict(headers or {})


def test_mark_outcome_sets_the_header():
    r = m._mark_outcome(_Resp(), "empty", "no_fts_match")
    assert r.headers["X-OCL-Outcome"] == "empty"
    assert r.headers["X-OCL-Empty-Reason"] == "no_fts_match"


def test_mark_outcome_without_reason():
    r = m._mark_outcome(_Resp(), "substantive")
    assert r.headers["X-OCL-Outcome"] == "substantive"
    assert "X-OCL-Empty-Reason" not in r.headers


def test_mark_outcome_returns_the_response():
    """It must be usable inline: return _mark_outcome(JSONResponse(...), …)."""
    resp = _Resp()
    assert m._mark_outcome(resp, "empty") is resp


def test_reason_is_bounded():
    r = m._mark_outcome(_Resp(), "empty", "x" * 200)
    assert len(r.headers["X-OCL-Empty-Reason"]) <= 48


def test_mark_outcome_never_raises():
    """Telemetry must not be able to fail a request that already worked."""
    class Hostile:
        @property
        def headers(self):
            raise RuntimeError("no headers here")
    h = Hostile()
    assert m._mark_outcome(h, "empty", "boom") is h


def test_counters_record_source_and_reason():
    before_src = dict(m._metrics["outcome_sources"]["cite"])
    m._record_outcome_source("cite", "declared")
    m._record_outcome_source("cite", "status")
    m._record_empty_reason("cite", "filters_excluded_all")
    src = m._metrics["outcome_sources"]["cite"]
    assert src["declared"] == before_src.get("declared", 0) + 1
    assert src["status"] == before_src.get("status", 0) + 1
    assert m._metrics["empty_reasons"]["cite"]["filters_excluded_all"] >= 1


def test_recorders_never_raise():
    m._record_outcome_source("t2", None)      # type: ignore[arg-type]
    m._record_empty_reason("t2", None)        # type: ignore[arg-type]


def test_status_fallback_counts_rejections_as_unanswered():
    """A route that has not declared its outcome falls back to the status.
    422 was counted as answered until 2026-08-19, which flattered exactly
    the calls whose parameters need better documentation: the caller got
    no law, so it is empty."""
    for status, expected in ((204, "empty"), (400, "empty"), (404, "empty"),
                             (410, "empty"), (422, "empty"),
                             (200, "substantive"), (201, "substantive"),
                             (206, "substantive"), (304, "substantive")):
        got = "empty" if status in m._EMPTY_STATUSES else "substantive"
        assert got == expected, status


def test_metrics_expose_provenance():
    """A reader must be able to tell measured from assumed."""
    m._record_tool_call("cite", 1.0, error=False)
    m._record_tool_outcome("cite", "substantive")
    m._record_outcome_source("cite", "status")
    stats = m._get_metrics()["tools"]["cite"]
    for key in ("substantive", "empty", "outcome_declared",
                "outcome_from_status", "empty_reasons"):
        assert key in stats, key
    assert stats["outcome_from_status"] >= 1
