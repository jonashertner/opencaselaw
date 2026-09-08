"""Tests for OG / Twitter card meta tags on /entscheid/<id> pages.

Without these, links to decision pages shared in claude.ai / Slack /
Twitter / WhatsApp / LinkedIn show only the bare URL — no title, no
preview image, no description. Practitioners and citizens routinely
share Swiss legal decisions, so the link preview is the difference
between "click to see" and "ignore".
"""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

import seo_pages


@pytest.fixture
def fixture_decision(monkeypatch):
    """Stand up a tiny in-memory decisions DB so render_decision_page
    can find the row, render the full HTML, and we can assert on the
    meta tags it emits."""
    tmp = Path(tempfile.mkdtemp())
    db_path = tmp / "decisions.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE decisions (
            decision_id TEXT PRIMARY KEY,
            court TEXT, canton TEXT, docket_number TEXT,
            decision_date TEXT, language TEXT,
            title TEXT, regeste TEXT, full_text TEXT,
            source_url TEXT, pdf_url TEXT
        );
    """)
    conn.execute("""
        INSERT INTO decisions VALUES (
            'bge_BGE_140_III_86', 'bge', 'CH', 'BGE 140 III 86',
            '2014-02-05', 'de',
            'Beschwerdebefugnis im Mietrecht',
            'Regeste\nArt. 76 BGG; Beschwerdebefugnis. Schutzwürdiges Interesse.',
            'Sample Erwägungen text...',
            'https://www.bger.ch/...', NULL
        )
    """)
    conn.commit()
    conn.close()
    # Patch _get_db so render_decision_page reads from the fixture
    def _fake_get_db():
        c = sqlite3.connect(str(db_path))
        c.row_factory = sqlite3.Row
        return c
    monkeypatch.setattr(seo_pages, "_get_db", _fake_get_db)
    return db_path


def test_og_card_meta_tags_present(fixture_decision):
    html, status, redirect_location = seo_pages.render_decision_page("bge_BGE_140_III_86")
    assert status == 200
    assert redirect_location is None

    # Open Graph essentials — image is the key add for v0.1
    assert '<meta property="og:image" content="https://opencaselaw.ch/og-image.png">' in html
    assert '<meta property="og:image:width" content="1200">' in html
    assert '<meta property="og:image:height" content="630">' in html
    assert 'og:image:alt' in html

    # article:published_time uses the decision_date
    assert '<meta property="article:published_time" content="2014-02-05T00:00:00Z">' in html

    # Twitter card — switches Twitter to large-image preview
    assert '<meta name="twitter:card" content="summary_large_image">' in html
    assert '<meta name="twitter:title" content="BGE 140 III 86' in html
    assert 'twitter:description' in html
    assert '<meta name="twitter:image" content="https://opencaselaw.ch/og-image.png">' in html


def test_existing_og_tags_preserved(fixture_decision):
    """The existing og:type, og:title, og:description, og:url,
    og:site_name, og:locale must NOT regress when adding the new
    image + Twitter tags."""
    html, _, _ = seo_pages.render_decision_page("bge_BGE_140_III_86")
    for tag in [
        '<meta property="og:type" content="article">',
        '<meta property="og:title" content="BGE 140 III 86',
        'og:description',
        'og:url',
        '<meta property="og:site_name" content="OpenCaseLaw.ch">',
        '<meta property="og:locale" content="de_CH">',
    ]:
        assert tag in html, f"Pre-existing OG tag missing: {tag!r}"


def test_meta_description_in_og_description(fixture_decision):
    """og:description should reuse the truncated regeste-derived
    meta_desc — same value as <meta name="description">."""
    html, _, _ = seo_pages.render_decision_page("bge_BGE_140_III_86")
    # The regeste contains "Schutzwürdiges Interesse" so og:description
    # should mention it (after truncation).
    assert "Schutzwürdiges Interesse" in html
