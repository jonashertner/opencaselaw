"""
Bern Zivil- und Strafgerichte Scraper
=======================================
Scrapes civil and criminal court decisions from the Tribuna VTPlus platform.

Platform: Tribuna GWT-RPC (VTPlus, 47-param search)
Coverage: Zivil- und Strafgerichte des Kantons Bern
Volume: ~5,637 decisions (OG=5,632, BM=2, WSG=3)
Language: de/fr

Source: https://www.zsg-entscheide.apps.be.ch/tribunapublikation
"""
from __future__ import annotations

from datetime import date

import source_dates
from scrapers.cantonal.base_tribuna import TribunaBaseScraper

# The Obergericht's letterhead states the ruling's date ("Bern, 5. Februar
# 2026", "Berne, le 19 juin 2026"); the regional courts sit elsewhere.
LETTERHEAD_PLACES = ("Bern", "Berne", "Biel", "Bienne", "Thun", "Burgdorf",
                     "Langenthal", "Langnau")


class BEZivilStrafScraper(TribunaBaseScraper):
    CANTON = "BE"
    COURT_CODE_STR = "be_zivilstraf"
    BASE_URL = "https://www.zsg-entscheide.apps.be.ch/tribunapublikation"
    COURT_FILTERS = ["OG", "BM", "BJS", "EO", "O", "WSG"]
    LOCALE = "de"
    REQUEST_DELAY = 2.5
    # VERIFY_SSL removed 2026-08-26: www.zsg-entscheide.apps.be.ch verifies cleanly against
    # the scraper CA bundle (deploy/certs). The old opt-out silently did
    # nothing anyway once REQUESTS_CA_BUNDLE shipped — see base_scraper._build_session.
    SEARCH_FIELD_COUNT = 21  # VTPlus upgrade removed the 46-param search() (2026-06-19; was 20)

    def fetch_decision(self, stub: dict):
        """The register's own date is the source (base_tribuna reads each
        row's record since 2026-09-27; 65 of 66 later rows agree with the
        letterhead). When the register gives none, the letterhead does."""
        dec = super().fetch_decision(stub)
        if dec is None or dec.decision_date:
            return dec
        head = source_dates.place_date_head(dec.full_text, LETTERHEAD_PLACES)
        if head and source_dates.plausible(head, dec.docket_number, None):
            dec.decision_date = date.fromisoformat(head)
        return dec
