"""publish.py's linear path: which steps may fail without exit 1.

The set lives at module level (``publish._NON_FATAL_STEPS``) so it can be
pinned here; main() binds it to its local ``NON_FATAL_STEPS`` name. Keep it in
sync with publish_dag's ``non_fatal`` targets for the steps both paths share.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import publish
import publish_dag


def test_rss_feeds_step_is_non_fatal():
    # 2026-09-03: 5b timed out, everything else OK, unit red, marker not written.
    assert "5b" in publish._NON_FATAL_STEPS


def test_steps_that_must_stay_fatal():
    # Serving-relevant steps: a miss here is a real degradation worth alarming.
    for step in (2, "5c", "6a", 6, "6b", 3, 4, "2f"):
        assert step not in publish._NON_FATAL_STEPS, step


def test_linear_and_dag_non_fatal_markers_agree_for_documented_steps():
    """publish.py's comments promise 2e / 5d / 5e / 5b are marked non_fatal on
    the DAG too. (2c / 2g are non-fatal on the linear path only — the DAG
    still cascades on them; a pre-existing, deliberate divergence.)"""
    dag_non_fatal = {n for n, t in publish_dag.REGISTRY.items() if t.non_fatal}
    for step_key in ("2e", "5d", "5e", "5b", "5a"):
        assert step_key in publish._NON_FATAL_STEPS, step_key
        target = publish.STEP_TO_DAG_TARGET[step_key]
        assert target in dag_non_fatal, (step_key, target)


def test_early_stats_step_is_non_fatal():
    # 2026-10-06: 5a timed out (3600 s) under load, everything else OK; the run
    # went red and its checkpoint made the next 03:30 run skip the rebuild.
    assert "5a" in publish._NON_FATAL_STEPS
