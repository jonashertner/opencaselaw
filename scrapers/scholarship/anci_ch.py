#!/usr/bin/env python3
"""anci.ch (Ancilla Iuris) — custom scraper.

The journal site has both:
  - Static PDFs at /articles/Ancilla{year}_{page}_{lastname}.pdf
  - Article landing pages at /articles/{id}

We walk the homepage for the master list of article IDs, then fetch each
landing page to extract title, then derive year + author from the PDF
filename. Small journal (~100 articles total).

Output: output/legal_scholarship/anci_ch.jsonl
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

log = logging.getLogger("scholarship.anci_ch")

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = REPO_ROOT / "output" / "legal_scholarship"

HOMEPAGE = "https://www.anci.ch/"
USER_AGENT = "OpenCaseLaw-scholarship/0.1 (+https://opencaselaw.ch)"

# Filename pattern: Ancilla{year}_{page}_{lastname}.pdf OR
# {year}Ancilla_{lastname}_{page}.pdf (mixed conventions in the archive)
_FN_RE_A = re.compile(r"Ancilla(\d{4})_([0-9]+)_([A-Za-z]+)\.pdf", re.I)
_FN_RE_B = re.compile(r"Ancilla(\d{4})_([A-Za-z]+)_([0-9]+)\.pdf", re.I)
_FN_RE_C = re.compile(r"(\d{4})Ancilla_([A-Za-z_]+)_(\d+)\.pdf", re.I)


def _fetch(url: str, timeout: int = 15) -> str:
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def parse_filename(fn: str) -> dict:
    """Best-effort year/page/lastname extraction from PDF filename."""
    for rx, layout in [(_FN_RE_A, "ypa"), (_FN_RE_B, "yap"), (_FN_RE_C, "yap")]:
        m = rx.search(fn)
        if m:
            g = m.groups()
            if layout == "ypa":
                return {"year": int(g[0]), "page": g[1], "author": g[2]}
            return {"year": int(g[0]), "author": g[1], "page": g[2]}
    return {}


def harvest(*, max_records: int | None = None,
            output_dir: Path = DEFAULT_OUT,
            rate_limit: float = 0.4) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / "anci_ch.jsonl"
    started = time.time()

    homepage = _fetch(HOMEPAGE)
    article_ids = sorted(set(
        re.findall(r"href=\"https://anci\.ch/articles/(\d+)\"", homepage)
    ))
    pdf_links = sorted(set(re.findall(
        r"href=\"(/articles/[^\"]+\.pdf)\"", homepage,
    )))
    log.info("homepage: %d article landing pages + %d PDFs",
             len(article_ids), len(pdf_links))

    # Build a map: lastname → PDF URL (so we can later attach PDFs to articles)
    pdf_by_author: dict[str, str] = {}
    for path in pdf_links:
        fn = path.rsplit("/", 1)[-1]
        info = parse_filename(fn)
        if info.get("author"):
            key = info["author"].lower()
            pdf_by_author[key] = "https://www.anci.ch" + path

    total = 0
    with atomic_jsonl(out_path) as fh:
        # Index articles by their landing page IDs
        for aid in article_ids:
            url = f"https://anci.ch/articles/{aid}"
            try:
                html = _fetch(url)
            except Exception as e:
                log.warning("fetch %s: %s", url, e)
                continue
            title_m = re.search(r"<title>([^<]+)</title>", html)
            title = html_lib.unescape(title_m.group(1)) if title_m else None
            if title:
                title = title.replace(" – Ancilla Iuris", "").strip()
            # Authors: try <h2> or <h3> or meta tag
            authors_m = re.findall(
                r'<meta[^>]+name="author"[^>]+content="([^"]+)"', html,
            )
            authors = list(authors_m) if authors_m else []
            # Description (abstract)
            desc_m = re.search(
                r'<meta[^>]+(?:name|property)="(?:description|og:description)"[^>]+content="([^"]+)"',
                html,
            )
            abstract = html_lib.unescape(desc_m.group(1)).strip() if desc_m else None

            # Try to find the PDF link inside the article page
            pdf_m = re.search(r'href="(/articles/[^"]+\.pdf)"', html)
            pdf_url = ("https://www.anci.ch" + pdf_m.group(1)) if pdf_m else None
            year = None
            if pdf_url:
                fn = pdf_url.rsplit("/", 1)[-1]
                info = parse_filename(fn)
                year = info.get("year")
                # If meta-author missing, use last-name from PDF
                if not authors and info.get("author"):
                    authors = [info["author"]]
            if not title:
                continue

            record = {
                "source": "anci_ch",
                "source_record_id": f"anci:{aid}",
                "pub_type": "article",
                "title": title,
                "authors": authors,
                "abstract": abstract,
                "publication_date": str(year) if year else None,
                "year": year,
                "url": url,
                "pdf_url": pdf_url,
                "publisher": "Ancilla Iuris",
                "language": "de",  # mostly DE; some EN
                "license": "CC-BY-4.0",
                "license_url": "https://creativecommons.org/licenses/by/4.0/",
                "rights_raw": ["CC-BY-4.0"],
                "subjects": ["Swiss law", "philosophy of law", "legal theory"],
                "sources_raw": ["Ancilla Iuris"],
            }
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            total += 1
            if max_records and total >= max_records:
                break
            time.sleep(rate_limit)

    summary = {
        "source": "anci_ch",
        "scraped": total,
        "elapsed_seconds": round(time.time() - started, 1),
        "output_path": str(out_path),
    }
    log.info("Done: %s", summary)
    return summary


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--max-records", type=int, default=None)
    args = p.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    summary = harvest(max_records=args.max_records)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
