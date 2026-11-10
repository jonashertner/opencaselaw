"""tg_drop_legacy_ids: drop the legacy-id twin of a TG yearbook row, keep its id as an alias."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import tg_drop_legacy_ids as T

URL = "https://rechtsprechung.tg.ch/entscheid/123"


def row(did, docket="RBOG-2017-15", text="Regeste und Text", **kw):
    r = {"decision_id": did, "court": "tg_gerichte", "docket_number": docket,
         "decision_date": "2017-05-04", "full_text": text, "source_url": URL}
    r.update(kw)
    return r


def lines(*rows):
    return [json.dumps(r, ensure_ascii=False) + "\n" for r in rows]


def test_pair_drops_legacy_and_records_alias():
    rows, drop, alias, refusing = T.plan(lines(
        row("tg_gerichte_ZBR.2010.70"), row("tg_gerichte_RBOG-2017-15"),
        {**row("tg_gerichte_X"), "source_url": "other"}))
    assert refusing == []
    assert drop == {0}
    assert alias == {1: "tg_gerichte_ZBR.2010.70"}


@pytest.mark.parametrize("legacy, keeper, reason", [
    (row("tg_gerichte_ZBR.2010.70", text="anders"), row("tg_gerichte_RBOG-2017-15"), "full_text differs"),
    (row("tg_gerichte_ZBR.2010.70", decision_date="2010-01-01"), row("tg_gerichte_RBOG-2017-15"), "decision_date differs"),
    (row("tg_gerichte_ZBR.2010.70"), row("tg_gerichte_RBOG-2017-15", previous_decision_id="x"), "already has"),
    (row("tg_gerichte_A"), row("tg_gerichte_B"), "no single yearbook-id row"),
])
def test_refuses_unless_exact_twins(legacy, keeper, reason):
    _, drop, alias, refusing = T.plan(lines(legacy, keeper))
    assert not drop and not alias
    assert len(refusing) == 1 and reason in refusing[0]


def test_three_rows_on_one_url_refused():
    _, drop, _, refusing = T.plan(lines(row("tg_gerichte_A"), row("tg_gerichte_RBOG-2017-15"), row("tg_gerichte_B")))
    assert not drop and "3 rows" in refusing[0]


def test_apply_rewrites_with_backup_and_is_idempotent(tmp_path, monkeypatch):
    shard = tmp_path / "tg_gerichte.jsonl"
    shard.write_text("".join(lines(row("tg_gerichte_ZBR.2010.70"), row("tg_gerichte_RBOG-2017-15"))), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["x", "--shard", str(shard), "--expect", "1", "--apply"])
    T.main()
    out = [json.loads(l) for l in shard.read_text(encoding="utf-8").splitlines()]
    assert [r["decision_id"] for r in out] == ["tg_gerichte_RBOG-2017-15"]
    assert out[0]["previous_decision_id"] == "tg_gerichte_ZBR.2010.70"
    assert out[0]["previous_id_source"] == "tg_legacy_id"
    assert len(list(tmp_path.glob("tg_gerichte.jsonl.bak-tgids-*"))) == 1
    assert not (tmp_path / "tg_gerichte.jsonl.tmp").exists()
    monkeypatch.setattr(sys, "argv", ["x", "--shard", str(shard), "--expect", "0"])
    T.main()  # nothing left, no exit


def test_wrong_pair_count_stops_before_writing(tmp_path, monkeypatch):
    shard = tmp_path / "tg_gerichte.jsonl"
    body = "".join(lines(row("tg_gerichte_ZBR.2010.70"), row("tg_gerichte_RBOG-2017-15")))
    shard.write_text(body, encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["x", "--shard", str(shard), "--apply"])  # expect defaults to 25
    with pytest.raises(SystemExit):
        T.main()
    assert shard.read_text(encoding="utf-8") == body
