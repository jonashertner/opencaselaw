"""scripts/ag_entscheidsammlungen_es_fold.py (offline, synthetic rows in the
shape of the frozen es rows and the direct scraper's rows)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import ag_entscheidsammlungen_es_fold as fold  # noqa: E402

M = "https://www.ag.ch/media/kanton-aargau/bvu/bauen/baurecht/entscheidsammlung-rechtsabteilung-bvu/"


def _direct(stem, date, text="x" * 2000):
    return {"decision_id": f"ag_baugesetzgebung_{stem}", "court": "ag_baugesetzgebung",
            "docket_number": stem, "decision_date": date, "pdf_url": M + stem + ".pdf",
            "source_url": M + stem + ".pdf", "full_text": text}


def _es(did, title, date, stem, text="kurz"):
    return {"decision_id": did, "court": "ag_baugesetzgebung", "docket_number": title,
            "decision_date": date, "pdf_url": M + stem + ".pdf", "source_url": M + stem + ".pdf",
            "full_text": text, "source": "entscheidsuche"}


def _fixture():
    direct = [_direct("1994-02-21-umweltschutzrecht", "1994-02-21"),
              _direct("1993-12-03-missbraeuchliche-prozessfuehrung", "1993-12-03")]
    es = [_es("ag_baugesetzgebung_Umweltschutzrecht", "Umweltschutzrecht", "1983-10-07",
              "1994-02-21-umweltschutzrecht"),
          _es("ag_baugesetzgebung_Kein_Direkter", "Kein Direkter", "1999-01-01", "1999-01-01-kein")]
    return direct, es


def test_plan_pairs_by_pdf():
    direct, es = _fixture()
    out = fold.plan(direct, es)
    by = {r["decision_id"]: r for r in out["direct"]}
    assert by["ag_baugesetzgebung_1994-02-21-umweltschutzrecht"]["previous_decision_id"] == \
        "ag_baugesetzgebung_Umweltschutzrecht"
    assert "previous_decision_id" not in by["ag_baugesetzgebung_1993-12-03-missbraeuchliche-prozessfuehrung"]
    assert [r["decision_id"] for r in out["quarantine"]] == ["ag_baugesetzgebung_Umweltschutzrecht"]
    assert [r["decision_id"] for r in out["es"]] == ["ag_baugesetzgebung_Kein_Direkter"]  # no twin: stays
    st = out["stats"]
    assert (st["pairs"], st["es_date_differs"], st["es_stub_replaced_by_full_text"],
            st["direct_without_es_copy"]) == (1, 1, 1, 1)
    assert fold.served(out["direct"], out["es"]) == 3 and fold.served(direct, es) == 4


def test_refuses_ambiguous_pdf_and_conflicting_alias():
    direct, es = _fixture()
    with pytest.raises(SystemExit):
        fold.plan(direct + [dict(direct[0], decision_id="other")], es)
    with pytest.raises(SystemExit):
        fold.plan(direct, es + [dict(es[0], decision_id="dup")])
    d = [dict(direct[0], previous_decision_id="something_else")]
    with pytest.raises(SystemExit):
        fold.plan(d, es)


def test_apply_and_rerun(tmp_path):
    dec, q = tmp_path / "decisions", tmp_path / "quarantine"
    dec.mkdir()
    direct, es = _fixture()
    (dec / "ag_baugesetzgebung.jsonl").write_text("".join(json.dumps(r) + "\n" for r in direct))
    (dec / "es_ag_baugesetzgebung.jsonl").write_text("".join(json.dumps(r) + "\n" for r in es))
    args = ["--court", "ag_baugesetzgebung", "--decisions-dir", str(dec), "--quarantine-dir", str(q)]
    assert fold.main(args) == 0                                     # dry run writes nothing
    assert not q.exists()
    assert fold.main(args + ["--apply"]) == 0
    qf = q / "es_ag_baugesetzgebung.quarantined.jsonl.txt"
    assert [json.loads(x)["decision_id"] for x in qf.read_text().splitlines()] == \
        ["ag_baugesetzgebung_Umweltschutzrecht"]
    es_after = [json.loads(x) for x in (dec / "es_ag_baugesetzgebung.jsonl").read_text().splitlines()]
    assert [r["decision_id"] for r in es_after] == ["ag_baugesetzgebung_Kein_Direkter"]
    snap = {p.name: p.read_text() for p in dec.glob("*.jsonl")}
    assert fold.main(args + ["--apply"]) == 0                       # re-run: no-op
    assert {p.name: p.read_text() for p in dec.glob("*.jsonl")} == snap
    assert len(qf.read_text().splitlines()) == 1
    assert not list(dec.glob("*.tmp"))


def test_refuses_quarantine_inside_decisions_dir(tmp_path):
    (tmp_path / "ag_weitere.jsonl").write_text("")
    with pytest.raises(SystemExit):
        fold.main(["--court", "ag_weitere", "--decisions-dir", str(tmp_path),
                   "--quarantine-dir", str(tmp_path)])


def test_refuses_without_direct_shard(tmp_path):
    with pytest.raises(SystemExit):
        fold.main(["--court", "ag_weitere", "--decisions-dir", str(tmp_path),
                   "--quarantine-dir", str(tmp_path / "q")])
