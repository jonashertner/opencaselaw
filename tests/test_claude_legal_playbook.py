"""Offline checks for the Swiss playbook template shipped for Anthropic's
Claude for Legal plugin (docs/claude/legal.local.md).

The template is read by the plugin's review-contract / triage-nda /
legal-response / compliance-check skills under fixed headings, and every
"Art. X LAW" reference in it was retrieved through get_law on the date
recorded in docs/claude/verified-references.json. These tests keep the two
files consistent without touching the network:

* the headings the host plugin looks for are present, verbatim;
* every statute reference in the template is in the verified list;
* every verified entry is still used by the template (no stale entries);
* the decisions named in the template are the ones that were checked.

Re-verification against the live statute mirror is the job of
scripts/check_playbook_refs.py (network, `make smoke` class).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
TEMPLATE = REPO / "docs" / "claude" / "legal.local.md"
VERIFIED = REPO / "docs" / "claude" / "verified-references.json"

# Headings from the legal plugin's README example of legal.local.md. Its
# skills say "look for the playbook in local settings"; keeping these exact
# strings is what lets them find the positions.
HOST_HEADINGS = [
    "## Contract Review Positions",
    "### Limitation of Liability",
    "### Indemnification",
    "### IP Ownership",
    "### Data Protection",
    "### Term and Termination",
    "### Governing Law",
    "## NDA Defaults",
    "## Response Templates",
]

LAWS = "OR|ZGB|DSG|UWG|StGB|URG|IPRG|ZPO|KG"
# "Art. 100 Abs. 1 OR", "Art. 14 Abs. 2bis OR", "Art. 60 Abs. 1 lit. a DSG",
# "Art. 340a OR". Bare "Art. 30" in the GDPR column carries no law and is
# deliberately not matched.
ARTICLE_RE = re.compile(
    rf"Art\.\s+(\d+[a-z]?)"
    rf"(?:\s+Abs\.\s+\d+(?:bis|ter)?)?"
    rf"(?:\s+lit\.\s+[a-z])?"
    rf"\s+({LAWS})\b"
)
DECISION_RE = re.compile(r"\bBGE\s+\d+\s+[IVX]+\s+\d+(?:\s+E\.\s+[\d.]+)?")


@pytest.fixture(scope="module")
def template() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def verified() -> dict:
    return json.loads(VERIFIED.read_text(encoding="utf-8"))


def test_host_plugin_headings_present(template: str):
    lines = {line.rstrip() for line in template.splitlines()}
    missing = [h for h in HOST_HEADINGS if h not in lines]
    assert not missing, f"headings the legal plugin looks for are missing: {missing}"


def test_verification_rule_is_stated(template: str):
    # The one instruction that turns the host plugin's memory-based statute
    # statements into tool calls (server rule R3).
    assert "VERIFY BEFORE YOU WRITE" in template
    for tool in ("get_law", "cite", "attest_response"):
        assert f"`{tool}`" in template, f"verification rule does not name {tool}"


def test_every_statute_reference_is_verified(template: str, verified: dict):
    allowed = {(law, art) for law, art in verified["articles"]}
    found = {(law, art) for art, law in ARTICLE_RE.findall(template)}
    unverified = sorted(found - allowed)
    assert not unverified, (
        "statute references in the template that were not retrieved with get_law: "
        f"{unverified}; retrieve them, then add them to verified-references.json"
    )


def test_no_stale_verified_entries(template: str, verified: dict):
    found = {(law, art) for art, law in ARTICLE_RE.findall(template)}
    stale = sorted({(law, art) for law, art in verified["articles"]} - found)
    assert not stale, f"verified entries no longer used by the template: {stale}"


def test_every_law_in_the_list_has_metadata(verified: dict):
    laws_used = {law for law, _ in verified["articles"]}
    for law in sorted(laws_used):
        meta = verified["laws"].get(law)
        assert meta, f"no sr_number/consolidation_date recorded for {law}"
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", meta["consolidation_date"])


def test_template_states_retrieval_date_and_pending_or_changes(template: str, verified: dict):
    assert verified["retrieved"] in template
    for date in verified["laws"]["OR"]["pending_changes"]:
        assert date in template, f"pending OR amendment {date} is not surfaced (server rule R5)"


def test_decisions_named_are_the_checked_ones(template: str, verified: dict):
    checked = {d["reference"] for d in verified["decisions"]}
    named = set(DECISION_RE.findall(template))
    # Every BGE the template names must have been checked; a pinpointed
    # reference counts as checked when its decision-level form was.
    for ref in sorted(named):
        base = re.sub(r"\s+E\.\s+[\d.]+$", "", ref)
        assert ref in checked or base in checked or any(c.startswith(base) for c in checked), (
            f"{ref} is named in the template but not in the verified decisions list"
        )


def test_practice_notes_are_marked(template: str):
    # Anything that is drafting practice rather than statute text carries the
    # marker, so a reader can tell the two apart.
    assert "[practice note, not statute-verified" in template
    assert "not legal advice" in template
