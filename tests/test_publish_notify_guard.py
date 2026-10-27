"""publish._notify must never reach ntfy.sh from the test suite."""
from __future__ import annotations

import publish


def _record_urlopen(monkeypatch) -> list:
    calls: list = []
    monkeypatch.setattr(publish.urllib.request, "urlopen",
                        lambda req, timeout=None: calls.append(req))
    return calls


def test_notify_is_silent_under_pytest(monkeypatch):
    calls = _record_urlopen(monkeypatch)
    publish._notify("Publish OK", "? decisions, 0 min")
    assert calls == []


def test_notify_posts_outside_pytest(monkeypatch):
    calls = _record_urlopen(monkeypatch)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    publish._notify("Publish OK", "991000 decisions, 840 min", priority="high")
    assert len(calls) == 1
    req = calls[0]
    assert req.full_url == f"https://ntfy.sh/{publish.NTFY_TOPIC}"
    assert req.data == b"991000 decisions, 840 min"
    assert req.get_header("Title") == "Publish OK"
    assert req.get_header("Priority") == "high"
