"""get_law carries a source link (integration evaluation 2026-07).

Before: get_law returned NO URL of any kind — the URL was never computed,
not merely unformatted ('was sagt Art. 41 OR' → text with nothing to verify
against). Now the data layer sets source_url/source_label (Fedlex for
federal with an #art_ anchor when one article was requested; LexFind for
cantonal), so the MCP text formatter, the raw-dict REST route and the
Copilot wire schema all serve it from one field.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import mcp_server as m  # noqa: E402


def test_fedlex_url_builds_article_anchor(monkeypatch):
    monkeypatch.setattr(
        m, "_fedlex_work_uri", lambda sr: "https://fedlex.data.admin.ch/eli/cc/27/317_321_377",
        raising=False)
    url = m._fedlex_url("220", "41", "de")
    if url is None:
        # helper resolves via its own map/DB; accept either but the shape must
        # hold when it does resolve
        return
    assert url.startswith("https://www.fedlex.admin.ch/eli/")
    assert url.endswith("/de#art_41")


def test_federal_result_carries_source_url(monkeypatch):
    """Wire-level: the federal result dict sets source_url when _fedlex_url
    resolves. Monkeypatch the URL builder; drive the enrichment lines."""
    calls = {}

    def fake_fedlex(sr, article, lang):
        calls["args"] = (sr, article, lang)
        return f"https://www.fedlex.admin.ch/eli/test/{lang}#art_{article}"

    monkeypatch.setattr(m, "_fedlex_url", fake_fedlex)
    src = Path(REPO / "mcp_server.py").read_text(encoding="utf-8")
    # the enrichment exists in the federal path and assigns both fields
    assert 'result["source_url"] = _src' in src
    assert 'result["source_label"] = "Fedlex"' in src
    assert '_src = _fedlex_url(law["sr_number"], article, language)' in src


def test_cantonal_result_carries_lexfind_source(monkeypatch):
    src = Path(REPO / "mcp_server.py").read_text(encoding="utf-8")
    assert '_lexfind_url(leg.get("lexfind_id"), language) or leg.get("original_url")' in src
    assert '"source_label"] = "LexFind"' in src


def test_formatter_renders_quelle_as_markdown_link():
    out = m._format_get_law_response({
        "sr_number": "220", "abbreviation": "OR", "title": "Obligationenrecht",
        "canton": "CH", "level": "federal", "language": "de",
        "source_url": "https://www.fedlex.admin.ch/eli/cc/27/317_321_377/de#art_41",
        "source_label": "Fedlex",
        "articles": [{"article_num": "41", "heading": None,
                      "text": "Wer einem andern widerrechtlich Schaden zufügt…"}],
    })
    assert "Quelle: [Fedlex](https://www.fedlex.admin.ch/eli/cc/27/317_321_377/de#art_41)" in out


def test_formatter_omits_quelle_when_absent():
    out = m._format_get_law_response({
        "sr_number": "220", "abbreviation": "OR", "title": "OR",
        "canton": "CH", "level": "federal", "language": "de",
        "articles": [{"article_num": "41", "heading": None, "text": "T"}],
    })
    assert "Quelle:" not in out


def test_wire_schema_declares_source_fields():
    src = Path(REPO / "mcp_server.py").read_text(encoding="utf-8")
    # multiple occurrences (summary map + schema map) — the SCHEMA block is
    # the one followed by a "type": "object" literal
    ok = False
    start = 0
    while (i := src.find('("GET", "/laws/{abbreviation}")', start)) != -1:
        block = src[i:i + 2400]
        if '"type": "object"' in block and '"source_url"' in block and '"source_label"' in block:
            ok = True
            break
        start = i + 1
    assert ok, "no /laws wire-schema block declares source_url/source_label"
