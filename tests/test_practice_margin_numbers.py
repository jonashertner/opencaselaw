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
    pdf = _bsv_pdf([(0, "1"), (3, "2*"), (6, "3"), (8, "3.1*")])
    text, n = extract_with_margin_numbers(pdf)
    assert n == 4
    lines = text.splitlines()
    assert lines[lines.index("Rz 2*") + 1].endswith("Absatz 3.")
    assert lines[lines.index("Rz 3.1*") + 1].endswith("Absatz 8.")
    assert "2*" not in [ln.strip() for ln in lines]


def _pages(specs) -> bytes:
    """specs: [(width, height, heading, [(line, number)], number_x[, body_x])]
    with the body at body_x (default 85, or 142 beside a left column) and
    the numbers in a column at number_x."""
    doc = fitz.open()
    for w, h, heading, nums, nx, *bx in specs:
        page = doc.new_page(width=w, height=h)
        y0 = 100
        bx = bx[0] if bx else (85 if nx > 300 else 142)
        if heading:
            page.insert_text((bx, 80), heading, fontsize=11)
        for k in range(8):
            page.insert_text((bx, y0 + 30 * k), f"{BODY} Absatz {k}.", fontsize=10)
        for k, t in nums:
            page.insert_text((nx, y0 + 30 * k), t, fontsize=10)
    return doc.tobytes()


def test_annex_template_rows_are_not_rz():
    """FINMA-RS 2016/1 (live run 2026-11-20): the disclosure templates in
    "Anhang 2" print row numbers at the table's left edge on page after
    page; they were labelled "Anhang 2, Rz 1", "Anhang 2, Rz 2", ..."""
    main = [(595, 842, None, [(0, "1"), (3, "2"), (6, "3")], 530),
            (595, 842, None, [(1, "4"), (4, "5")], 530)]
    tables = [(595, 842, "Anhang 2", [(0, "1"), (2, "2"), (4, "3"), (6, "4")], 100)] * 3
    text, n = extract_with_margin_numbers(_pages(main + tables))
    assert n == 5 and "Anhang 2, Rz" not in text


def test_annex_restarts_numbering_once():
    main = [(595, 842, None, [(0, "1"), (3, "2"), (6, "3")], 530)]
    annex = [(595, 842, "Anhang 1", [(0, "1"), (3, "2"), (6, "3")], 530)]
    text, n = extract_with_margin_numbers(_pages(main + annex + annex))
    assert n == 6
    assert text.count("Anhang 1, Rz 1") == 1


def test_annex_field_numbers_01_02_are_not_rz():
    """BSV RWL (6857 v1/v2) Anhang IV: a record layout numbers its fields
    "01", "02", ... under the annex heading; they restarted the Rz."""
    main = [(595, 842, None, [(0, "1001"), (3, "1002"), (6, "1003")], 530),
            (595, 842, None, [(1, "1004"), (4, "1005")], 530)]
    layout = [(595, 842, "Anhang IV", [(0, "01"), (2, "02"), (4, "03"), (6, "04")], 530)]
    text, n = extract_with_margin_numbers(_pages(main + layout))
    assert n == 5 and "Anhang IV, Rz" not in text


def test_zero_padded_main_rz_kept():
    """BSV KSRA (6427) prints its own Rz as "01" ... "09"."""
    text, n = extract_with_margin_numbers(_bsv_pdf([(0, "01"), (3, "02"), (6, "03")]))
    assert n == 3 and "Rz 02" in text.splitlines()


def test_landscape_pages_continue_the_main_sequence():
    """FINMA-RS 2018/2: the Rz column moves from x 547 (portrait) to 722
    (landscape); the landscape pages' Rz 35-80 continue the main text's."""
    pdf = _pages([(595, 842, None, [(0, "1"), (3, "2"), (6, "3")], 530),
                  (842, 595, None, [(0, "4"), (3, "5"), (6, "6")], 766, 321)])
    text, n = extract_with_margin_numbers(pdf)
    assert n == 6 and "Rz 5" in text.splitlines()


def test_landscape_table_cells_are_not_rz():
    """FINMA-RS 2016/1 Anhang 1: a landscape overview table; its cells
    "1", "2" stand far right of the body text, not in the Rz column."""
    main = [(595, 842, None, [(0, "1"), (3, "2"), (6, "3")], 530),
            (595, 842, None, [(1, "4"), (4, "5")], 530)]
    table = [(842, 595, "Anhang 1", [(0, "1"), (2, "2"), (4, "3")], 760)]
    text, n = extract_with_margin_numbers(_pages(main + table))
    assert n == 5 and "Anhang 1, Rz" not in text


def test_section_numbers_beside_the_rz_column():
    """FINMA-RS 2015/1 balance-sheet pages: section numbers ("3.1") stand in
    the left margin, the Rz (118-121) in the right one. Read as one column
    they never rose and the page kept bare numbers."""
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    for k in range(5):
        y = 120 + 25 * k
        page.insert_text((85, y), f"3.{k}" if k else "3.", fontsize=10)
        page.insert_text((133, y), f"Eventualverpflichtungen gegenüber Dritten, Position {k}", fontsize=10)
        page.insert_text((520, y), str(1 + k), fontsize=10)
    text, n = extract_with_margin_numbers(doc.tobytes())
    lines = text.splitlines()
    assert n == 5
    assert lines[lines.index("Rz 2") + 1].strip() == "3.1"    # the row's first line
    assert "3.2" in [ln.strip() for ln in lines]                 # section numbers stay text


def test_toc_word_in_body_text_is_no_toc():
    """"Indice, il faut ..." (FINMA-RS 2008/18 fr) opens a body line; the
    page was taken for a table of contents and kept bare numbers."""
    def indice(page):
        page.insert_text((142, 320), "Indice, il faut s’assurer qu’il existe une large concordance",
                         fontsize=11)
    pdf = _bsv_pdf([(0, "1001"), (4, "1002"), (7, "1003")], extra=indice)
    assert extract_with_margin_numbers(pdf)[1] == 3


def test_wide_footnotes_do_not_hide_the_rz_column():
    """FINMA-RS 2013/3: the footnotes run wider than the body, into the Rz
    column, which then no longer counted as margin."""
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    for k in range(10):
        page.insert_text((90, 100 + 16 * k), f"{BODY} {k}.", fontsize=10)
    for k, t in [(0, "1"), (4, "1.1*"), (8, "2")]:
        page.insert_text((505, 100 + 16 * k), t, fontsize=10)
    for k in range(4):
        page.insert_text((90, 700 + 10 * k), f"{k + 1} Bei systemisch bedeutsamen Finanzmarktinfrastrukturen "
                         "kann die SNB ebenfalls solche Hinweise geben, die zu berücksichtigen sind.",
                         fontsize=8)
    assert extract_with_margin_numbers(doc.tobytes())[1] == 3


def test_formula_subscript_beside_rz_is_no_footnote():
    """FINMA-RS 2017/7 Rz 87 stands beside a formula whose subscript "1/2"
    is nearer than the body-size line; the page was refused as footnotes."""
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    for k in range(10):
        # the formula row's body line sits 4 pt below its Rz
        page.insert_text((142, 100 + 16 * k + (4 if k == 4 else 0)), f"{BODY} Absatz {k}.",
                         fontsize=11)
    for k, t in [(0, "1"), (2, "2"), (4, "3"), (7, "4")]:
        page.insert_text((85, 100 + 16 * k), t, fontsize=11)
    page.insert_text((300, 100 + 16 * 4 - 1), "1/2", fontsize=6)
    text, n = extract_with_margin_numbers(doc.tobytes())
    lines = text.splitlines()
    assert n == 4 and lines[lines.index("Rz 3") + 1].endswith("Absatz 4.")


def test_last_rz_with_nothing_beside_it_ends_the_page():
    """FINMA-RS 2020/1 Rz 89 stands at the foot of the page; its paragraph
    starts on the next. The page was refused."""
    pdf = _bsv_pdf([(0, "1001"), (4, "1002"), (13, "1003")])
    text, n = extract_with_margin_numbers(pdf)
    assert n == 3 and text.splitlines()[-1] == "Rz 1003"


def test_journal_toc_page_numbers_are_not_rz():
    """AHI-Praxis issues open with their table of contents: entry titles
    with page numbers in the right margin (153, 154, 155, 159, ...)."""
    doc = fitz.open()
    page = doc.new_page(width=419, height=595)
    for k, num in enumerate(("153", "154", "155", "159", "161", "162", "165", "168")):
        y = 70 + 30 * k
        page.insert_text((84, y), f"AHV: Beiträge der Nichterwerbstätigen, Fall {k}", fontsize=9)
        page.insert_text((84, y + 10), "Urteil des EVG vom 29. Oktober 2003 i. Sa. R. D.", fontsize=9)
        page.insert_text((364, y + 10), num, fontsize=9)
    assert extract_with_margin_numbers(doc.tobytes()) is None


def test_asterisk_after_a_space():
    """FINMA-RS 2008/19 prints amended Rz as "319 *"; the plain ones beside
    them (320, 322) skipped every other number and failed."""
    pdf = _bsv_pdf([(0, "1 *"), (2, "2"), (4, "3 *"), (7, "4")])
    text, n = extract_with_margin_numbers(pdf)
    assert n == 4 and {"Rz 1*", "Rz 3*"} <= set(text.splitlines())


def test_figure_table_is_not_rz():
    """BSV Beitragstabellen (6139): income brackets 45.20, 45.60, 46.10 down
    the margin rose like Rz and were placed, up to 547 in one version."""
    pdf = _bsv_pdf([(k, f"{45 + k // 2}.{(k % 2) * 40 + 20}") for k in range(8)])
    assert extract_with_margin_numbers(pdf) is None


def test_short_run_not_where_numbering_starts_is_not_rz():
    """BSV WML: worked examples "2.1" ... "2.9", one per page, and a list of
    fund numbers (28, 32, 33, 34) chained like Rz. A document's Rz begin
    at 1, at a chapter's 1001, or at an inserted ".1" series."""
    examples = [(595, 842, None, [(2, f"2.{k}")], 60, 142) for k in range(1, 10)]
    assert extract_with_margin_numbers(_pages(examples)) is None
    funds = _bsv_pdf([(0, "28"), (2, "32"), (4, "33"), (6, "34")])
    assert extract_with_margin_numbers(funds) is None


def test_row_numbers_beside_figures_are_not_rz():
    """BSV IV-Taggeldtabellen (6880): rows 1-407 beside day counts 365,
    730, 1 095. They follow on and start at 1, but stand beside figures."""
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    for k in range(4):
        page.insert_text((142, 80 + 14 * k), f"{BODY} Absatz {k}.", fontsize=11)
    for k in range(1, 9):
        y = 160 + 16 * k
        page.insert_text((85, y), str(k), fontsize=11)
        page.insert_text((142, y), f"{365 * k:,}".replace(",", " "), fontsize=11)
        page.insert_text((260, y), f"{12.5 * k:.2f}", fontsize=11)
    assert extract_with_margin_numbers(doc.tobytes()) is None


def test_line_numbers_are_not_rz():
    """BSV Rententabellen (6850) number every line of a passage; the
    numbers fall mid-sentence."""
    words = ("ihres Jahrganges ab, so entspricht die Teilrente einem nach Artikel 52",
             "vorgegebenen Bruchteil der Vollrente. Bevor die zutreffende Rentenskala",
             "entnommen werden kann, muss die Beitragsdauer des Jahrganges aufgrund",
             "der Jahrgangstabellen bestimmt werden. Die folgenden Beispiele zeigen,")
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    for k in range(8):
        page.insert_text((142, 100 + 14 * k), words[k % 4], fontsize=11)
        page.insert_text((85, 100 + 14 * k), str(k + 1), fontsize=11)
    assert extract_with_margin_numbers(doc.tobytes()) is None


def test_repealed_rz_with_lowercase_stub_kept():
    """"aufgehoben" / "abrogés" beside a repealed Rz starts in lowercase
    but is no line-numbered passage."""
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    for k in range(8):
        text = "aufgehoben" if k % 2 else f"{BODY} Absatz {k}."
        page.insert_text((142, 100 + 16 * k), text, fontsize=11)
        page.insert_text((85, 100 + 16 * k), str(1001 + k), fontsize=11)
    assert extract_with_margin_numbers(doc.tobytes())[1] == 8


def test_toc_with_consecutive_chapter_numbers():
    """BSV Rententabellen: "Inhaltsverzeichnis" over chapters 1-13 that run
    on without gaps; the "consecutive" exemption is for an "Inhalt"
    column head only."""
    def toc(page):
        page.insert_text((142, 70), "Inhaltsverzeichnis", fontsize=11)
    pdf = _bsv_pdf([(0, "1"), (2, "2"), (4, "3"), (6, "4")], extra=toc)
    assert extract_with_margin_numbers(pdf) is None


def test_split_range_is_one_marker():
    pdf = _bsv_pdf([(0, "2001"), (4, "2002.1-"), (5, "2002.4"), (7, "2003")])
    text, n = extract_with_margin_numbers(pdf)
    assert "Rz 2002.1–2002.4" in text.splitlines() and n == 3


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
    off = S()
    off.margin_numbers_off = True                  # a journal: plain text
    assert off._extract(pdf2) == extract_pdf_text(pdf2)
