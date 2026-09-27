"""BGE discovery retries failed volume listings once, at the end of the run.

search.bger.ch started dropping a few listing requests per run on 2026-09-26.
A transient drop must not reach the log as "Failed to fetch listing ... Max
retries exceeded" — run_all_scrapers counts those lines as discovery failures
and flags the whole run as portal-down at three. A listing that fails twice
still must.
"""
import logging
import sys
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scrapers.bge import BGELeitentscheideScraper as BGEScraper


class _Resp:
    def __init__(self, text):
        self.text = text


def _scraper(tmp_path, monkeypatch, fail_times):
    """Scraper whose listing for each (year, volume) in fail_times raises that
    many times before answering."""
    s = BGEScraper(state_dir=tmp_path, include_egmr=False)
    s.LISTING_RETRY_PAUSE = 0
    monkeypatch.setattr(s, "_establish_session", lambda: None)
    remaining = dict(fail_times)
    calls = []

    def fake_get(url):
        calls.append(url)
        return _Resp(url)

    def fake_parse(text, year, volume):
        key = (year, volume)
        if remaining.get(key, 0) > 0:
            remaining[key] -= 1
            raise requests.exceptions.ConnectionError(
                "SOCKSHTTPSConnectionPool(host='search.bger.ch', port=443): "
                "Max retries exceeded with url: /x"
            )
        return [{"docket_number": f"{year} {volume} 1", "url": text}]

    monkeypatch.setattr(s, "_safe_get", fake_get)
    monkeypatch.setattr(s, "_parse_volume_listing", fake_parse)
    return s, calls


def _discover(s, since):
    return [st["docket_number"] for st in s.discover_new(since_date=since)]


def test_transient_failure_recovered_without_error_line(tmp_path, monkeypatch, caplog):
    s, _ = _scraper(tmp_path, monkeypatch, {(2025, "II"): 1})
    with caplog.at_level(logging.INFO):
        got = _discover(s, "2025-01-01")
    assert "2025 II 1" in got
    assert len(got) == len(set(got))
    text = caplog.text
    assert "Max retries exceeded" not in text
    assert "Failed to fetch listing" not in text
    assert "retrying at end of run" in text


def test_persistent_failure_logged_once_as_error(tmp_path, monkeypatch, caplog):
    s, _ = _scraper(tmp_path, monkeypatch, {(2025, "III"): 5})
    with caplog.at_level(logging.INFO):
        got = _discover(s, "2025-01-01")
    assert "2025 III 1" not in got
    errors = [r for r in caplog.records
              if r.levelno == logging.ERROR and "Failed to fetch listing 2025/III" in r.getMessage()]
    assert len(errors) == 1
    # the counted line keeps the exhausted-retry text run_all_scrapers matches on
    assert "Max retries exceeded" in errors[0].getMessage()


def test_known_decisions_are_not_yielded(tmp_path, monkeypatch):
    s, _ = _scraper(tmp_path, monkeypatch, {})
    from models import make_decision_id
    monkeypatch.setattr(
        s.state, "is_known",
        lambda did: did == make_decision_id("bge", "2025 I 1"),
    )
    got = _discover(s, "2025-01-01")
    assert "2025 I 1" not in got
    assert "2025 II 1" in got


def test_no_failures_no_retry_pass(tmp_path, monkeypatch, caplog):
    s, calls = _scraper(tmp_path, monkeypatch, {})
    with caplog.at_level(logging.INFO):
        _discover(s, "2025-01-01")
    assert "Retrying" not in caplog.text
    assert len(calls) == len(set(calls))
