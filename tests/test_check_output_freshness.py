"""Offline tests for the alert plumbing of scripts/check_output_freshness.py
(no Hub, no ntfy)."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

import pytest

import scripts.check_hf_unmanaged_parquet as guard
import scripts.check_output_freshness as cof

NOW = datetime(2026, 10, 8, 8, 23, tzinfo=timezone.utc)
OLD_CARD = ("---\nconfigs:\n  - config_name: default\n    data_files:\n"
            "      - split: train\n        path: data/*.parquet\n---\n")


@pytest.fixture
def posts(monkeypatch):
    sent = []

    def fake_post(body, title, priority="high"):
        sent.append({"body": body, "title": title, "priority": priority})
        return True

    monkeypatch.setattr(cof, "post_ntfy", fake_post)
    return sent


def test_freshness_wording_and_state_unchanged(tmp_path, posts):
    assert cof.maybe_dispatch(["STALE HF mirror: 40h"], NOW, tmp_path, 24) == "posted"
    assert posts[-1] == {
        "title": "opencaselaw OUTPUTS STALE (1)",
        "priority": "high",
        "body": ("1 stale OUTPUT signal(s) — the publish may be silently frozen "
                 "(HF mirror / dashboard / git push not updated):\n\nSTALE HF mirror: 40h"),
    }
    assert (tmp_path / "output_freshness_last_dispatched.json").exists()
    assert cof.maybe_dispatch(["STALE HF mirror: 40h"], NOW, tmp_path, 24) == "unchanged"
    assert cof.maybe_dispatch(["STALE HF mirror: 40h"], NOW + timedelta(hours=25),
                              tmp_path, 24) == "posted"
    assert cof.maybe_dispatch([], NOW, tmp_path, 24) == "all-clear"
    assert posts[-1] == {"body": "All pipeline outputs are fresh again.",
                         "title": "opencaselaw outputs recovered", "priority": "default"}


def test_layout_family_has_its_own_state(tmp_path, posts):
    layout = {"state_name": "hf_layout_last_dispatched.json",
              "title": "opencaselaw HF dataset layout",
              "header": "problem(s) in the HF dataset layout",
              "recovered": ("layout clean", "layout recovered"), "priority": "default"}
    assert cof.maybe_dispatch(["STALE HF mirror: 40h"], NOW, tmp_path, 24) == "posted"
    assert cof.maybe_dispatch(["HF abc: 99 parquet file(s) outside"], NOW, tmp_path, 24,
                              **layout) == "posted"
    assert posts[-1]["title"] == "opencaselaw HF dataset layout (1)"
    assert posts[-1]["priority"] == "default"
    assert posts[-1]["body"].startswith("1 problem(s) in the HF dataset layout:\n\n")
    # clearing one family leaves the other armed
    assert cof.maybe_dispatch([], NOW, tmp_path, 24, **layout) == "all-clear"
    assert posts[-1]["body"] == "layout clean"
    assert (tmp_path / "output_freshness_last_dispatched.json").exists()
    assert not (tmp_path / "hf_layout_last_dispatched.json").exists()


def test_check_hf_layout_findings(monkeypatch):
    monkeypatch.setattr(guard, "list_remote_files", lambda repo, rev: (
        "f57a5b42" + "0" * 32, ["bge.parquet", "data/bge.parquet", "data/delta-2026-10-08.parquet"]))
    monkeypatch.setattr(guard, "fetch_remote_card", lambda repo, rev: OLD_CARD)
    out = cof.check_hf_layout()
    assert len(out) == 2 and all(x.startswith("HF f57a5b42: ") for x in out)
    assert "also reads data/delta-2026-10-08.parquet" in out[1]

    monkeypatch.setattr(guard, "fetch_remote_card",
                        lambda repo, rev: guard.CARD_PATH.read_text(encoding="utf-8"))
    monkeypatch.setattr(guard, "list_remote_files",
                        lambda repo, rev: ("0" * 40, ["data/bge.parquet", "data/delta-2026-10-08.parquet"]))
    assert cof.check_hf_layout() == []


def test_check_hf_layout_failure_is_logged_not_paged(monkeypatch, capsys):
    def boom(repo, rev):
        raise OSError("hub down")

    monkeypatch.setattr(guard, "list_remote_files", boom)
    assert cof.check_hf_layout() is None
    assert "hf layout check skipped: hub down" in capsys.readouterr().err


def test_failed_layout_check_sends_no_false_all_clear(monkeypatch, tmp_path, posts):
    monkeypatch.setattr(cof, "check_hf", lambda now, budget: None)
    monkeypatch.setattr(cof, "check_git_path", lambda *a: None)
    monkeypatch.setattr(sys, "argv", ["check_output_freshness.py", "--state-dir", str(tmp_path)])
    monkeypatch.setattr(cof, "check_hf_layout", lambda: ["HF abc: 99 parquet file(s) outside"])
    cof.main()
    monkeypatch.setattr(cof, "check_hf_layout", lambda: None)  # Hub unreachable
    cof.main()
    assert [p["title"] for p in posts] == ["opencaselaw HF dataset layout (1)"]
    assert (tmp_path / "hf_layout_last_dispatched.json").exists()


def test_main_pages_layout_separately(monkeypatch, tmp_path, posts):
    monkeypatch.setattr(cof, "check_hf", lambda now, budget: None)
    monkeypatch.setattr(cof, "check_git_path", lambda *a: None)
    monkeypatch.setattr(cof, "check_hf_layout", lambda: ["HF abc: 99 parquet file(s) outside"])
    monkeypatch.setattr(sys, "argv", ["check_output_freshness.py", "--state-dir", str(tmp_path)])
    assert cof.main() == 0
    assert [p["title"] for p in posts] == ["opencaselaw HF dataset layout (1)"]
    assert (tmp_path / "hf_layout_last_dispatched.json").exists()
    assert not (tmp_path / "output_freshness_last_dispatched.json").exists()
