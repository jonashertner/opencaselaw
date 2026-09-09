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
    # Federal acts whose abbreviation is also a canton code or a cantonal
    # collection label, and acts an edition prefix could be peeled off
    # (SR numbers and abbreviations as in docs/data/law_codes.json).
    {"sr_number": "631.0", "title_de": "Zollgesetz vom 18. März 2005 (ZG)", "abbr_de": "ZG", "abbr_fr": "LD", "abbr_it": "LD"},
    {"sr_number": "747.201", "title_de": "Bundesgesetz vom 3. Oktober 1975 über die Binnenschifffahrt (BSG)",
     "abbr_de": "BSG", "abbr_fr": "LNI", "abbr_it": "LNI"},
    {"sr_number": "935.51", "title_de": "Bundesgesetz vom 29. September 2017 über Geldspiele (Geldspielgesetz, BGS)",
     "abbr_de": "BGS", "abbr_fr": "LJAr", "abbr_it": "LGD"},
    {"sr_number": "741.01", "title_de": "Strassenverkehrsgesetz vom 19. Dezember 1958 (SVG)", "abbr_de": "SVG", "abbr_fr": "LCR", "abbr_it": "LCStr"},
    {"sr_number": "514.54", "title_de": "Bundesgesetz vom 20. Juni 1997 über Waffen, Waffenzubehör und Munition (Waffengesetz, WG)",
     "abbr_de": "WG", "abbr_fr": "LArm", "abbr_it": "LArm"},
    {"sr_number": "131.212", "title_de": "Verfassung des Kantons Bern, vom 6. Juni 1993 (KV)", "abbr_de": "KV", "abbr_fr": "ConstC", "abbr_it": "CostC"},
]
ROWS = [{"sr_number": law["sr_number"], "article_num": "1",
         "text": f"Art. 1 of SR {law['sr_number']}."} for law in LAWS]
SCHLT_DE = "Schlusstitel: Anwendungs- und Einführungsbestimmungen"
SCHLT_FR = "Titre final: De l’entrée en vigueur et de l’application du code civil"
ROWS += [
    {"sr_number": "220", "article_num": "41", "text": "Wer einem andern widerrechtlich Schaden zufügt ..."},
    {"sr_number": "210", "article_num": "314abis", "text": "Fürsorgerische Unterbringung ..."},
    {"sr_number": "311.0", "article_num": "146", "text": "Betrug ..."},
    {"sr_number": "631.0", "article_num": "7", "text": "Zollpflicht ..."},
    # The ZGB Schlusstitel as the production mirror exposes it: block
    # disp_u1, its own Art. 1 (a different provision from main-body Art. 1).
    {"sr_number": "210", "article_num": "1", "lang": "fr", "text": "Art. 1 CC (corps du code)."},
    {"sr_number": "210", "article_num": "1", "section": "disp_u1", "section_heading": SCHLT_DE,
     "heading": "A. Allgemeine Bestimmungen / I. Regel der Nichtrückwirkung",
     "text": "Die rechtlichen Wirkungen von Tatsachen, die vor dem Inkrafttreten ..."},
    {"sr_number": "210", "article_num": "2", "section": "disp_u1", "section_heading": SCHLT_DE,
     "text": "Die Bestimmungen dieses Gesetzes, die um der öffentlichen Ordnung ..."},
    {"sr_number": "210", "article_num": "1", "lang": "fr", "section": "disp_u1", "section_heading": SCHLT_FR,
     "text": "Les effets juridiques de faits antérieurs à l’entrée en vigueur ..."},
    # An OR block that is not a Schlusstitel: a heading mismatch must be a miss.
    {"sr_number": "220", "article_num": "1", "section": "disp_u2",
     "section_heading": "Schlussbestimmungen der Änderung vom 23. März 1962", "text": "Übergang ..."},
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
    # An article request consults Fedlex for pending consolidations; the
    # tests are about names, not the network.
    monkeypatch.setattr(m, "_fetch_pending_changes", lambda sr: [])
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
    # No edition prefix is peeled off an unknown name: ASVG is not the
    # former SVG (741.01), NWG not the current WG (514.54), AKV/NKV not the
    # Bern KV (131.212), AZG not the Zollgesetz (631.0) — all of which the
    # fixture mirror carries. Only the edition names in the table resolve.
    for name in ("AXYZ", "AT", "AI", "NR", "ASVG", "aSVG", "NWG", "nWG", "AKV", "NKV", "AZG", "NZGB"):
        r = m.get_law(abbreviation=name)
        assert r.get("error", "").startswith("No law found with abbreviation"), (name, r)
        assert "sr_number" not in r, (name, r)
    con = _conn()
    try:
        assert m._resolve_federal_abbreviation(con, "ASVG") == (None, None)
        assert m._resolve_federal_abbreviation(con, "NWG") == (None, None)
        assert m._resolve_federal_abbreviation(con, "aStGB")[0] == "311.0"   # a listed edition name
    finally:
        con.close()


def test_edition_names_come_from_the_table_only():
    doc = json.loads((REPO / "docs" / "api" / "law_aliases.json").read_text(encoding="utf-8"))
    assert "_edition_prefixes" not in doc
    for name, sr in (("aStGB", "311.0"), ("aCP", "311.0"), ("aZGB", "210"), ("aOR", "220"), ("nDSG", "235.1")):
        assert doc["aliases"][name]["sr_number"] == sr
    r = m.get_law(abbreviation="aOR", article="41")
    assert r["sr_number"] == "220" and r["articles"][0]["article_num"] == "41"
    assert r["abbreviation_alias"]["kind"] == "former_edition"
    assert "former edition of OR" in r["abbreviation_alias"]["note"]


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


def test_swapped_fields_never_hijack_a_federal_act(monkeypatch):
    # ZG is the Zollgesetz (631.0) before it is canton Zug; BSG the
    # Binnenschifffahrtsgesetz (747.201) before it is Bern's collection; BGS
    # the Geldspielgesetz (935.51) before it is Appenzell's. The federal act
    # is served and the cantonal reading offered, never the other way round.
    seen = []
    monkeypatch.setattr(m, "_get_law_cantonal",
                        lambda sr, abbr, art, lang, canton: seen.append((sr, abbr, art, canton)) or {"sr_number": sr})
    r = m.get_law(abbreviation="ZG", article="7")
    assert r["sr_number"] == "631.0" and r["abbreviation"] == "ZG"
    assert [a["article_num"] for a in r["articles"]] == ["7"]
    assert r["alternative_reading"] == {"canton": "ZG", "sr_number": "7"}
    assert "code of canton ZG" in r["argument_note"] and "canton='ZG', sr_number='7'" in r["argument_note"]
    assert "Note: 'ZG' is a federal act (SR 631.0)" in m._format_get_law_response(r)
    assert m.get_law(abbreviation="BSG", article="3")["sr_number"] == "747.201"
    assert "collection label of canton BE" in m.get_law(abbreviation="BSG", article="3")["argument_note"]
    assert m.get_law(abbreviation="bGS", article="3")["sr_number"] == "935.51"
    assert seen == []                                    # the cantonal branch never ran
    # No note where there is nothing to disambiguate.
    assert "argument_note" not in m.get_law(abbreviation="OR", article="41")
    # An explicit canton already says cantonal: the swapped reading stands.
    m.get_law(abbreviation="ZG", article="7", canton="ZG")
    assert seen == [("7", None, None, "ZG")]
    # Without a federal mirror there is nothing to check against; the
    # swapped reading is the only one that can answer.
    monkeypatch.setattr(m, "_get_statutes_conn", lambda: None)
    m.get_law(abbreviation="ZG", article="7")
    assert seen[-1] == ("7", None, None, "ZG")


def test_swapped_fields_route_to_the_canton_when_no_federal_act_has_the_name(monkeypatch):
    seen = []
    monkeypatch.setattr(m, "_get_law_cantonal",
                        lambda sr, abbr, art, lang, canton: seen.append((sr, abbr, art, canton)) or {"sr_number": sr})
    r = m.get_law(abbreviation="AG", article="211.1")      # no federal act is named AG
    assert seen == [("211.1", None, None, "AG")]
    assert "No federal act is named 'AG'" in r["argument_note"]


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


def test_cantonal_language_fallback_is_noted(monkeypatch):
    monkeypatch.setattr(m, "_get_law_cantonal",
                        lambda sr, abbr, art, lang, canton: {"sr_number": sr, "language": lang})
    r = m.get_law(canton="GR", sr_number="110.100", language="rm")
    assert r["language"] == "de"
    assert r["language_fallback"] == {"requested": "rm", "served": "de"}
    assert "language_fallback" not in m.get_law(canton="GR", sr_number="110.100", language="de")


# ---------------------------------------------------------------------------
# Section names: the ZGB Schlusstitel is served from its block, or not at all.
# ---------------------------------------------------------------------------

def test_schlusstitel_serves_the_section_not_the_main_body():
    r = m.get_law(abbreviation="SchlT ZGB", article="1")
    assert r["sr_number"] == "210" and r["abbreviation"] == "ZGB"
    assert [a["text"][:40] for a in r["articles"]] == ["Die rechtlichen Wirkungen von Tatsachen,"]
    assert r["articles"][0]["section"] == "disp_u1"
    assert r["section"] == {"section": "disp_u1", "section_heading": SCHLT_DE}
    alias = r["abbreviation_alias"]
    assert alias["kind"] == "section" and alias["requested"] == "SchlT ZGB"
    assert "block 'disp_u1'" in alias["note"] and "not its main body" in alias["note"]
    assert "also_in_sections" not in r and "article_section_note" not in r
    assert "#art_" not in (r.get("source_url") or "")          # the anchor would hit the main body
    text = m._format_get_law_response(r)
    assert "Section: Schlusstitel: Anwendungs- und Einführungsbestimmungen (block disp_u1)" in text
    # The main body is untouched.
    assert m.get_law(abbreviation="ZGB", article="1")["articles"][0]["text"] == "Art. 1 of SR 210."
    # French name, French block.
    r = m.get_law(abbreviation="Titre final CC", article="1", language="fr")
    assert r["articles"][0]["text"].startswith("Les effets juridiques")
    assert r["section"]["section_heading"] == SCHLT_FR
    # Without an article: the block's own list.
    r = m.get_law(abbreviation="Schlusstitel ZGB")
    assert r["article_count"] == 2
    assert {a["section"] for a in r["articles"]} == {"disp_u1"}
    assert [a["article_num"] for a in r["articles"]] == ["1", "2"]


def test_section_name_is_a_miss_when_the_mirror_does_not_expose_the_block():
    table = m._law_alias_data()["aliases"]
    entry = table["SCHLTZGB"]
    entry["section"] = "disp_u9"                    # a rebuild renumbered the blocks
    r = m.get_law(abbreviation="SchlT ZGB", article="1")
    assert r["error"] == "No law found with abbreviation 'SchlT ZGB'."
    assert "does not expose it as one" in r["note"] and "SR 210" in r["note"]
    assert "articles" not in r
    assert m._payload_outcome(r) == ("empty", "id_not_found")
    assert "does not expose" in m._format_get_law_response(r)
    # A block that exists under another heading is not the Schlusstitel either.
    entry["sr_number"], entry["section"] = "220", "disp_u2"
    assert m.get_law(abbreviation="SchlT ZGB", article="1").get("error")
    # An older build without the `section` column.
    con = _conn()
    try:
        block, miss = m._alias_section_in_mirror(
            con, "210", "de", {"requested": "SchlT ZGB", "section": "disp_u1",
                               "section_heading": {"de": SCHLT_DE}}, has_section=False)
        assert block is None and miss["error"].startswith("No law found")
    finally:
        con.close()


def test_section_name_with_as_of_is_refused(monkeypatch):
    called = []
    monkeypatch.setattr(m, "_fetch_historical_law_version",
                        lambda *a: called.append(a) or {"sr_number": "210", "articles": []})
    r = m.get_law(abbreviation="SchlT ZGB", article="1", as_of="2010-01-01")
    assert r["error"].startswith("as_of is not supported with a section name") and "SR 210" in r["error"]
    assert called == []
    # The act itself still takes as_of.
    assert not m.get_law(abbreviation="ZGB", article="1", as_of="2010-01-01").get("error")


def test_malformed_section_entry_is_not_loaded(monkeypatch, tmp_path):
    bad = {"aliases": {
        "Good": {"sr_number": "210", "kind": "section", "section": "disp_u1", "section_heading": {"de": "x"}},
        "Injected": {"sr_number": "210", "kind": "section", "section": "x' OR 1=1 --", "section_heading": {"de": "x"}},
        "NoHeading": {"sr_number": "210", "kind": "section", "section": "disp_u1"},
    }}
    path = tmp_path / "law_aliases.json"
    path.write_text(json.dumps(bad), encoding="utf-8")
    monkeypatch.setattr(m, "_law_alias_cache", None)
    monkeypatch.setattr(m, "_LAW_ALIASES_PATH", path)
    assert set(m._law_alias_data()["aliases"]) == {"GOOD"}


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
        assert m._abbreviation_lookup_federal("aStGB", "de", conn=con)[0]["sr_number"] == "311.0"
        assert m._abbreviation_lookup_federal("ASVG", "de", conn=con) == []
        assert m._abbreviation_lookup_federal("ZG", "de", conn=con)[0]["sr_number"] == "631.0"
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
    assert {"former_edition", "section"} <= kinds
    seen_norm: dict[str, str] = {}
    for key, entry in aliases.items():
        assert re.match(r"^\d[\d.]*$", entry["sr_number"]), key
        assert entry["kind"] in kinds, key
        assert entry["verified"], key            # never from memory
        if entry["kind"] == "former":
            assert entry["note"] and entry["as_of_hint"], key
        if entry["kind"] == "former_edition":
            assert entry["note"] and key[0] == "a", key
        if entry["kind"] == "section":
            assert m._LAW_SECTION_ID_RE.match(entry["section"]), key
            assert set(entry["section_heading"]) == {"de", "fr", "it"}, key
        if entry["verified"].startswith("fedlex-sparql"):
            assert entry["fedlex_work"].startswith("https://fedlex.data.admin.ch/eli/cc/"), key
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
