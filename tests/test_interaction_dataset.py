"""The impression→fetch join: raw events become labeled ranking pairs.

The server logs impressions (ranked list shown) and fetches (which
decision was pulled) separately, on purpose — REST is not sticky across
workers, so the join cannot happen online without losing most pairs. It
happens here, offline, per session. These tests pin the three properties
that make the output a training set rather than a guess: a fetch is
credited to the impression that showed it, at the rank it was shown; a
later search for the same decision does not steal an earlier
impression's credit; and terminal use (cite/attest) is a distinct,
stronger label than a fetch.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.build_interaction_dataset import build, census  # noqa: E402


def _imp(sid, ts, rsid, ids):
    return {"src": "impression", "sid": sid, "ts": ts, "result_set_id": rsid,
            "query_len": 10,
            "ranked": [{"id": i, "rank": r} for r, i in enumerate(ids)]}


def _call(sid, ts, tool, did, src="mcp"):
    return {"src": src, "sid": sid, "ts": ts, "tool": tool,
            "args": {"decision_id": did}}


def test_a_fetch_is_credited_at_the_rank_it_was_shown():
    rows = [
        _imp("s1", "2026-08-19T10:00:00Z", "rs_1", ["bge_a", "bge_b", "bge_c"]),
        _call("s1", "2026-08-19T10:00:20Z", "get_decision", "bge_c"),
    ]
    [row] = build(rows)
    assert row["result_set_id"] == "rs_1"
    c = {x["id"]: x for x in row["candidates"]}
    assert c["bge_c"]["fetched"] and c["bge_c"]["rank"] == 2
    assert c["bge_c"]["gap_s"] == 20.0
    assert not c["bge_a"]["fetched"] and not c["bge_b"]["fetched"]


def test_terminal_use_is_a_stronger_label_than_a_fetch():
    rows = [
        _imp("s1", "2026-08-19T10:00:00Z", "rs_1", ["bge_a", "bge_b"]),
        _call("s1", "2026-08-19T10:00:05Z", "get_decision", "bge_a"),
        _call("s1", "2026-08-19T10:00:30Z", "cite", "bge_a"),
    ]
    [row] = build(rows)
    c = {x["id"]: x for x in row["candidates"]}
    assert c["bge_a"]["fetched"] and c["bge_a"]["cited"]
    assert not c["bge_b"]["cited"]


def test_a_later_search_does_not_steal_the_earlier_impressions_credit():
    """bge_x is shown twice; a fetch after the SECOND impression must be
    credited to the second, not the first — otherwise rank labels blur
    across searches."""
    rows = [
        _imp("s1", "2026-08-19T10:00:00Z", "rs_1", ["bge_x", "bge_y"]),
        _imp("s1", "2026-08-19T10:05:00Z", "rs_2", ["bge_z", "bge_x"]),
        _call("s1", "2026-08-19T10:05:20Z", "get_decision", "bge_x"),
    ]
    out = {r["result_set_id"]: r for r in build(rows)}
    # rs_2 owns the fetch; rs_1 saw no engagement and is dropped.
    assert "rs_2" in out and "rs_1" not in out
    c = {x["id"]: x for x in out["rs_2"]["candidates"]}
    assert c["bge_x"]["fetched"] and c["bge_x"]["rank"] == 1


def test_fetch_before_the_impression_does_not_count():
    rows = [
        _call("s1", "2026-08-19T09:59:00Z", "get_decision", "bge_a"),
        _imp("s1", "2026-08-19T10:00:00Z", "rs_1", ["bge_a", "bge_b"]),
    ]
    assert build(rows) == [], "a prior fetch is not a response to this list"


def test_cross_session_never_joins():
    """Two people, same decision, different sessions — no join."""
    rows = [
        _imp("s1", "2026-08-19T10:00:00Z", "rs_1", ["bge_a"]),
        _call("s2", "2026-08-19T10:00:10Z", "get_decision", "bge_a"),
    ]
    assert build(rows) == []


def test_unengaged_impressions_are_dropped():
    rows = [_imp("s1", "2026-08-19T10:00:00Z", "rs_1", ["bge_a", "bge_b"])]
    assert build(rows) == [], "an impression nobody touched teaches nothing"


def test_permanent_rows_carry_no_session_by_default():
    rows = [
        _imp("s1", "2026-08-19T10:00:00Z", "rs_1", ["bge_a"]),
        _call("s1", "2026-08-19T10:00:10Z", "get_decision", "bge_a"),
    ]
    [row] = build(rows)
    assert "sid" not in row, "the training set is de-individualised"
    [kept] = build(rows, keep_session=True)
    assert kept["sid"] == "s1", "debug mode may keep it"


def test_traffic_census_counts_the_segments():
    rows = [
        {"src": "mcp", "traffic": "agent"},
        {"src": "rest", "traffic": "crawler"},
        {"src": "rest", "traffic": "crawler"},
        {"src": "impression", "traffic": None},   # not a tool call
    ]
    c = census(rows)
    assert c.get("crawler") == 2 and c.get("agent") == 1


# --- whole-history build, one day at a time (2026-11-26) -------------------
# The nightly run parsed every capture file into memory at once and was
# OOM-killed at 42 GB on 2026-11-25. build_all() streams the files, keeps
# only joinable records, and joins each day against the next.

import json  # noqa: E402

from scripts.build_interaction_dataset import build_all, compact  # noqa: E402


def _write_days(tmp_path, days: dict[str, list[dict]]):
    for day, recs in days.items():
        with open(tmp_path / f"capture_{day}.jsonl", "w") as fh:
            for r in recs:
                fh.write(json.dumps(r) + "\n")
    return tmp_path


def _key(rows):
    return sorted(json.dumps(r, sort_keys=True) for r in rows)


def test_build_all_matches_the_in_memory_join(tmp_path):
    days = {
        "2026-11-24": [
            _imp("s1", "2026-11-24T10:00:00Z", "rs_1", ["a", "b"]),
            _call("s1", "2026-11-24T10:00:09Z", "get_decision", "b"),
            {"src": "rest", "traffic": "crawler", "tool": "search"},
        ],
        "2026-11-25": [
            _imp("s2", "2026-11-25T08:00:00Z", "rs_2", ["c"]),
            _call("s2", "2026-11-25T08:01:00Z", "cite", "c"),
        ],
        "2026-11-26": [],
    }
    _write_days(tmp_path, days)
    flat = [r for recs in days.values() for r in recs]
    out, n, mix = build_all(tmp_path)
    assert _key(out) == _key(build(flat))
    assert n == 5 and mix == census(flat)


def test_a_session_across_midnight_keeps_its_fetch(tmp_path):
    _write_days(tmp_path, {
        "2026-11-24": [_imp("s1", "2026-11-24T23:59:50Z", "rs_1", ["a"])],
        "2026-11-25": [_call("s1", "2026-11-25T00:00:10Z", "get_decision", "a")],
    })
    [row] = build_all(tmp_path)[0]
    assert row["result_set_id"] == "rs_1"
    assert row["candidates"][0]["fetched"] and row["candidates"][0]["gap_s"] == 20.0


def test_a_next_day_search_still_takes_the_credit_and_is_emitted_once(tmp_path):
    _write_days(tmp_path, {
        "2026-11-24": [_imp("s1", "2026-11-24T23:59:00Z", "rs_1", ["x", "y"])],
        "2026-11-25": [
            _imp("s1", "2026-11-25T00:01:00Z", "rs_2", ["x"]),
            _call("s1", "2026-11-25T00:01:30Z", "get_decision", "x"),
        ],
    })
    out = build_all(tmp_path)[0]
    assert [r["result_set_id"] for r in out] == ["rs_2"]


def test_compact_keeps_only_what_the_join_reads():
    imp = _imp("s1", "t", "rs_1", ["a"])
    imp["ranked"][0]["title"] = "long text " * 100
    imp["ranked"].append({"rank": 9})          # no id: never joinable
    imp["query"] = "private query text"
    c = compact(imp)
    assert c["ranked"] == [{"id": "a", "rank": 0}] and "query" not in c
    assert compact(_call(None, "t", "get_decision", "a")) is None   # no session
    assert compact(_call("s1", "t", "search_decisions", "a")) is None
    assert compact({"sid": "s1", "tool": "get_decision", "args": {}}) is None
