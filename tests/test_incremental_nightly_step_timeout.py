"""incremental_nightly._run_step must never wedge: a step that exceeds its
wall-clock cap is killed with its whole process group (2026-09-08 incident:
the served-text structure extractor ran 7.4 h at 35 GB RSS with no timeout)."""
from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "incremental_nightly_under_test", REPO / "scripts" / "incremental_nightly.py")
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


def test_run_step_times_out_and_kills_process_group():
    t0 = time.monotonic()
    rec = mod._run_step(
        "sleeper",
        [sys.executable, "-c",
         "import subprocess, sys, time; "
         "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
         "time.sleep(60)"],
        False,
        timeout_s=1.5,
    )
    assert rec["exit_code"] == 124
    assert rec["timed_out"] is True
    assert "timeout" in rec["error"]
    assert time.monotonic() - t0 < 40  # SIGTERM grace, not the 60 s sleeps


def test_run_step_normal_exit_is_unchanged():
    rec = mod._run_step("ok", [sys.executable, "-c", "print('hi')"], False, timeout_s=30)
    assert rec["exit_code"] == 0
    assert "timed_out" not in rec


def test_run_step_default_cap_is_four_hours():
    assert mod.STEP_TIMEOUT_S == 14400


def test_served_text_structure_switch(monkeypatch):
    monkeypatch.setenv("OCL_SERVED_TEXT_STRUCTURE", "0")
    assert mod._served_text_structure_disabled()
    monkeypatch.setenv("OCL_SERVED_TEXT_STRUCTURE", "off")
    assert mod._served_text_structure_disabled()
    monkeypatch.setenv("OCL_SERVED_TEXT_STRUCTURE", "1")
    assert not mod._served_text_structure_disabled()
    monkeypatch.delenv("OCL_SERVED_TEXT_STRUCTURE")
    assert not mod._served_text_structure_disabled()
