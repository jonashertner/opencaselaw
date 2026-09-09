#!/usr/bin/env python3
"""
audit_bge_decision_dates.py — every `bge` decision whose stored decision_date
contradicts its own BGE volume.

BGE volumes are annual: volume N collects the year N + 1874 (vol. 76 = 1950,
vol. 150 = 2024). A stored year outside [volume_year - 1, volume_year + 1] is a
corpus defect, never an unusual case. The window mirrors mcp_server.py's
_bge_volume_year_mismatch (the `cite` tool's decision_date_warning) — the
constants are cross-checked against that source file at start-up so the two
never drift apart silently.

Two inputs, same report:
  --db        decisions.db (what is SERVED; also finds duplicate rows per BGE)
  --jsonl-dir output/decisions/ shards (what the build READS; bge.jsonl,
              es_bge.jsonl, bge_historical.jsonl) — use this while the nightly
              build runs, see below.

Provenance columns come from the row's own JSON: `source` ("entscheidsuche" for
the CH_BGE feed, absent for the direct CLIR scraper), and `date_extraction`,
the stamp scripts/repair_decision_dates.py left on every row whose date it
rewrote from body text (method, confidence, raw_match, metadata_date = the
value it overwrote). A "regression" below is a row that was volume-consistent
BEFORE that rewrite and is not any more.

INVARIANT 9 (CLAUDE.md): no full-table scans of the production decisions.db
during the build window (03:30 UTC until publish.py exits). --db refuses to
run while a publish.py / build_fts5 process is alive on this host; pass
--force only against a copy. Read-only either way (mode=ro&immutable=1).

Usage:
  python3 audit_bge_decision_dates.py --db output/decisions.db --out bad_rows.jsonl
  python3 audit_bge_decision_dates.py --jsonl-dir output/decisions --out bad_rows.jsonl
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

# Mirror of mcp_server._BGE_VOLUME_EPOCH / _BGE_YEAR_TOLERANCE (checked below).
BGE_VOLUME_EPOCH = 1874
BGE_YEAR_TOLERANCE = 1

# "150 III 367", "BGE 150 III 367", "120 IA 1", "1_I_3" (bge_historical),
# "BGE_150_III_367" (entscheidsuche id form).
_KEY_RE = re.compile(
    r"(?:^|[\s_])(?:BGE|ATF|DTF)?[\s_]*(\d{1,3})[\s_]+([IVX]+[abAB]?)[\s_]+(\d{1,4})(?:$|[\s_,;)])"
)


def bge_key(docket: str | None) -> tuple[int, str, int] | None:
    m = _KEY_RE.search(docket or "")
    if not m:
        return None
    part = m.group(2)
    part = part[:-1].upper() + part[-1].lower() if part[-1] in "abAB" else part.upper()
    return int(m.group(1)), part, int(m.group(3))


def year_of(iso: str | None) -> int | None:
    s = str(iso or "")
    return int(s[:4]) if s[:4].isdigit() else None


def consistent(iso: str | None, volume: int) -> bool | None:
    y = year_of(iso)
    if y is None:
        return None
    return abs(y - (volume + BGE_VOLUME_EPOCH)) <= BGE_YEAR_TOLERANCE


def check_parity(repo: Path) -> None:
    """Warn if mcp_server.py's constants differ from ours (they must not)."""
    src = repo / "mcp_server.py"
    if not src.exists():
        return
    text = src.read_text(encoding="utf-8", errors="replace")
    m1 = re.search(r"^_BGE_VOLUME_EPOCH\s*=\s*(\d+)", text, re.MULTILINE)
    m2 = re.search(r"^_BGE_YEAR_TOLERANCE\s*=\s*(\d+)", text, re.MULTILINE)
    if m1 and int(m1.group(1)) != BGE_VOLUME_EPOCH:
        print(f"WARNING: mcp_server._BGE_VOLUME_EPOCH={m1.group(1)} != {BGE_VOLUME_EPOCH}", file=sys.stderr)
    if m2 and int(m2.group(1)) != BGE_YEAR_TOLERANCE:
        print(f"WARNING: mcp_server._BGE_YEAR_TOLERANCE={m2.group(1)} != {BGE_YEAR_TOLERANCE}", file=sys.stderr)


def build_running() -> str | None:
    try:
        out = subprocess.run(["pgrep", "-af", "publish.py|build_fts5"],
                             capture_output=True, text=True, timeout=5, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    lines = [l for l in out.splitlines() if "pgrep" not in l]
    return lines[0] if lines else None


def source_of(decision_id: str, extra: dict, source_url: str | None) -> str:
    if extra.get("source") == "entscheidsuche" or decision_id.startswith("bge_BGE_"):
        return "entscheidsuche"
    if decision_id.startswith("bge_historical_") or re.match(r"^bge_\d+_[IVX]+[ab]?_\d+$", decision_id):
        return "historical"
    return "direct"


# ── row sources ─────────────────────────────────────────────────────────────

def rows_from_db(path: Path):
    con = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
    try:
        cur = con.execute(
            "SELECT decision_id, docket_number, decision_date, publication_date, source_url, "
            "json_extract(json_data, '$.source'), json_extract(json_data, '$.date_extraction'), "
            "json_extract(json_data, '$.scraped_at') "
            "FROM decisions WHERE court = 'bge'"
        )
        for did, docket, dd, pd, url, src, de, scraped in cur:
            extra = {"source": src, "scraped_at": scraped}
            if de:
                try:
                    extra["date_extraction"] = json.loads(de)
                except (TypeError, ValueError):
                    pass
            yield did, docket, dd, pd, url, extra
    except sqlite3.OperationalError as exc:  # no json1 / no json_data column
        print(f"json_extract unavailable ({exc}); falling back to Python JSON", file=sys.stderr)
        cur = con.execute(
            "SELECT decision_id, docket_number, decision_date, publication_date, source_url, json_data "
            "FROM decisions WHERE court = 'bge'"
        )
        for did, docket, dd, pd, url, blob in cur:
            extra = {}
            if blob:
                try:
                    o = json.loads(blob)
                    extra = {k: o.get(k) for k in ("source", "date_extraction", "scraped_at")}
                except ValueError:
                    pass
            yield did, docket, dd, pd, url, extra
    finally:
        con.close()


def rows_from_jsonl(directory: Path):
    names = ["bge.jsonl", "es_bge.jsonl", "bge_historical.jsonl"]
    for name in names:
        p = directory / name
        if not p.exists():
            print(f"  (no {name})", file=sys.stderr)
            continue
        with open(p, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                if o.get("court") not in ("bge", "bge_historical"):
                    continue
                did = o.get("decision_id") or ""
                if o.get("court") == "bge_historical":  # build_fts5 COURT_REMAP
                    did = did.replace("bge_historical_", "bge_", 1)
                extra = {k: o.get(k) for k in ("source", "date_extraction", "scraped_at")}
                extra["shard"] = name
                yield (did, o.get("docket_number"), o.get("decision_date"),
                       o.get("publication_date"), o.get("source_url"), extra)


# ── audit ───────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--db", type=Path, help="decisions.db (default output/decisions.db)")
    g.add_argument("--jsonl-dir", type=Path, help="output/decisions/ shard directory")
    ap.add_argument("--out", type=Path, help="write every mismatching row as JSONL")
    ap.add_argument("--limit", type=int, default=15, help="sample rows printed per class")
    ap.add_argument("--force", action="store_true", help="scan --db even while a build runs (copies only)")
    args = ap.parse_args()
    if not args.db and not args.jsonl_dir:
        args.db = Path("output/decisions.db")

    check_parity(Path(__file__).resolve().parent)

    if args.db:
        proc = build_running()
        if proc and not args.force:
            print(f"REFUSING: a build is running on this host ({proc[:80]}). Invariant 9: no "
                  f"decisions.db scans during the build window. Use --jsonl-dir, a copy with "
                  f"--force, or wait for publish.py to exit.", file=sys.stderr)
            return 2
        print(f"source: {args.db} (read-only)", file=sys.stderr)
        rows = rows_from_db(args.db)
    else:
        print(f"source: shards in {args.jsonl_dir}", file=sys.stderr)
        rows = rows_from_jsonl(args.jsonl_dir)

    total = Counter()            # per source: rows
    unparsed = Counter()         # per source
    nodate = Counter()
    bad = Counter()              # per source
    jan1 = Counter()             # per source: '-01-01' placeholders (year-only precision)
    stamped = Counter()          # per source: rows carrying date_extraction
    regression = Counter()       # per source: stamped, metadata consistent, now bad
    bad_by_volume = defaultdict(Counter)   # volume -> source -> n
    rows_by_volume = Counter()
    bad_by_method = Counter()
    bad_rows = []
    unparsed_samples = []
    by_key = defaultdict(list)   # (vol, part, page) -> [(decision_id, source, date, ok)]

    for did, docket, dd, pd, url, extra in rows:
        src = source_of(did, extra, url)
        total[src] += 1
        key = bge_key(docket) or bge_key(did)
        if not key:
            unparsed[src] += 1
            if len(unparsed_samples) < args.limit:
                unparsed_samples.append((did, docket, dd, src))
            continue
        vol = key[0]
        rows_by_volume[vol] += 1
        ok = consistent(dd, vol)
        if ok is None:
            nodate[src] += 1
            continue
        de = extra.get("date_extraction") or {}
        if de:
            stamped[src] += 1
        if str(dd).endswith("-01-01"):
            jan1[src] += 1
        by_key[key].append((did, src, str(dd)[:10], ok))
        if ok:
            continue
        bad[src] += 1
        bad_by_volume[vol][src] += 1
        bad_by_method[de.get("method") or "(no date_extraction stamp)"] += 1
        meta_ok = consistent(de.get("metadata_date"), vol) if de else None
        if de and meta_ok:
            regression[src] += 1
        bad_rows.append({
            "decision_id": did, "docket_number": docket, "source": src,
            "volume": vol, "expected_year": vol + BGE_VOLUME_EPOCH,
            "decision_date": dd, "stored_year": year_of(dd),
            "delta_years": (year_of(dd) or 0) - (vol + BGE_VOLUME_EPOCH),
            "publication_date": pd,
            "publication_date_consistent": consistent(pd, vol),
            "extraction_method": de.get("method"), "extraction_confidence": de.get("confidence"),
            "extraction_raw_match": de.get("raw_match"), "metadata_date": de.get("metadata_date"),
            "metadata_date_consistent": meta_ok,
            "scraped_at": extra.get("scraped_at"), "shard": extra.get("shard"),
            "source_url": url,
        })

    sources = sorted(total)
    print("\n=== BGE decision_date vs volume year "
          f"(volume N collects {BGE_VOLUME_EPOCH}+N, tolerance ±{BGE_YEAR_TOLERANCE}) ===")
    print(f"{'source':<15}{'rows':>8}{'unparsed':>10}{'no date':>9}{'BAD':>8}{'bad %':>8}"
          f"{'stamped':>9}{'regress.':>10}{'01-01':>8}")
    for s in sources:
        parsed = total[s] - unparsed[s] - nodate[s]
        pct = 100.0 * bad[s] / parsed if parsed else 0.0
        print(f"{s:<15}{total[s]:>8}{unparsed[s]:>10}{nodate[s]:>9}{bad[s]:>8}{pct:>7.1f}%"
              f"{stamped[s]:>9}{regression[s]:>10}{jan1[s]:>8}")
    print(f"{'TOTAL':<15}{sum(total.values()):>8}{sum(unparsed.values()):>10}{sum(nodate.values()):>9}"
          f"{sum(bad.values()):>8}{'':>8}{sum(stamped.values()):>9}{sum(regression.values()):>10}"
          f"{sum(jan1.values()):>8}")
    print("  stamped  = rows rewritten by scripts/repair_decision_dates.py (date_extraction present)")
    print("  regress. = stamped rows that were volume-consistent BEFORE the rewrite and are BAD now")
    print("  01-01    = 1 January placeholders (year-only precision; consistent but not a ruling date)")

    print("\n=== BAD rows by extraction method ===")
    for meth, n in bad_by_method.most_common():
        print(f"{n:>8}  {meth}")

    print("\n=== BAD rows by volume (volume: total rows / bad, per source) ===")
    for vol in sorted(bad_by_volume):
        parts = ", ".join(f"{s}={n}" for s, n in sorted(bad_by_volume[vol].items()))
        n_bad = sum(bad_by_volume[vol].values())
        print(f"  vol {vol:>3} ({vol + BGE_VOLUME_EPOCH}): {rows_by_volume[vol]:>5} rows, {n_bad:>4} bad  [{parts}]")

    # Duplicates: the same (vol, part, page) under several decision_ids. The
    # resolver (_bge_ref_candidates) tries the entscheidsuche id first, so a
    # bad entscheidsuche row masks a good direct row for `cite` / get_decision.
    multi = {k: v for k, v in by_key.items() if len(v) > 1}
    if multi:
        disagree = {k: v for k, v in multi.items() if len({d for _, _, d, _ in v}) > 1}
        served_bad = [k for k, v in disagree.items()
                      if any(s == "entscheidsuche" and not ok for _, s, _, ok in v)
                      and any(s != "entscheidsuche" and ok for _, s, _, ok in v)]
        masked_bad = [k for k, v in disagree.items()
                      if any(s == "entscheidsuche" and ok for _, s, _, ok in v)
                      and any(s != "entscheidsuche" and not ok for _, s, _, ok in v)]
        print(f"\n=== Same BGE under several ids: {len(multi)} keys; dates disagree on {len(disagree)} ===")
        print(f"  entscheidsuche row BAD while a direct row is fine  (served WRONG by cite): {len(served_bad)}")
        print(f"  direct row BAD while the entscheidsuche row is fine (bad row hidden behind cite): {len(masked_bad)}")
        for k in served_bad[:args.limit]:
            print("   ", f"BGE {k[0]} {k[1]} {k[2]}", by_key[k])

    if unparsed_samples:
        print(f"\n=== unparsed dockets (no volume tuple; {sum(unparsed.values())} rows) — samples ===")
        for did, docket, dd, src in unparsed_samples:
            print(f"  {did:<40} docket={docket!r:<28} date={dd} source={src}")

    print(f"\n=== sample BAD rows (of {len(bad_rows)}) ===")
    for r in sorted(bad_rows, key=lambda r: abs(r["delta_years"]), reverse=True)[:args.limit]:
        print(f"  {r['decision_id']:<32} vol {r['volume']:>3} expects {r['expected_year']}  "
              f"stored {r['decision_date']}  pub {r['publication_date']}  "
              f"[{r['source']}/{r['extraction_method']}: {str(r['extraction_raw_match'])[:40]!r}"
              f" over metadata {r['metadata_date']}]")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            for r in bad_rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"\nwrote {len(bad_rows)} rows → {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
