"""get_law(as_of=...) work resolution and input validation.

Fedlex tags SR numbers two ways: acts published before ~2020 carry
jolux:historicalLegalId, totally revised acts since then carry only the
taxonomy notation. The old lookup keyed on historicalLegalId alone and took
the first row, so SR 235.1 with any as_of returned the repealed 1992 DSG and
SR 101 resolved to the 1874 constitution (2026-09 statute gap report).

These tests pin the Python-side picker (_pick_fedlex_work), the candidate
query, and the validation in front of the Fedlex call. Offline: the single
SPARQL seam (_fedlex_sparql_select) and requests.get are stubbed.
"""
import sys
from pathlib import Path

import pytest
import requests

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import mcp_server as m  # noqa: E402

DSG_1992 = "https://fedlex.data.admin.ch/eli/cc/1993/1945_1945_1945"
DSG_2020 = "https://fedlex.data.admin.ch/eli/cc/2022/491"
BV_1874 = "https://fedlex.data.admin.ch/eli/cc/1/1_1_1"
BV_1999 = "https://fedlex.data.admin.ch/eli/cc/1999/404"
IN_FORCE = "https://fedlex.data.admin.ch/vocabulary/enforcement-status/0"
REPEALED = "https://fedlex.data.admin.ch/vocabulary/enforcement-status/3"


def W(work, eif=None, nlif=None, status=None):
    return {"work": work, "eif": eif, "nlif": nlif, "status": status}


def _no_cache(monkeypatch):
    monkeypatch.setattr(m, "_lexfind_cache_get", lambda k: None)
    monkeypatch.setattr(m, "_lexfind_cache_set", lambda k, v: None)


# ── the pure picker ───────────────────────────────────────────────────────────

def test_untagged_successor_both_sides_of_changeover():
    """235.1: the 2020 nDSG has no historicalLegalId. With both works as
    candidates the picker must switch exactly on the changeover day."""
    cands = [W(DSG_1992, "1993-07-01", "2023-09-01", "repealed"),
             W(DSG_2020, "2023-09-01", None, "in_force")]
    assert m._pick_fedlex_work(cands, "2020-01-01")["work"] == DSG_1992
    assert m._pick_fedlex_work(cands, "2023-08-31")["work"] == DSG_1992
    assert m._pick_fedlex_work(cands, "2023-09-01")["work"] == DSG_2020
    assert m._pick_fedlex_work(cands, "2026-01-01")["work"] == DSG_2020
    assert m._pick_fedlex_work(cands, "1990-01-01") is None


def test_two_tagged_works_picks_the_one_in_force():
    """101: both constitutions carry historicalLegalId; LIMIT 1 used to pick 1874."""
    cands = [W(BV_1874, "1874-05-29", "2000-01-01", "repealed"),
             W(BV_1999, "2000-01-01", None, "in_force")]
    assert m._pick_fedlex_work(cands, "1990-01-01")["work"] == BV_1874
    assert m._pick_fedlex_work(cands, "2000-01-01")["work"] == BV_1999
    assert m._pick_fedlex_work(cands, "2026-01-01")["work"] == BV_1999


def test_repealed_without_end_date_never_beats_a_live_candidate():
    """979 repealed works carry no dateNoLongerInForce; an unbound end must not
    keep them 'in force' over a datable live act."""
    dead = W("https://fedlex.data.admin.ch/eli/cc/1977/902_902_902", "1977-01-01", None, "repealed")
    live = W("https://fedlex.data.admin.ch/eli/cc/2010/100", "2010-01-01", None, "in_force")
    assert m._pick_fedlex_work([dead, live], "2024-01-01") is live
    # Before the live act existed the repealed one is the only candidate and
    # is returned, flagged, so the caller can render the caveat.
    picked = m._pick_fedlex_work([dead, live], "2000-01-01")
    assert picked is dead and picked["status"] == "repealed" and picked["nlif"] is None


def test_one_day_hole_when_end_date_is_last_day_inclusive():
    """SR 131.233 records nlif as the last day in force (2001-12-31) with the
    successor starting 2002-01-01; neither day may fall into a hole."""
    pred = W("https://fedlex.data.admin.ch/eli/cc/1970/1", "1970-01-01", "2001-12-31", "repealed")
    succ = W("https://fedlex.data.admin.ch/eli/cc/2002/2", "2002-01-01", None, "in_force")
    assert m._pick_fedlex_work([pred, succ], "2001-12-31") is pred
    assert m._pick_fedlex_work([pred, succ], "2002-01-01") is succ


def test_unbound_start_passes_window_but_ranks_after_bound():
    noeif = W("https://fedlex.data.admin.ch/eli/cc/1950/5", None, None, "in_force")
    dated = W("https://fedlex.data.admin.ch/eli/cc/1990/6", "1990-01-01", None, "in_force")
    assert m._pick_fedlex_work([noeif, dated], "2000-01-01") is dated
    assert m._pick_fedlex_work([noeif], "2000-01-01") is noeif


def test_vacant_slot_returns_none():
    assert m._pick_fedlex_work([], "2020-01-01") is None
    assert m._pick_fedlex_work([W(DSG_2020, "2023-09-01")], "2020-01-01") is None
    assert m._pick_fedlex_work([W(DSG_2020, "2023-09-01")], "") is None


# ── candidate query ───────────────────────────────────────────────────────────

def test_candidate_works_unions_taxonomy_and_groups_duplicate_solutions(monkeypatch):
    captured = {}

    def fake(query, timeout=15):
        captured["q"] = query
        return [
            {"work": DSG_1992, "eif": "1993-07-01", "nlif": "2023-09-01", "status": REPEALED},
            {"work": DSG_1992, "eif": "1993-07-01", "nlif": "2023-09-01", "status": REPEALED},
            {"work": DSG_2020, "eif": "2023-09-01T00:00:00", "status": IN_FORCE},
        ]

    monkeypatch.setattr(m, "_fedlex_sparql_select", fake)
    _no_cache(monkeypatch)
    cands = m._fedlex_candidate_works("235.1")
    q = captured["q"]
    assert "SELECT DISTINCT" in q
    assert 'jolux:historicalLegalId "235.1"' in q
    assert "classifiedByTaxonomyEntry" in q and 'FILTER(str(?n) = "235.1")' in q
    assert "LIMIT" not in q and "ORDER BY" not in q
    by = {c["work"]: c for c in cands}
    assert set(by) == {DSG_1992, DSG_2020}
    assert by[DSG_2020] == {"work": DSG_2020, "eif": "2023-09-01", "nlif": None, "status": "in_force"}
    assert by[DSG_1992]["status"] == "repealed" and by[DSG_1992]["nlif"] == "2023-09-01"


def test_candidate_works_rejects_sr_with_query_syntax(monkeypatch):
    called = []
    monkeypatch.setattr(m, "_fedlex_sparql_select", lambda q, timeout=15: called.append(q) or [])
    _no_cache(monkeypatch)
    with pytest.raises(ValueError):
        m._fedlex_candidate_works('220" . ?x ?y ?z #')
    assert called == []


# ── end to end through get_law ────────────────────────────────────────────────

AKN = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"
XML_2020 = f"""<?xml version="1.0"?>
<akomaNtoso xmlns="{AKN}"><act><body>
  <article eId="art_1"><num>Art. 1</num><heading>Zweck</heading>
    <paragraph eId="art_1/para"><content><p>Dieses Gesetz bezweckt den Schutz der Persönlichkeit.</p></content></paragraph>
  </article>
  <article eId="art_5_a"><num>Art. 5a</num><heading>Begriffe</heading>
    <paragraph eId="art_5_a/para"><content><p>In diesem Gesetz bedeuten die folgenden Ausdrücke:</p></content></paragraph>
  </article>
  <proviso eId="disp_u2"><heading>Übergangsbestimmungen</heading>
    <article eId="disp_u2/art_1"><num>Art. 1</num>
      <paragraph eId="disp_u2/art_1/para"><content><p>Laufende Verfahren werden nach altem Recht beurteilt.</p></content></paragraph>
    </article>
  </proviso>
</body></act></akomaNtoso>""".encode()

XML_1992 = f"""<?xml version="1.0"?>
<akomaNtoso xmlns="{AKN}"><act><body>
  <article eId="art_1"><num>Art. 1</num><heading>Zweck</heading>
    <paragraph eId="art_1/para"><content><p>Dieses Gesetz bezweckt den Schutz der Persönlichkeit und der Grundrechte.</p></content></paragraph>
  </article>
</body></act></akomaNtoso>""".encode()

WORKS_235_1 = [
    {"work": DSG_1992, "eif": "1993-07-01", "nlif": "2023-09-01", "status": REPEALED},
    {"work": DSG_2020, "eif": "2023-09-01", "status": IN_FORCE},
]
SNAPS_235_1 = {
    DSG_1992: [{"snapshot": DSG_1992 + "/20190301", "date": "2019-03-01"},
               {"snapshot": DSG_1992 + "/20140101", "date": "2014-01-01"}],
    DSG_2020: [{"snapshot": DSG_2020 + "/20230901", "date": "2023-09-01"}],
}
# Titles live on the WORK expression; the edition expression carries a generic
# "Consolidation: …" label (or nothing), which must never win.
EXPR_235_1 = {
    DSG_1992: [{"title": "Bundesgesetz vom 19. Juni 1992 über den Datenschutz", "abbr": "DSG"}],
    DSG_2020: [{"title": "Bundesgesetz vom 25. September 2020 über den Datenschutz", "abbr": "DSG"}],
    DSG_1992 + "/20190301": [{"fmt": "https://fedlex.data.admin.ch/vocabulary/user-format/xml",
                              "url": "https://fedlex.data.admin.ch/filestore/1992.xml",
                              "title": "Consolidation: 235.1 - 2019-03-01", "abbr": None}],
    # Word only: served neither as XML nor (yet) as structured text.
    DSG_1992 + "/20140101": [{"fmt": "https://fedlex.data.admin.ch/vocabulary/user-format/doc",
                              "url": "https://fedlex.data.admin.ch/filestore/1992-2014.doc",
                              "title": None, "abbr": None}],
    DSG_2020 + "/20230901": [{"fmt": "https://fedlex.data.admin.ch/vocabulary/user-format/xml",
                              "url": "https://fedlex.data.admin.ch/filestore/2020.xml",
                              "title": "Consolidation: 235.1 - 2023-09-01", "abbr": None}],
}


def _stub_fedlex(monkeypatch, works, snaps, expr, xml_by_url, *, record=None):
    def fake(query, timeout=15):
        if record is not None:
            record.append(query)
        if "historicalLegalId" in query:
            return works
        if "isMemberOf" in query:
            for w, rows in snaps.items():
                if f"<{w}>" in query:
                    return rows
            return []
        if "isRealizedBy" in query:
            for uri, rows in expr.items():
                if f"<{uri}>" in query:
                    return rows
            return []
        raise AssertionError("unexpected SPARQL: " + query)

    class _Resp:
        def __init__(self, body):
            self.status_code = 200 if body is not None else 404
            self.content = body or b""

    monkeypatch.setattr(m, "_fedlex_sparql_select", fake)
    monkeypatch.setattr(requests, "get", lambda url, timeout=30: _Resp(xml_by_url.get(url)))
    _no_cache(monkeypatch)


XML_BY_URL = {
    "https://fedlex.data.admin.ch/filestore/1992.xml": XML_1992,
    "https://fedlex.data.admin.ch/filestore/2020.xml": XML_2020,
}


def test_as_of_after_changeover_returns_the_successor_with_provenance(monkeypatch):
    _stub_fedlex(monkeypatch, WORKS_235_1, SNAPS_235_1, EXPR_235_1, XML_BY_URL)
    res = m.get_law(sr_number="235.1", article="1", as_of="2024-01-01")
    assert "error" not in res
    assert res["work_uri"] == DSG_2020
    assert res["snapshot_date"] == "2023-09-01"
    assert res["version"] == "historical" and res["as_of"] == "2024-01-01"
    assert res["title"].startswith("Bundesgesetz vom 25. September 2020")
    assert res["abbreviation"] == "DSG"
    assert res["work_entry_in_force"] == "2023-09-01" and res["work_no_longer_in_force"] is None
    assert res["work_in_force_status"] == "in_force"
    assert res["source_url"] == "https://www.fedlex.admin.ch/eli/cc/2022/491/20230901/de"
    assert res["source_label"] == "Fedlex (Fassung vom 2023-09-01)"
    assert res["verbatim_quotation"] == "verbatim" and res["structure"] == "articles"
    assert res["formats_available"] == ["xml"]
    # Only the main-body Art. 1, not the transitional disp_u2/art_1.
    assert [a["article_num"] for a in res["articles"]] == ["1"]
    assert res["articles"][0]["heading"] == "Zweck"
    assert "footnote" in res["articles"][0]
    assert res["articles"][0]["xml"].lstrip().startswith("<article")


def test_as_of_before_changeover_returns_the_old_act(monkeypatch):
    _stub_fedlex(monkeypatch, WORKS_235_1, SNAPS_235_1, EXPR_235_1, XML_BY_URL)
    res = m.get_law(sr_number="235.1", article="1", as_of="2020-01-01")
    assert res["work_uri"] == DSG_1992
    assert res["snapshot_date"] == "2019-03-01"
    assert res["title"].startswith("Bundesgesetz vom 19. Juni 1992")
    assert res["work_in_force_status"] == "repealed"
    assert res["work_no_longer_in_force"] == "2023-09-01"
    assert res["articles"][0]["text"].endswith("und der Grundrechte.")


def test_full_edition_keeps_transitional_articles_tagged(monkeypatch):
    _stub_fedlex(monkeypatch, WORKS_235_1, SNAPS_235_1, EXPR_235_1, XML_BY_URL)
    res = m.get_law(sr_number="235.1", as_of="2024-01-01")
    sections = {(a["article_num"], a["section"]) for a in res["articles"]}
    assert sections == {("1", ""), ("5a", ""), ("1", "disp_u2")}


def test_prefix_match_is_flagged(monkeypatch):
    _stub_fedlex(monkeypatch, WORKS_235_1, SNAPS_235_1, EXPR_235_1, XML_BY_URL)
    res = m.get_law(sr_number="235.1", article="5", as_of="2024-01-01")
    assert [a["article_num"] for a in res["articles"]] == ["5a"]
    assert res["article_match"] == {"requested": "5", "matched": ["5a"], "method": "prefix"}


def test_missing_article_lists_neighbours(monkeypatch):
    _stub_fedlex(monkeypatch, WORKS_235_1, SNAPS_235_1, EXPR_235_1, XML_BY_URL)
    res = m.get_law(sr_number="235.1", article="99", as_of="2024-01-01")
    assert res["articles"] == []
    assert res["note"].startswith("Art. 99 is not in this edition. Nearest article numbers: 5a, 1")


def test_as_of_swiss_format_is_normalised(monkeypatch):
    _stub_fedlex(monkeypatch, WORKS_235_1, SNAPS_235_1, EXPR_235_1, XML_BY_URL)
    res = m.get_law(sr_number="235.1", article="1", as_of="01.06.2019")
    assert res["as_of"] == "2019-06-01"
    assert res["snapshot_date"] == "2019-03-01"


def test_as_of_today_is_rejected_before_any_network_call(monkeypatch):
    calls = []
    _stub_fedlex(monkeypatch, WORKS_235_1, SNAPS_235_1, EXPR_235_1, XML_BY_URL, record=calls)
    for bad in ("today", "now", "9999", "2024-13-40"):
        res = m.get_law(sr_number="235.1", article="1", as_of=bad)
        assert "Invalid as_of" in res["error"], bad
    assert calls == []


def test_future_as_of_is_rejected(monkeypatch):
    calls = []
    _stub_fedlex(monkeypatch, WORKS_235_1, SNAPS_235_1, EXPR_235_1, XML_BY_URL, record=calls)
    res = m.get_law(sr_number="235.1", article="1", as_of="2999-01-01")
    assert "in the future" in res["error"] and "pending_changes" in res["error"]
    assert calls == []


def test_cantonal_with_as_of_errors_instead_of_returning_current_text(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("cantonal lookup must not run with as_of")

    monkeypatch.setattr(m, "_get_law_cantonal", boom)
    res = m.get_law(canton="ZH", sr_number="170.4", article="1", as_of="1990-01-01")
    assert "federal laws only" in res["error"]
    # A canton-qualified abbreviation flips the jurisdiction too.
    res = m.get_law(abbreviation="ZH/IDG", article="1", as_of="1990-01-01")
    assert "federal laws only" in res["error"]


def test_injected_sr_number_is_rejected_before_any_network_call(monkeypatch):
    calls = []
    _stub_fedlex(monkeypatch, WORKS_235_1, SNAPS_235_1, EXPR_235_1, XML_BY_URL, record=calls)
    res = m.get_law(sr_number='220" . ?x ?y ?z #', article="1", as_of="2020-01-01")
    assert "Invalid sr_number" in res["error"]
    assert calls == []


def test_three_failure_modes_have_distinct_messages_and_no_2021_claim(monkeypatch):
    # (a) no work carried the SR on that date
    _stub_fedlex(monkeypatch, [], {}, {}, {})
    a = m.get_law(sr_number="999.9", article="1", as_of="2020-01-01")["error"]
    assert a.startswith("No Fedlex act carried SR 999.9 on 2020-01-01")
    # (b) the act exists but has no consolidation on or before as_of
    _stub_fedlex(monkeypatch, WORKS_235_1, SNAPS_235_1, EXPR_235_1, XML_BY_URL)
    b = m.get_law(sr_number="235.1", article="1", as_of="1993-07-01")["error"]
    assert "no consolidation of it dated on or before 1993-07-01" in b
    assert "Earliest available edition: 2014-01-01" in b
    # (c) the edition exists but in no format we can serve in this language
    c = m.get_law(sr_number="235.1", article="1", as_of="2014-06-01")["error"]
    assert "edition of SR 235.1 dated 2014-01-01" in c
    assert "formats present: doc" in c
    assert "https://www.fedlex.admin.ch/eli/cc/1993/1945_1945_1945/20140101/de" in c
    for msg in (a, b, c):
        assert "2021" not in msg


def test_download_failure_is_reported_not_swallowed(monkeypatch):
    _stub_fedlex(monkeypatch, WORKS_235_1, SNAPS_235_1, EXPR_235_1, {})
    res = m.get_law(sr_number="235.1", article="1", as_of="2024-01-01")
    assert "download failed" in res["error"] and "HTTP 404" in res["error"]


def test_pending_changes_use_the_live_work(monkeypatch):
    """814.812.36: the repealed 2005 ordinance carries the historicalLegalId,
    the live 2022 one does not. Pending changes must come from the live one."""
    dead = "https://fedlex.data.admin.ch/eli/cc/2005/503"
    live = "https://fedlex.data.admin.ch/eli/cc/2022/866"
    works = [{"work": dead, "eif": "2005-08-01", "nlif": "2023-01-01", "status": REPEALED},
             {"work": live, "eif": "2023-01-01", "status": IN_FORCE}]
    snaps = {dead: [{"snapshot": dead + "/20050801", "date": "2005-08-01"}],
             live: [{"snapshot": live + "/20230101", "date": "2023-01-01"},
                    {"snapshot": live + "/29990101", "date": "2999-01-01"}]}
    _stub_fedlex(monkeypatch, works, snaps, {}, {})
    assert m._fetch_pending_changes("814.812.36") == [{"date": "2999-01-01"}]


def test_empty_fedlex_answers_are_not_cached(monkeypatch):
    """A transient zero-binding SPARQL response must not pin 'No Fedlex act
    carried SR ...' (7 d), an empty snapshot list (24 h) or an empty format
    map (30 d)."""
    keys = []
    monkeypatch.setattr(m, "_fedlex_sparql_select", lambda q, timeout=15: [])
    monkeypatch.setattr(m, "_lexfind_cache_get", lambda k: None)
    monkeypatch.setattr(m, "_lexfind_cache_set", lambda k, v: keys.append(k))
    assert m._fedlex_candidate_works("235.1") == []
    assert m._fedlex_snapshots(DSG_2020) == []
    assert m._fedlex_expression_info(DSG_2020, "de") == {"formats": {}, "title": None, "abbr": None}
    assert keys == []


def test_cache_keys_are_versioned_and_ttls_cover_the_new_prefixes(monkeypatch):
    keys = []
    _stub_fedlex(monkeypatch, WORKS_235_1, SNAPS_235_1, EXPR_235_1, XML_BY_URL)
    monkeypatch.setattr(m, "_lexfind_cache_set", lambda k, v: keys.append(k))
    m.get_law(sr_number="235.1", article="1", as_of="2024-01-01")
    m._fetch_pending_changes("235.1")
    prefixes = {k.split(":")[0] + ":" + k.split(":")[1] for k in keys}
    # v1 -> v2 with the resolution fix, v2 -> v3 with the parser fix (the
    # as_of path imports the statutes parser, so its output changed again).
    # v4 (2026-09-05): repealed stubs carry an empty body + `empty_body`
    # v5 (2026-11-14): PDF excerpts picked by font-verified header
    assert {"hist_law:v5", "pending:v2", "fedlex_works:v1", "fedlex_snaps:v1", "fedlex_expr:v1"} <= prefixes
    assert not any(k.startswith(("hist_law:v1", "hist_law:v2", "hist_law:v3", "hist_law:v4", "pending:v1")) for k in keys)
    assert m._ttl_for_key("hist_law:v5:235.1:1:de:2024-01-01") == 30 * 86400
    assert m._ttl_for_key("pending:v2:235.1") == 86400
    assert m._ttl_for_key("fedlex_works:v1:235.1") == 7 * 86400
    assert m._ttl_for_key("fedlex_snaps:v1:x") == 86400
    assert m._ttl_for_key("fedlex_expr:v1:x") == 30 * 86400


def test_as_of_schema_declares_a_date_pattern_and_federal_only():
    import asyncio
    tools = m._list_tools()
    if asyncio.iscoroutine(tools):
        tools = asyncio.run(tools)
    get_law = next(t for t in tools if t.name == "get_law")
    as_of = get_law.inputSchema["properties"]["as_of"]
    assert as_of["pattern"].startswith("^(")
    assert "Federal laws only" in as_of["description"]
    # The old text claimed XML "available from ~2021": false for 17% of
    # 2000-2020 editions and true for none of the pre-2011 ones.
    assert "available from ~2021" not in as_of["description"]
    assert "edition date" in as_of["description"]
