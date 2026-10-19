"""The Schaffhausen commentary as users reach it: parsed from the fixture ePub,
ingested with the real scholarship build, citations extracted, then served by
get_commentary / find_scholarship_citing_decision. Offline: fixture DBs only.
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
from scrapers.scholarship.sources import license_usage_hint  # noqa: E402
from search_stack import build_legal_scholarship as bls  # noqa: E402
from search_stack import oge_citation  # noqa: E402
from search_stack.scholarship_citation_extractor import (  # noqa: E402
    extract_all,
    extract_for_publication,
    load_decision_lookups,
    load_sh_dockets,
)

FIXTURE = REPO / "tests" / "fixtures" / "shk_kommentar"


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("shk_avail")
    epub = tmp / "excerpt.epub"
    with zipfile.ZipFile(epub, "w") as z:
        for f in sorted((FIXTURE / "OEBPS").glob("*.html")):
            z.write(f, f"OEBPS/{f.name}")
    records, problems = shk.parse_epub(epub)
    assert problems == []
    shard = tmp / "shk_kommentar.jsonl"
    shard.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records),
                     encoding="utf-8")

    decisions = tmp / "decisions.db"
    d = sqlite3.connect(decisions)
    d.execute("CREATE TABLE decisions (decision_id TEXT, court TEXT, docket_number TEXT, "
              "decision_date TEXT)")
    d.executemany("INSERT INTO decisions VALUES (?,?,?,?)", [
        # the same ruling under both SH representations, the dates as cited
        # ("OGE 60/2016/26 vom 20. September 2016")
        ("sh_gerichte_Nr. 60_2016_26", "sh_gerichte", "Nr. 60/2016/26", "2016-09-20"),
        ("sh_obergericht_60_2016_26", "sh_obergericht", "60/2016/26", "2016-09-20"),
        # held only by the legacy court code ("vom 16. Dezember 2005")
        ("sh_obergericht_60_2005_68", "sh_obergericht", "60/2005/68", "2005-12-16"),
        # a Jan-1 placeholder on one twin; the other carries the cited date
        # ("OGE 60/2015/42 vom 2. September 2016")
        ("sh_gerichte_Nr. 60_2015_42", "sh_gerichte", "Nr. 60/2015/42", "2015-01-01"),
        ("sh_obergericht_60_2015_42", "sh_obergericht", "60/2015/42", "2016-09-02"),
        # another ruling under a cited docket: cited "vom 10. Juni 2016"
        ("sh_gerichte_Nr. 60_2015_14", "sh_gerichte", "Nr. 60/2015/14", "2017-03-03"),
        # a docket of the same shape at another court must not match "OGE"
        ("zh_obergericht_60_2014_15", "zh_obergericht", "60/2014/15", "2015-08-04"),
    ])
    d.commit()
    d.close()
    statutes = tmp / "statutes.db"
    s = sqlite3.connect(statutes)
    s.execute("CREATE TABLE laws (sr_number TEXT, abbr_de TEXT, abbr_fr TEXT, abbr_it TEXT)")
    s.execute("INSERT INTO laws VALUES ('101', 'BV', 'Cst.', 'Cost.')")
    s.commit()
    s.close()

    db = tmp / "legal_scholarship.db"
    conn = sqlite3.connect(db)
    conn.executescript(bls.SCHEMA_SQL)
    bls.ingest_jsonl(conn, shard)
    conn.commit()
    summary = extract_all(conn, str(decisions), str(statutes))
    conn.close()
    return {"db": db, "records": {r["source_record_id"]: r for r in records},
            "summary": summary, "decisions": decisions}


@pytest.fixture
def served(built, monkeypatch):
    def _conn():
        c = sqlite3.connect(f"file:{built['db']}?immutable=1", uri=True)
        c.row_factory = sqlite3.Row
        return c
    monkeypatch.setattr(mcp_server, "_get_scholarship_conn", _conn)
    monkeypatch.setattr(mcp_server, "_representation_info", lambda _id: None)
    return built


# ── shard builder ────────────────────────────────────────────────────────

def test_coauthored_byline_is_two_authors_and_both_are_cited():
    assert shk._split_authors("Alexander Rihs und Andreas Baeckert") == [
        "Alexander Rihs", "Andreas Baeckert"]
    assert shk._split_authors("Beatrice Moll") == ["Beatrice Moll"]
    assert shk._cite_names(["Alexander Rihs", "Andreas Baeckert"]) == "RIHS/BAECKERT"
    assert shk._cite_names(["Cristina Baumgartner-Spahn"]) == "BAUMGARTNER-SPAHN"


def test_records_without_margin_numbers_still_get_an_abstract(built):
    recs = built["records"]
    for rid in ("vrg-art-17", "editorial", "checkliste-rekurs"):
        assert recs[rid]["abstract"], rid
        # verbatim: the abstract is the record's own opening text
        assert recs[rid]["abstract"][:40] in " ".join(recs[rid]["full_text"].split())
    assert recs["vrg-art-17"]["abstract"].startswith("Aufgehoben durch G vom")


# ── citation extraction ──────────────────────────────────────────────────

def test_oge_citations_resolve_to_schaffhausen_decisions(built):
    conn = sqlite3.connect(built["db"])
    rows = conn.execute(
        "SELECT p.pub_id, d.decision_id, d.snippet FROM pub_citations_decisions d "
        "JOIN publications p ON p.id = d.pub_id").fetchall()
    conn.close()
    linked = {(p, dec) for p, dec, _ in rows}
    # both twins carry the cited date: sh_gerichte wins; one link per ruling
    assert ("shk_kommentar:vrg-art-18", "sh_gerichte_Nr. 60_2016_26") in linked
    assert ("shk_kommentar:vrg-art-18", "sh_obergericht_60_2016_26") not in linked
    assert ("shk_kommentar:vrg-art-18", "sh_obergericht_60_2005_68") in linked
    # the twin with the cited date, not the placeholder-dated one
    assert ("shk_kommentar:vrg-art-18", "sh_obergericht_60_2015_42") in linked
    assert ("shk_kommentar:vrg-art-18", "sh_gerichte_Nr. 60_2015_42") not in linked
    # a ruling of another date under the cited docket is not the one cited
    assert not any(dec.endswith("60_2015_14") for _, dec in linked)
    assert not any(dec.startswith("zh_") for _, dec in linked)
    snip = {dec: s for _, dec, s in rows}
    assert "60/2016/26" in snip["sh_gerichte_Nr. 60_2016_26"]


def test_oge_pattern_edges(built):
    lookups = load_decision_lookups(str(built["decisions"]))
    sh = load_sh_dockets(str(built["decisions"]))
    text = ("x" * 100 + " OGE 60/2016/26 E. 2; OGE 60/2016/261 und "
            "OGE vom 14. November 1997 i.S. X; OGE 60/2005/68 vom 16.12.2005; "
            "OGE 60/2015/42 du 2 septembre 2016; OGE 60/2015/14 vom gestern.")
    decisions, _ = extract_for_publication(text, lookups, {}, sh)
    ids = [d for d, _ in decisions]
    # 60/2016/261 is a different docket, not a prefix match of 60/2016/26;
    # no date written -> sh_gerichte; an unreadable date links nothing
    assert ids == ["sh_gerichte_Nr. 60_2016_26", "sh_obergericht_60_2005_68",
                   "sh_obergericht_60_2015_42"]
    # without the SH lookups (an older caller) nothing SH is linked
    assert extract_for_publication(text, lookups, {})[0] == []


@pytest.mark.parametrize("after, want", [
    (" vom 10. Januar 2020 E. 3.1", "2020-01-10"),
    (" vom 2.3.2021 E. 4.7.4", "2021-03-02"),
    (" du 3 juillet 2018", "2018-07-03"),
    (" du 1er mars 2019", "2019-03-01"),
    (", AB 2006, S. 94", None),
    (" E. 2", None),
    (" vom gestern", oge_citation.UNREADABLE),
])
def test_cited_date(after, want):
    assert oge_citation.cited_date(after) == want


# ── get_commentary, cantonal ─────────────────────────────────────────────

@pytest.mark.parametrize("kwargs", [
    {"canton": "SH", "abbreviation": "VRG"},
    {"abbreviation": "SH/VRG"},
    {"abbreviation": "VRG/SH"},
    {"abbreviation": "VRG SH"},
    {"canton": "sh", "sr_number": "172.200"},
    {"sr_number": "SHR 172.200"},
])
def test_cantonal_key_forms(kwargs):
    assert mcp_server._cantonal_commentary_key(
        kwargs.get("abbreviation"), kwargs.get("sr_number"), kwargs.get("canton")
    ) == ("SH", "VRG")


@pytest.mark.parametrize("kwargs", [
    {"abbreviation": "VRG"},                      # no canton: not assumed
    {"sr_number": "172.200"},                     # bare number stays federal
    {"abbreviation": "OR"},
    {"canton": "ZH", "abbreviation": "VRG"},      # no ZH commentary held
    {"canton": "CH", "abbreviation": "VRG"},
])
def test_cantonal_key_misses(kwargs):
    assert mcp_server._cantonal_commentary_key(
        kwargs.get("abbreviation"), kwargs.get("sr_number"), kwargs.get("canton")) is None


def test_get_commentary_serves_the_kommentierung(served):
    res = mcp_server.get_commentary(abbreviation="VRG", article="18", canton="SH")
    assert res["found"] is True
    assert res["pub_id"] == "shk_kommentar:vrg-art-18"
    assert res["authors"] == ["Konrad Waldvogel"]
    assert res["suggested_citation"].startswith(
        "WALDVOGEL, in: Meyer/Herrmann/Bilger (Hrsg.), Kommentar zur Schaffhauser "
        "Verwaltungsrechtspflege, 2021, Art. 18 VRG N.")
    assert res["content_text"] == served["records"]["vrg-art-18"]["full_text"]
    assert res["license"] == "CC-BY-SA"
    assert res["license_usage"]["share_alike_required"] is True
    md = mcp_server._format_get_commentary_response(res)
    assert "OnlineKommentar" not in md
    assert "Kommentar zur Schaffhauser Verwaltungsrechtspflege" in md
    assert "N 1  " in md and "**Attribution:**" in md


def test_get_commentary_article_forms(served):
    for art in ("Art. 18", "18", " 18 "):
        assert mcp_server.get_commentary(abbreviation="SH/VRG", article=art)["found"], art
    jg = mcp_server.get_commentary(canton="SH", abbreviation="JG", article="50")
    assert jg["pub_id"] == "shk_kommentar:jg-art-50"


def test_get_commentary_uncommented_article_says_so(served):
    res = mcp_server.get_commentary(canton="SH", abbreviation="VRG", article="99")
    assert res["found"] is False and res["no_commentary"] is True
    assert "18" in res["commented_articles"]
    assert "get_law(canton='SH', sr_number='172.200', article='99')" in res["note"]
    assert mcp_server._is_miss_payload(res)


def test_get_commentary_lists_articles(served):
    res = mcp_server.get_commentary(canton="SH", abbreviation="VRG")
    nums = [a["article_num"] for a in res["articles"]]
    assert "17" in nums and "18" in nums
    assert nums == sorted(nums, key=mcp_server._article_sort_key)
    md = mcp_server._format_get_commentary_response(res)
    assert md.startswith("# Kommentar zur Schaffhauser Verwaltungsrechtspflege")


def test_federal_lookup_untouched(monkeypatch):
    called = {}
    monkeypatch.setattr(mcp_server, "_get_ok_conn", lambda: called.setdefault("ok", None))
    res = mcp_server.get_commentary(abbreviation="OR", article="41")
    assert "ok" in called                      # went to the OnlineKommentar path
    assert "error" in res


def test_coauthored_citation_from_stored_authors():
    assert mcp_server._cite_surnames("Alexander Rihs; Andreas Baeckert") == "RIHS/BAECKERT"
    # the DB built before the shard fix stores the byline as one string
    assert mcp_server._cite_surnames("Alexander Rihs und Andreas Baeckert") == "RIHS/BAECKERT"


# ── decision → scholarship, across representations ───────────────────────

def test_citing_decision_found_from_either_representation(served, monkeypatch):
    rep = {"canonical_decision_id": "sh_gerichte_Nr. 60_2016_26", "is_canonical": False,
           "members": []}
    monkeypatch.setattr(mcp_server, "_representation_info",
                        lambda _id: rep if _id == "sh_obergericht_60_2016_26" else None)
    via_twin = mcp_server.find_scholarship_citing_decision("sh_obergericht_60_2016_26")
    direct = mcp_server.find_scholarship_citing_decision("sh_gerichte_Nr. 60_2016_26")
    assert [r["pub_id"] for r in via_twin["results"]] == ["shk_kommentar:vrg-art-18"]
    assert [r["pub_id"] for r in direct["results"]] == ["shk_kommentar:vrg-art-18"]


# ── licence ──────────────────────────────────────────────────────────────

def test_unversioned_cc_by_sa_is_share_alike_not_all_rights_reserved():
    h = license_usage_hint("CC-BY-SA")
    assert h["share_alike_required"] is True
    assert h["may_redistribute"] is True
    assert "not a recognized" not in h["note"]
    assert "version not stated" in h["note"]


# ── get_law on the commented acts ────────────────────────────────────────

@pytest.fixture
def lexfind_stub(monkeypatch):
    asked = {}

    def _leg(systematic_number, canton, language):
        asked["number"] = systematic_number
        return {"systematic_number": systematic_number, "canton": canton,
                "title": "Gesetz über den Rechtsschutz in Verwaltungssachen",
                "lexfind_id": 14351, "source": "lexfind",
                "current_version": {"category": "Gesetz"},
                "articles": [{"article_num": "18", "heading": "Rekursfrist",
                              "text": "…"}]}
    monkeypatch.setattr(mcp_server, "_get_legislation", _leg)
    monkeypatch.setattr(mcp_server, "_get_cantonal_conn", lambda: None)
    monkeypatch.setattr(mcp_server, "_cantonal_candidates", lambda *a, **k: [])
    return asked


def test_get_law_reaches_sh_vrg_by_its_abbreviation(served, lexfind_stub):
    res = mcp_server.get_law(canton="SH", abbreviation="VRG", article="18")
    assert lexfind_stub["number"] == "172.200"
    assert res["abbreviation"] == "VRG"
    cm = res["commentary"]
    assert cm["pub_id"] == "shk_kommentar:vrg-art-18"
    assert cm["get"] == "get_commentary(canton='SH', abbreviation='VRG', article='18')"
    text = mcp_server._format_get_law_response(res)
    assert "Commentary: Art. 18 VRG" in text and "WALDVOGEL, in:" in text


def test_get_law_whole_act_names_the_commentary(served, lexfind_stub):
    res = mcp_server.get_law(canton="SH", sr_number="172.200")
    assert res["commentary"]["article_count"] >= 2
    assert "article" not in res["commentary"]["get"]


def test_get_law_uncommented_article_has_no_pointer(served, lexfind_stub):
    res = mcp_server.get_law(canton="SH", sr_number="172.200", article="99")
    assert "commentary" not in res


def test_get_law_other_canton_untouched(served, lexfind_stub):
    res = mcp_server.get_law(canton="ZH", sr_number="172.200", article="18")
    assert "commentary" not in res


# ── cite("OGE 60/2017/43") ───────────────────────────────────────────────

def _decisions_conn():
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE decisions (decision_id TEXT, court TEXT, "
              "docket_number TEXT, decision_date TEXT)")
    c.executemany("INSERT INTO decisions VALUES (?,?,?,?)", [
        ("sh_gerichte_Nr. 60_2017_43", "sh_gerichte", "Nr. 60/2017/43", "2020-01-10"),
        ("sh_obergericht_60_2017_43", "sh_obergericht", "60/2017/43", "2011-12-16"),
        ("sh_gerichte_Nr. 60_2019_5", "sh_gerichte", "Nr. 60/2019/5", "2019-01-01"),
        ("sh_obergericht_60_2019_5", "sh_obergericht", "60/2019/5", "2019-09-24"),
        ("sh_obergericht_60_2005_68", "sh_obergericht", "60/2005/68", "2006-01-01"),
        ("zh_obergericht_60_2014_15", "zh_obergericht", "60/2014/15", "2015-01-01"),
    ])
    return c


@pytest.mark.parametrize("ref, want", [
    ("OGE 60/2017/43", "sh_gerichte_Nr. 60_2017_43"),
    ("OGE 60/2017/43 vom 10. Januar 2020 E. 3.1", "sh_gerichte_Nr. 60_2017_43"),
    # a date no held ruling carries: another ruling under the docket
    ("OGE 60/2017/43 vom 19. Dezember 2017", None),
    # the twin that carries the cited date
    ("OGE 60/2019/5 vom 24. September 2019", "sh_obergericht_60_2019_5"),
    ("OGE 60/2019/5", "sh_gerichte_Nr. 60_2019_5"),
    ("OGE 60/2005/68", "sh_obergericht_60_2005_68"),
    ("OGE 60/2014/15", None),            # the ZH docket of the same shape
    ("OGE 60/2017/431", None),
    ("60/2017/43", None),                # not OGE form: left to the general resolver
])
def test_lookup_oge(ref, want):
    assert mcp_server._lookup_oge(_decisions_conn(), ref) == want


def test_cite_identity_accepts_oge_for_the_sh_ruling_only():
    sh = {"decision_id": "sh_gerichte_Nr. 60_2017_43", "court": "sh_gerichte",
          "docket_number": "Nr. 60/2017/43", "decision_date": "2020-01-10"}
    ident = mcp_server._cite_identity("OGE 60/2017/43", sh)
    assert ident == {"method": "exact_docket", "label": "Nr. 60/2017/43"}
    other = dict(sh, docket_number="Nr. 60/2017/44")
    assert mcp_server._cite_identity("OGE 60/2017/43", other) is None
    assert mcp_server._cite_identity("OGE 60/2017/43 vom 10. Januar 2020", sh)
    assert mcp_server._cite_identity("OGE 60/2017/43 vom 19. Dezember 2017", sh) is None


# ── get_doctrine("Art. 18 VRG SH") ───────────────────────────────────────

@pytest.mark.parametrize("query", ["Art. 18 VRG SH", "Art. 18 VRG Schaffhausen"])
def test_get_doctrine_carries_the_cantonal_commentary(served, monkeypatch, query):
    monkeypatch.setattr(mcp_server, "_get_ok_conn", lambda: None)
    monkeypatch.setattr(mcp_server, "_fetch_statute_text", lambda **k: {})
    monkeypatch.setattr(mcp_server, "_find_leading_cases",
                        lambda **k: {"results": [], "error": "offline"})
    monkeypatch.setattr(mcp_server, "_find_leading_cases_by_statute_fallback",
                        lambda **k: [])
    monkeypatch.setattr(mcp_server, "_get_materialien_for_doctrine", lambda *a: None)
    res = mcp_server._handle_get_doctrine(query=query)
    cm = res["commentary"]
    assert cm["pub_id"] == "shk_kommentar:vrg-art-18"
    assert cm["excerpt"] == served["records"]["vrg-art-18"]["full_text"][:800]
    assert cm["statute"] == "get_law(canton='SH', sr_number='172.200', article='18')"


def test_get_doctrine_without_canton_does_not_guess(served, monkeypatch):
    monkeypatch.setattr(mcp_server, "_get_ok_conn", lambda: None)
    monkeypatch.setattr(mcp_server, "_fetch_statute_text", lambda **k: {})
    monkeypatch.setattr(mcp_server, "_find_leading_cases",
                        lambda **k: {"results": [], "error": "offline"})
    monkeypatch.setattr(mcp_server, "_find_leading_cases_by_statute_fallback",
                        lambda **k: [])
    monkeypatch.setattr(mcp_server, "_get_materialien_for_doctrine", lambda *a: None)
    assert mcp_server._handle_get_doctrine(query="Art. 18 VRG")["commentary"] is None


def test_get_scholarship_lists_the_decisions_it_cites(served):
    res = mcp_server.get_scholarship("shk_kommentar:vrg-art-18")
    assert "sh_gerichte_Nr. 60_2016_26" in res["cites_decisions"]
    md = mcp_server._format_get_scholarship_response(res)
    assert "## Cites decisions held in the corpus" in md
    assert "sh_gerichte_Nr. 60_2016_26" in md


# ── double-check fixes ───────────────────────────────────────────────────

def test_oge_reference_never_cites_another_court():
    zh = {"decision_id": "zh_obergericht_60_2014_15", "court": "zh_obergericht",
          "docket_number": "60/2014/15", "decision_date": "2015-01-01"}
    assert mcp_server._cite_identity("OGE 60/2014/15", zh) is None


def test_canton_without_commentary_is_a_cantonal_miss(monkeypatch):
    monkeypatch.setattr(mcp_server, "_get_ok_conn",
                        lambda: pytest.fail("fell through to the federal path"))
    for kw in ({"canton": "SH", "abbreviation": "StG"}, {"abbreviation": "SH/StG"},
               {"canton": "ZH", "abbreviation": "VRG", "article": "6"}):
        res = mcp_server.get_commentary(**kw)
        assert res["found"] is False and res["no_commentary"] is True, kw
        assert "VRG SH" in res["note"]
        assert mcp_server._format_get_commentary_response(res).startswith("# No open-access")


@pytest.mark.parametrize("art", ["18 Abs. 1", "Art. 18 Abs. 2 lit. a", "18"])
def test_article_qualifiers_are_ignored(served, art):
    res = mcp_server.get_commentary(canton="SH", abbreviation="VRG", article=art)
    assert res["pub_id"] == "shk_kommentar:vrg-art-18"


def test_get_doctrine_cantonal_query_drops_other_cantons_cases(served, monkeypatch):
    monkeypatch.setattr(mcp_server, "_get_ok_conn", lambda: None)
    monkeypatch.setattr(mcp_server, "_fetch_statute_text", lambda **k: {})
    monkeypatch.setattr(mcp_server, "_count_citations", lambda _id: (0, 0))
    monkeypatch.setattr(mcp_server, "_find_leading_cases", lambda **k: {"results": [
        {"decision_id": "zh_verwaltungsgericht__AN.2011.00002", "docket_number": "AN.2011.00002",
         "decision_date": "2011-12-06", "regeste": "ZH", "citation_count": 5},
        {"decision_id": "bger_1C_346_2009", "docket_number": "1C_346/2009",
         "decision_date": "2009-11-06", "regeste": "BGer", "citation_count": 49},
        {"decision_id": "sh_gerichte_Nr. 60_2016_26", "docket_number": "Nr. 60/2016/26",
         "decision_date": "2017-01-01", "regeste": "SH", "citation_count": 1},
    ]})
    monkeypatch.setattr(mcp_server, "_get_materialien_for_doctrine", lambda *a: None)
    res = mcp_server._handle_get_doctrine(query="Art. 18 VRG SH")
    assert [c["decision_id"] for c in res["leading_cases"]] == ["sh_gerichte_Nr. 60_2016_26"]
    assert [t["bge_ref"] for t in res["doctrine_timeline"]] == ["Nr. 60/2016/26"]
    assert "limited to SH rulings" in res["leading_cases_note"]
    # a federal query is untouched
    fed = mcp_server._handle_get_doctrine(query="Art. 18 VRG")
    assert len(fed["leading_cases"]) == 3 and "leading_cases_note" not in fed


def test_oge_snippet_shows_the_matching_occurrence(built):
    sh = load_sh_dockets(str(built["decisions"]))
    text = ("x" * 100 + " vgl. OGE 60/2015/14 vom 1. Januar 2014 E. 1; später "
            "OGE 60/2015/14 vom 3. März 2017, AB 2017. " + "y" * 100)
    (did, snip), = extract_for_publication(text, {}, {}, sh)[0]
    assert did == "sh_gerichte_Nr. 60_2015_14"
    assert "3. März 2017" in snip
