"""structure.parquet and erwaegungen_paragraphs.parquet must describe the same
decisions, and the metadata export must finish on a served-text sidecar.

2026-10-07: on Hugging Face, structure/structure.parquet was last written
2026-09-10 and still keyed the 14,578 historical BGE rows as
``bge_historical_*`` while erwaegungen_paragraphs.parquet (2026-10-04) keyed
them as ``bge_*``. Since step 2g builds the sidecar from served text, the
``structure`` rows carry the full section text and every column the metadata
export needed sat behind it; the export projected over its budget and
``_bounded_stream`` kept the last good — stale — file. Fix (2026-10-08):
stored has-section flags + idx_structure_export, read index-only
(docs/proposals/structure-export-small-columns.md).

Also pinned here: ``erwaegungen_paragraph_count`` equals the rows the
paragraph file holds for the decision, and Erwägungen without numbered
markers are exported whole as e_number "0" — from ``erwaegungen_unnumbered``,
never from ``erwaegungen_paragraph`` (pinpoints, FTS, coverage gate).

The fixture sidecars are written by the production writers (step 2g's
``build_structure_incremental`` over a decisions.db, and the shard fallback
``build_db``), not by hand.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import sys
from collections import Counter
from operator import itemgetter
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

pa = pytest.importorskip("pyarrow")
pq = pytest.importorskip("pyarrow.parquet")
import export_parquet as ep  # noqa: E402
from export_parquet import STRUCTURE_META_SCHEMA, export_decision_structure  # noqa: E402
from search_stack.extract_decision_structure import (  # noqa: E402
    build_db,
    paragraph_count,
    paragraph_rows,
)
from search_stack.extract_decision_structure_incremental import (  # noqa: E402
    build_structure_incremental,
)


@pytest.fixture(autouse=True)
def _clean_budget_env(monkeypatch):
    # an operator's shell budget must not change what these tests see
    for var in ("OCL_STRUCTURE_EXPORT_BUDGET_S", "OCL_STRUCTURE_PARAGRAPHS_BUDGET_S",
                "OCL_EXPORT_WALLCLOCK_BUDGET_S"):
        monkeypatch.delenv(var, raising=False)


# ── fixture corpus ──────────────────────────────────────────────────────

def _para(seed: str, n: int) -> str:
    base = (f"Die Vorinstanz hat im Verfahren {seed} erwogen, die Voraussetzungen von "
            "Art. 41 OR seien nicht erfüllt; die Beschwerdeführerin rügt eine "
            "Verletzung von Bundesrecht. ")
    return (base * (n // len(base) + 1))[:n].rstrip() + "."


def _federal_text(seed: str, court_word: str = "Bundesgericht") -> str:
    """~13 KB: each structure row spills into a chain of several overflow
    pages; each paragraph (<4 KB) stays on its leaf page."""
    return ("Sachverhalt:\n\nA.- " + _para(seed, 3000) + "\n\n"
            "Erwägungen:\n\n"
            "1. " + _para(seed, 3500) + "\n\n"
            "2. " + _para(seed, 3500) + "\n\n"
            "2.1 " + _para(seed, 2500) + "\n\n"
            "3. Demnach ist die Beschwerde abzuweisen.\n\n"
            f"Demnach erkennt das {court_word}:\n\n"
            "1. Die Beschwerde wird abgewiesen.\n"
            "2. Die Gerichtskosten werden der Beschwerdeführerin auferlegt.\n")


def _unnumbered_text(seed: str) -> str:
    """BGE excerpt style: 'Aus den Erwägungen' with no numbered markers, so
    the extractor keeps the reasoning whole (its e_number "0" fallback)."""
    return ("Sachverhalt:\n\nA.- " + _para(seed, 2500) + "\n\n"
            "Aus den Erwägungen:\n\n" + _para(seed, 9000) + "\n\n"
            "Demnach erkennt das Bundesgericht:\n\n"
            "1. Die Beschwerde wird abgewiesen.\n")


# Historical BGE served as bge_* (the ids the stale file carried as
# bge_historical_*), current BGE, BGer, BVGer, a BGE excerpt whose
# Erwägungen carry no numbers, and a cantonal decision whose text has no
# section markers (a structure row with no paragraphs).
DECISIONS = [
    ("bge_101_Ia_1", "bge", "CH", "1975-01-29", _federal_text("101 Ia 1")),
    ("bge_102_II_100", "bge", "CH", "1976-05-04", _federal_text("102 II 100")),
    ("bge_150_III_12", "bge", "CH", "2024-01-10", _federal_text("150 III 12")),
    ("bge_140_III_200", "bge", "CH", "2014-03-03", _unnumbered_text("140 III 200")),
    ("bger_4A_100_2024", "bger", "CH", "2024-06-12", _federal_text("4A_100/2024")),
    ("bvger_A-1234_2023", "bvger", "CH", "2023-11-02",
     _federal_text("A-1234/2023", "Bundesverwaltungsgericht")),
    ("zh_obergericht_LB230001", "zh_obergericht", "ZH", "2023-03-03",
     "Das Obergericht des Kantons Zürich zieht in Betracht, dass die Eingabe "
     "verspätet ist und nicht darauf einzutreten ist. " * 10),
]
UNNUMBERED_ID = "bge_140_III_200"
NO_SECTIONS_ID = "zh_obergericht_LB230001"
STALE_IDS = ["bge_historical_101_Ia_1", "bge_historical_102_II_100"]


def _decisions_db(path: Path, decisions=DECISIONS) -> Path:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE decisions (decision_id TEXT PRIMARY KEY, court TEXT, "
                 "canton TEXT, language TEXT, decision_date TEXT, full_text TEXT, "
                 "regeste TEXT)")
    conn.executemany("INSERT INTO decisions VALUES (?, ?, ?, 'de', ?, ?, NULL)", decisions)
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def sidecar(tmp_path) -> Path:
    out = tmp_path / "decision_structure.db"
    build_structure_incremental(decisions_db=_decisions_db(tmp_path / "decisions.db"),
                                structure_db=tmp_path / "absent.db",
                                output_path=out, force_full=True)
    return out


def _ro(db: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db}?mode=ro&immutable=1", uri=True)


def _write_stale_structure_parquet(path: Path) -> None:
    """What Hugging Face served on 2026-10-07: the shard-era file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    n = len(STALE_IDS)
    pq.write_table(pa.Table.from_pydict({
        "decision_id": STALE_IDS, "court": ["bge"] * n, "language": ["de"] * n,
        "has_sachverhalt": [True] * n, "has_erwaegungen": [True] * n,
        "has_dispositiv": [True] * n, "sachverhalt_method": [None] * n,
        "erwaegungen_method": [None] * n, "dispositiv_method": [None] * n,
        "erwaegungen_paragraph_count": [3] * n,
    }, schema=STRUCTURE_META_SCHEMA), path)


def _export_both(sidecar: Path, out: Path) -> tuple[dict, list[dict], list[dict]]:
    counts = export_decision_structure(sidecar, out, include_paragraphs=True)
    meta = pq.read_table(out / "structure" / "structure.parquet").to_pylist()
    paras = pq.read_table(out / "structure" / "erwaegungen_paragraphs.parquet").to_pylist()
    return counts, meta, paras


# ── 1. the export finishes and both files key the same decisions ───────

def test_structure_export_finishes_with_the_paragraph_export_ids(sidecar, tmp_path):
    out = tmp_path / "dataset"
    _write_stale_structure_parquet(out / "structure" / "structure.parquet")

    counts, meta, paras = _export_both(sidecar, out)

    conn = _ro(sidecar)
    try:
        sidecar_ids = {r[0] for r in conn.execute("SELECT decision_id FROM structure")}
        n_rows = conn.execute(
            "SELECT (SELECT count(*) FROM erwaegungen_paragraph WHERE text != '') "
            "+ (SELECT count(*) FROM erwaegungen_unnumbered WHERE text != '')").fetchone()[0]
    finally:
        conn.close()
    assert sidecar_ids == {d[0] for d in DECISIONS}
    # finished: both files written this run, nothing skipped, nothing kept
    assert counts == {"structure": len(DECISIONS), "erwaegungen_paragraphs": n_rows}
    status = json.loads((out / "structure" / "export_status.json").read_text())
    assert status["counts"] == counts
    assert not list((out / "structure").glob("*.tmp"))

    meta_ids = {r["decision_id"] for r in meta}
    para_ids = {r["decision_id"] for r in paras}
    assert len(meta) == len(meta_ids)                        # one row per decision
    assert meta_ids == sidecar_ids                           # the stale file was replaced
    assert not meta_ids & set(STALE_IDS)
    assert para_ids <= meta_ids, f"paragraphs without a structure row: {para_ids - meta_ids}"
    # every decision with Erwägungen has paragraph rows — numbered, or the
    # unnumbered "0" — so the two exports name exactly the same decisions
    assert para_ids == {r["decision_id"] for r in meta if r["has_erwaegungen"]}
    assert meta_ids - para_ids == {NO_SECTIONS_ID}


def test_paragraph_count_equals_the_rows_the_paragraph_file_holds(sidecar, tmp_path):
    _, meta, paras = _export_both(sidecar, tmp_path / "o")
    rows = Counter(r["decision_id"] for r in paras)
    assert {r["decision_id"]: r["erwaegungen_paragraph_count"] for r in meta} == {
        r["decision_id"]: rows.get(r["decision_id"], 0) for r in meta}
    assert rows[UNNUMBERED_ID] == 1 and rows["bger_4A_100_2024"] == 4


def test_unnumbered_erwaegungen_are_exported_as_e0_but_not_served_as_paragraphs(
        sidecar, tmp_path):
    _, meta, paras = _export_both(sidecar, tmp_path / "o")
    zero = [r for r in paras if r["decision_id"] == UNNUMBERED_ID]
    assert [(r["e_number"], r["depth"], r["parent"]) for r in zero] == [("0", 0, None)]
    assert zero[0]["text"].startswith("Die Vorinstanz hat im Verfahren 140 III 200")
    assert len(zero[0]["text"]) > 8000                      # the whole reasoning, not a stub
    assert not [r for r in paras if r["e_number"] == "0" and r["decision_id"] != UNNUMBERED_ID]
    conn = _ro(sidecar)
    try:
        # the sidecar's paragraph table — get_erwaegung, pinpoints, FTS, the
        # coverage gate — never holds an "Erwägung 0"
        assert conn.execute("SELECT count(*) FROM erwaegungen_paragraph "
                            "WHERE e_number = '0' OR depth = 0").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM erwaegungen_paragraph WHERE decision_id = ?",
                            (UNNUMBERED_ID,)).fetchone()[0] == 0
        stored = conn.execute("SELECT text FROM erwaegungen_unnumbered WHERE decision_id = ?",
                              (UNNUMBERED_ID,)).fetchone()[0]
    finally:
        conn.close()
    assert stored == zero[0]["text"]


def test_paragraph_rows_counts_what_lands_in_the_file():
    numbered, unnumbered = paragraph_rows([
        {"e_number": "1", "depth": 1, "parent": None, "text": "erste Fassung"},
        {"e_number": "2", "depth": 1, "parent": None, "text": "zwei"},
        {"e_number": "1", "depth": 1, "parent": None, "text": "zweite Fassung"},
    ])
    # a repeated e_number keeps the later body and moves last (INSERT OR REPLACE)
    assert [(p["e_number"], p["text"]) for p in numbered] == [("2", "zwei"), ("1", "zweite Fassung")]
    assert unnumbered is None and paragraph_count(numbered, unnumbered) == 2

    numbered, unnumbered = paragraph_rows(
        [{"e_number": "0", "depth": 0, "parent": None, "text": "  Aus den Erwägungen ...  "}])
    assert numbered == [] and unnumbered == "Aus den Erwägungen ..."
    assert paragraph_count(numbered, unnumbered) == 1
    # an empty fallback (a historical BGE whose cut lands at the start) is a
    # placeholder: not stored, not counted
    numbered, unnumbered = paragraph_rows(
        [{"e_number": "0", "depth": 0, "parent": None, "text": " \n "}])
    assert unnumbered is None and paragraph_count(numbered, unnumbered) == 0


def test_incremental_run_keeps_unnumbered_rows_in_step(sidecar, tmp_path):
    """A decision that gains numbered Erwägungen drops its "0" row; a retired
    decision takes its "0" row with it."""
    other = ("bge_141_II_5", "bge", "CH", "2015-01-01", _unnumbered_text("141 II 5"))
    changed = [d if d[0] != UNNUMBERED_ID else d[:4] + (_federal_text("140 III 200"),)
               for d in DECISIONS] + [other]
    db1 = _decisions_db(tmp_path / "d1.db", DECISIONS + [other])
    first = tmp_path / "first.db"
    build_structure_incremental(decisions_db=db1, structure_db=tmp_path / "absent.db",
                                output_path=first, force_full=True)
    conn = _ro(first)
    assert {r[0] for r in conn.execute("SELECT decision_id FROM erwaegungen_unnumbered")} == {
        UNNUMBERED_ID, other[0]}
    conn.close()

    db2 = _decisions_db(tmp_path / "d2.db", [d for d in changed if d[0] != other[0]])
    second = tmp_path / "second.db"
    stats = build_structure_incremental(decisions_db=db2, structure_db=first,
                                        output_path=second)
    assert stats["mode"] == "incremental"
    assert stats["counts"] == {"new": 0, "changed": 1, "deleted": 1}
    conn = _ro(second)
    try:
        assert conn.execute("SELECT count(*) FROM erwaegungen_unnumbered").fetchone()[0] == 0
        assert conn.execute("SELECT has_erwaegungen, erwaegungen_paragraph_count FROM structure "
                            "WHERE decision_id = ?", (UNNUMBERED_ID,)).fetchone() == (1, 4)
        assert conn.execute("SELECT count(*) FROM structure WHERE decision_id = ?",
                            (other[0],)).fetchone()[0] == 0
    finally:
        conn.close()


def test_shard_builder_writes_what_the_served_text_builder_writes(sidecar, tmp_path):
    """The shard fallback (build_db) and step 2g must agree on the flags, the
    count and the unnumbered rows, or a fallback night changes the dataset."""
    shard = tmp_path / "decisions" / "fixture.jsonl"
    shard.parent.mkdir()
    shard.write_text("".join(json.dumps(
        {"decision_id": d, "court": c, "canton": k, "language": "de", "decision_date": dt,
         "full_text": t}, ensure_ascii=False) + "\n" for d, c, k, dt, t in DECISIONS))
    shard_db = tmp_path / "shard.db"
    build_db([shard], shard_db)

    def snapshot(db):
        conn = _ro(db)
        try:
            return (
                conn.execute("SELECT decision_id, has_sachverhalt, has_erwaegungen, has_dispositiv, "
                             "erwaegungen_paragraph_count FROM structure ORDER BY 1").fetchall(),
                conn.execute("SELECT decision_id, e_number, text FROM erwaegungen_paragraph "
                             "ORDER BY 1, 2").fetchall(),
                conn.execute("SELECT decision_id, text FROM erwaegungen_unnumbered ORDER BY 1")
                .fetchall())
        finally:
            conn.close()
    assert snapshot(shard_db) == snapshot(sidecar)


# ── 2. which read the export takes ─────────────────────────────────────

def test_export_reads_through_the_covering_index(sidecar, tmp_path, caplog):
    conn = _ro(sidecar)
    try:
        assert ep._structure_meta_source(conn) == (ep._STRUCTURE_META_COLS_INDEXED,
                                                   "idx_structure_export")
    finally:
        conn.close()
    with caplog.at_level(logging.INFO, logger="export_parquet"):
        export_decision_structure(sidecar, tmp_path / "o")
    assert "index-only via idx_structure_export" in caplog.text


def test_older_or_uncovered_sidecars_keep_the_table_scan(tmp_path, caplog):
    legacy = tmp_path / "legacy.db"
    conn = sqlite3.connect(legacy)
    conn.executescript(
        "CREATE TABLE structure (decision_id TEXT PRIMARY KEY, court TEXT, language TEXT, "
        "sachverhalt TEXT, sachverhalt_method TEXT, erwaegungen TEXT, erwaegungen_method TEXT, "
        "erwaegungen_paragraph_count INTEGER, dispositiv TEXT, dispositiv_method TEXT);"
        "INSERT INTO structure VALUES ('bger_1', 'bger', 'de', 'S', 'a', 'E', 'a', 1, 'D', 'a');")
    conn.commit()
    assert ep._structure_meta_source(conn) == (ep._STRUCTURE_META_COLS, None)
    # an index of that name that does not cover the export is not trusted
    conn.execute("CREATE INDEX idx_structure_export ON structure(decision_id)")
    conn.commit()
    with caplog.at_level(logging.WARNING, logger="export_parquet"):
        assert ep._structure_meta_source(conn) == (ep._STRUCTURE_META_COLS, None)
    conn.close()
    assert "does not cover the export" in caplog.text
    assert export_decision_structure(legacy, tmp_path / "o") == {"structure": 1}


def test_an_empty_indexed_sidecar_keeps_the_last_good_file(sidecar, tmp_path):
    out = tmp_path / "o"
    assert export_decision_structure(sidecar, out) == {"structure": len(DECISIONS)}
    good = out / "structure" / "structure.parquet"
    ino = good.stat().st_ino
    conn = sqlite3.connect(sidecar)
    conn.execute("DELETE FROM structure")
    conn.commit()
    conn.close()
    assert export_decision_structure(sidecar, out) == {
        "structure_skipped": "structure is empty; last good file kept"}
    assert good.stat().st_ino == ino


# ── 3. the metadata export must not read the section text ──────────────
#
# Deterministic, platform-independent stand-in for "the export reads only
# small columns": zero every overflow page of the ``structure`` table, so any
# read that walks a section-text chain fails with SQLITE_CORRUPT, while a
# read that stays on leaf/index pages is unaffected. On the production
# sidecar that walk is the ~52 GB / ~27 min that froze structure.parquet.

def _varint(buf: bytes, i: int) -> tuple[int, int]:
    v = 0
    for k in range(8):
        b = buf[i + k]
        v = (v << 7) | (b & 0x7F)
        if not b & 0x80:
            return v, i + k + 1
    return (v << 8) | buf[i + 8], i + 9


def _page_size(header: bytes) -> int:
    size = int.from_bytes(header[16:18], "big")
    return 65536 if size == 1 else size


def _overflow_chains(db: Path, table: str) -> list[list[int]]:
    """Overflow page chains of ``table``'s rows, read from the file format
    (sqlite.org/fileformat2.html, "B-tree Pages" and "Cell Payload Overflow
    Pages"): one list of page numbers per row that spills."""
    data = db.read_bytes()
    page_size = _page_size(data)
    usable = page_size - data[20]
    max_local = usable - 35
    min_local = (usable - 12) * 32 // 255 - 23
    conn = _ro(db)
    try:
        root = conn.execute("SELECT rootpage FROM sqlite_master WHERE type = 'table' "
                            "AND name = ?", (table,)).fetchone()[0]
    finally:
        conn.close()

    def page(n: int) -> bytes:
        return data[(n - 1) * page_size:n * page_size]

    chains, todo = [], [root]
    while todo:
        n = todo.pop()
        p = page(n)
        h = 100 if n == 1 else 0
        kind, ncells = p[h], int.from_bytes(p[h + 3:h + 5], "big")
        hdr = 12 if kind == 0x05 else 8
        cells = [int.from_bytes(p[h + hdr + 2 * i:h + hdr + 2 * i + 2], "big")
                 for i in range(ncells)]
        if kind == 0x05:      # table interior: left children + right-most child
            todo.extend(int.from_bytes(p[c:c + 4], "big") for c in cells)
            todo.append(int.from_bytes(p[h + 8:h + 12], "big"))
            continue
        assert kind == 0x0D, f"page {n}: not a table b-tree page ({kind:#x})"
        for c in cells:
            size, i = _varint(p, c)
            _, i = _varint(p, i)  # rowid
            if size <= max_local:
                continue
            k = min_local + (size - min_local) % (usable - 4)
            local = k if k <= max_local else min_local
            nxt, chain = int.from_bytes(p[i + local:i + local + 4], "big"), []
            while nxt:
                chain.append(nxt)
                nxt = int.from_bytes(page(nxt)[:4], "big")
            chains.append(chain)
    return chains


def _zero_pages(db: Path, pages: list[int]) -> None:
    page_size = _page_size(db.read_bytes()[:100])
    with open(db, "r+b") as f:
        for n in pages:
            f.seek((n - 1) * page_size)
            f.write(bytes(page_size))


def test_overflow_chain_walker_matches_sqlite(sidecar):
    chains = _overflow_chains(sidecar, "structure")
    assert len(chains) == len(DECISIONS) - 1     # the cantonal row has no sections
    # >= 2 pages per chain: a zeroed first page then ends the chain early,
    # which SQLite reports as corruption instead of returning NUL bytes
    assert all(len(c) >= 2 for c in chains)
    conn = _ro(sidecar)
    try:
        dbstat = {r[0] for r in conn.execute(
            "SELECT pageno FROM dbstat WHERE name = 'structure' AND pagetype = 'overflow'")}
    except sqlite3.OperationalError:          # SQLite built without DBSTAT
        pytest.skip("no dbstat in this SQLite build to cross-check against")
    finally:
        conn.close()
    assert {n for c in chains for n in c} == dbstat


class ReadsSectionText(AssertionError):
    """The metadata export walked a section-text overflow chain."""


def test_metadata_export_reads_no_section_text(sidecar, tmp_path):
    # what the pre-2026-10-08 export computed from the text itself, read from
    # the table: the stored flags must mean exactly that
    conn = _ro(sidecar)
    try:
        legacy = [dict(zip(STRUCTURE_META_SCHEMA.names, r, strict=True)) for r in conn.execute(
            f"SELECT {ep._STRUCTURE_META_COLS} FROM structure NOT INDEXED")]
    finally:
        conn.close()
    for r in legacy:
        for k in ("has_sachverhalt", "has_erwaegungen", "has_dispositiv"):
            r[k] = bool(r[k])

    _zero_pages(sidecar, [n for c in _overflow_chains(sidecar, "structure") for n in c])
    conn = _ro(sidecar)
    try:
        # read from the table itself (NOT INDEXED: the export index covers
        # these columns), the corruption is real for anything behind the text ...
        with pytest.raises(sqlite3.DatabaseError, match="malformed"):
            conn.execute("SELECT erwaegungen_method FROM structure NOT INDEXED").fetchall()
        # ... and invisible to the columns in front of it
        assert len(conn.execute("SELECT decision_id, court, language FROM structure "
                                "NOT INDEXED").fetchall()) == len(DECISIONS)
    finally:
        conn.close()

    try:
        counts = export_decision_structure(sidecar, tmp_path / "o")
    except sqlite3.DatabaseError as e:
        if "malformed" in str(e):
            raise ReadsSectionText(str(e)) from e
        raise
    assert counts == {"structure": len(DECISIONS)}
    got = pq.read_table(tmp_path / "o" / "structure" / "structure.parquet").to_pylist()
    # row order follows the index (decision_id), not rowid
    assert [r["decision_id"] for r in got] == sorted(r["decision_id"] for r in got)
    assert got == sorted(legacy, key=itemgetter("decision_id"))
