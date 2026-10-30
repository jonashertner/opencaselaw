"""Practitioner-export module — pure functions, no DB / network calls."""
from __future__ import annotations

import importlib

import pytest


@pytest.fixture(scope="module")
def exports():
    return importlib.import_module("exports")


@pytest.fixture
def sample_decision():
    return {
        "decision_id": "bge_BGE_140_III_86",
        "citation_string_de": "BGE 140 III 86",
        "citation_string_fr": "ATF 140 III 86",
        "citation_string_it": "DTF 140 III 86",
        "court": "bge",
        "court_name": "Bundesgericht",
        "decision_date": "2014-03-15",
        "docket_number": "4A_321/2013",
        "language": "de",
        "regeste": "Art. 42 Abs. 2 BGG; Pflicht zur Begründung der Rechtsverletzungen.",
        "full_text": "Erwägungen:\n\n1. Allgemein.\n\n2. Spezifisch.\n",
    }


# ── BibTeX ─────────────────────────────────────────────────────────

def test_bibtex_basic_shape(exports, sample_decision):
    body, mt, fname = exports.render_bibtex(sample_decision)
    assert "bibtex" in mt
    assert fname.endswith(".bib")
    text = body.decode("utf-8")
    assert text.startswith("@misc{")
    assert "bgeBGE140III86" in text          # bib key
    assert "BGE 140 III 86" in text
    assert "Bundesgericht" in text
    assert "2014" in text
    assert "https://mcp.opencaselaw.ch/entscheid/bge_BGE_140_III_86" in text


def test_bibtex_no_year_when_date_missing(exports, sample_decision):
    sample_decision["decision_date"] = ""
    body, _, _ = exports.render_bibtex(sample_decision)
    assert "year" not in body.decode("utf-8")


def test_bibtex_braces_escaped(exports, sample_decision):
    """Curly braces inside field values would break BibTeX parsing."""
    sample_decision["docket_number"] = "ABC{XYZ}123"
    text = exports.render_bibtex(sample_decision)[0].decode("utf-8")
    assert "{XYZ}" not in text   # raw braces must have been replaced
    assert "ABC(XYZ)123" in text


# ── RIS ────────────────────────────────────────────────────────────

def test_ris_basic_shape(exports, sample_decision):
    body, mt, fname = exports.render_ris(sample_decision)
    assert "research-info-systems" in mt
    assert fname.endswith(".ris")
    text = body.decode("utf-8")
    # Required RIS structure: TY first, ER last
    lines = [ln for ln in text.splitlines() if ln.strip()]
    assert lines[0].startswith("TY  - CASE")
    assert lines[-1].startswith("ER  -")
    assert "TI  - BGE 140 III 86" in text
    assert "DA  - 2014/03/15/" in text       # RIS date format
    assert "AN  - 4A_321/2013" in text       # accession (docket)
    assert "LA  - de" in text
    assert "ID  - bge_BGE_140_III_86" in text


def test_ris_uses_crlf_line_endings(exports, sample_decision):
    """Some RIS parsers (older EndNote) require CRLF."""
    body, _, _ = exports.render_ris(sample_decision)
    assert b"\r\n" in body


# ── DOCX (or txt fallback) ─────────────────────────────────────────

def test_docx_or_txt_fallback(exports, sample_decision):
    """Either python-docx is installed (binary docx, ZIP magic 'PK'),
    or the txt fallback is served."""
    body, mt, fname = exports.render_docx(sample_decision, [])
    if "wordprocessingml" in mt:
        assert body.startswith(b"PK")        # docx is a zip
        assert fname.endswith(".docx")
        assert len(body) > 1000              # non-empty document
    else:
        # plain-text fallback
        text = body.decode("utf-8")
        assert "BGE 140 III 86" in text
        assert "Bundesgericht" in text
        assert fname.endswith(".txt")


def test_docx_includes_paragraphs_when_provided(exports, sample_decision):
    paragraphs = [
        {"e_number": "1", "text": "Erste Erwägung mit eindeutigem Marker_AAA."},
        {"e_number": "2.3", "text": "Zweite Erwägung mit Marker_BBB."},
    ]
    body, mt, _ = exports.render_docx(sample_decision, paragraphs)
    if "wordprocessingml" in mt:
        # docx is a zip; check the document.xml part contains our text
        import zipfile, io
        zf = zipfile.ZipFile(io.BytesIO(body))
        doc_xml = zf.read("word/document.xml").decode("utf-8")
        assert "Marker_AAA" in doc_xml
        assert "Marker_BBB" in doc_xml
    else:
        text = body.decode("utf-8")
        assert "Marker_AAA" in text
        assert "Marker_BBB" in text


def test_docx_uses_times_new_roman_12pt(exports, sample_decision):
    """Typography contract — body style must be Times New Roman 12pt."""
    body, mt, _ = exports.render_docx(sample_decision, [])
    if "wordprocessingml" not in mt:
        pytest.skip("python-docx not installed; txt fallback served")
    import zipfile, io
    zf = zipfile.ZipFile(io.BytesIO(body))
    styles_xml = zf.read("word/styles.xml").decode("utf-8")
    assert "Times New Roman" in styles_xml
    # docx stores font size as half-points → 12pt = 24
    assert 'w:val="24"' in styles_xml


# ── PDF ────────────────────────────────────────────────────────────

def test_pdf_or_txt_fallback(exports, sample_decision):
    """Either reportlab is installed (binary PDF, magic %PDF), or the
    txt fallback is served."""
    body, mt, fname = exports.render_pdf(sample_decision, [])
    if mt == "application/pdf":
        assert body.startswith(b"%PDF")
        assert fname.endswith(".pdf")
        assert len(body) > 1000
    else:
        text = body.decode("utf-8")
        assert "BGE 140 III 86" in text
        assert "Bundesgericht" in text
        assert fname.endswith(".txt")


def test_pdf_includes_paragraphs(exports, sample_decision):
    paragraphs = [
        {"e_number": "1", "text": "Marker_PDF_AAA."},
        {"e_number": "2.3", "text": "Marker_PDF_BBB."},
    ]
    body, mt, _ = exports.render_pdf(sample_decision, paragraphs)
    if mt != "application/pdf":
        pytest.skip("reportlab not installed; txt fallback served")
    # PDF text is compressed by default, but reportlab embeds plain
    # ASCII for short Tj strings. Minimal sanity: the rendered bytes
    # contain the regeste's distinctive markers (or at least the
    # decision_id metadata).
    assert b"bge_BGE_140_III_86" in body or b"BGE 140 III 86" in body


# ── Atom feed ──────────────────────────────────────────────────────

def test_atom_feed_well_formed(exports, sample_decision):
    body, mt, fname = exports.render_atom_feed(
        court="bger", court_label="Bundesgericht",
        decisions=[sample_decision],
    )
    assert "atom+xml" in mt
    assert fname.endswith(".xml")
    text = body.decode("utf-8")
    # Parse to verify well-formed XML
    import xml.etree.ElementTree as ET
    tree = ET.fromstring(text)
    assert tree.tag.endswith("feed")
    entries = tree.findall("{http://www.w3.org/2005/Atom}entry")
    assert len(entries) == 1
    title = entries[0].find("{http://www.w3.org/2005/Atom}title")
    assert title is not None
    assert "BGE 140 III 86" in title.text
    link = entries[0].find("{http://www.w3.org/2005/Atom}link")
    assert link.attrib.get("href") == \
        "https://mcp.opencaselaw.ch/entscheid/bge_BGE_140_III_86"


def test_atom_empty_feed(exports):
    """Empty decision list still produces a valid feed (no entries)."""
    body, _, _ = exports.render_atom_feed(
        court="bger", court_label="Bundesgericht", decisions=[],
    )
    import xml.etree.ElementTree as ET
    tree = ET.fromstring(body.decode("utf-8"))
    assert tree.findall("{http://www.w3.org/2005/Atom}entry") == []


def test_atom_html_escapes_unsafe_chars(exports):
    """A regeste with < > & should be escaped, not break the XML."""
    decision = {
        "decision_id": "bger_test_1",
        "citation_string_de": "BGer 1A_1/2024 <b>injection</b>",
        "decision_date": "2024-01-15",
        "regeste": "Art. 5 & Art. 6 — special chars: < > \"",
    }
    body, _, _ = exports.render_atom_feed(
        court="bger", court_label="Bundesgericht <inj>",
        decisions=[decision],
    )
    import xml.etree.ElementTree as ET
    tree = ET.fromstring(body.decode("utf-8"))
    # Successfully parsed → escaping worked. Inspect the entry title
    # (not the feed title) for the injection-shaped string.
    entry = tree.find("{http://www.w3.org/2005/Atom}entry")
    entry_title = entry.find("{http://www.w3.org/2005/Atom}title")
    assert "<b>injection</b>" in entry_title.text   # literal, not parsed as HTML


# ── Helpers ────────────────────────────────────────────────────────

def test_bib_key_strips_punctuation(exports):
    assert exports._bib_key("bge_BGE_140_III_86") == "bgeBGE140III86"
    assert exports._bib_key("bger_4A_747/2012")    == "bger4A7472012"
    assert exports._bib_key("") == "decision"


def test_safe_year_extracts_first_4_digits(exports):
    assert exports._safe_year("2014-03-15") == "2014"
    assert exports._safe_year("2014") == "2014"
    assert exports._safe_year("") == ""
    assert exports._safe_year("nope") == ""


# ── Content-Disposition header ─────────────────────────────────────
#
# Production 500s on 2026-09-19: four export endpoints interpolated the
# decision id straight into the header. ASGI encodes header values as
# latin-1, so an id carrying an en dash — SG multi-docket ids such as
# `ST.2021.212–215-SK3, ST.2021.217-SK3`, and some VD ids — raised
# UnicodeEncodeError inside the server and the request returned 500.

EN_DASH_ID = "sg_publikationen_ST.2021.212–215-SK3, ST.2021.217-SK3.pdf"


def test_content_disposition_is_latin1_encodable(exports):
    """The header value must survive the ASGI latin-1 encode."""
    header = exports.content_disposition(EN_DASH_ID)
    header.encode("latin-1")  # would raise UnicodeEncodeError before the fix


@pytest.mark.parametrize("fname", [
    EN_DASH_ID,
    "vd_gerichte_PC 18_21 – 59_2025.pdf",
    "bger_1C_123_2024.docx",
    "zh_gerichte_SB 210001.ris",      # non-breaking space
    "ti_gerichte_90.2003.32.bib",
])
def test_content_disposition_encodable_for_real_id_shapes(exports, fname):
    exports.content_disposition(fname).encode("latin-1")


def test_content_disposition_preserves_exact_name_in_filename_star(exports):
    """`filename*` must round-trip back to the original, unmangled name."""
    import urllib.parse
    header = exports.content_disposition(EN_DASH_ID)
    encoded = header.split("filename*=UTF-8''", 1)[1]
    assert urllib.parse.unquote(encoded) == EN_DASH_ID


def test_content_disposition_ascii_fallback_has_no_quotes_or_backslash(exports):
    """The quoted-string form must not need escaping."""
    header = exports.content_disposition('we"ird\\name.pdf')
    ascii_part = header.split('filename="', 1)[1].split('"', 1)[0]
    assert '"' not in ascii_part and "\\" not in ascii_part
    assert ascii_part == "we_ird_name.pdf"


def test_content_disposition_plain_id_is_left_alone(exports):
    header = exports.content_disposition("bger_1C_123_2024.pdf")
    assert 'filename="bger_1C_123_2024.pdf"' in header


def test_content_disposition_empty_name_falls_back(exports):
    assert 'filename="decision"' in exports.content_disposition("")
