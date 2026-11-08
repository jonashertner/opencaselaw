"""Offline tests for the AI court scraper (scrapers/cantonal/ai_gerichte.py).

Golden fixtures from 2026-11-08 (invariant #8): the ai.ch Rechtsprechung table
and the Plone media folder listing. Since ~2026-08-18 every listing link
carries a cache-buster (``v-21-2025.pdf?_t=…``), some lack ".pdf" and some
point into a trash container; the old ``href.endswith(".pdf")`` filter matched
nothing and the scraper reported +0 new with exit 0 for 82 nights.
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scrapers.cantonal import ai_gerichte as ai  # noqa: E402
from scrapers.cantonal.ai_gerichte import (  # noqa: E402
    AIGerichteScraper,
    head_regeste,
    parse_listing,
)

FIX = REPO / "tests" / "fixtures"
LISTING = (FIX / "ai_gerichte_listing_20261108.html").read_text(encoding="utf-8")
FOLDER = json.loads((FIX / "ai_gerichte_folder_20261108.json").read_text(encoding="utf-8"))
FOLDER_PATH = "/themen/staat-und-recht/gerichte/rechtsprechung/4e6dead68fcb49b5a80fe5a1ea15232d"

# The ids the production state held on 2026-11-08 (both legacy shapes).
HELD = [
    "V 10-2024", "V 11-2023", "KSE 10-2024", "V 16-2024", "kse-2-2025", "kse-8-2025",
    "kse-7-2025", "KSE 12-2025", "ke-5-2025", "KSE 15-2025", "KSE 10-2025", "KE 24-2025",
    "kba-2-2025", "v-9-2025", "KE 14-2025", "V 11-2022", "ke-5-2026", "V 18-2025",
    "V 17-2025", "k-2-2024", "KE 19-2025",
]


class _Resp:
    def __init__(self, text="", payload=None):
        self._raw = text.encode("utf-8")
        self.encoding = "ISO-8859-1"        # what requests assumes for ai.ch
        self._payload = payload
        self.status_code = 200

    @property
    def text(self):
        return self._raw.decode(self.encoding)

    def json(self):
        return self._payload


def _scraper(tmp_path, listing=LISTING, folder=FOLDER, held=HELD):
    state = tmp_path / "ai_gerichte.jsonl"
    state.write_text("".join(f"ai_gerichte_{h}\n" for h in held))
    s = AIGerichteScraper(state_dir=tmp_path)
    seen = []

    def fake_get(url, **k):
        seen.append(url)
        if url.startswith(ai.LISTING_URL):
            return _Resp(text=listing)
        if url.startswith(ai.API_BASE):
            return _Resp(payload=folder)
        raise AssertionError(url)

    s.get = fake_get
    return s, seen


def test_listing_parse_survives_cache_busters_and_trash_links():
    rows, folders = parse_listing(LISTING)
    assert folders == {FOLDER_PATH}
    assert len(rows) == 12
    v21 = rows[("V", 21, "2025")]
    assert v21["file_id"] == "v-21-2025.pdf"                 # ?_t=… stripped
    assert v21["listed"] == date(2026, 10, 26)
    assert v21["subject"] == "Ergänzungsleistungen, Verzicht auf Vermögen durch Ehe- und Erbvertrag"
    assert rows[("K", 3, "2025")]["file_id"] == "k-3-2025"   # no extension
    assert rows[("V", 15, "2025")]["file_id"] == "v-15-2025"  # trash-container link


def test_discovery_yields_the_missed_rulings_under_entscheidsuche_ids(tmp_path):
    s, seen = _scraper(tmp_path)
    stubs = list(s.discover_new())
    by = {x["docket_number"]: x for x in stubs}
    # 104 files, V 9-2024 uploaded twice -> 103 rulings; 21 already held.
    assert len(stubs) == 103 - len(HELD)
    for d in ("V 21-2025", "V 7-2025", "K 3-2025", "V 15-2025", "V 14-2025"):
        assert d in by, d
    v21 = by["V 21-2025"]
    assert v21["decision_id"] == "ai_gerichte_V_21-2025"
    assert v21["decision_date"] == date(2026, 10, 26)
    assert v21["subject"].startswith("Ergänzungsleistungen")   # not "ErgÃ¤nzungs…"
    assert v21["url"] == f"https://www.ai.ch{FOLDER_PATH}/v-21-2025.pdf"
    assert v21["pdf_url"] == f"{ai.API_BASE}{FOLDER_PATH}/v-21-2025.pdf/@@download/file"
    # The trash-container link is not used: the file is fetched from the folder.
    assert "cms_trash_container" not in by["V 15-2025"]["pdf_url"]
    # A folder-only ruling (off the listing) has no listing date or subject.
    assert by["V 14-2025"]["decision_date"] is None and by["V 14-2025"]["subject"] is None
    # Ids the CMS percent-encodes stay encoded in the URL; dockets with a space
    # instead of a hyphen ("V 10 2017") are normalised.
    assert by["KE 25-2018"]["pdf_url"].endswith("/KE%2025-2018.pdf/@@download/file")
    assert "V 10-2017" in by
    # Neither legacy shape is re-fetched.
    assert "KSE 7-2025" not in by and "V 18-2025" not in by and "V 9-2025" not in by
    assert any(u.startswith(ai.API_BASE + FOLDER_PATH) for u in seen)


def test_empty_listing_fails_the_run_after_yielding_the_folder(tmp_path):
    s, _ = _scraper(tmp_path, listing="<html><body><p>Wartung</p></body></html>")
    got = []
    with pytest.raises(RuntimeError, match="parsed 0 rulings"):
        for stub in s.discover_new():
            got.append(stub)
    assert len(got) == 103 - len(HELD)          # the folder still delivered


def test_empty_folder_fails_the_run(tmp_path):
    s, _ = _scraper(tmp_path, folder={"items": [], "items_total": 0})
    got = []
    with pytest.raises(RuntimeError, match="listed 0 files"):
        for stub in s.discover_new():
            got.append(stub)
    # The listing alone still yields its unheld rows (V 15/21/7-2025, K 3-2025).
    assert {x["docket_number"] for x in got} == {"V 21-2025", "V 7-2025", "K 3-2025", "V 15-2025"}


def test_head_regeste_takes_the_block_above_erwaegungen():
    text = (" \n \n1 - 5 \n \nIVG-Beschwerde \n \nBeim im August 2000 geborenen "
            "Beschwerdeführer trat die Invalidität am 29. September \n2022 ein. \n \n \n"
            "Erwägungen: \n \nI. \n \n1. \nA., geboren im August 2000")
    assert head_regeste(text) == (
        "IVG-Beschwerde\nBeim im August 2000 geborenen Beschwerdeführer trat die "
        "Invalidität am 29. September\n2022 ein.")
    assert head_regeste("Kein Kopf, keine Erwägungen hier.") is None


def test_fetch_uses_own_line_date_and_headnote(tmp_path, monkeypatch):
    s, _ = _scraper(tmp_path)
    text = ("1 - 3\nIVG-Beschwerde\nKurzer Leitsatz zum Fall.\n\nErwägungen:\n\n1.\n" + "x " * 200
            + "\nKantonsgericht Appenzell I.Rh., Abteilung Verwaltungsgericht, "
              "Entscheid V 15-2025 vom 3. Juni 2026\n")

    class PDF:
        status_code = 200
        content = b"%PDF-1.7 " + b"0" * 2000

    s.get = lambda url, **k: PDF()
    monkeypatch.setattr(AIGerichteScraper, "_extract_pdf_text", staticmethod(lambda b: text))
    d = s.fetch_decision({
        "decision_id": "ai_gerichte_V_15-2025", "docket_number": "V 15-2025",
        "decision_date": date(2026, 7, 6), "subject": "IVG-Beschwerde",
        "url": "https://www.ai.ch/x", "pdf_url": "https://ai-api.webcloud7.ch/x/@@download/file",
    })
    assert d.decision_date == date(2026, 6, 3)          # the ruling's own line
    assert d.publication_date == date(2026, 7, 6)       # when ai.ch listed it
    assert d.title == "IVG-Beschwerde"
    assert d.regeste == "IVG-Beschwerde\nKurzer Leitsatz zum Fall."


def test_html_error_page_is_not_a_pdf(tmp_path):
    s, _ = _scraper(tmp_path)

    class HTML:
        status_code = 200
        content = b"<!doctype html>" + b"x" * 5000

    s.get = lambda url, **k: HTML()
    stub = {"decision_id": "ai_gerichte_V_1-2026", "docket_number": "V 1-2026",
            "url": "u", "pdf_url": "p"}
    assert s.fetch_decision(stub) is None
    assert not s.state.is_known("ai_gerichte_V_1-2026")   # retried next run
