"""generate_feeds.py — feeds rank by publication date (2026-10-17).

BGer published 4A_524/2025 (decided 2026-05-11) on 2026-10-16. In decision
order it never reached the top 50 of any feed, so a feed reader never saw it.
query_published() keeps the bounded newest-first walk and adds two small
reads; these tests pin that its output equals a brute-force sort of every
matching row by effective date (publication_date, else decision_date), with
the same exclusions as before, and that each read uses the intended index.
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import db_schema
import generate_feeds as gf

TODAY = datetime.now(timezone.utc).date()

FEEDS = [
    ("1=1", ()),
    ("court = ?", ("bger",)),
    ("court = ?", ("bvger",)),
    ("court = ?", ("bge",)),
    ("language = ?", ("de",)),
    ("language = ?", ("fr",)),
]


def _iso(days_ago: int) -> str:
    return (TODAY - timedelta(days=days_ago)).isoformat()


@pytest.fixture()
def conn(tmp_path: Path):
    path = tmp_path / "decisions.db"
    c = sqlite3.connect(path)
    c.executescript(db_schema.SCHEMA_SQL)
    c.execute(f"CREATE INDEX IF NOT EXISTS {gf.PUB_INDEX} ON decisions(court, publication_date)")
    rows = []

    def add(court, lang, decided, published=None):
        rows.append((f"{court}_{len(rows):03d}", court, "CH", f"{len(rows)}/2026",
                     decided, published, lang, "Regeste", "x" * 50, ""))

    langs = ("de", "fr", "it")
    # bger: most published 5-40 days after the decision, some undated.
    for i in range(120):
        decided = 2 + (i * 7) % 400
        pub = None if i % 5 == 0 else _iso(max(0, decided - 5 - i % 35))
        add("bger", langs[i % 3], _iso(decided), pub)
    # The late publication the feature exists for: decided ~5 months ago.
    add("bger", "fr", _iso(158), _iso(0))
    # Published before it was decided (within the build's 31-day guard).
    add("bger", "de", _iso(3), _iso(10))
    # Future publication date: must rank by its decision date, never on top.
    add("bger", "de", _iso(30), _iso(-20))
    # bvger: everything dated, short lag.
    for i in range(90):
        add("bvger", langs[i % 2], _iso(1 + i * 2), _iso(i * 2))
    # bge: no publication dates (as in production), decision dates lag.
    for i in range(70):
        add("bge", "de", _iso(60 + i * 3))
    # A quiet cantonal court, so the global feed probes more than three courts.
    for i in range(5):
        add("zg_obergericht", "de", _iso(200 + i), _iso(1 + i))
    c.executemany(
        "INSERT INTO decisions (decision_id, court, canton, docket_number, decision_date,"
        " publication_date, language, regeste, full_text, legal_area)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    c.commit()
    c.row_factory = None
    yield c
    c.close()


def _reference(conn, where, params, limit=gf.ITEMS_PER_FEED):
    """Every matching row, sorted by effective date in Python."""
    rows = gf._rows(conn.execute(
        gf._PUB_SELECT_COLS + f"FROM decisions WHERE {where} "
        "AND decision_date IS NOT NULL AND decision_date != '' "
        "AND decision_date <= date('now')", params))
    for r in rows:
        if (r.get("publication_date") or "")[:10] > TODAY.isoformat():
            r["publication_date"] = None
    return gf._feed_order(rows)[:limit]


def _courts(conn):
    return [r[0] for r in conn.execute("SELECT DISTINCT court FROM decisions")]


@pytest.mark.parametrize("where,params", FEEDS)
@pytest.mark.parametrize("limit", [1, 7, 50])
def test_published_feed_equals_brute_force(conn, where, params, limit):
    courts = [params[0]] if where == "court = ?" else _courts(conn)
    got = gf.query_published(conn, where, params, limit, courts=courts)
    assert [r["decision_id"] for r in got] == [
        r["decision_id"] for r in _reference(conn, where, params, limit)]


def test_late_publication_tops_the_bger_feed(conn):
    got = gf.query_published(conn, "court = ?", ("bger",), courts=["bger"])
    today = [r for r in got if r.get("publication_date") == _iso(0)]
    # In the feed, inside the block published today (ties rank by decision date).
    assert _iso(158) in [r["decision_date"] for r in today]
    assert got[:len(today)] == today
    # In decision-date order it would not have made the top 50.
    plain = gf.query_decisions(conn, "court = ?", ("bger",))
    assert _iso(158) not in [r["decision_date"] for r in plain]


def test_future_publication_never_ranks_first(conn):
    got = gf.query_published(conn, "1=1", (), courts=_courts(conn))
    assert all((r.get("publication_date") or "") <= TODAY.isoformat() for r in got)


def test_bge_without_publication_dates_keeps_decision_order(conn):
    got = gf.query_published(conn, "court = ?", ("bge",), courts=["bge"])
    dates = [r["decision_date"] for r in got]
    assert dates == sorted(dates, reverse=True)


def test_probe_and_decided_reads_use_their_indexes(conn):
    probe = " ".join(r[3] for r in conn.execute(
        "EXPLAIN QUERY PLAN SELECT decision_id FROM decisions "
        f"INDEXED BY {gf.PUB_INDEX} WHERE court = ? AND publication_date >= ? AND (language = ?)",
        ("bger", _iso(3), "de")))
    assert gf.PUB_INDEX in probe and "SCAN decisions" not in probe
    decided = " ".join(r[3] for r in conn.execute(
        f"EXPLAIN QUERY PLAN SELECT decision_id FROM decisions INDEXED BY {gf.DATE_INDEX} "
        "WHERE court = ? AND decision_date >= ? AND decision_date <= date('now')",
        ("bger", _iso(3))))
    assert gf.DATE_INDEX in decided and "SCAN decisions" not in decided


def test_item_pubdate_is_the_publication_date():
    item = gf.make_item({"decision_id": "bger_x", "court": "bger", "docket_number": "4A_524/2025",
                         "decision_date": "2026-05-11", "publication_date": "2026-10-16"})
    assert item.findtext("pubDate").startswith("Fri, 16 Oct 2026")
    assert "vom 2026-05-11" in item.findtext("title")


def test_main_uses_publication_order_only_with_the_index(conn, tmp_path, monkeypatch, capsys):
    db = tmp_path / "decisions.db"
    out = tmp_path / "site"
    monkeypatch.setattr(sys, "argv", ["generate_feeds.py", "--db", str(db), "--out", str(out)])
    gf.main()
    assert "feeds in decision-date order" not in capsys.readouterr().out
    conn.execute(f"DROP INDEX {gf.PUB_INDEX}")
    conn.commit()
    gf.main()
    assert f"{gf.PUB_INDEX} missing" in capsys.readouterr().out
