"""scripts/restore_ge_dates.py: the Geneva rows get back the ruling's own date
that the 2026-03-12 date pass moved into publication_date. Offline, on
throwaway shards; header shapes are the court's own (Mnemosyne reports of
2026-11-09 and 2026-11-25, and the rows read while counting)."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scripts import restore_ge_dates as ge


def body(seed: str, n: int = 40) -> str:
    """Reasoning unlike any other seed's (the containment test reads
    60-character windows)."""
    return "\n".join(f"{i}. Considérant {seed} {i} : la phrase {seed * 3} se lit ici {i * 7}."
                     for i in range(n))


# ACJC/1085/2024: the notification line stands above the header.
ACJC_HEAD = ("Le présent arrêt est communiqué aux parties par plis recommandés du 11 septembre 2024.\n\n"
             "REPUBLIQUE ET\nCANTON DE GENEVE\nPOUVOIR JUDICIAIRE\nC/7337/2018\nACJC/1085/2024\n"
             "ARRÊT\nDE LA COUR DE JUSTICE\nChambre civile\nDU MARDI 3 SEPTEMBRE 2024\n"
             "Entre\n1) Monsieur A______, domicilié ______\n")
# DAS/192/2020: the filing of the appeal follows the header.
DAS_HEAD = ("REPUBLIQUE ET\nCANTON DE GENEVE\nPOUVOIR JUDICIAIRE\nC/15650/2020-CS\nDAS/192/2020\n"
            "DECISION\nDE LA COUR DE JUSTICE\nChambre de surveillance\ndu Registre foncier\n"
            "DU MERCREDI 18 NOVEMBRE 2020\nRecours (C/15650/2020-CS) formé en date du 12 août 2020 "
            "par Maître A______\n")
# Chambre civile "Sommaires": no parties line; the judgment under appeal follows.
SOMMAIRE_HEAD = ("Cour de justice civile Chambre civile Recourant : Intimée : C/23433/2020 "
                 "POUVOIR JUDICIAIRE ACJC/146/2021 DU MERCREDI 3 FÉVRIER 2021 Vu le jugement "
                 "JTPI/431/2021 du 15 janvier 2021 prononçant la faillite\n")
# An entscheidsuche copy: the portal preamble ("En fait En droit") stands above.
ES_HEAD = ("Genève Cour de Justice (Cour civile) Assistance Juridique 04.09.2015 AC/1112/2015 "
           "AC/1112/2015 DAAJ/52/2015 du 04.09.2015 sur AJC/2062/2015 ( AJC ) , REJETE "
           "Descripteurs : CHANCES DE SUCCÈS En fait En droit RÉPUBLIQUE ET CANTON DE GENÈVE "
           "POUVOIR JUDICIAIRE AC/1112/2015 DAAJ/52/2015 COUR DE JUSTICE Assistance judiciaire "
           "DéCISION DU VENDREDI 4 SEPTEMBRE 2015 Statuant sur le recours déposé par : Monsieur "
           "A______ contre la décision du 5 mai 2015\n")


@pytest.mark.parametrize("head, want", [
    (ACJC_HEAD, "2024-09-03"),
    (DAS_HEAD, "2020-11-18"),
    (SOMMAIRE_HEAD, "2021-02-03"),
    (ES_HEAD, "2015-09-04"),
    ("POUVOIR JUDICIAIRE A/242/2005 ATAS/210/2006 ARRET DU TRIBUNAL CANTONAL DES ASSURANCES "
     "SOCIALES Chambre 5 du 1 er mars 2006 En la cause Madame M______", "2006-03-01"),
    ("POUVOIR JUDICIAIRE P/591/2026 ACPR/368/2026 COUR DE JUSTICE Chambre pénale de recours "
     "Arrêt du mercredi 15 avril 2026 Entre A______, recourante, contre l'ordonnance pénale "
     "rendue le 9 janvier 2026", "2026-04-15"),
    ("POUVOIR JUDICIAIRE A/1158/2023 DCSO/25/2024 Chambre de surveillance DU JEUDI 1ER FÉVRIER "
     "2024 Plainte 17 LP formée en date du 5 janvier 2024", "2024-02-01"),
    # Old DCSO: the head is a statute citation, not the ruling's date.
    ("POUVOIR JUDICIAIRE DCSO/419/10 Le recours en matière civile au sens de l'art. 72 al. 2 "
     "let. a de la loi sur le Tribunal fédéral du 17 juin 2005 (LTF) est ouvert", None),
    # A head that names no date of its own: the judgment under appeal is not it.
    ("POUVOIR JUDICIAIRE C/1931/2025 ACJC/404/2025 Vu le jugement JTPI/3688/2025 du "
     "13 mars 2025 prononçant la faillite", None),
    # No head at all (a metadata-only row).
    ("Descripteurs : PROCÉDURE ; Normes : CPC.311", None),
])
def test_the_header_date_is_the_rulings_own(head, want):
    assert ge.ge_header_date(head + body("h")) == want


def test_ruling_year_reads_the_ruling_number():
    assert ge.ruling_year(ACJC_HEAD) == 2024
    assert ge.ruling_year("POUVOIR JUDICIAIRE A/3680/2017-CS DCSO/177/18 DECISION") == 2018
    assert ge.ruling_year("Descripteurs : rien") is None
    # PM/1259/2023 is the case number of ACPR/263/2024, not a ruling number.
    assert ge.ruling_year("POUVOIR JUDICIAIRE PM/1259/2023 ACPR/ 263/2024 COUR") == 2024
    assert ge.id_year({"decision_id": "ge_gerichte_ACJC_1652_2016"}) == 2016
    assert ge.id_year({"decision_id": "ge_gerichte_C_7337_2018"}) is None


def test_a_typo_in_the_texts_ruling_number_does_not_hold_the_row():
    # ACJC_1652_2016 names itself "ACJC/1652/2017"; header 16 December 2016.
    head = ACJC_HEAD.replace("ACJC/1085/2024", "ACJC/1652/2017").replace(
        "DU MARDI 3 SEPTEMBRE 2024", "DU VENDREDI 16 DECEMBRE 2016")
    r = row("ge_gerichte_ACJC_1652_2016", "ACJC/1652/2016", "2017-01-04", head, "e",
            pub="2016-12-16", de=stamp("2017-01-04", "2016-12-16"))
    assert ge.decide(r)[:2] == ("restored", "2016-12-16")


def stamp(extracted: str, meta: str) -> dict:
    return {"method": "header_fr", "confidence": "high", "raw_match": "du ...",
            "category": "MISMATCH_FIXABLE", "extracted_date": extracted,
            "metadata_date": meta, "delta_days": 8}


def row(did: str, docket: str, dd: str, head: str, seed: str, *, pub=None, de=None,
        source=None, regeste="") -> dict:
    r = {"decision_id": did, "court": "ge_gerichte", "canton": "GE", "docket_number": docket,
         "decision_date": dd, "publication_date": pub, "full_text": head + body(seed),
         "regeste": regeste, "language": "fr",
         "source_url": "https://justice.ge.ch/apps/decis/fr/acjc/show/3356918?doc=",
         "scraped_at": "2026-02-16T06:36:29+00:00"}
    if source:
        r["source"] = source
    if de:
        r["date_extraction"] = de
    return r


def write(p: Path, rows) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    return p


def read(p: Path) -> dict:
    return {r["decision_id"]: r for r in map(json.loads, p.read_text(encoding="utf-8").splitlines())}


RESTORE = row("ge_gerichte_ACJC_1085_2024", "ACJC/1085/2024", "2024-09-11", ACJC_HEAD, "a",
              pub="2024-09-03", de=stamp("2024-09-11", "2024-09-03"))
DAS = row("ge_gerichte_DAS_192_2020", "DAS/192/2020", "2020-08-12", DAS_HEAD, "b",
          pub="2020-11-18", de=stamp("2020-08-12", "2020-11-18"))
# Scraped after 03-12: the listing date, no stamp, nothing to do.
FRESH = row("ge_gerichte_ACJC_264_2026", "ACJC/264/2026", "2026-02-09",
            ACJC_HEAD.replace("ACJC/1085/2024", "ACJC/264/2026")
            .replace("3 SEPTEMBRE 2024", "9 FEVRIER 2026"), "c")


def test_decide_restores_only_when_header_stamp_and_publication_date_agree():
    assert ge.decide(RESTORE) == ("restored", "2024-09-03", True, ge.METHOD)
    assert ge.decide(DAS)[:2] == ("restored", "2020-11-18")
    assert ge.decide(FRESH)[0] == "unstamped"
    # publication_date moved on since the pass: not provable.
    assert ge.decide({**RESTORE, "publication_date": "2024-09-04"})[0] == "pub_ne_stamp"
    # The stamp replaced another value than the header's.
    assert ge.decide({**RESTORE, "date_extraction": stamp("2024-09-11", "2024-09-05")})[0] \
        == "header_ne_stamp"
    # Already right: left alone (publication_date untouched).
    assert ge.decide({**RESTORE, "decision_date": "2024-09-03"})[0] == "agrees"
    assert ge.decide({**RESTORE, "date_extraction": {**stamp("x", "2024-09-03"),
                                                    "reverted": "future"}})[0] == "stamp_reverted"
    assert ge.decide({**RESTORE, "court": "vd_gerichte"})[0] == "other"


def test_a_header_year_the_ruling_number_contradicts_is_held():
    # ACJC/656/2014 prints "DU VENDREDI 30 MAI 2013": a typo in the year.
    head = ACJC_HEAD.replace("ACJC/1085/2024", "ACJC/656/2014").replace(
        "DU MARDI 3 SEPTEMBRE 2024", "DU VENDREDI 30 MAI 2013")
    r = row("ge_gerichte_ACJC_656_2014", "ACJC/656/2014", "2014-07-18", head, "d",
            pub="2013-05-30", de=stamp("2014-07-18", "2013-05-30"))
    assert ge.decide(r)[0] == "year_mismatch"


@pytest.fixture
def shards(tmp_path):
    d = tmp_path / "decisions"
    es_row = row("ge_gerichte_C_7337_2018", "C/7337/2018", "2024-09-11",
                 "Genève Cour de Justice (Cour civile) Chambre civile 03.09.2024 C/7337/2018 "
                 "ACJC/1085/2024 du 03.09.2024 En fait En droit " + ACJC_HEAD, "a",
                 pub="2024-09-03", de=stamp("2024-09-11", "2024-09-03"), source="entscheidsuche")
    return (write(d / "ge_gerichte.jsonl", [RESTORE, DAS, FRESH]),
            write(d / "es_ge_gerichte.jsonl", [es_row]))


def test_apply_restores_both_shards_and_changes_nothing_else(shards):
    direct, es = shards
    before = {**read(direct), **read(es)}
    stats = ge.run([direct, es], apply=True)
    assert stats[0]["outcome:restored"] == 2 and stats[0]["outcome:unstamped"] == 1
    assert stats[1]["outcome:restored"] == 1
    after = {**read(direct), **read(es)}
    for did in ("ge_gerichte_ACJC_1085_2024", "ge_gerichte_C_7337_2018"):
        r = after[did]
        assert r["decision_date"] == "2024-09-03" and r["publication_date"] is None
        assert r["date_restore"] == {
            "from": "2024-09-11", "to": "2024-09-03", "method": ge.METHOD,
            "at": r["date_restore"]["at"],
            "publication_date": {"from": "2024-09-03", "to": None}}
        assert r["date_extraction"] == before[did]["date_extraction"]
        changed = {k for k in set(r) | set(before[did]) if r.get(k) != before[did].get(k)}
        assert changed == {"decision_date", "publication_date", "date_restore"}
    assert after["ge_gerichte_DAS_192_2020"]["decision_date"] == "2020-11-18"
    assert after["ge_gerichte_ACJC_264_2026"] == before["ge_gerichte_ACJC_264_2026"]


def test_dry_run_writes_nothing_and_a_second_run_changes_nothing(shards):
    direct, es = shards
    raw = direct.read_bytes(), es.read_bytes()
    stats = ge.run([direct, es], apply=False)
    assert stats[0]["rows_changed"] == 2
    assert (direct.read_bytes(), es.read_bytes()) == raw
    assert not list(direct.parent.glob("*.bak-*"))
    ge.run([direct, es], apply=True)
    again = ge.run([direct, es], apply=False)
    assert again[0]["rows_changed"] == again[1]["rows_changed"] == 0
    assert again[0]["outcome:already_restored"] == 2


def test_backup_is_a_hard_link_not_named_jsonl_and_mode_is_kept(shards):
    direct, es = shards
    os.chmod(direct, 0o640)
    ino = os.stat(direct).st_ino
    ge.run([direct, es], apply=True)
    backups = sorted(direct.parent.glob("ge_gerichte.jsonl.bak-*"))
    assert len(backups) == 1 and not backups[0].name.endswith(".jsonl")
    assert os.stat(backups[0]).st_ino == ino
    assert os.stat(direct).st_mode & 0o777 == 0o640
    assert not list(direct.parent.glob("*.tmp"))


def test_two_rulings_of_one_case_are_not_folded_onto_one_day(tmp_path):
    """One case number carries several rulings. An es copy restored onto the
    day another copy of the case already holds, with other text, would make
    build Pass 1 delete one of them: held."""
    head2 = ACJC_HEAD.replace("ACJC/1085/2024", "ACJC/1090/2024")
    other = row("ge_gerichte_C_7337_2018_b", "C/7337/2018", "2024-09-03", head2, "zz",
                source="entscheidsuche")
    moving = row("ge_gerichte_C_7337_2018", "C/7337/2018", "2024-09-11", ACJC_HEAD, "a",
                 pub="2024-09-03", de=stamp("2024-09-11", "2024-09-03"), source="entscheidsuche")
    es = write(tmp_path / "es_ge_gerichte.jsonl", [other, moving])
    stats = ge.run([es], apply=True)
    assert stats[0]["outcome:held_dedup_collision"] == 1
    assert read(es)["ge_gerichte_C_7337_2018"]["decision_date"] == "2024-09-11"


def test_a_copy_of_the_same_ruling_folds_and_stays_answerable(tmp_path):
    """Two rows of one ruling under one docket (the longer keeps its id): the
    restored date lets Pass 1 fold them, and the kept row takes the dropped id
    as previous_decision_id."""
    longer = row("ge_gerichte_C_7337_2018", "C/7337/2018", "2024-09-03", ACJC_HEAD, "a",
                 source="entscheidsuche", regeste="Résumé du considérant.")
    short = row("ge_gerichte_C_7337_2018_x", "C/7337/2018", "2024-09-11", ACJC_HEAD, "a",
                pub="2024-09-03", de=stamp("2024-09-11", "2024-09-03"), source="entscheidsuche")
    es = write(tmp_path / "es_ge_gerichte.jsonl", [longer, short])
    stats = ge.run([es], apply=True)
    assert stats[0]["outcome:restored"] == 1 and stats[0]["alias_recorded_total"] == 1
    kept = read(es)["ge_gerichte_C_7337_2018"]
    assert kept["previous_decision_id"] == "ge_gerichte_C_7337_2018_x"
    assert kept["previous_id_source"] == ge.ALIAS_SOURCE


def test_a_repeated_id_changes_on_every_line_or_not_at_all(tmp_path):
    """ge_gerichte.jsonl repeats ids. The build inserts the first line and
    serves a repeat with another date as <id>_dYYYYMMDD, so both lines must
    move together; lines that would diverge stop the run."""
    p = write(tmp_path / "ge_gerichte.jsonl", [RESTORE, DAS, RESTORE])
    stats = ge.run([p], apply=True)
    assert stats[0]["repeated_id_lines"] == 1 and stats[0]["rows_changed"] == 3
    lines = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()]
    assert [x["decision_date"] for x in lines] == ["2024-09-03", "2020-11-18", "2024-09-03"]

    q = write(tmp_path / "x" / "ge_gerichte.jsonl",
              [RESTORE, {**RESTORE, "publication_date": "2024-09-04"}])
    raw = q.read_bytes()
    with pytest.raises(SystemExit, match="differ"):
        ge.run([q], apply=True)
    assert q.read_bytes() == raw


def test_the_03_12_date_pass_refuses_ge():
    from scripts import repair_decision_dates as rdd
    assert {"ge_gerichte", "es_ge_gerichte"} <= rdd.NEVER_REPAIR_SHARDS
