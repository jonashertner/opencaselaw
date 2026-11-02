"""Re-parse the Akoma Ntoso documents of materialien.db with the current parser.

Ingest keeps a document whose file hash is unchanged, so a parser fix reaches
only new documents. This rewrites the paragraphs of the stored XML documents
from their source files. Identity, citation, title and dates stay; so does a
document for which the file yields nothing. Run scripts/enrich_botschaft_links.py
afterwards: the links follow the anchors.

Never against the live file:

    python3 -m search_stack.materialien_swap --db output/materialien.db -- \\
        python3 scripts/reparse_xml_materialien.py --db {db} --rate-limit 0.5

Usage:
    python3 scripts/reparse_xml_materialien.py --db copy.db --cache-dir xml/   # offline
    python3 scripts/reparse_xml_materialien.py --db copy.db --dry-run
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from search_stack.build_botschaft_corpus import (  # noqa: E402
    FATAL_HTTP_STATUSES, STOP_AFTER_CONSECUTIVE_FAILURES, file_host_allowed,
    parse_akoma_ntoso_xml,
)

log = logging.getLogger("reparse_xml_materialien")
USER_AGENT = "opencaselaw-materialien/0.3"
EXIT_STOPPED = 4


class NotXml(ValueError):
    """Fedlex answers 200 with its HTML app shell for a file it does not have."""


def fetch_xml(url: str, timeout: int = 60) -> bytes:
    if not file_host_allowed(url):
        raise NotXml(f"file hosted off Fedlex: {url[:100]}")
    with urlopen(Request(url, headers={"User-Agent": USER_AGENT}), timeout=timeout) as r:
        body = r.read()
    if b"<akomaNtoso" not in body[:2000]:
        raise NotXml(f"not Akoma Ntoso: {url[:100]}")
    return body


def replace_paragraphs(conn: sqlite3.Connection, botschaft_id: int, paragraphs: list[dict],
                       raw: bytes) -> None:
    conn.execute("DELETE FROM botschaft_paragraphs WHERE botschaft_id = ?", (botschaft_id,))
    conn.executemany(
        "INSERT INTO botschaft_paragraphs (botschaft_id, para_order, page_number, "
        "section_path, article_anchor, text, text_length) VALUES (?,?,?,?,?,?,?)",
        [(botschaft_id, p["para_order"], p["page_number"], p["section_path"],
          p["article_anchor"], p["text"], p["text_length"]) for p in paragraphs])
    conn.execute(
        "UPDATE botschaft_documents SET text_hash = ?, ingested_at = ? WHERE botschaft_id = ?",
        (hashlib.sha256(raw).hexdigest(), datetime.now(timezone.utc).isoformat(), botschaft_id))


def reparse(conn: sqlite3.Connection, *, cache_dir: Path | None = None, rate_limit: float = 0.5,
            dry_run: bool = False, limit: int | None = None, cache_only: bool = False,
            fetch=fetch_xml, sleep=time.sleep) -> dict:
    docs = conn.execute(
        "SELECT botschaft_id, source_url FROM botschaft_documents "
        "WHERE format = 'akoma-ntoso-xml' ORDER BY botschaft_id").fetchall()
    if limit:
        docs = docs[:limit]
    stats = {"documents": len(docs), "reparsed": 0, "empty": 0, "failed": 0, "not_cached": 0,
             "paragraphs_before": 0, "paragraphs_after": 0, "stopped": False}
    consecutive = 0
    for bid, url in docs:
        cached = cache_dir / f"{bid}.xml" if cache_dir else None
        try:
            if cached is not None and cached.exists():
                raw = cached.read_bytes()
            elif cache_only:
                stats["not_cached"] += 1
                continue
            else:
                raw = fetch(url)
                sleep(rate_limit)
        except HTTPError as e:
            log.warning(f"  {bid}: HTTP {e.code} for {url[:100]}")
            stats["failed"] += 1
            consecutive += 1
            if e.code in FATAL_HTTP_STATUSES or consecutive >= STOP_AFTER_CONSECUTIVE_FAILURES:
                stats["stopped"] = True
                break
            continue
        except (OSError, NotXml) as e:
            log.warning(f"  {bid}: {e}")
            stats["failed"] += 1
            consecutive += 1
            if consecutive >= STOP_AFTER_CONSECUTIVE_FAILURES:
                stats["stopped"] = True
                break
            continue
        consecutive = 0
        paragraphs = list(parse_akoma_ntoso_xml(raw))
        if not paragraphs:
            stats["empty"] += 1
            continue
        stats["paragraphs_before"] += conn.execute(
            "SELECT COUNT(*) FROM botschaft_paragraphs WHERE botschaft_id = ?", (bid,)).fetchone()[0]
        stats["paragraphs_after"] += len(paragraphs)
        stats["reparsed"] += 1
        if not dry_run:
            with conn:
                replace_paragraphs(conn, bid, paragraphs, raw)
    return stats


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s",
                        datefmt="%H:%M:%S")
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--db", required=True)
    ap.add_argument("--cache-dir", type=Path, help="Read <botschaft_id>.xml from here first")
    ap.add_argument("--cache-only", action="store_true", help="Never fetch; skip what is not cached")
    ap.add_argument("--rate-limit", type=float, default=0.5)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    conn = sqlite3.connect(args.db)
    stats = reparse(conn, cache_dir=args.cache_dir, rate_limit=args.rate_limit,
                    dry_run=args.dry_run, limit=args.limit, cache_only=args.cache_only)
    conn.close()
    log.info(str(stats))
    return EXIT_STOPPED if stats["stopped"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
