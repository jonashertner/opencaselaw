#!/usr/bin/env python3
"""
entscheidsuche_download.py — Download decisions from entscheidsuche.ch
======================================================================

Parses the directory listing HTML to get the full file list, then
downloads individual JSON + HTML files. Replaces the broken wget-based
approach (wget can't parse the autoindex pages).

Usage:
    # Download all Tier A spiders (HTML available, ~247k decisions)
    python3 entscheidsuche_download.py

    # Single spider
    python3 entscheidsuche_download.py --spider BL_Gerichte

    # All spiders including PDF-only
    python3 entscheidsuche_download.py --all

    # Resume after interruption (skips valid existing files)
    python3 entscheidsuche_download.py  # just re-run, it's idempotent

    # Dry run
    python3 entscheidsuche_download.py --dry-run

Files saved to: /opt/caselaw/entscheidsuche/<SPIDER>/
"""

from __future__ import annotations

import argparse
import concurrent.futures
import logging
import re
import threading
import time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

logger = logging.getLogger("entscheidsuche_download")

BASE_URL = "https://entscheidsuche.ch/docs"
DEFAULT_DEST = "/opt/caselaw/entscheidsuche"
USER_AGENT = "CaselawBot/2.0 (legal research; swiss-caselaw project)"

# ── Spider tiers ──────────────────────────────────────────────

# Tier A: cantonal courts with HTML text available (~247k)
TIER_A = [
    "GE_Gerichte",       # ~88k
    "TI_Gerichte",       # ~58k
    "VD_FindInfo",       # ~43k
    "VD_Omni",           # ~28k
    "BL_Gerichte",       # ~20k
    "SO_Omni",           # ~9k
    "NE_Omni",           # ~7k
    "LU_Gerichte",       # ~4k
]

# Tier B: cantonal courts, PDF-only or mixed (~63k)
TIER_B = [
    "GR_Gerichte",       # ~19k
    "FR_Gerichte",       # ~14k
    "SG_Gerichte",       # ~13k
    "BE_Verwaltungsgericht",  # ~11k
    "BE_ZivilStraf",     # ~6k
]

# Tier C: smaller cantonal courts (~40k)
TIER_C_SMALL = [
    "SG_Publikationen",
    "VS_Gerichte",
    "AR_Gerichte",
    "SZ_Gerichte",
    "SZ_Verwaltungsgericht",
    "TG_OG",
    "UR_Gerichte",
    "OW_Gerichte",
    "NW_Gerichte",
    "SH_OG",
    "JU_Gerichte",
    "GL_Omni",
    "AI_Aktuell",
    "AI_Bericht",
    "BE_Steuerrekurs",
    "BE_BVD",
    "BE_Weitere",
    "BE_Anwaltsaufsicht",
    "AG_Baugesetzgebung",
    "AG_Weitere",
]

# Tier D: federal courts + regulatory (gap-fill for older decisions)
TIER_D_FEDERAL = [
    "CH_BGE",            # ~59k
    "CH_BVGer",          # ~91k
    "CH_BSTG",           # ~20k
    "CH_VB",             # ~33k
    "CH_EDOEB",          # ~2k
    "CH_BGer",           # ~large, overlaps with direct scraper
    "CH_BPatG",
    "CH_Bundesrat",
    "CH_WEKO",
]

# Tier E: ZH courts (gap-fill, we have direct scrapers)
TIER_E_GAPFILL = [
    "ZH_Obergericht",
    "ZH_Sozialversicherungsgericht",
    "ZH_Verwaltungsgericht",
    "ZH_Baurekurs",
    "ZH_Steuerrekurs",
    "ZG_Obergericht",
    "ZG_Verwaltungsgericht",
    "BS_Omni",
    "AG_Gerichte",
    # "TA_SST" retired 2026-09-14: scrapers/ta_sst.py reads sportstribunal.ch directly.
]


def fetch_url(url: str, timeout: int = 60) -> bytes:
    """Fetch URL content with our user agent."""
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=timeout) as resp:
        return resp.read()


def get_spider_file_list(spider: str) -> list[str]:
    """
    Get the full file listing for a spider by parsing the directory listing HTML.
    Returns list of JSON filenames (without path prefix).
    """
    url = f"{BASE_URL}/{spider}/"
    logger.info(f"  Fetching file listing from {url}")
    html = fetch_url(url, timeout=120).decode("utf-8", errors="replace")

    # Parse href links to .json files from the autoindex HTML
    # Links look like: href="/docs/GE_Gerichte/GE_CJ_001_C-25672-2021_2025-02-13.json"
    # or href="GE_CJ_001_C-25672-2021_2025-02-13.json"
    pattern = re.compile(
        rf'href="(?:/docs/{re.escape(spider)}/)?([^"]+\.json)"'
    )
    json_files = pattern.findall(html)

    logger.info(f"  {spider}: {len(json_files)} JSON files in directory listing")
    return json_files


def is_valid_file(path: Path) -> bool:
    """Check if a downloaded file is valid (not a 404 HTML error page)."""
    if not path.exists():
        return False
    if path.stat().st_size == 0:
        return False
    try:
        with open(path, "rb") as f:
            header = f.read(500)
        if b"404 Not Found" in header:
            return False
        if path.suffix == ".json":
            if header.startswith(b"<!DOCTYPE") or header.startswith(b"<html"):
                return False
    except Exception:
        return False
    return True


def download_file(url: str, dest: Path, force: bool = False) -> bool:
    """
    Download a single file. Returns True if downloaded, False if skipped.
    Skips if file already exists and is valid (resume-friendly).
    """
    if not force and is_valid_file(dest):
        return False

    if dest.exists():
        dest.unlink()

    dest.parent.mkdir(parents=True, exist_ok=True)

    try:
        data = fetch_url(url, timeout=60)
        if b"404 Not Found" in data[:500]:
            return False
        dest.write_bytes(data)
        return True
    except (URLError, HTTPError, OSError) as e:
        logger.debug(f"  Failed: {url}: {e}")
        return False


def download_spider(
    spider: str,
    dest_dir: Path,
    dry_run: bool = False,
    delay: float = 0.02,
    max_workers: int = 8,
) -> tuple[int, int, int]:
    """
    Download all files for a spider.

    Tasks are dispatched through a thread pool of size ``max_workers``
    so the per-file HTTP latency overlaps. The serial path with the
    legacy 0.02 s sleep + per-file HTTP RTT is the bottleneck that
    causes the weekly opencaselaw-entscheidsuche.service to time out
    at the 18 h systemd cap (~600 K files = json + html companions
    across all tiers); the pool runs the same workload in roughly
    ``1 / max_workers`` of the wall-clock time.

    Returns (downloaded, skipped, errors).
    """
    logger.info(f"{'[DRY RUN] ' if dry_run else ''}Downloading {spider}...")

    try:
        json_files = get_spider_file_list(spider)
    except Exception as e:
        logger.error(f"Failed to get file list for {spider}: {e}")
        return 0, 0, 1

    if not json_files:
        logger.warning(f"  No files found for {spider}")
        return 0, 0, 0

    if dry_run:
        logger.info(f"  Would download {len(json_files)} JSON files + HTML companions")
        return 0, len(json_files), 0

    # Build download tasks: for each JSON, also queue its HTML companion
    tasks: list[tuple[str, Path]] = []
    for json_name in json_files:
        json_url = f"{BASE_URL}/{spider}/{json_name}"
        json_dest = dest_dir / spider / json_name
        tasks.append((json_url, json_dest))

        html_name = json_name.rsplit(".", 1)[0] + ".html"
        html_url = f"{BASE_URL}/{spider}/{html_name}"
        html_dest = dest_dir / spider / html_name
        tasks.append((html_url, html_dest))

    total = len(tasks)
    logger.info(
        f"  {len(json_files)} decisions, {total} files to check "
        f"(parallel={max_workers})"
    )

    # Counters guarded by lock so the workers can update them concurrently.
    counters = {"downloaded": 0, "skipped": 0, "errors": 0}
    lock = threading.Lock()

    def _worker(task: tuple[str, Path]) -> None:
        url, dest = task
        try:
            result = download_file(url, dest)
            with lock:
                if result:
                    counters["downloaded"] += 1
                elif is_valid_file(dest):
                    counters["skipped"] += 1
        except Exception as e:
            with lock:
                counters["errors"] += 1
            logger.debug(f"  Error: {url}: {e}")
        # Politeness delay applied inside the worker so it scales per
        # connection rather than per-task; effective rate stays under
        # max_workers / delay requests/sec.
        if delay > 0:
            time.sleep(delay)

    progress_every = max(2000, total // 20)
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=max_workers,
        thread_name_prefix=f"es-{spider}",
    ) as pool:
        for i, _ in enumerate(pool.map(_worker, tasks), start=1):
            if i % progress_every == 0:
                with lock:
                    snapshot = dict(counters)
                logger.info(
                    f"  [{spider}] {i}/{total} "
                    f"(+{snapshot['downloaded']} new, "
                    f"{snapshot['skipped']} exist, {snapshot['errors']} err)"
                )

    logger.info(
        f"  {spider} done: +{counters['downloaded']} downloaded, "
        f"{counters['skipped']} already existed, {counters['errors']} errors"
    )
    return counters["downloaded"], counters["skipped"], counters["errors"]


def purge_corrupted(dest_dir: Path, spider: str) -> int:
    """Delete corrupted 404 files for a spider."""
    spider_dir = dest_dir / spider
    if not spider_dir.exists():
        return 0

    purged = 0
    for f in spider_dir.iterdir():
        if f.suffix in (".json", ".html", ".pdf") and not is_valid_file(f):
            f.unlink()
            purged += 1

    if purged:
        logger.info(f"  Purged {purged} corrupted files from {spider}")
    return purged


def main():
    parser = argparse.ArgumentParser(
        description="Download decisions from entscheidsuche.ch"
    )
    parser.add_argument(
        "--spider", type=str, default=None,
        help="Download a single spider only"
    )
    parser.add_argument(
        "--dest", type=str, default=DEFAULT_DEST,
        help=f"Destination directory (default: {DEFAULT_DEST})"
    )
    parser.add_argument(
        "--all", action="store_true",
        help="Download all tiers (A + B + small cantons)"
    )
    parser.add_argument(
        "--tier-b", action="store_true",
        help="Include Tier B (PDF-only) spiders"
    )
    parser.add_argument(
        "--delay", type=float, default=0.02,
        help="Delay between requests in seconds, applied per worker "
             "(default: 0.02)"
    )
    parser.add_argument(
        "--max-workers", type=int, default=8,
        help="Concurrent downloads per spider (default: 8). 1 reverts "
             "to the historic serial path."
    )
    parser.add_argument(
        "--no-purge", action="store_true",
        help="Don't purge corrupted files before downloading"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be downloaded without downloading"
    )
    parser.add_argument("-v", "--verbose", action="store_true")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    dest_dir = Path(args.dest)
    dest_dir.mkdir(parents=True, exist_ok=True)

    if args.spider:
        spiders = [args.spider]
    elif args.all:
        spiders = TIER_A + TIER_B + TIER_C_SMALL + TIER_D_FEDERAL + TIER_E_GAPFILL
    elif args.tier_b:
        spiders = TIER_A + TIER_B
    else:
        spiders = TIER_A

    total_downloaded = 0
    total_skipped = 0
    total_errors = 0
    total_purged = 0

    logger.info(f"Downloading {len(spiders)} spiders to {dest_dir}")
    logger.info(f"Spiders: {', '.join(spiders)}")

    for spider in spiders:
        if not args.no_purge:
            total_purged += purge_corrupted(dest_dir, spider)

        dl, skip, err = download_spider(
            spider, dest_dir,
            dry_run=args.dry_run,
            delay=args.delay,
            max_workers=args.max_workers,
        )
        total_downloaded += dl
        total_skipped += skip
        total_errors += err

    logger.info("=" * 60)
    logger.info("Download complete")
    logger.info(f"  Spiders: {len(spiders)}")
    logger.info(f"  Purged corrupted: {total_purged}")
    logger.info(f"  Downloaded: {total_downloaded}")
    logger.info(f"  Already existed: {total_skipped}")
    logger.info(f"  Errors: {total_errors}")
    logger.info("=" * 60)
    logger.info(
        f"Next: python3 entscheidsuche_ingest.py "
        f"--input {dest_dir} "
        f"--output /opt/caselaw/repo/output/decisions"
    )


if __name__ == "__main__":
    main()
