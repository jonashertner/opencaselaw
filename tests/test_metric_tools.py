"""Only known tool names are counted, and every real tool is known.

A REST tool name comes from the URL and an MCP one from the client. Counted
as-is, 2026-11-01 ended with 1,174 "tools" in daily_metrics.jsonl and one
request to /api/email stopped the dev-data export. The pins below run both ways: a
name nobody controls never reaches the metrics, and adding a tool or a route
fails here until metric_tools lists it, so real traffic cannot quietly land
in `_unknown`.
"""
from __future__ import annotations

import collections
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import mcp_server as m  # noqa: E402
from metric_tools import METRIC_TOOLS, UNKNOWN_TOOL, metric_tool_name  # noqa: E402
from scripts.collect_dev_data import _scan  # noqa: E402


@pytest.fixture
def metrics(monkeypatch):
    """Fresh counters, so the shared module state neither leaks in nor out."""
    for key in ("tool_calls", "tool_errors"):
        monkeypatch.setitem(m._metrics, key, collections.Counter())
    monkeypatch.setitem(m._metrics, "tool_latency_ms",
                        collections.defaultdict(list))
    for key in ("tool_outcomes", "outcome_sources", "empty_reasons"):
        monkeypatch.setitem(m._metrics, key,
                            collections.defaultdict(collections.Counter))
    return m._metrics


def _rest_route_prefixes() -> set[str]:
    # rest_api is built inside main_remote and cannot be imported, so the
    # routes are read from the source, as test_query_length_caps does.
    source = (REPO / "mcp_server.py").read_text(encoding="utf-8")
    return set(re.findall(r'@rest_api\.\w+\(\s*"/([^/"{]+)', source))


def test_every_mcp_tool_is_known():
    assert {t.name for t in m._list_tools()} <= METRIC_TOOLS


def test_every_tool_alias_is_known():
    """The dispatch counts an outcome before it resolves the alias."""
    assert set(m._TOOL_NAME_ALIASES) <= METRIC_TOOLS


def test_every_rest_route_is_known():
    prefixes = _rest_route_prefixes()
    assert {"laws", "billing", "tool"} <= prefixes, "route pattern went stale"
    for prefix in prefixes:
        for path in (f"/api/{prefix}", f"/api/{prefix}/x"):
            assert m._classify_rest_metric(path, 200)[0] in METRIC_TOOLS, path


def test_export_routes_are_known():
    assert m._classify_rest_metric("/api/decisions/x/export.pdf", 200)[0] \
        in METRIC_TOOLS


def test_word_addin_names_stay_known():
    assert metric_tool_name("word-addin:laws") == "word-addin:laws"
    assert metric_tool_name("word-addin:email") == UNKNOWN_TOOL


@pytest.mark.parametrize("name", [
    "email", "ip", "user_agent", "", "*", ".aws", "%2eenv", "phpinfo.php",
    "get_law_article", "1' AND 1=1 UNION SELECT NULL-- -",
    "someone@example.org", "203.0.113.7", "LAWS",
])
def test_anything_else_is_unknown(name):
    assert metric_tool_name(name) == UNKNOWN_TOOL


def test_a_probe_is_counted_but_its_name_is_not_kept(metrics):
    tool, err = m._classify_rest_metric("/api/email", 404)
    m._record_tool_call(tool, 0.5, error=err)
    m._record_tool_outcome(tool, "empty")
    m._record_outcome_source(tool, "status")
    m._record_empty_reason(tool, "http_404")
    for counter in ("tool_calls", "tool_latency_ms", "tool_outcomes",
                    "outcome_sources", "empty_reasons"):
        assert set(metrics[counter]) == {UNKNOWN_TOOL}, counter
    stats = m._get_metrics()["tools"]
    assert set(stats) == {UNKNOWN_TOOL}
    assert stats[UNKNOWN_TOOL]["calls"] == 1
    assert stats[UNKNOWN_TOOL]["outcome_from_status"] == 1


def test_probes_cannot_grow_the_metrics(metrics):
    for i in range(50):
        m._record_tool_call(f"probe-{i}", 1.0)
    assert metrics["tool_calls"] == {UNKNOWN_TOOL: 50}


def test_a_known_tool_is_counted_under_its_own_name(metrics):
    m._record_tool_call("cite", 1.0)
    m._record_tool_outcome("cite", "substantive")
    assert m._get_metrics()["tools"]["cite"]["substantive"] == 1
    assert UNKNOWN_TOOL not in metrics["tool_calls"]


def test_an_unknown_mcp_tool_does_not_break_the_followup_tracking(metrics, monkeypatch):
    monkeypatch.setitem(m._metrics, "last_tool_was_search", True)
    m._record_tool_call("get_law_article", 1.0)
    assert m._metrics["last_tool_was_search"] is False


def test_the_snapshot_passes_the_collector_scan(metrics):
    """What the server writes after a probe is what the collector accepts,
    with no help from its fold."""
    for name in ("email", "ip", "session_id", "cite"):
        m._record_tool_call(name, 1.0)
    assert _scan(m._get_metrics()["tools"]) == []
