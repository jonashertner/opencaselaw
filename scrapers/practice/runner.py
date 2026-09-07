"""
Run all federal Verwaltungspraxis scrapers in sequence.

Usage:
    python3 -m scrapers.practice.runner                # all scrapers, default
    python3 -m scrapers.practice.runner --only estv_ks # one source
    python3 -m scrapers.practice.runner --max-new 5    # smoke-test mode
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from .estv_kreisschreiben import EstvKreisschreibenScraper
from .estv_mwst import EstvMwstScraper
from .sem_weisungen import SemWeisungenScraper
from .bafu_vollzugshilfen import BafuVollzugshilfenScraper
from .seco_arg import SecoArgScraper
from .ssk_kreisschreiben import SskKreisschreibenScraper
from .are_vollzugshilfen import AreVollzugshilfenScraper
from .epa_personalrecht import EpaPersonalrechtScraper
from .finma_rundschreiben import FinmaRundschreibenScraper
from .seco_alv import SecoAlvScraper
from .bag_kvg import BagKvgScraper
from .sem_handbuch_asyl import SemHandbuchAsylScraper
from .bj_schkg import BjSchkgScraper
from .bsv_weisungen import BsvWeisungenScraper
from .weko_bekanntmachungen import WekoBekanntmachungenScraper
from .oak_bv import OakBvScraper

logger = logging.getLogger(__name__)

# Tested + production-ready (full implementations against real HTML)
ENABLED_SCRAPERS = {
    "estv_ks":       EstvKreisschreibenScraper,
    "estv_mwst":     EstvMwstScraper,
    "sem_weisungen": SemWeisungenScraper,
    "bafu_vollzug":  BafuVollzugshilfenScraper,
    "seco_arg":      SecoArgScraper,
    "finma_rs":      FinmaRundschreibenScraper,
    # Tier 1 social-law sources (Caritas access-to-justice memo, 2026-09-02).
    # Small ones first: the unit's TimeoutStartSec=3600 covers the whole run.
    "seco_alv":          SecoAlvScraper,
    "bag_kvg":           BagKvgScraper,
    "sem_handbuch_asyl": SemHandbuchAsylScraper,
    "bj_schkg":          BjSchkgScraper,
    # First full run 2026-09-03/04 on the VPS: 6,083 records, 0 failed, 1 h 45 min.
    "bsv_weisungen":     BsvWeisungenScraper,
    # Requested by a user 2026-09-07 (with the BSV BV Mitteilungen, which the
    # BSV crawl already holds). Small: ~60 + ~360 rows, minutes per run.
    "weko_bekanntmachungen": WekoBekanntmachungenScraper,
    "oak_bv":            OakBvScraper,
}

# Defensive scaffolds — need first-run validation before enabling
EXPERIMENTAL_SCRAPERS = {
    "ssk_ks":            SskKreisschreibenScraper,
    "are_vollzug":       AreVollzugshilfenScraper,
    "epa_personalrecht": EpaPersonalrechtScraper,
}

ALL_SCRAPERS = {**ENABLED_SCRAPERS, **EXPERIMENTAL_SCRAPERS}


def main():
    parser = argparse.ArgumentParser(description="Run federal practice scrapers")
    parser.add_argument("--only", help="Comma-separated source keys to run")
    parser.add_argument("--include-experimental", action="store_true",
                        help="Also run scrapers marked experimental")
    parser.add_argument("--max-new", type=int, default=None,
                        help="Stop after N new docs per source (smoke test)")
    parser.add_argument("--force-refresh", action="store_true",
                        help="Re-fetch already-known docs")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    if args.only:
        targets = {k: ALL_SCRAPERS[k] for k in args.only.split(",") if k in ALL_SCRAPERS}
        if not targets:
            logger.error("No matching scrapers; choose from %s", list(ALL_SCRAPERS))
            sys.exit(1)
    else:
        targets = dict(ENABLED_SCRAPERS)
        if args.include_experimental:
            targets.update(EXPERIMENTAL_SCRAPERS)

    summaries = []
    for key, cls in targets.items():
        logger.info("════ %s ════", key)
        try:
            s = cls().run(max_new=args.max_new, force_refresh=args.force_refresh)
            summaries.append(s)
        except Exception as e:
            logger.exception("[%s] crashed: %s", key, e)
            summaries.append({"source": key, "error": str(e)})

    health_path = Path(__file__).parent.parent.parent / "logs" / "practice_health.json"
    health_path.parent.mkdir(exist_ok=True)
    health_path.write_text(json.dumps({
        "run_at": datetime.now(timezone.utc).isoformat(),
        "summaries": summaries,
    }, indent=2, ensure_ascii=False))
    logger.info("Health written to %s", health_path)

    print()
    print("════════════════════════════════════════════════")
    print("Practice scrapers — run summary")
    print("════════════════════════════════════════════════")
    for s in summaries:
        if "error" in s:
            print(f"  ✗ {s['source']}: ERROR — {s['error']}")
        else:
            print(f"  ✓ {s['source']}: +{s['new']} new, "
                  f"{s['skipped']} skipped, {s['failed']} failed "
                  f"({s['duration_s']}s)")


if __name__ == "__main__":
    main()
