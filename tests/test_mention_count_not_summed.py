"""Graph counts are row counts, never sums of mention_count.

Until 2026-10 the extractor deduplicated references per decision before the
upsert, so mention_count was 1 on every row and SUM(mention_count) equalled
COUNT(*). Stats, find_leading_cases, the search graph signals and the
citation-resolution benchmark all relied on that. Once the extractor records
real mention counts, any remaining SUM would silently turn "decisions citing
X" into "times X is mentioned". mention_count may still be read per row
(ordering, display); it must not be aggregated as a count.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", ".claude", "output", "state",
             "tests", "web_ui", "artifacts"}
SUM_OF_MENTIONS = re.compile(r"\b(SUM|TOTAL)\s*\([^)]*\bmention_count\b", re.IGNORECASE)


def _sources():
    for path in REPO.rglob("*.py"):
        rel = path.relative_to(REPO)
        if SKIP_DIRS.intersection(rel.parts[:-1]) or rel.name.startswith("test_"):
            continue
        yield rel, path


def test_no_sql_sums_mention_count():
    hits = []
    for rel, path in _sources():
        text = path.read_text(encoding="utf-8", errors="replace")
        for m in SUM_OF_MENTIONS.finditer(text):
            hits.append(f"{rel}:{text.count(chr(10), 0, m.start()) + 1}: {m.group(0)}")
    assert not hits, "count rows with COUNT(*) instead:\n" + "\n".join(hits)


def test_guard_catches_the_old_forms():
    for sql in ("SUM(mention_count)", "SUM(ds.mention_count) AS n",
                "sum( dc.mention_count * COALESCE(ct.confidence_score, 1.0))",
                "SUM(\n    ds.mention_count)"):
        assert SUM_OF_MENTIONS.search(sql), sql
    assert not SUM_OF_MENTIONS.search("ORDER BY ds.mention_count DESC")
    assert not SUM_OF_MENTIONS.search("COUNT(*) AS n")
