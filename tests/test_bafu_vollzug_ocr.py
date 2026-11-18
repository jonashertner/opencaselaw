"""BAFU carries old Vollzugshilfen as image-only scans.

"Altlasten: erfassen, bewerten, sanieren" (44 pages, KODAK Capture, no text
layer) was dropped as "empty body" on 16 weekly runs to 2026-11-14. OCR
recovers ~65k characters of it; without OCR it must still be indexed under
its title rather than vanish.
"""
from scrapers.practice.bafu_vollzugshilfen import BafuVollzugshilfenScraper


def test_bafu_ocrs_scans_and_keeps_title_fallback():
    assert BafuVollzugshilfenScraper.OCR_SCANS is True
    assert BafuVollzugshilfenScraper.NO_TEXT_LAYER_BODY
