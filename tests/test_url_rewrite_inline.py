"""Build-time link repairs (2026-11-25).

- es_bl_gerichte: 7,579 links carried a dead proxy in front of the court's
  own baselland.ch address.
- ti_gerichte: ~60k links on http://www.sentenze.ti.ch, which serves the same
  documents on https.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from build_fts5 import _source_url_normalize_inline, _url_rewrite_inline  # noqa: E402
from scrapers.cantonal.ti_gerichte import _public_url  # noqa: E402

PROXY = "http://v2202109132150164038.luckysrv.de:8181/"
BL = ("https://www.baselland.ch/politik-und-behorden/gerichte/rechtsprechung/"
      "enteignungsgericht/entscheide-chronologisch/2009/09-001")
TI = ("http://www.sentenze.ti.ch/cgi-bin/nph-omniscgi?OmnisPlatform=WINDOWS"
      "&WebServerUrl=www.sentenze.ti.ch&nF30_KEY=140502")


def test_dead_proxy_prefix_is_stripped():
    assert _url_rewrite_inline(PROXY + BL) == BL
    assert _source_url_normalize_inline("bl_gerichte", PROXY + BL) == BL


def test_proxy_prefix_without_a_url_behind_it_is_kept():
    assert _url_rewrite_inline(PROXY + "index.html") == PROXY + "index.html"


def test_ti_links_move_to_https():
    want = "https://" + TI[len("http://"):]
    assert _url_rewrite_inline(TI) == want
    assert _source_url_normalize_inline("ti_gerichte", TI) == want
    # only the host part changes; the WebServerUrl parameter stays
    assert "WebServerUrl=www.sentenze.ti.ch" in want


def test_other_links_are_untouched():
    for url in (None, "", "http://relevancy.bger.ch/x", "https://www.bger.ch/x",
                "http://www.sentenze.ti.ch.example.org/x",
                "http://www.jurisprudence.vd.ch/x"):
        assert _url_rewrite_inline(url) == url


def test_idempotent():
    for url in (PROXY + BL, TI):
        once = _url_rewrite_inline(url)
        assert _url_rewrite_inline(once) == once


def test_relative_bs_gl_paths_still_get_their_host():
    assert (_source_url_normalize_inline("bs_gerichte", "/cgi-bin/x")
            == "https://www.gerichte.bs.ch/cgi-bin/x")


def test_ti_scraper_stores_https():
    assert _public_url(TI) == "https://" + TI[len("http://"):]
    assert _public_url("https://www.sentenze.ti.ch/x") == "https://www.sentenze.ti.ch/x"
    assert _public_url("") == ""
