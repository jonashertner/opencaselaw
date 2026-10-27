"""Verwaltungspraxis was in the corpus but undiscoverable, and its size was misreported.

A law firm evaluating the server in a Copilot agent asked whether we had any plans to
add Kreisschreiben and Wegleitungen. We had 790 of them. Their agent could not find
them: the server instructions never mentioned administrative practice and the
QUESTION -> TOOL ROUTING table had no row pointing at search_practice.

Two further defects in the same area:
  * `total` was `len(rows)`, i.e. capped by `limit`, so "how much guidance exists on
    this topic" was answered with the page size.
  * The inputSchema advertised sources/authorities/doc_types that return zero rows
    (SSK, ARE, EPA, handbuch, merkblatt) while omitting the 153 MWST documents.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import mcp_server  # noqa: E402


def _fixture_conn():
    """Practice DB with 25 matching rows, so total != page size is observable."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(
        """
        CREATE TABLE practice (
            doc_id TEXT, source TEXT, issuing_authority TEXT, doc_type TEXT,
            doc_number TEXT, title TEXT, date TEXT, language TEXT,
            url TEXT, pdf_url TEXT, body_text TEXT, topics_json TEXT, scraped_at TEXT
        );
        CREATE VIRTUAL TABLE practice_fts USING fts5(title, doc_number, body_text);
        """
    )
    for i in range(1, 26):
        c.execute(
            "INSERT INTO practice (rowid, doc_id, source, issuing_authority, doc_type, "
            "doc_number, title, date, language, url, pdf_url, body_text) VALUES "
            "(?,?,'estv_ks','ESTV','kreisschreiben',?,?,'2024-01-01','de',"
            "'http://x',?,'Verdecktes Eigenkapital nach Art. 65 DBG')",
            (i, f"d{i}", f"KS {i}", f"Kreisschreiben Nr. {i}", f"http://x/{i}.pdf"),
        )
        c.execute(
            "INSERT INTO practice_fts (rowid, title, doc_number, body_text) VALUES "
            "(?,?,?, 'Verdecktes Eigenkapital nach Art. 65 DBG')",
            (i, f"Kreisschreiben Nr. {i}", f"KS {i}"),
        )
    c.commit()
    return c


# ------------------------------------------------------------------ total

def test_total_is_corpus_count_not_page_size(monkeypatch):
    monkeypatch.setattr(mcp_server, "_open_practice_db", _fixture_conn)
    res = mcp_server._search_practice(query="Eigenkapital", limit=10)
    assert res["total"] == 25, res           # was 10 — the page size
    assert res["returned"] == 10
    assert len(res["results"]) == 10


def test_total_respects_filters(monkeypatch):
    monkeypatch.setattr(mcp_server, "_open_practice_db", _fixture_conn)
    hit = mcp_server._search_practice(query="Eigenkapital", issuing_authority="ESTV", limit=5)
    assert hit["total"] == 25 and hit["returned"] == 5
    miss = mcp_server._search_practice(query="Eigenkapital", issuing_authority="SEM", limit=5)
    assert miss["total"] == 0 and miss["results"] == []


def test_formatter_discloses_truncation(monkeypatch):
    monkeypatch.setattr(mcp_server, "_open_practice_db", _fixture_conn)
    out = mcp_server._format_search_practice_response(
        mcp_server._search_practice(query="Eigenkapital", limit=10))
    assert "Found 25" in out
    assert "showing the 10" in out


def test_pdf_rendered_as_markdown_link(monkeypatch):
    # Bare URLs are the form Microsoft documents as most likely to be stripped
    # by Copilot's @mention output sanitisation.
    monkeypatch.setattr(mcp_server, "_open_practice_db", _fixture_conn)
    out = mcp_server._format_search_practice_response(
        mcp_server._search_practice(query="Eigenkapital", limit=1))
    assert "](http://x/1.pdf)" in out


# ----------------------------------------------------------- discoverability

def _instructions() -> str:
    return mcp_server.server.instructions or ""


def test_instructions_mention_administrative_practice():
    ins = _instructions()
    for term in ("Verwaltungspraxis", "Kreisschreiben", "search_practice"):
        assert term in ins, f"{term!r} missing from server instructions"


def test_routing_table_routes_the_german_trigger_words():
    ins = _instructions()
    routing = ins[ins.index("QUESTION → TOOL ROUTING"):]
    for term in ("Kreisschreiben", "Weisung", "Wegleitung"):
        assert term in routing, f"{term!r} has no routing row"
    assert "search_practice" in routing
    # pre-2018 federal administrative decisions (VPB/JAAC) live in the
    # decision corpus, not in search_practice
    assert "VPB" in routing
    assert "search_decisions(court='ch_vb')" in routing


# ------------------------------------------------------------------ schema

def _search_practice_block() -> str:
    """The search_practice Tool(...) source, sliced up to the next tool so the
    window grows with the enums instead of a fixed character count."""
    src = Path(REPO / "mcp_server.py").read_text(encoding="utf-8")
    start = src.index('name="search_practice"')
    end = src.index('name="get_practice"', start)
    return src[start:end]


def test_schema_advertises_only_populated_values():
    """SSK / ARE / EPA / merkblatt return zero rows — do not offer them.
    (handbuch moved to the real list on 2026-09-02 with sem_handbuch_asyl.)"""
    block = _search_practice_block()
    for phantom in ("ssk_ks", "are_vollzug", "epa_personalrecht", "merkblatt"):
        assert phantom not in block, f"{phantom!r} is advertised but returns nothing"
    for real in ("seco_arg", "estv_ks", "estv_mwst", "bafu_vollzug", "sem_weisungen",
                 "bsv_weisungen", "seco_alv", "bag_kvg", "sem_handbuch_asyl", "bj_schkg",
                 "wegleitung", "kreisschreiben", "vollzugshilfe", "mwst_info", "handbuch"):
        assert real in block, f"{real!r} exists in the corpus but is not advertised"


def test_schema_names_the_uncovered_authorities():
    """The description must say what is absent, so a gap is not read as 'no
    guidance'. Since 2026-09-02 BSV/BAG/BJ are covered; the remaining gap is
    cantonal Sozialhilfe (SKOS, Handbücher) and Prämienverbilligung (IPV)."""
    [t] = [t for t in mcp_server._list_tools() if t.name == "search_practice"]
    d = t.description or ""
    gaps = d.split("NOT covered:", 1)[1]
    for absent in ("cantonal", "Sozialhilfe", "SKOS", "Prämienverbilligung"):
        assert absent in gaps, f"description does not disclose that {absent} is absent"
    from scrapers.practice import runner
    covered_now = ["BAG", "BJ", "FINMA"] + (["BSV"] if "bsv_weisungen" in runner.ENABLED_SCRAPERS else [])
    for covered in covered_now:
        assert covered in d.split("NOT covered:", 1)[0], covered
        assert covered not in gaps, f"{covered} is covered but listed as a gap"


# ------------------------------------------------------------- versions

def _versioned_conn():
    """Three editions of one FINMA circular + one unrelated document; all match."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(
        """
        CREATE TABLE practice (
            doc_id TEXT, source TEXT, issuing_authority TEXT, doc_type TEXT,
            doc_number TEXT, title TEXT, date TEXT, language TEXT,
            url TEXT, pdf_url TEXT, body_text TEXT, topics_json TEXT, scraped_at TEXT
        );
        CREATE VIRTUAL TABLE practice_fts USING fts5(title, doc_number, body_text);
        """
    )
    rows = [
        (1, "finma_rs_2008_21_v1_de", "finma_rs", "FINMA-RS 2008/21", "2008-11-20"),
        (2, "finma_rs_2008_21_v2_de", "finma_rs", "FINMA-RS 2008/21", "2017-01-25"),
        (3, "finma_rs_2008_21_v3_de", "finma_rs", "FINMA-RS 2008/21", "2019-10-31"),
        (4, "bsv_weisungen_6930_v20_de", "bsv_weisungen", "WEL", "2025-11-26"),
    ]
    for rowid, doc_id, source, num, date in rows:
        c.execute(
            "INSERT INTO practice (rowid, doc_id, source, issuing_authority, doc_type, "
            "doc_number, title, date, language, url, pdf_url, body_text) VALUES "
            "(?,?,?,'X','rundschreiben',?,?,?,'de','http://x','http://x.pdf','Geldwäscherei Risikoanalyse')",
            (rowid, doc_id, source, num, num, date),
        )
        c.execute("INSERT INTO practice_fts (rowid, title, doc_number, body_text) VALUES "
                  "(?,?,?, 'Geldwäscherei Risikoanalyse')", (rowid, num, num))
    c.commit()
    return c


def test_superseded_versions_collapse_to_newest_by_default(monkeypatch):
    monkeypatch.setattr(mcp_server, "_open_practice_db", _versioned_conn)
    res = mcp_server._search_practice(query="Geldwäscherei", limit=10)
    assert res["total"] == 4                      # matching rows, undiminished
    assert res["distinct_documents"] == 2
    assert res["returned"] == 2 and res["collapsed_versions"] == 2
    ids = [r["doc_id"] for r in res["results"]]
    assert "finma_rs_2008_21_v3_de" in ids and "bsv_weisungen_6930_v20_de" in ids
    assert "finma_rs_2008_21_v1_de" not in ids   # older editions gone
    out = mcp_server._format_search_practice_response(res)
    assert "2 superseded version(s) collapsed" in out and "include_superseded" in out


def test_include_superseded_returns_every_edition(monkeypatch):
    monkeypatch.setattr(mcp_server, "_open_practice_db", _versioned_conn)
    res = mcp_server._search_practice(query="Geldwäscherei", limit=10, include_superseded=True)
    assert res["returned"] == 4 and res["collapsed_versions"] == 0


def test_collapsing_never_reduces_a_full_page_of_distinct_documents(monkeypatch):
    monkeypatch.setattr(mcp_server, "_open_practice_db", _fixture_conn)
    res = mcp_server._search_practice(query="Eigenkapital", limit=10)
    assert res["returned"] == 10 and res["collapsed_versions"] == 0


def _heavily_versioned_conn():
    """3 documents × 30 editions with near-identical text — editions cluster at
    adjacent ranks, so a post-hoc window over the top rows would be swallowed
    by one document. Collapsing must happen before the LIMIT."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(
        """
        CREATE TABLE practice (
            doc_id TEXT, source TEXT, issuing_authority TEXT, doc_type TEXT,
            doc_number TEXT, title TEXT, date TEXT, language TEXT,
            url TEXT, pdf_url TEXT, body_text TEXT, topics_json TEXT, scraped_at TEXT
        );
        CREATE VIRTUAL TABLE practice_fts USING fts5(title, doc_number, body_text);
        """
    )
    rowid = 0
    for num in ("WEL", "RWL", "KSIH"):
        for v in range(1, 31):
            rowid += 1
            body = f"Ergänzungsleistungen Vermögensverzicht Version {v} " + ("Anrechnung " * (v % 3))
            c.execute(
                "INSERT INTO practice (rowid, doc_id, source, issuing_authority, doc_type, "
                "doc_number, title, date, language, url, pdf_url, body_text) VALUES "
                "(?,?,'bsv_weisungen','BSV','wegleitung',?,?,?,'de',?,'http://x.pdf',?)",
                (rowid, f"bsv_{num}_v{v}_de", num, num, f"{2000 + v}-01-01",
                 f"https://sozialversicherungen.admin.ch/de/d/{num}", body),
            )
            c.execute("INSERT INTO practice_fts (rowid, title, doc_number, body_text) VALUES (?,?,?,?)",
                      (rowid, num, num, body))
    c.commit()
    return c


def test_collapse_happens_before_the_limit(monkeypatch):
    monkeypatch.setattr(mcp_server, "_open_practice_db", _heavily_versioned_conn)
    res = mcp_server._search_practice(query="Vermögensverzicht", limit=3)
    assert res["total"] == 90 and res["distinct_documents"] == 3
    assert res["returned"] == 3 and res["collapsed_versions"] == 87
    assert {r["doc_number"] for r in res["results"]} == {"WEL", "RWL", "KSIH"}
    assert all(r["date"] == "2030-01-01" for r in res["results"])     # newest edition of each
    out = mcp_server._format_search_practice_response(res)
    assert out.startswith("Found 3 practice document(s) (90 matching version(s))")
    full = mcp_server._search_practice(query="Vermögensverzicht", limit=3, include_superseded=True)
    assert full["returned"] == 3 and full["collapsed_versions"] == 0 and full["total"] == 90


def _shared_abbreviation_conn():
    """BSV series share an abbreviation (three 'EVG-Urteile' digests); the
    per-document page url is the identity there, not doc_number."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(
        """
        CREATE TABLE practice (
            doc_id TEXT, source TEXT, issuing_authority TEXT, doc_type TEXT,
            doc_number TEXT, title TEXT, date TEXT, language TEXT,
            url TEXT, pdf_url TEXT, body_text TEXT, topics_json TEXT, scraped_at TEXT
        );
        CREATE VIRTUAL TABLE practice_fts USING fts5(title, doc_number, body_text);
        """
    )
    for rowid, doc in enumerate(("6302", "6303", "6304"), start=1):
        c.execute(
            "INSERT INTO practice (rowid, doc_id, source, issuing_authority, doc_type, doc_number, "
            "title, date, language, url, pdf_url, body_text) VALUES "
            "(?,?,'bsv_weisungen','BSV','rechtsprechung','EVG-Urteile',?,?,'de',?,'p','EVG Urteile Auswahl')",
            (rowid, f"bsv_weisungen_{doc}_v1_de", f"EVG-Urteile Liste {doc}", f"2007-0{rowid}-01",
             f"https://sozialversicherungen.admin.ch/de/d/{doc}"),
        )
        c.execute("INSERT INTO practice_fts (rowid, title, doc_number, body_text) VALUES (?,?,?,?)",
                  (rowid, f"EVG-Urteile Liste {doc}", "EVG-Urteile", "EVG Urteile Auswahl"))
    c.commit()
    return c


def test_bsv_series_sharing_an_abbreviation_are_distinct_documents(monkeypatch):
    monkeypatch.setattr(mcp_server, "_open_practice_db", _shared_abbreviation_conn)
    res = mcp_server._search_practice(query="Urteile", limit=10)
    assert res["distinct_documents"] == 3 and res["returned"] == 3 and res["collapsed_versions"] == 0
