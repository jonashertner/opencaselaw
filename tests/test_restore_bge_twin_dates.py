"""restore_bge_dates_from_urteilskopf.py --twin-shard (report 2026-10-13).

The entscheidsuche row of BGE 151 III 227 carries the volume's 1 January
because its Kopfzeile has only the year ("Band III 2025"); the direct row
states "4A_418/2024 vom 20. Dezember 2024" in its Urteilskopf. With the
direct shard as twin, the es row takes that date, only when the twin's own
Urteilskopf confirms it, and never against an es Kopfzeile date.
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

DIRECT_151 = {
    "decision_id": "bge_151 III 227", "court": "bge", "docket_number": "151 III 227",
    "decision_date": "2024-12-20",
    "full_text": ("Urteilskopf\n151 III 227\n22. Auszug aus dem Urteil der I. zivilrechtlichen "
                  "Abteilung i.S. A. GmbH gegen B. AG (Beschwerde in Zivilsachen)\n"
                  "4A_418/2024 vom 20. Dezember 2024\nRegeste\nArt. 93 BGG"),
}
# stored date disagrees with its own Urteilskopf: not a usable twin
DIRECT_UNCONFIRMED = {
    "decision_id": "bge_140 III 1", "court": "bge", "docket_number": "140 III 1",
    "decision_date": "2013-03-03",
    "full_text": ("Urteilskopf\n140 III 1\n1. Auszug aus dem Urteil der II. zivilrechtlichen "
                  "Abteilung i.S. X. gegen Y.\n5A_1/2013 vom 12. November 2013\nRegeste\nArt. 1 ZGB"),
}
ES_151 = {
    "decision_id": "bge_BGE_151_III_227", "court": "bge", "docket_number": "BGE 151 III 227",
    "decision_date": "2025-01-01", "source": "entscheidsuche",
    "full_text": "Bundesgericht (BGE) Band III 2025 BGE 151 III 227\nRegeste\nArt. 93 BGG",
}
ES_IA = {
    "decision_id": "bge_BGE_116_Ia_28", "court": "bge", "docket_number": "BGE 116 Ia 28",
    "decision_date": "1990-01-01", "source": "entscheidsuche",
    "full_text": "Bundesgericht (BGE) Band Ia 1990 BGE 116 Ia 28\nRegeste",
}
ES_KOPF_CONFLICT = {
    "decision_id": "bge_BGE_151_III_227", "court": "bge", "docket_number": "BGE 151 III 227",
    "decision_date": "2025-01-01", "source": "entscheidsuche",
    "full_text": "Bundesgericht (BGE) Band III 18.12.2024 BGE 151 III 227 (4A_418/2024)\nRegeste",
}
ES_140 = {
    "decision_id": "bge_BGE_140_III_1", "court": "bge", "docket_number": "BGE 140 III 1",
    "decision_date": "2014-01-01", "source": "entscheidsuche",
    "full_text": "Bundesgericht (BGE) Band III 2014 BGE 140 III 1\nRegeste",
}


def _shard(tmp_path, name, rows):
    p = tmp_path / name
    p.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in rows), encoding="utf-8")
    return p


def test_ref_folds_id_forms_and_case():
    assert r.bge_ref("bge_BGE_151_III_227") == r.bge_ref("bge_151 III 227") == "151 III 227"
    assert r.bge_ref("bge_BGE_116_Ia_28") == r.bge_ref("bge_116 IA 28") == "116 IA 28"
    assert r.bge_ref("bger_4A_418_2024") is None


def test_only_urteilskopf_confirmed_direct_dates_are_twins(tmp_path):
    twins = r.load_twin_dates(_shard(tmp_path, "bge.jsonl", [DIRECT_151, DIRECT_UNCONFIRMED, ES_151]))
    assert twins == {"151 III 227": "2024-12-20"}


def test_es_placeholder_takes_the_twin_date():
    assert r.decide(ES_151, {"151 III 227": "2024-12-20"})[:2] == ("direct_twin", "2024-12-20")


def test_ia_twin_matches_upper_case_direct_id():
    assert r.decide(ES_IA, {"116 IA 28": "1990-05-02"})[:2] == ("direct_twin", "1990-05-02")


def test_kopfzeile_date_that_contradicts_the_twin_is_left_alone():
    assert r.decide(ES_KOPF_CONFLICT, {"151 III 227": "2024-12-20"})[:2] == ("kopfzeile_twin_conflict", None)


def test_without_a_confirmed_twin_the_old_behaviour_holds():
    # no twins at all, or a twin that its own header does not confirm
    assert r.decide(ES_151)[:2] == r.decide(ES_151, {})[:2]
    assert r.decide(ES_140, {})[0] != "direct_twin"


def test_apply_with_twins_stamps_and_is_idempotent(tmp_path):
    direct = _shard(tmp_path, "bge.jsonl", [DIRECT_151])
    es = _shard(tmp_path, "es_bge.jsonl", [ES_151])
    es.chmod(0o600)
    twins = r.load_twin_dates(direct)
    stats = r.run(es, apply=True, examples=0, twins=twins)
    assert stats["rows_changed"] == 1 and stats["method:direct_twin"] == 1
    row = json.loads(es.read_text(encoding="utf-8"))
    assert row["decision_date"] == "2024-12-20"
    assert row["date_restore"]["from"] == "2025-01-01"
    assert row["date_restore"]["method"] == "direct_twin"
    assert (es.stat().st_mode & 0o777) == 0o600
    assert r.run(es, apply=True, examples=0, twins=twins)["rows_changed"] == 0
