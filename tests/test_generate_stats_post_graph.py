"""--post-graph (publish Step 5e) refreshes only the graph-derived blocks.

2026-10-05 and 2026-10-18: Step 5e re-ran the whole decisions block after the
reference-graph rebuild and hit its 3600 s cap; by_court's MAX(scraped_at) sits
after full_text and walks most of the 72 GB file. Step 5a has already computed
that block against the same run's swapped DB, so 5e reuses it unless it
predates the run (5a failed), in which case 5e regenerates in full as before.
"""
from __future__ import annotations

import json
import sys

import pytest

import generate_stats

RUN_START = "2026-10-19T03:30:00+00:00"


@pytest.fixture
def stubbed(monkeypatch, tmp_path):
    calls = {"full": 0}

    def full_regen(db_path):
        calls["full"] += 1
        return {"total": 7, "by_court": [], "by_canton": [], "court_count": 0,
                "generated_at": "2026-10-19T20:00:00+00:00"}

    monkeypatch.setattr(generate_stats, "generate_stats", full_regen)
    monkeypatch.setattr(generate_stats, "collect_corpus_snapshot",
                        lambda repo: {"citation_edges": 12, "statute_edges": 4})
    monkeypatch.setattr(generate_stats, "collect_interesting_stats",
                        lambda repo: {"graph_size": {"total": 16}})
    monkeypatch.setattr(generate_stats, "_load_previous_stats",
                        lambda out, repo, today: {"corpus": {"citation_edges": 10, "statute_edges": 4}})
    for name in ("collect_scraper_health", "collect_traffic"):
        monkeypatch.setattr(generate_stats, name, lambda *a, **k: None)
    monkeypatch.setattr(generate_stats, "collect_recent_decisions", lambda *a, **k: {})
    monkeypatch.setattr(generate_stats, "collect_upcoming_amendments", lambda *a, **k: [])
    db = tmp_path / "decisions.db"
    db.write_bytes(b"")
    return calls, db, tmp_path / "stats.json"


def _run(monkeypatch, db, out, *extra):
    monkeypatch.setattr(sys, "argv", ["generate_stats.py", "--db", str(db),
                                      "--output", str(out), *extra])
    generate_stats.main()
    return json.loads(out.read_text())


def _base(generated_at):
    return {
        "total": 1064900,
        "by_court": [{"court": "bger", "canton": "CH", "count": 193868}],
        "generated_at": generated_at,
        "corpus": {"citation_edges": 10, "statute_edges": 4},
        "interesting_stats": {"graph_size": {"total": 14}},
        "delta": {"total": 6, "by_court": {"bger": 6}, "by_canton": {},
                  "corpus": {}, "previous_generated_at": "2026-10-18T12:17:54+00:00"},
    }


def test_reuses_current_base_and_refreshes_graph_blocks(monkeypatch, stubbed):
    calls, db, out = stubbed
    out.write_text(json.dumps(_base("2026-10-19T12:17:54.212+00:00")))
    stats = _run(monkeypatch, db, out, "--post-graph", "--base-since", RUN_START)
    assert calls["full"] == 0
    assert stats["total"] == 1064900                      # decisions block kept
    assert stats["generated_at"].startswith("2026-10-19T12:17")
    assert stats["corpus"] == {"citation_edges": 12, "statute_edges": 4}
    assert stats["interesting_stats"] == {"graph_size": {"total": 16}}
    assert stats["delta"]["corpus"] == {"citation_edges": 2}
    assert stats["delta"]["by_court"] == {"bger": 6}      # decisions delta kept


@pytest.mark.parametrize("generated_at, base_since", [
    ("2026-10-18T12:17:54+00:00", RUN_START),   # Step 5a failed: yesterday's file
    ("2026-10-19T12:17:54+00:00", None),        # no run start passed (manual --step 5e)
    ("", RUN_START),                            # no timestamp
])
def test_falls_back_to_full_regenerate(monkeypatch, stubbed, generated_at, base_since):
    calls, db, out = stubbed
    out.write_text(json.dumps(_base(generated_at)))
    extra = ["--post-graph"] + (["--base-since", base_since] if base_since else [])
    stats = _run(monkeypatch, db, out, *extra)
    assert calls["full"] == 1
    assert stats["total"] == 7


def test_missing_stats_json_falls_back(monkeypatch, stubbed):
    calls, db, out = stubbed
    _run(monkeypatch, db, out, "--post-graph", "--base-since", RUN_START)
    assert calls["full"] == 1


def test_base_is_current_handles_naive_and_z_timestamps():
    base = {"by_court": [{}]}
    assert generate_stats._base_is_current(
        {**base, "generated_at": "2026-10-19T12:00:00"}, "2026-10-19T03:30:00Z")
    assert not generate_stats._base_is_current(
        {**base, "generated_at": "2026-10-19T03:29:59+00:00"}, "2026-10-19T03:30:00Z")
    assert not generate_stats._base_is_current(
        {"generated_at": "2026-10-19T12:00:00+00:00"}, "2026-10-19T03:30:00Z")
