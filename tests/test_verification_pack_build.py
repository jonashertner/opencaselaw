"""The verification pack builder: a layout that fits the weekly step.

publish.py step 3b timed out at two hours on 2026-11-08 and 11-15, before
the 11.2M paragraphs were written. Two costs were measured. Paragraphs were
inserted into a WITHOUT ROWID B-tree keyed on (decision_id, e_number) in
source order, which is not key order, and a rollback journal recorded every
page that changed. The fixes are to append to a rowid table, build a unique
index afterwards, write no journal, skip the final VACUUM, and read
has_full_text from the record header. The 11-22 production run showed the
reading was the larger cost, so both sources are now read in rowid ranges by
parallel processes; a parallel build must equal a one-process build. The
client's lookups by (decision_id, e_number) and by decision_id are unchanged.
Offline, on fixture databases.
"""
import importlib.util
import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def builder():
    spec = importlib.util.spec_from_file_location("build_verification_pack_t", ROOT / "scripts" / "build_verification_pack.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _dbs(tmp_path, paragraphs):
    dec = tmp_path / "decisions.db"
    con = sqlite3.connect(dec)
    con.execute("CREATE TABLE decisions (decision_id TEXT PRIMARY KEY, court TEXT, canton TEXT, language TEXT, "
                "decision_date TEXT, docket_number TEXT, docket_number_2 TEXT, source_url TEXT, content_hash TEXT, "
                "full_text TEXT)")
    con.executemany("INSERT INTO decisions VALUES (?,?,?,?,?,?,?,?,?,?)", [
        ("bger_4A_1_2020", "bger", "CH", "de", "2020-01-01", "4A_1/2020", None, "u1", "h1", "Volltext " * 5000),
        ("bger_4A_2_2020", "bger", "CH", "de", "2020-01-02", "4A_2/2020", None, "u2", "h2", ""),
        ("bger_4A_3_2020", "bger", "CH", "de", "2020-01-03", "4A_3/2020", None, "u3", "h3", None),
    ])
    con.commit(); con.close()
    st = tmp_path / "structure.db"
    con = sqlite3.connect(st)
    # The production table has PRIMARY KEY (decision_id, e_number); the fixture
    # omits it so a duplicate can be planted.
    con.execute("CREATE TABLE erwaegungen_paragraph (decision_id TEXT, e_number TEXT, depth INTEGER, parent TEXT, text TEXT)")
    con.executemany("INSERT INTO erwaegungen_paragraph VALUES (?,?,?,?,?)", paragraphs)
    con.commit(); con.close()
    return dec, st


# Source order is grouped by decision but not in key order, like the sidecar's.
PARAGRAPHS = [
    ("bger_4A_3_2020", "2", 1, None, "Erwägung zwei."),
    ("bger_4A_3_2020", "1", 1, None, "Erwägung eins."),
    ("bger_4A_1_2020", "1.1", 2, "1", "Erste Unterziffer."),
    ("bger_4A_1_2020", "1", 1, None, "Erste Erwägung."),
    ("bger_4A_2_2020", "3", 1, None, ""),            # empty text: not packed
]


def test_paragraphs_are_appended_and_looked_up_through_a_unique_index(tmp_path, builder):
    dec, st = _dbs(tmp_path, PARAGRAPHS)
    out = tmp_path / "2026-11-22.sqlite"
    meta = builder.build(dec, st, out, repo_dir=tmp_path / "no-repo")
    assert meta["paragraphs"] == "4" and meta["decisions"] == "3"
    con = sqlite3.connect(out)
    sql = con.execute("SELECT sql FROM sqlite_master WHERE name = 'paragraphs'").fetchone()[0]
    assert "WITHOUT ROWID" not in sql
    (idx_sql,) = con.execute("SELECT sql FROM sqlite_master WHERE name = 'idx_pack_paragraph'").fetchone()
    assert idx_sql.startswith("CREATE UNIQUE INDEX")
    # The two queries opencaselaw_cli/local.py runs, both through the index.
    for query, args in (("SELECT depth, parent, text_z FROM paragraphs WHERE decision_id = ? AND e_number = ?", ("bger_4A_1_2020", "1.1")),
                        ("SELECT e_number FROM paragraphs WHERE decision_id = ? ORDER BY e_number LIMIT 60", ("bger_4A_1_2020",))):
        plan = " ".join(r[-1] for r in con.execute("EXPLAIN QUERY PLAN " + query, args))
        assert "idx_pack_paragraph" in plan and "TEMP B-TREE" not in plan
    assert [r[0] for r in con.execute("SELECT e_number FROM paragraphs WHERE decision_id = ? ORDER BY e_number",
                                      ("bger_4A_1_2020",))] == ["1", "1.1"]
    assert con.execute("PRAGMA freelist_count").fetchone()[0] == 0     # no VACUUM needed
    assert not (tmp_path / "2026-11-22.sqlite.tmp-journal").exists()


def test_has_full_text_reads_the_header_and_still_tells_empty_from_present(tmp_path, builder):
    dec, st = _dbs(tmp_path, PARAGRAPHS)
    out = tmp_path / "pack.sqlite"
    builder.build(dec, st, out, repo_dir=tmp_path / "no-repo")
    con = sqlite3.connect(out)
    got = dict(con.execute("SELECT decision_id, has_full_text FROM decisions"))
    assert got == {"bger_4A_1_2020": 1, "bger_4A_2_2020": 0, "bger_4A_3_2020": 0}
    if sqlite3.sqlite_version_info >= (3, 43, 0):
        assert "octet_length" in builder._has_text_expr()


def test_unfinished_packs_from_killed_runs_are_removed(tmp_path, builder):
    dec, st = _dbs(tmp_path, PARAGRAPHS)
    pack_dir = tmp_path / "verification_pack"
    pack_dir.mkdir()
    for name in ("2026-11-08.sqlite.tmp", "2026-11-08.sqlite.tmp-journal", "2026-11-15.sqlite.tmp",
                 "2026-11-15.sqlite.tmp-journal", "2026-11-15.sqlite.gz.tmp"):
        (pack_dir / name).write_bytes(b"x")
    kept = ["2026-11-01.sqlite.gz", "2026-11-01.sqlite.gz.sha256"]
    for name in kept:
        (pack_dir / name).write_bytes(b"keep")
    builder.build(dec, st, pack_dir / "2026-11-22.sqlite", repo_dir=tmp_path / "no-repo")
    assert sorted(p.name for p in pack_dir.iterdir()) == sorted(kept + ["2026-11-22.sqlite"])


def test_a_duplicate_paragraph_key_fails_the_build_instead_of_publishing_two_texts(tmp_path, builder):
    dec, st = _dbs(tmp_path, PARAGRAPHS + [("bger_4A_1_2020", "1", 1, None, "Zweiter Text für dieselbe Erwägung.")])
    out = tmp_path / "pack.sqlite"
    with pytest.raises(sqlite3.IntegrityError):
        builder.build(dec, st, out, repo_dir=tmp_path / "no-repo")
    assert not out.exists()


def _content(path):
    con = sqlite3.connect(path)
    out = {table: sorted(con.execute(f"SELECT * FROM {table}")) for table in ("decisions", "paragraphs", "courts", "aliases")}
    con.close()
    return out


def test_a_parallel_build_equals_a_one_process_build(tmp_path, builder):
    many = [(f"bger_4A_{i}_2020", str(n), 1, None, f"Erwägung {n} von {i}.") for i in range(1, 4) for n in range(1, 30)]
    dec, st = _dbs(tmp_path, many)
    one = builder.build(dec, st, tmp_path / "one.sqlite", repo_dir=tmp_path / "no-repo", workers=1)
    par = builder.build(dec, st, tmp_path / "par.sqlite", repo_dir=tmp_path / "no-repo", workers=3)
    assert one["paragraphs"] == par["paragraphs"] == str(len(many))
    assert _content(tmp_path / "one.sqlite") == _content(tmp_path / "par.sqlite")


def test_a_duplicate_key_still_fails_a_parallel_build(tmp_path, builder):
    dec, st = _dbs(tmp_path, PARAGRAPHS + [("bger_4A_1_2020", "1", 1, None, "Zweiter Text.")])
    with pytest.raises(sqlite3.IntegrityError):
        builder.build(dec, st, tmp_path / "pack.sqlite", repo_dir=tmp_path / "no-repo", workers=3)
    assert not (tmp_path / "pack.sqlite").exists()


def test_a_failing_reader_fails_the_build(tmp_path, builder, monkeypatch):
    dec, st = _dbs(tmp_path, PARAGRAPHS)
    real = builder._paragraph_batches

    def broken(con, sql, args):
        if args and args[0] >= 3:
            raise ValueError("unreadable page")
        yield from real(con, sql, args)

    monkeypatch.setattr(builder, "_paragraph_batches", broken)
    with pytest.raises(RuntimeError, match="unreadable page"):
        builder.build(dec, st, tmp_path / "pack.sqlite", repo_dir=tmp_path / "no-repo", workers=2)
    assert not (tmp_path / "pack.sqlite").exists()


def test_rowid_ranges_cover_the_table_and_skip_tables_without_rowid(builder):
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE t (x)")
    assert builder._rowid_ranges(con, "t", 4) == []
    con.executemany("INSERT INTO t(rowid, x) VALUES (?, 0)", [(r,) for r in (5, 6, 9, 40, 41)])
    spans = builder._rowid_ranges(con, "t", 4)
    covered = [r for lo, hi in spans for (r,) in con.execute("SELECT rowid FROM t WHERE rowid BETWEEN ? AND ?", (lo, hi))]
    assert sorted(covered) == [5, 6, 9, 40, 41] and len(covered) == 5
    con.execute("CREATE TABLE w (k PRIMARY KEY, v) WITHOUT ROWID")
    assert builder._rowid_ranges(con, "w", 4) is None
    with pytest.raises(sqlite3.OperationalError):
        builder._rowid_ranges(con, "missing", 4)
