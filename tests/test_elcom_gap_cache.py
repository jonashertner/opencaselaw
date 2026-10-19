"""ElCom: an image-only PDF is cached as a gap, a failed download is not (2026-09-14).

The 8 text-less scans on elcom.admin.ch were re-downloaded nightly since March 2026;
the class-level CACHE_NONE_AS_GAP flag never took effect in production and, once
run_scraper honoured it, would also have cached the transient 01:00 UTC 502s that the
09:00 retry unit exists for. So the gap is marked precisely in fetch_decision.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import scrapers.elcom as elcom_mod  # noqa: E402
from scrapers.elcom import ElComScraper  # noqa: E402

STUB = {"docket_number": "211-00008", "pdf_url": "https://www.elcom.admin.ch/x.pdf",
        "decision_date": "", "title": "t"}


class _Resp:
    content = b"%PDF-1.4 scan"


def test_textless_pdf_is_gap_cached(monkeypatch, tmp_path):
    s = ElComScraper(state_dir=tmp_path)
    monkeypatch.setattr(s, "get", lambda url, **k: _Resp())
    monkeypatch.setattr(elcom_mod, "_extract_pdf_text", lambda data: "")
    assert s.fetch_decision(dict(STUB)) is None
    assert s.state.is_known("elcom_211-00008")
    assert not getattr(s, "CACHE_NONE_AS_GAP", False)   # no blanket caching


def test_failed_download_is_not_gap_cached(monkeypatch, tmp_path):
    s = ElComScraper(state_dir=tmp_path)

    def boom(url, **k):
        raise RuntimeError("502 Bad Gateway")

    monkeypatch.setattr(s, "get", boom)
    assert s.fetch_decision(dict(STUB)) is None
    assert not s.state.is_known("elcom_211-00008")      # retried by the 09:00 unit
