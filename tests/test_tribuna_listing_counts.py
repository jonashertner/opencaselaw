"""Tribuna health gap counts dockets, not listing rows (2026-09-15).

Read-only listing probes on 2026-09-15: GR 14,903 rows → 14,852 distinct dockets, 41
duplicate rows, 0 unknown ids ("gap 50" for weeks); BE VG 15 and FR 9 the same class.
A docket listed twice can never become a second decision (ids are keyed by docket), so
duplicate rows leave portal_count. Rows the parser cannot anchor on a docket stay in
the gap and are logged.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scrapers.cantonal.be_verwaltungsgericht import BEVerwaltungsgerichtScraper  # noqa: E402
from scrapers.cantonal.fr_gerichte import FRGerichteScraper  # noqa: E402


def _stub(docket: str) -> dict:
    return {"docket_number": docket, "decision_date": "2026-01-01", "enc_path": "ab" * 40, "title": "t"}


class _Resp:
    text = "//OK"


def _single_pass(monkeypatch, tmp_path, pages, fail_page=None):
    s = FRGerichteScraper(state_dir=tmp_path)
    s.PAGE_SIZE = 3
    monkeypatch.setattr(s, "_init_session", lambda: "cred")
    monkeypatch.setattr(s, "_build_search_body", lambda *a, **k: b"")
    calls = {"n": 0}

    def _post(*a, **k):
        if fail_page is not None and calls["n"] == fail_page:
            raise ConnectionError("boom")
        return _Resp()

    def _parse(text):
        page = calls["n"]
        calls["n"] += 1
        return pages[page]

    monkeypatch.setattr(s, "post", _post)
    monkeypatch.setattr(s, "_parse_search_response", _parse)
    return s


def test_duplicate_rows_leave_portal_count_after_a_complete_walk(monkeypatch, tmp_path):
    pages = [
        (5, [_stub("601 2026 1"), _stub("601 2026 1"), _stub("601 2026 2")]),   # docket listed twice
        (5, [_stub("601 2026 3"), _stub("601 2026 4")]),
    ]
    s = _single_pass(monkeypatch, tmp_path, pages)
    stubs = list(s.discover_new())
    assert [x["docket_number"] for x in stubs] == ["601 2026 1", "601 2026 1", "601 2026 2", "601 2026 3", "601 2026 4"]
    assert s.portal_count == 4                      # 5 rows, 1 duplicate row


def test_unparsed_rows_stay_in_the_gap(monkeypatch, tmp_path):
    pages = [
        (6, [_stub("601 2026 1"), _stub("601 2026 2"), _stub("601 2026 3")]),
        (6, [_stub("601 2026 4")]),                 # the server said 6, the parser found 4
        (6, []),
    ]
    s = _single_pass(monkeypatch, tmp_path, pages)
    list(s.discover_new())
    assert s.portal_count == 6                      # 2 rows without a docket remain a gap


def test_incomplete_walk_still_drops_observed_duplicates_only(monkeypatch, tmp_path):
    pages = [
        (9, [_stub("601 2026 1"), _stub("601 2026 1"), _stub("601 2026 2")]),
        (9, []),
    ]
    s = _single_pass(monkeypatch, tmp_path, pages, fail_page=1)
    list(s.discover_new())
    assert s.portal_count == 8                      # 9 − 1 observed duplicate; the rest is unknown


def test_windowed_walk_counts_duplicates_across_leaf_windows(monkeypatch, tmp_path):
    s = BEVerwaltungsgerichtScraper(state_dir=tmp_path)
    assert s.DATE_WINDOW_FIELD is not None
    s.DATE_WINDOW_START_YEAR = 2026
    monkeypatch.setattr(s, "_init_session", lambda: "cred")

    def _collect(credential, court_filter, value):
        if value == "2026":
            return 4, [_stub("100 2026 1"), _stub("100 2026 1"), _stub("100 2026 2"), _stub("100 2026 3")]
        return 0, []

    monkeypatch.setattr(s, "_collect_window", _collect)
    stubs = list(s.discover_new())
    assert sorted(x["docket_number"] for x in stubs) == ["100 2026 1", "100 2026 2", "100 2026 3"]
    assert s.portal_count == 3                      # 4 rows, 1 duplicate row
