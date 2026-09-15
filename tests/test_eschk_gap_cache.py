"""ESchK: a text-less PDF is cached as a gap, a failed download is not (2026-09-14)."""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import scrapers.eschk as eschk_mod  # noqa: E402
from scrapers.eschk import ESchKScraper  # noqa: E402

STUB = {"docket_number": "tarif_vn_2004", "pdf_url": "https://www.eschk.admin.ch/x.pdf",
        "decision_date": "", "title": "Tarif VN"}


class _Resp:
    content = b"%PDF-1.4 scan"


def test_textless_pdf_is_gap_cached(monkeypatch, tmp_path):
    s = ESchKScraper(state_dir=tmp_path)
    monkeypatch.setattr(s, "get", lambda url, **k: _Resp())
    monkeypatch.setattr(eschk_mod, "_extract_pdf_text", lambda data: "")
    assert s.fetch_decision(dict(STUB)) is None
    assert s.state.is_known("eschk_tarif_vn_2004")


def test_failed_download_is_not_gap_cached(monkeypatch, tmp_path):
    s = ESchKScraper(state_dir=tmp_path)

    def boom(url, **k):
        raise RuntimeError("503")

    monkeypatch.setattr(s, "get", boom)
    assert s.fetch_decision(dict(STUB)) is None
    assert not s.state.is_known("eschk_tarif_vn_2004")
