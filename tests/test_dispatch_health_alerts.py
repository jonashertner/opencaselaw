"""dispatch_health_alerts.decide(): the dedup / re-nag / all-clear logic
that turns the (previously dark) alerts_dry_run list into ntfy sends."""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO / "scripts"))

import dispatch_health_alerts as dha  # noqa: E402

NOW = 1_780_000_000.0
ALERT = {"level": "warning", "key": "quick_publish_stale", "message": "stale 3h"}


def test_new_alert_is_sent_and_recorded():
    to_send, clears, state = dha.decide([ALERT], {}, NOW)
    assert to_send == [ALERT]
    assert clears == []
    assert state["quick_publish_stale"]["last_sent"] == NOW


def test_recent_alert_is_not_resent():
    prior = {"quick_publish_stale": {"last_sent": NOW - 3600, "level": "warning"}}
    to_send, clears, state = dha.decide([ALERT], prior, NOW)
    assert to_send == []
    assert state["quick_publish_stale"]["last_sent"] == NOW - 3600  # unchanged


def test_stale_alert_renags_after_renag_hours():
    prior = {"quick_publish_stale": {
        "last_sent": NOW - (dha.RENAG_HOURS * 3600 + 1), "level": "warning"}}
    to_send, _, state = dha.decide([ALERT], prior, NOW)
    assert to_send == [ALERT]
    assert state["quick_publish_stale"]["last_sent"] == NOW


def test_cleared_alert_sends_all_clear_once_and_forgets():
    prior = {"quick_publish_stale": {"last_sent": NOW - 3600, "level": "warning"}}
    to_send, clears, state = dha.decide([], prior, NOW)
    assert to_send == []
    assert clears == ["quick_publish_stale"]
    assert state == {}  # forgotten -> a second empty poll sends nothing


def test_alert_without_key_is_ignored():
    to_send, clears, state = dha.decide([{"level": "warning"}], {}, NOW)
    assert to_send == [] and clears == [] and state == {}


# ── cert-expiry monitoring (added 2026-07-07) ──────────────────────────
import datetime as _dt
from scripts.dispatch_health_alerts import _cert_days_left, check_cert_expiry


def test_cert_days_left_parses_openssl_enddate():
    now = _dt.datetime(2026, 7, 7, 12, 0, 0)
    assert _cert_days_left("notAfter=Oct  5 07:05:00 2026 GMT", now) == 89
    assert _cert_days_left("Sep 20 20:36:52 2026 GMT", now) == 75
    # already expired -> negative
    assert _cert_days_left("Jun 26 09:50:31 2026 GMT", now) < 0


def test_cert_days_left_unparseable_returns_none():
    assert _cert_days_left("") is None
    assert _cert_days_left("garbage") is None


def test_check_cert_expiry_fires_warning_and_critical(tmp_path, monkeypatch):
    # fabricate two "certs" via a fake openssl by monkeypatching subprocess.run
    import scripts.dispatch_health_alerts as d
    (tmp_path / "word.opencaselaw.ch").mkdir()
    (tmp_path / "mcp.opencaselaw.ch").mkdir()
    wp = tmp_path / "word.opencaselaw.ch" / "fullchain.pem"; wp.write_text("x")
    mp = tmp_path / "mcp.opencaselaw.ch" / "fullchain.pem"; mp.write_text("x")
    soon = (_dt.datetime.utcnow() + _dt.timedelta(days=3)).strftime("notAfter=%b %d %H:%M:%S %Y GMT")
    far = (_dt.datetime.utcnow() + _dt.timedelta(days=75)).strftime("notAfter=%b %d %H:%M:%S %Y GMT")

    class _R:
        def __init__(self, out): self.stdout = out
    def fake_run(cmd, **kw):
        return _R(soon if "word" in cmd[-1] else far)
    monkeypatch.setattr(d.subprocess, "run", fake_run)

    alerts = check_cert_expiry(str(tmp_path / "*" / "fullchain.pem"))
    keys = {a["key"]: a["level"] for a in alerts}
    assert keys.get("cert_expiry:word.opencaselaw.ch") == "critical"  # 3d < 5
    assert "cert_expiry:mcp.opencaselaw.ch" not in keys                # 75d, fine


def test_fetch_health_gives_slow_workers_time_to_answer(monkeypatch):
    """/metrics/health took 6.6-7.3 s in Oct 2026; a 6 s timeout reported
    all 8 live workers as unreachable for days."""
    seen = []

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self, *a):
            return b'{"alerts_dry_run": []}'

    def fake_urlopen(url, timeout=None):
        seen.append(timeout)
        return _Resp()

    monkeypatch.setattr(dha.urllib.request, "urlopen", fake_urlopen)
    assert dha.fetch_health() == {"alerts_dry_run": []}
    assert seen == [dha.FETCH_TIMEOUT_S]
    assert dha.FETCH_TIMEOUT_S >= 15
