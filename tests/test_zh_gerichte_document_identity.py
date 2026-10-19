"""Document-aware identity of the gerichte-zh.ch scraper.

One docket can hold several documents: rulings of different days, and the
same ruling twice (extract and full text). Until 2026-10-02 everything after
the first document of a docket was skipped.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scrapers.cantonal.zh_gerichte import ZHGerichteScraper  # noqa: E402

BASE = "zh_obergericht_PS150113"
PDF = "https://www.gerichte-zh.ch/fileadmin/x/"


def make(tmp_path, held=(), sidecar=None):
    (tmp_path / "zh_gerichte.jsonl").write_text("".join(i + "\n" for i in held), encoding="utf-8")
    if sidecar is not None:
        (tmp_path / "zh_gerichte.docids.txt").write_text(
            "".join("\t".join(x) + "\n" for x in sidecar), encoding="utf-8")
    sc = ZHGerichteScraper(state_dir=tmp_path)
    sc._load_docids()
    return sc


def stub(doc, day, pdf="PS150113-O3.pdf", did=BASE):
    return {"decision_id": did, "doc_id": str(doc), "docket_number": "PS150113",
            "decision_date": date.fromisoformat(day), "pdf_url": PDF + pdf}


SEED = [("100", BASE, "2015-04-10", PDF + "PS150113.pdf")]


def test_without_a_sidecar_the_old_behaviour(tmp_path):
    sc = make(tmp_path, held=[BASE])
    assert sc._identity(stub(200, "2015-08-18")) is None
    assert sc._identity(stub(201, "2015-08-18", did="zh_obergericht_NEW1")) == "zh_obergericht_NEW1"
    sc._mark_docid("201", "zh_obergericht_NEW1", "2015-08-18", "x")
    assert not (tmp_path / "zh_gerichte.docids.txt").exists()


def test_known_document_is_skipped(tmp_path):
    sc = make(tmp_path, held=[BASE], sidecar=SEED)
    assert sc._identity(stub(100, "2015-04-10", "PS150113.pdf")) is None


def test_ruling_of_another_day_gets_a_dated_id(tmp_path):
    sc = make(tmp_path, held=[BASE], sidecar=SEED)
    assert sc._identity(stub(200, "2015-08-18")) == BASE + "_d20150818"
    # a second listing entry of that same new ruling in the same run
    assert sc._identity(stub(201, "2015-08-18", "other.pdf")) is None


def test_same_day_document_is_another_rendering_and_is_recorded(tmp_path):
    sc = make(tmp_path, held=[BASE], sidecar=SEED)
    assert sc._identity(stub(200, "2015-04-10", "PS150113-O1.pdf")) is None
    line = (tmp_path / "zh_gerichte.docids.txt").read_text(encoding="utf-8").splitlines()[-1]
    assert line.split("\t")[:2] == ["200", "-"]
    assert sc._identity(stub(200, "2015-04-10", "PS150113-O1.pdf")) is None


def test_same_file_listed_again_is_not_fetched(tmp_path):
    sc = make(tmp_path, held=[BASE], sidecar=SEED)
    assert sc._identity(stub(300, "2016-01-01", "PS150113.pdf")) is None


def test_new_docket_keeps_its_plain_id(tmp_path):
    sc = make(tmp_path, held=[BASE], sidecar=SEED)
    assert sc._identity(stub(400, "2026-09-01", did="zh_obergericht_LB260001")) == "zh_obergericht_LB260001"
    # two documents of a new docket in one run do not share the id
    assert sc._identity(stub(401, "2026-09-01", did="zh_obergericht_LB260001")) is None


def test_crash_window_pair_is_retried(tmp_path):
    """Sidecar line written, row never persisted: the id is not in state."""
    sidecar = [*SEED, ("200", BASE + "_d20150818", "2015-08-18", PDF + "PS150113-O3.pdf")]
    sc = make(tmp_path, held=[BASE], sidecar=sidecar)
    assert sc._identity(stub(200, "2015-08-18")) == BASE + "_d20150818"


def test_dated_ruling_already_held_is_not_fetched_again(tmp_path):
    sidecar = [*SEED, ("200", BASE + "_d20150818", "2015-08-18", PDF + "PS150113-O3.pdf")]
    sc = make(tmp_path, held=[BASE, BASE + "_d20150818"], sidecar=sidecar)
    assert sc._identity(stub(200, "2015-08-18")) is None
    # a further rendering of the dated ruling
    assert sc._identity(stub(201, "2015-08-18", "PS150113-O4.pdf")) is None


def test_half_seeded_sidecar_falls_back_to_legacy(tmp_path):
    held = [f"zh_obergericht_X{i}" for i in range(300)] + [BASE]
    sc = make(tmp_path, held=held, sidecar=SEED)
    assert sc._docids is None
    assert sc._identity(stub(200, "2015-08-18")) is None


def test_renumbering_cannot_trigger_a_refetch_storm(tmp_path):
    held = [f"zh_obergericht_X{i}" for i in range(80)]
    sidecar = [(str(i), h, "2015-01-01", PDF + f"{i}.pdf") for i, h in enumerate(held)]
    sc = make(tmp_path, held=held, sidecar=sidecar)
    got = [sc._identity(stub(1000 + i, "2016-02-02", f"n{i}.pdf", did=h)) for i, h in enumerate(held)]
    assert sum(1 for g in got if g) == sc.MAX_NEW_COLLISIONS
