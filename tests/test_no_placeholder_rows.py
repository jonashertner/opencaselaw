"""No "[Text extraction failed …]" placeholder rows (2026-09-14).

The 2026-09-14 corpus scan found 1,555 vs_gerichte rows (22 % of the court) whose full
text is the scraper's own placeholder, written when a PDF download timed out and then
marked known forever; ai_gerichte stored the title as the text, ti_gerichte the same
placeholder. Now: a failed download returns None (retried next run), a fetched document
without usable text is cached as a 7-day gap.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import scrapers.cantonal.ti_gerichte as ti_mod  # noqa: E402
from scrapers.cantonal.ai_gerichte import AIGerichteScraper  # noqa: E402
from scrapers.cantonal.ti_gerichte import TIGerichteScraper  # noqa: E402
from scrapers.cantonal.vs_gerichte import VSGerichteScraper  # noqa: E402


class _Resp:
    status_code = 200
    content = b"%PDF-1.4 " + b"x" * 2000
    text = "<html>" + "x" * 600 + "</html>"


def _boom(url, **k):
    raise RuntimeError("ReadTimeout")


VS_STUB = {"doc_id": "abc", "docket_number": "A1 25 1", "decision_id": "vs_gerichte_A1 25 1",
           "url": "https://api-justsearch.vs.ch/api/documents/abc/", "decision_date": None}
AI_STUB = {"pdf_url": "https://www.ai.ch/x/download", "docket_number": "AI-2025/1",
           "decision_id": "ai_gerichte_AI-2025_1", "url": "https://www.ai.ch/x", "title": "Titel"}
TI_STUB = {"docket_number": "35.2025.89", "url": "https://www.sentenze.ti.ch/doc",
           "decision_date": None, "autorita": None, "title": "t"}


def test_vs_textless_pdf_is_gap_not_placeholder(monkeypatch, tmp_path):
    s = VSGerichteScraper(state_dir=tmp_path)
    monkeypatch.setattr(s, "get", lambda url, **k: _Resp())
    monkeypatch.setattr(type(s), "_extract_pdf_text", staticmethod(lambda b: ""))
    assert s.fetch_decision(dict(VS_STUB)) is None
    assert s.state.is_known("vs_gerichte_A1 25 1")


def test_vs_download_failure_is_retried(monkeypatch, tmp_path):
    s = VSGerichteScraper(state_dir=tmp_path)
    monkeypatch.setattr(s, "get", _boom)
    assert s.fetch_decision(dict(VS_STUB)) is None
    assert not s.state.is_known("vs_gerichte_A1 25 1")


def test_ai_textless_pdf_is_gap_not_title(monkeypatch, tmp_path):
    s = AIGerichteScraper(state_dir=tmp_path)
    monkeypatch.setattr(s, "get", lambda url, **k: _Resp())
    monkeypatch.setattr(type(s), "_extract_pdf_text", staticmethod(lambda b: ""))
    assert s.fetch_decision(dict(AI_STUB)) is None
    assert s.state.is_known("ai_gerichte_AI-2025_1")


def test_ti_textless_document_is_gap(monkeypatch, tmp_path):
    s = TIGerichteScraper(state_dir=tmp_path)
    monkeypatch.setattr(s, "get", lambda url, **k: _Resp())
    monkeypatch.setattr(ti_mod, "_extract_document_text", lambda soup: "")
    assert s.fetch_decision(dict(TI_STUB)) is None
    assert s.state.is_known("ti_gerichte_35.2025.89")
