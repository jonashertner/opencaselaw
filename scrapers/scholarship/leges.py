#!/usr/bin/env python3
"""LeGes (Bundeskanzlei) — federal legislation & evaluation journal.

Hosted on leges.weblaw.ch (Weblaw's publication platform). No OAI-PMH;
no clean archive list page. URL structure is deterministic enough to
enumerate:

  Issue page:   /legesissues/{YEAR}/{N}.html      (year, issue 1..4)
  Article page: /legesissues/{YEAR}/{N}/{slug}_{hash8}.html

Strategy:
  1. Probe each (year, issue) combination 2008..currentyear × 1..4.
     Issue 200-pages reveal their article links.
  2. For each article URL found, fetch its HTML and extract title +
     authors + abstract.
  3. License: Federal Chancellery publication — per Art. 5(1)(a) URG,
     no copyright on official Confederation publications. Mark
     license=OA-Swiss-federal, attribution-courteous.

Output: output/legal_scholarship/leges.jsonl
"""
from __future__ import annotations

import argparse
import html as html_lib
import json
import logging
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from ._atomic import atomic_jsonl

log = logging.getLogger("scholarship.leges")

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = REPO_ROOT / "output" / "legal_scholarship"

BASE = "https://leges.weblaw.ch"
USER_AGENT = "OpenCaseLaw-scholarship/0.1 (+https://opencaselaw.ch)"

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_HASH_SUFFIX_RE = re.compile(r"_[a-f0-9]{8,}$", re.I)


def _strip(s: str) -> str:
    return _WS_RE.sub(" ", html_lib.unescape(_TAG_RE.sub(" ", s))).strip()


def _fetch(url: str, timeout: int = 20) -> tuple[int, str]:
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception:
        return -1, ""


def _extract_article_urls(issue_html: str) -> list[str]:
    """Pull all distinct /legesissues/YYYY/N/slug_hash.html links."""
    return sorted(set(re.findall(
        r"/legesissues/\d{4}/\d+/[a-z0-9-]+_[a-f0-9]{8,}(?:\d)?\.html",
        issue_html,
    )))


def _extract_article_meta(html: str) -> dict:
    title_m = re.search(r"<title>([^<]+)</title>", html)
    title = html_lib.unescape(title_m.group(1)).strip() if title_m else ""
    # Strip trailing journal/issue tail like "| LeGes 33 (2022) 3"
    title = re.sub(r"\s*[\|\-]\s*LeGes.*$", "", title).strip()

    meta_d = {}
    for k, v in re.findall(
        r'<meta[^>]*(?:name|property)="([^"]+)"[^>]*content="([^"]+)"', html,
    ):
        meta_d[k.lower()] = html_lib.unescape(v).strip()

    desc = meta_d.get("og:description") or meta_d.get("description") or ""
    author = meta_d.get("author") or meta_d.get("dc.creator") or ""
    # Body article block (best-effort)
    body_m = re.search(r"<article[^>]*>(.*?)</article>", html, re.DOTALL)
    if body_m is None:
        body_m = re.search(
            r"<div[^>]+class=\"[^\"]*article[^\"]*\"[^>]*>(.*?)</div>\s*</",
            html, re.DOTALL,
        )
    full_text = _strip(body_m.group(1)) if body_m else None
    if full_text and len(full_text) > 32_000:
        full_text = full_text[:32_000] + " …"
    return {
        "title": title,
        "abstract": desc or None,
        "author": author or None,
        "full_text": full_text,
    }


def harvest(*, max_records: int | None = None,
            output_dir: Path = DEFAULT_OUT,
            rate_limit: float = 0.4,
            year_start: int = 1989,
            year_end: int | None = None) -> dict:
    """Walk every (year, issue) page; collect articles; emit JSONL."""
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / "leges.jsonl"
    started = time.time()
    if year_end is None:
        year_end = datetime.now(timezone.utc).year

    article_urls: list[str] = []
    issues_found = 0
    for year in range(year_start, year_end + 1):
        for n in range(1, 5):  # 1-4 issues per year
            url = f"{BASE}/legesissues/{year}/{n}.html"
            code, html = _fetch(url)
            if code != 200 or len(html) < 5_000 or "404" in html[:200]:
                continue
            issues_found += 1
            urls = _extract_article_urls(html)
            for u in urls:
                article_urls.append(BASE + u)
            log.info("issue %d/%d: %d articles", year, n, len(urls))
            time.sleep(rate_limit)

    article_urls = sorted(set(article_urls))
    log.info("total issues=%d, distinct articles=%d", issues_found, len(article_urls))

    total = 0
    with atomic_jsonl(out_path) as fh:
        for url in article_urls:
            code, html = _fetch(url)
            if code != 200 or len(html) < 2000:
                continue
            meta = _extract_article_meta(html)
            if not meta["title"]:
                continue
            # Year + issue from URL
            m = re.search(r"/legesissues/(\d{4})/(\d+)/", url)
            year = int(m.group(1)) if m else None
            issue = m.group(2) if m else None

            # Author splits "X / Y" pattern
            authors = []
            if meta["author"]:
                for a in re.split(r"[/,;&]| und ", meta["author"]):
                    a = a.strip()
                    if a:
                        authors.append(a)

            # Source-record id: derive from the slug+hash in URL
            slug = url.rsplit("/", 1)[-1].rsplit(".", 1)[0]

            record = {
                "source": "leges",
                "source_record_id": f"leges:{year}/{issue}/{slug}",
                "pub_type": "article",
                "title": meta["title"],
                "authors": authors,
                "abstract": meta["abstract"],
                "full_text": meta["full_text"],
                "publication_date": f"{year}" if year else None,
                "year": year,
                "url": url,
                "publisher": "Bundeskanzlei (Swiss Federal Chancellery)",
                "language": "de",  # LeGes mostly DE/FR; per-article lang would need detection
                "license": "OA-Swiss-federal",
                "license_url": (
                    "https://www.bk.admin.ch/bk/de/home/dokumentation/"
                    "zeitschrift--leges-.html"
                ),
                "rights_raw": [
                    "Swiss Federal publication — no copyright per "
                    "Art. 5(1)(a) URG"
                ],
                "subjects": ["Gesetzgebung", "Evaluation", "Swiss law"],
                "sources_raw": [f"LeGes {year}/{issue}" if year else "LeGes"],
            }
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            total += 1
            if max_records and total >= max_records:
                break
            time.sleep(rate_limit)

    summary = {
        "source": "leges",
        "issues_found": issues_found,
        "scraped": total,
        "elapsed_seconds": round(time.time() - started, 1),
        "output_path": str(out_path),
    }
    log.info("Done: %s", summary)
    return summary


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--max-records", type=int, default=None)
    p.add_argument("--year-start", type=int, default=1989)
    p.add_argument("--year-end", type=int, default=None)
    args = p.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    summary = harvest(
        max_records=args.max_records,
        year_start=args.year_start, year_end=args.year_end,
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
