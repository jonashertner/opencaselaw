"""Which source each canton's laws come from, and when to say so loudly.

`build()` composes the cantonal corpus by choosing ONE file per canton:
the direct shard scraped from the canton's own portal if there is one,
the LexFind PDF fallback otherwise. The choice is wholesale, so a direct
shard that exists but is truncated does not merge with the fallback — it
replaces it. On 2026-08-19 a stale 3-law ZH shard displaced 1,374 LexFind
laws and flipped 22 other cantons to the fallback, and the only visible
symptom was the total going UP (15,608 -> 28,957), because the fallback
indexes more instruments than the portals do.

These tests pin the selection rule and the warning that makes a
suspicious override audible. ZH is the one exception: its portal holds a
fraction of the collection, so it adds LexFind's in-force laws it lacks
(LEXFIND_COMPLEMENT_CANTONS).
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from search_stack.build_cantonal_laws_db import build  # noqa: E402


def _law(canton, sr, title, pad=0):
    return {"canton": canton, "sr_number": sr, "title": title,
            "language": "de", "full_text": "x" * pad,
            "articles": [{"article_num": "1", "heading": title,
                          "text": "Der Text."}]}


def _shard(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")


def _laws_in(db: Path):
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return {(r[0], r[1]): r[2] for r in
                con.execute("SELECT canton, sr_number, title FROM laws")}
    finally:
        con.close()


def _build(tmp_path):
    db = tmp_path / "out" / "cantonal_laws.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    build(tmp_path / "direct", tmp_path / "lexfind", db)
    return db


def test_a_canton_without_a_direct_shard_falls_back_to_lexfind(tmp_path):
    """JU, SZ and VD have no portal scraper; losing them is the failure
    mode of pointing --input-lexfind at an empty directory."""
    _shard(tmp_path / "lexfind" / "JU.jsonl", [_law("JU", "173.11", "Loi")])
    _shard(tmp_path / "direct" / "ZG.jsonl", [_law("ZG", "231.1", "EG SchKG")])
    laws = _laws_in(_build(tmp_path))
    assert ("JU", "173.11") in laws, "the fallback is the only source for JU"
    assert ("ZG", "231.1") in laws


def test_direct_replaces_lexfind_wholesale_for_the_same_canton(tmp_path):
    """Not a merge: the LexFind row for the same canton does not survive
    alongside the direct one, even under a different sr_number."""
    _shard(tmp_path / "lexfind" / "AG.jsonl",
           [_law("AG", "554.5", "Hundegesetz (LexFind)"),
            _law("AG", "170.4", "Gemeindegesetz (LexFind)")])
    _shard(tmp_path / "direct" / "AG.jsonl",
           [_law("AG", "554.5", "Hundegesetz (Portal)")])
    laws = _laws_in(_build(tmp_path))
    assert laws[("AG", "554.5")] == "Hundegesetz (Portal)"
    assert ("AG", "170.4") not in laws, \
        "the override is wholesale — this is why a stale shard loses laws"


def test_a_direct_shard_shadowing_a_much_larger_fallback_warns(tmp_path, caplog):
    """The 2026-08-19 signature: a tiny direct shard displacing a large
    fallback. Legitimate when the portal is simply more selective, so it
    warns rather than aborts — but it must not pass in silence."""
    _shard(tmp_path / "lexfind" / "AG.jsonl",
           [_law("AG", f"1.{i}", f"Gesetz {i}", pad=2000) for i in range(50)])
    _shard(tmp_path / "direct" / "AG.jsonl", [_law("AG", "554.5", "Portal")])
    with caplog.at_level("WARNING"):
        _build(tmp_path)
    assert any("shadows" in r.getMessage() for r in caplog.records), \
        "a silent truncation is the bug"


def test_a_complement_canton_does_not_warn_about_shadowing(tmp_path, caplog):
    """ZH's portal is small by nature and its gaps are filled, so the warning
    would fire every month and say nothing."""
    _shard(tmp_path / "lexfind" / "ZH.jsonl",
           [_law("ZH", f"1.{i}", f"Gesetz {i}", pad=2000) for i in range(50)])
    _shard(tmp_path / "direct" / "ZH.jsonl", [_law("ZH", "554.5", "Portal")])
    with caplog.at_level("WARNING"):
        _build(tmp_path)
    assert not any("shadows" in r.getMessage() for r in caplog.records)


def test_a_comparable_direct_shard_does_not_warn(tmp_path, caplog):
    _shard(tmp_path / "lexfind" / "ZG.jsonl", [_law("ZG", "231.1", "A", 500)])
    _shard(tmp_path / "direct" / "ZG.jsonl", [_law("ZG", "231.1", "B", 500)])
    with caplog.at_level("WARNING"):
        _build(tmp_path)
    assert not [r for r in caplog.records if "shadows" in r.getMessage()]


def _lexfind_law(lid, sr, title, active=True, **extra):
    return {**_law("ZH", sr, title), "lexfind_id": lid, "is_active": active,
            "text_source": "lexfind_pdf", **extra}


def _complement_case(tmp_path):
    direct = _law("ZH", "101", "Portal")
    direct["text_source"] = "zhlex_pdf"
    _shard(tmp_path / "direct" / "ZH.jsonl", [direct])
    _shard(tmp_path / "lexfind" / "ZH.jsonl", [
        _lexfind_law(1, "101", "LexFind copy"),
        _lexfind_law(2, "700.1", "Planungs- und Baugesetz"),
        _lexfind_law(3, "999", "Aufgehoben", active=False),
    ])
    return _build(tmp_path)


def test_zh_takes_the_laws_in_force_its_portal_lacks(tmp_path):
    """The portal publishes 135 of 944 acts in force. A failed LexFind lookup
    for the rest answered that the law did not exist."""
    assert _laws_in(_complement_case(tmp_path)) == {
        ("ZH", "101"): "Portal",
        ("ZH", "700.1"): "Planungs- und Baugesetz",
    }


def test_the_complement_is_named_like_any_other_law(tmp_path):
    con = sqlite3.connect(_complement_case(tmp_path))
    try:
        assert con.execute(
            "SELECT 1 FROM law_names WHERE canton='ZH' AND sr_number='700.1'"
        ).fetchone()
        assert con.execute(
            "SELECT text_source FROM laws WHERE sr_number='700.1'"
        ).fetchone() == ("lexfind_pdf",)
    finally:
        con.close()


def test_a_portal_only_canton_takes_no_complement(tmp_path):
    _shard(tmp_path / "direct" / "AG.jsonl", [_law("AG", "101", "Portal")])
    _shard(tmp_path / "lexfind" / "AG.jsonl", [_lexfind_law(1, "700", "Zweites")])
    assert set(_laws_in(_build(tmp_path))) == {("AG", "101")}


def test_a_row_keeps_the_date_of_its_text(tmp_path):
    _shard(tmp_path / "lexfind" / "JU.jsonl", [
        _lexfind_law(1, "1", "Datiert", fetched_at="2026-04-11T00:00:00+00:00")])
    con = sqlite3.connect(_build(tmp_path))
    try:
        assert con.execute("SELECT fetched_at FROM laws").fetchone() == (
            "2026-04-11T00:00:00+00:00",)
    finally:
        con.close()


def test_an_unchanged_text_reads_as_of_its_last_check(tmp_path):
    _shard(tmp_path / "lexfind" / "JU.jsonl", [_lexfind_law(
        1, "1", "Geprüft", fetched_at="2026-04-11T00:00:00+00:00",
        checked_at="2026-11-02T00:00:00+00:00")])
    con = sqlite3.connect(_build(tmp_path))
    try:
        assert con.execute("SELECT fetched_at FROM laws").fetchone() == (
            "2026-11-02T00:00:00+00:00",)
    finally:
        con.close()


def test_a_row_without_a_date_takes_its_shard_not_the_build(tmp_path):
    """Stamping the build date made an April shard read as fresh every month."""
    path = tmp_path / "lexfind" / "JU.jsonl"
    _shard(path, [_lexfind_law(1, "1", "Alt")])
    os.utime(path, (1775865600, 1775865600))  # 2026-04-11
    con = sqlite3.connect(_build(tmp_path))
    try:
        assert con.execute("SELECT fetched_at FROM laws").fetchone()[0].startswith("2026-04-11")
    finally:
        con.close()
