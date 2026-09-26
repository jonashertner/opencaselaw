"""Document-aware identity for the Tribuna (SZ-lineage) scrapers — offline.

JU, 2026-09-26: all 1,192 portal rows classified; 12 real rulings were hidden
behind dockets already held (docket-keyed decision_id), three of them the main
judgment of the dossier. The fix is seeded only where a portal was classified
document by document; every other court keeps the old behaviour.
"""
from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from base_scraper import ScraperState  # noqa: E402
from models import make_decision_id  # noqa: E402
from scrapers.cantonal import sz_gerichte as sz  # noqa: E402
from scrapers.cantonal.ju_gerichte import JUGerichteScraper  # noqa: E402
from scrapers.cantonal.sz_gerichte import SZGerichteScraper, header_date  # noqa: E402

DOC_A = "a" * 32   # held document of JU "ADM 2022 94"
DOC_B = "b" * 32   # effet-suspensif decision under the same docket, never fetched
DOC_C = "c" * 32   # first document of a docket we do not hold


def jid(docket):
    return make_decision_id("ju_gerichte", docket)


def make(tmp_path, cls=JUGerichteScraper, held=(), seed_lines=None, sidecar_lines=None):
    sc = cls.__new__(cls)   # no network init
    state_file = tmp_path / f"{cls.COURT_CODE_STR}.jsonl"
    state_file.write_text("".join(f"{d}\n" for d in held))
    sc.state = ScraperState(state_file)
    if seed_lines is not None:
        seed = tmp_path / "seed.tsv"
        seed.write_text("# comment line\n" + "".join(f"{l}\n" for l in seed_lines))
        sc.DOCID_SEED = seed
    else:
        sc.DOCID_SEED = None
    if sidecar_lines is not None:
        (tmp_path / f"{cls.COURT_CODE_STR}.docids.txt").write_text(
            "".join(f"{l}\n" for l in sidecar_lines))
    sc._load_docids()
    return sc


def stub(docket, doc, d="2023-02-03"):
    return {"doc_id": doc, "docket_number": docket, "decision_date": d,
            "title": docket, "enc_path": "e" * 64}


# ── legacy mode ───────────────────────────────────────────────────────────

def test_legacy_without_seed_or_sidecar_is_the_old_behaviour(tmp_path):
    sc = make(tmp_path, SZGerichteScraper, held=[make_decision_id("sz_gerichte", "BEK 2020 1")])
    assert sc._docids is None and sc._full_walk is False
    assert sc._identity({"docket_number": "BEK 2020 1", "doc_id": DOC_B}) is None
    assert sc._identity({"docket_number": "BEK 2021 2", "doc_id": DOC_C}) == \
        make_decision_id("sz_gerichte", "BEK 2021 2")
    assert sc.CACHE_NONE_AS_GAP is False


def test_legacy_never_writes_a_sidecar(tmp_path):
    sc = make(tmp_path, SZGerichteScraper)
    sc._mark_docid(DOC_C, "sz_gerichte_X 2020 1")
    assert not (tmp_path / "sz_gerichte.docids.txt").exists()


def test_only_ju_ships_a_seed():
    assert JUGerichteScraper.DOCID_SEED is not None and JUGerichteScraper.DOCID_SEED.exists()
    assert SZGerichteScraper.DOCID_SEED is None
    from scrapers.cantonal.sz_verwaltungsgericht import SZVerwaltungsgerichtScraper
    assert SZVerwaltungsgerichtScraper.DOCID_SEED is None


# ── seeded / doc-id mode ──────────────────────────────────────────────────

def test_seed_creates_the_sidecar_and_arms_one_full_walk(tmp_path):
    sc = make(tmp_path, held=[jid("ADM 2022 94")], seed_lines=[f"{DOC_A}\t{jid('ADM 2022 94')}"])
    assert (tmp_path / "ju_gerichte.docids.txt").exists()
    assert sc._docids == {DOC_A} and sc._full_walk is True
    assert sc.CACHE_NONE_AS_GAP is True
    # the walk stays pending until a run has covered every row
    sc2 = make(tmp_path, held=[jid("ADM 2022 94")], seed_lines=[f"{DOC_A}\t{jid('ADM 2022 94')}"])
    assert sc2._full_walk is True and sc2._docids == {DOC_A}


def test_held_document_is_skipped_and_a_hidden_one_gets_its_own_id(tmp_path):
    sc = make(tmp_path, held=[jid("ADM 2022 94")], sidecar_lines=[f"{DOC_A}\t{jid('ADM 2022 94')}"])
    assert sc._identity(stub("ADM 2022 94", DOC_A)) is None
    alt = sc._identity(stub("ADM 2022 94", DOC_B))
    assert alt == jid(f"ADM 2022 94-T{DOC_B[:8]}")
    assert sc._identity(stub("ADM 2022 94", DOC_B)) is None   # claimed this run
    assert sc._identity(stub("ADM 2023 5", DOC_C)) == jid("ADM 2023 5")


def test_two_documents_of_a_new_docket_in_one_run_do_not_share_an_id(tmp_path):
    sc = make(tmp_path, held=[jid("X 2020 1")], sidecar_lines=[f"{DOC_A}\t{jid('X 2020 1')}"])
    first = sc._identity(stub("ADM 2025 9", DOC_B))
    second = sc._identity(stub("ADM 2025 9", DOC_C))
    assert first == jid("ADM 2025 9")
    assert second == jid(f"ADM 2025 9-T{DOC_C[:8]}")


def test_crash_window_pair_is_dropped_and_the_document_retried(tmp_path):
    alt = jid(f"ADM 2022 94-T{DOC_B[:8]}")
    sc = make(tmp_path, held=[jid("ADM 2022 94")],
              sidecar_lines=[f"{DOC_A}\t{jid('ADM 2022 94')}", f"{DOC_B}\t{alt}"])   # alt NOT in state
    assert DOC_B not in sc._docids
    assert sc._identity(stub("ADM 2022 94", DOC_B)) == alt


def test_half_seeded_sidecar_falls_back_to_legacy(tmp_path):
    held = [jid(f"CIV 2020 {i}") for i in range(300)]
    sc = make(tmp_path, held=held, sidecar_lines=[f"{'d' * 32}\t{held[0]}"])
    assert sc._docids is None


def test_a_renumbering_portal_cannot_trigger_a_refetch_storm(tmp_path):
    held = [jid(f"CIV 2020 {i}") for i in range(80)]
    sc = make(tmp_path, held=held, sidecar_lines=[f"{i:032x}\t{d}" for i, d in enumerate(held)])
    got = [sc._identity(stub(f"CIV 2020 {i}", f"{i + 1000:032x}")) for i in range(80)]
    assert sum(g is not None for g in got) == sc.MAX_NEW_COLLISIONS


def test_mark_docid_records_the_pair_once(tmp_path):
    sc = make(tmp_path, held=[jid("ADM 2022 94")], sidecar_lines=[f"{DOC_A}\t{jid('ADM 2022 94')}"])
    alt = jid(f"ADM 2022 94-T{DOC_B[:8]}")
    sc._mark_docid(DOC_B, alt)
    sc._mark_docid(DOC_B, alt)
    lines = (tmp_path / "ju_gerichte.docids.txt").read_text().splitlines()
    assert lines.count(f"{DOC_B}\t{alt}") == 1


# ── discovery ─────────────────────────────────────────────────────────────

class _Resp:
    def __init__(self, text):
        self.text = text


def _discover(sc, rows, total=None):
    total = total or len(rows)
    sc.post = lambda *a, **k: _Resp(f"//OK[{total},")
    it = iter(rows)
    sc._parse_single_result = lambda text: next(it)
    return list(sc.discover_new())


def test_seeding_run_walks_past_the_early_stop(tmp_path):
    held = [jid(f"CIV 2020 {i}") for i in range(250)]
    pairs = [f"{i:032x}\t{d}" for i, d in enumerate(held)]
    rows = [stub(f"CIV 2020 {i}", f"{i:032x}") for i in range(250)]
    rows.append(stub("CIV 2020 3", DOC_B))                      # hidden, behind 250 known rows
    sc = make(tmp_path, held=held, seed_lines=pairs)
    found = _discover(sc, rows)
    assert [s["decision_id"] for s in found] == [jid(f"CIV 2020 3-T{DOC_B[:8]}")]


def test_completed_walk_clears_the_marker_and_the_early_stop_returns(tmp_path):
    held = [jid(f"CIV 2020 {i}") for i in range(250)]
    pairs = [f"{i:032x}\t{d}" for i, d in enumerate(held)]
    rows = [stub(f"CIV 2020 {i}", f"{i:032x}") for i in range(250)]
    sc = make(tmp_path, held=held, seed_lines=pairs)
    assert _discover(sc, rows) == []
    assert not (tmp_path / "ju_gerichte.docids.fullwalk").exists()
    sc._load_docids()
    assert sc._full_walk is False


def test_interrupted_walk_is_resumed_next_run(tmp_path):
    held = [jid(f"CIV 2020 {i}") for i in range(250)]
    pairs = [f"{i:032x}\t{d}" for i, d in enumerate(held)]
    make(tmp_path, held=held, seed_lines=pairs)          # seeding run died before walking
    sc = make(tmp_path, held=held, seed_lines=pairs)
    assert sc._full_walk is True
    rows = [stub(f"CIV 2020 {i}", f"{i:032x}") for i in range(250)] + [stub("CIV 2020 3", DOC_B)]
    assert [s["decision_id"] for s in _discover(sc, rows)] == [jid(f"CIV 2020 3-T{DOC_B[:8]}")]


def test_after_seeding_the_nightly_early_stop_applies_again(tmp_path):
    held = [jid(f"CIV 2020 {i}") for i in range(250)]
    pairs = [f"{i:032x}\t{d}" for i, d in enumerate(held)]
    rows = [stub(f"CIV 2020 {i}", f"{i:032x}") for i in range(250)]
    rows.append(stub("CIV 2020 3", DOC_B))
    sc = make(tmp_path, held=held, sidecar_lines=pairs)
    assert _discover(sc, rows) == []                             # stopped after 200 known


# ── fetch: dates ──────────────────────────────────────────────────────────

HEAD = ("RÉPUBLIQUE ET CANTON DU JURA TRIBUNAL CANTONAL COUR ADMINISTRATIVE "
        "Eff. susp. 113 / 2022 DÉCISION DU 18 OCTOBRE 2022 statuant sur la requête "
        "afin de levée de l'effet suspensif ... recours du 31 mai 2022. " + "x " * 60)


def _fetch(sc, s, text):
    sc._fetch_html = lambda doc_id: ""
    sc._fetch_pdf = lambda st: text
    return sc.fetch_decision(s)


def test_hidden_document_takes_the_date_it_states_not_the_dossier_date(tmp_path):
    sc = make(tmp_path, held=[jid("ADM 2022 94")], sidecar_lines=[f"{DOC_A}\t{jid('ADM 2022 94')}"])
    s = stub("ADM 2022 94", DOC_B, d="2023-02-03")
    s["decision_id"] = sc._identity(s)
    d = _fetch(sc, s, HEAD)
    assert d.decision_date == date(2022, 10, 18)
    assert d.docket_number == "ADM 2022 94"                      # docket stays real
    assert f"{DOC_B}\t{s['decision_id']}" in (tmp_path / "ju_gerichte.docids.txt").read_text()


def test_hidden_document_stating_no_date_is_skipped(tmp_path):
    sc = make(tmp_path, held=[jid("ADM 2022 94")], sidecar_lines=[f"{DOC_A}\t{jid('ADM 2022 94')}"])
    s = stub("ADM 2022 94", DOC_B, d="0000-00-00")
    s["decision_id"] = sc._identity(s)
    assert _fetch(sc, s, "no heading here " * 20) is None


def test_undated_listing_row_of_a_new_docket_gets_its_stated_date(tmp_path):
    sc = make(tmp_path, held=[jid("X 2020 1")], sidecar_lines=[f"{DOC_A}\t{jid('X 2020 1')}"])
    s = stub("ADM 2022 94", DOC_B, d="0000-00-00")
    s["decision_id"] = sc._identity(s)
    assert _fetch(sc, s, HEAD).decision_date == date(2022, 10, 18)


def test_legacy_fetch_keeps_the_listing_date(tmp_path):
    sc = make(tmp_path, SZGerichteScraper)
    s = stub("BEK 2020 1", DOC_B, d="2023-02-03")
    s["decision_id"] = make_decision_id("sz_gerichte", "BEK 2020 1")
    assert _fetch(sc, s, HEAD).decision_date == date(2023, 2, 3)


def test_header_date_takes_the_earliest_heading():
    assert header_date("ARRET DU 8 JANVIER 2026 ... décision du 2 juillet 2025") == date(2026, 1, 8)
    assert header_date("Porrentruy, le 3 novembre 2025 DECISION dans le cadre ... "
                       "décision du 14 juillet 2025") == date(2025, 11, 3)
    assert header_date("ORDONNANCE / DECISION DU 3 MAI 2021") == date(2021, 5, 3)
    assert header_date("DÉCISION DU 5 FEVRIER 2025") == date(2025, 2, 5)
    assert header_date("ARRÊT DU 1er MARS 2021") == date(2021, 3, 1)
    assert header_date("MOTIFS DE LA DECISION PROVISIONNELLE DU 19 DECEMBRE 2024") == date(2024, 12, 19)
    assert header_date("Urteil vom 3. März 2021") is None                  # FR headings only
    assert header_date("x " * 2000 + "ARRÊT DU 8 JANVIER 2026") is None     # beyond the head


# ── the shipped JU seed ───────────────────────────────────────────────────

def test_shipped_ju_seed_is_well_formed():
    lines = [l for l in JUGerichteScraper.DOCID_SEED.read_text().splitlines()
             if l.strip() and not l.startswith("#")]
    assert len(lines) == 1164
    docs = [l.split("\t")[0] for l in lines]
    ids = [l.split("\t")[1] for l in lines]
    assert all(re.fullmatch(r"[0-9a-f]{32}", d) for d in docs)
    assert len(set(docs)) == len(docs)
    assert all(i.startswith("ju_gerichte_") and "-T" not in i for i in ids)
    assert len(set(ids)) == 1162          # 2 duplicate uploads map onto held rulings
