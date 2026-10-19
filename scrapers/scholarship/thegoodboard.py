#!/usr/bin/env python3
"""Scraper for thegoodboard.ch — Jonas Hertner's OA reference on Swiss
corporate governance.

The site exposes a clean sitemap.xml + structured Open Graph metadata +
LLM-friendly /llms.txt. Author has explicitly permitted ingestion by
training corpora and retrieval systems (see /llms.txt).

URL taxonomy → publication type:
  /reference/*        → 'reference_article'  (8 living articles)
  /commentary/*       → 'commentary'         (case commentary)
  /agenda/*           → 'guidance'           (14 board briefings)
  /glossary/*         → 'glossary_entry'     (term definitions)
  /prompts/*          → skip                 (not scholarship)
  / and /<lang>/      → skip                 (landing pages)
  /agenda/, /commentary/, /reference/, /glossary/ index pages → skip

Output: output/legal_scholarship/thegoodboard.jsonl
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
from xml.etree import ElementTree as ET

from ._atomic import atomic_jsonl

log = logging.getLogger("scholarship.thegoodboard")

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = REPO_ROOT / "output" / "legal_scholarship"

SITEMAP_URL = "https://thegoodboard.ch/sitemap.xml"
HOMEPAGE = "https://thegoodboard.ch/"
USER_AGENT = "OpenCaseLaw-scholarship/0.1 (+https://opencaselaw.ch)"

SITEMAP_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"

# Path prefix → publication type. Index pages (the bare /reference/ etc.)
# are skipped by the trailing-slash discriminator.
_SECTIONS = [
    ("/reference/",  "reference_article"),
    ("/commentary/", "commentary"),
    ("/agenda/",     "guidance"),
    ("/glossary/",   "glossary_entry"),
]

# Locales served on subpaths; we keep English (root) as canonical and store
# the alternates as separate publication records via the hreflang map.
_LOCALE_PREFIXES = ("/de/", "/fr/", "/it/", "/rm/")
_LOCALE_TO_LANG = {"/de/": "de", "/fr/": "fr", "/it/": "it", "/rm/": "rm"}


def _classify_url(url: str) -> tuple[str | None, str]:
    """Return (pub_type, language) or (None, '') to skip."""
    p = urllib.parse.urlparse(url)
    path = p.path
    language = "en"
    for pre, lang in _LOCALE_TO_LANG.items():
        if path.startswith(pre):
            language = lang
            path = "/" + path[len(pre):]
            break
    for prefix, pub_type in _SECTIONS:
        if path.startswith(prefix) and path.rstrip("/") != prefix.rstrip("/"):
            # Skip the section index pages
            return pub_type, language
    return None, ""


def _fetch(url: str, timeout: int = 20) -> str:
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "text/html, application/xml"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def _parse_sitemap(xml: str) -> list[tuple[str, str | None]]:
    """Return list of (loc, lastmod) tuples from a sitemap XML."""
    out: list[tuple[str, str | None]] = []
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as e:
        log.warning("sitemap parse failed: %s", e)
        return out
    for url in root.findall(f"{{{SITEMAP_NS}}}url"):
        loc = url.findtext(f"{{{SITEMAP_NS}}}loc") or ""
        lastmod = url.findtext(f"{{{SITEMAP_NS}}}lastmod")
        if loc:
            out.append((loc.strip(), (lastmod or "").strip() or None))
    return out


_META_RE = re.compile(
    r'<meta[^>]*(?:name|property)="([^"]+)"[^>]*content="([^"]*)"',
    re.IGNORECASE,
)
_TITLE_RE = re.compile(r"<title>([^<]+)</title>", re.IGNORECASE)
_H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.IGNORECASE | re.DOTALL)
_ARTICLE_RE = re.compile(
    r"<article[^>]*>(.*?)</article>", re.IGNORECASE | re.DOTALL,
)
_MAIN_RE = re.compile(r"<main[^>]*>(.*?)</main>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _strip_html(s: str) -> str:
    s = _TAG_RE.sub(" ", s)
    s = html_lib.unescape(s)
    return _WS_RE.sub(" ", s).strip()


def _extract_page(url: str, html: str, lastmod: str | None) -> dict:
    """Pull structured fields out of a thegoodboard.ch page."""
    metas = {k.lower(): v for k, v in _META_RE.findall(html)}
    title = (
        metas.get("og:title") or metas.get("twitter:title") or ""
    ).strip()
    if not title:
        m = _TITLE_RE.search(html)
        if m:
            title = html_lib.unescape(m.group(1)).strip()
    if not title:
        m = _H1_RE.search(html)
        if m:
            title = _strip_html(m.group(1))
    description = (
        metas.get("og:description") or metas.get("description") or ""
    ).strip()
    # Body — prefer <article>, fall back to <main>.
    body = ""
    m = _ARTICLE_RE.search(html)
    if m is None:
        m = _MAIN_RE.search(html)
    if m is not None:
        body = _strip_html(m.group(1))
    # Trim very long bodies — keep the first ~30k chars (more than enough
    # for FTS5; longer pages get truncated to bound DB size on first build).
    if len(body) > 32_000:
        body = body[:32_000] + " …"
    pub_date = lastmod or metas.get("article:modified_time") or metas.get("article:published_time")
    year = None
    if pub_date:
        m = re.match(r"(\d{4})", pub_date)
        year = int(m.group(1)) if m else None
    return {
        "title": title or url,
        "abstract": description or None,
        "full_text": body or None,
        "publication_date": pub_date,
        "year": year,
        "url": url,
    }


def harvest(*, max_records: int | None = None,
            output_dir: Path = DEFAULT_OUT,
            rate_limit: float = 0.5) -> dict:
    """Walk thegoodboard.ch sitemap, scrape each scholarship page, write JSONL.

    Returns a summary dict (called by harvest_all.run_source).
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / "thegoodboard.jsonl"

    started = time.time()
    try:
        sitemap_xml = _fetch(SITEMAP_URL, timeout=20)
    except Exception as e:
        log.error("sitemap fetch failed: %s", e)
        return {"source": "thegoodboard", "error": f"sitemap: {e}"}

    urls = _parse_sitemap(sitemap_xml)
    log.info("sitemap: %d URLs", len(urls))

    scraped = 0
    skipped = 0
    failed = 0
    with atomic_jsonl(out_path) as fh:
        for url, lastmod in urls:
            pub_type, lang = _classify_url(url)
            if pub_type is None:
                skipped += 1
                continue
            try:
                html = _fetch(url, timeout=20)
            except Exception as e:
                log.warning("fetch failed %s: %s", url, e)
                failed += 1
                continue
            doc = _extract_page(url, html, lastmod)
            if not doc.get("title") or not doc.get("full_text"):
                log.info("empty body, skipping %s", url)
                skipped += 1
                continue

            slug = url.rstrip("/").rsplit("/", 1)[-1]
            section = pub_type
            source_record_id = f"{section}/{slug}/{lang}"
            record = {
                "source": "thegoodboard",
                "source_record_id": source_record_id,
                "datestamp": lastmod,
                "pub_type": pub_type,
                "title": doc["title"],
                "authors": ["Jonas Hertner"],
                "abstract": doc.get("abstract"),
                "full_text": doc.get("full_text"),
                "publication_date": doc.get("publication_date"),
                "year": doc.get("year"),
                "publisher": "thegoodboard.ch",
                "url": url,
                "language": lang,
                "license": "OA-author-permitted-reuse",
                "license_url": "https://thegoodboard.ch/llms.txt",
                "rights_raw": [
                    "© Jonas Hertner — see https://thegoodboard.ch/llms.txt; "
                    "training corpora and retrieval systems explicitly invited.",
                ],
                "subjects": ["Swiss corporate governance"],
            }
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            scraped += 1
            if max_records and scraped >= max_records:
                break
            time.sleep(rate_limit)

    summary = {
        "source": "thegoodboard",
        "sitemap_url": SITEMAP_URL,
        "urls_total": len(urls),
        "scraped": scraped,
        "skipped": skipped,
        "failed": failed,
        "elapsed_seconds": round(time.time() - started, 1),
        "output_path": str(out_path),
    }
    log.info("Done: %s", summary)
    return summary


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--max-records", type=int, default=None)
    p.add_argument("--output-dir", type=Path, default=DEFAULT_OUT)
    p.add_argument("--rate-limit", type=float, default=0.5)
    args = p.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    summary = harvest(
        max_records=args.max_records,
        output_dir=args.output_dir,
        rate_limit=args.rate_limit,
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
