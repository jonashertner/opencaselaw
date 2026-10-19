"""The Materialien systemd units (2026-10-19): what they run, and the guards
that keep them out of the build window and away from the live file."""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SD = REPO / "systemd"


def _exec_start(unit: str) -> str:
    text = (SD / unit).read_text()
    m = re.search(r"^ExecStart=(.*?)(?=^\w|\Z)", text, re.S | re.M)
    return m.group(1).replace("\\\n", " ")


def test_vernehmlassungen_unit():
    text = (SD / "opencaselaw-vernehmlassungen.service").read_text()
    cmd = _exec_start("opencaselaw-vernehmlassungen.service")
    assert "search_stack.build_consultation_corpus run" in cmd
    assert "--max-seconds" in cmd and "--rate-limit 0.5" in cmd
    # work DB and served DB on the data volume, never the root disk
    assert "--work-db /mnt/HC_Volume_" in cmd and "--out /mnt/HC_Volume_" in cmd
    assert "ExecCondition=/bin/sh -c '! systemctl is-active --quiet opencaselaw-publish.service'" in text
    assert "check_materialien_completeness.py" in text and "ExecStartPost=-" in text
    timer = (SD / "opencaselaw-vernehmlassungen.timer").read_text()
    assert "Persistent=false" in timer and timer.count("OnCalendar=") == 2


def test_historical_unit_runs_on_a_copy_inside_the_window():
    text = (SD / "opencaselaw-materialien-historical.service").read_text()
    cmd = _exec_start("opencaselaw-materialien-historical.service")
    assert cmd.lstrip().startswith("/usr/bin/python3 -m search_stack.materialien_swap")
    assert "--deadline-seconds 9000" in cmd and "--db {db}" in cmd
    assert "--host-allow bar,fedlex" in cmd and "--discover-untyped-by-title" in cmd
    # shell variables escaped for systemd ($$), else systemd expands them to ""
    assert "$$lang" in cmd and "$$?" in cmd
    assert re.search(r"(?<!\$)\$lang", cmd) is None
    assert "opencaselaw-publish.service" in text and "opencaselaw-materialien.service" in text
    timer = (SD / "opencaselaw-materialien-historical.timer").read_text()
    assert "OnCalendar=*-*-* 00:20:00 UTC" in timer and "Persistent=false" in timer
