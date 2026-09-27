"""BGE dual ids (#40): one BGE is served under "bge_140 III 244" (direct
search.bger.ch scraper) and "bge_BGE_140_III_244" (entscheidsuche feed).

Live 2026-09-27: find_leading_cases(law_code='OR', article='273') listed
BGE 140 III 244 twice, once as "bge_140 III 244" dated 1990-05-09 — the date
of the VMWG ordinance, taken from the body text by the 2026-03-12 date repair
pass — and once as "bge_BGE_140_III_244" dated 2014-05-19, the Urteilskopf
date. A 2026-09-27 replica of the served build holds 15,038 such BGEs
(9 of them under three ids: Ia/Ib are stored as "IA" by the direct scraper and
as both "Ia" and "IA" by the feed), 6,813 dated differently. Neither id carries the right date reliably: the
entscheidsuche rows hold 1 January volume placeholders for most of the
disagreeing pairs. These fixtures pin the fold: one entry per BGE, under the
bge_BGE_ id, with the first credible date, and counts that are never summed.
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

# (decision_id, court, docket, date, regeste, citing decisions)
_DECISIONS = [
    # the reported pair: the space id carries the ordinance date, the
    # prefixed id the ruling date
    ("bge_140 III 244", "bge", "140 III 244", "1990-05-09",
     "Kündigung des Mietvertrags; amtliches Formular", 5),
    ("bge_BGE_140_III_244", "bge", "BGE 140 III 244", "2014-05-19",
     "Kündigung des Mietvertrags; amtliches Formular (Art. 266l OR)", 4),
    # the common shape: the prefixed id holds the volume placeholder, the
    # space id the ruling date
    ("bge_151 III 9", "bge", "151 III 9", "2024-08-07",
     "Anfechtung der Kündigung", 3),
    ("bge_BGE_151_III_9", "bge", "BGE 151 III 9", "2025-01-01",
     "Anfechtung der Kündigung (Art. 273 OR)", 3),
    # a BGer ruling on the same provision, cited least
    ("bger_4A_1_2024", "bger", "4A_1/2024", "2024-03-01",
     "Frist zur Anfechtung der Kündigung", 1),
]

# citing decisions shared by both ids of a pair: the graph resolves most
# citations to both, so the fold must not add them up
_SHARED = {"140|III|244": 3, "151|III|9": 3}


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
    for i, (did, court, docket, date, regeste, _) in enumerate(_DECISIONS, 1):
        c.execute(
            "INSERT INTO decisions VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (did, court, "CH", "", docket, date, "de", "", regeste,
             "Kündigung " + regeste, f"http://x/{i}", ""),
        )
        c.execute(
            "INSERT INTO decisions_fts (rowid, decision_id, court, canton, docket_number,"
            " language, title, regeste, full_text) VALUES (?,?,?,?,?,?,?,?,?)",
            (i, did, court, "CH", docket, "de", "", regeste, "Kündigung " + regeste),
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
    c.execute("INSERT INTO statutes VALUES(1,'OR','273')")
    for did, court, _docket, date, _reg, cites in _DECISIONS:
        c.execute("INSERT INTO decisions VALUES(?,?,?)", (did, court, date))
        c.execute("INSERT INTO decision_statutes VALUES(?,1,1)", (did,))
        key = m._bge_twin_key(did)
        for k in range(cites):
            # the first _SHARED sources of a pair cite both of its ids; each
            # source also applies Art. 273 OR, so every edge is topical
            src = f"src_{key}_{k}" if key and k < _SHARED[key] else f"src_{did}_{k}"
            c.execute("INSERT INTO citation_targets VALUES(?,?,?,?,?)",
                      (src, "r", did, "docket", 0.9))
            c.execute("INSERT OR IGNORE INTO decisions VALUES(?,?,?)", (src, "bger", "2020-01-01"))
            c.execute("INSERT INTO decision_statutes VALUES(?,1,1)", (src,))
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


# ── identity key ───────────────────────────────────────────────────────

def test_twin_key_joins_both_id_forms_and_nothing_else():
    assert m._bge_twin_key("bge_140 III 244") == "140|III|244"
    assert m._bge_twin_key("bge_BGE_140_III_244") == "140|III|244"
    assert m._bge_twin_key("bge_BGE_116_Ia_28") == m._bge_twin_key("bge_116 Ia 28")
    # the division's case differs between the sources: one key for all three
    assert m._bge_twin_key("bge_116 IA 28") == "116|Ia|28"
    assert m._bge_twin_key("bge_BGE_116_IA_28") == "116|Ia|28"
    assert m._bge_twin_key("bge_BGE_116_Ib_28") != m._bge_twin_key("bge_BGE_116_Ia_28")
    # the page is exact: 131 III 12 is not 131 III 121
    assert m._bge_twin_key("bge_131 III 12") != m._bge_twin_key("bge_BGE_131_III_121")
    for other in ("bger_4A_1_2024", "bge_egmr_20251211_9087_18", "bge_19791204_7710_76", "", None):
        assert m._bge_twin_key(other) is None


def test_twin_rank_puts_the_id_cite_resolves_first():
    ids = ["bge_116 IA 28", "bge_BGE_116_IA_28", "bge_BGE_116_Ia_28"]
    assert sorted(ids, key=m._bge_twin_rank)[0] == "bge_BGE_116_Ia_28"
    assert m._bge_ref_candidates("BGE 116 Ia 28")[0] == "bge_BGE_116_Ia_28"
    assert m._bge_twin_rank("bge_BGE_140_III_244") < m._bge_twin_rank("bge_140 III 244")


# ── date choice ────────────────────────────────────────────────────────

def _r(did, date):
    return {"decision_id": did, "decision_date": date}


def test_twin_date_skips_a_date_outside_the_volume_window():
    # vol 140 collects 2014: 1990 is the ordinance date from the body text
    assert m._bge_twin_date([_r("bge_140 III 244", "1990-05-09"),
                             _r("bge_BGE_140_III_244", "2014-05-19")]) == "2014-05-19"


def test_twin_date_skips_the_volume_placeholder():
    assert m._bge_twin_date([_r("bge_BGE_151_III_9", "2025-01-01"),
                             _r("bge_151 III 9", "2024-08-07")]) == "2024-08-07"


def test_twin_date_prefers_the_prefixed_row_when_both_are_credible():
    assert m._bge_twin_date([_r("bge_151 I 32", "2024-03-27"),
                             _r("bge_BGE_151_I_32", "2025-02-20")]) == "2025-02-20"


def test_twin_date_falls_back_to_the_prefixed_row_when_neither_is_credible():
    assert m._bge_twin_date([_r("bge_150 IV 10", "2024-01-01"),
                             _r("bge_BGE_150_IV_10", "2023-01-01")]) == "2023-01-01"


def test_twin_date_reads_sqlite_rows(tmp_path):
    c = sqlite3.connect(tmp_path / "r.db")
    c.row_factory = sqlite3.Row
    rows = c.execute("SELECT 'bge_140 III 244' AS decision_id, '1990-05-09' AS decision_date "
                     "UNION ALL SELECT 'bge_BGE_140_III_244', '2014-05-19'").fetchall()
    assert m._bge_twin_date(rows) == "2014-05-19"


# ── candidate fold ─────────────────────────────────────────────────────

def test_fold_keeps_the_better_ranked_slot_and_takes_the_maximum():
    cands = [("bge_140 III 244", 5), ("bger_4A_1_2024", 4), ("bge_BGE_140_III_244", 4)]
    folded, glob = m._fold_bge_twin_candidates(
        cands, {"bge_140 III 244": 256, "bger_4A_1_2024": 9, "bge_BGE_140_III_244": 214})
    assert folded == [("bge_140 III 244", 5), ("bger_4A_1_2024", 4)]
    # 256 and 214 edges share 198 citing decisions: never 470
    assert glob["bge_140 III 244"] == 256


def test_fold_joins_the_three_ids_of_an_ia_ruling():
    cands = [("bge_116 IA 28", 7), ("bge_BGE_116_IA_28", 5), ("bge_BGE_116_Ia_28", 6)]
    folded, glob = m._fold_bge_twin_candidates(cands, {})
    assert folded == [("bge_116 IA 28", 7)]


def test_fold_leaves_a_list_without_pairs_alone():
    cands = [("bger_4A_1_2024", 3), ("bge_BGE_151_III_9", 2)]
    assert m._fold_bge_twin_candidates(cands, {}) == (cands, {})


# ── find_leading_cases ─────────────────────────────────────────────────

def test_statute_path_lists_each_bge_once_under_the_prefixed_id(corpus):
    out = m._find_leading_cases(law_code="OR", article="273", limit=10)
    ids = [r["decision_id"] for r in out["results"]]
    assert ids == ["bge_BGE_140_III_244", "bge_BGE_151_III_9", "bger_4A_1_2024"]
    by_id = {r["decision_id"]: r for r in out["results"]}
    assert by_id["bge_BGE_140_III_244"]["decision_date"] == "2014-05-19"
    # the placeholder of the prefixed row gives way to the space row's date
    assert by_id["bge_BGE_151_III_9"]["decision_date"] == "2024-08-07"
    # 5 and 4 edges, 3 of them from the same citing decisions: the larger
    assert by_id["bge_BGE_140_III_244"]["citation_count"] == 5
    assert by_id["bge_BGE_140_III_244"]["topic_citation_count"] == 5


def test_statute_path_fills_the_page_after_folding(corpus):
    out = m._find_leading_cases(law_code="OR", article="273", limit=2)
    assert [r["decision_id"] for r in out["results"]] == [
        "bge_BGE_140_III_244", "bge_BGE_151_III_9"]


def test_global_path_folds_too(corpus):
    out = m._find_leading_cases(limit=10)
    keys = [m._bge_twin_key(r["decision_id"]) for r in out["results"]]
    bge_keys = [k for k in keys if k]
    assert sorted(bge_keys) == ["140|III|244", "151|III|9"]


def test_statute_path_with_date_filter_keeps_the_filtered_row(corpus):
    # a date filter runs in the graph on the stored date, before the fold:
    # only the row whose own date passes is a candidate, and its pair is
    # still served with the credible date
    out = m._find_leading_cases(law_code="OR", article="273",
                                date_from="2014-01-01", date_to="2014-12-31")
    assert [(r["decision_id"], r["decision_date"]) for r in out["results"]] == [
        ("bge_BGE_140_III_244", "2014-05-19")]


# ── search fold ────────────────────────────────────────────────────────

def test_search_fold_moves_the_kept_row_to_the_prefixed_id_and_credible_date():
    kept = {"decision_id": "bge_140 III 244", "decision_date": "1990-05-09",
            "docket_number": "140 III 244", "snippet": "kept snippet"}
    twin = {"decision_id": "bge_BGE_140_III_244", "decision_date": "2014-05-19",
            "docket_number": "BGE 140 III 244", "snippet": "twin snippet"}
    out = m._fold_bge_twin_row(kept, twin)
    assert out["decision_id"] == "bge_BGE_140_III_244"
    assert out["decision_date"] == "2014-05-19"
    assert out["docket_number"] == "BGE 140 III 244"
    assert out["snippet"] == "kept snippet"  # the better-ranked row's evidence stays
    assert kept["decision_id"] == "bge_140 III 244"  # input untouched


def test_search_fold_returns_the_kept_row_when_nothing_changes():
    kept = {"decision_id": "bge_BGE_151_III_9", "decision_date": "2024-08-07"}
    twin = {"decision_id": "bge_151 III 9", "decision_date": "2024-08-07"}
    assert m._fold_bge_twin_row(kept, twin) is kept


def test_search_fold_ignores_rows_of_different_decisions():
    kept = {"decision_id": "bge_BGE_131_III_12", "decision_date": "2004-11-01"}
    other = {"decision_id": "bge_131 III 121", "decision_date": "2004-12-01"}
    assert m._fold_bge_twin_row(kept, other) is kept


def test_ia_ruling_served_once_under_the_id_cite_resolves(tmp_path, monkeypatch):
    mod = sys.modules[__name__]
    monkeypatch.setattr(mod, "_DECISIONS", [
        ("bge_116 IA 28", "bge", "116 IA 28", "1990-03-14", "Stimmrecht; Abstimmung", 4),
        ("bge_BGE_116_IA_28", "bge", "BGE 116 IA 28", "1990-01-01", "Stimmrecht", 3),
        ("bge_BGE_116_Ia_28", "bge", "BGE 116 Ia 28", "1990-01-01", "Stimmrecht (Art. 273 OR)", 2),
    ])
    monkeypatch.setattr(mod, "_SHARED", {"116|Ia|28": 2})
    dp = _make_decisions_db(tmp_path / "decisions.db")
    gp = _make_graph(tmp_path / "graph.db")
    monkeypatch.setattr(m, "DB_PATH", dp)
    monkeypatch.setattr(m, "_get_graph_conn", lambda: _row_conn(gp))
    out = m._find_leading_cases(law_code="OR", article="273", limit=10)
    assert [(r["decision_id"], r["decision_date"], r["citation_count"])
            for r in out["results"]] == [("bge_BGE_116_Ia_28", "1990-03-14", 4)]
