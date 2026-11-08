"""
Bern Steuerrekurskommission Scraper
====================================
Scrapes tax appeal commission decisions from the Tribuna VTPlus platform.

Platform: Tribuna GWT-RPC, new protocol (47-param search, SEARCH_FIELD_COUNT=21).

Court filter: "StRK", case-sensitive. Captured from the portal's own search on
2026-11-08: "StRK" answers //OK[367]; the "STRK" this scraper sent before
answers //OK[0] with no error, which is what read as "portal DB disconnected"
from Feb 2026 on. The DLAConfig's court name ("BEJUS") is the shared Justiz
database, not the filter.

Dossier numbers: "100 YYYY N" and "200 YYYY N". decision_id =
be_steuerrekurs_<docket with underscores> (ID_SPACES_AS_UNDERSCORES), the id
the frozen entscheidsuche shard es_be_steuerrekurs.jsonl carries (343 rows) and
the one live today; see be_bvd.py for how the build merges the two copies.

Coverage: Steuerrekurskommission des Kantons Bern
Volume: 367 listed on 2026-11-08
Language: de/fr
Source: https://www.strk-entscheide.apps.be.ch/tribunapublikation
"""
from __future__ import annotations

from scrapers.cantonal.base_tribuna import TribunaBaseScraper


class BESteuerrekursScraper(TribunaBaseScraper):
    CANTON = "BE"
    COURT_CODE_STR = "be_steuerrekurs"
    BASE_URL = "https://www.strk-entscheide.apps.be.ch/tribunapublikation"
    COURT_FILTER = "StRK"  # case-sensitive; "STRK" answers //OK[0]
    LOCALE = "de"
    REQUEST_DELAY = 2.5
    SEARCH_FIELD_COUNT = 21  # New Tribuna version (47-param search)
    # The Betreff precedes the docket in this portal's string table, so the
    # span reading took the next field (municipality, "Steuerrekurskommission").
    TITLE_FROM_RECORD = True
    # = the live ids of the es_be_steuerrekurs rows (be_steuerrekurs_100_2010_922)
    ID_SPACES_AS_UNDERSCORES = True
