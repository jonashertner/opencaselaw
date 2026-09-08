"""scripts/migrate_zh_arbeitsgericht_rows.py — pure transformation on fixture rows.

Models the 2026-09-04 production state: the same TYPO3 document 40513 held as
zh_arbeitsgericht_AH230041 (old metadata shape) and as
zh_bezirksgericht_zuerich_AH230041-L (new shape), an Arbeitsgericht ruling only
under the Bezirksgericht, AGer-Z 2024 Nr. 6 dated 1937, and untouched rows.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.migrate_zh_arbeitsgericht_rows import (
    docket_registration_year,
    fix_dates,
    migrate,
    target_court,
)

TODAY = date(2026, 9, 4)


def _row(did, court, docket, chamber=None, ext=None, scraped="2026-01-01T00:00:00", text="x" * 100,
         ddate="2025-01-13", pdate=None):
    return {
        "decision_id": did, "court": court, "docket_number": docket, "chamber": chamber,
        "external_id": ext, "scraped_at": scraped, "full_text": text,
        "decision_date": ddate, "publication_date": pdate, "canton": "ZH",
    }


def test_target_court_precedence():
    assert target_court(_row("a", "zh_bezirksgericht_zuerich", "AH1", chamber="Arbeitsgericht")) == ("zh_arbeitsgericht", None)
    assert target_court(_row("a", "zh_gerichte", "AH1", chamber="Mietgericht")) == ("zh_mietgericht", None)
    assert target_court(_row("a", "zh_arbeitsgericht", "AH1", chamber="4. Abteilung")) == ("zh_arbeitsgericht", "4. Abteilung")
    assert target_court(_row("a", "zh_bezirksgericht_zuerich", "AH1", chamber="3. Abteilung")) == ("zh_bezirksgericht_zuerich", "3. Abteilung")
    assert target_court(_row("a", "zh_obergericht", "LA1", chamber="Arbeitsgericht")) == ("zh_obergericht", "Arbeitsgericht")


def test_docket_registration_year():
    assert docket_registration_year("AN230029-L", TODAY) == 2023
    assert docket_registration_year("AH250127", TODAY) == 2025
    assert docket_registration_year("AN990123", TODAY) == 1999
    assert docket_registration_year("LA240007", TODAY) == 2024
    assert docket_registration_year("BGE 140 III 264", TODAY) is None
    assert docket_registration_year(None, TODAY) is None


def test_fix_dates_takes_publication_date_or_nulls():
    r = _row("a", "zh_arbeitsgericht", "AN230029-L", ddate="1937-12-03", pdate="2024-11-25")
    assert "took publication_date" in fix_dates(r, TODAY)
    assert (r["decision_date"], r["publication_date"]) == ("2024-11-25", None)

    r = _row("a", "zh_arbeitsgericht", "AN230029-L", ddate="1937-12-03", pdate="1990-01-01")
    assert "NULLed" in fix_dates(r, TODAY)
    assert r["decision_date"] is None

    r = _row("a", "zh_arbeitsgericht", "AN230029-L", ddate="2022-12-30", pdate="2024-11-25")
    assert fix_dates(r, TODAY) is None          # one year of slack for late-registered cases
    assert r["decision_date"] == "2022-12-30"


def test_migrate_refiles_dedupes_and_keeps_the_rest():
    rows = [
        _row("zh_arbeitsgericht_AH230041", "zh_arbeitsgericht", "AH230041", chamber="4. Abteilung",
             ext="zh_gerichte_40513", scraped="2025-02-01T00:00:00", text="short"),
        _row("zh_bezirksgericht_zuerich_AH230041-L", "zh_bezirksgericht_zuerich", "AH230041-L",
             chamber="Arbeitsgericht", ext="zh_gerichte_40513", scraped="2026-06-01T00:00:00", text="longer text"),
        _row("zh_bezirksgericht_zuerich_AN230029-L", "zh_bezirksgericht_zuerich", "AN230029-L",
             chamber="Arbeitsgericht", ext="zh_gerichte_37364", ddate="1937-12-03", pdate="2024-11-25"),
        _row("zh_bezirksgericht_zuerich_CG240001", "zh_bezirksgericht_zuerich", "CG240001",
             chamber="3. Abteilung", ext="zh_gerichte_1", ddate="2024-05-05"),
        _row("zh_obergericht_LA240007", "zh_obergericht", "LA240007", chamber="I. Zivilkammer",
             ext="zh_gerichte_2"),
    ]
    final, stats, notes = migrate(rows, TODAY)
    ids = [r["decision_id"] for r in final]
    assert ids == [
        "zh_arbeitsgericht_AH230041-L",
        "zh_arbeitsgericht_AN230029-L",
        "zh_bezirksgericht_zuerich_CG240001",
        "zh_obergericht_LA240007",
    ]
    assert stats["refiled→zh_arbeitsgericht"] == 2
    assert stats["dropped_duplicate"] == 1
    assert stats["date_fixed"] == 1
    kept = {r["decision_id"]: r for r in final}
    assert kept["zh_arbeitsgericht_AH230041-L"]["full_text"] == "longer text"
    assert kept["zh_arbeitsgericht_AH230041-L"]["chamber"] is None
    assert kept["zh_arbeitsgericht_AN230029-L"]["decision_date"] == "2024-11-25"
    assert kept["zh_bezirksgericht_zuerich_CG240001"]["chamber"] == "3. Abteilung"
    assert any("drop zh_arbeitsgericht_AH230041 " in n for n in notes)


def test_migrate_is_idempotent():
    rows = [
        _row("zh_arbeitsgericht_AH230041-L", "zh_arbeitsgericht", "AH230041-L", ext="zh_gerichte_40513"),
        _row("zh_arbeitsgericht_AN230029-L", "zh_arbeitsgericht", "AN230029-L", ext="zh_gerichte_37364",
             ddate="2024-11-25"),
    ]
    final, stats, _ = migrate([dict(r) for r in rows], TODAY)
    assert final == rows
    assert sum(stats.values()) == 0
