#!/usr/bin/env python3
"""
Re-fetch the document text of the Basel-Stadt Gerichte rows (2026-09-17).

Why.  The BS document extractor kept only <p class="MsoNormal"> paragraphs.
The portal's Word export files most body text under other styles (aaText,
Entscheidtext, aaDispositiv, MsoBodyText ...) and whole paragraphs under <h2>,
so the served text was a fraction of the ruling: the Sozialversicherungsgericht
rows have a median of 1,742 characters (2,104 of 2,198 under 2,000; one checked
ruling is 26,233 characters with the fixed extractor, 1,695 with the old one),
and several hundred Appellationsgericht rows are short the same way.  The
extractor in scrapers/cantonal/bs_gerichte.py is fixed; this script brings the
rows already in the shard up to it.

Two phases, so the slow part never touches the shard:

  fetch   Walk the shard, request every selected document once (2 s apart,
          the scraper's own rate), and append {court, docket_number_2,
          full_text, decision_date, cited_decisions, fetched_at} to a sidecar
          (output/decisions/bs_gerichte.refetch.jsonl).  Resumable: rows whose
          decision number is already in the sidecar are skipped.  Runs at any
          time, for hours (10,629 rows ≈ 6–7 h).  Keyed on the decision number,
          which is the same before and after scripts/migrate_bs_gerichte_ids.py.

  apply   Rewrite the shard once (temp file + os.replace, ~1 min): a row takes
          the sidecar text when that text is longer than what it holds;
          decision_date is filled where the row has none; cited_decisions is
          re-extracted from the new text.  Must run in the same window as the
          migration (after the 01:00 UTC scrape, before the 03:30 full rebuild;
          the 20:00 incremental seeks into shards by byte offset).

Usage (on the VPS):
    nohup python3 scripts/refetch_bs_gerichte_text.py fetch >> logs/bs_refetch.log 2>&1 &
    python3 scripts/refetch_bs_gerichte_text.py fetch --court bs_sozialversicherungsgericht --max 50
    python3 scripts/refetch_bs_gerichte_text.py apply --dry-run
    python3 scripts/refetch_bs_gerichte_text.py apply
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tempfile
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
log = logging.getLogger("bs_refetch")

BS_DIRECT_COURTS = ("bs_appellationsgericht", "bs_sozialversicherungsgericht", "bs_zivilgericht")


def _key(row: dict) -> str | None:
    d2 = (row.get("docket_number_2") or "").strip()
    court = row.get("court") or ""
    return f"{court}|{d2}" if d2 and court in BS_DIRECT_COURTS else None


def _iter_jsonl(path: Path):
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


# ── fetch ───────────────────────────────────────────────────────────────


def fetch_document(url: str, fetcher) -> dict | None:
    """(full_text, decision_date, cited_decisions) for one document page, via
    the scraper's own extractor. ``fetcher(url) -> html``."""
    from bs4 import BeautifulSoup

    from models import extract_citations
    from scrapers.cantonal import bs_gerichte as B

    html = fetcher(url)
    if not html or len(html) < 500:
        return None
    soup = BeautifulSoup(html, "html.parser")
    text = B._extract_document_text(soup)
    if not text or len(text) < 50:
        return None
    date = B._extract_decision_date_from_doc(soup)
    return {
        "full_text": text,
        "decision_date": str(date) if date else None,
        "cited_decisions": extract_citations(text) if len(text) > 200 else [],
    }


def run_fetch(shard: Path, sidecar: Path, fetcher, courts, max_rows: int | None,
              delay: float, only_shorter_than: int | None) -> Counter:
    done: set[str] = set()
    if sidecar.exists():
        done = {f"{r['court']}|{r['docket_number_2']}" for r in _iter_jsonl(sidecar)}
    log.info(f"sidecar {sidecar}: {len(done)} documents already fetched")
    stats: Counter = Counter()
    with open(sidecar, "a", encoding="utf-8") as out:
        for row in _iter_jsonl(shard):
            key = _key(row)
            if not key or row.get("court") not in courts:
                continue
            stats["candidates"] += 1
            if key in done:
                stats["already"] += 1
                continue
            if only_shorter_than is not None and len(row.get("full_text") or "") >= only_shorter_than:
                stats["long_enough"] += 1
                continue
            if max_rows is not None and stats["fetched"] + stats["failed"] >= max_rows:
                break
            url = row.get("source_url") or ""
            try:
                doc = fetch_document(url, fetcher)
            except Exception as e:  # noqa: BLE001 — one bad page must not stop the walk
                log.warning(f"{key}: fetch error {e}")
                doc = None
            if doc is None:
                stats["failed"] += 1
                log.warning(f"{key}: no text ({url[-60:]})")
            else:
                stats["fetched"] += 1
                gain = len(doc["full_text"]) - len(row.get("full_text") or "")
                stats["chars_gained"] += max(gain, 0)
                out.write(json.dumps({
                    "court": row["court"], "docket_number_2": row["docket_number_2"],
                    "decision_id": row.get("decision_id"), "source_url": url,
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                    **doc,
                }, ensure_ascii=False) + "\n")
                out.flush()
                done.add(key)
                if stats["fetched"] % 100 == 0:
                    log.info(f"  fetched {stats['fetched']} (failed {stats['failed']})")
            time.sleep(delay)
    return stats


# ── apply ───────────────────────────────────────────────────────────────


def apply_rows(rows: list[dict], sidecar_rows: dict[str, dict]) -> Counter:
    """Pure: update ``rows`` in place from the sidecar. A row takes the fetched
    text only when it is longer than what the row holds."""
    stats: Counter = Counter()
    for row in rows:
        key = _key(row)
        doc = sidecar_rows.get(key) if key else None
        if not doc:
            stats["no_sidecar"] += 1
            continue
        old_len = len(row.get("full_text") or "")
        new_text = doc.get("full_text") or ""
        if len(new_text) <= old_len:
            stats["kept"] += 1
            continue
        row["full_text"] = new_text
        row["cited_decisions"] = doc.get("cited_decisions") or []
        if not row.get("decision_date") and doc.get("decision_date"):
            row["decision_date"] = doc["decision_date"]
            stats["date_filled"] += 1
        row["text_refetched_at"] = doc.get("fetched_at")
        stats["updated"] += 1
        stats[f"updated:{row['court']}"] += 1
        stats["chars_gained"] += len(new_text) - old_len
    return stats


def run_apply(shard: Path, sidecar: Path, dry_run: bool) -> Counter:
    if not sidecar.exists():
        log.error(f"sidecar not found: {sidecar}")
        return Counter({"error": 1})
    side = {}
    for r in _iter_jsonl(sidecar):
        side[f"{r['court']}|{r['docket_number_2']}"] = r     # last fetch of a document wins
    rows = list(_iter_jsonl(shard))
    log.info(f"{shard}: {len(rows)} rows; sidecar: {len(side)} documents")
    stats = apply_rows(rows, side)
    for k, v in sorted(stats.items()):
        log.info(f"  {k}: {v}")
    if dry_run or not stats["updated"]:
        log.info("dry run — nothing written" if dry_run else "nothing to apply")
        return stats
    fd, tmp = tempfile.mkstemp(dir=str(shard.parent), prefix=shard.name + ".", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        for r in rows:
            out.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, shard)
    log.info(f"wrote {shard} ({len(rows)} rows, {stats['updated']} updated)")
    return stats


# ── cli ─────────────────────────────────────────────────────────────────


def _http_fetcher(delay_unused=None):
    import requests

    s = requests.Session()
    s.headers["User-Agent"] = "OpenCaseLaw/1.0 (+https://opencaselaw.ch; text re-fetch)"

    def get(url: str) -> str:
        r = s.get(url, timeout=60)
        r.raise_for_status()
        return r.text
    return get


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("phase", choices=("fetch", "apply"))
    ap.add_argument("--shard", default="output/decisions/bs_gerichte.jsonl")
    ap.add_argument("--sidecar", default="output/decisions/bs_gerichte.refetch.jsonl")
    ap.add_argument("--court", action="append", choices=BS_DIRECT_COURTS,
                    help="fetch: restrict to a court (repeatable; default all three)")
    ap.add_argument("--max", type=int, default=None, help="fetch: stop after this many requests")
    ap.add_argument("--only-shorter-than", type=int, default=None,
                    help="fetch: skip rows whose stored text is at least this long")
    ap.add_argument("--delay", type=float, default=2.0, help="fetch: seconds between requests")
    ap.add_argument("--dry-run", action="store_true", help="apply: report only")
    args = ap.parse_args()

    shard, sidecar = Path(args.shard), Path(args.sidecar)
    if not shard.exists():
        log.error(f"shard not found: {shard}")
        return 2
    if args.phase == "fetch":
        stats = run_fetch(shard, sidecar, _http_fetcher(), tuple(args.court or BS_DIRECT_COURTS),
                          args.max, args.delay, args.only_shorter_than)
    else:
        stats = run_apply(shard, sidecar, args.dry_run)
    for k, v in sorted(stats.items()):
        log.info(f"  {k}: {v}")
    return 1 if stats.get("error") else 0


if __name__ == "__main__":
    sys.exit(main())
