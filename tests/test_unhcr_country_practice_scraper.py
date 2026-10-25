"""UNHCR country guidance via ecoi.net (scrapers/practice/unhcr_country.py).

Golden fixtures captured 2026-10-25: three Afghanistan pages of one chain
(Guidelines 2018 -> Guidance Note 2022 -> Update II 2025). No network."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

FIX = REPO / "tests" / "fixtures" / "practice" / "unhcr"

from scrapers.practice import unhcr_country as u  # noqa: E402


def _page(doc_id):
    return (FIX / f"doc_{doc_id}.html").read_text(encoding="utf-8")


def test_document_page():
    d = u.parse_document(_page(1442171))
    assert d["id"] == 1442171 and d["country"] == "Afghanistan" and d["language"] == "en"
    assert d["source"].startswith("UNHCR")
    assert d["title"].startswith("UNHCR Eligibility Guidelines for Assessing the International Protection Needs")
    assert d["pdf_url"] == ("https://www.ecoi.net/en/file/local/1442171/"
                            "90_1535962899_2018-08-30-unhcr-afghanistan-guidelines.pdf")
    assert {r["id"] for r in d["related"]} == {2067833, 2068281, 1449845}       # incl. the German version


@pytest.mark.parametrize("published, iso", [
    ("30 August 2018", "2018-08-30"), ("September 2025", "2025-09"), ("2013", "2013"), ("", ""),
])
def test_dates(published, iso):
    assert u._iso(published) == iso


def test_walk_follows_the_chain_and_keeps_unhcr_language_versions(tmp_path, monkeypatch):
    monkeypatch.setattr(u.UnhcrCountryGuidanceScraper, "OUTPUT_DIR", tmp_path / "practice")
    monkeypatch.setattr(u.UnhcrCountryGuidanceScraper, "REQUEST_DELAY", 0)
    # 2022 Guidance Note: links the 2018 Guidelines (a fixture) and editions
    # that are not fixtures (answered 404 here, as a removed page would be).
    monkeypatch.setattr(u, "SEEDS", {"Afghanistan": 2067833})
    s = u.UnhcrCountryGuidanceScraper()
    pages = {i: _page(i) for i in (2130459, 1442171, 2067833)}
    fetched = []

    def fake_get(url, **_):
        if "/file/local/" in url:
            raise AssertionError("PDFs go through fetch_pdf_text")
        doc_id = int(url.rsplit("/", 1)[1].split(".")[0])
        fetched.append(doc_id)
        if doc_id in pages:
            return SimpleNamespace(status_code=200, text=pages[doc_id], raise_for_status=lambda: None)
        return SimpleNamespace(status_code=404, text="", raise_for_status=lambda: None)

    s.get = fake_get
    s.fetch_pdf_text = lambda url: f"UNHCR guidance text from {url}. " * 40
    summary = s.run()
    rows = [json.loads(line) for line in (tmp_path / "practice" / "unhcr_country.jsonl").read_text().splitlines()]
    by_id = {r["doc_id"]: r for r in rows}
    assert set(by_id) == {"unhcr_country_2067833", "unhcr_country_1442171"}
    assert all(r["doc_number"] == "UNHCR country guidance: Afghanistan" for r in rows)
    assert by_id["unhcr_country_2067833"]["date"] == "2022-02"
    assert 1449845 in fetched                                     # the German 2018 version was followed
    assert by_id["unhcr_country_1442171"]["issuing_authority"] == "UNHCR"
    assert summary["failed"] == 0
    assert len(fetched) == len(set(fetched))                       # each page read once


def test_nothing_found_is_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(u.UnhcrCountryGuidanceScraper, "OUTPUT_DIR", tmp_path / "practice")
    monkeypatch.setattr(u.UnhcrCountryGuidanceScraper, "REQUEST_DELAY", 0)
    monkeypatch.setattr(u, "SEEDS", {"Afghanistan": 1})
    s = u.UnhcrCountryGuidanceScraper()
    s.get = lambda url, **_: SimpleNamespace(status_code=404, text="", raise_for_status=lambda: None)
    with pytest.raises(RuntimeError, match="no UNHCR document found"):
        s.run()
