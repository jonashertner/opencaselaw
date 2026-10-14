"""scripts/restore_tribuna_dates.py — Tribuna rows stored before the 2026-09-27
parser fix get the date of their ruling's own heading (report 2026-10-13)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import restore_tribuna_dates as r  # noqa: E402

TODAY = "2026-10-14"
PRE = "2026-09-26T08:00:00+00:00"
POST = "2026-09-30T08:00:00+00:00"

FR_HEAD = ("Tribunal cantonal TC Kantonsgericht KG Rue des Augustins 3, case postale 630, "
           "1701 Fribourg — Pouvoir Judiciaire PJ 604 2026 58 Arrêt du 1er septembre 2026 "
           "Cour fiscale Composition Président : X Parties A.________, recourant, contre "
           "Service cantonal des contributions, autorité intimée Objet Impôt — décision "
           "sur réclamation du 12 mai 2026 ")
BE_HEAD = ("ALV 200 2025 737 MAK/PES/SEE Verwaltungsgericht des Kantons Bern "
           "Sozialversicherungsrechtliche Abteilung Urteil der Einzelrichterin vom "
           "11. November 2025 Verwaltungsrichterin Mauerhofer Gerichtsschreiber Peter "
           "A.________ Beschwerdeführer gegen Amt für Arbeitslosenversicherung, "
           "Verfügung vom 3. September 2025 ")


def _row(court, docket, stored, text, scraped=PRE):
    return {"decision_id": f"{court}_{docket}", "court": court, "docket_number": docket,
            "decision_date": stored, "scraped_at": scraped, "full_text": text}


# ── heading_date ────────────────────────────────────────────────────────


def test_french_heading_with_premier():
    assert r.heading_date(FR_HEAD, "604 2026 58", "fr_gerichte", TODAY) == ("2026-09-01", "heading")


def test_heading_with_words_between_noun_and_date():
    assert r.heading_date(BE_HEAD, "200 2025 737", "be_verwaltungsgericht", TODAY)[0] == "2025-11-11"


def test_italian_and_presidial_forms():
    gr = ("Kantonsgericht von Graubünden Tribunale cantonale dei Grigioni Decisione del "
          "15 novembre 2024 N. d'incarto KSK 24 93 Parti A._____ reclamante")
    zg = ("II. Beschwerdeabteilung BZ 2025 80 Präsidialverfügung vom 30. Juli 2025 "
          "[rechtskräftig] in Sachen A.________ AG")
    assert r.heading_date(gr, "KSK 2024 93", "gr_gerichte", TODAY)[0] == "2024-11-15"
    assert r.heading_date(zg, "BZ 2025 80", "zg_obergericht", TODAY)[0] == "2025-07-30"


def test_reviewed_decision_after_the_parties_is_never_taken():
    # No heading date before the parties block; the only date is the
    # lower authority's ruling under review.
    text = ("Verwaltungsgericht des Kantons Bern 200 2025 1 A.________ Beschwerdeführer "
            "gegen IV-Stelle Bern betreffend Verfügung vom 3. September 2025")
    assert r.heading_date(text, "200 2025 1", "be_verwaltungsgericht", TODAY) == (None, "no_heading")


def test_dates_of_other_rulings_named_in_the_head_are_refused():
    # All three were proposed by the first dry run (2026-10-13) and are wrong:
    # the date belongs to a replaced judgment, the Federal Supreme Court's
    # ruling, the prosecutor's order.
    replaced = ("Cour suprême du canton de Berne SK 19 333 Berne, le 30 juin 2020 Ce jugement "
                "remplace partiellement celui rendu le 21 mars 2019 par la 2e Chambre pénale")
    federal = ("Cour suprême SK 19 410 Ce jugement remplace partiellement le jugement "
               "suite à l'arrêt du Tribunal fédéral du 24 septembre 2019 (arrêt 6B_696/2019)")
    order = ("Kantonsgericht SF 06 22 Angeklagter, vertreten durch Rechtsanwalt X, mit Verfügung "
             "der Staatsanwaltschaft Graubünden vom 2. Juni 2006, wegen Widerhandlung")
    assert r.heading_date(replaced, "SK 2019 333", "be_zivilstraf", TODAY)[0] is None
    assert r.heading_date(federal, "SK 2019 410", "be_zivilstraf", TODAY)[0] is None
    assert r.heading_date(order, "SF 2006 22", "gr_gerichte", TODAY)[0] is None


def test_qualified_designations_still_match():
    assert r.heading_date("TA 3.596 Jugement du juge unique du 13 mars 2024 Droit X",
                          "200 2023 596", "be_verwaltungsgericht", TODAY)[0] == "2024-03-13"
    assert r.heading_date("GB 501 2022 190 Arrêt par défaut du 14 novembre 2023 Cour X",
                          "501 2022 190", "fr_gerichte", TODAY)[0] == "2023-11-14"
    assert r.heading_date("U 17 57 3. Kammer Vorsitz X URTEIL 31. Januar 2018 in der Streitsache",
                          "U 2017 57", "gr_gerichte", TODAY)[0] == "2018-01-31"


def test_heading_before_docket_year_is_refused():
    text = "Obergericht 200 2025 5 Entscheid vom 4. Mai 2021 Besetzung X in Sachen A"
    assert r.heading_date(text, "200 2025 5", "be_verwaltungsgericht", TODAY) == (None, "before_docket_year")


def test_heading_in_future_is_refused():
    text = "Kantonsgericht 605 2025 140 Arrêt du 27 juillet 2027 Composition X Parties A"
    assert r.heading_date(text, "605 2025 140", "fr_gerichte", TODAY) == (None, "heading_in_future")


def test_typo_year_is_not_a_date():
    text = "Kantonsgericht 105 2025 117 Urteil vom 16. Dezember 20025 Besetzung X Parteien A"
    assert r.heading_date(text, "105 2025 117", "fr_gerichte", TODAY)[0] is None


def test_fribourg_signature_must_agree():
    agree = FR_HEAD + " … Fribourg, le 1er septembre 2026 La Présidente"
    clash = FR_HEAD + " … Fribourg, le 3 septembre 2026 La Présidente"
    assert r.heading_date(agree, "604 2026 58", "fr_gerichte", TODAY)[0] == "2026-09-01"
    assert r.heading_date(clash, "604 2026 58", "fr_gerichte", TODAY) == (None, "signature_disagrees")


def test_docket_year_forms():
    assert r.docket_year("200 2025 737") == 2025
    assert r.docket_year("BZ_2025_80") == 2025
    assert r.docket_year("BK 25 614") == 2025
    assert r.docket_year("353") is None


# ── decide ──────────────────────────────────────────────────────────────


def test_pre_fix_row_is_corrected():
    row = _row("fr_gerichte", "604 2026 58", "2026-10-26", FR_HEAD)
    assert r.decide(row, TODAY) == ("corrected", "2026-09-01")


def test_pre_fix_null_is_filled():
    row = _row("be_verwaltungsgericht", "200 2025 737", None, BE_HEAD)
    assert r.decide(row, TODAY) == ("filled", "2025-11-11")


def test_post_fix_row_is_never_changed():
    row = _row("be_verwaltungsgericht", "200 2025 737", "2025-10-13", BE_HEAD, scraped=POST)
    assert r.decide(row, TODAY) == ("post_fix_disagree", None)


def test_agreeing_row_is_untouched():
    row = _row("fr_gerichte", "604 2026 58", "2026-09-01", FR_HEAD)
    assert r.decide(row, TODAY) == ("agrees", None)


# ── run ─────────────────────────────────────────────────────────────────


def _write(tmp_path, rows):
    p = tmp_path / "fr_gerichte.jsonl"
    p.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in rows), encoding="utf-8")
    return p


def test_dry_run_writes_nothing(tmp_path):
    p = _write(tmp_path, [_row("fr_gerichte", "604 2026 58", "2026-10-26", FR_HEAD)])
    before = p.read_bytes()
    stats = r.run(p, apply=False, examples=0, today=TODAY)
    assert stats["corrected"] == 1 and stats["rows_changed"] == 1
    assert p.read_bytes() == before
    assert list(tmp_path.iterdir()) == [p]


def test_apply_stamps_and_is_idempotent(tmp_path):
    other = {"decision_id": "x", "court": "zh_obergericht", "decision_date": "2020-01-01"}
    p = _write(tmp_path, [_row("fr_gerichte", "604 2026 58", "2026-10-26", FR_HEAD), other])
    stats = r.run(p, apply=True, examples=0, today=TODAY)
    assert stats["rows_changed"] == 1 and stats["other_court"] == 1
    rows = [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["decision_date"] == "2026-09-01"
    assert rows[0]["date_restore"]["from"] == "2026-10-26"
    assert rows[0]["date_restore"]["method"] == "tribuna_heading"
    assert rows[1] == other
    again = r.run(p, apply=True, examples=0, today=TODAY)
    assert again["rows_changed"] == 0 and again["agrees"] == 1
    assert list(tmp_path.iterdir()) == [p]


def test_apply_keeps_the_shard_file_mode(tmp_path):
    p = _write(tmp_path, [_row("fr_gerichte", "604 2026 58", "2026-10-26", FR_HEAD)])
    p.chmod(0o644)
    r.run(p, apply=True, examples=0, today=TODAY)
    assert (p.stat().st_mode & 0o777) == 0o644
