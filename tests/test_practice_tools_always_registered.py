"""Practice tools are registered regardless of LEXFIND_ENABLED (integration evaluation 2026-07).

search_practice/get_practice read only the local practice.db yet sat inside
the LEXFIND_ENABLED conditional in _list_tools — one unrelated env flag
silently removed the entire 790-document Verwaltungspraxis corpus from
discovery. Dispatch never checked the flag, so the tools worked if called by
name; no client could find them.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import mcp_server as m  # noqa: E402

LEGISLATION_TRIO = {"search_legislation", "get_legislation", "browse_legislation_changes"}


def _names(monkeypatch, flag):
    monkeypatch.setattr(m, "LEXFIND_ENABLED", flag)
    return {t.name for t in m._list_tools()}


def test_practice_tools_present_with_lexfind_disabled(monkeypatch):
    names = _names(monkeypatch, False)
    assert "search_practice" in names
    assert "get_practice" in names


def test_legislation_trio_still_gated(monkeypatch):
    names = _names(monkeypatch, False)
    assert not (LEGISLATION_TRIO & names), LEGISLATION_TRIO & names


def test_all_five_present_when_enabled(monkeypatch):
    names = _names(monkeypatch, True)
    assert {"search_practice", "get_practice"} | LEGISLATION_TRIO <= names
