"""decision_ref: the BS decision numbers ('AG.2014.40', 'SVG.2018.352',
'ZG.2025.1') and case numbers follow the LETTERS.YYYY.N grammar, so a typed
reference mints candidates for all three BS direct-scraper courts."""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import decision_ref  # noqa: E402


def test_bs_courts_are_in_the_dotted_serial_grammar():
    for court in ("bs_appellationsgericht", "bs_sozialversicherungsgericht", "bs_zivilgericht"):
        assert court in decision_ref.DOTTED_SERIAL_COURTS


def test_decision_number_candidates_cover_every_bs_court():
    cands = decision_ref.resolve_decision_ref("SVG.2018.352")
    assert "bs_sozialversicherungsgericht_SVG.2018.352" in cands
    # 'AG' and 'ZG' read as the cantons Aargau and Zug to the head logic; the
    # explicit BS decision-number rule adds the Basel candidate after the
    # canton-restricted ones, so a real Aargau id would still win.
    cands = decision_ref.resolve_decision_ref("AG.2014.40")
    assert "bs_appellationsgericht_AG.2014.40" in cands
    assert cands.index("bs_appellationsgericht_AG.2014.40") > cands.index("ag_zivilgericht_AG.2014.40")
    cands = decision_ref.resolve_decision_ref("ZG.2026.4")
    assert "bs_zivilgericht_ZG.2026.4" in cands
    # A typed reference around the number works too
    assert "bs_appellationsgericht_AG.2014.40" in decision_ref.resolve_decision_ref("AGE AG.2014.40")
    # The Zivilgericht case numbers carry a digit in the prefix ('K3.2025.24',
    # 'K5.2024.4') and stay outside the grammar; they resolve through the DB's
    # docket_number lookup (see test_decision_id_aliases). Pinned so a grammar
    # change that lifts this shows up here.
    assert decision_ref.resolve_decision_ref("K3.2025.24") == []


def test_old_style_prefixed_id_still_mints_spelling_variants():
    # the old id shape is handled by the DB alias table, but the grammar must
    # not choke on it either
    cands = decision_ref.resolve_decision_ref("bs_appellationsgericht_SB.2013.5")
    assert all(c.startswith("bs_") for c in cands)
