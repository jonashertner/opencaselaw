"""Offline tests for scripts/repair_source_dates.py: dry run by default,
only decision_date (+ stamp) changes, backups and atomic swap, idempotence,
the fill/correct split, and the dedup simulation."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

import repair_source_dates as R

TODAY = "2026-11-08"


def _write(path: Path, rows: list[dict]) -> Path:
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    return path


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _egmr(did, docket, d, court="bge_egmr", text="Arrêt du 23 août 2016 …"):
    return {"decision_id": did, "court": court, "docket_number": docket, "decision_date": d,
            "full_text": text * 3, "scraped_at": "2026-02-13T10:00:00"}


def _run(court, shards, *extra):
    import io
    from contextlib import redirect_stdout
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = R.main(["--court", court, *map(str, shards), "--today", TODAY, *extra])
    assert rc == 0
    return json.loads(buf.getvalue())


def test_dry_run_writes_nothing(tmp_path):
    shard = _write(tmp_path / "bge_egmr.jsonl", [_egmr("bge_egmr_20160823_53377_11", "20160823_53377_11", "2026-01-01")])
    before = shard.read_bytes()
    rep = _run("bge_egmr", [shard])
    assert rep["bge_egmr.jsonl"]["rows_changed"] == 1
    assert shard.read_bytes() == before
    assert not list(tmp_path.glob("*.bak-*")) and not list(tmp_path.glob("*.tmp"))


def test_apply_changes_only_the_date_stamps_and_is_idempotent(tmp_path):
    rows = [
        _egmr("bge_egmr_20160823_53377_11", "20160823_53377_11", "2026-01-01"),
        _egmr("bge_egmr_20240613_30995_19", "20240613_30995_19", "2020-11-18"),
        _egmr("bge_egmr_20260219_28865_17", "20260219_28865_17", "2026-02-19"),
        {"decision_id": "bge_150_I_1", "court": "bge", "docket_number": "150 I 1",
         "decision_date": "2024-01-01", "full_text": "x" * 50},
    ]
    shard = _write(tmp_path / "bge.jsonl", rows)
    rep = _run("bge_egmr", [shard], "--apply")
    assert rep["bge.jsonl"]["rows_changed"] == 2
    out = _rows(shard)
    assert len(out) == 4
    assert out[0]["decision_date"] == "2016-08-23"
    assert out[0]["date_repair"] == {"from": "2026-01-01", "to": "2016-08-23",
                                     "rule": "egmr_docket", "at": TODAY}
    assert out[1]["decision_date"] == "2024-06-13"   # body-text date of 03-12 corrected
    assert out[2] == rows[2] and out[3] == rows[3]    # untouched, other court untouched
    for a, b in zip(rows, out):
        assert {k for k in a if a[k] != b.get(k)} <= {"decision_date"}
    backups = list(tmp_path.glob("bge.jsonl.bak-dates-*"))
    assert len(backups) == 1 and _rows(backups[0]) == rows
    assert not list(tmp_path.glob("*.tmp"))
    rep2 = _run("bge_egmr", [shard])
    assert rep2["bge.jsonl"].get("rows_changed", 0) == 0


def test_apply_refuses_to_overwrite_a_backup(tmp_path):
    shard = _write(tmp_path / "bge_egmr.jsonl", [_egmr("bge_egmr_20160823_53377_11", "20160823_53377_11", None)])
    _run("bge_egmr", [shard], "--apply")
    _write(shard, [_egmr("bge_egmr_20160823_53377_11", "20160823_53377_11", None)])
    with pytest.raises(SystemExit, match="existing backup"):
        _run("bge_egmr", [shard], "--apply")
    assert not list(tmp_path.glob("*.tmp"))


def test_check_refuses_any_other_field_change():
    a = {"decision_id": "x", "decision_date": None, "full_text": "t"}
    with pytest.raises(SystemExit, match="full_text"):
        R.check(a, dict(a, full_text="u", decision_date="2020-01-02"))
    R.check(a, dict(a, decision_date="2020-01-02", date_repair={}))


def test_be_letterhead_corrects_only_pre_fix_rows_and_fill_only(tmp_path):
    def be(did, d, scraped):
        head = "Beschluss\nBK 25 85\n3001 Bern\nBern, 1. Dezember 2025\nBesetzung\nOberrichter X\n"
        return {"decision_id": did, "court": "be_zivilstraf", "docket_number": "BK 25 85",
                "decision_date": d, "scraped_at": scraped,
                "full_text": head + "Sachverhalt …" * 300 + "\nBern, 1. Dezember 2025\n"}
    rows = [be("a", "2025-01-28", "2026-02-13T01:00:00"),   # pre-fix: contested decision's date
            be("b", "2025-12-02", "2026-10-02T01:00:00"),   # post-fix: never second-guessed
            be("c", None, "2026-06-24T01:00:00")]
    shard = _write(tmp_path / "be_zivilstraf.jsonl", rows)
    rep = _run("be_zivilstraf", [shard])
    st = rep["be_zivilstraf.jsonl"]
    assert st["outcome:be_letterhead_and_signature:corrected"] == 1
    assert st["outcome:be_letterhead_and_signature:differs_left_alone"] == 1
    assert st["outcome:be_letterhead_and_signature:filled"] == 1
    rep = _run("be_zivilstraf", [shard], "--fill-only")
    st = rep["be_zivilstraf.jsonl"]
    assert st["rows_changed"] == 1 and "outcome:be_letterhead_and_signature:corrected" not in st


def test_text_rules_are_vetoed_when_implausible(tmp_path):
    # A head date before the docket's registration year is a cited date.
    row = {"decision_id": "nw_gerichte_VA 21 18", "court": "nw_gerichte", "docket_number": "VA 21 18",
           "decision_date": None, "scraped_at": "2026-06-24T01:00:00",
           "full_text": "GERICHTE\nVA 21 18\nEntscheid vom 16. Dezember 2019\nBesetzung X"}
    shard = _write(tmp_path / "nw_gerichte.jsonl", [row])
    rep = _run("nw_gerichte", [shard])
    assert rep["nw_gerichte.jsonl"]["outcome:nw_head:implausible_left_alone"] == 1


def test_gr_federation_stub_takes_its_twins_trailer_and_folds(tmp_path):
    url = "https://entscheidsuche.gr.ch/tribunavtplus/ServletDownload/PKG_1999_25_{}?path=p"
    text = ("PKG 1999 25\n…\nSB 99 71\nUrteil vom 14. Dezember 1999\n25 – …\n" + "y" * 3000
            + "\nSB 98 74\nUrteil vom 20. Januar 1999\n")
    direct = {"decision_id": "gr_gerichte_PKG 1999 25", "court": "gr_gerichte",
              "docket_number": "PKG 1999 25", "decision_date": "1999-12-14",
              "scraped_at": "2026-02-14T01:00:00", "pdf_url": url.format("ea3b"), "full_text": text}
    stub = {"decision_id": "gr_gerichte_PKG_1999_25", "court": "gr_gerichte",
            "docket_number": "PKG 1999 25", "decision_date": "0000-00-00",
            "scraped_at": "2026-03-12T01:00:00", "source_url": url.format("4d6e"),
            "full_text": "Graubünden Kantonsgericht PKG 00.00.0000 PKG 1999 25 | stub"}
    d = _write(tmp_path / "gr_gerichte.jsonl", [direct])
    e = _write(tmp_path / "es_gr_gerichte.jsonl", [stub])
    rep = _run("gr_gerichte", [d, e], "--simulate")
    assert rep["gr_gerichte.jsonl"]["outcome:gr_yearbook_trailer:corrected"] == 1
    assert rep["es_gr_gerichte.jsonl"]["outcome:gr_yearbook_twin_pdf:filled"] == 1
    sim = rep["simulation"]
    assert sim["served_before"] == 2 and sim["served_after"] == 1
    assert sim["new_pass1_folds"] == 1 and sim["swap_gate_ok"]   # < 500 rows: gate n/a


def test_ti_needs_evidence_and_fills_from_it(tmp_path):
    row = {"decision_id": "ti_gerichte_60.2012.58", "court": "ti_gerichte",
           "docket_number": "60.2012.58", "decision_date": None, "full_text": "Incarto n. 60.2012.58 Lugano",
           "source_url": "http://www.sentenze.ti.ch/x", "scraped_at": "2026-02-16T09:00:00"}
    shard = _write(tmp_path / "ti_gerichte.jsonl", [row])
    with pytest.raises(SystemExit, match="evidence"):
        _run("ti_gerichte", [shard])
    ev = _write(tmp_path / "ev.json", [{"decision_id": "ti_gerichte_60.2012.58",
                                        "data_decisione": "2012-05-07"}])
    rep = _run("ti_gerichte", [shard], "--evidence", str(ev), "--apply")
    assert rep["ti_gerichte.jsonl"]["outcome:ti_portal_data_decisione:filled"] == 1
    assert _rows(shard)[0]["decision_date"] == "2012-05-07"


def test_rejects_non_shard_paths(tmp_path, capsys):
    p = tmp_path / "x.txt"
    p.write_text("")
    assert R.main(["--court", "nw_gerichte", str(p)]) == 2


def test_bl_slug_confirmed_by_head_corrects_and_its_stub_follows(tmp_path):
    # bl_wayback.jsonl: the recovered PDF says "vom 25. Januar 2018", the file
    # is 2018-01-25-sv-7, the stored date is 2015-01-25 (copied into the
    # federation stub too). Both move; a row whose head names another day
    # than its slug stays as it is.
    def wb(did, d, head):
        return {"decision_id": did, "court": "bl_gerichte", "docket_number": did[len("bl_gerichte_"):],
                "decision_date": d, "scraped_at": "2026-04-17T14:37:06+00:00",
                "full_text": head + " Invalidenversicherung …" * 50}
    head = ("Entscheid des Kantonsgerichts Basel-Landschaft, Abteilung Sozialversiche- rungsrecht\n"
            "vom 25. Januar 2018 (720 17 220 / 86)\n")
    rows = [wb("bl_gerichte_2018-01-25-sv-7", "2015-01-25", head),
            wb("bl_gerichte_2018-05-18-sv-2", "2018-02-08", head.replace("25. Januar", "8. Mai"))]
    stub = {"decision_id": "bl_gerichte_2018-01-25-sv-7", "court": "bl_gerichte",
            "docket_number": "2018-01-25-sv-7", "decision_date": "2015-01-25",
            "scraped_at": "2026-02-13T09:37:26Z", "full_text": "Basel-Landschaft Kantonsgericht 2018-01-25-sv-7 | stub"}
    w = _write(tmp_path / "bl_wayback.jsonl", rows)
    e = _write(tmp_path / "es_bl_gerichte.jsonl", [stub])
    rep = _run("bl_gerichte", [w, e], "--simulate")
    assert rep["bl_wayback.jsonl"]["outcome:bl_slug_and_head:corrected"] == 1
    assert rep["bl_wayback.jsonl"]["outcome:bl_slug:differs_left_alone"] == 1
    assert rep["es_bl_gerichte.jsonl"]["outcome:twin_follow:corrected"] == 1
    assert rep["simulation"]["twins_split"] == 0
    _run("bl_gerichte", [w, e], "--apply")
    assert [r["decision_date"] for r in _rows(w)] == ["2018-01-25", "2018-02-08"]
    assert _rows(e)[0]["decision_date"] == "2018-01-25"
