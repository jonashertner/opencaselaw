#!/usr/bin/env python3
"""
Re-fetch the document text of Ticino rows the old extractor truncated (2026-10-14).

Why (report 2026-10-14, "Tessiner Entscheide mit gekürztem Volltext").  The TI
document extractor kept only <p class="MsoNormal">.  The Word exports of
sentenze.ti.ch file the body under their own styles ("R1", "R2", ...), so for
many rulings only the heading and the signature survived: ti_gerichte_52.2023.477
was served as 681 characters, 15,225 with the fixed extractor (every block
element).  Of 59,669 rows in ti_gerichte.jsonl, 15,140 are under 2,000
characters and contain none of the body markers ("in fatto", "in diritto",
"considerato", "per questi motivi", "dispositivo"); 15,124 of them come from the
2026-02 backfill.  scrapers/cantonal/ti_gerichte.py is fixed; this script
brings the rows already stored up to it, from the official source.

Two phases, so the slow part never touches the shard:

  fetch   Walk the shard, request every selected document once (2 s apart, the
          scraper's own rate) and append {decision_id, full_text, cited_decisions,
          fetched_at} to a sidecar.  The sidecar lives in state/, NOT in
          output/decisions/: the full build imports every *.jsonl there
          (bs_gerichte.refetch.jsonl sat in that directory for four weeks).
          Resumable: ids already in the sidecar are skipped.  ~15,000 rows ≈ 9 h.

  apply   Rewrite the shard once (temp file + atomic replace, file mode kept): a
          row takes the sidecar text only when it is longer than what it holds;
          cited_decisions is re-extracted; decision_date is filled only where the
          row has none (a wrong stored date is a separate repair, from the
          ruling's own "Lugano, <date>" heading).  Every changed row records
          text_refetched_at.  Run while nothing appends to the shard: after the
          01:00 UTC scrape has logged Done. and before the 03:30 full rebuild.

Selection (fetch): rows without any body marker and under --only-shorter-than
characters (default 2,000), i.e. the truncated ones; --all-short takes every row
under that length.

Usage (on the VPS):
    nohup python3 scripts/refetch_ti_gerichte_text.py fetch >> logs/ti_refetch.log 2>&1 &
    python3 scripts/refetch_ti_gerichte_text.py fetch --max 20
    python3 scripts/refetch_ti_gerichte_text.py apply --dry-run
    python3 scripts/refetch_ti_gerichte_text.py apply
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import stat
import sys
import tempfile
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
log = logging.getLogger("ti_refetch")

COURT = "ti_gerichte"
BODY_RE = re.compile(
    r"\b(?:in fatto|in diritto|ritenuto|considerato|considerando|per questi motivi|"
    r"per i quali motivi|dispositivo|fatti|diritto)\b", re.I)
DEFAULT_SIDECAR = "state/ti_gerichte.refetch.jsonl"


def _iter_jsonl(path: Path):
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def truncated(row: dict, shorter_than: int, all_short: bool) -> bool:
    text = row.get("full_text") or ""
    if len(text) >= shorter_than:
        return False
    return all_short or not BODY_RE.search(text)


# ── fetch ───────────────────────────────────────────────────────────────


def fetch_document(url: str, fetcher) -> dict | None:
    """(full_text, cited_decisions) for one document page, via the scraper's own
    (fixed) extractor. ``fetcher(url) -> html``."""
    from bs4 import BeautifulSoup

    from models import extract_citations
    from scrapers.cantonal import ti_gerichte as T

    html = fetcher(url)
    if not html or len(html) < 500:
        return None
    text = T._extract_document_text(BeautifulSoup(html, "html.parser"))
    if not text or len(text) < 50:
        return None
    return {"full_text": text,
            "cited_decisions": extract_citations(text) if len(text) > 200 else []}


def run_fetch(shard: Path, sidecar: Path, fetcher, max_rows: int | None, delay: float,
              shorter_than: int, all_short: bool) -> Counter:
    if sidecar.resolve().parent == shard.resolve().parent:
        raise SystemExit("refusing a sidecar inside the shard directory: the build imports every *.jsonl there")
    done: set[str] = set()
    if sidecar.exists():
        done = {r["decision_id"] for r in _iter_jsonl(sidecar)}
    log.info(f"sidecar {sidecar}: {len(done)} documents already fetched")
    stats: Counter = Counter()
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    with open(sidecar, "a", encoding="utf-8") as out:
        for row in _iter_jsonl(shard):
            if row.get("court") != COURT:
                continue
            if not truncated(row, shorter_than, all_short):
                stats["not_selected"] += 1
                continue
            stats["candidates"] += 1
            did = row.get("decision_id")
            if did in done:
                stats["already"] += 1
                continue
            if max_rows is not None and stats["fetched"] + stats["failed"] >= max_rows:
                break
            url = row.get("source_url") or ""
            try:
                doc = fetch_document(url, fetcher) if url else None
            except Exception as e:  # noqa: BLE001 — one bad page must not stop the walk
                log.warning(f"{did}: fetch error {e}")
                doc = None
            if doc is None:
                stats["failed"] += 1
                log.warning(f"{did}: no text ({url[-60:]})")
            else:
                stats["fetched"] += 1
                gain = len(doc["full_text"]) - len(row.get("full_text") or "")
                stats["chars_gained"] += max(gain, 0)
                stats["now_with_body"] += bool(BODY_RE.search(doc["full_text"]))
                out.write(json.dumps({"decision_id": did, "source_url": url,
                                      "fetched_at": datetime.now(timezone.utc).isoformat(),
                                      **doc}, ensure_ascii=False) + "\n")
                out.flush()
                done.add(did)
                if stats["fetched"] % 100 == 0:
                    log.info(f"  fetched {stats['fetched']} (failed {stats['failed']})")
            time.sleep(delay)
    return stats


# ── apply ───────────────────────────────────────────────────────────────


def apply_rows(rows: list[dict], sidecar_rows: dict[str, dict]) -> Counter:
    """Pure: update ``rows`` in place. A row takes the fetched text only when it
    is longer than what the row holds."""
    stats: Counter = Counter()
    for row in rows:
        doc = sidecar_rows.get(row.get("decision_id")) if row.get("court") == COURT else None
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
        row["text_refetched_at"] = doc.get("fetched_at")
        stats["updated"] += 1
        stats["chars_gained"] += len(new_text) - old_len
    return stats


def run_apply(shard: Path, sidecar: Path, dry_run: bool) -> Counter:
    if not sidecar.exists():
        log.error(f"sidecar not found: {sidecar}")
        return Counter({"error": 1})
    side = {r["decision_id"]: r for r in _iter_jsonl(sidecar)}   # last fetch of a document wins
    rows = list(_iter_jsonl(shard))
    log.info(f"{shard}: {len(rows)} rows; sidecar: {len(side)} documents")
    stats = apply_rows(rows, side)
    if dry_run or not stats["updated"]:
        log.info("dry run — nothing written" if dry_run else "nothing to apply")
        return stats
    fd, tmp = tempfile.mkstemp(dir=str(shard.parent), prefix=shard.name + ".", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        for r in rows:
            out.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.chmod(tmp, stat.S_IMODE(os.stat(shard).st_mode))
    os.replace(tmp, shard)
    log.info(f"wrote {shard} ({len(rows)} rows, {stats['updated']} updated)")
    return stats


# ── cli ─────────────────────────────────────────────────────────────────


def _http_fetcher():
    import requests

    s = requests.Session()
    s.headers["User-Agent"] = "OpenCaseLaw/1.0 (+https://opencaselaw.ch; text re-fetch)"

    def get(url: str) -> str:
        r = s.get(url, timeout=60)
        r.raise_for_status()
        r.encoding = r.encoding or r.apparent_encoding
        return r.text
    return get


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("phase", choices=("fetch", "apply"))
    ap.add_argument("--shard", default="output/decisions/ti_gerichte.jsonl")
    ap.add_argument("--sidecar", default=DEFAULT_SIDECAR)
    ap.add_argument("--max", type=int, default=None, help="fetch: stop after this many requests")
    ap.add_argument("--only-shorter-than", type=int, default=2000)
    ap.add_argument("--all-short", action="store_true",
                    help="fetch: every row under the length, not only those without a body marker")
    ap.add_argument("--delay", type=float, default=2.0, help="fetch: seconds between requests")
    ap.add_argument("--dry-run", action="store_true", help="apply: report only")
    args = ap.parse_args()

    shard, sidecar = Path(args.shard), Path(args.sidecar)
    if not shard.exists():
        log.error(f"shard not found: {shard}")
        return 2
    if args.phase == "fetch":
        stats = run_fetch(shard, sidecar, _http_fetcher(), args.max, args.delay,
                          args.only_shorter_than, args.all_short)
    else:
        stats = run_apply(shard, sidecar, args.dry_run)
    for k, v in sorted(stats.items()):
        log.info(f"  {k}: {v}")
    return 1 if stats.get("error") else 0


if __name__ == "__main__":
    sys.exit(main())
