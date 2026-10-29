#!/usr/bin/env python3
"""Scraper for OpenLegalCommentary.ch BV commentaries.

Fetches commentary on the Swiss Federal Constitution (BV/Cst./Cost.)
from openlegalcommentary.ch. Content is CC BY-SA 4.0.

Output: JSONL at output/openlegalcommentary/commentaries.jsonl (or --output),
which search_stack/build_ok_commentaries_db.py merges into the rebuilt
ok_commentaries.db. --db still inserts directly into a given DB file.
"""

import argparse
import json
import logging
import os
import re
import sqlite3
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

BASE_URL = "https://openlegalcommentary.ch"
BV_SR = "101"
BV_ABBR = "BV"

# Languages to scrape
LANGUAGES = ["de", "fr", "it"]
LANG_ABBR = {"de": "BV", "fr": "Cst.", "it": "Cost."}

DELAY = 1.0  # seconds between requests

DEFAULT_OUTPUT = Path("output/openlegalcommentary/commentaries.jsonl")


def _fetch_article_list(lang: str = "de") -> list[str]:
    """Fetch list of article numbers from the BV index page."""
    url = f"{BASE_URL}/{lang}/bv/"
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    articles = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        m = re.match(rf"/{lang}/bv/art-(.+?)/?$", href)
        if m:
            articles.append(m.group(1))
    return sorted(set(articles), key=lambda x: (int(re.sub(r"[a-z]", "", x) or "0"), x))


def _block_text(el) -> str:
    """An element's text on one line: inline markup (links, emphasis, the
    N. margin-number anchors) joined without the line breaks get_text adds."""
    return re.sub(r"\s+", " ", el.get_text()).strip()


def _panel_text(panel) -> str:
    """One tab panel's prose: headings, paragraphs, quotes and list items as
    paragraphs. The table of contents and heading '#' anchors are marked
    data-pagefind-ignore and dropped."""
    body = panel.select_one(".panel-body") or panel
    for el in body.select("[data-pagefind-ignore]"):
        el.decompose()
    parts = []
    for el in body.find_all(["h2", "h3", "h4", "p", "blockquote", "li"]):
        # A <p> inside a quote or list item is covered by its container.
        if el.name == "p" and el.find_parent(["blockquote", "li"]) is not None:
            continue
        text = _block_text(el)
        if not text:
            continue
        if el.name == "blockquote":
            text = f"> {text}"
        elif el.name == "li":
            text = f"- {text}"
        parts.append(text)
    return "\n\n".join(parts)


def parse_article_html(html: str, art_num: str, lang: str, url: str) -> dict | None:
    """Parse an article page: the statute text (section.gesetzestext) and the
    Doktrin and Rechtsprechung tab panels (#panel-doctrine, #panel-caselaw).
    The Übersicht panel is a summary of those two and is left out. Returns
    None when the page has neither panel, so a site redesign yields no rows
    (and the scrape keeps the previous file) rather than page chrome."""
    soup = BeautifulSoup(html, "html.parser")

    titled = soup.select_one("[data-title]")
    h1 = soup.find("h1")
    title = (titled["data-title"].strip() if titled else
             _block_text(h1) if h1 else f"Art. {art_num} BV")

    # Paragraphs are <p> ("1" + text); letters are <dl> pairs ("a." + text).
    lines = []
    body = soup.select_one("section.gesetzestext .gesetzestext-body")
    for el in body.find_all(["p", "dt", "li"]) if body else []:
        if el.name == "dt":
            dd = el.find_next_sibling("dd")
            text = f"{_block_text(el)} {_block_text(dd) if dd else ''}"
        else:
            text = re.sub(r"\s+", " ", el.get_text(" "))
        if text.strip():
            lines.append(text.strip())
    legal_text = "\n".join(lines)

    content_parts = []
    for pid in ("panel-doctrine", "panel-caselaw"):
        panel = soup.find(id=pid)
        if panel is None:
            continue
        text = _panel_text(panel)
        if not text:
            continue
        if pid == "panel-caselaw" and content_parts:
            text = f"{panel.get('data-label') or 'Rechtsprechung'}:\n\n{text}"
        content_parts.append(text)
    content_text = "\n\n".join(content_parts).strip()

    if len(content_text) < 50:
        return None

    return {
        "article_num": art_num,
        "title": title,
        "language": lang,
        "legal_text": legal_text,
        "content_text": content_text,
        "html_link": url,
        "source": "OpenLegalCommentary.ch",
    }


def _fetch_article(art_num: str, lang: str = "de") -> dict | None:
    """Fetch and parse a single article commentary page."""
    url = f"{BASE_URL}/{lang}/bv/art-{art_num}"
    resp = requests.get(url, timeout=30)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return parse_article_html(resp.text, art_num, lang, url)


def scrape_all(languages: list[str] | None = None, db_path: Path | None = None) -> list[dict]:
    """Scrape all BV articles across languages.

    If db_path is provided, inserts directly into ok_commentaries.db.
    Returns list of scraped entries.
    """
    langs = languages or LANGUAGES
    results = []

    # Get article list from German index (most complete)
    articles = _fetch_article_list("de")
    logger.info("Found %d BV articles to scrape", len(articles))

    for lang in langs:
        abbr = LANG_ABBR.get(lang, "BV")
        logger.info("Scraping %s (%s)...", lang, abbr)
        for art_num in articles:
            time.sleep(DELAY)
            try:
                entry = _fetch_article(art_num, lang)
                if entry:
                    entry["sr_number"] = BV_SR
                    entry["abbr"] = abbr
                    results.append(entry)
                    logger.debug("  Art. %s (%s): %d chars", art_num, lang, len(entry["content_text"]))
                else:
                    logger.debug("  Art. %s (%s): no content", art_num, lang)
            except Exception as e:
                logger.warning("  Art. %s (%s): error: %s", art_num, lang, e)

    logger.info("Scraped %d commentaries total", len(results))

    if db_path:
        _insert_into_db(results, db_path)

    return results


def write_jsonl(entries: list[dict], path: Path) -> None:
    """Write entries as JSONL via a temp file + rename, so the build never
    reads a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        for r in entries:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def _insert_into_db(entries: list[dict], db_path: Path):
    """Insert scraped entries into ok_commentaries.db."""
    conn = sqlite3.connect(str(db_path))
    inserted = 0
    try:
        for e in entries:
            # Use a deterministic ID to avoid duplicates
            olc_id = f"olc_{e['abbr']}_{e['article_num']}_{e['language']}"
            conn.execute(
                """INSERT OR REPLACE INTO commentaries
                   (ok_uuid, legislative_act_uuid, sr_number, abbr, article_num,
                    title, language, date, authors, editors, suggested_citation,
                    html_link, pdf_link, content_html, content_text, legal_text)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    olc_id,
                    "olc_bv_101",  # synthetic legislative_act_uuid
                    e["sr_number"],
                    e["abbr"],
                    e["article_num"],
                    e["title"],
                    e["language"],
                    None,  # date
                    json.dumps(["AI-generated"]),
                    json.dumps([]),
                    None,  # suggested_citation
                    e["html_link"],
                    None,  # pdf_link
                    None,  # content_html
                    e["content_text"],
                    e.get("legal_text", ""),
                ),
            )
            inserted += 1
        conn.execute("INSERT INTO commentaries_fts(commentaries_fts) VALUES('rebuild')")
        conn.commit()
        logger.info("Inserted %d commentaries into %s", inserted, db_path)
    except Exception as e:
        logger.error("DB insert error: %s", e)
        conn.rollback()
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    parser = argparse.ArgumentParser(description="Scrape OpenLegalCommentary.ch BV")
    parser.add_argument("--db", type=Path, help="Path to ok_commentaries.db for direct insert")
    parser.add_argument("--output", type=Path,
                        help=f"Output JSONL file (default: {DEFAULT_OUTPUT}; "
                             "not written by default when --max is given)")
    parser.add_argument("--lang", nargs="+", default=LANGUAGES, help="Languages to scrape")
    parser.add_argument("--max", type=int, help="Max articles per language")
    args = parser.parse_args(argv)

    results = []
    articles = _fetch_article_list("de")
    if args.max:
        articles = articles[:args.max]

    for lang in args.lang:
        abbr = LANG_ABBR.get(lang, "BV")
        logger.info("Scraping %s (%s)...", lang, abbr)
        for art_num in articles:
            time.sleep(DELAY)
            try:
                entry = _fetch_article(art_num, lang)
                if entry:
                    entry["sr_number"] = BV_SR
                    entry["abbr"] = abbr
                    results.append(entry)
                    logger.info("  Art. %s (%s): %d chars", art_num, lang, len(entry["content_text"]))
            except Exception as e:
                logger.warning("  Art. %s (%s): error: %s", art_num, lang, e)

    logger.info("Total: %d commentaries", len(results))

    if args.db:
        _insert_into_db(results, args.db)

    # The build reads DEFAULT_OUTPUT, so a --max sample or a run that
    # scraped nothing (site down) must not replace the last full scrape.
    # Exit 0 either way: the commentaries unit chains the build after this
    # step with &&, and the OnlineKommentar refresh should still land.
    output = args.output or (None if args.max else DEFAULT_OUTPUT)
    if output and not results:
        logger.warning("No commentaries scraped; keeping existing %s", output)
    elif output:
        write_jsonl(results, output)
        logger.info("Written to %s", output)


if __name__ == "__main__":
    main()
