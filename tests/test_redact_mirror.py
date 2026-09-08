"""Parity tests for quality/redact.py — the server-side hard-guard
mirror of tools/word-addin/js/redact.js.

The Python module enforces a SUBSET of the JS rules — only the
high-confidence structurally-identifiable types (AHV, IBAN, CHE,
EMAIL, PHONE). Names, addresses, DOBs etc. live only in the JS
module because they need fuzzy matching that's too risky to enforce
server-side. These tests verify:

  1. Every blockable PII pattern triggers ``is_likely_unredacted``
     (false negatives = leaked PII = failure).
  2. Properly redacted text and pure legal prose pass cleanly
     (false positives = blocked legitimate Pro requests = failure).
  3. ``redact()`` produces deterministic placeholder output that
     round-trips structurally.
"""
from quality.redact import is_likely_unredacted, redact, patterns


# ────────────────────────────────────────────────────────────────────
# Guard — must catch
# ────────────────────────────────────────────────────────────────────

def test_guard_catches_dotted_ahv():
    r = is_likely_unredacted("AHV-Nr. 756.1234.5678.90 wurde abgelegt.")
    assert not r.clean
    assert "AHV" in r.patterns_found


def test_guard_catches_spaced_ahv():
    r = is_likely_unredacted("AHV 756 1234 5678 90 ist erfasst")
    assert not r.clean
    assert "AHV" in r.patterns_found


def test_guard_catches_iban():
    r = is_likely_unredacted("Konto: CH93 0076 2011 6238 5295 7")
    assert not r.clean
    assert "IBAN" in r.patterns_found


def test_guard_catches_che():
    r = is_likely_unredacted("UID CHE-123.456.789")
    assert not r.clean
    assert "CHE" in r.patterns_found


def test_guard_catches_email():
    r = is_likely_unredacted("Kontakt info@kanzlei.ch")
    assert not r.clean
    assert "EMAIL" in r.patterns_found


def test_guard_catches_phone():
    r = is_likely_unredacted("Tel +41 79 123 45 67")
    assert not r.clean
    assert "PHONE" in r.patterns_found


def test_guard_lists_multiple_types_when_present():
    r = is_likely_unredacted("info@x.ch, AHV 756.1111.2222.33, Tel 079 111 22 33")
    assert not r.clean
    assert set(r.patterns_found) >= {"EMAIL", "AHV", "PHONE"}


# ────────────────────────────────────────────────────────────────────
# Guard — must NOT catch (false-positive prevention)
# ────────────────────────────────────────────────────────────────────

def test_guard_passes_redacted_text():
    redacted = "Wie das Bundesgericht in BGE 143 III 480 ausführt, hat [NAME_1] (AHV [AHV_1]) Anspruch."
    r = is_likely_unredacted(redacted)
    assert r.clean
    assert r.patterns_found == []


def test_guard_passes_pure_legal_prose():
    src = (
        "Wie das Bundesgericht in BGE 132 III 222, E. 4.2 ausführt, "
        "gilt nach Art. 41 Abs. 1 OR i.V.m. Art. 8 BV das Verschuldensprinzip. "
        "Vgl. auch Urteil 4A_747/2012 vom 5. April 2013."
    )
    r = is_likely_unredacted(src)
    assert r.clean, f"unexpected hits: {r.patterns_found}"


def test_guard_passes_empty_text():
    assert is_likely_unredacted("").clean
    assert is_likely_unredacted("").patterns_found == []


def test_guard_does_not_treat_art_756_or_as_ahv():
    """The string 'Art. 756 OR' must not trigger AHV — it's a statute
    reference, the JS regex has the same guard."""
    r = is_likely_unredacted("Gemäss Art. 756 OR ist die Verantwortlichkeit geregelt.")
    assert r.clean


def test_guard_does_not_treat_docket_756_as_ahv():
    r = is_likely_unredacted("Urteil 6B_756/2025 vom heutigen Tag.")
    assert r.clean


# ────────────────────────────────────────────────────────────────────
# redact() — defense-in-depth scrubber
# ────────────────────────────────────────────────────────────────────

def test_redact_removes_email():
    result = redact("Kontakt info@kanzlei.ch melden.")
    assert "info@kanzlei.ch" not in result.redacted
    assert "[EMAIL_1]" in result.redacted
    assert result.summary == {"EMAIL": 1}


def test_redact_preserves_citations():
    src = "Vgl. BGE 143 III 480, E. 3.2 und Art. 41 OR."
    result = redact(src)
    assert result.redacted == src
    assert result.total == 0


def test_redact_handles_overlapping_patterns_left_to_right():
    """If two patterns could match, the higher-priority (= earlier in
    the list) wins. We don't have many overlap cases for the subset,
    but verify the algorithm doesn't double-count."""
    # AHV inside a sentence with surrounding email — both must be caught
    result = redact("a@b.ch mit AHV 756.1234.5678.90 stop")
    assert result.summary == {"EMAIL": 1, "AHV": 1}
    assert "[EMAIL_1]" in result.redacted
    assert "[AHV_1]" in result.redacted


def test_redact_counters_increment_per_type():
    src = "a@b.ch und c@d.ch und 756.0001.0002.03 und 756.9999.8888.77"
    result = redact(src)
    assert result.summary == {"EMAIL": 2, "AHV": 2}
    assert "[EMAIL_1]" in result.redacted and "[EMAIL_2]" in result.redacted
    assert "[AHV_1]" in result.redacted and "[AHV_2]" in result.redacted


def test_redact_same_original_shares_placeholder():
    """Mirror of the JS behaviour: one placeholder per distinct original,
    counts per occurrence — so the model sees one entity, and the banner
    still reports every occurrence."""
    result = redact("a@x.ch, a@x.ch, b@x.ch")
    assert result.redacted == "[EMAIL_1], [EMAIL_1], [EMAIL_2]"
    assert result.summary == {"EMAIL": 3}
    assert [r.placeholder for r in result.replacements] == ["[EMAIL_1]", "[EMAIL_1]", "[EMAIL_2]"]


def test_redact_empty_returns_empty():
    result = redact("")
    assert result.redacted == ""
    assert result.total == 0


# ────────────────────────────────────────────────────────────────────
# Module surface
# ────────────────────────────────────────────────────────────────────

def test_patterns_lists_enforced_labels():
    labels = list(patterns())
    assert set(labels) == {"EMAIL", "AHV", "IBAN", "CHE", "PHONE"}
