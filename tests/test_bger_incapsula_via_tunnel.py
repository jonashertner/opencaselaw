"""bger.ch challenges the residential tunnel address too (2026-10-14).

From 01:00 UTC every bger / bge / poller request through the tunnel was refused
(403, or a 953-byte Incapsula challenge page served with status 200), while a
real browser at the same address passed. The scrapers skipped the cookie harvest
whenever they egressed through the proxy, the harvesters had no proxy setting
(so a harvest could only run from the datacenter address, which Incapsula
hard-blocks), and the block detector missed the larger challenge page.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import requests

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import incapsula_bypass as ib  # noqa: E402
from scrapers.bge import BGELeitentscheideScraper  # noqa: E402
from scrapers.bger import BgerScraper  # noqa: E402
from scrapers.refusal import PortalRefused  # noqa: E402

PROXY = "socks5h://127.0.0.1:1080"
CHALLENGE = ('<html style="height:100%"><head><META NAME="ROBOTS" CONTENT="NOINDEX, NOFOLLOW">'
             '<script type="text/javascript" src="/_Incapsula_Resource?SWJIYLWA=5074a744"></script>'
             '</head><body>Request unsuccessful. Incapsula incident ID: 123-456</body></html>')
CHALLENGE = CHALLENGE + " " * (953 - len(CHALLENGE))
FF_UA = "Mozilla/5.0 (X11; Linux x86_64; rv:135.0) Gecko/20100101 Firefox/135.0"


class _Resp:
    def __init__(self, text, status=200, url="https://www.bger.ch/x"):
        self.text = text
        self.status_code = status
        self.url = url
        self.cookies = []


def _http_error(status):
    resp = requests.Response()
    resp.status_code = status
    resp.url = "https://search.bger.ch/x"
    return requests.HTTPError(f"{status} Client Error", response=resp)


# ── incapsula_bypass ─────────────────────────────────────────────────────────

def test_the_current_challenge_page_is_detected():
    assert len(CHALLENGE) == 953
    assert ib.IncapsulaCookieManager.is_incapsula_blocked(CHALLENGE)
    assert not ib.IncapsulaCookieManager.is_incapsula_blocked("<html>Eurospider " + "x" * 2000)


def test_browser_proxy_takes_socks5_not_socks5h():
    assert ib.browser_proxy(PROXY) == {"server": "socks5://127.0.0.1:1080"}
    assert ib.browser_proxy(None) is None


def test_manager_harvests_through_its_proxy_and_keeps_a_separate_cache(tmp_path, monkeypatch):
    seen = {}

    def fake_harvest(url, timeout_seconds=60, headless=True, proxy=None):
        seen["proxy"] = proxy
        return {"visid_incap_1": "v", "incap_ses_1": "s"}, FF_UA

    monkeypatch.setattr(ib, "harvest", fake_harvest)
    mgr = ib.IncapsulaCookieManager(cache_dir=tmp_path, proxy=PROXY)
    cookies = mgr.get_cookies("search.bger.ch")
    assert cookies["incap_ses_1"] == "s"
    assert seen["proxy"] == PROXY
    assert mgr.user_agent("search.bger.ch") == FF_UA
    cache = tmp_path / "incapsula_search_bger_ch_via_proxy.json"
    assert json.loads(cache.read_text())["user_agent"] == FF_UA
    assert not (tmp_path / "incapsula_search_bger_ch.json").exists()
    # a fresh manager reads the proxy cache, and the UA with it
    again = ib.IncapsulaCookieManager(cache_dir=tmp_path, proxy=PROXY)
    seen.clear()
    assert again.get_cookies("search.bger.ch") == cookies and "proxy" not in seen
    assert again.user_agent("search.bger.ch") == FF_UA
    # the direct manager does not use cookies earned through the tunnel
    direct = ib.IncapsulaCookieManager(cache_dir=tmp_path)
    direct.get_cookies("search.bger.ch")
    assert seen["proxy"] is None


def test_harvests_per_run_are_budgeted(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(ib, "harvest", lambda url, timeout_seconds=60, headless=True, proxy=None:
                        (calls.append(1) or ({"incap_ses_1": "s"}, None)))
    mgr = ib.IncapsulaCookieManager(cache_dir=tmp_path, proxy=PROXY)
    for _ in range(mgr.MAX_HARVESTS_PER_DOMAIN):
        mgr.refresh_cookies("search.bger.ch")
    with pytest.raises(RuntimeError, match="budget"):
        mgr.refresh_cookies("search.bger.ch")
    assert len(calls) == mgr.MAX_HARVESTS_PER_DOMAIN


# ── bger ─────────────────────────────────────────────────────────────────────

def _bger(tmp_path, monkeypatch, proxied=True):
    s = BgerScraper(state_dir=tmp_path)
    if proxied:
        s.session.proxies = {"http": PROXY, "https": PROXY}
    return s


def test_bger_unchallenged_tunnel_harvests_nothing(tmp_path, monkeypatch):
    s = _bger(tmp_path, monkeypatch)
    monkeypatch.setattr(s, "get", lambda url, **kw: _Resp("<html>Eurospider</html>" + "x" * 3000))
    monkeypatch.setattr(s._incapsula, "get_cookies", lambda d: pytest.fail("no harvest expected"))
    s._init_session()
    assert s._pow_required is False


@pytest.mark.parametrize("answer", ["page", "403"])
def test_bger_challenged_tunnel_harvests_through_the_tunnel(tmp_path, monkeypatch, answer):
    s = _bger(tmp_path, monkeypatch)
    state = {"harvested": False}

    def fake_get(url, **kw):
        if not state["harvested"]:
            if answer == "403":
                raise _http_error(403)
            return _Resp(CHALLENGE)
        return _Resp("<html>Eurospider</html>" + "x" * 3000)

    def fake_get_cookies(domain):
        assert s._incapsula.proxy == PROXY          # never from the datacenter address
        state["harvested"] = True
        s._incapsula._user_agents[s._incapsula._key(domain)] = FF_UA
        return {"incap_ses_1": "s"}

    monkeypatch.setattr(s, "get", fake_get)
    monkeypatch.setattr(s._incapsula, "get_cookies", fake_get_cookies)
    s._init_session()
    assert state["harvested"]
    assert s._req_headers()["User-Agent"] == FF_UA
    assert s.session.headers["User-Agent"] == FF_UA


def test_bger_direct_path_unchanged(tmp_path, monkeypatch):
    s = _bger(tmp_path, monkeypatch, proxied=False)
    harvested = []
    monkeypatch.setattr(s, "get", lambda url, **kw: _Resp("<html>Eurospider</html>" + "x" * 3000))
    monkeypatch.setattr(s._incapsula, "get_cookies", lambda d: harvested.append(d) or {})
    s._init_session()
    assert harvested == ["www.bger.ch", "search.bger.ch"]
    assert s._incapsula.proxy is None


# ── bge ──────────────────────────────────────────────────────────────────────

def _bge(tmp_path):
    s = BGELeitentscheideScraper(state_dir=tmp_path, include_egmr=False)
    s.session.proxies = {"http": PROXY, "https": PROXY}
    return s


def test_bge_refusal_refreshes_through_the_tunnel_then_passes(tmp_path, monkeypatch):
    s = _bge(tmp_path)
    state = {"refreshed": 0}

    def fake_get(url, **kw):
        if not state["refreshed"]:
            raise _http_error(403)
        return _Resp("<html><ol><li>x</li></ol></html>" + "x" * 600)

    def fake_refresh(domain):
        assert s._incapsula.proxy == PROXY
        state["refreshed"] += 1
        return {"incap_ses_1": "s"}

    monkeypatch.setattr(s, "get", fake_get)
    monkeypatch.setattr(s._incapsula, "refresh_cookies", fake_refresh)
    assert "<ol>" in s._safe_get("https://search.bger.ch/listing").text
    assert state["refreshed"] == 1


def test_bge_persistent_refusal_ends_as_portal_refused(tmp_path, monkeypatch):
    s = _bge(tmp_path)

    def always_403(url, **kw):
        raise _http_error(403)

    monkeypatch.setattr(s, "get", always_403)
    monkeypatch.setattr(s._incapsula, "refresh_cookies", lambda d: {})
    with pytest.raises(PortalRefused):
        s._safe_get("https://search.bger.ch/listing")


# ── poller ───────────────────────────────────────────────────────────────────

def test_poller_challenge_harvests_through_the_tunnel_not_direct(monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location("bger_poller", REPO / "scripts" / "bger_poller.py")
    bp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bp)
    monkeypatch.setenv("BGER_PROXY", PROXY)
    calls = {}

    def challenged(url, proxy):
        raise RuntimeError("not a Neuheiten page (953 bytes) — challenge or block page")

    monkeypatch.setattr(bp, "_fetch_neuheiten_via_proxy", challenged)
    monkeypatch.setattr(bp, "_fetch_neuheiten_via_proxy_with_cookies",
                        lambda url, proxy: calls.setdefault("with_cookies", proxy) and {"4A_1/2026"})
    monkeypatch.setattr(bp, "_fetch_neuheiten_direct", lambda url: calls.setdefault("direct", True))
    assert bp._fetch_neuheiten("20261014") == {"4A_1/2026"}
    assert calls["with_cookies"] == PROXY
    assert "direct" not in calls
    assert bp._is_tunnel_error(OSError("tunnel down"))
    assert not bp._is_tunnel_error(_http_error(403))
