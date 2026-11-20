"""search_practice: repealed documents left out by default, and offset paging.

User report 2026-11-20 (Verwaltungspraxis, state 13.11.): documents marked
"hinfällig", "aufgehoben" or "(Archiv)" appeared in the default search, the
repealed KSIH (BSV doc 6415) even at rank 1; and the search returned at most
50 hits with no way to page further.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import mcp_server  # noqa: E402

ROWS = [
    # doc_id, source, authority, doc_type, doc_number, title, date, url, topics
    ("bsv_6415_v18_de", "bsv_weisungen", "BSV", "kreisschreiben", "KSIH",
     "Kreisschreiben über Invalidität und Hilflosigkeit (KSIH) (gültig ab 1.1.2015; "
     "Stand 1.1.2021). Mit Inkrafttreten der Weiterentwicklung der IV (01.01.2022) hinfällig.",
     "2021-02-02", "https://sozialversicherungen.admin.ch/de/d/6415",
     ["IV", "Kreisschreiben (Archiv)", "KSIH"]),
    ("bsv_6857_v22_de", "bsv_weisungen", "BSV", "wegleitung", "RWL",
     "Wegleitung über die Renten (RWL)", "2026-11-03",
     "https://sozialversicherungen.admin.ch/de/d/6857", ["AHV", "RWL"]),
    ("bsv_5966_v1_de", "bsv_weisungen", "BSV", "rechtsprechung", "2003/5",
     "AHI-Praxis 5/2003", "2003-01-01",
     "https://sozialversicherungen.admin.ch/de/d/5966", ["AHI-Praxis (Archiv)"]),
    ("finma_rs_2019_01_de", "finma_rs", "FINMA", "rundschreiben", "FINMA-RS 2019/01",
     "2019/01 FINMA-Rundschreiben Risikoverteilung", "2017-12-07", "",
     ["aufgehoben oder ersetzt", "abrogé ou remplacé"]),
    ("estv_ks_12_de", "estv_ks", "ESTV", "kreisschreiben", "KS 12",
     "Kreisschreiben Nr. 12: Umsatzabgabe (ersetzt die Ausgabe 1-012-S-2005-d vom 20.12.2005)",
     "2011-03-10", "", []),
    ("bsv_5873_v1_de", "bsv_weisungen", "BSV", "rundschreiben", "305",
     "IV-Rundschreiben Nr. 305 / Pflegefamilien (Inhalt ins Kreisschreiben aufgenommen)",
     "2012-03-01", "https://sozialversicherungen.admin.ch/de/d/5873", []),
]


def _fixture_conn(extra: int = 0):
    def make():
        c = sqlite3.connect(":memory:")
        c.row_factory = sqlite3.Row
        c.executescript(
            """
            CREATE TABLE practice (
                doc_id TEXT, source TEXT, issuing_authority TEXT, doc_type TEXT,
                doc_number TEXT, title TEXT, date TEXT, language TEXT,
                url TEXT, pdf_url TEXT, body_text TEXT, topics_json TEXT, scraped_at TEXT
            );
            CREATE VIRTUAL TABLE practice_fts USING fts5(doc_number, title, body_text, topics_json);
            """
        )
        rows = list(ROWS) + [
            (f"seco_{i}_de", "seco_arg", "SECO", "wegleitung", f"ArGV 1 Art. {i}",
             f"ArGV 1 Art. {i}", "2024-01-01", "", [])
            for i in range(extra)
        ]
        for rowid, (did, src, auth, dt, num, title, date, url, topics) in enumerate(rows, 1):
            tj = json.dumps(topics, ensure_ascii=False)
            body = f"Text zur Hilflosenentschädigung {num}"
            lang = did.rsplit("_", 1)[-1] if did.rsplit("_", 1)[-1] in ("de", "fr", "it", "en") else "de"
            c.execute(
                "INSERT INTO practice (rowid, doc_id, source, issuing_authority, doc_type, "
                "doc_number, title, date, language, url, pdf_url, body_text, topics_json) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,'',?,?)",
                (rowid, did, src, auth, dt, num, title, date, lang, url, body, tj))
            c.execute(
                "INSERT INTO practice_fts (rowid, doc_number, title, body_text, topics_json) "
                "VALUES (?,?,?,?,?)", (rowid, num, title, body, tj))
        c.commit()
        return c
    return make


def test_repealed_left_out_by_default(monkeypatch):
    monkeypatch.setattr(mcp_server, "_open_practice_db", _fixture_conn())
    res = mcp_server._search_practice(query="Hilflosenentschädigung")
    ids = {r["doc_id"] for r in res["results"]}
    assert "bsv_6415_v18_de" not in ids          # "hinfällig" + Kreisschreiben (Archiv)
    assert "finma_rs_2019_01_de" not in ids      # aufgehoben oder ersetzt
    assert "bsv_5873_v1_de" not in ids           # (Inhalt ins Kreisschreiben aufgenommen)
    # Not repealed: the new edition that replaces an old one, and archived
    # case-law digests (AHI-Praxis), which do not lapse.
    assert {"bsv_6857_v22_de", "estv_ks_12_de", "bsv_5966_v1_de"} <= ids
    assert res["repealed_hidden"] == 3
    text = mcp_server._format_search_practice_response(res)
    assert "3 repealed or archived document(s) left out" in text


def test_include_superseded_shows_repealed(monkeypatch):
    monkeypatch.setattr(mcp_server, "_open_practice_db", _fixture_conn())
    res = mcp_server._search_practice(query="Hilflosenentschädigung", include_superseded=True)
    ids = {r["doc_id"] for r in res["results"]}
    assert "bsv_6415_v18_de" in ids and "finma_rs_2019_01_de" in ids


def test_offset_pages_past_50(monkeypatch):
    monkeypatch.setattr(mcp_server, "_open_practice_db", _fixture_conn(extra=120))
    first = mcp_server._search_practice(query="Hilflosenentschädigung", limit=50)
    assert first["returned"] == 50 and first["next_offset"] == 50
    second = mcp_server._search_practice(query="Hilflosenentschädigung", limit=50, offset=50)
    third = mcp_server._search_practice(query="Hilflosenentschädigung", limit=50, offset=100)
    seen = [r["doc_id"] for page in (first, second, third) for r in page["results"]]
    assert len(seen) == len(set(seen)) == first["distinct_documents"] == 123
    assert third["next_offset"] is None
    text = mcp_server._format_search_practice_response(second)
    assert "showing results 51–100" in text and "offset=100" in text
    assert "\n51. " in text


def test_offset_beyond_end_is_empty_not_error(monkeypatch):
    monkeypatch.setattr(mcp_server, "_open_practice_db", _fixture_conn())
    res = mcp_server._search_practice(query="Hilflosenentschädigung", offset=500)
    assert "error" not in res and res["results"] == [] and res["next_offset"] is None


def test_title_markers_do_not_overmatch():
    f = mcp_server._practice_is_repealed
    assert not f("OLT 4 Art. 34: Abrogation de l'assujettissement", "[]")
    assert not f("Wie ist vorzugehen, wenn eine Stiftung aufgehoben werden soll?", "[]")
    assert not f("Weisung über die Aktenarchivierung (WAF)", "[]")
    assert not f("Lettre circulaire AI n° 363 / Lettre circulaire AI no 166 abrogée", "[]")
    assert f("Weisung Nr. 2 vom 15. April 2014 zum Zahlungsbefehl (obsolet)", "[]")
    assert f("Circulaire no C5 (abrogée)", "[]")
    assert f("IV-Rundschreiben Nr. 087 / Zuständigkeit (Durch RS 186 ersetzt)", "[]")
    assert f("Circolare LAI (Valida dal 1.1.2012). Obsoleto con l'entrata in vigore", "[]")
    assert f("Lettre circulaire AI n 233 / Petits rapports selon Tarmed (caduc)", "[]")


def test_archived_edition_of_in_force_circular_stays(monkeypatch):
    """FINMA's archive holds English editions of circulars whose in-force
    edition the corpus has in German only: a document is repealed only when
    no language has it in force."""
    extra = [
        ("finma_rs_2017_01_current_de", "finma_rs", "FINMA", "rundschreiben", "FINMA-RS 2017/01",
         "2017/01 FINMA-Rundschreiben Corporate Governance", "2020-11-04", "", ["in Kraft"]),
        ("finma_rs_2017_01_20161207_en", "finma_rs", "FINMA", "rundschreiben", "FINMA-RS 2017/01",
         "2017/01 FINMA Circular Corporate governance", "2016-12-07", "",
         ["aufgehoben oder ersetzt", "repealed or replaced"]),
    ]
    monkeypatch.setattr(mcp_server, "_open_practice_db", _fixture_conn())
    monkeypatch.setattr(sys.modules[__name__], "ROWS", ROWS + extra)
    res = mcp_server._search_practice(query="Hilflosenentschädigung")
    ids = {r["doc_id"] for r in res["results"]}
    assert "finma_rs_2017_01_20161207_en" in ids         # EN text of an in-force circular
    assert "finma_rs_2019_01_de" not in ids               # wholly repealed
