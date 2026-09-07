"""Tool-description budget + search-family de-overlap (integration evaluation 2026-07).

Microsoft 365 Copilot silently ignores tool-description text beyond 1,024
characters — attest_response (1,711) lost 40% of its description including
the entire calling protocol, and search_decisions (1,074) lost its tail.
Microsoft also names near-identical descriptions as THE cause of wrong-tool
selection ("Names matter more than anything"); five of nine search tools
opened with the identical stem "Full-text search across".
"""
from __future__ import annotations

import itertools
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import mcp_server as m  # noqa: E402

SEARCH_FAMILY = [
    "search_decisions", "search_laws", "search_legislation",
    "search_scholarship", "search_commentaries", "search_botschaft",
    "search_materialien", "search_practice",
]


def test_every_description_at_most_1024_chars():
    over = [(t.name, len(t.description or ""))
            for t in m._list_tools() if len(t.description or "") > 1024]
    assert not over, f"silently truncated in M365 Copilot: {over}"


def test_search_family_first_sentences_pairwise_distinct():
    firsts = {t.name: (t.description or "")[:60]
              for t in m._list_tools() if t.name in SEARCH_FAMILY}
    dupes = [(a, b) for a, b in itertools.combinations(firsts, 2)
             if firsts[a] == firsts[b]]
    assert not dupes, dupes


def test_search_family_states_negative_scope():
    """Each of the three most-confused tools names at least one tool it is
    NOT for (Microsoft's worked-example pattern)."""
    tools = {t.name: t.description or "" for t in m._list_tools()}
    assert "search_laws" in tools["search_decisions"]
    assert "search_legislation" in tools["search_laws"]
    assert "search_laws" in tools["search_legislation"]
    assert "search_scholarship" in tools["search_commentaries"]


def test_attest_response_keeps_calling_protocol_in_budget():
    [t] = [t for t in m._list_tools() if t.name == "attest_response"]
    d = t.description or ""
    assert len(d) <= 1024
    # the calling protocol must survive within the un-truncated budget
    assert "CALL THIS BEFORE" in d
    assert "audit_grounding=true" in d
    assert "linked_text" in d


def test_practice_description_constraints_still_hold():
    """The description must state the corpus honestly: what is covered, with
    counts, and what is still missing. Updated 2026-07-29 when SECO shipped
    (790 -> 1,892 documents) and 2026-08-19 when FINMA shipped (-> 3,062) —
    this assertion is what caught the stale "NOT covered: ... SECO ..." line
    both times, which would have told users we lack a corpus we had just
    ingested. A confident false statement is worse than the gap it
    describes, so the claim and the corpus have to move together."""
    [t] = [t for t in m._list_tools() if t.name == "search_practice"]
    d = t.description or ""
    # still-missing sources must stay disclosed (2026-09-02: BSV, BAG and BJ
    # shipped; the remaining gap is cantonal — Sozialhilfe/SKOS and IPV)
    for gap in ("cantonal", "Sozialhilfe", "Prämienverbilligung"):
        assert gap in d, gap
    # shipped sources must be named; the total count lives in the description,
    # per-source counts in the enum descriptions (1,024-char budget)
    for present in ("FINMA", "SECO", "ESTV", "BAFU", "SEM", "BSV", "BAG", "BJ",
                    "ch_vb", "include_superseded"):
        assert present in d, present
    assert re.search(r"\d,\d{3}\+? documents", d), "total count missing"
    props = t.inputSchema["properties"]
    for counted in ("1,133", "1,102"):
        assert counted in props["source"]["description"], counted
    # a shipped source must never appear in the gap list. BSV is scraped but
    # its first (multi-hour) ingest lands after the 2026-09-03 deploy; until
    # it is promoted in runner.py it stays in the gap list, marked in progress.
    gaps = d.split("NOT covered:", 1)[1] if "NOT covered:" in d else ""
    from scrapers.practice import runner
    shipped = ["SECO", "FINMA", "ESTV", "BAFU", "SEM", "BAG", "BJ"]
    if "bsv_weisungen" in runner.ENABLED_SCRAPERS:
        shipped.append("BSV")
    else:
        assert "BSV" in gaps and "in progress" in gaps, gaps[:160]
    for name in shipped:
        assert name not in gaps, f"{name} listed as a gap: {gaps[:120]}"


def test_practice_filters_match_the_description():
    """Every source/authority the description advertises must actually be
    selectable, or the filter enum and the prose disagree."""
    [t] = [t for t in m._list_tools() if t.name == "search_practice"]
    props = t.inputSchema["properties"]
    assert "finma_rs" in props["source"]["enum"]
    assert "FINMA" in props["issuing_authority"]["enum"]
    # FINMA circular annexes are a distinct class and must be filterable.
    assert "rundschreiben_anhang" in props["doc_type"]["enum"]
    # Tier 1 social-law sources (2026-09-02): enum values == SOURCE_KEY, and
    # mcp 1.26 validates the schema before dispatch, so a missing enum value
    # makes the whole source unreachable by filter.
    from scrapers.practice import runner
    for key in ("bsv_weisungen", "seco_alv", "bag_kvg", "sem_handbuch_asyl", "bj_schkg"):
        assert key in props["source"]["enum"], key
        assert runner.ALL_SCRAPERS[key].ISSUING_AUTHORITY in props["issuing_authority"]["enum"], key
        assert runner.ALL_SCRAPERS[key].DEFAULT_DOC_TYPE in props["doc_type"]["enum"], key
    assert props["include_superseded"]["type"] == "boolean"
