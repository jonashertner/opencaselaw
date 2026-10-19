"""Offline tests for scripts/append_stats_history.py (the /stats/ growth series)."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "append_stats_history.py"
sys.path.insert(0, str(REPO / "scripts"))
import append_stats_history as ash  # noqa: E402


def _stats(**over):
    base = {
        "generated_at": "2026-09-17T12:00:00+00:00",
        "total": 1080318,
        "unique_decisions": 939515,
        "duplicate_representations": 140803,
        "unique_decisions_status": "current",
        "court_count": 123,
        "corpus": {"federal_laws": 5535, "cantonal_laws": 15618, "scholarship_publications": 44434,
                   "commentaries": 1173, "citation_edges": 10032404, "statute_edges": 12783588},
    }
    base.update(over)
    return base


def test_point_carries_unique_only_when_current():
    p = ash.point_from_stats(_stats())
    assert p["date"] == "2026-09-17" and p["total"] == 1080318 and p["unique"] == 939515
    p2 = ash.point_from_stats(_stats(unique_decisions_status="stale"))
    assert p2["unique"] is None and p2["duplicates"] is None and p2["total"] == 1080318


def test_point_rejects_broken_stats():
    with pytest.raises(ValueError):
        ash.point_from_stats({"generated_at": "", "total": 5})
    with pytest.raises(ValueError):
        ash.point_from_stats({"generated_at": "2026-09-17T00:00:00Z", "total": 0})


def test_merge_replaces_same_day_and_sorts():
    hist = {"series": [{"date": "2026-09-16", "total": 1080221, "unique": 939418},
                       {"date": "2026-09-17", "total": 1080300, "unique": None}]}
    out = ash.merge(hist, ash.point_from_stats(_stats()))
    dates = [p["date"] for p in out["series"]]
    assert dates == ["2026-09-16", "2026-09-17"]
    assert out["series"][-1]["total"] == 1080318 and out["series"][-1]["unique"] == 939515
    assert out["schema"] == ash.SCHEMA


def test_cli_is_idempotent_and_atomic(tmp_path):
    stats = tmp_path / "stats.json"; hist = tmp_path / "history.json"
    stats.write_text(json.dumps(_stats()))
    for _ in range(2):
        r = subprocess.run([sys.executable, str(SCRIPT), "--stats", str(stats), "--history", str(hist)],
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
    data = json.loads(hist.read_text())
    assert len(data["series"]) == 1 and data["series"][0]["total"] == 1080318
    assert not (tmp_path / "history.json.tmp").exists()


def test_cli_refuses_bad_input_without_writing(tmp_path):
    stats = tmp_path / "stats.json"; hist = tmp_path / "history.json"
    stats.write_text("{not json")
    r = subprocess.run([sys.executable, str(SCRIPT), "--stats", str(stats), "--history", str(hist)],
                       capture_output=True, text=True)
    assert r.returncode == 2 and not hist.exists() and "not written" in r.stderr


def test_committed_seed_matches_schema():
    seed = json.loads((REPO / "docs" / "stats" / "history.json").read_text())
    assert seed["schema"] == ash.SCHEMA and len(seed["series"]) > 100
    assert [p["date"] for p in seed["series"]] == sorted(p["date"] for p in seed["series"])
    assert all(isinstance(p["total"], int) for p in seed["series"])
