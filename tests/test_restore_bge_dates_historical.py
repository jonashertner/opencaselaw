"""restore_bge_dates_from_urteilskopf.py on bge_historical rows (2026-09-27).

The 2026-03-12 date pass rewrote 649 rows of bge_historical.jsonl (volumes
1-79, OCR text) with body dates the OCR had garbled: "Arrêt du 6 avril 1980"
for BGE 26 II 254 of 1900. Every such row still records the value it replaced
in date_extraction.metadata_date. The script skipped the court, so the rows
kept the wrong year; it now restores them and leaves every other row alone.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
for p in (REPO, REPO / "scripts"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import restore_bge_dates_from_urteilskopf as r  # noqa: E402

_GARBLED = {
    "decision_id": "bge_historical_26_II_254", "court": "bge_historical",
    "docket_number": "26_II_254", "decision_date": "1980-04-06",
    "publication_date": "1900-01-01",
    "full_text": "254 \nCi vilrechtspflege . \n36. Ar/'et du 6 avril 1980, dans la cause",
    "date_extraction": {"method": "bare_header", "raw_match": "6 avril 1980",
                        "metadata_date": "1900-01-01"},
}
_FINE = {
    "decision_id": "bge_historical_26_II_300", "court": "bge_historical",
    "docket_number": "26_II_300", "decision_date": "1900-05-12",
    "publication_date": None, "full_text": "300 Urteil vom 12. Mai 1900",
}
_OTHER = {"decision_id": "bge_egmr_x", "court": "bge_egmr", "docket_number": "x",
          "decision_date": "1980-04-06"}


def test_decide_restores_the_replaced_value_of_a_garbled_historical_row():
    assert r.decide(_GARBLED)[:2] == ("metadata", "1900-01-01")
    assert r.decide(_FINE)[:2] == ("keep", None)


def test_apply_rewrites_only_the_garbled_historical_row(tmp_path):
    shard = tmp_path / "bge_historical.jsonl"
    rows = [_GARBLED, _FINE, _OTHER]
    shard.write_text("".join(json.dumps(o, ensure_ascii=False) + "\n" for o in rows))
    stats = r.run(shard, apply=True, examples=0)
    assert stats["bge_rows"] == 2 and stats["other_court"] == 1
    assert stats["rows_changed"] == 1 and stats["bad_fixed"] == 1
    out = [json.loads(line) for line in shard.read_text().splitlines()]
    assert out[0]["decision_date"] == "1900-01-01"
    assert out[0]["publication_date"] is None          # the 03-12 pass had planted it
    assert out[0]["date_restore"]["from"] == "1980-04-06"
    assert out[1] == _FINE and out[2] == _OTHER
