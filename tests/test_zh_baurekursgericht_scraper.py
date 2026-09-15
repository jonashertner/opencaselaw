"""Offline test for the ZH Baurekursgericht listing parser (scrapers/cantonal/zh_baurekursgericht.py).

Golden excerpt of the live volltextsuche listing (invariant #8: no network). Regression
for 2026-09-14: "Zwischenentscheid vom …" entries have no BRGE number, so they all mapped
to one decision_id and every one after the first (2012) was skipped as already known —
~80 missing rows. They are now keyed by the PDF's case number.
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scrapers.cantonal.zh_baurekursgericht import ZHBaurekursgerichtScraper  # noqa: E402

FIXTURE = (REPO / "tests" / "fixtures" / "zh_baurekursgericht_listing_2026_excerpt.html").read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _no_corpus_shard(tmp_path, monkeypatch):
    """Tests never read the real output/ shard."""
    monkeypatch.setenv("ZH_BAUREKURSGERICHT_SHARD", str(tmp_path / "no-shard.jsonl"))


def _items(tmp_path):
    s = ZHBaurekursgerichtScraper(state_dir=tmp_path)
    soup = BeautifulSoup(FIXTURE, "html.parser")
    return [s._parse_item(d) for d in soup.find_all("div", class_="search-listing-item")]


def test_numbered_entry_keeps_its_brge_docket(tmp_path):
    stubs = _items(tmp_path)
    assert stubs[0]["docket_number"] == "BRGE I Nr. 0007/2026"
    assert stubs[0]["decision_date"] == date(2026, 1, 30)
    assert stubs[0]["decision_id"] == "zh_baurekursgericht_BRGE I Nr. 0007_2026"


def test_zwischenentscheide_get_distinct_ids_from_the_pdf_case_number(tmp_path):
    stubs = _items(tmp_path)
    zw = [st for st in stubs if st["docket_number"].startswith("Zwischenentscheid")]
    assert len(zw) == 2
    assert zw[0]["docket_number"] == "Zwischenentscheid r1s.2024.05044_45"
    assert zw[1]["docket_number"] == "Zwischenentscheid r3.2025.104_108"
    assert zw[0]["decision_id"] != zw[1]["decision_id"]
    assert zw[0]["decision_date"] == date(2026, 1, 30)
    assert zw[1]["decision_date"] == date(2026, 3, 4)
    assert all(st["decision_id"] != "zh_baurekursgericht_Zwischenentscheid" for st in zw)


def _zw_urls(tmp_path):
    return [st["pdf_url"] for st in _items(tmp_path) if st["docket_number"].startswith("Zwischenentscheid")]


def test_held_pdf_resolves_to_the_row_already_in_the_corpus(tmp_path, monkeypatch):
    """2026-09-15: 193 of the 223 docket-less entries were held under their BRGE number;
    minting stem ids for them would have duplicated 193 decisions."""
    urls = _zw_urls(tmp_path)
    assert len(urls) == 2
    shard = tmp_path / "shard.jsonl"
    shard.write_text(json.dumps({
        "decision_id": "zh_baurekursgericht_BRGE I Nrn. 0044-0045_2024",
        "docket_number": "BRGE I Nrn. 0044-0045/2024",
        "pdf_url": urls[0],
    }) + "\n", encoding="utf-8")
    monkeypatch.setenv("ZH_BAUREKURSGERICHT_SHARD", str(shard))
    by_url = {st["pdf_url"]: st for st in _items(tmp_path)}
    assert by_url[urls[0]]["decision_id"] == "zh_baurekursgericht_BRGE I Nrn. 0044-0045_2024"
    assert by_url[urls[0]]["docket_number"] == "BRGE I Nrn. 0044-0045/2024"
    assert by_url[urls[1]]["docket_number"] == "Zwischenentscheid r3.2025.104_108"   # not held: new id


def test_unreadable_shard_never_mints_ids_while_state_holds_decisions(tmp_path):
    s = ZHBaurekursgerichtScraper(state_dir=tmp_path)
    s.state.mark_scraped("zh_baurekursgericht_BRGE I Nr. 0007_2026")
    soup = BeautifulSoup(FIXTURE, "html.parser")
    stubs = [s._parse_item(d) for d in soup.find_all("div", class_="search-listing-item")]
    assert [st["docket_number"] for st in stubs if st] == ["BRGE I Nr. 0007/2026"]   # numbered entries unaffected
    assert sum(1 for st in stubs if st is None) == 2                                  # docket-less entries skipped
