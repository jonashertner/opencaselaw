#!/usr/bin/env python3
"""Measure what the structure.parquet metadata query reads, offline.

Why: since step 2g builds decision_structure.db from served text, the
``structure`` rows carry the full Sachverhalt / Erwägungen / Dispositiv, and
the nightly metadata export (``export_parquet._STRUCTURE_META_COLS``) projects
over its budget and keeps the last good structure.parquet — the 2026-09-10
shard-era file was re-uploaded unchanged for four weeks. This script shows
*why* from first principles and compares the proposed storage layouts
(docs/proposals/structure-export-small-columns.md).

SQLite stores a row as one record — header, then the column bodies in
declaration order — and spills whatever does not fit on the leaf page into a
linked chain of overflow pages. Reading a column that sits behind a large
text column means walking that chain page by page. In ``structure`` the
columns the export needs (the three ``*_method`` columns,
``erwaegungen_paragraph_count``) all sit behind the text, and the has-section
flags are computed from the text itself, so the export reads ~every page of
the table.

Two modes, both offline:

  synthetic (default): builds sidecars of ``--rows`` decisions in a temp dir
    with the production ``SCHEMA`` (and the proposed variants of it) and
    measures, per layout, the bytes the export query reads (Linux
    /proc/self/io; None elsewhere), the wall time on a warm OS cache, the
    query plan, and the export's own probe projection. Nothing outside the
    temp dir is touched.

  --structure-db PATH: read-only (``mode=ro&immutable=1``) look at a real
    sidecar — column order, whether the proposed columns/index exist, the
    query plans and the export probe's projection (the same 16x50-row probe
    the export runs). It never scans the table.

    python3 scripts/measure_structure_export_cost.py [--rows 5000] [--json]
    python3 scripts/measure_structure_export_cost.py --structure-db output/decision_structure.db
"""
from __future__ import annotations

import argparse
import json
import math
import random
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import export_parquet as ep  # noqa: E402
from search_stack.extract_decision_structure import SCHEMA  # noqa: E402

# Order of magnitude of the production sidecar (step 2g docstrings: "~1.07M
# decisions"); only used to scale per-row figures, never as a measurement.
PRODUCTION_ROWS = 1_070_000

# --- the proposal, as DDL + query (docs/proposals/structure-export-small-columns.md)
_FLAG_COLUMNS = (
    "    has_sachverhalt      INTEGER NOT NULL",
    "    has_erwaegungen      INTEGER NOT NULL",
    "    has_dispositiv       INTEGER NOT NULL",
)
PROPOSED_COLUMNS = ",\n".join(_FLAG_COLUMNS) + "\n"
PROPOSED_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_structure_export ON structure("
    "decision_id, court, language, has_sachverhalt, has_erwaegungen, has_dispositiv, "
    "sachverhalt_method, erwaegungen_method, dispositiv_method, "
    "erwaegungen_paragraph_count);"
)
PROPOSED_META_COLS = (
    "decision_id, court, language, has_sachverhalt, has_erwaegungen, has_dispositiv, "
    "sachverhalt_method, erwaegungen_method, dispositiv_method, "
    "CAST(erwaegungen_paragraph_count AS INTEGER)"
)
# The alternative the brief also names: no new columns, an index on the
# export's own expressions. Works on SQLite >= 3.41-ish (indexed-expression
# substitution) but is invisible in the plan ("USING INDEX", not "COVERING")
# and silently breaks if the export's expression text drifts from the DDL.
EXPRESSION_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_structure_export_expr ON structure("
    "decision_id, court, language, "
    "(sachverhalt IS NOT NULL AND sachverhalt != ''), "
    "(erwaegungen IS NOT NULL AND erwaegungen != ''), "
    "(dispositiv IS NOT NULL AND dispositiv != ''), "
    "sachverhalt_method, erwaegungen_method, dispositiv_method, "
    "erwaegungen_paragraph_count);"
)

_LAST_COLUMN = "    extracted_at         TEXT\n);"
assert _LAST_COLUMN in SCHEMA, "structure DDL changed: update this script"
APPENDED_SCHEMA = SCHEMA.replace(_LAST_COLUMN, "    extracted_at         TEXT,\n" + PROPOSED_COLUMNS + ");")


def _reordered_schema() -> str:
    """Small columns first (the in-row alternative to an index)."""
    small = ("    sachverhalt_method   TEXT,\n", "    erwaegungen_method   TEXT,\n",
             "    erwaegungen_paragraph_count INTEGER,\n", "    dispositiv_method    TEXT,\n")
    out = SCHEMA
    for line in small:
        assert line in out, f"structure DDL changed: {line.strip()}"
        out = out.replace(line, "")
    anchor = "    regeste              TEXT,"
    return out.replace(anchor, "".join(small) + ",\n".join(_FLAG_COLUMNS) + ",\n" + anchor)


# --- synthetic corpus --------------------------------------------------------

_WORDS = ("Die Beschwerdeführerin rügt eine Verletzung von Art. 9 BV und Art. 41 OR; "
          "die Vorinstanz habe den Sachverhalt offensichtlich unrichtig festgestellt. ")


def _text(n: int) -> str:
    return (_WORDS * (n // len(_WORDS) + 1))[:max(0, n)]


def _size(rng: random.Random, mean: int) -> int:
    # right-skewed like real decisions (a few very long Erwägungen)
    return int(rng.lognormvariate(math.log(mean) - 0.32, 0.8))


def synthetic_rows(n: int, seed: int = 20261007):
    """~76 % of rows with sections (step 2g: '76% of the corpus with an
    indexed Erwägung'); sizes are illustrative, the comparison between
    layouts does not depend on them."""
    rng = random.Random(seed)
    for i in range(n):
        structured = rng.random() < 0.76
        regeste = _text(_size(rng, 800)) if rng.random() < 0.15 else None
        sv = _text(_size(rng, 5000)) if structured and rng.random() < 0.9 else None
        erw = _text(_size(rng, 17000)) if structured else None
        disp = _text(_size(rng, 1500)) if structured and rng.random() < 0.95 else None
        yield {
            "decision_id": f"bger_{i:07d}", "court": "bger", "canton": "CH",
            "language": "de", "decision_date": "2024-01-01", "regeste": regeste,
            "sachverhalt": sv, "sachverhalt_method": "ranked_de_header" if sv else None,
            "erwaegungen": erw, "erwaegungen_method": "ranked_de_header" if erw else None,
            "erwaegungen_paragraph_count": rng.randint(2, 30) if erw else 0,
            "dispositiv": disp, "dispositiv_method": "ranked_de_BGer" if disp else None,
            "dispositiv_orders": "[]" if disp else None,
            "extracted_at": "2026-10-07T00:00:00Z",
            "has_sachverhalt": int(bool(sv)), "has_erwaegungen": int(bool(erw)),
            "has_dispositiv": int(bool(disp)),
        }


def build_sidecar(path: Path, schema: str, rows: int, extra_ddl: str = "") -> None:
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    conn.executescript(schema + extra_ddl)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(structure)")]
    sql = (f"INSERT INTO structure ({', '.join(cols)}) "
           f"VALUES ({', '.join(':' + c for c in cols)})")
    batch = []
    for row in synthetic_rows(rows):
        batch.append(row)
        if len(batch) >= 2000:
            conn.executemany(sql, batch)
            batch.clear()
    if batch:
        conn.executemany(sql, batch)
    conn.commit()
    conn.close()


# --- measurement ---------------------------------------------------------------

def _rchar() -> int | None:
    try:
        with open("/proc/self/io") as f:
            for line in f:
                if line.startswith("rchar:"):
                    return int(line.split()[1])
    except OSError:
        return None
    return None


def _ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)


def query_plan(conn: sqlite3.Connection, sql: str) -> str:
    return " | ".join(str(r[-1]) for r in conn.execute("EXPLAIN QUERY PLAN " + sql))


def page_breakdown(path: Path) -> dict | None:
    """Pages per b-tree and page type via dbstat (reads the whole file:
    synthetic sidecars only). None when SQLite lacks DBSTAT."""
    conn = _ro(path)
    try:
        rows = conn.execute(
            "SELECT name, pagetype, count(*) FROM dbstat "
            "WHERE name IN ('structure', 'idx_structure_export', 'idx_structure_export_expr') "
            "GROUP BY name, pagetype").fetchall()
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()
    out: dict = {}
    for name, ptype, n in rows:
        out.setdefault(name, {})[ptype] = n
    return out


def measure(path: Path, select_cols: str, label: str, indexed_by: str | None = None,
            scale_rows: int = PRODUCTION_ROWS) -> dict:
    """One full export-shaped read on a fresh read-only connection (cold
    SQLite cache, warm OS cache) plus the export's own probe projection."""
    conn = _ro(path)
    try:
        page_size = conn.execute("PRAGMA page_size").fetchone()[0]
        table = "structure" + (f" INDEXED BY {indexed_by}" if indexed_by else "")
        sql = f"SELECT {select_cols} FROM {table}"
        plan = query_plan(conn, sql)
        r0, t0 = _rchar(), time.perf_counter()
        n = 0
        for _ in conn.execute(sql):
            n += 1
        elapsed = time.perf_counter() - t0
        r1 = _rchar()
        projected = ep._projected_seconds(conn, "structure", select_cols)
    finally:
        conn.close()
    read = (r1 - r0) if (r0 is not None and r1 is not None) else None
    return {
        "layout": label,
        "rows": n,
        "plan": plan,
        "bytes_read": read,
        "pages_read": (read // page_size) if read is not None else None,
        "bytes_read_per_row": round(read / n, 1) if (read is not None and n) else None,
        "projected_bytes_at_production_rows": (int(read / n * scale_rows)
                                               if (read is not None and n) else None),
        "elapsed_s": round(elapsed, 4),
        # the export probe ignores INDEXED BY: it reads rowid windows of the
        # TABLE, so on the indexed layout it still prices the overflow walk
        "export_probe_projection_s": round(projected, 3),
        "file_bytes": path.stat().st_size,
    }


def run_synthetic(rows: int, workdir: Path, scale_rows: int) -> dict:
    workdir.mkdir(parents=True, exist_ok=True)
    current = workdir / "current.db"
    appended = workdir / "appended_no_index.db"
    proposed = workdir / "proposed.db"
    reordered = workdir / "reordered.db"
    expr = workdir / "current_expr_index.db"
    build_sidecar(current, SCHEMA, rows)
    build_sidecar(appended, APPENDED_SCHEMA, rows)
    build_sidecar(proposed, APPENDED_SCHEMA, rows, PROPOSED_INDEX)
    build_sidecar(reordered, _reordered_schema(), rows)
    shutil.copy2(current, expr)
    c = sqlite3.connect(expr)
    c.executescript(EXPRESSION_INDEX)
    c.close()

    results = [
        measure(current, ep._STRUCTURE_META_COLS, "current schema, current export query",
                scale_rows=scale_rows),
        measure(current, "decision_id, court, language", "current schema, leading columns only (floor)",
                scale_rows=scale_rows),
        measure(appended, PROPOSED_META_COLS, "flag columns appended, no index",
                scale_rows=scale_rows),
        measure(reordered, PROPOSED_META_COLS, "small columns moved before the text, no index",
                scale_rows=scale_rows),
        measure(expr, ep._STRUCTURE_META_COLS, "current schema + expression index",
                indexed_by="idx_structure_export_expr", scale_rows=scale_rows),
        measure(proposed, PROPOSED_META_COLS, "PROPOSED: flag columns + covering index",
                indexed_by="idx_structure_export", scale_rows=scale_rows),
    ]
    return {
        "mode": "synthetic",
        "rows": rows,
        "sqlite_version": sqlite3.sqlite_version,
        "scale_rows": scale_rows,
        "pages": {"current": page_breakdown(current), "proposed": page_breakdown(proposed)},
        "results": results,
    }


def run_real(path: Path) -> dict:
    """Read-only facts about a real sidecar; no full scans."""
    conn = _ro(path)
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(structure)")]
        idx = {r[1] for r in conn.execute("PRAGMA index_list(structure)")}
        max_rowid = conn.execute("SELECT max(rowid) FROM structure").fetchone()[0]
        out = {
            "mode": "real",
            "path": str(path),
            "sqlite_version": sqlite3.sqlite_version,
            "page_size": conn.execute("PRAGMA page_size").fetchone()[0],
            "file_bytes": path.stat().st_size,
            "structure_columns_in_record_order": cols,
            "max_rowid": max_rowid,
            "has_proposed_columns": all(c in cols for c in
                                        ("has_sachverhalt", "has_erwaegungen", "has_dispositiv")),
            "has_proposed_index": "idx_structure_export" in idx,
            "plan_current_query": query_plan(
                conn, f"SELECT {ep._STRUCTURE_META_COLS} FROM structure"),
        }
        t0 = time.perf_counter()
        out["export_probe_projection_s"] = round(
            ep._projected_seconds(conn, "structure", ep._STRUCTURE_META_COLS), 1)
        out["export_probe_wall_s"] = round(time.perf_counter() - t0, 2)
        # same rowid windows as the probe: how many text bytes sit in front of
        # (or among) the columns the export needs, per row
        if max_rowid:
            windows = []
            for k in range(ep._PROBE_WINDOWS):
                frac = 0.02 + 0.96 * k / max(1, ep._PROBE_WINDOWS - 1)
                a = max(1, int(max_rowid * frac) - ep._PROBE_WINDOW_ROWS // 2)
                windows.append((a, a + ep._PROBE_WINDOW_ROWS - 1))
            n = text = 0
            for a, b in windows:
                r = conn.execute(
                    "SELECT count(*), coalesce(sum(coalesce(length(CAST(regeste AS BLOB)), 0) "
                    "+ coalesce(length(CAST(sachverhalt AS BLOB)), 0) "
                    "+ coalesce(length(CAST(erwaegungen AS BLOB)), 0) "
                    "+ coalesce(length(CAST(dispositiv AS BLOB)), 0)), 0) "
                    "FROM structure WHERE rowid BETWEEN ? AND ?", (a, b)).fetchone()
                n += r[0]
                text += r[1]
            out["sampled_rows"] = n
            out["sampled_text_bytes_per_row"] = round(text / n, 1) if n else None
            out["projected_text_bytes_walked"] = int(text / n * max_rowid) if n else None
        if out["has_proposed_columns"] and out["has_proposed_index"]:
            out["plan_proposed_query"] = query_plan(
                conn, f"SELECT {PROPOSED_META_COLS} FROM structure INDEXED BY idx_structure_export")
    finally:
        conn.close()
    return out


def _fmt_bytes(n) -> str:
    if n is None:
        return "n/a"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1000:
            return f"{n:,.1f} {unit}"
        n /= 1000
    return f"{n:,.1f} PB"


def _print_synthetic(res: dict) -> None:
    print(f"synthetic sidecar: {res['rows']:,} rows, SQLite {res['sqlite_version']}")
    for name, pages in (res.get("pages") or {}).items():
        if pages:
            print(f"  pages [{name}]: " + ", ".join(
                f"{k}: {', '.join(f'{t}={n:,}' for t, n in v.items())}" for k, v in pages.items()))
    print()
    at_rows = f"@{res['scale_rows']:,} rows"
    hdr = f"{'layout':48s} {'read':>11s} {'B/row':>9s} {at_rows:>16s} {'probe':>8s}  plan"
    print(hdr)
    print("-" * len(hdr))
    for r in res["results"]:
        print(f"{r['layout']:48s} {_fmt_bytes(r['bytes_read']):>11s} "
              f"{(r['bytes_read_per_row'] if r['bytes_read_per_row'] is not None else float('nan')):>9,.0f} "
              f"{_fmt_bytes(r['projected_bytes_at_production_rows']):>16s} "
              f"{r['export_probe_projection_s']:>7.2f}s  {r['plan']}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--rows", type=int, default=5000, help="synthetic sidecar size")
    ap.add_argument("--workdir", type=Path, default=None,
                    help="keep the synthetic sidecars here (default: a temp dir, removed)")
    ap.add_argument("--scale-rows", type=int, default=PRODUCTION_ROWS,
                    help="row count to scale per-row figures to")
    ap.add_argument("--structure-db", type=Path, default=None,
                    help="measure a real sidecar read-only instead (no full scans)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.structure_db is not None:
        res = run_real(args.structure_db)
        print(json.dumps(res, indent=2, ensure_ascii=False))
        return 0
    tmp = None
    workdir = args.workdir
    if workdir is None:
        tmp = tempfile.mkdtemp(prefix="structure_export_cost_")
        workdir = Path(tmp)
    try:
        res = run_synthetic(args.rows, workdir, args.scale_rows)
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)
    if args.json:
        print(json.dumps(res, indent=2, ensure_ascii=False))
    else:
        _print_synthetic(res)
    return 0


if __name__ == "__main__":
    sys.exit(main())
