#!/usr/bin/env python3
"""Build the corpus lexicon that dehyphenate.py decides line breaks with.

Counts every word, every hyphenated pair ("procès-verbal") and every spaced
pair ("Rechtfertigungs- noch", a suspended compound) that appears inside a
line, i.e. not next to a line-end hyphen, a soft hyphen or a
"x- y" break, so the halves of broken words never count. The result is a
small SQLite file, opened read-only and immutable by the builds.

Inputs are JSONL shards and/or Parquet files (the HuggingFace mirror's
data/<court>.parquet carries the same full_text):

    python scripts/build_dehyphen_lexicon.py --shards output/decisions \\
        --out output/dehyphen_lexicon.db
    python scripts/build_dehyphen_lexicon.py --parquet hf/data --workers 6 \\
        --out dehyphen_lexicon.db

Building from the mirror needs no read of the production volume at all.
From the shards it is one streaming read of every shard: on the production
host run it outside the build window (CLAUDE.md invariant 9).

Each input file is counted by one worker into its own partial file (counts
flushed every --flush-every rows, so memory stays bounded); the partials
are then summed into --out, pruned below --min-count, and swapped in with
os.replace.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sqlite3
import sys
import tempfile
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dehyphenate import count_tokens  # noqa: E402

FIELDS = ("full_text", "regeste", "title")
SCHEMA = """
CREATE TABLE IF NOT EXISTS words (w TEXT PRIMARY KEY, n INTEGER NOT NULL) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS hyphenated (w TEXT PRIMARY KEY, n INTEGER NOT NULL) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS spaced (w TEXT PRIMARY KEY, n INTEGER NOT NULL) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS capitalized (w TEXT PRIMARY KEY, n INTEGER NOT NULL) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def iter_rows(path: Path, fields=FIELDS) -> Iterator[dict]:
    """Rows of one JSONL shard or Parquet file, only ``fields`` read."""
    if path.suffix == ".parquet":
        import pyarrow.parquet as pq
        pf = pq.ParquetFile(path)
        cols = [f for f in fields if f in pf.schema_arrow.names]
        for batch in pf.iter_batches(batch_size=2000, columns=cols):
            yield from batch.to_pylist()
        return
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def _flush(conn: sqlite3.Connection, words: Counter, hyph: Counter, spaced: Counter,
           caps: Counter) -> None:
    for table, c in (("words", words), ("hyphenated", hyph), ("spaced", spaced),
                     ("capitalized", caps)):
        conn.executemany(
            f"INSERT INTO {table} (w, n) VALUES (?, ?) "
            f"ON CONFLICT(w) DO UPDATE SET n = n + excluded.n",
            c.items(),
        )
        c.clear()
    conn.commit()


def count_file(path: str, partial: str, flush_every: int = 20_000) -> tuple[str, int]:
    """Count one input file into the partial lexicon ``partial``."""
    conn = sqlite3.connect(partial)
    conn.executescript("PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;" + SCHEMA)
    words: Counter = Counter()
    hyph: Counter = Counter()
    spaced: Counter = Counter()
    caps: Counter = Counter()
    rows = 0
    for row in iter_rows(Path(path)):
        for f in FIELDS:
            v = row.get(f)
            if isinstance(v, str) and v:
                count_tokens(v, words, hyph, spaced, caps)
        rows += 1
        if rows % flush_every == 0:
            _flush(conn, words, hyph, spaced, caps)
    _flush(conn, words, hyph, spaced, caps)
    conn.close()
    return path, rows


def input_paths(shards: list[str], parquet: list[str]) -> list[Path]:
    out: list[Path] = []
    for specs, suffix in ((shards, "*.jsonl"), (parquet, "*.parquet")):
        for spec in specs or []:
            p = Path(spec)
            if p.is_dir():
                out.extend(sorted(p.glob(suffix)))
            else:
                out.extend(Path(x) for x in sorted(glob.glob(spec)))
    return out


def build(inputs: list[Path], out: Path, min_count: int = 2, workers: int = 1,
          flush_every: int = 20_000) -> dict:
    t0 = time.time()
    tmp = out.with_suffix(out.suffix + ".tmp")
    if tmp.exists():
        tmp.unlink()
    with tempfile.TemporaryDirectory(prefix="dehyphen_lex_", dir=out.parent) as td:
        jobs = {str(p): os.path.join(td, f"part_{i:04d}.db") for i, p in enumerate(inputs)}
        rows = 0
        # Largest files first, so the long ones do not start last.
        order = sorted(jobs, key=lambda p: -os.path.getsize(p))
        if workers > 1:
            with ProcessPoolExecutor(max_workers=workers) as ex:
                futs = [ex.submit(count_file, p, jobs[p], flush_every) for p in order]
                for fut in as_completed(futs):
                    path, n = fut.result()
                    rows += n
                    print(f"  {Path(path).name}: {n:,} rows ({time.time() - t0:,.0f}s)",
                          file=sys.stderr)
        else:
            for p in order:
                _, n = count_file(p, jobs[p], flush_every)
                rows += n
                print(f"  {Path(p).name}: {n:,} rows ({time.time() - t0:,.0f}s)", file=sys.stderr)

        conn = sqlite3.connect(tmp)
        conn.executescript("PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;" + SCHEMA)
        for part in jobs.values():
            conn.execute("ATTACH DATABASE ? AS p", (part,))
            for table in ("words", "hyphenated", "spaced", "capitalized"):
                conn.execute(
                    f"INSERT INTO {table} (w, n) SELECT w, n FROM p.{table} WHERE true "
                    f"ON CONFLICT(w) DO UPDATE SET n = n + excluded.n"
                )
            conn.commit()
            conn.execute("DETACH DATABASE p")
    conn.execute("DELETE FROM words WHERE n < ?", (min_count,))
    conn.execute("DELETE FROM hyphenated WHERE n < ?", (min_count,))
    conn.execute("DELETE FROM spaced WHERE n < ?", (min_count,))
    conn.execute("DELETE FROM capitalized WHERE n < ?", (min_count,))
    n_spaced = conn.execute("SELECT COUNT(*) FROM spaced").fetchone()[0]
    n_words = conn.execute("SELECT COUNT(*) FROM words").fetchone()[0]
    n_hyph = conn.execute("SELECT COUNT(*) FROM hyphenated").fetchone()[0]
    meta = {
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "rows": str(rows),
        "inputs": str(len(inputs)),
        "min_count": str(min_count),
        "words": str(n_words),
        "hyphenated": str(n_hyph),
        "spaced": str(n_spaced),
        "fields": ",".join(FIELDS),
        "seconds": str(round(time.time() - t0)),
    }
    conn.executemany("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", meta.items())
    conn.commit()
    conn.execute("VACUUM")
    conn.close()
    os.replace(tmp, out)
    return meta


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--shards", nargs="+", default=[], help="JSONL shard directories or globs")
    ap.add_argument("--parquet", nargs="+", default=[], help="Parquet directories or globs")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--min-count", type=int, default=2)
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--flush-every", type=int, default=20_000)
    args = ap.parse_args()
    inputs = input_paths(args.shards, args.parquet)
    if not inputs:
        print("no inputs found", file=sys.stderr)
        return 1
    meta = build(inputs, args.out, args.min_count, args.workers, args.flush_every)
    print(json.dumps(meta, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
