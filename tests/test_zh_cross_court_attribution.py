"""Cross-court dedup: the row filed under the deciding court beats a
near-equal federation copy in the generic bucket.

Measured 2026-10-02 on the production Zürich shards: 125 rulings were served
as court "zh_gerichte" although the direct scrape held them under their court.
The entscheidsuche copy was a few characters longer (the direct row had a
median 98% of its content), and "longest wins" deleted the attributed row.
"""
from __future__ import annotations

import sqlite3

import pytest

import build_fts5
from db_schema import SCHEMA_SQL


def _row(decision_id, court, text, docket="LA170001", date="2017-10-06"):
    return {
        "decision_id": decision_id, "court": court, "canton": "ZH",
        "docket_number": docket, "decision_date": date, "language": "de",
        "title": "Forderung", "regeste": None, "full_text": text,
        "source_url": "https://www.gerichte-zh.ch/x",
    }


def _db(tmp_path, *rows):
    c = sqlite3.connect(str(tmp_path / "d.db"))
    c.executescript(SCHEMA_SQL)
    for r in rows:
        assert build_fts5.insert_decision(c, r)
    c.commit()
    return c


def _ids(c):
    return sorted(r[0] for r in c.execute("SELECT decision_id FROM decisions"))


def test_attributed_row_beats_a_slightly_longer_federation_copy(tmp_path):
    c = _db(tmp_path,
            _row("zh_obergericht_LA170001", "zh_obergericht", "x" * 980),
            _row("zh_gerichte_LA170001", "zh_gerichte", "x" * 1000))
    assert build_fts5._cross_court_dedup(c) == 1
    assert _ids(c) == ["zh_obergericht_LA170001"]


def test_a_substantially_fuller_copy_still_wins(tmp_path):
    """A truncated direct row must not displace the complete text."""
    c = _db(tmp_path,
            _row("zh_obergericht_LA170001", "zh_obergericht", "x" * 500),
            _row("zh_gerichte_LA170001", "zh_gerichte", "x" * 1000))
    assert build_fts5._cross_court_dedup(c) == 1
    assert _ids(c) == ["zh_gerichte_LA170001"]


def test_longer_attributed_row_wins_as_before(tmp_path):
    c = _db(tmp_path,
            _row("zh_obergericht_LA170001", "zh_obergericht", "x" * 1000),
            _row("zh_gerichte_LA170001", "zh_gerichte", "x" * 900))
    assert build_fts5._cross_court_dedup(c) == 1
    assert _ids(c) == ["zh_obergericht_LA170001"]


def test_another_date_and_another_text_is_another_decision(tmp_path):
    c = _db(tmp_path,
            _row("zh_obergericht_LA170001", "zh_obergericht", "Urteil in der Hauptsache. " * 40),
            _row("zh_gerichte_LA170001", "zh_gerichte", "Beschluss über die Kosten. " * 40,
                 date="2018-01-15"))
    assert build_fts5._cross_court_dedup(c) == 0


def test_groups_without_a_generic_bucket_keep_the_longest(tmp_path):
    """SG is not in _GENERIC_BUCKET_COURTS: unchanged behaviour."""
    c = _db(tmp_path,
            _row("sg_kantonsgericht_X1", "sg_kantonsgericht", "x" * 980, docket="X1"),
            _row("sg_gerichte_X1", "sg_gerichte", "x" * 1000, docket="X1"))
    assert build_fts5._cross_court_dedup(c) == 1
    assert _ids(c) == ["sg_gerichte_X1"]


# ── per-court swap gate ─────────────────────────────────────────────────


def test_draining_bucket_does_not_block_the_swap():
    build_fts5._check_swap_per_court_gate(
        {"zh_gerichte": 20, "zh_obergericht": 31_000}, {"zh_gerichte": 1_438, "zh_obergericht": 30_000})


def test_a_real_court_still_blocks():
    with pytest.raises(RuntimeError):
        build_fts5._check_swap_per_court_gate(
            {"zh_gerichte": 20, "zh_obergericht": 10_000}, {"zh_gerichte": 1_438, "zh_obergericht": 30_000})


def test_env_adds_exemptions_for_a_run(monkeypatch):
    live, new = {"bger": 100_000, "bvger": 90_000}, {"bger": 50_000, "bvger": 90_000}
    with pytest.raises(RuntimeError):
        build_fts5._check_swap_per_court_gate(new, live)
    monkeypatch.setenv("OCL_SWAP_GATE_EXEMPT", "bger, xx")
    build_fts5._check_swap_per_court_gate(new, live)
    with pytest.raises(RuntimeError):
        build_fts5._check_swap_per_court_gate({"bger": 50_000, "bvger": 10_000}, live)


# ── the same ruling under another date ──────────────────────────────────

TEXT = " ".join(f"Erwägung {i}: Die Vorinstanz hat Ziffer {i} zutreffend gewürdigt." for i in range(80))
OTHER = " ".join(f"Kostenpunkt {i}: Die Gebühr für Abschnitt {i} wird neu festgesetzt." for i in range(80))


def test_federation_copy_with_another_date_is_the_same_ruling(tmp_path):
    c = _db(tmp_path,
            _row("zh_mietgericht_ED200048", "zh_mietgericht", TEXT, docket="ED200048", date="2020-08-04"),
            _row("zh_gerichte_ED200048", "zh_gerichte", TEXT.replace(" ", "\n"), docket="ED200048",
                 date="2020-10-20"))
    assert build_fts5._cross_court_dedup(c) == 1
    assert _ids(c) == ["zh_mietgericht_ED200048"]


def test_near_identical_extraction_is_the_same_ruling(tmp_path):
    c = _db(tmp_path,
            _row("zh_handelsgericht_HG210238", "zh_handelsgericht", TEXT, docket="HG210238", date="2023-06-14"),
            _row("zh_gerichte_HG210238", "zh_gerichte", "Seite 1\n" + TEXT.replace("Ziffer 7 ", "Zif- fer 7 "),
                 docket="HG210238", date="2023-06-16"))
    assert build_fts5._cross_court_dedup(c) == 1
    assert _ids(c) == ["zh_handelsgericht_HG210238"]


def test_a_different_ruling_of_the_docket_stays(tmp_path):
    c = _db(tmp_path,
            _row("zh_mietgericht_MJ210048", "zh_mietgericht", TEXT, docket="MJ210048", date="2021-05-06"),
            _row("zh_gerichte_MJ210048", "zh_gerichte", OTHER, docket="MJ210048", date="2023-01-17"))
    assert build_fts5._cross_court_dedup(c) == 0


def test_copy_without_a_direct_row_stays(tmp_path):
    c = _db(tmp_path, _row("zh_gerichte_LA110009", "zh_gerichte", TEXT, docket="LA110009", date="2011-02-17"))
    assert build_fts5._cross_court_dedup(c) == 0
