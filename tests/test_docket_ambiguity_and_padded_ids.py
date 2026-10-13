"""External report 2026-10-13, findings 1 and 2.

1. Ids minted from a docket that ended in a newline and spaces carry trailing
   underscores ('vd_findinfo_353___________'); the id a reader derives from the
   trimmed docket answered "not found". And a bare docket shared by several
   rulings ('353': VD 2011 and VD 2023) resolved silently to the newest one.
2. A stored decision date in the future (fr_gerichte 2026-10-26 for a ruling of
   1 September 2026) was served without any warning.
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import build_fts5  # noqa: E402
import mcp_server as m  # noqa: E402
from db_schema import SCHEMA_SQL  # noqa: E402

PADDED = "vd_findinfo_353___________"
NEWER = "vd_gerichte_1eb90e6d-34a5-4cca-bcc0-6a6f0b25d98d"
FUTURE = "fr_gerichte_604 2026 58"
# Within the build's 365-day tolerance (build_fts5._normalize_dates), as the
# reported rows were; a date further out is cleared at build time.
FUTURE_DATE = (date.today() + timedelta(days=40)).isoformat()


def _row(decision_id, court, docket, date, text, canton="VD", language="fr"):
    return {
        "decision_id": decision_id, "court": court, "canton": canton,
        "docket_number": docket, "decision_date": date, "language": language,
        "title": f"Title {decision_id}", "regeste": None, "full_text": text,
        "source_url": "https://example.invalid/" + decision_id,
    }


def _serve(tmp_path, monkeypatch):
    dbp = tmp_path / "decisions.db"
    c = sqlite3.connect(str(dbp))
    c.executescript(SCHEMA_SQL)
    for r in (
        _row(PADDED, "vd_findinfo", "353", "2011-12-27",
             "Arrêt du 27 décembre 2011, recours en matière de bail " * 5),
        _row(NEWER, "vd_gerichte", "353", "2023-09-04",
             "Arrêt du 4 septembre 2023, divorce sur demande unilatérale " * 5),
        _row("vd_gerichte_unique", "vd_gerichte", "HC/2020/77", "2020-03-02",
             "Arrêt du 2 mars 2020, mesures provisionnelles " * 5),
        _row(FUTURE, "fr_gerichte", "604 2026 58", FUTURE_DATE,
             "Arrêt du 1er septembre 2026, recours " * 5, canton="FR"),
    ):
        assert build_fts5.insert_decision(c, r)
    c.commit()
    c.close()

    def _conn():
        conn = sqlite3.connect(str(dbp))
        conn.row_factory = sqlite3.Row
        return conn

    monkeypatch.setattr(m, "get_db", _conn)
    monkeypatch.setattr(m, "_canonical_warned", False, raising=False)
    monkeypatch.setattr(m, "CANONICAL_DB_PATH", Path(tmp_path / "missing.db"))
    monkeypatch.setattr(m, "REPRESENTATION_MANIFEST_DB_PATH", Path(tmp_path / "missing_manifest.db"))
    return _conn


# ── padded ids ──────────────────────────────────────────────────────────


def test_id_without_trailing_underscores_resolves(tmp_path, monkeypatch):
    _serve(tmp_path, monkeypatch)
    d = m.get_decision_by_id("vd_findinfo_353")
    assert d["decision_id"] == PADDED
    assert d["resolved_via"] == "padded_decision_id"
    assert m._resolve_decision_id("vd_findinfo_353") == PADDED


def test_id_with_extra_trailing_underscores_resolves(tmp_path, monkeypatch):
    _serve(tmp_path, monkeypatch)
    assert m.get_decision_by_id("vd_gerichte_unique__")["decision_id"] == "vd_gerichte_unique"


def test_exact_padded_id_is_untouched(tmp_path, monkeypatch):
    _serve(tmp_path, monkeypatch)
    d = m.get_decision_by_id(PADDED)
    assert d["decision_id"] == PADDED
    assert "resolved_via" not in d
    assert "docket_ambiguous" not in d


def test_padded_lookup_never_widens_to_another_id(tmp_path, monkeypatch):
    conn = _serve(tmp_path, monkeypatch)()
    # 'vd_findinfo_35' must not reach 'vd_findinfo_353___…'
    assert m._lookup_padded_id(conn, "vd_findinfo_35") is None
    # a reference without any underscore is never treated as an id
    assert m._lookup_padded_id(conn, "353") is None


# ── shared dockets ──────────────────────────────────────────────────────


def test_bare_shared_docket_says_it_chose(tmp_path, monkeypatch):
    _serve(tmp_path, monkeypatch)
    d = m.get_decision_by_id("353")
    assert d["decision_id"] == NEWER  # long-standing choice: the newest
    assert d["docket_ambiguous"] is True
    others = d["other_decisions_with_docket"]
    assert [o["decision_id"] for o in others] == [PADDED]
    assert others[0]["decision_date"] == "2011-12-27"
    assert "353" in d["docket_ambiguity_note"]


def test_unique_docket_is_not_flagged(tmp_path, monkeypatch):
    _serve(tmp_path, monkeypatch)
    d = m.get_decision_by_id("HC/2020/77")
    assert d["decision_id"] == "vd_gerichte_unique"
    assert "docket_ambiguous" not in d


def test_cite_flags_a_shared_docket(tmp_path, monkeypatch):
    _serve(tmp_path, monkeypatch)
    r = m._handle_cite(reference="353")
    assert r["exists"] is True
    assert r["decision_id"] == NEWER
    assert r["docket_ambiguous"] is True
    assert [o["decision_id"] for o in r["other_decisions_with_docket"]] == [PADDED]


def test_cite_by_exact_id_is_not_flagged(tmp_path, monkeypatch):
    _serve(tmp_path, monkeypatch)
    r = m._handle_cite(reference=NEWER)
    assert r["exists"] is True
    assert "docket_ambiguous" not in r


# ── future dates ────────────────────────────────────────────────────────


def test_future_date_is_flagged_not_replaced(tmp_path, monkeypatch):
    _serve(tmp_path, monkeypatch)
    d = m.get_decision_by_id(FUTURE)
    assert d["decision_date"] == FUTURE_DATE
    assert d["date_is_implausible"] is True
    assert d["decision_date_warning"]["stored_date"] == FUTURE_DATE


def test_cite_warns_on_future_date(tmp_path, monkeypatch):
    _serve(tmp_path, monkeypatch)
    r = m._handle_cite(reference=FUTURE)
    assert r["decision_date_warning"]["stored_date"] == FUTURE_DATE


def test_past_and_today_dates_are_not_flagged():
    assert m._future_date_warning("2023-09-04") is None
    assert m._future_date_warning(None) is None
    today = (m.datetime.now(m.timezone.utc) + m.timedelta(hours=2)).date().isoformat()
    assert m._future_date_warning(today) is None
