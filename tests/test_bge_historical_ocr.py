"""Image-only BGE volumes are read with the Fraktur model, or stay a gap (2026-09-17, offline).

161 rulings of 1877 and 1909 exist only as scans on www.fallrecht.ch. They were dropped as
"no text extracted" every night, then gap-cached on 2026-09-16, after which every run of this
closed collection was a fast zero and tripped the "possible API outage" warning. The German
rulings are set in Fraktur: Tesseract's Antiqua models turn them into German-looking nonsense
("Die Natur diefer Klage ift", measured on BGE 3 I 457), so OCR runs only with the Fraktur
script model and a scan stays a gap where that model is missing. Tesseract is faked here.
"""
from __future__ import annotations

import sys
import types

import scrapers.bge_historical as bge
from scrapers import pdf_ocr
from scrapers.bge_historical import BGEHistoricalScraper

# What the Fraktur model returns for BGE 3 I 457: long s as printed, the ch and ck ligatures
# as "<" and ">". Words broken at the line end stay as printed.
RAW = (
    "Die Natur dieſer Klage iſt eine zweifelhafte und beſtrit-\n"
    "tene. Wo eine Grundverſicherung nur dur< Löſchung vollſtän-\n"
    "dig getilgt werden kann, wird ausdrü>lich die Uebergabe der\n"
    "Pfandurkunde an die Notariatskanzlei verlangt.\n"
) * 3


class _Resp:
    status_code = 200
    content = b"%PDF-1.4 scanned pages"
    text = ""


def _scraper(monkeypatch):
    s = BGEHistoricalScraper.__new__(BGEHistoricalScraper)
    monkeypatch.setattr(s, "get", lambda url, **kw: _Resp(), raising=False)
    return s


def _stub():
    return {
        "url": "https://www.fallrecht.ch/c1003457.pdf",
        "docket_number": "3_I_457", "bge_ref": "BGE 3 I 457",
        "volume": 3, "section": "I", "page": 457, "year": 1877, "is_pdf": True,
    }


def _fake_ocr(monkeypatch, text):
    calls = []

    def _ocr(data, **kw):
        calls.append(kw)
        return text

    monkeypatch.setattr(pdf_ocr, "ocr_pdf_bytes", _ocr)
    return calls


def test_scan_is_read_with_the_fraktur_model_and_normalised(monkeypatch):
    monkeypatch.setattr(bge, "_extract_pdf_text", lambda data: "")
    monkeypatch.setattr(bge, "_ocr_model_installed", lambda: True)
    calls = _fake_ocr(monkeypatch, RAW)

    d = _scraper(monkeypatch).fetch_decision(_stub())

    assert d is not None
    assert calls == [{"lang": "script/Fraktur"}]
    text = " ".join(d.full_text.split())
    assert "Die Natur dieser Klage ist eine zweifelhafte und bestrit- tene." in text
    assert "nur durch Löschung vollstän- dig getilgt" in text
    assert "wird ausdrücklich die Uebergabe" in text
    assert "ſ" not in text and "<" not in text and ">" not in text
    assert d.docket_number == "3_I_457" and d.decision_date.year == 1877
    assert d.source_url == "https://www.fallrecht.ch/c1003457.pdf"


def test_without_the_fraktur_model_a_scan_stays_a_gap(monkeypatch):
    """An Antiqua model would return enough characters to pass the length check, and garbage."""
    monkeypatch.setattr(bge, "_extract_pdf_text", lambda data: "")
    monkeypatch.setattr(bge, "_ocr_model_installed", lambda: False)
    calls = _fake_ocr(monkeypatch, RAW)

    assert _scraper(monkeypatch).fetch_decision(_stub()) is None
    assert calls == []


def test_scan_that_ocr_cannot_read_stays_a_gap(monkeypatch):
    monkeypatch.setattr(bge, "_extract_pdf_text", lambda data: "  \n ")
    monkeypatch.setattr(bge, "_ocr_model_installed", lambda: True)
    calls = _fake_ocr(monkeypatch, "")

    assert _scraper(monkeypatch).fetch_decision(_stub()) is None
    assert len(calls) == 1


def test_pdf_with_a_text_layer_is_not_ocred(monkeypatch):
    layer = "Urteil vom 3. Februar 1910 i. S. Müller gegen Regierungsrat des Kantons Zürich. " * 5
    monkeypatch.setattr(bge, "_extract_pdf_text", lambda data: layer)
    monkeypatch.setattr(bge, "_ocr_model_installed", lambda: True)
    calls = _fake_ocr(monkeypatch, RAW)

    d = _scraper(monkeypatch).fetch_decision(_stub())

    assert d is not None and "Regierungsrat des Kantons Zürich" in d.full_text
    assert calls == []


def _fake_tesseract(monkeypatch, listing, on="stdout"):
    runs = []

    def _run(cmd, **kw):
        runs.append(cmd)
        return types.SimpleNamespace(stdout=listing if on == "stdout" else "",
                                     stderr=listing if on == "stderr" else "", returncode=0)

    monkeypatch.setattr(bge, "_OCR_MODEL_STATE", {})
    monkeypatch.setitem(sys.modules, "pytesseract",
                        types.SimpleNamespace(pytesseract=types.SimpleNamespace(tesseract_cmd="tesseract")))
    monkeypatch.setattr(bge.subprocess, "run", _run)
    return runs


def test_model_check_reads_tesseracts_own_listing_once(monkeypatch):
    """pytesseract.get_languages() drops every name that is not lowercase letters, so it never
    reports script/Fraktur; the listing is read directly."""
    runs = _fake_tesseract(
        monkeypatch,
        'List of available languages in "/usr/share/tesseract-ocr/5/tessdata/" (5):\n'
        "deu\nfra\nita\nosd\nscript/Fraktur\n",
    )
    assert bge._ocr_model_installed() is True
    assert bge._ocr_model_installed() is True
    assert runs == [["tesseract", "--list-langs"]]


def test_model_check_finds_a_listing_printed_on_stderr(monkeypatch):
    _fake_tesseract(monkeypatch, "List of available languages (2):\ndeu\nscript/Fraktur\n", on="stderr")
    assert bge._ocr_model_installed() is True


def test_model_check_is_false_on_a_host_without_the_model(monkeypatch):
    _fake_tesseract(monkeypatch, 'List of available languages in "/x/" (3):\ndeu\nfra\nita\n')
    assert bge._ocr_model_installed() is False


def test_model_check_is_false_when_tesseract_cannot_be_run(monkeypatch):
    _fake_tesseract(monkeypatch, "")

    def _boom(cmd, **kw):
        raise FileNotFoundError("tesseract")

    monkeypatch.setattr(bge.subprocess, "run", _boom)
    assert bge._ocr_model_installed() is False


def test_normalisation_touches_only_ocr_artefacts():
    assert bge._normalise_ocr("Bundes-\nGericht und Art. 4 < 5 > 3") == "Bundes-\nGericht und Art. 4 < 5 > 3"
    # Line-end hyphens are not joined: the running header of section III ends in one and is
    # followed by unrelated body text (measured on BGE 35 I 773: "Schuldbetreibungs-" / "gane de").
    for printed in ("Schuldbetreibungs-\nund Konkurskammer", "Schuldbetreibungs-\ngane de son président",
                    "correc-\ntionnel de la Sarine"):
        assert bge._normalise_ocr(printed) == printed
    assert bge._normalise_ocr("ſi< als condictio") == "sich als condictio"
