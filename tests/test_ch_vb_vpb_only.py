"""ch_vb holds the VPB/JAAC series only: the QC gate expects the court, the
entscheidsuche feeds never refresh it, and scripts/ch_vb_keep_vpb.py keeps one
row per VPB PDF (runbooks/ch_vb_amtsdruckschriften_contamination.md)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from quality.checks.courts import EXPECTED_FEDERAL
from scrapers import entscheidsuche_download, entscheidsuche_ingest
from scripts import ch_vb_keep_vpb as kv

PORTAL = "https://www.amtsdruckschriften.bar.admin.ch/viewOrigDoc/{0}.pdf?ID={0}"


def _row(rid, pid, docket, text="x" * 600):
    return {"decision_id": rid, "court": "ch_vb", "docket_number": docket,
            "source_url": PORTAL.format(pid), "full_text": text}


def test_quality_gate_expects_ch_vb():
    assert "ch_vb" in EXPECTED_FEDERAL


def test_entscheidsuche_spider_maps_exclude_ch_vb():
    assert "CH_VB" not in entscheidsuche_ingest.SPIDER_MAP
    assert "CH_VB" not in entscheidsuche_download.TIER_D_FEDERAL
    for name in ("TIER_A", "TIER_B", "TIER_C_SMALL", "TIER_E_GAPFILL"):
        assert "CH_VB" not in getattr(entscheidsuche_download, name)


def test_plan_keeps_only_prefix_15_and_folds_copies_to_the_real_docket():
    records = [
        _row("ch_vb_bbl", "10138080", "2004-2262 5421 "),            # Bundesblatt
        _row("ch_vb_ab", "20012345", "<td class=\"metadataCell\">20012345</td>"),
        _row("ch_vb__td_html", "150000020", '<td class="metadataCell">150000020</td>'),
        _row("ch_vb_150000020", "150000020", "150000020"),
        _row("ch_vb_frag", "150000020", "terne Abklärung ", text="y" * 900),
        _row("ch_vb_JAAC_70.103__", "150004711", "JAAC 70.103"),
        _row("ch_vb_150004711", "150004711", "150004711", text="z" * 5000),
    ]
    keep, dropped, folded = kv.plan(records)
    kept = [records[i]["decision_id"] for i in keep]
    # real VPB docket beats a longer text; the bare portal id beats a fragment
    assert kept == ["ch_vb_150000020", "ch_vb_JAAC_70.103__"]
    assert dropped == {"10": 1, "20": 1}
    assert folded == 3


def test_apply_writes_backup_and_keeps_lines_byte_identical(tmp_path):
    rows = [_row("ch_vb_bbl", "10138080", "x"),
            # U+2028 inside a string must not split the record
            _row("ch_vb_JAAC_51.1__", "150000001", "VPB 51.1", text="a b" * 300)]
    shard = tmp_path / "es_ch_vb.jsonl"
    lines = [json.dumps(r, ensure_ascii=False) + "\n" for r in rows]
    shard.write_text("".join(lines), encoding="utf-8")
    assert kv.main(["--shard", str(shard)]) == 0
    assert shard.read_text(encoding="utf-8") == "".join(lines)          # dry run
    assert kv.main(["--shard", str(shard), "--apply"]) == 0
    assert shard.read_text(encoding="utf-8") == lines[1]
    assert (tmp_path / ("es_ch_vb.jsonl" + kv.BACKUP_SUFFIX)).read_text(encoding="utf-8") == "".join(lines)
