#!/usr/bin/env python3
"""Re-probe KNOWN_DEAD_SOURCES for resumption — detect when a dead portal returns data again.

A court flagged dead is silently excluded from freshness alerts forever; nothing re-tests
whether it RESUMED. This weekly monitor re-probes the truly-dead sources that have a working
scraper class, using a RAW DATA-COUNT signal (not mere reachability): instantiate the scraper
against a FRESH temp state dir (so ``state.is_known()`` is always False — a resumed source
returning already-known rows still surfaces rows) and run a BOUNDED discovery (first few stubs
via ``itertools.islice``). Any row => the source has data => RESUMED. For Tribuna sources we
also read ``scraper.portal_count`` (the first-page ``//OK[N]`` total, set BEFORE is_known
filtering) — the exact be_verwaltungsgericht(//OK[11420]) vs be_steuerrekurs(//OK[0]) distinction
established by the 2026-06-21 manual verification this generalizes.

Scope is an EXPLICIT allowlist, never "KNOWN_DEAD minus exclusions" — so a future dead tag can't
silently become a probe target or crash on a missing class:
  - REPROBE_DEAD_PORTALS   truly-dead portals WITH an instantiable scraper (the only probe-able ones)
  - OPTIONAL_REPROBE       sg_publikationen — its key is the LIVE direct scraper, so off by default
  - RARE_PUBLICATION_*     ch_bundesrat/comcom/ta_sst — empty is their NORMAL state (would false-positive)
  - NO_SCRAPER_CLASS       ch_vb — entscheidsuche-ingest key only, nothing to instantiate
  - CLOSED_COLLECTIONS     ag_baugesetzgebung/ag_weitere — direct scraper, closed collections

Alerts on GOOD news (a dead source has rows again) via ntfy (priority 'default'), deduped on the
resumed-set digest with its OWN state file, always exit 0. PROTOCOL_BROKEN (//EX) is the one loud
case. The script only PROPOSES re-enable; the human edits KNOWN_DEAD_SOURCES (scrapers pipeline-gate
posture — never auto-undead). Tests stay offline via the scraper_factory seam. A weekly systemd
timer is PROPOSED in systemd/opencaselaw-reprobe-dead.* (gated; deploy with approval).
"""
from __future__ import annotations

import argparse
import importlib
import itertools
import json
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.check_scraper_freshness import (  # reuse, don't fork
    KNOWN_DEAD_SOURCES,
    _alert_set_digest,
    post_ntfy,
)

# Truly-dead portals WITH an instantiable scraper whose discover_new filters via state.is_known —
# the only KNOWN_DEAD sources the fresh-temp-state + bounded-discovery probe can meaningfully test.
# be_steuerrekurs left 2026-11-08: the portal never died, the scraper sent the
# court filter "STRK" where the portal expects "StRK" (case-sensitive).
REPROBE_DEAD_PORTALS = {"ow_gerichte"}
# sg_publikationen: its registry key is the LIVE direct TYPO3/DIAM scraper, so a bounded discovery
# would yield rows and flag RESUMED every week (noise). Off by default; --probe-sg detects the ES
# feed reviving (operationally moot — direct coverage already exists).
OPTIONAL_REPROBE = {"sg_publikationen"}
# Documented exclusions (NEVER probed):
RARE_PUBLICATION_NO_REPROBE = {"ta_sst", "ch_bundesrat", "comcom"}  # empty is their normal state
NO_SCRAPER_CLASS = {"ch_vb"}                                         # entscheidsuche-ingest key only
CLOSED_COLLECTIONS = {"ag_baugesetzgebung", "ag_weitere"}            # direct scraper, nothing new to find

LOG_JSON = REPO / "logs" / "reprobe_dead_sources.json"
LOG_TXT = REPO / "logs" / "reprobe_dead_sources.log"
STATE_FILE = REPO / "state" / "reprobe_dead_sources_last_dispatched.json"

# Per-source wall-clock guard. islice bounds the row COUNT, not connect time, and a probe runs
# real scrapers (Tribuna sessions, ow_gerichte's Playwright). A single hung portal must never block
# the whole run, so each probe runs in a daemon thread joined with this budget; over-budget => moved
# on as PROBE_TIMEOUT (silent, like PROBE_ERROR). The systemd unit TimeoutStartSec is the outer cap.
PROBE_TIMEOUT_S = 150


def _default_factory(court: str, state_dir: Path):
    """court -> instantiated scraper(state_dir=...). Reads run_scraper.SCRAPERS (module, classname)."""
    import run_scraper
    module_name, class_name = run_scraper.SCRAPERS[court]
    mod = importlib.import_module(module_name)
    return getattr(mod, class_name)(state_dir=state_dir)


def _result(status, stub_count, sample_docket, portal_count, t0, error):
    return {
        "status": status,
        "stub_count": stub_count,
        "sample_docket": sample_docket,
        "portal_count": portal_count,
        "elapsed_s": round(time.monotonic() - t0, 1),
        "error": error,
    }


def probe_source(court: str, *, max_stubs: int = 3, scraper_factory=None) -> dict:
    """Bounded, isolated raw-data-count probe of a known-dead source.

    status in {RESUMED, STILL_DEAD, PROTOCOL_BROKEN, PROBE_ERROR}.
    NEVER writes real state/; NEVER calls fetch_decision; bounded to the first ``max_stubs``.
    """
    factory = scraper_factory or _default_factory
    try:
        from scrapers.cantonal.base_tribuna import TribunaProtocolError
    except Exception:  # base_tribuna unavailable — a non-Tribuna probe must still run
        class TribunaProtocolError(Exception):  # type: ignore[no-redef]
            pass
    t0 = time.monotonic()
    stub_count, sample_docket, portal_count = 0, None, None
    # FRESH isolated temp state dir => state.is_known() always False => a resumed source
    # returning ALREADY-KNOWN rows still surfaces rows (the hardest-won lesson here).
    with tempfile.TemporaryDirectory(prefix="reprobe_") as tmp:
        try:
            scraper = factory(court, Path(tmp))
            # BOUNDED: islice caps consumption — a resumed 50k-row source is detected from
            # its first page, never fully crawled.
            for stub in itertools.islice(scraper.discover_new(), max_stubs):
                stub_count += 1
                if sample_docket is None and isinstance(stub, dict):
                    sample_docket = stub.get("docket_number")
            portal_count = getattr(scraper, "portal_count", None)
        except TribunaProtocolError as e:  # //EX is NOT empty — a distinct, loud signal
            return _result("PROTOCOL_BROKEN", stub_count, sample_docket, portal_count, t0, str(e))
        except Exception as e:  # best-effort: one dead portal must not crash the run
            return _result("PROBE_ERROR", stub_count, sample_docket, portal_count, t0, repr(e))
    has_data = stub_count > 0 or (isinstance(portal_count, int) and portal_count > 0)
    return _result("RESUMED" if has_data else "STILL_DEAD", stub_count, sample_docket, portal_count, t0, None)


def _probe_with_timeout(court: str, timeout_s: float, scraper_factory=None) -> dict:
    """Run probe_source in a daemon thread; if it exceeds timeout_s (a hung portal), return
    PROBE_TIMEOUT and move on. One hung source must not block the whole run (the live ow_gerichte
    Playwright hang that motivated this)."""
    import threading

    box: dict = {}
    t0 = time.monotonic()

    def _w():
        box["r"] = probe_source(court, scraper_factory=scraper_factory)

    th = threading.Thread(target=_w, daemon=True)
    th.start()
    th.join(timeout_s)
    if th.is_alive():
        return _result("PROBE_TIMEOUT", 0, None, None, t0, f"probe exceeded {timeout_s}s (hung portal)")
    return box.get("r") or _result("PROBE_ERROR", 0, None, None, t0, "no result")


def candidate_sources(probe_sg: bool = False) -> list[str]:
    cands = set(REPROBE_DEAD_PORTALS)
    if probe_sg:
        cands |= OPTIONAL_REPROBE
    return sorted(cands)


def build_alerts(results: dict[str, dict]) -> list[str]:
    alerts: list[str] = []
    for court in sorted(results):
        r = results[court]
        if r["status"] == "RESUMED":
            pc = r.get("portal_count")
            n = pc if isinstance(pc, int) and pc else r.get("stub_count", 0)
            alerts.append(
                f"RESUMED {court}: discovery returned >= {n} row(s) — candidate for re-enable "
                f"(remove from KNOWN_DEAD_SOURCES in scripts/check_scraper_freshness.py)"
            )
        elif r["status"] == "PROTOCOL_BROKEN":
            alerts.append(f"FAIL {court}: scraper protocol broke during re-probe ({(r.get('error') or '')[:80]})")
    return alerts


def _load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}


def _save_state(payload: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(payload, indent=2))


def run(*, probe_sg: bool = False, send_ntfy: bool = True, scraper_factory=None) -> dict:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    results = {c: _probe_with_timeout(c, PROBE_TIMEOUT_S, scraper_factory) for c in candidate_sources(probe_sg)}
    alerts = build_alerts(results)
    digest = _alert_set_digest(alerts)
    last = _load_state()
    dispatched: object = False
    if alerts and digest != last.get("digest"):
        if send_ntfy:
            if post_ntfy(alerts, today, priority="default",
                         title=f"opencaselaw dead-source reprobe — {len(alerts)} candidate(s) for re-enable"):
                _save_state({"digest": digest, "dispatched_at": today, "alerts": alerts})
                dispatched = True
        else:
            dispatched = "skipped"
    elif not alerts and STATE_FILE.exists():
        # resumed set went empty (a source was re-enabled + removed) -> drop state so a future
        # re-resumption notifies again.
        try:
            STATE_FILE.unlink()
        except Exception:
            pass
    # durable record (always)
    LOG_JSON.parent.mkdir(parents=True, exist_ok=True)
    record = {"run_at": datetime.now(timezone.utc).isoformat(), "results": results, "alerts": alerts}
    LOG_JSON.write_text(json.dumps(record, indent=2, ensure_ascii=False))
    LOG_TXT.parent.mkdir(parents=True, exist_ok=True)
    with LOG_TXT.open("a") as fh:
        line = "; ".join(f"{c}={results[c]['status']}" for c in sorted(results))
        fh.write(f"{record['run_at']} reprobe: {line}" + (f" | ALERTS={len(alerts)}" if alerts else "") + "\n")
    return {"results": results, "alerts": alerts, "dispatched": dispatched}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Re-probe KNOWN_DEAD_SOURCES for resumption.")
    p.add_argument("--no-ntfy", action="store_true", help="do not post to ntfy")
    p.add_argument("--probe-sg", action="store_true", help="also probe sg_publikationen (ES-feed revival)")
    args = p.parse_args(argv)
    out = run(probe_sg=args.probe_sg, send_ntfy=not args.no_ntfy)
    for c, r in sorted(out["results"].items()):
        print(f"  {c}: {r['status']} (stubs={r['stub_count']} portal_count={r['portal_count']} {r['elapsed_s']}s)")
    for a in out["alerts"]:
        print("  ALERT:", a)
    return 0  # always exit 0 — ntfy is the signal


if __name__ == "__main__":
    sys.exit(main())
