"""scripts/source_status.py: one derived status per source for /coverage/.

Offline: dicts and tmp files only. The registry checks run against the
committed docs/ data, so a new court code or a new scraper that is not in
docs/sources/registry.json fails here instead of vanishing from the page.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import source_status as ss  # noqa: E402

TODAY = date(2026, 11, 8)
REGISTRY = json.loads((REPO / "docs" / "sources" / "registry.json").read_text())
FACTS = ss.load_code_facts(REPO)
LANGS = ("de", "fr", "it", "rm", "en")


# ─── schedules from code ────────────────────────────────────────────────────

@pytest.mark.parametrize("spec,expected", [
    ("*-*-* 01:00:00 UTC", {"cadence": "daily", "time": "01:00"}),
    ("Sat 21:30 UTC # comment", {"cadence": "weekly", "day": "Sat", "time": "21:30"}),
    ("Mon *-*-* 13:00:00 UTC", {"cadence": "weekly", "day": "Mon", "time": "13:00"}),
    ("*-*-02 04:00:00 UTC", {"cadence": "monthly", "day": 2, "time": "04:00"}),
    ("Mon..Fri *-*-* 05..16:00:00 UTC", {"cadence": "hourly", "from": "05", "to": "16", "days": "Mon-Fri"}),
    ("weekly", {"raw": "weekly"}),
])
def test_parse_oncalendar(spec, expected):
    assert ss.parse_oncalendar(spec) == expected


def _kinds(key):
    return [r["kind"] for r in FACTS["schedule"][key]]


def test_schedules_are_read_from_the_code_that_decides_them():
    assert _kinds("bger") == ["nightly", "late", "poller"]
    assert _kinds("bvger") == ["nightly", "federal"]
    assert _kinds("ti_gerichte") == ["nightly", "late"]
    assert _kinds("ecthr") == ["own_timer"]
    assert FACTS["schedule"]["ecthr"][0]["when"] == [{"cadence": "daily", "time": "14:00"}]
    # Registered but in SKIP_BY_DEFAULT and without an own timer: never runs.
    assert _kinds("be_steuerrekurs") == []
    assert FACTS["schedule"]["vd_gerichte"][0]["when"] == [{"cadence": "daily", "time": "01:00"}]


# ─── health merge ───────────────────────────────────────────────────────────

def test_latest_real_run_wins_and_skips_never_shadow_a_run():
    nightly = {"run_at": "2026-11-08T01:52:07+00:00", "run_source": "cron", "scrapers": {
        "bger": {"success": True, "new_count": 12},
        "ti_gerichte": {"success": False, "error": "tunnel down", "new_count": 0}}}
    late = {"run_at": "2026-11-08T10:52:05+00:00", "run_source": "manual", "scrapers": {
        "bger": {"success": True, "new_count": 0, "note": "skipped: today's 01:52 UTC nightly run succeeded"},
        "ti_gerichte": {"success": True, "new_count": 3}}}
    h = ss.merge_health([nightly, None, late])
    assert h["bger"]["new_count"] == 12 and h["bger"]["run_source"] == "cron"
    assert h["ti_gerichte"]["new_count"] == 3 and ss.run_ok(h["ti_gerichte"]) is True


# ─── one court, every status ────────────────────────────────────────────────

POLICY = {"expected_stale": {"mkg_like": "no new volumes"}, "lag_days": {"weko_like": 300}}


def _entry(code, reg, *, by_year=None, latest=None, added=None, rate=None, health=None,
           facts=None, notes=None):
    cov = {"court": code, "total": sum((by_year or {}).values()), "by_year": by_year or {}}
    st = {"latest": latest, "last_scraped": added}
    f = facts or {"scrapers": [code, "parent"], "schedule": {
        code: [{"kind": "nightly", "unit": "opencaselaw-scrape", "when": []}],
        "parent": [{"kind": "nightly", "unit": "opencaselaw-scrape", "when": []}]}}
    return ss.court_entry(code, reg, cov, st, rate, f, health or {}, {}, POLICY, notes or {},
                          [code, "x_a", "x_b"], TODAY)


BUSY = {"2024": 240, "2025": 250, "2026": 200}  # ~245/yr -> a decision every ~1.5 days


def test_fresh_when_new_decisions_keep_arriving():
    e = _entry("c", {"name": "C"}, by_year=BUSY, latest="2026-11-01", added="2026-11-07",
               rate={"status": "OK"}, health={"c": {"success": True, "run_at": "2026-11-08T01:00:00+00:00"}})
    assert e["status"] == "fresh" and e["why"] is None
    assert e["cadence"]["every_days"] == 1 and e["cadence"]["stale_after_days"] == ss.MIN_WINDOW_NEWEST
    assert e["intake"]["live"] is True and e["intake"]["feeders"][0]["last_run"]["ok"] is True


def test_silent_success_is_stale_the_ai_gerichte_case():
    # ~22/yr, every run exits 0 with new_count 0, newest decision 180 days old.
    e = _entry("c", {}, by_year={"2024": 22, "2025": 23, "2026": 9}, latest="2026-05-12",
               added="2026-08-18", rate={"status": "LOW"},
               health={"c": {"success": True, "new_count": 0, "run_at": "2026-11-08T01:00:00+00:00"}})
    assert (e["status"], e["why"]) == ("stale", "no_new_decision")
    assert e["days_since_newest"] == 180 and e["cadence"]["stale_after_days"] == 65


def test_a_recent_row_keeps_a_lagging_court_fresh():
    # Newest decision 51 days old (publication lag) but rows were added 3 days ago.
    e = _entry("c", {}, by_year=BUSY, latest="2026-09-18", added="2026-11-05", rate={"status": "OK"})
    assert e["status"] == "fresh"


def test_rate_detector_stale_wins_even_with_recent_dates():
    e = _entry("c", {}, by_year=BUSY, latest="2026-11-01", added="2026-11-07",
               rate={"status": "STALE", "reason": "3 of 69 expected"})
    assert (e["status"], e["why"]) == ("stale", "rate_stale")
    assert e["rate_check"] == {"status": "STALE", "reason": "3 of 69 expected"}


def test_failed_latest_run_is_failing():
    e = _entry("c", {}, by_year=BUSY, latest="2026-11-01", added="2026-11-07",
               health={"c": {"success": False, "error": "HTTP 503", "run_at": "2026-11-08T01:00:00+00:00"}})
    assert (e["status"], e["why"]) == ("failing", "run_failed")
    assert e["intake"]["feeders"][0]["last_run"]["error"] == "HTTP 503"


def test_quiet_for_by_design_silence_and_for_lagging_series():
    assert _entry("mkg_like", {}, by_year={"2024": 9}, latest="2026-06-25",
                  rate={"status": "EXPECTED", "reason": "no new volumes"})["status"] == "quiet"
    e = _entry("weko_like", {}, by_year={"2024": 25, "2025": 27}, latest="2025-10-21",
               added="2026-09-08", rate={"status": "WAIT"})
    assert (e["status"], e["why"]) == ("quiet", "publication_lag")
    assert e["cadence"]["lag_days"] == 300


def test_retired_feed_without_scraper_is_no_intake_the_vd_cdap_case():
    e = _entry("vd_like", {"feed": "entscheidsuche"}, by_year=BUSY, latest="2026-02-09",
               added="2026-03-10", rate={"status": "EXPECTED", "reason": "superseded at parity"},
               facts={"scrapers": [], "schedule": {}})
    assert (e["status"], e["why"]) == ("no_intake", "feed_retired")
    assert e["intake"]["live"] is False
    # The alert policy's (wrong) reason is not published for a no-intake code.
    assert e["note"] is None


def test_registered_but_unscheduled_scraper_is_no_intake():
    f = {"scrapers": ["c"], "schedule": {"c": []}}
    e = _entry("c", {"feed": "entscheidsuche"}, by_year=BUSY, latest="2025-12-16", facts=f)
    assert (e["status"], e["why"]) == ("no_intake", "scraper_not_scheduled")


def test_superseded_by_prefix_and_closed():
    e = _entry("x_", {"fed_by": [], "feed": "entscheidsuche", "superseded_by": {"prefix": "x_"}},
               by_year={"2025": 3})
    assert e["status"] == "superseded" and e["superseded_by"] == ["x_a", "x_b"]
    e = _entry("c", {"ended": "2011-01-01"}, by_year={"2010": 50}, latest="2012-06-25")
    assert (e["status"], e["why"]) == ("closed", "ended")


def test_sub_court_code_takes_its_parent_scraper_run():
    e = _entry("c", {"fed_by": ["parent"]}, by_year=BUSY, latest="2026-11-01", added="2026-11-07",
               health={"parent": {"success": True, "portal_count": 900, "our_count": 899, "gap": 1,
                                  "run_at": "2026-11-08T01:00:00+00:00"}})
    f = e["intake"]["feeders"]
    assert [x["scraper"] for x in f] == ["parent"]
    assert f[0]["last_run"]["portal_count"] == 900 and f[0]["last_run"]["gap"] == 1


def test_future_dates_are_not_a_newest_decision():
    e = _entry("c", {}, by_year=BUSY, latest="2027-07-27", added="2026-11-07")
    assert e["newest_decision"] is None


def test_own_timer_scraper_reads_its_systemd_unit():
    f = {"scrapers": ["ecthr"], "schedule": {"ecthr": [{"kind": "own_timer", "unit": "opencaselaw-ecthr", "when": []}]}}
    units = {"opencaselaw-ecthr": {"result": "success", "finished": "2026-11-07T14:15:42+00:00"}}
    e = ss.court_entry("ecthr_chamber", {"fed_by": ["ecthr"]}, {"by_year": BUSY, "total": 690},
                       {"latest": "2026-11-01", "last_scraped": "2026-11-07"}, None, f, {}, units,
                       POLICY, {}, [], TODAY)
    assert e["intake"]["feeders"][0]["last_run"]["at"] == "2026-11-07T14:15:42+00:00"
    assert e["status"] == "fresh"


# ─── the registry against the committed corpus data ─────────────────────────

def _corpus_codes():
    stats = json.loads((REPO / "docs" / "stats.json").read_text())
    cov = json.loads((REPO / "docs" / "coverage.json").read_text())
    return {r["court"] for r in stats.get("by_court", [])} | {r["court"] for r in cov.get("courts", [])}


def test_every_court_code_in_the_corpus_is_in_the_registry():
    missing = sorted(_corpus_codes() - set(REGISTRY["courts"]))
    assert not missing, f"add to docs/sources/registry.json: {missing}"


def test_every_running_scraper_feeds_a_registered_code():
    fed = set()
    for code, e in REGISTRY["courts"].items():
        fed.update(e["fed_by"] if "fed_by" in e else ([code] if code in FACTS["scrapers"] else []))
    running = {k for k, runs in FACTS["schedule"].items() if runs}
    assert not sorted(running - fed), "scraper runs but feeds no registry code"
    unknown = sorted(f for e in REGISTRY["courts"].values() for f in e.get("fed_by", [])
                     if f not in FACTS["scrapers"])
    assert not unknown, f"fed_by names unregistered scrapers: {unknown}"


def test_registry_entries_are_well_formed():
    groups = {"federal", "authority", "international", "cantonal"}
    for code, e in REGISTRY["courts"].items():
        assert e["name"] and e["group"] in groups, code
        assert e["licence"] in REGISTRY["licences"], code
        assert e["portal"] is None or e["portal"].startswith("https://"), code
        assert (e["group"] == "cantonal") == bool(e.get("canton")), code
        assert e.get("feed") in (None, "entscheidsuche", "import"), code
        assert "status" not in e and "note" not in e, f"{code}: the registry holds facts, not statuses"
    for key, text in REGISTRY["licences"].items():
        assert set(text) == set(LANGS) and all(text.values()), key
    for c in REGISTRY["collections"]:
        assert c["licence"] in REGISTRY["licences"], c["id"]


def test_build_lists_every_code_exactly_once_and_surfaces_unknown_codes():
    cov = {"generated_at": "2026-11-08T11:55:30+00:00", "courts": [
        {"court": "bger", "total": 10, "by_year": {"2025": 5, "2026": 5}},
        {"court": "zz_new_court", "total": 3, "by_year": {"2026": 3}}]}
    stats = {"by_court": [
        {"court": "bger", "canton": "CH", "count": 10, "latest": "2026-10-28", "last_scraped": "2026-11-06T11:02:52+00:00"},
        {"court": "zz_new_court", "canton": "ZZ", "count": 3, "latest": "2026-11-01", "last_scraped": "2026-11-02"}]}
    p = ss.build(REGISTRY, cov, stats, [], {"expected_stale": {}, "lag_days": {}}, {}, FACTS, {}, {},
                 REPO / "systemd", TODAY, now_iso="2026-11-08T12:00:00+00:00")
    codes = [c["code"] for c in p["courts"]]
    assert codes == ["bger", "zz_new_court"]
    assert p["summary"]["unmapped"] == ["zz_new_court"]
    assert "vd_omni" in p["summary"]["registered_without_rows"]
    assert p["summary"]["courts"] == len(codes)
    assert sum(p["summary"]["by_status"].values()) == len(codes)
    assert p["schema"] == ss.SCHEMA and p["licences"] == REGISTRY["licences"]


# ─── collections ────────────────────────────────────────────────────────────

def _coll_registry(**kw):
    base = {"id": "federal_laws", "kind": "laws", "name": "Bundesrecht", "licence": "ch_enactments",
            "count_from_stats": "federal_laws", "units": ["opencaselaw-fedlex"], "expected_every_days": 31}
    base.update(kw)
    return {"collections": [base]}


@pytest.mark.parametrize("unit,expected", [
    ({"result": "success", "finished": "2026-11-02T06:27:34+00:00"}, "fresh"),
    ({"result": "success", "finished": "2026-08-02T06:27:34+00:00"}, "stale"),
    ({"result": "exit-code", "finished": "2026-11-02T06:27:34+00:00"}, "failing"),
    ({}, "unknown"),
])
def test_collection_status_follows_its_unit(unit, expected):
    rows = ss.collections(_coll_registry(), {"corpus": {"federal_laws": 5546}}, {},
                          {"opencaselaw-fedlex": unit} if unit else {}, REPO / "systemd", TODAY)
    assert rows[0]["status"] == expected and rows[0]["count"] == 5546
    assert rows[0]["runs"][0]["when"] == [{"cadence": "monthly", "day": 2, "time": "04:00"}]


def test_collection_falls_back_to_the_db_swap_time(tmp_path):
    (tmp_path / "legal_scholarship.db").write_bytes(b"")
    reg = _coll_registry(id="scholarship", units=["opencaselaw-publish"], db="legal_scholarship.db",
                         count_from_stats="scholarship_publications", expected_every_days=7)
    rows = ss.collections(reg, {"corpus": {"scholarship_publications": 3}}, {},
                          {"opencaselaw-publish": {"result": "success", "finished": None, "active": "activating"}},
                          REPO / "systemd", date.today(), tmp_path)
    assert rows[0]["last_success"] == date.today().isoformat() and rows[0]["status"] == "fresh"


def test_collection_facts_read_tiny_tables_only(tmp_path):
    c = sqlite3.connect(tmp_path / "practice.db")
    c.executescript("""
        CREATE TABLE sources (source TEXT, issuing_authority TEXT, doc_count INT, last_scraped_at TEXT);
        CREATE TABLE practice (doc_id TEXT, source TEXT, date TEXT, body_text TEXT);
        INSERT INTO sources VALUES ('bsv', 'BSV', 2, '2026-11-07T07:40:48+00:00');
        INSERT INTO practice VALUES ('a', 'bsv', '1944-01-01', ''), ('b', 'bsv', '2026-03-01', '');
    """)
    c.commit()
    c.close()
    f = ss.collect_collection_facts(tmp_path)
    assert f["practice"]["count"] == 2
    assert f["practice"]["sources"][0] == {"source": "bsv", "authority": "BSV", "count": 2,
                                           "last_scraped": "2026-11-07T07:40:48+00:00",
                                           "first_year": "1944", "last_year": "2026"}
    assert "materialien" not in f  # absent DB -> absent, never an exception


# ─── wiring ─────────────────────────────────────────────────────────────────

def test_step_5g_writes_status_json_and_survives_its_failure(tmp_path, monkeypatch):
    import generate_coverage as gc
    db = tmp_path / "decisions.db"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE decisions (court TEXT, decision_date TEXT)")
    c.executemany("INSERT INTO decisions VALUES (?, ?)", [("bger", "2026-10-28"), ("bger", "2025-03-01")])
    c.commit()
    c.close()
    (tmp_path / "stats.json").write_text(json.dumps({"by_court": [
        {"court": "bger", "canton": "CH", "count": 2, "latest": "2026-10-28", "last_scraped": "2026-11-06"}]}))
    monkeypatch.setattr(ss, "read_units", lambda units: {})
    args = ["generate_coverage.py", "--db", str(db), "--health", str(tmp_path / "scraper_health.json"),
            "--notes", str(tmp_path / "notes.json"), "--output", str(tmp_path / "coverage.json"),
            "--stats", str(tmp_path / "stats.json"), "--status-output", str(tmp_path / "status.json")]
    monkeypatch.setattr(sys, "argv", args)
    assert gc.main() == 0
    status = json.loads((tmp_path / "status.json").read_text())
    assert any(x["code"] == "bger" and x["count"] == 2 for x in status["courts"])
    assert json.loads((tmp_path / "coverage.json").read_text())["totals"]["decisions"] == 2

    (tmp_path / "status.json").unlink()
    monkeypatch.setattr(ss, "generate", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert gc.main() == 0
    assert (tmp_path / "coverage.json").exists() and not (tmp_path / "status.json").exists()


def test_step_6_commits_coverage_and_status():
    src = (REPO / "publish.py").read_text()
    assert '"docs/coverage.json", "docs/sources/status.json"]' in src
