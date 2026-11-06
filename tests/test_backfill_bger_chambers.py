"""GitHub #57, data half: the corpus keeps wrong chambers until re-derived.

343dd6c fixed the scraper-side derivation; this backfill (wired into
build_fts5 as a defensive phase) corrects what is already stored. The rules
it must respect are subtle enough to pin individually:

  - the 7B docket prefix was REUSED: '7B.64/2000' (dot) is the historic
    Schuldbetreibungs- und Konkurskammer and is correct as stored;
    '7B_311/2023' / '7B 1008/2023' are the post-2023 criminal-procedure
    series and belong to the II. strafrechtliche Abteilung;
  - for every other prefix only provably broken values are touched, and the
    replacement comes from the decision's own text (period-correct — the
    2023 reorganisation also RENAMED divisions, so today's prefix map must
    not be stamped onto 2010 rows);
  - a broken value with no derivable replacement becomes NULL, not a guess.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import backfill_bger_chambers as bbc  # noqa: E402
from scrapers.bger import ABTEILUNG_MAP, PREFIX_TO_ABTEILUNG  # noqa: E402

II_STRAF = PREFIX_TO_ABTEILUNG["7B"][1]["de"]
# any real division name, for period-correct re-derivation fixtures
_SOME_SIG, _SOME_INFO = next(
    (s, i) for s, i in ABTEILUNG_MAP.items() if i["de"] != II_STRAF)
DIV_DE = _SOME_INFO["de"]

FOREIGN = "Beschwerdekammer des Bundesstrafgerichts"


def _db(rows):
    con = sqlite3.connect(":memory:")
    con.execute("""CREATE TABLE decisions (decision_id TEXT PRIMARY KEY,
        court TEXT, docket_number TEXT, chamber TEXT, full_text TEXT,
        decision_date TEXT)""")
    con.executemany("INSERT INTO decisions (decision_id, court, docket_number,"
                    " chamber, full_text) VALUES (?,?,?,?,?)", rows)
    con.commit()
    return con


def _chamber(con, did):
    return con.execute(
        "SELECT chamber FROM decisions WHERE decision_id=?", (did,)).fetchone()[0]


def test_new_series_7bf_forced_old_dotted_series_untouched():
    con = _db([
        ("a", "bger", "7B_311/2023", "I. Strafrechtliche Abteilung", ""),
        ("b", "bger", "7B 1008/2023", "7B", ""),
        ("c", "bger", "7F_9/2024", "I. Strafrechtliche Abteilung", ""),
        ("d", "bger", "7B.64/2000", "Schuldbetreibungs- und Konkurskammer", ""),
    ])
    n1, n2, n3 = bbc.apply_to_db(con)
    assert n1 == 3 and n2 == 0 and n3 == 0
    assert _chamber(con, "a") == II_STRAF
    assert _chamber(con, "b") == II_STRAF
    assert _chamber(con, "c") == II_STRAF
    assert _chamber(con, "d") == "Schuldbetreibungs- und Konkurskammer"


def test_plausible_values_on_other_prefixes_are_never_touched():
    con = _db([
        ("a", "bger", "6B_100/2020", "I. Strafrechtliche Abteilung",
         f"text mentioning {FOREIGN} in passing"),
    ])
    n1, n2, n3 = bbc.apply_to_db(con)
    assert (n1, n2, n3) == (0, 0, 0)
    assert _chamber(con, "a") == "I. Strafrechtliche Abteilung"


def test_foreign_court_value_rederived_from_own_text():
    con = _db([
        ("a", "bger", "1C_500/2015", FOREIGN,
         f"Urteil der {DIV_DE} vom 1. Januar 2015 ..."),
    ])
    n1, n2, n3 = bbc.apply_to_db(con)
    assert (n1, n2, n3) == (0, 1, 0)
    assert _chamber(con, "a") == DIV_DE


def test_foreign_court_value_with_no_derivable_text_becomes_null():
    con = _db([
        ("a", "bger", "1S.23/2005", FOREIGN, "kein Abteilungsname im Text"),
    ])
    n1, n2, n3 = bbc.apply_to_db(con)
    assert (n1, n2, n3) == (0, 0, 1)
    assert _chamber(con, "a") is None


def test_bare_prefix_and_null_chambers_rederive():
    con = _db([
        ("a", "bger", "1C_1/2019", "1C", f"Urteil der {DIV_DE} ..."),
        ("b", "bger", "1C_2/2019", None, f"Urteil der {DIV_DE} ..."),
        # A BGG-era docket series belongs to exactly one division, so a
        # silent text no longer leaves a gap: the series names it (2026-11,
        # suggestion 0003; see tests/test_bger_chamber_header_first.py).
        ("c", "bger", "1C_3/2019", None, "nichts"),
        ("d", "bger", "1A.3/2005", None, "nichts"),   # pre-BGG: NULL stays NULL
    ])
    n1, n2, n3 = bbc.apply_to_db(con)
    assert (n1, n2, n3) == (0, 3, 0)
    assert _chamber(con, "a") == DIV_DE
    assert _chamber(con, "b") == DIV_DE
    assert _chamber(con, "c") == DIV_DE
    assert _chamber(con, "d") is None


def test_other_courts_are_out_of_scope():
    con = _db([
        ("a", "zh_obergericht", "7B_1/2023", "II. Zivilkammer", ""),
    ])
    assert bbc.apply_to_db(con) == (0, 0, 0)
    assert _chamber(con, "a") == "II. Zivilkammer"


def test_dry_run_changes_nothing():
    con = _db([
        ("a", "bger", "7B_311/2023", "I. Strafrechtliche Abteilung", ""),
        ("b", "bger", "1C_500/2015", FOREIGN, f"Urteil der {DIV_DE} ..."),
    ])
    n1, n2, n3 = bbc.apply_to_db(con, dry_run=True)
    assert n1 == 1 and n2 == 1
    assert _chamber(con, "a") == "I. Strafrechtliche Abteilung"
    assert _chamber(con, "b") == FOREIGN
