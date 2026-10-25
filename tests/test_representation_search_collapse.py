"""Search collapse of manifest-linked representations (design doc §5/§7,
phase 5 — GitHub issue #90 part A).

Before this: search_decisions labeled manifest-linked twins
(canonical_decision_id/is_canonical via _attach_canonical_ids) but never
collapsed them, so a Vaud ruling scraped under two portal identifiers
(vd_gerichte's procedure number, vd_findinfo's publication number) came back
as two separate hits (tests/test_representation_links.py and
tests/test_identity_exposure.py pin the label-only behaviour and must keep
passing). This file pins the collapse itself:

- a twin pair present together in a result page folds to ONE hit, with the
  folded member(s) listed under `representations`;
- the canonical's missing regeste is harvested verbatim from a safely-linked
  member, with `regeste_source` naming where it came from (R2: provenance
  travels with every harvested quotation);
- a link that is NOT safe (confidence below the bar, or date_match=0 — the
  design doc's "could be a different ruling of the same procedure" case) is
  never collapsed or harvested from, only labeled;
- when only one representation of a canonical matched the query, it is
  served as-is (not substituted for a non-matching canonical/member);
- none of this fires without the manifest, or disturbs the existing BGE
  dual-ID fold.

Offline: a synthetic FTS5 + decisions.db (pattern copied from
tests/test_search_bge_tuple_pin.py) and a synthetic representation_manifest.db
(schema copied from tests/test_representation_links.py). _rerank_rows runs
for real here (unlike those docket-path fixtures, which stub it out) so the
collapse loop inside it is actually exercised; Haiku/cross-encoder/vector/
graph stages are disabled by construction (no ANTHROPIC_API_KEY, no graph/
vector DBs), not stubbed around.
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

VD_CANON = "vd_gerichte_JJ13.035036"
VD_MEMBER = "vd_findinfo_HC___2015___488"
PLAIN = "bger_1C_1_2020"
BGE_A = "bge_BGE_140_III_1"
BGE_B = "bge_140 III 1"

# (decision_id, court, canton, docket_number, decision_date, regeste, full_text, source_url)
ROWS = [
    # VD canonical: legal_area/chamber data, NO regeste (vd_gerichte: ~0%, design doc §4).
    (VD_CANON, "vd_gerichte", "VD", "JJ13.035036", "2015-05-18", None,
     "mesures protection enfant JJ13.035036 curatelle texte complet", "https://vd1"),
    # VD member: the publication page, carries the official regeste (vd_findinfo: ~99%)
    # and cross-references the procedure number in its rubrum.
    (VD_MEMBER, "vd_findinfo", "VD", "HC/2015/488", "2015-05-18",
     "Regeste officiel HC 2015 488 mesures protection enfant",
     "Rubrum JJ13.035036 objet du present arret HC/2015/488 curatelle", "https://vd2"),
    (PLAIN, "bger", "CH", "1C_1/2020", "2020-01-01", None, "Urteil ordinaire Bauprojekt", "https://bger1"),
    # BGE stored under two ids (unrelated to the manifest in this fixture) —
    # must still fold via the PRE-EXISTING docket-key rule, untouched by this change.
    (BGE_A, "bge", "CH", "BGE 140 III 1", "2014-01-01", None, "Notwehr Putativnotwehr dual id", "https://bgeA"),
    (BGE_B, "bge", "CH", "140 III 1", "2014-01-01", None, "Notwehr Putativnotwehr dual id", "https://bgeB"),
]


def _build(path: Path, rows=None) -> str:
    rows = ROWS if rows is None else rows
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE VIRTUAL TABLE decisions_fts USING fts5("
        "decision_id, court, canton, docket_number, language, title, regeste, full_text);"
        "CREATE TABLE decisions (decision_id TEXT PRIMARY KEY, court TEXT, canton TEXT,"
        " chamber TEXT, docket_number TEXT, decision_date TEXT, language TEXT, title TEXT,"
        " regeste TEXT, full_text TEXT, source_url TEXT, pdf_url TEXT);"
    )
    for i, (did, court, canton, docket, date, regeste, text, url) in enumerate(rows, start=1):
        conn.execute(
            "INSERT INTO decisions_fts (rowid, decision_id, court, canton, docket_number,"
            " language, title, regeste, full_text) VALUES (?,?,?,?,?,'de','',?,?)",
            (i, did, court, canton, docket, regeste or "", text))
        conn.execute(
            "INSERT INTO decisions (rowid, decision_id, court, canton, chamber, docket_number,"
            " decision_date, language, title, regeste, full_text, source_url, pdf_url)"
            " VALUES (?,?,?,?,'',?,?,'de','',?,?,?,NULL)",
            (i, did, court, canton, docket, date, regeste, text, url))
    conn.commit()
    conn.close()
    return str(path)


def _rconn(p: str) -> sqlite3.Connection:
    c = sqlite3.connect(p)
    c.row_factory = sqlite3.Row
    return c


SCHEMA = (
    "CREATE TABLE decision_representations ("
    " canonical_decision_id TEXT NOT NULL, member_decision_id TEXT NOT NULL,"
    " canton TEXT NOT NULL, relation_type TEXT NOT NULL, evidence_method TEXT NOT NULL,"
    " confidence REAL NOT NULL, date_match INTEGER NOT NULL DEFAULT 1,"
    " PRIMARY KEY (canonical_decision_id, member_decision_id), UNIQUE (member_decision_id));"
    "CREATE INDEX idx_repr_member ON decision_representations(member_decision_id);"
)


def _manifest(path: Path, rows) -> str:
    c = sqlite3.connect(path)
    c.executescript(SCHEMA)
    c.executemany(
        "INSERT INTO decision_representations "
        "(canonical_decision_id, member_decision_id, canton, relation_type,"
        " evidence_method, confidence, date_match) VALUES (?,?,?,?,?,?,?)", rows)
    c.commit()
    c.close()
    return str(path)


SAFE_LINK_ROWS = [
    (VD_CANON, VD_CANON, "VD", "judgment", "procedure_cross_reference", 1.0, 1),
    (VD_CANON, VD_MEMBER, "VD", "publication_page", "procedure_cross_reference", 0.9, 1),
]


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    dbp = _build(tmp_path / "d.db")
    monkeypatch.setattr(m, "get_db", lambda: _rconn(dbp))
    monkeypatch.setattr(m, "_get_graph_conn", lambda: None)
    monkeypatch.setattr(m, "ANTHROPIC_API_KEY", "")
    monkeypatch.setattr(
        m, "_analyze_query",
        lambda q, d, **kw: ([{"query": q, "name": "nl_and", "weight": 1.0}], [], {}, "skipped"))
    monkeypatch.setattr(m, "_search_vectors_chunks", lambda *a, **k: {})
    monkeypatch.setattr(m, "_search_statute_graph", lambda *a, **k: [])
    monkeypatch.setattr(m, "_past_deadline", lambda d: False)
    monkeypatch.setattr(m, "_search_vectors", lambda *a, **k: {})
    monkeypatch.setattr(m, "_search_sparse", lambda *a, **k: {})
    m._FTS_TOTAL_CACHE.clear()
    m._SEARCH_RESULT_CACHE.clear()
    yield dbp
    m._FTS_TOTAL_CACHE.clear()
    m._SEARCH_RESULT_CACHE.clear()


@pytest.fixture
def manifest(tmp_path, monkeypatch):
    mp = _manifest(tmp_path / "representation_manifest.db", SAFE_LINK_ROWS)
    monkeypatch.setattr(m, "REPRESENTATION_MANIFEST_DB_PATH", Path(mp))
    monkeypatch.setattr(m, "_manifest_warned", False)
    return mp


def _ids(rows):
    return [r["decision_id"] for r in rows]


def _by_id(rows):
    return {r["decision_id"]: r for r in rows}


# ── end-to-end: search_fts5 collapses the twin pair ─────────────────────────

def test_twin_pair_present_together_collapses_to_one_hit(corpus, manifest):
    rows, total = m.search_fts5(query="JJ13.035036", limit=10)
    ids = _ids(rows)
    assert VD_MEMBER not in ids, ids  # folded away, never a second hit
    assert ids.count(VD_CANON) == 1
    survivor = _by_id(rows)[VD_CANON]
    reps = survivor["representations"]
    assert [r["decision_id"] for r in reps] == [VD_MEMBER]
    assert reps[0]["evidence_method"] == "procedure_cross_reference"
    assert reps[0]["confidence"] == pytest.approx(0.9)
    assert reps[0]["source_url"] == "https://vd2"
    # The canonical itself had no regeste — harvested verbatim from the member.
    assert survivor["regeste"] == "Regeste officiel HC 2015 488 mesures protection enfant"
    assert survivor["regeste_source"] == VD_MEMBER


def test_member_only_hit_is_served_labeled_not_substituted(corpus, manifest):
    # "Rubrum" only occurs in the member's own full_text; the canonical never
    # matches this query, so there is nothing in the page to fold against —
    # the member is served as its own hit, not swapped for the (non-matching)
    # canonical. Design choice (see _collapse_representation_rows docstring):
    # substituting a candidate that did not itself match the query would
    # serve a relevance score and snippet computed against different text
    # under a different decision's identity.
    rows, total = m.search_fts5(query="Rubrum", limit=10)
    ids = _ids(rows)
    assert ids == [VD_MEMBER]


def test_unrelated_decision_untouched(corpus, manifest):
    rows, _ = m.search_fts5(query="Bauprojekt", limit=10)
    assert _ids(rows) == [PLAIN]


def test_bge_dual_id_fold_unaffected_by_manifest_fold(corpus, manifest):
    rows, _ = m.search_fts5(query="Putativnotwehr", limit=10)
    ids = _ids(rows)
    assert ids.count(BGE_A) + ids.count(BGE_B) == 1
    # The surviving row carries no `representations` from the (unrelated)
    # manifest fold — only the BGE twin-fold ran for this pair.
    assert not _by_id(rows).get(BGE_A, _by_id(rows).get(BGE_B)).get("representations")


def test_no_collapse_without_the_manifest(corpus):
    # No `manifest` fixture: both representations come back untouched.
    rows, _ = m.search_fts5(query="JJ13.035036", limit=10)
    ids = _ids(rows)
    assert VD_CANON in ids and VD_MEMBER in ids
    assert "representations" not in _by_id(rows)[VD_CANON]
    assert _by_id(rows)[VD_CANON]["regeste"] is None


# ── confidence / date_match safety gate ─────────────────────────────────────

def test_date_disagreeing_link_is_never_collapsed_or_harvested_from(tmp_path, monkeypatch, corpus):
    # The design doc (§4): a VD procedure number can carry an interim AND a
    # final ruling; a date-disagreeing link might be the SAME decision under
    # two date conventions, or it might be a DIFFERENT ruling of the same
    # procedure. date_match=0 means "not adjudicated safe" either way.
    mp = _manifest(tmp_path / "unsafe.db", [
        (VD_CANON, VD_CANON, "VD", "judgment", "procedure_cross_reference", 1.0, 1),
        (VD_CANON, VD_MEMBER, "VD", "publication_page", "procedure_cross_reference", 0.9, 0),
    ])
    monkeypatch.setattr(m, "REPRESENTATION_MANIFEST_DB_PATH", Path(mp))
    monkeypatch.setattr(m, "_manifest_warned", False)
    rows, _ = m.search_fts5(query="JJ13.035036", limit=10)
    ids = _ids(rows)
    assert VD_CANON in ids and VD_MEMBER in ids  # both still served
    assert "representations" not in _by_id(rows)[VD_CANON]
    assert _by_id(rows)[VD_CANON]["regeste"] is None  # never donated from an unsafe link


def test_low_confidence_link_is_never_collapsed_or_harvested_from(tmp_path, monkeypatch, corpus):
    mp = _manifest(tmp_path / "lowconf.db", [
        (VD_CANON, VD_CANON, "VD", "judgment", "procedure_cross_reference", 1.0, 1),
        (VD_CANON, VD_MEMBER, "VD", "publication_page", "deep_body_citation", 0.5, 1),
    ])
    monkeypatch.setattr(m, "REPRESENTATION_MANIFEST_DB_PATH", Path(mp))
    monkeypatch.setattr(m, "_manifest_warned", False)
    rows, _ = m.search_fts5(query="JJ13.035036", limit=10)
    ids = _ids(rows)
    assert VD_CANON in ids and VD_MEMBER in ids
    assert "representations" not in _by_id(rows)[VD_CANON]


# ── unit tests: the shared primitives ───────────────────────────────────────

def test_safe_representation_links_for_filters_confidence_and_date_match(manifest):
    links = m._safe_representation_links_for([VD_CANON, VD_MEMBER, PLAIN])
    assert set(links) == {VD_MEMBER}  # self-link + unrelated id excluded
    assert links[VD_MEMBER]["canonical_decision_id"] == VD_CANON
    assert links[VD_MEMBER]["evidence_method"] == "procedure_cross_reference"


def test_safe_representation_links_for_absent_manifest_is_none(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "REPRESENTATION_MANIFEST_DB_PATH", tmp_path / "nope.db")
    monkeypatch.setattr(m, "_manifest_warned", False)
    assert m._safe_representation_links_for([VD_MEMBER]) is None


def test_fold_representation_row_harvests_regeste_and_lists_member():
    kept = {"decision_id": VD_CANON, "regeste": None, "source_url": "https://vd1"}
    member = {"decision_id": VD_MEMBER, "regeste": "Official headnote text", "source_url": "https://vd2"}
    link = {"relation_type": "publication_page", "evidence_method": "procedure_cross_reference", "confidence": 0.9}
    out = m._fold_representation_row(kept, member, link)
    assert out["regeste"] == "Official headnote text"
    assert out["regeste_source"] == VD_MEMBER
    assert out["representations"] == [{
        "decision_id": VD_MEMBER, "relation_type": "publication_page",
        "evidence_method": "procedure_cross_reference", "confidence": 0.9,
        "source_url": "https://vd2",
    }]
    # kept/member are untouched (the function never mutates its inputs).
    assert kept["regeste"] is None and "representations" not in kept


def test_fold_representation_row_never_overwrites_an_existing_regeste():
    kept = {"decision_id": VD_CANON, "regeste": "Own headnote", "source_url": "https://vd1"}
    member = {"decision_id": VD_MEMBER, "regeste": "Other text", "source_url": "https://vd2"}
    out = m._fold_representation_row(kept, member, {})
    assert out["regeste"] == "Own headnote"
    assert "regeste_source" not in out


def test_collapse_representation_rows_folds_canonical_and_member(manifest):
    rows = [
        {"decision_id": VD_CANON, "regeste": None, "source_url": "https://vd1"},
        {"decision_id": PLAIN, "regeste": None, "source_url": "https://bger1"},
        {"decision_id": VD_MEMBER, "regeste": "Official headnote", "source_url": "https://vd2"},
    ]
    out = m._collapse_representation_rows(rows)
    assert [r["decision_id"] for r in out] == [VD_CANON, PLAIN]
    assert out[0]["regeste"] == "Official headnote"
    assert out[0]["regeste_source"] == VD_MEMBER
    assert [r["decision_id"] for r in out[0]["representations"]] == [VD_MEMBER]


def test_collapse_representation_rows_noop_without_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "REPRESENTATION_MANIFEST_DB_PATH", tmp_path / "nope.db")
    monkeypatch.setattr(m, "_manifest_warned", False)
    rows = [{"decision_id": VD_CANON}, {"decision_id": VD_MEMBER}]
    out = m._collapse_representation_rows(rows)
    assert out == rows
    assert len(out) == 2


# ── _harvest_missing_regeste: the canonical is the SOLE hit on the page ─────

def test_harvest_missing_regeste_fills_in_from_an_offpage_member(corpus, manifest):
    results = [{"decision_id": VD_CANON, "regeste": None}]
    m._harvest_missing_regeste(results)
    assert results[0]["regeste"] == "Regeste officiel HC 2015 488 mesures protection enfant"
    assert results[0]["regeste_source"] == VD_MEMBER


def test_harvest_missing_regeste_noop_when_already_present(corpus, manifest):
    results = [{"decision_id": VD_CANON, "regeste": "Already has one"}]
    m._harvest_missing_regeste(results)
    assert results[0]["regeste"] == "Already has one"
    assert "regeste_source" not in results[0]


def test_harvest_missing_regeste_noop_without_manifest(corpus, tmp_path, monkeypatch):
    monkeypatch.setattr(m, "REPRESENTATION_MANIFEST_DB_PATH", tmp_path / "nope.db")
    monkeypatch.setattr(m, "_manifest_warned", False)
    results = [{"decision_id": VD_CANON, "regeste": None}]
    m._harvest_missing_regeste(results)
    assert results[0]["regeste"] is None
    assert "regeste_source" not in results[0]


def test_harvest_missing_regeste_respects_date_match_gate(corpus, tmp_path, monkeypatch):
    mp = _manifest(tmp_path / "unsafe.db", [
        (VD_CANON, VD_CANON, "VD", "judgment", "procedure_cross_reference", 1.0, 1),
        (VD_CANON, VD_MEMBER, "VD", "publication_page", "procedure_cross_reference", 0.9, 0),
    ])
    monkeypatch.setattr(m, "REPRESENTATION_MANIFEST_DB_PATH", Path(mp))
    monkeypatch.setattr(m, "_manifest_warned", False)
    results = [{"decision_id": VD_CANON, "regeste": None}]
    m._harvest_missing_regeste(results)
    assert results[0]["regeste"] is None


# ── get_decision / get_regeste: harvest feeds both tools ────────────────────

def test_get_decision_by_id_harvests_regeste_with_provenance(corpus, manifest):
    result = m.get_decision_by_id(VD_CANON)
    assert result["regeste"] == "Regeste officiel HC 2015 488 mesures protection enfant"
    assert result["regeste_source"] == VD_MEMBER
    assert result["canonical_decision_id"] == VD_CANON and result["is_canonical"] is True
    # The member's own record is untouched — it already has its own regeste.
    member = m.get_decision_by_id(VD_MEMBER)
    assert member["regeste"] == "Regeste officiel HC 2015 488 mesures protection enfant"
    assert "regeste_source" not in member


def test_get_regeste_harvests_and_notes_provenance(corpus, manifest, monkeypatch):
    monkeypatch.setattr(m, "_resolve_decision_id", lambda d: d)
    monkeypatch.setattr(m, "_fetch_structure_row", lambda d: None)
    monkeypatch.setattr(m, "_auto_link_citations", lambda text: text)
    monkeypatch.setattr(m, "_build_citation_strings", lambda d: {
        "citation_string_de": "stub-de", "citation_string_fr": "stub-fr",
        "citation_string_it": "stub-it", "canonical_url": "https://x/y"})
    monkeypatch.setattr(m, "_rule_statement", lambda d: None)
    out = m._handle_get_regeste(decision_id=VD_CANON)
    assert out["regeste"] == "Regeste officiel HC 2015 488 mesures protection enfant"
    assert out["regeste_source"] == VD_MEMBER
    assert VD_MEMBER in out["_note"]


def test_get_decision_by_id_noop_without_manifest(corpus, tmp_path, monkeypatch):
    monkeypatch.setattr(m, "REPRESENTATION_MANIFEST_DB_PATH", tmp_path / "nope.db")
    monkeypatch.setattr(m, "_manifest_warned", False)
    result = m.get_decision_by_id(VD_CANON)
    assert result["regeste"] is None
    assert "regeste_source" not in result
    assert "canonical_decision_id" not in result


# ── R2 BLOCKER fix: get_decision_by_id / get_regeste must never clip a ──────
# ── harvested Regeste — only a SEARCH RESULT ROW may (review finding #1) ────

LONG_REGESTE = "Regeste officiel détaillé mesures protection enfant. " * 15
assert len(LONG_REGESTE) > m.MAX_SNIPPET_LEN

LONG_ROWS = [
    (VD_CANON, "vd_gerichte", "VD", "JJ13.035036", "2015-05-18", None,
     "mesures protection enfant JJ13.035036 curatelle texte complet", "https://vd1"),
    (VD_MEMBER, "vd_findinfo", "VD", "HC/2015/488", "2015-05-18",
     LONG_REGESTE,
     "Rubrum JJ13.035036 objet du present arret HC/2015/488 curatelle", "https://vd2"),
]


@pytest.fixture
def long_regeste_db(tmp_path, monkeypatch):
    dbp = _build(tmp_path / "long.db", LONG_ROWS)
    monkeypatch.setattr(m, "get_db", lambda: _rconn(dbp))
    return dbp


def test_harvest_missing_regeste_default_truncates_for_search_rows(long_regeste_db, manifest):
    results = [{"decision_id": VD_CANON, "regeste": None}]
    m._harvest_missing_regeste(results)  # truncate defaults to True
    assert results[0]["regeste"] == m._truncate(LONG_REGESTE, m.MAX_SNIPPET_LEN)
    assert results[0]["regeste"] != LONG_REGESTE
    assert results[0]["regeste_source"] == VD_MEMBER


def test_harvest_missing_regeste_truncate_false_returns_full_text(long_regeste_db, manifest):
    results = [{"decision_id": VD_CANON, "regeste": None}]
    m._harvest_missing_regeste(results, truncate=False)
    assert results[0]["regeste"] == LONG_REGESTE  # full and unchanged
    assert results[0]["regeste_source"] == VD_MEMBER


def test_get_decision_by_id_harvests_full_long_regeste_unchanged(long_regeste_db, manifest):
    result = m.get_decision_by_id(VD_CANON)
    assert result["regeste"] == LONG_REGESTE  # NOT clipped to MAX_SNIPPET_LEN
    assert result["regeste_source"] == VD_MEMBER


def test_get_regeste_handler_returns_full_long_regeste_verbatim(long_regeste_db, manifest, monkeypatch):
    monkeypatch.setattr(m, "_resolve_decision_id", lambda d: d)
    monkeypatch.setattr(m, "_fetch_structure_row", lambda d: None)
    monkeypatch.setattr(m, "_auto_link_citations", lambda text: text)
    monkeypatch.setattr(m, "_build_citation_strings", lambda d: {
        "citation_string_de": "stub-de", "citation_string_fr": "stub-fr",
        "citation_string_it": "stub-it", "canonical_url": "https://x/y"})
    monkeypatch.setattr(m, "_rule_statement", lambda d: None)
    out = m._handle_get_regeste(decision_id=VD_CANON)
    # get_regeste's own _note tells clients to quote `regeste` verbatim (R2) —
    # it must therefore never be the 500-char search-row clip.
    assert out["regeste"] == LONG_REGESTE
    assert out["regeste_source"] == VD_MEMBER


# ── Kill switch: OCL_REPR_COLLAPSE=0 restores pre-#90 behaviour ────────────

def test_kill_switch_off_disables_collapse_and_harvest(corpus, manifest, monkeypatch):
    monkeypatch.setattr(m, "OCL_REPR_COLLAPSE", False)
    rows, _ = m.search_fts5(query="JJ13.035036", limit=10)
    ids = _ids(rows)
    assert VD_CANON in ids and VD_MEMBER in ids  # both come back, not folded
    assert "representations" not in _by_id(rows)[VD_CANON]
    assert _by_id(rows)[VD_CANON]["regeste"] is None  # never harvested either

    # _harvest_missing_regeste is a direct no-op too, independent of search.
    results = [{"decision_id": VD_CANON, "regeste": None}]
    m._harvest_missing_regeste(results)
    assert results[0]["regeste"] is None
    assert "regeste_source" not in results[0]


def test_kill_switch_off_safe_links_return_none(manifest, monkeypatch):
    monkeypatch.setattr(m, "OCL_REPR_COLLAPSE", False)
    assert m._safe_representation_links_for([VD_MEMBER]) is None


# ── Provenance: regeste_source must name the record the text actually ──────
# ── came from, never the row's own abstract_{lang} column (finding #3) ────

def test_fold_representation_row_regeste_source_not_set_when_own_abstract_supplies_text():
    # `kept` HAS its own abstract_de (so _pick_regeste will use that, not the
    # harvested `regeste` field the fold writes) but carries no plain
    # `regeste` of its own — the pre-fix check only looked at
    # `result.get("regeste")` being truthy and would have mislabeled this.
    kept = {"decision_id": VD_CANON, "regeste": None, "abstract_de": "Own abstract text",
            "source_url": "https://vd1"}
    member = {"decision_id": VD_MEMBER, "regeste": "Harvested text", "source_url": "https://vd2"}
    out = m._fold_representation_row(kept, member, {})
    # The fold itself still donates the harvested regeste/regeste_source —
    # the mislabeling only happens downstream, in the helper
    # _regeste_source_if_harvested, which _rerank_rows consults before
    # attaching regeste_source to the rendered result.
    assert out["regeste"] == "Harvested text"
    assert out["regeste_source"] == VD_MEMBER


# _regeste_source_if_harvested is the extracted, directly-testable check
# _rerank_rows runs before attaching `regeste_source` to a result row.

def test_regeste_source_if_harvested_suppressed_by_own_abstract_column():
    row = {"regeste_source": VD_MEMBER, "abstract_de": "Own abstract text"}
    result = {"regeste": "Own abstract text"}  # what _pick_regeste actually shows
    assert m._regeste_source_if_harvested(row, result, "de") is None


def test_regeste_source_if_harvested_returns_source_when_harvested_text_is_shown():
    row = {"regeste_source": VD_MEMBER}  # no abstract_de on this row
    result = {"regeste": "Harvested text"}
    assert m._regeste_source_if_harvested(row, result, "de") == VD_MEMBER


def test_regeste_source_if_harvested_none_without_a_harvest():
    row = {"abstract_de": "Own text"}  # no regeste_source: nothing was harvested
    result = {"regeste": "Own text"}
    assert m._regeste_source_if_harvested(row, result, "de") is None


def test_regeste_source_if_harvested_none_when_nothing_is_shown():
    row = {"regeste_source": VD_MEMBER}
    result = {"regeste": None}
    assert m._regeste_source_if_harvested(row, result, "de") is None


def test_regeste_source_if_harvested_checks_the_language_actually_used():
    # abstract_fr is irrelevant when the row was picked in German — only the
    # SAME language _pick_regeste used may suppress the attribution.
    row = {"regeste_source": VD_MEMBER, "abstract_fr": "French abstract"}
    result = {"regeste": "Harvested text"}
    assert m._regeste_source_if_harvested(row, result, "de") == VD_MEMBER


# ── Survivor signals: max citation_count + OR'd is_leading_case (finding #4) ─

def test_fold_representation_row_keeps_max_citation_count_and_ors_leading_case():
    kept = {"decision_id": VD_CANON, "regeste": "x", "citation_count": 3,
            "source_url": "https://vd1"}
    member = {"decision_id": VD_MEMBER, "regeste": "y", "citation_count": 9,
              "is_leading_case": True, "source_url": "https://vd2"}
    out = m._fold_representation_row(kept, member, {})
    assert out["citation_count"] == 9  # max(3, 9), not the survivor's own 3
    assert out["is_leading_case"] is True
    assert out["decision_id"] == VD_CANON  # which row survives is unchanged


def test_fold_representation_row_keeps_survivors_higher_citation_count():
    kept = {"decision_id": VD_CANON, "regeste": "x", "citation_count": 42}
    member = {"decision_id": VD_MEMBER, "regeste": None, "citation_count": 5}
    out = m._fold_representation_row(kept, member, {})
    assert out["citation_count"] == 42


def test_fold_representation_row_citation_fields_absent_when_both_absent():
    kept = {"decision_id": VD_CANON, "regeste": "x"}
    member = {"decision_id": VD_MEMBER, "regeste": None}
    out = m._fold_representation_row(kept, member, {})
    assert "citation_count" not in out
    assert "is_leading_case" not in out


# ── Fallback to the next eligible member when the best-confidence one has ──
# ── no Regeste of its own; deterministic tie-break (finding #5) ────────────

VD_MEMBER_NOTEXT = "vd_findinfo_notext"

FALLBACK_ROWS = ROWS + [
    (VD_MEMBER_NOTEXT, "vd_findinfo", "VD", "HC/2015/999", "2015-05-18", None,
     "texte sans regeste JJ13.035036", "https://vd3"),
]

FALLBACK_LINK_ROWS = [
    (VD_CANON, VD_CANON, "VD", "judgment", "procedure_cross_reference", 1.0, 1),
    # Highest confidence: no regeste of its own.
    (VD_CANON, VD_MEMBER_NOTEXT, "VD", "publication_page", "procedure_cross_reference", 0.95, 1),
    # Next eligible member: has one — the harvester must fall back to it.
    (VD_CANON, VD_MEMBER, "VD", "publication_page", "procedure_cross_reference", 0.9, 1),
]


@pytest.fixture
def fallback_db(tmp_path, monkeypatch):
    dbp = _build(tmp_path / "fallback.db", FALLBACK_ROWS)
    monkeypatch.setattr(m, "get_db", lambda: _rconn(dbp))
    return dbp


@pytest.fixture
def fallback_manifest(tmp_path, monkeypatch):
    mp = _manifest(tmp_path / "fallback_manifest.db", FALLBACK_LINK_ROWS)
    monkeypatch.setattr(m, "REPRESENTATION_MANIFEST_DB_PATH", Path(mp))
    monkeypatch.setattr(m, "_manifest_warned", False)
    return mp


def test_harvest_missing_regeste_falls_back_past_textless_best_confidence_member(
        fallback_db, fallback_manifest):
    results = [{"decision_id": VD_CANON, "regeste": None}]
    m._harvest_missing_regeste(results)
    assert results[0]["regeste"] == "Regeste officiel HC 2015 488 mesures protection enfant"
    assert results[0]["regeste_source"] == VD_MEMBER  # not VD_MEMBER_NOTEXT


TIE_ROWS = [
    (VD_CANON, "vd_gerichte", "VD", "JJ13.035036", "2015-05-18", None,
     "mesures protection enfant JJ13.035036 curatelle texte complet", "https://vd1"),
    ("vd_findinfo_bbb", "vd_findinfo", "VD", "HC/2015/002", "2015-05-18",
     "Regeste BBB", "texte BBB", "https://vdB"),
    ("vd_findinfo_aaa", "vd_findinfo", "VD", "HC/2015/001", "2015-05-18",
     "Regeste AAA", "texte AAA", "https://vdA"),
]

TIE_LINK_ROWS = [
    (VD_CANON, VD_CANON, "VD", "judgment", "procedure_cross_reference", 1.0, 1),
    # Equal confidence: tie-break must be deterministic (member id, ascending).
    (VD_CANON, "vd_findinfo_bbb", "VD", "publication_page", "procedure_cross_reference", 0.9, 1),
    (VD_CANON, "vd_findinfo_aaa", "VD", "publication_page", "procedure_cross_reference", 0.9, 1),
]


@pytest.fixture
def tie_db(tmp_path, monkeypatch):
    dbp = _build(tmp_path / "tie.db", TIE_ROWS)
    monkeypatch.setattr(m, "get_db", lambda: _rconn(dbp))
    return dbp


@pytest.fixture
def tie_manifest(tmp_path, monkeypatch):
    mp = _manifest(tmp_path / "tie_manifest.db", TIE_LINK_ROWS)
    monkeypatch.setattr(m, "REPRESENTATION_MANIFEST_DB_PATH", Path(mp))
    monkeypatch.setattr(m, "_manifest_warned", False)
    return mp


def test_harvest_missing_regeste_tie_break_is_deterministic_by_member_id(tie_db, tie_manifest):
    results = [{"decision_id": VD_CANON, "regeste": None}]
    m._harvest_missing_regeste(results)
    assert results[0]["regeste_source"] == "vd_findinfo_aaa"  # lexicographically first wins


def test_harvest_missing_regeste_date_match_column_missing_logs_once(tmp_path, monkeypatch, corpus):
    # A manifest built before date_match existed: the query raises
    # OperationalError instead of silently returning no rows. The feature
    # must disable itself with exactly one warning per process, not swallow
    # the error (finding #5).
    c = sqlite3.connect(tmp_path / "old_schema.db")
    c.executescript(
        "CREATE TABLE decision_representations ("
        " canonical_decision_id TEXT NOT NULL, member_decision_id TEXT NOT NULL,"
        " canton TEXT NOT NULL, relation_type TEXT NOT NULL, evidence_method TEXT NOT NULL,"
        " confidence REAL NOT NULL,"
        " PRIMARY KEY (canonical_decision_id, member_decision_id));"
        "CREATE INDEX idx_repr_member ON decision_representations(member_decision_id);"
    )
    c.execute(
        "INSERT INTO decision_representations VALUES (?,?,?,?,?,?)",
        (VD_CANON, VD_MEMBER, "VD", "publication_page", "procedure_cross_reference", 0.9),
    )
    c.commit()
    c.close()
    monkeypatch.setattr(m, "REPRESENTATION_MANIFEST_DB_PATH", tmp_path / "old_schema.db")
    monkeypatch.setattr(m, "_manifest_warned", False)
    monkeypatch.setattr(m, "_harvest_date_match_warned", False)
    calls = []
    monkeypatch.setattr(m.logger, "warning", lambda *a, **k: calls.append((a, k)))
    results = [{"decision_id": VD_CANON, "regeste": None}]
    m._harvest_missing_regeste(results)
    assert results[0]["regeste"] is None  # disabled, not silently working around it
    assert len(calls) == 1
    # Second call in the same process: no second warning.
    m._harvest_missing_regeste(results)
    assert len(calls) == 1


# ── total_count is computed pre-fold (not in scope, documented only) ───────

def test_total_count_comment_marks_it_pre_fold():
    import inspect
    src = inspect.getsource(m._search_fts5_inner)
    assert "pre-fold" in src.lower()
