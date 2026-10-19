"""Atomic shard writes for the custom (non-OAI) scholarship harvesters.

The custom modules used to open the LIVE shard with ``open("w")`` and stream
into it. Two things then destroyed good data:

* the weekly pipeline harvest runs under one shared wall-clock cap and is
  killed with SIGTERM when it runs out; the module in flight is left with a
  truncated shard (2026-09-13: ``anci_ch.jsonl`` went from 100 records to 0
  bytes and the next two DB builds served none of them);
* per-item fetch errors are swallowed, so an upstream outage produces a short
  but "successful" run that overwrites the full shard.

``atomic_jsonl`` gives them the contract the OAI harvester already has: write
``<shard>.tmp``, and replace the live shard only when the block completes
normally AND the new file is not suspiciously smaller than the old one. A kill
never reaches the replace, so the live shard stays as it was.
"""
from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Iterator

log = logging.getLogger("scholarship.atomic")

# A harvest that returns fewer than this share of the previous record count is
# treated as an upstream failure, not as the source shrinking.
MIN_KEEP_FRACTION = 0.9


def _count_lines(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("rb") as fh:
        return sum(1 for line in fh if line.strip())


@contextmanager
def atomic_jsonl(out_path: Path, *,
                 min_keep_fraction: float = MIN_KEEP_FRACTION) -> Iterator[IO[str]]:
    out_path = Path(out_path)
    tmp_path = out_path.with_name(out_path.name + ".tmp")
    fh = tmp_path.open("w", encoding="utf-8")
    try:
        yield fh
    except BaseException:
        fh.close()
        log.error("%s: harvest interrupted — live shard left untouched, partial in %s",
                  out_path.name, tmp_path.name)
        raise
    fh.close()

    new = _count_lines(tmp_path)
    old = _count_lines(out_path)
    if old > 0 and new < old * min_keep_fraction:
        keep_as = out_path.with_name(out_path.name + ".shrunk")
        os.replace(tmp_path, keep_as)
        log.error("%s: harvest returned %d records, previous shard has %d "
                  "(< %.0f%%) — keeping the previous shard; new result saved to %s",
                  out_path.name, new, old, min_keep_fraction * 100, keep_as.name)
        return
    os.replace(tmp_path, out_path)
