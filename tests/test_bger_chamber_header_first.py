"""BGer chamber = the division the ruling's own header names (suggestion 0003).

bger_4A_594_2026 was served as "II. Zivilrechtliche Abteilung". Its header
says "I Corte di diritto civile"; the reasons discuss a recusal request
against the President of the II Corte di diritto civile and a related case
pending there. The text scan tested division names longest-first across the
WHOLE document, so the longer "II Corte di diritto civile" (found in the
reasons) beat the header. Now the earliest division in the header window
wins, the entscheidsuche Kopfzeile is skipped, and the build-time backstop
re-derives stored values that contradict both their docket series and their
own header.

Two map errors surfaced by the same audit are pinned here too: 8C/9C had
their post-2023 names swapped (8C is the IV., 9C the III. öffentlich-
rechtliche Abteilung — the judgment headers say so), and the prefix fallback
stamped today's divisions onto dotted pre-2007 dockets and pre-2023 rows.

All offline: the 4A_594/2026 text is a fixture (official decision text,
captured from the public API on 2026-11-06).
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import date
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import backfill_bger_chambers as bbc  # noqa: E402
from scrapers.bger import (  # noqa: E402
    HEADER_WINDOW,
    PREFIX_TO_ABTEILUNG,
    BgerScraper,
    chamber_from_text,
    division_for_docket,
    division_key,
    docket_division_key,
)

FIXTURE = REPO / "tests" / "fixtures" / "bger_4A_594_2026_fulltext_20261106.txt"
TEXT_4A_594 = FIXTURE.read_text(encoding="utf-8")

I_ZIVIL = "I. Zivilrechtliche Abteilung"
II_ZIVIL = "II. Zivilrechtliche Abteilung"


# ── the reported decision ──────────────────────────────────────────

def test_fixture_is_the_reported_shape():
    """Guard the fixture itself: header names the I., the reasons the II."""
    head = TEXT_4A_594[:300]
    assert "4A_594/2026" in head and "I Corte di diritto civile" in head
    assert TEXT_4A_594.count("II Corte di diritto civile") >= 3


def test_4a_594_2026_reads_its_own_header():
    assert chamber_from_text(TEXT_4A_594) == I_ZIVIL


def test_longer_name_later_in_the_reasons_does_not_win():
    """The general shape: a short header name, a longer name further down."""
    text = ("Urteil vom 1. März 2026\n\nI. zivilrechtliche Abteilung\n\n"
            "Besetzung …\n\nDas Gesuch um Ausstand des Präsidenten der "
            "II. zivilrechtlichen Abteilung … vor der II. Zivilrechtliche "
            "Abteilung hängig …")
    assert chamber_from_text(text) == I_ZIVIL


def test_a_division_named_only_past_the_header_window_is_ignored():
    """Past the Rubrum a division name is a mention, not a statement;
    the caller falls back to the docket prefix."""
    text = "Sentenza del 26 ottobre 2026\n" + "x " * HEADER_WINDOW + \
        "la II Corte di diritto civile"
    assert chamber_from_text(text) is None


# ── entscheidsuche Kopfzeile ───────────────────────────────────────

ES_9C_10_2023 = (
    "Bundesgericht II. öffentlich-rechtliche Abteilung 26.07.2023 9C 10/2023 (9C_10/2023)\n"
    "Tribunal fédéral IIe Cour de droit public 26.07.2023 9C 10/2023 (9C_10/2023)\n"
    "Tribunale federale II Corte di diritto pubblico 26.07.2023 9C 10/2023 (9C_10/2023)\n"
    "\nStaats- und Gemeindesteuern des Kantons St. Gallen, Steuerperiode 2014\n"
    "Öffentliche Finanzen & Abgaberecht\n\nBundesgericht\nTribunal fédéral\n"
    "Tribunale federale\nTribunal federal\n9C_10/2023\nUrteil vom 26. Juli 2023\n"
    "III. öffentlich-rechtliche Abteilung\nBesetzung\n"
)


def test_es_kopfzeile_is_skipped_for_the_courts_own_rubrum():
    """entscheidsuche's synthetic first lines carry its own (wrong) division
    names; 9C_10/2023 was decided by the III. öffentlich-rechtliche
    Abteilung, as the Rubrum below the Kopfzeile says."""
    assert chamber_from_text(ES_9C_10_2023) == "III. Öffentlich-rechtliche Abteilung"


def test_kopfzeile_skip_needs_the_date_shape():
    """A real header that merely starts with 'Bundesgericht' is not eaten."""
    text = "Bundesgericht\n\nTribunal fédéral\n\n4A_1/2026\n\nI. zivilrechtliche Abteilung\n"
    assert chamber_from_text(text) == I_ZIVIL


# ── header spellings ───────────────────────────────────────────────

@pytest.mark.parametrize("header,expected", [
    ("II.Strafrechtliche Abteilung", "II. Strafrechtliche Abteilung"),   # 7B_181/2022
    ("II zivilrechtliche Abteilung", II_ZIVIL),                          # 5A_177/2009
    ("I. sozialrechtliche Abteilung", "I. sozialrechtliche Abteilung"),  # 8C pre-2023
    ("IIe Cour de droit social", "II. sozialrechtliche Abteilung"),
    ("Strafrechtliche Abteilung", "Strafrechtliche Abteilung"),          # 6B pre-2023
    ("IV. öffentlich-rechtliche Abteilung", "IV. Öffentlich-rechtliche Abteilung"),
])
def test_header_spellings(header, expected):
    assert chamber_from_text(f"Urteil vom 3. Mai 2015\n\n{header}\n\nBesetzung") == expected


def test_pre_2023_header_beats_a_lower_instance_in_the_parties_block():
    """8C_572/2012 shape: without the pre-2023 names the header matched
    nothing and the Obergericht's Verwaltungskommission won."""
    text = ("8C_572/2012\n\nUrteil vom 11. Januar 2013\n\n"
            "I. sozialrechtliche Abteilung\n\nBesetzung …\n\ngegen\n\n"
            "Verwaltungskommission des Obergerichts des Kantons Zürich")
    assert chamber_from_text(text) == "I. sozialrechtliche Abteilung"


# ── the prefix map and its era ─────────────────────────────────────

def test_social_insurance_series_carry_their_post_2023_names():
    """8C_467/2022 of 3 Jan 2023: 'IV. öffentlich-rechtliche Abteilung';
    9C_595/2022 of 4 Jan 2023: 'III.'. The numerals cross."""
    assert PREFIX_TO_ABTEILUNG["8C"][1]["de"] == "IV. Öffentlich-rechtliche Abteilung"
    assert PREFIX_TO_ABTEILUNG["9C"][1]["de"] == "III. Öffentlich-rechtliche Abteilung"
    assert PREFIX_TO_ABTEILUNG["8C"][0] == "CH_BGer_008"
    assert PREFIX_TO_ABTEILUNG["9C"][0] == "CH_BGer_009"


@pytest.mark.parametrize("docket,day,expected", [
    ("8C_1/2022", date(2022, 12, 31), "I. sozialrechtliche Abteilung"),
    ("8C_467/2022", date(2023, 1, 3), "IV. Öffentlich-rechtliche Abteilung"),
    ("9C_1/2022", date(2022, 12, 31), "II. sozialrechtliche Abteilung"),
    ("9C_595/2022", date(2023, 1, 4), "III. Öffentlich-rechtliche Abteilung"),
    ("6B_1/2023", date(2023, 6, 30), "Strafrechtliche Abteilung"),
    ("6B_2/2023", date(2023, 7, 1), "I. Strafrechtliche Abteilung"),
    ("4A_594/2026", date(2026, 10, 26), I_ZIVIL),
    ("4A_1/2010", date(2010, 1, 1), I_ZIVIL),
    ("7B 1008/2023", date(2024, 1, 12), "II. Strafrechtliche Abteilung"),
    ("12T_1/2020", date(2020, 1, 1), "Verwaltungskommission"),
    ("8C_1/2015", None, "IV. Öffentlich-rechtliche Abteilung"),       # no date: today's
])
def test_division_for_docket_is_period_correct(docket, day, expected):
    assert division_for_docket(docket, day) == expected


@pytest.mark.parametrize("docket", [
    "7B.64/2000",    # Schuldbetreibungs- und Konkurskammer, not II. strafrechtliche
    "8G.12/2002",    # Anklagekammer
    "6S.1/2005",     # Kassationshof
    "U_49/1998",     # EVG
    "",
])
def test_no_todays_division_for_pre_bgg_dockets(docket):
    assert division_for_docket(docket, date(2005, 1, 1)) is None
    assert docket_division_key(docket) is None


def test_prefix_fallback_keeps_the_label_language():
    assert division_for_docket("4A_594/2026", date(2026, 10, 26), "it") == \
        "I Corte di diritto civile"


@pytest.mark.parametrize("label,key", [
    ("II. Zivilrechtliche Abteilung", "CH_BGer_005"),
    ("I. zivilrechtliche Abteilung", "CH_BGer_004"),
    ("Ire Cour de droit civil", "CH_BGer_004"),
    ("I. sozialrechtliche Abteilung", "CH_BGer_008"),
    ("IV. Öffentlich-rechtliche Abteilung", "CH_BGer_008"),
    ("Ie Cour de droit social", "CH_BGer_008"),
    ("Strafrechtliche Abteilung", "CH_BGer_006"),
    ("Beschwerdekammer des Bundesstrafgerichts", None),
    ("I. und II. sozialrechtliche Abteilungen", None),
    ("4A", None),
])
def test_division_key_reads_any_era_and_language(label, key):
    assert division_key(label) == key


# ── the scraper path ───────────────────────────────────────────────

def _page(decision_text: str) -> str:
    """AZA page shape: ~1,100 characters of navigation before the ruling."""
    chrome = "".join(f"<li>Rechtsprechung (gratis) {i}</li>" for i in range(40))
    body = decision_text.replace("&", "&amp;").replace("<", "&lt;")
    return (f"<html><body><ul>{chrome}</ul><div id='highlight_content'>"
            f"<div class='content'><pre>{body}</pre></div></div></body></html>")


def test_scraper_files_4a_594_under_the_first_civil_division():
    sc = BgerScraper.__new__(BgerScraper)
    stub = {"docket_number": "4A_594/2026", "decision_date": "26.10.2026",
            "vkammer": II_ZIVIL}   # even a wrong listing value loses to the header
    dec = sc._parse_decision_html(_page(TEXT_4A_594), stub, "https://x")
    assert dec is not None
    assert dec.chamber == I_ZIVIL


def test_scraper_falls_back_to_the_period_correct_series(caplog):
    sc = BgerScraper.__new__(BgerScraper)
    text = ("Bundesgericht\n\n8C_572/2012\n\nUrteil vom 11. Januar 2013\n\n"
            "Besetzung …\n\n" + "Die Beschwerde wird abgewiesen. " * 10)
    stub = {"docket_number": "8C_572/2012", "decision_date": "11.01.2013"}
    dec = sc._parse_decision_html(_page(text), stub, "https://x")
    assert dec.chamber == "I. sozialrechtliche Abteilung"


def test_scraper_warns_when_the_header_contradicts_the_series(caplog):
    sc = BgerScraper.__new__(BgerScraper)
    text = ("Bundesgericht\n\n2C_313/2015\n\nArrêt du 1er mai 2015\n\n"
            "IIe Cour de droit civil\n\nComposition …\n\n" + "rejeté. " * 30)
    stub = {"docket_number": "2C_313/2015", "decision_date": "01.05.2015"}
    with caplog.at_level("WARNING"):
        dec = sc._parse_decision_html(_page(text), stub, "https://x")
    assert dec.chamber == II_ZIVIL          # the header is kept …
    assert "docket series belongs to" in caplog.text   # … but flagged


# ── the build-time backstop ────────────────────────────────────────

def _db(rows):
    con = sqlite3.connect(":memory:")
    con.execute("""CREATE TABLE decisions (decision_id TEXT PRIMARY KEY,
        court TEXT, docket_number TEXT, chamber TEXT, full_text TEXT,
        decision_date TEXT)""")
    con.executemany("INSERT INTO decisions VALUES (?,?,?,?,?,?)", rows)
    con.commit()
    return con


def _chamber(con, did):
    return con.execute("SELECT chamber FROM decisions WHERE decision_id=?",
                       (did,)).fetchone()[0]


def test_backfill_corrects_4a_594_2026():
    con = _db([("bger_4A_594_2026", "bger", "4A_594/2026", II_ZIVIL,
                TEXT_4A_594, "2026-10-26")])
    assert bbc.apply_to_db(con) == (0, 1, 0)
    assert _chamber(con, "bger_4A_594_2026") == I_ZIVIL


def test_backfill_keeps_a_value_the_header_states():
    """6B_819/2010: 'II. sozialrechtliche Abteilung (als strafrechtliche
    Abteilung)'. Not the series' division, but the court's own statement."""
    text = ("6B_819/2010\n\nUrteil vom 3. Mai 2011\n\nII. sozialrechtliche "
            "Abteilung\n\n(als strafrechtliche Abteilung)\n\nBesetzung")
    con = _db([("a", "bger", "6B_819/2010", "II. sozialrechtliche Abteilung",
                text, "2011-05-03")])
    assert bbc.apply_to_db(con) == (0, 0, 0)
    assert _chamber(con, "a") == "II. sozialrechtliche Abteilung"


def test_backfill_uses_the_series_when_the_header_is_silent():
    con = _db([("a", "bger", "8C_199/2014", "Verwaltungskommission",
                "Urteil vom 5. September 2014\n\nBesetzung …", "2014-09-05")])
    assert bbc.apply_to_db(con) == (0, 1, 0)
    assert _chamber(con, "a") == "I. sozialrechtliche Abteilung"


def test_backfill_rederives_es_rows_past_the_kopfzeile():
    """es rows arrive with the docket code as chamber (rule 2); their
    re-derivation must read the Rubrum, not entscheidsuche's Kopfzeile."""
    con = _db([("bger_9C_10_2023", "bger", "9C 10/2023", "9C",
                ES_9C_10_2023, "2023-07-26")])
    assert bbc.apply_to_db(con) == (0, 1, 0)
    assert _chamber(con, "bger_9C_10_2023") == "III. Öffentlich-rechtliche Abteilung"


def test_backfill_leaves_same_division_other_era_names_alone():
    """No churn: a pre-2023 name on a pre-2023 row, today's name on an old
    row, a French label — all the series' own division."""
    con = _db([
        ("a", "bger", "8C_1/2015", "I. sozialrechtliche Abteilung", "", "2015-01-01"),
        ("b", "bger", "6B_1/2015", "I. Strafrechtliche Abteilung", "", "2015-01-01"),
        ("c", "bger", "4A_1/2015", "Ire Cour de droit civil", "", "2015-01-01"),
        ("d", "bger", "4P.253/1999", "I. zivilrechtliche Abteilung", "", "2000-01-04"),
        ("e", "bger", "7B.64/2000", "Schuldbetreibungs- und Konkurskammer", "", "2000-03-01"),
    ])
    assert bbc.apply_to_db(con) == (0, 0, 0)


def test_backfill_never_touches_pre_bgg_dockets_by_series():
    """Dotted dockets have no series rule: a wrong-looking label stays."""
    con = _db([("a", "bger", "5P.1/2004", "I. Öffentlich-rechtliche Abteilung",
                "", "2004-01-01")])
    assert bbc.apply_to_db(con) == (0, 0, 0)


# ── the census ─────────────────────────────────────────────────────

def test_audit_counts_wrong_and_null_and_respects_the_header():
    import audit_bger_chambers as abc

    con = _db([
        ("w", "bger", "4A_594/2026", II_ZIVIL, TEXT_4A_594, "2026-10-26"),
        ("n", "bger", "8C_1/2015", None, "", "2015-01-01"),
        ("h", "bger", "6B_819/2010", "II. sozialrechtliche Abteilung",
         ("Urteil\n\nII. sozialrechtliche Abteilung\n\n(als strafrechtliche "
          "Abteilung)"), "2011-05-03"),
        ("ok", "bger", "4A_1/2026", I_ZIVIL, "", "2026-01-01"),
        ("old", "bger", "7B.64/2000", "Schuldbetreibungs- und Konkurskammer", "", "2000-01-01"),
    ])
    total, nulls, wrong = abc.audit(con)
    assert sum(total.values()) == 4          # the dotted docket is out of scope
    assert sum(nulls.values()) == 1
    assert [r["decision_id"] for r in wrong] == ["w"]
    bbc.apply_to_db(con)
    _total, nulls, wrong = abc.audit(con)
    assert wrong == [] and sum(nulls.values()) == 0
