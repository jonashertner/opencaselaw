"""Step 2f's preserve loop must keep FTS5 rowids and swap on one filesystem.

Two prerequisites for growing materialien.db (ops critic, 2026-09-09):

1. The preserve loop copied ``botschaft_paragraphs_fts`` with
   ``INSERT INTO fts SELECT * FROM live.fts``. On an external-content FTS5
   table that drops the rowid: the copy numbers its rows 1..N in scan order,
   which equals ``paragraph_id`` only while the id sequence has no gap. The
   first re-ingest of a changed Botschaft (``DELETE FROM botschaft_paragraphs``)
   creates one, and from then on every MATCH after the gap returns the text
   of a different paragraph — silently, so ``search_botschaft`` would quote
   the wrong paragraph (R1-R3). Today max(paragraph_id) == count, so nothing
   has surfaced yet.

2. ``tmp_path`` was ``output_path.with_suffix('.db.tmp')``, i.e. next to the
   symlink, while the final ``os.replace`` targets the realpath. Once
   ``output/materialien.db`` is a symlink to the data volume that rename
   crosses filesystems (EXDEV) and the step fails on its first night.

Offline: the live DB is a fixture with the production botschaft schema.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from search_stack import build_materialien_db as bmd  # noqa: E402

# Verbatim from search_stack/build_botschaft_corpus.py SCHEMA_SQL: the tables
# the 04:30 writer owns and Step 2f must carry over.
BOTSCHAFT_SCHEMA = """
CREATE TABLE botschaft_documents (
    botschaft_id    INTEGER PRIMARY KEY,
    bbl_year        INTEGER NOT NULL,
    bbl_page        INTEGER NOT NULL,
    bbl_citation    TEXT NOT NULL,
    eli_uri         TEXT,
    title           TEXT,
    publication_date TEXT,
    source_url      TEXT NOT NULL,
    format          TEXT,
    language        TEXT,
    UNIQUE(bbl_year, bbl_page, language)
);
CREATE TABLE botschaft_paragraphs (
    paragraph_id    INTEGER PRIMARY KEY,
    botschaft_id    INTEGER NOT NULL REFERENCES botschaft_documents(botschaft_id) ON DELETE CASCADE,
    para_order      INTEGER NOT NULL,
    page_number     INTEGER,
    section_path    TEXT,
    article_anchor  TEXT,
    text            TEXT NOT NULL,
    text_length     INTEGER NOT NULL,
    UNIQUE(botschaft_id, para_order)
);
CREATE INDEX ix_bp_anchor ON botschaft_paragraphs(article_anchor);
CREATE INDEX ix_bp_doc ON botschaft_paragraphs(botschaft_id);
CREATE VIRTUAL TABLE botschaft_paragraphs_fts USING fts5(
    text,
    section_path,
    article_anchor UNINDEXED,
    content='botschaft_paragraphs',
    content_rowid='paragraph_id',
    tokenize='unicode61 remove_diacritics 1'
);
"""
BOTSCHAFT_TRIGGERS = """
CREATE TRIGGER botschaft_paragraphs_ai AFTER INSERT ON botschaft_paragraphs BEGIN
    INSERT INTO botschaft_paragraphs_fts(rowid, text, section_path, article_anchor)
    VALUES (new.paragraph_id, new.text, new.section_path, new.article_anchor);
END;
CREATE TRIGGER botschaft_paragraphs_ad AFTER DELETE ON botschaft_paragraphs BEGIN
    INSERT INTO botschaft_paragraphs_fts(botschaft_paragraphs_fts, rowid, text, section_path, article_anchor)
    VALUES ('delete', old.paragraph_id, old.text, old.section_path, old.article_anchor);
END;
"""

# One paragraph per Greek letter; the word doubles as the MATCH probe.
WORDS = ["alpha", "beta", "gamma", "delta", "epsilon"]


def _seed_live(db: Path) -> None:
    """A live materialien.db as the 04:30 writer leaves it, with a gap."""
    c = sqlite3.connect(db)
    c.executescript(BOTSCHAFT_SCHEMA + BOTSCHAFT_TRIGGERS)
    # Not a citation the tool will ever return: the row exists so the
    # NOT NULL / UNIQUE constraints of the production schema hold.
    c.execute(
        "INSERT INTO botschaft_documents VALUES (1, 2017, 399, 'fixture', "
        "NULL, NULL, NULL, 'file:///fixture', 'xml', 'de')"
    )
    for i, w in enumerate(WORDS, start=1):
        text = f"Absatz {w} des Fixtures."
        c.execute(
            "INSERT INTO botschaft_paragraphs VALUES (?, 1, ?, 1, '1', NULL, ?, ?)",
            (i, i, text, len(text)),
        )
    # The gap: a re-ingest deleted paragraph 4 (the delete trigger keeps
    # the live FTS in step, as it does in production).
    c.execute("DELETE FROM botschaft_paragraphs WHERE paragraph_id = 4")
    c.commit()
    c.close()


def _digest_dir(tmp_path: Path) -> Path:
    """The smallest digest input that keeps build_db from exiting empty."""
    d = tmp_path / "digests"
    d.mkdir()
    # Values taken from data/materialien/bv.json (article 0, first source).
    (d / "bv.json").write_text(json.dumps({
        "law": "BV", "sr_number": "101",
        "articles": {"0": {"sources": [{
            "bbl_ref": "BBl 1997 I 1", "bbl_page_refs": ["122"],
            "legislative_intent": "Fixture.",
        }]}},
    }))
    return d


def _match_rowid_text(db: Path, word: str) -> list[tuple[int, str]]:
    c = sqlite3.connect(f"file:{db}?mode=ro&immutable=1", uri=True)
    try:
        return c.execute(
            "SELECT f.rowid, p.text FROM botschaft_paragraphs_fts f "
            "JOIN botschaft_paragraphs p ON p.paragraph_id = f.rowid "
            "WHERE botschaft_paragraphs_fts MATCH ?", (word,),
        ).fetchall()
    finally:
        c.close()


def test_fts5_rowids_survive_a_paragraph_id_gap(tmp_path, caplog):
    out = tmp_path / "materialien.db"
    _seed_live(out)
    caplog.set_level(logging.INFO, logger="build_materialien_db")

    bmd.build_db(_digest_dir(tmp_path), out, skip_fedlex=True)

    # The paragraph after the gap must come back as itself.
    assert _match_rowid_text(out, "epsilon") == [(5, "Absatz epsilon des Fixtures.")]
    assert _match_rowid_text(out, "alpha") == [(1, "Absatz alpha des Fixtures.")]
    # The deleted paragraph stays gone from the rebuilt index.
    assert _match_rowid_text(out, "delta") == []

    c = sqlite3.connect(out)
    try:
        # Content table and index agree row for row (checks the index
        # against botschaft_paragraphs on SQLite >= 3.42).
        c.execute(
            "INSERT INTO botschaft_paragraphs_fts(botschaft_paragraphs_fts) "
            "VALUES ('integrity-check')"
        )
        assert [r[0] for r in c.execute(
            "SELECT paragraph_id FROM botschaft_paragraphs ORDER BY 1"
        )] == [1, 2, 3, 5]
        # The 04:30 writer's triggers were carried over: a paragraph written
        # into the swapped DB is searchable without a rebuild.
        c.execute(
            "INSERT INTO botschaft_paragraphs VALUES (9, 1, 9, 2, '2', NULL, "
            "'Absatz zeta des Fixtures.', 26)"
        )
        c.commit()
    finally:
        c.close()
    assert _match_rowid_text(out, "zeta") == [(9, "Absatz zeta des Fixtures.")]

    # The shadow tables are no longer reported as failed copies.
    assert "Could not preserve table" not in caplog.text
    assert "Rebuilt FTS5 index botschaft_paragraphs_fts from botschaft_paragraphs (4 rows)" in caplog.text


def test_the_old_select_star_copy_misaligns_after_a_gap(tmp_path):
    """Pin the failure mode the rebuild replaces, so the reason stays visible."""
    live = tmp_path / "live.db"
    _seed_live(live)
    new = tmp_path / "new.db"
    c = sqlite3.connect(new)
    # As the preserve loop did it: tables first, triggers only afterwards.
    c.executescript(BOTSCHAFT_SCHEMA)
    c.execute(f"ATTACH DATABASE 'file:{live}?mode=ro' AS live")
    c.execute("INSERT INTO botschaft_paragraphs SELECT * FROM live.botschaft_paragraphs")
    c.execute("INSERT INTO botschaft_paragraphs_fts SELECT * FROM live.botschaft_paragraphs_fts")
    c.commit()
    c.execute("DETACH DATABASE live")
    c.executescript(BOTSCHAFT_TRIGGERS)
    # The index numbered the four surviving paragraphs 1..4: 'epsilon' sits
    # at rowid 4, a paragraph_id that no longer exists, and rowid 5 (the real
    # epsilon) is unindexed. Joined to its text the hit vanishes; a tool that
    # reads the FTS rowid alone quotes paragraph 4's neighbour instead.
    assert c.execute(
        "SELECT rowid FROM botschaft_paragraphs_fts WHERE botschaft_paragraphs_fts MATCH 'epsilon'"
    ).fetchall() == [(4,)]
    c.close()
    assert _match_rowid_text(new, "epsilon") == []


def test_self_contained_fts5_tables_keep_their_rowids(tmp_path):
    """A regular FTS5 table (own _content) is copied with explicit rowids."""
    out = tmp_path / "materialien.db"
    c = sqlite3.connect(out)
    c.execute("CREATE VIRTUAL TABLE notes_fts USING fts5(body, tag UNINDEXED)")
    c.executemany(
        "INSERT INTO notes_fts(rowid, body, tag) VALUES (?, ?, ?)",
        [(10, "erste Notiz", "a"), (30, "zweite Notiz", "b"), (70, "dritte Notiz", "c")],
    )
    c.commit()
    c.close()

    bmd.build_db(_digest_dir(tmp_path), out, skip_fedlex=True)

    c = sqlite3.connect(f"file:{out}?mode=ro&immutable=1", uri=True)
    try:
        assert c.execute(
            "SELECT rowid, tag FROM notes_fts WHERE notes_fts MATCH 'dritte'"
        ).fetchall() == [(70, "c")]
        assert c.execute("SELECT COUNT(*) FROM notes_fts").fetchone()[0] == 3
    finally:
        c.close()


def test_fts5_create_statement_parsing():
    ext = ("CREATE VIRTUAL TABLE botschaft_paragraphs_fts USING fts5(text, "
           "content='botschaft_paragraphs', content_rowid='paragraph_id')")
    assert bmd._is_fts5_virtual_table(ext)
    assert bmd._fts5_content_option(ext) == "botschaft_paragraphs"
    # content_rowid alone must not read as a content= option.
    assert bmd._fts5_content_option(
        "CREATE VIRTUAL TABLE t USING fts5(a, content_rowid='id')"
    ) is None
    assert bmd._fts5_content_option("CREATE VIRTUAL TABLE t USING fts5(a, content='')") == ""
    assert bmd._fts5_content_option("CREATE VIRTUAL TABLE t USING fts5(a, content=materialien, content_rowid=id)") == "materialien"
    assert not bmd._is_fts5_virtual_table("CREATE TABLE botschaft_paragraphs_fts_data(id INTEGER PRIMARY KEY, block BLOB)")
    assert bmd._fts5_shadow_tables("x") == {"x_data", "x_idx", "x_docsize", "x_config", "x_content"}


def test_symlinked_output_builds_and_swaps_next_to_the_realpath(tmp_path, monkeypatch):
    """output/materialien.db -> volume: the .tmp and the rename stay on the volume."""
    volume = tmp_path / "volume"
    volume.mkdir()
    out_dir = tmp_path / "output"
    out_dir.mkdir()
    real = volume / "materialien.db"
    _seed_live(real)
    link = out_dir / "materialien.db"
    link.symlink_to(real)

    replaces: list[tuple[str, str]] = []
    real_replace = os.replace

    def spy(src, dst, *a, **kw):
        replaces.append((str(src), str(dst)))
        return real_replace(src, dst, *a, **kw)

    monkeypatch.setattr(os, "replace", spy)

    bmd.build_db(_digest_dir(tmp_path), link, skip_fedlex=True)

    assert replaces == [(str(volume / "materialien.db.tmp"), str(real))]
    assert link.is_symlink() and link.resolve() == real
    # Nothing was built next to the symlink; nothing is left on the volume.
    assert sorted(p.name for p in out_dir.iterdir()) == ["materialien.db"]
    assert sorted(p.name for p in volume.iterdir()) == ["materialien.db"]
    # The swapped file is the new build with the live tables carried over.
    assert _match_rowid_text(link, "epsilon") == [(5, "Absatz epsilon des Fixtures.")]
    c = sqlite3.connect(f"file:{real}?mode=ro&immutable=1", uri=True)
    try:
        assert c.execute("SELECT value FROM meta WHERE key='built_at'").fetchone()
    finally:
        c.close()
