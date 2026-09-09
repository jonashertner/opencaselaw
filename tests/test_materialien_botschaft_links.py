"""get_materialien must answer for laws outside the curated digest.

Found 2026-07-29 by probing all 42 tools with realistic arguments:
get_materialien("OR"), ("ZGB"), ("StGB"), ("DSG") all returned sources=[]
and search_materialien returned 0 for every query but "Datenschutz".

The cause was not missing data. The curated `materialien` digest covers two
laws (BV 128 articles, BGFA 39), and both tools read only that table — while
the same database holds 6,154 Federal Council messages and 19,809
article->Botschaft links across 620 SR numbers. For OR, ZGB and StGB the tool
was answering "No Materialien found" over a shelf full of them.

These tests use a fixture with the production schema and pin: the digest still
wins where it exists (no regression for BV/BGFA), the link fallback answers
where it does not, one message is listed once rather than once per article and
once per language, and the response says which corpus answered.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import mcp_server  # noqa: E402

# Verbatim opening paragraph shape of a real Botschaft (BBl 2017 399).
HEAD_OR = ("16.077\nBotschaft\nzur Änderung des Obligationenrechts\n(Aktienrecht)\n"
           "vom 23. November 2016\nSehr geehrte Frau Nationalratspräsidentin\n")
# A recent message: opens with the Übersicht, no header paragraph at all.
HEAD_NEW = ("Die vorliegende Botschaft umfasst vier Bundesbeschlüsse, welche die "
            "Übernahme und Umsetzung von fünf EU-Verordnungen betreffen.\n")


@pytest.fixture()
def mat_db(tmp_path, monkeypatch):
    p = tmp_path / "materialien.db"
    c = sqlite3.connect(p)
    c.executescript("""
        CREATE TABLE materialien (id INTEGER PRIMARY KEY AUTOINCREMENT,
            law_code TEXT NOT NULL, sr_number TEXT, article TEXT NOT NULL,
            bbl_ref TEXT NOT NULL, bbl_page_refs TEXT, legislative_intent TEXT,
            key_arguments TEXT, design_choices TEXT, rejected_alternatives TEXT,
            general_context TEXT);
        CREATE TABLE botschaft_documents (botschaft_id INTEGER PRIMARY KEY,
            bbl_year INTEGER NOT NULL, bbl_page INTEGER NOT NULL,
            bbl_citation TEXT NOT NULL, eli_uri TEXT, title TEXT,
            publication_date TEXT, source_url TEXT NOT NULL, format TEXT,
            language TEXT);
        CREATE TABLE botschaft_paragraphs (paragraph_id INTEGER PRIMARY KEY,
            botschaft_id INTEGER NOT NULL, para_order INTEGER NOT NULL,
            page_number INTEGER, section_path TEXT, article_anchor TEXT,
            text TEXT, text_length INTEGER);
        CREATE TABLE article_botschaft_links (sr_number TEXT NOT NULL,
            article TEXT NOT NULL, botschaft_id INTEGER NOT NULL,
            relation TEXT NOT NULL, evidence TEXT,
            PRIMARY KEY (sr_number, article, botschaft_id, relation));
        CREATE TABLE amendment_refs (sr_number TEXT, article TEXT, ref_type TEXT,
            year INTEGER, page INTEGER, fedlex_url TEXT, context TEXT);
        CREATE TABLE parliamentary_modifications (law_code TEXT, council TEXT,
            date TEXT, text TEXT);
        CREATE TABLE debate_pages (id INTEGER PRIMARY KEY, law_code TEXT,
            council TEXT, page_num INTEGER, text TEXT);
        CREATE VIRTUAL TABLE materialien_fts USING fts5(law_code, article,
            bbl_ref, legislative_intent, key_arguments, general_context);
        CREATE VIRTUAL TABLE debate_fts USING fts5(law_code, council, text);
    """)
    # BV Art. 8 has a curated digest (the corpus that already worked).
    c.execute("INSERT INTO materialien (law_code, sr_number, article, bbl_ref, "
              "legislative_intent) VALUES ('BV','101','8','BBl 1997 I 1',"
              "'Rechtsgleichheit')")
    # One OR message, published in DE and FR, touching two articles — the
    # shape that produced four rows for one document before grouping.
    c.executemany(
        "INSERT INTO botschaft_documents (botschaft_id, bbl_year, bbl_page, "
        "bbl_citation, eli_uri, title, publication_date, source_url, format, "
        "language) VALUES (?,?,?,?,?,NULL,NULL,?,'pdf',?)",
        [(1, 2017, 399, "BBl 2017 399", "eli/de", "https://x/de.pdf", "de"),
         (2, 2017, 399, "FF 2017 399", "eli/fr", "https://x/fr.pdf", "fr"),
         (3, 2025, 1478, "BBl 2025 1478", "eli/de2", "https://x/n.pdf", "de")])
    c.executemany(
        "INSERT INTO botschaft_paragraphs (botschaft_id, para_order, text) "
        "VALUES (?,1,?)", [(1, HEAD_OR), (2, HEAD_OR), (3, HEAD_NEW)])
    c.executemany(
        "INSERT INTO article_botschaft_links (sr_number, article, botschaft_id, "
        "relation, evidence) VALUES (?,?,?,'considered','amendment_refs:1')",
        [("220", "1077", 1), ("220", "620", 1), ("220", "1077", 2),
         ("220", "620", 2), ("220", "957", 3)])
    c.commit()
    c.close()
    monkeypatch.setattr(mcp_server, "MATERIALIEN_DB_PATH", p)
    return p


def test_curated_digest_still_wins_for_bv(mat_db):
    r = mcp_server.get_materialien("BV", "8")
    assert len(r["sources"]) == 1
    assert r["sources"][0]["legislative_intent"] == "Rechtsgleichheit"
    assert r["botschaft_documents"] == []
    assert "note" not in r          # a digest answered; no routing needed


def test_or_now_answers_from_the_link_table(mat_db):
    """The regression: this returned {"error": "No Materialien found for OR"}."""
    r = mcp_server.get_materialien("OR")
    assert "error" not in r
    assert r["sr_number"] == "220"
    assert r["sources"] == []
    docs = r["botschaft_documents"]
    assert docs, "OR must not answer 'no Materialien' when links exist"
    assert "no curated digest" in r["note"].lower()
    assert "search_botschaft" in r["note"]


def test_one_message_listed_once_with_its_articles_and_languages(mat_db):
    r = mcp_server.get_materialien("OR")
    aktien = [d for d in r["botschaft_documents"] if d["year"] == 2017]
    assert len(aktien) == 1, "one message per entry, not one per article/language"
    d = aktien[0]
    assert sorted(d["articles"]) == ["1077", "620"]
    assert d["language"] == "de" and d["bbl_citation"] == "BBl 2017 399"
    assert [o["bbl_citation"] for o in d["other_languages"]] == ["FF 2017 399"]


def test_title_comes_from_the_opening_paragraph(mat_db):
    """botschaft_documents.title is empty for all 6,154 production rows."""
    r = mcp_server.get_materialien("OR", "1077")
    d = r["botschaft_documents"][0]
    assert d["title"] == "Botschaft zur Änderung des Obligationenrechts (Aktienrecht)"
    assert d["date"] == "23. November 2016"
    assert "excerpt" not in d
    assert d["url"] and d["relation"] == "considered"


def test_message_without_a_header_falls_back_to_an_excerpt(mat_db):
    r = mcp_server.get_materialien("OR", "957")
    d = r["botschaft_documents"][0]
    assert d["title"] is None
    assert d["excerpt"].startswith("Die vorliegende Botschaft umfasst vier")


def test_article_filter_narrows_to_that_article(mat_db):
    r = mcp_server.get_materialien("OR", "1077")
    assert len(r["botschaft_documents"]) == 1
    assert r["botschaft_documents"][0]["articles"] == ["1077"]


def test_unknown_law_still_reports_honestly(mat_db):
    r = mcp_server.get_materialien("ZZZ")
    # The miss keeps the success shape, flags itself and still names the
    # better tool; nothing in it pretends to be Materialien.
    assert r.get("no_materialien") is True
    assert "search_botschaft" in r["note"]
    assert r["sources"] == [] and r["botschaft_documents"] == []


def test_article_with_no_link_routes_to_search_botschaft(mat_db):
    r = mcp_server.get_materialien("OR", "336")
    # nothing at all for this article -> honest error naming the better tool
    assert "search_botschaft" in (r.get("error") or r.get("note") or "")


def test_search_materialien_states_its_corpus_and_routes(mat_db):
    r = mcp_server.search_materialien("Werkvertrag")
    assert r["count"] == 0
    assert "BV" in r["corpus"] and "BGFA" in r["corpus"]
    assert "search_botschaft" in r["note"]
    assert "coverage limit" in r["note"]


def test_tool_descriptions_do_not_overstate_coverage():
    tools = {t.name: t.description for t in mcp_server._list_tools()}
    sm = tools["search_materialien"]
    assert "search_botschaft" in sm and "6,154" in sm
    gm = tools["get_materialien"]
    assert "botschaft_documents" in gm
    # the old text told callers sources=[] was the whole story
    assert "NOT that the law has no" in gm
