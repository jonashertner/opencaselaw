"""search_stack/historical_botschaft_links.py: statute footnotes' volume
citations ("BBl 1975 I 609") link articles to the stored 1850-1999 documents
(2026-10-21: those had no article links at all)."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from search_stack import build_botschaft_corpus as bbc  # noqa: E402
from search_stack import historical_botschaft_links as hbl  # noqa: E402

FGA = "https://fedlex.data.admin.ch/eli/fga/"


def test_parse_citations():
    t = ("AS 1977 237; BBl 1975 I 609 f. und BBl 1920 II 62 f.; BBl 1949 1 1249; "
         "BBl 1946 11 1227; BBl 1940 1257; BBl 1946 1222 1227 ff.; BBl 1998 3770; BBl 2003 6013")
    got = [(y, v, p) for y, v, p, _ in hbl.parse_citations(t)]
    assert got == [(1975, 1, 609), (1920, 2, 62), (1949, 1, 1249), (1946, 2, 1227),
                   (1940, None, 1257), (1946, None, 1222), (1998, None, 3770)]


def _fixture(tmp_path):
    m = sqlite3.connect(tmp_path / "materialien.db")
    bbc.ensure_schema(m)
    docs = [  # (id, year, vol, page, pages, lang, eli)
        (1, 1975, 1, 609, 40, "de", FGA + "1975/1_609__"),
        (2, 1975, 1, 609, 42, "fr", FGA + "1975/1_609__"),   # same work, FR
        (3, 1975, 2, 609, 10, "de", FGA + "1975/2_609__"),   # same page, other volume
        (4, 1940, 1, 1257, 20, "de", FGA + "1940/1_1257__"),
        (5, 1998, 3, 3770, 30, "de", FGA + "1998/3_3770__"),
        (6, 1998, 4, 3770, 30, "de", FGA + "1998/4_3770__"),  # no-volume ambiguity
        (7, 2003, None, 6013, 5, "de", FGA + "2003/6013"),     # modern: not ours
    ]
    for bid, y, v, p, n, lg, eli in docs:
        m.execute("INSERT INTO botschaft_documents (botschaft_id, bbl_year, bbl_volume, bbl_page, "
                  "bbl_citation, eli_uri, source_url, format, language, page_count, ingested_at) "
                  "VALUES (?,?,?,?, 'x', ?, 'u', 'pdf', ?, ?, 'now')", (bid, y, v, p, eli, lg, n))
    m.commit()
    st = tmp_path / "statutes.db"
    s = sqlite3.connect(st)
    s.execute("CREATE TABLE articles (sr_number, article_num, lang, text, footnote)")
    s.executemany("INSERT INTO articles VALUES (?,?,?,?,?)", [
        ("210", "1", "de", "Text", "Fassung gemäss Ziff. I (AS 1977 237; BBl 1975 I 609)."),
        ("210", "2", "de", "vgl. BBl 1975 I 620 f.", None),          # pinpoint inside doc 1
        ("210", "3", "de", "BBl 1975 II 609", None),                 # the other volume
        ("220", "5", "de", None, "(AS 57 1269; BBl 1940 1257)."),    # no volume, unique
        ("220", "6", "de", None, "(BBl 1998 3770)."),                # no volume, ambiguous
        ("220", "7", "de", None, "(BBl 1975 I 9999)."),               # matches nothing
        ("220", "8", "fr", None, "(FF 1975 I 609)."),                 # French row: not read
        ("220", "9", "de", None, "(BBl 2003 6013)."),                 # modern: amendment_refs' job
    ])
    s.commit(); s.close()
    return m, st


def test_link_historical(tmp_path):
    m, st = _fixture(tmp_path)
    stats = hbl.link_historical(m, st)
    links = sorted(m.execute("SELECT sr_number, article, botschaft_id, evidence "
                             "FROM article_botschaft_links").fetchall())
    got = {(sr, a, b) for sr, a, b, _ in links}
    assert got == {("210", "1", 1), ("210", "1", 2),   # exact, DE + FR of the same work
                   ("210", "2", 1), ("210", "2", 2),   # page inside the document
                   ("210", "3", 3),                    # the volume decides
                   ("220", "5", 4)}                    # no volume, one candidate
    ev = {(sr, a): e for sr, a, b, e in links}
    assert ev[("210", "1")] == "statute_footnote:exact:BBl 1975 I 609"
    assert ev[("210", "2")].startswith("statute_footnote:in_document:")
    assert ev[("220", "5")].startswith("statute_footnote:no_volume:")
    assert stats["resolved"] == 4 and stats["unresolved"] == 2
    # idempotent
    again = hbl.link_historical(m, st)
    assert again["new_links"] == 0


def test_dry_run_writes_nothing(tmp_path):
    m, st = _fixture(tmp_path)
    stats = hbl.link_historical(m, st, dry_run=True)
    assert stats["link_rows"] == 6 and stats["new_links"] == 0
    assert m.execute("SELECT COUNT(*) FROM article_botschaft_links").fetchone()[0] == 0


def test_enrichment_script_runs_the_historical_pass_first():
    src = (REPO / "scripts" / "enrich_botschaft_links.py").read_text()
    assert "--no-historical" in src
    assert src.index("link_historical(") < src.index("fetch_all_botschaft_cc_pairs(type_ids)")
