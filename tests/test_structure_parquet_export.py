"""P1.4: the structure sidecar leaves the MCP silo — per-decision section
metadata + the erwaegungen paragraph segmentation WITH verbatim text."""
from __future__ import annotations

import json
import math
import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

pq = pytest.importorskip("pyarrow.parquet")
import export_parquet as ep  # noqa: E402
from export_parquet import (PARAGRAPH_SCHEMA, STRUCTURE_META_SCHEMA,  # noqa: E402
                            export_decision_structure)


@pytest.fixture(autouse=True)
def _clean_budget_env(monkeypatch):
    # the budgets are read from the environment; an operator tuning them in
    # a shell must not turn this file red
    for var in ("OCL_STRUCTURE_EXPORT_BUDGET_S", "OCL_STRUCTURE_PARAGRAPHS_BUDGET_S",
                "OCL_EXPORT_WALLCLOCK_BUDGET_S"):
        monkeypatch.delenv(var, raising=False)


def test_structure_export_roundtrip(tmp_path):
    db = tmp_path / "decision_structure.db"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE structure (decision_id TEXT, court TEXT, canton TEXT,
            language TEXT, decision_date TEXT, regeste TEXT,
            sachverhalt TEXT, sachverhalt_method TEXT,
            erwaegungen TEXT, erwaegungen_method TEXT,
            erwaegungen_paragraph_count INTEGER,
            dispositiv TEXT, dispositiv_method TEXT, dispositiv_orders TEXT,
            extracted_at TEXT);
        CREATE TABLE erwaegungen_paragraph (decision_id TEXT, e_number TEXT,
            depth INTEGER, parent TEXT, text TEXT);
        INSERT INTO structure VALUES
            ('bger_1', 'bger', 'CH', 'de', '2024-01-01', 'reg',
             'A. Sachverhalt...', 'anchor', 'Erwägungen...', 'anchor', 2,
             'Demnach erkennt...', 'anchor', '[]', '2026-07-02'),
            ('zh_1', 'zh_gerichte', 'ZH', 'de', '2024-02-02', NULL,
             NULL, NULL, NULL, NULL, 0, NULL, NULL, NULL, '2026-07-02');
        INSERT INTO erwaegungen_paragraph VALUES
            ('bger_1', '1', 1, NULL, 'Die Beschwerde richtet sich gegen...'),
            ('bger_1', '1.1', 2, '1', 'Nach Art. 76 BGG ist...'),
            ('bger_1', NULL, NULL, NULL, '');
    """)
    conn.commit(); conn.close()

    out = tmp_path / "dataset"
    # default: lean metadata only (paragraphs are 4.8 GB, weekly opt-in)
    counts = export_decision_structure(db, out)
    assert counts == {"structure": 2}
    assert not (out / "structure" / "erwaegungen_paragraphs.parquet").exists()
    counts = export_decision_structure(db, out, include_paragraphs=True)
    assert counts == {"structure": 2, "erwaegungen_paragraphs": 2}  # empty text excluded

    meta = pq.read_table(out / "structure" / "structure.parquet")
    assert meta.schema.equals(STRUCTURE_META_SCHEMA)
    rows = {r["decision_id"]: r for r in meta.to_pylist()}
    assert rows["bger_1"]["has_erwaegungen"] is True
    assert rows["bger_1"]["erwaegungen_paragraph_count"] == 2
    assert rows["zh_1"]["has_sachverhalt"] is False

    paras = pq.read_table(out / "structure" / "erwaegungen_paragraphs.parquet")
    assert paras.schema.equals(PARAGRAPH_SCHEMA)
    p = paras.to_pylist()
    assert p[1]["e_number"] == "1.1" and p[1]["depth"] == 2 and p[1]["parent"] == "1"


def test_missing_sidecar_is_clean_skip(tmp_path):
    assert export_decision_structure(tmp_path / "absent.db", tmp_path / "o") == {}


# --- bounded export (2026-09-10): the served-text sidecar made the metadata
# scan ~27 min and pushed publish step 3 over its 3,600 s timeout, which
# cascade-skipped the HuggingFace upload and the git pushes.  The structure
# exports are now probe-budgeted, hard-stopped, and non-fatal.


def _sidecar(tmp_path, n_rows=50):
    db = tmp_path / "decision_structure.db"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE structure (decision_id TEXT PRIMARY KEY, court TEXT, canton TEXT,
            language TEXT, decision_date TEXT, regeste TEXT,
            sachverhalt TEXT, sachverhalt_method TEXT,
            erwaegungen TEXT, erwaegungen_method TEXT,
            erwaegungen_paragraph_count INTEGER,
            dispositiv TEXT, dispositiv_method TEXT, dispositiv_orders TEXT,
            extracted_at TEXT);
        CREATE TABLE erwaegungen_paragraph (decision_id TEXT, e_number TEXT,
            depth INTEGER, parent TEXT, text TEXT);
    """)
    conn.executemany(
        "INSERT INTO structure VALUES (?, 'bger', 'CH', 'de', '2024-01-01', NULL, "
        "'A. Sachverhalt', 'anchor', ?, 'anchor', 1, 'Demnach', 'anchor', '[]', '2026-09-10')",
        [(f"bger_{i}", "Erwägungen " * 200) for i in range(n_rows)])
    conn.executemany(
        "INSERT INTO erwaegungen_paragraph VALUES (?, '1', 1, NULL, 'Die Beschwerde...')",
        [(f"bger_{i}",) for i in range(n_rows)])
    conn.commit(); conn.close()
    return db


def test_bounded_export_runs_within_budget(tmp_path):
    db = _sidecar(tmp_path)
    counts = export_decision_structure(db, tmp_path / "o", include_paragraphs=True)
    assert counts == {"structure": 50, "erwaegungen_paragraphs": 50}
    assert not list((tmp_path / "o" / "structure").glob("*.tmp"))


def test_projection_over_budget_skips_and_keeps_last_good_file(tmp_path, monkeypatch):
    db = _sidecar(tmp_path)
    out = tmp_path / "o"
    assert export_decision_structure(db, out) == {"structure": 50}
    good = out / "structure" / "structure.parquet"
    before = (good.stat().st_mtime_ns, good.read_bytes())

    # the probe says the full scan would take 27 min against a 10 min budget
    monkeypatch.setattr(ep, "_projected_seconds", lambda *a, **k: 27 * 60.0)
    counts = export_decision_structure(db, out, budget_s=600)
    assert "structure" not in counts
    assert counts["structure_skipped"].startswith("projected 27.0 min > budget 10 min")
    assert (good.stat().st_mtime_ns, good.read_bytes()) == before
    assert not list((out / "structure").glob("*.tmp"))


def test_budget_zero_disables(tmp_path):
    db = _sidecar(tmp_path)
    counts = export_decision_structure(db, tmp_path / "o", include_paragraphs=True,
                                       budget_s=0, paragraphs_budget_s=0)
    assert counts == {"structure_skipped": "disabled (budget 0)",
                      "erwaegungen_paragraphs_skipped": "disabled (budget 0)"}
    assert not (tmp_path / "o" / "structure" / "structure.parquet").exists()


def test_env_budgets_are_read(tmp_path, monkeypatch):
    db = _sidecar(tmp_path)
    monkeypatch.setenv("OCL_STRUCTURE_EXPORT_BUDGET_S", "0")
    monkeypatch.setenv("OCL_STRUCTURE_PARAGRAPHS_BUDGET_S", "not-a-number")  # → default
    counts = export_decision_structure(db, tmp_path / "o", include_paragraphs=True)
    assert counts == {"structure_skipped": "disabled (budget 0)",
                      "erwaegungen_paragraphs": 50}


def test_hard_stop_interrupts_and_removes_partial_file(tmp_path, monkeypatch):
    db = _sidecar(tmp_path, n_rows=400)
    out = tmp_path / "o"
    assert export_decision_structure(db, out) == {"structure": 400}
    good = out / "structure" / "structure.parquet"
    st = good.stat()
    before = (st.st_ino, st.st_mtime_ns, good.read_bytes())

    # probe passes, but the deadline is already in the past when the real
    # query starts: the progress handler (checked every opcode here) aborts it
    monkeypatch.setattr(ep, "_projected_seconds", lambda *a, **k: 1.0)
    monkeypatch.setattr(ep, "_PROGRESS_EVERY_OPCODES", 1)
    monkeypatch.setattr(ep, "_HARD_STOP_FACTOR", -1.0)
    counts = export_decision_structure(db, out, budget_s=60)
    assert "structure" not in counts
    assert counts["structure_skipped"].startswith("hard stop after")
    st = good.stat()
    assert (st.st_ino, st.st_mtime_ns, good.read_bytes()) == before  # same inode: never replaced
    assert not list((out / "structure").glob("*.tmp"))
    status = json.loads((out / "structure" / "export_status.json").read_text())
    assert status["counts"] == counts and status["at"]


def test_projection_probe_scales_with_table(tmp_path):
    db = _sidecar(tmp_path, n_rows=1000)
    conn = sqlite3.connect(f"file:{db}?mode=ro&immutable=1", uri=True)
    try:
        proj = ep._projected_seconds(conn, "structure", ep._STRUCTURE_META_COLS)
        assert 0.0 < proj < 60.0  # 1000 tiny rows: milliseconds, not minutes
        assert ep._projected_seconds(conn, "erwaegungen_paragraph", ep._PARAGRAPH_COLS,
                                     "text IS NOT NULL AND text != ''") > 0.0
    finally:
        conn.close()
    empty = tmp_path / "empty.db"
    c = sqlite3.connect(empty); c.execute("CREATE TABLE structure (decision_id TEXT)"); c.commit(); c.close()
    c = sqlite3.connect(f"file:{empty}?mode=ro&immutable=1", uri=True)
    assert ep._projected_seconds(c, "structure", "decision_id") == 0.0
    c.close()

    # every probe window in a deleted-rowid hole → inconclusive, not "free"
    holes = tmp_path / "holes.db"
    c = sqlite3.connect(holes)
    c.execute("CREATE TABLE structure (decision_id TEXT)")
    c.execute("INSERT INTO structure (rowid, decision_id) VALUES (1, 'a')")
    c.execute("INSERT INTO structure (rowid, decision_id) VALUES (10000000, 'b')")
    c.commit(); c.close()
    c = sqlite3.connect(f"file:{holes}?mode=ro&immutable=1", uri=True)
    assert ep._projected_seconds(c, "structure", "decision_id") == math.inf
    c.close()


def test_inconclusive_probe_and_empty_table_keep_last_good_file(tmp_path):
    db = _sidecar(tmp_path)
    out = tmp_path / "o"
    assert export_decision_structure(db, out) == {"structure": 50}
    good = out / "structure" / "structure.parquet"
    ino = good.stat().st_ino

    c = sqlite3.connect(db); c.execute("DELETE FROM structure WHERE rowid > 1"); c.execute(
        "INSERT INTO structure (rowid, decision_id) VALUES (10000000, 'x')"); c.commit(); c.close()
    counts = export_decision_structure(db, out)
    assert counts["structure_skipped"].startswith("probe inconclusive")
    assert good.stat().st_ino == ino

    c = sqlite3.connect(db); c.execute("DELETE FROM structure"); c.commit(); c.close()
    counts = export_decision_structure(db, out)
    assert counts["structure_skipped"] == "structure is empty; last good file kept"
    assert good.stat().st_ino == ino
    assert pq.read_table(good).num_rows == 50


def test_structure_failure_is_nonfatal(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise sqlite3.OperationalError("disk I/O error")
    monkeypatch.setattr(ep, "export_decision_structure", boom)
    counts = ep.export_decision_structure_nonfatal(tmp_path / "x.db", tmp_path / "o")
    assert counts == {"structure_skipped": "error: OperationalError: disk I/O error"}


def test_stream_writer_removes_tmp_on_failure(tmp_path):
    db = _sidecar(tmp_path)
    conn = sqlite3.connect(f"file:{db}?mode=ro&immutable=1", uri=True)
    out = tmp_path / "x.parquet"
    with pytest.raises(sqlite3.OperationalError):
        ep._stream_query_to_parquet(conn, "SELECT * FROM no_such_table",
                                    STRUCTURE_META_SCHEMA, out)
    conn.close()
    assert not out.exists() and not out.with_suffix(".parquet.tmp").exists()


def test_wallclock_cap_skips_when_window_is_used_up(tmp_path):
    db = _sidecar(tmp_path)
    out = tmp_path / "o"
    assert export_decision_structure(db, out) == {"structure": 50}
    before = (out / "structure" / "structure.parquet").read_bytes()
    # main() started 55 min ago against a 55 min window: nothing left
    counts = export_decision_structure(db, out, include_paragraphs=True,
                                       deadline_monotonic=ep.time.monotonic() - 1.0)
    assert counts["structure_skipped"].startswith("no time left in the export window")
    assert counts["erwaegungen_paragraphs_skipped"].startswith("no time left")
    assert (out / "structure" / "structure.parquet").read_bytes() == before
    assert not (out / "structure" / "erwaegungen_paragraphs.parquet").exists()


def test_wallclock_cap_tightens_budget_and_hard_stop(tmp_path, monkeypatch):
    db = _sidecar(tmp_path)
    seen = {}

    def fake_projection(*a, **k):
        return 8 * 60.0  # 8 min: fits a 10 min budget but not a 5 min window
    monkeypatch.setattr(ep, "_projected_seconds", fake_projection)
    counts = export_decision_structure(db, tmp_path / "o", budget_s=600,
                                       deadline_monotonic=ep.time.monotonic() + 5 * 60)
    assert counts["structure_skipped"].startswith("projected 8.0 min > budget 5 min")

    # with the window wide open the same projection runs (and finishes)
    counts = export_decision_structure(db, tmp_path / "o", budget_s=600,
                                       deadline_monotonic=ep.time.monotonic() + 3600)
    assert counts == {"structure": 50}


def _run_main(tmp_path, monkeypatch, capsys, structure_db):
    (tmp_path / "in").mkdir(exist_ok=True)
    monkeypatch.setattr(ep, "export_parquet", lambda *a, **k: {"decisions_2024": 1})
    monkeypatch.setattr(sys, "argv", [
        "export_parquet.py", "--jsonl", "--input", str(tmp_path / "in"),
        "--output", str(tmp_path / "out"), "--graph-db", str(tmp_path / "absent_graph.db"),
        "--structure-db", str(structure_db), "--structure-paragraphs"])
    ep.main()
    return capsys.readouterr().out


def test_main_prints_skip_reasons_and_exits_zero(tmp_path, monkeypatch, capsys):
    db = _sidecar(tmp_path)
    monkeypatch.setenv("OCL_STRUCTURE_EXPORT_BUDGET_S", "0")
    out = _run_main(tmp_path, monkeypatch, capsys, db)
    assert "Structure: 50 paragraphs, structure skipped: disabled (budget 0)" in out
    assert "Exported 1 decisions to 1 Parquet files" in out


def test_main_survives_a_broken_sidecar(tmp_path, monkeypatch, capsys):
    broken = tmp_path / "decision_structure.db"
    broken.write_bytes(b"not a database at all" * 100)
    out = _run_main(tmp_path, monkeypatch, capsys, broken)   # no SystemExit, no raise
    assert "structure skipped: error: DatabaseError" in out
    assert "Exported 1 decisions to 1 Parquet files" in out


def test_main_wallclock_window_used_up_skips_structure(tmp_path, monkeypatch, capsys):
    db = _sidecar(tmp_path)
    monkeypatch.setenv("OCL_EXPORT_WALLCLOCK_BUDGET_S", "30")   # main() spent it all
    monkeypatch.setattr(ep.time, "monotonic", (lambda base=ep.time.monotonic(): lambda: base + 100)())
    out = _run_main(tmp_path, monkeypatch, capsys, db)
    assert "structure skipped: no time left in the export window" in out
    assert "erwaegungen_paragraphs skipped: no time left" in out
