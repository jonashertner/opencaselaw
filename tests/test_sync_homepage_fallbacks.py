"""The homepage's static numbers must be derivable, not remembered.

Found 2026-08-22: docs/index.html carried hardcoded fallbacks two months
stale (991'298 rendered against a live 1'054'206) because nothing
regenerated them, while stats.json beside them rebuilt nightly. Non-JS
consumers — crawlers, ~76 % of traffic — only ever saw the stale values.

These tests pin scripts/sync_homepage_fallbacks.py's contract: it rewrites
exactly the hydration ids, is idempotent, refuses implausible input, and
fails loudly when the markup drifts from its patterns.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "sync_homepage_fallbacks.py"

FIXTURE_HTML = """<html><body>
<p class="bignum tnum" id="bignum">991&#8217;298</p>
<span id="trust-delta-seg"><b id="trust-delta">198</b> · </span><span id="trust-date">2026-06-21</span>
<div class="fact-n tnum" id="f-decisions">991&#8217;298 <span class="u">decisions</span></div>
<div class="fact-n tnum" id="f-laws">21&#8217;108 <span class="u">laws</span></div>
<div class="fact-n tnum" id="f-cites">8.9M <span class="u">citations</span></div>
<div class="fact-n tnum" id="f-echr">8&#8217;900 <span class="u">ECtHR judgments</span></div>
<p class="cov-foot"><span id="cov-courts">109</span> courts</p>
<span class="reg-n tnum" id="f-scholarship">43&#8217;000</span>
<div class="d-n pos tnum">+<span id="d-today">198</span></div>
<p class="stamp">As of <span id="stamp">2026-06-21</span></p>
</body></html>"""

FIXTURE_STATS = {
    "total": 1054206,
    "court_count": 118,
    "generated_at": "2026-08-22T09:34:20+00:00",
    "delta": {"total": 139},
    "corpus": {"federal_laws": 5528, "cantonal_laws": 15608,
               "citation_edges": 9836225,
               "scholarship_publications": 44491},
    # by_court splits each ECtHR court by canton: 'CE' for the
    # Council-of-Europe-wide rows, 'CH' for Swiss-respondent. Both count.
    "by_court": [
        {"court": "bger", "canton": "CH", "count": 192794},
        {"court": "ecthr_chamber", "canton": "CE", "count": 7591},
        {"court": "ecthr_chamber", "canton": "CH", "count": 165},
        {"court": "ecthr_grand_chamber", "canton": "CE", "count": 495},
        {"court": "ecthr_grand_chamber", "canton": "CH", "count": 19},
        {"court": "hudoc_ch", "canton": "CH", "count": 853},
        {"court": "bge_egmr", "canton": "CH", "count": 487},
    ],
}


def run(tmp_path: Path, *args: str, html=FIXTURE_HTML, stats=FIXTURE_STATS):
    index = tmp_path / "index.html"
    statsf = tmp_path / "stats.json"
    index.write_text(html, encoding="utf-8")
    statsf.write_text(json.dumps(stats), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--index", str(index),
         "--stats", str(statsf), *args],
        capture_output=True, text=True)
    return proc, index.read_text(encoding="utf-8")


def test_rewrites_every_stale_element(tmp_path):
    proc, out = run(tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert "1&#8217;054&#8217;206" in out          # bignum + f-decisions
    assert "21&#8217;136" in out                    # 5528 + 15608
    assert "9.8M" in out                            # citation edges
    assert '<span id="cov-courts">118</span>' in out
    assert '<b id="trust-delta">139</b>' in out
    assert '<span id="trust-date">2026-08-22</span>' in out
    # No stale survivors
    assert "991&#8217;298" not in out
    assert "2026-06-21" not in out
    assert "8.9M" not in out


def test_markup_around_numbers_survives(tmp_path):
    _, out = run(tmp_path)
    assert '<span class="u">decisions</span>' in out
    assert 'class="bignum tnum"' in out
    assert 'id="trust-delta-seg"' in out


def test_idempotent(tmp_path):
    _, first = run(tmp_path)
    proc2, second = run(tmp_path, html=first)
    assert "nothing written" in proc2.stdout or second == first


def test_check_mode_flags_stale_and_writes_nothing(tmp_path):
    proc, out = run(tmp_path, "--check")
    assert proc.returncode == 1
    assert "STALE" in proc.stdout
    assert out == FIXTURE_HTML                      # untouched


def test_check_mode_passes_when_current(tmp_path):
    _, synced = run(tmp_path)
    proc, _ = run(tmp_path, "--check", html=synced)
    assert proc.returncode == 0


def test_missing_element_fails_loudly(tmp_path):
    broken = FIXTURE_HTML.replace('id="cov-courts"', 'id="renamed"')
    proc, out = run(tmp_path, html=broken)
    assert proc.returncode == 1
    assert "cov-courts" in proc.stderr
    assert out == broken                            # nothing half-written


def test_implausible_stats_are_refused(tmp_path):
    bad = dict(FIXTURE_STATS, total=12)
    proc, out = run(tmp_path, stats=bad)
    assert proc.returncode != 0
    assert out == FIXTURE_HTML


# ── #f-echr ─────────────────────────────────────────────────────────────
# Summed from by_court, mirroring the hydration JS. Unlike every other
# value here it reads a filtered list rather than a stable top-level key,
# so it is the one element allowed to drop out rather than block the run.

def test_echr_total_is_summed_across_both_cantons(tmp_path):
    """7591+165+495+19+853+487 = 9610 — the CE/CH split must not be halved."""
    proc, out = run(tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert 'id="f-echr">9&#8217;610 <span' in out


def test_stale_echr_is_repaired(tmp_path):
    proc, out = run(tmp_path)
    assert "8&#8217;900" not in out          # the fixture's stale value
    assert "#f-echr" in proc.stdout


def test_missing_by_court_skips_echr_but_still_syncs_the_headline(tmp_path):
    """The 2026-08-22 drift is the one that must always get repaired.

    A renamed court code or an aggregation that stops emitting the ECtHR
    rows must not cost the decisions count its refresh.
    """
    stats = {k: v for k, v in FIXTURE_STATS.items() if k != "by_court"}
    proc, out = run(tmp_path, stats=stats)

    assert proc.returncode == 0, proc.stderr
    assert "implausible ECtHR total 0" in proc.stderr      # warned, not fatal
    assert 'id="f-decisions">1&#8217;054&#8217;206 <span' in out   # still repaired
    assert 'id="f-echr">8&#8217;900 <span' in out          # left untouched


def test_collapsed_echr_count_does_not_zero_the_page(tmp_path):
    stats = dict(FIXTURE_STATS, by_court=[{"court": "bger", "canton": "CH",
                                           "count": 192794}])
    proc, out = run(tmp_path, stats=stats)
    assert proc.returncode == 0
    assert 'id="f-echr">8&#8217;900 <span' in out


def _with_delta(n: int) -> dict:
    return {**FIXTURE_STATS, "delta": {"total": n}}


def test_new_today_carries_one_sign_never_plus_minus(tmp_path):
    """A clean-up night has a negative delta; the fixed "+" in the markup read "+-2409"."""
    proc, out = run(tmp_path, stats=_with_delta(-2409))
    assert proc.returncode == 0, proc.stderr
    assert '>−<span id="d-today">2409</span>' in out
    assert "+<span" not in out and "-2409" not in out.split('id="d-today"')[1][:20]

    proc, out = run(tmp_path, stats=_with_delta(229))
    assert '>+<span id="d-today">229</span>' in out

    proc, out = run(tmp_path, stats=_with_delta(0))
    assert 'tnum"><span id="d-today">0</span>' in out


def test_check_mode_sees_a_stale_sign(tmp_path):
    proc, out = run(tmp_path, stats=_with_delta(-5))
    synced = out
    proc, _ = run(tmp_path, "--check", html=synced, stats=_with_delta(-5))
    assert proc.returncode == 0, proc.stdout
    proc, _ = run(tmp_path, "--check", html=synced, stats=_with_delta(5))
    assert proc.returncode == 1
