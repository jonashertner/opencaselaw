"""scripts/zh_brg_remove_republications.py (2026-09-15): offline, on a temporary shard."""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import scripts.zh_brg_remove_republications as rr  # noqa: E402

HELD = {"decision_id": "zh_baurekursgericht_BRGE II Nrn. 0053-0054_2022", "docket_number": "BRGE II Nrn. 0053-0054/2022",
        "decision_date": "2022-03-15", "full_text": "Baurekursgericht ..."}
REPUB = {"decision_id": "zh_baurekursgericht_Zwischenentscheid 2023-13", "docket_number": "Zwischenentscheid 2023-13",
         "decision_date": "2022-03-15", "full_text": "BRGE II Nrn. 0053/2022 - 0054/2022 vom 15. März 2022 in BEZ 2023 Nr. 13 ..."}
OTHER_DATE = {"decision_id": "zh_baurekursgericht_Zwischenentscheid 2022-99", "docket_number": "Zwischenentscheid 2022-99",
              "decision_date": "2021-12-10", "full_text": "BRGE II Nr. 0054/2022 vom 10. Dezember 2021 ..."}
PRAESIDIAL = {"decision_id": "zh_baurekursgericht_Zwischenentscheid BRG_1476176437", "docket_number": "Zwischenentscheid BRG_1476176437",
              "decision_date": "2014-10-08", "full_text": "Präsidialverfügung vom 21. August 2014 im Verfahren G.-Nr. R4.2014.00115 ..."}
NUMBERED_CITING = {"decision_id": "zh_baurekursgericht_BRGE I Nr. 0007_2026", "docket_number": "BRGE I Nr. 0007/2026",
                   "decision_date": "2022-03-15", "full_text": "BRGE II Nrn. 0053/2022 - 0054/2022 vom 15. März 2022 (zitiert) ..."}


def _shard(tmp_path):
    p = tmp_path / "zh_baurekursgericht.jsonl"
    p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in (HELD, REPUB, OTHER_DATE, PRAESIDIAL, NUMBERED_CITING)),
                 encoding="utf-8")
    return p


def test_plan_removes_only_same_date_republications():
    removals, refusals = rr.plan([HELD, REPUB, OTHER_DATE, PRAESIDIAL, NUMBERED_CITING])
    assert [r[0] for r in removals] == [REPUB["decision_id"]]
    assert [r[0] for r in refusals] == [OTHER_DATE["decision_id"]]


def test_dry_run_writes_nothing(tmp_path):
    p = _shard(tmp_path)
    before = p.read_bytes()
    assert rr.main(["--shard", str(p)]) == 0
    assert p.read_bytes() == before
    assert not (tmp_path / ("zh_baurekursgericht.jsonl" + rr.BACKUP_SUFFIX)).exists()


def test_apply_removes_the_row_and_keeps_a_backup(tmp_path):
    p = _shard(tmp_path)
    before = p.read_bytes()
    assert rr.main(["--shard", str(p), "--apply"]) == 0
    ids = [json.loads(l)["decision_id"] for l in p.read_text(encoding="utf-8").splitlines()]
    assert REPUB["decision_id"] not in ids and len(ids) == 4
    assert (tmp_path / ("zh_baurekursgericht.jsonl" + rr.BACKUP_SUFFIX)).read_bytes() == before
    assert rr.main(["--shard", str(p), "--apply"]) == 0          # idempotent: nothing left to remove
    assert len(p.read_text(encoding="utf-8").splitlines()) == 4
