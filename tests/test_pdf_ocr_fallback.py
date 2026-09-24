"""Image-only PDFs are OCRed before a scraper gives up (2026-09-15, offline).

8 ElCom Verfügungen, 8 PostCom Verfügungen of 2013-2016 and 2 ESchK tariff decisions are
scans without a text layer; they were downloaded nightly and dropped. OCR itself is
replaced by a fake here: tests stay offline and do not need Tesseract.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import scrapers.elcom as elcom_mod  # noqa: E402
import scrapers.eschk as eschk_mod  # noqa: E402
import scrapers.finma_versicherungsrecht as finma_vr_mod  # noqa: E402
import scrapers.pdf_ocr as pdf_ocr  # noqa: E402
import scrapers.postcom as postcom_mod  # noqa: E402
from base_scraper import BaseScraper  # noqa: E402
from models import make_decision_id  # noqa: E402

TEXT = "Eidgenössische Kommission. Verfügung vom 27. März 2013. Sachverhalt und Erwägungen. " * 4


class _Resp:
    status_code = 200
    content = b"%PDF-1.4 scanned page"


def _scraper_class(mod):
    return next(v for v in vars(mod).values()
                if isinstance(v, type) and issubclass(v, BaseScraper) and v.__module__ == mod.__name__)


CASES = [
    (postcom_mod, "postcom", {"docket_number": "VFG-3-2013", "decision_date": "27.03.2013", "title": "t",
                              "pdf_url": "https://www.postcom.admin.ch/dam/de/sd-web/x/a.pdf", "decision_type": "Verfügung"}),
    (elcom_mod, "elcom", {"docket_number": "211-00008", "pdf_url": "https://www.elcom.admin.ch/x.pdf",
                          "decision_date": "", "title": "t"}),
    (eschk_mod, "eschk", {"docket_number": "tarif_vn_2004", "pdf_url": "https://www.eschk.admin.ch/x.pdf",
                          "decision_date": "", "title": "t"}),
    # 2 of 2,585 FINMA Art. 49 VAG decisions are scans (2026-09-24)
    (finma_vr_mod, "finma_versicherungsrecht",
     {"decision_id": make_decision_id("finma_versicherungsrecht", "20091204_d_ch_b_01"),
      "docket_number": "20091204_d_ch_b_01", "decision_date": "04.12.2009",
      "url": "https://www.finma.ch/~/media/finma/dokumente/dokumentencenter/myfinma/"
             "versicherungsrecht/2009/20091204_d_ch_b_01.pdf",
      "title": "4. Dezember 2009 Bundesgericht Deutsch"}),
]


def _prepare(mod, tmp_path, monkeypatch, ocr_text):
    s = _scraper_class(mod)(state_dir=tmp_path)
    monkeypatch.setattr(s, "get", lambda url, **k: _Resp())
    monkeypatch.setattr(mod, "_extract_pdf_text", lambda data: "")
    calls = []

    def fake_ocr(data, **kw):
        calls.append(data)
        return ocr_text

    monkeypatch.setattr(pdf_ocr, "ocr_pdf_bytes", fake_ocr)
    return s, calls


@pytest.mark.parametrize("mod, court, stub", CASES, ids=[c[1] for c in CASES])
def test_scan_is_recovered_by_ocr(mod, court, stub, tmp_path, monkeypatch):
    s, calls = _prepare(mod, tmp_path, monkeypatch, TEXT)
    d = s.fetch_decision(dict(stub))
    assert d is not None
    assert "Erwägungen" in d.full_text
    assert d.decision_id == make_decision_id(court, stub["docket_number"])
    assert calls == [_Resp.content]


@pytest.mark.parametrize("mod, court, stub", CASES, ids=[c[1] for c in CASES])
def test_scan_without_ocr_text_is_cached_as_gap(mod, court, stub, tmp_path, monkeypatch):
    s, _ = _prepare(mod, tmp_path, monkeypatch, "")
    assert s.fetch_decision(dict(stub)) is None
    assert s.state.is_known(make_decision_id(court, stub["docket_number"]))


def test_unreadable_bytes_give_empty_text():
    assert pdf_ocr.ocr_pdf_bytes(b"not a pdf at all") == ""
