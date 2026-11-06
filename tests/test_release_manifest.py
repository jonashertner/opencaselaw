"""scripts/generate_release_manifest.py must not scan the decisions table.

It runs inside the nightly build with a 120 s cap while the same database
serves searches. Any column after full_text sits behind each row's overflow
chain, so a query that has to read the table rows instead of an index reads
about 70 GB on production. On 2026-11-06 two content_hash counts did that
and the step timed out.
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "generate_release_manifest.py"

_spec = importlib.util.spec_from_file_location("generate_release_manifest", SCRIPT)
grm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(grm)

# The production column order matters: content_hash after full_text.
_SCHEMA = """
CREATE TABLE decisions (
    decision_id TEXT PRIMARY KEY,
    court TEXT NOT NULL,
    canton TEXT NOT NULL,
    docket_number TEXT NOT NULL,
    decision_date TEXT,
    publication_date TEXT,
    language TEXT NOT NULL,
    regeste TEXT,
    full_text TEXT,
    source_url TEXT,
    content_hash TEXT,
    canonical_key TEXT
);
CREATE INDEX idx_decisions_court ON decisions(court);
CREATE INDEX idx_decisions_canton ON decisions(canton);
CREATE INDEX idx_decisions_date ON decisions(decision_date);
CREATE INDEX idx_decisions_language ON decisions(language);
CREATE INDEX idx_decisions_docket ON decisions(docket_number);
CREATE INDEX idx_decisions_canonical ON decisions(canonical_key);
CREATE INDEX idx_decisions_court_pub ON decisions(court, publication_date);
"""

_ROWS = [
    ("bge_1", "bge", "CH", "1", "2001-02-03", None, "de", "r", "x" * 5000, None, "h1", "k1"),
    ("bge_2", "bge", "CH", "2", "1899-01-01", None, "fr", None, "y", None, None, "k2"),
    ("zh_1", "zh_gerichte", "ZH", "3", "2025-10-10", None, "de", None, "z", None, "h3", "k3"),
    ("old_1", "zh_gerichte", "ZH", "4", "1650-01-01", None, "it", None, "w", None, "h4", "k4"),
]


def _make_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA)
    conn.executemany(
        "INSERT INTO decisions VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", _ROWS
    )
    conn.commit()
    conn.close()


def test_no_summary_query_reads_table_rows(tmp_path):
    db = tmp_path / "decisions.db"
    _make_db(db)
    conn = sqlite3.connect(db)
    for name, sql in grm.SUMMARY_QUERIES.items():
        plan = " | ".join(
            row[3] for row in conn.execute("EXPLAIN QUERY PLAN " + sql)
        )
        assert "USING COVERING INDEX" in plan, (name, plan)
        assert "SCAN decisions" not in plan.replace(
            "SCAN decisions USING COVERING INDEX", ""
        ), (name, plan)


def test_no_query_names_a_late_column():
    for sql in grm.SUMMARY_QUERIES.values():
        for col in ("full_text", "source_url", "content_hash", "json_data"):
            assert col not in sql


def test_summary_values(tmp_path):
    db = tmp_path / "decisions.db"
    _make_db(db)
    conn = sqlite3.connect(f"file:{db}?mode=ro&immutable=1", uri=True)
    out = grm._summarise(conn)
    assert out["total_decisions"] == 4
    assert out["distinct_courts"] == 2
    assert out["distinct_cantons"] == 2
    assert out["per_language"] == {"de": 2, "fr": 1, "it": 1}
    assert out["per_court"] == {"bge": 2, "zh_gerichte": 2}
    assert out["date_range"] == ["1899-01-01", "2025-10-10"]
    # No count is claimed for content hashes; the manifest says where it is.
    ch = out["content_hashes"]
    assert "populated" not in ch and "missing" not in ch
    assert "skipped_no_content_hash" in ch["where"]


def test_cli_writes_manifest(tmp_path):
    db = tmp_path / "decisions.db"
    _make_db(db)
    quality = tmp_path / "quality.json"
    quality.write_text(json.dumps({
        "run_at": "2026-11-06T12:17:59+00:00",
        "duration_seconds": 1266.0,
        "summary": {"publish_safe": True, "total": 63, "passed": 57,
                    "critical_failures": 0, "warning_failures": 6},
    }))
    out_root = tmp_path / "releases"
    res = subprocess.run(
        [sys.executable, str(SCRIPT), "--db", str(db), "--repo", str(tmp_path),
         "--quality-json", str(quality), "--out-root", str(out_root),
         "--date", "2026-11-06"],
        capture_output=True, text=True, timeout=60,
    )
    assert res.returncode == 0, res.stderr
    m = json.loads((out_root / "2026-11-06" / "manifest.json").read_text())
    assert m["corpus"]["total_decisions"] == 4
    assert m["quality_gate"]["critical_failures"] == 0
    assert m["corpus"]["content_hashes"]["counted_by"].startswith("integrity root")
