"""Daily delta publisher — ports the old private-repo pipeline into
caselaw-repo-1 so the same process that produces the FTS5 DB also emits
the incremental delta artifacts that consumers (Adrian et al.) already
wired up against `voilaj/swiss-caselaw`.

What this writes to HF `voilaj/swiss-caselaw` (identical paths to the
previous pipeline — zero consumer breakage):

  artifacts/sqlite/deltas/{date}.sqlite.zst   ← daily delta, zstd-compressed
  artifacts/parquet/deltas/{date}.parquet     ← same data, parquet (columns)
  data/delta-{date}.parquet                   ← append-form for load_dataset
  artifacts/manifest.json                     ← append this delta entry

Source of truth: the FTS5 decisions.db on the VPS.

Delta semantics: we detect "new in our corpus since last publish" via
a snapshot of all decision_ids stored locally. Today's delta = current
FTS5 ids − yesterday's snapshot ids. Robust to backfills, scraper gap-
fills, and `scraped_at` backdating. Avoids any reliance on a date
filter over the 62 GB FTS5 DB (which has no scraped_at index).

Why we diverge from the old private-repo schema in one place:
the old `id` column was a PostgreSQL UUID. Consumers (per Adrian)
compute their own decision_id via `make_decision_id(court, docket)`.
Here we set `id = decision_id` directly (e.g. "bger_4A_747_2012"),
which IS Adrian's expected decision_id format. Consumers can read `id`
unchanged and skip the make_decision_id step entirely.
"""
from __future__ import annotations

import datetime as _dt
import hashlib as _hashlib
import json as _json
import logging as _logging
import os as _os
import shutil as _shutil
import sqlite3 as _sqlite3
import subprocess as _subprocess
import time as _time
from pathlib import Path as _Path
from typing import Any, Dict, Iterable, Iterator, List, Optional

log = _logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────

HF_REPO_ID = "voilaj/swiss-caselaw"
MANIFEST_PATH_IN_REPO = "artifacts/manifest.json"
MANIFEST_SCHEMA = "swiss-caselaw-artifacts-v1"
SQLITE_SNAPSHOT_SCHEMA_VERSION = 1

# How many dated build subdirs to retain under build_dir/{snapshot,delta}.
# Older ones are pruned at the start of each build. Before this, only the
# CURRENT date's dir was removed, so dated dirs accumulated one per nightly
# run without bound — the scratch tree grew to ~17 GB on the root disk and was
# the suspected cause of the 2026-06-15 root-fill publish failure.
BUILD_DIR_RETENTION = 3


def _prune_old_build_dirs(parent: _Path, keep: str,
                          retain: int = BUILD_DIR_RETENTION) -> None:
    """Keep only the ``retain`` newest dated subdirs under ``parent``, always
    retaining ``keep`` (the date about to be built). Subdir names are
    YYYY-MM-DD, so a lexicographic sort is chronological. Bounds the delta /
    snapshot scratch tree, which otherwise grew without limit."""
    if not parent.exists():
        return
    dated = sorted((c for c in parent.iterdir() if c.is_dir()), key=lambda c: c.name)
    survivors = {keep}
    for child in reversed(dated):
        if len(survivors) >= retain:
            break
        survivors.add(child.name)
    for child in dated:
        if child.name in survivors:
            continue
        try:
            _shutil.rmtree(child)
            log.info("pruned stale build dir %s", child)
        except OSError as e:
            log.warning("prune failed for %s: %s", child, e)

# Courts classified as federal-level for the `level` column.
FEDERAL_COURTS = frozenset({
    "bger", "bge", "bvger", "bstger", "bpatger", "mkg",
    "finma", "finma_versicherungsrecht",
    "weko", "edoeb", "ubi", "elcom", "postcom", "comcom", "uek", "six_ser",
    "ch_bundesrat", "ch_vb",
    "ta_sst", "bge_egmr", "hudoc_ch", "emark", "bge_historical",
})

# Columns of the delta SQLite — preserved from the old pipeline so
# existing consumers see an identical schema.
DECISION_COLS: List[str] = [
    "id", "source_id", "source_name", "level", "canton", "court", "chamber",
    "language", "docket", "decision_date", "published_date", "title",
    "url", "pdf_url", "content_text", "content_sha256",
    "fetched_at", "updated_at",
]

# Columns written to data/delta-{date}.parquet (subset for load_dataset())
BASE_COLS: List[str] = [
    "id", "source_id", "source_name", "level", "canton", "court", "chamber",
    "docket", "decision_date", "published_date", "title", "language",
    "url", "pdf_url", "content_text",
]


# ── Helpers ─────────────────────────────────────────────────────────────

def _utc_now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def _sha256_text(s: str) -> str:
    return _hashlib.sha256((s or "").encode("utf-8", errors="ignore")).hexdigest()


def _sha256_file(path: _Path) -> str:
    h = _hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(1 << 20)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _classify_level(court: str | None, canton: str | None) -> str:
    c = (court or "").lower()
    if c in FEDERAL_COURTS:
        return "federal"
    canton_norm = (canton or "").upper()
    if canton_norm == "CH" or canton_norm == "":
        # Fall-through: unknown court with no canton → assume federal
        return "federal" if not canton_norm else "cantonal"
    return "cantonal"


def _normalize_canton(canton: str | None) -> str | None:
    """Old pipeline used NULL for federal-scope decisions; preserve that."""
    if not canton or canton.upper() == "CH":
        return None
    return canton.upper()


def _row_to_decision(row: Dict[str, Any]) -> Dict[str, Any]:
    """Map our FTS5 row → delta-schema dict."""
    court = row.get("court") or ""
    canton = _normalize_canton(row.get("canton"))
    content_text = row.get("full_text") or ""
    title = row.get("title") or ""
    docket = row.get("docket_number") or ""
    content_sha = row.get("content_hash") or _sha256_text(f"{content_text}\n{title}\n{docket}")
    scraped_at = row.get("scraped_at") or _utc_now_iso()
    return {
        "id": row.get("decision_id") or "",
        "source_id": row.get("source_id") or "",
        "source_name": row.get("source") or row.get("source_spider") or "",
        "level": _classify_level(court, row.get("canton")),
        "canton": canton,
        "court": court,
        "chamber": row.get("chamber") or "",
        "language": row.get("language") or "",
        "docket": docket,
        "decision_date": row.get("decision_date") or "",
        "published_date": row.get("publication_date") or "",
        "title": title,
        "url": row.get("source_url") or "",
        "pdf_url": row.get("pdf_url") or "",
        "content_text": content_text,
        "content_sha256": content_sha,
        "fetched_at": scraped_at,
        "updated_at": scraped_at,
    }


# ── FTS5 source ─────────────────────────────────────────────────────────

def snapshot_all_ids(db_path: _Path) -> set[str]:
    """Return every decision_id currently in the FTS5 DB. O(N) scan, cheap —
    a ~1M-row SELECT on an immutable DB is a single sequential pass."""
    uri = f"file:{db_path}?mode=ro&immutable=1"
    conn = _sqlite3.connect(uri, uri=True)
    try:
        cur = conn.execute("SELECT decision_id FROM decisions")
        return {row[0] for row in cur if row[0]}
    finally:
        conn.close()


_SELECT_COLS = (
    "decision_id, court, canton, chamber, docket_number, docket_number_2, "
    "decision_date, publication_date, language, title, regeste, "
    "full_text, source_url, pdf_url, scraped_at, source, source_id, "
    "source_spider, content_hash, "
    # base-parquet parity fields (P0.4): everything DECISION_SCHEMA needs
    # that decisions.db carries. judges/clerks/collection/appeal_info/
    # bge_reference/external_id exist only in the JSONL shards, so they are
    # schema-present but NULL in deltas.
    "abstract_de, abstract_fr, abstract_it, legal_area, outcome, "
    "decision_type, cited_decisions, marked_for_publication, "
    # derived classification columns (inline at insert from 2026-07-04
    # builds; normalize_row re-derives when NULL so older DBs still export)
    "branch, proceeding_type, procedural_code, "
    "appealed_court_raw, appealed_date, appealed_docket"
)


def iter_decisions_by_ids(db_path: _Path, ids: Iterable[str], chunk: int = 500,
                          raw: bool = False) -> Iterator[Dict[str, Any]]:
    """Stream full decision rows for the given decision_ids. Chunks the IN
    clause to keep SQLite parameter count sane.

    ``raw=False`` (default) yields delta-vocabulary dicts via
    _row_to_decision (the delta-sqlite consumer). ``raw=True`` yields the
    unmapped decisions.db row dicts — used by the base-schema parquet
    writer, which must see the base vocabulary (P0.4).

    Defensive against single-row page corruption (2026-05-16 incident):
    if a batch read raises ``DatabaseError`` mid-cursor (typically caused
    by a corrupt overflow page holding ``full_text`` for one specific
    row — e.g. ``ecthr_chamber_29447_17``), the function falls back to
    fetching the remaining IDs in the batch one-by-one and skips just
    the rows whose individual SELECT raises. Skipped rows are logged at
    ERROR level so monitoring picks them up. They get re-attempted on the
    next full FTS5 rebuild from JSONL, which writes fresh pages.
    """
    id_list = list(ids)
    if not id_list:
        return
    uri = f"file:{db_path}?mode=ro&immutable=1"
    conn = _sqlite3.connect(uri, uri=True)
    conn.row_factory = _sqlite3.Row
    try:
        for i in range(0, len(id_list), chunk):
            batch_ids = id_list[i : i + chunk]
            yielded: set[str] = set()
            placeholders = ",".join(["?"] * len(batch_ids))
            try:
                cur = conn.execute(
                    f"SELECT {_SELECT_COLS} FROM decisions "
                    f"WHERE decision_id IN ({placeholders})",
                    batch_ids,
                )
                for row in cur:
                    yielded.add(row["decision_id"])
                    yield dict(row) if raw else _row_to_decision(dict(row))
            except _sqlite3.DatabaseError as e:
                # Batched cursor died mid-iteration (almost always means
                # one specific row's overflow chain is corrupt). Retry the
                # remaining IDs one-at-a-time, skipping per-row failures.
                remaining = [did for did in batch_ids if did not in yielded]
                log.warning(
                    "iter_decisions_by_ids: batch of %d hit DatabaseError "
                    "(yielded %d, retrying %d per-ID): %s",
                    len(batch_ids), len(yielded), len(remaining), e,
                )
                for did in remaining:
                    try:
                        row = conn.execute(
                            f"SELECT {_SELECT_COLS} FROM decisions "
                            f"WHERE decision_id = ?",
                            [did],
                        ).fetchone()
                        if row:
                            yield dict(row) if raw else _row_to_decision(dict(row))
                    except _sqlite3.DatabaseError as e2:
                        log.error(
                            "iter_decisions_by_ids: SKIPPING %s — "
                            "page corruption (likely overflow chain). "
                            "Will be re-fetched on next full rebuild from "
                            "JSONL: %s",
                            did, e2,
                        )
                        continue
    finally:
        conn.close()


def load_snapshot_ids(snapshot_path: _Path) -> set[str]:
    """Load previously-seen decision_ids from a local state file.
    Returns empty set if file doesn't exist (first-run behavior)."""
    if not snapshot_path.exists():
        return set()
    try:
        data = _json.loads(snapshot_path.read_text(encoding="utf-8"))
        return set(data.get("ids", []))
    except Exception as e:
        log.warning("snapshot load failed (%s); treating as empty", e)
        return set()


def save_snapshot_ids(snapshot_path: _Path, ids: set[str], date: str) -> None:
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = snapshot_path.with_suffix(".tmp")
    tmp.write_text(
        _json.dumps({"saved_at": _utc_now_iso(), "date": date, "count": len(ids),
                     "ids": sorted(ids)}, ensure_ascii=False),
        encoding="utf-8",
    )
    tmp.replace(snapshot_path)


# ── Delta SQLite build ──────────────────────────────────────────────────

def _create_delta_db(path: _Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = _sqlite3.connect(str(path))
    try:
        conn.execute("PRAGMA journal_mode=OFF")
        conn.execute("PRAGMA synchronous=OFF")
        conn.execute("PRAGMA temp_store=MEMORY")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS decisions (
              id TEXT PRIMARY KEY,
              source_id TEXT,
              source_name TEXT,
              level TEXT,
              canton TEXT,
              court TEXT,
              chamber TEXT,
              language TEXT,
              docket TEXT,
              decision_date TEXT,
              published_date TEXT,
              title TEXT,
              url TEXT,
              pdf_url TEXT,
              content_text TEXT,
              content_sha256 TEXT,
              fetched_at TEXT,
              updated_at TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_delta_decision_date ON decisions(decision_date)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_delta_court ON decisions(court)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_delta_level ON decisions(level)")
        conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
        conn.commit()
    finally:
        conn.close()


def _bulk_insert(delta_db: _Path, decisions: Iterable[Dict[str, Any]], batch: int = 2000) -> int:
    conn = _sqlite3.connect(str(delta_db))
    inserted = 0
    try:
        conn.execute("PRAGMA journal_mode=OFF")
        conn.execute("PRAGMA synchronous=OFF")
        conn.execute("PRAGMA temp_store=MEMORY")
        placeholders = ",".join(["?"] * len(DECISION_COLS))
        sql = f"INSERT OR REPLACE INTO decisions ({','.join(DECISION_COLS)}) VALUES ({placeholders})"
        buf: List[List[Any]] = []
        for d in decisions:
            buf.append([d.get(c) for c in DECISION_COLS])
            if len(buf) >= batch:
                conn.executemany(sql, buf)
                inserted += len(buf)
                buf.clear()
        if buf:
            conn.executemany(sql, buf)
            inserted += len(buf)
        conn.commit()
    finally:
        conn.close()
    return inserted


# ── Parquet export ──────────────────────────────────────────────────────

def _export_parquet_base_schema(db_path: _Path, ids, out_path: _Path) -> int:
    """Write delta-{date}.parquet with the SAME schema as the full corpus
    export (export_parquet.DECISION_SCHEMA incl. has_full_text/text_length
    and real types), so consumers can append deltas to the base without
    remapping (LegalStats wishlist P0.5; pre-fix this artifact was an
    all-string parquet in the delta-sqlite vocabulary, no has_full_text).

    Reads straight from decisions.db (base vocabulary) and reuses
    export_parquet.normalize_row so computed fields match the full export
    exactly. Fields that exist only in the JSONL shards (judges/clerks/
    collection/appeal_info/bge_reference/external_id) are schema-present
    but NULL in deltas.
    """
    import sys as _sys
    _repo_root = str(_Path(__file__).resolve().parents[1])
    if _repo_root not in _sys.path:
        _sys.path.insert(0, _repo_root)
    import pyarrow as pa
    import pyarrow.parquet as pq
    from export_parquet import DECISION_SCHEMA, normalize_row

    out_path.parent.mkdir(parents=True, exist_ok=True)
    writer = pq.ParquetWriter(str(out_path), schema=DECISION_SCHEMA,
                              compression="zstd", use_dictionary=True)
    total = 0
    batch: List[Dict[str, Any]] = []
    try:
        for row in iter_decisions_by_ids(db_path, ids, raw=True):
            batch.append(normalize_row(dict(row)))
            if len(batch) >= 2000:
                writer.write_table(pa.Table.from_pylist(batch, schema=DECISION_SCHEMA))
                total += len(batch)
                batch = []
        if batch:
            writer.write_table(pa.Table.from_pylist(batch, schema=DECISION_SCHEMA))
            total += len(batch)
    finally:
        writer.close()
    return total


def _export_parquet(delta_db: _Path, out_path: _Path, columns: List[str] | None = None) -> int:
    import pyarrow as pa
    import pyarrow.parquet as pq

    cols = columns or DECISION_COLS
    schema = pa.schema([pa.field(c, pa.string()) for c in cols])
    out_path.parent.mkdir(parents=True, exist_ok=True)

    conn = _sqlite3.connect(str(delta_db))
    conn.row_factory = _sqlite3.Row
    writer = pq.ParquetWriter(str(out_path), schema=schema, compression="zstd", use_dictionary=True)
    total = 0
    try:
        col_list = ",".join(cols)
        cur = conn.execute(f"SELECT {col_list} FROM decisions ORDER BY decision_date, id")
        while True:
            rows = cur.fetchmany(5000)
            if not rows:
                break
            batch = {c: [] for c in cols}
            for r in rows:
                for c in cols:
                    batch[c].append(r[c])
            table = pa.Table.from_pydict(batch, schema=schema)
            writer.write_table(table)
            total += table.num_rows
    finally:
        writer.close()
        conn.close()
    return total


# ── Zstd compression ────────────────────────────────────────────────────

def _compress_zst(src: _Path, dst: _Path, level: int = 10) -> None:
    import zstandard as zstd
    dst.parent.mkdir(parents=True, exist_ok=True)
    cctx = zstd.ZstdCompressor(level=level, threads=-1)
    with src.open("rb") as ifh, dst.open("wb") as ofh:
        cctx.copy_stream(ifh, ofh)


# ── Manifest ────────────────────────────────────────────────────────────

def _empty_manifest() -> Dict[str, Any]:
    return {
        "schema": MANIFEST_SCHEMA,
        "generated_at": _utc_now_iso(),
        "snapshot": None,
        "deltas": [],
    }


def _add_delta_to_manifest(m: Dict[str, Any], date: str, sqlite_zst: Dict, parquet: Optional[Dict]) -> Dict[str, Any]:
    m = dict(m)
    m["schema"] = MANIFEST_SCHEMA
    m["generated_at"] = _utc_now_iso()
    entries = [e for e in (m.get("deltas") or []) if e.get("date") != date]
    entries.append({"date": date, "sqlite_zst": sqlite_zst, "parquet": parquet})
    entries.sort(key=lambda e: e.get("date") or "")
    m["deltas"] = entries
    return m


def _set_snapshot_in_manifest(m: Dict[str, Any], snapshot: Dict[str, Any]) -> Dict[str, Any]:
    import copy as _copy
    m = dict(m)
    m["schema"] = MANIFEST_SCHEMA
    m["generated_at"] = _utc_now_iso()
    # Deep-copy so post-call mutation of `snapshot` by the caller can't
    # silently rewrite the manifest we just constructed.
    m["snapshot"] = _copy.deepcopy(snapshot)
    if "deltas" not in m or m["deltas"] is None:
        m["deltas"] = []
    return m


def _download_manifest(hf_repo: str) -> Dict[str, Any]:
    import httpx
    url = f"https://huggingface.co/datasets/{hf_repo}/resolve/main/{MANIFEST_PATH_IN_REPO}"
    try:
        with httpx.Client(follow_redirects=True, timeout=60.0) as client:
            r = client.get(url)
            if r.status_code == 404:
                return _empty_manifest()
            r.raise_for_status()
            return _json.loads(r.text)
    except Exception as e:
        log.warning("manifest download failed (%s); starting fresh", e)
        return _empty_manifest()


# ── HF upload ───────────────────────────────────────────────────────────

def _hf_retryable_exceptions() -> tuple:
    """Transient HF/transport errors worth retrying (issue #20)."""
    excs: list = []
    try:
        from huggingface_hub.utils import HfHubHTTPError
        excs.append(HfHubHTTPError)
    except Exception:
        pass
    try:
        import httpx
        excs.append(httpx.HTTPError)
    except Exception:
        pass
    return tuple(excs) or (Exception,)


def _hf_call_with_retry(call, *args, attempts: int = 3, base_delay: float = 5.0,
                        max_delay: float = 60.0, **kwargs):
    """Call an HF Hub API method with retry + exponential backoff on transient
    HTTP errors (issue #20). A hiccup between the sequential snapshot/checksum/
    manifest uploads otherwise leaves HF in a dirty state (orphan artifact or
    stale manifest). Re-raises the final error so a genuine failure still
    surfaces in publish.log."""
    retryable = _hf_retryable_exceptions()
    for attempt in range(1, attempts + 1):
        try:
            return call(*args, **kwargs)
        except retryable as e:
            if attempt >= attempts:
                raise
            delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
            log.warning("HF call failed (attempt %d/%d): %s — retrying in %.0fs",
                        attempt, attempts, e, delay)
            _time.sleep(delay)


def _upload_file(local: _Path, repo: str, path_in_repo: str, token: str, commit_message: str) -> None:
    from huggingface_hub import HfApi
    api = HfApi(token=token)
    _hf_call_with_retry(
        api.upload_file,
        path_or_fileobj=str(local),
        path_in_repo=path_in_repo,
        repo_id=repo,
        repo_type="dataset",
        commit_message=commit_message,
    )


def _delete_file(repo: str, path_in_repo: str, token: str, commit_message: str) -> None:
    from huggingface_hub import HfApi
    api = HfApi(token=token)
    _hf_call_with_retry(
        api.delete_file,
        path_in_repo=path_in_repo,
        repo_id=repo,
        repo_type="dataset",
        commit_message=commit_message,
    )


def _producer_commit() -> str | None:
    try:
        repo_root = _Path(__file__).resolve().parents[1]
        r = _subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_root),
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        commit = r.stdout.strip()
        return commit or None
    except Exception:
        return None


_REQUIRED_TABLES = ("decisions", "decisions_fts")


def _validate_snapshot_source(db_path: _Path) -> Dict[str, Any]:
    if not db_path.exists():
        raise FileNotFoundError(f"SQLite DB not found: {db_path}")

    stat_before = db_path.stat()
    uri = f"file:{db_path}?mode=ro"
    conn = _sqlite3.connect(uri, uri=True)
    try:
        # Verify the FTS5-rebuild schema is present (catches a snapshot
        # taken against a half-built or non-FTS5 DB before we compress
        # 61 GB and ship it to consumers).
        present = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
            )
        }
        missing = [t for t in _REQUIRED_TABLES if t not in present]
        if missing:
            raise RuntimeError(
                f"snapshot source missing expected table(s) {missing}: {db_path}"
            )
        rows = conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
        sample = conn.execute("SELECT decision_id FROM decisions LIMIT 1").fetchone()
        db_generation = conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()

    if rows <= 0:
        raise RuntimeError(f"snapshot source has no decisions: {db_path}")
    if sample is None or not sample[0]:
        raise RuntimeError(f"snapshot source sample SELECT failed: {db_path}")

    return {
        "rows": rows,
        "sample_decision_id": sample[0],
        "db_generation": db_generation,
        "source_stat": stat_before,
    }


def _assert_snapshot_source_unchanged(db_path: _Path, source_stat: _os.stat_result) -> None:
    current = db_path.stat()
    before = (
        source_stat.st_dev,
        source_stat.st_ino,
        source_stat.st_size,
        source_stat.st_mtime_ns,
    )
    after = (
        current.st_dev,
        current.st_ino,
        current.st_size,
        current.st_mtime_ns,
    )
    if before != after:
        raise RuntimeError(
            f"snapshot source changed while compressing: {db_path}. "
            "Re-run snapshot publish so manifest metadata matches the compressed DB."
        )


def build_sqlite_snapshot(
    *,
    db_path: _Path,
    date: str,
    build_dir: _Path,
    zstd_level: int = 10,
) -> Dict[str, Any]:
    """Compress the full FTS5 SQLite DB as a bootstrap snapshot.

    This intentionally snapshots the live SQLite DB directly instead of
    rebuilding from Parquet. The whole point is to publish the already
    validated DB so local MCP users can avoid repeating that build.
    """
    source_meta = _validate_snapshot_source(db_path)

    _prune_old_build_dirs(build_dir / "snapshot", keep=date)
    work = build_dir / "snapshot" / date
    if work.exists():
        _shutil.rmtree(work)
    work.mkdir(parents=True)

    snapshot_zst = work / f"{date}.decisions.sqlite.zst"
    _compress_zst(db_path, snapshot_zst, level=zstd_level)
    _assert_snapshot_source_unchanged(db_path, source_meta["source_stat"])
    sha256 = _sha256_file(snapshot_zst)

    checksum = work / f"{snapshot_zst.name}.sha256"
    checksum.write_text(f"{sha256}  {snapshot_zst.name}\n", encoding="utf-8")

    info = {
        "date": date,
        "sqlite_zst": snapshot_zst,
        "sqlite_zst_bytes": snapshot_zst.stat().st_size,
        "sqlite_zst_sha256": sha256,
        "checksum": checksum,
        "checksum_bytes": checksum.stat().st_size,
        "rows": source_meta["rows"],
        "sample_decision_id": source_meta["sample_decision_id"],
        "db_generation": source_meta["db_generation"],
        "schema_version": SQLITE_SNAPSHOT_SCHEMA_VERSION,
        "producer_commit": _producer_commit(),
        "work_dir": work,
    }
    log.info(
        "build_sqlite_snapshot: %s rows=%d bytes=%d sha256=%s",
        date, info["rows"], info["sqlite_zst_bytes"], info["sqlite_zst_sha256"],
    )
    return info


def _list_snapshot_paths(hf_repo: str, token: str | None) -> list[str]:
    """All snapshot sqlite paths on HF, sorted oldest→newest (the names are
    date-prefixed, so a lexical sort is chronological). Issue #19."""
    from huggingface_hub import HfApi
    api = HfApi(token=token)
    files = api.list_repo_files(repo_id=hf_repo, repo_type="dataset")
    prefix = "artifacts/sqlite/snapshots/"
    suffix = ".decisions.sqlite.zst"
    return sorted(f for f in files if f.startswith(prefix) and f.endswith(suffix))


def _prune_old_snapshots(hf_repo: str, token: str | None, retain: int = 3) -> list[str]:
    """Keep the `retain` newest snapshots on HF; delete older ones (+ .sha256).

    Replaces the prior "delete the previous snapshot immediately" behaviour,
    which created a 404 race for clients that fetched the old manifest moments
    before the swap (issue #19). Keeping N gives them a grace window without
    growing HF storage unboundedly. Returns the snapshot paths deleted."""
    snaps = _list_snapshot_paths(hf_repo, token)
    to_delete = snaps[:-retain] if retain >= 0 and len(snaps) > retain else []
    for snap in to_delete:
        for path in (snap, f"{snap}.sha256"):
            try:
                _delete_file(
                    hf_repo, path, token,
                    commit_message=f"snapshot retention: prune {path} (keep newest {retain})",
                )
            except Exception as e:
                log.warning("snapshot retention failed for %s: %s", path, e)
    return to_delete


def publish_sqlite_snapshot(
    *,
    build_info: Dict[str, Any],
    hf_repo: str = HF_REPO_ID,
    hf_token: str | None = None,
    dry_run: bool = False,
    prune_previous: bool = True,
    retain_snapshots: int = 3,
) -> None:
    """Upload full SQLite snapshot and update manifest.snapshot.

    Upload order is snapshot first, checksum second, manifest last. That
    keeps consumers from seeing a manifest that references missing files.
    """
    date = build_info["date"]
    hf_token = hf_token or _os.environ.get("HF_TOKEN")
    if not hf_token and not dry_run:
        raise RuntimeError("HF_TOKEN not set")

    sqlite_path_in_repo = f"artifacts/sqlite/snapshots/{date}.decisions.sqlite.zst"
    checksum_path_in_repo = f"{sqlite_path_in_repo}.sha256"

    if dry_run:
        log.info("[dry-run] would upload full SQLite snapshot:")
        log.info("  → %s (%d bytes)", sqlite_path_in_repo, build_info["sqlite_zst_bytes"])
        log.info("  → %s (%d bytes)", checksum_path_in_repo, build_info["checksum_bytes"])
        log.info("  → artifacts/manifest.json (set snapshot entry)")
        return

    manifest = _download_manifest(hf_repo)

    _upload_file(
        build_info["sqlite_zst"],
        hf_repo,
        sqlite_path_in_repo,
        hf_token,
        commit_message=f"snapshot {date} (sqlite)",
    )
    _upload_file(
        build_info["checksum"],
        hf_repo,
        checksum_path_in_repo,
        hf_token,
        commit_message=f"snapshot {date} checksum",
    )

    sqlite_meta = {
        "path": sqlite_path_in_repo,
        "sha256": build_info["sqlite_zst_sha256"],
        "bytes": build_info["sqlite_zst_bytes"],
    }
    snapshot_entry = {
        "date": date,
        "sqlite_zst": sqlite_meta,
        "rows": build_info["rows"],
        "schema_version": build_info["schema_version"],
        "db_generation": build_info["db_generation"],
    }
    if build_info.get("producer_commit"):
        snapshot_entry["producer_commit"] = build_info["producer_commit"]

    manifest = _set_snapshot_in_manifest(manifest, snapshot_entry)
    manifest_local = build_info["work_dir"] / "manifest.json"
    manifest_local.write_text(
        _json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _upload_file(
        manifest_local,
        hf_repo,
        MANIFEST_PATH_IN_REPO,
        hf_token,
        commit_message=f"manifest: set snapshot {date}",
    )

    # Retain the newest N snapshots instead of deleting the previous one
    # immediately — gives clients that fetched the prior manifest a grace
    # window before its snapshot disappears (issue #19).
    if prune_previous:
        _prune_old_snapshots(hf_repo, hf_token, retain=retain_snapshots)

    log.info("publish_sqlite_snapshot: %s published (%d rows)", date, build_info["rows"])


# ── Top-level build + publish ───────────────────────────────────────────

def build_delta(
    *,
    db_path: _Path,
    date: str,
    build_dir: _Path,
    snapshot_path: _Path,
    zstd_level: int = 10,
    update_snapshot: bool = True,
) -> Dict[str, Any]:
    """Build delta artifacts for `date` from the FTS5 `db_path` via
    snapshot-diff. After publishing, the current id-set becomes the new
    snapshot (unless update_snapshot=False, useful for dry-runs).

    Returns dict with local paths + row count.
    """
    _prune_old_build_dirs(build_dir / "delta", keep=date)
    work = build_dir / "delta" / date
    if work.exists():
        _shutil.rmtree(work)
    work.mkdir(parents=True)

    delta_sqlite = work / f"delta-{date}.sqlite"
    _create_delta_db(delta_sqlite)

    log.info("build_delta: scanning FTS5 decision_ids")
    current_ids = snapshot_all_ids(db_path)
    prev_ids = load_snapshot_ids(snapshot_path)
    new_ids = current_ids - prev_ids
    log.info("build_delta: current=%d prev=%d new=%d", len(current_ids), len(prev_ids), len(new_ids))

    log.info("build_delta: fetching %d new decisions", len(new_ids))
    row_count = _bulk_insert(delta_sqlite, iter_decisions_by_ids(db_path, new_ids))
    log.info("build_delta: %d rows inserted", row_count)

    delta_zst = work / f"delta-{date}.sqlite.zst"
    _compress_zst(delta_sqlite, delta_zst, level=zstd_level)

    delta_parquet = work / f"delta-{date}.parquet"
    _export_parquet_base_schema(db_path, new_ids, delta_parquet)

    data_parquet = work / f"delta-{date}-data.parquet"
    _export_parquet(delta_sqlite, data_parquet, columns=BASE_COLS)

    return {
        "date": date,
        "rows": row_count,
        "sqlite_zst": delta_zst,
        "sqlite_zst_bytes": delta_zst.stat().st_size,
        "sqlite_zst_sha256": _sha256_file(delta_zst),
        "parquet": delta_parquet,
        "parquet_bytes": delta_parquet.stat().st_size,
        "parquet_sha256": _sha256_file(delta_parquet),
        "data_parquet": data_parquet,
        "data_parquet_bytes": data_parquet.stat().st_size,
        "work_dir": work,
        "current_ids": current_ids,
        "snapshot_path": snapshot_path,
        "update_snapshot": update_snapshot,
    }


def publish_delta(
    *,
    build_info: Dict[str, Any],
    hf_repo: str = HF_REPO_ID,
    hf_token: str | None = None,
    dry_run: bool = False,
) -> None:
    """Upload delta artifacts + update manifest. When dry_run, only logs."""
    date = build_info["date"]
    hf_token = hf_token or _os.environ.get("HF_TOKEN")
    if not hf_token and not dry_run:
        raise RuntimeError("HF_TOKEN not set")

    sqlite_path_in_repo = f"artifacts/sqlite/deltas/{date}.sqlite.zst"
    parquet_path_in_repo = f"artifacts/parquet/deltas/{date}.parquet"
    data_path_in_repo = f"data/delta-{date}.parquet"

    if build_info["rows"] == 0:
        log.info("publish_delta: 0 rows for %s — nothing to upload", date)
        return

    if dry_run:
        log.info("[dry-run] would upload:")
        log.info("  → %s (%d bytes)", sqlite_path_in_repo, build_info["sqlite_zst_bytes"])
        log.info("  → %s (%d bytes)", parquet_path_in_repo, build_info["parquet_bytes"])
        log.info("  → %s (%d bytes)", data_path_in_repo, build_info["data_parquet_bytes"])
        log.info("  → artifacts/manifest.json (add delta entry)")
        return

    # Upload artifacts in dependency order (manifest LAST so consumers never
    # see a manifest referencing files that haven't been uploaded yet).
    _upload_file(build_info["sqlite_zst"], hf_repo, sqlite_path_in_repo, hf_token,
                 commit_message=f"delta {date} (sqlite)")
    _upload_file(build_info["parquet"], hf_repo, parquet_path_in_repo, hf_token,
                 commit_message=f"delta {date} (parquet)")
    _upload_file(build_info["data_parquet"], hf_repo, data_path_in_repo, hf_token,
                 commit_message=f"daily delta {date}")

    manifest = _download_manifest(hf_repo)
    sqlite_meta = {
        "path": sqlite_path_in_repo,
        "sha256": build_info["sqlite_zst_sha256"],
        "bytes": build_info["sqlite_zst_bytes"],
    }
    parquet_meta = {
        "path": parquet_path_in_repo,
        "sha256": build_info["parquet_sha256"],
        "bytes": build_info["parquet_bytes"],
    }
    manifest = _add_delta_to_manifest(manifest, date, sqlite_meta, parquet_meta)
    manifest_local = build_info["work_dir"] / "manifest.json"
    manifest_local.write_text(_json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                              encoding="utf-8")
    _upload_file(manifest_local, hf_repo, MANIFEST_PATH_IN_REPO, hf_token,
                 commit_message=f"manifest: add delta {date}")
    log.info("publish_delta: %s published (%d rows)", date, build_info["rows"])

    # Persist new snapshot AFTER successful HF publish — so a mid-upload
    # failure means tomorrow's delta will re-try the same IDs.
    if build_info.get("update_snapshot"):
        save_snapshot_ids(build_info["snapshot_path"],
                          build_info["current_ids"], date)
        log.info("publish_delta: snapshot saved (%d ids)", len(build_info["current_ids"]))


# ── CLI ─────────────────────────────────────────────────────────────────

def seed_snapshot(db_path: _Path, snapshot_path: _Path, date: str | None = None) -> int:
    """Save the current FTS5 id-set to the snapshot file without uploading
    anything. Used once before the pipeline goes live, so the first real
    run emits a small genuine delta instead of re-publishing the whole
    corpus. Safe to re-run (idempotent — overwrites snapshot)."""
    date = date or _dt.date.today().isoformat()
    ids = snapshot_all_ids(db_path)
    save_snapshot_ids(snapshot_path, ids, date)
    log.info("seed_snapshot: saved %d ids to %s", len(ids), snapshot_path)
    return len(ids)


def _cli() -> None:
    import argparse
    p = argparse.ArgumentParser(description="Daily delta publisher for voilaj/swiss-caselaw")
    p.add_argument("--db", type=_Path,
                   default=_Path("/opt/caselaw/repo/output/decisions.db"),
                   help="Path to FTS5 decisions.db")
    p.add_argument("--date", type=str,
                   default=_dt.date.today().isoformat(),
                   help="YYYY-MM-DD (UTC day to publish delta for). Default: today.")
    p.add_argument("--build-dir", type=_Path,
                   default=_Path("/mnt/HC_Volume_104655575/caselaw_delta_build"),
                   help="Scratch directory for build artifacts (on the data "
                        "volume, not root /tmp — keeps root-disk pressure off "
                        "the nightly publish; bounded by BUILD_DIR_RETENTION)")
    p.add_argument("--snapshot", type=_Path,
                   default=_Path("/opt/caselaw/repo/state/hf_delta_snapshot.json"),
                   help="Path to local id-snapshot state file")
    p.add_argument("--dry-run", action="store_true",
                   help="Build locally; do NOT upload to HF, do NOT update snapshot")
    p.add_argument("--publish-snapshot", action="store_true",
                   help="Also publish a full compressed SQLite base snapshot "
                        "and set manifest.snapshot.")
    p.add_argument("--snapshot-only", action="store_true",
                   help="Publish only the full SQLite snapshot; skip daily delta build. "
                        "Useful for a one-off base snapshot.")
    p.add_argument("--keep-previous-snapshot", action="store_true",
                   help="Do not delete the previously referenced full SQLite snapshot "
                        "after manifest has been updated.")
    p.add_argument("--seed", action="store_true",
                   help="Save current FTS5 id-set as snapshot and exit — no delta build, no HF upload. "
                        "Run this ONCE before enabling the pipeline so the first real delta is a "
                        "real incremental set, not the entire corpus.")
    p.add_argument("--hf-repo", default=HF_REPO_ID)
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    _logging.basicConfig(
        level=_logging.DEBUG if args.verbose else _logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    if args.seed:
        n = seed_snapshot(args.db, args.snapshot, args.date)
        log.info("seed complete (%d ids). Next real run will diff against this.", n)
        return

    if not args.snapshot_only:
        info = build_delta(
            db_path=args.db,
            date=args.date,
            build_dir=args.build_dir,
            snapshot_path=args.snapshot,
            update_snapshot=not args.dry_run,
        )
        summary = {k: v for k, v in info.items()
                   if k in ("date", "rows", "sqlite_zst_bytes", "parquet_bytes", "data_parquet_bytes")}
        summary["current_ids"] = len(info["current_ids"])
        log.info("build summary: %s", summary)
        publish_delta(build_info=info, hf_repo=args.hf_repo, dry_run=args.dry_run)

    if args.publish_snapshot or args.snapshot_only:
        snap = build_sqlite_snapshot(
            db_path=args.db,
            date=args.date,
            build_dir=args.build_dir,
        )
        snap_summary = {
            k: v for k, v in snap.items()
            if k in ("date", "rows", "sqlite_zst_bytes", "sqlite_zst_sha256", "db_generation")
        }
        log.info("snapshot build summary: %s", snap_summary)
        publish_sqlite_snapshot(
            build_info=snap,
            hf_repo=args.hf_repo,
            dry_run=args.dry_run,
            prune_previous=not args.keep_previous_snapshot,
        )


if __name__ == "__main__":
    _cli()
