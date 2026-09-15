"""ComCom: two-year range pages are discovered from /de/entscheide, not only the static
list (2026-09-14). Offline: golden anchor list of the index page plus a synthetic
2026-2027 entry."""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scrapers.comcom import DATE_RANGES, ComComScraper  # noqa: E402

INDEX = (REPO / "tests" / "fixtures" / "comcom_entscheide_index_20260914.html").read_text(encoding="utf-8")


class _R:
    def __init__(self, text):
        self.text = text


def test_ranges_union_static_and_index(monkeypatch, tmp_path):
    s = ComComScraper(state_dir=tmp_path)
    monkeypatch.setattr(s, "get", lambda url, **k: _R(INDEX if url.endswith("/de/entscheide") else "<html></html>"))
    ranges = s._date_ranges()
    assert ranges[0] == (2026, 2027)                      # the new page comes first
    assert set(DATE_RANGES) <= set(ranges)                 # nothing from the static list is lost
    assert ranges == sorted(set(ranges), reverse=True)


def test_index_failure_falls_back_to_static(monkeypatch, tmp_path):
    s = ComComScraper(state_dir=tmp_path)

    def boom(url, **k):
        raise RuntimeError("503")

    monkeypatch.setattr(s, "get", boom)
    assert s._date_ranges() == sorted(set(DATE_RANGES), reverse=True)
