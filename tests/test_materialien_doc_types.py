"""Bundesblatt Materialien beyond the Botschaft (2026-09-09).

Fedlex tags every Bundesblatt work with a resource type. Until now the
pipeline discovered only typeDocument=23 (Botschaft des Bundesrates); the
Berichte and Stellungnahmen des Bundesrates, the parliamentary committee
reports, the Erlassentwürfe and the consultation openings sit on the same
file store and go through the same parse/anchor/FTS path. These tests pin:

  * discovery generalised to a type list (the type-23 wrapper keeps its
    tuple shape),
  * the additive ``botschaft_documents.doc_type`` column — migrated with a
    guarded ALTER TABLE, default 'botschaft' for every existing row,
  * ingest storing the slug, refusing off-Fedlex hosts and citation-page
    collisions,
  * the server: doc_type filter + payload on search_botschaft /
    get_materialien / get_article_purpose, Fedlex's own label in the
    document's language, and a materialien.db from before the column still
    answering (every row read as a Botschaft),
  * the systemd unit and the link enrichment carrying the type list.

Everything runs offline against fixture rows and tmp SQLite files.
"""
from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import mcp_server  # noqa: E402
from scrapers import fedlex_materialien as fm  # noqa: E402
from search_stack import build_botschaft_corpus as bbc  # noqa: E402

T23 = "https://fedlex.data.admin.ch/vocabulary/resource-type/23"
T25 = "https://fedlex.data.admin.ch/vocabulary/resource-type/25"
T33 = "https://fedlex.data.admin.ch/vocabulary/resource-type/33"


# ── discovery ─────────────────────────────────────────────────────────────


def _fake_rows():
    return [
        {"act": {"value": "https://fedlex.data.admin.ch/eli/fga/2011/1325"},
         "typeDoc": {"value": T23}, "title": {"value": "Botschaft VVG"},
         "memPage": {"value": "7705"}},
        {"act": {"value": "https://fedlex.data.admin.ch/eli/fga/2026/938"},
         "typeDoc": {"value": T25},
         "title": {"value": "Parlamentarische Initiative. Stellungnahme des Bundesrates"}},
        # tagged twice: Botschaft wins over the higher id
        {"act": {"value": "https://fedlex.data.admin.ch/eli/fga/2026/938"},
         "typeDoc": {"value": T23},
         "title": {"value": "Parlamentarische Initiative. Stellungnahme des Bundesrates"}},
        # a type we never ingest, even if the endpoint returned it
        {"act": {"value": "https://fedlex.data.admin.ch/eli/fga/2026/1"},
         "typeDoc": {"value": T33}, "title": {"value": "Mitteilung"}},
        # garbage URI
        {"act": {"value": "https://fedlex.data.admin.ch/garbage"},
         "typeDoc": {"value": T23}, "title": {"value": "noise"}},
    ]


def test_discover_fga_documents_two_types(monkeypatch):
    seen = {}

    def fake_sparql(q, timeout=120):
        seen["q"] = q
        return _fake_rows()

    monkeypatch.setattr(fm, "sparql_query", fake_sparql)
    docs = fm.discover_fga_documents(language="de", doc_types=(23, 25))
    # one VALUES clause naming both types, no per-type round trips
    assert "VALUES ?typeDoc" in seen["q"] and T23 in seen["q"] and T25 in seen["q"]
    by_uri = {d["eli_uri"]: d for d in docs}
    assert set(by_uri) == {
        "https://fedlex.data.admin.ch/eli/fga/2011/1325",
        "https://fedlex.data.admin.ch/eli/fga/2026/938",
    }
    vvg = by_uri["https://fedlex.data.admin.ch/eli/fga/2011/1325"]
    assert (vvg["year"], vvg["page"], vvg["doc_type"], vvg["type_id"]) == (2011, 7705, "botschaft", 23)
    assert vvg["title"] == "Botschaft VVG"
    st = by_uri["https://fedlex.data.admin.ch/eli/fga/2026/938"]
    # double-tagged work reported once, under the Botschaft type
    assert st["doc_type"] == "botschaft" and st["page"] == 938


def test_discover_fga_documents_single_type_implies_type(monkeypatch):
    rows = [{"act": {"value": "https://fedlex.data.admin.ch/eli/fga/2026/563"},
             "title": {"value": "Aussenpolitischer Bericht 2025"}}]
    monkeypatch.setattr(fm, "sparql_query", lambda q, timeout=120: rows)
    [d] = fm.discover_fga_documents(language="de", doc_types=(24,))
    assert d["doc_type"] == "bericht_bundesrat" and d["type_id"] == 24


def test_discover_fga_botschaften_keeps_tuple_shape(monkeypatch):
    monkeypatch.setattr(fm, "sparql_query", lambda q, timeout=120: _fake_rows())
    out = fm.discover_fga_botschaften(language="de")
    assert all(len(t) == 4 for t in out)
    assert (2011, 7705, "https://fedlex.data.admin.ch/eli/fga/2011/1325", "Botschaft VVG") in out
    # the wrapper asks for type 23 only, so the Stellungnahme row is reported
    # under its Botschaft tag and nothing else leaks in
    assert len(out) == 2


def test_discover_rejects_unknown_type_and_language(monkeypatch):
    monkeypatch.setattr(fm, "sparql_query", lambda q, timeout=120: [])
    with pytest.raises(ValueError):
        fm.discover_fga_documents(language="de", doc_types=(23, 99))
    with pytest.raises(ValueError):
        fm.discover_fga_documents(language="rm", doc_types=(23,))


def test_parse_doc_types():
    assert fm.parse_doc_types("23,24, 30,23") == (23, 24, 30)
    assert fm.parse_doc_types("") == (23,)
    assert fm.parse_doc_types(None) == (23,)
    with pytest.raises(ValueError):
        fm.parse_doc_types("23,33")


def test_type_labels_in_sync_between_scraper_and_server():
    """The server never imports scrapers/, so the label table is duplicated;
    this pins the copy to Fedlex's vocabulary as recorded in the scraper."""
    assert set(fm.FGA_DOC_TYPES.values()) == set(fm.FGA_DOC_TYPE_LABELS)
    assert mcp_server._MATERIALIEN_DOC_TYPE_LABELS == fm.FGA_DOC_TYPE_LABELS
    for slug, labels in fm.FGA_DOC_TYPE_LABELS.items():
        assert set(labels) == {"de", "fr", "it"}, slug
    assert mcp_server._materialien_doc_type_label("stellungnahme_bundesrat", "fr") == "Avis du Conseil fédéral"
    assert mcp_server._materialien_doc_type_label("bericht_kommission", "it") == "Rapporto di una Commissione parlamentare"
    # unknown language → DE; NULL slug → Botschaft
    assert mcp_server._materialien_doc_type_label(None, "rm") == "Botschaft des Bundesrates"


# ── schema migration ──────────────────────────────────────────────────────


_OLD_DOCUMENTS_DDL = """
CREATE TABLE botschaft_documents (
    botschaft_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    bbl_year        INTEGER NOT NULL,
    bbl_page        INTEGER NOT NULL,
    bbl_citation    TEXT NOT NULL,
    eli_uri         TEXT,
    title           TEXT,
    publication_date TEXT,
    source_url      TEXT NOT NULL,
    format          TEXT NOT NULL CHECK(format IN ('akoma-ntoso-xml','pdf')),
    language        TEXT NOT NULL,
    page_count      INTEGER,
    text_hash       TEXT,
    ingested_at     TEXT NOT NULL,
    UNIQUE(bbl_year, bbl_page, language)
);
"""


def _old_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.executescript(_OLD_DOCUMENTS_DDL)
    conn.execute(
        "INSERT INTO botschaft_documents (bbl_year, bbl_page, bbl_citation, eli_uri, "
        "source_url, format, language, ingested_at) VALUES (2024, 2485, 'BBl 2024 2485', "
        "'https://fedlex.data.admin.ch/eli/fga/2024/2485', 'https://fedlex.data.admin.ch/f.pdf', "
        "'pdf', 'de', '2026-05-11T10:00:00+00:00')")
    conn.commit()
    return conn


def test_ensure_schema_adds_doc_type_to_pre_existing_db(tmp_path):
    conn = _old_db(tmp_path / "m.db")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(botschaft_documents)")}
    assert "doc_type" not in cols
    bbc.ensure_schema(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(botschaft_documents)")}
    assert "doc_type" in cols
    # existing rows read as Botschaft, the index exists, and it is idempotent
    assert conn.execute("SELECT doc_type FROM botschaft_documents").fetchone()[0] == "botschaft"
    assert conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name='ix_bd_doctype'"
    ).fetchone()
    assert bbc.migrate_additive(conn) == []
    # the ALTER shows in sqlite_master, which is what build_materialien_db's
    # table copy replays before its INSERT … SELECT *
    ddl = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='botschaft_documents'").fetchone()[0]
    assert "doc_type" in ddl


def test_fresh_schema_has_doc_type(tmp_path):
    conn = sqlite3.connect(tmp_path / "fresh.db")
    bbc.ensure_schema(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(botschaft_documents)")}
    assert "doc_type" in cols
    assert bbc.migrate_additive(conn) == []


# ── ingest ────────────────────────────────────────────────────────────────


def _stub_fetch(monkeypatch, url="https://fedlex.data.admin.ch/filestore/x/1.pdf"):
    monkeypatch.setattr(bbc, "resolve_manifestation",
                        lambda eli, language="de", strict_language=True, skip_formats=(): (url, "pdf-a"))
    monkeypatch.setattr(bbc, "fetch_pdf_parts", lambda first_url, timeout=60, max_parts=200: [b"%PDF-stub"])
    monkeypatch.setattr(bbc, "extract_pdf_pages", lambda pdf_bytes: [
        "Bericht des Bundesrates\n\n"
        "Zu Art. 5\nDer Bundesrat legt in diesem Bericht die Grundlagen des Artikels dar.\n\n"
        "Allgemeine Ausführungen zum Vernehmlassungsergebnis und zu den Folgen."
    ])


def test_ingest_one_stores_doc_type_and_anchors(tmp_path, monkeypatch):
    _stub_fetch(monkeypatch)
    conn = sqlite3.connect(tmp_path / "m.db")
    bbc.ensure_schema(conn)
    bid = bbc.ingest_one(
        conn, year=2026, page=563, language="de", prefiltered=True,
        eli_uri="https://fedlex.data.admin.ch/eli/fga/2026/563",
        doc_type="bericht_bundesrat",
    )
    assert bid
    row = conn.execute(
        "SELECT doc_type, bbl_citation, eli_uri, format FROM botschaft_documents WHERE botschaft_id=?",
        (bid,)).fetchone()
    assert row == ("bericht_bundesrat", "BBl 2026 563",
                   "https://fedlex.data.admin.ch/eli/fga/2026/563", "pdf")
    anchors = {r[0] for r in conn.execute(
        "SELECT article_anchor FROM botschaft_paragraphs WHERE botschaft_id=?", (bid,))}
    assert "5" in anchors
    # FTS5 keeps up through the trigger
    assert conn.execute(
        "SELECT COUNT(*) FROM botschaft_paragraphs_fts WHERE botschaft_paragraphs_fts MATCH 'Bundesrat'"
    ).fetchone()[0] >= 1


def test_ingest_one_defaults_to_botschaft(tmp_path, monkeypatch):
    _stub_fetch(monkeypatch)
    conn = sqlite3.connect(tmp_path / "m.db")
    bbc.ensure_schema(conn)
    bid = bbc.ingest_one(conn, year=2024, page=2485, language="de", prefiltered=True,
                         eli_uri="https://fedlex.data.admin.ch/eli/fga/2024/2485")
    assert conn.execute("SELECT doc_type FROM botschaft_documents WHERE botschaft_id=?",
                        (bid,)).fetchone()[0] == "botschaft"


def test_ingest_one_rejects_unknown_doc_type(tmp_path, monkeypatch):
    _stub_fetch(monkeypatch)
    conn = sqlite3.connect(tmp_path / "m.db")
    with pytest.raises(ValueError):
        bbc.ingest_one(conn, year=2026, page=1, language="de", prefiltered=True,
                       doc_type="mitteilung")


def test_ingest_one_skips_files_off_fedlex(tmp_path, monkeypatch):
    """The BAR portal is robots-restricted and not part of this ingest;
    this path must never download from it."""
    _stub_fetch(monkeypatch,
                url="https://www.amtsdruckschriften.bar.admin.ch/viewOrigDoc.do?id=10000001&action=open")
    calls = []
    monkeypatch.setattr(bbc, "fetch_pdf_parts",
                        lambda *a, **k: calls.append(a) or [b"%PDF"])
    conn = sqlite3.connect(tmp_path / "m.db")
    bbc.ensure_schema(conn)
    assert bbc.ingest_one(conn, year=1995, page=1, language="de", prefiltered=True,
                          eli_uri="https://fedlex.data.admin.ch/eli/fga/1995/1_1_1",
                          doc_type="bericht_kommission") is None
    assert calls == []
    assert conn.execute("SELECT COUNT(*) FROM botschaft_documents").fetchone()[0] == 0
    assert bbc.file_host_allowed("https://fedlex.data.admin.ch/filestore/a.pdf")
    assert not bbc.file_host_allowed(None)


def test_ingest_one_refuses_citation_page_collision(tmp_path, monkeypatch):
    _stub_fetch(monkeypatch)
    conn = sqlite3.connect(tmp_path / "m.db")
    bbc.ensure_schema(conn)
    first = bbc.ingest_one(conn, year=2026, page=938, language="de", prefiltered=True,
                           eli_uri="https://fedlex.data.admin.ch/eli/fga/2026/938",
                           doc_type="stellungnahme_bundesrat")
    n_paras = conn.execute("SELECT COUNT(*) FROM botschaft_paragraphs").fetchone()[0]
    # a different work claiming the same gazette page is refused, not merged
    assert bbc.ingest_one(conn, year=2026, page=938, language="de", prefiltered=True,
                          eli_uri="https://fedlex.data.admin.ch/eli/fga/2026/999",
                          doc_type="bericht_kommission") is None
    rows = conn.execute("SELECT botschaft_id, eli_uri, doc_type FROM botschaft_documents").fetchall()
    assert rows == [(first, "https://fedlex.data.admin.ch/eli/fga/2026/938", "stellungnahme_bundesrat")]
    assert conn.execute("SELECT COUNT(*) FROM botschaft_paragraphs").fetchone()[0] == n_paras
    # the same work again is matched by ELI and left alone (unchanged hash)
    assert bbc.ingest_one(conn, year=2026, page=938, language="de", prefiltered=True,
                          eli_uri="https://fedlex.data.admin.ch/eli/fga/2026/938",
                          doc_type="stellungnahme_bundesrat") == first


# ── server ────────────────────────────────────────────────────────────────


_SERVER_DDL_COMMON = """
    CREATE TABLE botschaft_paragraphs (
        paragraph_id    INTEGER PRIMARY KEY AUTOINCREMENT,
        botschaft_id    INTEGER NOT NULL,
        para_order      INTEGER NOT NULL,
        page_number     INTEGER,
        section_path    TEXT,
        article_anchor  TEXT,
        text            TEXT NOT NULL,
        text_length     INTEGER NOT NULL
    );
    CREATE VIRTUAL TABLE botschaft_paragraphs_fts USING fts5(
        text, section_path, article_anchor UNINDEXED,
        content='botschaft_paragraphs', content_rowid='paragraph_id'
    );
    CREATE TRIGGER bp_ai AFTER INSERT ON botschaft_paragraphs BEGIN
        INSERT INTO botschaft_paragraphs_fts(rowid, text, section_path, article_anchor)
        VALUES (new.paragraph_id, new.text, new.section_path, new.article_anchor);
    END;
    CREATE TABLE article_botschaft_links (
        sr_number TEXT NOT NULL, article TEXT NOT NULL, botschaft_id INTEGER NOT NULL,
        relation TEXT NOT NULL, evidence TEXT,
        PRIMARY KEY (sr_number, article, botschaft_id, relation)
    );
    CREATE TABLE materialien (id INTEGER PRIMARY KEY AUTOINCREMENT,
        law_code TEXT NOT NULL, sr_number TEXT, article TEXT NOT NULL,
        bbl_ref TEXT NOT NULL, bbl_page_refs TEXT, legislative_intent TEXT,
        key_arguments TEXT, design_choices TEXT, rejected_alternatives TEXT,
        general_context TEXT);
    CREATE TABLE amendment_refs (id INTEGER PRIMARY KEY, sr_number TEXT, article TEXT,
        ref_type TEXT, year INTEGER, page INTEGER, fedlex_url TEXT, context TEXT);
    CREATE TABLE parliamentary_modifications (law_code TEXT, council TEXT,
        date TEXT, text TEXT);
"""


def _server_db(tmp_path, monkeypatch, with_doc_type: bool):
    p = tmp_path / "materialien.db"
    conn = sqlite3.connect(p)
    conn.executescript(_OLD_DOCUMENTS_DDL + _SERVER_DDL_COMMON)
    if with_doc_type:
        bbc.migrate_additive(conn)

    def doc(year, page, lang, eli, doc_type, head, anchored):
        cols = "bbl_year, bbl_page, bbl_citation, eli_uri, source_url, format, language, ingested_at"
        vals = [year, page, f"{'BBl' if lang == 'de' else 'FF'} {year} {page}", eli,
                f"https://fedlex.data.admin.ch/filestore/{year}-{page}.pdf", "pdf", lang, "2026-09-09T00:00:00+00:00"]
        if with_doc_type:
            cols += ", doc_type"
            vals.append(doc_type)
        cur = conn.execute(
            f"INSERT INTO botschaft_documents ({cols}) VALUES ({','.join('?' * len(vals))})", vals)
        bid = cur.lastrowid
        conn.execute(
            "INSERT INTO botschaft_paragraphs (botschaft_id, para_order, page_number, section_path, "
            "article_anchor, text, text_length) VALUES (?, 1, 1, 'Übersicht', NULL, ?, ?)",
            (bid, head, len(head)))
        conn.execute(
            "INSERT INTO botschaft_paragraphs (botschaft_id, para_order, page_number, section_path, "
            "article_anchor, text, text_length) VALUES (?, 2, 2, 'Zu Artikel 5', '5', ?, ?)",
            (bid, anchored, len(anchored)))
        conn.execute(
            "INSERT INTO article_botschaft_links VALUES ('220', '5', ?, 'considered', 'fixture')", (bid,))
        return bid

    b = doc(2024, 2485, "de", "https://fedlex.data.admin.ch/eli/fga/2024/2485", "botschaft",
            "16.077\nBotschaft\nzur Änderung des Obligationenrechts\nvom 23. November 2016\n",
            "Artikel 5 regelt den Vaterschaftsurlaub der Arbeitnehmenden im Betrieb.")
    st = doc(2026, 938, "fr", "https://fedlex.data.admin.ch/eli/fga/2026/938", "stellungnahme_bundesrat",
             "Avis du Conseil fédéral sur le rapport de la commission concernant le congé.",
             "L'article 5 traite du Vaterschaftsurlaub selon l'avis du Conseil fédéral.")
    conn.commit()
    conn.close()
    monkeypatch.setenv("SWISS_CASELAW_MATERIALIEN_DB", str(p))
    monkeypatch.setattr(mcp_server, "MATERIALIEN_DB_PATH", p)
    return p, b, st


def test_search_botschaft_filters_by_doc_type_and_labels_hits(tmp_path, monkeypatch):
    _server_db(tmp_path, monkeypatch, with_doc_type=True)
    out = mcp_server._handle_search_botschaft(query="Vaterschaftsurlaub")
    assert out["total"] == 2 and out["doc_type_filter"] is None
    by_type = {r["doc_type"]: r for r in out["results"]}
    assert set(by_type) == {"botschaft", "stellungnahme_bundesrat"}
    # Fedlex's label in the document's own language
    assert by_type["botschaft"]["doc_type_label"] == "Botschaft des Bundesrates"
    assert by_type["stellungnahme_bundesrat"]["doc_type_label"] == "Avis du Conseil fédéral"

    out = mcp_server._handle_search_botschaft(query="Vaterschaftsurlaub",
                                             doc_type="stellungnahme_bundesrat")
    assert out["total"] == 1 and out["doc_type_filter"] == "stellungnahme_bundesrat"
    assert out["results"][0]["bbl_citation"] == "FF 2026 938"
    # case-insensitive, 'all' means no filter
    assert mcp_server._handle_search_botschaft(query="Vaterschaftsurlaub", doc_type="BOTSCHAFT")["total"] == 1
    assert mcp_server._handle_search_botschaft(query="Vaterschaftsurlaub", doc_type="all")["total"] == 2
    # filter with no hit says the filter was applied
    out = mcp_server._handle_search_botschaft(query="Vaterschaftsurlaub", doc_type="erlassentwurf")
    assert out["total"] == 0 and "doc_type" in out["_hint"]


def test_search_botschaft_rejects_unknown_doc_type(tmp_path, monkeypatch):
    _server_db(tmp_path, monkeypatch, with_doc_type=True)
    out = mcp_server._handle_search_botschaft(query="Vaterschaftsurlaub", doc_type="mitteilung")
    assert "error" in out and "bericht_bundesrat" in out["error"]


def test_search_botschaft_on_db_without_doc_type_column(tmp_path, monkeypatch):
    """A materialien.db from before the column: every row is a Botschaft."""
    _server_db(tmp_path, monkeypatch, with_doc_type=False)
    out = mcp_server._handle_search_botschaft(query="Vaterschaftsurlaub")
    assert out["total"] == 2
    assert {r["doc_type"] for r in out["results"]} == {"botschaft"}
    labels = {r["language"]: r["doc_type_label"] for r in out["results"]}
    assert labels == {"de": "Botschaft des Bundesrates", "fr": "Message du Conseil fédéral"}
    assert mcp_server._handle_search_botschaft(query="Vaterschaftsurlaub", doc_type="botschaft")["total"] == 2
    assert mcp_server._handle_search_botschaft(query="Vaterschaftsurlaub", doc_type="bericht_bundesrat")["total"] == 0


def test_get_materialien_lists_doc_type_and_filters(tmp_path, monkeypatch):
    _server_db(tmp_path, monkeypatch, with_doc_type=True)
    out = mcp_server.get_materialien("OR", "5")
    assert out["sr_number"] == "220" and out["doc_type_filter"] is None
    docs = {d["doc_type"]: d for d in out["botschaft_documents"]}
    assert set(docs) == {"botschaft", "stellungnahme_bundesrat"}
    assert docs["botschaft"]["doc_type_label"] == "Botschaft des Bundesrates"
    assert docs["stellungnahme_bundesrat"]["doc_type_label"] == "Avis du Conseil fédéral"
    assert docs["stellungnahme_bundesrat"]["language"] == "fr"

    out = mcp_server.get_materialien("OR", "5", doc_type="botschaft")
    assert out["doc_type_filter"] == "botschaft"
    assert [d["doc_type"] for d in out["botschaft_documents"]] == ["botschaft"]
    assert "error" in mcp_server.get_materialien("OR", "5", doc_type="mitteilung")


def test_get_materialien_on_db_without_doc_type_column(tmp_path, monkeypatch):
    _server_db(tmp_path, monkeypatch, with_doc_type=False)
    out = mcp_server.get_materialien("OR", "5")
    assert {d["doc_type"] for d in out["botschaft_documents"]} == {"botschaft"}


def test_get_article_purpose_carries_doc_type_botschaft_first(tmp_path, monkeypatch):
    _server_db(tmp_path, monkeypatch, with_doc_type=True)
    # the Stellungnahme is the only FR document; the DE answer is the Botschaft
    out = mcp_server._handle_get_article_purpose(sr_number="220", article="5", language="fr")
    [src] = out["sources"]
    assert src["doc_type"] == "stellungnahme_bundesrat"
    assert src["doc_type_label"] == "Avis du Conseil fédéral"
    assert src["paragraphs"][0]["text"].startswith("L'article 5")
    out = mcp_server._handle_get_article_history(sr_number="220", article="5", language="de")
    [entry] = [e for e in out["timeline"] if e["kind"] == "botschaft"]
    assert entry["doc_type"] == "botschaft"
    assert entry["doc_type_label"] == "Botschaft des Bundesrates"


def test_get_article_purpose_orders_botschaft_before_other_types(tmp_path, monkeypatch):
    p, b, st = _server_db(tmp_path, monkeypatch, with_doc_type=True)
    conn = sqlite3.connect(p)
    # add a DE Erlassentwurf linked to the same article, newer than the Botschaft
    cur = conn.execute(
        "INSERT INTO botschaft_documents (bbl_year, bbl_page, bbl_citation, eli_uri, source_url, "
        "format, language, ingested_at, doc_type) VALUES (2026, 952, 'BBl 2026 952', "
        "'https://fedlex.data.admin.ch/eli/fga/2026/952', 'https://fedlex.data.admin.ch/f.pdf', "
        "'pdf', 'de', '2026-09-09T00:00:00+00:00', 'erlassentwurf')")
    eid = cur.lastrowid
    conn.execute(
        "INSERT INTO botschaft_paragraphs (botschaft_id, para_order, page_number, section_path, "
        "article_anchor, text, text_length) VALUES (?, 1, 1, NULL, '5', ?, 60)",
        (eid, "Art. 5 Der Arbeitnehmer hat Anspruch auf Vaterschaftsurlaub."))
    conn.execute("INSERT INTO article_botschaft_links VALUES ('220', '5', ?, 'considered', 'fixture')", (eid,))
    conn.commit(); conn.close()
    out = mcp_server._handle_get_article_purpose(sr_number="220", article="5", language="de")
    assert [s["doc_type"] for s in out["sources"]] == ["botschaft", "erlassentwurf"]


def test_tool_schemas_expose_doc_type():
    tools = {t.name: t for t in mcp_server._list_tools()}
    for name in ("search_botschaft", "get_materialien", "search_materialien"):
        prop = tools[name].inputSchema["properties"]["doc_type"]
        assert prop["enum"] == list(mcp_server._MATERIALIEN_DOC_TYPES)
        assert len(tools[name].description) <= 1024
    assert "doc_type" in tools["search_botschaft"].description
    assert "Berichte" in tools["search_materialien"].description
    # symmetry, documented as a no-op on the digest
    sm = tools["search_materialien"].inputSchema["properties"]["doc_type"]["description"]
    assert "NO-OP" in sm and "search_botschaft" in sm


# ── unit file + enrichment ────────────────────────────────────────────────


def test_systemd_unit_passes_the_type_list():
    unit = (REPO / "systemd" / "opencaselaw-materialien.service").read_text()
    flags = re.findall(r"--types ([\d,]+)", unit)
    # three ingest runs (de/fr/it) + the link enrichment
    assert len(flags) == 4 and set(flags) == {"23,24,25,30,38,39,40"}
    assert set(int(t) for t in flags[0].split(",")) == set(fm.FGA_DOC_TYPES)
    # fetch policy: every language run is capped, the rate stays at 0.5 s
    # (the flag as passed, not the comment that explains it)
    assert re.findall(r"--max-per-run (\d+) \\", unit) == ["1500"] * 3
    assert unit.count("--rate-limit 0.5") == 3


def test_enrich_chain_query_covers_types():
    sys.path.insert(0, str(REPO / "scripts"))
    import enrich_botschaft_links as ebl
    q = ebl.build_chain_query((23, 25, 38))
    assert "VALUES ?td" in q
    for t in (23, 25, 38):
        assert fm.fga_type_uri(t) in q
    assert "hasResultingLegalResource" in q and "parliamentDraftId" in q
