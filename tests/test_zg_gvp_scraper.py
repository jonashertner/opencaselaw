"""Offline tests for the Zug GVP scraper (scrapers/cantonal/zg_gvp.py).

Golden fixtures captured from the live DecWork API on 2026-09-09 (invariant #8,
no network):
- tests/fixtures/zg_gvp_chronology_excerpt.json — nine chronology entries in the
  real nested shape ({year: {German month name: [entries]}}) covering the
  edge cases: "N/A " docket prefix, repeated umbrella
  ("Obergericht, Obergericht, Justizkommission"), Landammann, bare
  Datenschutzstelle number, stray leading space, chronology id != decree_id.
- tests/fixtures/zg_gvp_decree_655.json — detail JSON of N/A RR 1996 052.
- tests/fixtures/zg_gvp_decree_81_dss_2010.pdf / .txt — the 21 KB 2010
  Datenschutzstelle PDF and its pdfplumber text (text-layer check).
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from models import make_decision_id  # noqa: E402
from scrapers.cantonal import zg_gvp  # noqa: E402
from scrapers.cantonal.zg_gvp import (  # noqa: E402
    ZGGVPScraper,
    clean_docket,
    extract_pdf_text,
    looks_glued,
    parse_institution,
)

FIX = REPO / "tests" / "fixtures"
CHRONOLOGY = json.loads((FIX / "zg_gvp_chronology_excerpt.json").read_text())
DETAIL_655 = json.loads((FIX / "zg_gvp_decree_655.json").read_text())
PDF_81 = (FIX / "zg_gvp_decree_81_dss_2010.pdf").read_bytes()
TEXT_81 = (FIX / "zg_gvp_decree_81_dss_2010.txt").read_text()


def _entries():
    return [
        e
        for months in CHRONOLOGY["chronology"].values()
        for bucket in months.values()
        for e in bucket
    ]


def _entry(number: str) -> dict:
    return next(e for e in _entries() if e["number"] == number)


class _Resp:
    def __init__(self, *, content=b"", payload=None, content_type="application/json"):
        self.content = content
        self._payload = payload
        self.headers = {"Content-Type": content_type}

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


def _scraper(monkeypatch, tmp_path, *, detail=None, pdf=PDF_81):
    s = ZGGVPScraper(state_dir=tmp_path)
    calls = []

    def fake_post(url, **kw):
        calls.append(("POST", url, kw.get("headers", {}).get("Origin")))
        assert url == zg_gvp.CHRONOLOGY_URL
        return _Resp(payload=CHRONOLOGY)

    def fake_get(url, **kw):
        calls.append(("GET", url, kw.get("headers", {}).get("Origin")))
        if url.startswith(zg_gvp.PDF_URL + "/"):
            return _Resp(content=pdf, content_type="application/pdf")
        if url.startswith(zg_gvp.DETAIL_URL + "/"):
            if detail is None:
                raise RuntimeError("detail endpoint down")
            return _Resp(payload=detail)
        raise AssertionError(f"unexpected GET {url}")

    monkeypatch.setattr(s, "post", fake_post)
    monkeypatch.setattr(s, "get", fake_get)
    s._calls = calls
    return s


# ── registry ────────────────────────────────────────────────────────────


def test_registered_in_run_scraper():
    from run_scraper import SCRAPERS

    assert SCRAPERS["zg_gvp"] == ("scrapers.cantonal.zg_gvp", "ZGGVPScraper")


def test_court_code_and_politeness(tmp_path):
    s = ZGGVPScraper(state_dir=tmp_path)
    assert s.court_code == "zg_gvp"
    assert s.REQUEST_DELAY >= 1.5  # as ag_gerichte
    assert zg_gvp.HEADERS["Origin"] == "https://bgs.zg.ch"
    assert zg_gvp.CHRONOLOGY_URL == "https://decwork.zg.ch/api/main/v1/de/decrees_chronology"


# ── institution / docket parsing ────────────────────────────────────────


@pytest.mark.parametrize(
    "institution, expected",
    [
        ("Verwaltungsgericht", ("zg_verwaltungsgericht", None)),
        ("Regierungsrat", ("zg_regierungsrat", None)),
        ("Obergericht", ("zg_obergericht", None)),
        ("Obergericht, Justizkommission", ("zg_obergericht", "Justizkommission")),
        ("Obergericht, Obergericht, Justizkommission", ("zg_obergericht", "Justizkommission")),
        ("Obergericht, Zivilabteilung", ("zg_obergericht", "Zivilabteilung")),
        ("Obergericht, Beschwerdeabteilung", ("zg_obergericht", "Beschwerdeabteilung")),
        ("Obergericht, Strafabteilung", ("zg_obergericht", "Strafabteilung")),
        ("Kantonsgericht", ("zg_kantonsgericht", None)),
        ("Strafgericht", ("zg_strafgericht", None)),
        ("Strafgericht, Berufungskammer", ("zg_strafgericht", "Berufungskammer")),
        ("Datenschutzstelle", ("zg_datenschutzstelle", None)),
        (
            "Aufsichtskommission über die Rechtsanwältinnen und Rechtsanwälte",
            ("zg_anwaltsaufsicht", None),
        ),
        ("Landammann", ("zg_regierungsrat", "Landammann")),
        ("", ("zg_gvp", None)),
        (None, ("zg_gvp", None)),
        ("Schätzungskommission", ("zg_gvp", None)),  # unknown body → fallback, never a guess
    ],
)
def test_parse_institution(institution, expected):
    assert parse_institution(institution) == expected


def test_institution_parts_dedupes_repeated_umbrella():
    assert zg_gvp.institution_parts("Obergericht, Obergericht, Justizkommission") == [
        "Obergericht", "Justizkommission",
    ]
    assert zg_gvp.institution_parts("Verwaltungsgericht") == ["Verwaltungsgericht"]
    assert zg_gvp.institution_parts(None) == []
    assert zg_gvp.institution_parts(" , ") == []


def test_every_institution_in_the_fixture_maps_to_a_specific_court():
    for e in _entries():
        court, _ = parse_institution(e["institution_name"])
        assert court != "zg_gvp", e["institution_name"]


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("N/A RR 1995 016", "RR 1995 016"),
        ("N/A OG 1995 006", "OG 1995 006"),
        (" DI 2025-141", "DI 2025-141"),
        ("V 1997 / 52", "V 1997 / 52"),  # slash kept: make_decision_id maps it to "_"
        ("64", "64"),
        ("Z2  2020 47", "Z2 2020 47"),
        ("", ""),
        (None, ""),
    ],
)
def test_clean_docket(raw, expected):
    assert clean_docket(raw) == expected


# ── discovery ───────────────────────────────────────────────────────────


def test_discover_yields_all_newest_first_with_stable_ids(monkeypatch, tmp_path):
    s = _scraper(monkeypatch, tmp_path)
    stubs = list(s.discover_new())
    assert len(stubs) == 9
    assert s.portal_count == 1357
    assert s._calls == [("POST", zg_gvp.CHRONOLOGY_URL, "https://bgs.zg.ch")]

    dates = [st["decree_date"] for st in stubs]
    assert dates == sorted(dates, reverse=True)  # newest first, across month-name buckets
    assert stubs[0]["docket_number"] == "AK 2026 3"
    assert stubs[-1]["docket_number"] == "RR 1996 052"

    by_docket = {st["docket_number"]: st for st in stubs}

    # ids: court + docket, never the DecWork ids → stable across re-imports
    assert by_docket["RR 1996 052"]["decision_id"] == "zg_regierungsrat_RR 1996 052"
    assert by_docket["RR 1996 052"]["decree_id"] == "655"
    assert by_docket["JA 2000 7"]["decision_id"] == "zg_obergericht_JA 2000 7"
    assert by_docket["LA 2003 011"]["decision_id"] == "zg_regierungsrat_LA 2003 011"
    assert by_docket["64"]["decision_id"] == "zg_datenschutzstelle_64"
    assert by_docket["DI 2025-141"]["decision_id"] == "zg_regierungsrat_DI 2025-141"
    assert by_docket["BK 1999 19"]["decision_id"] == "zg_strafgericht_BK 1999 19"
    assert by_docket["V 2024 109"]["decision_id"] == "zg_verwaltungsgericht_V 2024 109"
    assert by_docket["AK 2026 3"]["decision_id"] == "zg_anwaltsaufsicht_AK 2026 3"

    # chronology `id` differs from `decree_id` for some rows: PDFs use decree_id
    assert _entry("S 2015 9")["id"] == 1350 and _entry("S 2015 9")["decree_id"] == 490
    assert by_docket["S 2015 9"]["decree_id"] == "490"
    assert _entry("V 2024 109")["id"] == 1352
    assert by_docket["V 2024 109"]["decree_id"] == "1350"

    # regeste candidate carried verbatim from the chronology
    assert by_docket["RR 1996 052"]["guidance_summary"] == _entry("N/A RR 1996 052")["guidance_summary"]
    assert by_docket["64"]["guidance_summary"] == ""
    assert by_docket["RR 1996 052"]["guiding_decree"] is True
    assert by_docket["64"]["guiding_decree"] is False


def test_discover_skips_known_and_filters_since(monkeypatch, tmp_path):
    s = _scraper(monkeypatch, tmp_path)
    s.state.mark_scraped("zg_datenschutzstelle_64")
    assert len(list(s.discover_new())) == 8

    s = _scraper(monkeypatch, tmp_path)
    recent = list(s.discover_new(since_date=date(2024, 1, 1)))
    assert [st["docket_number"] for st in recent] == ["AK 2026 3", "DI 2025-141", "V 2024 109"]

    s = _scraper(monkeypatch, tmp_path)
    assert [st["docket_number"] for st in s.discover_new(since_date="2025-06-01")] == ["AK 2026 3", "DI 2025-141"]


def test_ids_merge_with_the_tribuna_scrapers():
    """A decision published on the Tribuna portal (zg_obergericht /
    zg_verwaltungsgericht scrapers, 2019+) and in the GVP must get the same
    decision_id, so build_fts5's INSERT OR IGNORE + canonical_key collapses
    the pair without a new _COURT_OVERLAP_GROUPS entry."""
    from scrapers.cantonal.zg_gerichte import ZGVerwaltungsgerichtScraper
    from scrapers.cantonal.zg_obergericht import ZGObergerichtScraper

    assert parse_institution("Obergericht, Zivilabteilung")[0] == ZGObergerichtScraper.COURT_CODE_STR
    assert parse_institution("Verwaltungsgericht")[0] == ZGVerwaltungsgerichtScraper.COURT_CODE_STR

    gvp = ZGGVPScraper.stub_from_entry(_entry("V 2024 109"))
    tribuna_id = make_decision_id(ZGVerwaltungsgerichtScraper.COURT_CODE_STR, "V 2024 109")
    assert gvp["decision_id"] == tribuna_id


# ── PDF text ────────────────────────────────────────────────────────────


def _pdf_backend_available() -> bool:
    for mod in ("pdfplumber", "fitz", "pdfminer.high_level"):
        try:
            __import__(mod)
            return True
        except ImportError:
            continue
    return False


def _importable(mod: str) -> bool:
    try:
        __import__(mod)
        return True
    except ImportError:
        return False


# extract_pdf_text tries pdfplumber → pymupdf → pdfminer and keeps the first
# text it gets, so which backend rendered the fixture depends on the machine:
# CI installs requirements.txt (pymupdf only); the VPS and the dev venv have
# all three and use pdfplumber. Each case hides the backends that would win
# over the one under test, so a single local run covers every path CI and
# production take. pdfminer is never hidden: pdfplumber imports pdfminer.six.
_PDF_BACKENDS = [
    pytest.param("pdfplumber", ("fitz",), id="pdfplumber"),
    pytest.param("fitz", ("pdfplumber",), id="pymupdf"),
    pytest.param("pdfminer.high_level", ("pdfplumber", "fitz"), id="pdfminer"),
]


@pytest.mark.parametrize("backend, hidden", _PDF_BACKENDS)
def test_2010_pdf_has_a_text_layer(monkeypatch, backend, hidden):
    if not _importable(backend):
        pytest.skip(f"{backend} not installed")
    for mod in hidden:
        monkeypatch.setitem(sys.modules, mod, None)  # makes `import mod` raise ImportError
    text = extract_pdf_text(PDF_81)
    assert text.startswith("Datenschutzpraxis")
    assert "Datenschutzgesetz" in text and "BGS 157.1" in text
    assert not looks_glued(text)
    assert len(text) > 3000
    # The printed GVP page numbers (328, 329) are part of the page text and
    # kept verbatim — but WHERE they land differs by backend. pdfplumber and
    # pdfminer sort by position and end the text with "329" (offset 3336 of
    # 3339 and 3475 of 3481 chars); pymupdf emits blocks in content-stream
    # order and puts the running head "Datenschutzpraxis / 329" first
    # (offset 2184 of 3363), which is why endswith("329") went red on CI
    # (2026-09-09, pymupdf 1.28.2). So: each number once, on its own line.
    for page_no in ("328", "329"):
        assert len(re.findall(rf"(?m)^{page_no}\s*$", text)) == 1, page_no


def test_looks_glued_heuristic():
    assert not looks_glued(TEXT_81)
    glued = " ".join(["DieEventualmaximebeidoppelseitigenKlagenistanwendbar"] * 3 + ["kurz"] * 30)
    assert looks_glued(glued)
    assert not looks_glued("Rechtsschutzinteresse " * 10)  # too short to judge


def test_extract_prefers_pymupdf_when_pdfplumber_glues(monkeypatch):
    glued = " ".join(["DieEventualmaximebeidoppelseitigenKlagenistanwendbar"] * 3 + ["kurz"] * 30)
    monkeypatch.setattr(zg_gvp, "_extract_text_pdfplumber_first", lambda b: glued)
    monkeypatch.setattr(zg_gvp, "_extract_with_pymupdf", lambda b: TEXT_81)
    assert extract_pdf_text(b"%PDF") == TEXT_81
    # …but keeps the first result when pymupdf is unavailable or no better
    monkeypatch.setattr(zg_gvp, "_extract_with_pymupdf", lambda b: "")
    assert extract_pdf_text(b"%PDF") == glued


# ── fetch ───────────────────────────────────────────────────────────────


def test_fetch_decision_fields_verbatim(monkeypatch, tmp_path):
    s = _scraper(monkeypatch, tmp_path, detail=DETAIL_655)
    monkeypatch.setattr(zg_gvp, "extract_pdf_text", lambda b: TEXT_81)
    stub = ZGGVPScraper.stub_from_entry(_entry("N/A RR 1996 052"))
    d = s.fetch_decision(stub)

    assert d is not None
    assert d.decision_id == "zg_regierungsrat_RR 1996 052"
    assert d.court == "zg_regierungsrat"
    assert d.canton == "ZG"
    assert d.chamber is None
    assert d.docket_number == "RR 1996 052"
    assert d.decision_date == date(1996, 12, 23)
    assert d.publication_date == date(1996, 12, 31)  # from the detail endpoint
    assert d.language == "de"
    assert d.title == "Regierungsrat — RR 1996 052"
    # R1–R3: the headnote is the court's own text, byte-for-byte
    assert d.regeste == _entry("N/A RR 1996 052")["guidance_summary"]
    assert d.regeste.startswith("§ 40 WAG") or d.regeste.startswith("§ 10 Abs. 3 PVO")
    assert d.full_text == TEXT_81
    assert d.decision_type == "Leitentscheid"
    assert d.source_url == "https://bgs.zg.ch/app/de/decrees/655"
    assert d.pdf_url == "https://decwork.zg.ch/api/main/v1/de/decrees_pdf/655"
    assert d.external_id == "decwork_zg_655"
    assert d.collection is None  # the API does not expose the GVP volume

    gets = [c for c in s._calls if c[0] == "GET"]
    assert [c[1] for c in gets] == [
        "https://decwork.zg.ch/api/main/v1/de/decrees/655",
        "https://decwork.zg.ch/api/main/v1/de/decrees_pdf/655",
    ]
    assert all(c[2] == "https://bgs.zg.ch" for c in gets)


@pytest.mark.skipif(not _pdf_backend_available(), reason="no PDF text backend installed")
def test_fetch_decision_real_pdf_detail_down(monkeypatch, tmp_path):
    """Detail endpoint failure must not block the decision; no headnote → no regeste."""
    s = _scraper(monkeypatch, tmp_path, detail=None, pdf=PDF_81)
    stub = ZGGVPScraper.stub_from_entry(_entry("64"))
    d = s.fetch_decision(stub)
    assert d is not None
    assert d.court == "zg_datenschutzstelle"
    assert d.decision_id == "zg_datenschutzstelle_64"
    assert d.publication_date is None
    assert d.regeste is None
    assert d.decision_type is None
    assert d.full_text.startswith("Datenschutzpraxis")
    assert d.decision_date == date(2010, 12, 31)


def test_fetch_decision_chamber_and_landammann(monkeypatch, tmp_path):
    s = _scraper(monkeypatch, tmp_path, detail={"decree": {}})
    monkeypatch.setattr(zg_gvp, "extract_pdf_text", lambda b: TEXT_81)

    d = s.fetch_decision(ZGGVPScraper.stub_from_entry(_entry("JA 2000 7")))
    assert (d.court, d.chamber) == ("zg_obergericht", "Justizkommission")
    # the API repeats the umbrella; the title uses the de-duplicated parts
    assert d.title == "Obergericht, Justizkommission — JA 2000 7"

    d = s.fetch_decision(ZGGVPScraper.stub_from_entry(_entry("N/A LA 2003 011")))
    assert (d.court, d.chamber, d.docket_number) == ("zg_regierungsrat", "Landammann", "LA 2003 011")

    d = s.fetch_decision(ZGGVPScraper.stub_from_entry(_entry("BK 1999 19")))
    assert (d.court, d.chamber) == ("zg_strafgericht", "Berufungskammer")


def test_fetch_skips_retracted_and_non_pdf(monkeypatch, tmp_path):
    s = _scraper(monkeypatch, tmp_path, detail={"decree": {"retracted": True}})
    assert s.fetch_decision(ZGGVPScraper.stub_from_entry(_entry("64"))) is None
    assert [c for c in s._calls if "decrees_pdf" in c[1]] == []  # no PDF download for a retracted row

    s = _scraper(monkeypatch, tmp_path, detail={"decree": {}}, pdf=b"nope")
    monkeypatch.setattr(zg_gvp, "extract_pdf_text", lambda b: TEXT_81)

    def html_get(url, **kw):
        return _Resp(content=b"<html>error</html>", content_type="text/html") if "decrees_pdf" in url else _Resp(payload={"decree": {}})

    monkeypatch.setattr(s, "get", html_get)
    assert s.fetch_decision(ZGGVPScraper.stub_from_entry(_entry("64"))) is None


# ── serving-side registrations ──────────────────────────────────────────


def test_new_codes_have_display_names_and_safe_branches():
    import branch_map
    import mcp_server as m

    codes = set(zg_gvp._COURT_MAP.values())
    for code in codes:
        assert code in m.COURT_DISPLAY_NAMES, code
        assert m.COURT_DISPLAY_NAMES[code].startswith("ZG "), code

    assert branch_map.derive_branch("zg_regierungsrat") == "oeffentlich"
    assert branch_map.derive_branch("zg_datenschutzstelle") == "oeffentlich"
    assert branch_map.derive_branch("zg_anwaltsaufsicht") == "oeffentlich"
    assert branch_map.derive_branch("zg_strafgericht") == "straf"
    # composite courts stay NULL over guess
    assert branch_map.derive_branch("zg_kantonsgericht") is None
    assert branch_map.derive_branch("zg_obergericht") is None
