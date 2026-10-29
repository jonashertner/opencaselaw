"""All operator-alert scripts honor the unified NTFY_TOPIC env.

2026-08-24: alerts were split across three hardcoded topics with zero
subscribers. One env-driven topic (set via /opt/caselaw/ops.env drop-ins)
now feeds them all; each script keeps its legacy topic as the fallback so
an unconfigured run behaves exactly as before.
"""
import importlib
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def _reload(modname, monkeypatch, **env):
    for k, v in env.items():
        (monkeypatch.setenv if v is not None else monkeypatch.delenv)(
            k, *( [v] if v is not None else [False] ))
    mod = importlib.import_module(modname)
    return importlib.reload(mod)


def test_scraper_freshness_topic_env(monkeypatch):
    m = _reload("scripts.check_scraper_freshness", monkeypatch,
                NTFY_TOPIC="ops-test-topic")
    assert m.NTFY_URL == "https://ntfy.sh/ops-test-topic"
    m = _reload("scripts.check_scraper_freshness", monkeypatch,
                NTFY_TOPIC=None)
    assert m.NTFY_URL.endswith("/opencaselaw-scrapers")  # legacy fallback


def test_output_freshness_topic_env(monkeypatch):
    m = _reload("scripts.check_output_freshness", monkeypatch,
                NTFY_TOPIC="ops-test-topic")
    assert m.NTFY_URL == "https://ntfy.sh/ops-test-topic"
    m = _reload("scripts.check_output_freshness", monkeypatch,
                NTFY_TOPIC=None)
    assert m.NTFY_URL.endswith("/opencaselaw-publish")


def test_anomaly_explicit_url_still_wins(monkeypatch):
    m = _reload("scripts.citation_anomaly_report", monkeypatch,
                NTFY_TOPIC="ops-test-topic",
                OCL_ANOMALY_NTFY="https://ntfy.example/explicit")
    assert m.NTFY_URL == "https://ntfy.example/explicit"
    m = _reload("scripts.citation_anomaly_report", monkeypatch,
                OCL_ANOMALY_NTFY=None, NTFY_TOPIC="ops-test-topic")
    assert m.NTFY_URL == "https://ntfy.sh/ops-test-topic"


def test_bger_withdrawn_topic_env(monkeypatch):
    m = _reload("scripts.check_bger_withdrawn", monkeypatch,
                NTFY_TOPIC="ops-test-topic")
    assert m.NTFY_URL == "https://ntfy.sh/ops-test-topic"
    m = _reload("scripts.check_bger_withdrawn", monkeypatch,
                NTFY_TOPIC=None)
    assert m.NTFY_URL.endswith("/opencaselaw-scrapers")  # legacy fallback


# 2026-10-29: the publish, failure, health and suggestion alerts take the operator
# topic from the environment only. A topic name in this public repo reached every old
# checkout's test loop (~350 fake "Publish OK"); unset, they send nothing.

def test_publish_topic_env_only(monkeypatch):
    m = _reload("publish", monkeypatch, NTFY_TOPIC="ops-test-topic")
    assert m.NTFY_TOPIC == "ops-test-topic"
    m = _reload("publish", monkeypatch, NTFY_TOPIC=None)
    assert m.NTFY_TOPIC == ""


def test_publish_failure_alert_topic_env_only(monkeypatch, capsys):
    m = _reload("scripts.publish_failure_alert", monkeypatch, NTFY_TOPIC="ops-test-topic")
    assert m.NTFY_URL == "https://ntfy.sh/ops-test-topic"
    m = _reload("scripts.publish_failure_alert", monkeypatch, NTFY_TOPIC=None)
    assert m.NTFY_URL == ""
    sent = []
    monkeypatch.setattr(m.urllib.request, "urlopen", lambda *a, **k: sent.append(a))
    m.post_ntfy("tail")
    assert sent == [] and "NTFY_TOPIC not set" in capsys.readouterr().err


def test_health_alerts_topic_env_only(monkeypatch):
    m = _reload("scripts.dispatch_health_alerts", monkeypatch,
                HEALTH_ALERTS_NTFY=None, NTFY_TOPIC="ops-test-topic")
    assert m.NTFY_URL == "https://ntfy.sh/ops-test-topic"
    m = _reload("scripts.dispatch_health_alerts", monkeypatch,
                HEALTH_ALERTS_NTFY="https://ntfy.example/explicit", NTFY_TOPIC="ops-test-topic")
    assert m.NTFY_URL == "https://ntfy.example/explicit"
    m = _reload("scripts.dispatch_health_alerts", monkeypatch,
                HEALTH_ALERTS_NTFY=None, NTFY_TOPIC=None)
    assert m.NTFY_URL == ""
    sent = []
    monkeypatch.setattr(m.urllib.request, "urlopen", lambda *a, **k: sent.append(a))
    assert m.send("t", "m") is False and sent == []


def test_suggestions_topic_env_only(monkeypatch):
    import suggestions as sg
    urls = []
    monkeypatch.setattr(sg.urllib.request, "urlopen", lambda req, timeout=None: urls.append(req.full_url))
    for k in ("OCL_SUGGEST_NTFY_TOPIC", "NTFY_TOPIC", "OCL_SUGGEST_NTFY"):
        monkeypatch.delenv(k, raising=False)
    sg._notify("1 Vorschlag")
    monkeypatch.setenv("NTFY_TOPIC", "ops-test-topic")
    sg._notify("1 Vorschlag")
    monkeypatch.setenv("OCL_SUGGEST_NTFY_TOPIC", "suggest-topic")
    sg._notify("1 Vorschlag")
    assert urls == ["https://ntfy.sh/ops-test-topic", "https://ntfy.sh/suggest-topic"]


def test_no_alert_script_names_the_old_public_topic_as_its_target():
    for rel in ("publish.py", "suggestions.py", "scripts/publish_failure_alert.py",
                "scripts/dispatch_health_alerts.py"):
        src = (REPO / rel).read_text(encoding="utf-8")
        assert "ntfy.sh/opencaselaw-publish" not in src, rel
        assert '"opencaselaw-publish")' not in src, rel
