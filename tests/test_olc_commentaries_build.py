"""OpenLegalCommentary.ch (OLC) BV commentaries reach ok_commentaries.db.

The monthly commentaries unit ran the OLC scraper without --db/--output, so
its ~700 entries were dropped and get_commentary(abbreviation='BV',
article='8') answered "no commentary" although openlegalcommentary.ch has
one. The scraper now writes JSONL by default and the build merges it into the
same tmp DB as the OnlineKommentar JSON, before the rename.
"""
import json
import sqlite3

import mcp_server
import search_stack.build_ok_commentaries_db as okmod
from scrapers import openlegalcommentary as olc


def _olc_line(art, lang, abbr, text):
    return {
        "article_num": art, "title": f"Art. {art} {abbr}", "language": lang,
        "legal_text": f"LEGAL_{art}_{lang}", "content_text": text,
        "html_link": f"https://openlegalcommentary.ch/{lang}/bv/art-{art}",
        "source": "OpenLegalCommentary.ch", "sr_number": "101", "abbr": abbr,
    }


def _build(tmp_path, monkeypatch, olc_lines=None):
    ok = tmp_path / "commentaries.json"
    ok.write_text(json.dumps([
        # OnlineKommentar comments BV Art. 10 (de) and OR Art. 41; OLC has
        # every BV article, so Art. 10 overlaps.
        {"ok_uuid": "uuid-bv10", "legislative_act_uuid": "act-bv", "sr_number": "101",
         "abbr": "BV", "article_num": "10", "title": "Art. 10 BV", "language": "de",
         "authors": ["Corina Heri"], "content_text": "OK_BV10_DE"},
        {"ok_uuid": "uuid-or41", "legislative_act_uuid": "act-or", "sr_number": "220",
         "abbr": "OR", "article_num": "41", "title": "Art. 41 OR", "language": "de",
         "authors": ["X"], "content_text": "OK_OR41_DE"},
    ]), encoding="utf-8")
    olc_path = tmp_path / "olc" / "commentaries.jsonl"
    if olc_lines is not None:
        olc_path.parent.mkdir()
        olc_path.write_text(
            "".join(line if isinstance(line, str) else json.dumps(line) + "\n"
                    for line in olc_lines),
            encoding="utf-8")
    out = tmp_path / "ok_commentaries.db"
    monkeypatch.setattr(okmod, "INPUT_FILE", ok)
    monkeypatch.setattr(okmod, "OLC_INPUT_FILE", olc_path)
    monkeypatch.setattr(okmod, "OUTPUT_DB", out)
    monkeypatch.setattr(okmod, "STATUTES_DB", tmp_path / "absent-statutes.db")
    okmod.build_db()
    return out


OLC_LINES = [
    _olc_line("8", "de", "BV", "OLC_BV8_DE"),
    _olc_line("8", "fr", "Cst.", "OLC_BV8_FR"),
    _olc_line("8", "it", "Cost.", "OLC_BV8_IT"),
    _olc_line("10", "de", "BV", "OLC_BV10_DE"),
    _olc_line("10", "fr", "Cst.", "OLC_BV10_FR"),
]


def test_build_merges_olc_jsonl_with_existing_mapping(tmp_path, monkeypatch):
    out = _build(tmp_path, monkeypatch, OLC_LINES + [
        "not json\n",
        {"article_num": "9", "language": "de", "abbr": "BV"},  # no content_text
        _olc_line("8", "fr", "Cst.", "OLC_BV8_FR_v2"),  # later line wins
    ])
    assert not out.with_suffix(".tmp").exists()
    conn = sqlite3.connect(out)
    conn.row_factory = sqlite3.Row
    assert conn.execute("SELECT COUNT(*) FROM commentaries").fetchone()[0] == 7
    r = conn.execute(
        "SELECT * FROM commentaries WHERE ok_uuid = 'olc_Cst._8_fr'").fetchone()
    assert r["legislative_act_uuid"] == "olc_bv_101"
    assert r["sr_number"] == "101" and r["abbr"] == "Cst." and r["article_num"] == "8"
    assert r["language"] == "fr" and r["content_text"] == "OLC_BV8_FR_v2"
    assert json.loads(r["authors"]) == ["AI-generated"]
    assert json.loads(r["editors"]) == []
    assert r["date"] is None and r["suggested_citation"] is None
    assert r["legal_text"] == "LEGAL_8_fr"
    assert r["html_link"] == "https://openlegalcommentary.ch/fr/bv/art-8"
    assert conn.execute(
        "SELECT sr_number FROM legislative_acts WHERE ok_uuid = 'olc_bv_101'"
    ).fetchone()[0] == "101"
    # OLC rows are in the FTS index alongside the OnlineKommentar rows.
    hits = conn.execute(
        "SELECT COUNT(*) FROM commentaries_fts WHERE commentaries_fts MATCH 'OLC_BV8_DE'"
    ).fetchone()[0]
    assert hits == 1
    conn.close()


def test_build_without_olc_file_still_builds_onlinekommentar(tmp_path, monkeypatch):
    out = _build(tmp_path, monkeypatch, olc_lines=None)
    conn = sqlite3.connect(out)
    assert conn.execute("SELECT COUNT(*) FROM commentaries").fetchone()[0] == 2
    assert conn.execute(
        "SELECT COUNT(*) FROM commentaries WHERE ok_uuid LIKE 'olc%'").fetchone()[0] == 0
    conn.close()


def _served(tmp_path, monkeypatch):
    out = _build(tmp_path, monkeypatch, OLC_LINES)
    monkeypatch.setattr(mcp_server, "OK_COMMENTARIES_DB_PATH", out)
    return out


def test_get_commentary_bv_8_returns_olc_in_requested_language(tmp_path, monkeypatch):
    _served(tmp_path, monkeypatch)
    fr = mcp_server.get_commentary(abbreviation="BV", article="8", language="fr")
    assert fr["content_text"] == "OLC_BV8_FR"
    assert fr["sr_number"] == "101" and fr["language"] == "fr"
    assert fr["source"] == "OpenLegalCommentary.ch (CC BY-SA 4.0)"
    assert fr["authors"] == ["AI-generated"]
    assert mcp_server.get_commentary(
        abbreviation="Cst.", article="8", language="fr")["content_text"] == "OLC_BV8_FR"
    assert mcp_server.get_commentary(
        abbreviation="BV", article="8")["content_text"] == "OLC_BV8_DE"
    # No English OLC text: falls back to German.
    assert mcp_server.get_commentary(
        abbreviation="BV", article="8", language="en")["language"] == "de"
    md = mcp_server._format_get_commentary_response(fr)
    assert "[OpenLegalCommentary](https://openlegalcommentary.ch/fr/bv/art-8)" in md


def test_onlinekommentar_outranks_olc_on_overlap(tmp_path, monkeypatch):
    _served(tmp_path, monkeypatch)
    for lang in ("de", "fr"):
        res = mcp_server.get_commentary(abbreviation="BV", article="10", language=lang)
        assert res["content_text"] == "OK_BV10_DE"
        assert res["source"] == "OnlineKommentar.ch (CC-BY-4.0)"


def test_listing_shows_olc_once_per_article(tmp_path, monkeypatch):
    _served(tmp_path, monkeypatch)
    res = mcp_server.get_commentary(abbreviation="BV", language="fr")
    arts = [(a["article_num"], a["language"]) for a in res["articles"]]
    assert arts == [("8", "fr"), ("10", "de"), ("10", "fr")]
    # Other laws are untouched by the OLC language filter.
    assert mcp_server.get_commentary(abbreviation="OR")["article_count"] == 1


def test_scraper_writes_default_jsonl_atomically(tmp_path, monkeypatch):
    target = tmp_path / "out" / "commentaries.jsonl"
    olc.write_jsonl([_olc_line("8", "de", "BV", "x")], target)
    assert [json.loads(l)["article_num"] for l in target.read_text().splitlines()] == ["8"]
    assert not list(target.parent.glob("*.tmp"))


def _run_main(monkeypatch, argv, scraped):
    monkeypatch.setattr(olc, "_fetch_article_list", lambda lang="de": ["8", "9"])
    monkeypatch.setattr(olc, "_fetch_article",
                        lambda art, lang="de": scraped(art, lang))
    monkeypatch.setattr(olc, "DELAY", 0)
    olc.main(argv)


def test_scraper_main_default_output_and_guards(tmp_path, monkeypatch):
    default = tmp_path / "output" / "openlegalcommentary" / "commentaries.jsonl"
    monkeypatch.setattr(olc, "DEFAULT_OUTPUT", default)
    _run_main(monkeypatch, ["--lang", "de", "fr"],
              lambda art, lang: {"article_num": art, "language": lang,
                                 "content_text": "c", "title": "t", "html_link": "u"})
    rows = [json.loads(l) for l in default.read_text().splitlines()]
    assert len(rows) == 4
    assert {(r["abbr"], r["sr_number"]) for r in rows} == {("BV", "101"), ("Cst.", "101")}
    before = default.read_text()
    # A --max sample does not replace the full scrape.
    _run_main(monkeypatch, ["--max", "1"],
              lambda art, lang: {"article_num": art, "language": lang,
                                 "content_text": "c", "title": "t", "html_link": "u"})
    assert default.read_text() == before
    # Nor does a run that scraped nothing.
    _run_main(monkeypatch, [], lambda art, lang: None)
    assert default.read_text() == before


# Markup of an openlegalcommentary.ch article page (2026-10), reduced to the
# parts the parser reads, with placeholder prose.
_PAGE = """<html><body><main><div class="page-wrapper">
<nav class="breadcrumb" data-pagefind-ignore><ol><li>Startseite</li><li>BV</li></ol></nav>
<div class="page-grid"><aside class="left-col">
 <span data-pagefind-meta="title[data-title]" data-title="Art. 189 BV — Zuständigkeiten"></span>
 <h1 class="article-heading"><span>Artikel</span>Art. 189 BV — Zuständigkeiten</h1></aside>
<div class="right-col">
 <section class="gesetzestext"><div class="gesetzestext-header" data-pagefind-ignore>
  <span>Gesetzestext</span><a href="#">Fedlex ↗</a></div>
  <div class="gesetzestext-body">
   <p class="gesetzestext-absatz has-num"><span class="absatz-num">1</span><span class="absatz-text">Satz eins:</span></p>
   <dl class="gesetzestext-list"><dt>a.</dt><dd>erstens;</dd><dt>b.</dt><dd>zweitens.</dd></dl>
   <p class="gesetzestext-absatz has-num"><span class="absatz-num">2</span><span class="absatz-text">Satz zwei.</span></p>
 </div></section>
 <div class="tabs-container">
  <div class="tabs-nav" role="tablist" data-pagefind-ignore><button>Übersicht</button><button>Doktrin</button></div>
  <div class="tab-panel" id="panel-summary" data-label="Übersicht"><div class="panel-body prose"><p>SUMMARY_TEXT</p></div></div>
  <div class="tab-panel hidden has-toc" id="panel-doctrine" data-label="Doktrin">
   <nav class="toc" data-pagefind-ignore>Inhaltsverzeichnis 1. Entstehung</nav>
   <div class="panel-body prose">
    <h2 id="1"><a class="heading-anchor" data-pagefind-ignore href="#1">#</a>1. Entstehung</h2>
    <p class="rz"><strong class="randziffer"><a class="rz-link" href="#n-1">N. 1</a></strong> Lehre mit <em>Betonung</em>, und Fortsetzung.</p>
    <hr/>
    <ul><li><p>Punkt eins</p></li></ul>
   </div></div>
  <div class="tab-panel hidden has-toc" id="panel-caselaw" data-label="Rechtsprechung">
   <nav class="toc" data-pagefind-ignore>Inhaltsverzeichnis</nav>
   <div class="panel-body prose">
    <h3 id="x"><a class="heading-anchor" data-pagefind-ignore href="#x">#</a>Grundsätze</h3>
    <p>BGE-Hinweis mit Erläuterung.</p>
    <blockquote><p>«Zitat aus dem Urteil.»</p></blockquote>
   </div></div>
 </div>
</div></div></div></main></body></html>"""


def test_parse_article_page():
    r = olc.parse_article_html(_PAGE, "189", "de", "https://example/de/bv/art-189")
    assert r["title"] == "Art. 189 BV — Zuständigkeiten"
    assert r["legal_text"] == ("1 Satz eins:\na. erstens;\nb. zweitens.\n2 Satz zwei.")
    assert r["content_text"] == (
        "1. Entstehung\n\n"
        "N. 1 Lehre mit Betonung, und Fortsetzung.\n\n"
        "- Punkt eins\n\n"
        "Rechtsprechung:\n\n"
        "Grundsätze\n\n"
        "BGE-Hinweis mit Erläuterung.\n\n"
        "> «Zitat aus dem Urteil.»")
    # No breadcrumbs, tab labels, TOC or summary panel leak into the text.
    for chrome in ("Startseite", "Inhaltsverzeichnis", "SUMMARY_TEXT", "#", "Fedlex"):
        assert chrome not in r["content_text"]


def test_parse_page_without_commentary_returns_none():
    # Pages for articles not yet commented ("Kommentierung in Vorbereitung")
    # have no tab panels: no row, rather than page chrome as commentary.
    page = ('<html><main><nav class="breadcrumb">Startseite / BV</nav>'
            '<h1>Art. 5a BV</h1><p>' + "Kommentierung in Vorbereitung. " * 5
            + '</p></main></html>')
    assert olc.parse_article_html(page, "5a", "de", "u") is None
