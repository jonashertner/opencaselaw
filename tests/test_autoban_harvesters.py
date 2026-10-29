"""Time-limited bans for export-harvesting networks (2026-10-23).

A 50-address fleet in one cloud range pulled ~12,900 export renders per
15 minutes for six hours; every nginx limit was per address, so none fired.
scripts/autoban_harvesters.py counts export requests per network in the last
window of tier1.log and bans offenders from the export renderers for a while.
Offline: addresses come from non-global documentation and benchmark ranges
(the bannable() guard is relaxed for them), nginx and systemctl are stubbed.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import ipaddress
import json
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "autoban_harvesters", REPO / "scripts" / "autoban_harvesters.py")
ab = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ab)

NOW = dt.datetime(2026, 10, 23, 12, 0, tzinfo=dt.timezone.utc)
REAL_BANNABLE = ab.bannable


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setattr(ab, "bannable", lambda addr: True)
    monkeypatch.setattr(ab, "NET_MAX", 600)
    monkeypatch.setattr(ab, "NET_MIN_IPS", 3)
    monkeypatch.setattr(ab, "WIDE_MAX", 2000)
    monkeypatch.setattr(ab, "WIDE_MIN_IPS", 10)
    monkeypatch.setattr(ab, "WIDE_MEMBER_MIN", 50)
    monkeypatch.setattr(ab, "TTL_H", 24.0)
    monkeypatch.setattr(ab, "MAX_NEW", 64)
    monkeypatch.setattr(ab, "DRY_RUN", False)


def line(ip, ts, path="/api/decisions/bger_1A_1_2020/export.docx", status=200):
    return f'{ip} {ts.isoformat(timespec="seconds")} "GET {path}" {status} 0.050 "Synthetic/1.0"\n'


def burst(ips, n_each, start=NOW - dt.timedelta(minutes=10), path=None):
    out = []
    for i in range(n_each):
        for ip in ips:
            ts = start + dt.timedelta(seconds=i % 500)
            out.append(line(ip, ts) if path is None else line(ip, ts, path))
    return out


def counts(lines):
    return ab.count_harvest(lines)


SPREAD = ["198.51.100.7", "198.51.100.8", "198.51.100.9"]


def test_one_network_over_the_line_is_banned():
    lines = burst(SPREAD, 200)
    found = ab.offenders(*counts(lines))
    assert list(map(str, found)) == ["198.51.100.0/24"]
    assert "600 export requests" in found[ipaddress.ip_network("198.51.100.0/24")]


def test_a_single_address_is_left_to_the_per_address_budget():
    # One address is already held to 10 exports a minute by nginx; its excess
    # is a cheap 429. The replay of 10-20..23 found five such addresses (one a
    # Swiss line) that a per-network rule alone would have banned.
    assert ab.offenders(*counts(burst(["198.51.100.7"], 1200))) == {}
    assert ab.offenders(*counts(burst(["198.51.100.7", "198.51.100.8"], 600))) == {}


def test_ordinary_traffic_and_other_paths_are_never_counted():
    lines = (burst(SPREAD, 199) + burst(["198.51.100.10"], 2)
             + burst(["203.0.113.9"], 5000, path="/entscheid/bger_1A_1_2020")
             + burst(["203.0.113.10"], 5000, path="/api/decisions/bger_1A_1_2020"))
    hits, ips = counts(lines)
    assert hits[ipaddress.ip_network("198.51.100.0/24")] == 599
    assert ipaddress.ip_network("203.0.113.0/24") not in hits
    assert ab.offenders(hits, ips) == {}


def test_bulk_notice_fetches_count_as_harvest():
    lines = burst(SPREAD, 100) + burst(SPREAD, 100, path="/bulk-access.txt")
    assert list(map(str, ab.offenders(*counts(lines)))) == ["198.51.100.0/24"]


def test_a_fleet_spread_below_the_per_network_line_is_caught_by_its_16():
    # 8 /24s in one /16, 6 addresses each, 400 requests per /24: none crosses
    # 600 alone, together 3,200 from 48 addresses.
    ips = [f"198.18.{n}.{h}" for n in range(1, 9) for h in range(10, 16)]
    lines = []
    for ip in ips:
        lines += [line(ip, NOW - dt.timedelta(minutes=5))] * 67
    lines += [line("198.18.9.1", NOW - dt.timedelta(minutes=5))] * 10  # small member
    found = ab.offenders(*counts(lines))
    assert sorted(map(str, found)) == [f"198.18.{n}.0/24" for n in range(1, 9)]
    assert all("fleet in 198.18.0.0/16" in r for r in found.values())


def test_a_busy_16_with_few_addresses_is_not_a_fleet():
    lines = []
    for n in range(1, 6):  # 5 addresses, 500 each: 2,500 from 5 < 10 addresses
        lines += [line(f"198.18.{n}.1", NOW - dt.timedelta(minutes=5))] * 500
    assert ab.offenders(*counts(lines)) == {}


def test_ipv6_is_grouped_by_48():
    lines = [line(f"2001:db8:1:{k}::5", NOW - dt.timedelta(minutes=5)) for k in range(600)]
    assert list(map(str, ab.offenders(*counts(lines)))) == ["2001:db8:1::/48"]


def test_real_guard_never_bans_loopback_private_or_documentation_space():
    for ip in ("127.0.0.1", "10.1.2.3", "192.168.1.1", "198.51.100.7", "::1"):
        assert REAL_BANNABLE(ipaddress.ip_address(ip)) is False
    assert REAL_BANNABLE(ipaddress.ip_address("8.8.8.8")) is True


def test_recent_lines_reads_only_the_window_of_a_large_log(tmp_path):
    log = tmp_path / "tier1.log"
    start = NOW - dt.timedelta(hours=10)
    with log.open("w") as fh:
        for s in range(0, 10 * 3600, 2):  # 18,000 lines over 10 h
            fh.write(line("198.51.100.7", start + dt.timedelta(seconds=s)))
    since = NOW - dt.timedelta(minutes=15)
    got = list(ab.recent_lines([str(log)], since))
    assert len(got) == 15 * 60 // 2
    assert all(ab._parse_ts(g.split()[1]) >= since for g in got)


def test_log_paths_add_the_rotated_file_only_inside_the_window(tmp_path):
    live = tmp_path / "tier1.log"
    live.write_text("")
    rotated = tmp_path / "tier1.log-20261023-00"
    rotated.write_text("")
    (tmp_path / "tier1.log-20261022-00.gz").write_text("")
    import os
    old = (NOW - dt.timedelta(hours=3)).timestamp()
    os.utime(rotated, (old, old))
    assert ab.log_paths([str(live)], NOW - dt.timedelta(minutes=15)) == [str(live)]
    assert ab.log_paths([str(live)], NOW - dt.timedelta(hours=4)) == [str(rotated), str(live)]


def test_bans_renew_expire_respect_the_allowlist_and_the_per_run_cap(monkeypatch):
    nets = {ipaddress.ip_network(f"198.18.{n}.0/24"): "r" for n in range(1, 5)}
    old = {
        "198.18.1.0/24": {"since": "2026-10-22T12:00:00+00:00", "until": "2026-10-23T13:00:00+00:00", "reason": "x"},
        "203.0.113.0/24": {"since": "2026-10-21T12:00:00+00:00", "until": "2026-10-23T11:00:00+00:00", "reason": "x"},
    }
    monkeypatch.setattr(ab, "MAX_NEW", 1)
    allow = [ipaddress.ip_network("198.18.2.0/23")]  # covers .2 and .3
    bans, new, expired = ab.update_bans(old, nets, allow, NOW)
    assert expired == ["203.0.113.0/24"]
    assert new == ["198.18.4.0/24"]
    assert bans["198.18.1.0/24"]["until"] == "2026-10-24T12:00:00+00:00"  # renewed
    assert bans["198.18.1.0/24"]["since"] == "2026-10-22T12:00:00+00:00"
    assert set(bans) == {"198.18.1.0/24", "198.18.4.0/24"}


@pytest.fixture
def host(tmp_path, monkeypatch):
    log = tmp_path / "tier1.log"
    monkeypatch.setattr(ab, "LOGS", [str(log)])
    monkeypatch.setattr(ab, "GEO_FILE", tmp_path / "ocl-autoban.geo")
    monkeypatch.setattr(ab, "STATE_FILE", tmp_path / "state" / "autoban.json")
    monkeypatch.setattr(ab, "ALLOW_FILE", tmp_path / "allow.txt")
    state = {"cmds": [], "nginx_ok": True, "notified": []}

    def _run(cmd, **kw):
        state["cmds"].append(cmd)
        rc = 0 if (cmd != ["nginx", "-t"] or state["nginx_ok"]) else 1
        return subprocess.CompletedProcess(cmd, rc, stdout="", stderr="" if rc == 0 else "emerg: broken")

    monkeypatch.setattr(ab.subprocess, "run", _run)
    monkeypatch.setattr(ab, "notify", lambda new, bans: state["notified"].append(list(new)))

    class _Clock(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW

    monkeypatch.setattr(ab.dt, "datetime", _Clock)
    state["log"] = log
    return state


def test_main_bans_reloads_once_and_is_idempotent(host):
    host["log"].write_text("".join(burst(SPREAD, 240)))
    assert ab.main() == 0
    geo = ab.GEO_FILE.read_text()
    assert "198.51.100.0/24 1;" in geo and geo.startswith("# Generated")
    assert host["cmds"] == [["nginx", "-t"], ["systemctl", "reload", "nginx"]]
    assert host["notified"] == [["198.51.100.0/24"]]
    assert "198.51.100.0/24" in json.loads(ab.STATE_FILE.read_text())["bans"]

    host["cmds"].clear()
    assert ab.main() == 0  # same window again: renewal only, file unchanged
    assert host["cmds"] == []
    assert host["notified"] == [["198.51.100.0/24"]]


def test_main_restores_the_previous_list_when_nginx_rejects_it(host):
    ab.GEO_FILE.write_text("# previous\n")
    host["log"].write_text("".join(burst(SPREAD, 240)))
    host["nginx_ok"] = False
    assert ab.main() == 1
    assert ab.GEO_FILE.read_text() == "# previous\n"
    assert ["systemctl", "reload", "nginx"] not in host["cmds"]
    assert not ab.STATE_FILE.exists()


def test_dry_run_changes_nothing(host, monkeypatch):
    monkeypatch.setattr(ab, "DRY_RUN", True)
    host["log"].write_text("".join(burst(SPREAD, 240)))
    assert ab.main() == 0
    assert host["cmds"] == [] and not ab.GEO_FILE.exists() and not ab.STATE_FILE.exists()


def test_allowlist_file_keeps_a_network_out(host):
    ab.ALLOW_FILE.write_text("# integrators\n198.51.100.0/24  # synthetic partner\n")
    host["log"].write_text("".join(burst(SPREAD, 240)))
    assert ab.main() == 0
    assert "198.51.100.0/24" not in ab.GEO_FILE.read_text()
    assert host["notified"] == []


def test_public_snapshot_ships_no_generated_ban_list():
    for root in (REPO / "deploy", REPO / "systemd"):
        assert not list(root.rglob("ocl-autoban.geo"))
        assert not list(root.rglob("autoban-allow.txt"))
