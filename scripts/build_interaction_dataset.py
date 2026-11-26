#!/usr/bin/env python3
"""Join impressions to their fetches → the reranker training dataset.

Capture gives us three raw streams (all in research_logs/capture_*.jsonl,
all carrying the session id during the evaluation period):

  * impression — one per search: result_set_id, session, ranked [id, rank]
  * tool call  — the search itself (query text) and every later fetch,
                 cite, attest, keyed by decision_id
  * (trace)    — the model-internal labels (candidate scores, llm_order),
                 cross-referenced by result_set_id

This script does the join the server deliberately does NOT do online
(REST is not sticky across workers, so an in-memory join would silently
lose most REST pairs). For each impression it finds, within the same
session and after it in time, which ranked decisions were subsequently
FETCHED (the click), and which were CITED or attested (terminal use —
the strongest positive). The output is one row per impression:

  {result_set_id, ts, traffic, query?, query_len,
   candidates: [{id, rank, fetched, cited, gap_s}]}

which is exactly the impression-with-engagement that learning-to-rank
trains on: the displayed list, the positions, and what the user did.
Skip-above preference pairs (a fetched result outranks the unengaged
results shown above it) fall straight out of it.

Two things this is careful about:
  * A fetch joins to the MOST RECENT prior impression in the session
    that contained the id — a later search for the same decision must
    not steal the credit.
  * The permanent dataset drops the session id: the join needs it, the
    training set does not, and the notice keeps only de-individualised
    data. `--keep-session` overrides for in-period debugging.

Usage:
    python3 scripts/build_interaction_dataset.py \
        --captures output/research_logs \
        --out output/datasets/interactions
"""
from __future__ import annotations

import argparse
import datetime as _dt
import glob
import json
import logging
from collections import defaultdict
from pathlib import Path

log = logging.getLogger("interactions")

# Tools that count as a fetch of the decision they name (the "click").
_FETCH_TOOLS = {"get_decision", "get_erwaegung", "get_regeste",
                "get_decision_structure", "get_case_brief",
                "find_relevant_erwaegung", "export_decision"}
# Tools that count as terminal use (the strongest positive signal).
_USE_TOOLS = {"cite", "attest_response", "check_claim_support",
              "find_citations", "find_appeal_chain"}
_DID_KEYS = ("decision_id", "case", "id")


def _iso_to_epoch(ts: str) -> float:
    try:
        return _dt.datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def _fetched_id(rec: dict) -> str | None:
    args = rec.get("args") or rec.get("params") or {}
    for k in _DID_KEYS:
        v = args.get(k)
        if isinstance(v, str) and v:
            return v
    return None


def compact(rec: dict) -> dict | None:
    """The part of a capture record the join reads, or None if it cannot
    take part in a join.

    The whole capture history used to be parsed into memory at once. On
    2026-11-25, at 100 days and 7.4 GB of JSONL, that reached 42 GB RSS
    and the kernel's OOM killer stopped the nightly dev-data run. Only a
    session's impressions and its fetch/use calls can join, and of those
    only a few fields are read, so everything else is dropped as each
    line is read.
    """
    sid = rec.get("sid")
    if not sid:
        return None
    if rec.get("src") == "impression":
        return {"src": "impression", "sid": sid, "ts": rec.get("ts", ""),
                "result_set_id": rec.get("result_set_id"),
                "query_len": rec.get("query_len"),
                "ranked": [{"id": c["id"], "rank": c.get("rank")}
                           for c in (rec.get("ranked") or [])
                           if isinstance(c, dict) and c.get("id")]}
    tool = rec.get("tool", "")
    if tool in _FETCH_TOOLS or tool in _USE_TOOLS:
        did = _fetched_id(rec)
        if did:
            return {"sid": sid, "ts": rec.get("ts", ""), "tool": tool,
                    "args": {"decision_id": did}}
    return None


def _census_add(counter, rec: dict) -> None:
    if rec.get("src") in (None, "mcp", "rest"):
        counter[rec.get("traffic", "?")] += 1


def load_day(path: str, counter=None) -> tuple[list[dict], int]:
    """One capture file → its joinable records (compacted) and the number
    of records read. Adds every record to the traffic census `counter`."""
    rows, n = [], 0
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict):
                continue
            n += 1
            if counter is not None:
                _census_add(counter, rec)
            c = compact(rec)
            if c is not None:
                rows.append(c)
    return rows, n


def build_all(capture_dir: Path, keep_session: bool = False):
    """Join the whole capture history, one day at a time.

    Each day's impressions are joined against that day and the next, so
    a session running past midnight keeps its fetches and its later
    searches. Peak memory is two days of compacted records, not the whole
    history. Only a session spanning three calendar days could lose a
    join this way. Returns (dataset, records_read, traffic_census).
    """
    from collections import Counter
    files = sorted(glob.glob(str(capture_dir / "capture_*.jsonl")))
    counter: Counter = Counter()
    out: list[dict] = []
    total = 0
    nxt, n = (load_day(files[0], counter) if files else ([], 0))
    total += n
    for i in range(len(files)):
        cur = nxt
        if i + 1 < len(files):
            nxt, n = load_day(files[i + 1], counter)
            total += n
        else:
            nxt = []
        # The next day's impressions still end an earlier impression's
        # claim on a decision; they are emitted in their own window.
        out.extend(build(cur + nxt, keep_session=keep_session,
                         skip={id(r) for r in nxt
                               if r.get("src") == "impression"}))
    return out, total, dict(counter)


def build(rows: list[dict], keep_session: bool = False,
          skip: set[int] | None = None) -> list[dict]:
    # Bucket every record by session, in time order. Records with no
    # session (stateless REST) cannot be joined and are skipped here —
    # they still count in the traffic census, just not in the pair set.
    by_session: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        sid = r.get("sid")
        if sid:
            by_session[sid].append(r)
    for recs in by_session.values():
        recs.sort(key=lambda r: _iso_to_epoch(r.get("ts", "")))

    out = []
    joined = fetched_total = cited_total = 0
    for sid, recs in by_session.items():
        # Impressions in this session, and the engagement events after each.
        impressions = [r for r in recs if r.get("src") == "impression"]
        for imp in impressions:
            if skip and id(imp) in skip:
                continue
            t0 = _iso_to_epoch(imp.get("ts", ""))
            ranked = imp.get("ranked") or []
            ids = {c["id"]: c["rank"] for c in ranked if c.get("id")}
            if not ids:
                continue
            fetched: dict[str, float] = {}
            cited: set[str] = set()
            for r in recs:
                if _iso_to_epoch(r.get("ts", "")) <= t0:
                    continue
                # A LATER impression containing the same id ends this
                # impression's claim on it — credit goes to the nearest
                # prior search.
                if r.get("src") == "impression" and r is not imp:
                    later_ids = {c.get("id") for c in (r.get("ranked") or [])}
                    ids = {i: rk for i, rk in ids.items() if i not in later_ids}
                did = _fetched_id(r)
                if not did or did not in ids:
                    continue
                tool = r.get("tool", "")
                if tool in _FETCH_TOOLS and did not in fetched:
                    fetched[did] = _iso_to_epoch(r.get("ts", "")) - t0
                if tool in _USE_TOOLS:
                    cited.add(did)
            if not fetched and not cited:
                continue  # an impression nobody engaged with teaches little
            joined += 1
            fetched_total += len(fetched)
            cited_total += len(cited)
            row = {
                "result_set_id": imp.get("result_set_id"),
                "ts": imp.get("ts"),
                "query_len": imp.get("query_len"),
                "candidates": [{
                    "id": c["id"], "rank": c["rank"],
                    "fetched": c["id"] in fetched,
                    "cited": c["id"] in cited,
                    "gap_s": round(fetched[c["id"]], 1) if c["id"] in fetched else None,
                } for c in ranked if c.get("id")],
            }
            if keep_session:
                row["sid"] = sid
            out.append(row)

    log.info("impressions joined: %d | fetches: %d | terminal uses: %d",
             joined, fetched_total, cited_total)
    return out


def census(rows: list[dict]) -> dict:
    from collections import Counter
    c: Counter = Counter()
    for r in rows:
        _census_add(c, r)
    return dict(c)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--captures", type=Path,
                    default=Path("output/research_logs"))
    ap.add_argument("--out", type=Path,
                    default=Path("output/datasets/interactions"))
    ap.add_argument("--keep-session", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(message)s")
    dataset, n, mix = build_all(args.captures, keep_session=args.keep_session)
    log.info("read %d capture records; traffic mix: %s", n, mix)
    args.out.mkdir(parents=True, exist_ok=True)
    day = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")
    path = args.out / f"{day}.jsonl"
    with open(path, "w", encoding="utf-8") as fh:
        for row in dataset:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    log.info("wrote %d training rows -> %s", len(dataset), path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
