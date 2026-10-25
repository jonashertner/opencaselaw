"""External report 2026-10-24 (Adrian König's AI): four findings, each
reproduced on the public endpoint the next day.

1. A date that is another decision's: BGer 7B.150/2001 (14 August 2001)
   carried the date of the Lucerne ruling it reviewed, Gericht AI K 7-2021
   (16 November 2021) the day ai.ch listed it. scripts/restore_closing_dates.py
   repairs the shards, the AI scraper reads the ruling's own line.
2. A PDF page counter ("6 - 8") was taken for an Erwägung heading and cut
   AI K 7-2021 E. 1.5 at the page turn.
3. A heading written with no-break spaces ("2.\\u00a0\\u00a0Vorladungen")
   was no heading at all (Obergericht TG RBOG 2012 Nr. 9).
4. find_leading_cases with a statute and a query ran its topic filter as
   `decision_id IN (...)` on the UNINDEXED FTS column: a filter over every
   row the MATCH produced, 60 s on the first call for a common term.

The texts below are excerpts of the served full texts.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
for p in (REPO, REPO / "search_stack"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import mcp_server as m  # noqa: E402
import extract_decision_structure as eds  # noqa: E402
from scrapers.cantonal.ai_gerichte import own_decision_date  # noqa: E402
from scripts import restore_closing_dates as rcd  # noqa: E402


# ── 2. page counter ─────────────────────────────────────────────────────

AI_TEXT = (
    "1 - 8 \nGültigkeit der Klagebewilligung \n \nErwägungen: \nI. \n \n"
    "1. \nDer Rechtsvertreter von A. reichte beim Bezirksgericht Appenzell I.Rh. eine "
    "Forderungsklage ein. \n \n"
    "II. \n \n"
    "1.1. Die Berufung ist zulässig, weil der Zwischenentscheid die Gültigkeit der "
    "Klagebewilligung betrifft. \n \n"
    "1.5. Die Vermittlerin lud die Parteien vor. Mit E-Mail vom 30. April \n \n\n\n \n"
    "6 - 8 \n"
    "2020 teilte die Vermittlerin dem Rechtsvertreter mit, der Termin bleibe bestehen. "
    "Mit Verfügung vom 30. April 2020 setzte die Vermittlerin den neuen Termin auf den "
    "12. Mai 2020 fest. \n \n"
    "1.6. Die Klagebewilligung ist somit gültig. \n \n"
    "Kantonsgericht Appenzell I.Rh., Entscheid K 7-2021 vom 16. November 2021 \n"
)
SENTENCE = "Mit Verfügung vom 30. April 2020 setzte"


def test_parser_runs_an_erwaegung_across_a_page_counter():
    paras = {p["e_number"]: p["text"] for p in eds.parse_erwaegungen_paragraphs(AI_TEXT)}
    assert "6" not in paras
    assert SENTENCE in paras["1.5"]
    assert paras["1.6"].startswith("Die Klagebewilligung")


def test_a_page_counter_is_not_a_heading_in_the_text_fallback():
    block = m._erwaegung_from_text(AI_TEXT, "1.5")
    assert SENTENCE in block["text"]
    assert m._erwaegung_from_text(AI_TEXT, "6") is None


def test_a_real_dashed_heading_still_counts():
    # "3 - Die Beschwerde ..." is the dashed heading form, not a page counter.
    text = "1. Erstens ist das so.\n2. Zweitens auch.\n3 - Die Beschwerde ist abzuweisen.\n"
    nums = [p["e_number"] for p in eds.parse_erwaegungen_paragraphs(text)]
    assert nums == ["1", "2", "3"]
    assert not m._is_page_of_pages("3 - Die Beschwerde")
    assert not m._is_page_of_pages("8 - 6")  # page 8 of 6 is no page counter


# ── 3. no-break spaces and a prepended Abstract ─────────────────────────

TG_REGESTE = (
    "1. Die Schlichtungsbehörde schrieb das Verfahren wegen Anfechtung der Kündigung "
    "zufolge unentschuldigten Nichterscheinens der klagenden Partei als gegenstandslos ab. "
    "134 ZPO mindestens zehn Tage vor dem Erscheinungstermin zu versenden."
)
TG_TEXT = (
    "Thurgau Obergericht Rechenschaftsbericht 04.04.2012 RBOG 2012 Nr. 9 (ZR.2012.18)\n\n"
    + TG_REGESTE + "\n\n"
    "RBOG 2012 Nr. 9\nDie Vorladungsfrist ist zwingend einzuhalten\n(Art. 134 ZPO)\n"
    "1.     Die Schlichtungsbehörde schrieb das Verfahren als gegenstandslos ab. "
    "Der Kläger führte Beschwerde.\n"
    "2.     Vorladungen sind gemäss Art. 134 ZPO mindestens zehn Tage vor "
    "dem Erscheinungstermin zu versenden. Die Vorladungsfrist wurde nicht eingehalten.\n"
    "Obergericht, 1. Abteilung, 4. April 2012, ZR.2012.18\n"
)


def test_a_heading_with_no_break_spaces_is_found_and_served_verbatim():
    block = m._erwaegung_from_text(TG_TEXT, "2", TG_REGESTE)
    assert block["text"].startswith("2.     Vorladungen sind")
    assert "Obergericht, 1. Abteilung" in block["text"]  # cut from the served text
    assert m._pinpoint_in_text(TG_TEXT, "2")


def test_a_heading_inside_the_prepended_regeste_is_not_the_erwaegung():
    block = m._erwaegung_from_text(TG_TEXT, "1", TG_REGESTE)
    assert block["text"].startswith("1. ")
    assert "Der Kläger führte Beschwerde." in block["text"]
    assert "134 ZPO mindestens" not in block["text"]


def test_a_regeste_heading_is_still_taken_when_the_body_has_none():
    text = TG_REGESTE + "\n\nDer Rest des Entscheids hat keine Ziffern und ist lang genug."
    assert m._erwaegung_from_text(text, "1", TG_REGESTE)["text"].startswith("1. Die")


def test_parser_reads_headings_with_no_break_spaces():
    text = (
        "1.   Erstens hat der Kläger Beschwerde geführt.\n"
        "2.   Zweitens ist die Frist nicht eingehalten.\n"
        "2.1\u00a0\u00a0Die Vorladung kam zu spät.\n"
        "3.   Drittens ist der Entscheid aufzuheben.\n"
    )
    paras = {p["e_number"]: p["text"] for p in eds.extract(text, "de").erwaegungen_paragraphs}
    assert list(paras) == ["1", "2", "2.1", "3"]
    assert paras["2.1"] == "Die Vorladung kam zu spät."


def test_tg_ruling_with_padded_headings_gets_its_structure():
    paras = eds.paragraph_rows(eds.extract(TG_TEXT, "de").erwaegungen_paragraphs)[0]
    by_number = {p["e_number"]: p["text"] for p in paras}
    assert by_number["2"].startswith("Vorladungen sind gemäss Art.\u00a0134 ZPO")
    assert by_number["1"].startswith("Die Schlichtungsbehörde schrieb das Verfahren als")


def test_padding_does_not_move_erwaegungen_a_plain_parse_already_found():
    # The Anträge quoted in E. 1 are padded too; read as headings they made
    # "1." the Antrag (St. Gallen B 2013_29).
    text = (
        "Erwägungen:\n1. Die Beschwerdeführerin beantragt:\n"
        "1.\u00a0\u00a0 Der angefochtene Rekursentscheid sei aufzuheben.\n"
        "Das Gericht ist zuständig und der Antrag ist zulässig.\n"
        "2. Die Beschwerde ist begründet, weil die Frist nicht eingehalten wurde.\n"
        "3. Die Kosten trägt der Staat, weil er unterliegt.\n"
    )
    by_number = {p["e_number"]: p["text"] for p in
                 eds.paragraph_rows(eds.extract(text, "de").erwaegungen_paragraphs)[0]}
    assert by_number["1"].startswith("Die Beschwerdeführerin beantragt")


def test_a_range_of_days_is_not_a_heading_once_spaces_are_read():
    # St. Gallen writes "8. bis 16. Januar 2019: 9 Tage" in its facts.
    text = (
        "6. ​\nDarlehen\n6.1.\nNach Art. 62 Abs. 1 StG können Schuldzinsen abgezogen werden.\n"
        "7. ​\nAufenthalt\nEr weilte nicht in der Türkei:\n"
        "8. bis 16. Januar 2019: 9 Tage\n"
    )
    nums = [p["e_number"] for p in eds.parse_erwaegungen_paragraphs(text, plain_spaces=True)]
    assert nums == ["6", "6.1", "7"]


# ── 4. find_leading_cases topic filter ──────────────────────────────────

def _graph(path: Path) -> str:
    c = sqlite3.connect(path)
    c.executescript(
        """
        CREATE TABLE decisions(decision_id TEXT PRIMARY KEY, court TEXT, decision_date TEXT);
        CREATE TABLE statutes(statute_id INTEGER PRIMARY KEY, law_code TEXT, article TEXT);
        CREATE TABLE decision_statutes(decision_id TEXT, statute_id INTEGER, mention_count INTEGER);
        CREATE TABLE citation_targets(source_decision_id TEXT, target_ref TEXT,
                                      target_decision_id TEXT, match_type TEXT, confidence_score REAL);
        INSERT INTO statutes VALUES(1, 'ZPO', '194');
        """
    )
    for did in ("A", "B", "C", "S"):
        c.execute("INSERT INTO decisions VALUES(?,?,?)", (did, "bger", "2020-01-01"))
        c.execute("INSERT INTO decision_statutes VALUES(?,1,1)", (did,))
    for tgt in ("A", "B", "C"):
        c.execute("INSERT INTO citation_targets VALUES('S','r',?,'docket',0.9)", (tgt,))
    c.commit()
    c.close()
    return str(path)


def _decisions(path: Path) -> str:
    c = sqlite3.connect(path)
    c.executescript(
        """
        CREATE TABLE decisions(decision_id TEXT UNIQUE, court TEXT, canton TEXT,
            docket_number TEXT, language TEXT, title TEXT, regeste TEXT, full_text TEXT);
        CREATE VIRTUAL TABLE decisions_fts USING fts5(decision_id UNINDEXED, court, canton,
            docket_number, language, title, regeste, full_text,
            content=decisions, content_rowid=rowid, tokenize='unicode61 remove_diacritics 2');
        """
    )
    rows = [("filler%d" % i, "Rechtshilfe entraide ohne Bezug") for i in range(50)]
    rows += [("A", "Rechtshilfe nach Art. 194 ZPO, entraide judiciaire"),
             ("B", "Beweisaufnahme ohne Bezug"),
             ("C", "entraide entre tribunaux")]
    for did, text in rows:
        c.execute("INSERT INTO decisions(decision_id, court, full_text) VALUES(?,?,?)",
                  (did, "bger", text))
    c.execute("INSERT INTO decisions_fts(decisions_fts) VALUES('rebuild')")
    c.commit()
    c.close()
    return str(path)


def test_statute_topic_filter_is_keyed_by_rowid(tmp_path, monkeypatch):
    gp = _graph(tmp_path / "graph.db")
    dp = _decisions(tmp_path / "decisions.db")
    statements: list[str] = []

    def graph_conn():
        conn = sqlite3.connect(gp)
        conn.row_factory = sqlite3.Row
        return conn

    def db_conn():
        conn = sqlite3.connect(dp)
        conn.row_factory = sqlite3.Row
        conn.set_trace_callback(statements.append)
        return conn

    monkeypatch.setattr(m, "_get_graph_conn", graph_conn)
    monkeypatch.setattr(m, "get_db", db_conn)
    monkeypatch.setattr(m, "_fetch_decision_rows_by_ids", lambda ids: [])
    out = m._find_leading_cases(law_code="ZPO", article="194", query="entraide", limit=5)
    assert sorted(r["decision_id"] for r in out["results"]) == ["A", "C"]
    matches = [s for s in statements if "MATCH" in s]
    assert matches and all("rowid IN" in s for s in matches)
    assert not any("decision_id IN" in s for s in matches)


# ── 1. dates ────────────────────────────────────────────────────────────

def test_ai_own_line_gives_the_ruling_date_not_the_listing_date():
    assert own_decision_date(AI_TEXT, "K 7-2021") == date(2021, 11, 16)
    # The id may write the docket with hyphens, the line wraps, the date is numeric.
    wrapped = "…\nKantonsgericht Appenzell I.Rh., Entscheid KSE 15-\n2025 vom 25.11.2025"
    assert own_decision_date(wrapped, "kse-15-2025") == date(2025, 11, 25)
    # Another docket's line (the decision reviewed, the Federal Supreme Court's) is not it.
    other = "… mit Entscheid KE 6-2024 vom 26. September 2024 … Entscheid 4A_629/2021 vom 3. Januar 2022"
    assert own_decision_date(other, "K 7-2021") is None
    # A date before the docket's year is somebody else's.
    assert own_decision_date("Entscheid K 7-2021 vom 1. Mai 2009", "K 7-2021") is None


BGER_ROW = {
    "decision_id": "bger_7B.150_2001", "court": "bger", "docket_number": "7B.150/2001",
    "decision_date": "2001-05-17", "publication_date": "2001-08-14",
    "source_url": "http://relevancy.bger.ch/cgi-bin/JumpCGI?id=14.08.2001_7B.150/2001",
    "full_text": (
        "SCHULDBETREIBUNGS- UND KONKURSKAMMER\n\n14. August 2001\n\nIn Sachen\n\nA.________, "
        "Beschwerdeführer,\n\ngegen\n\nden Entscheid vom 17. Mai 2001 des Obergerichts des "
        "Kantons Luzern\n\n1.- Auf die Beschwerde wird nicht eingetreten.\n\n"
        "Lausanne, 14. August 2001\n\nIm Namen der Schuldbetreibungs- und Konkurskammer"
    ),
}


def test_bger_row_given_the_appealed_decision_s_date_is_restored():
    assert rcd.decide(dict(BGER_ROW)) == ("corrected", "2001-08-14")


def test_bger_restore_needs_publication_date_and_url_to_agree():
    row = dict(BGER_ROW, publication_date=None)
    assert rcd.decide(row)[1] is None
    row = dict(BGER_ROW, source_url="http://relevancy.bger.ch/cgi-bin/JumpCGI?id=15.08.2001_7B.150/2001")
    assert rcd.decide(row) == ("url_disagrees", None)
    row = dict(BGER_ROW, decision_date="2001-08-14")
    assert rcd.decide(row) == ("agrees", None)


def test_restore_rewrites_the_shard_once_and_stamps_the_change(tmp_path):
    ai_row = {"decision_id": "ai_gerichte_K_7-2021", "court": "ai_gerichte",
              "docket_number": "K 7-2021", "decision_date": "2022-01-19",
              "publication_date": None, "full_text": AI_TEXT}
    other = {"decision_id": "x", "court": "zh_gerichte", "decision_date": "2020-01-01"}
    shard = tmp_path / "s.jsonl"
    shard.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n"
                             for r in (BGER_ROW, ai_row, other)), encoding="utf-8")
    stats = rcd.run(shard, apply=True, examples=0)
    assert stats["rows_changed"] == 2 and stats["other_court"] == 1
    rows = {r["decision_id"]: r for r in map(json.loads, shard.read_text().splitlines())}
    assert rows["bger_7B.150_2001"]["decision_date"] == "2001-08-14"
    assert rows["bger_7B.150_2001"]["date_restore"]["from"] == "2001-05-17"
    ai = rows["ai_gerichte_K_7-2021"]
    assert ai["decision_date"] == "2021-11-16"
    assert ai["publication_date"] == "2022-01-19"      # the listing date
    assert rows["x"] == other
    again = rcd.run(shard, apply=True, examples=0)
    assert again["rows_changed"] == 0 and again["agrees"] == 2


def test_the_03_12_date_pass_refuses_bger_and_ai():
    from scripts import repair_decision_dates as rdd
    assert {"bger", "ai_gerichte"} <= rdd.NEVER_REPAIR_SHARDS
