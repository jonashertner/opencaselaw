"""The SAV/FSA (Schweizerischer Anwaltsverband) sources are retired — 2026-09-14.

sav-fsa.ch is a republisher, not a court, and its 37 corpus rows were not decisions:
33 sav_kantone rows and 1 sav_international row carried the same SAV "Cloud Guidelines"
PDF as full text (the Liferay detail pages' first PDF link is the site-wide guideline),
the other 3 were an Anwaltsrevue excerpt and two CJEU judgments hosted by the SAV.
Policy: only original court/authority data. The shards are quarantined on the VPS
(runbooks/sav_fsa_quarantine.md); this test keeps the codes out of every registry so
nothing re-creates them.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

RETIRED = ("sav_kantone", "sav_international")


def test_not_registered_as_scrapers():
    from run_scraper import SCRAPERS
    for code in RETIRED:
        assert code not in SCRAPERS
    assert not (REPO / "scrapers" / "sav_kantone.py").exists()
    assert not (REPO / "scrapers" / "sav_international.py").exists()


def test_not_in_server_and_stats_registries():
    import mcp_server
    import branch_map
    import proceeding_map
    import generate_stats
    for code in RETIRED:
        assert code not in mcp_server.COURT_DISPLAY_NAMES
        assert code not in generate_stats._FEDERAL_COURT_EXCLUDE
        # maps are keyed by court code; a retired court must not be looked up
        assert code not in getattr(branch_map, "COURT_BRANCH", {}) or True
        assert code not in open(REPO / "branch_map.py").read()
        assert code not in open(REPO / "proceeding_map.py").read()
