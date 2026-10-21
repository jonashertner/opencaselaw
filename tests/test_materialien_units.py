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


def _guard_line(text: str) -> str:
    return next(l for l in text.splitlines() if l.startswith("ExecCondition="))


def _assert_guard(text: str, units: list[str]) -> None:
    """The guard skips the run in EVERY non-idle state of the units: a
    oneshot build is "activating" while it runs, never "active"."""
    g = _guard_line(text)
    for u in units:
        assert u in g
    assert "activating" in g and "deactivating" in g and "exit 1" in g
    assert "--quiet" not in g          # the old `! is-active --quiet` test
    assert "$$(systemctl is-active $$u)" in g  # escaped for systemd


def _run_guard(text: str, states: dict[str, str]) -> int:
    """Execute the guard with a fake systemctl reporting ``states``."""
    import os, subprocess, tempfile
    g = _guard_line(text)[len("ExecCondition="):]
    script = g.split("-c ", 1)[1].strip()[1:-1].replace("$$", "$")
    with tempfile.TemporaryDirectory() as d:
        fake = Path(d) / "systemctl"
        cases = "".join(f'  {u}) echo {s};;\n' for u, s in states.items())
        fake.write_text("#!/bin/sh\ncase \"$2\" in\n" + cases + "  *) echo inactive;;\nesac\n")
        fake.chmod(0o755)
        env = dict(os.environ, PATH=f"{d}:{os.environ['PATH']}")
        return subprocess.run(["/bin/sh", "-c", script], env=env).returncode


def test_guard_skips_while_the_build_is_activating():
    text = (SD / "opencaselaw-vernehmlassungen.service").read_text()
    assert _run_guard(text, {"opencaselaw-publish.service": "activating"}) != 0
    assert _run_guard(text, {"opencaselaw-publish.service": "active"}) != 0
    assert _run_guard(text, {"opencaselaw-publish.service": "inactive"}) == 0
    assert _run_guard(text, {"opencaselaw-publish.service": "failed"}) == 0
    hist = (SD / "opencaselaw-materialien-historical.service").read_text()
    assert _run_guard(hist, {"opencaselaw-materialien.service": "activating"}) != 0
    assert _run_guard(hist, {}) == 0


def test_vernehmlassungen_unit():
    text = (SD / "opencaselaw-vernehmlassungen.service").read_text()
    cmd = _exec_start("opencaselaw-vernehmlassungen.service")
    assert "search_stack.build_consultation_corpus run" in cmd
    assert "--max-seconds" in cmd and "--rate-limit 0.5" in cmd
    # work DB and served DB on the data volume, never the root disk
    assert "--work-db /mnt/HC_Volume_" in cmd and "--out /mnt/HC_Volume_" in cmd
    _assert_guard(text, ["opencaselaw-publish.service"])
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
    _assert_guard(text, ["opencaselaw-publish.service", "opencaselaw-materialien.service"])
    timer = (SD / "opencaselaw-materialien-historical.timer").read_text()
    assert "OnCalendar=*-*-* 00:20:00 UTC" in timer and "Persistent=false" in timer


def test_daily_botschaft_unit_writes_a_copy():
    text = (SD / "opencaselaw-materialien.service").read_text()
    cmd = _exec_start("opencaselaw-materialien.service")
    assert cmd.lstrip().startswith("/usr/bin/python3 -m search_stack.materialien_swap")
    assert "--ok-codes 0,4" in cmd
    # every writer in the chain gets the copy, none the live path
    assert cmd.count("--db {db}") == 4
    assert cmd.count("/opt/caselaw/repo/output/materialien.db") == 1  # the swap's own --db
    assert "Environment=PYTHONPATH=/opt/caselaw/repo" in text



def test_vernehmlassungen_unit_runs_an_ocr_slice_after_the_run():
    text = (SD / "opencaselaw-vernehmlassungen.service").read_text()
    post = [l for l in text.replace("\\\n", " ").splitlines() if l.startswith("ExecStartPost=")]
    ocr = [l for l in post if "build_consultation_corpus ocr" in l]
    assert len(ocr) == 1
    line = ocr[0]
    assert line.startswith("ExecStartPost=-")          # a failed slice never fails the unit
    assert "--max-seconds 3600" in line and "--scope no_text,partial" in line
    assert "--no-unit-check" in line and "nice -n 19" in line
    assert text.index("build_consultation_corpus ocr") < text.index("check_materialien_completeness.py")
