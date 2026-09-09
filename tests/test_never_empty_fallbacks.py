"""Never-empty answers on four research tools (measured 30 days to 2026-09-08).

find_leading_cases answered nothing on 31% of MCP calls because the free-text
query is ANDed onto the citation ranking; search_botschaft on 57% (multi-term
AND); get_commentary missed ~6,700 times a month against ~1,170 open-access
commentaries; get_materialien ~4,100. Each tool now falls back — to the
statute-ranked set re-ordered by ranked OR, to ranked OR, or to what the corpus
does hold on the provision — and FLAGS the fallback (`filters_relaxed`,
`no_commentary`, `no_materialien`) with a one-line note. A strict hit is
unchanged, and every field of a fallback comes from an existing handler.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import mcp_server as m  # noqa: E402


# ── fixtures ──────────────────────────────────────────────────────────

# Decisions: A and HUB apply Art. 41 OR; X1 is off-topic. A's text carries
# "Tierhalter", HUB's carries "Werkeigentümer"; nothing carries "Xyzzy".
_DECISIONS = [
    ("bger_4A_1_2020", "bger", "2020-01-01", "4A_1/2020", "Tierhalterhaftung",
     "Der Tierhalter haftet nach Art. 56 OR und Art. 41 OR"),
    ("bge_BGE_126_I_97", "bge", "1999-06-01", "126 I 97", "Rechtliches Gehör",
     "Werkeigentümerhaftung Art. 58 OR; Anspruch auf rechtliches Gehör"),
    ("bger_6B_9_2019", "bger", "2019-01-01", "6B_9/2019", "Notwehr",
     "Notwehr und Putativnotwehr im Strafrecht"),
]


def _make_decisions_db(path: Path) -> Path:
    c = sqlite3.connect(path)
    c.executescript(
        "CREATE TABLE decisions (decision_id TEXT PRIMARY KEY, court TEXT, canton TEXT,"
        " chamber TEXT, docket_number TEXT, decision_date TEXT, language TEXT,"
        " title TEXT, regeste TEXT, full_text TEXT, source_url TEXT, pdf_url TEXT);"
        "CREATE VIRTUAL TABLE decisions_fts USING fts5("
        "decision_id, court, canton, docket_number, language, title, regeste, full_text);"
    )
    for i, (did, court, date, docket, title, text) in enumerate(_DECISIONS, 1):
        c.execute(
            "INSERT INTO decisions (rowid, decision_id, court, canton, chamber, docket_number,"
            " decision_date, language, title, regeste, full_text, source_url, pdf_url)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (i, did, court, "CH", "", docket, date, "de", title, text[:60], text,
             f"http://x/{i}", ""),
        )
        c.execute(
            "INSERT INTO decisions_fts (rowid, decision_id, court, canton, docket_number,"
            " language, title, regeste, full_text) VALUES (?,?,?,?,?,?,?,?,?)",
            (i, did, court, "CH", docket, "de", title, text[:60], text),
        )
    c.commit()
    c.close()
    return path


def _make_graph(path: Path) -> Path:
    c = sqlite3.connect(path)
    c.executescript(
        """
        CREATE TABLE decisions(decision_id TEXT PRIMARY KEY, court TEXT, decision_date TEXT);
        CREATE TABLE statutes(statute_id INTEGER PRIMARY KEY, law_code TEXT, article TEXT);
        CREATE TABLE decision_statutes(decision_id TEXT, statute_id INTEGER, mention_count INTEGER);
        CREATE TABLE citation_targets(source_decision_id TEXT, target_ref TEXT,
                                      target_decision_id TEXT, match_type TEXT, confidence_score REAL);
        """
    )
    for did, court, date, *_ in _DECISIONS:
        c.execute("INSERT INTO decisions VALUES(?,?,?)", (did, court, date))
    c.execute("INSERT INTO statutes VALUES(1,'OR','41')")
    for did in ("bger_4A_1_2020", "bge_BGE_126_I_97"):
        c.execute("INSERT INTO decision_statutes VALUES(?,1,1)", (did,))
    # HUB is cited by the topic case and by the off-topic one; A by HUB.
    for s, t in (("bger_4A_1_2020", "bge_BGE_126_I_97"),
                 ("bger_6B_9_2019", "bge_BGE_126_I_97"),
                 ("bge_BGE_126_I_97", "bger_4A_1_2020")):
        c.execute("INSERT INTO citation_targets VALUES(?,?,?,?,?)", (s, "r", t, "docket", 0.9))
    c.commit()
    c.close()
    return path


def _row_conn(p):
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    return conn


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    dp = _make_decisions_db(tmp_path / "decisions.db")
    gp = _make_graph(tmp_path / "graph.db")
    monkeypatch.setattr(m, "DB_PATH", dp)
    monkeypatch.setattr(m, "_get_graph_conn", lambda: _row_conn(gp))
    return dp, gp


# ── the ranked-OR helper ───────────────────────────────────────────────

def test_or_query_needs_two_terms_and_drops_operators():
    assert m._fts5_or_query("Tierhalter") == ""
    assert m._fts5_or_query("") == ""
    assert m._fts5_or_query("Tierhalter Xyzzy") == '"Tierhalter" OR "Xyzzy"'
    # the Obligationenrecht abbreviation "OR" is an operator word, dropped;
    # duplicates collapse; a single leftover term is not a relaxation
    assert m._fts5_or_query("Haftung OR Haftung") == ""
    # "Art" is a function word of legal queries and is dropped with the rest
    assert m._fts5_or_query("Art. 41 OR Tierhalter") == '"41" OR "Tierhalter"'


def test_or_query_strips_stop_words_in_every_language():
    # de / fr / it / en function words never become an OR term: on the
    # production table "in" or "der" alone matches most of the ~1M rows
    assert m._fts5_or_query("Haftung des Tierhalters") == '"Haftung" OR "Tierhalters"'
    assert m._fts5_or_query("in der und") == ""
    assert m._fts5_or_query("de la et dans que") == ""
    assert m._fts5_or_query("della che non sono") == ""
    assert m._fts5_or_query("the of and in") == ""
    assert m._fts5_or_query("responsabilité du détenteur d'animal") == (
        '"responsabilité" OR "détenteur" OR "animal"')
    # a single content word next to stop words is not a relaxation either
    assert m._fts5_or_query("die Haftung des") == ""


def test_or_query_is_safe_by_construction_against_hostile_input():
    # Invariant #3 exemption: the OR form never passes _sanitize_fts5, so
    # no operator, column filter or punctuation may leak from the input.
    hostile = 'title:foo NEAR(bar baz) "unterminated OR * ^ - { } ( ) Tierhalter Xyzzy'
    q = m._fts5_or_query(hostile)
    assert q == '"title" OR "foo" OR "bar" OR "baz" OR "unterminated" OR "Tierhalter" OR "Xyzzy"'
    for tok in ("NEAR", ":", "*", "^", "(", ")", "{", "}", "-"):
        assert tok not in q.replace(" OR ", " ")
    # every term is a quoted \w+ token, so the expression always parses
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
    conn.execute("INSERT INTO t VALUES ('Tierhalter')")
    assert conn.execute("SELECT count(*) FROM t WHERE t MATCH ?", (q,)).fetchone()[0] == 1
    assert m._fts5_or_query("\"\" '' `` ; DROP TABLE decisions; --") == '"DROP" OR "TABLE" OR "decisions"'


# ── find_leading_cases ────────────────────────────────────────────────

class _RecordingConn:
    """Wraps a sqlite3 connection and records every SQL text executed."""

    def __init__(self, conn, log):
        self._conn, self._log = conn, log

    def execute(self, sql, *a, **kw):
        self._log.append(" ".join(sql.split()))
        return self._conn.execute(sql, *a, **kw)

    def __getattr__(self, name):
        return getattr(self._conn, name)


@pytest.fixture
def sql_log(corpus, monkeypatch):
    dp, _ = corpus
    log: list[str] = []
    real_get_db = m.get_db

    def _get_db(*a, **kw):
        return _RecordingConn(real_get_db(*a, **kw), log)

    monkeypatch.setattr(m, "get_db", _get_db)
    return log


def test_stop_word_only_query_never_relaxes_to_a_corpus_wide_or(corpus, sql_log):
    # "in" is in no fixture decision, so the strict AND is empty; the OR
    # form of three function words is "" and the relaxed query is never run
    out = m._find_leading_cases(query="in der und", limit=5)
    assert out == {"results": [], "total": 0}
    assert not [s for s in sql_log if "MATERIALIZED" in s or " OR " in s]


def test_relaxed_or_scan_is_capped_and_filtered_inside_the_cap(corpus, sql_log):
    out = m._find_leading_cases(query="Tierhalter Xyzzy", court="bger", limit=5)
    assert out["filters_relaxed"] is True
    assert [r["decision_id"] for r in out["results"]] == ["bger_4A_1_2020"]
    relaxed = [s for s in sql_log if "MATERIALIZED" in s]
    assert len(relaxed) == 1
    sql = relaxed[0]
    # the court filter and the scan ceiling both sit inside the CTE, and
    # the ranking happens over the capped set, never over the whole table
    cte, outer = sql.split(") SELECT", 1)
    assert "d.court = ?" in cte and f"LIMIT {m._OR_RELAX_SCAN_CAP}" in cte
    assert "ORDER BY hits.r LIMIT 2000" in outer
    assert "ORDER BY rank" not in sql


def test_statute_rerank_is_rowid_keyed_and_capped(corpus, sql_log, monkeypatch):
    out = m._find_leading_cases(law_code="OR", article="41", query="Tierhalter Xyzzy", limit=5)
    assert [r["decision_id"] for r in out["results"]] == ["bger_4A_1_2020", "bge_BGE_126_I_97"]
    rerank = [s for s in sql_log if "MATERIALIZED" in s]
    assert len(rerank) == 1
    # keyed by rowid (one doclist seek per candidate), not by the UNINDEXED
    # decision_id column (a filter over every row the OR matches)
    assert "rowid IN (" in rerank[0] and "decision_id IN" not in rerank[0]
    # beyond the ceiling the statute order stands: with a ceiling of one,
    # only HUB (statute rank 1) is scored, and A stays behind it
    monkeypatch.setattr(m, "_OR_RERANK_MAX_CANDIDATES", 1)
    out = m._find_leading_cases(law_code="OR", article="41", query="Tierhalter Xyzzy", limit=5)
    assert out["filters_relaxed"] is True
    assert [r["decision_id"] for r in out["results"]] == ["bge_BGE_126_I_97", "bger_4A_1_2020"]


def test_rerank_helper_keeps_every_candidate(corpus):
    cands = [("bge_BGE_126_I_97", 2), ("bger_4A_1_2020", 1), ("not_in_fts", 0)]
    out = m._rerank_candidates_by_query_or(cands, "Tierhalter Xyzzy")
    assert out == [("bger_4A_1_2020", 1), ("bge_BGE_126_I_97", 2), ("not_in_fts", 0)]
    # nothing to relax: the input order is the output
    assert m._rerank_candidates_by_query_or(cands, "Tierhalter") == cands
    assert m._rerank_candidates_by_query_or([], "Tierhalter Xyzzy") == []


# ── the statute reference lifted out of the free text ─────────────────

def test_known_law_codes_come_from_the_mirror_and_the_alias_table():
    codes = m._known_law_codes()
    assert {"OR", "BV", "ZGB", "CEDH", "EMRK", "CO", "CST"} <= codes
    # function words and made-up codes are not laws
    assert not {"DER", "DES", "IN", "AL", "ZZZQ"} & codes
    assert all(c == c.upper() and not c.endswith(".") for c in codes)


def test_function_word_law_token_is_not_a_statute():
    # QUERY_STATUTE_PATTERN is case-insensitive: "der" / "des" would match
    # the law group and route the call onto a statute that does not exist
    assert m._statute_ref_from_free_text("Art. 29 der BV rechtliches Gehör") is None
    assert m._statute_ref_from_free_text("Art. 8 des Gesetzes") is None
    assert m._statute_ref_from_free_text("art. 8 cedh") is None          # no capital
    assert m._statute_ref_from_free_text("Art. 5 Oder so") is None       # title-case word
    assert m._statute_ref_from_free_text("Art. 5 ZZZQ Haftung") is None  # unknown code
    # known codes in any casing of the "Art." prefix still lift
    assert m._statute_ref_from_free_text("art. 8 CEDH") == ("CEDH", "8", None)
    assert m._statute_ref_from_free_text("Art. 36 Cst. liberté") == ("CST", "36", "liberté")
    assert m._looks_like_law_code("BV") and m._looks_like_law_code("Cst")
    assert not m._looks_like_law_code("der") and not m._looks_like_law_code("Oder")


def test_function_word_query_keeps_the_fts_path(corpus):
    # the old path: strict AND over the whole text, no statute parsing
    out = m._find_leading_cases(query="Art. 41 der OR Tierhalter", limit=5)
    assert "statute_parsed_from_query" not in out
    assert [r["decision_id"] for r in out["results"]] == ["bger_4A_1_2020"]
    assert "filters_relaxed" not in out


def test_free_text_statute_reference_exposes_the_topic_query(corpus):
    out = m._find_leading_cases(query="Art. 41 OR Xyzzy", limit=5)
    assert out["statute_parsed_from_query"] is True and out["topic_query"] == "Xyzzy"
    bare = m._find_leading_cases(query="Art. 41 OR", limit=5)
    assert bare["statute_parsed_from_query"] is True and bare["topic_query"] is None
    explicit = m._find_leading_cases(law_code="OR", article="41", limit=5)
    assert "topic_query" not in explicit


def test_pinpoint_claim_is_the_topic_not_the_provision():
    args = {"query": "Art. 8 BV"}
    assert m._leading_cases_pinpoint_claim(
        args, {"statute_parsed_from_query": True, "topic_query": None}) == ""
    assert m._leading_cases_pinpoint_claim(
        {"query": "Art. 8 BV Privatsphäre"},
        {"statute_parsed_from_query": True, "topic_query": "Privatsphäre"}) == "Privatsphäre"
    assert m._leading_cases_pinpoint_claim({"query": " Tierhalter "}, {"results": []}) == "Tierhalter"
    assert m._leading_cases_pinpoint_claim({}, {"error": "x"}) == ""

def test_statute_path_strict_hit_is_unchanged(corpus):
    out = m._find_leading_cases(law_code="OR", article="41", query="Tierhalter", limit=5)
    ids = [r["decision_id"] for r in out["results"]]
    assert ids == ["bger_4A_1_2020"]
    assert "filters_relaxed" not in out and "note" not in out


def test_statute_path_relaxes_when_the_query_excludes_every_candidate(corpus):
    # "Xyzzy" is in no decision: the strict AND used to answer [] here.
    out = m._find_leading_cases(law_code="OR", article="41", query="Tierhalter Xyzzy", limit=5)
    ids = [r["decision_id"] for r in out["results"]]
    assert out["filters_relaxed"] is True
    assert "Art. 41 OR" in out["note"] and "Tierhalter Xyzzy" in out["note"]
    # both decisions applying the provision are back; the one carrying a
    # query term ranks first, ahead of the better-cited HUB
    assert set(ids) == {"bger_4A_1_2020", "bge_BGE_126_I_97"}
    assert ids[0] == "bger_4A_1_2020"
    assert out["total"] == 2
    # the statute ranking fields are intact
    assert all("topic_citation_count" in r for r in out["results"])


def test_statute_path_single_unknown_term_falls_back_to_statute_order(corpus):
    out = m._find_leading_cases(law_code="OR", article="41", query="Xyzzy", limit=5)
    assert out["filters_relaxed"] is True
    ids = [r["decision_id"] for r in out["results"]]
    # nothing matches any term, so the topical ranking stands: HUB (cited by
    # the topic case) ahead of A
    assert ids == ["bge_BGE_126_I_97", "bger_4A_1_2020"]


def test_statute_reference_is_parsed_out_of_the_free_text():
    assert m._statute_ref_from_free_text("Art. 29 BV rechtliches Gehör") == (
        "BV", "29", "rechtliches Gehör")
    assert m._statute_ref_from_free_text("art. 8 CEDH") == ("CEDH", "8", None)
    assert m._statute_ref_from_free_text("Artikel 336c Abs. 1 OR Kündigung") == (
        "OR", "336c", "Kündigung")
    # two provisions: no single statute path, leave the query alone
    assert m._statute_ref_from_free_text("Art. 41 OR vs Art. 55 OR") is None
    assert m._statute_ref_from_free_text("Tierhalterhaftung") is None


def test_free_text_statute_reference_takes_the_statute_path(corpus):
    out = m._find_leading_cases(query="Art. 41 OR Xyzzy", limit=5)
    assert out["statute_parsed_from_query"] is True
    assert out["law_code"] == "OR" and out["article"] == "41"
    assert out["query"] == "Art. 41 OR Xyzzy"          # the caller's text, echoed
    assert out["filters_relaxed"] is True
    assert {r["decision_id"] for r in out["results"]} == {"bger_4A_1_2020", "bge_BGE_126_I_97"}


def test_free_text_statute_reference_alone_is_a_statute_lookup(corpus):
    out = m._find_leading_cases(query="Art. 41 OR", limit=5)
    assert out["statute_parsed_from_query"] is True
    assert "filters_relaxed" not in out
    assert len(out["results"]) == 2


def test_explicit_statute_args_win_over_the_text(corpus):
    out = m._find_leading_cases(law_code="OR", article="41", query="Art. 58 OR Tierhalter", limit=5)
    assert "statute_parsed_from_query" not in out
    assert out["law_code"] == "OR" and out["article"] == "41"


def test_query_only_path_relaxes_and_to_ranked_or(corpus):
    strict = m._find_leading_cases(query="Tierhalter", limit=5)
    assert [r["decision_id"] for r in strict["results"]] == ["bger_4A_1_2020"]
    assert "filters_relaxed" not in strict

    out = m._find_leading_cases(query="Tierhalter Xyzzy", limit=5)
    assert out["filters_relaxed"] is True
    assert "nearest neighbours" in out["note"]
    assert [r["decision_id"] for r in out["results"]] == ["bger_4A_1_2020"]


def test_query_only_path_keeps_explicit_filters_when_relaxing(corpus):
    # court=bge excludes the only decision carrying "Tierhalter": the
    # relaxation must not drop the caller's court filter to find it
    out = m._find_leading_cases(query="Tierhalter Xyzzy", court="bge", limit=5)
    assert out["results"] == [] and "filters_relaxed" not in out


def test_nothing_matches_any_term_stays_empty(corpus):
    out = m._find_leading_cases(query="Xyzzy Plugh", limit=5)
    assert out == {"results": [], "total": 0}


def test_markdown_carries_the_relaxation_note(corpus):
    out = m._find_leading_cases(law_code="OR", article="41", query="Xyzzy", limit=5)
    text = m._format_leading_cases_response(out)
    assert "_Note: No decision applying Art. 41 OR" in text
    assert "4A_1/2020" in text


# ── get_commentary miss ───────────────────────────────────────────────

def _make_ok_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(
        """CREATE TABLE commentaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ok_uuid TEXT,
            legislative_act_uuid TEXT, sr_number TEXT, abbr TEXT,
            article_num TEXT, title TEXT, language TEXT, date TEXT,
            authors TEXT, editors TEXT, suggested_citation TEXT, html_link TEXT,
            pdf_link TEXT, content_html TEXT, content_text TEXT, legal_text TEXT);
        CREATE VIRTUAL TABLE commentaries_fts USING fts5(
            sr_number, abbr, article_num, title, content_text, language,
            content='commentaries', content_rowid='id');
        CREATE TRIGGER c_ai AFTER INSERT ON commentaries BEGIN
            INSERT INTO commentaries_fts(rowid, sr_number, abbr, article_num, title,
                                         content_text, language)
            VALUES (new.id, new.sr_number, new.abbr, new.article_num, new.title,
                    new.content_text, new.language);
        END;"""
    )
    conn.execute(
        "INSERT INTO commentaries (ok_uuid,legislative_act_uuid,sr_number,abbr,"
        "article_num,title,language,authors,content_text) VALUES (?,?,?,?,?,?,?,?,?)",
        ("a", "act", "220", "OR", "58", "Art. 58 OR", "de", '["Muster"]',
         "Die Werkeigentümerhaftung setzt einen Werkmangel voraus."),
    )
    conn.commit()
    conn.close()


@pytest.fixture
def ok_db(tmp_path, monkeypatch):
    db = tmp_path / "ok.db"
    _make_ok_db(db)
    monkeypatch.setattr(m, "OK_COMMENTARIES_DB_PATH", db)
    return db


_DOCTRINE_STUB = {
    "query": "Art. 41 OR",
    "statute": {"law_code": "OR", "article": "41", "sr_number": "220",
                "lang_served": "de", "text": "Wer einem andern widerrechtlich Schaden zufügt"},
    "doctrine_summary": {"principal_rule": "Haftung aus unerlaubter Handlung",
                         "established_by": "4A_1/2020"},
    "leading_cases": [], "doctrine_timeline": [], "commentary": None, "materialien": None,
}


def _stub_scholarship(sr_number, article=None, limit=20):
    return {"sr_number": sr_number, "article": article, "count": 1, "results": [
        {"pub_id": "p1", "source": "zora", "pub_type": "article",
         "title": "Zur Haftung nach Art. 41 OR", "authors": "Muster",
         "language": "de", "year": 2021, "url": "https://example.org/p1",
         "article": article}]}


def test_commentary_miss_returns_the_fallback_payload(corpus, ok_db, monkeypatch):
    monkeypatch.setattr(m, "_handle_get_doctrine", lambda *, query: dict(_DOCTRINE_STUB))
    monkeypatch.setattr(m, "find_scholarship_citing_statute", _stub_scholarship)
    res = m.get_commentary(abbreviation="OR", article="41")
    assert res.get("no_commentary") is True
    assert "error" not in res and "content_text" not in res
    assert res["law"] == "OR" and res["article"] == "41" and res["sr_number"] == "220"
    # leading cases come from _find_leading_cases over the graph, each with
    # the citation fields the citation helper produces — never composed here
    ids = [r["decision_id"] for r in res["leading_cases"]]
    assert set(ids) == {"bger_4A_1_2020", "bge_BGE_126_I_97"}
    for r in res["leading_cases"]:
        assert r["citation_string_de"] and r["canonical_url"]
    bge = next(r for r in res["leading_cases"] if r["decision_id"] == "bge_BGE_126_I_97")
    assert bge["citation_string_de"] == "BGE 126 I 97"
    # doctrine excerpt and scholarship are the sub-handlers' own output
    assert res["doctrine"]["statute"]["text"].startswith("Wer einem andern")
    assert res["doctrine"]["doctrine_summary"]["principal_rule"] == "Haftung aus unerlaubter Handlung"
    assert res["scholarship_citing_statute"][0]["pub_id"] == "p1"
    assert "No open-access commentary covers Art. 41 OR" in res["note"]
    assert "find_leading_cases" in res["note"]


def test_commentary_miss_markdown_lists_the_fallback(corpus, ok_db, monkeypatch):
    monkeypatch.setattr(m, "_handle_get_doctrine", lambda *, query: dict(_DOCTRINE_STUB))
    monkeypatch.setattr(m, "find_scholarship_citing_statute", _stub_scholarship)
    res = m.get_commentary(abbreviation="OR", article="41")
    text = m._format_get_commentary_response(res)
    assert text.startswith("# No open-access commentary on Art. 41 OR")
    assert "BGE 126 I 97" in text and "Gesetzestext" in text
    assert "Zur Haftung nach Art. 41 OR" in text


def test_commentary_miss_with_nothing_held_is_still_flagged(ok_db, monkeypatch):
    monkeypatch.setattr(m, "_get_graph_conn", lambda: None)
    monkeypatch.setattr(m, "_handle_get_doctrine", lambda *, query: {"error": "x"})
    monkeypatch.setattr(m, "find_scholarship_citing_statute",
                        lambda **kw: {"count": 0, "results": []})
    res = m.get_commentary(abbreviation="ZZZQ", article="7")
    assert res["no_commentary"] is True
    assert res["leading_cases"] == [] and res["doctrine"] is None
    assert "search_commentaries" in res["note"]


def test_commentary_hit_is_unchanged(ok_db):
    res = m.get_commentary(abbreviation="OR", article="58")
    assert res["content_text"].startswith("Die Werkeigentümerhaftung")
    assert "no_commentary" not in res and "found" not in res


def test_miss_payloads_carry_a_machine_readable_found_false(corpus, ok_db, mat_db, monkeypatch):
    monkeypatch.setattr(m, "_handle_get_doctrine", lambda *, query: dict(_DOCTRINE_STUB))
    monkeypatch.setattr(m, "find_scholarship_citing_statute", _stub_scholarship)
    monkeypatch.setattr(m, "_resolve_materialien_sr", lambda conn, law: "220")
    c = m.get_commentary(abbreviation="OR", article="41")
    assert c["found"] is False and c["no_commentary"] is True and "error" not in c
    assert m._is_miss_payload(c)
    mat = m.get_materialien("OR", "41")
    assert mat["found"] is False and mat["no_materialien"] is True and "error" not in mat
    assert m._is_miss_payload(mat)
    # the note quotes the corpus size read from the database, not a
    # number written into the code
    assert "the 1 OnlineKommentar.ch / OpenLegalCommentary.ch commentaries" in c["note"]


def test_is_miss_payload_reads_error_and_found():
    assert m._is_miss_payload(None) and m._is_miss_payload({})
    assert m._is_miss_payload({"error": "Database error"})
    assert m._is_miss_payload({"found": False, "no_commentary": True, "leading_cases": [{"x": 1}]})
    assert not m._is_miss_payload({"content_text": "Die Werkeigentümerhaftung", "law": "OR"})
    assert not m._is_miss_payload({"found": True})


def _legislation_stub(**kw):
    return {"abbreviation": "OR", "title": "Obligationenrecht", "consolidation_date": None,
            "articles": [{"article_num": "41", "text": "Wer einem andern widerrechtlich"},
                         {"article_num": "58", "text": "Der Eigentümer eines Gebäudes"}]}


def test_article_history_has_commentary_is_false_on_a_fallback(corpus, ok_db, monkeypatch, tmp_path):
    monkeypatch.setattr(m, "_handle_get_doctrine", lambda *, query: dict(_DOCTRINE_STUB))
    monkeypatch.setattr(m, "find_scholarship_citing_statute", _stub_scholarship)
    monkeypatch.setattr(m, "_get_legislation_local", _legislation_stub)
    monkeypatch.setenv("SWISS_CASELAW_MATERIALIEN_DB", str(tmp_path / "absent.db"))
    # Art. 41 OR has no commentary: get_commentary answers with the
    # never-empty fallback, which must not count as a commentary
    miss = m._handle_get_article_history(sr_number="220", article="41")
    assert miss["summary"]["has_commentary"] is False
    assert not [e for e in miss["timeline"] if e.get("kind") == "commentary"]
    assert miss["summary"]["leading_cases_count"] == 2
    # Art. 58 OR has one: the hit still counts
    hit = m._handle_get_article_history(sr_number="220", article="58")
    assert hit["summary"]["has_commentary"] is True


def test_commentary_miss_survives_the_real_doctrine_cascade(corpus, ok_db):
    # No stubs: _statute_fallback_context runs _handle_get_doctrine and
    # find_scholarship_citing_statute for real against the fixture corpus
    # (no statutes.db, no scholarship db). Nothing may raise, and the
    # leading cases still arrive with the helper's citation strings.
    res = m.get_commentary(abbreviation="OR", article="41")
    assert res["found"] is False and res["no_commentary"] is True
    ids = {r["decision_id"] for r in res["leading_cases"]}
    assert ids == {"bger_4A_1_2020", "bge_BGE_126_I_97"}
    assert all(r.get("citation_string_de") for r in res["leading_cases"])
    assert isinstance(res["scholarship_citing_statute"], list)
    assert res["doctrine"] is None or res["doctrine"].get("source") == "get_doctrine"


def test_get_doctrine_concept_path_inherits_the_relaxation_flag(corpus):
    strict = m._handle_get_doctrine(query="Tierhalter")
    assert "filters_relaxed" not in strict
    relaxed = m._handle_get_doctrine(query="Tierhalter Xyzzy")
    assert relaxed["filters_relaxed"] is True
    assert "nearest neighbours" in relaxed["note"]
    assert [c["decision_id"] for c in relaxed["leading_cases"]] == ["bger_4A_1_2020"]


def test_search_commentaries_relaxes_and_to_or(ok_db):
    strict = m.search_commentaries(query="Werkeigentümerhaftung Werkmangel")
    assert strict["count"] == 1 and "filters_relaxed" not in strict
    res = m.search_commentaries(query="Werkeigentümerhaftung Xyzzy")
    assert res["count"] == 1 and res["filters_relaxed"] is True
    assert res["results"][0]["article_num"] == "58"
    assert "ranked OR" in res["note"]
    empty = m.search_commentaries(query="Xyzzy Plugh")
    assert empty["count"] == 0 and "filters_relaxed" not in empty


# ── get_materialien miss ──────────────────────────────────────────────

@pytest.fixture
def mat_db(tmp_path, monkeypatch):
    p = tmp_path / "materialien.db"
    c = sqlite3.connect(p)
    c.executescript("""
        CREATE TABLE materialien (id INTEGER PRIMARY KEY AUTOINCREMENT,
            law_code TEXT NOT NULL, sr_number TEXT, article TEXT NOT NULL,
            bbl_ref TEXT NOT NULL, bbl_page_refs TEXT, legislative_intent TEXT,
            key_arguments TEXT, design_choices TEXT, rejected_alternatives TEXT,
            general_context TEXT);
        CREATE TABLE botschaft_documents (botschaft_id INTEGER PRIMARY KEY,
            bbl_year INTEGER NOT NULL, bbl_page INTEGER NOT NULL,
            bbl_citation TEXT NOT NULL, eli_uri TEXT, title TEXT,
            publication_date TEXT, source_url TEXT NOT NULL, format TEXT,
            language TEXT);
        CREATE TABLE botschaft_paragraphs (paragraph_id INTEGER PRIMARY KEY,
            botschaft_id INTEGER NOT NULL, para_order INTEGER NOT NULL,
            page_number INTEGER, section_path TEXT, article_anchor TEXT,
            text TEXT, text_length INTEGER);
        CREATE TABLE article_botschaft_links (sr_number TEXT NOT NULL,
            article TEXT NOT NULL, botschaft_id INTEGER NOT NULL,
            relation TEXT NOT NULL, evidence TEXT,
            PRIMARY KEY (sr_number, article, botschaft_id, relation));
        CREATE TABLE amendment_refs (sr_number TEXT, article TEXT, ref_type TEXT,
            year INTEGER, page INTEGER, fedlex_url TEXT, context TEXT);
        CREATE TABLE parliamentary_modifications (law_code TEXT, council TEXT,
            date TEXT, text TEXT);
        CREATE TABLE debate_pages (id INTEGER PRIMARY KEY, law_code TEXT,
            council TEXT, page_num INTEGER, text TEXT);
        CREATE VIRTUAL TABLE materialien_fts USING fts5(law_code, article,
            bbl_ref, legislative_intent, key_arguments, general_context);
        CREATE VIRTUAL TABLE debate_fts USING fts5(law_code, council, text);
    """)
    c.execute("INSERT INTO materialien (law_code, sr_number, article, bbl_ref, "
              "legislative_intent) VALUES ('BV','101','8','BBl 1997 I 1',"
              "'Rechtsgleichheit vor dem Gesetz')")
    c.execute("INSERT INTO materialien_fts (rowid, law_code, article, bbl_ref, "
              "legislative_intent) VALUES (1,'BV','8','BBl 1997 I 1',"
              "'Rechtsgleichheit vor dem Gesetz')")
    c.commit()
    c.close()
    monkeypatch.setattr(m, "MATERIALIEN_DB_PATH", p)
    return p


def test_materialien_miss_returns_the_fallback_payload(corpus, mat_db, monkeypatch):
    monkeypatch.setattr(m, "_handle_get_doctrine", lambda *, query: dict(_DOCTRINE_STUB))
    monkeypatch.setattr(m, "find_scholarship_citing_statute", _stub_scholarship)
    monkeypatch.setattr(m, "_resolve_materialien_sr", lambda conn, law: "220")
    res = m.get_materialien("OR", "41")
    assert res["no_materialien"] is True and "error" not in res
    # success shape kept, lists empty
    assert res["sources"] == [] and res["botschaft_documents"] == []
    assert res["amendment_refs"] == [] and res["parliamentary_modifications"] == []
    assert res["law_code"] == "OR" and res["article"] == "41" and res["sr_number"] == "220"
    assert {r["decision_id"] for r in res["leading_cases"]} == {
        "bger_4A_1_2020", "bge_BGE_126_I_97"}
    assert all(r.get("citation_string_de") for r in res["leading_cases"])
    assert res["doctrine"]["statute"]["sr_number"] == "220"
    assert res["scholarship_citing_statute"][0]["pub_id"] == "p1"
    assert "search_botschaft" in res["note"] and "find_leading_cases" in res["note"]


def test_materialien_hit_is_unchanged(mat_db):
    res = m.get_materialien("BV", "8")
    assert res["sources"][0]["legislative_intent"].startswith("Rechtsgleichheit")
    assert "no_materialien" not in res


def test_search_materialien_relaxes_and_to_or(mat_db):
    res = m.search_materialien("Rechtsgleichheit Xyzzy")
    assert res["count"] == 1 and res["filters_relaxed"] is True
    assert res["results"][0]["article"] == "8"
    assert "ranked OR" in res["note"]
    empty = m.search_materialien("Xyzzy Plugh")
    assert empty["count"] == 0 and "filters_relaxed" not in empty
    assert "coverage limit" in empty["note"]


# ── search_botschaft ──────────────────────────────────────────────────

def _make_botschaft_db(path: Path):
    c = sqlite3.connect(path)
    c.executescript(
        """
        CREATE TABLE botschaft_documents (
            botschaft_id TEXT PRIMARY KEY, bbl_citation TEXT, eli_uri TEXT,
            language TEXT, publication_date TEXT, bbl_year INTEGER
        );
        CREATE TABLE botschaft_paragraphs (
            paragraph_id INTEGER PRIMARY KEY, botschaft_id TEXT, page_number INTEGER,
            section_path TEXT, article_anchor TEXT, text TEXT
        );
        CREATE VIRTUAL TABLE botschaft_paragraphs_fts USING fts5(text);
        """
    )
    c.execute("INSERT INTO botschaft_documents VALUES ('d1990','BBl 1990 I 1','eli1','de',NULL,1990)")
    c.execute("INSERT INTO botschaft_documents VALUES ('d2020','BBl 2020 II 2','eli2','de',NULL,2020)")
    for pid, bid, txt in [
        (1, "d1990", "Text ueber Vaterschaftsurlaub aus 1990"),
        (2, "d2020", "Text ueber Vaterschaftsurlaub und Entschaedigung aus 2020"),
    ]:
        c.execute("INSERT INTO botschaft_paragraphs VALUES (?,?,?,?,?,?)",
                  (pid, bid, 10, "1.1", "Art. 1", txt))
        c.execute("INSERT INTO botschaft_paragraphs_fts (rowid, text) VALUES (?,?)", (pid, txt))
    c.commit()
    c.close()


@pytest.fixture
def botschaft_db(tmp_path, monkeypatch):
    db = tmp_path / "materialien.db"
    _make_botschaft_db(db)
    monkeypatch.setenv("SWISS_CASELAW_MATERIALIEN_DB", str(db))
    return db


def test_search_botschaft_strict_hit_is_unchanged(botschaft_db):
    res = m._handle_search_botschaft(query="Vaterschaftsurlaub Entschaedigung")
    assert res["total"] == 1 and "filters_relaxed" not in res and "note" not in res


def test_search_botschaft_relaxes_and_to_ranked_or(botschaft_db):
    res = m._handle_search_botschaft(query="Vaterschaftsurlaub Entschaedigung Xyzzy")
    assert res["filters_relaxed"] is True
    assert res["total"] == 2
    # BM25 puts the paragraph carrying two of the three terms first
    assert res["results"][0]["bbl_year"] == 2020
    assert "ranked OR" in res["note"] and "verbatim" in res["note"]
    assert "_hint" not in res


def test_search_botschaft_relaxation_keeps_the_year_filter(botschaft_db):
    res = m._handle_search_botschaft(query="Vaterschaftsurlaub Xyzzy", year_max=2000)
    assert res["filters_relaxed"] is True
    assert [r["bbl_year"] for r in res["results"]] == [1990]
    assert res["year_filter"] == {"min": None, "max": 2000}


def test_search_botschaft_nothing_matches_any_term_stays_empty(botschaft_db):
    res = m._handle_search_botschaft(query="Xyzzy Plugh")
    assert res["total"] == 0 and "_hint" in res and "filters_relaxed" not in res


# ── outcome accounting ────────────────────────────────────────────────

def _as_result(payload: dict):
    import json
    from mcp.types import TextContent
    return [TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))]


def test_an_empty_fallback_still_counts_as_empty():
    assert m._classify_outcome(_as_result({
        "law": "ZZZQ", "article": "7", "found": False, "no_commentary": True, "note": "n",
        "doctrine": None, "leading_cases": [], "scholarship_citing_statute": [],
        "source": "s"})) == "empty"
    assert m._classify_outcome(_as_result({
        "law_code": "ZZZ", "sr_number": None, "article": None, "found": False, "no_materialien": True,
        "sources": [], "botschaft_documents": [], "amendment_refs": [],
        "parliamentary_modifications": [], "doctrine": None, "leading_cases": [],
        "scholarship_citing_statute": [], "note": "n"})) == "empty"


def test_a_fallback_with_content_counts_as_substantive():
    assert m._classify_outcome(_as_result({
        "law": "OR", "article": "41", "no_commentary": True, "note": "n",
        "doctrine": None, "leading_cases": [{"decision_id": "bger_4A_1_2020"}],
        "scholarship_citing_statute": []})) == "substantive"
    assert m._payload_outcome({
        "law": "OR", "article": "41", "no_commentary": True,
        "leading_cases": [{"decision_id": "x"}]}) == ("substantive", None)
    assert m._payload_outcome({
        "law": "ZZZQ", "article": "7", "no_commentary": True,
        "leading_cases": [], "scholarship_citing_statute": []}) == ("empty", None)
