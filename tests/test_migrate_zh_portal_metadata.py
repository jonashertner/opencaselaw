"""scripts/migrate_zh_portal_metadata.py — the shard follows the portal listing."""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

import migrate_zh_portal_metadata as mig  # noqa: E402


def _row(did, court, docket, doc, **kw):
    return {"decision_id": did, "court": court, "docket_number": docket, "chamber": None,
            "external_id": f"zh_gerichte_{doc}" if doc else None, "regeste": None,
            "appeal_info": None, "decision_type": "Urteil", **kw}


def _stub(doc, court, chamber=None, **kw):
    return {"doc_id": str(doc), "court_code": court, "chamber": chamber, "leitsatz": "",
            "verweise": "", "entscheidart": "Urteil", **kw}


def _run(tmp_path, rows, listing, write=False):
    shard = tmp_path / "zh.jsonl"
    shard.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    if not write:
        return mig.migrate(shard, listing), None
    out = tmp_path / "out.jsonl"
    with open(out, "w", encoding="utf-8") as f:
        res = mig.migrate(shard, listing, f)
    return res, [json.loads(ln) for ln in out.read_text(encoding="utf-8").splitlines()]


def test_district_ruling_moves_to_its_court_and_keeps_the_old_id(tmp_path):
    rows = [_row("zh_arbeitsgericht_AH260006", "zh_arbeitsgericht", "AH260006", 41914)]
    listing = {"41914": _stub(41914, "zh_bezirksgericht_dielsdorf", "Arbeitsgericht")}
    (stats, notes, new_ids), out = _run(tmp_path, rows, listing, write=True)
    assert out[0]["decision_id"] == "zh_bezirksgericht_dielsdorf_AH260006"
    assert out[0]["court"] == "zh_bezirksgericht_dielsdorf"
    assert out[0]["chamber"] == "Arbeitsgericht"
    assert out[0]["previous_decision_id"] == "zh_arbeitsgericht_AH260006"
    assert new_ids == ["zh_bezirksgericht_dielsdorf_AH260006"]
    assert stats["court"] == 1


def test_zurich_arbeitsgericht_row_is_left_where_it_is(tmp_path):
    rows = [_row("zh_arbeitsgericht_AH250118-L", "zh_arbeitsgericht", "AH250118-L", 7)]
    (stats, _, new_ids), out = _run(tmp_path, rows, {"7": _stub(7, "zh_arbeitsgericht")}, write=True)
    assert out == rows and not new_ids and stats["rows_changed"] == 0


def test_refile_never_lands_on_an_id_another_row_holds(tmp_path):
    rows = [_row("zh_arbeitsgericht_AN230001", "zh_arbeitsgericht", "AN230001", 1),
            _row("zh_bezirksgericht_pfaeffikon_AN230001", "zh_bezirksgericht_pfaeffikon", "AN230001", 2)]
    listing = {"1": _stub(1, "zh_bezirksgericht_pfaeffikon", "Arbeitsgericht"),
               "2": _stub(2, "zh_bezirksgericht_pfaeffikon", "Arbeitsgericht")}
    (stats, notes, new_ids), out = _run(tmp_path, rows, listing, write=True)
    assert [r["decision_id"] for r in out] == [r["decision_id"] for r in rows]
    assert stats["court_blocked"] == 1 and not new_ids
    assert any(n.startswith("blocked") for n in notes)


def test_headnote_and_verweise_fill_only_empty_fields(tmp_path):
    rows = [_row("zh_obergericht_A", "zh_obergericht", "A", 1, chamber="-"),
            _row("zh_obergericht_B", "zh_obergericht", "B", 2, regeste="eigene Regeste",
                 appeal_info="schon da"),
            _row("zh_obergericht_C", "zh_obergericht", "C", 3,
                 regeste="[PDF text extraction failed for C]")]
    listing = {str(i): _stub(i, "zh_obergericht", leitsatz="Leitsatz", verweise="Weiterzug")
               for i in (1, 2, 3)}
    _, out = _run(tmp_path, rows, listing, write=True)
    assert (out[0]["regeste"], out[0]["appeal_info"], out[0]["chamber"]) == ("Leitsatz", "Weiterzug", None)
    assert (out[1]["regeste"], out[1]["appeal_info"]) == ("eigene Regeste", "schon da")
    assert out[2]["regeste"] == "Leitsatz"


def test_rows_without_a_portal_document_are_untouched(tmp_path):
    rows = [_row("zh_arbeitsgericht_AGer-Z 2023 Nr. 7", "zh_arbeitsgericht", "AGer-Z 2023 Nr. 7", None),
            _row("zh_obergericht_X", "zh_obergericht", "X", 99)]
    (stats, _, _), out = _run(tmp_path, rows, {}, write=True)
    assert out == rows
    assert stats["no_portal_doc_id"] == 1 and stats["not_on_portal"] == 1


def test_document_id_falls_back_to_the_print_view_url():
    row = {"external_id": None, "source_url":
           "https://www.gerichte-zh.ch/entscheide/entscheide-drucken.html"
           "?tx_frpentscheidsammlungextended_pi3[entscheidDrucken]=41914"}
    assert mig.doc_id_of(row) == "41914"


def test_document_already_refetched_under_its_right_id_replaces_the_old_row(tmp_path):
    """Scrape with the new mapping ran before the migration."""
    rows = [_row("zh_arbeitsgericht_AH260006", "zh_arbeitsgericht", "AH260006", 41914),
            _row("zh_bezirksgericht_dielsdorf_AH260006", "zh_bezirksgericht_dielsdorf",
                 "AH260006", 41914, chamber="Arbeitsgericht")]
    listing = {"41914": _stub(41914, "zh_bezirksgericht_dielsdorf", "Arbeitsgericht")}
    (stats, notes, new_ids), out = _run(tmp_path, rows, listing, write=True)
    assert [r["decision_id"] for r in out] == ["zh_bezirksgericht_dielsdorf_AH260006"]
    assert out[0]["previous_decision_id"] == "zh_arbeitsgericht_AH260006"
    assert stats["dropped_refetched_twin"] == 1 and not new_ids
