"""A portal that refuses us (403/429) stops the walk instead of absorbing it.

2026-09-30 01:00: bger.ch refused the tunnel address; bge still sent 304
listing requests plus 304 retries, bger ~90 search requests. A block should
cost a handful of requests, and the run must still read as failed.
"""
import sys
from datetime import date, timedelta
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import scrapers.bge as bge_mod
from run_all_scrapers import _is_listing_refusal
from scrapers.bge import BGELeitentscheideScraper
from scrapers.bger import HOST, SEARCH_HOST, BgerScraper
from scrapers.refusal import is_refusal

TODAY = date(2026, 9, 30)


def _http_error(status):
    resp = requests.Response()
    resp.status_code = status
    resp.url = "https://search.bger.ch/x"
    return requests.HTTPError(f"{status} Client Error: Forbidden for url: {resp.url}",
                              response=resp)


def test_is_refusal():
    assert is_refusal(_http_error(403))
    assert is_refusal(_http_error(429))
    assert not is_refusal(_http_error(404))
    assert not is_refusal(_http_error(500))
    assert not is_refusal(requests.ConnectionError("Max retries exceeded"))
    assert is_refusal(requests.exceptions.RetryError("too many 429 error responses"))


# ── bge ──────────────────────────────────────────────────────────────────────

class _FixedDate(date):
    @classmethod
    def today(cls):
        return cls(TODAY.year, TODAY.month, TODAY.day)


class _Resp:
    def __init__(self, text):
        self.text = text


def _bge(tmp_path, monkeypatch, outcome, egmr=False):
    """outcome(year, volume) -> None (ok) or an exception to raise."""
    monkeypatch.setattr(bge_mod, "date", _FixedDate)
    monkeypatch.delenv("OCL_SCRAPER_RESCAN_ALL", raising=False)
    s = BGELeitentscheideScraper(state_dir=tmp_path, include_egmr=egmr)
    s.LISTING_RETRY_PAUSE = 0
    monkeypatch.setattr(s, "_establish_session", lambda: None)
    calls = []

    def fake_get(url):
        calls.append(url)
        return _Resp(url)

    def fake_parse(text, year, volume):
        err = outcome(year, volume)
        if err:
            raise err
        return []

    monkeypatch.setattr(s, "_safe_get", fake_get)
    monkeypatch.setattr(s, "_parse_volume_listing", fake_parse)
    monkeypatch.setattr(s, "_parse_egmr_listing", lambda text: [])
    return s, calls


def _counted(caplog):
    return [r.getMessage() for r in caplog.records
            if _is_listing_refusal(f" ERROR {r.getMessage()}")]


def test_blocked_full_walk_stops_after_five(tmp_path, monkeypatch, caplog):
    # 2026 and 2025 load, then every listing is refused (the 09-30 01:00 shape)
    s, calls = _bge(tmp_path, monkeypatch,
                    lambda y, v: _http_error(403) if y < 2025 else None, egmr=True)
    list(s.discover_new())
    assert len(calls) == 10 + 5                  # no 300 more, no retry pass, no EGMR
    assert len(_counted(caplog)) == 5            # the run reads as failed (>= 3)
    assert not (tmp_path / "bge.fullwalk").exists()


def test_refusals_broken_by_a_success_do_not_stop(tmp_path, monkeypatch):
    # four refused, one loads, four refused ... never five in a row
    s, calls = _bge(tmp_path, monkeypatch,
                    lambda y, v: None if v == "V" else _http_error(403))
    list(s.discover_new())
    assert len(calls) > 300                      # walked everything


def test_connection_errors_are_not_refusals(tmp_path, monkeypatch):
    s, calls = _bge(tmp_path, monkeypatch,
                    lambda y, v: requests.ConnectionError("dropped") if y == 1990 else None)
    list(s.discover_new())
    assert len(calls) > 350                      # full walk + retries of 1990


def test_block_during_retry_pass_stops_it(tmp_path, monkeypatch, caplog):
    first = set()

    def outcome(y, v):
        if y in (2000, 1999) and (y, v) not in first:
            first.add((y, v))
            return requests.ConnectionError("dropped")     # first pass: transient
        if y in (2000, 1999):
            return _http_error(403)                         # retry: refused
        return None

    s, calls = _bge(tmp_path, monkeypatch, outcome)
    list(s.discover_new())
    retried = [c for c in calls[365:]]
    assert len(retried) == 5                     # stopped after five refused retries
    assert len(_counted(caplog)) == 5


# ── bger AZA search ──────────────────────────────────────────────────────────

class _FakeState:
    def is_known(self, decision_id):
        return False


class _FakeResp:
    text = "<html><body>Keine Treffer</body></html>"


def _bger(monkeypatch, fail):
    sc = BgerScraper.__new__(BgerScraper)
    sc.state = _FakeState()
    sc.until_date = TODAY
    requested = []

    def fake_get(url, retry=0):
        requested.append(url)
        err = fail(url)
        if err:
            raise err
        return _FakeResp()

    monkeypatch.setattr(sc, "_get_with_pow", fake_get)
    monkeypatch.setattr(sc, "_get_hit_count", lambda soup: 0)
    monkeypatch.setattr(sc, "_is_no_results", lambda soup: True)
    return sc, requested


def test_bger_search_stops_after_three_refused_windows(monkeypatch, caplog):
    sc, requested = _bger(monkeypatch, lambda url: _http_error(403))
    list(sc._discover_via_search(TODAY - timedelta(days=180)))
    assert len(requested) == 3 * 2               # 3 windows x both hosts, not ~90
    counted = _counted(caplog)                   # search.bger.ch lines count, www does not
    assert len(counted) == 3


def test_bger_fallback_host_answering_resets(monkeypatch):
    sc, requested = _bger(monkeypatch,
                          lambda url: _http_error(403) if url.startswith(HOST) else None)
    list(sc._discover_via_search(TODAY - timedelta(days=40)))
    assert requested[1].startswith(SEARCH_HOST)
    assert len(requested) == 1 + 11              # www once, then search.bger.ch for all 11 windows


def test_bger_timeouts_do_not_trip_the_breaker(monkeypatch):
    sc, requested = _bger(monkeypatch,
                          lambda url: requests.ConnectionError("Max retries exceeded"))
    list(sc._discover_via_search(TODAY - timedelta(days=40)))
    assert len(requested) == 11 * 2              # old behaviour: every window, both hosts
