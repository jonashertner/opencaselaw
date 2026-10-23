"""run_all_scrapers: a tunnel-dependent scraper (bger/bge/ju/ne) must read as
skipped, not failed, when the Mac reverse-SOCKS tunnel is down — the portal is
unreachable because the Mac was asleep at the 01:00 UTC scrape, not because the
scraper broke. Before this, a Mac-off night fired false portal-down alerts.

These cover the building blocks (the tunnel-dependent set + the listener probe);
the reclassification itself is a three-line guard on top of them.
"""
from __future__ import annotations

import socket
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import run_all_scrapers as r  # noqa: E402


def test_tunnel_dependent_set_covers_the_blocked_courts():
    assert {"bger", "bge", "ju_gerichte", "ne_gerichte", "ti_gerichte"} <= r.TUNNEL_DEPENDENT
    # A normal, non-tunnel scraper must NOT be reclassified.
    assert "bvger" not in r.TUNNEL_DEPENDENT
    assert "zh_gerichte" not in r.TUNNEL_DEPENDENT


def test_tunnel_down_on_closed_port():
    assert r._socks_tunnel_up(port=65000) is False


def test_tunnel_up_when_something_listens():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    try:
        assert r._socks_tunnel_up(port=port) is True
    finally:
        srv.close()
