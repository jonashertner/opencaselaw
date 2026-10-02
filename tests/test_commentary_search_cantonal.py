"""search_commentaries covers the cantonal commentaries, not only OnlineKommentar.

Until 2026-10-03 the tool read ok_commentaries.db alone: a topic search such as
"Rechtsverweigerungsbeschwerde" returned 0 results while the Kommentar zur
Schaffhauser Verwaltungsrechtspflege (in the scholarship corpus) has a
Kommentierung of exactly that. Offline: the fixture ePub, ingested with the
real scholarship build; OnlineKommentar rows are stubbed.
"""
import json
import sqlite3
import sys
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

import build_shk_kommentar_shard as shk  # noqa: E402
import mcp_server  # noqa: E402
from search_stack import build_legal_scholarship as bls  # noqa: E402

FIXTURE = REPO / "tests" / "fixtures" / "shk_kommentar"


@pytest.fixture(scope="module")
def scholarship_db(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("shk_search")
    epub = tmp / "excerpt.epub"
    with zipfile.ZipFile(epub, "w") as z:
        for f in sorted((FIXTURE / "OEBPS").glob("*.html")):
            z.write(f, f"OEBPS/{f.name}")
    records, problems = shk.parse_epub(epub)
    assert problems == []
    shard = tmp / "shk_kommentar.jsonl"
    shard.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records),
                     encoding="utf-8")
    db = tmp / "legal_scholarship.db"
    conn = sqlite3.connect(db)
    conn.executescript(bls.SCHEMA_SQL)
    bls.ingest_jsonl(conn, shard)
    conn.commit()
    conn.close()
    return db


class _OkConn:
    """ok_commentaries.db stand-in: returns the given rows for every query."""

    def __init__(self, rows):
        self.rows = rows

    def execute(self, *a, **k):
        rows = self.rows

        class _Cur:
            def fetchall(self):
                return rows
        return _Cur()

    def close(self):
        pass


def _ok_row(article, score, snippet="s"):
    return {"sr_number": "220", "abbr": "OR", "article_num": article,
            "title": f"Art. {article} OR", "authors": None, "language": "de",
            "html_link": f"https://onlinekommentar.ch/de/kommentare/or{article}",
            "snippet": snippet, "score": score}


@pytest.fixture
def served(scholarship_db, monkeypatch):
    calls = {"scholarship": 0}

    def _conn():
        calls["scholarship"] += 1
        c = sqlite3.connect(f"file:{scholarship_db}?immutable=1", uri=True)
        c.row_factory = sqlite3.Row
        return c
    monkeypatch.setattr(mcp_server, "_get_scholarship_conn", _conn)
    monkeypatch.setattr(mcp_server, "_get_ok_conn", lambda *a, **k: _OkConn([]))
    return calls


def test_topic_search_finds_the_schaffhausen_kommentierung(served):
    out = mcp_server.search_commentaries("Rekursberechtigung")
    ids = [r.get("pub_id") for r in out["results"]]
    assert "shk_kommentar:vrg-art-18" in ids
    hit = out["results"][ids.index("shk_kommentar:vrg-art-18")]
    assert hit["abbreviation"] == "VRG SH" and hit["canton"] == "SH"
    assert hit["article_num"] == "18"
    assert hit["authors"]
    assert "Schaffhauser" in hit["source"]
    assert hit["get"] == "get_commentary(canton='SH', abbreviation='VRG', article='18')"
    assert "Schaffhauser" in out["source"]
    # CC BY-SA: the attribution travels with the hit
    assert out["attribution"] and out["attribution"][0]["attribution"]


def test_only_kommentierungen_are_returned(served):
    """The checklist, the editorial and the author list are records of the
    same book but not article commentary."""
    out = mcp_server.search_commentaries("Rekurs OR Kommentar OR Herausgeber", limit=50)
    ids = {r.get("pub_id") for r in out["results"]}
    assert ids, out
    assert all("-art-" in i for i in ids), ids


def test_jg_article(served):
    out = mcp_server.search_commentaries("Ausstand")
    hit = next(r for r in out["results"] if r.get("pub_id") == "shk_kommentar:jg-art-50")
    assert hit["abbreviation"] == "JG SH" and hit["article_num"] == "50"


@pytest.mark.parametrize("abbr", ["VRG SH", "SH/VRG", "VRG"])
def test_cantonal_abbreviation_searches_that_commentary_only(served, monkeypatch, abbr):
    def _no_ok(*a, **k):
        raise AssertionError("the OnlineKommentar index must not be searched")
    monkeypatch.setattr(mcp_server, "_get_ok_conn", _no_ok)
    out = mcp_server.search_commentaries("Rekursberechtigung", abbreviation=abbr)
    assert [r["pub_id"] for r in out["results"]] == ["shk_kommentar:vrg-art-18"]


def test_federal_abbreviation_leaves_the_cantonal_commentary_out(served, monkeypatch):
    monkeypatch.setattr(mcp_server, "_get_ok_conn",
                        lambda *a, **k: _OkConn([_ok_row("41", -9.0)]))
    out = mcp_server.search_commentaries("Rekursberechtigung", abbreviation="OR")
    assert [r["abbreviation"] for r in out["results"]] == ["OR"]
    assert served["scholarship"] == 0
    assert "attribution" not in out


def test_include_cantonal_false_is_the_old_behaviour(served, monkeypatch):
    monkeypatch.setattr(mcp_server, "_get_ok_conn",
                        lambda *a, **k: _OkConn([_ok_row("41", -9.0)]))
    out = mcp_server.search_commentaries("Rekursberechtigung", include_cantonal=False)
    assert [r["abbreviation"] for r in out["results"]] == ["OR"]
    assert served["scholarship"] == 0


def test_merge_follows_bm25_and_keeps_each_list_in_order():
    fed = [{"source": "OK", "_score": s, "n": f"f{i}"} for i, s in enumerate([-12.0, -8.0, -3.0])]
    can = [{"source": "SHK", "canton": "SH", "abbreviation": "VRG SH",
            "_score": s, "n": f"c{i}"} for i, s in enumerate([-10.0, -1.0])]
    out = mcp_server._commentary_search_payload("q", "q", fed, can, 4, False)
    assert [r["n"] for r in out["results"]] == ["f0", "c0", "f1", "f2"]
    assert all("_score" not in r for r in out["results"])
    assert out["source"] == "OK; SHK"


def test_formatter_names_the_source_and_the_attribution(served):
    out = mcp_server.search_commentaries("Rekursberechtigung")
    text = mcp_server._format_search_commentaries_response(out)
    assert "shk_kommentar:vrg-art-18" in text
    assert "Art. 18 VRG SH" in text
    assert "Attribution" in text
    assert ">>>" not in text and "<<<" not in text
