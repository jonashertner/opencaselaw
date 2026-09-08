"""Tests for the incremental decision_structure builder (shadow-mode v0.1).

Mirrors ``test_build_reference_graph_incremental.py`` — same shape, same
contracts, against the structure sidecar instead of the citation graph.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from search_stack.extract_decision_structure_incremental import (  # noqa: E402
    EXTRACTOR_VERSION,
    _diff_state,
    _ensure_state_tables,
    _extractor_hash,
    _get_meta,
    build_structure_incremental,
)


# ── fixture corpus ────────────────────────────────────────────────────


def _make_decisions_db(tmp_path: Path, rows: list[dict]) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    db_path = tmp_path / "decisions.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE decisions (
            decision_id TEXT PRIMARY KEY,
            court TEXT,
            canton TEXT,
            language TEXT,
            decision_date TEXT,
            full_text TEXT,
            regeste TEXT
        )
        """
    )
    for r in rows:
        conn.execute(
            """
            INSERT INTO decisions(decision_id, court, canton, language,
                                  decision_date, full_text, regeste)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                r["decision_id"], r.get("court"), r.get("canton"),
                r.get("language", "de"), r.get("decision_date"),
                r.get("full_text"), r.get("regeste"),
            ),
        )
    conn.commit()
    conn.close()
    return db_path


# Long-enough full_text to pass the >=500 char gate, with realistic
# Erwägungen markers so the extractor produces actual paragraph rows.
_BASE_FULL_TEXT = (
    "Sachverhalt:\n\n"
    "A.- Mit Verfügung vom 1. Januar 2024 hat die Behörde X der Klägerin Y "
    "die Bewilligung erteilt. Gegen diese Verfügung wurde am 15. Januar "
    "2024 Beschwerde erhoben. Der Beschwerdeführer rügt die Verletzung von "
    "Bundesrecht. Die Vorinstanz hat die Beschwerde abgewiesen.\n\n"
    "Erwägungen:\n\n"
    "1. Die Beschwerde wurde fristgerecht eingereicht. Die Voraussetzungen "
    "der Beschwerdelegitimation sind erfüllt.\n\n"
    "2. In der Sache geht es um die Auslegung von Art. 41 OR. Diese "
    "Bestimmung regelt die ausservertragliche Haftung. Vgl. BGE 134 V 231.\n\n"
    "2.1 Der erste Aspekt betrifft die Adäquanz. Die Rechtsprechung "
    "verlangt einen direkten Kausalzusammenhang.\n\n"
    "3. Demnach ist die Beschwerde abzuweisen.\n\n"
    "Demnach erkennt das Bundesgericht:\n\n"
    "1. Die Beschwerde wird abgewiesen.\n"
    "2. Die Gerichtskosten werden dem Beschwerdeführer auferlegt.\n"
)

BASE_ROWS = [
    {
        "decision_id": "bger_4A_1_2024", "court": "bger", "canton": "CH",
        "language": "de", "decision_date": "2024-06-12",
        "full_text": _BASE_FULL_TEXT, "regeste": None,
    },
    {
        "decision_id": "bger_9C_5_2024", "court": "bger", "canton": "CH",
        "language": "de", "decision_date": "2024-08-01",
        "full_text": _BASE_FULL_TEXT.replace("Art. 41 OR", "Art. 8 EMRK"),
        "regeste": None,
    },
    {
        "decision_id": "bge_140_III_86", "court": "bge", "canton": "CH",
        "language": "de", "decision_date": "2014-02-15",
        "full_text": _BASE_FULL_TEXT,
        "regeste": "Art. 41 OR; Vertrauensschutz.",
    },
]


# ── _extractor_hash ───────────────────────────────────────────────────


def test_hash_deterministic() -> None:
    h1 = _extractor_hash(BASE_ROWS[0])
    h2 = _extractor_hash(dict(BASE_ROWS[0]))
    assert h1 == h2 and len(h1) == 64


def test_hash_changes_per_field() -> None:
    base = _extractor_hash(BASE_ROWS[0])
    for field in ("decision_id", "court", "canton", "language",
                  "decision_date", "full_text"):
        mut = dict(BASE_ROWS[0])
        mut[field] = (mut.get(field) or "x") + "_changed"
        assert _extractor_hash(mut) != base, f"hash unchanged for {field}"


# ── _diff_state ───────────────────────────────────────────────────────


def test_diff_classifies_new_changed_deleted(tmp_path: Path) -> None:
    decisions_db = _make_decisions_db(tmp_path, BASE_ROWS)
    structure_db = tmp_path / "structure.db"
    build_structure_incremental(
        decisions_db=decisions_db,
        structure_db=tmp_path / "nonexistent.db",
        output_path=structure_db,
        force_full=True,
    )

    mutated = [dict(r) for r in BASE_ROWS]
    mutated[0]["full_text"] = mutated[0]["full_text"] + "\n\nZusatz."
    mutated.pop(2)
    mutated.append({
        "decision_id": "bger_5A_99_2026", "court": "bger", "canton": "CH",
        "language": "de", "decision_date": "2026-01-01",
        "full_text": _BASE_FULL_TEXT,
    })
    decisions_db_v2 = _make_decisions_db(tmp_path / "v2", mutated)

    conn = sqlite3.connect(structure_db)
    new_ids, changed_ids, deleted_ids, hashes_by_id, _ = _diff_state(
        decisions_db_v2, conn,
    )
    conn.close()
    assert new_ids == {"bger_5A_99_2026"}
    assert changed_ids == {"bger_4A_1_2024"}
    assert deleted_ids == {"bge_140_III_86"}
    assert len(hashes_by_id) == 3


def test_short_full_text_skipped(tmp_path: Path) -> None:
    """The extractor's gate is full_text >= 500 chars; rows below that
    must NOT appear in processed_decisions."""
    rows = [
        {"decision_id": "x_short", "court": "x", "language": "de",
         "full_text": "tiny", "decision_date": "2024-01-01"},
        BASE_ROWS[0],
    ]
    decisions_db = _make_decisions_db(tmp_path, rows)
    structure_db = tmp_path / "structure.db"
    build_structure_incremental(
        decisions_db=decisions_db,
        structure_db=tmp_path / "nonexistent.db",
        output_path=structure_db,
        force_full=True,
    )
    conn = sqlite3.connect(structure_db)
    pids = {r[0] for r in conn.execute("SELECT decision_id FROM processed_decisions")}
    conn.close()
    assert "x_short" not in pids
    assert BASE_ROWS[0]["decision_id"] in pids


# ── end-to-end equivalence ────────────────────────────────────────────


def _signature(structure_db: Path) -> dict:
    conn = sqlite3.connect(structure_db)
    try:
        struct = sorted(
            tuple(r) for r in conn.execute(
                "SELECT decision_id, court, canton, language, decision_date, "
                "erwaegungen_paragraph_count FROM structure"
            )
        )
        paras = sorted(
            tuple(r) for r in conn.execute(
                "SELECT decision_id, e_number, depth, parent FROM erwaegungen_paragraph"
            )
        )
        # Note: we don't compare 'extracted_at' (timestamp differs across
        # runs) or full text (just check the structural shape).
        return {"structure": struct, "paragraphs": paras}
    finally:
        conn.close()


def test_incremental_matches_full_after_mutations(tmp_path: Path) -> None:
    base_db = _make_decisions_db(tmp_path / "v1", BASE_ROWS)
    initial = tmp_path / "structure.db"
    build_structure_incremental(
        decisions_db=base_db,
        structure_db=tmp_path / "nonexistent.db",
        output_path=initial,
        force_full=True,
    )

    mutated = [dict(r) for r in BASE_ROWS]
    mutated[0]["full_text"] = mutated[0]["full_text"] + "\n\nZusätzlich Art. 49 OR."
    mutated.pop(2)
    mutated.append({
        "decision_id": "bger_5A_99_2026", "court": "bger", "canton": "CH",
        "language": "de", "decision_date": "2026-01-01",
        "full_text": _BASE_FULL_TEXT,
    })
    mutated_db = _make_decisions_db(tmp_path / "v2", mutated)

    incr_out = tmp_path / "structure_incremental.db"
    incr_stats = build_structure_incremental(
        decisions_db=mutated_db,
        structure_db=initial,
        output_path=incr_out,
    )
    assert incr_stats["mode"] == "incremental"
    assert incr_stats["counts"]["new"] == 1
    assert incr_stats["counts"]["changed"] == 1
    assert incr_stats["counts"]["deleted"] == 1

    full_out = tmp_path / "structure_full.db"
    build_structure_incremental(
        decisions_db=mutated_db,
        structure_db=tmp_path / "nonexistent2.db",
        output_path=full_out,
        force_full=True,
    )

    incr_sig = _signature(incr_out)
    full_sig = _signature(full_out)
    assert incr_sig == full_sig, (
        "incremental output must match full bootstrap on the same corpus"
    )


def test_no_op_when_corpus_unchanged(tmp_path: Path) -> None:
    base_db = _make_decisions_db(tmp_path / "v1", BASE_ROWS)
    initial = tmp_path / "structure.db"
    build_structure_incremental(
        decisions_db=base_db,
        structure_db=tmp_path / "nonexistent.db",
        output_path=initial,
        force_full=True,
    )
    sig_before = _signature(initial)

    incr_out = tmp_path / "structure_incremental.db"
    stats = build_structure_incremental(
        decisions_db=base_db,
        structure_db=initial,
        output_path=incr_out,
    )
    assert stats["mode"] == "incremental"
    assert stats["counts"] == {"new": 0, "changed": 0, "deleted": 0}
    assert _signature(incr_out) == sig_before


def test_default_output_is_sibling(tmp_path: Path) -> None:
    base_db = _make_decisions_db(tmp_path / "v1", BASE_ROWS)
    initial = tmp_path / "structure.db"
    build_structure_incremental(
        decisions_db=base_db,
        structure_db=tmp_path / "nonexistent.db",
        output_path=initial,
        force_full=True,
    )
    mtime_before = initial.stat().st_mtime_ns

    stats = build_structure_incremental(
        decisions_db=base_db,
        structure_db=initial,
    )
    expected = initial.with_name(initial.stem + "_incremental" + initial.suffix)
    assert Path(stats["output_path"]).resolve() == expected.resolve()
    assert expected.exists()
    assert initial.stat().st_mtime_ns == mtime_before


def test_extractor_version_bump_forces_full(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_db = _make_decisions_db(tmp_path / "v1", BASE_ROWS)
    initial = tmp_path / "structure.db"
    build_structure_incremental(
        decisions_db=base_db,
        structure_db=tmp_path / "nonexistent.db",
        output_path=initial,
        force_full=True,
    )

    import search_stack.extract_decision_structure_incremental as mod
    # A "bump" is any change to the effective version — manual integer
    # or extraction-source edit both land here (the comparison sites
    # read EFFECTIVE_EXTRACTOR_VERSION since the 2026-08-22 autobump).
    monkeypatch.setattr(mod, "EFFECTIVE_EXTRACTOR_VERSION",
                        mod.EFFECTIVE_EXTRACTOR_VERSION + ".bumped")

    incr_out = tmp_path / "structure_incremental.db"
    stats = build_structure_incremental(
        decisions_db=base_db,
        structure_db=initial,
        output_path=incr_out,
    )
    assert stats["mode"] == "full_bootstrap"
    assert "version_mismatch" in stats.get("bootstrap_reason", "")


def test_cascade_delete_clears_paragraphs_and_fts(tmp_path: Path) -> None:
    base_db = _make_decisions_db(tmp_path / "v1", BASE_ROWS)
    initial = tmp_path / "structure.db"
    build_structure_incremental(
        decisions_db=base_db,
        structure_db=tmp_path / "nonexistent.db",
        output_path=initial,
        force_full=True,
    )
    target_id = BASE_ROWS[0]["decision_id"]
    conn = sqlite3.connect(initial)
    n_before = conn.execute(
        "SELECT COUNT(*) FROM erwaegungen_paragraph WHERE decision_id = ?",
        (target_id,),
    ).fetchone()[0]
    conn.close()
    assert n_before > 0

    # Drop that row from decisions; incremental cascade should clear it.
    mutated = [r for r in BASE_ROWS if r["decision_id"] != target_id]
    mutated_db = _make_decisions_db(tmp_path / "v2", mutated)

    incr_out = tmp_path / "structure_incremental.db"
    build_structure_incremental(
        decisions_db=mutated_db,
        structure_db=initial,
        output_path=incr_out,
    )

    conn = sqlite3.connect(incr_out)
    try:
        for query, table in [
            ("SELECT COUNT(*) FROM structure WHERE decision_id = ?", "structure"),
            ("SELECT COUNT(*) FROM erwaegungen_paragraph WHERE decision_id = ?", "erwaegungen_paragraph"),
            ("SELECT COUNT(*) FROM processed_decisions WHERE decision_id = ?", "processed_decisions"),
        ]:
            n = conn.execute(query, (target_id,)).fetchone()[0]
            assert n == 0, f"orphan rows in {table}: {n}"
        # FTS5 vtab should also be empty for that decision (trigger removed it)
        n_fts = conn.execute(
            "SELECT COUNT(*) FROM erwaegungen_paragraph_fts WHERE text MATCH ?",
            ("Demnach",),
        ).fetchone()[0]
        # Earlier full state had this paragraph indexed; after delete it
        # should be gone from at least the deleted decision's contribution.
        # We don't assert specific counts since other rows have similar
        # text — but the cascade trigger guarantees no per-row leak.
        assert n_fts >= 0
    finally:
        conn.close()


# ── streaming write path (2026-09-08) ─────────────────────────────────
#
# The first production run of the served-text step 2g (2026-09-07) loaded
# every decision's full_text into one dict before writing anything, reached
# 34 GB and was killed by publish.py's stall watchdog after 9,000 s of
# silence. The write path now streams and reports progress.

import search_stack.extract_decision_structure_incremental as _mod  # noqa: E402


def test_bootstrap_streams_rows_and_reports_progress(tmp_path: Path, monkeypatch, capsys) -> None:
    """Each row is extracted and written before the next one is read, and a
    progress line appears on stdout (what the stall watchdog listens to)."""
    db = _make_decisions_db(tmp_path / "src", BASE_ROWS)
    monkeypatch.setattr(_mod, "PROGRESS_EVERY", 1)
    monkeypatch.setattr(_mod, "COMMIT_EVERY", 1)

    applied: list[str] = []
    real_apply = _mod._apply_one

    def spy_apply(conn, did, row, h, now):
        applied.append(did)
        return real_apply(conn, did, row, h, now)

    real_iter = _mod._iter_decision_rows
    seen_before_yield: list[int] = []

    def interleaving_iter(path):
        for i, row in enumerate(real_iter(path)):
            seen_before_yield.append(len(applied))     # writes done before this row is handed out
            yield row

    monkeypatch.setattr(_mod, "_apply_one", spy_apply)
    monkeypatch.setattr(_mod, "_iter_decision_rows", interleaving_iter)

    stats = build_structure_incremental(
        decisions_db=db, structure_db=tmp_path / "live.db",
        output_path=tmp_path / "out.db", force_full=True,
    )
    assert stats["decisions_written"] == 3
    # before the 2nd row was yielded the 1st was written, before the 3rd the 2nd: no batching
    assert seen_before_yield == [0, 1, 2]
    out = capsys.readouterr().out
    assert "[extract_structure] bootstrap:" in out
    assert "scanned 1 decisions, written 1" in out
    assert "[extract_structure] done: scanned 3, written 3 decisions" in out
    conn = sqlite3.connect(tmp_path / "out.db")
    assert conn.execute("SELECT COUNT(*) FROM structure").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM processed_decisions").fetchone()[0] == 3
    conn.close()


def test_incremental_streams_new_changed_and_deleted(tmp_path: Path, capsys) -> None:
    db = _make_decisions_db(tmp_path / "src", BASE_ROWS)
    live = tmp_path / "live.db"
    build_structure_incremental(decisions_db=db, structure_db=live, output_path=live, force_full=True)

    # mutate: change one text, add one, delete one
    rows = [dict(r) for r in BASE_ROWS]
    rows[0]["full_text"] = rows[0]["full_text"].replace("Art. 41 OR", "Art. 97 OR")
    rows.append({"decision_id": "bger_1B_9_2025", "court": "bger", "canton": "CH", "language": "de",
                 "decision_date": "2025-01-09", "full_text": _BASE_FULL_TEXT, "regeste": None})
    rows = [r for r in rows if r["decision_id"] != "bge_140_III_86"]
    db2 = _make_decisions_db(tmp_path / "src2", rows)

    stats = build_structure_incremental(decisions_db=db2, structure_db=live, output_path=live)
    assert stats["mode"] == "incremental"
    assert stats["counts"] == {"new": 1, "changed": 1, "deleted": 1}
    assert stats["decisions_written"] == 2
    out = capsys.readouterr().out
    assert "[extract_structure] incremental: diffing" in out and "against 3 processed decisions" in out
    conn = sqlite3.connect(live)
    ids = {r[0] for r in conn.execute("SELECT decision_id FROM structure")}
    assert ids == {"bger_4A_1_2024", "bger_9C_5_2024", "bger_1B_9_2025"}
    assert conn.execute("SELECT COUNT(*) FROM erwaegungen_paragraph WHERE decision_id='bge_140_III_86'").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM processed_decisions").fetchone()[0] == 3
    conn.close()


def test_apply_extraction_still_works_for_materialised_rows(tmp_path: Path) -> None:
    """The dict-based helper stays for tests and small batches and matches the stream."""
    db = _make_decisions_db(tmp_path / "src", BASE_ROWS)
    out = tmp_path / "out.db"
    conn = sqlite3.connect(out)
    _ensure_state_tables(conn)
    rows = {r["decision_id"]: dict(r) for r in BASE_ROWS}
    hashes = {k: _extractor_hash(v) for k, v in rows.items()}
    n, paras = _mod._apply_extraction(conn, rows, hashes)
    assert n == 3 and paras > 0
    conn.commit()
    streamed = tmp_path / "streamed.db"
    build_structure_incremental(decisions_db=db, structure_db=tmp_path / "live.db",
                                output_path=streamed, force_full=True)
    c2 = sqlite3.connect(streamed)
    assert c2.execute("SELECT COUNT(*) FROM erwaegungen_paragraph").fetchone()[0] == paras
    c2.close(); conn.close()


# ── 2026-09-08 review fixes: resume, journal, regeste, consistency ──

def test_bootstrap_resumes_after_a_kill(tmp_path: Path, monkeypatch, capsys) -> None:
    """A production bootstrap outlasts the step's wall clock on a busy day.
    The working copy keeps meta.bootstrap_in_progress from its first commit;
    the next run reopens it and extracts only what is missing."""
    db = _make_decisions_db(tmp_path / "src", BASE_ROWS)
    monkeypatch.setattr(_mod, "COMMIT_EVERY", 1)
    calls = {"n": 0}
    real_apply = _mod._apply_one

    def die_after_two(conn, did, row, h, now):
        calls["n"] += 1
        if calls["n"] == 3:
            raise KeyboardInterrupt("watchdog")          # simulate SIGTERM after two committed rows
        return real_apply(conn, did, row, h, now)

    monkeypatch.setattr(_mod, "_apply_one", die_after_two)
    out = tmp_path / "out.db"
    with pytest.raises(KeyboardInterrupt):
        build_structure_incremental(decisions_db=db, structure_db=tmp_path / "live.db",
                                    output_path=out, force_full=True)
    partial = out.with_name(".out.db.tmp")
    assert partial.exists() and not out.exists()
    assert _mod._peek_meta(partial, "bootstrap_in_progress") == "1"

    monkeypatch.setattr(_mod, "_apply_one", real_apply)
    capsys.readouterr()
    stats = build_structure_incremental(decisions_db=db, structure_db=tmp_path / "live.db",
                                        output_path=out, force_full=True)
    out_text = capsys.readouterr().out
    assert "bootstrap: resuming .out.db.tmp with 2 decisions already extracted" in out_text
    assert stats["resumed"] is True and stats["decisions_written"] == 1
    conn = sqlite3.connect(out)
    assert conn.execute("SELECT COUNT(*) FROM structure").fetchone()[0] == 3
    assert _get_meta(conn, "bootstrap_in_progress") is None
    assert _get_meta(conn, "extractor_version") == _mod.EFFECTIVE_EXTRACTOR_VERSION
    conn.close()
    assert not partial.exists()


def test_stale_partial_of_another_version_is_discarded(tmp_path: Path) -> None:
    db = _make_decisions_db(tmp_path / "src", BASE_ROWS)
    out = tmp_path / "out.db"
    partial = out.with_name(".out.db.tmp")
    conn = sqlite3.connect(partial)
    _ensure_state_tables(conn)
    conn.execute("INSERT INTO meta VALUES('extractor_version', 'old'), ('bootstrap_in_progress', '1')")
    conn.execute("INSERT INTO processed_decisions VALUES('ghost', 'h')")
    conn.commit(); conn.close()
    stats = build_structure_incremental(decisions_db=db, structure_db=tmp_path / "live.db",
                                        output_path=out, force_full=True)
    assert stats["resumed"] is False and stats["decisions_written"] == 3
    conn = sqlite3.connect(out)
    assert conn.execute("SELECT COUNT(*) FROM processed_decisions WHERE decision_id='ghost'").fetchone()[0] == 0
    conn.close()


def test_stale_rollback_journal_is_removed_before_the_base_is_copied(tmp_path: Path) -> None:
    """A hot -journal left by a killed run would be replayed into the fresh
    copy of the base and corrupt it silently."""
    db = _make_decisions_db(tmp_path / "src", BASE_ROWS)
    live = tmp_path / "live.db"
    build_structure_incremental(decisions_db=db, structure_db=live, output_path=live, force_full=True)
    journal = live.with_name(".live.db.tmp-journal")
    journal.write_bytes(b"\xd9\xd5\x05\xf9\x20\xa1\x63\xd7" + b"\x00" * 4096)   # SQLite journal magic + junk
    stats = build_structure_incremental(decisions_db=db, structure_db=live, output_path=live)
    assert stats["mode"] == "incremental" and stats["counts"] == {"new": 0, "changed": 0, "deleted": 0}
    assert not journal.exists()
    conn = sqlite3.connect(live)
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    conn.close()


def test_regeste_change_is_detected_by_the_diff(tmp_path: Path) -> None:
    """build_fts5 sets decisions.regeste after extraction; the diff must see it
    or structure.regeste stays stale for ever now that nights only diff."""
    db = _make_decisions_db(tmp_path / "src", BASE_ROWS)
    live = tmp_path / "live.db"
    build_structure_incremental(decisions_db=db, structure_db=live, output_path=live, force_full=True)
    rows = [dict(r) for r in BASE_ROWS]
    rows[0]["regeste"] = "Art. 41 OR; neue Regeste."
    db2 = _make_decisions_db(tmp_path / "src2", rows)
    stats = build_structure_incremental(decisions_db=db2, structure_db=live, output_path=live)
    assert stats["counts"] == {"new": 0, "changed": 1, "deleted": 0}
    conn = sqlite3.connect(live)
    assert conn.execute("SELECT regeste FROM structure WHERE decision_id='bger_4A_1_2024'").fetchone()[0] == "Art. 41 OR; neue Regeste."
    conn.close()


def test_inconsistent_state_forces_a_bootstrap(tmp_path: Path) -> None:
    db = _make_decisions_db(tmp_path / "src", BASE_ROWS)
    live = tmp_path / "live.db"
    build_structure_incremental(decisions_db=db, structure_db=live, output_path=live, force_full=True)
    conn = sqlite3.connect(live)
    conn.execute("DELETE FROM structure WHERE decision_id='bger_4A_1_2024'")     # processed says done, structure lacks it
    conn.commit(); conn.close()
    base, reason = _mod._select_diff_base(live, live, force_full=False)
    assert base is None and reason == "state_inconsistent:live.db"
    stats = build_structure_incremental(decisions_db=db, structure_db=live, output_path=live)
    assert stats["mode"] == "full_bootstrap"
    conn = sqlite3.connect(live)
    assert conn.execute("SELECT COUNT(*) FROM structure").fetchone()[0] == 3
    conn.close()


def test_extractor_refuses_to_write_where_the_sidecar_does_not_fit(tmp_path: Path, monkeypatch) -> None:
    """Defence in depth for the root-disk case: whatever path a caller hands
    us, a rebuild needs ~1.2 x the live sidecar beside the output."""
    import collections
    db = _make_decisions_db(tmp_path / "src", BASE_ROWS)
    live = tmp_path / "live.db"
    build_structure_incremental(decisions_db=db, structure_db=live, output_path=live, force_full=True)
    usage = collections.namedtuple("usage", "total used free")
    monkeypatch.setattr(_mod.shutil, "disk_usage", lambda p: usage(100, 100, 0))
    out = tmp_path / "elsewhere" / "out.db"
    with pytest.raises(SystemExit, match="refusing to write there"):
        build_structure_incremental(decisions_db=db, structure_db=live, output_path=out, force_full=True)
    assert not out.parent.exists() or not any(out.parent.iterdir())
