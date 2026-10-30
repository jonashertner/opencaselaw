"""Repo-wide pytest fixtures. A file here also covers the root test_*.py files,
which tests/conftest.py does not reach."""
import pytest


@pytest.fixture(autouse=True)
def _no_early_stats_push(monkeypatch):
    """Never let a test build publish its stats (CLAUDE.md: tests stay offline).

    A swap with OCL_EARLY_STATS_PUSH=1 commits docs/stats.json and pushes main.
    publish.step_2_build_fts5 sets it with setdefault, and a test driving that
    step used to leak it into the rest of the process: on 2026-10-30 a later
    test file's real builds made 22 such commits, and only a stale local main
    kept the pushes from landing (on 2026-10-02 they landed). setenv records
    the original, so teardown also undoes whatever a test sets. Tests of the
    flag itself set it themselves; their value wins."""
    monkeypatch.setenv("OCL_EARLY_STATS_PUSH", "0")
