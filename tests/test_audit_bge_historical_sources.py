"""Offline tests for scripts/audit_bge_historical_sources.py (read-only audit of
historical BGE rows whose DFR source holds another reference's ruling;
runbooks/historical_bge_source_errors_2026-10-07.md)."""
import json
import random
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import audit_bge_historical_sources as audit


def _prose(seed: int, words: int = 600) -> str:
    """Deterministic text of distinct pseudo-words, a line per ten words."""
    rng = random.Random(seed)
    toks = ["".join(rng.choice("abcdefghiklmnoprstuvz") for _ in range(rng.randint(4, 9)))
            for _ in range(words)]
    return "\n".join(" ".join(toks[i:i + 10]) for i in range(0, len(toks), 10))


def _row(ref: str, text: str) -> dict:
    return {"decision_id": f"bge_{ref}", "docket_number": ref, "full_text": text}


def _checks(findings):
    return {(f[0], f[1], f[2], f[3] if f[0] in ("same_text", "html_label") else "") for f in findings}


def test_parse_ref_takes_volumes_1_to_79_only():
    assert audit.parse_ref(_row("52_I_8", "")) == audit.Ref(52, "I", 8)
    assert audit.parse_ref({"decision_id": "bge_historical_78_IV_83"}) == audit.Ref(78, "IV", 83)
    assert audit.parse_ref(_row("80_I_1", "")) is None
    assert audit.parse_ref({"decision_id": "bger_4A_1_2020"}) is None


def test_same_text_in_another_volume_is_reported():
    # BGE 52 I 8: the DFR PDF is the scan of BGE 65 I 8 (same page number).
    page = "8\nStaatsrecht.\n" + _prose(1)
    rows = [_row("52_I_8", page), _row("65_I_8", page), _row("52_I_14", "14\n" + _prose(2))]
    assert _checks(audit.audit(rows)) == {("same_text", "bge_52_I_8", "bge_65_I_8", "other_volume")}


def test_same_text_kinds():
    text = _prose(3)
    rows = [_row("20_I_384", text), _row("20_I_385", text),          # facing pages
            _row("39_I_469", _prose(4)), _row("39_I_483", _prose(4))]  # 14 pages apart
    assert _checks(audit.audit(rows)) == {
        ("same_text", "bge_20_I_384", "bge_20_I_385", "same_spread"),
        ("same_text", "bge_39_I_469", "bge_39_I_483", "other_pages"),
    }


def test_short_texts_are_not_compared():
    rows = [_row("1_I_1", "kurz"), _row("2_I_1", "kurz")]
    assert audit.audit(rows) == []


def test_pages_of_another_volume_appended():
    # BGE 71 II 223: two spreads of its own, then BGE 77 II 154-161.
    own = "223\n" + _prose(10, 300) + "\n224\n" + _prose(11, 300)
    foreign_pages = "".join(f"\n{p}\n" + _prose(100 + p, 300) for p in (154, 155, 156, 157))
    other = "".join(f"\n{p}\n" + _prose(100 + p, 300) for p in (154, 155, 156, 157))
    rows = [_row("71_II_223", own + foreign_pages), _row("77_II_154", other.strip())]
    got = _checks(audit.audit(rows))
    assert ("page_jump", "bge_71_II_223", "", "") in got
    assert ("shared_text", "bge_71_II_223", "bge_77_II_154", "") in got


def test_neighbour_overlap_in_one_volume_is_not_shared_text():
    # Every row of a volume shares a spread with its neighbours: not a finding.
    shared = _prose(20, 400)
    rows = [_row("78_IV_83", "82\n" + _prose(21) + "\n84\n" + shared),
            _row("78_IV_84", "84\n" + shared + "\n86\n" + _prose(22))]
    assert audit.audit(rows) == []


def test_a_short_quotation_across_volumes_is_not_shared_text():
    quote = _prose(30, 40)
    rows = [_row("40_II_1", _prose(31) + "\n" + quote), _row("60_II_1", _prose(32) + "\n" + quote)]
    assert audit.audit(rows) == []


def test_page_jump_ignores_ocr_noise_and_lists():
    ref = audit.Ref(52, "II", 451)
    # OCR misread of a run that comes back to the own pages
    assert audit.page_jump("450\na\n451\nb\n152\nc\n153\nd\n154\ne\n454\nf", ref) is None
    # an enumeration below the floor
    assert audit.page_jump("451\na\n452\nb\n6\nc\n7\nd\n8\ne", audit.Ref(20, "I", 451)) is None
    # two lines are not a run
    assert audit.page_jump("143\na\n144\nb\n117\nc\n119\nd", audit.Ref(58, "III", 143)) is None
    assert audit.page_jump("224\na\n154\nb\n155\nc\n156\nd", audit.Ref(71, "II", 223)) == (6, 154)


def test_html_label_names_another_reference():
    html = "DFR - BGE 22 I 1012 - Kantonsverfassung Nidwalden\n" + _prose(40)
    ok = "DFR - BGE 1 I 3 - Fliniaux\n" + _prose(41)
    rows = [_row("22_I_12", html), _row("1_I_3", ok)]
    assert _checks(audit.audit(rows)) == {("html_label", "bge_22_I_12", "", "page titled BGE 22 I 1012")}


def test_cli_reads_jsonl_and_writes_tsv(tmp_path):
    page = _prose(50)
    shard = tmp_path / "bge_historical.jsonl"
    rows = [_row("52_I_23", page), _row("65_I_23", page), {"decision_id": "x", "full_text": page}]
    shard.write_text("\n".join(json.dumps(r) for r in rows) + "\n\n", encoding="utf-8")
    before = shard.read_bytes()
    out = tmp_path / "out.tsv"
    proc = subprocess.run([sys.executable, str(REPO / "scripts" / "audit_bge_historical_sources.py"),
                           str(shard), "--out", str(out)], capture_output=True, text=True, check=True)
    assert out.read_text(encoding="utf-8").splitlines() == [
        "check\tdecision_id\tother_id\tdetail",
        "same_text\tbge_52_I_23\tbge_65_I_23\tother_volume",
    ]
    assert "same_text:other_volume" in proc.stderr
    assert shard.read_bytes() == before        # read-only
