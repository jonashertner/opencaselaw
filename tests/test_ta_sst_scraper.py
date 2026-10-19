"""Offline tests for the Swiss Sports Tribunal scraper (scrapers/ta_sst.py).

Golden excerpt of https://www.sportstribunal.ch/rechtsprechung (fetched 2026-09-14;
invariant #8: no network). The excerpt keeps the CMS's real markup for 9 of the 112
listed decisions: both <h3> sections, the three docket languages (SSG/TSS/TDS), a joint
two-docket PDF, an "und" double ruling, a "resp." rectification, the "27 décember 2024"
typo (file-name date fallback) and an 8.3 short file name (SSG202~15.PDF).

The scraper reads the tribunal's page directly — it must not depend on entscheidsuche.
"""
from __future__ import annotations

import inspect
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import scrapers.ta_sst as ta_sst_mod  # noqa: E402
from models import make_decision_id  # noqa: E402
from scrapers.ta_sst import RECHTSPRECHUNG_URL, TaSSTScraper, parse_listing  # noqa: E402

FIXTURE = REPO / "tests" / "fixtures" / "sportstribunal_rechtsprechung_excerpt.html"
HTML = FIXTURE.read_text(encoding="utf-8")

EXPECTED = [
    # docket(s)                          type                      date               chamber?
    (["SSG 2026/E/71"],                  "Schiedsspruch",          date(2026, 7, 6),  False),
    (["TSS 2025/E/64"],                  "Schiedsspruch",          date(2026, 4, 20), False),
    (["TSS 2026/E/74"],                  "Abschreibungsverfügung", date(2026, 4, 17), False),
    (["SSG 2025/E/59", "SSG 2025/E/60"], "Abschreibungsverfügung", date(2025, 10, 3), False),
    (["TDS 2024/E/40"],                  "Entscheid",              date(2025, 6, 2),  False),
    (["SSG 2024/DO/22"],                 "Entscheid",              date(2025, 3, 14), False),
    (["CD 2016/DO/1"],                   "Entscheid",              date(2016, 3, 3),  True),
    (["DK 2019/DO/1"],                   "Entscheid",              date(2019, 6, 24), True),
    (["CD 2024/E/18"],                   "Entscheid",              date(2024, 12, 27), True),
]


class _Resp:
    def __init__(self, text: str = "", content: bytes = b""):
        self.text = text
        self.content = content


def _scraper(monkeypatch, tmp_path, pdf_bytes: bytes = b"%PDF-1.4 stub") -> TaSSTScraper:
    s = TaSSTScraper(state_dir=tmp_path)
    fetched: list[str] = []

    def fake_get(url, **kwargs):
        fetched.append(url)
        if url == RECHTSPRECHUNG_URL:
            return _Resp(text=HTML)
        assert url.lower().endswith(".pdf"), url
        return _Resp(content=pdf_bytes)

    monkeypatch.setattr(s, "get", fake_get)
    s.fetched = fetched  # type: ignore[attr-defined]
    return s


# ── listing parser ───────────────────────────────────────────────────────────

def test_parse_listing_entries_in_page_order():
    entries = parse_listing(HTML)
    assert [e["dockets"] for e in entries] == [x[0] for x in EXPECTED]
    assert [e["decision_type"] for e in entries] == [x[1] for x in EXPECTED]
    assert [e["decision_date"] for e in entries] == [x[2] for x in EXPECTED]
    assert [e["chamber"] is not None for e in entries] == [x[3] for x in EXPECTED]
    for e in entries:
        assert e["pdf_url"].startswith("https://www.sportstribunal.ch/customer/files/")
        assert e["title"].startswith(e["dockets"][0])
    # the Verfahrensreglement PDF (no docket in its link text) is not a decision
    assert not any("Verfahrensreglement" in e["pdf_url"] for e in entries)


def test_predecessor_section_gets_chamber_label():
    by_docket = {e["dockets"][0]: e for e in parse_listing(HTML)}
    assert by_docket["DK 2019/DO/1"]["chamber"] == "Disziplinarkammer des Schweizer Sports"
    assert by_docket["CD 2016/DO/1"]["chamber"] == "Disziplinarkammer des Schweizer Sports"
    assert by_docket["SSG 2026/E/71"]["chamber"] is None


def test_short_cms_filename_still_resolves():
    by_docket = {e["dockets"][0]: e for e in parse_listing(HTML)}
    # the CMS serves some 2026 files under 8.3 names; the date then comes from the text
    assert by_docket["SSG 2026/E/71"]["pdf_url"].endswith("/SSG202~15.PDF")
    assert by_docket["SSG 2026/E/71"]["decision_date"] == date(2026, 7, 6)


def test_typo_month_falls_back_to_filename_date():
    by_docket = {e["dockets"][0]: e for e in parse_listing(HTML)}
    e = by_docket["CD 2024/E/18"]
    assert "27 décember 2024" in e["title"]            # the page's typo, kept verbatim
    assert e["pdf_url"].endswith("Decision-du-27-decembre-2024.pdf")
    assert e["decision_date"] == date(2024, 12, 27)


def test_joint_pdf_is_one_entry_keyed_by_first_docket_with_both_in_title():
    joint = [e for e in parse_listing(HTML) if len(e["dockets"]) > 1]
    assert len(joint) == 1
    e = joint[0]
    assert e["dockets"] == ["SSG 2025/E/59", "SSG 2025/E/60"]
    assert "SSG 2025/E/59" in e["title"] and "SSG 2025/E/60" in e["title"]


# ── discovery / identity ─────────────────────────────────────────────────────

def test_decision_ids_match_the_existing_corpus_rows():
    # The corpus has carried these ids since 2026-03 (space kept, slashes → underscores).
    assert make_decision_id("ta_sst", "SSG 2025/E/60") == "ta_sst_SSG 2025_E_60"
    assert make_decision_id("ta_sst", "TDS 2024/DO/13") == "ta_sst_TDS 2024_DO_13"


def test_discover_yields_every_unknown_entry(monkeypatch, tmp_path):
    s = _scraper(monkeypatch, tmp_path)
    stubs = list(s.discover_new())
    assert [st["docket_number"] for st in stubs] == [x[0][0] for x in EXPECTED]
    assert s.fetched == [RECHTSPRECHUNG_URL]           # discovery is one page fetch
    joint = next(st for st in stubs if st["docket_number"] == "SSG 2025/E/59")
    assert joint["dockets"] == ["SSG 2025/E/59", "SSG 2025/E/60"]
    assert joint["decision_date"] == "2025-10-03"
    assert joint["url"] == joint["pdf_url"] == joint["source_url"]


def test_discover_skips_joint_entry_when_any_docket_is_known(monkeypatch, tmp_path):
    # Corpus rows for the two joint PDFs are keyed by the SECOND docket (E/60, E/57);
    # knowing either docket must suppress the entry, or the same PDF would be re-ingested
    # under a second id.
    s = _scraper(monkeypatch, tmp_path)
    s.state.mark_scraped("ta_sst_SSG 2025_E_60")
    s.state.mark_scraped("ta_sst_DK 2019_DO_1")
    dockets = [st["docket_number"] for st in s.discover_new()]
    assert "SSG 2025/E/59" not in dockets
    assert "DK 2019/DO/1" not in dockets
    assert len(dockets) == len(EXPECTED) - 2


def test_since_filter_keeps_undated_and_newer_entries(monkeypatch, tmp_path):
    s = _scraper(monkeypatch, tmp_path)
    dockets = [st["docket_number"] for st in s.discover_new(since_date=date(2026, 1, 1))]
    assert dockets == ["SSG 2026/E/71", "TSS 2025/E/64", "TSS 2026/E/74"]


# ── fetch ────────────────────────────────────────────────────────────────────

FR_TEXT = (
    "Tribunal du sport suisse TSS 2025/E/64 Sentence du 20 avril 2026 "
    "Le Tribunal du sport suisse, composé de la formation arbitrale, statuant sur la "
    "demande d'arbitrage déposée le 3 décembre 2025, considérant que la procédure est "
    "régie par le règlement de procédure et que les parties ont été entendues, "
    "prononce la sentence suivante. " * 3
)


def test_fetch_builds_decision_from_pdf_text(monkeypatch, tmp_path):
    s = _scraper(monkeypatch, tmp_path)
    monkeypatch.setattr(ta_sst_mod, "_extract_pdf_text", lambda data: FR_TEXT)
    stub = next(st for st in s.discover_new() if st["docket_number"] == "TSS 2025/E/64")
    d = s.fetch_decision(stub)
    assert d is not None
    assert d.decision_id == "ta_sst_TSS 2025_E_64"
    assert d.court == "ta_sst" and d.canton == "CH" and d.chamber is None
    assert d.decision_date == date(2026, 4, 20)
    assert d.decision_type == "Schiedsspruch"
    assert d.language == "fr"
    assert d.legal_area == "Sportrecht"
    assert d.title == "TSS 2025/E/64 - Sentence du 20 avril 2026"
    assert d.pdf_url == d.source_url == stub["url"]
    assert "Sentence du 20 avril 2026" in d.full_text


def test_fetch_predecessor_decision_carries_chamber(monkeypatch, tmp_path):
    s = _scraper(monkeypatch, tmp_path)
    monkeypatch.setattr(ta_sst_mod, "_extract_pdf_text", lambda data: "Entscheid " * 60)
    stub = next(st for st in s.discover_new() if st["docket_number"] == "DK 2019/DO/1")
    d = s.fetch_decision(stub)
    assert d is not None
    assert d.chamber == "Disziplinarkammer des Schweizer Sports"
    assert d.decision_date == date(2019, 6, 24)


def test_fetch_textless_pdf_returns_none_and_caches_gap(monkeypatch, tmp_path):
    s = _scraper(monkeypatch, tmp_path)
    monkeypatch.setattr(ta_sst_mod, "_extract_pdf_text", lambda data: "")
    stub = next(st for st in s.discover_new() if st["docket_number"] == "CD 2016/DO/1")
    assert s.fetch_decision(stub) is None
    assert s.state.is_known("ta_sst_CD 2016_DO_1")      # gap cached → not re-probed nightly
    assert "CD 2016/DO/1" not in [st["docket_number"] for st in s.discover_new()]


def test_fetch_download_failure_returns_none_without_gap(monkeypatch, tmp_path):
    s = _scraper(monkeypatch, tmp_path)
    stub = next(st for st in s.discover_new() if st["docket_number"] == "SSG 2026/E/71")

    def failing_get(url, **kwargs):
        raise RuntimeError("503 from sportstribunal.ch")

    monkeypatch.setattr(s, "get", failing_get)
    assert s.fetch_decision(stub) is None
    assert not s.state.is_known("ta_sst_SSG 2026_E_71")  # transient: retried next run


# ── source policy ────────────────────────────────────────────────────────────

def test_scraper_reads_the_tribunal_not_entscheidsuche():
    src = inspect.getsource(ta_sst_mod)
    assert "sportstribunal.ch/rechtsprechung" in src
    code = src.split('"""', 2)[2]          # everything after the module docstring
    assert "entscheidsuche" not in code.lower()
    assert "es_ta_sst" not in code and "_load_stubs" not in code
