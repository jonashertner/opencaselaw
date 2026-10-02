"""/entscheid/<written reference> resolves like the `cite` tool, unique only.

Measured live 2026-09-22: /entscheid/73_II_6 and /entscheid/BGE 140 III 115
redirected (the path happened to equal the stored docket_number) while
/entscheid/BGE 73 II 6, /entscheid/140 III 115 and /entscheid/4C.230/2006
returned 404 although cite() resolves all three. The page compared the path
to docket_number byte for byte; early BGE volumes store "73_II_6", later ones
"BGE 140 III 115", and a pre-2007 joined docket lives in the alias table only.

The page now asks mcp_server._resolve_reference_unique: the exact steps of the
cite resolver, accepted under cite's own rule (_cite_identity), every tie
refused. The fixture mirrors the production row shapes read over SSH the same
day, including the BGE tuples stored under two id spellings (15,038 of 35,455
in production), which are one decision and must not read as ambiguous.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import build_fts5  # noqa: E402
import docket_aliases  # noqa: E402
import mcp_server as m  # noqa: E402
import seo_pages  # noqa: E402
from db_schema import SCHEMA_SQL  # noqa: E402


def _row(decision_id, court, canton, docket, date, lang="de"):
    return {
        "decision_id": decision_id, "court": court, "canton": canton,
        "docket_number": docket, "decision_date": date, "language": lang,
        "title": f"Title {docket}", "regeste": None,
        "full_text": f"Erwägungen zu {docket}. " * 8, "source_url": "https://example.invalid/x",
    }


# Row shapes as stored in production (decision_id, court, docket_number, date).
ROWS = [
    _row("bge_73_II_6", "bge", "CH", "73_II_6", "1947-01-01"),
    _row("bge_BGE_140_III_115", "bge", "CH", "BGE 140 III 115", "2014-01-17"),
    # one BGE tuple under both id spellings (the #40 duplication)
    _row("bge_BGE_100_Ia_106", "bge", "CH", "BGE 100 Ia 106", "1974-03-20"),
    _row("bge_100 IA 106", "bge", "CH", "100 IA 106", "1974-03-20"),
    # C-1 guard: a longer page number is a different decision
    _row("bge_BGE_131_III_121", "bge", "CH", "BGE 131 III 121", "2004-11-02"),
    _row("bger_4A_747_2012", "bger", "CH", "4A_747/2012", "2013-04-05"),
    # lead docket of the consolidated 4P.166/2006 + 4C.230/2006
    _row("bger_4P.166_2006", "bger", "CH", "4P.166/2006", "2006-11-09", "fr"),
    _row("bvger_A-4843_2020", "bvger", "CH", "A-4843/2020", "2021-05-04"),
    _row("zh_obergericht_LB190012", "zh_obergericht", "ZH", "LB190012", "2019-06-07"),
    _row("sg_publikationen_K 2015_3, K 2017_3", "sg_verwaltungsgericht", "SG",
         "K 2015/3, K 2017/3", "2020-11-18"),
    # an EVG docket and a cantonal ruling whose whole docket is its tail; the
    # same for a two-digit BGer chamber
    _row("bger_B_59_2001", "bger", "CH", "B_59/2001", "2001-09-27"),
    _row("bl_gerichte_59_2001", "bl_gerichte", "BL", "59/2001", "2001-04-06"),
    _row("bger_12T_3_2013", "bger", "CH", "12T_3/2013", "2013-05-21"),
    _row("vd_findinfo_3_2013", "vd_findinfo", "VD", "3/2013", "2013-02-01"),
    # cantonal dockets the parser used to read as their bare tail
    _row("zh_baurekursgericht_BRGE_I_Nr._0167_2014", "zh_baurekursgericht", "ZH", "BRGE I Nr. 0167/2014", "2014-12-05"),
    _row("vd_findinfo_AI_12_14_-_140_2014", "vd_findinfo", "VD", "AI 12/14 - 140/2014", "2014-09-03", "fr"),
    _row("vd_findinfo_140_2014", "vd_findinfo", "VD", "140/2014", "2014-06-11", "fr"),
    # one docket, two different decisions
    _row("zh_obergericht_AMB1", "zh_obergericht", "ZH", "PS200001", "2020-01-01"),
    _row("zh_bezirksgericht_AMB1", "zh_bezirksgericht", "ZH", "PS200001", "2020-02-01"),
]


@pytest.fixture(scope="module")
def fixture_db(tmp_path_factory):
    """Built once per module (keeps `make test` fast); opened read-only after."""
    dbp = tmp_path_factory.mktemp("entscheid_redirect") / "decisions.db"
    c = sqlite3.connect(str(dbp))
    c.executescript(SCHEMA_SQL)
    for r in ROWS:
        assert build_fts5.insert_decision(c, dict(r)), r["decision_id"]
    c.execute(
        "INSERT INTO decision_docket_aliases VALUES (?,?,?,?,?)",
        ("bger", "4C_230/2006", docket_aliases.normalize_docket_key("4C_230/2006"),
         "bger_4P.166_2006", "test"),
    )
    c.commit()
    stored = {r[0] for r in c.execute("SELECT decision_id FROM decisions")}
    c.close()
    assert stored == {r["decision_id"] for r in ROWS}  # no build-side dedup ate a row
    return dbp


@pytest.fixture
def served(fixture_db, tmp_path, monkeypatch):
    dbp = fixture_db

    def _conn():
        conn = sqlite3.connect(f"file:{dbp}?mode=ro&immutable=1", uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    monkeypatch.setattr(m, "get_db", _conn)
    monkeypatch.setattr(m, "REPRESENTATION_MANIFEST_DB_PATH", tmp_path / "no_manifest.db")
    monkeypatch.setattr(seo_pages, "_get_db", _conn)
    return dbp


def _page(path):
    return seo_pages.render_decision_page(path, resolve_reference=m._resolve_reference_unique)


REDIRECTS = [
    # redirected before the fix — must keep doing so
    ("4A_747/2012", "bger_4A_747_2012"),
    ("73_II_6", "bge_73_II_6"),
    ("BGE 140 III 115", "bge_BGE_140_III_115"),
    ("A-4843/2020", "bvger_A-4843_2020"),
    # 404 before the fix
    ("BGE 73 II 6", "bge_73_II_6"),
    ("ATF 73 II 6", "bge_73_II_6"),
    ("DTF 73 II 6", "bge_73_II_6"),
    ("73 II 6", "bge_73_II_6"),
    ("140 III 115", "bge_BGE_140_III_115"),
    ("ATF 140 III 115", "bge_BGE_140_III_115"),
    ("4A 747/2012", "bger_4A_747_2012"),
    ("4A.747/2012", "bger_4A_747_2012"),
    ("4C.230/2006", "bger_4P.166_2006"),
    ("4C_230/2006", "bger_4P.166_2006"),
    ("4C 230/2006", "bger_4P.166_2006"),
    ("4P.166/2006", "bger_4P.166_2006"),
    ("B_59/2001", "bger_B_59_2001"),
    ("59/2001", "bl_gerichte_59_2001"),
    # the EVG space/dot forms and the two-digit chambers: 404 under the stop-gap
    # guard, now the parser knows the chamber (measured wrong-authority
    # resolutions on production, 2026-09-22)
    ("B 59/2001", "bger_B_59_2001"),
    ("B.59/2001", "bger_B_59_2001"),
    ("EVG B 59/2001", "bger_B_59_2001"),
    ("12T 3/2013", "bger_12T_3_2013"),
    ("12T_3/2013", "bger_12T_3_2013"),
    ("3/2013", "vd_findinfo_3_2013"),
    # a court's own stored docket, written whole
    ("BRGE I Nr. 0167/2014", "zh_baurekursgericht_BRGE_I_Nr._0167_2014"),
    ("AI 12/14 - 140/2014", "vd_findinfo_AI_12_14_-_140_2014"),
    ("140/2014", "vd_findinfo_140_2014"),
    # one decision under two id spellings is not an ambiguity
    ("BGE 100 Ia 106", "bge_BGE_100_Ia_106"),
    ("ATF 100 Ia 106", "bge_BGE_100_Ia_106"),
]


@pytest.mark.parametrize("path,target", REDIRECTS)
def test_reference_redirects_to_canonical_url(served, path, target):
    html, status, location = _page(path)
    assert (status, html) == (301, "")
    assert location == "/entscheid/" + seo_pages.urllib.parse.quote(target, safe="")


@pytest.mark.parametrize("path,target", REDIRECTS)
def test_redirect_agrees_with_cite(served, path, target):
    """Never redirect where cite() would not cite, nor to another decision."""
    cited = m._handle_cite(reference=path)
    assert cited.get("exists") is True
    assert cited["decision_id"] == target


NOT_FOUND = [
    # cite() does not resolve these either (exists=false, measured live
    # 2026-09-22): the ZH chamber suffix and one half of a comma-joined
    # cantonal docket are close matches, not identities. The page follows.
    "LB190012-O",
    "K 2015/3",
    # a docket two different decisions carry
    "PS200001",
    # a chamber the parser does not know: the bare tail '59/2001' is the
    # Basel-Landschaft ruling, never this reference's target
    "X 59/2001",
    "4Z 59/2001",
    # C-1: '131 III 12' is not '131 III 121'
    "BGE 131 III 12",
    "131 III 12",
    # P1.4: fragments and garbage
    "1",
    "747/2012",
    "BGE",
    "bger_9Z_999_2099",
    "x" * 300,
]


@pytest.mark.parametrize("path", NOT_FOUND)
def test_unresolved_or_ambiguous_is_404(served, path):
    html, status, location = _page(path)
    assert status == 404 and location is None
    assert m._resolve_reference_unique(path) is None


def test_stored_id_and_full_docket_still_served(served):
    assert _page("zh_obergericht_LB190012")[1] == 200
    assert _page("LB190012")[1:] == (301, "/entscheid/zh_obergericht_LB190012")
    assert _page("K 2015/3, K 2017/3")[1] == 301


def test_manifest_linked_duplicates_are_one_decision(served, tmp_path, monkeypatch):
    """Two rows the representation manifest links to one canonical redirect to it."""
    man = tmp_path / "manifest.db"
    c = sqlite3.connect(str(man))
    c.execute("CREATE TABLE decision_representations (canonical_decision_id TEXT, "
              "member_decision_id TEXT, relation_type TEXT, evidence_method TEXT, confidence REAL)")
    c.executemany("INSERT INTO decision_representations VALUES (?,?,?,?,?)", [
        ("zh_obergericht_AMB1", "zh_obergericht_AMB1", "self", "t", 1.0),
        ("zh_obergericht_AMB1", "zh_bezirksgericht_AMB1", "duplicate", "t", 1.0),
    ])
    c.commit()
    c.close()
    monkeypatch.setattr(m, "REPRESENTATION_MANIFEST_DB_PATH", man)
    assert _page("PS200001")[1:] == (301, "/entscheid/zh_obergericht_AMB1")


def test_resolver_fault_is_a_404_not_a_500(served):
    def boom(_):
        raise RuntimeError("resolver down")
    assert seo_pages.render_decision_page("BGE 73 II 6", resolve_reference=boom)[1] == 404
    # and without a resolver the page behaves as before
    assert seo_pages.render_decision_page("BGE 73 II 6")[1] == 404


def test_route_handler_passes_the_resolver():
    """handle_decision_page is a closure inside the HTTP entry point, out of a
    TestClient's reach; without this kwarg every test above would still pass
    while the live route went back to byte-for-byte docket matching."""
    import inspect
    src = inspect.getsource(m)
    handler = src[src.index("async def handle_decision_page(request):"):]
    handler = handler[:handler.index("async def handle_sitemap_index")]
    assert "resolve_reference=_resolve_reference_unique" in handler
