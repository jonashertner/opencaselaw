#!/usr/bin/env python3
"""Build the verification pack: one SQLite file with everything the research CLI
needs to check citations, pinpoints and quotations offline.

    python scripts/build_verification_pack.py --decisions-db output/decisions.db \\
        --structure-db output/decision_structure.db --output output/dataset/verification_pack.sqlite

Contents (schema_version 2): decision metadata with the service's own citation
strings (built by mcp_server._build_citation_strings, so R1 holds offline),
docket aliases, canonical representations, every indexed Erwägung paragraph
with its text zlib-compressed per row, and a `courts` table (court, canton,
decisions, first_year, last_year; schema 2) grouped from the pack's own
decisions so the offline report can qualify "not in the corpus" by the court's
coverage. Clients tolerate packs without it. No full texts: the pack is
for verification, not for reading. Read-only on its inputs; writes a .tmp and
renames it. The pipeline runs it weekly (publish.py step 3b) and uploads the
gzip to the HuggingFace mirror; `ocl pack pull` fetches it. With --gzip the
script also writes <output>.gz.sha256 in sha256sum format ("<hex>  <name>"),
the sidecar `ocl pack pull` verifies the download against before unpacking.
"""
from __future__ import annotations

import argparse
import contextlib
import gzip
import hashlib
import json
import multiprocessing
import os
import queue
import shutil
import sqlite3
import sys
import time
import traceback
import zlib
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 2
SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE courts (court TEXT, canton TEXT, decisions INTEGER, first_year TEXT, last_year TEXT, PRIMARY KEY (court, canton));
CREATE TABLE decisions (
    decision_id TEXT PRIMARY KEY, court TEXT, canton TEXT, language TEXT, decision_date TEXT,
    docket_number TEXT, docket_number_2 TEXT, citation_string_de TEXT, citation_string_fr TEXT,
    citation_string_it TEXT, canonical_url TEXT, source_url TEXT, content_hash TEXT,
    canonical_decision_id TEXT, has_full_text INTEGER
);
CREATE INDEX idx_pack_docket ON decisions(docket_number);
CREATE INDEX idx_pack_docket2 ON decisions(docket_number_2);
CREATE TABLE aliases (alias_docket_norm TEXT, canonical_decision_id TEXT);
CREATE INDEX idx_pack_alias ON aliases(alias_docket_norm);
CREATE TABLE paragraphs (decision_id TEXT, e_number TEXT, depth INTEGER, parent TEXT, text_z BLOB);
"""
# Created after the paragraphs are loaded. The table was WITHOUT ROWID keyed on
# (decision_id, e_number) until 2026-11-15; the unique index answers the same
# lookups. The source delivers paragraphs grouped by decision but in no key
# order, so every insert into that B-tree landed at a random leaf, and rows of
# ~550 bytes split pages constantly. With the reads (see _source_batches) that
# pass outran the step's two hours on 2026-11-08 and 11-15, at 11.2M paragraphs. Appends to a rowid table cost one
# page each; the index is built once, from a sort of the keys alone.
PARAGRAPH_INDEX = "CREATE UNIQUE INDEX idx_pack_paragraph ON paragraphs(decision_id, e_number)"
META_COLUMNS = ("decision_id", "court", "canton", "language", "decision_date", "docket_number", "docket_number_2",
                "source_url", "content_hash", "collection", "bge_reference")


def _log(message: str) -> None:
    print(f"{datetime.now(timezone.utc).strftime('%H:%M:%S')} {message}", file=sys.stderr, flush=True)


def _citation_builder(repo_dir: Path):
    """mcp_server._build_citation_strings, imported from the checkout so the pack
    carries the service's own strings; a minimal fallback keeps the builder
    usable in tests without the server module."""
    try:
        sys.path.insert(0, str(repo_dir))
        os.environ.setdefault("SWISS_CASELAW_DIR", str(repo_dir / "output"))
        import mcp_server  # noqa: WPS433
        return mcp_server._build_citation_strings
    except Exception as exc:  # noqa: BLE001
        _log(f"mcp_server not importable ({exc}); citation strings will be absent")
        return None


def _remove_stale_partials(output: Path) -> None:
    """A build killed by the step timeout leaves <date>.sqlite.tmp and its
    journal behind, ~17 GB a week (two were found on 2026-11-15). Clear every
    unfinished pack in the output directory before starting a new one."""
    for pattern in ("*.sqlite.tmp", "*.sqlite.tmp-journal", "*.sqlite.gz.tmp"):
        for stale in output.parent.glob(pattern):
            try:
                stale.unlink()
                _log(f"removed unfinished {stale.name}")
            except OSError as exc:
                _log(f"could not remove {stale.name}: {exc}")


def _has_text_expr() -> str:
    """octet_length() takes the size from the record header (SQLite >= 3.43);
    length() on TEXT decodes the whole value to count characters. The overflow
    pages are still read, because source_url and content_hash come after
    full_text in the record (see _source_batches)."""
    if sqlite3.sqlite_version_info >= (3, 43, 0):
        return "(full_text IS NOT NULL AND octet_length(full_text) > 0)"
    return "(full_text IS NOT NULL AND length(full_text) > 0)"


# Both inputs are read in rowid ranges by parallel processes. One reader waits
# on one page at a time: the pack's columns source_url and content_hash come
# after full_text in decisions.db, so every row walks its judgment's overflow
# chain (the whole 73 GB file), and the paragraph rows lie scattered between
# the FTS pages of decision_structure.db. On 2026-11-22 a single reader took
# 27 minutes for the decisions and ~65 for the paragraphs, at ~10 MB/s with
# the CPU mostly idle; the data volume answers many reads in flight far
# faster than one. Eight readers on 11-23: 6 and 23 minutes, the whole step
# with index and gzip 27.5 minutes. The writer stays a single connection in the parent.
CHUNKS_PER_WORKER = 16
BATCH_ROWS = 2000


def _default_workers() -> int:
    return max(1, min(8, os.cpu_count() or 1))


def _rowid_ranges(con: sqlite3.Connection, table: str, parts: int) -> list[tuple[int, int]] | None:
    """Split the table's rowid span into `parts` contiguous ranges; None when
    the table has no rowid (WITHOUT ROWID), so the caller scans it whole."""
    try:
        lo, hi = con.execute(f"SELECT min(rowid), max(rowid) FROM {table}").fetchone()
    except sqlite3.OperationalError as exc:
        if "rowid" in str(exc):
            return None
        raise
    if lo is None:
        return []
    step = max(1, -(-(hi - lo + 1) // parts))
    return [(a, min(a + step - 1, hi)) for a in range(lo, hi + 1, step)]


def _decision_batches(con, sql, args, selected, canonical, build_strings):
    batch = []
    for row in con.execute(sql, args):
        record = {k: row[k] for k in selected}
        strings = {}
        if build_strings is not None:
            try:
                strings = build_strings(dict(record))
            except Exception:  # noqa: BLE001
                strings = {}
        batch.append((record["decision_id"], record.get("court"), record.get("canton"), record.get("language"),
                      record.get("decision_date"), record.get("docket_number"), record.get("docket_number_2"),
                      strings.get("citation_string_de"), strings.get("citation_string_fr"), strings.get("citation_string_it"),
                      strings.get("canonical_url"), record.get("source_url"), record.get("content_hash"),
                      canonical.get(record["decision_id"], record["decision_id"]), int(row["has_full_text"] or 0)))
        if len(batch) >= BATCH_ROWS:
            yield batch
            batch = []
    if batch:
        yield batch


def _paragraph_batches(con, sql, args):
    batch = []
    for decision_id, e_number, depth, parent, text in con.execute(sql, args):
        if not isinstance(text, str) or not text:
            continue
        batch.append((decision_id, str(e_number), int(depth or 0), parent, zlib.compress(text.encode("utf-8"), 6)))
        if len(batch) >= BATCH_ROWS:
            yield batch
            batch = []
    if batch:
        yield batch


def _reader_process(open_reader, tasks, out) -> None:
    try:
        read = open_reader()
        while (span := tasks.get()) is not None:
            for batch in read(span):
                out.put(("rows", batch))
        out.put(("done", None))
    except BaseException:  # noqa: BLE001
        out.put(("error", traceback.format_exc()))


def _parallel_batches(open_reader, spans, workers: int):
    """Yield row batches read by `workers` forked processes, each taking rowid
    spans from a shared queue. open_reader() runs in the child and returns a
    function span -> batches. Fork, not spawn: the reader closures are not
    picklable, and nothing is imported into the parent that starts threads
    (mcp_server is imported in each child), so forking is safe. A reader
    that raises or dies fails the build."""
    ctx = multiprocessing.get_context("fork")
    # SimpleQueue for the spans: a Queue starts a feeder thread in the parent
    # on its first put, and the parent must have no threads when it forks.
    # The parent only reads `out`, so that queue starts no thread here.
    tasks, out = ctx.SimpleQueue(), ctx.Queue(maxsize=workers * 4)
    procs = [ctx.Process(target=_reader_process, args=(open_reader, tasks, out), daemon=True) for _ in range(workers)]
    for proc in procs:
        proc.start()
    for span in spans:
        tasks.put(span)
    for _ in range(workers):
        tasks.put(None)
    try:
        done = 0
        while done < workers:
            try:
                kind, payload = out.get(timeout=30)
            except queue.Empty:
                dead = [p.exitcode for p in procs if p.exitcode not in (None, 0)]
                if dead:
                    raise RuntimeError(f"a pack reader exited with code {dead[0]}")
                continue
            if kind == "rows":
                yield payload
            elif kind == "done":
                done += 1
            else:
                raise RuntimeError(f"a pack reader failed:\n{payload}")
    finally:
        for proc in procs:
            if proc.is_alive():
                proc.terminate()
        for proc in procs:
            proc.join(timeout=10)


def _source_batches(db: Path, table: str, sql: str, batches, *, workers: int, limit: int | None, row_factory=None,
                    setup=None):
    """Row batches from `sql` (a SELECT over `table` without WHERE), read by
    `workers` processes over rowid ranges, or in this process when workers is
    1, a limit is set, or the table has no rowid. setup(), if given, runs once
    in each reader and returns extra arguments for batches()."""
    def connect():
        con = sqlite3.connect(f"file:{db}?mode=ro&immutable=1", uri=True)
        con.row_factory = row_factory
        return con

    spans = None
    if workers > 1 and not limit:
        probe = connect()
        spans = _rowid_ranges(probe, table, workers * CHUNKS_PER_WORKER)
        probe.close()
    if spans is None:
        con = connect()
        extra = setup() if setup else ()
        tail = f" LIMIT {int(limit)}" if limit else ""
        yield from batches(con, sql + tail, (), *extra)
        con.close()
        return

    def open_reader():
        con = connect()
        extra = setup() if setup else ()
        return lambda span: batches(con, sql + " WHERE rowid BETWEEN ? AND ?", span, *extra)

    yield from _parallel_batches(open_reader, spans, min(workers, max(1, len(spans))))


def build(decisions_db: Path, structure_db: Path, output: Path, *, repo_dir: Path, manifest_db: Path | None = None,
          limit: int | None = None, workers: int = 1) -> dict:
    started = time.time()
    tmp = output.with_name(output.name + ".tmp")
    _remove_stale_partials(output)
    dec = sqlite3.connect(f"file:{decisions_db}?mode=ro&immutable=1", uri=True)
    columns = {r[1] for r in dec.execute("PRAGMA table_info(decisions)")}
    selected = [c for c in META_COLUMNS if c in columns]
    has_text = "full_text" in columns
    pack = sqlite3.connect(tmp)
    # The .tmp is renamed only once complete, so a crash loses nothing a
    # journal could save; the rollback journal used to double the writes.
    pack.execute("PRAGMA journal_mode=OFF")
    pack.execute("PRAGMA synchronous=OFF")
    pack.execute("PRAGMA cache_size=-1048576")  # 1 GiB: the key sort of the paragraph index stays in memory
    pack.executescript(SCHEMA)
    canonical: dict[str, str] = {}
    if manifest_db and manifest_db.exists():
        try:
            man = sqlite3.connect(f"file:{manifest_db}?mode=ro&immutable=1", uri=True)
            canonical = {r[0]: r[1] for r in man.execute(
                "SELECT member_decision_id, canonical_decision_id FROM decision_representations "
                "WHERE canonical_decision_id != member_decision_id")}
            man.close()
        except sqlite3.Error as exc:
            _log(f"representation manifest skipped: {exc}")
    query = "SELECT " + ", ".join(selected) + (f", {_has_text_expr()} AS has_full_text" if has_text else ", 0 AS has_full_text") + " FROM decisions"
    _log(f"reading with {workers} process(es)" if workers > 1 and not limit else "reading in one process")
    rows_out = 0
    next_log = 100000
    # closing(): a failed insert stops the readers now, not when the traceback is freed.
    with contextlib.closing(_source_batches(decisions_db, "decisions", query, _decision_batches, workers=workers,
                                            limit=limit, row_factory=sqlite3.Row,
                                            setup=lambda: (selected, canonical, _citation_builder(repo_dir)))) as source:
        for batch in source:
            pack.executemany("INSERT OR REPLACE INTO decisions VALUES (" + ",".join("?" * 15) + ")", batch)
            rows_out += len(batch)
            if rows_out >= next_log:
                _log(f"decisions {rows_out:,}")
                next_log += 100000
    pack.commit()
    _log(f"decisions {rows_out:,} in {time.time() - started:.0f}s")
    # Per-court coverage, from the pack's own decisions table, so the offline report
    # can say what "not in the corpus" means for the court a reference names.
    pack.execute("INSERT OR REPLACE INTO courts SELECT court, canton, COUNT(*), "
                 "substr(MIN(decision_date), 1, 4), substr(MAX(decision_date), 1, 4) FROM decisions "
                 "WHERE court IS NOT NULL GROUP BY court, canton")
    pack.commit()
    _log(f"courts {pack.execute('SELECT COUNT(*) FROM courts').fetchone()[0]:,}")
    try:
        aliases = dec.execute("SELECT alias_docket_norm, canonical_decision_id FROM decision_docket_aliases").fetchall()
        pack.executemany("INSERT INTO aliases VALUES (?, ?)", [(r[0], r[1]) for r in aliases])
        _log(f"aliases {len(aliases):,}")
    except sqlite3.Error as exc:
        _log(f"aliases skipped: {exc}")
    dec.close()
    paragraphs = 0
    next_log = 500000
    with contextlib.closing(_source_batches(structure_db, "erwaegungen_paragraph",
                                            "SELECT decision_id, e_number, depth, parent, text FROM erwaegungen_paragraph",
                                            _paragraph_batches, workers=workers,
                                            limit=int(limit) * 20 if limit else None)) as source:
        for batch in source:
            pack.executemany("INSERT INTO paragraphs VALUES (?,?,?,?,?)", batch)
            paragraphs += len(batch)
            if paragraphs >= next_log:
                pack.commit()
                _log(f"paragraphs {paragraphs:,}")
                next_log += 500000
    pack.commit()
    _log(f"paragraphs {paragraphs:,} in {time.time() - started:.0f}s")
    # The source's primary key is (decision_id, e_number), so the index cannot
    # meet a duplicate; if it ever did, the build fails here instead of
    # publishing a pack with two texts for one Erwägung.
    pack.execute(PARAGRAPH_INDEX)
    pack.commit()
    _log(f"paragraph index in {time.time() - started:.0f}s")
    generation = ""
    try:
        health = json.loads((repo_dir / "output" / "stats.json").read_text(encoding="utf-8")) if (repo_dir / "output" / "stats.json").exists() else {}
        generation = str(health.get("db_generation") or "")
    except (OSError, ValueError):
        pass
    if not generation:
        generation = str(int(decisions_db.stat().st_mtime))
    meta = {"schema_version": str(SCHEMA_VERSION), "built_at": datetime.now(timezone.utc).isoformat(), "db_generation": generation,
            "decisions": str(rows_out), "paragraphs": str(paragraphs), "source": "OpenCaseLaw verification pack; citation strings by the service; CC0 data"}
    pack.executemany("INSERT INTO meta VALUES (?, ?)", list(meta.items()))
    pack.commit()
    # No VACUUM: an append-only build leaves no free pages, and VACUUM rewrote
    # the whole 16 GB file through a temp copy in SQLITE_TMPDIR (the root disk).
    pack.close()
    os.replace(tmp, output)
    meta["bytes"] = output.stat().st_size
    meta["seconds"] = round(time.time() - started)
    _log(f"pack {output} {meta['bytes'] / 1e9:.2f} GB in {meta['seconds']}s")
    return meta


class _HashingWriter:
    """Forwards writes to a file and feeds the same bytes to a digest, so the gzip is hashed as it is written."""

    def __init__(self, fp, digest):
        self._fp, self._digest = fp, digest

    def write(self, data):
        self._digest.update(data)
        return self._fp.write(data)

    def flush(self):
        self._fp.flush()


def gzip_file(path: Path) -> tuple[Path, str]:
    """Write <path>.gz (level 6); returns the gzip path and the sha256 hex digest of its bytes."""
    target = path.with_name(path.name + ".gz")
    tmp = target.with_name(target.name + ".tmp")
    digest = hashlib.sha256()
    with open(path, "rb") as src, open(tmp, "wb") as raw, gzip.GzipFile(filename=target.name, mode="wb", compresslevel=6,
                                                                       fileobj=_HashingWriter(raw, digest)) as dst:
        shutil.copyfileobj(src, dst, 1 << 20)
    os.replace(tmp, target)
    return target, digest.hexdigest()


def write_sha256(gz: Path, digest: str) -> Path:
    """<gz>.sha256 in sha256sum format, so `sha256sum -c <gz>.sha256` and `ocl pack pull` both verify it."""
    sidecar = gz.with_name(gz.name + ".sha256")
    tmp = sidecar.with_name(sidecar.name + ".tmp")
    tmp.write_text(f"{digest}  {gz.name}\n", encoding="utf-8")
    os.replace(tmp, sidecar)
    return sidecar


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--decisions-db", type=Path, required=True)
    ap.add_argument("--structure-db", type=Path, required=True)
    ap.add_argument("--manifest-db", type=Path, default=None, help="representation_manifest.db (optional)")
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--repo-dir", type=Path, default=Path(__file__).resolve().parents[1])
    ap.add_argument("--gzip", action="store_true", help="also write <output>.gz and <output>.gz.sha256 (sha256sum format) for upload")
    ap.add_argument("--limit", type=int, default=None, help="build a small pack (tests)")
    ap.add_argument("--workers", type=int, default=_default_workers(),
                    help="reader processes for the two source scans (default: CPUs, at most 8; 1 reads in-process)")
    args = ap.parse_args()
    # SQLite reads SQLITE_TMPDIR once, when it initialises, so this must come
    # before the first connection: any sort that spills while the index is
    # built goes to the pack's own volume, not the root disk.
    os.environ.setdefault("SQLITE_TMPDIR", str(args.output.resolve().parent))
    meta = build(args.decisions_db, args.structure_db, args.output, repo_dir=args.repo_dir, manifest_db=args.manifest_db, limit=args.limit,
                 workers=args.workers)
    if args.gzip:
        gz, digest = gzip_file(args.output)
        sidecar = write_sha256(gz, digest)
        meta["gzip_bytes"] = gz.stat().st_size
        meta["gzip_sha256"] = digest
        meta["sha256_file"] = str(sidecar)
        _log(f"gzip {gz} {meta['gzip_bytes'] / 1e9:.2f} GB ({meta['gzip_bytes']:,} bytes) sha256 {digest} -> {sidecar.name}")
    print(json.dumps(meta, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
