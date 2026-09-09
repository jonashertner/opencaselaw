"""Closing-audit tests for the four hallucination classes that
attest_response now defends against:

  case      — citation exists in corpus, pinpoint resolves
  statute   — Art. X LAW reference resolves in statutes.db
  quote     — "..." appears verbatim in a cited decision
  date      — "vom DD.MM.YYYY" matches the actual decision date

These tests exercise the parser/auditor helpers in isolation so they
do not depend on the full corpus DB. The case-citation existence path
is covered by the integration test that hits a live decisions.db.
"""
from __future__ import annotations

import importlib

import pytest


@pytest.fixture(scope="module")
def m():
    return importlib.import_module("mcp_server")


# ── Quote audit ────────────────────────────────────────────────────

def test_quote_in_source_passes(m):
    """Quote present verbatim in the cited source — no issue. The
    citation anchor (BGE 140 III 86) qualifies the quote for the
    audit; the source-pool match then clears it."""
    src = [{
        "regeste": "Der Vermieter haftet für Mängel an der Sache, soweit dies vereinbart wurde.",
        "full_text": "",
        "paragraphs": [],
    }]
    draft = (
        'BGE 140 III 86 E. 2.3: '
        '\u201eDer Vermieter haftet f\u00fcr M\u00e4ngel an der Sache, '
        'soweit dies vereinbart wurde.\u201c'
    )
    issues = m._audit_quotes(draft, src)
    assert issues == []


def test_quote_not_in_source_flagged(m):
    """Quote that's NEAR a Swiss case citation but NOT in the cited
    source must be flagged — that's the original hallucination case."""
    src = [{"regeste": "Etwas ganz anderes.", "full_text": "", "paragraphs": []}]
    # Embed a BGE citation near the quote so the audit qualifies it
    # as a verifiable-source claim. Without this, after the
    # 2026-05-11 scoping refinement, a standalone quote is left alone.
    draft = ('BGE 140 III 86 E. 2.3: '
             '\u201eDieser Satz steht so nicht im Entscheid und ist '
             'tats\u00e4chlich frei erfunden.\u201c')
    issues = m._audit_quotes(draft, src)
    assert len(issues) == 1
    assert issues[0]["category"] == "quote"
    assert issues[0]["problem"] == "quote_not_in_cited_sources"


def test_standalone_quote_without_authority_context_skipped(m):
    """After 2026-05-11: a quote that has NO nearby Swiss-case citation
    or statute reference is left alone — the writer isn't claiming a
    legal source, so the audit doesn't fire (party narrative,
    defined terms, idioms, dialogue all live here)."""
    src = []  # no cited decisions
    draft = ('Der Beklagte sagte am Verhandlungstermin: '
             '\u201eIch werde liefern, sobald die Zahlung eingeht.\u201c '
             'Diese Aussage wurde protokolliert.')
    assert m._audit_quotes(draft, src) == []


def test_short_quote_skipped(m):
    """Quotes under 60 chars (raised from 30 on 2026-05-11) are noise:
    defined legal terms ('Treuepflicht', 'guter Glaube'), article
    labels, names — not actual verbatim source quotations."""
    src = []
    draft = ('BGE 140 III 86: \u201eTreuepflicht\u201c und '
             '\u201eguter Glaube\u201c')  # short defined terms near a citation
    assert m._audit_quotes(draft, src) == []


def test_french_quotes_handled(m):
    """French «...» quotes with adjacent ATF/BGer citation behave the
    same as German „..." quotes — match against the cited source."""
    src = [{
        "regeste": "Le bailleur est responsable des d\u00e9fauts de la chose lou\u00e9e, sauf clause contraire.",
        "full_text": "", "paragraphs": [],
    }]
    draft = ('ATF 140 III 86 c. 2.3: '
             '\u00abLe bailleur est responsable des d\u00e9fauts de la chose '
             'lou\u00e9e, sauf clause contraire.\u00bb')
    assert m._audit_quotes(draft, src) == []


def test_whitespace_normalisation_in_quote_match(m):
    """Source has line breaks; draft has the same text with single
    spaces, alongside a citation anchor."""
    src = [{
        "regeste": "Der Vermieter haftet \n\n  f\u00fcr M\u00e4ngel\n  an der Sache, soweit dies vereinbart wurde.",
        "full_text": "", "paragraphs": [],
    }]
    draft = ('BGE 140 III 86 E. 2.3: '
             '\u201eDer Vermieter haftet f\u00fcr M\u00e4ngel an der Sache, '
             'soweit dies vereinbart wurde.\u201c')
    assert m._audit_quotes(draft, src) == []


def test_quote_anchored_to_statute_reference(m):
    """A quote near an Art. X LAW reference also qualifies (the
    statute audit's source pool ends up in the same path)."""
    src = [{
        "regeste": "Definierte Treuepflichten zwischen den Parteien greifen erst nach Vertragsabschluss.",
        "full_text": "", "paragraphs": [],
    }]
    draft = ('Im Anwendungsbereich von Art. 2 ZGB gilt: '
             '\u201eDefinierte Treuepflichten zwischen den Parteien greifen '
             'erst nach Vertragsabschluss.\u201c')
    assert m._audit_quotes(draft, src) == []


# ── Date audit ─────────────────────────────────────────────────────

def test_date_match_no_issue(m):
    cits = [{
        "span": (0, 14), "full_match": "BGer 4A_1/2024",
        "_decision_date": "2024-03-12",
    }]
    draft = "BGer 4A_1/2024 vom 12.03.2024 hielt fest"
    assert m._audit_dates(draft, cits) == []


def test_date_mismatch_flagged(m):
    cits = [{
        "span": (0, 14), "full_match": "BGer 4A_1/2024",
        "_decision_date": "2024-03-12",
    }]
    draft = "BGer 4A_1/2024 vom 15.03.2024 hielt fest"
    issues = m._audit_dates(draft, cits)
    assert len(issues) == 1
    assert issues[0]["category"] == "date"
    assert issues[0]["claimed_date"] == "2024-03-15"
    assert issues[0]["actual_date"] == "2024-03-12"


def test_no_adjacent_date_no_issue(m):
    cits = [{
        "span": (0, 14), "full_match": "BGer 4A_1/2024",
        "_decision_date": "2024-03-12",
    }]
    draft = "BGer 4A_1/2024 hielt fest, dass ..."
    assert m._audit_dates(draft, cits) == []


def test_unverified_citation_skipped_by_date_audit(m):
    """A citation without _decision_date (failed existence check) is not
    date-audited — we cannot know what the right date would have been."""
    cits = [{"span": (0, 14), "full_match": "BGer X", "_decision_date": ""}]
    draft = "BGer X vom 99.99.9999"
    assert m._audit_dates(draft, cits) == []


# ── Statute audit guard ────────────────────────────────────────────

def test_statute_audit_noop_without_db(m, monkeypatch, tmp_path):
    """When statutes.db is missing, the audit must NOT flag every Art. ref."""
    monkeypatch.setattr(m, "STATUTES_DB_PATH", tmp_path / "nope.db")
    draft = "Siehe Art. 41 OR und Art. 256 OR sowie Art. 999 ZZZ."
    assert m._audit_statutes(draft) == []


# ── Statute pattern: full Swiss subdivision-chain support ──────────
# These exercise the regex directly so we lock in the (article, law)
# tuple regardless of statutes.db presence. Bug fixed 2026-05-11:
# the audit broke when references included Bst./lit./let./Ziff./Nr./
# Satz markers between Art. and the law abbreviation.

@pytest.mark.parametrize("inp,exp_article,exp_law", [
    # Base case
    ("Art. 4 OR",                              "4",       "OR"),
    ("Art. 41 OR",                             "41",      "OR"),
    # Article suffixes
    ("Art. 10a Abs. 2 ZGB",                    "10a",     "ZGB"),
    ("Art. 41bis OR",                          "41bis",   "OR"),
    # German subdivision chain
    ("Art. 4 Abs. 1 OR",                       "4",       "OR"),
    ("Art. 4 Abs. 1 Bst. a OR",                "4",       "OR"),
    ("Art. 4 Abs. 1 Bst. a Ziff. 2 OR",        "4",       "OR"),
    ("Art. 4 Abs. 1 Bst. a Ziff. 2 Satz 3 OR", "4",       "OR"),
    ("Art. 5 Nr. 3 IPRG",                      "5",       "IPRG"),
    # French subdivision chain (alinea/lettre/chiffre/phrase)
    ("art. 4 al. 1 let. a CO",                 "4",       "CO"),
    ("Article 4 alinea 1 CO",                  "4",       "CO"),
    # Italian subdivision chain (capoverso/lettera/numero/frase)
    ("art. 4 cpv. 1 lett. a CC",               "4",       "CC"),
    ("articolo 5 cpv. 2 lett. b CO",           "5",       "CO"),
    # Hyphenated law abbreviations — ordinances, specialised acts,
    # newer instruments. The original failure case was PLB-NVO
    # (Public Liquidity Backstop Notverordnung); the pre-fix law
    # slot stopped at the first hyphen, treating only the leading
    # chunk as the law and missing the rest.
    ("Art. 5 PLB-NVO",                         "5",       "PLB-NVO"),
    ("Art. 3 Abs. 1 PLB-NVO",                  "3",       "PLB-NVO"),
    ("Art. 5 GwV-Banken",                      "5",       "GwV-Banken"),
    ("Art. 8 DSG-V",                           "8",       "DSG-V"),
    ("Art. 12 Abs. 1 Bst. a AHV-IV",           "12",      "AHV-IV"),
    # Cantonal suffix preserved alongside hyphenated base
    ("Art. 5 OR/ZH",                           "5",       "OR/ZH"),
])
def test_statute_pattern_parses_all_subdivisions(m, inp, exp_article, exp_law):
    matches = list(m._STATUTE_AUDIT_PATTERN.finditer(inp))
    assert matches, f"pattern failed to match: {inp!r}"
    g = matches[0]
    assert (g.group("article") or "").strip() == exp_article, inp
    assert (g.group("law") or "").strip() == exp_law, inp


def test_statute_pattern_does_not_mistake_subdivision_marker_for_law(m):
    """Pre-fix bug: 'Art. 4 Abs. 1 Bst. a OR' would either fail to
    match, or worse, match 'Bst' as the law slot. Confirm that all
    matches in such a string land on the real law abbreviation."""
    text = ("Vgl. Art. 4 Abs. 1 Bst. a OR; ebenso Art. 5 Abs. 2 ZGB "
            "und Art. 6 Bst. b lit. ii BGG.")
    laws = [m_.group("law") for m_ in m._STATUTE_AUDIT_PATTERN.finditer(text)]
    # The matched laws should be the real ones, not 'Bst', 'Lit', 'Ziff' etc.
    assert "OR" in laws
    assert "ZGB" in laws
    assert "BGG" in laws
    # And none of the bogus ones should sneak through.
    for bogus in ("BST", "LIT", "ZIFF", "ABS", "LET", "LETT"):
        assert bogus not in laws, f"{bogus} leaked into law slot for: {text!r}"


# ── Top-level handler shape ────────────────────────────────────────

def test_attest_empty_draft_clean(m):
    res = m._handle_attest_response(draft_text="Pure prose, no claims.")
    assert res["ok"] is True
    assert res["citations_found"] == 0
    assert res["issues_count"] == 0
    assert res["issues_by_category"] == {
        "case": 0, "statute": 0, "quote": 0, "date": 0, "grounding": 0,
    }
    # New shape promises both rails even on the no-citation path
    assert "annotated_text" in res
    assert "linked_text" in res


def test_attest_no_case_but_unsourced_quote_flagged(m, monkeypatch, tmp_path):
    """Quote near a citation anchor + not in any cited source: flagged
    by default; audit_quotes=False leaves it alone. The citation itself
    is stubbed unresolved so the test needs no decisions.db (CLAUDE.md,
    invariant 8: tests stay offline).

    2026-05-11: the document-wide scan was switched to opt-in because
    quotes that aren't legal-source claims (party narrative, contract
    text, witness statements) tripped it, and the statute mirror spliced
    footnotes into bodies. 2026-09-05: back on by default for the handler
    (the MCP tool) after the footnote fix measured 0.0 % false positives
    on statute quotes; the REST body keeps opt-in (_AttestBody)."""
    monkeypatch.setattr(m, "_resolve_decision_id_strict", lambda ref: None)
    monkeypatch.setattr(m, "STATUTES_DB_PATH", tmp_path / "absent.db")
    draft = ('BGE 140 III 86 E. 2: '
             '\u201eDies ist ein erfundenes Zitat von mehr als sechzig '
             'Zeichen, das so im Urteil gar nicht vorkommt.\u201c')
    # Default: the invented quote is flagged.
    res = m._handle_attest_response(draft_text=draft)
    assert res["issues_by_category"]["quote"] == 1
    # Explicit opt-out: the same draft raises no quote issue.
    res2 = m._handle_attest_response(draft_text=draft, audit_quotes=False)
    assert res2["issues_by_category"]["quote"] == 0
    # The REST body keeps quote verification opt-in.
    assert m._AttestBody(redacted_text="x").audit_quotes is False


def test_attest_standalone_unsourced_quote_NOT_flagged(m):
    """A long quote with NO citation/statute anchor stays unflagged
    even when audit_quotes=True — the nearby-authority guard is
    still in effect; an unanchored quote is not asserting a
    verifiable source."""
    draft = ('Der Zeuge sagte aus: '
             '\u201eIch war an jenem Abend nicht zu Hause, sondern '
             'unterwegs im Tessin bei meiner Schwester.\u201c '
             'Diese Aussage wurde protokolliert.')
    res = m._handle_attest_response(draft_text=draft, audit_quotes=True)
    assert res["issues_by_category"]["quote"] == 0


def test_attest_quotes_on_by_default_and_opt_out(m, monkeypatch, tmp_path):
    """The handler audits quotes by default (2026-09-05); audit_quotes=False
    skips them while citation + statute + date audits still run. Offline:
    the citation is stubbed unresolved, no decisions.db is opened."""
    monkeypatch.setattr(m, "_resolve_decision_id_strict", lambda ref: None)
    monkeypatch.setattr(m, "STATUTES_DB_PATH", tmp_path / "absent.db")
    draft = ('BGE 140 III 86 E. 2.3 hielt fest: '
             '\u201eDas ist ein langer Satz von mehr als sechzig Zeichen '
             'und steht so im Urteil gar nicht.\u201c')
    res = m._handle_attest_response(draft_text=draft)
    assert res["issues_by_category"]["quote"] == 1
    res = m._handle_attest_response(draft_text=draft, audit_quotes=False)
    assert res["issues_by_category"]["quote"] == 0
    assert "citations_found" in res and "issues_by_category" in res


def test_attest_returns_required_keys(m):
    res = m._handle_attest_response(draft_text="Nothing to audit.")
    for key in (
        "ok", "citations_found", "citations_ok", "issues_count",
        "issues_by_category", "annotated_text", "linked_text",
        "issues", "_note",
    ):
        assert key in res, f"missing key: {key}"


# ── Quote-normalisation helper (unit) ──────────────────────────────

def test_normalise_collapses_whitespace_and_quotes(m):
    raw = '  \u00abHello\u00bb   \nworld\u2014today\u2019s  '
    out = m._normalise_for_quote_match(raw)
    # « » → "; — → -; ’ → '; whitespace collapsed; lowercased
    assert out == '"hello" world-today\'s'


# ── Regression: regex must NOT eat connector words as law slot ────

@pytest.mark.parametrize("connector", ["und", "oder", "et", "ou", "bzw"])
def test_connector_after_absatz_not_flagged_as_law(m, connector, monkeypatch, tmp_path):
    """Bug fix: regex used re.IGNORECASE on the law slot, so "Art. 18 Abs. 1 und"
    parsed as law='und'. Connector words are now case-sensitive-rejected via
    invalid-laws guard AND can't match the (uppercase-required) law slot.
    """
    # Force the audit to run by pretending statutes.db exists; the body
    # itself never calls the DB because no Art. <num> <UPPERCASE-LAW>
    # match should fire.
    fake_db = tmp_path / "fake.db"
    fake_db.touch()
    monkeypatch.setattr(m, "STATUTES_DB_PATH", fake_db)
    monkeypatch.setattr(m, "_fetch_statute_text",
                        lambda **kw: {"sr_number": "x", "text_de": "y"})
    draft = f"Art. 18 Abs. 1 {connector} Art. 32 Abs. 1 OR sind klar."
    issues = m._audit_statutes(draft)
    flagged_laws = [i.get("law_code") for i in issues]
    assert connector not in [(s or "").lower() for s in flagged_laws]


# ── Regression: pinpoint full-text fallback ────────────────────────

@pytest.mark.parametrize("body,pinpoint,expected", [
    ("Erwägungen: ... E. 2.3. Wie das BGer hielt fest", "2.3", True),
    ("siehe consid. 4.1 hierzu", "4.1", True),
    ("siehe Erw. 5.2 hierzu", "5.2", True),
    ("(E. 6) ist klar", "6", True),
    ("nichts Erwägendes hier", "2.3", False),
    ("Erwägung 99.99 wird nicht erwähnt", "99.99", False),
])
def test_pinpoint_in_text_authoritative(m, body, pinpoint, expected):
    assert m._pinpoint_in_text(body, pinpoint) is expected


# ── Regression: strict resolver must not run LIKE %x% scan ─────────

@pytest.mark.parametrize("draft,citation_start,expected_starts_with", [
    ("Der Vermieter haftet für Mängel an der Mietsache (BGE 140 III 86).",
     len("Der Vermieter haftet für Mängel an der Mietsache ("),
     "Der Vermieter haftet"),
    ("Erstens. Zweitens haftet der Vermieter (BGE 140 III 86).",
     len("Erstens. Zweitens haftet der Vermieter ("),
     "Zweitens haftet"),
    # See-cite — should return None
    ("Vgl. BGE 140 III 86", 5, None),
    # Too short — None
    ("Ja (BGE 140 III 86)", 4, None),
])
def test_extract_preceding_claim(m, draft, citation_start, expected_starts_with):
    out = m._extract_preceding_claim(draft, citation_start)
    if expected_starts_with is None:
        assert out is None
    else:
        assert out is not None
        assert out.startswith(expected_starts_with)


def test_grounding_audit_unavailable_without_api_key(m, monkeypatch):
    monkeypatch.setattr(m, "ANTHROPIC_API_KEY", None)
    out, meta = m._audit_grounding("draft", [], [])
    assert out == []
    assert meta["available"] is False
    assert meta.get("error") == "anthropic_api_key_missing"


def test_grounding_audit_calls_judge_with_pairs(m, monkeypatch):
    """End-to-end stub: synthesize verified citations + cited sources,
    monkeypatch the Sonnet call, confirm pairs are built and verdicts
    flow through to issues."""
    monkeypatch.setattr(m, "ANTHROPIC_API_KEY", "sk-test")
    captured: dict = {}

    def fake_judge(pairs):
        captured["pairs"] = pairs
        # Verdicts: pair 0 = grounded, pair 1 = unrelated
        return [
            {"index": 0, "supports": "yes", "confidence": 0.9, "reasoning": "matches"},
            {"index": 1, "supports": "unrelated", "confidence": 0.8,
             "reasoning": "off-topic"},
        ]

    monkeypatch.setattr(m, "_judge_grounding_batched", fake_judge)

    claim_a = "Der Vermieter haftet f\u00fcr M\u00e4ngel an der Mietsache "
    cit_a = "BGE 140 III 86"
    sep = ". "
    claim_b = "Schadenersatz nach Art. 41 OR setzt Verschulden voraus "
    cit_b = "BGer 4A_747/2012"
    draft = claim_a + cit_a + sep + claim_b + cit_b + "."
    span_a = (len(claim_a), len(claim_a) + len(cit_a))
    pre_b = len(claim_a) + len(cit_a) + len(sep)
    span_b = (pre_b + len(claim_b), pre_b + len(claim_b) + len(cit_b))
    citations = [
        {"span": span_a, "full_match": cit_a, "_status": "OK",
         "_resolved_id": "bge_BGE_140_III_86", "pinpoint": None},
        {"span": span_b, "full_match": cit_b, "_status": "OK",
         "_resolved_id": "bger_4A_747_2012", "pinpoint": None},
    ]
    sources = [
        {"decision_id": "bge_BGE_140_III_86",
         "regeste": "Der Vermieter haftet für Mängel an der Sache.",
         "full_text": "", "paragraphs": []},
        {"decision_id": "bger_4A_747_2012",
         "regeste": "Verfahrensrechtliche Grundsätze zur Beschwerde.",
         "full_text": "", "paragraphs": []},
    ]
    issues, meta = m._audit_grounding(draft, citations, sources)
    assert meta["checked"] == 2, f"expected 2 pairs, meta={meta}"
    assert meta.get("error") is None
    # Pair 1 (unrelated) → flagged; pair 0 (yes) → not flagged
    assert len(issues) == 1
    assert issues[0]["category"] == "grounding"
    assert issues[0]["supports"] == "unrelated"
    assert "Schadenersatz" in issues[0]["claim"]


def test_grounding_audit_judge_failure_is_soft(m, monkeypatch):
    """If the Sonnet API errors, the audit must NOT throw — return []
    and surface the failure in meta."""
    monkeypatch.setattr(m, "ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(m, "_judge_grounding_batched", lambda pairs: None)
    claim = "Eine ausreichend lange Behauptung mit Substanz "
    cit_str = "BGE 140 III 86"
    draft_full = claim + cit_str + "."
    span = (len(claim), len(claim) + len(cit_str))
    citations = [{
        "span": span, "full_match": cit_str,
        "_status": "OK", "_resolved_id": "bge_BGE_140_III_86", "pinpoint": None,
    }]
    sources = [{"decision_id": "bge_BGE_140_III_86",
                "regeste": "x" * 80, "full_text": "", "paragraphs": []}]
    out, meta = m._audit_grounding(draft_full, citations, sources)
    assert out == []
    assert meta.get("error") == "judge_unavailable"


def test_attest_skips_grounding_when_not_requested(m):
    res = m._handle_attest_response(draft_text="Pure prose.")
    assert res["grounding_meta"]["requested"] is False
    assert "grounding" in res["issues_by_category"]


def test_canonical_id_prefix_skips_like(m):
    """get_decision_by_id and _resolve_decision_id must NOT fall through
    to LIKE %x% scan when the input looks like a canonical decision_id
    (has a known court prefix). Such inputs either hit the exact-match
    path or are fabricated; the LIKE scan costs ~2 s on the live 970k
    table and produces nothing useful."""
    for cid in (
        "bge_BGE_999_IV_999",       # fabricated BGE
        "bger_4A_99999/9999",        # fabricated BGer
        "bvger_X-1234/2099",         # fabricated BVGer
        "zh_obergericht_NONE",       # fabricated cantonal
    ):
        assert m._CANONICAL_ID_PREFIX_RE.match(cid), \
            f"prefix regex should match canonical id {cid!r}"
    # Non-canonical inputs should NOT match (so LIKE fallback runs)
    for raw in ("4A_747/2012", "ABC.123/2099", "random text"):
        assert not m._CANONICAL_ID_PREFIX_RE.match(raw), \
            f"prefix regex should NOT match raw input {raw!r}"


def test_pragma_helper_rejects_bad_identifier(m, monkeypatch):
    """_sqlite_has_column must reject anything that isn't a clean
    identifier — defence in depth against future callers."""
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE t (a INTEGER)")
    assert m._sqlite_has_column(conn, "t", "a") is True
    assert m._sqlite_has_column(conn, "t; DROP TABLE t", "a") is False
    assert m._sqlite_has_column(conn, "1bad", "a") is False
    assert m._sqlite_has_column(conn, "", "a") is False
    conn.close()


def test_statute_text_cache_hits(m, monkeypatch, tmp_path):
    """Second call with the same args must be served from cache (no DB
    round-trip). Verified by counting connection opens."""
    monkeypatch.setattr(m, "_statute_text_cache", {})
    calls = {"n": 0}

    class FakeRow(dict):
        def __getitem__(self, k):
            return super().__getitem__(k)

    class FakeConn:
        def execute(self, sql, params=()):
            calls["n"] += 1
            class _C:
                def fetchone(_self):
                    if "FROM laws" in sql:
                        return FakeRow({"sr_number": "220"})
                    return FakeRow({"article_num": "41", "text": "TEXT", "lang": "de"})
            return _C()
        def close(self): pass

    monkeypatch.setattr(m, "_get_statutes_conn", lambda: FakeConn())
    r1 = m._fetch_statute_text(law_code="OR", article="41")
    r2 = m._fetch_statute_text(law_code="OR", article="41")
    assert r1 == r2
    assert r1.get("sr_number") == "220"
    # Three statements on the first call (laws lookup, ONE schema probe for
    # the `section` / `footnote` columns of the 2026-09 rebuild, article
    # text), ZERO on the cached second call.
    assert calls["n"] == 3, f"expected 3 DB calls (uncached only), got {calls['n']}"


def test_strict_resolver_returns_none_on_miss(m, monkeypatch):
    """The whole point of _resolve_decision_id_strict is to return None
    fast on a miss. Confirm it doesn't fall through to LIKE."""
    class FakeRow:
        def __init__(self, val): self.val = val
        def __getitem__(self, idx): return self.val

    calls = []
    class FakeConn:
        def execute(self, sql, params=()):
            calls.append((sql, params))
            class _Cur:
                def fetchone(self_): return None
            return _Cur()
        def close(self): pass

    monkeypatch.setattr(m, "get_db", lambda: FakeConn())
    out = m._resolve_decision_id_strict("bge_BGE_999_IV_999")
    assert out is None
    # Every SQL should be exact-match — none must contain LIKE
    for sql, _ in calls:
        assert "LIKE" not in sql.upper(), f"strict resolver leaked LIKE: {sql}"


# ── 2026-09-09: the audit gets a denominator, and its worst false alarm goes ─
#
# Before: _audit_quotes pooled the first 8,000 characters of each cited
# decision's full text, so a correct quotation from E. 5 of a long judgment
# was reported as a fabrication; quotes_checked counted every 30-400 char
# span although only anchored spans of 60+ chars are examined; and with no
# statutes.db the statute audit returned [] while statutes_checked reported
# N, which read as "N passed". The `ledger` block makes every rail report
# found / examined / verified, so ok=true over nothing examined is visible.

LONG_HEAD = ("Sachverhalt. Die Beschwerdeführerin verlangt Schadenersatz. " * 160)
DEEP_SENTENCE = ("Der Vermieter haftet für Mängel an der Mietsache nur, soweit "
                 "er diese bei Vertragsschluss kannte oder hätte kennen müssen.")
LONG_TAIL = (" Weitere Erwägungen folgen hier ohne Bedeutung für den Fall. " * 40)
LONG_FULL_TEXT = LONG_HEAD + "E. 5.2 " + DEEP_SENTENCE + LONG_TAIL
assert LONG_FULL_TEXT.index(DEEP_SENTENCE) > 9_000, "fixture must sit beyond the old 8k bound"


@pytest.fixture
def long_decision(m, monkeypatch, tmp_path):
    """One resolvable BGE whose only copy of the quoted sentence sits ~9.5k
    characters into full_text; no structure rows, no statutes.db."""
    did = "bge_BGE_140_III_86"
    row = {"decision_id": did, "court": "bge", "decision_date": "2014-01-14",
           "language": "de", "regeste": "", "full_text": LONG_FULL_TEXT}
    monkeypatch.setattr(m, "_resolve_decision_id_strict",
                        lambda ref: did if "140_III_86" in (ref or "") else None)
    monkeypatch.setattr(m, "_get_decision_strict", lambda d: row if d == did else None)
    monkeypatch.setattr(m, "_fetch_structure_paragraphs", lambda d: [])
    monkeypatch.setattr(m, "STATUTES_DB_PATH", tmp_path / "absent.db")
    return did


def test_quote_beyond_8k_chars_is_found_and_attributed(m, long_decision):
    draft = f"BGE 140 III 86 hält fest: „{DEEP_SENTENCE}“"
    res = m._handle_attest_response(draft_text=draft)
    assert res["issues_by_category"]["quote"] == 0
    assert res["ok"] is True
    q = res["ledger"]["quotations"]
    assert (q["found"], q["examined"], q["verbatim"], q["not_found"]) == (1, 1, 1, 0)
    [hit] = q["matches"]
    assert hit["matched_source"] == long_decision
    assert hit["normalisation"] == "fold-v1"
    # The offset is into the normalised source text and points at the quote.
    norm_src = m._normalise_for_quote_match(LONG_FULL_TEXT)
    assert hit["offset"] > 8_000
    assert norm_src[hit["offset"]:].startswith(m._normalise_for_quote_match(DEEP_SENTENCE))
    # Negative control: a changed word at the same depth is still caught, and
    # the issue names the sources that were searched.
    bad = draft.replace("kannte oder hätte kennen müssen", "kannte oder kennen konnte")
    res = m._handle_attest_response(draft_text=bad)
    assert res["issues_by_category"]["quote"] == 1
    [issue] = [i for i in res["issues"] if i["category"] == "quote"]
    assert issue["searched_sources"] == [long_decision]
    assert issue["searched_sources_count"] == 1
    assert issue["normalisation"] == "fold-v1"
    assert res["ledger"]["quotations"]["not_found"] == 1
    assert res["ledger"]["quotations"]["searched_sources"] == [long_decision]


def test_quote_ledger_counts_examined_short_and_unanchored(m):
    src = [{"decision_id": "bge_BGE_140_III_86",
            "regeste": "Der Vermieter haftet für Mängel an der Sache, soweit dies vereinbart wurde.",
            "full_text": "", "paragraphs": []}]
    draft = (
        # examined + verbatim
        'BGE 140 III 86 E. 2.3: „Der Vermieter haftet für Mängel an der Sache, '
        'soweit dies vereinbart wurde.“ '
        # examined + not found
        'BGE 140 III 86 E. 2.4: „Dieser Satz steht so nicht im Entscheid und ist '
        'tatsächlich frei erfunden worden.“ '
        # short (30-59 chars): found, skipped
        '„Ein kurzer Begriff von unter sechzig Zeichen.“ '
        + "Zwischentext ohne jede Autorität. " * 12 +
        # long, but no citation or statute reference within 250 chars
        '„Ich war an jenem Abend nicht zu Hause, sondern unterwegs im Tessin bei '
        'meiner Schwester und ihrer Familie.“'
    )
    ledger: dict = {}
    issues = m._audit_quotes(draft, src, ledger=ledger)
    assert len(issues) == 1
    assert ledger["found"] == 4
    assert ledger["examined"] == 2
    assert ledger["verbatim"] == 1 and ledger["not_found"] == 1
    assert ledger["skipped_short"] == 1
    assert ledger["skipped_unanchored"] == 1
    assert ledger["matches"][0]["matched_source"] == "bge_BGE_140_III_86"
    # Repeated quotations inherit the first verdict without a second issue.
    # The copies are separated by more than the 250-char anchor radius so the
    # second copy's citations do not anchor the first copy's standalone quote.
    ledger2: dict = {}
    issues2 = m._audit_quotes(draft + " Fliesstext. " * 30 + draft, src, ledger=ledger2)
    assert len(issues2) == 1
    assert ledger2["examined"] == 4 and ledger2["verbatim"] == 2 and ledger2["not_found"] == 2
    assert ledger2["skipped_short"] == 2 and ledger2["skipped_unanchored"] == 2


def test_quotes_checked_is_the_examined_count(m, monkeypatch, tmp_path):
    """quotes_checked used to count every 30-400 char span; it is now the
    number of spans the rail examined (anchored, 60+ chars)."""
    monkeypatch.setattr(m, "STATUTES_DB_PATH", tmp_path / "absent.db")
    draft = ('„Ein kurzer Begriff von unter sechzig Zeichen.“ steht hier allein. '
             'Art. 41 OR lautet: „Wer einem andern widerrechtlich Schaden zufügt, '
             'sei es mit Absicht, sei es aus Fahrlässigkeit, wird ihm zum Ersatze '
             'verpflichtet.“')
    res = m._handle_attest_response(draft_text=draft)
    assert res["citations_found"] == 0
    assert res["quotes_checked"] == 1
    assert res["ledger"]["quotations"]["found"] == 2
    assert res["ledger"]["quotations"]["skipped_short"] == 1
    res = m._handle_attest_response(draft_text=draft, audit_quotes=False)
    assert res["quotes_checked"] == 0
    assert res["ledger"]["quotations"]["audited"] is False


def test_statutes_not_checked_without_db_is_visible(m, monkeypatch, tmp_path):
    """No statutes.db: the audit still returns no issues, but the ledger says
    the references were not checked and statutes_checked no longer claims N."""
    monkeypatch.setattr(m, "STATUTES_DB_PATH", tmp_path / "absent.db")
    draft = "Siehe Art. 41 OR und Art. 256 OR sowie nochmals Art. 41 OR und Art. 999 ZZZ."
    ledger: dict = {}
    assert m._audit_statutes(draft, ledger=ledger) == []
    assert ledger == {"found_unique": 3, "verified": 0, "unknown_law": 0,
                      "missing_article": 0, "not_checked": 3}
    res = m._handle_attest_response(draft_text=draft, audit_quotes=False)
    assert res["ok"] is True
    assert res["statutes_checked"] == 0
    s = res["ledger"]["statutes"]
    assert s["found_unique"] == 3 and s["not_checked"] == 3 and s["checked"] is False
    assert "not_checked" in res["_note"]


def test_statute_ledger_counts_verified_and_failures(m, monkeypatch, tmp_path):
    fake_db = tmp_path / "statutes.db"
    fake_db.touch()
    monkeypatch.setattr(m, "STATUTES_DB_PATH", fake_db)

    def fake_fetch(**kw):
        if kw["law_code"] == "ZZZ":
            return {}
        if kw["article"] == "999":
            return {"sr_number": "220"}
        return {"sr_number": "220", "text_de": "x", "text": "x"}

    monkeypatch.setattr(m, "_fetch_statute_text", fake_fetch)
    draft = "Art. 41 OR, Art. 999 OR, Art. 1 ZZZ und nochmals Art. 41 OR."
    ledger: dict = {}
    issues = m._audit_statutes(draft, ledger=ledger)
    assert sorted(i["problem"] for i in issues) == ["article_not_in_law", "law_abbreviation_unknown"]
    assert ledger == {"found_unique": 3, "verified": 1, "unknown_law": 1,
                      "missing_article": 1, "not_checked": 0}
    res = m._handle_attest_response(draft_text=draft, audit_quotes=False)
    assert res["statutes_checked"] == 3
    assert res["ledger"]["statutes"]["verified"] == 1


def test_date_ledger_has_a_denominator(m):
    cits = [
        {"span": (0, 14), "full_match": "BGer 4A_1/2024", "_decision_date": "2024-03-12"},
        {"span": (30, 44), "full_match": "BGer 4A_2/2024", "_decision_date": "2024-03-12"},
        {"span": (60, 74), "full_match": "BGer 4A_3/2024", "_decision_date": ""},
    ]
    draft = ("BGer 4A_1/2024 vom 12.03.2024; " "BGer 4A_2/2024 vom 15.03.2024; "
             "BGer 4A_3/2024 vom 01.01.2024")
    ledger: dict = {}
    issues = m._audit_dates(draft, cits, ledger=ledger)
    assert len(issues) == 1
    assert ledger == {"found": 3, "plausible": 1, "issues": 1, "not_checked": 1}


def test_out_of_scope_references_are_counted_not_flagged(m, monkeypatch, tmp_path):
    monkeypatch.setattr(m, "STATUTES_DB_PATH", tmp_path / "absent.db")
    draft = ("Vgl. Urteil des Obergerichts des Kantons Zürich LB190012 vom 3. Mai 2020, "
             "OGer ZH LB200015, arrêt du Tribunal cantonal VD HC/2020/123, "
             "EGMR 12345/12 sowie EuGH C-131/12.")
    n, samples = m._scan_out_of_scope_references(draft, [])
    assert n == 5, samples
    # Samples carry the whole reference (court, docket, date), not a
    # court name cut off where the docket begins.
    assert samples == [
        "Urteil des Obergerichts des Kantons Zürich LB190012 vom 3. Mai 2020",
        "OGer ZH LB200015",
        "arrêt du Tribunal cantonal VD HC/2020/123",
        "EGMR 12345/12",
        "EuGH C-131/12",
    ]
    res = m._handle_attest_response(draft_text=draft)
    assert res["ok"] is True and res["issues_count"] == 0
    assert res["citations_found"] == 0
    c = res["ledger"]["citations"]
    assert c["unknown_pattern"] == 5 and c["out_of_scope"] == samples
    assert "out_of_scope" in res["_note"]
    # A recognised federal citation is never counted as out of scope, and
    # prose without references yields nothing.
    assert m._scan_out_of_scope_references("BGE 140 III 86 E. 2.3", m._parse_citations_in_text("BGE 140 III 86 E. 2.3")) == (0, [])
    assert m._scan_out_of_scope_references("Reiner Fliesstext ohne Verweise.", []) == (0, [])


@pytest.mark.parametrize("ref", [
    # BVGer in its French and Italian names, with and without a docket
    "arrêt du Tribunal administratif fédéral A-1234/2020 du 5 mai 2020",
    "sentenza del Tribunale amministrativo federale C-5678/2019",
    "Urteil des Bundesverwaltungsgerichts A-1234/2020",
    "Urteil des BVGer A-1234/2020 vom 5. Mai 2020",
    "arrêt du TAF A-1234/2020",
    # EVG / TFA, in every spelling the drafts use
    "Urteil des Eidgenössischen Versicherungsgerichts I 123/04 vom 3. März 2005",
    "Eidgenössisches Versicherungsgericht",
    "Eidg. Versicherungsgericht",
    "Urteil des EVG I 123/04",
    "arrêt du TFA du 2 mai 2004",
    # BGer / TF and the BStGer, whose dotted docket shape is shared with
    # cantonal courts
    "arrêt du Tribunal fédéral 4A_123/2020",
    "sentenza del Tribunale federale 4A_123/2020",
    "arrêt du TF 4A_123/2020 du 3 mars 2021",
    "Urteil des Bundesstrafgerichts BB.2020.12",
    "Urteil des BStGer, Beschwerdekammer, BB.2020.12 vom 1. Mai 2020",
    "arrêt du TPF BB.2020.12",
])
def test_out_of_scope_scanner_does_not_count_federal_courts(m, ref):
    recognised = m._parse_citations_in_text(ref) or []
    assert m._scan_out_of_scope_references(ref, recognised) == (0, []), ref


@pytest.mark.parametrize("ref", [
    # Corroborated docket shapes: ZH OGer (LB), ZH VGer (VB.), GR
    # Kantonsgericht (ZK1 2020 12), BE Obergericht (ZK 20 99), SG (BZ.),
    # VD (HC/…), GE (ATA/…); canton names with a dot ("St. Gallen"), a
    # hyphen ("Basel-Stadt") or a lowercase adjective ("vaudois") sit
    # between court and docket.
    "Urteil des Obergerichts des Kantons Zürich LB190012 vom 3. März 2020",
    "Urteil des Verwaltungsgerichts des Kantons Zürich VB.2019.00123 vom 3. März 2020",
    "Urteil des Kantonsgerichts Graubünden ZK1 2020 12 vom 3. März 2020",
    "Urteil des Obergerichts des Kantons Bern ZK 20 99 vom 1. Juli 2020",
    "Urteil des Kantonsgerichts St. Gallen BZ.2020.12",
    "Urteil des Appellationsgerichts Basel-Stadt BEZ.2020.12",
    "arrêt du Tribunal cantonal vaudois HC/2018/391 du 12 mars 2019",
    "arrêt du Tribunal cantonal du canton de Vaud HC/2018/391 du 12 mars 2019",
    "arrêt de la Cour de justice ATA/655/2017 du 30 mai 2017",
])
def test_out_of_scope_sample_is_the_whole_reference(m, ref):
    # One reference, one count, and the sample is the citation itself —
    # never the court name truncated where the docket begins.
    assert m._scan_out_of_scope_references(ref, []) == (1, [ref]), ref


def test_cjeu_full_name_is_one_european_reference(m):
    # "Cour de justice" alone is a Geneva court; followed by "de l'Union
    # européenne" it is the CJEU, counted once, with its case number.
    ref = "arrêt de la Cour de justice de l'Union européenne du 13 mai 2014, C-131/12"
    assert m._scan_out_of_scope_references(ref, []) == (
        1, ["Cour de justice de l'Union européenne du 13 mai 2014, C-131/12"])


def test_out_of_scope_samples_list_citation_shaped_references_first(m):
    draft = ("Das Bezirksgericht Zürich wies die Klage ab; das Obergericht "
             "bestätigte (Urteil des Obergerichts des Kantons Zürich LB190012 "
             "vom 3. Mai 2020).")
    n, samples = m._scan_out_of_scope_references(draft, [])
    assert n == 3
    assert samples[0] == "Urteil des Obergerichts des Kantons Zürich LB190012 vom 3. Mai 2020"
    assert samples[1:] == ["Bezirksgericht Zürich", "Obergericht"]


def test_ledger_present_in_both_branches_with_stable_keys(m, monkeypatch, tmp_path):
    monkeypatch.setattr(m, "STATUTES_DB_PATH", tmp_path / "absent.db")
    monkeypatch.setattr(m, "_resolve_decision_id_strict", lambda ref: None)
    keys = {"citations", "pinpoints", "quotations", "statutes", "dates",
            "certifies", "does_not_certify"}
    for draft in ("Pure prose, no claims.", "Siehe BGE 999 IV 999 E. 1."):
        res = m._handle_attest_response(draft_text=draft)
        assert set(res["ledger"]) == keys
        assert set(res["ledger"]["citations"]) == {"found", "resolved", "unresolved",
                                                   "unknown_pattern", "out_of_scope"}
        assert set(res["ledger"]["pinpoints"]) == {"found", "verified_structure",
                                                   "verified_text", "unverified", "invalid"}
        for key in ("statutes_checked", "quotes_checked", "issues_by_category",
                    "warnings_count", "citations_ok"):
            assert key in res
    res = m._handle_attest_response(draft_text="Siehe BGE 999 IV 999 E. 1.")
    assert res["ledger"]["citations"] == {"found": 1, "resolved": 0, "unresolved": 1,
                                          "unknown_pattern": 0, "out_of_scope": []}
    assert res["ok"] is False and res["citations_found"] == 1


def test_pinpoint_ledger_uses_verify_pinpoint_method(m, long_decision, monkeypatch):
    # No structure rows + heading in the body → verified by text.
    res = m._handle_attest_response(draft_text="BGE 140 III 86 E. 5.2 ist einschlägig.")
    p = res["ledger"]["pinpoints"]
    assert p == {"found": 1, "verified_structure": 0, "verified_text": 1,
                 "unverified": 0, "invalid": 0}
    # No structure rows + no heading → unverified: a warning, not an issue.
    res = m._handle_attest_response(draft_text="BGE 140 III 86 E. 9.9 ist einschlägig.")
    assert res["ok"] is True and res["warnings_count"] == 1
    assert res["ledger"]["pinpoints"]["unverified"] == 1
    # Structure rows decide: E. 4.1 verified by structure, E. 7.1 invalid.
    monkeypatch.setattr(m, "_fetch_structure_paragraphs",
                        lambda d: [{"e_number": "4", "text": "x"}, {"e_number": "4.1", "text": "y"}])
    res = m._handle_attest_response(draft_text="BGE 140 III 86 E. 4.1 und BGE 140 III 86 E. 7.1.")
    p = res["ledger"]["pinpoints"]
    assert p == {"found": 2, "verified_structure": 1, "verified_text": 0,
                 "unverified": 0, "invalid": 1}
    assert res["issues_by_category"]["case"] == 1 and res["citations_ok"] == 1


def test_certification_boundary_is_stated_everywhere(m, monkeypatch, tmp_path):
    monkeypatch.setattr(m, "STATUTES_DB_PATH", tmp_path / "absent.db")
    monkeypatch.setattr(m, "_resolve_decision_id_strict", lambda ref: None)
    [t] = [t for t in m._list_tools() if t.name == "attest_response"]
    assert "does not certify that no relevant authority is missing" in t.description
    assert len(t.description) <= 1024
    for draft in ("Pure prose.", "Siehe BGE 999 IV 999."):
        res = m._handle_attest_response(draft_text=draft)
        assert "does not certify that no relevant authority is missing" in res["_note"]
    from pathlib import Path
    html = (Path(__file__).resolve().parents[2] / "docs" / "api" / "index.html").read_text(encoding="utf-8")
    assert html.count("does not certify that no relevant authority is missing") == 2  # HTML + en
    for needle in ("bescheinigt nicht, dass keine einschlägige Autorität fehlt",
                   "ne certifie pas qu\\'aucune autorité pertinente ne manque",
                   "non certifica che non manchi alcuna autorità rilevante",
                   "na certifitgescha betg che nagina autoritad relevanta manca"):
        assert needle in html, needle
