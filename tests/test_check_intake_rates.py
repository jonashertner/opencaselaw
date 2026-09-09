"""check_intake_rates.py — per-court year-rate stale-intake detector.

Fixture coverage.json only; no network, no production DB. The fixture is
shaped like docs/coverage.json (schema coverage/v1) and models the real
cases that motivated the detector: ow_gerichte (silent since 2022),
be_steuerrekurs (dead since Feb 2026, noted), an entscheidsuche-only feed at
0 rows, bge (decision dates lag publication), and a by-design frozen archive.
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timezone
from itertools import pairwise
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO / "scripts"))

import check_intake_rates as cir

AS_OF = date(2026, 9, 9)          # day 252 of 365 -> 0.690 of the year elapsed


def _court(name, by_year, total=None, note=None):
    years = sorted(by_year)
    return {
        "court": name,
        "total": total if total is not None else sum(by_year.values()),
        "first_year": years[0] if years else None,
        "last_year": years[-1] if years else None,
        "undated": 0,
        "by_year": {str(k): v for k, v in by_year.items()},
        "portal_total": None, "our_count_at_check": None, "gap": None,
        "portal_checked_at": None, "note": note,
    }


def fixture_coverage() -> dict:
    return {
        "schema": "coverage/v1",
        "generated_at": "2026-09-09T12:05:14+00:00",
        "courts": [
            # healthy: 100 + 100 -> expected 69, have 70
            _court("bger", {2024: 100, 2025: 100, 2026: 70}, total=5000),
            # LOW: 40 of 69 expected -> 0.58
            _court("zh_obergericht", {2024: 100, 2025: 100, 2026: 40}, total=3000),
            # STALE by rate: entscheidsuche-only feed, 0 rows dated 2026
            _court("be_bvd", {2024: 165, 2025: 116, 2026: 0}, total=2094),
            # STALE by rate, noted outage (note must NOT exempt)
            _court("be_steuerrekurs", {2024: 67, 2025: 32, 2026: 0}, total=343,
                   note="Portal's backing database returns zero rows since Feb 2026."),
            # silent since 2022: no rows in either previous year (ow shape)
            _court("ow_gerichte", {2020: 83, 2022: 71}, total=3470,
                   note="Portal offline since Dec 2022; re-probed weekly."),
            # silent by design -> policy exemption
            _court("zh_kassationsgericht", {2010: 145, 2011: 108, 2012: 26}, total=1454),
            # lagging series: 28 dated this year, 104 published -> WAIT under lag
            _court("bge", {2024: 295, 2025: 259, 2026: 28}, total=50492),
            # too small to judge by rate
            _court("comcom", {2024: 1, 2025: 0, 2026: 0}, total=64),
            # tiny rate: expected 1.0 -> SMALL, not STALE
            _court("eschk", {2024: 2, 2025: 1, 2026: 0}, total=415),
            # new this year: rows now, none before
            _court("zh_arbeitsgericht", {2026: 14}, total=140),
        ],
    }


POLICY = {
    "expected_stale": {"zh_kassationsgericht": "court abolished 2012"},
    "lag_days": {"bge": 270},
}


def _status(rows, court):
    return next(r for r in rows if r.court == court).status


def test_rate_thresholds_and_special_cases():
    rows = cir.evaluate(fixture_coverage(), POLICY, AS_OF)
    assert _status(rows, "bger") == "OK"
    assert _status(rows, "zh_obergericht") == "LOW"
    assert _status(rows, "be_bvd") == "STALE"
    assert _status(rows, "be_steuerrekurs") == "STALE"      # note is context, not exemption
    assert _status(rows, "ow_gerichte") == "STALE"
    assert _status(rows, "zh_kassationsgericht") == "EXPECTED"
    assert _status(rows, "bge") == "WAIT"
    assert _status(rows, "comcom") == "SMALL"
    assert _status(rows, "eschk") == "SMALL"
    assert _status(rows, "zh_arbeitsgericht") == "NEW"


def test_expected_is_pro_rated_from_the_two_previous_years():
    rows = cir.evaluate(fixture_coverage(), POLICY, AS_OF)
    bger = next(r for r in rows if r.court == "bger")
    assert bger.expected == pytest.approx(100 * 252 / 365)
    assert bger.ratio == pytest.approx(70 / (100 * 252 / 365))
    low = next(r for r in rows if r.court == "zh_obergericht")
    assert 0.25 <= low.ratio < 0.6


def test_silent_court_reports_last_year_and_sorts_by_its_old_rate():
    rows = cir.evaluate(fixture_coverage(), POLICY, AS_OF)
    ow = next(r for r in rows if r.court == "ow_gerichte")
    assert ow.reason == "no rows since 2022"
    assert ow.ratio == 0.0
    # mean of the last two active years (83, 71) pro-rated over the window
    assert ow.expected == pytest.approx(77 * 252 / 365)
    assert ow.shortfall == pytest.approx(ow.expected)


def test_lag_days_shifts_the_window_and_judges_once_it_is_wide_enough():
    cov = fixture_coverage()
    # without the allowance bge would be STALE (28 of ~191 expected)
    rows = cir.evaluate(cov, {"expected_stale": {}, "lag_days": {}}, AS_OF)
    assert _status(rows, "bge") == "STALE"
    # with a 270-day lag the window at day 252 is 0 -> WAIT
    rows = cir.evaluate(cov, POLICY, AS_OF)
    assert _status(rows, "bge") == "WAIT"
    # by December the window is 95 days; expected = 277 * 95/365 ~= 72 -> 28 is LOW,
    # and the same court with a healthy Q4 count is OK
    dec = date(2026, 12, 31)
    rows = cir.evaluate(cov, POLICY, dec)
    bge = next(r for r in rows if r.court == "bge")
    assert bge.status == "LOW" and bge.expected == pytest.approx(277 * 95 / 365)
    cov["courts"][6]["by_year"]["2026"] = 90
    rows = cir.evaluate(cov, POLICY, dec)
    assert _status(rows, "bge") == "OK"


def test_sorted_by_status_then_ratio_then_shortfall():
    rows = cir.evaluate(fixture_coverage(), POLICY, AS_OF)
    order = [r.status for r in rows]
    assert order == sorted(order, key=lambda s: cir.STATUS_ORDER[s])
    stale = [r for r in rows if r.status == "STALE"]
    assert all(a.ratio <= b.ratio for a, b in pairwise(stale))
    zero = [r for r in stale if r.ratio == 0.0]
    assert [r.court for r in zero] == ["be_bvd", "ow_gerichte", "be_steuerrekurs"]  # by shortfall


def test_flagged_excludes_expected_and_low_never_fails():
    rows = cir.evaluate(fixture_coverage(), POLICY, AS_OF)
    stale, low = cir.flagged(rows)
    assert {r.court for r in stale} == {"be_bvd", "be_steuerrekurs", "ow_gerichte"}
    assert [r.court for r in low] == ["zh_obergericht"]
    line = cir.summary_line(stale, low)
    assert line.startswith("STALE intake (3): ")
    assert "ow_gerichte (no rows since 2022)" in line
    assert line.endswith("; LOW (1): zh_obergericht")


def test_main_exit_code_and_as_of_default(tmp_path, capsys):
    cov = tmp_path / "coverage.json"
    cov.write_text(json.dumps(fixture_coverage()))
    pol = tmp_path / "policy.json"
    pol.write_text(json.dumps({
        "expected_stale": {"zh_kassationsgericht": {"reason": "abolished 2012"}},
        "lag_days": {"bge": {"days": 270, "evidence": "..."}},
    }))
    rc = cir.main(["--coverage", str(cov), "--policy", str(pol)])
    out = capsys.readouterr().out
    assert rc == 1
    assert "(as of 2026-09-09, day 252 of 365" in out     # generated_at, not today
    assert "STALE    ow_gerichte" in out and "note: Portal offline" in out
    assert "EXPECTED zh_kassationsgericht" in out
    assert "WAIT     bge" in out

    # exempt the three dead feeds -> exit 0, table still lists them as EXPECTED
    pol.write_text(json.dumps({"expected_stale": {
        "zh_kassationsgericht": "x", "be_bvd": "x", "be_steuerrekurs": "x", "ow_gerichte": "x"},
        "lag_days": {"bge": 270}}))
    rc = cir.main(["--coverage", str(cov), "--policy", str(pol), "--only-flagged"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "EXPECTED" not in out and "LOW      zh_obergericht" in out


def test_main_json_output(tmp_path, capsys):
    cov = tmp_path / "coverage.json"
    cov.write_text(json.dumps(fixture_coverage()))
    rc = cir.main(["--coverage", str(cov), "--policy", str(tmp_path / "missing.json"),
                   "--json", "--as-of", "2026-06-30"])
    data = json.loads(capsys.readouterr().out)
    assert rc == 1 and data["as_of"] == "2026-06-30"
    by = {r["court"]: r for r in data["rows"]}
    assert by["ow_gerichte"]["status"] == "STALE"
    assert by["zh_kassationsgericht"]["status"] == "STALE"   # no policy file -> no exemption


def test_notify_is_opt_in_and_dedups_on_change(tmp_path, capsys):
    sent: list[tuple] = []

    def fake_send(title, message, priority="default", tags="warning", url=None):
        sent.append((title, message, priority, tags))
        return True

    rows = cir.evaluate(fixture_coverage(), POLICY, AS_OF)
    state = tmp_path / "state.json"
    now = datetime(2026, 9, 9, 12, tzinfo=timezone.utc)
    assert cir.maybe_notify(rows, now, state, sender=fake_send) == "posted"
    assert len(sent) == 1
    title, msg, prio, _tags = sent[0]
    assert title == "OpenCaseLaw intake: 3 STALE, 1 LOW" and prio == "high"
    assert "be_steuerrekurs (0 of 34 expected)" in msg
    assert json.loads(state.read_text())["stale"] == ["be_bvd", "ow_gerichte", "be_steuerrekurs"]

    # same set an hour later: silence
    later = datetime(2026, 9, 9, 13, tzinfo=timezone.utc)
    assert cir.maybe_notify(rows, later, state, sender=fake_send) == "unchanged"
    assert len(sent) == 1
    # re-nag after RENAG_HOURS
    assert cir.maybe_notify(rows, datetime(2026, 9, 10, 13, tzinfo=timezone.utc),
                            state, sender=fake_send) == "posted"
    assert len(sent) == 2
    # set changes (one court recovers): posted again
    cov = fixture_coverage()
    cov["courts"][2]["by_year"]["2026"] = 200      # be_bvd back
    rows2 = cir.evaluate(cov, POLICY, AS_OF)
    assert cir.maybe_notify(rows2, later, state, sender=fake_send) == "posted"
    assert len(sent) == 3
    # everything recovers: one all-clear, state removed, then silence
    policy_all = {"expected_stale": {k: "x" for k in
                  ("zh_kassationsgericht", "be_bvd", "be_steuerrekurs", "ow_gerichte")},
                  "lag_days": {"bge": 270}}
    rows3 = cir.evaluate(fixture_coverage(), policy_all, AS_OF)
    assert cir.maybe_notify(rows3, later, state, sender=fake_send) == "all-clear"
    assert sent[-1][3] == "white_check_mark" and not state.exists()
    assert cir.maybe_notify(rows3, later, state, sender=fake_send) == "ok"
    assert len(sent) == 4

    # main() without --notify never touches the sender
    cov_path = tmp_path / "c.json"
    cov_path.write_text(json.dumps(fixture_coverage()))
    cir.main(["--coverage", str(cov_path), "--policy", str(tmp_path / "none.json"),
              "--state-file", str(state)])
    capsys.readouterr()
    assert len(sent) == 4 and not state.exists()


def test_post_ntfy_failure_is_reported_not_raised(monkeypatch):
    def boom(*a, **k):
        raise OSError("no network in tests")
    monkeypatch.setattr(cir.urllib.request, "urlopen", boom)
    assert cir.post_ntfy("t", "m") is False


def test_repo_policy_file_parses_and_names_the_documented_courts():
    policy = cir.load_policy(cir.DEFAULT_POLICY)
    assert "zh_kassationsgericht" in policy["expected_stale"]
    assert "ch_vb" in policy["expected_stale"]
    assert policy["lag_days"]["bge"] >= 180
    # the two motivating outages must never be exempted silently
    assert "ow_gerichte" not in policy["expected_stale"]
    assert "be_steuerrekurs" not in policy["expected_stale"]
