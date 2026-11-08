"""
Graubunden Courts Scraper (GR Gerichte)
========================================
Scrapes court decisions from the Tribuna VTPlus platform.

Platform: Tribuna GWT-RPC (new protocol, Feb 2026)
Coverage: Kantonsgericht / Verwaltungsgericht Graubunden
Volume: ~14,000 decisions
Language: de/rm/it (trilingual canton)

Source: https://entscheidsuche.gr.ch
"""
from __future__ import annotations

from datetime import date

import source_dates
from scrapers.cantonal.base_tribuna import TribunaBaseScraper


class GRGerichteScraper(TribunaBaseScraper):
    CANTON = "GR"
    COURT_CODE_STR = "gr_gerichte"
    BASE_URL = "https://entscheidsuche.gr.ch"
    COURT_FILTER = "OG"  # Obergericht — returns all courts
    LOCALE = "de"
    REQUEST_DELAY = 2.5
    # entscheidsuche.gr.ch upgraded VTPlus on 2026-10-21/22 and dropped the
    # 46-param search(): the 01:00 run got a GWT IncompatibleRemoteServiceException
    # ("application is out of date"). Same upgrade BE went through 2026-06-19;
    # send the 47-param form (was 20).
    SEARCH_FIELD_COUNT = 21

    def fetch_decision(self, stub: dict):
        """A Praxis entry (PKG/PVG/POG) is dated by the trailer that closes it
        ("S 23 137 / Urteil vom 27. Februar 2024"). The register's date for
        these is an upload or volume date (875 entries carried the scrape day,
        2026-02-13/14) or another entry's, so the trailer wins; without one
        the row stays undated rather than take the register's guess."""
        dec = super().fetch_decision(stub)
        if dec is None or not source_dates.GR_YEARBOOK.match(dec.docket_number or ""):
            return dec
        trailer = source_dates.gr_yearbook_trailer(dec.full_text, dec.docket_number)
        dec.decision_date = date.fromisoformat(trailer) if trailer else None
        return dec
