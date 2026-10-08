"""Historical BGE rows hold the page range a ruling is printed on (user report
2026-10-07). BGE 78 IV 83 (Fyg gegen Born) opened with the last page of No 21
and closed with the head of No 23 (Friedlin): it was dated by a letter of
17 July 1951 quoted in No 21, and its only "Erwägung" was No 23's serial number.

The fixtures are abridged from the served texts (OCR errors kept: "30. 1fai
1952", '81"', "A.rtet", "1l juillet").
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
for p in (REPO, REPO / "scripts"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import bge_historical_segment as seg  # noqa: E402

FYG = (
    "82\nStrafgesetzbuch. No 21.\nEinen Mann in diesem Alter trifft erhöhte Verantwortung\n"
    "gegenüber Kindern. Übrigens ist in der Unter-\nsuchung nur davon die Rede gewesen, dass das\n"
    "Mädchen ihn gekitzelt habe; auch im Brief vom 17. Juli 1951 hat das Mädchen\n"
    "nichts anderes geschrieben.\nAbwehr.\nStrafgesetzbuch. No 22.\n83\n"
    "22. Auszug aus dem Urteil des Kassationshofes vom 3. Juni 1952\ni. S. Fyg gegen Born.\n"
    "Art. 57 Abs. 1 OR, Art. 32, 145 StGB. Sachbeschädigung durch\nAbschuss einer Katze.\n"
    "Friedrich Fyg sah am Nachmittag des 25. November\n1951 wie schon öfters eine fremde Katze.\n"
    "Aus den Erwägungen :\nDer Beschwerdeführer beruft sich auf Art. 57 OR, wo-\n"
    "nach der Besitzer eines Grundstückes berechtigt ist ...\n\n84\nStrafgesetzbuch. No 23.\n"
    "rechtigte das ihn nicht, wahllos jede beliebige Katze abzuschiessen.\n"
    "jede Vorstellung bei Born zu unterlassen.\n"
    "23. Auszug aus dem Urteil des Kassationshofes vom 30. 1fai 1952\n"
    "i. S. Staatsanwaltschaft des Kantons Aargau gegen Friedlin.\n"
    "Art. 148 StGB trifft auf den sog. Prozessbetrug nicht zu.\nA. -\n"
    "Margrith Friedlin und ihr am 6. Juli 1948 aus-\nserehelich geborenes Kind klagten\n"
    "Strafgesetzbuch. No 23.\n85\nBaden gegen Ernst Hitz auf Entschädigung.\n"
)

# BGE 78 IV 81: page 81's number came through as '81"'.
A_GEGEN_LUZERN = (
    "80\nVerfahren. No 20.\ndeclare le pourvoi irrecevable.\nVgl. auch Nr. 2, 7, 18. -\n"
    "IMPRIMERIES REUNIES S. A., LAUSANNE\n81\"\nI. STRAFGESETZBUCH\nCODE PENAL\n"
    "21. Auszug aus dem Urteil des Kassationshofes vom 5. März\n"
    "1952 i. S. A. gegen Staatsanwaltschaft des Kantons Luzern.\nArt. 191 StGB.\n"
    "Der Kassationshof zieht in Erwägung :\n1. -\nDer Beschwerdeführer ...\n\n82\n"
    "Strafgesetzbuch. No 21.\nEinen Mann in diesem Alter ...\nStrafgesetzbuch. No 22.\n83\n"
    "22. Auszug aus dem Urteil des Kassationshofes vom 3. Juni 1952\ni. S. Fyg gegen Born.\n"
)

# BGE 8 I 290: the ruling noun of No 47 is OCR noise; No 48 confirms the sequence.
CURIEL = (
    "290 \nA. Staatsrechtliche Entscheidungen. V. Abschnitt. Staatsverträge. \n"
    "1)ie Auslieferung wird bewilligt. \n4. Vertrag mit Frankreich. -\n"
    "47. A.rtet du 3 Juin 1882 dans la cause Curiel. \nPar note du 24 Decembre 1881, "
    "communiquee au Tribunal federal \n291 \nle Conseil federal ... \n294 \n"
    "Par ces motifs, le Tribunal federal prononce : \nL'extradition est accordee. \n"
    "48. Sentenza del 5 maggio 1882 nella causa Bernasconi \ne Vela. \n"
)

# BGE 14 I 598: the document opens with the ruling, no page number survived.
STAUB = (
    "95. Urtheil vom 16. November 1888\nin Sachen Staub gegen Rust.\n"
    "A. Durch Urtheil vom 29. Dezember 1887 hat das Kantonsgericht des Kantons Zug erkannt:\n"
    "1. Beklagte seien pflichtig, die Police ... anzuerkennen.\n"
    "Das Bundesgericht zieht in Erwägung:\n1. Die Entscheidung über die Kompetenz ...\n"
)

# BGE 1 I 26: Fraktur set, read with an Antiqua model — nothing to anchor on.
FRAKTUR = (
    "26\n1. Abschnitt. Bundesverfassung.\n;r;emnadj\n~at bag munbcggeridjt\ncrtannt:\n"
    "6. Ud~eH llom 12. ~ai 1875 in ~adjcn ij. ~e~er.\nA. mientidj\n27\n~)(e~et ift feit\n"
)


def test_fyg_is_cut_to_its_own_ruling_and_dated_by_its_header():
    s = seg.segment(FYG, 83)
    assert s is not None and s.serial == 22
    own = FYG[s.start:s.end]
    assert own.startswith("22. Auszug aus dem Urteil des Kassationshofes vom 3. Juni 1952")
    assert "Brief vom 17. Juli 1951" not in own        # No 21's tail
    assert "Friedlin" not in own                        # No 23's head
    assert "jede Vorstellung bei Born zu unterlassen." in own   # its last sentence
    assert s.header == "22. Auszug aus dem Urteil des Kassationshofes vom 3. Juni 1952 i. S. Fyg gegen Born."
    assert seg.header_date(s.header, 78) == date(1952, 6, 3)


def test_the_next_ruling_starts_at_its_header_and_an_unreadable_date_stays_unread():
    # No 23 (BGE 78 IV 84) begins on page 84 of the same spread.
    s = seg.segment(FYG, 84)
    assert s is not None and s.serial == 23
    assert FYG[s.start:s.end].startswith("23. Auszug") and "Fyg gegen Born" not in FYG[s.start:s.end]
    # "30. 1fai 1952": no date rather than a body date such as 6 July 1948.
    assert seg.header_date(s.header, 78) is None


def test_a_page_number_with_ocr_noise_still_places_the_header():
    s = seg.segment(A_GEGEN_LUZERN, 81)
    assert s is not None and s.serial == 21
    own = A_GEGEN_LUZERN[s.start:s.end]
    assert "Verfahren. No 20." not in own and "Fyg" not in own
    assert seg.header_date(s.header, 78) == date(1952, 3, 5)


def test_a_header_whose_ruling_noun_ocr_destroyed_counts_when_its_serial_continues():
    s = seg.segment(CURIEL, 290)
    assert s is not None and s.serial == 47
    own = CURIEL[s.start:s.end]
    assert own.startswith("47. A.rtet du 3 Juin 1882") and "Bernasconi" not in own
    assert seg.header_date(s.header, 8) == date(1882, 6, 3)


def test_a_text_that_opens_with_its_header_and_has_no_page_numbers():
    s = seg.segment(STAUB, 598)
    assert s is not None and (s.start, s.end) == (0, len(STAUB))
    assert seg.header_date(s.header, 14) == date(1888, 11, 16)


def test_numbered_paragraphs_are_not_rulings():
    # "1. Beklagte ..." and "1. Die Entscheidung ..." carry no ruling noun after
    # the number; "A. Durch Urtheil vom ..." has no serial number.
    assert [h.serial for h in seg.ruling_headers(STAUB)] == [95]


def test_an_unreadable_text_is_left_alone():
    assert seg.segment(FRAKTUR, 26) is None
    assert seg.own_text(FRAKTUR, 26) == (FRAKTUR, None)


def test_a_garbled_day_is_repaired_only_before_a_month():
    assert seg.header_date("26. Arret du 1l juillet 1927 dans la cause Spycher.", 53) == date(1927, 7, 11)
    assert seg.header_date("32. Sll1teDl& 24 ottobre 1919 nella causa Ehret.", 45) == date(1919, 10, 24)
    # a date outside the volume window is no ruling date of this volume
    assert seg.header_date("12. Urteil vom 3. März 1930 i. S. X.", 78) is None


def test_measured_ocr_damage_to_a_header_date_is_undone():
    # accent dropped (BGE 73 IV 132), stray marks (69 IV 54), preposition misread (42 II 72)
    assert seg.header_date("34. Arret de la Cour de cassation penale du 28 fevrier 1947 "
                           "dans la cause Robert.", 73) == date(1947, 2, 28)
    assert seg.header_date("11. Urteil des Kassationshofes vom 11 \u2022 .Juni 1943 i. S. "
                           "Staatsanwaltschaft gegen Calori.", 69) == date(1943, 6, 11)
    assert seg.header_date("12. Urteil der II. Zivilabteilung Tom 16. Januar 1916 i. S. "
                           "Bodenkreditanstalt gegen Lenz.", 42) == date(1916, 1, 16)
    assert seg.header_date("16. Urteil vom 10. Mal 1940 i. S. von Arx.", 66) == date(1940, 5, 10)
    # beyond repair: no date, never a guess
    assert seg.header_date("16. Auszug aus dem Urteil vom 10. Mal UMO i. S. von Arx.", 66) is None


def test_a_stored_date_survives_when_the_header_shows_its_day_and_year():
    assert script._header_holds("8. Arret du 8 ferner 1933 dans la cause Jenny.", "1933-02-08")
    assert not script._header_holds("8. Arret du S ferner 1933 dans la cause Jenny.", "1931-06-19")
    assert not script._header_holds("22. Auszug vom 3. Juni 1952 i. S. Fyg.", "1951-07-17")
    # a readable month must be the stored one (BGE 54 II 464: 12 December, not October)
    assert not script._header_holds("86. Urteil der I. Zivila.bteUung vom 12~ Dezember 1928 i. S. "
                                    "J. Wyler gegen Eheleute Stalder.", "1928-10-12")
    # and the noise-glued day is not the stored day either
    assert not script._header_holds("22. Sentenza !3 aprile 1914 nella causa Brunschwyler.", "1914-04-03")


def test_a_stored_date_with_an_ocr_year_is_not_kept():
    # BGE 47 I 242 was stored as 16 July 1991 from "vom 16. Juli 1991" (1921)
    text = "36. Urteil vom 16. Juli 1991 i. S. Stutz gegen Z\u00fcrich Regierungsrat.\nArt. 4 BV.\n"
    d = script.decide(_row("bge_historical_47_I_242", "47_I_242", "1991-07-16", text))
    assert d["_date_method"] == "placeholder" and d["decision_date"] == "1921-01-01"


def test_a_header_whose_i_s_lost_a_period_is_still_a_header():
    # BGE 63 III 57: "16. Entscheid vom as. April 1937 i S. Schweiz. ..."
    text = ("57\nSchuldbetreibungs- und Konkursrecht. N° 16.\n"
            "16. Entscheid vom as. April 1937 i S. Schweiz. Volksbank.\nArt. 93 SchKG.\n"
            "A. - Die Beschwerdeführerin ...\n58\nDie Schuldbetreibungs- und Konkurskammer zieht in Erwägung:\n"
            "1. - Nach der Rechtsprechung ...\n17. Entscheid vom 20. Mai 1937 i. S. Ackermann.\n")
    s = seg.segment(text, 57)
    assert s is not None and s.serial == 16
    assert "Ackermann" not in text[s.start:s.end]
    assert seg.header_date(s.header, 63) is None        # "as." is no day


def test_a_next_ruling_ends_the_text_even_when_ocr_garbled_its_serial_or_its_heading():
    # BGE 79 II 137: No 22 was read as "2."; No 23 still ends it.
    text = ("136\nObligationenrecht. N° 21.\n... schliesst die Klage aus.\n137\n"
            "2. Urteil der 11. Zivilabteilung vom 2. Juli 1953 i. S. Schaad gegen Meier.\n"
            "Art. 41 OR.\nA. - Der Kläger ...\n138\n1. - Die Berufung ist ...\n"
            "139\n23. Auszug aus dem Urteil der I. Zivilabteilung vom 22. April 1953 i. S. X gegen Y.\n")
    s = seg.segment(text, 137)
    assert s is not None and text[s.end:].startswith("23. Auszug")
    assert seg.header_date(s.header, 79) == date(1953, 7, 2)
    # BGE 75 III 44: "13. Ardt du 4 aoftt 1949 dans la cause Hausmann." after No 12.
    text = ("44\n12. Entscheid vom 10. Juni 1949 i. S. Schlachtviehversicherungskasse.\n"
            "Art. 92 SchKG.\n13. - Ein Erwägungsabsatz vom 3. März 1949 ohne Parteien.\n"
            "45\nDie Kammer zieht in Erwägung ...\n50\n"
            "13. Ardt du 4 aoftt 1949 dans la cause Hausmann.\nArt. 93 LP.\n")
    s = seg.segment(text, 44)
    assert s is not None and text[s.end:].startswith("13. Ardt")


def test_the_date_line_of_the_own_header_is_never_the_next_ruling():
    # BGE 52 I 54: the header's second line opens with "10.", the day; without
    # page numbers it ended the ruling after its first line.
    text = ("9. Auszug aus dem Urteil des Kassa.tionshofes vom\n"
            "10. M\u00e4rz 1926 i. S. Bundesanwa.ltschaft gegen Stettler.\n"
            "Patenttaxengesetz: Art. 1 Abs. 1.\nA. - Der Angeklagte ...\n"
            "Demnach erkennt der Kassationshof:\nDie Beschwerde wird abgewiesen.\n")
    s = seg.segment(text, 54)
    assert s is not None and (s.start, s.end) == (0, len(text))
    assert seg.header_date(s.header, 52) == date(1926, 3, 10)


def test_a_header_year_the_volume_cannot_hold_is_ocr():
    # DFR scans: "21. Dezember 1915" read as 1916 (BGE 41 II 739, volume 1915);
    # "11. Oktober 1928" read as 1925 (54 III 268, volume 1928)
    assert seg.header_date("97. Urteil dar IL ZivUabteilung vom 2l Dezember 1916 i. S. Wegmann.", 41) is None
    assert seg.header_date("62. Entscheid vom 11. Oktober 1925 i. S. Robert Aebi & Oie A.-G.", 54) is None
    # a ruling of the year before is a late publication (4 I 147, volume 1878)
    assert seg.header_date("30. Urtheil vom 22. Februar 1877 in Sachen Roget und Comp.", 4) == date(1877, 2, 22)


def test_volume_and_page():
    assert seg.volume_and_page("78_IV_83") == (78, 83)
    assert seg.volume_and_page("bge_historical_45_III_126") == (45, 126)
    assert seg.volume_and_page("bge_116_Ia_28") is None       # volume 116: not historical
    assert seg.volume_and_page("bger_4A_231_2014") is None



# ── validation on the published rows (2026-10-07) ────────────────────────────
# 14,578 rows of the published dataset, read against the DFR scans: the cases
# below placed, cut or dated a neighbour's ruling before these rules.

def _page(n: int) -> str:
    """About one page of body text without page numbers (2,000 characters)."""
    return "Ein Absatz der Begründung ohne Seitenzahl, wie er im Band steht.\n" * (n * 31)


# BGE 14 I 479: the right-hand page is read header first; the scan has No 73
# (Godat c. Hoffmann) at the top of page 479, No 74 on a later page.
GODAT = (
    "478 \nB. Civilrechtspflege. \nne~men. ~ei bierer @)ad)lage ift ben 3ntereffen beg Stiiuferg \n"
    + _page(1)
    + "73. Arret du 21 Septembre 1888 dans la cause Godat \ncontre Hotfmann. \n479 \n"
    "Les conseils des parties reprennent les conclusions formu-\n"
    "lees devant la derniere instance cantonale.\n" + _page(3)
    + "74. Arret du 31 Aout 1888 dans la cause Rody \ncontre Savoy. \n"
    "Le recourant a conelu, a l'audi'ence de ce jour.\n"
)


def test_a_right_hand_page_read_header_first_keeps_its_own_ruling():
    s = seg.segment(GODAT, 479)
    assert s is not None and s.serial == 73
    assert GODAT[s.start:].startswith("73. Arret du 21 Septembre 1888")
    assert GODAT[s.end:].startswith("74. Arret")
    assert seg.header_date(s.header, 14) == date(1888, 9, 21)


def test_a_header_that_ends_a_page_stays_on_that_page():
    # BGE 4 I 60: No 16 begins two lines above page 61, whose number follows.
    text = ("Aranno e reietto in via d\u00b7ordine.\n2. Bundesgerichtliehe Kompetenz in Civilsachen.\n"
            "Competence du Tribunal federal en matiere civile.\n"
            "16. Arret du 29 Mars 1878 dans la cause Bonvin.\n"
            "Par exploit notifie le 18 Octobre 1877, l'Etat du Valais a\n"
            "invite Charles-Marie Bonvin fils, a Sion, a 1ui payer dans le\n"
            "I. Organisation der Bundesrechtspflege. N\u00b0 16.\n61\n"
            "terme Mgalla somme de 4158 fr., avec interet des le 1er Juin\n")
    s = seg.segment(text, 60)
    assert s is not None and s.serial == 16
    assert seg.header_date(s.header, 4) == date(1878, 3, 29)


def test_a_header_more_than_a_page_past_the_reference_page_is_not_its_own():
    # BGE 44 III 163: only page 162 is numbered; No 45 stands 7,000 characters on.
    text = ("162 \nEntscheidungen der Schuldbetreibungs-\n" + _page(3)
            + "45. Beschluss vom G. November 1918 i. S. Schrimll. \n"
            "Stellung des Bundesgerichtes in Pfandstundungssachen.\n")
    assert seg.segment(text, 163) is None


def test_two_headers_without_the_reference_page_number_are_not_guessed():
    # BGE 57 III 19: page 19's number is lost; No 6 can begin on page 18.
    text = ("18 \nSchuldbetreibungs- und Konkursrecht. N\u00b0 5.\n... der Rekurs wird abgewiesen.\n"
            "6. Entscheid vom 19. Janu&r 1931 i. S. Mattes.\nArt. 92 SchKG.\n" + _page(1)
            + "7. Entscheid vom 22. Januar 1931 i. S. Huber.\nArt. 93 SchKG.\n")
    assert seg.segment(text, 19) is None


def test_a_next_ruling_more_than_a_page_on_ends_the_ruling():
    # BGE 40 III 332: pages 333 and 334 lost their numbers; No 60 stands
    # 5,300 characters on, just before page 335, and ends No 59.
    text = ("332 \nEntscheidungen der Schuldbetreibungs-\n"
            "59. Entscheid vom 15. September 1914 i. S. Sigg. \n"
            "Widerspruchsverfahren. Anwendbarkeit von Art. 109 SchKG.\n" + _page(3)
            + "60. Entscheid vom 30. September 1914 i. S. Forster, \nAltorfer & Oie und Genossen.\n"
            "335\nDer Rekurs wird abgewiesen.\n")
    s = seg.segment(text, 332)
    assert s is not None and s.serial == 59
    assert text[s.end:].startswith("60. Entscheid")


# BGE 47 III 116: OCR garbled No 35's heading ("Besohluss", "1Sa1"); No 36 is
# on page 117. Taking No 36 dated the row 30 July 1921 and dropped No 35.
BUERER = (
    "116 \nSanierung von Hotelunternehmungen. N0 35. \n"
    "35. Auszug aus dem Besohluss vom aa. September 1Sa1 \ni. S. B\u00fcrer. \n"
    "Die Ausdehnung des Pfandnachlassverfahrens auf andere \n"
    "als zum Fortbetrieb des Hotelgewerbes notwendige Grundst\u00fccke ...\n"
    "OFDAG Offset-, Formular- und Fotodruck AG 3000 Bem \n"
    "A. Schuldhetreihungs- und KonkursrechL. \nPoursuite et faiIliLe. \n"
    "36. Arret du 30 juillet 1921 dans la cause Flotron et. oonsorts. \n"
    "Art. 19 LP. La dccision du commissaire au sursis ...\n"
)


def test_a_garbled_own_heading_is_never_replaced_by_the_next_ruling():
    assert seg.segment(BUERER, 116) is None
    # BGE 45 I 54: "7. Auszug aus d.em Urteil vom 9. Kai 1919 i. S. Biklin" before No 8
    text = ("54\nStaatsrecht.\n7. Auszug aus d.em Urteil vom 9. Kai 1919 i. S. Biklin\n"
            "gegen St. Gallen.\nAnerkennung eines Bergregals.\n... stand.\n"
            "Gewaltentrennung. N\u00b0 8.\n8. Urteil vom 17. F.bruar 1919\n"
            "i. S. Fischer und D\u00fcrrenmatt gegen Bern.\n55\nLegitimation ...\n")
    assert seg.segment(text, 54) is None
    # BGE 1 I 520: No 141 is Fraktur read as Antiqua, only its number and year
    # survive; No 142 begins on page 521 (BGE 1 I 521)
    text = ("520 \nB. Civilreehtspflege. \n141. mef~lu\u00fc Uom 9. ,sufi 1875 in Sa~en ma~V!i. \n"
            ":l)emtta~ ~at bag munbeggeti~t \nedannt: \n"
            "142. Arret dit 24 septembre 1875, dans la cattse de la Muni- \ncipalite de Sion.\n")
    assert seg.segment(text, 520) is None
    # a date opening a line is no heading: "28. November 1916 aufgehoben ..."
    text = ("181\nObligationenrecht. N\u00b0 29.\n... das Urteil vom \n"
            "28. November 1916 aufgehoben und die Klage in der H\u00f6he gutgeheissen.\n"
            "29. Urteil der II. Zivilabteilung vom 8. M\u00e4rz 1917 i. S. Meier gegen Huber.\n")
    s = seg.segment(text, 181)
    assert s is not None and s.serial == 29


def test_ocr_spellings_of_the_ruling_noun_and_of_cause():
    # BGE 31 I 33: the OCR of volumes 30-39 opens the text with "Arteil".
    text = ("6. Arteil vom 2. Februar 1905\nin Sachen Zuppinger gegen Regierungsrat Z\u00fcrich.\n"
            "Internationale Doppelbesteuerung.\nA. Der Rekurrent ...\n")
    s = seg.segment(text, 33)
    assert s is not None and (s.start, s.end) == (0, len(text))
    assert seg.header_date(s.header, 31) == date(1905, 2, 2)
    # volumes 40-64: a stray mark or a misread first letter before the noun,
    # "vom" glued to it (BGE 40 I 116), "Arrit" (41 I 384); never "Vorteil"
    assert [h.serial for h in seg.ruling_headers(
        "13. T1rteilvom 12. M\u00e4rz 1914 i. S. Politische Gemeinde St. Gallen.\n")] == [13]
    assert seg.header_date("13. T1rteilvom 12. M\u00e4rz 1914 i. S. Politische Gemeinde.", 40) == date(1914, 3, 12)
    assert [h.serial for h in seg.ruling_headers(
        "28. 'Urteil der I. Zivila.btenung vom 27. Februa.r 1915 \ni. S. Z\u00fcrioh.\n")] == [28]
    assert [h.serial for h in seg.ruling_headers(
        "55. Arrit du 24 decembre 1915 \ndans Ia cause J. Brann & eie contre Geneve.\n")] == [55]
    assert seg.ruling_headers("3. Vorteil vom 3. Mai 1915 i. S. X gegen Y.\n") == []
    # BGE 62 II 193: "dans la causa Eichoz contra Bava.ud." names the parties
    text = ("49. Arret d.e 1a IIe Beetien eime d.u 19 juin 1936 \n"
            "dans la causa Eichoz contra Bava.ud. \nLe deces de l'enfant ...\n")
    assert [h.serial for h in seg.ruling_headers(text)] == [49]


def test_a_day_glued_to_ocr_noise_gives_no_date():
    # DFR scans: 2 April (BGE 40 II 109), 22 October (40 III 355), 17 December (1 I 159)
    assert seg.header_date("22. Sentenza !3 aprile 1914 della na Sezione civile nella causa "
                           "S. A. Brunschw)'ler.", 40) is None
    assert seg.header_date("64. Sentenza. a2 ottobre 1914 nella causa Raineri.", 40) is None
    assert seg.header_date("41. Arret du 1.7 decembre 1875 dans la cause Giroud.", 1) is None
    assert seg.header_date("12. Urteil vom a2. januar 1914 i. S.Luzern gegen St. Gallen.", 40) is None
    # not noise: a hearing range, the Italian elision, a two-digit day
    assert seg.header_date("109. Urtheil vom 1./2. Dezember 1882 in Sachen Meier.", 8) == date(1882, 12, 2)
    assert seg.header_date("56. Sentenza dell'8 luglio 1882 nella causa Rossi.", 8) == date(1882, 7, 8)
    assert seg.header_date("105. Sentenza. deI :30 dicembre 1905 nella causa Bianchi.", 31) == date(1905, 12, 30)

# ── the shard repair ─────────────────────────────────────────────────────────

import segment_bge_historical as script  # noqa: E402


def _row(did, docket, ddate, text):
    return {"decision_id": did, "court": "bge_historical", "docket_number": docket,
            "decision_date": ddate, "full_text": text, "cited_decisions": ["BGE 1 I 1"]}


def test_shard_repair_cuts_dates_stamps_and_is_idempotent(tmp_path):
    shard = tmp_path / "bge_historical.jsonl"
    rows = [
        _row("bge_historical_78_IV_83", "78_IV_83", "1951-07-17", FYG),
        _row("bge_historical_1_I_26", "1_I_26", "1875-01-01", FRAKTUR),
        _row("bge_historical_14_I_598", "14_I_598", "1888-11-16", STAUB),
        {"decision_id": "bge_x", "court": "bge", "docket_number": "x", "full_text": "y"},
    ]
    shard.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    stats = script.run(shard, apply=True, examples=0)
    assert stats["rows"] == 3 and stats["other_court"] == 1
    assert stats["text_cut"] == 1 and stats["date_changed"] == 1
    assert stats["outcome:own_header_not_placed"] == 1

    out = [json.loads(line) for line in shard.read_text().splitlines()]
    fyg = out[0]
    assert fyg["full_text"].startswith("22. Auszug aus dem Urteil")
    assert "Friedlin" not in fyg["full_text"] and "17. Juli 1951" not in fyg["full_text"]
    assert fyg["decision_date"] == "1952-06-03"
    assert fyg["date_restore"]["from"] == "1951-07-17" and fyg["date_restore"]["method"] == "own_header"
    assert fyg["text_segment"]["chars_before"] == len(FYG)
    assert fyg["cited_decisions"] == []                  # recomputed from the own text
    assert out[1] == rows[1] and out[2] == rows[2] and out[3] == rows[3]

    undo = list(tmp_path.glob("bge_historical.jsonl.presegment-*.jsonl"))
    assert len(undo) == 1
    assert json.loads(undo[0].read_text().splitlines()[0]) == {
        "decision_id": "bge_historical_78_IV_83", "full_text": FYG}

    again = script.run(shard, apply=True, examples=0)
    assert again["rows_changed"] == 0
    assert [json.loads(line) for line in shard.read_text().splitlines()] == out


def test_shard_repair_drops_an_unverified_text_date_to_the_placeholder(tmp_path):
    # The header's date is unreadable and the stored date is not in the header:
    # it was read from the body (a lower court's ruling), so the row goes back
    # to the volume placeholder, which the API flags as estimated.
    text = ("104 \nSchuldbetreibungs- und Konkursrecht. N° 26. \n"
            "26. Arret du ?? juillet 1927 \ndans la cause Spycher et consorts. \n"
            "Par arret du 23 juin 1926, la Cour d'appel du canton de Fribourg a prononce la faillite.\n")
    shard = tmp_path / "bge_historical.jsonl"
    shard.write_text(json.dumps(_row("bge_historical_53_III_104", "53_III_104", "1926-06-23", text)) + "\n")
    stats = script.run(shard, apply=False, examples=0)
    assert stats["date:placeholder"] == 1 and stats["rows_changed"] == 1
    assert json.loads(shard.read_text())["decision_date"] == "1926-06-23"     # dry run wrote nothing
    script.run(shard, apply=True, examples=0)
    assert json.loads(shard.read_text())["decision_date"] == "1927-01-01"


# ── the scraper ──────────────────────────────────────────────────────────────

def test_scraper_stores_the_own_ruling_with_its_header_date(monkeypatch):
    from scrapers.bge_historical import BGEHistoricalScraper

    s = BGEHistoricalScraper.__new__(BGEHistoricalScraper)

    class _Resp:
        content = b""
        text = "<html><body><pre>" + FYG + "</pre></body></html>"

    monkeypatch.setattr(s, "get", lambda url, **kw: _Resp(), raising=False)
    stub = {"url": "https://servat.unibe.ch/dfr/c4078083.html", "docket_number": "78_IV_83",
            "bge_ref": "BGE 78 IV 83", "volume": 78, "section": "IV", "page": 83,
            "year": 1952, "is_pdf": False}
    d = s.fetch_decision(stub)
    assert d is not None
    assert d.full_text.startswith("22. Auszug aus dem Urteil des Kassationshofes")
    assert "Friedlin" not in d.full_text
    assert d.decision_date == date(1952, 6, 3)
