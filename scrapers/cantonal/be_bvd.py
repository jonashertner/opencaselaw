"""
Bern BVD (Bau-, Verkehrs- und Energiedirektion) Scraper
=======================================================
Scrapes BVD administrative-appeal decisions from the Tribuna VTPlus platform.

Platform: Tribuna GWT-RPC, classic 46-param search (SEARCH_FIELD_COUNT=20;
nf=21 answers //EX IncompatibleRemoteServiceException).

Court filter: the portal's own search sends "TRI", NOT the "BVD" that the
DLAConfig carries as its court name. Captured from the browser on 2026-11-08:
"BVD" answers //OK[0] with no error (the reason this scraper was parked as
"backend dead" on 2026-06-17); "TRI" answers //OK[2255]. The credential does
not matter: all five config hex strings return the same total.

Dossier numbers: "110 YYYY N" up to 2025, "120 YYYY N" from 2026, plus a few
"195 YYYY N". decision_id = be_bvd_<docket with underscores>
(ID_SPACES_AS_UNDERSCORES), the id the frozen entscheidsuche shard
es_be_bvd.jsonl carries (2,094 rows, last 2026-03-12) and the one live today.
The build's direct-first insert gives the direct row that id; an es copy with
another date (407 of 2,091, the es date being wrong in every sampled case)
becomes <id>_dYYYYMMDD and is folded into it by the federation twin fold. The
frozen rows still fill what the portal no longer lists (3 dockets).

Coverage: Bau-, Verkehrs- und Energiedirektion des Kantons Bern
Volume: 2,255 listed on 2026-11-08 (decisions 2002-2026)
Language: de (some fr)
Source: https://www.bvd-entscheide.apps.be.ch/
"""
from __future__ import annotations

from scrapers.cantonal.base_tribuna import TribunaBaseScraper


class BEBvdScraper(TribunaBaseScraper):
    CANTON = "BE"
    COURT_CODE_STR = "be_bvd"
    BASE_URL = "https://www.bvd-entscheide.apps.be.ch"
    TRIBUNA_PATH = "tribunavtplus"
    COURT_FILTER = "TRI"           # what the portal's own search sends (see above)
    LOCALE = "de"
    REQUEST_DELAY = 4.0            # match the BE siblings; avoid 503 rate-limit
    SEARCH_FIELD_COUNT = 20        # classic Tribuna; nf=21 answers //EX
    TITLE_FROM_RECORD = True       # the row's Betreff, not the span's first long string
    ID_SPACES_AS_UNDERSCORES = True  # = the live ids of the es_be_bvd rows (be_bvd_110_2002_108)
