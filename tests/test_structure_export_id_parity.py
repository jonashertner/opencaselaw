"""structure.parquet and erwaegungen_paragraphs.parquet must describe the same
decisions, and the metadata export must be able to finish on a served-text
sidecar.

2026-10-07: on Hugging Face, structure/structure.parquet was last written
2026-09-10 and still keyed the 14,578 historical BGE rows as
``bge_historical_*`` while erwaegungen_paragraphs.parquet (2026-10-04) keyed
them as ``bge_*``. Since step 2g builds the sidecar from served text, the
``structure`` rows carry the full section text and every column the metadata
export needs sits behind it; the export projects over its budget and
``_bounded_stream`` keeps the last good — stale — file. Proposed fix:
docs/proposals/structure-export-small-columns.md.

The fixture sidecar here is written by the production step-2g writer
(``build_structure_incremental`` over a decisions.db), not by hand.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from operator import itemgetter
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

pa = pytest.importorskip("pyarrow")
pq = pytest.importorskip("pyarrow.parquet")
from export_parquet import STRUCTURE_META_SCHEMA, export_decision_structure  # noqa: E402
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


# Historical BGE served as bge_* (the ids the stale file carried as
# bge_historical_*), current BGE, BGer, BVGer, and a cantonal decision whose
# text has no section markers (a structure row with no paragraphs).
DECISIONS = [
    ("bge_101_Ia_1", "bge", "CH", "1975-01-29", _federal_text("101 Ia 1")),
    ("bge_102_II_100", "bge", "CH", "1976-05-04", _federal_text("102 II 100")),
    ("bge_150_III_12", "bge", "CH", "2024-01-10", _federal_text("150 III 12")),
    ("bger_4A_100_2024", "bger", "CH", "2024-06-12", _federal_text("4A_100/2024")),
    ("bvger_A-1234_2023", "bvger", "CH", "2023-11-02",
     _federal_text("A-1234/2023", "Bundesverwaltungsgericht")),
    ("zh_obergericht_LB230001", "zh_obergericht", "ZH", "2023-03-03",
     "Das Obergericht des Kantons Zürich zieht in Betracht, dass die Eingabe "
     "verspätet ist und nicht darauf einzutreten ist. " * 10),
]
STALE_IDS = ["bge_historical_101_Ia_1", "bge_historical_102_II_100"]


def _decisions_db(path: Path) -> Path:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE decisions (decision_id TEXT PRIMARY KEY, court TEXT, "
                 "canton TEXT, language TEXT, decision_date TEXT, full_text TEXT, "
                 "regeste TEXT)")
    conn.executemany("INSERT INTO decisions VALUES (?, ?, ?, 'de', ?, ?, NULL)", DECISIONS)
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


# ── 1. the export finishes and both files key the same decisions ───────

def test_structure_export_finishes_with_the_paragraph_export_ids(sidecar, tmp_path):
    out = tmp_path / "dataset"
    meta_path = out / "structure" / "structure.parquet"
    para_path = out / "structure" / "erwaegungen_paragraphs.parquet"
    _write_stale_structure_parquet(meta_path)

    counts = export_decision_structure(sidecar, out, include_paragraphs=True)

    conn = sqlite3.connect(f"file:{sidecar}?mode=ro&immutable=1", uri=True)
    try:
        sidecar_ids = {r[0] for r in conn.execute("SELECT decision_id FROM structure")}
        n_paragraphs = conn.execute(
            "SELECT count(*) FROM erwaegungen_paragraph WHERE text IS NOT NULL AND text != ''"
        ).fetchone()[0]
    finally:
        conn.close()
    assert sidecar_ids == {d[0] for d in DECISIONS}
    # finished: both files written this run, nothing skipped, nothing kept
    assert counts == {"structure": len(DECISIONS), "erwaegungen_paragraphs": n_paragraphs}
    status = json.loads((out / "structure" / "export_status.json").read_text())
    assert status["counts"] == counts
    assert not list((out / "structure").glob("*.tmp"))

    meta = pq.read_table(meta_path).to_pylist()
    para_ids = set(pq.read_table(para_path, columns=["decision_id"]).column(0).to_pylist())
    meta_ids = {r["decision_id"] for r in meta}

    assert len(meta) == len(meta_ids)                        # one row per decision
    assert meta_ids == sidecar_ids                           # the stale file was replaced
    assert not meta_ids & set(STALE_IDS)
    assert para_ids <= meta_ids, f"paragraphs without a structure row: {para_ids - meta_ids}"
    # every decision in this fixture with Erwägungen has numbered ones, so the
    # two exports must name exactly the same decisions
    assert para_ids == {r["decision_id"] for r in meta if r["has_erwaegungen"]}
    assert "zh_obergericht_LB230001" in meta_ids - para_ids  # structure row, no paragraphs


# ── 2. the metadata export must not read the section text ──────────────
#
# Deterministic, platform-independent stand-in for "the export reads only
# small columns": zero every overflow page of the ``structure`` table, so any
# read that walks a section-text chain fails with SQLITE_CORRUPT, while a
# read that stays on leaf/index pages is unaffected. On the production
# sidecar that walk is the ~52 GB / ~27 min that keeps structure.parquet
# frozen.

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
    conn = sqlite3.connect(f"file:{db}?mode=ro&immutable=1", uri=True)
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
    structured = len(DECISIONS) - 1          # the cantonal row has no sections
    assert len(chains) == structured
    # >= 2 pages per chain: a zeroed first page then ends the chain early,
    # which SQLite reports as corruption instead of returning NUL bytes
    assert all(len(c) >= 2 for c in chains)
    conn = sqlite3.connect(f"file:{sidecar}?mode=ro&immutable=1", uri=True)
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


@pytest.mark.xfail(
    raises=ReadsSectionText, strict=True,
    reason="proposal docs/proposals/structure-export-small-columns.md not implemented: "
           "the metadata export (and its probe) still read every row's section text. "
           "When this XPASSes, the fix has landed: drop this marker.")
def test_metadata_export_reads_no_section_text(sidecar, tmp_path):
    reference = export_decision_structure(sidecar, tmp_path / "ref")
    expected = pq.read_table(tmp_path / "ref" / "structure" / "structure.parquet").to_pylist()

    _zero_pages(sidecar, [n for c in _overflow_chains(sidecar, "structure") for n in c])
    conn = sqlite3.connect(f"file:{sidecar}?mode=ro&immutable=1", uri=True)
    try:
        # read from the table itself (NOT INDEXED: an index may cover these
        # columns), the corruption is real for anything behind the text ...
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
    assert counts == reference == {"structure": len(DECISIONS)}
    got = pq.read_table(tmp_path / "o" / "structure" / "structure.parquet").to_pylist()
    # row order may follow the index rather than rowid
    assert (sorted(got, key=itemgetter("decision_id"))
            == sorted(expected, key=itemgetter("decision_id")))
