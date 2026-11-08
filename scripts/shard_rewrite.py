"""Atomic rewrite of a JSONL shard, for the dry-run-first repair scripts.

The convention of scripts/repair_sh_markup.py, shared: write ``<shard>.tmp``
(no build glob matches it), check the line count, refuse when the shard
changed while the repair ran (a scrape appending), hard-link a backup
``<shard>.bak-<tag>-<UTC date>`` beside it and swap the new file in with
os.replace, so the build never sees a half-written shard.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


def read_shard(path: Path) -> tuple[list[dict], os.stat_result]:
    """Rows of a shard (blank lines dropped) and its stat for the race check.
    Refuses a shard with an unparseable line: a repair must never drop one."""
    st = os.stat(path)
    rows = []
    with open(path, "rb") as fh:
        for n, raw in enumerate(fh, 1):
            if not raw.strip():
                continue
            try:
                rows.append(json.loads(raw))
            except ValueError as e:
                raise SystemExit(f"refusing: {path} line {n} is not JSON ({e})")
    return rows, st


def write_shard(path: Path, rows: list[dict], before: os.stat_result, tag: str) -> Path:
    """Replace ``path`` by ``rows`` atomically; returns the backup path.
    ``before`` is the stat taken when the shard was read."""
    tmp = path.with_name(path.name + ".tmp")
    try:
        with open(tmp, "wb") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False).encode("utf-8") + b"\n")
            fh.flush()
            os.fsync(fh.fileno())
        with open(tmp, "rb") as fh:
            written = sum(1 for _ in fh)
        if written != len(rows):
            raise SystemExit(f"refusing: wrote {written} lines for {len(rows)} rows")
        now = os.stat(path)
        if (now.st_size, now.st_mtime_ns) != (before.st_size, before.st_mtime_ns):
            raise SystemExit(f"refusing: {path} changed while the repair ran (a scrape?)")
        stamp = time.strftime("%Y%m%d", time.gmtime())
        backup = path.with_name(f"{path.name}.bak-{tag}-{stamp}")
        if backup.exists():
            raise SystemExit(f"refusing to overwrite existing backup {backup}")
        os.link(path, backup)
        os.chmod(tmp, before.st_mode & 0o7777)
        os.replace(tmp, path)
        print(f"rewrote {path} ({len(rows)} rows); backup {backup}", file=sys.stderr)
        return backup
    finally:
        if tmp.exists():
            tmp.unlink()


def append_quarantine(path: Path, rows: list[dict]) -> None:
    """Append rows to a quarantine file (created if missing). The file is
    named ``*.quarantined.jsonl.txt`` by the callers so no ``*.jsonl`` glob
    of the build ever picks it up."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "ab") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False).encode("utf-8") + b"\n")
        fh.flush()
        os.fsync(fh.fileno())
