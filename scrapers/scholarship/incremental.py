"""Incremental, budgeted, resumable OAI-PMH harvesting for the weekly run.

Why (2026-10-11): publish step 2h gives harvest_all one 3600 s wall-clock cap,
and harvest_all used to re-walk every source in full, serially. A full walk of
the 25 sources takes 4+ hours (UNIGE ~68 min, ZORA ~73 min, BORIS ~39 min), so
the cap killed the run inside unige_law every Sunday and the twelve sources
registered after it were never harvested at all. A week of change, by
contrast, is a few pages per source: OAI `from=` returned 159 UNIGE, 245 ZORA
and ~1,000 BORIS records for the week to 2026-10-11.

So each OAI source keeps a small state file and runs one of two operations:

* incremental — ListRecords from the high-water mark (minus a day of overlap)
  to tomorrow, merged into the existing shard: new and changed records are
  upserted, records the server reports deleted or that no longer pass the
  subject filter are removed, nothing else is touched;
* full — the whole repository (the source's own window plan), installed as a
  replacement so records gone upstream disappear. Due every
  FULL_EVERY_DAYS (default 90), staggered per source.

Either operation runs under a deadline. When the deadline passes, the window
stack still to walk is saved with the partial output, whatever was harvested
so far is merged into the shard (merging never drops a record), and the next
run resumes from that stack. Windowed sources therefore progress through a
repository of any size; a non-windowed chain can only restart.

The high-water mark is the START of the operation that completed (our clock,
less a day of overlap), never the newest record datestamp: UNIGE's `from`
filters on an internal modification date while its headers carry older
datestamps, and a multi-week full walk must not miss what changed while it ran.

Gapped windows (oai_pmh's day-atomic fallback) are carried as retry windows
into the next incremental run, up to MAX_RETRY_ATTEMPTS, instead of forcing
another full walk.

State lives in output/legal_scholarship/.harvest_state/<source>.json and the
partial output in <source>.jsonl.partial — neither matches the builder's
*.jsonl glob.
"""
from __future__ import annotations

import json
import logging
import os
import time
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import oai_pmh

log = logging.getLogger("scholarship.incremental")

STATE_DIRNAME = ".harvest_state"
FULL_EVERY_DAYS = int(os.environ.get("OCL_SCHOLARSHIP_FULL_EVERY_DAYS", "90"))
OVERLAP = timedelta(days=1)
MAX_RETRY_ATTEMPTS = 3
# A full harvest that would shrink the shard by more than this share is
# merged instead of replacing it (same threshold as the custom harvesters'
# atomic writer): an upstream outage must not read as mass deletion.
MIN_KEEP_FRACTION = 0.9
_CHECKPOINT_EVERY_S = 30.0


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(s: str) -> datetime:
    if "T" not in s:
        return datetime.fromisoformat(s[:10]).replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


# ── state ───────────────────────────────────────────────────────────────

def state_path(output_dir: Path, key: str) -> Path:
    return output_dir / STATE_DIRNAME / f"{key}.json"


def load_state(output_dir: Path, key: str) -> dict:
    p = state_path(output_dir, key)
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        log.error("%s: unreadable harvest state %s (%s) — starting over",
                  key, p, e)
        return {}


def save_state(output_dir: Path, key: str, state: dict) -> None:
    p = state_path(output_dir, key)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(state, indent=1, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    os.replace(tmp, p)


def last_success(output_dir: Path, key: str) -> str:
    """When this source last completed a harvest, for staleness ordering.

    Falls back to the shard's mtime before the first stateful run; "" (the
    most stale) when there is neither."""
    st = load_state(output_dir, key)
    if st.get("last_success"):
        return st["last_success"]
    shard = output_dir / f"{key}.jsonl"
    if shard.exists():
        return _iso(datetime.fromtimestamp(shard.stat().st_mtime, timezone.utc))
    return ""


def _seed(state: dict, shard: Path, key: str, now: datetime,
          full_every_days: int) -> None:
    """Adopt a shard harvested before state files existed.

    The high-water mark becomes the shard's mtime less a day: whatever the
    shard holds was harvested by then. Whether that harvest was COMPLETE is
    unknowable here, so the first full re-harvest is scheduled within one
    refresh period, staggered by source key so the big repositories don't all
    fall due in the same week. A `.jsonl.aborted` newer than the shard says
    the last full attempt failed — that source's full is due now.
    """
    mtime = datetime.fromtimestamp(shard.stat().st_mtime, timezone.utc)
    state["high_water"] = _iso(min(mtime, now) - OVERLAP)
    state["seeded_from_shard_mtime"] = _iso(mtime)
    aborted = shard.with_name(shard.name + ".aborted")
    if aborted.exists() and aborted.stat().st_mtime > shard.stat().st_mtime:
        state["next_full_after"] = now.date().isoformat()
    else:
        stagger = zlib.crc32(key.encode()) % max(1, full_every_days)
        state["next_full_after"] = (now + timedelta(days=stagger)).date().isoformat()
    log.info("%s: no harvest state — seeded high-water %s from the shard; "
             "first full re-harvest after %s", key, state["high_water"],
             state["next_full_after"])


# ── shard install ───────────────────────────────────────────────────────

def _repair_tail(path: Path) -> None:
    """Drop a half-written last line (the process was killed mid-write)."""
    if not path.exists() or path.stat().st_size == 0:
        return
    with path.open("rb+") as fh:
        fh.seek(-1, os.SEEK_END)
        if fh.read(1) == b"\n":
            return
        fh.seek(0)
        fh.truncate(fh.read().rfind(b"\n") + 1)


def _iter_jsonl(path: Path):
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line), line
            except ValueError:
                continue


def _partial_ids(path: Path) -> tuple[set[str], int]:
    """Record ids already written to a partial file, and how many records."""
    ids: set[str] = set()
    n = 0
    if not path.exists():
        return ids, n
    for d, _ in _iter_jsonl(path):
        if "_removed" in d:
            continue
        n += 1
        rid = d.get("source_record_id")
        if rid:
            ids.add(rid)
    return ids, n


def install(shard: Path, partial: Path, *, replace: bool) -> dict:
    """Write `partial` into `shard` atomically.

    replace=False merges: the existing shard is the base, partial records are
    upserted by source_record_id (in place, so shard order is stable), and
    `{"_removed": id}` markers delete. replace=True starts from an empty base
    — unless that would drop more than 1 - MIN_KEEP_FRACTION of the shard,
    in which case it merges instead and says so.
    """
    existing = 0
    base: dict[str, str] = {}
    if shard.exists():
        for i, (d, line) in enumerate(_iter_jsonl(shard)):
            existing += 1
            base[d.get("source_record_id") or f"\0noid{i}"] = line
    fresh: dict[str, str] = {}
    for i, (d, line) in enumerate(_iter_jsonl(partial)):
        if "_removed" in d:
            fresh.pop(d["_removed"], None)
            continue
        fresh[d.get("source_record_id") or f"\0new{i}"] = line

    mode = "merge"
    if replace:
        if existing and len(fresh) < existing * MIN_KEEP_FRACTION:
            log.error("%s: full harvest returned %d records, shard has %d "
                      "(< %.0f%%) — merging instead of replacing",
                      shard.name, len(fresh), existing,
                      MIN_KEEP_FRACTION * 100)
        else:
            mode = "replace"

    if mode == "replace":
        out = dict(fresh)
    else:
        out = dict(base)
        # Replay in file order so a later line for the same id wins.
        for i, (d, line) in enumerate(_iter_jsonl(partial)):
            if "_removed" in d:
                out.pop(d["_removed"], None)
            else:
                out[d.get("source_record_id") or f"\0new{i}"] = line
    added = sum(1 for k in out if k not in base)
    updated = sum(1 for k in out if k in base and out[k] != base[k])
    dropped = sum(1 for k in base if k not in out)

    tmp = shard.with_name(shard.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for line in out.values():
            fh.write(line + "\n")
    os.replace(tmp, shard)
    return {"mode": mode, "before": existing, "after": len(out),
            "added": added, "updated": updated, "removed": dropped}


# ── one source ──────────────────────────────────────────────────────────

def _retry_list(retried: list[dict], gaps: list[dict],
                key: str) -> list[dict]:
    """Next run's retry windows: this run's gaps, counting repeat failures."""
    tried = {tuple(r["window"]): r.get("attempts", 1) for r in retried}
    out: list[dict] = []
    for g in gaps:
        w = tuple(g["window"])
        attempts = tried.get(w, 0) + 1
        if attempts > MAX_RETRY_ATTEMPTS:
            log.error("%s window [%s..%s]: still failing after %d attempts "
                      "(%s) — dropped until the next full re-harvest",
                      key, w[0], w[1], attempts - 1, g.get("reason"))
            continue
        out.append({"window": list(w), "attempts": attempts,
                    "reason": g.get("reason")})
    return out


def _source_kwargs(src) -> dict:
    license_override = None
    if src.license_authoritative and src.license_default:
        license_override = (src.license_default, src.license_url_default or "")
    return {
        "set_spec": src.set_spec,
        "metadata_prefix": src.metadata_prefix,
        "rate_limit": src.rate_limit,
        "subject_filter": list(src.subject_filter) if src.subject_filter else None,
        "license_override": license_override,
    }


def harvest_source(
    src,
    *,
    deadline: float | None = None,
    output_dir: Path = oai_pmh.DEFAULT_OUT,
    now: datetime | None = None,
    full_every_days: int = FULL_EVERY_DAYS,
    fetch_timeout: int = 180,
    max_window_records: int = oai_pmh.MAX_WINDOW_RECORDS,
    user_agent: str = "OpenCaseLaw-scholarship/0.1 (+https://opencaselaw.ch)",
) -> dict:
    """Run (or resume) this source's due operation until done or `deadline`
    (a time.monotonic() value). Returns a summary; `pending` is True when
    work remains for the next run."""
    now = now or datetime.now(timezone.utc)
    key = src.key
    output_dir.mkdir(parents=True, exist_ok=True)
    shard = output_dir / f"{key}.jsonl"
    partial = output_dir / f"{key}.jsonl.partial"
    started = time.monotonic()
    state = load_state(output_dir, key)

    pending = state.get("pending")
    if pending and not partial.exists():
        log.warning("%s: pending %s has no partial file — starting it over",
                    key, pending.get("op"))
        pending = None

    resumed = pending is not None
    if pending is None:
        if (not state.get("high_water") and not state.get("last_full")
                and shard.exists() and shard.stat().st_size > 0):
            _seed(state, shard, key, now, full_every_days)
        tomorrow = (now + timedelta(days=1)).date().isoformat()
        full_due = (not state.get("high_water")
                    or (state.get("next_full_after") or "") <= now.date().isoformat())
        if full_due:
            op = "full"
            stack = oai_pmh._window_plan(src.windowed, None,
                                         tomorrow if src.windowed else None)
            retried: list[dict] = []
        else:
            op = "incremental"
            frm = (_parse(state["high_water"]) - OVERLAP).date().isoformat()
            stack = [(frm, tomorrow, 0, None)]
            retried = list(state.get("retry_windows") or [])
            # LIFO: retry windows go on top and are walked first.
            stack += [(r["window"][0], r["window"][1], 0, None) for r in retried]
        pending = {"op": op, "started_at": _iso(now),
                   "stack": [list(w) for w in stack], "gaps": [],
                   "retried": retried, "runs": 0}
        partial.write_bytes(b"")
        state["pending"] = pending
        save_state(output_dir, key, state)
    op = pending["op"]
    pending["runs"] = pending.get("runs", 0) + 1
    log.info("%s: %s %s (started %s, run %d, %d window(s) queued)", key,
             "resuming" if resumed else "starting", op, pending["started_at"],
             pending["runs"], len(pending["stack"]))

    _repair_tail(partial)
    seen, _ = _partial_ids(partial)
    prior_gaps = list(pending.get("gaps") or [])
    # Incremental runs are always windowed: a delta that outgrows its budget
    # must resume from a window, not restart a chain whose token is dead.
    windowed = True if op == "incremental" else src.windowed

    walk = oai_pmh._Walk(
        src.base_url, key, windowed=windowed,
        max_window_records=max_window_records, fetch_timeout=fetch_timeout,
        user_agent=user_agent, deadline=deadline, record_removals=True,
        seen_ids=seen, newest_first=True, **_source_kwargs(src))
    last_ck = [time.monotonic()]

    with partial.open("a", encoding="utf-8") as fh:
        def _checkpoint(w) -> None:
            if time.monotonic() - last_ck[0] < _CHECKPOINT_EVERY_S:
                return
            fh.flush()
            pending["stack"] = [list(x) for x in w.stack]
            pending["gaps"] = prior_gaps + w.gaps
            state["pending"] = pending
            save_state(output_dir, key, state)
            last_ck[0] = time.monotonic()
        walk.checkpoint = _checkpoint
        walk.run(fh, [tuple(w) for w in pending["stack"]])

    pending["stack"] = [list(w) for w in walk.stack]
    gaps = prior_gaps + walk.gaps
    pending["gaps"] = gaps
    _, op_records = _partial_ids(partial)
    summary = {
        "source": key, "op": op, "resumed": resumed,
        "started_at": pending["started_at"], "pages": walk.pages,
        "records": walk.total, "removal_markers": walk.removed,
        "windows": walk.windows_done, "gaps": walk.gaps,
    }

    if walk.interrupted:
        result = install(shard, partial, replace=False)
        state["pending"] = pending
        state["last_run"] = _iso(now)
        save_state(output_dir, key, state)
        summary.update(pending=True, windows_left=len(pending["stack"]),
                       install=result,
                       elapsed_seconds=round(time.monotonic() - started, 1))
        log.info("%s: %s paused with %d window(s) left; progress merged: %s",
                 key, op, len(pending["stack"]), result)
        return summary

    # Operation finished. A non-windowed chain that died is `aborted`; a
    # windowed walk that only produced gaps is a wholesale failure too.
    aborted = (bool(gaps) and op_records == 0) if windowed else walk.aborted
    clean = not aborted and not gaps
    result = install(shard, partial, replace=(op == "full" and clean))
    partial.unlink()
    state["pending"] = None
    state["last_run"] = _iso(now)
    outcome = "aborted" if aborted else ("gapped" if gaps else "clean")
    state["last_outcome"] = {"op": op, "outcome": outcome, "at": _iso(now),
                             "gaps": len(gaps), "install": result}
    if not aborted:
        state["high_water"] = pending["started_at"]
        state["last_success"] = _iso(now)
        state["retry_windows"] = _retry_list(
            pending.get("retried") or [], gaps, key)
        if op == "full":
            state["last_full"] = _iso(now)
            state["next_full_after"] = (
                now + timedelta(days=full_every_days)).date().isoformat()
    save_state(output_dir, key, state)
    summary.update(pending=False, outcome=outcome, install=result,
                   high_water=state.get("high_water"),
                   elapsed_seconds=round(time.monotonic() - started, 1))
    log.info("Done %s: %s %s — %s", key, op, outcome, result)
    return summary
