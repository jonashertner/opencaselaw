"""
SZ historical repair: re-fetch short JSONL entries using the fixed
sz_gerichte scraper logic.

Why a separate tool from repair_short_text.py:
  The SZ scraper writes `source_url = GWT_MODULE_BASE` and `pdf_url = None`
  (since the PDF download URL needs a fresh GWT-RPC session token). The
  simple URL-refetch tool can't reconstruct that token from JSONL alone.
  This tool runs SZ's own discovery (which includes session init + token
  decryption) and matches results against our short-text JSONL entries.

Approach:
  1. Load output/decisions/sz_gerichte.jsonl into a docket-keyed dict.
  2. Identify entries with full_text < threshold (default 2000).
  3. Run SZGerichteScraper.discover_new() with an isolated state dir
     (so is_known() doesn't skip everything).
  4. For each yielded stub whose docket matches a short entry, call
     fetch_decision() — which now uses the PDF-prefer logic (commit
     335dd81). If the new full_text is longer, update the dict entry.
  5. Atomically rewrite the JSONL.

The original is preserved as .bak.

Cost: SZ portal has ~3,300 entries. Discovery iterates all of them
(~1,300 sec at SZ's REQUEST_DELAY=3s for a fresh session). Then PDF
fetches for the ~1,500 short entries (~75 min). Total ~110 min.
Run as a transient systemd unit.

Usage:
    python3 tools/repair_sz_historical.py --threshold 2000
    python3 tools/repair_sz_historical.py --threshold 2000 --max 50  # test
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--threshold", type=int, default=2000,
                    help="Repair entries with full_text < THRESHOLD chars")
    ap.add_argument("--max", type=int, default=None,
                    help="Stop after repairing N entries")
    ap.add_argument("--state-dir", default="/tmp/sz_repair_state",
                    help="Isolated state dir for the scraper (not the live one)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    repo = Path(__file__).parent.parent
    sys.path.insert(0, str(repo))
    from scrapers.cantonal.sz_gerichte import SZGerichteScraper

    src = repo / "output" / "decisions" / "sz_gerichte.jsonl"
    if not src.exists():
        logger.error(f"JSONL not found: {src}")
        return 1

    # Load existing entries keyed by docket
    entries: dict[str, dict] = {}
    order: list[str] = []
    with src.open("r", encoding="utf-8") as f:
        for line in f:
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            dk = d.get("docket_number")
            if not dk:
                continue
            if dk in entries:
                continue  # keep first occurrence (insertion order)
            entries[dk] = d
            order.append(dk)

    short_dockets = {
        dk for dk, d in entries.items()
        if len(d.get("full_text") or "") < args.threshold
    }
    logger.info(
        f"Loaded {len(entries)} entries; {len(short_dockets)} below "
        f"{args.threshold} chars"
    )

    state_dir = Path(args.state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    scraper = SZGerichteScraper(state_dir=state_dir)
    scraper.DOCID_SEED = None   # isolated empty state: docket-keyed legacy walk

    repaired = 0
    seen = 0
    for stub in scraper.discover_new():
        seen += 1
        if seen % 200 == 0:
            logger.info(f"Discovery: {seen} stubs seen, {repaired} repaired")
        dk = stub.get("docket_number")
        if dk not in short_dockets:
            continue
        try:
            new_decision = scraper.fetch_decision(stub)
        except Exception as e:
            logger.warning(f"fetch_decision({dk}) failed: {e}")
            continue
        if not new_decision:
            continue
        existing_len = len(entries[dk].get("full_text") or "")
        new_len = len(new_decision.full_text or "")
        if new_len <= existing_len + 100:
            continue  # no meaningful improvement
        # Update in place — only the content fields. Preserve everything
        # else (decision_id, court, canton, dates, source_url, etc.)
        entries[dk]["full_text"] = new_decision.full_text
        entries[dk]["language"] = new_decision.language
        entries[dk]["cited_decisions"] = list(new_decision.cited_decisions)
        repaired += 1
        logger.info(f"repaired {dk}: {existing_len} → {new_len} chars")
        if args.max is not None and repaired >= args.max:
            break

    # Atomic rewrite
    tmp = src.with_suffix(".jsonl.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for dk in order:
            f.write(json.dumps(entries[dk], ensure_ascii=False) + "\n")
    bak = src.with_suffix(".jsonl.bak")
    if bak.exists():
        bak.unlink()
    os.replace(src, bak)
    os.replace(tmp, src)

    logger.info(
        f"DONE total={len(entries)} short_target={len(short_dockets)} "
        f"repaired={repaired} backup={bak}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
