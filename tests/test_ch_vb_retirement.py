"""ch_vb (VPB) retirement: the QC expectation and the entscheidsuche feeds
must not name it (runbooks/ch_vb_amtsdruckschriften_contamination.md)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from quality.checks.courts import EXPECTED_FEDERAL
from scrapers import entscheidsuche_download, entscheidsuche_ingest


def test_quality_gate_no_longer_expects_ch_vb():
    assert "ch_vb" not in EXPECTED_FEDERAL


def test_entscheidsuche_spider_maps_exclude_ch_vb():
    assert "CH_VB" not in entscheidsuche_ingest.SPIDER_MAP
    assert "CH_VB" not in entscheidsuche_download.TIER_D_FEDERAL
    for name in ("TIER_A", "TIER_B", "TIER_C_SMALL", "TIER_E_GAPFILL"):
        assert "CH_VB" not in getattr(entscheidsuche_download, name)
