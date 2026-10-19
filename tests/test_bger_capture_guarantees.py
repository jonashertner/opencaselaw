"""No BGer/BGE publication day may be skipped without the run failing, and a
day that could not be read stays inside the walk until it has been read.

Gaps closed 2026-10-02:
  * bger.ch refuses with a 404 error page too (not only 403);
  * a block page served with status 200 parsed as "0 published";
  * unread Neuheiten pages were WARNINGs, invisible to the health check
    (55 on 2026-09-30);
  * the 14-day Neuheiten window moved on regardless of whether its days had
    been read.
"""
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import scrapers.bge as bge_mod
import scrapers.bger as bger_mod
from run_all_scrapers import _is_listing_refusal
from scrapers.bge import BGELeitentscheideScraper
from scrapers.bger import BgerScraper
from scrapers.refusal import PortalRefused, is_refusal

TODAY = date(2026, 10, 2)
BLOCK_404 = "<!doctype html><html><head><title>HTTP Status 404 – Not Found</title></head></html>" + " " * 800
NEUHEITEN = """<html><body><h1>Liste der neu aufgenommenen Entscheide</h1><table>
<tr><td><a href="/ext/eurospider/live/de/php/aza/http/index.php?highlight_docid=aza://18-08-2026-4A_1-2026&amp;lang=de&amp;type=show_document">4A_1/2026</a></td></tr>
</table></body></html>"""


def _http_error(status):
    resp = requests.Response()
    resp.status_code = status
    resp.url = "https://search.bger.ch/x"
    kind = "Client" if status < 500 else "Server"
    return requests.HTTPError(f"{status} {kind} Error: for url: {resp.url}", response=resp)


class _FixedDate(date):
    @classmethod
    def today(cls):
        return cls(TODAY.year, TODAY.month, TODAY.day)


# ── what counts as a refusal ─────────────────────────────────────────────────

def test_404_is_a_refusal_only_for_listing_urls():
    assert is_refusal(_http_error(404), listing=True)
    assert not is_refusal(_http_error(404))               # a document can be gone
    assert is_refusal(_http_error(403)) and is_refusal(_http_error(403), listing=True)
    assert is_refusal(PortalRefused("block page"))
    assert not is_refusal(_http_error(500), listing=True)


@pytest.mark.parametrize("line,counted", [
    (" ERROR Failed to fetch listing 2024/I: 404 Client Error:  for url: https://search.bger.ch/x", True),
    (" ERROR Neuheiten listing 2026-10-02: 403 Client Error: Forbidden for url: https://x", True),
    (" ERROR Neuheiten listing 2026-10-02: portal refused the request (not a Neuheiten page, 950 bytes)", True),
    (" ERROR Search 01.10.2026-04.10.2026 via https://search.bger.ch: 404 Client Error", True),
    (" ERROR Search 01.10.2026-04.10.2026 via https://www.bger.ch: 404 Client Error", False),
    (" ERROR Could not fetch 4A_1/2026: 404 Client Error", False),          # a document
    (" WARNING Neuheiten listing 2026-10-02: 403 Client Error", False),     # not terminal
])
def test_health_check_counts_refused_listings(line, counted):
    assert _is_listing_refusal(line) is counted


# ── bge ──────────────────────────────────────────────────────────────────────

class _Resp:
    def __init__(self, text, status=200):
        self.text = text
        self.status_code = status
        self.url = "https://search.bger.ch/x"
        self.cookies = []


def _bge(tmp_path, monkeypatch):
    monkeypatch.setattr(bge_mod, "date", _FixedDate)
    monkeypatch.delenv("OCL_SCRAPER_RESCAN_ALL", raising=False)
    s = BGELeitentscheideScraper(state_dir=tmp_path, include_egmr=False)
    s.LISTING_RETRY_PAUSE = 0
    monkeypatch.setattr(s, "_establish_session", lambda: None)
    return s


def test_bge_404_block_stops_the_walk_and_fails_the_run(tmp_path, monkeypatch, caplog):
    s = _bge(tmp_path, monkeypatch)
    calls = []

    def fake_get(url):
        calls.append(url)
        raise _http_error(404)

    monkeypatch.setattr(s, "_safe_get", fake_get)
    with caplog.at_level(logging.INFO):
        list(s.discover_new())
    assert len(calls) == 5
    counted = [r for r in caplog.records
               if _is_listing_refusal(f" ERROR {r.getMessage()}") and r.levelno == logging.ERROR]
    assert len(counted) == 5
    assert not (tmp_path / "bge.fullwalk").exists()


def test_bge_block_page_with_status_200_is_not_an_empty_listing(tmp_path, monkeypatch):
    s = _bge(tmp_path, monkeypatch)
    monkeypatch.setattr(s, "get", lambda url, **kw: _Resp("<html>Incapsula incident</html>"))
    monkeypatch.setattr(s._incapsula, "refresh_cookies", lambda domain: {})
    with pytest.raises(PortalRefused):
        s._safe_get("https://search.bger.ch/listing")


def test_bge_real_listing_passes(tmp_path, monkeypatch):
    s = _bge(tmp_path, monkeypatch)
    page = "<html><ol><li>x</li></ol></html>" + "x" * 600
    monkeypatch.setattr(s, "get", lambda url, **kw: _Resp(page))
    assert s._safe_get("https://search.bger.ch/listing").text == page


# ── bger Neuheiten ───────────────────────────────────────────────────────────

class _State:
    def __init__(self, tmp_path):
        self.state_file = tmp_path / "bger.jsonl"

    def is_known(self, decision_id):
        return True


def _bger(tmp_path, monkeypatch, page_for):
    """page_for(check_date) -> text, or an exception to raise."""
    monkeypatch.setattr(bger_mod, "date", _FixedDate)
    sc = BgerScraper.__new__(BgerScraper)
    sc.state = _State(tmp_path)
    asked = []

    def fake_get(url, retry=0):
        d = url.split("date=")[1][:8]
        day = date(int(d[:4]), int(d[4:6]), int(d[6:]))
        asked.append(day)
        out = page_for(day)
        if isinstance(out, Exception):
            raise out
        return _Resp(out)

    monkeypatch.setattr(sc, "_get_with_pow", fake_get)
    return sc, asked


def _marker(tmp_path):
    return tmp_path / "bger.neuheiten_clean"


def test_clean_walk_reads_14_days_and_marks(tmp_path, monkeypatch):
    sc, asked = _bger(tmp_path, monkeypatch, lambda d: NEUHEITEN if d.weekday() < 5 else "\n")
    list(sc._discover_via_neuheiten())
    assert len(asked) == 14
    assert _marker(tmp_path).read_text().strip() == "2026-10-02"


def test_block_page_is_a_failed_page_not_an_empty_day(tmp_path, monkeypatch, caplog):
    sc, _ = _bger(tmp_path, monkeypatch, lambda d: BLOCK_404)
    with caplog.at_level(logging.INFO):
        list(sc._discover_via_neuheiten())
    errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 14
    assert all(_is_listing_refusal(f" ERROR {m}") for m in errors)
    assert "could not be read" in caplog.text
    assert not _marker(tmp_path).exists()


def test_refused_pages_are_counted_and_keep_the_marker(tmp_path, monkeypatch, caplog):
    _marker(tmp_path).write_text("2026-10-01\n")
    sc, _ = _bger(tmp_path, monkeypatch,
                  lambda d: _http_error(404) if d >= TODAY - timedelta(days=2) else NEUHEITEN)
    with caplog.at_level(logging.INFO):
        list(sc._discover_via_neuheiten())
    counted = [r for r in caplog.records if _is_listing_refusal(f" ERROR {r.getMessage()}")]
    assert len(counted) == 3
    assert _marker(tmp_path).read_text().strip() == "2026-10-01"   # unchanged


def test_window_reaches_back_past_an_outage(tmp_path, monkeypatch):
    _marker(tmp_path).write_text("2026-09-02\n")              # 30 days without a clean walk
    sc, asked = _bger(tmp_path, monkeypatch, lambda d: "\n")
    list(sc._discover_via_neuheiten())
    assert len(asked) == 30 + 14
    assert min(asked) == date(2026, 8, 20)                    # 14 days before the last clean walk... and one
    assert _marker(tmp_path).read_text().strip() == "2026-10-02"


def test_window_is_capped(tmp_path, monkeypatch):
    _marker(tmp_path).write_text("2025-01-01\n")
    sc, asked = _bger(tmp_path, monkeypatch, lambda d: "\n")
    list(sc._discover_via_neuheiten())
    assert len(asked) == BgerScraper.NEUHEITEN_MAX_DAYS


def test_unreadable_marker_means_the_normal_window(tmp_path, monkeypatch):
    _marker(tmp_path).write_text("garbage\n")
    sc, asked = _bger(tmp_path, monkeypatch, lambda d: "\n")
    list(sc._discover_via_neuheiten())
    assert len(asked) == 14


# ── bger AZA search ──────────────────────────────────────────────────────────

def test_aza_search_stops_on_404_refusals_too(monkeypatch):
    sc = BgerScraper.__new__(BgerScraper)
    sc.state = type("S", (), {"is_known": lambda self, d: False})()
    sc.until_date = TODAY
    requested = []

    def fake_get(url, retry=0):
        requested.append(url)
        raise _http_error(404)

    monkeypatch.setattr(sc, "_get_with_pow", fake_get)
    list(sc._discover_via_search(TODAY - timedelta(days=180)))
    assert len(requested) == 3 * 2
