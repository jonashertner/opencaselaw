"""Pinpoint bounds for synthetic document-length claims.

_compute_pinpoint built its FTS query from the ENTIRE claim: a pasted letter
became a ~400-term OR chain (decision_id is a post-filter, not in the FTS
index) executed serially per result — the primary mechanism behind the 120 s
document-query timeouts.

Now: claims over PINPOINT_CLAIM_MAX_CHARS are condensed to the
PINPOINT_OR_TOKEN_CAP most informative tokens and skip the phrase pass;
or_tokens are hard-capped for every caller; enrichment parallelises with
per-thread connections and degrades to pinpoint=None on worker failure.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import mcp_server  # noqa: E402
from tests.test_pinpoint_enrichment import _make_structure_db  # noqa: E402


LETTER = (
    "Synthetische Testeingabe. Sehr geehrte Damen und Herren, Gegenstand ist "
    "eine fiktive arbeitsrechtliche Kündigung. Die Testpartei hält die "
    "Kündigung für missbräuchlich im Sinne von Art. 336 OR und macht eine "
    "Sperrfrist nach Art. 336c OR geltend. Diese erfundene Korrespondenz "
    "enthält keine Angaben aus einer echten Anfrage. Wir bitten um eine "
    "rechtliche Stellungnahme. Mit freundlichen Grüssen. "
) * 5


class _RecordingConn:
    """Wraps a sqlite3 conn, recording every MATCH argument."""

    def __init__(self, real):
        self._real = real
        self.match_args: list[str] = []

    def execute(self, sql, params=()):
        if "MATCH" in sql and params:
            self.match_args.append(str(params[0]))
        return self._real.execute(sql, params)

    def __getattr__(self, name):
        return getattr(self._real, name)


def _db(tmp_path):
    return _make_structure_db(tmp_path, [
        ("d_1", "1", "Sachverhalt zur Kündigung."),
        ("d_1", "2.1", "Die Kündigung ist missbräuchlich im Sinne von "
                       "Art. 336 OR; die "
                       "Sperrfrist war missachtet."),
        ("d_1", "3", "Kosten und Entschädigung."),
    ])


def test_long_claim_is_condensed_and_phrase_pass_skipped(tmp_path):
    conn = _RecordingConn(_db(tmp_path))
    try:
        mcp_server._compute_pinpoint("d_1", LETTER, conn=conn)
    finally:
        conn._real.close()
    assert conn.match_args, "no FTS query executed"
    for arg in conn.match_args:
        # no whole-letter phrase, no unbounded OR chain
        assert len(arg) <= 400, f"oversized MATCH arg: {len(arg)} chars"
        assert arg.count(" OR ") <= mcp_server.PINPOINT_OR_TOKEN_CAP - 1, arg
        # the letter itself must never appear as a quoted phrase
        assert "Sehr geehrte" not in arg


def test_long_claim_still_finds_the_relevant_paragraph(tmp_path):
    """Bounding the query must not lose the match — the informative tokens
    (kündigung, missbräuchlich, sperrfrist, 336) drive BM25 to E.2.1."""
    conn = _db(tmp_path)
    try:
        pp = mcp_server._compute_pinpoint("d_1", LETTER, conn=conn)
    finally:
        conn.close()
    assert pp is not None
    assert pp["e_number"] == "2.1"


def test_short_claims_unchanged(tmp_path):
    """Below the cap nothing changes — byte-identical OR chain, phrase pass on."""
    conn = _RecordingConn(_db(tmp_path))
    claim = "missbräuchliche Kündigung Sperrfrist"
    try:
        mcp_server._compute_pinpoint("d_1", claim, conn=conn)
    finally:
        conn._real.close()
    # first MATCH arg is the phrase pass, verbatim
    assert conn.match_args[0] == f'"{claim}"'


def test_statute_digits_survive_condensation():
    toks = [t for t in __import__("re").findall(r"\w+", LETTER) if len(t) > 2]
    scores = mcp_server._rank_query_tokens([t.lower() for t in toks], LETTER)
    ranked = sorted(zip(toks, scores), key=lambda x: -x[1])
    top = {t.lower() for t, _ in ranked[: mcp_server.PINPOINT_OR_TOKEN_CAP]}
    assert "336" in top


def test_parallel_and_sequential_agree(tmp_path, monkeypatch):
    db_file = tmp_path / "structure.db"
    _db(tmp_path).close()

    def _fresh():
        c = sqlite3.connect(str(db_file), check_same_thread=False)
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setattr(mcp_server, "_get_structure_conn", _fresh)
    claim = "missbräuchliche Kündigung Mutterschaftsurlaub Art. 336 OR"

    def run(workers):
        monkeypatch.setattr(mcp_server, "PINPOINT_MAX_WORKERS", workers)
        rs = [{"decision_id": "d_1"}, {"decision_id": "d_none"}]
        mcp_server._pinpoint_enrich_results(rs, claim, top_n=2)
        return [(r.get("pinpoint") or {}).get("e_number") for r in rs]

    assert run(1) == run(4)


def test_worker_failure_degrades_to_none_not_error(tmp_path, monkeypatch):
    db_file = tmp_path / "structure.db"
    _db(tmp_path).close()
    calls = {"n": 0}

    def _flaky():
        calls["n"] += 1
        c = sqlite3.connect(str(db_file), check_same_thread=False)
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setattr(mcp_server, "_get_structure_conn", _flaky)

    def boom(did, claim, **kw):
        raise RuntimeError("worker exploded")

    monkeypatch.setattr(mcp_server, "_compute_pinpoint", boom)
    rs = [{"decision_id": "d_1"}]
    # must not raise
    mcp_server._pinpoint_enrich_results(rs, "Kündigung missbräuchlich", top_n=1)
    assert rs[0]["pinpoint"] is None
