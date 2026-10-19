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
