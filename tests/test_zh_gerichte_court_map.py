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

from bs4 import BeautifulSoup

from scrapers.cantonal.zh_gerichte import (
    _extract_headnote,
    _get_detail_field,
    _map_court,
    _value_or_none,
)


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


# 2026-10-02: every district has its own Arbeitsgericht / Mietgericht. Filing
# them all under the Zürich codes showed a Dielsdorf ruling (AH260006) as
# "Arbeitsgericht ZH" and merged the docket series of different courts.

def test_district_arbeitsgericht_stays_with_its_district_court():
    assert _map_court("Bezirksgericht Dielsdorf", "Arbeitsgericht") == (
        "zh_bezirksgericht_dielsdorf", "Arbeitsgericht")
    assert _map_court("Bezirksgericht Bülach", "Arbeitsgericht") == (
        "zh_bezirksgericht_buelach", "Arbeitsgericht")
    assert _map_court("Bezirksgericht Uster", "Arbeitsgerichtspräsidium als Einzelgericht") == (
        "zh_bezirksgericht_uster", "Arbeitsgerichtspräsidium als Einzelgericht")


def test_district_mietgericht_stays_with_its_district_court():
    assert _map_court("Bezirksgericht Winterthur", "Mietgericht") == (
        "zh_bezirksgericht_winterthur", "Mietgericht")
    assert _map_court("Mietgericht des Bezirkes Horgen", "-") == (
        "zh_bezirksgericht_horgen", "Mietgericht")


def test_old_zurich_shape_with_einzelgericht():
    assert _map_court("Mietgericht Zürich", "Einzelgericht") == ("zh_mietgericht", "Einzelgericht")


def test_dash_is_not_a_chamber():
    assert _map_court("Handelsgericht des Kantons Zürich", "-") == ("zh_handelsgericht", None)
    assert _map_court("Bezirksgericht Hinwil", "-") == ("zh_bezirksgericht_hinwil", None)


_DETAILS = """
<div class="entscheidDetails container_2331">
  <p><b><span lang="DE-CH">§§ 430 Abs. 1 Ziff. 4 und 5 StPO/ZH. </span></b><br />
     <span lang="DE-CH">Kein Nachweis eines Nich\xadtigkeitsgrundes (Erw. III).</span></p>
  <div class="pdf"><p><a class="pdf-icon" href="/fileadmin/x/AC110002.pdf"></a></p></div>
  <p>&nbsp;</p>
  <p><span class="titel">Gericht/Behörde</span><span>Kassationsgericht des Kantons Zürich</span></p>
  <p><span class="titel">Gesetz/e, Verordnung/en etc.</span><span class="text">StGB 12;
StGB 25<br></span></p>
  <p><span class="titel">Verweise</span><span class="text">Weiterzug ans Bundesgericht,<br>6B_122/2024</span></p>
</div>
"""


def _details():
    return BeautifulSoup(_DETAILS, "html.parser").find("div")


def test_headnote_is_the_unlabelled_text_of_the_details_block():
    head = _extract_headnote(_details())
    assert head == ("§§ 430 Abs. 1 Ziff. 4 und 5 StPO/ZH. "
                    "Kein Nachweis eines Nichtigkeitsgrundes (Erw. III).")


def test_no_headnote_when_the_block_has_only_fields():
    soup = BeautifulSoup(
        '<div><p><span class="titel">Verweise</span><span class="text"></span></p></div>',
        "html.parser").find("div")
    assert _extract_headnote(soup) == ""


def test_verweise_and_gesetze_are_read_with_their_real_labels():
    d = _details()
    assert _get_detail_field(d, "Verweise") == "Weiterzug ans Bundesgericht,\n6B_122/2024"
    assert _get_detail_field(d, "Gesetz/e, Verordnung/en etc.") == "StGB 12;\nStGB 25"


def test_portal_placeholders_are_not_values():
    assert _value_or_none("keine") is None
    assert _value_or_none("-") is None
    assert _value_or_none("n/A") is None
    assert _value_or_none("Entscheid wurde nicht angefochten") == "Entscheid wurde nicht angefochten"


def test_a_title_or_a_stub_in_the_headnote_slot_is_not_a_headnote():
    from scrapers.cantonal.zh_gerichte import ZHGerichteScraper

    def stub(head):
        html = (
            '<div class="entscheid entscheid_nummer_7"><p><strong>Forderung</strong></p></div>'
            f'<div class="entscheidDetails container_7"><p>{head}</p>'
            '<div class="pdf"><p><a class="pdf-icon" href="/f/X.pdf"></a></p></div>'
            '<p><span class="titel">Gericht/Behörde</span><span>Bezirksgericht Uster</span></p>'
            '<p><span class="titel">Abteilung/Kammer</span><span>-</span></p>'
            '<p><span class="titel">Entscheiddatum</span><span>09.06.2026</span></p>'
            '<p><span class="titel">Geschäftsnummer</span><span>CG260001</span></p></div>'
        )
        sc = ZHGerichteScraper.__new__(ZHGerichteScraper)
        return next(iter(sc._parse_window(html)))

    assert stub("(Volltext)")["leitsatz"] == ""
    assert stub("Forderung")["leitsatz"] == ""
    long = "Die Wahrung der Rechtsmittelfrist ist von Amtes wegen zu prüfen (E. 2)."
    assert stub(long)["leitsatz"] == long
    assert stub(long)["chamber"] is None
