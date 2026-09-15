"""Offline test for the ESBK scraper (scrapers/esbk.py).

Golden HTML fixture of the admin.ch download-item component (invariant #8 — no live
network): asserts discover_new extracts docket (= the h4 title), decision date (from the
description "... vom DD. Monat YYYY"), and decision type, and dedups by DAM content hash.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scrapers.esbk import ESBKScraper  # noqa: E402

FIXTURE = """<html><body>
 <a class="download-item" aria-label="Download 62-2021-021-01"
    href="https://www.esbk.admin.ch/dam/de/sd-web/AAAAAAAA/62-2021-021-01-d.pdf"
    download="62-2021-021-01-d.pdf">
   <h4 class="download-item__title">62-2021-021-01</h4>
   <p class="download-item__description">Strafbescheid der ESBK vom 20. August 2021</p></a>
 <!-- same decision, fr variant (same DAM hash) -> must dedup -->
 <a class="download-item"
    href="https://www.esbk.admin.ch/dam/fr/sd-web/AAAAAAAA/62-2021-021-01-f.pdf"
    download="62-2021-021-01-f.pdf">
   <h4 class="download-item__title">62-2021-021-01</h4>
   <p class="download-item__description">Prononcé pénal</p></a>
 <a class="download-item"
    href="https://www.esbk.admin.ch/dam/de/sd-web/BBBBBBBB/62-2022-077-01-d.pdf"
    download="62-2022-077-01-d.pdf">
   <h4 class="download-item__title">62-2022-077-01</h4>
   <p class="download-item__description">Verfügung der ESBK vom 3. Februar 2023</p></a>
</body></html>"""


class _Resp:
    text = FIXTURE


# Real markup of a /de/verwaltungsrecht tile (2026-09-14): docket-less, date in the title.
FIX_VERW = """<html><body>
 <a class="download-item" href="https://www.esbk.admin.ch/dam/de/sd-web/nb1450XGtNk9/verrwaltungssanktion-2021-02-23-d.pdf">
   <div><h4 class="download-item__title">Verwaltungssanktion der ESBK vom 23. Februar 2021 (Art. 100 BGS)</h4>
   <p class="download-item__description">Unberechtigte Spielteilnahme (online Spielteilnahme trotz Spielsperre)</p>
   <p class="download-item__meta-info"><span class="meta-info__item">PDF</span><span class="meta-info__item">23. Februar 2021</span></p></div></a>
</body></html>"""
# The /de/rechtsprechung hub since 2026: links, no tiles.
HUB = """<html><body><a href="/de/strafrecht">Mehr über «Strafrecht»</a>
<a href="/de/verwaltungsrecht">Mehr über «Verwaltungsrecht»</a></body></html>"""


class _R:
    def __init__(self, text):
        self.text = text


def test_esbk_discover(monkeypatch, tmp_path):
    s = ESBKScraper(state_dir=tmp_path)
    monkeypatch.setattr(s, "get", lambda url, **k: _Resp())
    stubs = list(s.discover_new())

    # two distinct decisions (the de/fr variants of 62-2021-021-01 dedup by DAM hash)
    assert len(stubs) == 2
    by = {x["docket_number"]: x for x in stubs}
    assert set(by) == {"62-2021-021-01", "62-2022-077-01"}
    assert by["62-2021-021-01"]["decision_date"] == "20. August 2021"
    assert by["62-2021-021-01"]["decision_type"] == "Strafbescheid"
    assert by["62-2021-021-01"]["pdf_url"].endswith("62-2021-021-01-d.pdf")
    assert by["62-2022-077-01"]["decision_date"] == "3. Februar 2023"
    assert by["62-2022-077-01"]["decision_type"] == "Verfügung"


def test_esbk_since_filter(monkeypatch, tmp_path):
    from datetime import date
    s = ESBKScraper(state_dir=tmp_path)
    monkeypatch.setattr(s, "get", lambda url, **k: _Resp())
    stubs = list(s.discover_new(since_date=date(2022, 1, 1)))
    # only the 2023 decision survives the since-filter
    assert {x["docket_number"] for x in stubs} == {"62-2022-077-01"}


def test_esbk_reads_strafrecht_and_verwaltungsrecht_pages(monkeypatch, tmp_path):
    # 2026 restructure: the hub has no tiles; decisions sit on the two sub-pages.
    s = ESBKScraper(state_dir=tmp_path)
    fetched = []

    def fake_get(url, **k):
        fetched.append(url)
        if url.endswith("/de/strafrecht"):
            return _R(FIXTURE)
        if url.endswith("/de/verwaltungsrecht"):
            return _R(FIX_VERW)
        return _R(HUB)

    monkeypatch.setattr(s, "get", fake_get)
    stubs = list(s.discover_new())
    assert fetched == ["https://www.esbk.admin.ch/de/strafrecht",
                       "https://www.esbk.admin.ch/de/verwaltungsrecht"]
    by = {x["docket_number"]: x for x in stubs}
    assert len(by) == 3
    vs = by["Verwaltungssanktion der ESBK vom 23. Februar 2021 (Art. 100 BGS)"]
    assert vs["decision_type"] == "Verwaltungssanktion"
    assert vs["decision_date"] == "23. Februar 2021"          # from the title, not the description
    assert by["62-2021-021-01"]["decision_type"] == "Strafbescheid"


def test_esbk_type_words_most_specific_first():
    from scrapers.esbk import _decision_type
    assert _decision_type("Strafverfügung der ESBK vom 5. Februar 2025") == "Strafverfügung"
    assert _decision_type("Einziehungsbescheid der ESBK vom 10. Dezember 2025") == "Einziehungsbescheid"
    assert _decision_type("Prononcé pénal", "62-2021-021-01") is None


def test_esbk_no_tiles_anywhere_is_an_error(monkeypatch, tmp_path):
    # A silent "Found 0 new" on a restructured site hid 12 decisions for months.
    import pytest
    s = ESBKScraper(state_dir=tmp_path)
    monkeypatch.setattr(s, "get", lambda url, **k: _R(HUB))
    with pytest.raises(RuntimeError, match="no download-item tiles"):
        list(s.discover_new())
