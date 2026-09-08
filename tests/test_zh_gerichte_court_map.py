"""Court mapping of the gerichte-zh.ch scraper (scrapers/cantonal/zh_gerichte.py).

Regression for 2026-09-04: the portal files Arbeitsgericht / Mietgericht rulings
as Gericht="Bezirksgericht Zürich", Abteilung/Kammer="Arbeitsgericht". The
longest-keyword rule preferred "bezirksgericht zürich", so 67 Arbeitsgericht
rulings (2006–2026) sat under zh_bezirksgericht_zuerich while the same court
also had 34 rows under zh_arbeitsgericht from the older metadata shape
(Gericht="Arbeitsgericht Zürich", Kammer="4. Abteilung").
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scrapers.cantonal.zh_gerichte import _map_court


def test_arbeitsgericht_as_kammer_of_bezirksgericht():
    assert _map_court("Bezirksgericht Zürich", "Arbeitsgericht") == ("zh_arbeitsgericht", None)


def test_mietgericht_as_kammer_of_bezirksgericht():
    assert _map_court("Bezirksgericht Zürich", "Mietgericht") == ("zh_mietgericht", None)


def test_old_shape_keeps_abteilung_as_chamber():
    assert _map_court("Arbeitsgericht Zürich", "4. Abteilung") == ("zh_arbeitsgericht", "4. Abteilung")
    assert _map_court("Mietgericht Zürich", "") == ("zh_mietgericht", None)


def test_ordinary_bezirksgericht_chambers_unchanged():
    assert _map_court("Bezirksgericht Zürich", "3. Abteilung") == ("zh_bezirksgericht_zuerich", "3. Abteilung")
    assert _map_court("Bezirksgericht Winterthur", "Einzelgericht") == ("zh_bezirksgericht_winterthur", "Einzelgericht")
    assert _map_court("Bezirksgericht Meilen", "") == ("zh_bezirksgericht_meilen", None)


def test_upper_courts_unchanged():
    assert _map_court("Obergericht des Kantons Zürich", "I. Zivilkammer") == ("zh_obergericht", "I. Zivilkammer")
    assert _map_court("Handelsgericht des Kantons Zürich", "") == ("zh_handelsgericht", None)
    assert _map_court("Kassationsgericht des Kantons Zürich", "") == ("zh_kassationsgericht", None)


def test_unknown_court_falls_back_to_generic():
    assert _map_court("Friedensrichteramt Zürich", "") == ("zh_gerichte", None)
