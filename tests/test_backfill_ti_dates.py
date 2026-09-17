"""TI date backfill: harvest from the document page (mocked), apply to the shard.

Why not the text we hold: measured 2026-09-16, backfill_dates._extract_date reproduces a
known TI date only 85.6% of the time, and a year guard cannot catch same-year wrong days
(72.2013.80: text 2013-07-09, page 2013-11-05). The structured page cell is the source.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

spec = importlib.util.spec_from_file_location("backfill_ti_dates", REPO / "scripts" / "backfill_ti_dates.py")
bti = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bti)


def _target(docket, key="1"):
    return {"decision_id": f"ti_gerichte_{docket}", "docket_number": docket,
            "source_url": f"http://www.sentenze.ti.ch/x?nF30_KEY={key}"}


def _row(did, date=None):
    return {"decision_id": did, "docket_number": did.split("_", 2)[-1], "court": "ti_gerichte",
            "decision_date": date, "full_text": "In nome della Repubblica"}


def _write_jsonl(path, rows):
    with open(path, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return path


# -- harvest ---------------------------------------------------------------------------

def test_harvest_writes_only_successes_and_is_resumable(tmp_path):
    out = tmp_path / "dates.jsonl"
    pages = {"1": SimpleNamespace(decision_date="2013-11-05", chamber="PENAL"),
             "2": None,                                                  # fetch failed
             "3": SimpleNamespace(decision_date=None, chamber="CRPTI")}   # page without a date
    calls = []

    def fetch(stub):
        key = stub["url"].split("=")[-1]
        calls.append(key)
        return pages[key]

    targets = [_target("72.2013.80", "1"), _target("60.2012.250", "2"), _target("14.2011.9", "3")]
    stats = bti.harvest(targets, out, fetch, log=lambda *a, **k: None)
    assert stats["dated"] == 1 and stats["fetch_failed"] == 1 and stats["no_date"] == 1
    written = [json.loads(l) for l in out.read_text().splitlines()]
    assert written == [{"decision_id": "ti_gerichte_72.2013.80", "docket_number": "72.2013.80",
                        "decision_date": "2013-11-05", "chamber": "PENAL"}]
    failed = [json.loads(l) for l in (tmp_path / "dates.jsonl.failed").read_text().splitlines()]
    assert {f["decision_id"] for f in failed} == {"ti_gerichte_60.2012.250", "ti_gerichte_14.2011.9"}

    # second run: the success is skipped, the two failures are retried
    calls.clear()
    stats = bti.harvest(targets, out, fetch, log=lambda *a, **k: None)
    assert stats["already_done"] == 1
    assert calls == ["2", "3"]


def test_harvest_refuses_a_date_before_the_docket_year(tmp_path):
    """A decision cannot predate its own registration; such a date is a cited document's."""
    out = tmp_path / "dates.jsonl"
    fetch = lambda stub: SimpleNamespace(decision_date="2005-04-01", chamber="PENAL")
    stats = bti.harvest([_target("72.2008.55")], out, fetch, log=lambda *a, **k: None)
    assert stats["rejected"] == 1 and stats["dated"] == 0
    assert not out.exists() or out.read_text() == ""


def test_harvest_accepts_date_objects_and_survives_exceptions(tmp_path):
    import datetime
    out = tmp_path / "dates.jsonl"

    def fetch(stub):
        if stub["docket_number"] == "9.2020.1":
            raise ConnectionError("portal down")
        return SimpleNamespace(decision_date=datetime.date(2020, 3, 5), chamber=None)

    stats = bti.harvest([_target("9.2020.1", "a"), _target("9.2020.2", "b")], out, fetch,
                        log=lambda *a, **k: None)
    assert stats["fetch_failed"] == 1 and stats["dated"] == 1
    assert json.loads(out.read_text())["decision_date"] == "2020-03-05"


def test_harvest_limit_takes_the_first_pending_only(tmp_path):
    out = tmp_path / "dates.jsonl"
    fetch = lambda stub: SimpleNamespace(decision_date="2021-01-01", chamber=None)
    targets = [_target(f"1.2021.{i}", str(i)) for i in range(5)]
    stats = bti.harvest(targets, out, fetch, limit=2, log=lambda *a, **k: None)
    assert stats["dated"] == 2


# -- apply -----------------------------------------------------------------------------

def test_apply_patches_both_copies_of_a_duplicate_and_nothing_else(tmp_path):
    shard = _write_jsonl(tmp_path / "ti_gerichte.jsonl", [
        _row("ti_gerichte_72.2013.80"),
        _row("ti_gerichte_72.2013.80"),            # the build keeps one of these arbitrarily
        _row("ti_gerichte_60.2012.250"),            # undated, not in the sidecar
        _row("ti_gerichte_99.2020.1", "2020-05-05"),  # already dated, sidecar must not override
    ])
    dates = _write_jsonl(tmp_path / "dates.jsonl", [
        {"decision_id": "ti_gerichte_72.2013.80", "docket_number": "72.2013.80", "decision_date": "2013-11-05"},
        {"decision_id": "ti_gerichte_99.2020.1", "docket_number": "99.2020.1", "decision_date": "2021-01-01"},
    ])
    out, stats = bti.plan_apply(shard, bti.load_dates(dates))
    rows = [json.loads(l) for l in out]
    assert stats["patched"] == 2 and stats["ids_patched"] == 1 and stats["duplicate_rows_patched"] == 1
    assert rows[0]["decision_date"] == rows[1]["decision_date"] == "2013-11-05"
    assert rows[2]["decision_date"] is None and stats["still_undated"] == 1
    assert rows[3]["decision_date"] == "2020-05-05"


def test_apply_skips_a_sidecar_date_before_the_docket_year(tmp_path):
    shard = _write_jsonl(tmp_path / "ti_gerichte.jsonl", [_row("ti_gerichte_72.2008.55")])
    dates = _write_jsonl(tmp_path / "dates.jsonl",
                         [{"decision_id": "ti_gerichte_72.2008.55", "docket_number": "72.2008.55",
                           "decision_date": "2005-04-01"}])
    assert bti.load_dates(dates) == {}
    out, stats = bti.plan_apply(shard, bti.load_dates(dates))
    assert stats["patched"] == 0 and stats["still_undated"] == 1


def test_untouched_lines_are_copied_byte_for_byte(tmp_path):
    shard = tmp_path / "ti_gerichte.jsonl"
    exotic = '{"decision_id":"ti_gerichte_x","decision_date":"2001-01-01","full_text":"x",  "spacing":1}\n'
    shard.write_text(exotic, encoding="utf-8")
    out, stats = bti.plan_apply(shard, {"ti_gerichte_x": "2009-09-09"})
    assert out == [exotic]


def test_cli_dry_run_writes_nothing_and_apply_keeps_a_backup(tmp_path, capsys):
    shard = _write_jsonl(tmp_path / "ti_gerichte.jsonl", [_row("ti_gerichte_72.2013.80")])
    dates = _write_jsonl(tmp_path / "dates.jsonl",
                         [{"decision_id": "ti_gerichte_72.2013.80", "docket_number": "72.2013.80",
                           "decision_date": "2013-11-05"}])
    before = shard.read_bytes()
    assert bti.main(["apply", "--dates", str(dates), "--shard", str(shard)]) == 0
    assert shard.read_bytes() == before and "dry run" in capsys.readouterr().out
    assert bti.main(["apply", "--dates", str(dates), "--shard", str(shard), "--apply"]) == 0
    assert json.loads(shard.read_text())["decision_date"] == "2013-11-05"
    backup = shard.with_name(shard.name + bti.BACKUP_SUFFIX)
    assert backup.exists() and backup.read_bytes() == before


def test_unparseable_line_is_copied_not_dropped(tmp_path):
    shard = tmp_path / "ti_gerichte.jsonl"
    shard.write_text("not json at all\n", encoding="utf-8")
    out, stats = bti.plan_apply(shard, {})
    assert out == ["not json at all\n"] and stats["bad_json"] == 1


# -- overwrite mode (the full re-fetch: the Feb-Mar 2026 portal index gave ~22% of TI rows a wrong date)

def test_overwrite_replaces_a_wrong_stored_date_but_default_mode_does_not(tmp_path):
    shard = _write_jsonl(tmp_path / "ti_gerichte.jsonl", [_row("ti_gerichte_32.2019.51", "2019-01-28")])
    dates = {"ti_gerichte_32.2019.51": "2020-06-30"}
    out, stats = bti.plan_apply(shard, dates)                       # default: fill only
    assert stats["replaced"] == 0 and stats["unchanged"] == 1
    assert json.loads(out[0])["decision_date"] == "2019-01-28"
    out, stats = bti.plan_apply(shard, dates, overwrite=True)
    assert stats["replaced"] == 1 and stats["patched"] == 1
    assert json.loads(out[0])["decision_date"] == "2020-06-30"


def test_overwrite_leaves_matching_rows_byte_for_byte(tmp_path):
    shard = tmp_path / "ti_gerichte.jsonl"
    exotic = '{"decision_id":"ti_gerichte_1.2020.1","docket_number":"1.2020.1","decision_date":"2020-06-30",  "x":1}\n'
    shard.write_text(exotic, encoding="utf-8")
    out, stats = bti.plan_apply(shard, {"ti_gerichte_1.2020.1": "2020-06-30"}, overwrite=True)
    assert out == [exotic] and stats["unchanged"] == 1 and stats["patched"] == 0


def test_chambers_replace_numeric_or_empty_but_never_a_real_one(tmp_path):
    rows = [dict(_row("ti_gerichte_1.2020.1", "2020-06-30"), chamber="30"),
            dict(_row("ti_gerichte_1.2020.2", "2020-06-30"), chamber=None),
            dict(_row("ti_gerichte_1.2020.3", "2020-06-30"), chamber="TCA")]
    shard = _write_jsonl(tmp_path / "ti_gerichte.jsonl", rows)
    chambers = {"ti_gerichte_1.2020.1": "TCA", "ti_gerichte_1.2020.2": "TRAM", "ti_gerichte_1.2020.3": "CEF"}
    out, stats = bti.plan_apply(shard, {}, chambers=chambers)
    got = [json.loads(l)["chamber"] for l in out]
    assert got == ["TCA", "TRAM", "TCA"] and stats["chamber_set"] == 2


def test_sidecar_loader_drops_numeric_chambers_and_bad_dates(tmp_path):
    side = _write_jsonl(tmp_path / "dates.jsonl", [
        {"decision_id": "a", "docket_number": "1.2020.1", "decision_date": "2020-06-30", "chamber": "30"},
        {"decision_id": "b", "docket_number": "1.2020.2", "decision_date": "2005-01-01", "chamber": "TCA"},
    ])
    dates, chambers = bti.load_sidecar(side)
    assert dates == {"a": "2020-06-30"} and chambers == {"b": "TCA"}


def test_cli_overwrite_and_chambers_flags(tmp_path, capsys):
    shard = _write_jsonl(tmp_path / "ti_gerichte.jsonl", [dict(_row("ti_gerichte_32.2019.51", "2019-01-28"), chamber="30")])
    dates = _write_jsonl(tmp_path / "dates.jsonl", [{"decision_id": "ti_gerichte_32.2019.51", "docket_number": "32.2019.51",
                                                      "decision_date": "2020-06-30", "chamber": "TCA"}])
    assert bti.main(["apply", "--dates", str(dates), "--shard", str(shard), "--overwrite", "--chambers", "--apply"]) == 0
    r = json.loads(shard.read_text())
    assert r["decision_date"] == "2020-06-30" and r["chamber"] == "TCA"


def test_a_date_in_the_future_is_refused():
    """The page cell is typed by hand; a year like 2062 must not reach the shard."""
    assert bti.date_ok("2026-09-17", "80.2022.9", today="2026-09-17")
    assert not bti.date_ok("2026-09-18", "80.2022.9", today="2026-09-17")
    assert not bti.date_ok("2062-03-01", None, today="2026-09-17")
    assert bti.date_ok("1998-03-01", None)
