"""vs_gerichte health gap counts decisions, not documents (2026-09-15).

api-justsearch.vs.ch lists documents: a case number carries the original PDF plus
ZWR/RVJ journal copies (``is_zwr``) or a second ruling (district court and cantonal
court under one number). Ids are keyed by case number, so ``portal_count`` must be the
number of distinct ids or the nightly gap is a phantom (5,006 documents under 4,606
case numbers reported as "397 missing" for weeks).
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scrapers.cantonal.vs_gerichte import VSGerichteScraper  # noqa: E402


def _item(doc_id: str, case: str, date_: str, *, zwr: bool = False, tribunal: str = "TCVS") -> dict:
    return {
        "id": doc_id,
        "case_number": {"id": doc_id, "text": case},
        "date_decision": date_,
        "date_publication": date_,
        "language": {"id": "fr"},
        "tribunal": {"text": "Tribunal cantonal", "abbreviation": tribunal},
        "case_instance": {"text": "2e instance"},
        "legal_nature": {"text": "civil"},
        "file_name": f"{tribunal}-{case}.pdf",
        "is_zwr": zwr,
    }


PAGES = {
    0: [
        _item("d1", "C1 25 26", "2025-11-10"),
        _item("d2", "C1 25 26", "2025-11-10", zwr=True),            # journal copy
        _item("d3", "P1 25 12", "2025-11-26"),
    ],
    3: [
        _item("d4", "A1 25 1", "2025-09-01"),
        _item("d5", "P1 25 12", "2025-08-18", tribunal="TDENT"),    # first-instance ruling
    ],
}


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def _fake_get(url, params=None, **kw):
    results = PAGES.get(params["offset"], [])
    return _Resp({"count": 5, "results": results})


def test_portal_count_is_distinct_ids_after_a_complete_walk(tmp_path, monkeypatch):
    s = VSGerichteScraper(state_dir=tmp_path)
    monkeypatch.setattr(s, "get", _fake_get)
    stubs = list(s.discover_new())
    assert len(stubs) == 5                      # discovery behaviour unchanged
    assert s.portal_count == 3                  # C1 25 26, P1 25 12, A1 25 1


def test_incomplete_walk_keeps_the_document_total(tmp_path, monkeypatch):
    s = VSGerichteScraper(state_dir=tmp_path)
    monkeypatch.setattr(s, "get", _fake_get)
    # since_date stops the walk on the first page: no distinct-id claim is made.
    list(s.discover_new(since_date="2025-11-20"))
    assert s.portal_count == 5
