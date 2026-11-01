"""The closed set of names a call may be counted under in the metrics.

The REST middleware takes a tool name from the URL path and the MCP dispatch
takes it from the client, so both are strings anyone can choose. Counted as
they arrive they reach /metrics, metrics.db and daily_metrics.jsonl: by
2026-11-01 that file held 1,174 distinct "tools", most of them scanner paths
and SQL injection strings, and one request to /api/email aborted the nightly
dev-data export, because `email` is an identifier key to
scripts/collect_dev_data.py. A name nobody controls can also carry an
identifier itself, so anything off this list is counted as UNKNOWN_TOOL and the
name is not kept. capture_*.jsonl keeps the raw tool and path for the cases
where the name matters.

The set lives here and not in mcp_server because the collector needs it too
and must not import the server. tests/test_metric_tools.py pins it to
_list_tools() and the REST routes, so a new tool or route fails there until it
is listed.
"""
from __future__ import annotations

UNKNOWN_TOOL = "_unknown"

_WORD_ADDIN_PREFIX = "word-addin:"

METRIC_TOOLS = frozenset({
    # MCP tools (_list_tools)
    "analyze_legal_trend", "attest_response", "browse_legislation_changes",
    "check_claim_support", "check_update_status", "cite",
    "draft_mock_decision", "fetch", "find_appeal_chain", "find_citations",
    "find_leading_cases", "find_relevant_erwaegung",
    "find_scholarship_citing_decision", "find_scholarship_citing_statute",
    "generate_exam_question", "get_article_history", "get_article_purpose",
    "get_case_brief", "get_commentary", "get_decision",
    "get_decision_structure", "get_decisions", "get_doctrine",
    "get_erwaegung", "get_law", "get_legislation", "get_materialien",
    "get_practice", "get_regeste", "get_scholarship",
    "get_scholarship_full_text", "get_statistics", "list_courts",
    "list_scholarship_sources", "search", "search_botschaft",
    "search_commentaries", "search_decisions", "search_laws",
    "search_legislation", "search_materialien", "search_practice",
    "search_scholarship", "update_database",
    # REST routes, by first path segment (_classify_rest_metric)
    "amendment-ref", "appeal-chain", "article-history", "article-purpose",
    "atom", "attest", "billing", "case-brief", "citations", "commentaries",
    "courts", "doctrine", "erwaegung", "exam-question", "export_decision",
    "integrity", "laws", "leading-cases", "legislation", "lookup",
    "materialien", "mock-decision", "openapi-v3.json", "openapi.copilot.json",
    "quota", "regeste", "relevant-erwaegung", "research", "scholarship",
    "scholarship-fulltext", "scraper-health", "search-botschaft",
    "statistics", "structure", "suggestions", "tool", "trends", "verify-claim",
    # Not a tool, recorded by the /health handler
    "health",
})


def metric_tool_name(name: str) -> str:
    """The name a call is counted under: `name` itself if it is a known tool
    (with or without the Word add-in's prefix), otherwise UNKNOWN_TOOL."""
    return name if name.removeprefix(_WORD_ADDIN_PREFIX) in METRIC_TOOLS \
        else UNKNOWN_TOOL
