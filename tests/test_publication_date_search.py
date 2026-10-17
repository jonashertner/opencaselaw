"""Publication-date filters and sort (published_from / published_to /
sort="published_desc") and the publication-ordered Atom feed.

A daily "newly published" alert cannot work on decision_date: BGer released
4A_524/2025 (decided 2026-05-11) on 2026-10-16, so a date_from=yesterday alert
never saw it, and the Atom feed ranked it below hundreds of rulings decided
later (reported by a user 2026-10-16).
"""
import sqlite3
from datetime import datetime, timedelta

import pytest

import exports
import mcp_server as m


def _ago(days: int) -> str:
    return (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")


TODAY = _ago(0)
YESTERDAY = _ago(1)

# (decision_id, court, decision_date, publication_date, full_text)
ROWS = [
    ("bger_late", "bger", _ago(158), TODAY, "Arbeitsvertrag fristlose Kündigung"),
    ("bger_new", "bger", _ago(6), YESTERDAY, "Arbeitsvertrag Lohn"),
    ("bger_older", "bger", _ago(45), _ago(26), "Arbeitsvertrag Ferien"),
    ("bger_nopub", "bger", _ago(4), None, "Arbeitsvertrag Zeugnis"),
    # Published today, but decided before the 18-month window.
    ("bger_ancient", "bger", _ago(m.PUBLICATION_LAG_WINDOW_DAYS + 90), TODAY, "Arbeitsvertrag alt"),
    ("bvger_x", "bvger", _ago(15), TODAY, "Asyl Wegweisung"),
    # Timestamped publication_date on YESTERDAY: published_to=YESTERDAY must match it.
    ("bger_stamp", "bger", _ago(20), YESTERDAY + "T08:00:00", "Arbeitsvertrag Probezeit"),
]


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(
        "CREATE VIRTUAL TABLE decisions_fts USING fts5("
        "decision_id, court, canton, docket_number, language, title, regeste, full_text);"
        "CREATE TABLE decisions (decision_id TEXT PRIMARY KEY, court TEXT, canton TEXT,"
        " chamber TEXT, docket_number TEXT, decision_date TEXT, publication_date TEXT,"
        " language TEXT, title TEXT, regeste TEXT, full_text TEXT, decision_type TEXT,"
        " source_url TEXT, pdf_url TEXT);"
        "CREATE INDEX idx_decisions_court ON decisions(court);"
        "CREATE INDEX idx_decisions_date ON decisions(decision_date);"
    )
    for rowid, (did, court, dd, pd, text) in enumerate(ROWS, start=1):
        c.execute(
            "INSERT INTO decisions (rowid, decision_id, court, canton, chamber, docket_number,"
            " decision_date, publication_date, language, title, regeste, full_text,"
            " source_url, pdf_url) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (rowid, did, court, "CH", "", did, dd, pd, "de", did, "", text, "http://x", ""),
        )
        c.execute(
            "INSERT INTO decisions_fts (rowid, decision_id, court, canton, docket_number,"
            " language, title, regeste, full_text) VALUES (?,?,?,?,?,?,?,?,?)",
            (rowid, did, court, "CH", did, "de", did, "", text),
        )
    c.commit()
    yield c
    c.close()


def _ids(rows):
    return [r["decision_id"] for r in rows]


# ── argument validation ──

def test_published_to_requires_published_from():
    assert "requires published_from" in m._published_arg_error(None, "2026-10-16")
    assert m._published_arg_error("2026-10-01", "2026-10-16") is None
    assert m._published_arg_error(None, None) is None


def test_unparseable_published_date_is_rejected():
    assert "NOT applied" in m._published_arg_error("16/10/2026", None)
    assert m._published_arg_error("16.10.2026", None) is None  # Swiss form accepted


# ── filter-only path (the alert / feed case) ──

def test_published_from_finds_late_published_ruling(conn):
    meta: dict = {}
    rows, total = m._list_recent(conn, "bger", None, None, None, None,
                                 published_from=YESTERDAY, meta=meta)
    # bger_late is the 4A_524/2025 shape: decided five months ago, published today.
    assert set(_ids(rows)) == {"bger_late", "bger_new", "bger_stamp"}
    assert total == 3
    # Decided before the window: excluded, and the window is reported.
    assert "bger_ancient" not in _ids(rows)
    assert meta["publication_window_floor"] == m._publication_window_floor(YESTERDAY)


def test_date_from_filter_alone_misses_it(conn):
    """The failure the user hit: a decision-date window of 'since yesterday'."""
    rows, _ = m._list_recent(conn, "bger", None, None, YESTERDAY, None)
    assert "bger_late" not in _ids(rows)


def test_published_to_is_inclusive_of_timestamped_day(conn):
    rows, _ = m._list_recent(conn, "bger", None, None, None, None,
                             published_from=YESTERDAY, published_to=YESTERDAY)
    assert set(_ids(rows)) == {"bger_new", "bger_stamp"}


def test_explicit_date_from_replaces_the_window(conn):
    meta: dict = {}
    rows, _ = m._list_recent(conn, "bger", None, None, "2000-01-01", None,
                             published_from=TODAY, meta=meta)
    assert set(_ids(rows)) == {"bger_late", "bger_ancient"}
    assert "publication_window_floor" not in meta


def test_published_desc_orders_by_publication_then_undated_last(conn):
    rows, _ = m._list_recent(conn, "bger", None, None, None, None, sort="published_desc")
    ids = _ids(rows)
    assert ids[0] == "bger_late"              # published today
    assert ids.index("bger_new") < ids.index("bger_older")
    assert ids[-1] == "bger_nopub"            # no publication_date
    assert "bger_ancient" not in ids          # outside the decision-date window
    assert all("publication_date" in r for r in rows)


def test_date_sort_unchanged(conn):
    rows, _ = m._list_recent(conn, "bger", None, None, None, None, sort="date_desc")
    assert _ids(rows)[0] == "bger_nopub"      # newest decision_date
    assert "publication_date" not in rows[0]  # ordinary listing reads no new column


# ── public entry point ──

@pytest.fixture
def served(conn, monkeypatch):
    class _NoClose:
        def __init__(self, c):
            self._c = c

        def close(self):
            pass

        def __getattr__(self, name):
            return getattr(self._c, name)

    monkeypatch.setattr(m, "get_db", lambda: _NoClose(conn))
    m._SEARCH_RESULT_CACHE.clear()
    yield conn
    m._SEARCH_RESULT_CACHE.clear()


def test_search_results_carry_publication_date(served):
    rows, _ = m.search_fts5(query="", court="bger", sort="date_desc", limit=10)
    by_id = {r["decision_id"]: r for r in rows}
    assert by_id["bger_late"]["publication_date"] == TODAY
    assert by_id["bger_nopub"]["publication_date"] is None


def test_search_fts5_publication_args_reach_the_filter(served):
    rows, _ = m.search_fts5(query="", court="bger", published_from=TODAY,
                            sort="published_desc", limit=10)
    assert _ids(rows) == ["bger_late"]


def test_cache_key_distinguishes_publication_window(served):
    a, _ = m.search_fts5(query="", court="bger", published_from=TODAY, limit=10)
    b, _ = m.search_fts5(query="", court="bger", published_from=YESTERDAY, limit=10)
    assert len(a) == 1 and len(b) == 3


# ── full-text path ──

def _wire_fts(monkeypatch):
    monkeypatch.setattr(m, "_analyze_query",
                        lambda q, d, **kw: ([{"query": "Arbeitsvertrag", "name": "nl_and", "weight": 1.0}],
                                            [], {}, "empty"))
    monkeypatch.setattr(m, "_load_graph_signal_map", lambda *a, **k: {})
    monkeypatch.setattr(m, "_search_vectors_chunks", lambda *a, **k: {})
    monkeypatch.setattr(m, "_search_vectors", lambda *a, **k: {})
    monkeypatch.setattr(m, "_search_sparse", lambda *a, **k: {})
    monkeypatch.setattr(m, "_search_statute_graph", lambda *a, **k: [])
    monkeypatch.setattr(m, "_past_deadline", lambda d: False)
    monkeypatch.setattr(m, "_apply_llm_rerank",
                        lambda *a, **k: pytest.fail("LLM rerank must not run under published_desc"))


def test_fts_query_with_published_filter_and_sort(conn, monkeypatch):
    _wire_fts(monkeypatch)
    meta: dict = {}
    rows, _ = m._search_fts5_inner(
        conn, "Arbeitsvertrag", "bger", None, None, None, None, None, None, None, 10,
        sort="published_desc", meta=meta, published_from=_ago(30))
    ids = _ids(rows)
    assert set(ids) == {"bger_late", "bger_new", "bger_stamp", "bger_older"}
    assert ids[0] == "bger_late"
    assert ids[-1] == "bger_older"
    assert meta["publication_window_floor"] == m._publication_window_floor(_ago(30))


# ── Atom feed ──

def test_atom_rows_rank_late_publication_first(conn):
    ids = _ids(m._atom_feed_rows(conn, "bger"))
    assert ids[0] == "bger_late"
    # Undated rows rank by decision_date: bger_nopub (4 days) sits between
    # yesterday's publications and the one from 26 days ago.
    assert ids.index("bger_nopub") < ids.index("bger_older")
    # Topped up from before the window, so a sparse court still gets a feed.
    assert ids[-1] == "bger_ancient"


def test_atom_rows_respect_limit(conn):
    assert len(m._atom_feed_rows(conn, "bger", limit=2)) == 2


def test_atom_entry_dated_by_publication():
    body, _, _ = exports.render_atom_feed(
        court="bger", court_label="Bundesgericht",
        decisions=[{"decision_id": "bger_late", "decision_date": "2026-05-11",
                    "publication_date": "2026-10-16", "citation_string_de": "x"},
                   {"decision_id": "bger_nopub", "decision_date": "2026-10-12",
                    "publication_date": None, "citation_string_de": "y"}])
    text = body.decode()
    assert "<published>2026-10-16T00:00:00Z</published>" in text
    assert "<published>2026-10-12T00:00:00Z</published>" in text
    assert "2026-05-11" not in text


# ── tool surface ──

def test_tool_schema_exposes_publication_params():
    [t] = [t for t in m._list_tools() if t.name == "search_decisions"]
    props = t.inputSchema["properties"]
    assert "published_from" in props and "published_to" in props
    assert "published_desc" in props["sort"]["enum"]


def test_get_decisions_shows_publication_date(monkeypatch):
    dec = {"decision_id": "bger_late", "court": "bger", "canton": "CH",
           "docket_number": "4A_524/2025", "decision_date": "2026-05-11",
           "publication_date": "2026-10-16", "language": "fr",
           "title": "contrat de travail", "regeste": None, "full_text": "x"}
    monkeypatch.setattr(m, "get_decision_by_id", lambda x: dict(dec))
    out = m._handle_get_decisions(decision_ids=["bger_late"])
    assert "date: 2026-05-11 · published: 2026-10-16" in out


def test_one_lookup_adds_publication_date_and_bge_flag(served):
    served.execute("ALTER TABLE decisions ADD COLUMN marked_for_publication INTEGER")
    served.execute("UPDATE decisions SET marked_for_publication = 1 WHERE decision_id = 'bger_late'")
    rows, _ = m.search_fts5(query="", court="bger", published_from=TODAY, limit=10)
    [r] = rows
    assert r["publication_date"] == TODAY
    assert r["marked_for_publication"] is True


# ── with the (court, publication_date) index (db_schema.py) ──

@pytest.fixture
def indexed(conn):
    conn.execute(f"CREATE INDEX {m.PUBLICATION_INDEX} ON decisions(court, publication_date)")
    return conn


def test_index_backed_court_query_has_no_window(indexed):
    meta: dict = {}
    rows, total = m._list_recent(indexed, "bger", None, None, None, None,
                                 published_from=TODAY, meta=meta)
    assert set(_ids(rows)) == {"bger_late", "bger_ancient"}
    assert total == 2
    assert "publication_window_floor" not in meta


def test_index_backed_query_plan_walks_the_index(indexed):
    plan = " ".join(r[3] for r in indexed.execute(
        f"EXPLAIN QUERY PLAN SELECT decision_id FROM decisions INDEXED BY {m.PUBLICATION_INDEX} "
        "WHERE court = ? AND publication_date >= ? ORDER BY publication_date DESC",
        ("bger", TODAY)))
    assert m.PUBLICATION_INDEX in plan and "SCAN decisions" not in plan


def test_index_backed_published_desc_order(indexed):
    rows, _ = m._list_recent(indexed, "bger", None, None, None, None, sort="published_desc")
    ids = _ids(rows)
    assert ids[:2] == sorted(["bger_late", "bger_ancient"], reverse=True)  # both published today
    assert ids[-1] == "bger_nopub"


def test_no_court_keeps_the_window_even_with_index(indexed):
    meta: dict = {}
    rows, _ = m._list_recent(indexed, None, None, None, None, None,
                             published_from=TODAY, meta=meta)
    assert set(_ids(rows)) == {"bger_late", "bvger_x"}
    assert meta.get("publication_window_floor")


def test_index_backed_fts_court_query_has_no_window(indexed, monkeypatch):
    _wire_fts(monkeypatch)
    meta: dict = {}
    rows, _ = m._search_fts5_inner(
        indexed, "Arbeitsvertrag", "bger", None, None, None, None, None, None, None, 10,
        sort="published_desc", meta=meta, published_from=TODAY)
    assert set(_ids(rows)) == {"bger_late", "bger_ancient"}
    assert "publication_window_floor" not in meta


@pytest.mark.parametrize("limit", [1, 2, 3, 4, 6, 50])
def test_indexed_atom_matches_the_plain_ordering(indexed, limit):
    """The two-read merge must equal a straight sort by effective date."""
    want = [r["decision_id"] for r in indexed.execute(
        "SELECT decision_id FROM decisions WHERE court = 'bger' "
        "ORDER BY COALESCE(NULLIF(substr(publication_date, 1, 10), ''), decision_date) DESC, "
        "decision_date DESC, decision_id DESC LIMIT ?", (limit,))]
    assert _ids(m._atom_feed_rows(indexed, "bger", limit=limit)) == want


@pytest.mark.parametrize("limit", [1, 2, 3, 4, 6, 50])
def test_windowed_atom_matches_the_plain_ordering(conn, limit):
    """Same contract without the index (every fixture row is in the window but
    bger_ancient, which the top-up appends last)."""
    want = [r["decision_id"] for r in conn.execute(
        "SELECT decision_id FROM decisions WHERE court = 'bger' AND decision_id <> 'bger_ancient' "
        "ORDER BY COALESCE(NULLIF(substr(publication_date, 1, 10), ''), decision_date) DESC, "
        "decision_date DESC, decision_id DESC")]
    want = (want + ["bger_ancient"])[:limit]
    assert _ids(m._atom_feed_rows(conn, "bger", limit=limit)) == want
