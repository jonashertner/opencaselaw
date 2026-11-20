"""Margin numbers (Randziffern) placed next to their paragraph.

User report 2026-11-20: in BSV Weisungen the Rz stood as a block at the page
end (the Rz -> paragraph link was lost); in FINMA Rundschreiben since 2019
they were missing from the text altogether (glyph images, not text).
Synthetic PDFs, built in memory with PyMuPDF: no network, no fixtures on disk.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

fitz = pytest.importorskip("fitz")

from scrapers.practice.base import extract_pdf_text  # noqa: E402
from scrapers.practice.margin_numbers import (  # noqa: E402
    MarginNumbersMixin, extract_with_margin_numbers,
)

BODY = "Die Versicherungsunterstellung ergibt sich aus den anwendbaren Bestimmungen."


def _bsv_pdf(margin, *, body_lines=10, margin_size=11, extra=None) -> bytes:
    """Body column at x=142; `margin` = [(line_index, text)] in a left column
    at x=85, written AFTER the body so plain extraction puts it at the end."""
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    for k in range(body_lines):
        page.insert_text((142, 100 + 16 * k), f"{BODY} Absatz {k}.", fontsize=11)
    for k, t in margin:
        page.insert_text((85, 100 + 16 * k), t, fontsize=margin_size)
    if extra:
        extra(page)
    return doc.tobytes()


def test_bsv_layout_rz_before_its_paragraph():
    pdf = _bsv_pdf([(0, "1001"), (1, "1/26"), (4, "1002"), (7, "1002.1")])
    plain = extract_pdf_text(pdf)
    assert plain.rstrip().endswith("1002.1")            # the reported defect
    text, n = extract_with_margin_numbers(pdf)
    assert n == 3
    lines = text.splitlines()
    i = lines.index("Rz 1001 (1/26)")
    assert lines[i + 1].endswith("Absatz 0.")
    assert lines[lines.index("Rz 1002") + 1].endswith("Absatz 4.")
    assert lines[lines.index("Rz 1002.1") + 1].endswith("Absatz 7.")
    assert "1/26" not in [ln.strip() for ln in lines]   # the mark moved into the marker
    # nothing else changed
    rest = [ln for ln in lines if not ln.startswith("Rz ")]
    assert rest == [ln for ln in plain.splitlines()
                    if ln.strip() not in {"1001", "1/26", "1002", "1002.1"}]


def test_falling_column_of_figures_is_not_rz():
    pdf = _bsv_pdf([(0, "300"), (2, "150"), (5, "90")])
    assert extract_with_margin_numbers(pdf) is None


def test_footnote_numbers_are_not_rz():
    def footnotes(page):
        for k, y in enumerate((700, 730, 760), 1):
            page.insert_text((85, y), str(k), fontsize=5)
            page.insert_text((99, y), "Fussnote mit kleinem Text, die keine Randziffer ist.",
                             fontsize=7)
    pdf = _bsv_pdf([(0, "1001"), (4, "1002"), (7, "1003")], extra=footnotes)
    text, n = extract_with_margin_numbers(pdf)
    assert n == 3 and "Rz 1" not in text.splitlines()


def test_table_of_contents_left_alone():
    def toc(page):
        for k in range(4):
            page.insert_text((142, 400 + 16 * k), "Kapitel ........................ 15", fontsize=11)
    pdf = _bsv_pdf([(0, "1"), (4, "2"), (7, "3")], extra=toc)
    assert extract_with_margin_numbers(pdf) is None


@pytest.mark.parametrize("label", ["Cm", "nm.", "Rz"])
def test_toc_number_column_with_language_label_is_not_rz(label):
    """FINMA-RS 2019/1 fr/it (live run 2026-11-20): the TOC prints its Rz
    column beside "Cm" / "nm." labels in the margin; its numbers were placed
    as Rz 1, 19, 20, 70, 102-105 before the "Cm" lines."""
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    for k, (head, nums) in enumerate([("Objet", "1"), ("Champ", "19"), ("Contreparties", "20"),
                                       ("Positions", "70"), ("Réserves", "102"),
                                       ("Disposition transitoire", "105")]):
        y = 120 + 19 * k
        page.insert_text((118, y), f"{head} de la circulaire et autres dispositions", fontsize=10)
        page.insert_text((395, y), label, fontsize=10)
        page.insert_text((445, y), nums, fontsize=10)
    body = doc.new_page(width=595, height=842)
    for k in range(10):
        body.insert_text((85, 100 + 16 * k), f"{BODY} Absatz {k}.", fontsize=10)
    assert extract_with_margin_numbers(doc.tobytes(), images=True) is None


def test_asterisk_marked_rz_placed_and_kept():
    """FINMA marks amended Rz "16*": such pages were refused whole, and on
    mixed pages the starred number stayed a bare line between paragraphs."""
    pdf = _bsv_pdf([(0, "15"), (3, "16*"), (6, "17"), (8, "17.1*")])
    text, n = extract_with_margin_numbers(pdf)
    assert n == 4
    lines = text.splitlines()
    assert lines[lines.index("Rz 16*") + 1].endswith("Absatz 3.")
    assert lines[lines.index("Rz 17.1*") + 1].endswith("Absatz 8.")
    assert "16*" not in [ln.strip() for ln in lines]


def test_split_range_is_one_marker():
    pdf = _bsv_pdf([(0, "2022"), (4, "2023.1-"), (5, "2023.4"), (7, "2024")])
    text, n = extract_with_margin_numbers(pdf)
    assert "Rz 2023.1–2023.4" in text.splitlines() and n == 3


# ── FINMA layout: Rz as soft-masked glyph images in the right margin ──

def _glyph(width: int):
    pm = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, width, 7), True)
    pm.clear_with(0)
    return pm


def _finma_pdf(n_rz: int, *, toc_max: int | None, widths=None) -> bytes:
    doc = fitz.open()
    toc = doc.new_page(width=595, height=842)
    toc.insert_text((85, 100), "Inhaltsverzeichnis", fontsize=11)
    if toc_max is not None:
        toc.insert_text((450, 130), "Rz", fontsize=11)
        toc.insert_text((480, 130), f"1-{toc_max}", fontsize=11)   # base-14 font: no en dash
    widths = widths or [5 if k < 10 else 10 for k in range(1, n_rz + 1)]
    per_page = 8
    for start in range(0, n_rz, per_page):
        page = doc.new_page(width=595, height=842)
        for j, k in enumerate(range(start, min(start + per_page, n_rz))):
            y = 120 + 60 * j
            page.insert_text((85, y), f"{BODY} Abschnitt {k + 1}.", fontsize=10)
            page.insert_text((85, y + 13), "Fortsetzung des Absatzes mit weiterem Text hier.", fontsize=10)
            w = widths[k]
            page.insert_image(fitz.Rect(529 - w, y - 8, 529, y - 1), pixmap=_glyph(w))
    return doc.tobytes()


def test_finma_glyph_images_numbered_when_proven():
    pdf = _finma_pdf(12, toc_max=12)
    assert "Rz" not in extract_pdf_text(pdf).replace("Rz\n", "")
    text, n = extract_with_margin_numbers(pdf, images=True)
    assert n == 12
    lines = text.splitlines()
    for k in (1, 9, 10, 12):
        assert lines[lines.index(f"Rz {k}") + 1].endswith(f"Abschnitt {k}.")


def test_finma_glyph_images_refused_when_toc_disagrees():
    # one Rz more in the TOC than images: an inserted / dropped number
    assert extract_with_margin_numbers(_finma_pdf(12, toc_max=13), images=True) is None


def test_finma_glyph_images_refused_when_widths_disagree():
    # "10.1"-style insertion: a wide glyph where a one-digit Rz is expected
    widths = [5] * 4 + [14] + [5] * 4 + [10] * 3
    assert extract_with_margin_numbers(
        _finma_pdf(12, toc_max=12, widths=widths), images=True) is None


def test_mixin_keeps_plain_text_when_nothing_to_place():
    class Base:
        def _extract(self, pdf_bytes):
            self._last_ocr = False
            return extract_pdf_text(pdf_bytes)

    class S(MarginNumbersMixin, Base):
        pass

    pdf = _bsv_pdf([(0, "300"), (2, "150"), (5, "90")])
    assert S()._extract(pdf) == extract_pdf_text(pdf)
    pdf2 = _bsv_pdf([(0, "1001"), (4, "1002"), (7, "1003")])
    assert "Rz 1002" in S()._extract(pdf2)
