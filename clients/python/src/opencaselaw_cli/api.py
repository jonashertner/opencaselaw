"""Programmatic access for scripts and agents that prefer code over a shell:
the same operations the commands run, returning plain dicts.

    from opencaselaw_cli import api
    rows = api.resolve([{"reference": "BGE 136 III 513 E. 2.3"}])
    api.check_quotes([{"reference": "BGE 136 III 513", "pinpoint": "2.3", "quote": "..."}])
    api.tool("find_leading_cases", query="Rachekündigung")

Every function accepts ``client=`` (a :class:`Client`) and otherwise builds one
from the environment (OCL_BASE_URL, OCL_CACHE, ...). Nothing here rewrites what
the service returns.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from .client import APIError, Client
from .cli import build_parser, create_client, run as _run_args
from . import workflows

__all__ = ["APIError", "Client", "run", "resolve", "check_quotes", "identify", "passage", "search", "tool", "tools"]


def _client(client):
    if client is not None:
        return client
    return create_client(build_parser().parse_args(["doctor"]))


def run(argv: list[str], client: Client | None = None) -> tuple[dict, int]:
    """Run any command line, e.g. run(["decisions", "search", "Rachekündigung", "--max-results", "5"])."""
    args = build_parser().parse_args(argv)
    return _run_args(args, client or create_client(args))


def resolve(rows: list[dict], *, language: str = "de", jobs: int = 4, client: Client | None = None) -> list[dict]:
    """Citation check: rows of {"reference": ..., "pinpoint": ?, "quote": ?}; returns one result row each."""
    client = _client(client)
    with ThreadPoolExecutor(max_workers=max(1, min(int(jobs), 8))) as pool:
        return list(pool.map(lambda row: workflows._resolve_one(client, dict(row), language), rows))


def check_quotes(rows: list[dict], *, language: str = "de", jobs: int = 4, client: Client | None = None) -> list[dict]:
    """Quotation check: rows of {"reference": ..., "pinpoint": ?, "quote": ...}."""
    client = _client(client)
    with ThreadPoolExecutor(max_workers=max(1, min(int(jobs), 8))) as pool:
        return list(pool.map(lambda row: workflows.check_one_quote(client, dict(row), language), rows))


def identify(reference: str, *, language: str = "de", client: Client | None = None) -> dict:
    """The resolved row for one reference (decision_id, identity_check, ...); ResolutionError if not exactly one."""
    return workflows.identify_row(_client(client), reference, language)


def passage(decision_id: str, number: str, *, client: Client | None = None) -> dict:
    """One Erwägung: the served passage plus text_plain; raises APIError when not there."""
    served, status, error = workflows.fetch_passage(_client(client), decision_id, workflows.normalise_pinpoint(number) or number)
    if served is None:
        raise error or APIError(200, f"E. {number} not available")
    served["pinpoint_status"] = status
    return served


def search(query: str | None = None, *, max_results: int = 20, client: Client | None = None, **filters) -> dict:
    """Bounded search; filters: court, canton, language, date_from, date_to, published_from, published_to, sort."""
    from .cli import search as _search
    params = {"query": query, **{k: v for k, v in filters.items() if v is not None}, "fields": "compact", "include_pinpoint": False}
    result, _ = _search(_client(client), params, max_results, 50)
    return result


def tools(*, client: Client | None = None) -> list[dict]:
    """Every server tool with its schemas."""
    return _client(client).mcp_tools()


def tool(name: str, *, client: Client | None = None, **arguments) -> dict:
    """Call any server tool and return its own dict (fields, not Markdown); a tool-reported error raises APIError(200)."""
    value = _client(client).tool_json(name, arguments)
    if value.get("_is_error"):
        error = APIError(200, str(value.get("error") or f"{name} reported an error"))
        error.response = value
        raise error
    return value
