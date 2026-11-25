"""restore_sz_dates.py — SZ rows take the date their own heading states (offline)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.restore_sz_dates import decide, docket_year, run  # noqa: E402

TODAY = "2026-11-25"


def row(did, docket, stored, text, court="sz_gerichte"):
    return {"decision_id": did, "court": court, "docket_number": docket,
            "decision_date": stored, "full_text": text}


# Real heads from the 2026-11-25 classification.
OTHER_DOC_DATE = row("sz_gerichte_BEK 2023 94", "BEK 2023 94", "2024-04-08",
                     "Kantonsgericht Schwyz Beschluss vom 14. Dezember 2023 BEK 2023 94 Mitwirkend")
ES_CONTESTED = row("sz_verwaltungsgericht_II_2021_36", "II 2021 36", "2020-07-30",
                   "Schwyz Verwaltungsgericht 2. Kammer 09.07.2021 II 2021 36  Krankenversicherung "
                   "(Zahlungsverfügung vom 30. Juli 2020) ... II 2021 36Entscheid vom 9. Juli 2021",
                   court="sz_verwaltungsgericht")
AGREES = row("sz_gerichte_ZK2 2025 49", "ZK2 2025 49", "2025-12-23",
             "Beschluss vom 23. Dezember 2025ZK2 2025 49Mitwirkend")


def test_docket_year():
    assert docket_year("BEK 2023 94") == 2023
    assert docket_year("III_2021_196") == 2021
    assert docket_year(None) is None


def test_decide():
    assert decide(OTHER_DOC_DATE, TODAY) == ("corrected", "2023-12-14")
    assert decide(ES_CONTESTED, TODAY) == ("corrected", "2021-07-09")
    assert decide(AGREES, TODAY) == ("agrees", None)
    assert decide(row("x", "STK 2014 12", "2014-06-04", "Urteil vom 3./4. Juni 2014"), TODAY) \
        == ("no_heading", None)
    assert decide(row("x", "BEK 2024 1", "", "Verfügung vom 9. Januar 2025"), TODAY) \
        == ("filled", "2025-01-09")
    assert decide(row("x", "BEK 2024 1", "2024-05-01", "Urteil vom 3. März 2021"), TODAY) \
        == ("before_docket_year", None)
    assert decide(row("x", "BEK 2026 1", "2026-05-01", "Urteil vom 3. März 2027"), TODAY) \
        == ("heading_in_future", None)


def _write(path, rows):
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def test_dry_run_writes_nothing(tmp_path):
    shard = tmp_path / "sz_gerichte.jsonl"
    _write(shard, [OTHER_DOC_DATE, AGREES])
    before = shard.read_bytes()
    stats = run(shard, apply=False, examples=0, today=TODAY)
    assert stats["rows_changed"] == 1 and shard.read_bytes() == before


def test_apply_changes_only_the_wrong_row_and_is_idempotent(tmp_path):
    shard = tmp_path / "es_sz_mixed.jsonl"
    other = {"decision_id": "zg_x", "court": "zg_gerichte", "decision_date": "2020-01-01",
             "full_text": "Urteil vom 3. März 2021"}
    _write(shard, [OTHER_DOC_DATE, other, AGREES, ES_CONTESTED])
    lines_before = shard.read_text(encoding="utf-8").splitlines()
    stats = run(shard, apply=True, examples=0, today=TODAY)
    assert stats["rows_changed"] == 2 and stats["other_court"] == 1
    lines = shard.read_text(encoding="utf-8").splitlines()
    assert lines[1] == lines_before[1] and lines[2] == lines_before[2]   # byte-for-byte
    fixed = json.loads(lines[0])
    assert fixed["decision_date"] == "2023-12-14"
    assert fixed["date_restore"]["from"] == "2024-04-08"
    assert fixed["date_restore"]["method"] == "sz_heading"
    assert json.loads(lines[3])["decision_date"] == "2021-07-09"
    again = run(shard, apply=True, examples=0, today=TODAY)
    assert again["rows_changed"] == 0 and again["agrees"] == 3
