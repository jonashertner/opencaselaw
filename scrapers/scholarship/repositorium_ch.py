#!/usr/bin/env python3
"""Scraper for Repositorium.ch — Swiss-law-specific disciplinary repository.

Backed by Supabase. The site's public SPA bundle ships its anonymous JWT
key inline (intended public access). PostgREST-style REST API at
api.repositorium.ch/rest/v1/repo with rich per-record metadata.

Schema (per row in `repo` table):
  id, titel, author (uuid), coauthors[], abstract, content/text,
  doi, isbn, sprache, peer_review, public, license, tags[], custom_tags[],
  typ ('Zeitschriftenartikel' | 'Buch' | 'Dissertation' | …),
  erschienen_in (journal/source), erscheinungsort, erschienen_am,
  verlag, datei_url, link_zur_originalpublikation, zitiervorschlag,
  anzahl_seiten, date_created, date_updated, created_at

The `author` field is a UUID into the `profiles` table; we expand it to
"Lastname, Firstname" via a paged join.

Output: output/legal_scholarship/repositorium_ch.jsonl
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from ._atomic import atomic_jsonl

log = logging.getLogger("scholarship.repositorium_ch")

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = REPO_ROOT / "output" / "legal_scholarship"

API_BASE = "https://api.repositorium.ch/rest/v1"
# Public anonymous JWT key — embedded in the SPA bundle at
# https://repositorium.ch/assets/index.*.js (intentional public access).
ANON_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImRqZnFyZ3locnF1endhcmNla3FjIiwicm9sZSI6"
    "ImFub24iLCJpYXQiOjE2ODUyODA3NDIsImV4cCI6MjAwMDg1Njc0Mn0."
    "fpX6TPr9Q0lQHzqut69dds3DSBwtbz3bFUuLo1zUcRA"
)
USER_AGENT = "OpenCaseLaw-scholarship/0.1 (+https://opencaselaw.ch)"


def _fetch_json(path: str, params: dict | None = None, timeout: int = 30):
    qs = ("?" + urllib.parse.urlencode(params, doseq=True)) if params else ""
    url = f"{API_BASE}{path}{qs}"
    req = urllib.request.Request(
        url,
        headers={
            "apikey": ANON_KEY,
            "Authorization": f"Bearer {ANON_KEY}",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _normalize_typ(t: str | None) -> str:
    if not t:
        return "article"
    tl = t.lower()
    if "dissertation" in tl:
        return "dissertation"
    if "buch" in tl or "book" in tl or "monograph" in tl:
        return "book"
    if "kapitel" in tl or "chapter" in tl:
        return "chapter"
    if "kommentar" in tl or "commentary" in tl:
        return "commentary"
    if "zeitschrift" in tl or "article" in tl or "artikel" in tl:
        return "article"
    if "report" in tl or "bericht" in tl:
        return "report"
    return "article"


def harvest(*, max_records: int | None = None,
            output_dir: Path = DEFAULT_OUT,
            rate_limit: float = 0.3) -> dict:
    """Pull every public publication from Repositorium.ch.

    Returns a summary dict (called by harvest_all.run_source).
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / "repositorium_ch.jsonl"

    started = time.time()

    # First fetch author profiles so we can resolve UUID→name.
    # Real schema (discovered 2026-05-27): id, username, full_name,
    # avatar_url, website, about, linkedin, updated_at.
    log.info("fetching profiles map…")
    profiles_by_id: dict[str, str] = {}
    try:
        offset = 0
        while True:
            batch = _fetch_json(
                "/profiles",
                {
                    "select": "id,username,full_name",
                    "limit": "1000",
                    "offset": str(offset),
                },
            )
            if not batch:
                break
            for p in batch:
                pid = p.get("id")
                if not pid:
                    continue
                name = p.get("full_name") or p.get("username") or pid
                profiles_by_id[pid] = name.strip()
            if len(batch) < 1000:
                break
            offset += len(batch)
    except Exception as e:
        log.warning("profile fetch failed (%s) — author UUIDs won't resolve", e)

    log.info("profiles loaded: %d", len(profiles_by_id))

    # Now fetch the publications.
    total = 0
    failed = 0
    offset = 0
    PAGE = 200
    with atomic_jsonl(out_path) as fh:
        while True:
            try:
                batch = _fetch_json(
                    "/repo",
                    {
                        "select": "*",
                        "public": "eq.true",
                        "limit": str(PAGE),
                        "offset": str(offset),
                        "order": "created_at.desc",
                    },
                )
            except Exception as e:
                log.error("repo fetch failed at offset %d: %s", offset, e)
                failed += 1
                if failed > 3:
                    break
                time.sleep(rate_limit * 5)
                continue
            failed = 0
            if not batch:
                break
            for row in batch:
                if not row.get("public"):
                    continue
                author_uuid = row.get("author")
                author_name = (
                    profiles_by_id.get(author_uuid)
                    if author_uuid else None
                )
                authors_list = [author_name] if author_name else []
                co = row.get("coauthors") or []
                if isinstance(co, list):
                    authors_list.extend([str(x) for x in co if x])
                year = None
                d = row.get("erschienen_am") or row.get("date_created") or row.get("created_at")
                if d:
                    import re
                    m = re.match(r"(\d{4})", d)
                    if m:
                        year = int(m.group(1))
                subjects = []
                for k in ("tags", "custom_tags"):
                    v = row.get(k)
                    if isinstance(v, list):
                        subjects.extend([str(x) for x in v if x])
                pub_type = _normalize_typ(row.get("typ"))
                record = {
                    "source": "repositorium_ch",
                    "source_record_id": f"repo:{row['id']}",
                    "datestamp": row.get("date_updated") or row.get("created_at"),
                    "pub_type": pub_type,
                    "title": row.get("titel") or "(untitled)",
                    "authors": authors_list,
                    "abstract": row.get("abstract"),
                    "publication_date": row.get("erschienen_am"),
                    "year": year,
                    "publisher": row.get("verlag"),
                    "doi": row.get("doi"),
                    "isbn": row.get("isbn"),
                    "url": (
                        row.get("link_zur_originalpublikation")
                        or f"https://repositorium.ch/publikation/{row['id']}"
                    ),
                    "language": (row.get("sprache") or "").lower() or None,
                    "license": row.get("license"),
                    "rights_raw": [row.get("license")] if row.get("license") else [],
                    "subjects": subjects,
                    "sources_raw": [row.get("erschienen_in")] if row.get("erschienen_in") else [],
                    "full_text": row.get("text") or row.get("content"),
                }
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                total += 1
                if max_records and total >= max_records:
                    break
            if max_records and total >= max_records:
                break
            if len(batch) < PAGE:
                break
            offset += len(batch)
            time.sleep(rate_limit)

    summary = {
        "source": "repositorium_ch",
        "scraped": total,
        "profiles_resolved": len(profiles_by_id),
        "elapsed_seconds": round(time.time() - started, 1),
        "output_path": str(out_path),
    }
    log.info("Done: %s", summary)
    return summary


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--max-records", type=int, default=None)
    p.add_argument("--output-dir", type=Path, default=DEFAULT_OUT)
    args = p.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    summary = harvest(max_records=args.max_records, output_dir=args.output_dir)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
