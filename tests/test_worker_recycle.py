"""Memory-triggered worker recycling (2026-10-05).

On 2026-09-30 and 2026-10-04 the eight mcp-server@ workers grew to 5-13 GB
each, squeezed the page cache and searches took 20-45 s until a rolling
restart. scripts/recycle_bloated_workers.py restarts the largest workers above
a limit, one at a time, never while the fleet is short. Offline: systemctl,
/proc and /health are stubbed.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "recycle_bloated_workers", REPO / "scripts" / "recycle_bloated_workers.py")
rw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rw)

UNITS = [f"mcp-server@{p}.service" for p in range(8770, 8778)]


def test_plan_picks_the_largest_above_the_limit():
    sizes = {u: 2000 for u in UNITS}
    sizes[UNITS[3]] = 5200
    sizes[UNITS[5]] = 4100
    sizes[UNITS[6]] = 9800
    assert rw.plan(sizes, True, 3584, 2) == [UNITS[6], UNITS[3]]


def test_plan_does_nothing_below_the_limit_or_while_a_worker_is_down():
    sizes = {u: 3000 for u in UNITS}
    assert rw.plan(sizes, True, 3584, 2) == []
    sizes[UNITS[0]] = 9000
    assert rw.plan(sizes, False, 3584, 2) == []


@pytest.fixture
def fleet(monkeypatch):
    state = {"sizes": {u: 2000 for u in UNITS}, "pids": {u: 1000 + i for i, u in enumerate(UNITS)},
             "down": set(), "restarted": [], "fail_after_restart": set()}
    pid_to_unit = {v: k for k, v in state["pids"].items()}
    monkeypatch.setattr(rw, "active_workers", lambda: list(UNITS))
    monkeypatch.setattr(rw, "main_pid", lambda u: state["pids"][u])
    monkeypatch.setattr(rw, "rss_mb", lambda pid: state["sizes"][pid_to_unit[pid]])
    monkeypatch.setattr(rw, "healthy", lambda port, timeout=5.0: f"mcp-server@{port}.service" not in state["down"])

    def _run(cmd, check=False, **kw):
        assert cmd[:2] == ["systemctl", "restart"], cmd
        unit = cmd[2]
        state["restarted"].append(unit)
        state["sizes"][unit] = 300
        if unit in state["fail_after_restart"]:
            state["down"].add(unit)
    monkeypatch.setattr(rw.subprocess, "run", _run)
    monkeypatch.setattr(rw.time, "sleep", lambda s: None)
    monkeypatch.setattr(rw, "DRY_RUN", False)
    monkeypatch.setattr(rw, "HEALTH_WAIT", 3)
    monkeypatch.setattr(rw, "limit_for", lambda hour: 3584)   # night unless a test says otherwise
    return state


def test_night_threshold_low_day_threshold_high():
    assert rw.limit_for(2, "0-6", 3584, 5120) == 3584
    assert rw.limit_for(0, "0-6", 3584, 5120) == 3584
    assert rw.limit_for(6, "0-6", 3584, 5120) == 5120
    assert rw.limit_for(14, "0-6", 3584, 5120) == 5120
    # a window that wraps past midnight
    assert rw.limit_for(23, "22-6", 3584, 5120) == 3584
    assert rw.limit_for(3, "22-6", 3584, 5120) == 3584
    assert rw.limit_for(12, "22-6", 3584, 5120) == 5120


def test_by_day_a_worker_between_the_thresholds_is_left_alone(fleet, monkeypatch):
    monkeypatch.setattr(rw, "limit_for", lambda hour: 5120)
    fleet["sizes"][UNITS[2]] = 4200
    fleet["sizes"][UNITS[4]] = 6000
    assert rw.main() == 0
    assert fleet["restarted"] == [UNITS[4]]


def test_restarts_the_bloated_workers_one_at_a_time(fleet):
    fleet["sizes"][UNITS[2]] = 6000
    fleet["sizes"][UNITS[7]] = 4500
    fleet["sizes"][UNITS[4]] = 5000
    assert rw.main() == 0
    assert fleet["restarted"] == [UNITS[2], UNITS[4]]      # at most two, largest first


def test_a_healthy_fleet_below_the_limit_is_left_alone(fleet):
    assert rw.main() == 0
    assert fleet["restarted"] == []


def test_nothing_is_restarted_while_a_worker_is_down(fleet):
    fleet["sizes"][UNITS[2]] = 9000
    fleet["down"].add(UNITS[5])
    assert rw.main() == 0
    assert fleet["restarted"] == []


def test_a_worker_that_does_not_come_back_stops_the_run_and_alerts(fleet):
    fleet["sizes"][UNITS[2]] = 9000
    fleet["sizes"][UNITS[3]] = 8000
    fleet["fail_after_restart"].add(UNITS[2])
    assert rw.main() == 1                                     # OnFailure -> ntfy
    assert fleet["restarted"] == [UNITS[2]]                   # the second is not touched


def test_dry_run_changes_nothing(fleet, monkeypatch):
    monkeypatch.setattr(rw, "DRY_RUN", True)
    fleet["sizes"][UNITS[2]] = 9000
    assert rw.main() == 0
    assert fleet["restarted"] == []


def test_units_wire_it_up():
    svc = (REPO / "systemd" / "opencaselaw-worker-recycle.service").read_text()
    assert "scripts/recycle_bloated_workers.py" in svc
    assert "OnFailure=ntfy-alert@%n.service" in svc
    timer = (REPO / "systemd" / "opencaselaw-worker-recycle.timer").read_text()
    assert "OnUnitActiveSec=15min" in timer
    cap = (REPO / "systemd" / "mcp-server@.service.d" / "memory-cap.conf").read_text()
    lines = [l.strip() for l in cap.splitlines() if l.strip() and not l.lstrip().startswith("#")]
    assert "MemoryMax=8G" in lines
    # throttling anonymous memory stalls the worker instead of freeing it
    assert not any(l.startswith("MemoryHigh") for l in lines)
    # the cap must sit well above the recycle threshold
    assert rw.RSS_MAX_MB < rw.RSS_DAY_MAX_MB < 8 * 1024
