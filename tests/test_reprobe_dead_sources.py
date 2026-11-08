"""Offline tests for the dead-source re-probe monitor (scripts/reprobe_dead_sources.py).

Generalizes the 2026-06-21 be_steuerrekurs verification: a dead source is RESUMED iff a
bounded, isolated discovery yields any row (or, for Tribuna, portal_count > 0). Every test
injects a FakeScraper via the scraper_factory seam — no real scraper, no network (invariant #8).
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import scripts.reprobe_dead_sources as rp  # noqa: E402
from scripts.check_scraper_freshness import KNOWN_DEAD_SOURCES  # noqa: E402


class FakeScraper:
    def __init__(self, *, stubs=None, portal_count=None, raises=None, state_dir=None):
        self._stubs = list(stubs or [])
        self.portal_count = portal_count
        self._raises = raises
        self.state_dir = state_dir

    def discover_new(self, since=None):
        if self._raises:
            raise self._raises
        for s in self._stubs:
            yield s


def _factory(**kw):
    """A scraper_factory that builds a FakeScraper and records the state_dir it got."""
    cap = {}

    def factory(court, state_dir):
        cap["state_dir"] = state_dir
        cap["empty_at_build"] = not any(Path(state_dir).iterdir())
        return FakeScraper(state_dir=state_dir, **kw)

    factory.captured = cap
    return factory


# ── candidate set ────────────────────────────────────────────────────────────

def test_candidates_subset_of_known_dead():
    assert set(rp.candidate_sources(probe_sg=True)) <= KNOWN_DEAD_SOURCES


def test_candidates_membership():
    base = set(rp.candidate_sources())
    assert "ow_gerichte" in base
    assert "be_steuerrekurs" not in base   # live again since the StRK filter fix (2026-11-08)
    assert not ({"ta_sst", "ch_bundesrat", "comcom"} & base)            # rare-publication
    assert not ({"ch_vb", "ag_baugesetzgebung", "ag_weitere"} & base)   # no scraper class / closed collection
    assert "sg_publikationen" not in base
    assert "sg_publikationen" in rp.candidate_sources(probe_sg=True)


# ── probe classification ─────────────────────────────────────────────────────

def test_probe_rows_resumed():
    f = _factory(stubs=[{"docket_number": "STRK 2026 1"}, {"docket_number": "STRK 2026 2"}])
    r = rp.probe_source("be_steuerrekurs", scraper_factory=f)
    assert r["status"] == "RESUMED" and r["stub_count"] >= 1
    assert r["sample_docket"] == "STRK 2026 1"


def test_probe_empty_still_dead():
    r = rp.probe_source("be_steuerrekurs", scraper_factory=_factory(stubs=[]))
    assert r["status"] == "STILL_DEAD" and r["stub_count"] == 0


def test_probe_tribuna_portal_count_signal():
    # 0 yielded stubs but a non-zero raw total -> RESUMED (be_vg //OK[11420] vs be_steuerrekurs //OK[0])
    assert rp.probe_source("be_steuerrekurs", scraper_factory=_factory(stubs=[], portal_count=42))["status"] == "RESUMED"
    assert rp.probe_source("be_steuerrekurs", scraper_factory=_factory(stubs=[], portal_count=0))["status"] == "STILL_DEAD"


def test_probe_protocol_error():
    from scrapers.cantonal.base_tribuna import TribunaProtocolError
    r = rp.probe_source("be_steuerrekurs", scraper_factory=_factory(raises=TribunaProtocolError("//EX")))
    assert r["status"] == "PROTOCOL_BROKEN"  # a //EX is NOT empty


def test_probe_generic_exception_is_probe_error():
    r = rp.probe_source("ow_gerichte", scraper_factory=_factory(raises=ConnectionError("offline")))
    assert r["status"] == "PROBE_ERROR"


def test_probe_content_agnostic():
    # answers "rows now?", not "rows well-formed?" — a minimal stub still = a row
    r = rp.probe_source("ow_gerichte", scraper_factory=_factory(stubs=[{"docket_number": ""}]))
    assert r["status"] == "RESUMED"


def test_probe_uses_fresh_temp_state_dir():
    f = _factory(stubs=[{"docket_number": "x"}])
    rp.probe_source("be_steuerrekurs", scraper_factory=f)
    sd = Path(f.captured["state_dir"])
    assert "reprobe_" in str(sd)                          # fresh temp dir, not production state/
    assert (REPO / "state") not in sd.parents
    assert f.captured["empty_at_build"]                   # empty -> is_known() always False (the hard-won lesson)


def test_probe_is_bounded_does_not_drain():
    class InfScraper:
        portal_count = None

        def discover_new(self, since=None):
            i = 0
            while True:
                i += 1
                yield {"docket_number": f"d{i}"}

    r = rp.probe_source("ow_gerichte", max_stubs=3, scraper_factory=lambda c, s: InfScraper())
    assert r["status"] == "RESUMED" and r["stub_count"] == 3


def test_probe_never_touches_network(monkeypatch):
    import base_scraper
    monkeypatch.setattr(base_scraper.BaseScraper, "get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network!")))
    monkeypatch.setattr(base_scraper.BaseScraper, "post", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network!")))
    r = rp.probe_source("be_steuerrekurs", scraper_factory=_factory(stubs=[{"docket_number": "x"}]))
    assert r["status"] == "RESUMED"  # the probe path itself never reaches transport


# ── alerts + orchestration ───────────────────────────────────────────────────

def test_resumed_emits_alert_still_dead_silent():
    results = {
        "be_steuerrekurs": {"status": "RESUMED", "stub_count": 5, "portal_count": 5},
        "ow_gerichte": {"status": "STILL_DEAD", "stub_count": 0, "portal_count": None},
    }
    alerts = rp.build_alerts(results)
    assert len(alerts) == 1
    assert "RESUMED be_steuerrekurs" in alerts[0]
    assert "check_scraper_freshness.py" in alerts[0]  # names the exact file to edit


def test_run_resumed_does_not_mutate_known_dead(monkeypatch, tmp_path):
    before = set(KNOWN_DEAD_SOURCES)
    monkeypatch.setattr(rp, "LOG_JSON", tmp_path / "r.json")
    monkeypatch.setattr(rp, "LOG_TXT", tmp_path / "r.log")
    monkeypatch.setattr(rp, "STATE_FILE", tmp_path / "s.json")
    out = rp.run(send_ntfy=False, scraper_factory=_factory(stubs=[{"docket_number": "x"}]))
    assert KNOWN_DEAD_SOURCES == before                              # never auto-undead
    assert out["results"]["ow_gerichte"]["status"] == "RESUMED"


def test_probe_with_timeout_hung_source_moves_on():
    import time as _t

    class HungScraper:
        portal_count = None

        def discover_new(self, since=None):
            _t.sleep(5)            # hang past the budget
            yield {"docket_number": "x"}

    r = rp._probe_with_timeout("ow_gerichte", 0.5, lambda c, s: HungScraper())
    assert r["status"] == "PROBE_TIMEOUT"          # moved on, did not block the run
    assert rp.build_alerts({"ow_gerichte": r}) == []  # timeout is silent (not a resumed signal)


def test_probe_with_timeout_fast_source_ok():
    r = rp._probe_with_timeout("be_steuerrekurs", 5, _factory(stubs=[{"docket_number": "x"}]))
    assert r["status"] == "RESUMED"


def test_main_always_exits_zero(monkeypatch, tmp_path):
    monkeypatch.setattr(rp, "_default_factory", lambda c, s: (_ for _ in ()).throw(RuntimeError("kaboom")))
    monkeypatch.setattr(rp, "LOG_JSON", tmp_path / "r.json")
    monkeypatch.setattr(rp, "LOG_TXT", tmp_path / "r.log")
    monkeypatch.setattr(rp, "STATE_FILE", tmp_path / "s.json")
    assert rp.main(["--no-ntfy"]) == 0                               # all PROBE_ERROR, still exit 0
