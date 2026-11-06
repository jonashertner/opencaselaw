#!/usr/bin/env python3
"""Append-only release manifest for each successful nightly publish.

Writes ``releases/<YYYY-MM-DD>/manifest.json`` summarising what's in
the corpus on this date — counts per court, where the content-hash
count is published, gate state, git commit, schema version. Lets anyone reconstruct exactly what
was live on a given date for citation / reproducibility / forensic use.

The manifest is small (a few KB) and committed to the repo by
publish.py Step 6, so the GitHub history is the immutable audit trail.

Optional Zenodo deposition: if ``ZENODO_TOKEN`` is set in the env, the
manifest is also POSTed to Zenodo for a permanent DOI. Defer this until
v2 — for now the GitHub history + Hugging Face dataset card carry
provenance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB = Path(
    os.environ.get(
        "OCL_DECISIONS_DB",
        "/opt/caselaw/repo/output/decisions.db",
    )
)
DEFAULT_OUT_ROOT = Path(
    os.environ.get(
        "OCL_RELEASE_DIR",
        "/opt/caselaw/repo/releases",
    )
)


def _git_commit(repo: Path) -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(repo), text=True, timeout=10,
        ).strip()
    except Exception:
        return "unknown"


# Every query here must be answerable from an index. publish.py gives this
# step 120 s, and it runs during the build while the same decisions.db is
# serving searches. A query that touches a column stored after `full_text`
# (ordinal 22) follows every row's overflow chain, about 70 GB on production
# (see quality/checks/_late_columns.py). The two content_hash counts this
# step used to run did exactly that: 2-20 s while the quality gate had just
# left the table in the page cache, a 120 s timeout on 2026-11-06 when it
# had not. tests/test_release_manifest.py checks the plans.
SUMMARY_QUERIES = {
    "total_decisions": "SELECT COUNT(*) FROM decisions",
    "distinct_courts": "SELECT COUNT(DISTINCT court) FROM decisions",
    "distinct_cantons": (
        "SELECT COUNT(DISTINCT canton) FROM decisions WHERE canton IS NOT NULL"
    ),
    "per_language": "SELECT language, COUNT(*) FROM decisions GROUP BY language",
    "per_court": (
        "SELECT court, COUNT(*) FROM decisions GROUP BY court ORDER BY 2 DESC"
    ),
    "date_range": (
        "SELECT MIN(decision_date), MAX(decision_date) FROM decisions "
        "WHERE decision_date >= '1700-01-01'"
    ),
}

# Content-hash coverage is not counted here. build_fts5 hashes every row at
# the end of the build (_compute_content_hashes), and Step 5f, which reads
# every content_hash anyway to build the Merkle root, publishes the count of
# rows it had to skip for want of one.
CONTENT_HASHES_NOTE = {
    "counted_by": "integrity root (publish Step 5f)",
    "where": "docs/integrity/latest.json of the same build: "
             "decisions_count, skipped_no_content_hash",
}


def _summarise(conn: sqlite3.Connection) -> dict:
    q = SUMMARY_QUERIES
    out: dict = {}
    out["total_decisions"] = conn.execute(q["total_decisions"]).fetchone()[0]
    out["distinct_courts"] = conn.execute(q["distinct_courts"]).fetchone()[0]
    out["distinct_cantons"] = conn.execute(q["distinct_cantons"]).fetchone()[0]
    out["per_language"] = dict(conn.execute(q["per_language"]).fetchall())
    out["per_court"] = dict(conn.execute(q["per_court"]).fetchall())
    out["content_hashes"] = dict(CONTENT_HASHES_NOTE)
    out["date_range"] = list(conn.execute(q["date_range"]).fetchone())
    return out


def _read_quality_summary(quality_json: Path) -> dict:
    if not quality_json.exists():
        return {"status": "quality.json missing"}
    try:
        d = json.loads(quality_json.read_text(encoding="utf-8"))
    except Exception as e:
        return {"status": f"unreadable: {e}"}
    s = d.get("summary", {})
    return {
        "publish_safe": s.get("publish_safe"),
        "total_checks": s.get("total"),
        "passed": s.get("passed"),
        "critical_failures": s.get("critical_failures"),
        "warning_failures": s.get("warning_failures"),
        "run_at": d.get("run_at"),
        "duration_seconds": d.get("duration_seconds"),
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", type=Path, default=DEFAULT_DB)
    p.add_argument("--repo", type=Path,
                   default=Path("/opt/caselaw/repo"))
    p.add_argument("--quality-json", type=Path,
                   default=Path("/opt/caselaw/repo/docs/quality.json"))
    p.add_argument("--out-root", type=Path, default=DEFAULT_OUT_ROOT)
    p.add_argument("--date",
                   help="Override release date (YYYY-MM-DD; default = today UTC)")
    args = p.parse_args()

    if not args.db.exists():
        print(f"decisions.db not found at {args.db}", file=sys.stderr)
        return 1

    today = args.date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_dir = args.out_root / today
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "manifest.json"

    conn = sqlite3.connect(
        f"file:{args.db}?mode=ro&immutable=1", uri=True,
    )

    summary = _summarise(conn)
    quality = _read_quality_summary(args.quality_json)
    db_size = args.db.stat().st_size
    db_size_h = sha256_first_mb(args.db)

    manifest = {
        "release_date": today,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_commit": _git_commit(args.repo),
        "decisions_db": {
            "path": str(args.db),
            "size_bytes": db_size,
            "size_gb": round(db_size / 1e9, 2),
            "sha256_first_mb": db_size_h,
        },
        "corpus": summary,
        "quality_gate": quality,
        "schema_version": 1,
        "license": {
            "corpus": "CC-BY-4.0",
            "code": "MIT",
            "benchmarks": "CC0",
        },
    }

    out_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"Wrote {out_path} ({out_path.stat().st_size} bytes)")
    return 0


def sha256_first_mb(path: Path) -> str:
    """Cheap fingerprint of the corpus DB (first 1 MB only). The first
    MB of a SQLite file contains the schema + some early data; changes
    in that prefix detect schema migrations and large-scale rebuilds.
    Cheaper than a full-file hash on a 67 GB DB."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read(1024 * 1024))
    return h.hexdigest()


if __name__ == "__main__":
    sys.exit(main())
