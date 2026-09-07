"""cite + find_relevant_erwaegung emit markdown_link (integration evaluation 2026-07).

cite is the tool the server's own instructions route citation formatting to;
it returned canonical_url with no rendered-link form, unlike get_regeste /
get_erwaegung. find_relevant_erwaegung built a rich display_url (highlight +
e= + #e- anchor) but emitted it as a bare string the _hint never mentioned.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import mcp_server as m  # noqa: E402

SRC = Path(REPO / "mcp_server.py").read_text(encoding="utf-8")


def test_cite_success_dict_includes_markdown_link():
    assert '"markdown_link": _md_link(primary, citation["canonical_url"]),' in SRC


def test_cite_close_matches_include_markdown_link():
    assert ('"markdown_link": _md_link(cand_citation["citation_string_de"], '
            'cand_citation["canonical_url"]),') in SRC


def test_cite_note_teaches_the_link():
    i = SRC.index('"markdown_link": _md_link(primary')
    note_region = SRC[i:i + 2000]
    assert "embed markdown_link" in note_region
    assert "strip bare URLs" in note_region


def test_relevant_erwaegung_matches_carry_markdown_link():
    assert '"markdown_link": _md_link(' in SRC
    i = SRC.index('"display_url": display_url,')
    region = SRC[i:i + 500]
    assert "markdown_link" in region
    # label is the pinpointed citation string; URL is the deep link
    assert 'cite.get("citation_string_de") or r["e_number"]' in region


def test_relevant_erwaegung_hint_mentions_the_link():
    i = SRC.index("Each match carries markdown_link")
    assert i > 0


def test_wire_schemas_declare_markdown_link():
    for route in ('("GET", "/cite")', '("GET", "/relevant-erwaegung/{decision_id}")'):
        i = SRC.rindex(route)
        block = SRC[i:i + 2500]
        assert '"markdown_link"' in block, route


def test_md_link_escapes_bracket_labels():
    assert m._md_link("BGE [x] 12", "https://u") == r"[BGE \[x\] 12](https://u)" or \
        "](https://u)" in m._md_link("BGE [x] 12", "https://u")
