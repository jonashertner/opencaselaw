"""Fake scrapers for tests/test_run_scraper_gap_cache.py (registered via monkeypatch)."""
from __future__ import annotations

from datetime import datetime, timezone

from base_scraper import BaseScraper
from models import Decision, make_decision_id

STUBS = [
    {"docket_number": "A-1", "url": "https://example.invalid/a1"},   # fetch succeeds
    {"docket_number": "A-2", "url": "https://example.invalid/a2"},   # fetch returns None
]


class _Base(BaseScraper):
    def discover_new(self, since_date=None):
        for st in STUBS:
            if not self.state.is_known(make_decision_id(self.court_code, st["docket_number"])):
                yield dict(st)

    def fetch_decision(self, stub):
        if stub["docket_number"] == "A-2":
            return None
        return Decision(
            decision_id=make_decision_id(self.court_code, stub["docket_number"]),
            court=self.court_code, canton="CH", docket_number=stub["docket_number"],
            decision_date=None, language="de", full_text="x" * 200,
            source_url=stub["url"], scraped_at=datetime.now(timezone.utc),
        )


class GapCachingScraper(_Base):
    CACHE_NONE_AS_GAP = True

    @property
    def court_code(self) -> str:
        return "fakegap"


class PlainScraper(_Base):
    @property
    def court_code(self) -> str:
        return "fakeplain"
