"""find_leading_cases, free-text mode: rank by what the regeste says the case
decides, not by citation count alone.

Live 2026-09-24: "résiliation abusive du bail" returned BGE 141 IV 1
(standing of the private plaintiff, 4,885 citations; the phrase appears once
in E. 3.4) and BGE 145 I 73 (Neuchâtel travellers' sites) ahead of every
lease-termination case, because every text match was ranked by citations.
The fix re-ranks the most-cited matches by the share of query terms their
regeste and title carry, and collapses the two ids a BGE can carry. Measured
on production before shipping; these fixtures pin the mechanism.
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

_LEASE = "Die Kündigung des Mietverhältnisses; résiliation abusive du bail"

# (decision_id, court, docket, regeste, full_text, citations)
_DECISIONS = [
    # the megacase: its regeste is about standing, the lease phrase is in passing
    ("bge_BGE_141_IV_1", "bge", "BGE 141 IV 1",
     "Legitimation der Privatklägerschaft zur Beschwerde in Strafsachen; qualité pour recourir",
     "Qualité pour recourir de la partie plaignante ... en E. 3.4 la résiliation abusive du bail",
     9),
    # on topic, both ids of one BGE: the bare id carries only a German
    # regeste, the prefixed one the trilingual regeste
    ("bge_120 II 31", "bge", "120 II 31",
     "Anfechtung einer Kündigung, die gegen Treu und Glauben verstösst (Art. 271 OR)",
     "résiliation abusive du bail " + _LEASE, 4),
    ("bge_BGE_120_II_31", "bge", "BGE 120 II 31",
     "Anfechtung einer Kündigung (Art. 271 OR). Résiliation abusive du bail. Disdetta abusiva",
     "résiliation abusive du bail " + _LEASE, 3),
    # on topic, cited less: must still beat the megacase
    ("bger_4A_345_2007", "bger", "4A_345/2007",
     "Congé contraire à la bonne foi; résiliation abusive du bail à loyer",
     "résiliation abusive du bail", 2),
    # partial regeste match (2 of 3 terms), much cited
    ("bge_BGE_133_III_61", "bge", "BGE 133 III 61",
     "Bail à loyer; protection contre les loyers abusifs",
     "résiliation abusive du bail; loyers abusifs", 8),
    # full regeste match, barely cited
    ("bger_4A_1_2024", "bger", "4A_1/2024",
     "Résiliation abusive du bail",
     "résiliation abusive du bail", 1),
    # no regeste at all: nothing to judge by, sits between the bands
    ("bger_4A_9_2010", "bger", "4A_9/2010", "",
     "résiliation abusive du bail", 6),
]


def _make_decisions_db(path: Path) -> Path:
    c = sqlite3.connect(path)
    c.executescript(
        "CREATE TABLE decisions (decision_id TEXT PRIMARY KEY, court TEXT, canton TEXT,"
        " chamber TEXT, docket_number TEXT, decision_date TEXT, language TEXT,"
        " title TEXT, regeste TEXT, full_text TEXT, source_url TEXT, pdf_url TEXT);"
        "CREATE VIRTUAL TABLE decisions_fts USING fts5("
        "decision_id UNINDEXED, court, canton, docket_number, language, title, regeste,"
        " full_text, tokenize='unicode61 remove_diacritics 2');"
    )
    for i, (did, court, docket, regeste, text, _) in enumerate(_DECISIONS, 1):
        c.execute(
            "INSERT INTO decisions VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (did, court, "CH", "", docket, "2010-01-01", "fr", "", regeste, text,
             f"http://x/{i}", ""),
        )
        c.execute(
            "INSERT INTO decisions_fts (rowid, decision_id, court, canton, docket_number,"
            " language, title, regeste, full_text) VALUES (?,?,?,?,?,?,?,?,?)",
            (i, did, court, "CH", docket, "fr", "", regeste, text),
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
    c.execute("INSERT INTO statutes VALUES(1,'OR','271')")
    for did in ("bge_BGE_141_IV_1", "bger_4A_345_2007"):
        c.execute("INSERT INTO decision_statutes VALUES(?,1,1)", (did,))
    for did, court, *_rest, cites in _DECISIONS:
        c.execute("INSERT INTO decisions VALUES(?,?,?)", (did, court, "2010-01-01"))
        for k in range(cites):
            c.execute("INSERT INTO citation_targets VALUES(?,?,?,?,?)",
                      (f"src_{did}_{k}", "r", did, "docket", 0.9))
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


# ── term extraction and matching ───────────────────────────────────────

def test_topic_terms_fold_stem_and_drop_function_and_statute_words():
    assert m._leading_topic_terms("résiliation abusive du bail") == ["resiliati", "abusi", "bail"]
    # statute tokens and numbers are not topic words; ß folds, short words stay whole
    assert m._leading_topic_terms("Art. 271 Abs. 1 OR Kündigung Miete") == ["kundigu", "miet"]
    assert m._leading_topic_terms("Straße der Haftung") == ["stras", "haftu"]
    assert m._leading_topic_terms("in der und") == []


def test_regeste_share_matches_word_starts_across_inflections():
    terms = m._leading_topic_terms("congé abusif")
    assert m._regeste_term_share(terms, "Congés abusifs; résiliation du bail à loyer") == 1.0
    # a word start, not a substring anywhere
    assert m._regeste_term_share(m._leading_topic_terms("Bild"), "Abbildung und Unterbild") == 0.0
    assert m._regeste_term_share(m._leading_topic_terms("Kündigung"),
                                 "Kündigungsschutz im Mietrecht") == 1.0
    # no regeste to judge by
    assert m._regeste_term_share(terms, "") is None
    assert m._regeste_term_share(terms, "Mietrecht") is None


def test_sort_key_bands():
    full, partial, some, unknown, none = (
        m._leading_topic_key(1.0, 1),
        m._leading_topic_key(2 / 3, 8),
        m._leading_topic_key(1 / 3, 900),
        m._leading_topic_key(None, 900),
        m._leading_topic_key(0.0, 5000),
    )
    # a much-cited partial match may pass a barely cited full match ...
    assert partial < full
    # ... but below the partial band citations never lift a case over it
    assert full < some and partial < some
    assert some[0] == unknown[0] < none[0]
    assert m._leading_topic_key(2 / 3, 4) > m._leading_topic_key(1.0, 1)


# ── the tool ───────────────────────────────────────────────────────────

def test_incidental_megacase_no_longer_leads(corpus):
    out = m._find_leading_cases(query="résiliation abusive du bail", limit=10)
    ids = [r["decision_id"] for r in out["results"]]
    # by citations alone the megacase (9) led; its regeste carries none of the terms
    assert ids[-1] == "bge_BGE_141_IV_1"
    assert ids.index("bger_4A_345_2007") < ids.index("bge_BGE_141_IV_1")
    mega = out["results"][-1]
    assert mega["regeste_match"] == 0.0
    assert mega["citation_count"] == 9


def test_ranking_order_and_regeste_match_field(corpus):
    out = m._find_leading_cases(query="résiliation abusive du bail", limit=10)
    got = [(r["docket_number"], r["regeste_match"]) for r in out["results"]]
    assert got == [
        ("120 II 31", 1.0),        # full match, 4 citations
        ("4A_345/2007", 1.0),      # full match, 2
        ("BGE 133 III 61", 0.67),  # partial, 8 x 0.2 = 1.6
        ("4A_1/2024", 1.0),        # full match, 1
        ("4A_9/2010", None),       # no regeste, 6
        ("BGE 141 IV 1", 0.0),     # incidental, 9
    ]


def test_bge_dual_ids_collapse_to_one_entry_with_the_better_regeste(corpus):
    out = m._find_leading_cases(query="résiliation abusive du bail", limit=10)
    dockets = [r["docket_number"] for r in out["results"]]
    # only the prefixed id's regeste is trilingual (its bare twin's German
    # regeste carries none of the terms), but the bare id is better cited:
    # one entry, under the better-cited id, judged by the better regeste
    assert dockets.count("120 II 31") + dockets.count("BGE 120 II 31") == 1
    top = out["results"][0]
    assert top["decision_id"] == "bge_120 II 31"
    assert top["citation_count"] == 4
    assert top["regeste_match"] == 1.0


def test_relaxed_page_is_ranked_by_regeste_too(corpus):
    out = m._find_leading_cases(query="résiliation Xyzzy", limit=10)
    assert out["filters_relaxed"] is True
    assert "regeste carries more of them first" in out["note"]
    ids = [r["decision_id"] for r in out["results"]]
    # the megacase is still the most cited, and still behind every decision
    # whose regeste carries a query term
    assert ids.index("bger_4A_345_2007") < ids.index("bge_BGE_141_IV_1")
    assert all("regeste_match" in r for r in out["results"])


def test_statute_path_is_untouched(corpus):
    out = m._find_leading_cases(law_code="OR", article="271", limit=5)
    # ranked by the graph as before: the megacase applies the provision and
    # is the most cited, and no regeste re-ranking touches the page
    assert [r["decision_id"] for r in out["results"]] == [
        "bge_BGE_141_IV_1", "bger_4A_345_2007"]
    assert all("regeste_match" not in r for r in out["results"])


def test_citation_order_stands_when_the_regeste_lookup_fails(corpus, monkeypatch):
    def _boom(ids):
        raise sqlite3.OperationalError("disk I/O error")

    cands = [("a", 9), ("b", 3)]
    monkeypatch.setattr(m, "_fetch_decision_rows_by_ids", _boom)
    assert m._rank_leading_by_regeste(cands, "Kündigung") == (cands, {})
    # nothing to judge a query of function words by either
    assert m._rank_leading_by_regeste(cands, "in der und") == (cands, {})


def test_regeste_scoped_expression_parses_for_every_sanitizer_shape():
    # the pool augment wraps the sanitized query in a column filter; each
    # shape _sanitize_fts5 can emit must stay valid FTS5 inside it
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE VIRTUAL TABLE decisions_fts USING fts5("
                 "decision_id UNINDEXED, court, canton, docket_number, language, title,"
                 " regeste, full_text)")
    conn.execute("INSERT INTO decisions_fts VALUES "
                 "('x','bge','CH','1','de','','Kündigung Treu und Glauben','Kündigung')")
    for q in ('"Treu und Glauben" Kündigung', "Kündigung NEAR/5 Mietvertrag",
              "regeste:Kündigung Miete", "Kryptobestände OR cryptomonnaies",
              "Art. 271 OR", "Kündigung*", "l'obligation", "full_text:Kündigung x"):
        expr = f"{{title regeste}} : ({m._sanitize_fts5(q)})"
        conn.execute("SELECT count(*) FROM decisions_fts WHERE decisions_fts MATCH ?",
                     (expr,)).fetchone()
