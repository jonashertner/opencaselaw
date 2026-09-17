"""BGE decision-date audit 2026-09-10: the CLIR page splits the Urteilskopf
over several <div class="paraatf"> blocks and the scraper read only the
first, so "vom <Datum>" was never seen, every direct row got the 1 January
placeholder and chamber/docket_2 stayed NULL (117/117 rows scraped after
2026-03-13 in output/decisions/bge.jsonl). Fixtures are the trimmed
Urteilskopf of four live pages fetched 2026-09-10 (DE new form, DE 2012,
FR 1993 date-before-parties form, IT)."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scrapers.bge import (
    BGELeitentscheideScraper,
    parse_urteilskopf,
    urteilskopf_text,
)

FIXTURES = REPO / "tests" / "fixtures"

# (fixture, volume year, expected date, expected docket_2, chamber fragment)
CASES = [
    ("bge_150_III_367_head.html", 2024, date(2024, 8, 13), "5A_691/2023",
     "II. zivilrechtlichen Abteilung"),
    ("bge_138_III_425_head.html", 2012, date(2012, 4, 17), "4A_688/2011",
     "I. zivilrechtlichen Abteilung"),
    ("bge_119_II_339_head.html", 1993, date(1993, 7, 13), None,
     "Ire Cour civile"),
    ("bge_149_I_105_head.html", 2023, date(2022, 12, 12), "2C_886/2021",
     "II Corte di diritto pubblico"),
]


def _scraper(tmp_path):
    return BGELeitentscheideScraper(state_dir=tmp_path, include_egmr=False)


@pytest.mark.parametrize("fixture,year,expected,docket_2,chamber", CASES)
def test_urteilskopf_ruling_date(tmp_path, fixture, year, expected, docket_2, chamber):
    html = (FIXTURES / fixture).read_text(encoding="utf-8")
    meta = _scraper(tmp_path)._parse_document_metadata(html, year)
    assert meta["decision_date"] == expected
    assert not meta.get("date_is_placeholder")
    assert meta.get("docket_2") == docket_2
    assert chamber in (meta.get("chamber") or "")


def test_first_block_alone_was_the_bug(tmp_path):
    """The old code saw only the first paraatf block, which carries no date."""
    html = (FIXTURES / "bge_150_III_367_head.html").read_text(encoding="utf-8")
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    first = soup.find("div", class_="paraatf").get_text(strip=True)
    assert "vom" not in first
    assert "vom 13. August 2024" in urteilskopf_text(soup)


def test_placeholder_when_header_has_no_date(tmp_path):
    html = (
        '<div id="highlight_content"><div class="content">'
        '<div class="big bold">Urteilskopf</div><br>150 III 1<br>'
        '<div class="paraatf">1. Auszug aus dem Urteil der I. öffentlich-rechtlichen '
        'Abteilung i.S. A. gegen B. (Beschwerde in öffentlich-rechtlichen Angelegenheiten)</div>'
        '<div id="regeste"><div class="paraatf">Art. 1 BV. Text vom 16. Mai 1972.</div></div>'
        '</div></div>'
    )
    meta = _scraper(tmp_path)._parse_document_metadata(html, 2024)
    assert meta["decision_date"] == date(2024, 1, 1)
    assert meta["date_is_placeholder"] is True
    # A Regeste date never leaks into the Urteilskopf parse.
    assert "1972" not in urteilskopf_text(__import__("bs4").BeautifulSoup(html, "html.parser"))


def test_volume_year_vetoes_a_foreign_date():
    header = ("37. Auszug aus dem Urteil der II. zivilrechtlichen Abteilung i.S. A. gegen B. "
              "(Beschwerde in Zivilsachen) 5A_691/2023 vom 16. Mai 1972")
    meta = parse_urteilskopf(header, volume_year=2024)
    assert "decision_date" not in meta
    assert meta["date_rejected"] == date(1972, 5, 16)
    assert meta["docket_2"] == "5A_691/2023"
    # Without a volume year the parse is taken at face value.
    assert parse_urteilskopf(header)["decision_date"] == date(1972, 5, 16)


def test_late_publication_is_kept_and_typo_year_is_not():
    # BGE 149 IV 97 = 6B_1079/2021 of 22.11.2021, printed in the 2023 volume
    # (corroborated by the BGer row bger_6B_1079_2021).
    header = ("8. Extrait de l'arrêt de la Cour de droit pénal dans la cause A. contre Ministère "
              "public central du canton de Vaud (recours en matière pénale) 6B_1079/2021 du 22 novembre 2021")
    assert parse_urteilskopf(header, volume_year=2023)["decision_date"] == date(2021, 11, 22)
    # BGE 84 III 59 (1958) reads "Entscheid vom 28. August 1985" — a source typo,
    # a ruling cannot postdate its volume by more than a year.
    meta = parse_urteilskopf("16. Entscheid vom 28. August 1985 i.S. U.", volume_year=1958)
    assert "decision_date" not in meta and meta["date_rejected"] == date(1985, 8, 28)


def test_edge_of_tolerance_is_kept():
    # A ruling of December 2023 printed in volume 150 (2024) is legitimate.
    header = ("1. Auszug aus dem Urteil der I. zivilrechtlichen Abteilung i.S. A. gegen B. "
              "(Beschwerde in Zivilsachen) 4A_1/2023 vom 20. Dezember 2023")
    assert parse_urteilskopf(header, volume_year=2024)["decision_date"] == date(2023, 12, 20)


def test_fetch_decision_end_to_end(tmp_path, monkeypatch):
    scraper = _scraper(tmp_path)
    html = (FIXTURES / "bge_150_III_367_head.html").read_text(encoding="utf-8")
    monkeypatch.setattr(scraper, "_fetch_trilingual_regesten",
                        lambda base_url: {"de": "Regeste", "fr": None, "it": None})
    monkeypatch.setattr(scraper, "_safe_get",
                        lambda url, **kw: type("R", (), {"text": html})())
    decision = scraper.fetch_decision({
        "docket_number": "150 III 367", "bge_reference": "150 III 367",
        "url": "/ext/eurospider/live/de/php/clir/http/index.php?highlight_docid=atf%3A%2F%2F150-III-367%3Ade&lang=de",
        "year": 2024, "volume": "III",
    })
    assert decision is not None
    assert decision.decision_date == date(2024, 8, 13)
    assert decision.docket_number_2 == "5A_691/2023"
    assert decision.chamber == "II. zivilrechtlichen Abteilung"


# Ruling date first, a referenced decision's date later (BGE 126 III 283).
_H_126_III_283 = ("48. Extrait de l'arrêt de la Ire Cour civile du 15 mai 2000 dans la cause Office "
                  "fédéral de la justice contre la décision du 31 janvier 2000 rendue par l'Autorité")
# Erläuterung of an earlier ruling (BGE 104 V 51): first date is the ruling.
_H_104_V_51 = ("10. Urteil vom 16. März 1978 i.S. Z. gegen Schweizerische Unfallversicherungsanstalt "
               "betreffend Erläuterung des Urteils des Eidg. Versicherungsgerichts vom 5. September 1977")
# Statute date first, then the act's own date (BGE 85 II 369, a Tarif): the
# statute date fails the volume gate and the next candidate is taken.
_H_85_II_369 = ("59. TARIF für die Gerichtsgebühren (zu Art. 153 des Organisationsgesetzes vom 16. Dezember "
                "1943/19. Juni 1959) vom 14. November 1959, in Kraft vom 1. Januar 1960")


@pytest.mark.parametrize("header,year,expected", [
    (_H_126_III_283, 2000, date(2000, 5, 15)),
    (_H_104_V_51, 1978, date(1978, 3, 16)),
    (_H_85_II_369, 1959, date(1959, 11, 14)),
])
def test_first_volume_consistent_date_wins(header, year, expected):
    assert parse_urteilskopf(header, volume_year=year)["decision_date"] == expected
