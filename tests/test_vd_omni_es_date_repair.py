"""scripts/vd_omni_es_date_repair.py: offline, on a synthetic shard shaped like
es_vd_omni.jsonl (rows as the entscheidsuche ingest wrote them)."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO))

import vd_omni_es_date_repair as R  # noqa: E402

HEADER = "Vaud Tribunal cantonal Cour de droit administratif et public {d} {dk}\n\nRésumé\n\n"


def row(dk, field_date, header_date, body="CANTON DE VAUD\nTRIBUNAL ADMINISTRATIF\n..."):
    return {"decision_id": f"vd_omni_{dk}", "court": "vd_omni", "canton": "VD", "docket_number": dk,
            "decision_date": field_date, "regeste": "X | Résumé", "source": "entscheidsuche",
            "full_text": HEADER.format(d=header_date, dk=dk) + body}


def write(tmp_path, rows, listed):
    shard = tmp_path / "es_vd_omni.jsonl"
    shard.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    lst = tmp_path / "list.tsv"
    lst.write_text("es_decision_id\tes_date\tportal_date\tnF30_KEY\n"
                   + "".join(f"{a}\t{b}\t{c}\t1\n" for a, b, c in listed), encoding="utf-8")
    return shard, R.load_list(lst)


def test_dry_run_reports_and_writes_nothing(tmp_path):
    shard, wanted = write(tmp_path, [row("AC.1990.6992", "1990-08-06", "10.07.1991"),
                                     row("AC.2019.0145", "2019-07-04", "04.07.2019")],
                          [("vd_omni_AC.1990.6992", "1990-08-06", "1991-07-10")])
    before = shard.read_bytes()
    st = R.run(shard, wanted, apply=False)
    assert st["rows_changed"] == 1 and st["outcome:confirmed_by_header"] == 1
    assert shard.read_bytes() == before and not list(tmp_path.glob("*.bak-*"))


def test_apply_changes_only_the_date_and_keeps_a_hardlinked_backup(tmp_path):
    rows = [row("AC.1990.6992", "1990-08-06", "10.07.1991"), row("AC.2019.0145", "2019-07-04", "04.07.2019")]
    shard, wanted = write(tmp_path, rows, [("vd_omni_AC.1990.6992", "1990-08-06", "1991-07-10")])
    ino = os.stat(shard).st_ino
    st = R.run(shard, wanted, apply=True)
    assert st["rows_changed"] == 1 and st["lines"] == 2
    out = [json.loads(l) for l in shard.read_text(encoding="utf-8").splitlines()]
    assert out[0]["decision_date"] == "1991-07-10"
    assert {k: v for k, v in out[0].items() if k != "decision_date"} == \
           {k: v for k, v in rows[0].items() if k != "decision_date"}
    assert out[1] == rows[1]
    bak = next(tmp_path.glob("es_vd_omni.jsonl.bak-*"))
    assert os.stat(bak).st_ino == ino  # the original inode, untouched
    assert not list(tmp_path.glob("*.tmp"))
    # idempotent: the date no longer matches the listed es date
    assert R.run(shard, wanted, apply=False)["rows_changed"] == 0


def test_a_date_confirmed_only_by_the_rulings_own_text(tmp_path):
    # FO.2023.0004: field and header 2023-06-27, the ruling and the portal say 2023-07-11
    r = row("FO.2023.0004", "2023-06-27", "27.06.2023",
            body="TRIBUNAL CANTONAL\nArrêt du 11 juillet 2023\n...\nLausanne, le 11 juillet 2023\n")
    shard, wanted = write(tmp_path, [r], [("vd_omni_FO.2023.0004", "2023-06-27", "2023-07-11")])
    assert R.run(shard, wanted, apply=False)["outcome:confirmed_by_text"] == 1


def test_an_unconfirmed_date_is_left_alone(tmp_path):
    shard, wanted = write(tmp_path, [row("AC.2000.0001", "2000-01-05", "05.01.2000")],
                          [("vd_omni_AC.2000.0001", "2000-01-05", "2000-03-01")])
    st = R.run(shard, wanted, apply=True)
    assert st["rows_changed"] == 0 and st["outcome:unconfirmed"] == 1


def test_refuses_a_correction_that_collides_with_another_row(tmp_path):
    rows = [row("PS.1996.0189", "1997-01-20", "09.01.1997"),
            {**row("PS.1996.0189", "1997-01-09", "09.01.1997"), "decision_id": "vd_omni_PS.1996.0189_x"}]
    shard, wanted = write(tmp_path, rows, [("vd_omni_PS.1996.0189", "1997-01-20", "1997-01-09")])
    with pytest.raises(SystemExit, match="collide"):
        R.run(shard, wanted, apply=True)
    assert not list(tmp_path.glob("*.bak-*"))


def test_the_committed_list_is_well_formed():
    wanted = R.load_list(R.DEFAULT_LIST)
    assert len(wanted) == 825
    assert all(k.startswith("vd_omni_") and a != b for k, (a, b) in wanted.items())


def test_a_date_checked_on_the_portal_by_hand(tmp_path):
    # FO.2023.0004: the es copy is the earlier version dated 27 juin; the
    # portal's re-issued ruling is of 11 juillet 2023
    r = row("FO.2023.0004", "2023-06-27", "27.06.2023",
            body="TRIBUNAL CANTONAL\nArrêt du 27 juin 2023\n...\nLausanne, le\n")
    shard, wanted = write(tmp_path, [r], [("vd_omni_FO.2023.0004", "2023-06-27", "2023-07-11")])
    assert R.run(shard, wanted, apply=False)["outcome:confirmed_on_portal"] == 1
