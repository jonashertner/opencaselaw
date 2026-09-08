"""A change to the extraction code must bootstrap the shadows by itself.

Twice, an extraction improvement shipped without the manual
``EXTRACTOR_VERSION`` bump, and the incremental twins silently kept
extending a stale base:

* c5edb71c (ATF/DTF prefixes) — graph shadow drifted to −13.2 %
  citation_targets over ten weeks, 12/12 drift-check nights red.
* 9cf68db5 (TI structure fix) — 3,995 decisions' paragraph coverage LOST,
  even though the file's own comments narrate the identical 2026-07-03
  incident.

The stored version is now derived from the extraction source bytes
(search_stack/extractor_version.py), so the third occurrence is
structurally impossible: change the code, the version no longer matches,
the next run reseeds. These tests pin that contract offline.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from search_stack.extractor_version import effective_version  # noqa: E402


def test_deterministic(tmp_path):
    f = tmp_path / "a.py"
    f.write_text("x = 1\n")
    assert effective_version(1, f) == effective_version(1, f)


def test_source_change_changes_the_version(tmp_path):
    """The property that would have prevented both incidents."""
    f = tmp_path / "reference_extraction.py"
    f.write_text("PATTERNS = ['BGE']\n")
    before = effective_version(1, f)
    f.write_text("PATTERNS = ['BGE', 'ATF', 'DTF']\n")   # c5edb71c, in effigy
    after = effective_version(1, f)
    assert before != after


def test_manual_bump_still_works(tmp_path):
    f = tmp_path / "a.py"
    f.write_text("x = 1\n")
    assert effective_version(1, f) != effective_version(2, f)


def test_argument_order_is_irrelevant(tmp_path):
    a, b = tmp_path / "a.py", tmp_path / "b.py"
    a.write_text("a\n"); b.write_text("b\n")
    assert effective_version(1, a, b) == effective_version(1, b, a)


def test_missing_source_fails_loudly(tmp_path):
    with pytest.raises(FileNotFoundError):
        effective_version(1, tmp_path / "gone.py")
    with pytest.raises(ValueError):
        effective_version(1)


def test_builders_carry_a_source_derived_version():
    """Both incremental builders must expose and use the derived form —
    a bare integer version means the regression has been reintroduced."""
    import search_stack.build_reference_graph_incremental as g
    import search_stack.extract_decision_structure_incremental as st
    for mod, manual in ((g, "1"), (st, "3")):     # structure v3: regeste joined the hash (2026-09-08)
        v = mod.EFFECTIVE_EXTRACTOR_VERSION
        assert v.startswith(f"{manual}+src.") and len(v.split("src.")[1]) == 12


def test_stored_plain_integer_mismatches_and_forces_bootstrap(tmp_path):
    """The production shadows store '1' / '2'. Under the derived scheme they
    must mismatch — that single mismatch IS the reseed the 12-red-nights
    drift needs."""
    import sqlite3
    import search_stack.build_reference_graph_incremental as g
    db = tmp_path / "shadow.db"
    conn = sqlite3.connect(db)
    conn.executescript(g.STATE_SCHEMA_SQL)
    conn.execute("INSERT INTO meta(key, value) VALUES('extractor_version', '1')")
    conn.commit(); conn.close()
    base, reason = g._select_diff_base(tmp_path / "live.db", db, force_full=False)
    assert base is None
    assert reason and reason.startswith("version_mismatch")
