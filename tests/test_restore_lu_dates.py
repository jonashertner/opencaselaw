"""scripts/restore_lu_dates.py: the Luzern rows get back the portal's
Entscheiddatum that the 2026-03-12 date pass moved into publication_date.
Offline, on throwaway shards."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scripts import restore_lu_dates as lu


def text(seed: str, n: int = 40) -> str:
    """A ruling text unlike any other seed's (the containment test reads
    60-character windows)."""
    return "\n".join(f"{i}. Erwägung {seed} {i}: der Satz {seed * 3} lautet hier {i * 7}."
                     for i in range(n))


def stamp(extracted: str, meta: str, method: str = "header_de") -> dict:
    return {"method": method, "confidence": "high", "raw_match": "Urteil vom ...",
            "category": "MISMATCH_FIXABLE", "extracted_date": extracted,
            "metadata_date": meta, "delta_days": 1}


def direct(enid: int, docket: str, date: str, body: str, *, pub=None, de=None, regeste="") -> dict:
    r = {"decision_id": f"lu_gerichte_{enid}", "court": "lu_gerichte", "canton": "LU",
         "docket_number": docket, "decision_date": date, "publication_date": pub,
         "full_text": body, "regeste": regeste, "language": "de",
         "source_url": f"https://gerichte.lu.ch/recht_sprechung/lgve/Ajax?EnId={enid}",
         "scraped_at": "2026-02-13T19:19:50"}
    if de:
        r["date_extraction"] = de
    return r


def es_copy(docket: str, date: str, header_date: str, body: str, *, regeste="", pub=None, de=None,
            suffix="") -> dict:
    d, m, y = header_date[8:10], header_date[5:7], header_date[:4]
    r = {"decision_id": "lu_gerichte_" + docket.replace(" ", "_") + suffix, "court": "lu_gerichte",
         "canton": "LU", "docket_number": docket, "decision_date": date, "publication_date": pub,
         "source": "entscheidsuche", "regeste": regeste, "language": "de",
         "full_text": f"Luzern Kantonsgericht 4. Abteilung {d}.{m}.{y} {docket} (2025 IV Nr. 1)\n\n"
                      f"{regeste}\n{body}"}
    if de:
        r["date_extraction"] = de
    return r


def write(p: Path, rows) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    return p


def read(p: Path) -> dict:
    return {r["decision_id"]: r for r in map(json.loads, p.read_text(encoding="utf-8").splitlines())}


# 7H 24 52 (EnId 11092): portal 14.01.2025; the 03-12 pass gave it 2024-02-22.
RESTORE = direct(11092, "7H 24 52", "2024-02-22", text("a"), pub="2025-01-14",
                 de=stamp("2024-02-22", "2025-01-14"))
UNSTAMPED = direct(11116, "7H 25 1", "2025-10-01", text("b"))
MOVED_PUB = direct(11000, "7H 22 9", "2019-01-01", text("c"), pub="2023-05-05",
                   de=stamp("2019-01-01", "2022-05-05"))
# lu_gerichte_1286: the pass put 2049-08-31 in, a later guard reverted it.
REVERTED = direct(1286, "OG 1991 4", "1991-06-14", text("d"), pub="1991-06-14",
                  de={**stamp("2049-08-31", "1991-06-14"), "reverted": "future date"})


@pytest.fixture
def shards(tmp_path):
    d = tmp_path / "decisions"
    es_row = es_copy("7H 24 52", "2024-02-22", "2025-01-14", text("a"), regeste="Leitsatz A.",
                     pub="2025-01-14", de=stamp("2024-02-22", "2025-01-14"))
    return (write(d / "lu_gerichte.jsonl", [RESTORE, UNSTAMPED, MOVED_PUB, REVERTED]),
            write(d / "es_lu_gerichte.jsonl", [es_row]))


def test_the_portal_date_comes_back_from_the_stamp(shards):
    direct_p, es_p = shards
    stats = lu.run([direct_p, es_p], apply=True)
    rows = read(direct_p)
    fixed = rows["lu_gerichte_11092"]
    assert fixed["decision_date"] == "2025-01-14"
    assert fixed["publication_date"] is None
    assert fixed["date_restore"]["from"] == "2024-02-22"
    assert fixed["date_restore"]["method"] == lu.METHOD_STAMP
    assert fixed["date_restore"]["publication_date"] == {"from": "2025-01-14", "to": None}
    assert fixed["date_extraction"] == RESTORE["date_extraction"]   # provenance kept
    assert rows["lu_gerichte_11116"] == UNSTAMPED
    assert rows["lu_gerichte_11000"] == MOVED_PUB                    # unprovable: left alone
    assert rows["lu_gerichte_1286"]["decision_date"] == "1991-06-14"
    assert rows["lu_gerichte_1286"]["publication_date"] is None
    es_row = read(es_p)["lu_gerichte_7H_24_52"]
    assert es_row["decision_date"] == "2025-01-14" and es_row["publication_date"] is None
    assert es_row["date_restore"]["method"] == lu.METHOD_ES_HEADER
    assert stats[0]["outcome:restored"] == 1 and stats[0]["outcome:unprovable"] == 1
    assert stats[0]["outcome:agrees_pub_cleared"] == 1 and stats[0]["outcome:unstamped"] == 1
    assert stats[1]["outcome:es_restored"] == 1
    # The pair shared the wrong date and shares the right one: build Pass 1
    # folded it before and still does (the es copy, which contains the direct
    # text, is kept). Nothing newly dropped, so no alias.
    assert stats[0]["restored_onto_copy"] == 1
    assert "previous_decision_id" not in es_row and stats[1]["alias_on_kept_row"] == 0


def test_a_row_the_repair_newly_folds_stays_answerable(tmp_path):
    """The es copy already holds the portal date; restoring the direct row
    makes build Pass 1 drop it for the longer copy. The copy takes the direct
    id as previous_decision_id (decision_id_aliases: the id resolves, and the
    per-court swap gate counts it)."""
    d = tmp_path / "decisions"
    right = es_copy("7H 24 52", "2025-01-14", "2025-01-14", text("a"), regeste="Leitsatz A.")
    p = write(d / "lu_gerichte.jsonl", [RESTORE])
    e = write(d / "es_lu_gerichte.jsonl", [right])
    stats = lu.run([p, e], apply=True)
    kept = read(e)["lu_gerichte_7H_24_52"]
    assert kept["previous_decision_id"] == "lu_gerichte_11092"
    assert kept["previous_id_source"] == lu.ALIAS_SOURCE
    assert kept["decision_date"] == "2025-01-14" and "date_restore" not in kept
    assert "previous_decision_id" not in read(p)["lu_gerichte_11092"]
    assert stats[1]["alias_on_kept_row"] == 1 and stats[1]["rows_changed"] == 1
    again = lu.run([p, e], apply=True)
    assert again[0]["rows_changed"] == 0 and again[1]["rows_changed"] == 0


def test_dry_run_writes_nothing_and_a_second_run_changes_nothing(shards):
    direct_p, es_p = shards
    before = (direct_p.read_bytes(), es_p.read_bytes())
    stats = lu.run([direct_p, es_p], apply=False)
    assert stats[0]["rows_changed"] == 2 and stats[1]["rows_changed"] == 1
    assert (direct_p.read_bytes(), es_p.read_bytes()) == before
    lu.run([direct_p, es_p], apply=True)
    after = (direct_p.read_bytes(), es_p.read_bytes())
    again = lu.run([direct_p, es_p], apply=True)
    assert again[0]["rows_changed"] == 0 and again[1]["rows_changed"] == 0
    assert again[0]["outcome:already_restored"] == 2
    assert (direct_p.read_bytes(), es_p.read_bytes()) == after


def test_backup_is_a_hard_link_not_named_jsonl_and_mode_is_kept(shards):
    direct_p, es_p = shards
    os.chmod(direct_p, 0o640)
    ino = os.stat(direct_p).st_ino
    lu.run([direct_p, es_p], apply=True)
    baks = sorted(direct_p.parent.glob("lu_gerichte.jsonl.bak-*"))
    assert len(baks) == 1 and os.stat(baks[0]).st_ino == ino
    assert sorted(p.name for p in direct_p.parent.glob("*.jsonl")) == [
        "es_lu_gerichte.jsonl", "lu_gerichte.jsonl"]
    assert not list(direct_p.parent.glob("*.tmp"))
    assert os.stat(direct_p).st_mode & 0o777 == 0o640
    assert json.loads(baks[0].read_text().splitlines()[0])["decision_date"] == "2024-02-22"
    # a second apply the same day must not overwrite that backup
    write(direct_p, [RESTORE])
    with pytest.raises(SystemExit, match="existing backup"):
        lu.run([direct_p, es_p], apply=True)


def test_two_lgve_entries_of_one_ruling_are_not_folded(tmp_path):
    """EnIds 10968 and 10969 are two Leitsätze of 7Q 20 4 (3 June 2022): 10968
    already holds the date, 10969 has the text date 2007-03-06. Moving 10969
    to 2022-06-03 would let build Pass 1 delete one of two distinct texts."""
    d = tmp_path / "decisions"
    first = direct(10968, "7Q 20 4", "2022-06-03", text("first", 60))
    second = direct(10969, "7Q 20 4", "2007-03-06", text("second"), pub="2022-06-03",
                    de=stamp("2007-03-06", "2022-06-03"))
    # 3B 17 46: two stamped entries compete for one free key; the lower EnId
    # takes it, the other is held.
    third = direct(10691, "3B 17 46", "2017-01-01", text("third"), pub="2018-06-28",
                   de=stamp("2017-01-01", "2018-06-28"))
    fourth = direct(10692, "3B 17 46", "2016-01-01", text("fourth"), pub="2018-06-28",
                    de=stamp("2016-01-01", "2018-06-28"))
    p = write(d / "lu_gerichte.jsonl", [first, second, third, fourth])
    stats, held, losses, _alias = lu.plan([p])
    assert held == {(0, "lu_gerichte_10969"), (0, "lu_gerichte_10692")} and losses == []
    assert stats[0]["outcome:held_dedup_collision"] == 2 and stats[0]["outcome:restored"] == 1
    lu.run([p], apply=True)
    rows = read(p)
    assert rows["lu_gerichte_10969"] == second and rows["lu_gerichte_10692"] == fourth
    assert rows["lu_gerichte_10691"]["decision_date"] == "2018-06-28"


def test_an_es_copy_of_another_entry_is_not_folded_into_a_restored_row(tmp_path):
    """entscheidsuche holds one LGVE entry of 3B 17 46 (the second one's text);
    restoring the direct row of the first entry onto the es copy's key would
    make Pass 1 drop the shorter of two different texts."""
    d = tmp_path / "decisions"
    entry1 = direct(10691, "3B 17 46", "2017-01-01", text("one", 60), pub="2018-06-28",
                    de=stamp("2017-01-01", "2018-06-28"))
    es_other = es_copy("3B 17 46", "2018-06-28", "2018-06-28", text("two"))
    p, e = write(d / "lu_gerichte.jsonl", [entry1]), write(d / "es_lu_gerichte.jsonl", [es_other])
    _stats, held, losses, _alias = lu.plan([p, e])
    assert held == {(0, "lu_gerichte_10691")} and losses == []


def test_es_rows_need_the_header_to_agree(tmp_path):
    d = tmp_path / "decisions"
    ok = es_copy("V 13 31", "2013-01-01", "2013-07-19", text("v"), pub="2013-07-19",
                 de=stamp("2013-01-01", "2013-07-19"))
    other_header = es_copy("V 13 32", "2013-01-01", "2013-07-20", text("w"), pub="2013-07-19",
                           de=stamp("2013-01-01", "2013-07-19"))
    unstamped = es_copy("V 13 33", "2013-07-21", "2013-07-21", text("x"))
    e = write(d / "es_lu_gerichte.jsonl", [ok, other_header, unstamped])
    stats = lu.run([e], apply=True)
    rows = read(e)
    assert rows["lu_gerichte_V_13_31"]["decision_date"] == "2013-07-19"
    assert rows["lu_gerichte_V_13_32"] == other_header
    assert rows["lu_gerichte_V_13_33"] == unstamped
    assert stats[0]["outcome:es_unprovable"] == 1 and stats[0]["outcome:es_unstamped"] == 1


def test_a_reread_portal_value_wins_and_a_moved_enid_is_left_alone(tmp_path):
    d = tmp_path / "decisions"
    p = write(d / "lu_gerichte.jsonl", [RESTORE, MOVED_PUB, UNSTAMPED])
    tsv = d.parent / "portal.tsv"
    tsv.write_text("# EnId\tstatus\tEntscheiddatum\tdocket\n"
                   "11092\tok\t2025-01-15\t7H 24 52\n"       # differs from the stamp
                   "11000\tok\t2022-05-05\t7H 22 99\n"       # EnId now shows another case
                   "11116\tok\t2025-10-02\t7H 25 1\n", encoding="utf-8")
    portal = lu.load_portal(tsv)
    stats = lu.run([p], apply=True, portal=portal)
    rows = read(p)
    assert rows["lu_gerichte_11092"]["decision_date"] == "2025-01-15"
    assert rows["lu_gerichte_11092"]["date_restore"]["method"] == lu.METHOD_PORTAL
    assert rows["lu_gerichte_11000"] == MOVED_PUB
    assert rows["lu_gerichte_11116"] == UNSTAMPED          # counted, never changed
    assert stats[0]["outcome:portal_docket_mismatch"] == 1
    assert stats[0]["outcome:unstamped_portal_differs"] == 1


PAGE = """<div id="JurisdictionPrintArea"><table class="headerleft">
<tr><th>Instanz:</th><td>Kantonsgericht</td></tr>
<tr><th>Entscheiddatum:</th><td>14.01.2025</td></tr>
<tr><th>Fallnummer:</th><td>7H 24 52</td></tr>
<tr><th>LGVE:</th><td></td></tr>
<tr><th>Entscheid:</th><td>Text</td></tr></table></div>""" + " " * 2500


class FakeResponse:
    def __init__(self, body: str, status: int = 200):
        self.text, self.content, self.status_code = body, body.encode(), status


class FakeSession:
    def __init__(self, pages: dict):
        self.pages, self.calls = pages, []

    def get(self, url, params=None, timeout=None):
        self.calls.append(params["EnId"])
        return self.pages[params["EnId"]]


def test_fetch_portal_reads_the_header_table_and_resumes(tmp_path):
    d = tmp_path / "decisions"
    gone = direct(11093, "7H 24 53", "2020-01-01", text("g"), pub="2025-02-02",
                  de=stamp("2020-01-01", "2025-02-02"))
    p = write(d / "lu_gerichte.jsonl", [RESTORE, gone, UNSTAMPED])
    out = tmp_path / "portal.tsv"
    session = FakeSession({11092: FakeResponse(PAGE), 11093: FakeResponse("x" * lu.INVALID_SIZE)})
    stats = lu.fetch_portal(p, out, delay=0, session=session)
    assert session.calls == [11092, 11093]                 # stamped rows only
    assert stats["status:ok"] == 1 and stats["status:invalid"] == 1
    portal = lu.load_portal(out)
    assert portal[11092] == {"status": "ok", "date": "2025-01-14", "docket": "7H 24 52"}
    assert portal[11093]["status"] == "invalid"
    again = lu.fetch_portal(p, out, delay=0, session=session)
    assert again["already"] == 2 and session.calls == [11092, 11093]
    with pytest.raises(SystemExit):
        lu.fetch_portal(p, tmp_path / "portal.jsonl", delay=0, session=session)


def test_the_03_12_date_pass_refuses_lu():
    from scripts import repair_decision_dates as rdd
    assert {"lu_gerichte", "es_lu_gerichte"} <= rdd.NEVER_REPAIR_SHARDS
