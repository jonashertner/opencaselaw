"""Offline tests for source_dates.py: decision dates read from the source
(comparison report 2026-11-08, item #5). Fixtures are excerpts of the real
documents named in each test."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import source_dates as sd

TODAY = date(2026, 11, 8)


# ------------------------------------------------------------ EGMR

def test_egmr_docket_is_the_judgment_date():
    # Zuisens SA c. Suisse, no. 53377/11, decision of 23 August 2016 (HUDOC kpdate)
    assert sd.egmr_docket_date("20160823_53377_11", TODAY) == "2016-08-23"
    assert sd.egmr_docket_date("20250327_2633_23", TODAY) == "2025-03-27"


def test_egmr_docket_rejects_non_dates_and_future():
    assert sd.egmr_docket_date("53377/11", TODAY) is None
    assert sd.egmr_docket_date("20161323_1_11", TODAY) is None   # month 13
    assert sd.egmr_docket_date("20271105_38204_19", TODAY) is None  # after today
    assert sd.egmr_docket_date(None, TODAY) is None


def test_egmr_listing_two_digit_year_uses_docket_century():
    assert sd.egmr_listing_date("05.11.26", "20261105_38204_19", TODAY) == "2026-11-05"
    assert sd.egmr_listing_date("25.03.83", "19830325_8660_79", TODAY) == "1983-03-25"


# ------------------------------------------------------- ruling heads

NW_VA_20_9 = (
    "GERICHTE VERWALTUNGSGERICHT Marktgasse 4, 6371 Stans, 041 618 79 70, www.nw.ch\n"
    "VA 20 9 Urteil BGer 2C_405/2021 vom 14. Juni 2022/Abweisung\n"
    "Entscheid vom 30. November 2020\nVerwaltungsabteilung\n"
    "Besetzung Präsidentin Livia Zimmermann, Vorsitz,\n")


def test_head_skips_the_bger_appeal_note():
    assert sd.head_vom_date(NW_VA_20_9, today=TODAY) == "2020-11-30"


def test_head_reads_compound_kinds():
    t = ("GERICHTE VERWALTUNGSGERICHT Marktgasse 4, 6371 Stans\nVA 21 18\n"
         "Abschreibungsentscheid vom 17. August 2021\nVerwaltungsabteilung\n"
         "Besetzung Präsidentin Livia Zimmermann\nVerfahrensbeteiligte A.__,\n"
         "Sachverhalt: Mit Entscheid vom 16. Dezember 2019 …")
    assert sd.head_vom_date(t, today=TODAY) == "2021-08-17"


def test_head_stops_at_the_parties_block():
    # Only a reviewed decision named after "Verfahrensbeteiligte": no head date.
    t = ("OBERGERICHT\nOG V 25 19\nBesetzung Präsidentin X\nVerfahrensbeteiligte A\n"
         "Gegenstand Entscheid Kantonsgericht vom 12. Januar 2021")
    assert sd.head_vom_date(t, today=TODAY) is None


def test_head_ur_letterhead_layout():
    t = (" \n OBERGERICHT \n Verwaltungsrechtliche Abteilung \n______ \nOG V 25 19 \n \n"
         "Entscheid vom 27. März 2026 \n \n______ \nBesetzung \n Präsidentin Agnes H. Planzer")
    assert sd.head_vom_date(t, today=TODAY) == "2026-03-27"


def test_be_letterhead_de_fr_and_no_space():
    de = ("Obergericht\ndes Kantons Bern\nBeschwerdekammer in\nStrafsachen\nBeschluss\nBK 25 533\n"
          "Hochschulstrasse 17\nPostfach\n3001 Bern\nTelefon +41 31 635 48 09\n"
          "Bern, 5. Februar 2026\nBesetzung\nOberrichter Bähler (Präsident)")
    fr = ("Cour suprême\ndu canton de Berne\nDécision\nBK 26 325\nCase postale\n3001 Berne\n"
          "Berne, le 19 juin 2026\nComposition\nJuges d’appel Bähler")
    tight = "Beschluss\nBK 25 472\n3001 Bern\nBern, 1.Juni 2026\nBesetzung"
    places = ("Bern", "Berne")
    assert sd.place_date_head(de, places, today=TODAY) == "2026-02-05"
    assert sd.place_date_head(fr, places, today=TODAY) == "2026-06-19"
    assert sd.place_date_head(tight, places, today=TODAY) == "2026-06-01"


# ------------------------------------------------------- GR Praxis

PKG_1999_25 = (
    "PKG 1999 25\nhätte sich der Berufungskläger bewusst sein müssen …\n"
    + "x" * 600 + "\nSB 99 71\nUrteil vom 14. Dezember 1999\n"
    + "25 – Berufungsfrist …\n" + "y" * 3000
    + "\nSB 98 74\nUrteil vom 20. Januar 1999\n")


def test_gr_trailer_is_the_last_one_not_the_previous_entrys():
    assert sd.gr_yearbook_trailer(PKG_1999_25, "PKG 1999 25", TODAY) == "1999-01-20"


def test_gr_trailer_missing_returns_none_not_the_previous_entrys():
    # The PDF lost the entry's own trailer: only the previous entry's, far
    # from the end, remains. No date rather than the wrong one.
    t = "PKG 1999 25\n…\nSB 99 71\nUrteil vom 14. Dezember 1999\n25 – …\n" + "z" * 9000
    assert sd.gr_yearbook_trailer(t, "PKG 1999 25", TODAY) is None


def test_gr_trailer_same_line_and_after_volume_rejected():
    t = "… " * 50 + "\nS 23 137 Urteil vom 27. Februar 2024"
    assert sd.gr_yearbook_trailer(t, "PVG 2024 4", TODAY) == "2024-02-27"
    assert sd.gr_yearbook_trailer(t, "PVG 2021 4", TODAY) is None  # after volume + 1


def test_gr_pdf_key_matches_direct_and_federation_copies():
    a = "https://entscheidsuche.gr.ch/tribunavtplus/ServletDownload/PVG_2002_7_4d6e0efbfb0c?path=x"
    b = "https://entscheidsuche.gr.ch/tribunavtplus/ServletDownload/PVG_2002_7_ea3b182bee?path=y"
    assert sd.gr_yearbook_pdf_key(a) == sd.gr_yearbook_pdf_key(b) == "PVG_2002_7"
    assert sd.GR_YEARBOOK.match("PKG 1994 10") and sd.GR_YEARBOOK.match("PKG_1994_10")
    assert not sd.GR_YEARBOOK.match("ZK2 2011 45")


# ------------------------------------------------------------- AR GVP

def test_ar_filename_date():
    u = ("https://rechtsprechung.ar.ch/download/AR%20GVP/Gerichtsentscheide/2002/"
         "OG-20021114-20021114-ARGVP-2002-3410.pdf")
    assert sd.ar_filename_date(u, TODAY) == "2002-11-14"   # trailer "JuAK 14.11.02"
    assert sd.is_ar_gvp(u)
    v = ("https://rechtsprechung.ar.ch/download/AR GVP/Verwaltungsentscheide/1988/"
         "Verwaltung-19601128-19601128-ARGVP-1988-1018.pdf")
    assert sd.ar_filename_date(v, TODAY) == "1960-11-28"   # trailer "RRB 28.11.1960"
    k = "https://rechtsprechung.ar.ch/download/Kantonsgericht/2013/KG-20130117-K1S-12-2-20190618.pdf"
    assert sd.ar_filename_date(k, TODAY) == "2013-01-17"
    assert not sd.is_ar_gvp(k)
    assert sd.ar_filename_date("https://rechtsprechung.ar.ch/cache?id=x", TODAY) is None


# ----------------------------------------------------------------- UR

def test_ur_citation_line_needs_the_rows_own_docket():
    t = ("Kantonales Verfahrensrecht. … Mangels Beschwerdeberechtigung …\n"
         "Obergericht, 11. Februar 2020, OG V 19 63 \n…")
    assert sd.ur_citation_line(t, "2020_OG V 19 63", TODAY) == "2020-02-11"
    assert sd.ur_citation_line(t, "2020_OG_V_19_63.", TODAY) == "2020-02-11"
    assert sd.ur_citation_line(t, "OG V 19 6", TODAY) is None
    assert sd.ur_citation_line(t, "OG V 19 630", TODAY) is None


# ----------------------------------------------------------------- BL

def test_bl_slug_and_steuergericht_trailer():
    assert sd.bl_slug_date("2018-12-06-sv-5", TODAY) == "2018-12-06"
    assert sd.bl_slug_date("2018-09-07-stger-2", TODAY) == "2018-09-07"
    assert sd.bl_slug_date("745 16 144", TODAY) is None
    t = "… Demnach erweist sich der Rekurs als unbegründet.\nEntscheid Nr. 61/2003 vom 4.7.2003\nBack to Top"
    assert sd.bl_steuergericht_trailer(t, "2003/61", TODAY) == "2003-07-04"
    assert sd.bl_steuergericht_trailer(t, "2003/62", TODAY) is None
    t2 = "Entscheid Nr. 044/2008 vom 30.05.2008"
    assert sd.bl_steuergericht_trailer(t2, "2008/44", TODAY) == "2008-05-30"


def test_bl_kg_head_with_and_without_comma():
    a = ("Urteil des Kantonsgerichts Basel-Landschaft, Abteilung Sozialversicherungsrecht "
         "vom 29. September 2016 (745 16 144) Ergänzungsleistungen")
    b = ("Urteil des Kantonsgerichts Basel-Landschaft, Abteilung Strafrecht, vom 8. November "
         "2019 (460 19 151) Strafrecht")
    assert sd.bl_kg_head(a, TODAY) == "2016-09-29"
    assert sd.bl_kg_head(b, TODAY) == "2019-11-08"


# ------------------------------------------------------- plausibility

def test_docket_year_and_plausibility():
    assert sd.docket_year("720 13 319") == 2013
    assert sd.docket_year("725 2023 77 / 170") == 2023
    assert sd.docket_year("VA 21 18") == 2021
    assert sd.docket_year("OG V 22 38") == 2022
    assert sd.docket_year("BK 2025 533") == 2025
    assert sd.docket_year("16855") is None
    assert not sd.plausible("2019-12-16", "VA 21 18", None)      # before registration
    assert sd.plausible("2021-08-17", "VA 21 18", None)
    assert not sd.plausible("2023-08-01", "725 2023 77", "2023-07-24")  # after publication
    assert sd.plausible("2023-06-24", "725 2023 77", "2023-07-24")


def test_placeholders():
    assert sd.is_placeholder(None) and sd.is_placeholder("") and sd.is_placeholder("0000-00-00")
    assert sd.is_placeholder("2026-01-01") and sd.is_placeholder("1905-01-01")
    assert sd.is_placeholder("2026-02-14", "2026-02-14T01:02:03")
    assert not sd.is_placeholder("2026-02-14", "2026-02-15T01:02:03")
    assert not sd.is_placeholder("2016-08-23")


def test_parse_one_rejects_impossible_and_future():
    assert sd.parse_one("31. Februar 2020", today=TODAY) is None
    assert sd.parse_one("1. Dezember 2027", today=TODAY) is None
    assert sd.parse_one("1er septembre 2026", today=TODAY) == "2026-09-01"
    assert sd.parse_one("1°marzo 2024", today=TODAY) == "2024-03-01"
