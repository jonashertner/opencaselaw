"""Tests for the verbatim Botschaft corpus pipeline (v0.2).

The v0.2 module wires three pieces together:
  - URL resolution via Fedlex SPARQL (``resolve_manifestation``)
  - Akoma Ntoso XML parser (``parse_akoma_ntoso_xml``)
  - PDF fallback (``parse_botschaft_text`` from v0.1)

These tests don't hit the SPARQL endpoint or the Fedlex filestore — they
unit-test the pure functions and the schema migration.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from search_stack.build_botschaft_corpus import (  # noqa: E402
    SCHEMA_SQL,
    _FORMAT_PRIORITY,
    _PART_SUFFIX_RE,
    bbl_citation,
    bbl_eli_uri,
    ensure_schema,
    fetch_pdf_parts,
    parse_akoma_ntoso_xml,
    parse_botschaft_text,
)


# ── URL helpers ───────────────────────────────────────────────────────


def test_bbl_eli_uri_format() -> None:
    assert bbl_eli_uri(2024, 2945) == "https://fedlex.data.admin.ch/eli/fga/2024/2945"


def test_bbl_citation_format() -> None:
    assert bbl_citation(1999, 6013) == "BBl 1999 6013"


def test_format_priority_includes_xml_an() -> None:
    """xml-an is Akoma Ntoso XML — the cleanest source. Must come first."""
    assert _FORMAT_PRIORITY[0] == "xml-an"
    assert "pdf-a" in _FORMAT_PRIORITY


# ── Akoma Ntoso parser ────────────────────────────────────────────────


_AKN_FIXTURE = b"""<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
<act><body>
<chapter><heading>Allgemeiner Teil</heading>
<p>Das ist ein Einleitungsabsatz mit ausreichend Inhalt zum Testen.</p>
</chapter>
<chapter><heading>Besonderer Teil</heading>
<article eId="art_1"><heading>Artikel 1</heading>
<p>Diese Bestimmung legt den Geltungsbereich fest und gilt fuer alle.</p>
<p>Sie wurde 2024 angepasst um neue Anforderungen zu erfassen.</p>
</article>
<article eId="art_2">
<p>Persoenlicher Anwendungsbereich, definiert die betroffenen Personen.</p>
</article>
<article eId="art_41a">
<p>Letter-suffixed article (41a) - modern revision style.</p>
</article>
</chapter>
</body></act></akomaNtoso>"""


def test_akn_parser_yields_paragraphs() -> None:
    paras = list(parse_akoma_ntoso_xml(_AKN_FIXTURE))
    # five text paragraphs plus the heading row of art_1
    assert len(paras) == 6


def test_akn_parser_assigns_article_anchors() -> None:
    paras = list(parse_akoma_ntoso_xml(_AKN_FIXTURE))
    by_text_prefix = {p["text"][:25]: p for p in paras}
    assert by_text_prefix["Das ist ein Einleitungsab"]["article_anchor"] is None
    assert by_text_prefix["Diese Bestimmung legt den"]["article_anchor"] == "1"
    assert by_text_prefix["Sie wurde 2024 angepasst "]["article_anchor"] == "1"
    assert by_text_prefix["Persoenlicher Anwendungsb"]["article_anchor"] == "2"
    assert by_text_prefix["Letter-suffixed article ("]["article_anchor"] == "41a"


def test_akn_parser_tracks_section_path() -> None:
    paras = list(parse_akoma_ntoso_xml(_AKN_FIXTURE))
    assert paras[0]["section_path"] == "Allgemeiner Teil"
    assert paras[1]["section_path"] == "Besonderer Teil"
    assert paras[-1]["section_path"] == "Besonderer Teil"


def test_akn_parser_metadata_only_yields_nothing() -> None:
    """The 2024/2945 case: <akomaNtoso> with FRBR metadata but no <body>.
    Parser must yield 0 paragraphs cleanly so callers fall through to PDF."""
    metadata_only = b"""<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
<act><meta><identification><FRBRWork><FRBRname value="metadata only"/></FRBRWork>
</identification></meta></act></akomaNtoso>"""
    paras = list(parse_akoma_ntoso_xml(metadata_only))
    assert paras == []


def test_akn_parser_skips_short_paragraphs() -> None:
    """Same 20-char floor as the PDF parser — avoids picking up
    fragments like '<p>1.</p>'."""
    short = b"""<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
<act><body><p>x</p><p>Lange genug um eingeschlossen zu werden.</p></body></act>
</akomaNtoso>"""
    paras = list(parse_akoma_ntoso_xml(short))
    assert len(paras) == 1
    assert "Lange genug" in paras[0]["text"]


def test_akn_parser_handles_malformed_xml_gracefully() -> None:
    """Malformed XML must not crash — return empty iterator so caller
    falls through to PDF."""
    bad = b"<not><xml at all"
    paras = list(parse_akoma_ntoso_xml(bad))
    assert paras == []


# ── PDF parser invariants (v0.1, kept) ────────────────────────────────


def test_pdf_parser_tags_zu_artikel() -> None:
    """Same parser the v0.1 PoC used for PDF text."""
    sample_pages = [
        "Allgemeines\n\n"
        "Zu Artikel 1\nDiese Bestimmung legt den Geltungsbereich fest, "
        "siehe Art. 5.\n\n"
        "Zu Artikel 2\nPersoenlicher Anwendungsbereich.",
    ]
    paras = list(parse_botschaft_text(sample_pages, "de"))
    anchors = [p["article_anchor"] for p in paras]
    # Both "Zu Artikel 1" and "Zu Artikel 2" should be picked up as
    # article anchors (the surrounding mid-sentence "Art. 5" must NOT
    # be treated as an anchor).
    assert "1" in anchors
    assert "2" in anchors


# ── Schema migration ──────────────────────────────────────────────────


def test_ensure_schema_creates_required_tables(tmp_path: Path) -> None:
    db = tmp_path / "materialien_test.db"
    conn = sqlite3.connect(db)
    ensure_schema(conn)
    tables = {
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','index','trigger')"
        )
    }
    assert "botschaft_documents" in tables
    assert "botschaft_paragraphs" in tables
    assert "article_botschaft_links" in tables
    # FTS5 vtab + triggers
    assert "botschaft_paragraphs_fts" in tables
    assert "botschaft_paragraphs_ai" in tables  # insert trigger
    assert "botschaft_paragraphs_ad" in tables  # delete trigger
    conn.close()


def test_ensure_schema_is_idempotent(tmp_path: Path) -> None:
    """Critical for nightly re-runs — calling --schema-only twice in a
    row must not error."""
    db = tmp_path / "materialien_test2.db"
    conn = sqlite3.connect(db)
    ensure_schema(conn)
    ensure_schema(conn)  # second call should be no-op via IF NOT EXISTS
    n_tables = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
    ).fetchone()[0]
    # Just sanity-check that we have at least the expected core tables.
    assert n_tables >= 4
    conn.close()


def test_schema_format_check_constraint(tmp_path: Path) -> None:
    """The format column has a CHECK constraint allowing only
    akoma-ntoso-xml and pdf — the two stored format labels."""
    db = tmp_path / "materialien_test3.db"
    conn = sqlite3.connect(db)
    ensure_schema(conn)
    # Allowed values insert cleanly:
    conn.execute(
        "INSERT INTO botschaft_documents "
        "(bbl_year, bbl_page, bbl_citation, source_url, format, language, "
        " text_hash, ingested_at) "
        "VALUES (2024, 100, 'BBl 2024 100', 'http://example/x', 'pdf', 'de', "
        "'h', '2026-05-07T00:00:00Z')",
    )
    conn.execute(
        "INSERT INTO botschaft_documents "
        "(bbl_year, bbl_page, bbl_citation, source_url, format, language, "
        " text_hash, ingested_at) "
        "VALUES (2024, 101, 'BBl 2024 101', 'http://example/x', 'akoma-ntoso-xml', 'fr', "
        "'h', '2026-05-07T00:00:00Z')",
    )
    # Disallowed value rejected:
    import pytest
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO botschaft_documents "
            "(bbl_year, bbl_page, bbl_citation, source_url, format, language, "
            " text_hash, ingested_at) "
            "VALUES (2024, 102, 'BBl 2024 102', 'http://example/x', 'docx', 'it', "
            "'h', '2026-05-07T00:00:00Z')",
        )
    conn.close()


# ── v0.3 additions ────────────────────────────────────────────────────


def test_part_suffix_regex_matches_dash_n_pdf() -> None:
    """The ``-N.pdf`` suffix detector drives multi-part probing."""
    m = _PART_SUFFIX_RE.search(
        "https://fedlex.data.admin.ch/filestore/abc-pdf-a-an-1.pdf"
    )
    assert m is not None
    assert int(m.group(1)) == 1
    # Non-multipart URL: returns None
    assert _PART_SUFFIX_RE.search(
        "https://fedlex.data.admin.ch/filestore/abc-pdf-a.pdf"
    ) is None
    # XML extension: doesn't match
    assert _PART_SUFFIX_RE.search(
        "https://fedlex.data.admin.ch/filestore/abc-xml-an-1.xml"
    ) is None


def test_fetch_pdf_parts_single_when_no_part_suffix(monkeypatch) -> None:
    """URLs not ending in ``-N.pdf`` produce a single-element list — no
    HEAD probing happens."""
    calls: dict[str, int] = {"head": 0, "get": 0}

    def fake_get(url, timeout=60):
        calls["get"] += 1
        return b"FAKEPDF"

    def fake_head(url, timeout=15):
        calls["head"] += 1
        return False

    monkeypatch.setattr(
        "search_stack.build_botschaft_corpus.fetch_pdf_bytes", fake_get,
    )
    monkeypatch.setattr(
        "search_stack.build_botschaft_corpus._url_exists", fake_head,
    )
    out = fetch_pdf_parts(
        "https://fedlex.data.admin.ch/filestore/single-pdf-a.pdf",
    )
    assert out == [b"FAKEPDF"]
    assert calls["head"] == 0  # no probing on non-multipart URLs


def test_fetch_pdf_parts_stitches_multiple(monkeypatch) -> None:
    """When SPARQL returns -1.pdf and -2.pdf exists, both are fetched."""
    seen: list[str] = []

    def fake_get(url, timeout=60):
        seen.append(url)
        return f"PART:{url[-7:]}".encode()

    def fake_head(url, timeout=15):
        # Pretend -2.pdf exists but -3.pdf doesn't.
        return url.endswith("-2.pdf")

    monkeypatch.setattr(
        "search_stack.build_botschaft_corpus.fetch_pdf_bytes", fake_get,
    )
    monkeypatch.setattr(
        "search_stack.build_botschaft_corpus._url_exists", fake_head,
    )
    out = fetch_pdf_parts(
        "https://fedlex.data.admin.ch/filestore/abc-pdf-a-an-1.pdf",
    )
    assert len(out) == 2
    assert out[0].startswith(b"PART:")
    assert out[1].startswith(b"PART:")
    assert seen[0].endswith("-1.pdf")
    assert seen[1].endswith("-2.pdf")


def test_discover_fga_botschaften_parses_sparql_rows(monkeypatch) -> None:
    """The SPARQL discoverer must extract (year, page, title) from FGA URIs."""
    from scrapers import fedlex_materialien

    fake_rows = [
        {
            # memorialPage (7705) differs from the ELI segment (1325) — issue #30.
            "act": {"value": "https://fedlex.data.admin.ch/eli/fga/2011/1325"},
            "title": {"value": "Botschaft VVG"},
            "memPage": {"value": "7705"},
        },
        {
            # No memorialPage (post-2022 style) — the segment is the doc number.
            "act": {"value": "https://fedlex.data.admin.ch/eli/fga/2013/823"},
            "title": {"value": "Botschaft zum Weiterbildungsgesetz"},
        },
        # Garbage row that should be skipped
        {
            "act": {"value": "https://fedlex.data.admin.ch/garbage"},
            "title": {"value": "noise"},
        },
    ]
    monkeypatch.setattr(
        fedlex_materialien, "sparql_query", lambda q, timeout=120: fake_rows,
    )
    out = fedlex_materialien.discover_fga_botschaften(language="de")
    # Two valid rows, one garbage filtered. Tuple = (year, citation_page, eli_uri, title);
    # the citation page is memorialPage (7705), not the ELI segment, and the ELI is kept.
    assert len(out) == 2
    assert (2011, 7705, "https://fedlex.data.admin.ch/eli/fga/2011/1325", "Botschaft VVG") in out
    assert (2013, 823, "https://fedlex.data.admin.ch/eli/fga/2013/823", "Botschaft zum Weiterbildungsgesetz") in out


def test_discover_fga_botschaften_rejects_unsupported_language() -> None:
    from scrapers import fedlex_materialien
    import pytest
    with pytest.raises(ValueError):
        fedlex_materialien.discover_fga_botschaften(language="rm")


def test_pdf_anchor_resets_at_section_boundary() -> None:
    """v0.3 fix: ``current_anchor`` must reset when a new top-level
    section header (``Übersicht``, ``Schlussbestimmungen``…) appears.

    Setup: page contains 'Zu Art. 1' + paragraph, then a section header
    'Schlussbestimmungen', then a generic paragraph. Without the fix
    the generic paragraph inherits article_anchor='1'. With the fix
    it carries None.
    """
    # Real Botschaft layout: article header is on the first line of a
    # multi-line chunk. The parser filters chunks with len<20, so the
    # header must run into body text without a blank-line break, and
    # standalone section titles need to be ≥20 chars to even reach the
    # SECTION_HEADER_RE branch.
    pages = [
        "Zu Art. 1\n"
        "Diese Bestimmung legt den Geltungsbereich fest und gilt fuer alle.\n\n"
        "SCHLUSSBESTIMMUNGEN UND ANHANG\n\n"
        "Diese Botschaft ist anlaesslich der Beratung im Plenum vorgelegt worden."
    ]
    paras = list(parse_botschaft_text(pages, language="de"))
    # First content paragraph anchored to article 1
    art_para = next(p for p in paras if "Geltungsbereich" in p["text"])
    assert art_para["article_anchor"] == "1"
    # The post-section paragraph must not inherit '1'
    post = next(p for p in paras if "anlaesslich der Beratung" in p["text"])
    assert post["article_anchor"] is None
