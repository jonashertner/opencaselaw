"""In-build canonical date correction (audit C-2): apply_to_db replaces synthetic
YYYY-01-01 BGE dates with the text-verified Urteilsdatum on the built decisions
DB, so search/sort/filter use the real date. Only synthetic BGE dates are
touched; real dates and non-BGE rows are never changed. Exercised through the
real decisions_fts + sync triggers so the UPDATE is proven to pass them.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import backfill_canonical_identity as bci  # noqa: E402

BGE_152_HEAD = ("Urteilskopf 152 II 1 1. Auszug aus dem Urteil … 9C_113/2025 "
                "vom 27. September 2025 Regeste …")


def _db(path):
    c = sqlite3.connect(path)
    c.executescript(
        """
        CREATE TABLE decisions(decision_id TEXT PRIMARY KEY, court TEXT, canton TEXT,
            docket_number TEXT, decision_date TEXT, publication_date TEXT,
            language TEXT, title TEXT, regeste TEXT, full_text TEXT);
        CREATE VIRTUAL TABLE decisions_fts USING fts5(decision_id UNINDEXED, court,
            canton, docket_number, language, title, regeste, full_text);
        CREATE TRIGGER decisions_ai AFTER INSERT ON decisions BEGIN
          INSERT INTO decisions_fts(decision_id,court,canton,docket_number,language,title,regeste,full_text)
          VALUES(new.decision_id,new.court,new.canton,new.docket_number,new.language,new.title,new.regeste,new.full_text);
        END;
        CREATE TRIGGER decisions_ad AFTER DELETE ON decisions BEGIN
          DELETE FROM decisions_fts WHERE decision_id=old.decision_id; END;
        CREATE TRIGGER decisions_au AFTER UPDATE ON decisions BEGIN
          DELETE FROM decisions_fts WHERE decision_id=old.decision_id;
          INSERT INTO decisions_fts(decision_id,court,canton,docket_number,language,title,regeste,full_text)
          VALUES(new.decision_id,new.court,new.canton,new.docket_number,new.language,new.title,new.regeste,new.full_text);
        END;
        """
    )
    rows = [
        ("bge_152 II 1", "bge", "CH", "152 II 1", "2026-01-01", None, "de", "", "", BGE_152_HEAD),
        ("bge_old", "bge", "CH", "100 II 5", "1974-01-01", None, "de", "", "", "no parseable date here"),
        ("bger_real", "bger", "CH", "9C_113/2025", "2025-09-27", None, "de", "", "", "x"),
    ]
    c.executemany("INSERT INTO decisions VALUES(?,?,?,?,?,?,?,?,?,?)", rows)
    c.commit()
    return c


def test_apply_corrects_only_synthetic_bge(tmp_path):
    c = _db(tmp_path / "d.db")
    n_date, n_pub = bci.apply_to_db(c, max_date="2026-06-28")
    assert n_date == 1 and n_pub >= 1
    g = lambda did: c.execute("SELECT decision_date, publication_date, date_provenance "
                              "FROM decisions WHERE decision_id=?", (did,)).fetchone()
    # synthetic BGE with recoverable text date -> corrected + provenance + pub date
    assert g("bge_152 II 1") == ("2025-09-27", "2026-01-01", "extracted_from_text")
    # synthetic BGE with NO recoverable date -> date NOT changed, flagged
    assert g("bge_old")[0] == "1974-01-01" and g("bge_old")[2] in ("volume_synthetic", "null")
    # real non-BGE date untouched
    assert g("bger_real")[0] == "2025-09-27"
    # search-by-corrected-date now works (the whole point)
    found = c.execute("SELECT decision_id FROM decisions WHERE decision_date='2025-09-27' "
                      "AND court='bge'").fetchone()
    assert found and found[0] == "bge_152 II 1"


def test_apply_is_idempotent(tmp_path):
    c = _db(tmp_path / "d.db")
    bci.apply_to_db(c, max_date="2026-06-28")
    n_date, _ = bci.apply_to_db(c, max_date="2026-06-28")   # second run: nothing left synthetic
    assert n_date == 0


# ── volume gate (2026-09-27) ────────────────────────────────────────────
# The historical volumes are OCR text: "Arrêt du 6 avril 1980" for 1900, a
# scan footer "15.03.2020" in volume 1 (1875). Without a volume check the
# correction replaced the placeholder with such a date; a replica of the
# served build carried 1,531 of them in the historical volumes.

def _gate_db(path, rows):
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE decisions(decision_id TEXT PRIMARY KEY, court TEXT,"
              " docket_number TEXT, decision_date TEXT, publication_date TEXT, full_text TEXT)")
    c.executemany("INSERT INTO decisions VALUES(?,?,?,?,?,?)", rows)
    c.commit()
    return c


def test_text_date_outside_the_volume_window_is_rejected(tmp_path):
    c = _gate_db(tmp_path / "d.db", [
        # historical row after the build's id remap: volume 1 = 1875
        ("bge_1_I_396", "bge", "1_I_396", "1875-01-01", None,
         "396 A. Staatsrechtliche Entscheidungen. Urteil vom 15. März 2020 in Sachen X"),
        # OCR misread year: volume 26 = 1900
        ("bge_26_II_254", "bge", "26_II_254", "1900-01-01", None,
         "254 Civilrechtspflege. 36. Arrêt du 6 avril 1980, dans la cause Gade"),
        # a genuine late publication stays: volume 149 = 2023, ruling of 2021,
        # read from the docket-validated Urteilskopf
        ("bge_149 IV 97", "bge", "149 IV 97", "2023-01-01", None,
         "Urteilskopf 149 IV 97 Auszug aus dem Urteil 6B_1079/2021 vom 22. November 2021"),
        # a body date two years before the volume is the lower court's:
        # volume 121 = 1995, BGE 121 I 267 was decided on 15.9.1995
        ("bge_BGE_121_I_267", "bge", "BGE 121 I 267", "1995-01-01", None,
         "Bundesgericht (BGE) Band I 1995 BGE 121 I 267 Regeste ... Entscheid des "
         "Verwaltungsgerichts vom 6. Juni 1993"),
    ])
    n_date, _ = bci.apply_to_db(c, max_date="2026-09-27")
    g = lambda did: c.execute("SELECT decision_date, date_provenance FROM decisions "
                              "WHERE decision_id=?", (did,)).fetchone()
    assert g("bge_1_I_396") == ("1875-01-01", "volume_synthetic")
    assert g("bge_26_II_254") == ("1900-01-01", "volume_synthetic")
    assert g("bge_149 IV 97") == ("2021-11-22", "extracted_from_text")
    assert g("bge_BGE_121_I_267") == ("1995-01-01", "volume_synthetic")
    assert n_date == 1


def test_volume_gate_reads_every_bge_id_form():
    ok = bci.bge_date_in_volume
    assert ok("2014-05-19", "140 III 244", "bge_140 III 244")
    assert ok("2014-05-19", "BGE 140 III 244", "bge_BGE_140_III_244")
    assert not ok("1990-05-09", "140 III 244", "bge_140 III 244")
    assert ok("1990-03-14", "116 IA 28", "bge_116 IA 28")
    assert not ok("1980-04-06", "26_II_254", "bge_26_II_254")
    # year window: volume year -3 .. +1
    assert ok("2011-01-01", "140 III 1", None) and not ok("2010-12-31", "140 III 1", None)
    assert ok("2015-12-31", "140 III 1", None) and not ok("2016-01-01", "140 III 1", None)
    # a date that is not from the ruling's header gets no late-publication allowance
    assert not ok("2012-06-01", "140 III 1", None, lag=1) and ok("2013-06-01", "140 III 1", None, lag=1)
    # no volume to check against: not this gate's business
    assert ok("1990-05-09", "", "bge_19791204_7710_76")


def test_volume_gate_constants_match_the_scraper():
    from scrapers import bge as scraper
    assert bci.BGE_VOLUME_EPOCH == scraper.BGE_VOLUME_EPOCH
    assert bci.BGE_HEADER_LAG_YEARS == scraper.BGE_HEADER_LAG_YEARS


# ── volumes 1-79: only the ruling's own header dates it (report 2026-10-07) ──
# The DFR text of BGE 78 IV 83 opens with the last page of No 21, which quotes a
# letter "vom 17. Juli 1951"; the ruling's own header says "vom 3. Juni 1952".
# Both dates pass the one-year volume window, so the window alone cannot tell.

_FYG = ("82\nStrafgesetzbuch. No 21.\nauch im Brief vom 17. Juli 1951 hat das Mädchen\n"
        "nichts anderes geschrieben.\nStrafgesetzbuch. No 22.\n83\n"
        "22. Auszug aus dem Urteil des Kassationshofes vom 3. Juni 1952\ni. S. Fyg gegen Born.\n"
        "Friedrich Fyg sah am Nachmittag des 25. November\n1951 eine fremde Katze.\n"
        "84\nStrafgesetzbuch. No 23.\n23. Auszug aus dem Urteil des Kassationshofes vom 30. 1fai 1952\n"
        "i. S. Staatsanwaltschaft des Kantons Aargau gegen Friedlin.\nA. -\n"
        "Margrith Friedlin und ihr am 6. Juli 1948 geborenes Kind klagten\n")
_FRIEDLIN = _FYG[_FYG.index("84\n"):]


def test_historical_bge_takes_the_own_header_date_not_the_neighbours(tmp_path):
    c = _gate_db(tmp_path / "d.db", [
        ("bge_78_IV_83", "bge", "78_IV_83", "1952-01-01", None, _FYG),
        # own header date unreadable ("30. 1fai 1952"): the body date of the
        # facts (6 July 1948) must not stand in for it
        ("bge_78_IV_84", "bge", "78_IV_84", "1952-01-01", None, _FRIEDLIN),
    ])
    bci.apply_to_db(c, max_date="2026-10-07")
    g = lambda did: c.execute("SELECT decision_date, date_provenance FROM decisions "
                              "WHERE decision_id=?", (did,)).fetchone()
    assert g("bge_78_IV_83") == ("1952-06-03", "extracted_from_text")
    assert g("bge_78_IV_84") == ("1952-01-01", "volume_synthetic")


def test_the_sidecar_writer_applies_the_same_checks(tmp_path):
    src = tmp_path / "src.db"
    c = sqlite3.connect(src)
    c.execute("CREATE TABLE decisions(decision_id TEXT PRIMARY KEY, court TEXT, decision_date TEXT, "
              "publication_date TEXT, docket_number TEXT, full_text TEXT)")
    c.executemany("INSERT INTO decisions VALUES(?,?,?,?,?,?)", [
        ("bge_78_IV_83", "bge", "1952-01-01", None, "78_IV_83", _FYG),
        ("bge_78_IV_84", "bge", "1952-01-01", None, "78_IV_84", _FRIEDLIN),
        ("bge_1_I_396", "bge", "1875-01-01", None, "1_I_396",
         "396 A. Staatsrechtliche Entscheidungen. Urteil vom 15. März 2020 in Sachen X"),
    ])
    c.commit(); c.close()
    out = tmp_path / "ci.db"
    bci.run_write(str(src), str(out), max_year=2026)
    o = sqlite3.connect(out)
    g = lambda did: o.execute("SELECT decision_date, decision_date_provenance, ecli FROM "
                              "canonical_identity WHERE decision_id=?", (did,)).fetchone()
    assert g("bge_78_IV_83") == ("1952-06-03", "extracted_from_text", "ECLI:CH:BGER:1952:78_IV_83")
    assert g("bge_78_IV_84")[:2] == (None, "volume_synthetic")
    assert g("bge_1_I_396")[:2] == (None, "volume_synthetic")
