"""KNOWN_GAP_OFFSETS: structural portal-vs-ours differences that a full
rescan has proven not to be a backlog. Each entry must carry its evidence so
the claim can be re-tested; the persistent-gap alert must stay quiet while
the measured gap does not grow past it."""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scripts.check_scraper_freshness import (  # noqa: E402
    GAP_PERSIST_DAYS, KNOWN_GAP_OFFSETS, check_persistent_gaps,
)


def test_every_known_offset_is_evidenced():
    for court, entry in KNOWN_GAP_OFFSETS.items():
        assert isinstance(entry["gap"], int) and entry["gap"] > 0, court
        assert entry["verified"] >= "2026-08-01", court
        ev = entry["evidence"]
        assert "RESCAN_ALL" in ev, court
        # the walk's result is stated: nothing came back, or what did and what remained
        assert re.search(r"\+0|0 new|\+\d+ new, gap \d+", ev), court


def test_fribourg_offset_matches_the_2026_09_15_rescan():
    """2026-09-05 read the first two base64-path rows as 'rows without a
    download link'; 2026-09-15 corrected that after the fix recovered 25."""
    fr = KNOWN_GAP_OFFSETS["fr_gerichte"]
    assert fr["gap"] == 11
    assert fr["verified"] == "2026-09-15"
    assert "14708" in fr["evidence"] and "+25 new, gap 9" in fr["evidence"]
    assert "CORRECTION" in fr["evidence"]


def test_bern_offset_is_the_duplicate_rows_not_the_backlog():
    be = KNOWN_GAP_OFFSETS["be_verwaltungsgericht"]
    assert be["gap"] == 15 and be["verified"] == "2026-09-15"
    assert "11633" in be["evidence"] and "11600" in be["evidence"]
    assert "0 unknown ids" in be["evidence"]


def _health(gap: int) -> dict:
    return {"scrapers": {"fr_gerichte": {
        "success": True, "gap": gap, "our_count": 14685 - gap, "portal_count": 14685}}}


def test_persistent_gap_stays_quiet_at_the_known_offset(tmp_path):
    state = tmp_path / "gap_state.json"
    alerts = []
    for i in range(GAP_PERSIST_DAYS + 2):
        alerts += check_persistent_gaps(_health(11), f"2026-09-{5 + i:02d}", state_path=state)
    assert alerts == []


def test_persistent_gap_alerts_once_it_grows_past_the_offset(tmp_path):
    state = tmp_path / "gap_state.json"
    alerts = []
    for i in range(GAP_PERSIST_DAYS + 1):
        alerts += check_persistent_gaps(_health(12), f"2026-09-{5 + i:02d}", state_path=state)
    assert any("fr_gerichte" in a and "12" in a for a in alerts)
