"""Data half of the BVGer Abteilung substring bug (scrapers/bvger.py).

The scraper fix (2026-09-17) stops new rows from being mislabelled; this
backfill would correct what is already stored. It is a PROPOSAL: not wired
into build_fts5 (pipeline gate). The rules it must respect:

  - the judgment's own header wins (period-correct: pre-2016 F-dockets were
    decided by Abteilung III/IV, today's letter map says VI);
  - the docket letter is the fallback, only for the ordinary "A-1/2025"
    series — "BVGE 2007/10" is never mapped;
  - nothing derivable -> untouched, counted as unresolved;
  - non-bvger rows are never read; dry_run writes nothing.

Pure in-memory SQLite, no network.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import backfill_bvger_chambers as bbc
from scrapers.bvger import BVGER_ABTEILUNGEN as ABT

HEADER = (
    "Bundesverwaltungsgericht\nTribunal administratif fédéral\n"
    "Tribunale amministrativo federale\nTribunal administrativ federal\n\n"
    "Abteilung {n}\n{docket}\n\nUrteil vom 1. Januar 2020\n"
)


def _db(rows):
    con = sqlite3.connect(":memory:")
    con.execute("""CREATE TABLE decisions (decision_id TEXT PRIMARY KEY,
        court TEXT, docket_number TEXT, chamber TEXT, full_text TEXT)""")
    con.executemany("INSERT INTO decisions VALUES (?,?,?,?,?)", rows)
    con.commit()
    return con


def _chamber(con, did):
    return con.execute(
        "SELECT chamber FROM decisions WHERE decision_id=?", (did,)).fetchone()[0]


def test_bug_labels_are_relabelled_from_header_or_prefix():
    con = _db([
        ("b", "bvger", "B-5477/2025", ABT["I"], HEADER.format(n="II", docket="B-5477/2025")),
        ("c", "bvger", "C-1/2025", ABT["I"], "no header numeral here"),
        ("d", "bvger", "D-1/2025", ABT["I"], HEADER.format(n="IV", docket="D-1/2025")),
        ("f", "bvger", "F-1/2025", ABT["V"], HEADER.format(n="VI", docket="F-1/2025")),
        ("a", "bvger", "A-1/2025", ABT["I"], HEADER.format(n="I", docket="A-1/2025")),
    ])
    stats = {}
    n1, n2, n3 = bbc.apply_to_db(con, stats=stats)
    assert (n1, n2, n3) == (4, 0, 0)
    assert _chamber(con, "b") == ABT["II"]
    assert _chamber(con, "c") == ABT["III"]      # prefix fallback
    assert _chamber(con, "d") == ABT["IV"]
    assert _chamber(con, "f") == ABT["VI"]
    assert _chamber(con, "a") == ABT["I"]        # already right, untouched
    assert stats["how"] == {"header": 4, "prefix": 1}


def test_header_wins_over_docket_letter_period_correct():
    # F-3336/2015 was decided by Abteilung III (spot check 2026-09-17).
    con = _db([
        ("f", "bvger", "F-3336/2015", ABT["V"], HEADER.format(n="III", docket="F-3336/2015")),
    ])
    assert bbc.apply_to_db(con) == (1, 0, 0)
    assert _chamber(con, "f") == ABT["III"]


def test_null_bare_letter_and_verbatim_panel_are_filled():
    raw = "Abt. II (Wirtschaft, Wettbewerb, Bildung);;Cour II (économie)"
    con = _db([
        ("n", "bvger", "D-2/2020", None, ""),
        ("l", "bvger", "E-2/2020", "E", ""),
        ("r", "bvger", "B-2/2020", raw, ""),
    ])
    assert bbc.apply_to_db(con) == (0, 3, 0)
    assert _chamber(con, "n") == ABT["IV"]
    assert _chamber(con, "l") == ABT["V"]
    assert _chamber(con, "r") == ABT["II"]


def test_bvge_collection_docket_uses_header_only():
    con = _db([
        ("h", "bvger", "BVGE 2007/10", ABT["I"], HEADER.format(n="V", docket="D-9/2006")),
        ("x", "bvger", "BVGE 2007/11", ABT["I"], "no header numeral"),
        ("y", "bvger", "BVGE 2007/12", None, "no header numeral"),
    ])
    stats = {}
    assert bbc.apply_to_db(con, stats=stats) == (1, 0, 1)
    assert stats["kept_canonical_no_evidence"] == 1   # row "x" survives the repair
    assert _chamber(con, "h") == ABT["V"]
    assert _chamber(con, "x") == ABT["I"]        # canonical, no evidence: kept
    assert _chamber(con, "y") is None            # unresolved, not guessed


def test_ambiguous_header_falls_back_to_prefix():
    text = HEADER.format(n="II", docket="C-3/2021") + "vgl. Urteil der Abteilung I vom ..."
    con = _db([("c", "bvger", "C-3/2021", ABT["I"], text)])
    stats = {}
    assert bbc.apply_to_db(con, stats=stats) == (1, 0, 0)
    assert _chamber(con, "c") == ABT["III"]
    assert stats["how"] == {"prefix": 1}


def test_other_courts_and_dry_run_are_untouched():
    con = _db([
        ("g", "bger", "B-1/2025", ABT["I"], HEADER.format(n="II", docket="B-1/2025")),
        ("b", "bvger", "B-1/2025", ABT["I"], HEADER.format(n="II", docket="B-1/2025")),
    ])
    assert bbc.apply_to_db(con, dry_run=True) == (1, 0, 0)
    assert _chamber(con, "g") == ABT["I"]
    assert _chamber(con, "b") == ABT["I"]
    assert bbc.apply_to_db(con) == (1, 0, 0)
    assert _chamber(con, "g") == ABT["I"]
    assert _chamber(con, "b") == ABT["II"]
