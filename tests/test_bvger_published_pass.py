"""BVGer: the publication-date pass finds old rulings published late (offline).

C-5904/2013 was decided 2015-11-27 and published 2026-10-20. The daily
ruling-date scan walks back from today and stops after two empty years, so it
never saw it (entscheidsuche parity audit, 2026-11-08)."""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scrapers import bvger  # noqa: E402


def _doc(docket: str, ruling: str, published: str) -> dict:
    return {
        "leid": f"leid-{docket}",
        "metadataKeywordTextMap": {"title": [f"BVGer {docket};;TAF {docket}"],
                                   "language": ["de"]},
        "metadataDateMap": {"rulingDate": f"{ruling}T02:00:00.000Z",
                            "publicationDate": f"{published}T02:00:00.000Z"},
    }


class FakeSearch:
    """Stands in for the Weblaw API: filters a fixed corpus by date field."""

    def __init__(self, docs, cap=100):
        self.docs, self.cap, self.calls = docs, cap, []

    def __call__(self, ab, bis, offset=0, language=None, date_field="rulingDate"):
        self.calls.append((ab, bis, offset, language, date_field))
        hit = [d for d in self.docs
               if ab.isoformat() <= d["metadataDateMap"][date_field][:10] <= bis.isoformat()
               and (language is None
                    or d["metadataKeywordTextMap"]["language"][0] == language)]
        page = hit[offset:offset + self.cap]
        return page, len(hit), offset + len(page) < len(hit)


def _scraper(tmp_path, docs, known=()):
    s = bvger.BVGerScraper(state_dir=tmp_path)
    for d in known:
        s.state.mark_scraped(d)
    s._wl_search = FakeSearch(docs)
    return s


def test_late_published_old_ruling_is_found(tmp_path):
    today = date.today()
    recent = (today - timedelta(days=19)).isoformat()
    docs = [
        _doc("C-5904/2013", "2015-11-27", recent),            # the late one
        _doc("E-1/2026", (today - timedelta(days=20)).isoformat(), recent),
        _doc("A-9/2010", "2010-01-01", "2010-02-01"),          # published long ago
    ]
    s = _scraper(tmp_path, docs, known=["bvger_E-1_2026"])
    got = [st["docket_number"] for st in s._discover_weblaw_published()]
    assert got == ["C-5904/2013"]
    assert all(c[4] == "publicationDate" for c in s._wl_search.calls)
    stub = next(s._discover_weblaw_published())
    assert stub["decision_date"] == "2015-11-27"


def test_busy_days_split_windows_and_languages(tmp_path):
    today = date.today()
    day = (today - timedelta(days=3)).isoformat()
    docs = [_doc(f"D-{i}/2026", day, day) for i in range(150)]
    for d in docs[100:]:
        d["metadataKeywordTextMap"]["language"] = ["fr"]
    s = _scraper(tmp_path, docs)
    got = [st["docket_number"] for st in s._discover_weblaw_published()]
    assert len(got) == len(set(got)) == 150
    assert {c[3] for c in s._wl_search.calls if c[3]} == {"de", "fr", "it"}


def test_discover_new_runs_the_published_pass_first_and_survives_its_failure(tmp_path):
    s = bvger.BVGerScraper(state_dir=tmp_path)
    order = []

    def published():
        order.append("published")
        raise RuntimeError("publicationDate filter gone")
        yield  # pragma: no cover

    def ruling(since_date=None):
        order.append("ruling")
        yield {"docket_number": "F-1/2026"}

    s._discover_weblaw_published = published
    s._discover_weblaw = ruling
    assert [st["docket_number"] for st in s.discover_new()] == ["F-1/2026"]
    assert order == ["published", "ruling"]


def test_search_body_uses_the_requested_date_field(tmp_path, monkeypatch):
    s = bvger.BVGerScraper(state_dir=tmp_path)
    sent = {}

    class Resp:
        def json(self):
            return {"documents": [], "totalNumberOfDocuments": 0, "hasMoreResults": False}

    def post(url, headers=None, data=None, **kw):
        import json
        sent.update(json.loads(data))
        return Resp()

    monkeypatch.setattr(s, "post", post)
    s._wl_search(date(2026, 11, 1), date(2026, 11, 6), date_field="publicationDate")
    assert list(sent["metadataDateMap"]) == ["publicationDate"]
    s._wl_search(date(2026, 11, 1), date(2026, 11, 6))
    assert list(sent["metadataDateMap"]) == ["rulingDate"]
