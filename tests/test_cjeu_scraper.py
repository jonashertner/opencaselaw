"""CJEU judgments relevant to Swiss law (scrapers/cjeu.py).

Fixtures captured 2026-10-25 from CELLAR: C-417/15 Schmidt (German and
French XHTML) and 15/69 Ugliola (the old HTML form). SPARQL results are
synthetic, in the endpoint's JSON shape. No network."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

FIX = REPO / "tests" / "fixtures" / "cjeu"

from scrapers import cjeu as c  # noqa: E402


def _lit(v):
    return {"type": "literal", "value": v}


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    def refuse(self, url, **_):
        raise AssertionError(f"network access in a test: {url}")
    monkeypatch.setattr(c.CJEUScraper, "get", refuse)
    monkeypatch.setattr(c.CJEUScraper, "post", refuse)


def test_scope_grouping_collects_acts_per_judgment():
    rows = [
        {"celex": _lit("62015CJ0417"), "date": _lit("2016-11-16"), "ecli": _lit("ECLI:EU:C:2016:881"),
         "actc": _lit("32012R1215")},
        {"celex": _lit("62015CJ0417"), "date": _lit("2016-11-16"), "ecli": _lit("ECLI:EU:C:2016:881"),
         "actc": _lit("22007A1221(03)")},
        {"celex": _lit("61969CJ0015"), "actc": _lit("31968R1612")},
    ]
    g = c.group_scope(rows)
    assert g["62015CJ0417"] == {"date": date(2016, 11, 16), "ecli": "ECLI:EU:C:2016:881",
                                "acts": ["EuGVVO 1215/2012", "LugÜ 2007"]}
    assert g["61969CJ0015"]["acts"] == ["VO 1612/68"] and g["61969CJ0015"]["date"] is None


def test_queries_name_every_act():
    q = c.scope_query()
    assert all(f'"{act}"^^xsd:string' in q for act in c.SCOPE_ACTS)
    assert '"6[0-9]{4}CJ"' not in q and "^6[0-9]{4}CJ" in q          # Court of Justice judgments only
    assert '"62015CJ0417"^^xsd:string' in c.meta_query(["62015CJ0417"])


def test_meta_grouping():
    lang = "http://publications.europa.eu/resource/authority/language/{}"
    rows = [{"celex": _lit("62015CJ0417"), "lang": {"value": lang.format("DEU")},
             "ident": _lit("Rechtssache C-417/15"), "parties": _lit("Wolfgang Schmidt gegen Christiane Schmidt"),
             "info": _lit("Urteil des Gerichtshofs (Zweite Kammer) vom 16. November 2016")},
            {"celex": _lit("62015CJ0417"), "lang": {"value": lang.format("SPA")}, "ident": _lit("x")}]
    g = c.group_meta(rows)
    assert set(g["62015CJ0417"]) == {"de"}
    assert c.chamber_of(g["62015CJ0417"]["de"]["info"]) == "Zweite Kammer"


@pytest.mark.parametrize("ident, celex, docket", [
    ("Rechtssache C-417/15", "62015CJ0417", "C-417/15"),
    ("Verbundene Rechtssachen C-411/10 und C-493/10", "62010CJ0411", "C-411/10, C-493/10"),
    ("Rechtssache 15-69", "61969CJ0015", "15/69"),
    ("", "62015CJ0417", "C-417/15"),
    ("", "61969CJ0015", "15/69"),
])
def test_case_numbers(ident, celex, docket):
    assert c.case_numbers(ident, celex) == docket


def test_keyword_chains():
    de = c.judgment_text((FIX / "62015CJ0417_de.xhtml").read_bytes())
    fr = c.judgment_text((FIX / "62015CJ0417_fr.xhtml").read_bytes())
    old = c.judgment_text((FIX / "61969CJ0015_de.html").read_bytes())
    assert c.keyword_chain(de, "de").startswith("Vorlage zur Vorabentscheidung — Raum der Freiheit")
    assert c.keyword_chain(fr, "fr").startswith("Renvoi préjudiciel — Espace de liberté")
    assert c.keyword_chain(old, "de").startswith("FREIZUEGIGKEIT - ARBEITNEHMER - GLEICHBEHANDLUNG")


def _scraper(tmp_path, files: dict[str, bytes]):
    s = c.CJEUScraper(state_dir=tmp_path)

    def fake_session_get(url, headers=None, **_):
        key = (url.rsplit("/", 1)[1], headers["Accept-Language"], headers["Accept"])
        body = files.get(key)
        return SimpleNamespace(status_code=200 if body else 404, content=body or b"",
                               raise_for_status=lambda: None)

    s.session.get = fake_session_get
    s.REQUEST_DELAY = 0
    return s


def _stub():
    return {"celex": "62015CJ0417", "decision_id": "cjeu_62015CJ0417", "docket_number": "62015CJ0417",
            "date": date(2016, 11, 16), "ecli": "ECLI:EU:C:2016:881", "acts": ["EuGVVO 1215/2012"],
            "meta": {"de": {"ident": "Rechtssache C-417/15", "parties": "Wolfgang Schmidt gegen Christiane Schmidt.",
                            "info": "Urteil des Gerichtshofs (Zweite Kammer) vom 16. November 2016"}}}


def test_fetch_builds_the_decision_and_its_citation(tmp_path):
    import mcp_server
    x = "application/xhtml+xml"
    s = _scraper(tmp_path, {("62015CJ0417", "deu", x): (FIX / "62015CJ0417_de.xhtml").read_bytes(),
                            ("62015CJ0417", "fra", x): (FIX / "62015CJ0417_fr.xhtml").read_bytes()})
    d = s.fetch_decision(_stub())
    assert d.court == "cjeu" and d.canton == "EU" and d.language == "de"
    assert d.docket_number == "C-417/15" and d.docket_number_2 == "62015CJ0417"
    assert d.title == "Wolfgang Schmidt gegen Christiane Schmidt" and d.chamber == "Zweite Kammer"
    assert d.external_id == "ECLI:EU:C:2016:881"
    assert d.regeste.endswith("[Bezug: EuGVVO 1215/2012]")
    assert d.abstract_fr.startswith("Renvoi préjudiciel") and d.abstract_it is None
    assert d.source_url == "https://eur-lex.europa.eu/legal-content/DE/TXT/?uri=CELEX:62015CJ0417"
    row = {"decision_id": d.decision_id, "court": d.court, "docket_number": d.docket_number,
           "title": d.title, "decision_date": "2016-11-16", "external_id": d.external_id, "language": "de"}
    cs = mcp_server._build_citation_strings(row)
    assert cs["citation_string_de"] == ("EuGH, Urteil C-417/15 vom 16. November 2016, Wolfgang Schmidt gegen "
                                        "Christiane Schmidt, ECLI:EU:C:2016:881")
    assert cs["citation_string_fr"] == "CJUE, arrêt C-417/15 du 16 novembre 2016, ECLI:EU:C:2016:881"
    assert mcp_server._build_citation_strings(row, pinpoint="27")["citation_string_de"].endswith(", Rn. 27")


def test_old_judgment_from_html_and_french_fallback(tmp_path):
    s = _scraper(tmp_path, {("62015CJ0417", "fra", "application/xhtml+xml"): (FIX / "62015CJ0417_fr.xhtml").read_bytes()})
    d = s.fetch_decision(_stub())
    assert d.language == "fr" and d.abstract_de is None and d.decision_type == "Arrêt"
    s2 = _scraper(tmp_path, {("61969CJ0015", "deu", "text/html"): (FIX / "61969CJ0015_de.html").read_bytes()})
    old = s2.fetch_decision({**_stub(), "celex": "61969CJ0015", "decision_id": "cjeu_61969CJ0015", "meta": {}})
    assert old.language == "de" and old.docket_number == "15/69"
    assert old.regeste.startswith("FREIZUEGIGKEIT")


def test_discovery(tmp_path, monkeypatch):
    s = c.CJEUScraper(state_dir=tmp_path)
    s.state.mark_scraped("cjeu_61969CJ0015")
    scope = [{"celex": _lit(x), "actc": _lit("31968R1612")} for x in ("61969CJ0015", "62015CJ0417")]
    calls = []
    monkeypatch.setattr(s, "_sparql", lambda q: (calls.append(q), scope if "SELECT DISTINCT" in q else [])[1])
    stubs = list(s.discover_new())
    assert [x["celex"] for x in stubs] == ["62015CJ0417"] and stubs[0]["meta"] == {}
    monkeypatch.setattr(s, "_sparql", lambda q: [])
    with pytest.raises(RuntimeError, match="scope query returned nothing"):
        list(s.discover_new())


def test_registries_know_cjeu():
    import cli_ch
    import ecli
    import export_parquet
    import mcp_server
    import run_all_scrapers
    import run_scraper
    assert run_scraper.SCRAPERS["cjeu"] == ("scrapers.cjeu", "CJEUScraper")
    assert "cjeu" in run_all_scrapers.SKIP_BY_DEFAULT and "cjeu" in export_parquet.EXCLUDED_COURTS
    assert mcp_server.COURT_LEVELS["cjeu"] == "international" and "cjeu" in mcp_server.COURT_DISPLAY_NAMES
    assert ecli.mint_ecli("cjeu_62015CJ0417", "cjeu", "C-417/15", "2016-11-16") is None
    assert cli_ch.mint_cli_ch("cjeu_62015CJ0417", "cjeu", "C-417/15") is None
