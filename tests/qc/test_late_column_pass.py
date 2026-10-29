"""The shared late-column pass, and per-check timings in the gate report.

Context (2026-10-26). publish.py Step 5c timed out on 10-23 and 10-26 and
finished with 58 s to spare on 10-20. The gate reports since 2026-09 show the
two URL checks at ~1,600-2,100 s each, and on the slow days the run going on
for several hundred to 1,700 s more with no visible check to account for it.
Three gate checks read a column stored after `full_text` (ordinal 22 of 36)
for every row, so each walked every row's overflow chain — ~70 GB on
production — and two of them did it twice:

  urls.no_relative_source_urls     count pass + sample pass
  urls.http_when_https_available   GROUP BY scheme pass
  duplicates.court_docket_collisions
                                   count pass + sample pass, both through the
                                   non-covering court index

They now share one sequential pass (quality/checks/_late_columns.py). These
tests hold the new numbers to the old queries, which are kept here verbatim.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from db_schema import SCHEMA_SQL  # noqa: E402
from quality import runner  # noqa: E402
from quality.checks import _late_columns, duplicates, urls  # noqa: E402

LATE_CHECKS = ["urls.no_relative_source_urls", "urls.http_when_https_available",
               "duplicates.court_docket_collisions"]

# ------------------------------------------------- the old queries, verbatim

OLD_RELATIVE_COUNT = (
    "SELECT COUNT(*) FROM decisions "
    "WHERE source_url IS NOT NULL AND source_url != '' "
    "AND source_url NOT LIKE 'http%'"
)
OLD_RELATIVE_SAMPLE = (
    "SELECT decision_id, court, source_url FROM decisions "
    "WHERE source_url IS NOT NULL AND source_url != '' "
    "AND source_url NOT LIKE 'http%' LIMIT 5"
)
OLD_SCHEMES = (
    "SELECT "
    "  CASE WHEN source_url LIKE 'http://%' THEN substr(source_url, 8) "
    "       ELSE substr(source_url, 9) END AS path_part, "
    "  CASE WHEN source_url LIKE 'http://%' THEN 'http' ELSE 'https' END AS scheme, "
    "  COUNT(*) AS n "
    "FROM decisions "
    "WHERE source_url IS NOT NULL AND source_url != '' "
    "GROUP BY scheme"
)
OLD_DUP_COUNT = """
    SELECT COUNT(*) FROM (
        SELECT 1 FROM decisions
        WHERE docket_number IS NOT NULL AND docket_number != ''
          AND content_hash IS NOT NULL AND content_hash != ''
        GROUP BY court, docket_number, content_hash
        HAVING COUNT(*) > 1
    )
"""
OLD_DUP_SAMPLE = """
    SELECT court, docket_number, content_hash, COUNT(*) AS n FROM decisions
    WHERE docket_number IS NOT NULL AND docket_number != ''
      AND content_hash IS NOT NULL AND content_hash != ''
    GROUP BY court, docket_number, content_hash HAVING n > 1
    ORDER BY n DESC LIMIT 5
"""

# ----------------------------------------------------------------- fixtures

URLS = [None, "", "https://a.ch/1", "http://a.ch/2", "HTTP://a.ch/3",
        "HTTPS://a.ch/4", "/cgi-bin/5", "ftp://a.ch/6", "httpfoo/7",
        "www.a.ch/8"]

# (court, docket, content_hash, copies)
DUP_SHAPES = [
    ("bger", "1C_1/2020", "h1", 3),     # a group of three
    ("bger", "1C_2/2020", "h2", 2),     # a pair
    ("bvger", "1C_1/2020", "h1", 2),    # same docket+hash, other court: own group
    ("bger", "1C_3/2020", "h3", 1),     # distinct content under one docket:
    ("bger", "1C_3/2020", "h4", 1),     #   not a duplicate
    ("bger", "", "h5", 2),              # empty docket: excluded
    ("bger", "1C_6/2020", "", 2),       # empty hash: excluded
    ("bger", "1C_7/2020", None, 2),     # NULL hash: excluded
]


def _corpus(tmp_path: Path, extra_dup_groups: int = 0,
            name: str = "decisions.db") -> Path:
    """The production schema, with full_text long enough to overflow."""
    db = tmp_path / name
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA_SQL)
    rows = []
    i = 0

    def add(court, docket, chash, url):
        nonlocal i
        i += 1
        rows.append((f"{court}_{i}", court, "CH", docket or "", "de",
                     "Erwägung " * 800, url, chash))

    for k, url in enumerate(URLS * 3):
        add("bger", f"2C_{k}/2021", f"u{k}", url)
    for court, docket, chash, copies in DUP_SHAPES:
        for _ in range(copies):
            add(court, docket, chash, "https://x.ch/")
    for g in range(extra_dup_groups):
        for _ in range(2):
            add("zh_obergericht", f"LE{g}", f"z{g}", "https://x.ch/")
    conn.executemany(
        "INSERT INTO decisions (decision_id, court, canton, docket_number, "
        "language, full_text, source_url, content_hash) VALUES (?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()
    return db


def _ro(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db}?mode=ro&immutable=1", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


@pytest.fixture(autouse=True)
def _clean_cache():
    _late_columns._reset_cache()
    yield
    _late_columns._reset_cache()


# ------------------------------------------------ same numbers as before


def test_relative_urls_match_the_old_queries(tmp_path):
    db = _corpus(tmp_path)
    conn = _ro(db)
    r = urls.check_no_relative_source_urls(conn, db_path=str(db))
    old_n = conn.execute(OLD_RELATIVE_COUNT).fetchone()[0]
    assert old_n == 9            # '/cgi-bin', 'ftp://', 'www.' x 3 copies
    assert r.metric_value == old_n
    assert r.passed is False
    old_sample = [dict(x) for x in conn.execute(OLD_RELATIVE_SAMPLE)]
    assert r.sample_rows == old_sample


def test_http_counts_match_the_old_query(tmp_path):
    db = _corpus(tmp_path)
    conn = _ro(db)
    r = urls.check_http_when_https_available(conn, db_path=str(db))
    old = {x["scheme"]: x["n"] for x in conn.execute(OLD_SCHEMES)}
    assert old == {"http": 6, "https": 33}   # HTTP:// counts as http
    assert r.extra == old
    assert r.metric_value == old["http"]


def test_a_scheme_with_no_rows_stays_absent(tmp_path):
    db = tmp_path / "d.db"
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA_SQL)
    conn.execute("INSERT INTO decisions (decision_id, court, canton, "
                 "docket_number, language, source_url) "
                 "VALUES ('a', 'bger', 'CH', 'x', 'de', 'https://a')")
    conn.commit()
    conn.close()
    r = urls.check_http_when_https_available(_ro(db), db_path=str(db))
    assert r.extra == {"https": 1}
    assert r.metric_value == 0


def test_duplicate_groups_match_the_old_queries(tmp_path):
    db = _corpus(tmp_path)
    conn = _ro(db)
    r = duplicates.check_court_docket_collisions(conn, db_path=str(db))
    old_n = conn.execute(OLD_DUP_COUNT).fetchone()[0]
    assert old_n == 3
    assert r.metric_value == old_n
    old_sample = [dict(x) for x in conn.execute(OLD_DUP_SAMPLE)]
    assert r.sample_rows == old_sample        # here n is 3, 2, 2: ties only
    assert r.sample_rows[0] == {"court": "bger", "docket_number": "1C_1/2020",
                                "content_hash": "h1", "n": 3}


def test_duplicate_sample_keeps_the_largest_groups(tmp_path):
    db = _corpus(tmp_path, extra_dup_groups=12)
    conn = _ro(db)
    r = duplicates.check_court_docket_collisions(conn, db_path=str(db))
    assert r.metric_value == conn.execute(OLD_DUP_COUNT).fetchone()[0] == 15
    assert len(r.sample_rows) == 5
    assert r.sample_rows[0]["n"] == 3
    assert [s["n"] for s in r.sample_rows] == sorted(
        (s["n"] for s in r.sample_rows), reverse=True)


def test_clean_corpus_passes_everything(tmp_path):
    db = tmp_path / "d.db"
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA_SQL)
    conn.executemany(
        "INSERT INTO decisions (decision_id, court, canton, docket_number, "
        "language, source_url, content_hash) VALUES (?,?,?,?,?,?,?)",
        [(f"d{i}", "bger", "CH", f"1C_{i}/2020", "de", f"https://a/{i}", f"h{i}")
         for i in range(20)],
    )
    conn.commit()
    conn.close()
    ro = _ro(db)
    for fn in (urls.check_no_relative_source_urls,
               urls.check_http_when_https_available,
               duplicates.check_court_docket_collisions):
        r = fn(ro, db_path=str(db))
        assert r.passed and r.metric_value == 0 and not r.sample_rows, r.name


def test_empty_table(tmp_path):
    db = tmp_path / "d.db"
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA_SQL)
    conn.close()
    c = _late_columns.census(_ro(db), str(db))
    assert (c.n_url, c.n_relative, c.n_http, c.dup_groups) == (0, 0, 0, 0)


# -------------------------------------------------------- one pass, in order


def test_the_pass_is_a_sequential_scan(tmp_path):
    """Not the court index: that visits rows out of rowid order and is what
    the old duplicate query was planned through."""
    conn = _ro(_corpus(tmp_path))
    plan = [r[3] for r in conn.execute("EXPLAIN QUERY PLAN " + _late_columns.PASS_SQL)]
    scans = [d for d in plan if d.startswith("SCAN")]
    assert scans == ["SCAN decisions"], plan
    old = [r[3] for r in conn.execute("EXPLAIN QUERY PLAN " + OLD_DUP_COUNT)]
    assert any("USING INDEX idx_decisions_court" in d for d in old), old


def _traced_statements(monkeypatch) -> list[str]:
    seen: list[str] = []
    real_open = runner._open_db

    def open_traced(p):
        conn = real_open(p)
        conn.set_trace_callback(seen.append)
        return conn

    monkeypatch.setattr(runner, "_open_db", open_traced)
    return seen


def test_the_three_checks_make_one_pass_between_them(tmp_path, monkeypatch):
    db = _corpus(tmp_path)
    seen = _traced_statements(monkeypatch)
    report = runner.run(db, only=LATE_CHECKS, record_history=False, parallel=True)
    assert {r.name for r in report.results} == {
        "urls.no_relative_source_urls", "urls.http_count",
        "duplicates.court_docket_collisions"}
    # Every statement that reads source_url or content_hash for more than a
    # handful of rowids is a full pass. There must be exactly one.
    full = [s for s in seen
            if ("source_url" in s or "content_hash" in s)
            and "rowid IN" not in s]
    assert len(full) == 1, full
    assert "NOT INDEXED" in full[0]


def test_a_changed_file_is_scanned_again(tmp_path):
    db = _corpus(tmp_path)
    first = _late_columns.census(_ro(db), str(db))
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO decisions (decision_id, court, canton, docket_number, "
                 "language, source_url) VALUES ('new', 'bger', 'CH', 'x', 'de', '/rel')")
    conn.commit()
    conn.close()
    second = _late_columns.census(_ro(db), str(db))
    assert second.n_relative == first.n_relative + 1


def test_without_a_path_nothing_is_cached(tmp_path):
    db = _corpus(tmp_path)
    _late_columns.census(_ro(db))
    assert not _late_columns._cache


# ------------------------------------------------- timings in the report


def test_gate_report_keeps_timings_of_checks_it_filters_out(tmp_path):
    """A passing WARNING is dropped from a --critical-only report. Its cost
    must not be dropped with it: that is how the slowest check in the gate
    stayed invisible."""
    db = _corpus(tmp_path)
    report = runner.run(db, only=["duplicates.court_docket_collisions",
                                  "urls.no_relative_source_urls"],
                        critical_only=True, record_history=False)
    names = {r.name for r in report.results}
    assert "duplicates.court_docket_collisions" not in names   # filtered
    assert "urls.no_relative_source_urls" in names              # CRITICAL fail
    assert set(report.check_timings) == {
        "duplicates.court_docket_collisions", "urls.no_relative_source_urls"}
    assert all(v >= 0 for v in report.check_timings.values())


def test_timings_are_serialised_slowest_first(tmp_path):
    from quality.types import CheckRunReport
    rep = CheckRunReport(run_at="x", db_path="d", duration_seconds=3.0,
                         results=[], check_timings={"a.fast": 0.1, "b.slow": 2.5,
                                                    "c.mid": 1.0})
    assert list(rep.to_dict()["check_timings"]) == ["b.slow", "c.mid", "a.fast"]


def test_fanned_out_checks_are_timed_once(tmp_path):
    db = _corpus(tmp_path)
    report = runner.run(db, only=["courts.expected_federal_courts_present"],
                        record_history=False)
    assert list(report.check_timings) == ["courts.expected_federal_courts_present"]
