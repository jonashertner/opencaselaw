"""P1.4: invalid /entscheid/<id> URLs must not resolve to unrelated cases.

Before this fix, an unknown decision_id fell back to
``docket_number LIKE '%<id>%'`` — a nondeterministic substring match, so a
garbage id like "1" matched whichever docket happened to contain a "1"
(most of them) and the route served HTTP 200 with an unrelated decision.

Fixed behavior:
- exact decision_id match -> 200 (unchanged).
- exact (not substring) docket_number match, if unique -> 301 to the
  canonical /entscheid/<decision_id> URL, percent-encoded.
- zero or multiple exact docket matches -> 404.
- the LIKE substring fallback is gone entirely.
"""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

import seo_pages


@pytest.fixture
def fixture_decisions(monkeypatch):
    tmp = Path(tempfile.mkdtemp())
    db_path = tmp / "decisions.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE decisions (
            decision_id TEXT PRIMARY KEY,
            court TEXT, canton TEXT, docket_number TEXT,
            decision_date TEXT, language TEXT,
            title TEXT, regeste TEXT, full_text TEXT,
            source_url TEXT, pdf_url TEXT
        );
    """)
    rows = [
        # Plain case: docket "BGE 140 III 86" contains a "1" (in "140"),
        # which is exactly the kind of docket the old LIKE '%1%' fallback
        # would have wrongly matched against a garbage id like "1".
        ("bge_BGE_140_III_86", "bge", "CH", "BGE 140 III 86",
         "2014-02-05", "de", "Title A", "Regeste A", "Text A", "u", None),
        # Docket with a literal slash — must be matched exactly, not as a
        # path-splitting hazard, and the request path goes through
        # Starlette's {decision_id:path} converter which decodes %2F back
        # to '/' before this function ever sees it.
        ("bger_1B_243_2022_v1", "bger", "CH", "1B_243/2022",
         "2022-03-01", "de", "Title B", "Regeste B", "Text B", "u", None),
        # Docket with a literal space — the redirect Location must
        # percent-encode it (issue #59 class of bug applied to the
        # redirect target, not just outbound citation links).
        ("bge_152 V 60", "bge", "CH", "152 V 60",
         "2026-01-15", "de", "Title C", "Regeste C", "Text C", "u", None),
        # Ambiguous docket shared by two decisions — must 404, not pick one
        # nondeterministically.
        ("amb_a", "zh_obergericht", "ZH", "AMBIG 1/2",
         "2020-01-01", "de", "Title D1", "Regeste D1", "Text D1", "u", None),
        ("amb_b", "zh_obergericht", "ZH", "AMBIG 1/2",
         "2020-01-02", "de", "Title D2", "Regeste D2", "Text D2", "u", None),
    ]
    conn.executemany(
        "INSERT INTO decisions VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows
    )
    conn.commit()
    conn.close()

    def _fake_get_db():
        c = sqlite3.connect(str(db_path))
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setattr(seo_pages, "_get_db", _fake_get_db)
    return db_path


def test_exact_decision_id_gives_200(fixture_decisions):
    html, status, location = seo_pages.render_decision_page("bge_BGE_140_III_86")
    assert status == 200
    assert location is None
    assert "Title A" in html


def test_garbage_id_gives_404_not_substring_match(fixture_decisions):
    """The old LIKE '%1%' fallback would have matched "BGE 140 III 86"
    (contains "1") here. It must now 404."""
    html, status, location = seo_pages.render_decision_page("1")
    assert status == 404
    assert location is None


def test_unique_exact_docket_redirects_301_with_encoded_slash(fixture_decisions):
    # Simulates the value Starlette hands the view function after decoding
    # the {decision_id:path} segment — a docket containing a real '/'.
    html, status, location = seo_pages.render_decision_page("1B_243/2022")
    assert status == 301
    assert location == "/entscheid/bger_1B_243_2022_v1"


def test_unique_exact_docket_redirects_301_with_encoded_space(fixture_decisions):
    html, status, location = seo_pages.render_decision_page("152 V 60")
    assert status == 301
    # The canonical id itself has a literal space; the Location header must
    # be percent-encoded (no raw space — invalid in a Location header).
    assert location == "/entscheid/bge_152%20V%2060"
    assert " " not in location


def test_ambiguous_docket_gives_404(fixture_decisions):
    html, status, location = seo_pages.render_decision_page("AMBIG 1/2")
    assert status == 404
    assert location is None


def test_docket_substring_of_another_docket_does_not_match(fixture_decisions):
    """A partial docket string must not resolve via substring matching
    even though it used to under the LIKE fallback."""
    html, status, location = seo_pages.render_decision_page("140 III")
    assert status == 404
    assert location is None
