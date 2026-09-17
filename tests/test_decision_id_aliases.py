"""decision_id_aliases: an id a row carried before a re-key keeps resolving to
that exact decision (BS Gerichte case number -> decision number, 2026-09-17).

Build side: insert_decision records the shard row's previous_decision_id;
dedup keys BS rows on the decision number so two decisions under one case
number survive. Serve side: get_decision_by_id, both resolvers and the
decision page resolve the old id; an alias whose target is gone falls through.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import build_fts5  # noqa: E402
import mcp_server as m  # noqa: E402
import seo_pages  # noqa: E402
from db_schema import SCHEMA_SQL  # noqa: E402

OLD = "bs_appellationsgericht_SB.2013.5"
NEW = "bs_appellationsgericht_AG.2020.102"
SIBLING = "bs_appellationsgericht_AG.2014.40"


def _row(decision_id, docket, docket2, date, text, previous=None):
    r = {
        "decision_id": decision_id, "court": "bs_appellationsgericht", "canton": "BS",
        "docket_number": docket, "docket_number_2": docket2, "decision_date": date,
        "language": "de", "title": f"Title {docket2}", "regeste": None,
        "full_text": text, "source_url": "https://rechtsprechung.gerichte.bs.ch/x",
    }
    if previous:
        r["previous_decision_id"] = previous
    return r


def _build(path):
    c = sqlite3.connect(str(path))
    c.executescript(SCHEMA_SQL)
    # the re-keyed row (previously stored under the case number) ...
    assert build_fts5.insert_decision(c, _row(NEW, "SB.2013.5", "AG.2020.102", "2020-01-20",
                                              "Gesuch um Erlass der Verfahrenskosten " * 5, previous=OLD))
    # ... and its sibling under the same case number, fetched fresh
    assert build_fts5.insert_decision(c, _row(SIBLING, "SB.2013.5", "AG.2014.40", "2014-02-10",
                                              "versuchter Mord, mehrfache Drohung " * 5))
    c.commit()
    return c


def _serve(tmp_path, monkeypatch):
    dbp = tmp_path / "decisions.db"
    _build(dbp).close()

    def _conn():
        c = sqlite3.connect(str(dbp))
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setattr(m, "get_db", _conn)
    monkeypatch.setattr(m, "_canonical_warned", False, raising=False)
    monkeypatch.setattr(m, "CANONICAL_DB_PATH", Path(tmp_path / "missing.db"))
    monkeypatch.setattr(seo_pages, "_get_db", _conn)
    return dbp


# ── build ───────────────────────────────────────────────────────────────


def test_insert_records_previous_id(tmp_path):
    c = _build(tmp_path / "d.db")
    assert c.execute("SELECT decision_id, source FROM decision_id_aliases WHERE previous_id = ?",
                     (OLD,)).fetchone() == (NEW, "rekey")
    assert c.execute("SELECT COUNT(*) FROM decision_id_aliases").fetchone()[0] == 1
    # the two decisions under one case number both survive the dedup passes
    assert build_fts5._dedup_decisions(c) == 0
    assert build_fts5._cross_court_dedup(c) == 0
    assert c.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 2
    keys = {r[0] for r in c.execute("SELECT canonical_key FROM decisions")}
    assert len(keys) == 2


def test_same_day_siblings_are_not_a_duplicate(tmp_path):
    c = sqlite3.connect(str(tmp_path / "d.db"))
    c.executescript(SCHEMA_SQL)
    assert build_fts5.insert_decision(c, _row(NEW, "SB.2013.5", "AG.2020.102", "2020-01-20", "a " * 50))
    assert build_fts5.insert_decision(c, _row(SIBLING, "SB.2013.5", "AG.2014.40", "2020-01-20", "b " * 50))
    assert build_fts5._dedup_decisions(c) == 0
    assert build_fts5._cross_court_dedup(c) == 0


def test_es_leftover_with_the_same_decision_number_is_one_decision(tmp_path):
    c = sqlite3.connect(str(tmp_path / "d.db"))
    c.executescript(SCHEMA_SQL)
    assert build_fts5.insert_decision(c, _row(SIBLING, "SB.2013.5", "AG.2014.40", "2014-02-10", "a " * 50))
    es = _row("bs_gerichte_SB.2013.5_BS_APG_001", "SB.2013.5", "AG.2014.40", "2014-02-10", "a " * 20)
    es["court"] = "bs_gerichte"
    assert build_fts5.insert_decision(c, es)
    assert build_fts5._cross_court_dedup(c) == 1
    assert c.execute("SELECT decision_id FROM decisions").fetchall() == [(SIBLING,)]


def test_insert_without_alias_table_still_works(tmp_path):
    c = sqlite3.connect(str(tmp_path / "d.db"))
    c.executescript("CREATE TABLE decisions (%s);" % ", ".join(
        f"{col} TEXT" for col in __import__("db_schema").INSERT_COLUMNS))
    assert build_fts5.insert_decision(c, _row(NEW, "SB.2013.5", "AG.2020.102", "2020-01-20", "a " * 50, previous=OLD))


# ── serve ───────────────────────────────────────────────────────────────


def test_get_decision_by_old_id(tmp_path, monkeypatch):
    _serve(tmp_path, monkeypatch)
    r = m.get_decision_by_id(OLD)
    assert r and r["decision_id"] == NEW
    assert r["resolved_via"] == "previous_decision_id"
    assert r["queried_id"] == OLD
    assert m.get_decision_by_id(NEW)["decision_id"] == NEW
    assert "resolved_via" not in m.get_decision_by_id(NEW)


def test_both_resolvers_map_the_old_id(tmp_path, monkeypatch):
    _serve(tmp_path, monkeypatch)
    assert m._resolve_decision_id(OLD) == NEW
    assert m._resolve_decision_id_strict(OLD) == NEW
    # the case number itself still resolves (to one of its decisions, newest first)
    assert m._resolve_decision_id_strict("SB.2013.5") == NEW


def test_decision_page_redirects_old_id(tmp_path, monkeypatch):
    _serve(tmp_path, monkeypatch)
    html, status, location = seo_pages.render_decision_page(OLD)
    assert status == 301
    assert location == "/entscheid/" + NEW
    assert seo_pages.render_decision_page(NEW)[1] == 200
    # the bare case number is ambiguous (two decisions) → 404, as before
    assert seo_pages.render_decision_page("SB.2013.5")[1] == 404


def test_alias_to_a_removed_row_falls_through(tmp_path, monkeypatch):
    dbp = _serve(tmp_path, monkeypatch)
    c = sqlite3.connect(str(dbp))
    c.execute("DELETE FROM decisions WHERE decision_id = ?", (NEW,))
    c.commit()
    c.close()
    assert m._resolve_decision_id_strict(OLD) is None
    assert seo_pages.render_decision_page(OLD)[1] == 404


def test_lookup_is_guarded_without_the_table(tmp_path):
    c = sqlite3.connect(str(tmp_path / "bare.db"))
    c.execute("CREATE TABLE decisions (decision_id TEXT PRIMARY KEY)")
    assert m._lookup_previous_id(c, OLD) is None
    assert m._lookup_previous_id(c, "") is None
