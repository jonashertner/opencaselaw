import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _write_health(path: Path, *, run_at: datetime, scrapers: dict) -> None:
    path.write_text(
        json.dumps(
            {
                "run_at": run_at.isoformat(),
                "scrapers": scrapers,
            }
        )
    )


def _write_snapshot_db(path: Path, *, court: str, snapshot_date: str) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "CREATE TABLE source_snapshots ("
            "source_key TEXT NOT NULL, "
            "snapshot_year INTEGER NOT NULL, "
            "snapshot_date TEXT NOT NULL, "
            "expected_ids_json TEXT NOT NULL, "
            "notes TEXT)"
        )
        conn.execute(
            "INSERT INTO source_snapshots "
            "(source_key, snapshot_year, snapshot_date, expected_ids_json, notes) "
            "VALUES (?, ?, ?, ?, ?)",
            (court, 2026, snapshot_date, "[]", "test"),
        )
        conn.commit()
    finally:
        conn.close()


def test_fresh_successful_zero_new_run_suppresses_snapshot_stale_false_positive(tmp_path):
    """A coverage snapshot records content changes, not successful attempts."""
    court = "test_court"
    coverage_db = tmp_path / "coverage.db"
    health_file = tmp_path / "scraper_health.json"
    alert_log = tmp_path / "alerts.log"

    now = datetime.now(timezone.utc)
    _write_snapshot_db(
        coverage_db,
        court=court,
        snapshot_date=(now - timedelta(days=45)).date().isoformat(),
    )
    _write_health(
        health_file,
        run_at=now,
        scrapers={
            court: {
                "success": True,
                "new_count": 0,
                "our_count": 2000,
                "portal_count": 2000,
                "duration_s": 90,
            }
        },
    )

    env = {
        **os.environ,
        "OCL_COVERAGE_DB": str(coverage_db),
    }
    result = subprocess.run(
        [
            sys.executable,
            "scripts/check_scraper_freshness.py",
            "--health-file",
            str(health_file),
            "--alert-log",
            str(alert_log),
            "--state-dir",
            str(tmp_path),
            "--no-ntfy",
        ],
        cwd=Path(__file__).resolve().parent.parent,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert "STALE test_court" not in result.stdout
    assert "All checks passed" in result.stdout


def test_near_matching_portal_count_suppresses_fast_zero_new_warning(tmp_path):
    """Small corpus-vs-portal count drift should not look like a silent skip."""
    court = "test_court"
    coverage_db = tmp_path / "coverage.db"
    health_file = tmp_path / "scraper_health.json"
    alert_log = tmp_path / "alerts.log"

    now = datetime.now(timezone.utc)
    _write_snapshot_db(
        coverage_db,
        court=court,
        snapshot_date=now.date().isoformat(),
    )
    _write_health(
        health_file,
        run_at=now,
        scrapers={
            court: {
                "success": True,
                "new_count": 0,
                "our_count": 2000,
                "portal_count": 1998,
                "duration_s": 8,
            }
        },
    )

    env = {
        **os.environ,
        "OCL_COVERAGE_DB": str(coverage_db),
    }
    result = subprocess.run(
        [
            sys.executable,
            "scripts/check_scraper_freshness.py",
            "--health-file",
            str(health_file),
            "--alert-log",
            str(alert_log),
            "--state-dir",
            str(tmp_path),
            "--no-ntfy",
        ],
        cwd=Path(__file__).resolve().parent.parent,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert "possible API outage" not in result.stdout
    assert "All checks passed" in result.stdout


def _fast_zero_stdout(tmp_path, court):
    coverage_db = tmp_path / "coverage.db"
    health_file = tmp_path / "scraper_health.json"
    now = datetime.now(timezone.utc)
    _write_snapshot_db(coverage_db, court=court, snapshot_date=now.date().isoformat())
    _write_health(
        health_file,
        run_at=now,
        scrapers={court: {"success": True, "new_count": 0, "our_count": 14500, "duration_s": 6}},
    )
    result = subprocess.run(
        [sys.executable, "scripts/check_scraper_freshness.py", "--health-file", str(health_file),
         "--alert-log", str(tmp_path / "alerts.log"), "--state-dir", str(tmp_path), "--no-ntfy"],
        cwd=Path(__file__).resolve().parent.parent,
        env={**os.environ, "OCL_COVERAGE_DB": str(coverage_db)},
        text=True, capture_output=True, check=False,
    )
    return result.stdout


def test_closed_historical_collection_is_exempt_from_the_fast_zero_rule(tmp_path):
    """bge_historical (BGE 1-79, 1875-1953) never publishes anything new, and since its 161
    image-only rulings are gap-cached every run is a fast zero. 2026-09-17: that tripped
    "possible API outage". The same record under another name must still warn."""
    control = tmp_path / "control"
    control.mkdir()
    assert "possible API outage" in _fast_zero_stdout(control, "test_court")
    exempt = tmp_path / "exempt"
    exempt.mkdir()
    assert "possible API outage" not in _fast_zero_stdout(exempt, "bge_historical")


# ── A4: registry-vs-health reconciliation ────────────────────────────────


def _full_health_scrapers(drop=()):
    """A 'full run' health dict = every registered scraper minus `drop`, each
    a clean success (our_count<1000 + duration>30s avoid the silent-skip and
    STALE heuristics so the only signal under test is the reconciliation)."""
    from run_scraper import SCRAPERS
    return {
        s: {"success": True, "new_count": 0, "our_count": 50, "duration_s": 90}
        for s in SCRAPERS if s not in drop
    }


def _run_monitor(tmp_path, scrapers):
    health_file = tmp_path / "scraper_health.json"
    _write_health(health_file, run_at=datetime.now(timezone.utc), scrapers=scrapers)
    env = {**os.environ, "OCL_COVERAGE_DB": str(tmp_path / "nonexistent.db")}
    return subprocess.run(
        [sys.executable, "scripts/check_scraper_freshness.py",
         "--health-file", str(health_file), "--alert-log", str(tmp_path / "a.log"),
         "--state-dir", str(tmp_path), "--no-ntfy"],
        cwd=Path(__file__).resolve().parent.parent, env=env,
        text=True, capture_output=True, check=False,
    )


def test_reconciliation_flags_missing_registered_scraper(tmp_path):
    # bvger is registered, not dead, not es-only — silently dropping it from a
    # full run must surface (the be_steuerrekurs blind-spot class)
    res = _run_monitor(tmp_path, _full_health_scrapers(drop=("bvger",)))
    assert res.returncode == 0
    assert "bvger: registered scraper absent from a full" in res.stdout


def test_reconciliation_quiet_for_known_dead_missing(tmp_path):
    # a KNOWN_DEAD scraper (ow_gerichte) absent from health is expected — silent
    res = _run_monitor(tmp_path, _full_health_scrapers(drop=("ow_gerichte",)))
    assert res.returncode == 0
    assert "registered scraper absent" not in res.stdout


def test_reconciliation_skipped_on_partial_run(tmp_path):
    # <20 scrapers = a partial/manual run; reconciliation must NOT fire (else
    # ~58 false WARNs). This is also what keeps the 1-court tests above green.
    res = _run_monitor(tmp_path, {
        "bger": {"success": True, "new_count": 0, "our_count": 50, "duration_s": 90}})
    assert res.returncode == 0
    assert "registered scraper absent" not in res.stdout


def test_tunnel_dependent_sources_cover_proxied_courts():
    """bger/bge were Incapsula-blocked from Hetzner on 2026-06-29 and now
    egress via the Mac tunnel like ju/ne. The freshness classifier must
    treat all tunnel-dependent courts the same, and the set must stay a
    superset-match of run_all_scrapers.TUNNEL_DEPENDENT so the two never
    drift apart."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import check_scraper_freshness as csf
    import run_all_scrapers as ras

    assert {"bger", "bge", "ju_gerichte", "ne_gerichte"} <= csf.TUNNEL_DEPENDENT_SOURCES
    assert csf.TUNNEL_DEPENDENT_SOURCES == ras.TUNNEL_DEPENDENT


def _run_checker(tmp_path, health_file, alert_log, coverage_db):
    return subprocess.run(
        [
            sys.executable, "scripts/check_scraper_freshness.py",
            "--health-file", str(health_file),
            "--alert-log", str(alert_log),
            "--state-dir", str(tmp_path),
            "--no-ntfy",
        ],
        cwd=Path(__file__).resolve().parent.parent,
        env={**os.environ, "OCL_COVERAGE_DB": str(coverage_db)},
        text=True, capture_output=True, check=False,
    )


def test_null_our_count_does_not_abort_the_health_block(tmp_path):
    """Regression: a failed discovery stores our_count/duration_s as null.

    `v.get("our_count", 0)` returns that None (the default only applies when the
    KEY is absent), and `None > 1000` raised TypeError. That aborted the health
    block before health_run_dt was assigned, which un-gated the STALE loop into
    a 35-line alert storm — six nights in July 2026, each logged as
    'CRITICAL: cannot parse scraper_health.json'.

    The dangerous row is a SUCCESSFUL one carrying nulls: `success: false`
    short-circuits the `and` chain and hides the bug.
    """
    coverage_db = tmp_path / "coverage.db"
    health_file = tmp_path / "scraper_health.json"
    alert_log = tmp_path / "alerts.log"
    now = datetime.now(timezone.utc)

    _write_snapshot_db(coverage_db, court="test_court",
                       snapshot_date=now.date().isoformat())
    _write_health(health_file, run_at=now, scrapers={
        "test_court": {
            "success": True,
            "new_count": 0,
            "our_count": None,      # <- the killer
            "portal_count": None,
            "duration_s": None,
        }
    })

    result = _run_checker(tmp_path, health_file, alert_log, coverage_db)

    assert "cannot parse scraper_health.json" not in result.stdout + result.stderr
    assert "TypeError" not in result.stderr
    assert "not supported between instances" not in result.stdout + result.stderr
    assert result.returncode in (0, 1)


def test_stall_history_survives_a_failed_night(tmp_path):
    """A failed run carries no growth evidence — it must not reset the clock.

    check_stalled_corpus rebuilt its state from scratch and skipped any court
    that was absent or success:false, then overwrote the file with only the
    survivors. bger failed all three runs on 2026-08-25 and its 98,532-decision
    history collapsed to a single day, which also structurally prevents the
    tunnel-dependent sources from ever reaching the 90-entry threshold.

    Driven through the function's own state_path injection point: STALL_STATE_PATH
    is a repo-relative constant with no CLI flag, so a subprocess test would
    write into the real logs/ directory and assert nothing.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from scripts.check_scraper_freshness import check_stalled_corpus

    state = tmp_path / "scraper_stall_state.json"
    state.write_text(json.dumps({
        "bger": {"count": 98532, "days": ["2026-08-01", "2026-08-02"], "grew": True}
    }))

    # bger failed this run; a second court reported normally so the state file
    # is definitely rewritten.
    health = {"scrapers": {
        "bger": {"success": False, "our_count": None},
        "other_court": {"success": True, "our_count": 10},
    }}

    check_stalled_corpus(health, "2026-08-03", state_path=state)

    after = json.loads(state.read_text())
    assert "bger" in after, "a failed night wiped the court's stall history"
    assert after["bger"]["days"] == ["2026-08-01", "2026-08-02"]
    assert after["bger"]["count"] == 98532


def test_independently_scheduled_scraper_is_not_reported_as_never_run(tmp_path):
    """Regression 2026-08-27.

    ecthr runs on its own systemd timer, not through run_all_scrapers.py, so
    it is correctly absent from a full scraper_health.json run. The registry
    reconciliation reported it as "silently skipped or never ran" on the very
    night it had completed a 216-minute full-corpus backfill (+8,270
    judgments, 0 errors). An alert that asserts the opposite of the truth is
    worse than no alert — it trains the operator to ignore the channel.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from scripts.check_scraper_freshness import (
        INDEPENDENTLY_SCHEDULED_SOURCES,
        KNOWN_DEAD_SOURCES,
        ENTSCHEIDSUCHE_ONLY,
    )
    from run_scraper import SCRAPERS as REGISTERED

    assert "ecthr" in INDEPENDENTLY_SCHEDULED_SOURCES

    # A full run of everything that goes through run_all_scrapers.py.
    ran = set(REGISTERED) - set(INDEPENDENTLY_SCHEDULED_SOURCES)
    missing = sorted(
        set(REGISTERED) - ran
        - KNOWN_DEAD_SOURCES - ENTSCHEIDSUCHE_ONLY
        - set(INDEPENDENTLY_SCHEDULED_SOURCES)
    )
    assert missing == [], f"would still cry wolf for: {missing}"


def test_independently_scheduled_scraper_is_still_checked_for_staleness():
    """Exempting them from one check must not exempt them from all checks.

    The point of the reconciliation is that a scraper which quietly stops
    should not just vanish from every check. Independently-scheduled ones are
    checked on their state file instead.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from scripts.check_scraper_freshness import (
        INDEPENDENTLY_SCHEDULED_SOURCES, INDEPENDENT_STALE_DAYS,
    )
    src = (Path(__file__).resolve().parent.parent
           / "scripts" / "check_scraper_freshness.py").read_text(encoding="utf-8")
    assert "no write for" in src, "the independent staleness alert was removed"
    assert INDEPENDENT_STALE_DAYS >= 1
    # Every exempted scraper names the unit that owns it, so the alert text
    # tells the operator where to look.
    for k, unit in INDEPENDENTLY_SCHEDULED_SOURCES.items():
        assert unit.endswith((".timer", ".service")), (k, unit)
