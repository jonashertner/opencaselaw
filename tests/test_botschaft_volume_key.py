"""Volume-aware citation key on botschaft_documents (2026-10-19).

Before 1999 a Bundesblatt year has several volumes, so "BBl 1975 I 609" and
"BBl 1975 II 609" are two documents; UNIQUE(bbl_year, bbl_page, language)
kept the second out (301 German pairs, recorded as 'collision' and never
fetched). The migration rebuilds the table on the production schema as it
is stored today (original CREATE text + columns added by ALTER), keeps every
botschaft_id, and resets the collision rows for a retry.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from search_stack import build_botschaft_corpus as bbc  # noqa: E402

# Verbatim from production materialien.db sqlite_master, 2026-10-19.
PROD_DOCS = """CREATE TABLE botschaft_documents (
    botschaft_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    bbl_year        INTEGER NOT NULL,
    bbl_page        INTEGER NOT NULL,
    bbl_citation    TEXT NOT NULL,            -- "BBl 1999 6013"
    eli_uri         TEXT,                     -- https://fedlex.data.admin.ch/eli/fga/1999/6013
    title           TEXT,
    publication_date TEXT,
    source_url      TEXT NOT NULL,
    format          TEXT NOT NULL CHECK(format IN ('akoma-ntoso-xml','pdf')),
    language        TEXT NOT NULL,
    page_count      INTEGER,
    text_hash       TEXT,                     -- sha-256 of full text, for delta detection
    ingested_at     TEXT NOT NULL,
    UNIQUE(bbl_year, bbl_page, language)
)"""


@pytest.fixture
def prod_like(tmp_path):
    db = tmp_path / "materialien.db"
    conn = sqlite3.connect(db)
    conn.execute(PROD_DOCS)
    conn.execute("CREATE INDEX ix_bd_eli ON botschaft_documents(eli_uri)")
    # the rest of the schema exactly as ensure_schema creates it
    conn.executescript(bbc.SCHEMA_SQL)
    for i, (y, p) in enumerate([(2011, 7705), (2019, 4747), (1975, 609)], start=1):
        conn.execute(
            "INSERT INTO botschaft_documents (botschaft_id, bbl_year, bbl_page, bbl_citation, "
            "eli_uri, source_url, format, language, ingested_at) "
            "VALUES (?, ?, ?, ?, ?, 'u', 'pdf', 'de', 'now')",
            (i * 10, y, p, f"BBl {y} {p}", f"https://fedlex.data.admin.ch/eli/fga/{y}/{p}"))
        conn.execute("INSERT INTO botschaft_paragraphs (botschaft_id, para_order, text, text_length) "
                     "VALUES (?, 1, ?, 20)", (i * 10, f"Absatz der Botschaft {y}"))
        conn.execute("INSERT INTO article_botschaft_links VALUES ('220', '1', ?, 'considered', 'x')",
                     (i * 10,))
    conn.execute("INSERT INTO botschaft_fetch_state (eli_uri, language, status, attempts) "
                 "VALUES ('https://fedlex.data.admin.ch/eli/fga/1975/2_609', 'de', 'collision', 1)")
    conn.commit()
    bbc.migrate_additive(conn)  # the columns production gets by ALTER TABLE
    conn.execute("UPDATE botschaft_documents SET bbl_volume = 1 WHERE bbl_year = 1975")
    conn.commit()
    yield conn
    conn.close()


def test_production_schema_needs_the_migration(prod_like):
    assert bbc.needs_volume_key_migration(prod_like)
    # ensure_schema alone must not touch the old constraint (no rebuild on the live file)
    bbc.ensure_schema(prod_like)
    assert bbc.needs_volume_key_migration(prod_like)


def test_migration_keeps_ids_children_and_fts(prod_like):
    out = bbc.migrate_volume_key(prod_like)
    assert out == {"migrated": True, "rows": 3, "collisions_reset": 1}
    assert not bbc.needs_volume_key_migration(prod_like)
    ids = [r[0] for r in prod_like.execute("SELECT botschaft_id FROM botschaft_documents ORDER BY 1")]
    assert ids == [10, 20, 30]
    assert prod_like.execute("PRAGMA foreign_key_check").fetchall() == []
    assert prod_like.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    hit = prod_like.execute("SELECT bp.botschaft_id FROM botschaft_paragraphs_fts f JOIN "
                            "botschaft_paragraphs bp ON bp.paragraph_id = f.rowid "
                            "WHERE botschaft_paragraphs_fts MATCH '1975'").fetchall()
    assert hit == [(30,)]
    idx = {r[1] for r in prod_like.execute("PRAGMA index_list(botschaft_documents)")}
    assert {"ux_bd_citation", "ix_bd_eli", "ix_bd_doctype"} <= idx
    st = prod_like.execute("SELECT status, attempts FROM botschaft_fetch_state").fetchone()
    assert tuple(st) == ("error", 0)
    # ON DELETE CASCADE still holds after the rebuild
    prod_like.execute("PRAGMA foreign_keys=ON")
    prod_like.execute("DELETE FROM botschaft_documents WHERE botschaft_id = 20")
    assert prod_like.execute("SELECT COUNT(*) FROM botschaft_paragraphs WHERE botschaft_id=20").fetchone()[0] == 0


def test_new_key_allows_other_volume_and_refuses_true_duplicates(prod_like):
    bbc.migrate_volume_key(prod_like)
    ins = ("INSERT INTO botschaft_documents (bbl_year, bbl_page, bbl_citation, bbl_volume, "
           "source_url, format, language, ingested_at) VALUES (?, ?, ?, ?, 'u', 'pdf', 'de', 'now')")
    prod_like.execute(ins, (1975, 609, "BBl 1975 II 609", 2))     # another volume: fine
    with pytest.raises(sqlite3.IntegrityError):
        prod_like.execute(ins, (1975, 609, "BBl 1975 I 609", 1))  # same volume
    with pytest.raises(sqlite3.IntegrityError):
        prod_like.execute(ins, (2011, 7705, "BBl 2011 7705", None))  # post-1998, no volume
    prod_like.execute(ins.replace("'de'", "'fr'"), (2011, 7705, "FF 2011 7705", None))


def test_migration_is_idempotent(prod_like):
    bbc.migrate_volume_key(prod_like)
    assert bbc.migrate_volume_key(prod_like) == {"migrated": False}
    assert prod_like.execute("SELECT COUNT(*) FROM botschaft_documents").fetchone()[0] == 3


def test_fresh_database_has_the_new_key(tmp_path):
    conn = sqlite3.connect(tmp_path / "fresh.db")
    bbc.ensure_schema(conn)
    assert not bbc.needs_volume_key_migration(conn)
    idx = {r[1] for r in conn.execute("PRAGMA index_list(botschaft_documents)")}
    assert "ux_bd_citation" in idx
    conn.close()



def test_c1_control_codes_are_restored(tmp_path):
    from scrapers.fedlex_materialien import _clean_title, fix_cp1252_controls
    assert _clean_title("Organspende fördern \x96 Leben retten ") == "Organspende fördern – Leben retten"
    assert fix_cp1252_controls("Plan 2022\x962024, man\x9cuvre, \x84so\x93") == "Plan 2022–2024, manœuvre, „so“"
    assert fix_cp1252_controls("\x81 undefined stays") == "\x81 undefined stays"
    assert fix_cp1252_controls("plain – text") == "plain – text"
    conn = sqlite3.connect(tmp_path / "m.db")
    bbc.ensure_schema(conn)
    conn.execute("INSERT INTO botschaft_documents (bbl_year, bbl_page, bbl_citation, title, "
                 "source_url, format, language, ingested_at) VALUES "
                 "(2020, 1, 'BBl 2020 1', 'Voranschlag 2021\x962024', 'u', 'pdf', 'de', 'now')")
    conn.commit()
    assert bbc.repair_c1_titles(conn) == {"titles_repaired": 1}
    assert conn.execute("SELECT title FROM botschaft_documents").fetchone()[0] == "Voranschlag 2021–2024"
    assert bbc.repair_c1_titles(conn) == {"titles_repaired": 0}
    conn.close()
