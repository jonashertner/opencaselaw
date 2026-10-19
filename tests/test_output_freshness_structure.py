"""check_output_freshness.py pages when the structure/ exports stop being
written. 2026-10-07: structure/structure.parquet had sat at its 2026-09-10
file for four weeks — every nightly export was skipped over budget and the
last good file re-uploaded — while the HF mirror's lastModified, the only
dataset signal, stayed fresh. Offline: local files and mtimes only."""
from __future__ import annotations

import importlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

fresh = importlib.import_module("scripts.check_output_freshness")

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
SKIP = "projected 27.0 min > budget 10 min (last good file kept)"


def _dataset(tmp_path: Path, structure_age_h: float, paragraphs_age_h: float,
             counts: dict | None = None) -> Path:
    sdir = tmp_path / "dataset" / "structure"
    sdir.mkdir(parents=True)
    for name, age in (("structure.parquet", structure_age_h),
                      ("erwaegungen_paragraphs.parquet", paragraphs_age_h)):
        p = sdir / name
        p.write_bytes(b"PAR1")
        t = (NOW - timedelta(hours=age)).timestamp()
        os.utime(p, (t, t))
    if counts is not None:
        (sdir / "export_status.json").write_text(json.dumps(
            {"at": "2026-10-07T01:40:00+00:00", "counts": counts}))
    return tmp_path / "dataset"


def test_frozen_metadata_export_pages_with_the_skip_reason(tmp_path):
    ds = _dataset(tmp_path, structure_age_h=27 * 24, paragraphs_age_h=3 * 24,
                  counts={"structure_skipped": SKIP})
    alerts = fresh.check_structure_exports(NOW, ds, 36.0, 204.0)
    assert len(alerts) == 1
    assert alerts[0].startswith("STALE structure/structure.parquet (nightly): 648h")
    assert SKIP in alerts[0] and "2026-10-07T01:40:00+00:00" in alerts[0]


def test_fresh_exports_are_quiet(tmp_path):
    ds = _dataset(tmp_path, structure_age_h=20, paragraphs_age_h=6 * 24,
                  counts={"structure": 1_070_000})
    assert fresh.check_structure_exports(NOW, ds, 36.0, 204.0) == []


def test_weekly_paragraphs_have_their_own_budget(tmp_path):
    ds = _dataset(tmp_path, structure_age_h=20, paragraphs_age_h=10 * 24,
                  counts={"structure": 5, "erwaegungen_paragraphs_skipped": "disabled (budget 0)"})
    alerts = fresh.check_structure_exports(NOW, ds, 36.0, 204.0)
    assert alerts == [
        "STALE structure/erwaegungen_paragraphs.parquet (weekly): 240h since last written; "
        "the last export run (2026-10-07T01:40:00+00:00) skipped it: disabled (budget 0)"]


def test_stale_without_a_status_file_still_pages(tmp_path):
    ds = _dataset(tmp_path, structure_age_h=72, paragraphs_age_h=24)
    assert fresh.check_structure_exports(NOW, ds, 36.0, 204.0) == [
        "STALE structure/structure.parquet (nightly): 72h since last written"]


def test_missing_files_are_logged_not_paged(tmp_path, capsys):
    assert fresh.check_structure_exports(NOW, tmp_path / "nowhere", 36.0, 204.0) == []
    err = capsys.readouterr().err
    assert "structure.parquet not found" in err and "export_status.json" in err
