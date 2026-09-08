"""Offline tests for the Arbeitsgericht Zürich yearbook scraper
(scrapers/cantonal/zh_arbeitsgericht_sammlung.py). Invariant #8: no network —
the index page and the PDF text are inline fixtures modelled on the 2023 and
2008 volumes (printer's marks, running heads, wrapped TOC titles, kerning,
numbered paragraphs inside a ruling, old and new trailer forms).
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scrapers.cantonal import zh_arbeitsgericht_sammlung as mod
from scrapers.cantonal.zh_arbeitsgericht_sammlung import (
    ZHArbeitsgerichtSammlungScraper,
    split_volume,
)

VOLUME = """Entscheide des
Arbeitsgerichtes Zürich
2023
ARBEITSGERICHT ZÜRICH ARBEITSGERICHT ZÜRICH

Entscheide des Arbeitsgerichtes Zürich 2023
Herausgegeben vom Arbeitsgericht Zürich
Zitiervorschlag: AGer-Z 2023 Nr. X
Erscheint jährlich.
Preis Fr. 24.–
Layout und Druck: Buchmann Druck AG Zürich

Inhaltsverzeichnis
Editorial 3
I. Aus den Entscheiden 5
1. O R 1/OR 322d; Verbindliche Zusicherung zum Fortbestand 5
der Bonusansprüche?
2. OR 321c/ArG 9; Beweis von Überzeit 8
3. ZPO 107; Verteilung der Prozesskosten 11
II. Ergänzungen zu weitergezogenen Entscheiden 14
III. S tatistischer Überblick 15
Hinweis
Durch die Redaktorin angebrachte Anmerkungen sind mit eckigen Klammern gekennzeichnet.
1

EDITORIAL
Sehr geehrte Leserschaft
Wir freuen uns, Ihnen eine Auswahl von Entscheiden zu präsentieren (Nr. 1 bis 3).
Zürich, im Mai 2024
3

Kapitel I. Aus den Entscheiden
I. AUS DEN ENTSCHEIDEN
1. O R 1/OR 322d; Verbindliche Zusicherung zum Fortbestand
der Bonusansprüche?
Der Kläger war ab dem 15. April 2001 für die Beklagte tätig. Die Beklagte relati­
vierte ihre Zusage. Es wurde ein Beweisverfahren durchgeführt.
Aus den Erwägungen:
«3.5.1. Themen des Beweisverfahrens
Wie gesehen ist die Frage zentral, ob eine Zusicherung erfolgte. Das Gericht
prüfte den Sachverhalt eingehend.»
(AN200007-L Urteil vom 24. Januar 2023; Berufung am Obergericht anhängig)
2. OR 321c/ArG 9; Beweis von Überzeit
rz_arbeitsgericht_23 16.6.2024 9:38 Uhr Seite 8
Der Kläger verlangte Überzeitentschädigung. Am 9. Januar 2015 wurde ihm
9. Januar 2015 als Stichtag mitgeteilt.
Aus den Erwägungen:
«2. Rechtliches
Überzeit ist zu beweisen.
3. Würdigung
Der Beweis ist nicht erbracht. Die Klage ist abzuweisen.»
5
Kapitel I. Aus den Entscheiden
(AH220101-L Urteil vom 14. Juni 2023, gegen diesen Entscheid wurde kein
Rechtsmittel ergriffen)
3. ZPO 107; Verteilung der Prozesskosten
Die Parteien stritten über die Kosten.
Aus den Erwägungen:
«Die Kosten sind nach Ermessen zu verteilen.»
(AGer., AN070249 vom 28. Januar 2008; eine Berufung ist noch hängig)
II. ERGÄNZUNGEN ZU WEITERGEZOGENEN ENTSCHEIDEN
AGer-Z 2022 Nr. 6: Das Bundesgericht wies die Beschwerde ab.
III. STATISTISCHER ÜBERBLICK
Klageeingänge 2023: 1'234
"""

INDEX_HTML = """<html><body>
<a href="/sites/default/files/2026-04/AGer-Z_2023.pdf">Jahrgang 2023</a>
<a href="/sites/default/files/Themen/Arbeit/Mit%20Register%20versehene%20Version%20AGer-Z%202022.pdf">Jahrgang 2022</a>
<a href="/sites/default/files/2026-04/Schlussdokument_AGer-Z_2021.pdf">Jahrgang 2021</a>
<a href="/sites/default/files/2026-04/AGer-Z_2023.pdf">Jahrgang 2023 (Duplikat)</a>
<a href="/sites/default/files/2026-04/AGer-Z_2024.pdf">Jahrgang 2024</a>
<a href="https://www.gerichte-zh.ch/entscheide/entscheide-arbeitsgericht-zuerich.html">Online</a>
</body></html>"""


# ------------------------------------------------------------ split_volume

def test_split_finds_every_toc_entry_and_nothing_else():
    r = split_volume(VOLUME, 2023)
    assert r.toc_count == 3
    assert r.missing == []
    assert [e.number for e in r.entries] == [1, 2, 3]


def test_titles_come_from_toc_with_kerning_and_wrap_fixed():
    r = split_volume(VOLUME, 2023)
    assert r.entries[0].title == "OR 1/OR 322d; Verbindliche Zusicherung zum Fortbestand der Bonusansprüche?"
    assert r.entries[1].title == "OR 321c/ArG 9; Beweis von Überzeit"
    assert r.entries[2].title == "ZPO 107; Verteilung der Prozesskosten"


def test_numbered_paragraphs_and_dates_do_not_start_a_ruling():
    r = split_volume(VOLUME, 2023)
    e2 = r.entries[1].text
    assert "3. Würdigung" in e2                 # sub-heading stays inside Nr. 2
    assert "9. Januar 2015 als Stichtag" in e2  # date line stays inside Nr. 2
    assert "AH220101-L" in e2                   # trailer stays inside Nr. 2
    assert "Verteilung der Prozesskosten" not in e2


def test_section_after_last_ruling_is_excluded():
    r = split_volume(VOLUME, 2023)
    e3 = r.entries[2].text
    assert "AN070249" in e3
    assert "ERGÄNZUNGEN" not in e3
    assert "Klageeingänge" not in e3


def test_print_marks_running_heads_and_soft_hyphens_removed():
    r = split_volume(VOLUME, 2023)
    all_text = "\n".join(e.text for e in r.entries)
    assert "Seite 8" not in all_text
    assert "Kapitel I." not in all_text
    assert "relativierte" in all_text           # soft hyphen + newline joined
    assert "\n5\n" not in all_text              # bare page number dropped


def test_trailer_yields_docket_date_and_appeal_status():
    r = split_volume(VOLUME, 2023)
    e1, e2, e3 = r.entries
    assert (e1.docket, e1.decision_date) == ("AN200007-L", date(2023, 1, 24))
    assert e1.appeal_info == "Berufung am Obergericht anhängig"
    assert (e2.docket, e2.decision_date) == ("AH220101-L", date(2023, 6, 14))
    assert "kein Rechtsmittel" in e2.appeal_info
    # old form "(AGer., AN070249 vom 28. Januar 2008; …)" — date outside the volume's
    # plausible window is dropped, the docket is kept
    assert e3.docket == "AN070249"
    assert e3.decision_date is None


def test_numeric_trailer_date_2003_style():
    txt = VOLUME.replace("(AN200007-L Urteil vom 24. Januar 2023;", "(AGer., AN020607 vom 8.1.2003;")
    r = split_volume(txt, 2003)
    assert r.entries[0].docket == "AN020607"
    assert r.entries[0].decision_date == date(2003, 1, 8)


def test_volume_without_toc_still_splits_on_statute_headings():
    txt = VOLUME.split("Inhaltsverzeichnis")[0] + VOLUME.split("EDITORIAL", 1)[1]
    r = split_volume(txt, 2023)
    assert r.toc_count == 0
    assert [e.number for e in r.entries] == [1, 2, 3]
    assert r.entries[2].title == "ZPO 107; Verteilung der Prozesskosten"


# ------------------------------------------------------------ scraper

def _scraper(monkeypatch, tmp_path):
    s = ZHArbeitsgerichtSammlungScraper(state_dir=tmp_path)
    calls: list[str] = []

    def fake_get(url, **k):
        calls.append(url)

        class R:
            text = INDEX_HTML if url.endswith("entscheidsammlung") else ""
            content = b"%PDF-1.4" + b"x" * 20_000
        return R()

    monkeypatch.setattr(s, "get", fake_get)
    monkeypatch.setattr(mod, "extract_pdf_text", lambda pdf_bytes: VOLUME)
    return s, calls


def test_list_volumes_dedupes_and_caps_at_2023(monkeypatch, tmp_path):
    s, _ = _scraper(monkeypatch, tmp_path)
    vols = s.list_volumes()
    assert [y for y, _ in vols] == [2021, 2022, 2023]
    assert vols[1][1].endswith("Version%20AGer-Z%202022.pdf")
    assert all(u.startswith("https://www.gerichte-zh.ch/") for _, u in vols)


def test_discover_and_fetch_build_zh_arbeitsgericht_rows(monkeypatch, tmp_path):
    s, calls = _scraper(monkeypatch, tmp_path)
    stubs = list(s.discover_new())
    assert len(stubs) == 9                       # 3 volumes × 3 rulings (same fixture text)
    d = s.fetch_decision(stubs[-1])              # AGer-Z 2023 Nr. 3
    assert d.court == "zh_arbeitsgericht"
    assert d.canton == "ZH"
    assert d.decision_id == "zh_arbeitsgericht_AGer-Z 2023 Nr. 3"
    assert d.docket_number == "AGer-Z 2023 Nr. 3"
    assert d.docket_number_2 == "AN070249"
    assert d.collection == "AGer-Z 2023"
    assert d.external_id == "ager_z_2023_3"
    assert d.legal_area == "Arbeitsrecht"
    assert d.full_text.startswith("AGer-Z 2023 Nr. 3\nZPO 107; Verteilung der Prozesskosten\n")
    assert d.source_url == d.pdf_url == "https://www.gerichte-zh.ch/sites/default/files/2026-04/AGer-Z_2023.pdf"
    d1 = s.fetch_decision(stubs[6])              # AGer-Z 2023 Nr. 1
    assert d1.decision_date == date(2023, 1, 24)
    assert d1.appeal_info == "Berufung am Obergericht anhängig"


def test_known_volume_is_not_downloaded_again(monkeypatch, tmp_path):
    s, calls = _scraper(monkeypatch, tmp_path)
    s.state.mark_scraped("zh_arbeitsgericht_AGer-Z 2023 Nr. 1")
    s.state.mark_scraped("zh_arbeitsgericht_AGer-Z 2022 Nr. 1")
    stubs = list(s.discover_new())
    assert {st["year"] for st in stubs} == {2021}
    pdf_calls = [u for u in calls if u.endswith(".pdf")]
    assert len(pdf_calls) == 1 and "2021" in pdf_calls[0]
