#!/usr/bin/env python3
"""Generic WordPress-REST-API scraper.

Drop-in adapter for any WordPress site that exposes /wp-json/wp/v2/...
Use cases on opencaselaw:
  - medialex.ch — Zeitschrift für Medienrecht, 268 posts
  - eizpublishing.ch (EuZ) — uses custom post types `publikationen`
    (161) and `artikel` (87); standard `posts` is empty.

Each source supplies a post_type (default 'posts') and optional taxonomy/
author maps. Authors are resolved from /wp-json/wp/v2/users.

Output: output/legal_scholarship/<key>.jsonl
"""
from __future__ import annotations

import argparse
import html as html_lib
import json
import logging
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from ._atomic import atomic_jsonl

log = logging.getLogger("scholarship.wordpress")

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = REPO_ROOT / "output" / "legal_scholarship"

USER_AGENT = "OpenCaseLaw-scholarship/0.1 (+https://opencaselaw.ch)"

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _strip_html(s: str | None) -> str:
    if not s:
        return ""
    return _WS_RE.sub(" ", html_lib.unescape(_TAG_RE.sub(" ", s))).strip()


def _fetch_json(url: str, timeout: int = 30):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read()), dict(r.headers)


def harvest(
    *,
    base_url: str,
    source_key: str,
    post_type: str = "posts",
    license_default: str | None = None,
    license_url_default: str | None = None,
    pub_type: str = "article",
    extra_post_types: list[str] | None = None,
    max_records: int | None = None,
    output_dir: Path = DEFAULT_OUT,
    rate_limit: float = 0.5,
) -> dict:
    """Pull every published post (or custom-type item) via /wp-json/wp/v2.

    base_url: e.g. "https://medialex.ch"
    post_type: 'posts' for the default WP type; otherwise the rest_base
               of a custom type (e.g. 'publikationen' for eizpublishing).
    extra_post_types: also harvest these custom-type rest_bases (for
               sites where content is split across multiple types).
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"{source_key}.jsonl"
    started = time.time()

    # Resolve authors first
    authors_by_id: dict[int, str] = {}
    try:
        offset = 0
        while True:
            url = f"{base_url}/wp-json/wp/v2/users?per_page=100&offset={offset}"
            try:
                batch, headers = _fetch_json(url)
            except Exception as e:
                log.warning("users fetch failed: %s", e)
                break
            if not batch:
                break
            for u in batch:
                if isinstance(u, dict):
                    authors_by_id[u.get("id")] = (
                        u.get("name") or u.get("slug") or "unknown"
                    )
            if len(batch) < 100:
                break
            offset += len(batch)
    except Exception as e:
        log.warning("users iteration failed: %s", e)
    log.info("authors loaded: %d", len(authors_by_id))

    total = 0
    types_to_walk = [post_type] + (extra_post_types or [])
    with atomic_jsonl(out_path) as fh:
        for ptype in types_to_walk:
            log.info("walking post_type=%s", ptype)
            page = 1
            while True:
                url = (
                    f"{base_url}/wp-json/wp/v2/{ptype}"
                    f"?per_page=50&page={page}&_embed=author,wp:term"
                )
                try:
                    batch, headers = _fetch_json(url)
                except urllib.error.HTTPError as e:
                    if e.code == 400:
                        # WP REST returns 400 when page exceeds total
                        break
                    log.error("page %d fetch failed: %s", page, e)
                    break
                except Exception as e:
                    log.error("page %d fetch failed: %s", page, e)
                    break
                if not batch:
                    break
                for row in batch:
                    if not isinstance(row, dict):
                        continue
                    title = _strip_html(
                        (row.get("title") or {}).get("rendered") or ""
                    )
                    if not title:
                        continue
                    excerpt = _strip_html(
                        (row.get("excerpt") or {}).get("rendered") or ""
                    )
                    content = _strip_html(
                        (row.get("content") or {}).get("rendered") or ""
                    )
                    pub_date = row.get("date") or row.get("date_gmt")
                    year = None
                    if pub_date:
                        m = re.match(r"(\d{4})", pub_date)
                        if m:
                            year = int(m.group(1))
                    # Authors: prefer _embedded.author, fall back to author id
                    authors = []
                    if "_embedded" in row and row["_embedded"].get("author"):
                        for a in row["_embedded"]["author"]:
                            if isinstance(a, dict) and a.get("name"):
                                authors.append(a["name"])
                    elif row.get("author") in authors_by_id:
                        authors.append(authors_by_id[row["author"]])
                    # Tags / categories
                    subjects = []
                    if "_embedded" in row and row["_embedded"].get("wp:term"):
                        for term_group in row["_embedded"]["wp:term"]:
                            if isinstance(term_group, list):
                                for t in term_group:
                                    if isinstance(t, dict) and t.get("name"):
                                        subjects.append(t["name"])

                    rec = {
                        "source": source_key,
                        "source_record_id": f"wp:{ptype}:{row.get('id')}",
                        "datestamp": row.get("modified"),
                        "pub_type": pub_type,
                        "title": title,
                        "authors": authors,
                        "abstract": excerpt or None,
                        "full_text": content[:32_000] if content else None,
                        "publication_date": pub_date,
                        "year": year,
                        "url": row.get("link"),
                        "language": "de" if base_url.endswith(".ch") else None,
                        "license": license_default,
                        "license_url": license_url_default,
                        "rights_raw": [license_default] if license_default else [],
                        "subjects": subjects,
                    }
                    fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    total += 1
                    if max_records and total >= max_records:
                        break
                if max_records and total >= max_records:
                    break
                if len(batch) < 50:
                    break
                page += 1
                time.sleep(rate_limit)
            if max_records and total >= max_records:
                break

    summary = {
        "source": source_key,
        "scraped": total,
        "elapsed_seconds": round(time.time() - started, 1),
        "output_path": str(out_path),
    }
    log.info("Done: %s", summary)
    return summary


# Source-specific entry points (called by harvest_all via custom_module)

class MedialexAdapter:
    @staticmethod
    def harvest(*, max_records=None, **kwargs):
        return harvest(
            base_url="https://medialex.ch",
            source_key="medialex",
            license_default="CC-BY-SA-4.0",
            license_url_default="https://creativecommons.org/licenses/by-sa/4.0/",
            pub_type="article",
            max_records=max_records,
        )


class EizpublishingAdapter:
    @staticmethod
    def harvest(*, max_records=None, **kwargs):
        return harvest(
            base_url="https://eizpublishing.ch",
            source_key="eizpublishing",
            post_type="publikationen",
            extra_post_types=["artikel"],
            license_default="CC-BY-NC-ND-4.0",
            license_url_default="https://creativecommons.org/licenses/by-nc-nd/4.0/",
            pub_type="article",
            max_records=max_records,
        )


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--source", choices=["medialex", "eizpublishing"], required=True)
    p.add_argument("--max-records", type=int, default=None)
    args = p.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    if args.source == "medialex":
        summary = MedialexAdapter.harvest(max_records=args.max_records)
    else:
        summary = EizpublishingAdapter.harvest(max_records=args.max_records)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
