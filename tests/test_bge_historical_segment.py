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


def test_volume_and_page():
    assert seg.volume_and_page("78_IV_83") == (78, 83)
    assert seg.volume_and_page("bge_historical_45_III_126") == (45, 126)
    assert seg.volume_and_page("bge_116_Ia_28") is None       # volume 116: not historical
    assert seg.volume_and_page("bger_4A_231_2014") is None


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
