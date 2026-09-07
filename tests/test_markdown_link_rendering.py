"""Every URL a tool emits must be a rendered Markdown link (integration evaluation 2026-07).

Microsoft documents that Copilot's @mention pipeline strips bare URLs from
answers ("removed, hidden, or downgraded to plain text"); Markdown links and
structured JSON fields survive. Decisions already rendered links
(search_decisions/get_decision H1); commentaries, scholarship, legislation,
get_practice and get_decision's Source/PDF lines were bare — and get_law
carried no URL at all (covered in test_get_law_source_url.py).
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import mcp_server as m  # noqa: E402


def test_commentary_detail_link_is_markdown():
    out = m._format_get_commentary_response({
        "article_num": "41", "abbreviation": "OR", "title": "Kommentar zu Art. 41",
        "authors": "X", "language": "de", "html_link": "https://onlinekommentar.ch/x",
        "text": "Body",
    })
    assert "[OnlineKommentar](https://onlinekommentar.ch/x)" in out
    assert "**Link:** https://" not in out


def test_search_commentaries_link_is_markdown():
    out = m._format_search_commentaries_response({
        "query": "Haftung", "count": 1, "results": [{
            "article_num": "41", "abbreviation": "OR", "title": "T",
            "language": "de", "snippet": "s",
            "html_link": "https://onlinekommentar.ch/y",
        }],
    })
    assert "[OnlineKommentar](https://onlinekommentar.ch/y)" in out


def test_scholarship_citing_decision_url_is_markdown():
    out = m._format_find_scholarship_citing_decision_response({
        "decision_id": "bge_BGE_140_III_86", "count": 1, "results": [{
            "source": "sui-generis", "year": 2023, "title": "Aufsatz",
            "authors": "A", "snippet": "sn", "url": "https://sui-generis.ch/a1",
        }],
    })
    assert "[Volltext](https://sui-generis.ch/a1)" in out
    assert "\n  https://sui-generis.ch/a1" not in out


def test_legislation_search_url_is_markdown():
    out = m._format_search_legislation_response({
        "query": "Hundegesetz", "total": 1, "laws": [{
            "title": "Hundegesetz", "entity": "BE", "entity_name": "Bern",
            "systematic_number": "916.31", "is_active": True,
            "original_url": "https://www.lexfind.ch/tol/1234",
            "snippet": "sn",
        }],
    })
    assert "[LexFind](https://www.lexfind.ch/tol/1234)" in out


def test_get_practice_pdf_and_source_are_markdown():
    out = m._format_get_practice_response({
        "doc_id": "estv_ks_6a", "title": "KS 6a", "doc_number": "KS Nr. 6a",
        "issuing_authority": "ESTV", "source": "estv_ks",
        "doc_type": "kreisschreiben",
        "date": "2024-10-10", "language": "de",
        "url": "https://estv.admin.ch/ks6a",
        "pdf_url": "https://estv.admin.ch/ks6a.pdf",
        "body_text": "Text",
    })
    assert "[KS Nr. 6a](https://estv.admin.ch/ks6a.pdf)" in out
    assert "[Quelle](https://estv.admin.ch/ks6a)" in out


def test_no_bare_url_source_lines_left_in_get_decision():
    """Source-scan (house precedent): the exact bare-emission forms must not
    reappear."""
    src = Path(REPO / "mcp_server.py").read_text(encoding="utf-8")
    assert "f\"- URL: <{citation['canonical_url']}>" not in src
    assert 'text += f"\\n**Source:** {result[\'source_url\']}' not in src
    assert 'text += f"**PDF:** {result[\'pdf_url\']}' not in src
    # the backticked (inert) markdown template is gone
    assert "`[{citation['citation_string_de']}]({citation['canonical_url']})`" not in src


def test_get_decision_truncation_notice_carries_link():
    src = Path(REPO / "mcp_server.py").read_text(encoding="utf-8")
    assert "The remainder — which may include the operative part" in src
    assert "_md_link('Volltext', citation['canonical_url'])" in src
