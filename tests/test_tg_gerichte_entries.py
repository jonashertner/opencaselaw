"""Thurgau yearbook entries (RBOG/TVR) — encoding, head block, signature, fold.

Measured on the 2026-11-08 shard (2,304 tg_gerichte rows):
  * rechtsprechung.tg.ch sends ``text/html`` without a charset; requests read
    the UTF-8 pages as Latin-1, so 2,285 rows hold "EntschÃ¤digung" and "März"
    never matched the month table;
  * 960 rows sat on 1 January (the volume's year), TVR 2008 Nr. 19 on the
    taxpayer's birthday 1928-06-14;
  * no row carried a regeste or the underlying docket (VG.2024.42/E);
  * 1,030 entries were served twice, as tg_gerichte "RBOG-2016-8" and as the
    frozen entscheidsuche tg_obergericht "RBOG 2016 Nr. 8", because the two
    carry different dates.
The fixtures copy the portal's markup (2026-11-08) in miniature.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import build_fts5 as b  # noqa: E402
from db_schema import SCHEMA_SQL  # noqa: E402
from scrapers.cantonal import tg_gerichte as tg  # noqa: E402


def _page(body: str) -> bytes:
    return (
        '<!DOCTYPE html><html><head><meta charset="UTF-8"></head><body>'
        '<main class="main-content"><article id="content"><header><h1>TVR 2025 Nr. 12</h1></header>'
        f'<section class="article-body fb-layout-body" data-searchable-content="">{body}</section>'
        "</article></main></body></html>"
    ).encode("utf-8")


TVR_2025_12 = _page(
    "<p><strong>Kosten für den baulichen Unterhalt; Abzugsfähigkeit von Kosten.</strong></p><hr/>"
    '<p><a href="/vg/34-abs-1-ziff-1-stg">§ 34 Abs. 1 Ziff. 1 StG</a> '
    '<a href="/vg/art-32-abs-2-dbg">Art. 32 Abs. 2 DBG</a></p><hr/>'
    "<p><em>Wird auf einem bestehenden Gebäude der Dachstock vollständig ersetzt, ist die "
    "pauschale Verweigerung der Abzugsfähigkeit unzulässig.</em></p><hr/>"
    "<p>Die im Kanton Thurgau wohnhaften Beschwerdeführer sind Eigentümer einer Liegenschaft.</p>"
    "<p>Aus den Erwägungen:</p><p>2.1 Streitgegenstand bildet die Frage, ob die Kosten "
    "abzugsberechtigt sind; vgl. Entscheid vom 31. Mai 2024 E. 3.d.</p>"
    "<p>Entscheid des Verwaltungsgerichts VG.2024.42/E vom 11. Juni 2025</p>"
    "<p>Das Bundesgericht hat eine dagegen erhobene Beschwerde mit Urteil 9C_442/2025 vom "
    "11. März 2026 abgewiesen.</p>"
)

RBOG_2023_44 = _page(
    "<p><strong>Anwendungsfall eines hinreichenden Tatverdachts</strong></p>"
    '<p><a href="/og/art-309">Art. 309 Abs. 1 lit. a StPO</a> <a href="/og/art-158">Art. 158 StGB</a></p><hr/>'
    "<p><strong>Zusammenfassung des Sachverhalts:</strong></p>"
    "<p>Die Beschwerdeführerin erstattete Strafanzeige per 31. Dezember 2020.</p>"
    "<p>Obergericht, 2. Abteilung, 9. März 2023, SW.2022.89</p><p> <br/> </p><hr/>"
    "<p>[1] RBOG 2020 Nr. 30 Erw. 1.c</p>"
)

TVR_2008_19 = _page(
    "<p><strong>Abzug behinderungsbedingter Kosten</strong></p><hr/>"
    '<p><a href="/vg/34">§ 34 Abs. 1 Ziff. 12 StG</a></p><hr/>'
    "<p><em>Die Tatsache allein genügt nicht.</em></p><hr/>"
    "<p>E, geboren am 14. Juni 1928, wies Krankheitskosten aus.</p>"
    "<p>Entscheid vom 17. Dezember 2008</p>"
)


class _Resp:
    def __init__(self, content: bytes):
        self.content = content
        self.text = content.decode("latin-1")  # what requests does without a charset


# ── encoding ────────────────────────────────────────────────────────────────

def test_soup_reads_the_bytes_not_requests_latin1_text():
    soup = tg._soup(_Resp(TVR_2025_12))
    assert "Abzugsfähigkeit" in soup.get_text()
    assert "Ã¤" not in soup.get_text()


def test_fix_mojibake_undoes_latin1_reads_and_leaves_clean_text():
    assert tg.fix_mojibake("EntschÃ¤digung des Beistands; Â§ 88 KESV") == "Entschädigung des Beistands; § 88 KESV"
    assert tg.fix_mojibake("Fr. 36â\x80\x99223.â\x80\x93") == "Fr. 36’223.–"
    assert tg.fix_mojibake("Ã\x9cberschuldung, 9. MÃ¤rz 2023") == "Überschuldung, 9. März 2023"
    assert tg.fix_mojibake("gemäss Â§\xa047 Abs.\xa01") == "gemäss §\xa047 Abs.\xa01"
    clean = "Entschädigung × Überschuldung"
    assert tg.fix_mojibake(clean) == clean
    assert tg.fix_mojibake(None) is None


# ── head block (regeste) ────────────────────────────────────────────────────

def test_head_regeste_is_subject_provisions_and_leitsatz_verbatim():
    soup = BeautifulSoup(TVR_2025_12, "html.parser")
    assert tg.extract_head_regeste(soup) == (
        "Kosten für den baulichen Unterhalt; Abzugsfähigkeit von Kosten.\n"
        "§ 34 Abs. 1 Ziff. 1 StG Art. 32 Abs. 2 DBG\n"
        "Wird auf einem bestehenden Gebäude der Dachstock vollständig ersetzt, ist die "
        "pauschale Verweigerung der Abzugsfähigkeit unzulässig.")


def test_head_regeste_without_leitsatz_stops_at_the_facts():
    soup = BeautifulSoup(RBOG_2023_44, "html.parser")
    assert tg.extract_head_regeste(soup) == (
        "Anwendungsfall eines hinreichenden Tatverdachts\n"
        "Art. 309 Abs. 1 lit. a StPO Art. 158 StGB")


def test_head_regeste_none_when_page_opens_with_facts():
    soup = BeautifulSoup(_page("<p>Die Beschwerdeführerin rügt, sie sei nicht angehört worden.</p>"),
                         "html.parser")
    assert tg.extract_head_regeste(soup) is None


# ── signature line ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("line, expected", [
    ("Obergericht, 1. Abteilung, 13. Juni 2016, KES.2016.32",
     (date(2016, 6, 13), "KES.2016.32", "Obergericht, 1. Abteilung")),
    ("Rekurskommission, 5. August 1999, ZBR.1999.4", (date(1999, 8, 5), "ZBR.1999.4", "Rekurskommission")),
    ("Obergericht, 24. Februar 1998, ZB 97 61", (date(1998, 2, 24), "ZB 97 61", "Obergericht")),
    ("Obergericht. 1. Abteilung, 27. Januar 2021, KES.2020.74",
     (date(2021, 1, 27), "KES.2020.74", "Obergericht, 1. Abteilung")),
    ("Obergericht, 2. Abteilung, 10. / 22. Dezember 2020, BR.2020.53",
     (date(2020, 12, 22), "BR.2020.53", "Obergericht, 2. Abteilung")),
    ("Obergericht, 2. Abteilung, 7. Juli / 12. August 2021, SW.2020.90",
     (date(2021, 8, 12), "SW.2020.90", "Obergericht, 2. Abteilung")),
    ("Obergericht, 1. Abteilung, 24. Februar / 3. März 2021 ZBS.2020.11",
     (date(2021, 3, 3), "ZBS.2020.11", "Obergericht, 1. Abteilung")),
    ("Obergericht, 1. Abteilung, 7. April 2022, SBR. 2021.87",
     (date(2022, 4, 7), "SBR. 2021.87", "Obergericht, 1. Abteilung")),
    ("Rekurskommission, 23. April 1999, BR.1999.24, 25, 31 und 32",
     (date(1999, 4, 23), "BR.1999.24, 25, 31 und 32", "Rekurskommission")),
    ("Entscheid des Verwaltungsgerichts VG.2024.42/E vom 11. Juni 2025",
     (date(2025, 6, 11), "VG.2024.42/E", "Verwaltungsgericht")),
    ("Entscheid des Verwaltungsgerichts VG.2023.90/E vom 17. Januar 2024.",
     (date(2024, 1, 17), "VG.2023.90/E", "Verwaltungsgericht")),
    ("Entscheid des Verwaltungsgerichts VG.2019.77/VG.2019.81/E vom 3. Juni 2020",
     (date(2020, 6, 3), "VG.2019.77/VG.2019.81/E", "Verwaltungsgericht")),
    ("Einzelrichterentscheid des Verwaltungsgerichts als Versicherungsgericht VV.2018.26/E vom 4. Juli 2018",
     (date(2018, 7, 4), "VV.2018.26/E", "Verwaltungsgericht als Versicherungsgericht")),
    ("Entscheid der Steuerrekurskommission STRE.2021.117 und STRE.2021.118 vom 22. November 2021",
     (date(2021, 11, 22), "STRE.2021.117 und STRE.2021.118", "Steuerrekurskommission")),
    ("Entscheid der Personalrekurskommission vom 9. Juli 2021, A.2021.4, § 8 / 2021",
     (date(2021, 7, 9), "A.2021.4, § 8 / 2021", "Personalrekurskommission")),
    ("Entscheid VG.2012.155/E vom 27. Februar 2013; vgl. auch TVR 2013 Nr. 26",
     (date(2013, 2, 27), "VG.2012.155/E", None)),
    ("Entscheid vom 7./28. Februar 2001", (date(2001, 2, 28), None, None)),
    ("Entscheid vom 17. Dezember 2008", (date(2008, 12, 17), None, None)),
    ("Frauenfeld, 10. Juli 2025", (date(2025, 7, 10), None, None)),
])
def test_signature_line_shapes(line, expected):
    sig = tg._parse_signature_line(line)
    assert sig is not None, line
    assert (sig["decision_date"], sig["docket_number_2"], sig["chamber"]) == expected


@pytest.mark.parametrize("line", [
    "Urteil 2A.532/2004 vom 31. März 2005",                       # the BGer's judgment
    "Urteil vom 31. Oktober 2008 (2C_222/2008)",
    "Urteil des Bundesgerichts vom 2. März 2005 (2A.47/2005)",
    "BGE vom 20. Dezember 2002",
    "Entscheid des Verwaltungsgerichts VG.2019.215/E vom 25. März 202",  # year cut at the source
    "Mit Schreiben vom 17. November 2005 nahm die Steuerverwaltung Stellung.",
    "Im Ergebnis ist der Entscheid der Vorinstanz vom 2. Dezember 2022",
    "Die Beschwerde wies das Obergericht, 3. März 2010 ab.",
    "E, geboren am 14. Juni 1928, wies Krankheitskosten aus.",
])
def test_lines_that_are_not_signatures(line):
    assert tg._parse_signature_line(line) is None


def test_signature_is_read_from_the_end_and_skips_the_bgers_judgment():
    text = BeautifulSoup(TVR_2025_12, "html.parser").get_text("\n")
    sig = tg.parse_signature(text)
    assert sig["decision_date"] == date(2025, 6, 11)
    assert sig["docket_number_2"] == "VG.2024.42/E"


# ── whole entry ─────────────────────────────────────────────────────────────

def test_parse_entry_page_yearbook_entry():
    page = tg.parse_entry_page(BeautifulSoup(RBOG_2023_44, "html.parser"),
                               "https://rechtsprechung.tg.ch/og/rbog-2023-nr-44", "RBOG-2023-44")
    assert page["decision_date"] == date(2023, 3, 9)      # was 2023-01-01
    assert page["docket_number"] == "RBOG-2023-44"         # never replaced from the text
    assert page["docket_number_2"] == "SW.2022.89"
    assert page["chamber"] == "Obergericht, 2. Abteilung"
    assert page["regeste"].startswith("Anwendungsfall")
    assert page["pdf_url"] is None


def test_birthday_is_never_the_decision_date():
    page = tg.parse_entry_page(BeautifulSoup(TVR_2008_19, "html.parser"),
                               "https://rechtsprechung.tg.ch/vg/tvr-2008-nr-19", "TVR-2008-19")
    assert page["decision_date"] == date(2008, 12, 17)   # was 1928-06-14


def test_yearbook_entry_without_signature_stays_undated():
    text = "Thema\nDie Beschwerde ist abzuweisen.\nUrteil 5A_457/2009 vom 9. Dezember 2009"
    fields = tg.derive_entry_fields(text, "TVR-2009-9")
    assert fields["decision_date"] is None               # not 2009-01-01, not the BGer's date


def test_pdf_ruling_is_dated_by_its_caption():
    text = ("VERWALTUNGSGERICHT DES KANTONS THURGAU\nVG.2022.61/E\nDas Verwaltungsgericht\n"
            "hat am 14. September 2022\nin Sachen\nA\n- Entscheid vom 1. April 2022\n"
            "... mit Urteil 2C_885/2022 vom 9. November 2023 abgewiesen.")
    fields = tg.derive_entry_fields(text, "VG.2022.61", is_pdf=True)
    assert fields["decision_date"] == date(2022, 9, 14)  # was 2023-11-09, the BGer's date


def test_fetch_decision_end_to_end(monkeypatch):
    scraper = tg.TGGerichteScraper.__new__(tg.TGGerichteScraper)
    resp = _Resp(TVR_2025_12)
    resp.status_code = 200
    monkeypatch.setattr(scraper, "get", lambda url, **kw: resp, raising=False)
    stub = {"decision_id": "tg_gerichte_TVR-2025-12", "docket_number": "TVR-2025-12", "year": 2025,
            "title": "TVR 2025 Nr. 12", "url": "https://rechtsprechung.tg.ch/vg/tvr-2025-nr-12"}
    d = scraper.fetch_decision(stub)
    assert d.decision_date == date(2025, 6, 11)
    assert d.docket_number_2 == "VG.2024.42/E"
    assert d.regeste.startswith("Kosten für den baulichen Unterhalt")
    assert "Abzugsfähigkeit" in d.full_text and "Ã" not in d.full_text


# ── repair script ───────────────────────────────────────────────────────────

def test_repair_script_rebuilds_a_row_from_the_cached_page(tmp_path):
    from scripts import repair_tg_gerichte as rep
    url = "https://rechtsprechung.tg.ch/og/rbog-2023-nr-44"
    cache = tmp_path / "cache"
    cache.mkdir()
    rep.cache_path(cache, url).write_bytes(RBOG_2023_44)
    stored = BeautifulSoup(RBOG_2023_44, "html.parser").select_one("section").get_text("\n", strip=True)
    rec = {"decision_id": "tg_gerichte_RBOG-2023-44", "court": "tg_gerichte", "canton": "TG",
           "docket_number": "RBOG-2023-44", "docket_number_2": None, "decision_date": "2023-01-01",
           "full_text": stored.encode("utf-8").decode("latin-1"), "regeste": None,
           "title": "RBOG 2023 Nr. 44", "source_url": url, "pdf_url": None, "chamber": None}
    new, how = rep.repair(rec, cache)
    assert how == "page"
    assert new["decision_date"] == "2023-03-09"
    assert new["tg_repair"] == {"date_from": "2023-01-01"}
    assert new["docket_number_2"] == "SW.2022.89"
    assert "Ã" not in new["full_text"]
    assert new["regeste"].startswith("Anwendungsfall")
    rep.check(rec, new)  # identity unchanged


def test_repair_script_falls_back_to_the_stored_text(tmp_path):
    from scripts import repair_tg_gerichte as rep
    rec = {"decision_id": "tg_gerichte_RBOG-2016-8", "court": "tg_gerichte", "canton": "TG",
           "docket_number": "RBOG-2016-8", "decision_date": "2016-01-01",
           "full_text": "EntschÃ¤digung des Beistands\nObergericht, 1. Abteilung, 13. Juni 2016, KES.2016.32",
           "source_url": "https://rechtsprechung.tg.ch/og/rbog-2016-nr-8", "pdf_url": None}
    new, how = rep.repair(rec, tmp_path)  # empty cache
    assert how == "stored_text"
    assert new["full_text"].startswith("Entschädigung")
    assert (new["decision_date"], new["docket_number_2"]) == ("2016-06-13", "KES.2016.32")


def test_repair_script_gives_a_yearbook_row_its_own_docket_back(tmp_path):
    """The old scraper replaced docket_number with the first case number in the
    text's first 1,000 characters: RBOG 2017 Nr. 15 carried ZBR.2010.70, a ruling
    it cites (25 rows), so neither the fold nor a docket lookup found it."""
    from scripts import repair_tg_gerichte as rep
    rec = {"decision_id": "tg_gerichte_RBOG-2017-15", "court": "tg_gerichte", "canton": "TG",
           "docket_number": "ZBR.2010.70", "decision_date": "2017-01-01",
           "full_text": "Thema\nwie in ZBR.2010.70 entschieden\nObergericht, 3. Mai 2017, ZBR.2016.66",
           "source_url": "https://rechtsprechung.tg.ch/og/rbog-2017-nr-15", "pdf_url": None}
    new, _ = rep.repair(rec, tmp_path)
    assert new["docket_number"] == "RBOG-2017-15"
    assert new["docket_number_2"] == "ZBR.2016.66"
    assert new["tg_repair"] == {"docket_from": "ZBR.2010.70", "date_from": "2017-01-01"}
    rep.check(rec, new)
    with pytest.raises(SystemExit):
        rep.check(rec, {**new, "docket_number": "ZR.2017.1"})


def test_repair_script_apply_swaps_atomically_with_backup(tmp_path):
    from scripts import repair_tg_gerichte as rep
    shard = tmp_path / "tg_gerichte.jsonl"
    rows = [{"decision_id": f"tg_gerichte_RBOG-2016-{i}", "court": "tg_gerichte", "canton": "TG",
             "docket_number": f"RBOG-2016-{i}", "decision_date": "2016-01-01",
             "full_text": f"Thema {i}\nObergericht, 13. Juni 2016, KES.2016.{i}",
             "source_url": f"https://rechtsprechung.tg.ch/og/rbog-2016-nr-{i}", "pdf_url": None}
            for i in range(3)]
    shard.write_text("".join(json.dumps(r) + "\n" for r in rows))
    result = rep.run(shard, None, apply=True, report=None, examples=0)
    assert result["stats"]["rows_changed"] == 3
    backups = list(tmp_path.glob("tg_gerichte.jsonl.bak-tg-*"))
    assert len(backups) == 1 and not list(tmp_path.glob("*.tmp"))
    assert not [p for p in tmp_path.glob("*.jsonl") if p != shard]  # nothing the build would ingest
    out = [json.loads(line) for line in shard.read_text().splitlines()]
    assert [r["decision_date"] for r in out] == ["2016-06-13"] * 3
    assert result["before"]["yearbook_date_jan1"] == 3 and result["after"]["yearbook_date_jan1"] == 0


# ── build fold ─────────────────────────────────────────────────────────────

@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.executescript(SCHEMA_SQL)
    yield c
    c.close()


def _row(did, court, docket, d, text, source=None):
    return {"decision_id": did, "court": court, "canton": "TG", "docket_number": docket,
            "decision_date": d, "language": "de", "title": None, "regeste": None,
            "full_text": text, "source_url": "https://example.invalid/" + did, "source": source}


def _text(seed, n=40):
    return " ".join(f"{seed} Erwägung {i}: die Vorinstanz hat richtig entschieden." for i in range(n))


@pytest.mark.parametrize("docket, key", [
    ("RBOG-2016-8", ("RBOG", 2016, 8)),
    ("RBOG 2016 Nr. 8", ("RBOG", 2016, 8)),
    ("RBOG 2025 Nr. 08", ("RBOG", 2025, 8)),
    ("TVR 2008 Nr. 19", ("TVR", 2008, 19)),
    ("RBOG 2012 Nr. 5 (1. Teil)", None),
    ("VG.2024.42", None),
])
def test_tg_yearbook_key(docket, key):
    assert b._tg_yearbook_key(docket) == key


def test_fold_keeps_the_direct_row_whatever_the_dates(conn):
    assert b.insert_decision(conn, _row("tg_gerichte_RBOG-2016-8", "tg_gerichte", "RBOG-2016-8",
                                        "2016-06-13", _text("direct")))
    assert b.insert_decision(conn, _row("tg_obergericht_RBOG_2016_Nr._8", "tg_obergericht",
                                        "RBOG 2016 Nr. 8", "2016-01-01",
                                        "Thurgau Obergericht Rechenschaftsbericht\n" + _text("es") * 2,
                                        source="entscheidsuche"))
    assert b.insert_decision(conn, _row("tg_obergericht_RBOG_2016_Nr._9", "tg_obergericht",
                                        "RBOG 2016 Nr. 9", "2016-03-01", _text("only es")))
    counts = b._fold_tg_yearbook_twins(conn)
    assert counts == {"folded": 1, "folded_tg_obergericht": 1}
    ids = {r[0] for r in conn.execute("SELECT decision_id FROM decisions")}
    assert ids == {"tg_gerichte_RBOG-2016-8", "tg_obergericht_RBOG_2016_Nr._9"}
    alias = conn.execute("SELECT decision_id, source FROM decision_id_aliases WHERE previous_id = ?",
                         ("tg_obergericht_RBOG_2016_Nr._8",)).fetchone()
    assert alias == ("tg_gerichte_RBOG-2016-8", "tg_yearbook_twin")


def test_fold_keeps_the_es_copy_when_the_direct_extraction_failed(conn):
    assert b.insert_decision(conn, _row("tg_gerichte_TVR-2010-3", "tg_gerichte", "TVR-2010-3",
                                        "2010-05-05", "[Text extraction failed for TVR-2010-3]"))
    assert b.insert_decision(conn, _row("tg_obergericht_TVR_2010_Nr._3", "tg_obergericht",
                                        "TVR 2010 Nr. 3", "2010-05-05", _text("es")))
    counts = b._fold_tg_yearbook_twins(conn)
    assert counts["kept_es_copy"] == 1
    ids = {r[0] for r in conn.execute("SELECT decision_id FROM decisions")}
    assert ids == {"tg_obergericht_TVR_2010_Nr._3"}


def test_swap_gate_credits_the_folded_es_rows():
    """The per-court gate counts a folded tg_obergericht row as answered when its
    alias points at a row of the same (series, volume, number)."""
    assert b._fold_dockets("tg_obergericht", "RBOG 2025 Nr. 08", None) & \
        b._fold_dockets("tg_gerichte", "RBOG-2025-8", None)


def test_repair_gives_a_row_minted_from_an_overwritten_docket_its_entrys_docket(tmp_path):
    from scripts import repair_tg_gerichte as rep
    rec = {"decision_id": "tg_gerichte_ZR.2010.10", "court": "tg_gerichte", "canton": "TG",
           "docket_number": "ZR.2010.10", "decision_date": "2010-02-26",
           "full_text": "Thema\nObergericht, 26. Februar 2010, ZR.2010.10",
           "source_url": "https://rechtsprechung.tg.ch/og/rbog-2010-nr-08", "pdf_url": None}
    new, _ = rep.repair(rec, tmp_path)
    assert new["docket_number"] == "RBOG-2010-8"
    assert new["decision_id"] == "tg_gerichte_ZR.2010.10"
    rep.check(rec, new)


def test_fold_serves_the_yearbook_id_over_a_second_direct_row(conn):
    text = _text("same entry")
    assert b.insert_decision(conn, _row("tg_gerichte_ZR.2010.10", "tg_gerichte", "RBOG-2010-8",
                                        "2010-02-26", text + " und mehr"))
    assert b.insert_decision(conn, _row("tg_gerichte_RBOG-2010-8", "tg_gerichte", "RBOG-2010-8",
                                        "2010-02-27", text))
    assert b.insert_decision(conn, _row("tg_obergericht_RBOG_2010_Nr._8", "tg_obergericht",
                                        "RBOG 2010 Nr. 8", "2010-02-26", text))
    counts = b._fold_tg_yearbook_twins(conn)
    assert counts["folded"] == 2
    ids = {r[0] for r in conn.execute("SELECT decision_id FROM decisions")}
    assert ids == {"tg_gerichte_RBOG-2010-8"}
    assert conn.execute("SELECT decision_id FROM decision_id_aliases WHERE previous_id = ?",
                        ("tg_gerichte_ZR.2010.10",)).fetchone() == ("tg_gerichte_RBOG-2010-8",)
