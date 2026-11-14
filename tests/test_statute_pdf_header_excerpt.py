"""get_law(as_of=...) on PDF-only editions: the article is found by its header.

Reported 2026-11-14 on the SchKG. In plain PDF text the footnote mark is
glued to the article number, so Art. 2 with footnote 5 reads "Art. 25":
  - get_law(SchKG, 25, as_of=2020-06-01) returned the text of Art. 2 (the
    real Art. 25 is repealed and empty) with text_status "ok";
  - get_law(SchKG, 2, ...) returned Art. 2 of the final provisions of the
    1994 amendment, the longest block under that number.
Measured on six editions, 0.6-6.5 % of article numbers returned another
provision's text that way (ZPO 25 of 416, ZGB 74 of 1,133).

The extractor now reads the fonts: a bold "Art. N" opens a header, the small
raised digits after it are the footnote. The first header with the number is
the main body's. Offline: the PDFs are built here with pymupdf.
"""
import sys
from pathlib import Path

import pytest
import requests

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import mcp_server as m  # noqa: E402

fitz = pytest.importorskip("fitz")

BOLD, PLAIN = "hebo", "helv"


def _pdf(rows) -> bytes:
    """One page; each row is a list of (text, font, size, raise_pt) runs set
    on one baseline, left to right."""
    doc = fitz.open()
    page = doc.new_page()
    y = 72.0
    for row in rows:
        x = 72.0
        for text, font, size, lift in row:
            page.insert_text((x, y - lift), text, fontname=font, fontsize=size)
            x += fitz.get_text_length(text, fontname=font, fontsize=size)
        y += 14.0
    data = doc.tobytes()
    doc.close()
    return data


def _header(num, footnote=None, suffix=None):
    row = [(f"Art. {num}", BOLD, 9, 0)]
    if suffix:
        row.append((suffix, PLAIN, 9, 0))
    if footnote:
        row.append((str(footnote), PLAIN, 6.5, 2))
    return row


def _body(text):
    return [(text, PLAIN, 9, 0)]


# The SchKG shape: Art. 2 carries footnote 5, Art. 25 is repealed (header and
# footnote only), the final provisions restart at Art. 1, the table of
# contents lists the numbers in plain type.
SCHKG_ROWS = [
    _header(1), _body("1 Das Gebiet jedes Kantons bildet einen oder mehrere Kreise."),
    _header(2, footnote=5), _body("1 In jedem Betreibungskreis besteht ein Betreibungsamt."),
    _header(4, footnote=8, suffix="a"), _body("1 Die Betreibungsämter leisten einander Rechtshilfe."),
    _header(24), _body("Die Kantone bezeichnen die Depositenanstalten fuer die Hinterlegung."),
    _header(25, footnote=38),
    _header(26, footnote=39), _body("1 Die Kantone koennen an die fruchtlose Pfaendung Folgen knuepfen."),
    _body("Schlussbestimmungen der Aenderung vom 16. Dezember 1994"),
    _header(1), _body("Der Bundesrat und die Kantone erlassen die Ausfuehrungsbestimmungen."),
    _header(2), _body("1 Die Verfahrensvorschriften dieses Gesetzes und seine Ausfuehrungsbestimmungen "
                      "sind mit ihrem Inkrafttreten anwendbar."),
    _header(3), _body("Dieses Gesetz untersteht dem fakultativen Referendum und tritt in Kraft."),
    _body("Inhaltsverzeichnis"),
    _body("Art. 1"), _body("Art. 2"), _body("Art. 25"), _body("Art. 26"),
]


@pytest.fixture(scope="module")
def schkg():
    text, pages, headers = m._extract_pdf_text(_pdf(SCHKG_ROWS))
    return text, headers


def test_footnote_mark_is_not_read_as_part_of_the_article_number(schkg):
    text, headers = schkg
    assert [num for _, num in headers] == ["1", "2", "4a", "24", "25", "26", "1", "2", "3"]
    lines = [ln.strip() for ln in text.split("\n")]
    assert "Art. 2" in lines and "Art. 4a" in lines
    # Art. 2 + footnote 5 used to read "Art. 25", Art. 25 + footnote 38 "Art. 2538".
    assert "Art. 2538" not in text and "Art. 4a8" not in text
    assert lines.count("Art. 25") == 2            # the repealed header and the TOC entry
    for offset, num in headers:
        assert text[offset:].lstrip().startswith(f"Art. {num}")


def test_table_of_contents_entries_are_not_headers(schkg):
    text, headers = schkg
    toc = text.index("Inhaltsverzeichnis")
    assert all(offset < toc for offset, _ in headers)


def test_repealed_article_is_not_answered_with_its_neighbours_text(schkg):
    text, headers = schkg
    picked = m._pdf_header_excerpt(text, "25", headers)
    assert picked["text"] == "Art. 25"
    assert picked["candidates"] == 1 and picked["main_body"] is True
    assert m._pdf_excerpt_status(picked["text"]) == "empty"


def test_main_body_article_wins_over_a_longer_final_provision(schkg):
    text, headers = schkg
    picked = m._pdf_header_excerpt(text, "2", headers)
    assert "In jedem Betreibungskreis" in picked["text"]
    assert "Verfahrensvorschriften" not in picked["text"]
    assert picked["candidates"] == 2 and picked["main_body"] is True


def test_block_stops_at_the_next_header_of_any_article(schkg):
    text, headers = schkg
    picked = m._pdf_header_excerpt(text, "1", headers)
    assert picked["text"].startswith("Art. 1")
    assert "Betreibungsamt" not in picked["text"]


def test_article_found_only_after_the_restart_is_marked_as_a_later_block(schkg):
    text, headers = schkg
    picked = m._pdf_header_excerpt(text, "3", headers)
    assert "fakultativen Referendum" in picked["text"]
    assert picked["candidates"] == 1 and picked["main_body"] is False


def test_unknown_article_and_letter_case(schkg):
    text, headers = schkg
    assert m._pdf_header_excerpt(text, "7", headers) is None
    assert m._pdf_header_excerpt(text, "250", headers) is None
    assert "Rechtshilfe" in m._pdf_header_excerpt(text, "4A", headers)["text"]


def test_headers_are_trusted_only_when_they_cover_the_edition():
    text = "Art. 1\nText eins.\nArt. 2\nText zwei.\nArt. 3\nText drei.\nArt. 4\nText vier.\n"
    assert m._pdf_headers_usable(text, [[0, "1"], [18, "2"], [36, "3"], [54, "4"]])
    assert m._pdf_headers_usable(text, [[0, "1"], [18, "2"]])
    assert not m._pdf_headers_usable(text, [[0, "1"]])       # one stray bold line
    assert not m._pdf_headers_usable(text, [])


def test_pdf_without_bold_headers_yields_no_header_list():
    rows = [_body("Art. 1"), _body("Der Bundesrat erlaesst die Ausfuehrungsbestimmungen."),
            _body("Art. 2"), _body("Dieses Gesetz tritt am 1. Januar in Kraft.")]
    text, pages, headers = m._extract_pdf_text(_pdf(rows))
    assert headers == [] and pages == 1
    assert "Art. 1" in text and "Art. 2" in text


# --- end to end through get_law ----------------------------------------------

SCHKG_WORK = "https://fedlex.data.admin.ch/eli/cc/11/529_488_529"
SNAP_2020 = SCHKG_WORK + "/20200101"
IN_FORCE = "https://fedlex.data.admin.ch/vocabulary/enforcement-status/0"
FMT = "https://fedlex.data.admin.ch/vocabulary/user-format/"


def _stub(monkeypatch, cache_keys=None):
    def fake(query, timeout=15):
        if "historicalLegalId" in query:
            return [{"work": SCHKG_WORK, "eif": "1892-01-01", "status": IN_FORCE}]
        if "isMemberOf" in query:
            return [{"snapshot": SNAP_2020, "date": "2020-01-01"}] if f"<{SCHKG_WORK}>" in query else []
        if "isRealizedBy" in query:
            if f"<{SNAP_2020}>" in query:
                return [{"fmt": FMT + "pdf-a", "url": "https://fedlex.data.admin.ch/filestore/schkg.pdf",
                         "title": "Bundesgesetz über Schuldbetreibung und Konkurs", "abbr": "SchKG"}]
            return []
        raise AssertionError("unexpected SPARQL: " + query)

    class _Resp:
        status_code = 200
        content = _pdf(SCHKG_ROWS)

    monkeypatch.setattr(m, "_fedlex_sparql_select", fake)
    monkeypatch.setattr(requests, "get", lambda url, timeout=30: _Resp())
    monkeypatch.setattr(m, "_lexfind_cache_get", lambda k: None)
    monkeypatch.setattr(m, "_lexfind_cache_set",
                        (lambda k, v: cache_keys.append((k, v))) if cache_keys is not None
                        else (lambda k, v: None))


def test_get_law_art_25_is_flagged_empty_not_served_as_art_2(monkeypatch):
    _stub(monkeypatch)
    res = m.get_law(sr_number="281.1", article="25", as_of="2020-06-01")
    assert res["text_source"] == "fedlex_pdf" and res["snapshot_date"] == "2020-01-01"
    assert res["text_status"] == "empty"
    (art,) = res["articles"]
    assert art["text"] == "Art. 25" and "Betreibungsamt" not in art["text"]
    assert res["note"].startswith("UNRESOLVED")
    assert res["excerpt_block"] == "main_body" and "excerpt_note" not in res


def test_get_law_art_2_is_the_main_body_article_and_says_there_is_another(monkeypatch):
    _stub(monkeypatch)
    res = m.get_law(sr_number="281.1", article="2", as_of="2020-06-01")
    assert res["text_status"] == "ok"
    (art,) = res["articles"]
    assert art["text"].startswith("Art. 2 ") or art["text"].startswith("Art. 2\n")
    assert "In jedem Betreibungskreis" in art["text"]
    assert "Verfahrensvorschriften" not in art["text"]
    assert res["excerpt_candidates"] == 2 and res["excerpt_block"] == "main_body"
    assert "first, in the act's main body" in res["excerpt_note"]
    assert res["excerpt_note"] in m._format_get_law_response(res)


def test_get_law_final_provision_only_article_is_labelled(monkeypatch):
    _stub(monkeypatch)
    res = m.get_law(sr_number="281.1", article="3", as_of="2020-06-01")
    assert res["text_status"] == "ok" and res["excerpt_block"] == "later_block"
    assert "not to the act's main body" in res["excerpt_note"]


def test_edition_cache_carries_the_headers_under_a_new_key(monkeypatch):
    stored = []
    _stub(monkeypatch, cache_keys=stored)
    m.get_law(sr_number="281.1", article="2", as_of="2020-06-01")
    editions = {k: v for k, v in stored if k.startswith("hist_edition:")}
    (key,) = editions
    assert key == "hist_edition:v2:281.1:2020-01-01:de:pdf-a"
    assert [num for _, num in editions[key]["headers"]][:3] == ["1", "2", "4a"]
    assert any(k.startswith("hist_law:v5:281.1:2:de:") for k, _ in stored)
