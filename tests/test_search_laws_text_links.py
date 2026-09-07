"""search_laws text output carries per-hit source links (integration evaluation 2026-07).

The text surface had ZERO urls while structuredContent carried
source_url/source_label — and a spec-conformant client may discard
structuredContent entirely (only four tools declare outputSchema).
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import mcp_server as m  # noqa: E402


def _result(**hit):
    base = {"query": "Haftung", "count": 1, "federal_hits": 1, "cantonal_hits": 0,
            "results": [hit]}
    return base


def test_federal_hit_renders_fedlex_md_link(monkeypatch):
    monkeypatch.setattr(m, "_fedlex_url",
                        lambda sr, art, lang: f"https://www.fedlex.admin.ch/eli/x/{lang}#art_{art}")
    out = m._format_search_laws_response(_result(
        level="federal", canton="CH", sr_number="220", abbreviation="OR",
        article_num="41", heading="Haftung", snippet="Wer einem andern…"))
    assert "[Fedlex](https://www.fedlex.admin.ch/eli/x/de#art_41)" in out


def test_cantonal_hit_renders_lexfind_md_link(monkeypatch):
    monkeypatch.setattr(m, "_lexfind_url",
                        lambda lid, lang: f"https://www.lexfind.ch/fe/{lang}/tol/{lid}" if lid else None)
    out = m._format_search_laws_response(_result(
        level="cantonal", canton="ZH", sr_number="230", article_num="5",
        title="Haftungsgesetz", lexfind_id="22871", snippet="…"), lang="de")
    assert "[LexFind](https://www.lexfind.ch/fe/de/tol/22871)" in out


def test_hit_without_url_renders_no_dangling_label(monkeypatch):
    monkeypatch.setattr(m, "_fedlex_url", lambda *a: None)
    out = m._format_search_laws_response(_result(
        level="federal", canton="CH", sr_number="999.99", abbreviation="XYZ",
        article_num="1", snippet="…"))
    assert "Fedlex" not in out


def test_language_parameter_reaches_the_links(monkeypatch):
    captured = {}

    def fake(sr, art, lang):
        captured["lang"] = lang
        return f"https://www.fedlex.admin.ch/eli/x/{lang}#art_{art}"

    monkeypatch.setattr(m, "_fedlex_url", fake)
    m._format_search_laws_response(_result(
        level="federal", canton="CH", sr_number="220", abbreviation="CO",
        article_num="41", snippet="…"), lang="fr")
    assert captured["lang"] == "fr"


def test_dispatch_passes_language():
    src = Path(REPO / "mcp_server.py").read_text(encoding="utf-8")
    assert '_format_search_laws_response(result, arguments.get("language") or "de")' in src
