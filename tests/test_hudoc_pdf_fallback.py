"""ECtHR: the PDF endpoint is the fallback when docx-to-html conversion fails.

001-139179 (application 7974/11, judgment of 19.12.2013) answered HTTP 500 on
/app/conversion/docx/html/body every night from at least 2026-09-11 through
2026-09-16: discovery found it daily, the fetch returned None, ecthr.jsonl
gained nothing for five days and check_scraper_freshness fired
"no write for 5d". The same judgment converts fine at /app/conversion/pdf
(237 kB, verified live 2026-09-16), so the fetch now falls through to it.

A 204 or an empty body is a placeholder row, not a conversion failure, and
must not spend a second request.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import scrapers.hudoc as hudoc  # noqa: E402
from scrapers.hudoc import HUDOCFullScraper  # noqa: E402


class FakeResponse:
    def __init__(self, text="", status_code=200, content=b""):
        self.text = text
        self.status_code = status_code
        self.content = content

    def raise_for_status(self):
        return None


def test_pdf_fallback_recovers_a_judgment_whose_html_conversion_fails(tmp_path, monkeypatch):
    calls = []

    def fake_get(url, **kw):
        calls.append(url)
        if "docx/html" in url:
            raise RuntimeError("500 Server Error: Internal Server Error")
        return FakeResponse(content=b"%PDF-1.4 synthetic")

    monkeypatch.setattr(hudoc, "_extract_pdf_text", lambda data: "JUGEMENT " * 40)
    scraper = HUDOCFullScraper(state_dir=tmp_path)
    monkeypatch.setattr(scraper, "get", fake_get)

    text = scraper._fetch_body("001-139179")

    assert text and "JUGEMENT" in text
    assert any("docx/html" in u for u in calls)
    assert any("conversion/pdf" in u for u in calls)


def test_a_healthy_html_body_never_spends_a_pdf_request(tmp_path, monkeypatch):
    calls = []

    def fake_get(url, **kw):
        calls.append(url)
        return FakeResponse(text="<html><body>" + ("ARRET " * 40) + "</body></html>")

    scraper = HUDOCFullScraper(state_dir=tmp_path)
    monkeypatch.setattr(scraper, "get", fake_get)

    text = scraper._fetch_body("001-1")

    assert text and "ARRET" in text
    assert not any("conversion/pdf" in u for u in calls)


def test_placeholder_row_does_not_spend_a_pdf_request(tmp_path, monkeypatch):
    calls = []

    def fake_get(url, **kw):
        calls.append(url)
        return FakeResponse(text="", status_code=204)

    scraper = HUDOCFullScraper(state_dir=tmp_path)
    monkeypatch.setattr(scraper, "get", fake_get)

    assert scraper._fetch_body("001-2") is None
    assert not any("conversion/pdf" in u for u in calls)


def test_pdf_without_usable_text_returns_none(tmp_path, monkeypatch):
    def fake_get(url, **kw):
        if "docx/html" in url:
            raise RuntimeError("500 Server Error")
        return FakeResponse(content=b"%PDF-1.4 scan")

    monkeypatch.setattr(hudoc, "_extract_pdf_text", lambda data: "")
    monkeypatch.setattr(hudoc.pdf_ocr, "ocr_pdf_bytes", lambda data, **kw: "")
    scraper = HUDOCFullScraper(state_dir=tmp_path)
    monkeypatch.setattr(scraper, "get", fake_get)

    assert scraper._fetch_body("001-3") is None


def test_ocr_runs_only_when_the_text_layer_is_thin(tmp_path, monkeypatch):
    ocr_calls = []

    def fake_get(url, **kw):
        if "docx/html" in url:
            raise RuntimeError("500 Server Error")
        return FakeResponse(content=b"%PDF-1.4 scan")

    monkeypatch.setattr(hudoc, "_extract_pdf_text", lambda data: "")
    monkeypatch.setattr(
        hudoc.pdf_ocr, "ocr_pdf_bytes",
        lambda data, **kw: (ocr_calls.append(len(data)), "SCANNED JUDGMENT " * 20)[1],
    )
    scraper = HUDOCFullScraper(state_dir=tmp_path)
    monkeypatch.setattr(scraper, "get", fake_get)

    text = scraper._fetch_body("001-4")

    assert text and "SCANNED JUDGMENT" in text
    assert ocr_calls, "a PDF with no text layer must reach OCR"
