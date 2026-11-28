"""GR rulings dated by their own head, not by the decision under appeal
(report 2026-11-27: GR PZ 2006 225 served as 14 November 2006, the date of
the Kreispräsident's ruling it reviews; its letterhead reads "Chur, 16. April
2007")."""
from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stdout
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

import repair_decision_dates as rdd  # noqa: E402
import repair_source_dates as R  # noqa: E402
import source_dates as sd  # noqa: E402

TODAY = date(2026, 11, 27)

PZ_06_225 = (
    "Kantonsgericht von Graubünden\nDretgira chantunala dal Grischun\nTribunale cantonale dei Grigioni\n"
    "_______________\nRef.:\nChur, 16. April 2007\nSchriftlich mitgeteilt am:\nPZ 06 225\nVerfügung\n"
    "Kantonsgerichtspräsidium\nVorsitz\nPräsident Brunner\nAktuarin ad hoc\nBäder Federspiel\n——————\n"
    "Im Rekurs\ndes X., Gesuchsteller und Rekurrent, vertreten durch Rechtsanwalt Dr. iur. Marco\n"
    "Ettisberger, Hinterm Bach 40, Postfach 203, 7002 Chur,\ngegen\ndie Verfügung des Kreispräsidenten "
    "Chur vom 14. November 2006, mitgeteilt am\n15. November 2006, in Sachen ...\n")


def test_letterhead_wins_over_the_decision_under_appeal():
    assert sd.gr_head_date(PZ_06_225, today=TODAY) == "2007-04-16"
    # the old rule read the decision under appeal
    assert sd.head_vom_date(PZ_06_225, 2500, TODAY) == "2006-11-14"


def test_federal_court_note_in_the_head_is_not_the_rulings_date():
    t = ("Kantonsgericht von Graubünden\nRef.:\nChur, 26. März 2003\nSchriftlich mitgeteilt am: \n"
         "SB 02 50\n(nicht mündlich eröffnet)\n(Eine gegen diese Entscheidung erhobene staatsrechtliche "
         "Beschwerde hat das Bundesgericht mit Urteil vom 03. März 2003 (1P.494/2002) abgewiesen.)\nUrteil\n")
    assert sd.gr_head_date(t, today=TODAY) == "2003-03-26"
    # without a letterhead, a noted BGer ruling alone gives nothing
    no_letterhead = t.replace("Chur, 26. März 2003", "")
    assert sd.gr_head_date(no_letterhead, today=TODAY) is None


def test_newer_kantonsgericht_layout_stops_before_the_challenged_decision():
    t = ("Kantonsgericht von Graubünden\nEntscheid vom 02. Mai 2022\nReferenz\nKSK 22 8\nInstanz\n"
         "Schuldbetreibungs- und Konkurskammer\nBesetzung\nCavegn, Vorsitzender\nParteien\nA._____\n"
         "Anfechtungsobj.\nPfändungsvollzug des Betreibungsamts vom 17.02.2022\nMitteilung\n03. Mai 2022\n")
    assert sd.gr_head_date(t, today=TODAY) == "2022-05-02"


def test_verwaltungsgericht_and_italian_heads():
    vg = ("VERWALTUNGSGERICHT DES KANTONS GRAUBÜNDEN\nR 17 84\n5. Kammer\nVorsitz\nMeisser\nURTEIL\n"
          "vom 19. Juni 2018\nin der verwaltungsrechtlichen Streitsache\nA._____\n")
    assert sd.gr_head_date(vg, today=TODAY) == "2018-06-19"
    it = ("TRIBUNALE AMMINISTRATIVO DEL CANTONE DEI GRIGIONI\nS 22 99\nGiudice unico\nRacioppi\n"
          "SENTENZA\ndel 31 ottobre 2022\nnella vertenza di diritto delle assicurazioni sociali\n")
    assert sd.gr_head_date(it, today=TODAY) == "2022-10-31"
    coira = "Tribunale cantonale dei Grigioni\n1 / 4\nRif.:\nCoira, 21 agosto 2018\nComunicata per scritto il:\n"
    assert sd.gr_head_date(coira, today=TODAY) == "2018-08-21"


def test_obergericht_italian_head_with_apostrophe_and_notification():
    # SBK 2025 111: the first rule draft missed "dell'8" and read a date
    # from the parties block below.
    t = ("Obergericht des Kantons Graubünden\nTribunale d'appello del Cantone dei Grigioni\n"
         "Decisione dell’8 aprile 2026\ncomunicata il 10 aprile 2026\nN. d'incarto\nSBK 25 111\nIstanza\n"
         "Camera delle esecuzioni e dei fallimenti\nParti\nA._____\nOggetto\n"
         "decisione dell'Ufficio del 17 novembre 2025\n")
    assert sd.gr_head_date(t, today=TODAY) == "2026-04-08"
    assert sd.gr_head_date(t.replace("dell’8 aprile 2026", ""), today=TODAY) is None


def _row(did, d, scraped, text, stamp=False):
    r = {"decision_id": did, "court": "gr_gerichte", "docket_number": "PZ 2006 225",
         "decision_date": d, "scraped_at": scraped, "full_text": text + "Erwägungen …" * 200}
    if stamp:
        r["publication_date"] = "2007-01-03"
        r["date_extraction"] = {"method": "text_vom", "metadata_date": "2007-01-03"}
    return r


def _run(shards, *extra):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = R.main(["--court", "gr_gerichte", *map(str, shards), "--today", TODAY.isoformat(), *extra])
    assert rc == 0
    return json.loads(buf.getvalue())


def _write(path, rows):
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    return path


def test_repair_corrects_damaged_and_pre_fix_rows_only(tmp_path):
    rows = [
        _row("gr_gerichte_PZ 2006 225", "2006-11-14", "2026-02-14T02:05:06", PZ_06_225, stamp=True),
        # post-fix scrape: the portal's own field is never second-guessed
        dict(_row("gr_gerichte_PZ 2006 226", "2007-04-17", "2026-10-02T01:00:00", PZ_06_225),
             docket_number="PZ 2006 226"),
    ]
    d = _write(tmp_path / "gr_gerichte.jsonl", rows)
    rep = _run([d])["gr_gerichte.jsonl"]
    assert rep["outcome:gr_own_head:corrected"] == 1
    assert rep["outcome:gr_own_head:differs_left_alone"] == 1
    _run([d], "--apply")
    out = [json.loads(x) for x in d.read_text(encoding="utf-8").splitlines()]
    assert out[0]["decision_date"] == "2007-04-16"
    assert out[0]["date_repair"]["rule"] == "gr_own_head" and out[0]["date_repair"]["from"] == "2006-11-14"
    assert out[0]["publication_date"] == "2007-01-03"   # only decision_date changes
    assert out[1]["decision_date"] == "2007-04-17"
    assert _run([d])["gr_gerichte.jsonl"].get("rows_changed", 0) == 0   # idempotent


def test_text_less_federation_copy_follows_its_twin(tmp_path):
    direct = _row("gr_gerichte_PZ 2006 225", "2006-11-14", "2026-02-14T02:05:06", PZ_06_225, stamp=True)
    stub = {"decision_id": "gr_gerichte_PZ_2006_225", "court": "gr_gerichte",
            "docket_number": "PZ 2006 225", "decision_date": "2006-11-14",
            "scraped_at": "2026-03-12T01:00:00",
            "full_text": "Graubünden Kantonsgericht 14.11.2006 PZ 2006 225 | Erbrecht"}
    d = _write(tmp_path / "gr_gerichte.jsonl", [direct])
    e = _write(tmp_path / "es_gr_gerichte.jsonl", [stub])
    rep = _run([d, e], "--simulate")
    assert rep["es_gr_gerichte.jsonl"]["outcome:twin_follow:corrected"] == 1
    assert rep["simulation"]["twins_split"] == 0


def test_the_0312_pass_never_touches_gr_again():
    assert {"gr_gerichte", "es_gr_gerichte"} <= rdd.NEVER_REPAIR_SHARDS
