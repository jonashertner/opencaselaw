"""Server-side mirror of tools/word-addin/js/redact.js.

Used as a HARD GUARD on the five Pro endpoints (/attest, /billing/verify,
/billing/strengthen, /billing/find-support, /billing/reflect). The
contract is:

    The client (Word add-in) MUST run js/redact.js before sending Pro
    requests. The server independently re-runs the same patterns; if
    it finds any structurally-identifiable PII in what was supposed to
    be already-redacted text, it returns 400 ``client_redaction_incomplete``
    and refuses to dispatch to the LLM.

This catches three failure modes:
  1. Buggy or out-of-date client (old cached add-in)
  2. Tampered request (curl bypassing the add-in)
  3. Non-Word direct API users who don't know about redaction

The patterns are intentionally a SUBSET of what js/redact.js covers —
the high-confidence structurally-identifiable types (AHV, IBAN, CHE,
EMAIL, PHONE) where a false positive would be near-impossible. Names,
addresses, DOBs etc. need fuzzy matching that is too risky to enforce
server-side; the client carries that load.

The defense-in-depth ``redact()`` function additionally scrubs anything
that slipped past the guard before the LLM call, so even a malicious
or buggy guard couldn't leak PII downstream.

Tests: tests/test_redact_mirror.py (parity with js redact.test.js).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

# Patterns mirror tools/word-addin/js/redact.js — kept intentionally
# minimal and high-precision. Order matters: structural IDs first.
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # Email — TLD restricted to lowercase to avoid greedy-match across
    # case boundaries, matching the JS fix shipped 2026-05-03.
    ("EMAIL", re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[a-z]{2,}\b")),
    # Swiss social-security number 756.XXXX.XXXX.XX (dots OR spaces).
    ("AHV", re.compile(r"\b756[.\s]\d{4}[.\s]\d{4}[.\s]\d{2}\b")),
    # Swiss IBAN: CH + 2 check digits + 17 chars in 4-digit groups.
    ("IBAN", re.compile(r"\bCH\d{2}(?:\s?[A-Z0-9]{4}){4}\s?[A-Z0-9]\b")),
    # Swiss company UID: CHE-XXX.XXX.XXX (with dots, hyphens, or spaces).
    ("CHE", re.compile(r"\bCHE[\-\s]?\d{3}[.\-\s]?\d{3}[.\-\s]?\d{3}\b")),
    # Swiss phone numbers: +41 ... or 0XX ...
    ("PHONE", re.compile(
        r"(?:\+41[\s\-]?\(?0?\)?[\s\-]?\d{2}|\b0\d{2})[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}\b"
    )),
]


@dataclass
class GuardResult:
    """Result of ``is_likely_unredacted``.

    Attributes
    ----------
    clean
        True if no high-confidence PII patterns were found. Server
        proceeds with the request.
    patterns_found
        List of types that triggered (for the 400 error body — never
        exposes the matched substring to avoid logging PII).
    """
    clean: bool
    patterns_found: list[str] = field(default_factory=list)


def is_likely_unredacted(text: str) -> GuardResult:
    """Server-side hard guard. True ``clean`` means the request looks
    properly redacted; False means we found structurally-identifiable
    PII and the client either skipped or failed at redaction.

    The return ``patterns_found`` lists which TYPES leaked, never the
    matched substrings — we don't want PII in our own logs/responses.
    """
    if not text:
        return GuardResult(clean=True)
    found: list[str] = []
    for label, pattern in _PATTERNS:
        if pattern.search(text):
            found.append(label)
    return GuardResult(clean=not found, patterns_found=found)


@dataclass
class Replacement:
    type: str
    placeholder: str
    start: int
    end: int


@dataclass
class RedactionResult:
    redacted: str
    replacements: list[Replacement] = field(default_factory=list)
    summary: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return len(self.replacements)


def redact(text: str) -> RedactionResult:
    """Defense-in-depth redactor — runs after the guard passes, so any
    pattern that slipped past gets scrubbed before the LLM sees it.
    Mirrors the JS overlap-resolution logic (sort by start, then by
    pattern priority, drop overlaps left-to-right)."""
    if not text:
        return RedactionResult(redacted="", replacements=[], summary={})

    matches: list[tuple[int, int, int, str, str]] = []
    for priority, (label, pattern) in enumerate(_PATTERNS):
        for m in pattern.finditer(text):
            matches.append((m.start(), priority, -m.end(), label, m.group(0)))

    # Sort: start ASC, priority ASC, longer span first at tie (negative end DESC).
    matches.sort()
    keep: list[tuple[int, int, str, str]] = []
    last_end = -1
    for start, _prio, neg_end, label, original in matches:
        end = -neg_end
        if start >= last_end:
            keep.append((start, end, label, original))
            last_end = end

    # Same original → same placeholder within one call (mirrors the JS
    # redactor): a party that appears five times is one entity to the
    # model. ``summary`` still counts occurrences.
    counters: dict[str, int] = {}
    by_original: dict[tuple[str, str], str] = {}
    out_parts: list[str] = []
    replacements: list[Replacement] = []
    cursor = 0
    for start, end, label, original in keep:
        placeholder = by_original.get((label, original))
        if placeholder is None:
            counters[label] = counters.get(label, 0) + 1
            placeholder = f"[{label}_{counters[label]}]"
            by_original[(label, original)] = placeholder
        out_parts.append(text[cursor:start])
        out_parts.append(placeholder)
        replacements.append(Replacement(type=label, placeholder=placeholder, start=start, end=end))
        cursor = end
    out_parts.append(text[cursor:])

    summary: dict[str, int] = {}
    for rep in replacements:
        summary[rep.type] = summary.get(rep.type, 0) + 1
    return RedactionResult(redacted="".join(out_parts), replacements=replacements, summary=summary)


def patterns() -> Iterable[str]:
    """For diagnostics / docs — the labels we enforce server-side."""
    return [label for label, _ in _PATTERNS]
