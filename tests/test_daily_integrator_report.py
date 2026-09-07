"""daily_integrator_report must read nginx tier1.log* — the log that carries
the traffic — not access.log, which only sees the catch-all server's 301s.
The 2026-09-05 report analysed 9 entries on a ~400k-request day.

Offline: a small tier1 fixture, no network. ntfy is stubbed and must not be
called in dry-run mode.
"""
import gzip
import importlib.util
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / "tests" / "fixtures" / "nginx_tier1_sample.log"

_spec = importlib.util.spec_from_file_location(
    "daily_integrator_report", REPO / "scripts" / "daily_integrator_report.py"
)
dir_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dir_mod)

# The fixture is wholly synthetic: 38 well-formed lines plus a provenance
# comment and two malformed lines. Four valid lines are /health probes, which
# the report keeps in total_requests but drops from per-IP stats.
N_VALID = 38
N_HEALTH = 4
HTTPX_IP = "203.0.113.7"      # python-httpx MCP client, 12 req over 9.5 h
SCANNER_IP = "192.0.2.44"     # 11 probes for /.env & co, no MCP
GOOGLEBOT_IP = "192.0.2.53"
BROWSER_IP = "198.51.100.23"  # 3 req: below the 10-request candidate floor

# JSON shape of the report before the tier1 change (2026-09-05.json on the VPS).
TOP_KEYS = {"generated", "period", "summary", "integrator_candidates", "commercial_flags", "hourly_volume"}
PERIOD_KEYS = {"entries_analyzed", "first", "last"}
SUMMARY_KEYS = {"total_requests", "unique_ips", "client_breakdown", "mcp_sessions"}
CANDIDATE_KEYS = {
    "ip", "requests", "mcp_requests", "api_requests", "client_type", "top_user_agent",
    "unique_paths", "duration_hours", "first_seen", "last_seen", "bytes_total",
    "is_high_volume", "is_mcp_user", "is_programmatic",
}


def _entries(*files, since=None):
    return list(dir_mod.parse_logs(list(files), since=since))


def _log_dir(tmp_path: Path) -> Path:
    """A fake /var/log/nginx: the fixture as the hot tier1.log, plus a decoy
    access.log in combined format that must be ignored."""
    d = tmp_path / "nginx"
    d.mkdir()
    shutil.copy(FIXTURE, d / "tier1.log")
    (d / "access.log").write_text(
        '9.9.9.9 - - [05/Sep/2026:04:38:21 +0000] "GET / HTTP/1.1" 301 162 "-" "curl/8.5.0"\n' * 50
    )
    return d


def test_parses_tier1_format_and_skips_malformed_lines():
    entries = _entries(FIXTURE)
    assert len(entries) == N_VALID

    first = entries[0]
    assert first["ip"] == BROWSER_IP
    assert first["method"] == "GET"
    assert first["path"] == "/api/decisions/vd_omni_PE.2018.0215/export.ris"
    assert first["status"] == 200
    assert first["ua"] == "Synthetic Mozilla Chrome/1.0"
    assert first["timestamp"] == datetime(2026, 9, 5, 0, 0, 1, tzinfo=timezone.utc)
    assert first["timestamp"].tzinfo is not None
    assert first["size"] == 0  # tier1 logs no byte count

    # a UA of "-" parses (empty-ish UA, not a dropped line)
    dash = [e for e in entries if e["ip"] == "203.0.113.9"]
    assert len(dash) == 1 and dash[0]["ua"] == "-"
    # the 400 with an empty request line is not an entry
    assert not any(e["ip"] == "192.0.2.99" for e in entries)


def test_gzip_rotation_reads_like_plain(tmp_path):
    gz = tmp_path / "tier1.log-20260905-00.gz"
    with open(FIXTURE, "rb") as src, gzip.open(gz, "wb") as dst:
        shutil.copyfileobj(src, dst)
    assert _entries(gz) == _entries(FIXTURE)


def test_since_filters_entries_and_skips_files_older_than_the_window(tmp_path):
    since = datetime(2026, 9, 5, 6, 0, tzinfo=timezone.utc)
    fresh = tmp_path / "tier1.log"
    shutil.copy(FIXTURE, fresh)
    kept = _entries(fresh, since=since)
    assert len(kept) == 24  # 4 httpx + 3 googlebot + 3 health + 11 scanner + browser, claude, dash-UA
    assert all(e["timestamp"] >= since for e in kept)

    # naive `since` is treated as UTC (the .gz crash of the old version)
    assert len(_entries(fresh, since=since.replace(tzinfo=None))) == 24

    # A file last written before the window cannot hold entries in it:
    # it is not opened at all (the stale April rotations on the VPS).
    stale = tmp_path / "tier1.log-20200101-00"
    shutil.copy(FIXTURE, stale)
    old = datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp()
    os.utime(stale, (old, old))
    assert _entries(stale, since=since) == []
    assert len(_entries(stale)) == N_VALID


def test_report_shape_is_unchanged_and_candidates_appear():
    report = dir_mod.analyze(dir_mod.parse_logs([FIXTURE]))

    assert set(report) == TOP_KEYS
    assert set(report["period"]) == PERIOD_KEYS
    assert set(report["summary"]) == SUMMARY_KEYS
    for c in report["integrator_candidates"]:
        assert set(c) == CANDIDATE_KEYS
    for f in report["commercial_flags"]:
        assert set(f) == CANDIDATE_KEYS | {"flags"}

    assert report["period"]["entries_analyzed"] == N_VALID
    assert report["period"]["first"] == "2026-09-05T00:00:01+00:00"
    assert report["period"]["last"] == "2026-09-05T18:00:30+00:00"

    s = report["summary"]
    assert s["total_requests"] == N_VALID
    assert s["unique_ips"] == 6  # /health probes are not an IP of interest
    assert s["mcp_sessions"] == 2  # httpx + Claude-User
    assert s["client_breakdown"] == {
        "python-bot": 12, "unknown": 12, "googlebot": 6, "browser": 3, "claude.ai": 1,
    }
    assert sum(s["client_breakdown"].values()) == N_VALID - N_HEALTH

    # candidates: >= 10 requests, crawlers excluded, browser below the floor
    assert [c["ip"] for c in report["integrator_candidates"]] == [HTTPX_IP, SCANNER_IP]

    httpx = report["integrator_candidates"][0]
    assert httpx["requests"] == 12
    assert httpx["mcp_requests"] == 10  # /sse + /messages/ + /mcp
    assert httpx["api_requests"] == 2
    assert httpx["client_type"] == "python-bot"
    assert httpx["unique_paths"] == 5
    assert httpx["duration_hours"] == 9.5
    assert httpx["first_seen"] == "2026-09-05T00:10:00+00:00"
    assert httpx["last_seen"] == "2026-09-05T09:40:00+00:00"
    assert httpx["bytes_total"] == 0
    assert httpx["is_mcp_user"] and httpx["is_programmatic"] and not httpx["is_high_volume"]

    scanner = report["integrator_candidates"][1]
    assert scanner["client_type"] == "unknown"
    assert scanner["mcp_requests"] == 0 and not scanner["is_programmatic"]

    # only the sustained programmatic MCP client is flagged
    assert [f["ip"] for f in report["commercial_flags"]] == [HTTPX_IP]
    assert report["commercial_flags"][0]["flags"] == [
        "programmatic client (python-bot)",
        "sustained access (9.5h)",
    ]
    assert "2026-09-05 00:00" in report["hourly_volume"]

    alert = dir_mod.generate_alert(report)
    assert alert and HTTPX_IP in alert and "sustained access" in alert


def test_empty_input_yields_error_report():
    report = dir_mod.analyze([])
    assert report["error"] == "no log entries found"
    assert "generated" in report


def test_main_dry_run_reads_tier1_only_writes_nothing_and_never_notifies(tmp_path, monkeypatch, capsys):
    d = _log_dir(tmp_path)
    reports = tmp_path / "reports"
    monkeypatch.setattr(dir_mod, "OUTPUT_DIR", reports)
    calls = []
    monkeypatch.setattr(dir_mod, "notify", lambda *a, **k: calls.append(a))

    # --days wide enough that the 2026-09-05 fixture is inside the window
    dir_mod.main(["--log-dir", str(d), "--days", "36500", "--dry-run", "--print", "--notify"])

    out = capsys.readouterr().out
    assert not reports.exists()
    assert calls == []
    assert "[dry-run] report not written" in out
    assert "[dry-run] alert that would be sent" in out
    assert f"Total requests: {N_VALID}, Unique IPs: 6" in out  # access.log's 50 lines ignored
    assert HTTPX_IP in out and SCANNER_IP in out
    assert "programmatic client (python-bot)" in out


def test_main_writes_json_report_and_sends_alert_when_asked(tmp_path, monkeypatch, capsys):
    d = _log_dir(tmp_path)
    reports = tmp_path / "reports"
    monkeypatch.setattr(dir_mod, "OUTPUT_DIR", reports)
    calls = []
    monkeypatch.setattr(dir_mod, "notify", lambda title, message, **k: calls.append((title, message)))
    out_path = tmp_path / "out" / "report.json"

    dir_mod.main(["--log-dir", str(d), "--days", "36500", "--output", str(out_path), "--notify"])

    assert not reports.exists()  # explicit --output wins
    report = json.loads(out_path.read_text())
    assert set(report) == TOP_KEYS
    assert [c["ip"] for c in report["integrator_candidates"]] == [HTTPX_IP, SCANNER_IP]
    assert len(calls) == 1
    title, message = calls[0]
    assert title == "OpenCaseLaw Integrator Alert"
    assert HTTPX_IP in message
    assert "Alert sent via ntfy.sh" in capsys.readouterr().out


def test_main_without_log_files_writes_error_report(tmp_path, monkeypatch, capsys):
    empty = tmp_path / "nginx"
    empty.mkdir()
    monkeypatch.setattr(dir_mod, "OUTPUT_DIR", tmp_path / "reports")
    dir_mod.main(["--log-dir", str(empty), "--dry-run", "--print"])
    captured = capsys.readouterr()
    assert "no tier1.log* files" in captured.err
    assert "No data: no log entries found" in captured.out


def test_print_summary_caps_the_flag_list(capsys):
    """A real day flags hundreds of IPs; the journal must not get all of them."""
    base = dir_mod.analyze(dir_mod.parse_logs([FIXTURE]))
    flag = base["commercial_flags"][0]
    many = [{**flag, "ip": f"10.0.0.{i}", "requests": 1000 - i} for i in range(25)]
    report = {**base, "commercial_flags": many}
    dir_mod.print_summary(report)
    out = capsys.readouterr().out
    assert f"Commercial integrator candidates (25, top {dir_mod.PRINT_FLAGS_MAX} by volume)" in out
    assert "10.0.0.19" in out and "10.0.0.20" not in out
    assert "... and 5 more in the JSON report" in out
