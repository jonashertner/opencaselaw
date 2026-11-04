"""scripts/vd_docket_repair.py: held vd_gerichte rows get the docket the scraper
now writes, and keep their dedup key. Offline, on a throwaway shard."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

import vd_docket_repair as rep  # noqa: E402

U1 = "02949903-ae38-4ad9-833b-7dce612af97d"
U2 = "ffa44e63-115b-472f-b69a-12896bbe141e"
U3 = "0a1b2c3d-0000-4000-8000-000000000003"
U4 = "0a1b2c3d-0000-4000-8000-000000000004"
U5 = "0a1b2c3d-0000-4000-8000-000000000005"


def header(*lines):
    return "\n".join(lines) + "\nArrêt du 19 avril 2021\n__________\nComposition : M. X, président\n"


def row(uuid, docket, text, chamber="CREP", date="2021-04-19", **extra):
    r = {"decision_id": f"vd_gerichte_{uuid}", "court": "vd_gerichte", "canton": "VD",
         "chamber": chamber, "docket_number": docket, "decision_date": date,
         "full_text": text, "scraped_at": "2026-09-05T01:00:00+00:00",
         "pdf_url": f"https://prestations.vd.ch/pub/blv-publication/api/decision/download/{uuid}"}
    r.update(extra)
    return r


ROWS = [
    row(U1, "344", header("TRIBUNAL CANTONAL", "344", "DA21.000826-LAS", "CHAMBRE")),
    row(U2, "876", header("TRIBUNAL CANTONAL", "876", "PE25.***-***", "CHAMBRE"), date="2025-12-01"),
    row(U3, "111/I", header("TRIBUNAL CANTONAL", "CHAMBRE"), chamber="CREC"),
    row(U4, U4, header("TRIBUNAL CANTONAL", "CHAMBRE")),
    # a docket from the listing, before 2026-09-04: never touched
    {**row(U5, "ZD17.028583", header("TRIBUNAL CANTONAL", "ZD17.028583", "CHAMBRE")),
     "decision_id": "vd_gerichte_ZD17.028583"},
    {"decision_id": "ge_gerichte_X", "court": "ge_gerichte", "docket_number": "344",
     "decision_date": "2021-04-19", "full_text": "x"},
]


@pytest.fixture
def shard(tmp_path):
    p = tmp_path / "decisions" / "vd_gerichte.jsonl"
    p.parent.mkdir()
    p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in ROWS)
                 + '{"torn line\n', encoding="utf-8")
    return p


def records(p):
    out = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        out[r["decision_id"]] = r
    return out


def test_dry_run_counts_and_writes_nothing(shard):
    before = shard.read_bytes()
    stats = rep.run(shard, apply=False)
    assert shard.read_bytes() == before
    assert stats["rows_changed"] == 4
    assert stats["outcome:header"] == 1
    assert stats["outcome:chamber_year_number"] == 2
    assert stats["outcome:stays_uuid"] == 1
    assert stats["outcome:real_docket"] == 1
    assert stats["outcome:other_court"] == 1
    assert stats["unparsed"] == 1
    assert sorted(p.name for p in shard.parent.iterdir()) == ["vd_gerichte.jsonl"]


def test_apply_rewrites_dockets_and_keeps_every_key(shard):
    old = records(shard)
    rep.run(shard, apply=True)
    new = records(shard)
    assert new[f"vd_gerichte_{U1}"]["docket_number"] == "DA21.000826"
    assert new[f"vd_gerichte_{U1}"]["docket_number_2"] == "344"
    assert new[f"vd_gerichte_{U2}"]["docket_number"] == "CREP 2025/876"
    assert new[f"vd_gerichte_{U3}"]["docket_number"] == "CREC 2021/111/I"
    assert new[f"vd_gerichte_{U4}"]["docket_number"] == U4
    assert new[f"vd_gerichte_{U4}"]["docket_number_2"] == U4
    assert new["vd_gerichte_ZD17.028583"] == old["vd_gerichte_ZD17.028583"]
    assert new["ge_gerichte_X"] == old["ge_gerichte_X"]
    for did, r in old.items():
        assert rep.dedup_key(new[did]) == rep.dedup_key(r)
        assert new[did]["full_text"] == r.get("full_text")
    assert len(shard.read_text(encoding="utf-8").splitlines()) == len(ROWS) + 1
    # the backup is the old file; nothing new matches the build's *.jsonl glob
    names = sorted(p.name for p in shard.parent.iterdir())
    assert [n for n in names if n.endswith(".jsonl")] == ["vd_gerichte.jsonl"]
    (bak,) = [n for n in names if ".bak-" in n]
    assert records(shard.parent / bak) == old


def test_a_second_run_changes_nothing(shard):
    rep.run(shard, apply=True)
    for p in shard.parent.glob("*.bak-*"):
        p.unlink()
    assert rep.run(shard, apply=False)["rows_changed"] == 0


def test_refuses_without_the_build_line(shard, monkeypatch):
    monkeypatch.setattr(rep, "DECISION_NUMBER_COURTS", frozenset())
    before = shard.read_bytes()
    with pytest.raises(SystemExit, match="DECISION_NUMBER_COURTS"):
        rep.run(shard, apply=True)
    assert shard.read_bytes() == before


def test_refuses_a_changed_dedup_key_and_leaves_no_temp_file(shard, monkeypatch):
    real = rep.repair

    def broken(rec):
        new, kind = real(rec)
        if new is not None:
            new = {**new, "docket_number_2": None}
        return new, kind

    monkeypatch.setattr(rep, "repair", broken)
    before = shard.read_bytes()
    with pytest.raises(SystemExit, match="dedup key"):
        rep.run(shard, apply=True)
    assert shard.read_bytes() == before
    assert sorted(p.name for p in shard.parent.iterdir()) == ["vd_gerichte.jsonl"]
