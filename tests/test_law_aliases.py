"""Names for federal acts that used to answer "No law found".

Eight days of capture logs (2026-09-01..09) put 9.5 % of the federal-shaped
/api/laws/{abbreviation} calls on names the exact abbr_de/abbr_fr/abbr_it
match cannot see: 'Cst' for the stored 'Cst.', 'OPP2' for 'OPP 2', the
repealed 'OG', treaties without a Fedlex titleShort (IPBPR, KRK, CISG), an
'a'-prefixed former edition (aStGB), a canton and number in the wrong
fields, a cantonal act without its canton. The shapes below are the real
ones, each seen in those logs; the SR numbers were verified against Fedlex
or the mirror-derived docs/data/law_codes.json before they went into
docs/api/law_aliases.json.

Nothing is guessed: a name that resolves to nothing is still a miss, now
with the cantonal acts that carry it as candidates.
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
for p in (REPO, REPO / "tests"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import mcp_server as m  # noqa: E402
from _statutes_fixture import make_statutes_conn  # noqa: E402

# The mirror as Fedlex publishes it: abbreviations with their dots and
# spaces, treaties without any abbreviation at all.
LAWS = [
    {"sr_number": "101", "title_de": "Bundesverfassung", "title_fr": "Constitution fédérale",
     "abbr_de": "BV", "abbr_fr": "Cst.", "abbr_it": "Cost."},
    {"sr_number": "173.110", "title_de": "Bundesgerichtsgesetz", "abbr_de": "BGG", "abbr_fr": "LTF", "abbr_it": "LTF"},
    {"sr_number": "210", "title_de": "Zivilgesetzbuch", "abbr_de": "ZGB", "abbr_fr": "CC", "abbr_it": "CC"},
    {"sr_number": "220", "title_de": "Obligationenrecht", "abbr_de": "OR", "abbr_fr": "CO", "abbr_it": "CO"},
    {"sr_number": "311.0", "title_de": "Strafgesetzbuch", "abbr_de": "StGB", "abbr_fr": "CP", "abbr_it": "CP"},
    {"sr_number": "312.0", "title_de": "Strafprozessordnung", "abbr_de": "StPO", "abbr_fr": "CPP", "abbr_it": "CPP"},
    {"sr_number": "831.441.1", "title_de": "Verordnung über die berufliche Vorsorge (BVV 2)",
     "abbr_de": "BVV 2", "abbr_fr": "OPP 2", "abbr_it": "OPP 2"},
    {"sr_number": "822.113", "title_de": "Verordnung 3 zum Arbeitsgesetz", "abbr_de": "ArGV 3", "abbr_fr": "OLT 3", "abbr_it": "OLL 3"},
    {"sr_number": "832.10", "title_de": "Krankenversicherungsgesetz", "abbr_de": "KVG", "abbr_fr": "LAMal", "abbr_it": "LAMal"},
    {"sr_number": "142.20", "title_de": "Ausländer- und Integrationsgesetz", "abbr_de": "AIG", "abbr_fr": "LEI", "abbr_it": "LStrI"},
    {"sr_number": "235.1", "title_de": "Datenschutzgesetz", "abbr_de": "DSG", "abbr_fr": "LPD", "abbr_it": "LPD"},
    {"sr_number": "0.101", "title_de": "EMRK", "abbr_de": "EMRK", "abbr_fr": "CEDH", "abbr_it": "CEDU"},
    {"sr_number": "0.275.12", "title_de": "Lugano-Übereinkommen", "abbr_de": "LugÜ", "abbr_fr": "CL", "abbr_it": "CLug"},
    {"sr_number": "0.142.112.681", "title_de": "Freizügigkeitsabkommen", "abbr_de": "FZA", "abbr_fr": "ALCP", "abbr_it": "ALC"},
    {"sr_number": "0.103.2", "title_de": "Internationaler Pakt über bürgerliche und politische Rechte"},
    {"sr_number": "0.107", "title_de": "Übereinkommen über die Rechte des Kindes"},
    {"sr_number": "0.221.211.1", "title_de": "Wiener Kaufrecht"},
    {"sr_number": "0.211.230.02", "title_de": "Haager Kindesentführungsübereinkommen"},
    {"sr_number": "641.71", "title_de": "CO2-Gesetz"},
]
ROWS = [{"sr_number": law["sr_number"], "article_num": "1",
         "text": f"Art. 1 of SR {law['sr_number']}."} for law in LAWS]
ROWS += [
    {"sr_number": "220", "article_num": "41", "text": "Wer einem andern widerrechtlich Schaden zufügt ..."},
    {"sr_number": "210", "article_num": "314abis", "text": "Fürsorgerische Unterbringung ..."},
    {"sr_number": "311.0", "article_num": "146", "text": "Betrug ..."},
]


def _conn():
    con = make_statutes_conn(ROWS, LAWS)
    con.row_factory = sqlite3.Row            # as _get_statutes_conn opens it
    m._install_unicode_casing(con)
    return con


@pytest.fixture(autouse=True)
def statutes(monkeypatch):
    # get_law closes its connection, and a closed :memory: database is gone,
    # so every call gets a fresh one.
    monkeypatch.setattr(m, "_get_statutes_conn", _conn)
    monkeypatch.setattr(m, "_get_cantonal_conn", lambda: None)
    monkeypatch.setattr(m, "_law_alias_cache", None)
    yield
    monkeypatch.setattr(m, "_law_alias_cache", None)


# ---------------------------------------------------------------------------
# The top real miss shapes, as callers wrote them.
# ---------------------------------------------------------------------------

MISS_SHAPES = [
    # stored 'Cst.' / 'Cost.' — the dot
    ("Cst", "101"), ("CST", "101"), ("cst", "101"), ("Cst.", "101"), ("Cost", "101"),
    # stored 'OPP 2' / 'BVV 2' / 'ArGV 3' — the space
    ("OPP2", "831.441.1"), ("OPP 2", "831.441.1"), ("opp 2", "831.441.1"),
    ("O.P.P. 2", "831.441.1"), ("BVV2", "831.441.1"), ("ArGV3", "822.113"),
    # repealed under the same SR number
    ("OG", "173.110"), ("OJ", "173.110"), ("AOG", "173.110"),
    ("BStP", "312.0"), ("PPF", "312.0"),
    ("AuG", "142.20"), ("AUG", "142.20"), ("LEtr", "142.20"), ("ANAG", "142.20"),
    # current-edition names
    ("nDSG", "235.1"), ("nStPO", "312.0"),
    # treaties without a Fedlex abbreviation
    ("IPBPR", "0.103.2"), ("Pacte II", "0.103.2"), ("UNO-Pakt II", "0.103.2"),
    ("KRK", "0.107"), ("CDE", "0.107"), ("CRC", "0.107"),
    ("CISG", "0.221.211.1"), ("CVIM", "0.221.211.1"),
    ("HKÜ", "0.211.230.02"), ("CLaH80", "0.211.230.02"),
    # treaties the mirror abbreviates, under other names
    ("ECHR", "0.101"), ("CEDH", "0.101"), ("EMRK", "0.101"),
    ("Convention de Lugano", "0.275.12"), ("LugUe", "0.275.12"), ("LugÜ", "0.275.12"),
    ("ALCP", "0.142.112.681"),
    # former editions written with an 'a'
    ("aStGB", "311.0"), ("ASTGB", "311.0"), ("aZGB", "210"), ("aBV", "101"),
    # other-language names of the codes
    ("CPS", "311.0"), ("CCS", "210"), ("LAMal", "832.10"), ("LAMAL", "832.10"),
    # a number where a name goes
    ("SR 220", "220"), ("sr 220", "220"), ("311.0", "311.0"),
    ("CO2", "641.71"),
]


@pytest.mark.parametrize("name,sr", MISS_SHAPES, ids=[s[0] for s in MISS_SHAPES])
def test_real_miss_shape_resolves(name, sr):
    r = m.get_law(abbreviation=name)
    assert not r.get("error"), (name, r)
    assert r["sr_number"] == sr
    assert r.get("articles") or r.get("article_count") is not None


def test_exact_abbreviations_unchanged():
    for name, sr in (("OR", "220"), ("ZGB", "210"), ("StGB", "311.0"), ("BGG", "173.110")):
        r = m.get_law(abbreviation=name)
        assert r["sr_number"] == sr and "abbreviation_alias" not in r


def test_unknown_name_is_still_a_miss():
    for name in ("NOTALAW", "Erlass", "ALG", "XQ"):
        r = m.get_law(abbreviation=name)
        assert r.get("error", "").startswith("No law found with abbreviation"), (name, r)
        assert m._payload_outcome(r) == ("empty", "id_not_found")


def test_prefix_never_invents_an_act():
    # 'A' + something that resolves to nothing stays nothing; 'AT', 'AI' are
    # too short to be an edition prefix on anything.
    for name in ("AXYZ", "AT", "AI", "NR"):
        assert m.get_law(abbreviation=name).get("error"), name


# ---------------------------------------------------------------------------
# What the reader is told.
# ---------------------------------------------------------------------------

def test_former_act_serves_successor_with_note():
    r = m.get_law(abbreviation="OG", article="1")
    assert r["sr_number"] == "173.110" and r["abbreviation"] == "BGG"
    alias = r["abbreviation_alias"]
    assert alias["kind"] == "former" and alias["requested"] == "OG"
    assert "2007-01-01" in alias["note"]
    assert "BGG" in alias["note"] and "as_of='2006-12-31'" in alias["note"]
    text = m._format_get_law_response(r)
    assert "Note: OG/OJ is the Bundesrechtspflegegesetz" in text


def test_former_edition_prefix_note():
    r = m.get_law(abbreviation="aStGB", article="146")
    assert r["sr_number"] == "311.0"
    assert r["articles"][0]["article_num"] == "146"
    note = r["abbreviation_alias"]["note"]
    assert "former edition of StGB" in note and "as_of" in note


def test_punctuation_form_carries_no_note():
    r = m.get_law(abbreviation="Cst", article="1")
    assert r["abbreviation_alias"] == {"kind": "form", "requested": "Cst", "resolved": "Cst."}
    assert "Note:" not in m._format_get_law_response(r).split("\n\n")[0]


def test_as_of_uses_the_same_resolution(monkeypatch):
    seen = {}

    def fake_hist(sr, article, language, as_of):
        seen.update(sr=sr, article=article, as_of=as_of)
        return {"sr_number": sr, "version": "historical", "articles": []}

    monkeypatch.setattr(m, "_fetch_historical_law_version", fake_hist)
    r = m.get_law(abbreviation="OG", article="Art. 97", as_of="2005-01-01")
    assert seen == {"sr": "173.110", "article": "97", "as_of": "2005-01-01"}
    # With as_of the OG edition itself is served: no "current text" warning.
    assert "text served" not in (r["abbreviation_alias"].get("note") or "")


# ---------------------------------------------------------------------------
# Article and language forms that raised or missed.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("article", ["Art. 41", "art.41", "Art 41", " 41 "])
def test_article_prefix_stripped(article):
    r = m.get_law(abbreviation="OR", article=article)
    assert [a["article_num"] for a in r["articles"]] == ["41"]


def test_article_sup_tag_and_space():
    assert m.get_law(sr_number="210", article="314a<sup>bis</sup>")["articles"][0]["article_num"] == "314abis"
    assert m.get_law(sr_number="210", article="314a bis")["articles"][0]["article_num"] == "314abis"


def test_unsupported_language_falls_back_instead_of_500():
    # 2026-09-05: /api/laws/EMRK?language=en raised IndexError on the title lookup.
    r = m.get_law(abbreviation="EMRK", language="en")
    assert not r.get("error") and r["language"] == "de"
    assert r["language_fallback"] == {"requested": "en", "served": "de"}
    assert m.get_law(abbreviation="OR", language="DE")["language"] == "de"
    assert "language 'en' is not served" in m._format_get_law_response(r)


# ---------------------------------------------------------------------------
# Cantonal names: both orders, swapped fields, and candidates for a bare name.
# ---------------------------------------------------------------------------

def test_split_qualified_name_both_orders():
    assert m.split_qualified_law_name("ZH/StG") == ("ZH", "StG")
    assert m.split_qualified_law_name("StG/ZH") == ("ZH", "StG")
    assert m.split_qualified_law_name("GOG/GL") == ("GL", "GOG")
    assert m.split_qualified_law_name("LT/TI") == ("TI", "LT")        # TI's LT, not canton 'LT'
    assert m.split_qualified_law_name("LTF/BGG") == (None, "LTF/BGG")  # no canton in it
    assert m.split_qualified_law_name("EG SchKG") == (None, "EG SchKG")


def test_canton_after_the_name_routes_to_the_canton(monkeypatch):
    seen = []
    monkeypatch.setattr(m, "_get_law_cantonal",
                        lambda sr, abbr, art, lang, canton: seen.append((sr, abbr, art, canton)) or {"ok": 1})
    m.get_law(abbreviation="GOG/GL", article="16")
    m.get_law(abbreviation="GL/GOG", article="16")
    assert seen == [(None, "GOG", "16", "GL"), (None, "GOG", "16", "GL")]


def test_swapped_canton_and_number_fields(monkeypatch):
    seen = []
    monkeypatch.setattr(m, "_get_law_cantonal",
                        lambda sr, abbr, art, lang, canton: seen.append((sr, abbr, art, canton)) or {"sr_number": sr})
    r = m.get_law(abbreviation="ZH", article="211.1")
    assert seen == [("211.1", None, None, "ZH")]
    assert "read as canton='ZH', sr_number='211.1'" in r["argument_note"]
    # Zurich's collection label in place of the canton code.
    r = m.get_law(abbreviation="LS", article="211.1")
    assert seen[-1] == ("211.1", None, None, "ZH")


def _cantonal_mirror():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript("""
        CREATE TABLE laws (canton TEXT, sr_number TEXT, language TEXT, title TEXT);
        CREATE TABLE law_names (canton TEXT, language TEXT, name_folded TEXT,
                                name_type TEXT, source TEXT, sr_number TEXT);
        INSERT INTO laws VALUES ('ZH', '211.1', 'de', 'Gesetz über die Gerichts- und Behördenorganisation im Zivil- und Strafprozess (GOG)');
        INSERT INTO laws VALUES ('GL', 'III B/1/1', 'de', 'Gerichtsorganisationsgesetz (GOG)');
        INSERT INTO law_names VALUES ('ZH', 'de', 'gog', 'abbreviation', 'lexwork_api', '211.1');
        INSERT INTO law_names VALUES ('GL', 'de', 'gog', 'abbreviation', 'lexwork_api', 'III B/1/1');
    """)
    con.commit()
    return con


def test_bare_cantonal_name_returns_candidates(monkeypatch):
    monkeypatch.setattr(m, "_get_cantonal_conn", _cantonal_mirror)
    r = m.get_law(abbreviation="GOG", article="148")
    assert r["error"] == "No law found with abbreviation 'GOG'."
    assert sorted(c["key"] for c in r["candidates"]) == ["GL/III B/1/1", "ZH/211.1"]
    assert "GL, ZH publish an act under that name" in r["note"]
    assert m._payload_outcome(r) == ("empty", "id_not_found_with_candidates")
    text = m._format_get_law_response(r)
    assert "ZH/211.1" in text and "Gerichtsorganisationsgesetz" in text


def test_bare_cantonal_number_returns_candidates(monkeypatch):
    monkeypatch.setattr(m, "_get_cantonal_conn", _cantonal_mirror)
    r = m.get_law(abbreviation="211.1", article="15")
    assert r["error"] == "No law found with SR number '211.1'."
    assert r["candidates"][0]["key"] == "ZH/211.1"
    assert "canton='ZH', sr_number='211.1'" in r["note"]


# ---------------------------------------------------------------------------
# search_laws sees the same names.
# ---------------------------------------------------------------------------

def test_search_laws_abbreviation_prematch_resolves_aliases():
    con = _conn()
    try:
        assert m._abbreviation_lookup_federal("Cst", "de", conn=con)[0]["sr_number"] == "101"
        assert m._abbreviation_lookup_federal("Pacte II", "de", conn=con)[0]["sr_number"] == "0.103.2"
        assert m._abbreviation_lookup_federal("OG", "de", conn=con)[0]["sr_number"] == "173.110"
        assert m._abbreviation_lookup_federal("OR", "de", conn=con)[0]["sr_number"] == "220"
        assert m._abbreviation_lookup_federal("Kündigung des Mietvertrags wegen Zahlungsverzug", "de", conn=con) == []
    finally:
        con.close()


# ---------------------------------------------------------------------------
# REST: /api/laws/{abbreviation}/{canton} in either order.
# ---------------------------------------------------------------------------

@pytest.fixture
def client(monkeypatch):
    import uvicorn
    from starlette.testclient import TestClient
    for name in ("_capture_event", "_record_tool_call", "_record_tool_outcome", "_record_query"):
        monkeypatch.setattr(m, name, lambda *a, **k: None)
    captured = {}
    monkeypatch.setattr(m, "_warm_page_cache", lambda: None)
    monkeypatch.setattr(m, "_log_startup", lambda: None)
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: captured.update(app=app))
    m.main_remote("127.0.0.1", 0)
    return TestClient(captured["app"])


def test_rest_cantonal_path_both_orders(client, monkeypatch):
    seen = []
    monkeypatch.setattr(m, "get_law", lambda **kw: seen.append(kw) or {"sr_number": "x", "articles": [{"article_num": "16"}]})
    assert client.get("/api/laws/GOG/GL", params={"article": "16"}).status_code == 200
    assert client.get("/api/laws/GL/GOG", params={"article": "16"}).status_code == 200
    assert client.get("/api/laws/ZH/StG").status_code == 200
    assert [(k["abbreviation"], k["canton"], k.get("article")) for k in seen] == [
        ("GOG", "GL", "16"), ("GOG", "GL", "16"), ("StG", "ZH", None)]
    r = client.get("/api/laws/LTF/BGG")
    assert r.status_code == 404 and "names no canton" in r.json()["detail"]
    # The single-segment route is untouched.
    assert client.get("/api/laws/OR", params={"article": "41"}).status_code == 200
    assert seen[-1]["abbreviation"] == "OR" and seen[-1]["canton"] == "CH"


# ---------------------------------------------------------------------------
# The published table.
# ---------------------------------------------------------------------------

def test_alias_table_is_well_formed():
    path = REPO / "docs" / "api" / "law_aliases.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["license"] == "CC0-1.0"
    aliases = doc["aliases"]
    assert len(aliases) >= 150
    kinds = set(doc["_kinds"])
    seen_norm: dict[str, str] = {}
    for key, entry in aliases.items():
        assert re.match(r"^\d[\d.]*$", entry["sr_number"]), key
        assert entry["kind"] in kinds, key
        assert entry["verified"], key            # never from memory
        if entry["kind"] == "former":
            assert entry["note"] and entry["as_of_hint"], key
        norm = m._norm_law_alias(key)
        assert seen_norm.get(norm, entry["sr_number"]) == entry["sr_number"], key
        seen_norm[norm] = entry["sr_number"]
    for excluded in doc["_excluded_unverified"]:
        assert m._norm_law_alias(excluded) not in seen_norm, excluded
    # Loaded by the server, keyed by the normalised form.
    table = m._law_alias_data()
    assert table["aliases"]["IPBPR"]["sr_number"] == "0.103.2"
    assert table["aliases"]["PACTEII"]["sr_number"] == "0.103.2"
    assert table["collections"]["LS"] == "ZH"
    assert set(table["collections"].values()) <= m._CANTON_CODES


def test_alias_table_missing_file_degrades_to_mirror_only(monkeypatch):
    monkeypatch.setattr(m, "_law_alias_cache", None)
    monkeypatch.setattr(m, "_LAW_ALIASES_PATH", Path("/nonexistent/law_aliases.json"))
    assert m._law_alias_data() == {"aliases": {}, "collections": {}}
    assert m.get_law(abbreviation="Cst")["sr_number"] == "101"      # mirror form still works
    assert m.get_law(abbreviation="IPBPR").get("error")            # table entries do not
