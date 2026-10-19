"""search_stack/materialien_swap.py: writers of materialien.db work on a copy
that is checked and swapped in only when the live file did not change."""
from __future__ import annotations

import os
import sqlite3
import sys
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from search_stack import materialien_swap as ms  # noqa: E402


@pytest.fixture
def live(tmp_path):
    vol = tmp_path / "volume"
    vol.mkdir()
    real = vol / "materialien.db"
    c = sqlite3.connect(real)
    c.execute("CREATE TABLE botschaft_documents (botschaft_id INTEGER PRIMARY KEY, t TEXT)")
    c.execute("CREATE TABLE botschaft_paragraphs (paragraph_id INTEGER PRIMARY KEY, t TEXT)")
    c.executemany("INSERT INTO botschaft_documents (t) VALUES (?)", [("a",), ("b",)])
    c.commit()
    c.close()
    link = tmp_path / "materialien.db"
    link.symlink_to(real)
    return link


def _script(tmp_path, body: str) -> list[str]:
    f = tmp_path / "writer.py"
    f.write_text(textwrap.dedent(body))
    return [sys.executable, str(f), "{db}"]


def _rows(path):
    c = sqlite3.connect(path)
    try:
        return c.execute("SELECT COUNT(*) FROM botschaft_documents").fetchone()[0]
    finally:
        c.close()


def test_successful_write_is_swapped_in_through_the_symlink(live, tmp_path):
    ino = os.stat(live).st_ino
    cmd = _script(tmp_path, """
        import sqlite3, sys
        c = sqlite3.connect(sys.argv[1]); c.execute("INSERT INTO botschaft_documents (t) VALUES ('c')")
        c.commit()
    """)
    assert ms.swap_run(live, cmd) == 0
    assert live.is_symlink() and _rows(live) == 3
    assert os.stat(live).st_ino != ino  # a new inode: readers switch on their next connection
    assert not list(live.resolve().parent.glob("*.swapwork*"))


def test_failed_command_leaves_live_untouched(live, tmp_path):
    ino = os.stat(live).st_ino
    cmd = _script(tmp_path, "import sys; sys.exit(7)")
    assert ms.swap_run(live, cmd) == 7
    assert os.stat(live).st_ino == ino and _rows(live) == 2


def test_shrinking_copy_is_refused(live, tmp_path):
    cmd = _script(tmp_path, """
        import sqlite3, sys
        c = sqlite3.connect(sys.argv[1]); c.execute("DELETE FROM botschaft_documents"); c.commit()
    """)
    assert ms.swap_run(live, cmd) == ms.EXIT_CHECK_FAILED
    assert _rows(live) == 2
    assert ms.swap_run(live, cmd, allow_shrink=True) == 0
    assert _rows(live) == 0


def test_live_change_during_run_discards_the_copy(live, tmp_path):
    real = live.resolve()
    cmd = _script(tmp_path, f"""
        import sqlite3, sys
        c = sqlite3.connect(sys.argv[1]); c.execute("INSERT INTO botschaft_documents (t) VALUES ('c')"); c.commit()
        o = sqlite3.connect({str(real)!r}); o.execute("INSERT INTO botschaft_documents (t) VALUES ('other')"); o.commit()
    """)
    assert ms.swap_run(live, cmd) == ms.EXIT_LIVE_CHANGED
    c = sqlite3.connect(real)
    assert [r[0] for r in c.execute("SELECT t FROM botschaft_documents")] == ["a", "b", "other"]
    c.close()


def test_deadline_stops_the_writer_and_keeps_committed_work(live, tmp_path):
    cmd = _script(tmp_path, """
        import sqlite3, sys, time
        c = sqlite3.connect(sys.argv[1])
        c.execute("INSERT INTO botschaft_documents (t) VALUES ('done')"); c.commit()
        c.execute("BEGIN"); c.execute("INSERT INTO botschaft_documents (t) VALUES ('half')")
        time.sleep(30)
    """)
    rc = ms.main(["--db", str(live), "--deadline-seconds", "2", "--"] + cmd)
    assert rc < 0  # terminated by the deadline, still swapped
    c = sqlite3.connect(live)
    assert [r[0] for r in c.execute("SELECT t FROM botschaft_documents")] == ["a", "b", "done"]
    c.close()


def test_second_run_is_refused_while_locked(live, tmp_path):
    import fcntl
    lockf = tmp_path / "x.lock"
    held = open(lockf, "w")
    fcntl.flock(held, fcntl.LOCK_EX)
    try:
        assert ms.swap_run(live, _script(tmp_path, "pass"), lock_file=lockf) == ms.EXIT_LOCKED
    finally:
        held.close()


def test_cli_requires_the_db_placeholder(live):
    assert ms.main(["--db", str(live), "--", "true"]) == 2


def test_deadline_stops_a_shell_chain_as_a_whole(live, tmp_path):
    marker = tmp_path / "second_ran"
    py = _script(tmp_path, """
        import sqlite3, sys, time
        c = sqlite3.connect(sys.argv[1])
        c.execute("INSERT INTO botschaft_documents (t) VALUES ('first')"); c.commit()
        time.sleep(30)
    """)
    chain = f"{py[0]} {py[1]} {{db}} && touch {marker}"
    rc = ms.main(["--db", str(live), "--deadline-seconds", "2", "--", "/bin/bash", "-c", chain])
    assert rc < 0 and not marker.exists()
    c = sqlite3.connect(live)
    assert [r[0] for r in c.execute("SELECT t FROM botschaft_documents")] == ["a", "b", "first"]
    c.close()
