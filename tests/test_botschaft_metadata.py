"""Botschaft document metadata: title and publication_date populated end to end.

Found 2026-09-09: every one of the 6,166 rows in the live materialien.db had
title and publication_date NULL, although Fedlex SPARQL discovery returned the
title and the Work carries jolux:dateDocument / jolux:publicationDate (verified
for eli/fga/2011/1325, 2013/823 and 2025/960). These tests pin:

* discovery parses title + both dates from the SPARQL rows (fixture rows, no
  network), and the 4-tuple ``discover_fga_botschaften`` view is unchanged;
* ``ensure_schema`` adds date_document / date_source to an older DB in place;
* ``ingest_one`` writes title / publication_date / date_document / date_source,
  falls back to the BBl year with date_source='bbl_year', and heals an
  existing row whose text is unchanged;
* the backfill batches ELIs per language, updates in place, is idempotent,
  and writes nothing under --dry-run;
* the three server tools carry the fields (Fedlex title wins over the header
  regex in get_materialien) and the tool descriptions say so.
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
from scrapers import fedlex_materialien  # noqa: E402
from search_stack import build_botschaft_corpus as bbc  # noqa: E402
from scripts import backfill_botschaft_metadata as bf  # noqa: E402

ELI_VVG = "https://fedlex.data.admin.ch/eli/fga/2011/1325"
ELI_WEBI = "https://fedlex.data.admin.ch/eli/fga/2013/823"

# Shape of the live SPARQL answer (2026-09-09), trailing blank in the title
# included: Fedlex titles carry one.
SPARQL_ROWS = [
    {
        "act": {"value": ELI_VVG},
        "title": {"value": "Botschaft zur Totalrevision des Versicherungsvertragsgesetzes "},
        "memPage": {"value": "7705"},
        "dateDoc": {"value": "2011-09-07"},
        "pubDate": {"value": "2011-10-25"},
    },
    {
        # No publicationDate: dateDocument must feed publication_date.
        "act": {"value": ELI_WEBI},
        "title": {"value": "Botschaft zum Bundesgesetz über die Weiterbildung"},
        "memPage": {"value": "3729"},
        "dateDoc": {"value": "2013-05-15"},
    },
    {
        # Neither date, no title: only the BBl year is left.
        "act": {"value": "https://fedlex.data.admin.ch/eli/fga/1999/6013"},
        "memPage": {"value": "6013"},
    },
]

AKN = b"""<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
<act><body><chapter><heading>Allgemeiner Teil</heading>
<p>Das ist ein Einleitungsabsatz mit ausreichend Inhalt zum Testen.</p>
</chapter></body></act></akomaNtoso>"""


# ── discovery ─────────────────────────────────────────────────────────


def test_discovery_meta_parses_title_and_both_dates(monkeypatch):
    monkeypatch.setattr(fedlex_materialien, "sparql_query", lambda q, timeout=120: SPARQL_ROWS)
    out = {m["eli_uri"]: m for m in fedlex_materialien.discover_fga_botschaften_meta("de")}
    vvg = out[ELI_VVG]
    assert vvg["year"] == 2011 and vvg["page"] == 7705
    assert vvg["title"] == "Botschaft zur Totalrevision des Versicherungsvertragsgesetzes"
    assert vvg["date_document"] == "2011-09-07"
    assert vvg["publication_date"] == "2011-10-25"
    assert out[ELI_WEBI]["publication_date"] is None
    assert out[ELI_WEBI]["date_document"] == "2013-05-15"
    bare = out["https://fedlex.data.admin.ch/eli/fga/1999/6013"]
    assert bare["title"] is None and bare["publication_date"] is None


def test_discovery_query_asks_for_the_verified_predicates():
    q = fedlex_materialien._fga_meta_query("fr")
    assert "jolux:dateDocument" in q and "jolux:publicationDate" in q
    assert "jolux:title" in q and "language/FRA" in q
    assert "resource-type/23" in q


def test_four_tuple_view_is_unchanged(monkeypatch):
    monkeypatch.setattr(fedlex_materialien, "sparql_query", lambda q, timeout=120: SPARQL_ROWS)
    out = fedlex_materialien.discover_fga_botschaften("de")
    assert (2011, 7705, ELI_VVG, "Botschaft zur Totalrevision des Versicherungsvertragsgesetzes") in out
    assert all(len(t) == 4 for t in out)


def test_fetch_fga_metadata_batches_by_values_list(monkeypatch):
    seen = []

    def fake(q, timeout=120):
        seen.append(q)
        return SPARQL_ROWS[:2]

    monkeypatch.setattr(fedlex_materialien, "sparql_query", fake)
    meta = fedlex_materialien.fetch_fga_metadata([ELI_VVG, ELI_WEBI, ELI_VVG], language="de")
    assert len(seen) == 1
    assert f"VALUES ?act {{ <{ELI_VVG}> <{ELI_WEBI}> }}" in seen[0]
    assert "typeDocument" not in seen[0]      # pre-1999 works may lack the type
    assert meta[ELI_VVG]["publication_date"] == "2011-10-25"
    assert meta[ELI_WEBI]["title"] == "Botschaft zum Bundesgesetz über die Weiterbildung"
    assert fedlex_materialien.fetch_fga_metadata([], language="de") == {}


# ── schema + ingest ───────────────────────────────────────────────────


def _old_schema_db(path: Path) -> sqlite3.Connection:
    """A DB created by the pre-2026-09 SCHEMA_SQL (no date_document/date_source)."""
    conn = sqlite3.connect(path)
    old = bbc.SCHEMA_SQL
    conn.executescript(old)
    return conn


def test_ensure_schema_adds_columns_in_place(tmp_path):
    conn = _old_schema_db(tmp_path / "m.db")
    before = {r[1] for r in conn.execute("PRAGMA table_info(botschaft_documents)")}
    assert "date_source" not in before
    bbc.ensure_schema(conn)
    bbc.ensure_schema(conn)  # idempotent
    after = {r[1] for r in conn.execute("PRAGMA table_info(botschaft_documents)")}
    assert {"title", "publication_date", "date_document", "date_source"} <= after
    # existing columns untouched: the server's readers keep working
    assert before <= after


def test_resolve_publication_date_prefers_fedlex_then_bbl_year():
    assert bbc.resolve_publication_date("2011-10-25", "2011-09-07", 2011) == ("2011-10-25", "fedlex:publicationDate")
    assert bbc.resolve_publication_date(None, "2013-05-15", 2013) == ("2013-05-15", "fedlex:dateDocument")
    assert bbc.resolve_publication_date(None, None, 1999) == ("1999", "bbl_year")


@pytest.fixture()
def ingest_env(monkeypatch, tmp_path):
    monkeypatch.setattr(bbc, "resolve_manifestation",
                        lambda eli, language="de", **kw: ("https://fedlex.data.admin.ch/x.xml", "xml-an"))
    monkeypatch.setattr(bbc, "fetch_pdf_bytes", lambda url, timeout=60: AKN)
    conn = sqlite3.connect(tmp_path / "m.db")
    bbc.ensure_schema(conn)
    return conn


def _doc(conn, year, page, lang="de"):
    conn.row_factory = sqlite3.Row
    return conn.execute(
        "SELECT * FROM botschaft_documents WHERE bbl_year=? AND bbl_page=? AND language=?",
        (year, page, lang)).fetchone()


def test_ingest_stores_title_and_dates(ingest_env):
    conn = ingest_env
    bid = bbc.ingest_one(conn, 2011, 7705, language="de", eli_uri=ELI_VVG, prefiltered=True,
                         title="Botschaft zur Totalrevision des Versicherungsvertragsgesetzes",
                         publication_date="2011-10-25", date_document="2011-09-07")
    assert bid
    d = _doc(conn, 2011, 7705)
    assert d["title"] == "Botschaft zur Totalrevision des Versicherungsvertragsgesetzes"
    assert d["publication_date"] == "2011-10-25"
    assert d["date_document"] == "2011-09-07"
    assert d["date_source"] == "fedlex:publicationDate"
    assert conn.execute("SELECT COUNT(*) FROM botschaft_paragraphs").fetchone()[0] == 1


def test_ingest_falls_back_to_bbl_year_and_says_so(ingest_env):
    conn = ingest_env
    bbc.ingest_one(conn, 1999, 6013, language="fr", eli_uri="https://fedlex.data.admin.ch/eli/fga/1999/6013",
                   prefiltered=True)
    d = _doc(conn, 1999, 6013, "fr")
    assert d["title"] is None
    assert d["publication_date"] == "1999" and d["date_source"] == "bbl_year"


def test_ingest_heals_an_unchanged_row_and_never_downgrades(ingest_env):
    conn = ingest_env
    bbc.ingest_one(conn, 2013, 3729, language="de", eli_uri=ELI_WEBI, prefiltered=True)
    d = _doc(conn, 2013, 3729)
    assert d["title"] is None and d["date_source"] == "bbl_year"
    # Same text (hash unchanged) — the nightly delta passes metadata now.
    bbc.ingest_one(conn, 2013, 3729, language="de", eli_uri=ELI_WEBI, prefiltered=True,
                   title="Botschaft zum Bundesgesetz über die Weiterbildung", date_document="2013-05-15")
    d = _doc(conn, 2013, 3729)
    assert d["title"] == "Botschaft zum Bundesgesetz über die Weiterbildung"
    assert d["publication_date"] == "2013-05-15" and d["date_source"] == "fedlex:dateDocument"
    # A later run without metadata must not blank it or downgrade to the year.
    bbc.ingest_one(conn, 2013, 3729, language="de", eli_uri=ELI_WEBI, prefiltered=True)
    d = _doc(conn, 2013, 3729)
    assert d["title"] == "Botschaft zum Bundesgesetz über die Weiterbildung"
    assert d["publication_date"] == "2013-05-15"
    assert conn.execute("SELECT COUNT(*) FROM botschaft_documents").fetchone()[0] == 1


def test_language_specific_titles_are_kept_apart(ingest_env):
    conn = ingest_env
    bbc.ingest_one(conn, 2011, 7705, language="de", eli_uri=ELI_VVG, prefiltered=True,
                   title="Botschaft zur Totalrevision des Versicherungsvertragsgesetzes")
    bbc.ingest_one(conn, 2011, 7091, language="fr", eli_uri=ELI_VVG, prefiltered=True,
                   title="Message relatif à la révision totale de la loi sur le contrat d'assurance")
    assert _doc(conn, 2011, 7705, "de")["title"].startswith("Botschaft")
    assert _doc(conn, 2011, 7091, "fr")["title"].startswith("Message")


# ── backfill ──────────────────────────────────────────────────────────


@pytest.fixture()
def null_rows_db(tmp_path):
    """Production-shaped rows as they are today: metadata all NULL, the
    date_document/date_source columns not yet present."""
    p = tmp_path / "materialien.db"
    conn = _old_schema_db(p)
    rows = []
    for i in range(120):   # 120 DE rows -> three batches of 50
        rows.append((2000 + i % 20, 100 + i, f"BBl {2000 + i % 20} {100 + i}",
                     f"https://fedlex.data.admin.ch/eli/fga/{2000 + i % 20}/{100 + i}", "de"))
    rows.append((2011, 7091, "FF 2011 7091", ELI_VVG, "fr"))
    conn.executemany(
        "INSERT INTO botschaft_documents (bbl_year, bbl_page, bbl_citation, eli_uri, "
        "source_url, format, language, ingested_at) VALUES (?,?,?,?,'https://x','pdf',?,'t')",
        rows)
    conn.commit()
    conn.close()
    return p


def _fake_fetch(calls):
    def fetch(eli_uris, language="de", timeout=120):
        calls.append((language, list(eli_uris)))
        out = {}
        for u in eli_uris:
            if u == ELI_VVG:
                out[u] = {"title": "Message relatif à la révision totale de la LCA",
                          "date_document": "2011-09-07", "publication_date": "2011-10-25"}
            elif u.endswith("/119"):
                pass                       # Fedlex has no expression: unanswered
            elif u.endswith("/118"):
                out[u] = {"title": "Botschaft ohne Datum", "date_document": None,
                          "publication_date": None}
            else:
                out[u] = {"title": f"Botschaft {u.rsplit('/', 1)[-1]}",
                          "date_document": None, "publication_date": "2020-01-02"}
        return out
    return fetch


def test_backfill_batches_per_language_and_updates_in_place(null_rows_db):
    calls: list = []
    stats = bf.run(null_rows_db, batch_size=50, rate_limit=0, fetch=_fake_fetch(calls), sleep=lambda s: None)
    assert [(lang, len(u)) for lang, u in calls] == [("de", 50), ("de", 50), ("de", 20), ("fr", 1)]
    assert stats["candidates"] == 121 and stats["batches"] == 4
    conn = sqlite3.connect(null_rows_db)
    conn.row_factory = sqlite3.Row
    cols = {r[1] for r in conn.execute("PRAGMA table_info(botschaft_documents)")}
    assert {"date_document", "date_source"} <= cols
    fr = conn.execute("SELECT * FROM botschaft_documents WHERE language='fr'").fetchone()
    assert fr["title"].startswith("Message") and fr["publication_date"] == "2011-10-25"
    assert fr["date_document"] == "2011-09-07" and fr["date_source"] == "fedlex:publicationDate"
    no_date = conn.execute("SELECT * FROM botschaft_documents WHERE bbl_page=118").fetchone()
    assert no_date["title"] == "Botschaft ohne Datum"
    assert no_date["publication_date"] == str(no_date["bbl_year"]) and no_date["date_source"] == "bbl_year"
    unanswered = conn.execute("SELECT * FROM botschaft_documents WHERE bbl_page=119").fetchone()
    assert unanswered["title"] is None and unanswered["date_source"] == "bbl_year"
    filled = conn.execute("SELECT COUNT(*) FROM botschaft_documents WHERE title IS NOT NULL").fetchone()[0]
    assert filled == 120
    # everything else on the row is untouched
    assert conn.execute("SELECT COUNT(*) FROM botschaft_documents WHERE source_url='https://x'").fetchone()[0] == 121


def test_backfill_is_idempotent(null_rows_db):
    calls: list = []
    bf.run(null_rows_db, batch_size=50, rate_limit=0, fetch=_fake_fetch(calls), sleep=lambda s: None)
    n_first = len(calls)
    calls.clear()
    stats = bf.run(null_rows_db, batch_size=50, rate_limit=0, fetch=_fake_fetch(calls), sleep=lambda s: None)
    # Only the one row Fedlex never answered (title still NULL) is re-asked.
    assert stats["candidates"] == 1 and len(calls) == 1 and n_first == 4
    conn = sqlite3.connect(null_rows_db)
    assert conn.execute("SELECT COUNT(*) FROM botschaft_documents WHERE title IS NOT NULL").fetchone()[0] == 120


def test_backfill_dry_run_writes_nothing(null_rows_db):
    calls: list = []
    before = null_rows_db.read_bytes()
    stats = bf.run(null_rows_db, dry_run=True, batch_size=50, rate_limit=0,
                   fetch=_fake_fetch(calls), sleep=lambda s: None)
    # 121 planned: 120 titles plus the year fallback for the unanswered row.
    assert stats["dry_run"] and stats["updated"] == 121 and len(calls) == 4
    assert null_rows_db.read_bytes() == before
    conn = sqlite3.connect(null_rows_db)
    assert conn.execute("SELECT COUNT(*) FROM botschaft_documents WHERE title IS NOT NULL").fetchone()[0] == 0
    assert "date_source" not in {r[1] for r in conn.execute("PRAGMA table_info(botschaft_documents)")}


def test_backfill_rate_limits_between_batches(null_rows_db):
    slept: list = []
    bf.run(null_rows_db, batch_size=50, rate_limit=2.5, fetch=_fake_fetch([]), sleep=slept.append)
    assert slept == [2.5, 2.5, 2.5]      # between the four batches, not before the first


# ── server payloads ───────────────────────────────────────────────────

HEAD_OR = ("16.077\nBotschaft\nzur Änderung des Obligationenrechts\n(Aktienrecht)\n"
           "vom 23. November 2016\nSehr geehrte Frau Nationalratspräsidentin\n")


@pytest.fixture()
def server_db(tmp_path, monkeypatch):
    p = tmp_path / "materialien.db"
    c = sqlite3.connect(p)
    c.executescript(bbc.SCHEMA_SQL)
    bbc.ensure_schema(c)
    c.executescript("""
        CREATE TABLE materialien (id INTEGER PRIMARY KEY AUTOINCREMENT,
            law_code TEXT NOT NULL, sr_number TEXT, article TEXT NOT NULL,
            bbl_ref TEXT NOT NULL, bbl_page_refs TEXT, legislative_intent TEXT,
            key_arguments TEXT, design_choices TEXT, rejected_alternatives TEXT,
            general_context TEXT);
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
    c.executemany(
        "INSERT INTO botschaft_documents (botschaft_id, bbl_year, bbl_page, bbl_citation, "
        "eli_uri, title, publication_date, date_document, date_source, source_url, format, "
        "language, ingested_at) VALUES (?,?,?,?,?,?,?,?,?,?,'pdf',?,'t')",
        [(1, 2017, 399, "BBl 2017 399", "eli/de",
          "Botschaft zur Änderung des Obligationenrechts (Aktienrecht)",
          "2017-01-24", "2016-11-23", "fedlex:publicationDate", "https://x/de.pdf", "de"),
         (2, 2017, 399, "FF 2017 399", "eli/fr", None, None, None, None, "https://x/fr.pdf", "fr"),
         (3, 2025, 1478, "BBl 2025 1478", "eli/de2", None, None, None, None, "https://x/n.pdf", "de"),
         (4, 1997, 1, "BBl 1997 1", "eli/bv", "Botschaft über eine neue Bundesverfassung",
          "1997-01-14", "1996-11-20", "fedlex:publicationDate", "https://x/bv.pdf", "de")])
    c.executemany(
        "INSERT INTO botschaft_paragraphs (botschaft_id, para_order, text, text_length) "
        "VALUES (?,1,?,?)",
        [(1, "Vaterschaftsurlaub für alle Angestellten im Aktienrecht.", 50),
         (2, HEAD_OR, len(HEAD_OR)),
         (3, "Die vorliegende Botschaft umfasst vier Bundesbeschlüsse.", 50)])
    c.executemany(
        "INSERT INTO article_botschaft_links (sr_number, article, botschaft_id, relation, evidence) "
        "VALUES (?,?,?,'considered','amendment_refs:1')",
        [("220", "1077", 1), ("220", "1077", 2), ("220", "957", 3), ("101", "8", 4)])
    c.execute("INSERT INTO materialien (law_code, sr_number, article, bbl_ref, legislative_intent) "
              "VALUES ('BV','101','8','BBl 1997 I 1','Rechtsgleichheit')")
    c.execute("INSERT INTO materialien_fts (rowid, law_code, article, bbl_ref, legislative_intent) "
              "SELECT id, law_code, article, bbl_ref, legislative_intent FROM materialien")
    c.commit()
    c.close()
    monkeypatch.setattr(mcp_server, "MATERIALIEN_DB_PATH", p)
    monkeypatch.setenv("SWISS_CASELAW_MATERIALIEN_DB", str(p))
    return p


def test_search_botschaft_carries_title_and_date(server_db):
    r = mcp_server._handle_search_botschaft(query="Vaterschaftsurlaub")
    assert r["total"] == 1
    hit = r["results"][0]
    assert hit["title"] == "Botschaft zur Änderung des Obligationenrechts (Aktienrecht)"
    assert hit["publication_date"] == "2017-01-24"


def test_get_materialien_fedlex_title_wins_over_the_header(server_db):
    r = mcp_server.get_materialien("OR", "1077")
    d = r["botschaft_documents"][0]
    assert d["title"] == "Botschaft zur Änderung des Obligationenrechts (Aktienrecht)"
    assert d["title_source"] == "fedlex"
    assert d["publication_date"] == "2017-01-24"
    # the header regex on the FR edition still supplies the printed date
    assert d["date"] == "23. November 2016"
    assert "excerpt" not in d


def test_get_materialien_header_fallback_is_labelled(server_db):
    r = mcp_server.get_materialien("OR", "957")
    d = r["botschaft_documents"][0]
    assert d["title"] is None and d["title_source"] is None
    assert d["publication_date"] is None
    assert d["excerpt"].startswith("Die vorliegende Botschaft")


def test_get_materialien_keeps_working_on_a_fixture_without_the_new_columns(tmp_path, monkeypatch):
    """Readers only touch title/publication_date, which every DB has."""
    p = tmp_path / "old.db"
    c = _old_schema_db(p)
    c.execute("INSERT INTO botschaft_documents (botschaft_id, bbl_year, bbl_page, bbl_citation, "
              "eli_uri, source_url, format, language, ingested_at) "
              "VALUES (1, 2017, 399, 'BBl 2017 399', 'e', 'u', 'pdf', 'de', 't')")
    c.execute("INSERT INTO botschaft_paragraphs (botschaft_id, para_order, text, text_length) "
              "VALUES (1, 1, ?, ?)", (HEAD_OR, len(HEAD_OR)))
    c.execute("INSERT INTO article_botschaft_links VALUES ('220','1077',1,'considered','x')")
    c.executescript("""
        CREATE TABLE materialien (id INTEGER PRIMARY KEY, law_code TEXT, sr_number TEXT,
            article TEXT, bbl_ref TEXT, bbl_page_refs TEXT, legislative_intent TEXT,
            key_arguments TEXT, design_choices TEXT, rejected_alternatives TEXT, general_context TEXT);
        CREATE TABLE parliamentary_modifications (law_code TEXT, council TEXT, date TEXT, text TEXT);
    """)
    c.commit(); c.close()
    monkeypatch.setattr(mcp_server, "MATERIALIEN_DB_PATH", p)
    d = mcp_server.get_materialien("OR", "1077")["botschaft_documents"][0]
    assert d["title"] == "Botschaft zur Änderung des Obligationenrechts (Aktienrecht)"
    assert d["title_source"] == "document_header" and d["publication_date"] is None


def test_search_materialien_names_the_source_message(server_db):
    r = mcp_server.search_materialien("Rechtsgleichheit")
    assert r["count"] == 1
    hit = r["results"][0]
    assert hit["bbl_ref"] == "BBl 1997 I 1"
    assert hit["title"] == "Botschaft über eine neue Bundesverfassung"
    assert hit["publication_date"] == "1997-01-14"


def test_get_article_purpose_carries_title(server_db):
    r = mcp_server._handle_get_article_purpose(sr_number="220", article="1077", language="de")
    assert r["sources"][0]["title"] == "Botschaft zur Änderung des Obligationenrechts (Aktienrecht)"
    assert r["sources"][0]["publication_date"] == "2017-01-24"


def test_tool_descriptions_mention_the_fields():
    tools = {t.name: t.description for t in mcp_server._list_tools()}
    for name in ("search_botschaft", "get_materialien", "search_materialien"):
        assert "title" in tools[name] and "publication_date" in tools[name], name
        assert len(tools[name]) <= 1024
