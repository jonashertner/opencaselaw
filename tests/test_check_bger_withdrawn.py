"""check_bger_withdrawn.py — the periodic "withdrawn BGer decision" check.

Offline: golden pages captured from search.bger.ch on 2026-09-17
(tests/fixtures/bger_aza_*), a synthetic bger.jsonl tail, tmp ledger paths and
an injected fetcher. 6B_499/2026 (27.08.2026) is the withdrawn decision the
check was built for; 9C_251/2025 (22.07.2026) is a live one.

Every test runs under two autouse guards: requests.Session.request raises, so
an accidental live call fails loudly instead of hitting bger.ch, and
send_ntfy is neutralised, so no test can post to the operator topic
(tests/test_ntfy_topic_unification.py, 6c668bd0).
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO / "scripts"))

import check_bger_withdrawn as cbw  # noqa: E402

_REAL_SEND_NTFY = cbw.send_ntfy      # captured before the autouse guard replaces it

FIX = REPO / "tests" / "fixtures"
NOT_FOUND_PAGE = FIX / "bger_aza_not_found_6B_499_2026_20260917.html"
DOCUMENT_PAGE = FIX / "bger_aza_document_9C_251_2025_20260917.html"
SEARCH_ABSENT = FIX / "bger_aza_search_6B_499_2026_20260917.html"
SEARCH_LISTED = FIX / "bger_aza_search_9C_251_2025_20260917.html"

T0 = datetime(2026, 9, 17, 13, 30, tzinfo=timezone.utc)


def latin1(p: Path) -> str:
    return p.read_bytes().decode("iso-8859-1")


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    import requests

    def boom(self, *a, **k):
        raise AssertionError("live network call from an offline test")

    monkeypatch.setattr(requests.Session, "request", boom)
    monkeypatch.setattr(cbw, "send_ntfy", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("send_ntfy reached from a test")))
    yield


# ═══════════════════════════════════════════════════════════════════════════
# classifier
# ═══════════════════════════════════════════════════════════════════════════

def test_not_found_page_is_not_found_under_both_decodings():
    raw = NOT_FOUND_PAGE.read_bytes()
    for text in (raw.decode("iso-8859-1"), raw.decode("utf-8", errors="replace")):
        assert cbw.classify_document_page(text, "6B_499/2026") == cbw.NOT_FOUND
    # the site chrome is present and long: container non-empty is NOT "present"
    assert len(raw) > 20_000
    assert cbw.classify_document_page(raw.decode("iso-8859-1"), "6B_499/2026") != cbw.PRESENT


def test_live_document_page_is_present_only_for_its_own_docket():
    text = latin1(DOCUMENT_PAGE)
    assert cbw.classify_document_page(text, "9C_251/2025") == cbw.PRESENT
    assert cbw.classify_document_page(text, "6B_499/2026") == cbw.UNKNOWN


def test_service_error_blocked_and_short_pages():
    chrome = "<html><body>" + "menu " * 200
    assert cbw.classify_document_page(
        chrome + "Die Anfrage des Document Dienstes ist fehlgeschlagen.</body></html>",
        "1C_1/2026") == cbw.SERVICE_ERROR
    assert cbw.classify_document_page(
        "<html><script>_Incapsula_Resource</script></html>", "1C_1/2026") == cbw.BLOCKED
    assert cbw.classify_document_page("", "1C_1/2026") == cbw.BLOCKED
    assert cbw.classify_document_page("x" * 5000, "1C_1/2026",
                                      final_url="https://search.bger.ch/pow.php") == cbw.BLOCKED
    assert cbw.classify_document_page("<html><body>" + "y " * 3000, "1C_1/2026") == cbw.UNKNOWN


def test_not_found_wins_over_a_present_looking_container():
    text = latin1(DOCUMENT_PAGE) + "<p>Dieser AZA-Entscheid ist in elektronischer Form nicht verf&uuml;gbar.</p>"
    assert cbw.classify_document_page(text, "9C_251/2025") == cbw.NOT_FOUND
    titled = latin1(DOCUMENT_PAGE) + "<HTML><HEAD><TITLE>Dokument nicht gefunden</TITLE></HEAD></HTML>"
    assert cbw.classify_document_page(titled, "9C_251/2025") == cbw.NOT_FOUND


def test_a_judgment_that_merely_says_a_document_was_not_found_stays_present():
    text = latin1(DOCUMENT_PAGE).replace(
        "intimée,\xa0</div>", "intimée, Dokument nicht gefunden in den Akten. </div>")
    assert "Dokument nicht gefunden in den Akten" in text
    assert cbw.classify_document_page(text, "9C_251/2025") == cbw.PRESENT


# ═══════════════════════════════════════════════════════════════════════════
# urls
# ═══════════════════════════════════════════════════════════════════════════

def test_document_url_matches_the_withdrawn_decision_url():
    assert cbw.document_url(date(2026, 8, 27), "6B_499/2026") == (
        "https://search.bger.ch/ext/eurospider/live/de/php/aza/http/index.php"
        "?lang=de&type=show_document&highlight_docid=aza%3A%2F%2F27-08-2026-6B_499-2026")
    assert cbw.search_url("6B_499/2026").endswith(
        "type=simple_query&query_words=6B_499%2F2026&top_subcollection_aza=all")
    assert cbw.dated_decision_id("6B_499/2026", date(2026, 9, 3)) == "bger_6B_499_2026-D20260903"


# ═══════════════════════════════════════════════════════════════════════════
# second witness
# ═══════════════════════════════════════════════════════════════════════════

def test_search_hits_come_from_highlight_docid_not_the_hit_count():
    hits = cbw.parse_search_hits(latin1(SEARCH_ABSENT))
    assert hits and all(dk != "6B_499/2026" for _, dk in hits)   # 14 citing decisions
    assert (date(2026, 6, 9), "7B_763/2025") == hits[0]
    listed = cbw.parse_search_hits(latin1(SEARCH_LISTED))
    assert listed[0] == (date(2026, 7, 22), "9C_251/2025")


def test_witness_verdicts():
    absent = cbw.classify_search_page(latin1(SEARCH_ABSENT), "6B_499/2026", date(2026, 8, 27))
    assert absent["verdict"] == cbw.WITNESS_WITHDRAWN
    listed = cbw.classify_search_page(latin1(SEARCH_LISTED), "9C_251/2025", date(2026, 7, 22))
    assert listed["verdict"] == cbw.WITNESS_LISTED
    replaced = cbw.classify_search_page(latin1(SEARCH_LISTED), "9C_251/2025", date(2026, 7, 1))
    assert replaced["verdict"] == cbw.WITNESS_REPLACED
    assert replaced["listed_dates"] == ["2026-07-22"]
    assert replaced["relist_ids"] == ["bger_9C_251_2025-D20260722"]
    assert cbw.classify_search_page("<html>Incapsula</html>", "9C_251/2025",
                                    date(2026, 7, 22))["verdict"] == cbw.WITNESS_UNKNOWN


# ═══════════════════════════════════════════════════════════════════════════
# jsonl tail
# ═══════════════════════════════════════════════════════════════════════════

def _row(docket, decided, scraped_at, listed=None, did=None, text_kb=2):
    return {
        "decision_id": did or "bger_" + docket.replace("/", "_"),
        "court": "bger", "canton": "CH", "docket_number": docket,
        "decision_date": decided, "publication_date": listed, "language": "fr",
        "full_text": "x" * (text_kb * 1024), "scraped_at": scraped_at,
    }


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def test_recent_candidates_window_min_age_and_partial_first_line(tmp_path):
    p = tmp_path / "bger.jsonl"
    rows = [
        _row("1C_1/2026", "2026-06-01", "2026-08-01T10:00:00+00:00"),            # outside window
        _row("6B_499/2026", "2026-08-27", "2026-09-11T10:01:33.877476+00:00", "2026-09-11"),
        _row("5F_46/2025", "2026-09-07", "2026-09-16T10:03:50+00:00", "2026-09-16"),
        _row("9C_9/2026", "2026-09-10", "2026-09-17T09:50:00+00:00", "2026-09-17"),  # too young
    ]
    _write_jsonl(p, rows)
    out, truncated = cbw.recent_candidates(p, now=T0, window_days=21, min_age_hours=12,
                                           max_bytes=10 * 1024 * 1024)
    assert [c["docket_number"] for c in out] == ["6B_499/2026", "5F_46/2025"]
    assert out[0]["url"].endswith("aza%3A%2F%2F27-08-2026-6B_499-2026")
    assert out[0]["publication_date"] == "2026-09-11"
    assert truncated is False

    # a tail cap that lands mid-file drops the partial first line and warns
    # when the oldest complete row is still inside the window
    out2, truncated2 = cbw.recent_candidates(p, now=T0, window_days=21, min_age_hours=12,
                                             max_bytes=5 * 1024)
    assert all(c["docket_number"] != "1C_1/2026" for c in out2)
    assert truncated2 is True


# ═══════════════════════════════════════════════════════════════════════════
# ledger state machine
# ═══════════════════════════════════════════════════════════════════════════

def _entry(scraped="2026-09-11T10:01:33+00:00"):
    return cbw.new_entry({"decision_id": "bger_6B_499_2026", "docket_number": "6B_499/2026",
                          "decision_date": "2026-08-27", "publication_date": "2026-09-11",
                          "language": "fr", "scraped_at": scraped,
                          "url": cbw.document_url(date(2026, 8, 27), "6B_499/2026")})


def test_streak_matures_only_after_grace_and_two_distinct_days():
    e = _entry()
    assert cbw.is_due(e, T0, 3)
    cbw.observe(e, cbw.NOT_FOUND, T0)
    assert e["first_not_found"] == T0.isoformat() and e["not_found_days"] == ["2026-09-17"]
    assert not cbw.is_candidate(e, T0, 10)
    assert not cbw.is_due(e, T0 + timedelta(hours=2), 3)          # daily for not-found
    assert cbw.is_due(e, T0 + timedelta(hours=21), 3)
    # grace elapsed but a single observation day: still not a candidate
    assert not cbw.is_candidate(e, T0 + timedelta(days=11), 10)
    cbw.observe(e, cbw.NOT_FOUND, T0 + timedelta(days=11))
    assert cbw.is_candidate(e, T0 + timedelta(days=11), 10)
    # a second observation on the SAME day does not count as a second day
    e2 = _entry()
    cbw.observe(e2, cbw.NOT_FOUND, T0)
    cbw.observe(e2, cbw.NOT_FOUND, T0 + timedelta(hours=3))
    assert e2["not_found_days"] == ["2026-09-17"]
    assert not cbw.is_candidate(e2, T0 + timedelta(days=11), 10)


def test_present_after_not_found_is_a_relist_and_clears_the_streak():
    e = _entry()
    cbw.observe(e, cbw.NOT_FOUND, T0)
    cbw.observe(e, cbw.NOT_FOUND, T0 + timedelta(days=1))
    e["witness"] = {"verdict": "withdrawn"}
    cbw.observe(e, cbw.PRESENT, T0 + timedelta(days=4))
    assert e["first_not_found"] is None and e["not_found_days"] == []
    assert e["witness"] is None
    assert e["relisted_at"] == (T0 + timedelta(days=4)).isoformat()
    assert e["events"][-1]["event"] == "relisted"
    assert e["events"][-1]["not_found_days"] == ["2026-09-17", "2026-09-18"]
    assert not cbw.is_candidate(e, T0 + timedelta(days=30), 10)
    assert not cbw.is_due(e, T0 + timedelta(days=5), 3)            # healthy: every 3 days
    assert cbw.is_due(e, T0 + timedelta(days=7), 3)


def test_transient_classes_do_not_advance_last_checked():
    e = _entry()
    cbw.observe(e, cbw.NOT_FOUND, T0)
    for n, st in enumerate((cbw.SERVICE_ERROR, cbw.BLOCKED, cbw.ERROR), start=1):
        cbw.observe(e, st, T0 + timedelta(days=n))
        assert e["last_checked"] == T0.isoformat()
        assert e["last_transient"] == (T0 + timedelta(days=n)).isoformat()
        assert e["first_not_found"] == T0.isoformat()      # streak untouched
        assert e["transient_streak"] == n
    # the first two transients are retried next run
    e2 = _entry()
    cbw.observe(e2, cbw.UNKNOWN, T0)
    assert cbw.is_due(e2, T0 + timedelta(hours=1), 3)
    cbw.observe(e2, cbw.UNKNOWN, T0 + timedelta(days=1))
    assert cbw.is_due(e2, T0 + timedelta(days=1, hours=1), 3)
    # three in a row drop to the healthy cadence
    cbw.observe(e2, cbw.UNKNOWN, T0 + timedelta(days=2))
    assert not cbw.is_due(e2, T0 + timedelta(days=2, hours=1), 3)
    assert cbw.is_due(e2, T0 + timedelta(days=5), 3)
    cbw.observe(e2, cbw.PRESENT, T0 + timedelta(days=5))
    assert e2["transient_streak"] == 0
    assert e["checks"] == 4
    # a transient last_status is never a candidate, even past grace
    assert not cbw.is_candidate(e, T0 + timedelta(days=20), 10)


def test_prune_keeps_streaks_and_drops_stale_healthy_rows():
    ledger = {"version": 1, "entries": {}}
    old_ok = _entry(scraped=(T0 - timedelta(days=60)).isoformat())
    old_ok["decision_id"] = "a"
    cbw.observe(old_ok, cbw.PRESENT, T0 - timedelta(days=50))
    old_nf = _entry(scraped=(T0 - timedelta(days=60)).isoformat())
    old_nf["decision_id"] = "b"
    cbw.observe(old_nf, cbw.NOT_FOUND, T0 - timedelta(days=50))
    ancient = _entry(scraped=(T0 - timedelta(days=200)).isoformat())
    ancient["decision_id"] = "c"
    cbw.observe(ancient, cbw.NOT_FOUND, T0 - timedelta(days=150))
    fresh = _entry()
    fresh["decision_id"] = "d"
    for e in (old_ok, old_nf, ancient, fresh):
        ledger["entries"][e["decision_id"]] = e
    assert cbw.prune(ledger, T0, 21, 10) == 2
    assert set(ledger["entries"]) == {"b", "d"}


# ═══════════════════════════════════════════════════════════════════════════
# end to end with an injected fetcher
# ═══════════════════════════════════════════════════════════════════════════

class Site:
    """Fake search.bger.ch: per-docket page class, all calls recorded."""

    def __init__(self):
        self.status: dict[str, str] = {}
        self.calls: list[str] = []
        self.fail = False
        self.search_blocked = False

    def __call__(self, url):
        self.calls.append(url)
        if self.fail:
            raise ConnectionError("SOCKS tunnel down")
        if "type=simple_query" in url:
            if self.search_blocked:
                return 200, "<html><script>_Incapsula_Resource</script></html>", url
            return 200, latin1(SEARCH_ABSENT if "6B_499" in url else SEARCH_LISTED), url
        for docket, st in self.status.items():
            if cbw.docket_stem(docket) in url:
                if st == cbw.NOT_FOUND:
                    return 200, latin1(NOT_FOUND_PAGE), url
                if st == cbw.SERVICE_ERROR:
                    return 200, "<html>" + "m " * 300 + cbw.DOCUMENT_SERVICE_ERROR + "</html>", url
                if st == "http500":
                    return 500, "", url
                return 200, latin1(DOCUMENT_PAGE).replace("9C_251/2025", docket), url
        raise AssertionError(f"unexpected fetch {url}")


def _args(tmp_path, **over):
    ns = cbw.build_parser().parse_args([])
    ns.jsonl = str(tmp_path / "output" / "decisions" / "bger.jsonl")
    ns.ledger = str(tmp_path / "logs" / "bger_withdrawal_state.json")
    ns.report = str(tmp_path / "logs" / "bger_withdrawal_candidates.json")
    ns.state_dir = str(tmp_path / "state")
    for k, v in over.items():
        setattr(ns, k, v)
    return ns


@pytest.fixture
def corpus(tmp_path):
    rows = [
        _row("6B_499/2026", "2026-08-27", "2026-09-11T10:01:33+00:00", "2026-09-11"),
        _row("5F_46/2025", "2026-09-07", "2026-09-16T10:03:50+00:00", "2026-09-16"),
        _row("9C_251/2025", "2026-07-22", "2026-09-15T10:32:00+00:00"),
    ]
    _write_jsonl(Path(_args(tmp_path).jsonl), rows)
    return tmp_path


def test_full_lifecycle_alerts_once_and_never_deletes(corpus, monkeypatch):
    site = Site()
    site.status = {"6B_499/2026": cbw.NOT_FOUND, "5F_46/2025": cbw.PRESENT,
                   "9C_251/2025": cbw.PRESENT}
    posts: list[tuple] = []

    def sender(title, message, priority="default", tags="", url=None):
        posts.append((title, message, priority))
        return True

    args = _args(corpus)
    # day 0: everything checked once, one not-found streak starts, no alert
    rep = cbw.run(args, now=T0, fetch=site, sender=sender)
    assert rep["counts"]["candidates"] == 0 and rep["counts"]["watch"] == 1
    assert len([c for c in site.calls if "show_document" in c]) == 3
    assert not any("simple_query" in c for c in site.calls)      # witness only when matured
    assert posts == []
    ledger = json.loads(Path(args.ledger).read_text())
    assert ledger["entries"]["bger_6B_499_2026"]["not_found_days"] == ["2026-09-17"]

    # day 1: only the not-found row is due (healthy rows wait 3 days)
    site.calls.clear()
    cbw.run(args, now=T0 + timedelta(days=1), fetch=site, sender=sender)
    assert [c for c in site.calls if "show_document" in c] == [
        cbw.document_url(date(2026, 8, 27), "6B_499/2026")]
    assert posts == []

    # day 11: grace elapsed, two distinct days -> candidate, one alert; the
    # witness page is blocked that day, so the alert says "unknown"
    site.calls.clear()
    site.search_blocked = True
    rep = cbw.run(args, now=T0 + timedelta(days=11), fetch=site, sender=sender)
    assert rep["counts"]["candidates"] == 1
    cand = rep["candidates"][0]
    assert cand["decision_id"] == "bger_6B_499_2026"
    assert cand["witness"]["verdict"] == cbw.WITNESS_UNKNOWN
    assert cand["served_url"] == "https://mcp.opencaselaw.ch/entscheid/bger_6B_499_2026"
    assert len(posts) == 1
    title, body, prio = posts[0]
    assert "1 new removal candidate" in title and prio == "high"
    assert "6B_499/2026 (27.08.2026, listed 11.09.2026, scraped 11.09.2026)" in body
    assert "AZA index: unknown" in body and "Nothing was deleted" in body
    assert sum("simple_query" in c for c in site.calls) == 1

    # day 12: same candidate, no second alert; the unusable witness IS re-fetched
    site.calls.clear()
    site.search_blocked = False
    rep = cbw.run(args, now=T0 + timedelta(days=12), fetch=site, sender=sender)
    assert rep["counts"]["candidates"] == 1 and len(posts) == 1
    assert sum("simple_query" in c for c in site.calls) == 1
    assert rep["candidates"][0]["witness"]["verdict"] == cbw.WITNESS_WITHDRAWN

    # day 13 (21 h later): re-checked daily, a usable witness is not fetched again
    site.calls.clear()
    cbw.run(args, now=T0 + timedelta(days=12, hours=21), fetch=site, sender=sender)
    assert any("show_document" in c for c in site.calls)
    assert not any("simple_query" in c for c in site.calls)

    # day 14: the court relists it -> candidate cleared, one informational post
    site.status["6B_499/2026"] = cbw.PRESENT
    rep = cbw.run(args, now=T0 + timedelta(days=14), fetch=site, sender=sender)
    assert rep["counts"]["candidates"] == 0 and rep["counts"]["watch"] == 0
    assert len(posts) == 2 and "relisted" in posts[1][0] and posts[1][2] == "default"
    cbw.run(args, now=T0 + timedelta(days=15), fetch=site, sender=sender)
    assert len(posts) == 2                                         # relist told once

    # withdrawn AGAIN: a fresh streak, matured, alerts a third time
    site.status["6B_499/2026"] = cbw.NOT_FOUND
    cbw.run(args, now=T0 + timedelta(days=18), fetch=site, sender=sender)   # healthy cadence
    cbw.run(args, now=T0 + timedelta(days=19), fetch=site, sender=sender)
    assert len(posts) == 2
    rep = cbw.run(args, now=T0 + timedelta(days=28), fetch=site, sender=sender)
    assert rep["counts"]["candidates"] == 1 and len(posts) == 3
    assert "1 new removal candidate" in posts[2][0]
    ledger = json.loads(Path(args.ledger).read_text())
    e = ledger["entries"]["bger_6B_499_2026"]
    assert e["relisted_at"] is None and e["relist_alerted_at"] is None
    assert [ev["event"] for ev in e["events"]] == [
        "not_found_first_seen", "relisted", "not_found_first_seen"]

    # the corpus file was never touched
    assert len(Path(args.jsonl).read_text().splitlines()) == 3


def test_dry_run_and_adhoc_mode_write_nothing_and_post_nothing(corpus):
    site = Site()
    site.status = {"6B_499/2026": cbw.NOT_FOUND, "5F_46/2025": cbw.PRESENT,
                   "9C_251/2025": cbw.PRESENT}

    def sender(*a, **k):
        raise AssertionError("posted during a dry run")

    args = _args(corpus, dry_run=True)
    rep = cbw.run(args, now=T0, fetch=site, sender=sender)
    assert rep["counts"]["watch"] == 1
    assert not Path(args.ledger).exists() and not Path(args.report).exists()

    args = _args(corpus, decision_id=["bger_6B_499_2026", "bger_9C_251_2025", "bger_missing"])
    rep = cbw.run(args, now=T0, fetch=site, sender=sender)
    by_id = {r["decision_id"]: r for r in rep["rows"]}
    assert by_id["bger_6B_499_2026"]["last_status"] == cbw.NOT_FOUND
    assert by_id["bger_6B_499_2026"]["witness"]["verdict"] == cbw.WITNESS_WITHDRAWN
    assert by_id["bger_9C_251_2025"]["last_status"] == cbw.PRESENT
    assert "bger_missing" not in by_id
    assert not Path(args.ledger).exists()

    args = _args(corpus, docket="6B_499/2026", decision_date="2026-08-27", no_witness=True)
    rep = cbw.run(args, now=T0, fetch=site, sender=sender)
    assert rep["rows"][0]["last_status"] == cbw.NOT_FOUND
    assert rep["rows"][0]["url"].endswith("27-08-2026-6B_499-2026")
    assert not Path(args.ledger).exists()


def test_dead_tunnel_aborts_before_touching_the_ledger(corpus):
    site = Site()
    site.fail = True
    args = _args(corpus)
    rep = cbw.run(args, now=T0, fetch=site, sender=lambda *a, **k: True)
    assert rep["aborted"] is True
    assert len(site.calls) == cbw.ABORT_AFTER_ERRORS
    assert not Path(args.ledger).exists() and not Path(args.report).exists()


def test_transient_rows_are_retried_and_the_cap_is_fair(corpus):
    # never-attempted rows are ordered by scraped_at: 6B_499 (11.09), 9C_251
    # (15.09), 5F_46 (16.09); a cap of 2 leaves 5F_46 for the next run
    site = Site()
    site.status = {"6B_499/2026": cbw.SERVICE_ERROR, "9C_251/2025": "http500",
                   "5F_46/2025": cbw.PRESENT}
    args = _args(corpus, max_requests=2)
    cbw.run(args, now=T0, fetch=site, sender=lambda *a, **k: True)
    ledger = json.loads(Path(args.ledger).read_text())
    assert ledger["entries"]["bger_6B_499_2026"]["last_status"] == cbw.SERVICE_ERROR
    assert ledger["entries"]["bger_6B_499_2026"]["last_checked"] is None
    assert ledger["entries"]["bger_9C_251_2025"]["last_status"] == cbw.ERROR
    assert ledger["entries"]["bger_9C_251_2025"]["last_error"] == "HTTP 500"
    assert ledger["entries"]["bger_5F_46_2025"]["last_status"] is None      # cap hit
    # next run: all three are due; the never-attempted row goes first, the two
    # transient rows (attempted at T0) follow instead of starving it again
    site.calls.clear()
    site.status.update({"6B_499/2026": cbw.PRESENT, "9C_251/2025": cbw.PRESENT})
    cbw.run(args, now=T0 + timedelta(hours=1), fetch=site, sender=lambda *a, **k: True)
    assert "07-09-2026-5F_46-2025" in site.calls[0]
    assert len(site.calls) == 2
    ledger = json.loads(Path(args.ledger).read_text())
    assert ledger["entries"]["bger_5F_46_2025"]["last_status"] == cbw.PRESENT
    assert ledger["entries"]["bger_6B_499_2026"]["last_status"] == cbw.PRESENT


def test_no_ntfy_flag_suppresses_the_post(corpus):
    site = Site()
    site.status = {"6B_499/2026": cbw.NOT_FOUND, "5F_46/2025": cbw.PRESENT,
                   "9C_251/2025": cbw.PRESENT}
    args = _args(corpus, no_ntfy=True, fail_on_candidates=True)

    def sender(*a, **k):
        raise AssertionError("posted despite --no-ntfy")

    cbw.run(args, now=T0, fetch=site, sender=sender)
    rep = cbw.run(args, now=T0 + timedelta(days=11), fetch=site, sender=sender)
    assert rep["counts"]["candidates"] == 1
    ledger = json.loads(Path(args.ledger).read_text())
    assert ledger["entries"]["bger_6B_499_2026"]["alerted_at"] is None


def test_candidate_line_for_a_replaced_decision():
    e = _entry()
    e["first_not_found"] = "2026-09-17T13:30:00+00:00"
    e["not_found_days"] = ["2026-09-17", "2026-09-27"]
    e["witness"] = {"verdict": cbw.WITNESS_REPLACED, "listed_dates": ["2026-09-03"],
                    "relist_ids": ["bger_6B_499_2026-D20260903"]}
    line = cbw.candidate_line(e)
    assert "AZA index: replaced under 2026-09-03 (relist id bger_6B_499_2026-D20260903)" in line


def test_send_ntfy_posts_to_the_unified_topic(monkeypatch):
    posted = {}

    class _R:
        def read(self):
            return b""

    def fake_urlopen(req, timeout=None):
        posted["url"] = req.full_url
        posted["title"] = req.get_header("Title")
        posted["data"] = req.data
        return _R()

    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(cbw, "NTFY_URL", "https://ntfy.example/unit-test-topic")
    ok = _REAL_SEND_NTFY("BGer withdrawn-decision check: 1 new removal candidate",
                         "6B_499/2026 ...", priority="high", tags="warning")
    assert ok is True
    assert posted["url"] == "https://ntfy.example/unit-test-topic"
    assert posted["title"].startswith("BGer withdrawn-decision check")
