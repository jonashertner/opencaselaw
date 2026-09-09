"""Fetch policy of the nightly Bundesblatt ingest (2026-09-09).

With the type list the first run pushes ~3,500 candidates per language
through ``--ingest-all``; each costs a SPARQL manifestation lookup plus a
HEAD/GET on Fedlex's file store. Before this policy a refusal was one more
line in ``stats['errors']`` and the loop kept going at the configured rate;
a failed SPARQL lookup was even swallowed as "no manifestation". These tests
pin:

  * a 429/403 anywhere (SPARQL lookup, HEAD probe, GET) stops the run at
    once; five consecutive failures of any kind stop it too,
  * exponential backoff between failures, reset on the first success,
  * ``--max-per-run`` caps a night, ``--limit`` still works, and a stopped
    run exits non-zero so the unit's && chain halts,
  * committed rows survive a stop (the next run resumes after them),
  * search_materialien accepts doc_type for symmetry and says it is a no-op.

Everything runs offline against stubs and tmp SQLite files.
"""
from __future__ import annotations

import sqlite3
import sys
import urllib.error
from pathlib import Path

import pytest
import requests

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import mcp_server  # noqa: E402
from search_stack import build_botschaft_corpus as bbc  # noqa: E402


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://fedlex.data.admin.ch/x.pdf", code, "nope", {}, None)


def _requests_error(code: int) -> requests.HTTPError:
    resp = requests.Response()
    resp.status_code = code
    return requests.HTTPError(f"HTTP {code}", response=resp)


def _cands(n: int, year: int = 2026) -> list[tuple]:
    return [(year, p, f"https://fedlex.data.admin.ch/eli/fga/{year}/{p}", "botschaft")
            for p in range(1, n + 1)]


# ── status extraction ─────────────────────────────────────────────────────


def test_http_status_of_covers_every_client():
    assert bbc.http_status_of(_http_error(429)) == 429
    assert bbc.http_status_of(_requests_error(403)) == 403
    assert bbc.http_status_of(bbc.SourceUnavailable("x", status=503)) == 503
    assert bbc.http_status_of(bbc.SourceUnavailable("timeout")) is None
    assert bbc.http_status_of(RuntimeError("boom")) is None
    assert bbc.http_status_of(ConnectionResetError(104, "reset")) is None


# ── the loop ──────────────────────────────────────────────────────────────


def test_loop_stops_at_once_on_429(monkeypatch):
    seen, sleeps = [], []

    def fake_ingest(conn, **kw):
        seen.append(kw["page"])
        if kw["page"] == 2:
            raise _http_error(429)
        return kw["page"]

    monkeypatch.setattr(bbc, "ingest_one", fake_ingest)
    stats = bbc.ingest_candidates(
        None, _cands(10), language="de", prefiltered=True, rate_limit=0.5,
        sleep=sleeps.append)
    assert stats["stopped"] == "http_429"
    assert seen == [1, 2]
    assert stats["processed"] == 2 and stats["accepted"] == 1 and stats["errors"] == 1
    assert sleeps == [0.5]          # no backoff wait before a fatal stop


def test_loop_stops_at_once_on_403_from_requests(monkeypatch):
    monkeypatch.setattr(bbc, "ingest_one",
                        lambda conn, **kw: (_ for _ in ()).throw(_requests_error(403)))
    stats = bbc.ingest_candidates(
        None, _cands(3), language="fr", prefiltered=True, rate_limit=0.5,
        sleep=lambda s: None)
    assert stats["stopped"] == "http_403" and stats["processed"] == 1


def test_loop_stops_after_five_consecutive_failures_with_backoff(monkeypatch):
    sleeps = []
    monkeypatch.setattr(bbc, "ingest_one",
                        lambda conn, **kw: (_ for _ in ()).throw(RuntimeError("read timed out")))
    stats = bbc.ingest_candidates(
        None, _cands(50), language="de", prefiltered=True, rate_limit=0.5,
        sleep=sleeps.append)
    assert stats["stopped"] == "5_consecutive_failures"
    assert stats["processed"] == 5 and stats["errors"] == 5
    assert stats["max_consecutive_failures_seen"] == 5
    # 2, 4, 8, 16 s between the failures, then the stop (no fifth pause)
    assert sleeps == [2.0, 4.0, 8.0, 16.0]


def test_backoff_is_capped(monkeypatch):
    sleeps = []
    monkeypatch.setattr(bbc, "ingest_one",
                        lambda conn, **kw: (_ for _ in ()).throw(RuntimeError("x")))
    stats = bbc.ingest_candidates(
        None, _cands(50), language="de", prefiltered=True, rate_limit=0.5,
        max_consecutive_failures=8, sleep=sleeps.append)
    assert stats["stopped"] == "8_consecutive_failures"
    assert sleeps == [2.0, 4.0, 8.0, 16.0, 32.0, 60.0, 60.0]


def test_success_resets_the_failure_counter(monkeypatch):
    sleeps = []
    calls = {"n": 0}

    def fake_ingest(conn, **kw):
        calls["n"] += 1
        if calls["n"] % 5 == 0:
            return calls["n"]           # every fifth candidate succeeds
        raise RuntimeError("flaky")

    monkeypatch.setattr(bbc, "ingest_one", fake_ingest)
    stats = bbc.ingest_candidates(
        None, _cands(15), language="de", prefiltered=True, rate_limit=0.5,
        sleep=sleeps.append)
    assert stats["stopped"] is None
    assert stats["processed"] == 15 and stats["accepted"] == 3 and stats["errors"] == 12
    assert stats["max_consecutive_failures_seen"] == 4
    assert sleeps == [2.0, 4.0, 8.0, 16.0, 0.5] * 3


def test_loop_keeps_rate_limit_and_counts_filtered(monkeypatch):
    sleeps = []
    monkeypatch.setattr(bbc, "ingest_one",
                        lambda conn, **kw: None if kw["page"] % 2 else kw["page"])
    stats = bbc.ingest_candidates(
        None, _cands(4), language="it", prefiltered=False, rate_limit=0.5,
        sleep=sleeps.append)
    assert stats == {**stats, "stopped": None, "accepted": 2, "rejected_by_filter": 2,
                     "errors": 0, "processed": 4, "total": 4}
    assert sleeps == [0.5] * 4


# ── where the refusals come from ──────────────────────────────────────────


def test_failed_sparql_lookup_is_not_no_manifestation(monkeypatch):
    monkeypatch.setattr(bbc, "fetch_manifestations",
                        lambda eli: (_ for _ in ()).throw(_requests_error(429)))
    with pytest.raises(bbc.SourceUnavailable) as ei:
        bbc.resolve_manifestation("https://fedlex.data.admin.ch/eli/fga/2026/1", language="de")
    assert ei.value.status == 429
    # a plain network failure carries no status but still surfaces
    monkeypatch.setattr(bbc, "fetch_manifestations",
                        lambda eli: (_ for _ in ()).throw(requests.ConnectionError("refused")))
    with pytest.raises(bbc.SourceUnavailable) as ei:
        bbc.resolve_manifestation("https://fedlex.data.admin.ch/eli/fga/2026/1", language="de")
    assert ei.value.status is None


def test_403_on_sparql_lookup_stops_the_run_and_keeps_committed_rows(tmp_path, monkeypatch):
    """End to end through the real ingest_one: two documents land, the third
    lookup is refused, the run stops with the two rows committed."""
    conn = sqlite3.connect(tmp_path / "m.db")
    bbc.ensure_schema(conn)
    lookups = []

    def fake_resolve(eli, language="de", strict_language=True, skip_formats=()):
        lookups.append(eli)
        if eli.endswith("/3"):
            raise bbc.SourceUnavailable("SPARQL 403", status=403)
        return (f"https://fedlex.data.admin.ch/filestore/{eli.rsplit('/', 1)[1]}.pdf", "pdf-a")

    monkeypatch.setattr(bbc, "resolve_manifestation", fake_resolve)
    monkeypatch.setattr(bbc, "fetch_pdf_parts", lambda u, timeout=60, max_parts=200: [u.encode()])
    monkeypatch.setattr(bbc, "extract_pdf_pages",
                        lambda b: ["Botschaft\n\nZu Art. 1\nDer Bundesrat beantragt die Änderung."])
    stats = bbc.ingest_candidates(
        conn, _cands(5), language="de", prefiltered=True, rate_limit=0.5,
        sleep=lambda s: None)
    assert stats["stopped"] == "http_403" and stats["accepted"] == 2
    assert len(lookups) == 3
    fresh = sqlite3.connect(tmp_path / "m.db")     # committed, visible elsewhere
    assert fresh.execute("SELECT COUNT(*) FROM botschaft_documents").fetchone()[0] == 2


def test_head_probe_refusal_is_fatal_but_404_is_just_no_part(monkeypatch):
    import urllib.request

    def urlopen_429(req, timeout=15):
        raise _http_error(429)

    monkeypatch.setattr(urllib.request, "urlopen", urlopen_429)
    with pytest.raises(bbc.SourceUnavailable) as ei:
        bbc._url_exists("https://fedlex.data.admin.ch/filestore/a-2.pdf")
    assert ei.value.status == 429

    def urlopen_404(req, timeout=15):
        raise _http_error(404)

    monkeypatch.setattr(urllib.request, "urlopen", urlopen_404)
    assert bbc._url_exists("https://fedlex.data.admin.ch/filestore/a-2.pdf") is False


# ── CLI: --max-per-run and the exit code ──────────────────────────────────


def _run_main(tmp_path, monkeypatch, argv, discovered, ingest):
    db = tmp_path / "m.db"
    conn = sqlite3.connect(db)
    bbc.ensure_schema(conn)
    conn.close()
    import scrapers.fedlex_materialien as fm
    monkeypatch.setattr(fm, "discover_fga_documents",
                        lambda language, doc_types: discovered)
    monkeypatch.setattr(bbc, "ingest_one", ingest)
    monkeypatch.setattr(bbc, "link_from_amendment_refs", lambda conn: {"linked": 0})
    import time
    monkeypatch.setattr(time, "sleep", lambda s: None)
    monkeypatch.setattr(sys, "argv", ["build_botschaft_corpus", "--db", str(db), *argv])
    return bbc.main()


def _discovered(n: int) -> list[dict]:
    return [{"year": 2026, "page": p, "eli_uri": f"https://fedlex.data.admin.ch/eli/fga/2026/{p}",
             "doc_type": "botschaft", "title": f"Botschaft {p}"} for p in range(1, n + 1)]


def test_main_caps_per_run_newest_first(tmp_path, monkeypatch, capsys):
    seen = []
    rc = _run_main(
        tmp_path, monkeypatch,
        ["--ingest-all", "--discover-via-sparql", "--language", "de",
         "--types", "23,25", "--max-per-run", "3", "--rate-limit", "0.5"],
        _discovered(10), lambda conn, **kw: seen.append(kw["page"]) or kw["page"])
    assert rc == 0
    assert seen == [10, 9, 8]
    out = capsys.readouterr().out
    assert '"total": 3' in out and '"stopped": null' in out


def test_main_limit_still_caps(tmp_path, monkeypatch):
    seen = []
    rc = _run_main(
        tmp_path, monkeypatch,
        ["--ingest-all", "--discover-via-sparql", "--language", "de", "--limit", "2"],
        _discovered(5), lambda conn, **kw: seen.append(kw["page"]) or kw["page"])
    assert rc == 0 and seen == [5, 4]


def test_main_exits_nonzero_when_the_run_is_stopped(tmp_path, monkeypatch, capsys):
    rc = _run_main(
        tmp_path, monkeypatch,
        ["--ingest-all", "--discover-via-sparql", "--language", "de"],
        _discovered(5), lambda conn, **kw: (_ for _ in ()).throw(_http_error(429)))
    assert rc == bbc.INGEST_STOPPED_EXIT_CODE == 4
    assert '"stopped": "http_429"' in capsys.readouterr().out


def test_main_honours_max_consecutive_failures(tmp_path, monkeypatch, capsys):
    rc = _run_main(
        tmp_path, monkeypatch,
        ["--ingest-all", "--discover-via-sparql", "--language", "de",
         "--max-consecutive-failures", "2"],
        _discovered(5), lambda conn, **kw: (_ for _ in ()).throw(RuntimeError("x")))
    assert rc == 4
    assert '"stopped": "2_consecutive_failures"' in capsys.readouterr().out


# ── search_materialien: doc_type accepted, documented no-op ───────────────


@pytest.fixture()
def digest_db(tmp_path, monkeypatch):
    p = tmp_path / "materialien.db"
    c = sqlite3.connect(p)
    c.executescript("""
        CREATE TABLE materialien (id INTEGER PRIMARY KEY AUTOINCREMENT,
            law_code TEXT NOT NULL, sr_number TEXT, article TEXT NOT NULL,
            bbl_ref TEXT NOT NULL, bbl_page_refs TEXT, legislative_intent TEXT,
            key_arguments TEXT, design_choices TEXT, rejected_alternatives TEXT,
            general_context TEXT);
        CREATE VIRTUAL TABLE materialien_fts USING fts5(
            law_code, article, bbl_ref, legislative_intent, key_arguments,
            design_choices, rejected_alternatives, general_context,
            content='materialien', content_rowid='id');
        INSERT INTO materialien (law_code, sr_number, article, bbl_ref, legislative_intent)
            VALUES ('BV', '101', '8', 'BBl 1997 I 1',
                    'Die Rechtsgleichheit verbietet die Diskriminierung.');
        INSERT INTO materialien_fts(rowid, law_code, article, bbl_ref, legislative_intent)
            SELECT id, law_code, article, bbl_ref, legislative_intent FROM materialien;
    """)
    c.commit()
    c.close()
    monkeypatch.setenv("SWISS_CASELAW_MATERIALIEN_DB", str(p))
    monkeypatch.setattr(mcp_server, "MATERIALIEN_DB_PATH", p)
    return p


def test_search_materialien_doc_type_is_a_documented_noop(digest_db):
    plain = mcp_server.search_materialien("Rechtsgleichheit")
    assert plain["count"] == 1 and plain["doc_type_filter"] is None
    assert "doc_type_note" not in plain
    typed = mcp_server.search_materialien("Rechtsgleichheit", doc_type="bericht_kommission")
    # same hits: the filter narrows nothing
    assert typed["results"] == plain["results"]
    assert typed["doc_type_filter"] == "bericht_kommission"
    assert "search_botschaft" in typed["doc_type_note"]
    assert "does not narrow" in typed["doc_type_note"]
    # 'all' and case are normalised like everywhere else
    assert mcp_server.search_materialien("Rechtsgleichheit", doc_type="ALL")["doc_type_filter"] is None


def test_search_materialien_rejects_unknown_doc_type(digest_db):
    r = mcp_server.search_materialien("Rechtsgleichheit", doc_type="mitteilung")
    assert "Unknown doc_type" in r["error"]
