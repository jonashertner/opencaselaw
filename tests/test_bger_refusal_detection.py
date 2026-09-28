"""2026-09-28: Incapsula refused the residential tunnel IP ~05:00-10:30 UTC.

Two things reported that as a quiet day:

1. run_all_scrapers counted no discovery errors for "403 Client Error" lines,
   so bge (356/356 listings refused, 09:00) and bger (both hosts refused, 10:20)
   finished "+0 new, Errors: 0" = success.
2. bger_poller's tunnel path trusted any HTTP 200 and parsed whatever came
   back; a non-list page reads as "0 decisions".

Log lines below are verbatim from the VPS logs of that day (URLs trimmed).
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

spec = importlib.util.spec_from_file_location("bger_poller", REPO / "scripts" / "bger_poller.py")
bp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bp)

from run_all_scrapers import _is_listing_refusal  # noqa: E402

FIXTURE = REPO / "tests" / "fixtures" / "bger_neuheiten_20260928.html"


# ── run_all_scrapers: refused listing/search pages are discovery errors ──

REFUSED = [
    "2026-09-28 09:19:28,809 scrapers.bge ERROR Failed to fetch listing 2024/I: 403 Client "
    "Error: Forbidden for url: https://search.bger.ch/ext/eurospider/live/de/php/clir/http/index_atf.php",
    "2026-09-28 09:37:17,159 scrapers.bge ERROR Failed to fetch EGMR listing: 403 Client Error: "
    "Forbidden for url: https://search.bger.ch/ext/eurospider/live/de/php/clir/http/index_cedh.php?lang=de",
    "2026-09-28 10:24:07,097 scrapers.bger ERROR Search 01.04.2026-04.04.2026 via "
    "https://search.bger.ch: 403 Client Error: Forbidden for url: https://search.bger.ch/ext/",
    # before the two-host fallback the line had no "via"
    "2026-08-15 01:09:58,776 scrapers.bger ERROR Search 08.06.2026-11.06.2026: 403 Client Error: "
    "Forbidden for url: https://www.bger.ch/ext/eurospider/live/de/php/aza/http/index.php",
]

NOT_REFUSED = [
    # first host of the fallback: search.bger.ch is tried next, and logs its own line
    "2026-09-28 10:24:05,101 scrapers.bger ERROR Search 01.04.2026-04.04.2026 via "
    "https://www.bger.ch: 403 Client Error: Forbidden for url: https://www.bger.ch/ext/",
    # a deferred retry (43194cb3), not terminal
    "2026-09-28 09:17:01,403 scrapers.bge WARNING Listing 1959/I failed (HTTPError), retrying at end of run",
    # a per-document error is not a discovery failure (constructed example)
    "2026-09-28 01:10:00,000 scrapers.vd_gerichte ERROR Failed to fetch document 2026/123: "
    "403 Client Error: Forbidden for url: https://example.invalid/doc.pdf",
    "2026-09-28 01:10:00,000 scrapers.bge INFO Year 2026 Volume I: 15 decisions listed",
]


@pytest.mark.parametrize("line", REFUSED)
def test_a_refused_listing_counts_as_a_discovery_error(line):
    assert _is_listing_refusal(line)


@pytest.mark.parametrize("line", NOT_REFUSED)
def test_other_lines_do_not(line):
    assert not _is_listing_refusal(line)


def test_the_status_family_is_limited_to_refusals():
    base = "x scrapers.bge ERROR Failed to fetch listing 2024/I: {} for url: https://search.bger.ch/"
    for s in ("403 Client Error: Forbidden", "429 Client Error: Too Many Requests",
              "502 Server Error: Proxy Error", "503 Server Error: Service Unavailable",
              "504 Server Error: Gateway Timeout"):
        assert _is_listing_refusal(base.format(s)), s
    assert not _is_listing_refusal(base.format("404 Client Error: Not Found"))


# ── bger_poller: classify the tunnel answer instead of trusting a 200 ──

def test_the_real_page_of_2026_09_28_yields_its_six_rulings():
    got = bp._classify_neuheiten_page(FIXTURE.read_text(encoding="latin-1"))
    assert got == {"9C_604/2025", "8C_119/2026", "9C_88/2026",
                   "8C_421/2026", "9C_217/2026", "8F_7/2026"}


@pytest.mark.parametrize("body", ["\n", "", "  \n"])
def test_an_empty_body_is_bger_saying_nothing_listed_yet(body):
    """Measured 2026-09-28: a Sunday and a not-yet-published date both answer "\\n"."""
    assert bp._classify_neuheiten_page(body) == set()


@pytest.mark.parametrize("body", [
    # Incapsula challenge shape (constructed; the harvest log reports ~443 chars)
    '<html><head><META NAME="robots" CONTENT="noindex,nofollow"><script '
    'src="/_Incapsula_Resource?SWJIYLWA=719d34d31c8e3a6e6fffd"></script></head><body></body></html>',
    "<html><body><h1>Service Unavailable</h1></body></html>",
])
def test_a_page_that_is_not_the_list_raises(body):
    with pytest.raises(RuntimeError, match="not a Neuheiten page"):
        bp._classify_neuheiten_page(body)


def test_a_blocked_tunnel_answer_falls_back_to_direct(monkeypatch):
    monkeypatch.setenv("BGER_PROXY", "socks5h://127.0.0.1:1080")

    class R:
        text = "<html><body>blocked</body></html>"
        def raise_for_status(self):
            pass

    import requests
    monkeypatch.setattr(requests.Session, "get", lambda self, url, timeout=None: R())
    monkeypatch.setattr(bp, "_fetch_neuheiten_direct", lambda url: {"9C_9/2026"})
    assert bp._fetch_neuheiten("20260928") == {"9C_9/2026"}


# ── bger_poller: a failed fetch after the publication window alerts once ──

def _state(tmp_path, monkeypatch, content=None):
    f = tmp_path / "state.json"
    if content is not None:
        f.write_text(json.dumps(content))
    monkeypatch.setattr(bp, "STATE_FILE", f)
    return f


def _capture_alerts(monkeypatch):
    sent = []
    monkeypatch.setattr(bp, "_alert_ntfy", lambda title, msg, tags="": sent.append(title))
    return sent


MON_1200 = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
MON_0900 = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)
SUN_1200 = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)


def test_fetch_failure_alerts_once_per_workday_after_the_window(tmp_path, monkeypatch):
    f = _state(tmp_path, monkeypatch, {
        "date": "2026-09-28", "dockets": ["5A_1/2026"], "failing": {"6B_2/2026": 1},
        "pending_publish": True, "alerts": {"empty": "2026-09-28"}})
    sent = _capture_alerts(monkeypatch)
    assert bp._alert_fetch_failure("2026-09-28", RuntimeError("503"), now=MON_1200) is True
    assert bp._alert_fetch_failure("2026-09-28", RuntimeError("503"), now=MON_1200) is False
    assert sent == ["BGer poller: Neuheiten fetch failing"]
    s = json.loads(f.read_text())
    # everything else is kept as it was
    assert s["date"] == "2026-09-28" and s["dockets"] == ["5A_1/2026"]
    assert s["failing"] == {"6B_2/2026": 1} and s["pending_publish"] is True
    assert s["alerts"] == {"empty": "2026-09-28", "fetch": "2026-09-28"}


@pytest.mark.parametrize("now", [MON_0900, SUN_1200])
def test_no_alert_before_publication_or_at_weekends(tmp_path, monkeypatch, now):
    _state(tmp_path, monkeypatch, {})
    sent = _capture_alerts(monkeypatch)
    assert bp._alert_fetch_failure(now.date().isoformat(), RuntimeError("503"), now=now) is False
    assert sent == []


def test_a_new_day_alerts_again_and_keeps_yesterdays_state_date(tmp_path, monkeypatch):
    f = _state(tmp_path, monkeypatch, {"date": "2026-09-25", "dockets": ["1C_1/2026"],
                                        "alerts": {"fetch": "2026-09-25"}})
    sent = _capture_alerts(monkeypatch)
    assert bp._alert_fetch_failure("2026-09-28", OSError("tunnel"), now=MON_1200) is True
    s = json.loads(f.read_text())
    assert s["date"] == "2026-09-25", "the next good run must still see a new day"
    assert s["alerts"]["fetch"] == "2026-09-28" and len(sent) == 1
