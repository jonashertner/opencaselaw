"""A stale rollback journal at the live decisions.db path must not survive
the swap.

build_fts5 removes stale -wal/-shm at the destination after os.replace
(the 2026-05-05 incident: a May-2 WAL replayed into a fresh DB on the
first non-immutable open). A hot -journal is the rollback-mode twin: SQLite
replays its page images into whatever file is at the path, so a journal
left by a killed writer would corrupt the freshly swapped DB the first time
generate_stats or quick_publish opens it read-write. Same bug, found in the
graph builder on 2026-10-11 ("database disk image is malformed").
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import build_fts5  # noqa: E402


def _row(i: int) -> dict:
    return {
        "decision_id": f"bger_1C_{i}_2025",
        "court": "bger",
        "canton": "CH",
        "docket_number": f"1C_{i}/2025",
        "decision_date": "2025-03-0%d" % (i + 1),
        "language": "de",
        "title": f"Entscheid {i}",
        "full_text": f"Sachverhalt {i}. " + "Erwägungen zum Baurecht. " * 40,
        "source_url": f"https://example.org/{i}",
    }


def _plant_hot_journal(db: Path) -> Path:
    """Build an unrelated DB at ``db``, then die mid-transaction writing to
    it, so a hot journal of foreign page images sits at ``db-journal``."""
    code = textwrap.dedent(f"""
        import os, sqlite3
        c = sqlite3.connect({str(db)!r})
        c.execute("CREATE TABLE junk(id INTEGER PRIMARY KEY, blob TEXT)")
        c.executemany("INSERT INTO junk VALUES (?, ?)",
                      ((i, "j" * 300) for i in range(3000)))
        c.commit()
        c.execute("PRAGMA cache_size=5")
        c.execute("UPDATE junk SET blob = blob || 'k'")
        os._exit(9)
    """)
    subprocess.run([sys.executable, "-c", code], check=False)
    journal = Path(str(db) + "-journal")
    assert journal.exists() and journal.stat().st_size > 0
    return journal


def test_the_swap_removes_a_stale_journal_at_the_live_path(tmp_path: Path):
    out = tmp_path / "output"
    (out / "decisions").mkdir(parents=True)
    with open(out / "decisions" / "bger.jsonl", "w") as f:
        for i in range(2):
            f.write(json.dumps(_row(i)) + "\n")

    live = out / "decisions.db"
    journal = _plant_hot_journal(live)
    live.unlink()  # the live file is gone; only its journal is left

    build_fts5.build_database(out, full_rebuild=True, no_optimize=True)

    assert not journal.exists()
    conn = sqlite3.connect(live)  # read-write, as generate_stats opens it
    try:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 2
    finally:
        conn.close()
