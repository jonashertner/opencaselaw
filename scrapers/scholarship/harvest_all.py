#!/usr/bin/env python3
"""Run all active OA legal scholarship harvesters.

Walks the source registry in `scrapers/scholarship/sources.py` and dispatches
each active source either to the OAI-PMH harvester or to a custom adapter
module.

The run is budgeted (2026-10-11). It used to walk the sources serially with
no budget of its own while publish step 2h killed it at 3600 s, so the source
in flight at the cap (unige_law, every week) and every source after it were
never refreshed. Now:

* OAI sources harvest incrementally and resumably (see incremental.py);
* sources run least-recently-refreshed first;
* pass 1 gives every source a fair share of what is left of the budget
  (remaining / sources still to go), so whatever one source does, every
  other source gets its turn;
* pass 2 splits what remains among the OAI sources that paused with work
  left; they resume from their saved window stack next week if needed;
* custom adapters run in a child process killed at their share (at least
  CUSTOM_MIN_S): they cannot checkpoint, and they write atomically, so a
  kill leaves their live shard as it was.

The default budget (OCL_SCHOLARSHIP_BUDGET_S, 3300 s) ends the run before the
publish step's 3600 s cap.

Usage:
    python -m scrapers.scholarship.harvest_all
    python -m scrapers.scholarship.harvest_all --only sui_generis
    python -m scrapers.scholarship.harvest_all --budget-seconds 0   # unbudgeted
    python -m scrapers.scholarship.harvest_all --max-records 50     # smoke test
"""
from __future__ import annotations

import argparse
import importlib
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from . import incremental, oai_pmh
from .sources import active_sources, by_key

log = logging.getLogger("scholarship.harvest_all")

DEFAULT_BUDGET_S = int(os.environ.get("OCL_SCHOLARSHIP_BUDGET_S", "3300"))
# Below this, a source's turn is skipped rather than started.
MIN_SLICE_S = 15
# Custom adapters can't checkpoint; give them room to finish (leges takes
# ~6 min) even when the fair share is smaller.
CUSTOM_MIN_S = 600


def run_source(src, *, max_records: int | None = None) -> dict:
    """One full, unbudgeted harvest (smoke tests and the custom child)."""
    if src.kind == "oai_pmh":
        if not src.base_url:
            return {"source": src.key, "skipped": "no base_url"}
        license_override = None
        if src.license_authoritative and src.license_default:
            license_override = (src.license_default, src.license_url_default or "")
        return oai_pmh.harvest(
            src.base_url,
            src.key,
            set_spec=src.set_spec,
            metadata_prefix=src.metadata_prefix,
            rate_limit=src.rate_limit,
            windowed=src.windowed,
            max_records=max_records,
            subject_filter=list(src.subject_filter) if src.subject_filter else None,
            license_override=license_override,
        )
    if src.kind == "custom":
        if not src.custom_module:
            return {"source": src.key, "skipped": "no custom_module"}
        try:
            mod = importlib.import_module(src.custom_module)
        except ModuleNotFoundError:
            return {"source": src.key, "skipped": f"module {src.custom_module} not yet implemented"}
        if not hasattr(mod, "harvest"):
            return {"source": src.key, "skipped": "custom_module has no harvest()"}
        return mod.harvest(max_records=max_records)
    return {"source": src.key, "skipped": f"unknown kind {src.kind}"}


def _run_custom(src, timeout: float | None, output_dir: Path) -> dict:
    """A custom adapter in a child process, killed at `timeout` seconds."""
    cmd = [sys.executable, "-m", "scrapers.scholarship.harvest_all",
           "--only", src.key, "--in-process"]
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, text=True,
                              timeout=timeout, check=False,
                              cwd=str(oai_pmh.REPO_ROOT))
    except subprocess.TimeoutExpired:
        log.error("%s: custom harvest killed after %.0f s (its share of the "
                  "budget) — live shard unchanged", src.key, timeout or 0)
        return {"source": src.key, "error": f"timeout after {timeout:.0f}s"}
    try:
        out = proc.stdout
        summary = json.loads(out[out.rindex('{\n  "summaries"'):])["summaries"][0]
    except (ValueError, KeyError, IndexError):
        summary = {"source": src.key,
                   "error": f"child exited {proc.returncode} without a summary"}
    if proc.returncode == 0 and "error" not in summary and _skip_reason(summary) is None:
        st = incremental.load_state(output_dir, src.key)
        st["last_success"] = incremental._iso(datetime.now(timezone.utc))
        incremental.save_state(output_dir, src.key, st)
    return summary


def _run_one(src, slice_end: float | None, global_end: float | None,
             output_dir: Path) -> dict:
    if src.kind == "oai_pmh":
        if not src.base_url:
            return {"source": src.key, "skipped": "no base_url"}
        return incremental.harvest_source(src, deadline=slice_end,
                                          output_dir=output_dir)
    if src.kind == "custom":
        timeout = None
        if slice_end is not None and global_end is not None:
            now = time.monotonic()
            timeout = min(max(slice_end - now, CUSTOM_MIN_S), global_end - now)
        return _run_custom(src, timeout, output_dir)
    return {"source": src.key, "skipped": f"unknown kind {src.kind}"}


def _skip_reason(summary: dict) -> str | None:
    """Why harvest_all skipped a source, or None if it did not.

    harvest_all writes "skipped" as a reason string. A custom adapter can use
    the same key for a count of records it passed over (thegoodboard does),
    which says nothing about whether the source itself ran.
    """
    reason = summary.get("skipped")
    return reason if isinstance(reason, str) else None


def _safe(fn, src, *args) -> dict:
    try:
        return fn(src, *args)
    except Exception as e:
        log.exception("Harvest failed for %s", src.key)
        return {"source": src.key, "error": str(e)}


def schedule(targets: list, *, budget_s: float,
             output_dir: Path = oai_pmh.DEFAULT_OUT) -> list[dict]:
    """Harvest `targets` within `budget_s` seconds (<= 0: unbudgeted)."""
    order = {s.key: i for i, s in enumerate(targets)}
    targets = sorted(targets, key=lambda s: (
        incremental.last_success(output_dir, s.key), order[s.key]))
    start = time.monotonic()
    end = start + budget_s if budget_s > 0 else None
    summaries: dict[str, dict] = {}

    def _turn(src, n_left: int) -> dict | None:
        if end is None:
            return _safe(_run_one, src, None, None, output_dir)
        left = end - time.monotonic()
        if left < MIN_SLICE_S:
            return None
        share = left / n_left
        log.info("=== Harvest: %s (%s) — %.0f s of %.0f s left ===",
                 src.key, src.kind, share, left)
        return _safe(_run_one, src, time.monotonic() + share, end, output_dir)

    unfinished = []
    for i, src in enumerate(targets):
        if end is None:
            log.info("=== Harvest: %s (%s) ===", src.key, src.kind)
        s = _turn(src, len(targets) - i)
        if s is None:
            s = {"source": src.key, "skipped": "budget exhausted"}
            log.warning("%s: skipped — budget exhausted", src.key)
        summaries[src.key] = s
        if s.get("pending"):
            unfinished.append(src)

    for j, src in enumerate(unfinished):
        log.info("=== Continue: %s ===", src.key)
        s = _turn(src, len(unfinished) - j)
        if s is None:
            break
        first = summaries[src.key]
        s["earlier_turn"] = {k: first.get(k) for k in
                             ("pages", "records", "elapsed_seconds")}
        summaries[src.key] = s

    for src in targets:
        s = summaries[src.key]
        if s.get("error"):
            status = "ERROR " + s["error"]
        elif _skip_reason(s):
            status = "skipped: " + _skip_reason(s)
        elif s.get("pending"):
            status = f"{s['op']} paused, {s['windows_left']} window(s) left"
        elif "op" in s:
            status = f"{s['op']} {s['outcome']}"
        else:
            status = "done"
        inst = s.get("install") or {}
        log.info("  %-24s %-40s %s", src.key, status,
                 f"+{inst['added']} ~{inst['updated']} -{inst['removed']} "
                 f"= {inst['after']}" if inst else "")
    log.info("scholarship harvest: %.0f s", time.monotonic() - start)
    return [summaries[s.key] for s in targets]


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--only", default=None,
                   help="Run only the named source (e.g. 'sui_generis')")
    p.add_argument("--max-records", type=int, default=None,
                   help="Cap records per source (smoke tests: full, "
                        "unbudgeted, stateless harvest)")
    p.add_argument("--budget-seconds", type=float, default=DEFAULT_BUDGET_S,
                   help="Wall-clock budget for the whole run; 0 = none "
                        f"(default {DEFAULT_BUDGET_S}, env OCL_SCHOLARSHIP_BUDGET_S)")
    p.add_argument("--in-process", action="store_true",
                   help=argparse.SUPPRESS)      # the custom-adapter child
    args = p.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    targets = []
    if args.only:
        s = by_key(args.only)
        if s is None:
            log.error("Unknown source key: %s", args.only)
            return 2
        targets = [s]
    else:
        targets = active_sources()

    if not targets:
        log.warning("No active sources configured.")
        return 0

    if args.max_records is not None or args.in_process:
        summaries = []
        for src in targets:
            log.info("=== Harvest: %s (%s) ===", src.key, src.kind)
            summaries.append(_safe(
                lambda x: run_source(x, max_records=args.max_records), src))
    else:
        summaries = schedule(targets, budget_s=args.budget_seconds)

    print(json.dumps({"summaries": summaries}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
