"""ZH appeal-chain links (2026-10-05).

A practitioner's list of 16 appeals from Zürich labour rulings (Arbeitsgericht
→ Obergericht → Bundesgericht) found 4 of 13 Obergericht links and 0 of 3 BGer
links in find_appeal_chain. Four causes, each pinned here:

1. The header finder took the first "Gegenstand" anywhere in the text; in an
   Obergericht judgment (no Gegenstand heading) that is a word in the
   reasoning, so the real header "Berufung gegen ... (AN220047-L)" was never read.
2. A PDF line wrap split the docket at its hyphen: "(AN220047- L)".
3. ZH Geschäftsnummern carry a court letter and document suffix that the
   stored docket lacks: BGer cites "(LA200010-O/U)", the row is "LA200010";
   the Obergericht cites "(AH240003-G)", the row is "AH240003". The ruling's
   own caption supplies the alias.
4. A link whose lower instance postdates the higher one joins two different
   cases under the same number (LA210028 of 2021 → an AN210006-L of 2022).
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import decision_ref  # noqa: E402
from search_stack.build_reference_graph import (  # noqa: E402
    _resolve_via_docket_aliases,
    build_graph,
    caption_docket_aliases,
)
from search_stack.reference_extraction import extract_prior_instance  # noqa: E402


OGER_HEADER = (
    "Obergericht des Kantons Zürich\n"
    "I. Zivilkammer\n"
    "Geschäfts-Nr.: LA240031-O/U\n"
    "Beschluss und Urteil vom 29. Juli 2025\n"
    "in Sachen\nA._____,\nKlägerin und Berufungsklägerin\n"
    "betreffend arbeitsrechtliche Forderung\n"
    "Berufung gegen einen Beschluss und ein Urteil des Arbeitsgerichtes Zürich,\n"
    "3. Abteilung, im vereinfachten Verfahren vom 11. November 2024\n"
    "(AN220047-L)\n\n- 2 -\nRechtsbegehren:\n"
)
LATE_GEGENSTAND = "x " * 4000 + "Gegenstand des parallel geführten Berufungsverfahrens (LA240029-O)."


# ── 1. header finder ────────────────────────────────────────────────


def test_body_mention_of_gegenstand_does_not_hide_the_header():
    assert extract_prior_instance(OGER_HEADER + LATE_GEGENSTAND) == ["AN220047_L"]


def test_bger_gegenstand_heading_still_used():
    text = (
        "Bundesgericht\nUrteil vom 20. Juli 2021\nVerfahrensbeteiligte\nA.,\n"
        "Gegenstand\n\nLohndiskriminierung,\n\n"
        "Beschwerde gegen das Urteil des Obergerichts des Kantons Zürich, "
        "I. Zivilkammer, vom 29. Oktober 2020 (LA200010-O/U).\n\nSachverhalt:\n"
    )
    assert extract_prior_instance(text) == ["LA200010_O_U"]


def test_flattened_header_bare_gegenstand_near_top_still_used():
    text = (
        "Bundesgericht Urteil vom 1. März 2020 Gegenstand Arbeitsvertrag, "
        "Beschwerde gegen das Urteil des Obergerichts des Kantons Zürich "
        "vom 2. Mai 2019 (LA180001-O/U). Sachverhalt: A."
    )
    assert extract_prior_instance(text) == ["LA180001_O_U"]


# ── 2. line-wrapped docket ──────────────────────────────────────────


def test_line_wrapped_zh_docket():
    text = OGER_HEADER.replace("(AN220047-L)", "(AN220047- L)")
    assert extract_prior_instance(text) == ["AN220047_L"]


def test_spaced_dash_between_two_dockets_still_splits():
    text = (
        "Gegenstand\nX\nrecours contre l'arrêt de la Cour du 6 août 2024 "
        "(A/1168/2024 AIDSO - ATA/917/2024).\nFaits:\n"
    )
    assert extract_prior_instance(text) == ["A_1168_2024", "ATA_917_2024"]


# ── 3. caption aliases ──────────────────────────────────────────────


def test_caption_alias_obergericht():
    text = "Obergericht des Kantons Zürich\nGeschäfts-Nr.: LA200010-O/U\nUrteil vom"
    assert caption_docket_aliases(canton="ZH", docket_number="LA200010", full_text=text) == [
        "LA200010_O",
        "LA200010_O_U",
    ]


def test_caption_alias_district_letter_and_noise_suffix():
    text = "Arbeitsgericht Zürich\nGeschäfts-Nr.: AN210006-L/U-anonymisiert\n"
    assert caption_docket_aliases(canton="ZH", docket_number="AN210006-L", full_text=text) == [
        "AN210006_L_U"
    ]
    text = "Einzelgericht\nGeschäfts-Nr.: AH240003-G/U\n"
    assert caption_docket_aliases(canton="ZH", docket_number="AH240003", full_text=text) == [
        "AH240003_G",
        "AH240003_G_U",
    ]


def test_caption_alias_requires_zh_and_matching_number():
    text = "Geschäfts-Nr.: LA200010-O/U\n"
    assert caption_docket_aliases(canton="BE", docket_number="LA200010", full_text=text) == []
    assert caption_docket_aliases(canton="ZH", docket_number="LA200011", full_text=text) == []
    late = "x " * 1000 + text
    assert caption_docket_aliases(canton="ZH", docket_number="LA200010", full_text=late) == []


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8"
    )


def _row(decision_id, docket, court, date, text, canton="ZH"):
    return {
        "decision_id": decision_id, "docket_number": docket, "court": court,
        "canton": canton, "language": "de", "decision_date": date,
        "title": "", "regeste": "", "full_text": text,
    }


def _alias_graph(tmp_path: Path) -> Path:
    input_dir = tmp_path / "decisions"
    input_dir.mkdir()
    rows = [
        _row("zh_arbeitsgericht_AN140019-L", "AN140019-L", "zh_arbeitsgericht", "2020-01-20",
             "Arbeitsgericht Zürich\nGeschäfts-Nr.: AN140019-L/U\nUrteil vom 20. Januar 2020\n"),
        _row("zh_obergericht_LA200010", "LA200010", "zh_obergericht", "2020-10-29",
             "Obergericht des Kantons Zürich\nGeschäfts-Nr.: LA200010-O/U\n"
             "Berufung gegen ein Urteil des Arbeitsgerichtes Zürich, 1. Abteilung, vom\n"
             "20. Januar 2020 (AN140019-L)\n\n- 2 -\n"),
        _row("bger_4A_636_2020", "4A_636/2020", "bger", "2021-07-20",
             "Gegenstand\nLohndiskriminierung,\nBeschwerde gegen das Urteil des Obergerichts "
             "des Kantons Zürich, I. Zivilkammer, vom 29. Oktober 2020 (LA200010-O/U).\n"
             "Sachverhalt:\n", canton="CH"),
    ]
    _write_jsonl(input_dir / "sample.jsonl", rows)
    db_path = tmp_path / "reference_graph.db"
    build_graph(input_dir=input_dir, db_path=db_path)
    return db_path


def test_alias_pass_resolves_bger_to_obergericht(tmp_path: Path):
    db_path = _alias_graph(tmp_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    aliases = {r[0] for r in conn.execute("SELECT docket_norm FROM docket_aliases")}
    assert {"LA200010_O", "LA200010_O_U", "AN140019_L_U"} <= aliases
    insert_sql = (
        "INSERT OR IGNORE INTO citation_targets "
        "(source_decision_id, target_ref, target_decision_id, match_type, confidence_score) "
        "VALUES (?, ?, ?, ?, ?)"
    )
    _resolve_via_docket_aliases(conn, insert_sql)
    hit = conn.execute(
        "SELECT target_decision_id, match_type FROM citation_targets "
        "WHERE source_decision_id = 'bger_4A_636_2020' AND target_ref = 'LA200010_O_U'"
    ).fetchall()
    assert [tuple(r) for r in hit] == [("zh_obergericht_LA200010", "docket_alias")]
    # Additive: an exactly resolved ref is not re-resolved through an alias.
    n = conn.execute(
        "SELECT COUNT(*) FROM citation_targets WHERE target_ref = 'AN140019_L'"
    ).fetchone()[0]
    assert n == 1
    before = conn.execute("SELECT COUNT(*) FROM citation_targets").fetchone()[0]
    _resolve_via_docket_aliases(conn, insert_sql)
    assert conn.execute("SELECT COUNT(*) FROM citation_targets").fetchone()[0] == before
    conn.close()


def test_build_graph_runs_the_alias_pass(tmp_path: Path):
    """The pass is wired into the build: a plain build_graph links the BGer
    appeal "(LA200010-O/U)" to the Obergericht row stored as "LA200010"."""
    db_path = _alias_graph(tmp_path)
    conn = sqlite3.connect(db_path)
    hit = conn.execute(
        "SELECT target_decision_id, match_type FROM citation_targets "
        "WHERE source_decision_id = 'bger_4A_636_2020' AND target_ref = 'LA200010_O_U'"
    ).fetchall()
    conn.close()
    assert hit == [("zh_obergericht_LA200010", "docket_alias")]


# ── 4. date-order guard ─────────────────────────────────────────────


def test_chain_drops_lower_instance_dated_after_the_appeal(tmp_path: Path):
    import mcp_server

    input_dir = tmp_path / "decisions"
    input_dir.mkdir()
    rows = [
        # Same number, different case, decided after the appeal.
        _row("zh_arbeitsgericht_AN210006-L", "AN210006-L", "zh_arbeitsgericht", "2022-07-22",
             "Arbeitsgericht Zürich\n1. Abteilung\nGeschäfts-Nr.: AN210006-L/U\n"),
        _row("zh_obergericht_LA210028", "LA210028", "zh_obergericht", "2021-09-28",
             "Obergericht des Kantons Zürich\nGeschäfts-Nr.: LA210028-O/U\n"
             "Berufung gegen ein Urteil des Arbeitsgerichtes Zürich, 4. Abteilung, im\n"
             "vereinfachten Verfahren vom 3. August 2021 (AN210006-L)\n\n- 2 -\n"),
    ]
    _write_jsonl(input_dir / "sample.jsonl", rows)
    db_path = tmp_path / "reference_graph.db"
    build_graph(input_dir=input_dir, db_path=db_path)
    with patch.object(mcp_server, "GRAPH_DB_PATH", db_path), \
            patch.object(mcp_server, "_resolve_decision_id", lambda d: d):
        mcp_server._graph_warned = False
        down = mcp_server._find_appeal_chain("zh_obergericht_LA210028")
        mcp_server._graph_warned = False
        up = mcp_server._find_appeal_chain("zh_arbeitsgericht_AN210006-L")
    assert down["chain"] == []
    assert up["chain"] == []


def test_chain_dates_ordered():
    import mcp_server

    assert mcp_server._chain_dates_ordered("2020-01-20", "2020-10-29")
    assert mcp_server._chain_dates_ordered("2020-01-20", "2020-01-20")
    assert not mcp_server._chain_dates_ordered("2022-07-22", "2021-09-28")
    assert mcp_server._chain_dates_ordered(None, "2021-09-28")
    assert mcp_server._chain_dates_ordered("2022-07-22", "")


# ── lookup of the printed Geschäftsnummer ───────────────────────────


def test_obergericht_letter_resolves_to_stored_row():
    for ref in ("LA210028-O", "LA210028-O/U"):
        assert "zh_obergericht_LA210028" in decision_ref.resolve_decision_ref(ref)


def test_district_letter_is_not_dropped():
    cands = decision_ref.resolve_decision_ref("AH240003-G")
    assert all(c.endswith("AH240003-G") for c in cands)


def test_incremental_build_records_aliases_for_new_rows(tmp_path: Path):
    """A ZH Obergericht ruling added by the incremental builder gets its
    caption aliases, so a BGer appeal citing "(LA200010-O/U)" resolves to it
    without waiting for the next full build."""
    from search_stack.build_reference_graph_incremental import build_graph_incremental

    def _decisions_db(path: Path, rows: list[dict]) -> Path:
        path.mkdir(parents=True, exist_ok=True)
        db = path / "decisions.db"
        conn = sqlite3.connect(db)
        conn.execute(
            "CREATE TABLE decisions (decision_id TEXT PRIMARY KEY, docket_number TEXT, "
            "court TEXT, canton TEXT, language TEXT, decision_date TEXT, title TEXT, "
            "regeste TEXT, full_text TEXT)"
        )
        for r in rows:
            conn.execute(
                "INSERT INTO decisions VALUES (?,?,?,?,?,?,?,?,?)",
                (r["decision_id"], r["docket_number"], r["court"], r["canton"],
                 r["language"], r["decision_date"], r["title"], r["regeste"], r["full_text"]),
            )
        conn.commit()
        conn.close()
        return db

    bger = _row("bger_4A_636_2020", "4A_636/2020", "bger", "2021-07-20",
                "Gegenstand\nLohndiskriminierung,\nBeschwerde gegen das Urteil des Obergerichts "
                "des Kantons Zürich, I. Zivilkammer, vom 29. Oktober 2020 (LA200010-O/U).\n"
                "Sachverhalt:\n", canton="CH")
    oger = _row("zh_obergericht_LA200010", "LA200010", "zh_obergericht", "2020-10-29",
                "Obergericht des Kantons Zürich\nGeschäfts-Nr.: LA200010-O/U\nUrteil\n")
    graph = tmp_path / "reference_graph.db"
    build_graph_incremental(decisions_db=_decisions_db(tmp_path / "v1", [bger]),
                            graph_db=graph, output_path=graph)
    stats = build_graph_incremental(decisions_db=_decisions_db(tmp_path / "v2", [bger, oger]),
                                    graph_db=graph, output_path=graph)
    assert stats.get("mode") != "full_bootstrap"
    conn = sqlite3.connect(graph)
    aliases = {r[0] for r in conn.execute(
        "SELECT docket_norm FROM docket_aliases WHERE decision_id = 'zh_obergericht_LA200010'")}
    hit = conn.execute(
        "SELECT target_decision_id FROM citation_targets "
        "WHERE source_decision_id = 'bger_4A_636_2020' AND target_ref = 'LA200010_O_U'"
    ).fetchall()
    conn.close()
    assert {"LA200010_O", "LA200010_O_U"} <= aliases
    assert hit == [("zh_obergericht_LA200010",)]
