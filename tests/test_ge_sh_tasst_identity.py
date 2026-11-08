"""Identity fixes of 2026-11-08: the Geneva case number, Schaffhausen label
markup, and the Swiss Sports Tribunal's scanned predecessor rulings.

* GE: justice.ge.ch prints the case number (C/16444/2023, A/3880/2025) in the
  result block's flag; the scraper read the flag only for a "Fiche N" the
  portal no longer prints, so docket_number_2 was empty on every row.
* SH: the CMS label and headline sometimes carry editor HTML, which went into
  dockets, decision_ids and 220 titles.
* TA_SST: 29 Disziplinarkammer rulings (DK/CD 2016-2024) are scans without a
  text layer; they were cached as gaps and re-probed weekly, never stored.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

# ── GE ──────────────────────────────────────────────────────────────────────

GE_BLOCKS = """
<div id="lstDecis">
<div class="list-block col-lg-12 mb-5"> <div class="decis-block__flag"> C/16444/2023 </div>
 <div class="list-block__content row pb-3"> <h3 class="list-block__title col-lg-8">
 <a href="/apps/decis/fr/acjc/show/3515599?doc="> ACJC/1978/2026 </a> du 22.10.2026 sur JTBL/1257/2025 ( OBL ) , RETIRE </h3>
 <div class="col-lg-12"> </div> </div> </div>
<div class="list-block col-lg-12 mb-5"> <div class="decis-block__flag"> A/3880/2025 </div>
 <div class="list-block__content row pb-3"> <h3 class="list-block__title col-lg-8">
 <a href="/apps/decis/fr/ata/show/3512248?doc="> ATA/1089/2026 </a> du 06.10.2026 ( PATIEN ) , PARTIELMNT ADMIS </h3>
 <div class="col-lg-12"> <div> <b>Résumé</b> : Recours d'une patiente. </div> </div> </div> </div>
<div class="list-block col-lg-12 mb-5"> <div class="decis-block__flag"> Fiche 12345 </div>
 <div class="list-block__content row pb-3"> <h3 class="list-block__title col-lg-8">
 <a href="/apps/decis/fr/ata/show/1?doc="> ATA/1/2010 </a> du 06.01.2010 </h3> </div> </div>
</div>
"""


def _ge_scraper():
    from scrapers.cantonal.ge_gerichte import GEGerichteScraper
    return GEGerichteScraper.__new__(GEGerichteScraper)


def test_ge_listing_block_yields_the_case_number():
    stubs = list(_ge_scraper()._parse_result_page(GE_BLOCKS, "acjc"))
    assert [(s["docket_number"], s["case_number"], s["fiche_id"]) for s in stubs] == [
        ("ACJC/1978/2026", "C/16444/2023", None),
        ("ATA/1089/2026", "A/3880/2025", None),
        ("ATA/1/2010", None, "12345"),
    ]


@pytest.mark.parametrize("flag, ruling, expected", [
    ("C/16444/2023", "ACJC/1978/2026", "C/16444/2023"),
    ("P/11247/2025", "JTDP/1564/2025", "P/11247/2025"),
    ("AC/3395/2014", "DAAJ/160/2025", "AC/3395/2014"),
    ("CAPJ/4/2024", "ACAPJ/1/2025", "CAPJ/4/2024"),
    ("C/1/2024 C/2/2024 C/1/2024", "ACJC/5/2025", "C/1/2024, C/2/2024"),
    ("ACJC/5/2025", "ACJC/5/2025", None),
    ("Fiche 12345", "ATA/1/2010", None),
    ("", "ATA/1/2010", None),
])
def test_ge_parse_case_number(flag, ruling, expected):
    from scrapers.cantonal.ge_gerichte import parse_case_number
    assert parse_case_number(flag, ruling) == expected


def test_ge_fetch_decision_stores_case_number_as_docket_number_2(monkeypatch):
    sc = _ge_scraper()
    stub = next(iter(sc._parse_result_page(GE_BLOCKS, "acjc")))
    stub["detail_url"] = ""
    d = sc.fetch_decision(stub)
    assert (d.docket_number, d.docket_number_2) == ("ACJC/1978/2026", "C/16444/2023")


def test_ge_backfill_fills_only_empty_docket_number_2(tmp_path):
    from scripts import backfill_ge_case_numbers as bf
    m = tmp_path / "map.jsonl"
    m.write_text("".join(json.dumps(r) + "\n" for r in [
        {"decision_id": "ge_gerichte_ACJC_1978_2026", "docket_number": "ACJC/1978/2026",
         "case_number": "C/16444/2023", "section": "acjc", "year": 2026},
        {"decision_id": "ge_gerichte_ACJC_1978_2026", "docket_number": "ACJC/1978/2026",
         "case_number": "C/16444/2023", "section": "cabl", "year": 2026},   # same ruling, 2nd section
        {"decision_id": "ge_gerichte_X_1_2026", "case_number": "C/1/2026", "section": "acjc", "year": 2026},
        {"decision_id": "ge_gerichte_X_1_2026", "case_number": "C/2/2026", "section": "cabl", "year": 2026},
        {"done": True, "section": "acjc", "year": 2026, "total": 3},
    ]))
    mapping, conflicts = bf.load_map(m)
    assert mapping == {"ge_gerichte_ACJC_1978_2026": "C/16444/2023"}
    assert conflicts == {"ge_gerichte_X_1_2026"}
    shard = tmp_path / "ge_gerichte.jsonl"
    rows = [
        {"decision_id": "ge_gerichte_ACJC_1978_2026", "court": "ge_gerichte", "docket_number": "ACJC/1978/2026",
         "docket_number_2": None, "decision_date": "2026-10-22", "full_text": "x"},
        {"decision_id": "ge_gerichte_X_1_2026", "court": "ge_gerichte", "docket_number": "X/1/2026",
         "decision_date": "2026-01-01", "full_text": "y"},
        {"decision_id": "ge_gerichte_ATA_1_2010", "court": "ge_gerichte", "docket_number": "ATA/1/2010",
         "docket_number_2": "A/9/2009", "decision_date": "2010-01-06", "full_text": "z"},
    ]
    shard.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    dry = bf.run(shard, mapping, conflicts, apply=False)
    assert (dry["filled"], dry["conflict"], dry["already_filled"]) == (1, 1, 1)
    assert json.loads(shard.read_text().splitlines()[0])["docket_number_2"] is None  # dry run wrote nothing
    bf.run(shard, mapping, conflicts, apply=True)
    out = [json.loads(line) for line in shard.read_text().splitlines()]
    assert [r.get("docket_number_2") for r in out] == ["C/16444/2023", None, "A/9/2009"]
    assert {k: v for k, v in out[0].items() if k != "docket_number_2"} == \
        {k: v for k, v in rows[0].items() if k != "docket_number_2"}
    assert len(list(tmp_path.glob("ge_gerichte.jsonl.bak-ge-case-*"))) == 1


def test_ge_backfill_refuses_if_docket_number_2_became_a_dedup_key(tmp_path, monkeypatch):
    from scripts import backfill_ge_case_numbers as bf
    monkeypatch.setattr(bf, "DECISION_NUMBER_COURTS", frozenset({"ge_gerichte"}))
    shard = tmp_path / "ge_gerichte.jsonl"
    shard.write_text("")
    with pytest.raises(SystemExit):
        bf.run(shard, {}, set(), apply=False)


# ── SH ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw, clean", [
    ("<p>Nr. 60/2018/23</p>", "Nr. 60/2018/23"),
    ("Nr. 63/2020/17<br>", "Nr. 63/2020/17"),
    ('<div><span style="color: rgb(70, 70, 70); font-family: Helvetica_Neue_Web_Light, 宋体;">'
     "Nr. 63/2020/6</span><br></div>", "Nr. 63/2020/6"),
    ('Nr. 60/2023/3<span style="x">&nbsp;&nbsp;</span>', "Nr. 60/2023/3"),
    ("Nr. 50/2016/13 und 50/2016/17&nbsp;", "Nr. 50/2016/13 und 50/2016/17"),
    ("Nr. 60/2020/31 –  <br>Wahl des Kantonsrats", "Nr. 60/2020/31 – Wahl des Kantonsrats"),
    ("Nr. 60/2017/43", "Nr. 60/2017/43"),
    ("", ""),
])
def test_sh_clean_html_text(raw, clean):
    from scrapers.cantonal.sh_gerichte import clean_html_text
    assert clean_html_text(raw) == clean


def test_sh_item_gets_a_clean_id_and_remembers_the_legacy_one():
    from scrapers.cantonal.sh_gerichte import SHGerichteScraper
    sc = SHGerichteScraper.__new__(SHGerichteScraper)
    stub = sc._parse_item({"contentid": "7708830", "kachellabel": "<p>Nr. 60/2018/23</p>",
                           "articleHeadline": "Nr. 60/2018/23 –<br>Submission", "permalink": "/x"})
    assert stub["docket_number"] == "Nr. 60/2018/23"
    assert stub["decision_id"] == "sh_gerichte_Nr. 60_2018_23"
    assert stub["legacy_decision_id"] == "sh_gerichte_<p>Nr. 60_2018_23<_p>"
    assert stub["title"] == "Nr. 60/2018/23 – Submission"
    plain = sc._parse_item({"contentid": "1", "kachellabel": "Nr. 60/2017/43", "articleHeadline": "T"})
    assert plain["legacy_decision_id"] is None


def test_sh_discovery_skips_rows_held_under_the_legacy_id(monkeypatch):
    from scrapers.cantonal import sh_gerichte as sh

    class _State:
        def is_known(self, did):
            return did == "sh_gerichte_<p>Nr. 60_2018_23<_p>"

    class _R:
        def json(self):
            return [{"contentid": "1", "kachellabel": "<p>Nr. 60/2018/23</p>", "articleHeadline": "A"},
                    {"contentid": "2", "kachellabel": "Nr. 61/2025/1", "articleHeadline": "B"}]

    sc = sh.SHGerichteScraper.__new__(sh.SHGerichteScraper)
    sc.state = _State()
    monkeypatch.setattr(sc, "get", lambda *a, **k: _R(), raising=False)
    assert [s["docket_number"] for s in sc.discover_new()] == ["Nr. 61/2025/1"]


def test_sh_repair_rekeys_direct_rows_and_cleans_es_rows(tmp_path):
    from scripts import repair_sh_markup as rep
    direct = tmp_path / "sh_gerichte.jsonl"
    direct.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in [
        {"decision_id": "sh_gerichte_<p>Nr. 60_2018_23<_p>", "court": "sh_gerichte",
         "docket_number": "<p>Nr. 60/2018/23</p>", "title": "Nr. 60/2018/23 –<br>Submission",
         "decision_date": "2021-02-23", "full_text": "t"},
        {"decision_id": "sh_gerichte_Nr. 60_2017_43", "court": "sh_gerichte",
         "docket_number": "Nr. 60/2017/43", "title": "Akteneinsicht", "full_text": "u"},
    ]))
    es = tmp_path / "es_sh_obergericht.jsonl"
    es.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in [
        {"decision_id": "sh_obergericht__p_Nr._60_2018_23__p_", "court": "sh_obergericht",
         "docket_number": "<p>Nr. 60/2018/23</p>", "regeste": "Erste Zeile<br>zweite Zeile",
         "full_text": "v"},
        {"decision_id": "sh_obergericht_60_2018_23", "court": "sh_obergericht",
         "docket_number": "60/2018/23", "full_text": "v"},
    ]))
    s1 = rep.run(direct, apply=True)
    s2 = rep.run(es, apply=True)
    assert s1["changed:decision_id"] == 1 and s1["rows_changed"] == 1
    assert s2["rows_changed"] == 1 and "changed:decision_id" not in s2
    d = [json.loads(line) for line in direct.read_text().splitlines()]
    assert d[0]["decision_id"] == "sh_gerichte_Nr. 60_2018_23"
    assert d[0]["previous_decision_id"] == "sh_gerichte_<p>Nr. 60_2018_23<_p>"
    assert (d[0]["docket_number"], d[0]["title"]) == ("Nr. 60/2018/23", "Nr. 60/2018/23 – Submission")
    e = [json.loads(line) for line in es.read_text().splitlines()]
    assert e[0]["decision_id"] == "sh_obergericht__p_Nr._60_2018_23__p_"   # es ids are kept
    assert (e[0]["docket_number"], e[0]["regeste"]) == ("60/2018/23", "Erste Zeile\nzweite Zeile")


def test_sh_repair_refuses_to_rekey_onto_a_held_id(tmp_path):
    from scripts import repair_sh_markup as rep
    rec = {"decision_id": "sh_gerichte_Nr. 63_2020_17<br>", "court": "sh_gerichte",
           "docket_number": "Nr. 63/2020/17<br>"}
    with pytest.raises(SystemExit):
        rep.repair(rec, {"sh_gerichte_Nr. 63_2020_17"})


# ── TA_SST ──────────────────────────────────────────────────────────────────

def test_ta_sst_scan_without_text_layer_is_ocrd(monkeypatch):
    from scrapers import ta_sst

    class _State:
        GAP_TTL_DAYS = 7
        gaps: list = []

        def mark_gap(self, did):
            self.gaps.append(did)

    class _R:
        content = b"%PDF-scan"

    sc = ta_sst.TaSSTScraper.__new__(ta_sst.TaSSTScraper)
    sc.state = _State()
    monkeypatch.setattr(sc, "get", lambda url: _R(), raising=False)
    monkeypatch.setattr(ta_sst, "_extract_pdf_text", lambda data: "")
    ocr_text = "Entscheid der Disziplinarkammer des Schweizer Sports vom 24. Juni 2019. " * 5
    monkeypatch.setattr(ta_sst.pdf_ocr, "ocr_pdf_bytes", lambda data: ocr_text)
    stub = {"docket_number": "DK 2019/DO/1", "url": "https://www.sportstribunal.ch/x.pdf",
            "decision_date": "2019-06-24", "title": "DK 2019/DO/1 - Entscheid vom 24. Juni 2019"}
    d = sc.fetch_decision(stub)
    assert d is not None and d.decision_id == "ta_sst_DK 2019_DO_1"
    assert "Disziplinarkammer" in d.full_text
    assert sc.state.gaps == []


def test_ta_sst_scan_without_ocr_stays_a_gap(monkeypatch):
    from scrapers import ta_sst

    class _State:
        GAP_TTL_DAYS = 7

        def __init__(self):
            self.gaps = []

        def mark_gap(self, did):
            self.gaps.append(did)

    class _R:
        content = b"%PDF-scan"

    sc = ta_sst.TaSSTScraper.__new__(ta_sst.TaSSTScraper)
    sc.state = _State()
    monkeypatch.setattr(sc, "get", lambda url: _R(), raising=False)
    monkeypatch.setattr(ta_sst, "_extract_pdf_text", lambda data: "")
    monkeypatch.setattr(ta_sst.pdf_ocr, "ocr_pdf_bytes", lambda data: "")
    assert sc.fetch_decision({"docket_number": "CD 2023/E/1", "url": "u"}) is None
    assert sc.state.gaps == ["ta_sst_CD 2023_E_1"]


# ── SH cross-court twins ────────────────────────────────────────────────────

@pytest.fixture
def conn():
    import sqlite3
    from db_schema import SCHEMA_SQL
    c = sqlite3.connect(":memory:")
    c.executescript(SCHEMA_SQL)
    yield c
    c.close()


def _sh_row(did, court, docket, d, text):
    return {"decision_id": did, "court": court, "canton": "SH", "docket_number": docket,
            "decision_date": d, "language": "de", "title": None, "regeste": None,
            "full_text": text, "source_url": "https://example.invalid/" + did}


def test_sh_twins_differing_only_by_nr_prefix_fold_to_the_fuller_row(conn):
    """613 SH rulings were served more than once: direct "Nr. 60/2018/18", entscheidsuche
    "60/2018/18", same date. The direct copy of many holds only its headline
    (its stored PDF link is dead), so the fuller copy is kept and the other id
    stays as an alias that the swap gate credits."""
    import build_fts5 as b
    full = " ".join(f"Erwägung {i}: die Beschwerde ist begründet." for i in range(80))
    assert b.insert_decision(conn, _sh_row("sh_gerichte_Nr. 60_2018_18", "sh_gerichte", "Nr. 60/2018/18",
                                           "2019-01-04", "Art. 116a ZPO.\nKostenerlass"))
    assert b.insert_decision(conn, _sh_row("sh_obergericht_60_2018_18", "sh_obergericht", "60/2018/18",
                                           "2019-01-04", full))
    assert b.insert_decision(conn, _sh_row("sh_gerichte_Nr. 60_2018_19", "sh_gerichte", "Nr. 60/2018/19",
                                           "2019-02-01", full + " anders"))
    assert b._cross_court_dedup(conn) == 1
    ids = {r[0] for r in conn.execute("SELECT decision_id FROM decisions")}
    assert ids == {"sh_obergericht_60_2018_18", "sh_gerichte_Nr. 60_2018_19"}
    assert b._fold_dockets("sh_gerichte", "Nr. 60/2018/18", None) & \
        b._fold_dockets("sh_obergericht", "60/2018/18", None)


def test_sh_nr_strip_is_leading_only():
    import build_fts5 as b
    assert not b._fold_dockets("sh_gerichte", "ANR.2024.1", None) & b._fold_dockets("sh_obergericht", "A.2024.1", None)
