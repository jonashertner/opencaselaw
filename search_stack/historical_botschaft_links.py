"""Link the historical (1850-1999) Bundesblatt documents to statute articles.

The statute footnotes cite pre-1999 Bundesblatt documents with their volume
("BBl 1975 I 609", "BBl 1920 II 62 f."). The footnote parser that feeds
amendment_refs reads only the volume-less post-1998 form ("BBl 1999 6013"),
so the 1850-1999 messages and reports stored from the Bundesarchiv scans had
no article links: search_botschaft found them, get_materialien(law, article)
and get_article_purpose did not. Measured 2026-10-21: 3,562 volume citations
in 2,835 articles of 243 laws, pointing to 401 distinct documents.

This module reads statutes.db's German article texts and footnotes, parses
every pre-1999 BBl citation and links the article to the stored document it
names, in order of confidence:

* ``exact``          year, volume and start page match a stored document;
* ``in_document``    the page lies inside a stored document of that year and
                     volume (a pinpoint into it: "BBl 1920 II 62 f.");
* ``no_volume``      the citation omits the volume ("BBl 1940 1257", "BBl
                     1998 3770"): linked only when exactly one stored document
                     of that year starts at, else contains, that page.

OCR'd volumes in the statute texts ("BBl 1949 1 1249", "BBl 1946 11 1227")
are read as I / II / III. A citation that matches nothing, or more than one
document, links nothing. The German row is matched (statute footnotes are
German); the French and Italian rows of the same Fedlex work are linked with
it. Every link row says how it was made (``evidence``). Idempotent.
"""
from __future__ import annotations

import logging
import re
import sqlite3
from pathlib import Path

log = logging.getLogger("historical_botschaft_links")

_ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8,
          # OCR of the statute texts: "1" for I, "11" for II, "111" for III
          "1": 1, "11": 2, "111": 3}
_CITE_RE = re.compile(
    r"\bBBl\s+(1[89]\d\d)\s+"
    r"(?:(VIII|VII|VI|IV|V|III|II|I|111|11|1)\s+(?=\d))?"
    r"(\d{1,5})\b")


def parse_citations(text: str | None) -> list[tuple[int, int | None, int, str]]:
    """(year, volume or None, page, cited text) for every pre-1999 BBl
    citation in ``text``."""
    out = []
    for m in _CITE_RE.finditer(text or ""):
        year = int(m.group(1))
        if year >= 1999:
            continue
        vol = _ROMAN.get(m.group(2)) if m.group(2) else None
        out.append((year, vol, int(m.group(3)), m.group(0)))
    return out


class _Index:
    """Stored German historical documents by year: (volume, start, pages, id, eli)."""

    def __init__(self, conn: sqlite3.Connection):
        self.by_year: dict[int, list[tuple[int | None, int, int, int, str | None]]] = {}
        for y, v, p, n, bid, eli in conn.execute(
                "SELECT bbl_year, bbl_volume, bbl_page, page_count, botschaft_id, eli_uri "
                "FROM botschaft_documents WHERE language = 'de' AND bbl_year < 1999"):
            self.by_year.setdefault(y, []).append((v, p, max(1, n or 1), bid, eli))

    def resolve(self, year: int, vol: int | None, page: int) -> tuple[int, str | None, str] | None:
        docs = self.by_year.get(year, [])
        if vol is not None:
            same = [d for d in docs if (d[0] or 0) == vol]
            hit = [d for d in same if d[1] == page]
            if len(hit) == 1:
                return hit[0][3], hit[0][4], "exact"
            inside = [d for d in same if d[1] < page < d[1] + d[2]]
            if len(inside) == 1:
                return inside[0][3], inside[0][4], "in_document"
            return None
        hit = [d for d in docs if d[1] == page]
        if len(hit) == 1:
            return hit[0][3], hit[0][4], "no_volume"
        if hit:
            return None
        inside = [d for d in docs if d[1] < page < d[1] + d[2]]
        if len(inside) == 1:
            return inside[0][3], inside[0][4], "no_volume"
        return None


def link_historical(conn: sqlite3.Connection, statutes_db: str | Path, *,
                    dry_run: bool = False) -> dict:
    """Insert article_botschaft_links rows for the historical documents the
    statute footnotes cite. Returns counts."""
    idx = _Index(conn)
    siblings: dict[str, list[int]] = {}
    for eli, bid in conn.execute(
            "SELECT eli_uri, botschaft_id FROM botschaft_documents "
            "WHERE bbl_year < 1999 AND eli_uri IS NOT NULL"):
        siblings.setdefault(eli, []).append(bid)
    st = sqlite3.connect(f"file:{Path(statutes_db)}?mode=ro", uri=True)
    stats = {"citations": 0, "resolved": 0, "unresolved": 0, "by_method": {}}
    rows: set[tuple[str, str, int, str]] = set()
    try:
        for sr, art, text, fn in st.execute(
                "SELECT sr_number, article_num, text, footnote FROM articles WHERE lang = 'de'"):
            if not sr or not art:
                continue
            for year, vol, page, cited in parse_citations(f"{text or ''}\n{fn or ''}"):
                stats["citations"] += 1
                hit = idx.resolve(year, vol, page)
                if hit is None:
                    stats["unresolved"] += 1
                    continue
                bid, eli, method = hit
                stats["resolved"] += 1
                stats["by_method"][method] = stats["by_method"].get(method, 0) + 1
                ev = f"statute_footnote:{method}:{' '.join(cited.split())}"
                for b in (siblings.get(eli) or [bid]):
                    rows.add((sr, str(art), b, ev))
    finally:
        st.close()
    before = conn.execute("SELECT COUNT(*) FROM article_botschaft_links").fetchone()[0]
    stats["link_rows"] = len(rows)
    if not dry_run:
        conn.executemany(
            "INSERT OR IGNORE INTO article_botschaft_links "
            "(sr_number, article, botschaft_id, relation, evidence) VALUES (?, ?, ?, 'considered', ?)",
            sorted(rows))
        conn.commit()
    after = conn.execute("SELECT COUNT(*) FROM article_botschaft_links").fetchone()[0]
    stats["new_links"] = after - before
    log.info("historical links: %s", stats)
    return stats


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s",
                        datefmt="%H:%M:%S")
    ap = argparse.ArgumentParser(description="Link 1850-1999 Bundesblatt documents to statute articles")
    ap.add_argument("--db", required=True, help="materialien.db (run on a copy: search_stack.materialien_swap)")
    ap.add_argument("--statutes", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    conn = sqlite3.connect(args.db)
    try:
        print(json.dumps(link_historical(conn, args.statutes, dry_run=args.dry_run)))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
