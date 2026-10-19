"""The early stats push publishes to the live site: only the nightly build may ask for it."""
import os

import build_fts5
import publish


def test_a_plain_build_does_not_push_stats(monkeypatch):
    monkeypatch.delenv("OCL_EARLY_STATS_PUSH", raising=False)
    assert build_fts5._early_stats_push_enabled() is False


def test_only_an_explicit_yes_enables_the_push(monkeypatch):
    for value, expected in (("1", True), ("true", True), ("0", False), ("", False), ("no", False)):
        monkeypatch.setenv("OCL_EARLY_STATS_PUSH", value)
        assert build_fts5._early_stats_push_enabled() is expected


def test_the_nightly_build_step_asks_for_the_push(monkeypatch):
    monkeypatch.delenv("OCL_EARLY_STATS_PUSH", raising=False)
    monkeypatch.setattr(publish, "run_cmd", lambda *a, **k: True)
    assert publish.step_2_build_fts5(dry_run=True) is True
    assert os.environ["OCL_EARLY_STATS_PUSH"] == "1"
    assert build_fts5._early_stats_push_enabled() is True


def test_an_operator_opt_out_survives_the_nightly_step(monkeypatch):
    monkeypatch.setenv("OCL_EARLY_STATS_PUSH", "0")
    monkeypatch.setattr(publish, "run_cmd", lambda *a, **k: True)
    publish.step_2_build_fts5(dry_run=True)
    assert build_fts5._early_stats_push_enabled() is False
