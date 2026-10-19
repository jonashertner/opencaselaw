"""scripts/backfill_zh_hidden_documents.py — which rendering is kept, and
applying the patch to the shard."""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

import backfill_zh_hidden_documents as bf  # noqa: E402

FULL = " ".join(f"Erwägung {i}: Die Vorinstanz hat den Sachverhalt in Ziffer {i} zutreffend gewürdigt."
                for i in range(120))
EXTRACT = "Leitentscheid. " + " ".join(FULL.split(" ")[:400])
OTHER = " ".join(f"Kostenpunkt {i}: Die Gerichtsgebühr für Abschnitt {i} wird neu festgesetzt."
                 for i in range(120))


def held(text, doc="100"):
    return {"decision_id": "zh_obergericht_LF180040", "external_id": f"zh_gerichte_{doc}", "full_text": text}


def test_full_text_replaces_a_held_extract():
    assert bf.classify(FULL, "200", held(EXTRACT)) == "fuller"


def test_extract_does_not_replace_a_held_full_text():
    assert bf.classify(EXTRACT, "200", held(FULL)) == "shorter"


def test_identical_text_is_identical():
    assert bf.classify(FULL.replace(" ", "\n"), "200", held(FULL)) == "identical"


def test_corrected_version_the_later_upload_wins():
    corrected = FULL.replace("Ziffer 7 ", "Ziffer 7 (berichtigt) ")
    assert bf.classify(corrected, "200", held(FULL, doc="100")) == "newer_version"
    assert bf.classify(corrected, "50", held(FULL, doc="100")) == "older_version"


def test_captions_naming_different_days_are_different_rulings():
    """LC120032: a fee order of 13 November listed under the judgment's 29 October."""
    a = "Obergericht\nBeschluss und Urteil vom 29. Oktober 2012\nin Sachen\n" + FULL
    b = "§ 23 AnwGebV. Honorar.\nBeschluss vom 13. November 2012\n" + OTHER
    assert bf.classify(b, "200", held(a)) == "distinct"


def test_extract_citing_its_judgment_by_date_is_the_same_ruling():
    """NG140002: the extract says "Urteil vom …", the judgment "Beschluss und Urteil vom …"."""
    full = "Obergericht\nBeschluss und Urteil vom 6. Januar 2015\nin Sachen\n" + FULL
    extract = "Art. 316 Abs. 3 ZPO, Beweisabnahme.\nUrteil vom 6. Januar 2015\n" + OTHER[:900]
    assert bf.classify(full, "200", held(extract)) == "fuller"
    assert bf.classify(extract, "200", held(full)) == "shorter"


def test_heavily_edited_extract_without_a_caption_is_the_same_ruling():
    full = "Obergericht\nUrteil vom 5. September 2018\nin Sachen\n" + FULL
    extract = "Art. 566 ZGB. Erbausschlagung. " + OTHER[:900]
    assert bf.classify(full, "200", held(extract)) == "fuller"
    assert bf.classify(extract, "200", held(full)) == "shorter"


def test_dated_id_matches_the_build_convention():
    assert bf.dated_id("zh_obergericht_PS150113", "2015-08-18") == "zh_obergericht_PS150113_d20150818"


def _row(did, doc, text="alt", **kw):
    return {"decision_id": did, "court": "zh_obergericht", "docket_number": "X", "decision_date": "2018-09-05",
            "external_id": f"zh_gerichte_{doc}", "full_text": text, "regeste": None,
            "pdf_url": f"https://x/{doc}.pdf", **kw}


def _files(tmp_path, rows, patch):
    shard = tmp_path / "zh.jsonl"
    shard.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    p = tmp_path / "patch.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in patch), encoding="utf-8")
    return shard, p


def _apply(tmp_path, shard, patch):
    out = tmp_path / "out.jsonl"
    with open(out, "w", encoding="utf-8") as f:
        stats, new_ids, _ = bf.apply_patch(shard, patch, f)
    return stats, new_ids, [json.loads(ln) for ln in out.read_text(encoding="utf-8").splitlines()]


def test_apply_replaces_adds_and_fills(tmp_path):
    rows = [_row("zh_obergericht_A", 1), _row("zh_obergericht_B", 2), _row("zh_obergericht_C", 3)]
    patch = [
        {"op": "replace", "doc_id": "10", "row": _row("zh_obergericht_A", 10, "volltext")},
        {"op": "add", "doc_id": "11", "row": _row("zh_obergericht_B_d20190101", 11, "anderes Urteil")},
        {"op": "regeste", "doc_id": "12", "decision_id": "zh_obergericht_C", "regeste": "Leitsatz"},
        {"op": "skip", "doc_id": "13", "why": "same_pdf"},
        {"op": "held", "doc_id": "14", "why": "distinct_same_day"},
    ]
    shard, p = _files(tmp_path, rows, patch)
    stats, new_ids, out = _apply(tmp_path, shard, p)
    by = {r["decision_id"]: r for r in out}
    assert by["zh_obergericht_A"]["full_text"] == "volltext"
    assert by["zh_obergericht_A"]["external_id"] == "zh_gerichte_10"
    assert by["zh_obergericht_C"]["regeste"] == "Leitsatz"
    assert new_ids == ["zh_obergericht_B_d20190101"] and len(out) == 4
    assert (stats["replaced"], stats["added"], stats["regeste_filled"], stats["held_not_applied"]) == (1, 1, 1, 1)


def test_apply_is_idempotent(tmp_path):
    rows = [_row("zh_obergericht_A", 1)]
    patch = [{"op": "replace", "doc_id": "10", "row": _row("zh_obergericht_A", 10, "volltext")},
             {"op": "add", "doc_id": "11", "row": _row("zh_obergericht_A_d20190101", 11)}]
    shard, p = _files(tmp_path, rows, patch)
    _, _, once = _apply(tmp_path, shard, p)
    shard.write_text("".join(json.dumps(r) + "\n" for r in once), encoding="utf-8")
    stats, new_ids, twice = _apply(tmp_path, shard, p)
    assert twice == once and not new_ids
    assert stats["replaced"] == 0 and stats["add_already_present"] == 1


def test_missing_replace_target_is_reported(tmp_path):
    shard, p = _files(tmp_path, [_row("zh_obergericht_A", 1)],
                      [{"op": "replace", "doc_id": "10", "row": _row("zh_obergericht_GONE", 10)}])
    stats, _, _ = _apply(tmp_path, shard, p)
    assert stats["replace_target_missing"] == 1


def test_sidecar_lists_stored_and_looked_at_documents(tmp_path):
    shard, p = _files(tmp_path, [_row("zh_obergericht_A", 7)],
                      [{"op": "skip", "doc_id": "3", "why": "same_pdf", "date": "2018-09-05"}])
    side = tmp_path / "zh_gerichte.docids.txt"
    assert bf.write_docids(shard, p, side) == 2
    lines = side.read_text(encoding="utf-8").splitlines()
    assert lines[0].split("\t")[:3] == ["3", "-", "2018-09-05"]
    assert lines[1].split("\t") == ["7", "zh_obergericht_A", "2018-09-05", "https://x/7.pdf"]


def test_fuller_rendering_of_a_row_the_patch_itself_adds(tmp_path):
    patch = [{"op": "add", "doc_id": "11", "row": _row("zh_obergericht_A_d20190101", 11, "auszug")},
             {"op": "replace", "doc_id": "12", "row": _row("zh_obergericht_A_d20190101", 12, "volltext")},
             {"op": "regeste", "doc_id": "13", "decision_id": "zh_obergericht_A_d20190101", "regeste": "Leitsatz"}]
    shard, p = _files(tmp_path, [_row("zh_obergericht_A", 1)], patch)
    stats, new_ids, out = _apply(tmp_path, shard, p)
    assert stats["replace_target_missing"] == 0 and new_ids == ["zh_obergericht_A_d20190101"]
    assert (out[1]["full_text"], out[1]["regeste"]) == ("volltext", "Leitsatz")
